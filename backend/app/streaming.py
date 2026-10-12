"""Single upstream read-only V3 connection. No token or authorized URL is persisted."""

import json
import threading
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse
from google.protobuf.json_format import MessageToDict
from websockets.sync.client import connect
from .MarketDataFeed_pb2 import FeedResponse
from .core import DomainError, now, UTC, timestamp, money
from .paper import PaperBroker
from .automation import Automation
from .activity import record
from .subscriptions import batch, FULL_LIMIT, ROTATE_AFTER_SECONDS


def authorized_uri(uri):
    u = urlparse(uri)
    if (
        u.scheme != "wss"
        or not u.hostname
        or not u.hostname.endswith(".upstox.com")
        or u.username
        or u.password
        or u.port not in (None, 443)
        or u.fragment
    ):
        raise DomainError("Untrusted market-data socket endpoint")
    return uri


class MarketStream:
    def __init__(self, db, provider, lock):
        self.db = db
        self.provider = provider
        self.lock = lock
        self.stop_event = threading.Event()
        self.thread = None
        self.socket = None
        self.state = "STOPPED"
        self.keys = {}
        self.universe_keys = {}
        self.batch_cursor = 0
        self.batch_seconds = 0.0
        self.batch_last_tick = None
        self.rotation_count = 0
        self.accounts = []
        self.quotes = {}
        self.segments = {}
        self.cas = {}
        self.reconnects = 0
        self.accepted = 0
        self.rejected = 0
        self.last_received = None
        self.error = None
        self.session = ""
        self.bars = {}
        self.bar_sessions = {}

    def status(self):
        with self.lock:
            quotes = [
                {**q, "stale": (now() - timestamp(q["timestamp"])).total_seconds() > 10}
                for q in self.quotes.values()
            ]
            return dict(
                state=self.state,
                session=self.session,
                reconnects=self.reconnects,
                accepted=self.accepted,
                rejected=self.rejected,
                last_received=self.last_received,
                error=self.error,
                accounts=self.accounts,
                universe_count=len(self.universe_keys or self.keys),
                subscribed_count=len(self.keys),
                queued_count=max(0,len(self.universe_keys)-len(self.keys)),
                active_instrument_ids=list(self.keys.values()),
                rotation_count=self.rotation_count,
                rotation_after_market_minutes=max(0,round((ROTATE_AFTER_SECONDS-self.batch_seconds)/60,1)),
                segments=self.segments,
                quotes=quotes,
                snapshots=[
                    PaperBroker(self.db).snapshot(
                        a,
                        {
                            q["instrument_id"]: q["price_paise"]
                            for q in quotes
                            if not q["stale"]
                        },
                    )
                    for a in self.accounts
                ],
            )

    def start(self, ids, accounts, *, universe=False, cursor=0):
        self.provider.policy.require_live()
        self.provider.enabled()
        if self.thread and self.thread.is_alive():
            raise DomainError("Stop the existing stream before changing subscriptions")
        if not ids or (not universe and len(set(ids)) > FULL_LIMIT):
            raise DomainError("Manual subscriptions support 1–2000 instruments; use Auto Paper for rotating larger universes")
        keys = {}
        for i in ids:
            rows = self.db.rows("SELECT * FROM instruments WHERE id=?", (i,))
            if not rows:
                raise DomainError("Unknown instrument")
            key = self.provider.key(rows[0]["provider_key"] or "")
            if key in keys:
                raise DomainError("Duplicate provider mapping")
            keys[key] = i
        pinned = set()
        for a in accounts:
            s = PaperBroker(self.db).snapshot(a)
            if s["mode"] != "LIVE" or s["halted"]:
                raise DomainError(
                    "Stream execution requires active LIVE paper accounts"
                )
            required = {p["instrument_id"] for p in s["positions"] + s["basket_positions"]}
            required |= {
                r["instrument_id"]
                for r in self.db.rows(
                    "SELECT instrument_id FROM automation_rules WHERE account_id=? AND enabled=1",
                    (a,),
                )
            }
            required |= {r['instrument_id'] for r in self.db.rows(
                "SELECT instrument_id FROM paper_orders WHERE account_id=? AND status='PENDING'", (a,))}
            pinned |= required
            if not required.issubset(set(ids)):
                raise DomainError("Subscribe to every held and automated instrument")
        self.universe_keys = keys
        self.keys, self.batch_cursor = batch(keys, pinned, cursor)
        self.batch_seconds = 0.0
        self.batch_last_tick = None
        self.rotation_count = 0
        self.accounts = list(set(accounts))
        self.stop_event.clear()
        self.error = None
        self.thread = threading.Thread(
            target=self._run, name="paperlab-market-stream", daemon=True
        )
        self.thread.start()

    def rotate_if_due(self):
        """Only called under the shared execution lock by the automatic scanner."""
        if (len(self.universe_keys) <= FULL_LIMIT or self.state != 'CONNECTED'
                or self.socket is None or self.batch_seconds < ROTATE_AFTER_SECONDS):
            return False
        pinned = set()
        for account in self.accounts:
            for row in self.db.rows(
                "SELECT instrument_id FROM paper_positions WHERE account_id=? UNION SELECT instrument_id FROM paper_orders WHERE account_id=? AND status='PENDING' UNION SELECT instrument_id FROM automation_rules WHERE account_id=? AND enabled=1",
                (account, account, account)):
                pinned.add(row['instrument_id'])
        selected, cursor = batch(self.universe_keys, pinned, self.batch_cursor)
        removed = [k for k in self.keys if k not in selected]
        added = [k for k in selected if k not in self.keys]
        try:
            # Unsubscribe first, so the upstream never temporarily exceeds 2000.
            for method, keys in [('unsub', removed), ('sub', added)]:
                if keys:
                    self.socket.send(json.dumps({'guid':uuid.uuid4().hex,'method':method,
                        'data':{'mode':'full','instrumentKeys':keys}}).encode())
        except Exception:
            self.state = 'RECONNECTING'
            self.quotes = {}
            self.socket.close()
            raise DomainError('Batch subscription failed; feed reconnecting, new entries paused.') from None
        for key in removed:
            i = self.keys[key]
            self.quotes.pop(i, None)
            self.bars.pop(i, None)
            self.bar_sessions.pop(i, None)
        for key in added:
            i = selected[key]
            self.bars.pop(i, None)
            self.bar_sessions[i] = uuid.uuid4().hex
        self.keys = selected
        self.batch_cursor = cursor
        self.batch_seconds = 0.0
        self.batch_last_tick = None
        self.rotation_count += 1
        record(self.db,'stream','BATCH_ROTATED',{'active':len(selected),'queued':len(self.universe_keys)-len(selected),'added':len(added),'pinned':len(pinned)})
        return True

    def stop(self):
        self.stop_event.set()
        if self.socket:
            self.socket.close()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=20)
        if self.thread and self.thread.is_alive():
            self.state = "STOPPING"
        else:
            self.state = "STOPPED"

    def _run(self):
        delay = 1
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    self.state = "CONNECTING"
                    self.batch_seconds = 0.0
                    self.batch_last_tick = None
                    self.quotes = {}
                    self.bars = {}
                    self.bar_sessions = {}
                    self.segments = {}
                    self.cas = {}
                    self.session = uuid.uuid4().hex
                data = self.provider._get("/v3/feed/market-data-feed/authorize")
                uri = authorized_uri(data["authorized_redirect_uri"])
                with connect(
                    uri,
                    open_timeout=10,
                    close_timeout=2,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=4_000_000,
                    proxy=None,
                ) as socket:
                    self.socket = socket
                    if self.stop_event.is_set():
                        break
                    socket.send(
                        json.dumps(
                            {
                                "guid": uuid.uuid4().hex,
                                "method": "sub",
                                "data": {
                                    "mode": "full",
                                    "instrumentKeys": list(self.keys),
                                },
                            }
                        ).encode()
                    )
                    self.state = "CONNECTED"
                    self.error = None
                    record(
                        self.db,
                        "stream",
                        "CONNECTED",
                        {"instruments": len(self.keys), "session": self.session},
                    )
                    while not self.stop_event.is_set():
                        try:
                            raw = socket.recv(timeout=1)
                        except TimeoutError:
                            continue
                        if self.stop_event.is_set():
                            break
                        if not isinstance(raw, bytes):
                            raise DomainError("Non-Protobuf market frame")
                        frame = FeedResponse()
                        frame.ParseFromString(raw)
                        self.ingest(MessageToDict(frame))
                        delay = 1
            except Exception as e:
                if not self.stop_event.is_set():
                    self.state = "RECONNECTING"
                    self.error = "Connection failed: " + type(e).__name__
                    self.reconnects += 1
                    record(
                        self.db,
                        "stream",
                        "RECONNECTING",
                        {"reason": self.error, "retry_seconds": delay},
                    )
            finally:
                self.socket = None
                with self.lock:
                    self.quotes = {}
                    self.bars = {}
                    self.segments = {}
                    for account in self.accounts:
                        with self.db.connect(write=True) as c:
                            count = c.execute(
                                "UPDATE paper_orders SET status='CANCELLED',reason='Stream unavailable; wait for new strategy signal' WHERE account_id=? AND status='PENDING' AND request_key LIKE 'auto:%'",
                                (account,),
                            ).rowcount
                            count += c.execute(
                                "UPDATE baskets SET status='CANCELLED',reason='Stream unavailable; wait for a new F&O signal' WHERE account_id=? AND status='PENDING' AND id IN (SELECT basket_id FROM fno_tickets)",
                                (account,),
                            ).rowcount
                        if count:
                            record(
                                self.db,
                                "automation",
                                "ENTRIES_CANCELLED",
                                {"reason": "Stream unavailable", "count": count},
                                account,
                            )
            if self.stop_event.wait(delay):
                break
            delay = min(30, delay * 2)
        self.state = "STOPPED"
        record(self.db, "stream", "STOPPED", {})

    def ingest(self, frame):
        with self.lock:
            received = now()
            self.last_received = received.isoformat()
            feed_at = datetime.fromtimestamp(int(frame.get("currentTs", 0)) / 1000, UTC)
            if not 0 <= (received - feed_at).total_seconds() <= 10:
                self.rejected += 1
                return
            if "marketInfo" in frame:
                self.segments.update(frame["marketInfo"].get("segmentStatus", {}))
                self.cas = frame["marketInfo"].get("casMarketStatus", {})
                return
            if frame.get('feeds') and self.segments.get('NSE_EQ') == 'NORMAL_OPEN' and self.cas.get('NSE_EQ',{}).get('status') not in ('CTS_CLOSE','CAS_LM_START','CAS_M_STOP'):
                if self.batch_last_tick:
                    elapsed = (received - self.batch_last_tick).total_seconds()
                    if 0 <= elapsed <= 10:
                        self.batch_seconds += elapsed
                self.batch_last_tick = received
            else:
                self.batch_last_tick = None
            for key, f in frame.get("feeds", {}).items():
                if key not in self.keys:
                    continue
                segment = "NSE_EQ" if key.startswith("NSE_INDEX|") else key.split("|")[0]
                if self.segments.get(segment) != "NORMAL_OPEN" or self.cas.get(
                    segment, {}
                ).get("status") in ("CTS_CLOSE", "CAS_LM_START", "CAS_M_STOP"):
                    self.rejected += 1
                    continue
                full = f.get("fullFeed", {})
                market = full.get("marketFF", full.get("indexFF", {}))
                ltpc = f.get("ltpc", market.get("ltpc", {}))
                i = self.keys[key]
                try:
                    at = datetime.fromtimestamp(int(ltpc["ltt"]) / 1000, UTC)
                    p = money(ltpc["ltp"])
                    v = int(market["vtt"]) if "vtt" in market else (0 if key.startswith("NSE_INDEX|") else None)
                    inst = self.db.rows(
                        "SELECT tick_paise FROM instruments WHERE id=?", (i,)
                    )[0]
                    if (
                        not 0 <= (received - at).total_seconds() <= 10
                        or p <= 0
                        or p % inst["tick_paise"]
                    ):
                        raise ValueError()
                    prior = self.quotes.get(i)
                    if prior and (
                        timestamp(prior["timestamp"]) > at
                        or (
                            prior["timestamp"] == at.isoformat()
                            and prior["price_paise"] == p
                            and prior.get("volume_total") == v
                        )
                    ):
                        continue
                except (ValueError, KeyError, OverflowError, DomainError):
                    self.rejected += 1
                    continue
                q = dict(
                    instrument_id=i,
                    instrument_key=key,
                    timestamp=at.isoformat(),
                    received_at=received.isoformat(),
                    price_paise=p,
                    volume_total=v,
                    source="UPSTOX_V3",
                )
                self.quotes[i] = q
                self.accepted += 1
                with self.db.connect(write=True) as c:
                    cur = c.execute(
                        "INSERT INTO quotes(instrument_id,timestamp,received_at,price,source,payload) VALUES(?,?,?,?,?,?)",
                        (
                            i,
                            q["timestamp"],
                            q["received_at"],
                            p,
                            q["source"],
                            json.dumps(f),
                        ),
                    )
                    ref = f"quote:{cur.lastrowid}"
                complete = self._bar(i, at, p, v)
                marks = {
                    j: x["price_paise"]
                    for j, x in self.quotes.items()
                    if 0 <= (received - timestamp(x["timestamp"])).total_seconds() <= 10
                    and self.segments.get("NSE_EQ" if x["instrument_key"].startswith("NSE_INDEX|") else x["instrument_key"].split("|")[0])
                    == "NORMAL_OPEN"
                    and self.cas.get(x["instrument_key"].split("|")[0], {}).get(
                        "status"
                    )
                    not in ("CTS_CLOSE", "CAS_LM_START", "CAS_M_STOP")
                }
                if getattr(self, 'fno_tick', None):
                    self.fno_tick(q, int(ref.split(':')[1]))
                for account in self.accounts:
                    if not self.db.rows(
                        "SELECT 1 FROM paper_positions WHERE account_id=? AND instrument_id=? UNION ALL SELECT 1 FROM paper_orders WHERE account_id=? AND instrument_id=? AND status='PENDING' UNION ALL SELECT 1 FROM automation_rules WHERE account_id=? AND instrument_id=? AND enabled=1 LIMIT 1",
                        (account,i,account,i,account,i),
                    ):
                        continue
                    b = PaperBroker(self.db)
                    s = b.snapshot(account, marks)
                    if s["halted"] or any(
                        pos["instrument_id"] not in marks for pos in s["positions"]
                    ):
                        continue
                    before = self.db.rows(
                        "SELECT COUNT(*) AS n FROM account_ledger WHERE account_id=?",
                        (account,),
                    )[0]["n"]
                    b.protective_orders(account, i, p, q["timestamp"])
                    b.execute_observation(account, i, p, q["timestamp"], ref, marks)
                    after = self.db.rows(
                        "SELECT COUNT(*) AS n FROM account_ledger WHERE account_id=?",
                        (account,),
                    )[0]["n"]
                    if after > before:
                        record(
                            self.db,
                            "execution",
                            "PAPER_FILL",
                            {"instrument_id": i, "price_paise": p, "source": ref},
                            account,
                        )
                    if complete:
                        rules = self.db.rows(
                            "SELECT instrument_id FROM automation_rules WHERE account_id=? AND enabled=1",
                            (account,),
                        )
                        if rules and rules[0]["instrument_id"] == i:
                            if getattr(self, "daily_refresh", None):
                                self.daily_refresh(account)
                            rows = self.db.rows(
                                "SELECT * FROM stream_bars WHERE instrument_id=? AND session=? ORDER BY timestamp",
                                (i, self.bar_sessions.get(i, self.session)),
                            )
                            Automation(self.db).evaluate(account, rows, marks)

    def _bar(self, i, at, p, v):
        if v is None:
            return False
        bucket = at.replace(second=0, microsecond=0)
        old = self.bars.get(i)
        complete = False
        if i not in self.bar_sessions:
            self.bar_sessions[i] = self.session
        if old and bucket < old["bucket"]:
            return False
        if old and (bucket - old["bucket"] > timedelta(minutes=1) or v < old["last_v"]):
            self.bar_sessions[i] = uuid.uuid4().hex
            old = None
        if old and bucket > old["bucket"]:
            if old["eligible"] and bucket - old["bucket"] == timedelta(minutes=1):
                with self.db.connect(write=True) as c:
                    c.execute(
                        "INSERT OR IGNORE INTO stream_bars(instrument_id,session,timestamp,available_at,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            i,
                            self.bar_sessions[i],
                            old["bucket"].isoformat(),
                            at.isoformat(),
                            old["open"],
                            old["high"],
                            old["low"],
                            old["close"],
                            old["last_v"] - old["start_v"],
                        ),
                    )
                complete = True
        if not old or bucket > old["bucket"]:
            self.bars[i] = dict(
                bucket=bucket,
                open=p,
                high=p,
                low=p,
                close=p,
                start_v=v,
                last_v=v,
                eligible=bool(old and bucket - old["bucket"] == timedelta(minutes=1)),
            )
        else:
            if v < old["last_v"]:
                old["eligible"] = False
            old.update(
                high=max(old["high"], p), low=min(old["low"], p), close=p, last_v=v
            )
        return complete
