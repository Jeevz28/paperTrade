"""Atomic, observed-price replay baskets with separate collateral and evidence."""

import hashlib
import json
from .core import DomainError, timestamp, now
from .risk import charges, day


def digest(payload):
    return hashlib.sha256(payload.encode()).hexdigest()


def exposure(c, account_id):
    return [
        dict(r)
        for r in c.execute(
            "SELECT p.*,b.account_id,b.strategy,b.status,i.symbol,i.kind,i.expiry,i.settlement FROM basket_positions p JOIN baskets b ON b.id=p.basket_id JOIN instruments i ON i.id=p.instrument_id WHERE b.account_id=?",
            (account_id,),
        )
    ]


def collateral(c, account_id):
    return sum(p["margin"] for p in exposure(c, account_id))


def audit(c, account_id, ledger):
    expected = {}
    valid = True
    linked = {e["source"]: e for e in ledger if e["event_type"].startswith("BASKET_")}
    rows = c.execute(
        "SELECT e.* FROM basket_events e JOIN baskets b ON b.id=e.basket_id WHERE b.account_id=? ORDER BY e.id",
        (account_id,),
    ).fetchall()
    if len(rows) != len(linked):
        return False
    for row in rows:
        sha = digest(row["payload"])
        event = linked.get(f'basket_event:{row["id"]}:{sha}')
        if sha != row["digest"] or not event or event["event_type"] != row["kind"]:
            return False
        payload = json.loads(row["payload"])
        gross = realized = 0
        if row["kind"] == "BASKET_OPEN":
            for leg in payload["legs"]:
                key = (row["basket_id"], leg["instrument_id"])
                inst = c.execute(
                    "SELECT kind FROM instruments WHERE id=?", (leg["instrument_id"],)
                ).fetchone()
                book = 0 if inst["kind"] == "FUTURE" else leg["price"] * leg["quantity"]
                expected[key] = {
                    "quantity": leg["quantity"],
                    "basis": leg["price"],
                    "book": book,
                    "margin": payload["margin"] if inst["kind"] == "FUTURE" else 0,
                    "kind": inst["kind"],
                }
                gross -= book
        elif row["kind"] == "BASKET_CLOSE":
            for leg in payload["legs"]:
                p = expected.pop((row["basket_id"], leg["instrument_id"]), None)
                if not p or p["quantity"] != leg["quantity"]:
                    return False
                change = (
                    (leg["price"] - p["basis"]) * p["quantity"]
                    if p["kind"] == "FUTURE"
                    else leg["price"] * p["quantity"] - p["book"]
                )
                realized += change
                gross += (
                    change if p["kind"] == "FUTURE" else leg["price"] * p["quantity"]
                )
        elif row["kind"] == "BASKET_SETTLEMENT":
            key = (row["basket_id"], payload["instrument_id"])
            p = expected.get(key)
            if not p:
                return False
            realized = (payload["price"] - p["basis"]) * p["quantity"]
            gross = (
                realized if p["kind"] == "FUTURE" else payload["price"] * p["quantity"]
            )
            if payload["final"]:
                del expected[key]
            else:
                p["basis"] = payload["price"]
        elif row["kind"] == "BASKET_DELIVERY":
            from .delivery import plan

            held = [
                dict(
                    instrument_id=k[1],
                    signed_quantity=p["quantity"],
                    basis_price=p["basis"],
                    book_cost=p["book"],
                )
                for k, p in expected.items()
                if k[0] == row["basket_id"]
            ]
            instruments = {
                p["instrument_id"]: dict(
                    c.execute(
                        "SELECT * FROM instruments WHERE id=?", (p["instrument_id"],)
                    ).fetchone()
                )
                for p in held
            }
            evidence = {}
            for eid in payload["evidence_ids"]:
                e = c.execute(
                    "SELECT * FROM delivery_evidence WHERE id=?", (eid,)
                ).fetchone()
                if not e:
                    return False
                evidence[e["instrument_id"]] = dict(e)
            try:
                result = plan(held, instruments, evidence)
            except (DomainError, KeyError):
                return False
            if (
                any(result[k] != payload[k] for k in result)
                or result["fees"] != event["fees"]
            ):
                return False
            gross = result["gross"]
            realized = result["realized"]
            expected = {k: p for k, p in expected.items() if k[0] != row["basket_id"]}
            for p in result["positions"]:
                expected[(row["basket_id"], p["instrument_id"])] = dict(
                    quantity=p["signed_quantity"],
                    basis=p["basis_price"],
                    book=p["book_cost"],
                    margin=0,
                    kind="SPOT",
                )
        if gross != event["gross_amount"] or realized != event["realized_pnl"]:
            return False
    actual = {(p["basket_id"], p["instrument_id"]): p for p in exposure(c, account_id)}
    if set(actual) != set(expected):
        return False
    return all(
        (
            actual[k]["signed_quantity"],
            actual[k]["basis_price"],
            actual[k]["book_cost"],
            actual[k]["margin"],
        )
        == (p["quantity"], p["basis"], p["book"], p["margin"])
        for k, p in expected.items()
    )


def mark_positions(c, account, live_marks=None):
    positions = exposure(c, account["id"])
    pnl = 0
    complete = True
    for p in positions:
        mark = None
        if account["mode"] == "REPLAY" and account["clock"]:
            r = c.execute(
                "SELECT close FROM candles WHERE dataset_id=? AND instrument_id=? AND available_at=?",
                (account["dataset_id"], p["instrument_id"], account["clock"]),
            ).fetchone()
            if r:
                mark = r["close"]
        elif live_marks:
            mark = live_marks.get(p["instrument_id"])
        value = (
            (
                (mark - p["basis_price"]) * p["signed_quantity"]
                if p["kind"] == "FUTURE"
                else mark * p["signed_quantity"] - p["book_cost"]
            )
            if mark is not None
            else None
        )
        p["mark_paise"] = mark
        p["unrealized_pnl_paise"] = value
        complete = complete and value is not None
        pnl += value if value is not None else 0
    return positions, pnl if complete else None


class DerivativeBroker:
    def __init__(self, db):
        self.db = db

    def submit(self, account_id, strategy, legs, request_key, stop_paise=None, *, live=False):
        from .paper import PaperBroker

        if not PaperBroker(self.db).reconcile(account_id)["ok"]:
            raise DomainError("Accounting reconciliation failed")
        if not 1 <= len(legs) <= 2 or len({x["instrument_id"] for x in legs}) != len(
            legs
        ):
            raise DomainError("One or two distinct instruments required")
        with self.db.connect(write=True) as c:
            a = PaperBroker(self.db).account(c, account_id)
            managed = live and a["mode"] == "LIVE" and c.execute(
                "SELECT 1 FROM fno_sessions WHERE account_id=?", (account_id,)
            ).fetchone()
            if live and (not managed or len(legs) != 1 or strategy not in ('future','long_call','long_put')):
                raise DomainError("Only managed single-leg F&O paper tickets support live execution")
            if a["halted"] or (not managed and (a["mode"] != "REPLAY" or not a["clock"])):
                raise DomainError("Active replay account and visible candle required")
            encoded = json.dumps(legs, sort_keys=True)
            old = c.execute(
                "SELECT * FROM baskets WHERE account_id=? AND request_key=?",
                (account_id, request_key),
            ).fetchone()
            if old:
                if (
                    old["legs_json"] != encoded
                    or old["strategy"] != strategy
                    or old["stop_paise"] != stop_paise
                ):
                    raise DomainError("Idempotency key reused with different content")
                return dict(old)
            for leg in legs:
                inst = c.execute(
                    "SELECT * FROM instruments WHERE id=?", (leg["instrument_id"],)
                ).fetchone()
                if (
                    not inst
                    or not leg["quantity"]
                    or abs(leg["quantity"]) % inst["lot_size"]
                ):
                    raise DomainError("Unknown contract or invalid lot quantity")
                if managed and not c.execute(
                    "SELECT 1 FROM fno_contracts WHERE account_id=? AND instrument_id=?", (account_id, inst['id'])
                ).fetchone():
                    raise DomainError("Contract is outside this account's F&O universe")
                if not managed and not c.execute(
                    "SELECT 1 FROM candles WHERE dataset_id=? AND instrument_id=?",
                    (a["dataset_id"], inst["id"]),
                ).fetchone():
                    raise DomainError(
                        "Every basket leg must exist in the account dataset"
                    )
            cur = c.execute(
                "INSERT INTO baskets(account_id,strategy,status,legs_json,stop_paise,created_at,request_key) VALUES(?,?,'PENDING',?,?,?,?)",
                (account_id, strategy, encoded, stop_paise, now().isoformat() if managed else a["clock"], request_key),
            )
            return dict(
                c.execute(
                    "SELECT * FROM baskets WHERE id=?", (cur.lastrowid,)
                ).fetchone()
            )

    def request_close(self, account_id, basket_id):
        with self.db.connect(write=True) as c:
            a = c.execute(
                "SELECT clock,mode FROM paper_accounts WHERE id=?", (account_id,)
            ).fetchone()
            if not a:
                raise DomainError("Unknown account")
            cur = c.execute(
                "UPDATE baskets SET status='CLOSING',close_requested_at=? WHERE id=? AND account_id=? AND status='OPEN'",
                (now().isoformat() if a["mode"] == "LIVE" else a["clock"], basket_id, account_id),
            )
            if not cur.rowcount:
                raise DomainError("Only open baskets can be closed")

    def _event(self, c, a, b, at, kind, cash_delta, realized, fee, payload):
        from .paper import append_event

        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        sha = digest(raw)
        cur = c.execute(
            "INSERT INTO basket_events(basket_id,timestamp,kind,payload,digest) VALUES(?,?,?,?,?)",
            (b["id"], at, kind, raw, sha),
        )
        source = f"basket_event:{cur.lastrowid}:{sha}"
        after = a["cash_balance"] + cash_delta
        append_event(
            c,
            a["id"],
            at,
            kind,
            a["cash_balance"],
            after,
            f"basket-event:{cur.lastrowid}",
            gross=cash_delta + fee,
            fees=fee,
            realized=realized,
            source=source,
        )
        c.execute(
            "UPDATE paper_accounts SET cash_balance=?,costs=costs+?,realized_pnl=realized_pnl+? WHERE id=?",
            (after, fee, realized, a["id"]),
        )

    def _validate(self, c, a, b, insts, legs, prices, at):
        risk = json.loads(a["risk_json"])
        costs = self.costs(c, a, insts[0]["kind"])
        managed = c.execute('SELECT state FROM fno_sessions WHERE account_id=?',(a['id'],)).fetchone() if a['mode']=='LIVE' else None
        if managed:
            state = json.loads(managed['state'])
            if state.get('halted'):
                raise DomainError('Managed account drawdown stop reached')
            risk['risk_pct'] *= state.get('risk_scale',1)
            if insts[0]['kind'] in ('CALL','PUT') and b.get('stop_paise') and prices[0] <= b['stop_paise']:
                raise DomainError('Observed option price crossed planned stop before entry')
            ticket = c.execute('SELECT target_paise FROM fno_tickets WHERE basket_id=?',(b.get('id'),)).fetchone()
            if ticket and insts[0]['kind'] in ('CALL','PUT') and prices[0]>=ticket['target_paise']:
                raise DomainError('Observed option price crossed planned target before entry')
        if c.execute(
            "SELECT 1 FROM paper_positions WHERE account_id=?", (a["id"],)
        ).fetchone():
            raise DomainError(
                "Close standalone positions before opening an atomic basket"
            )
        held = exposure(c, a["id"])
        if held:
            raise DomainError("One atomic basket at a time per account")
        if a["cash_balance"] <= 0:
            raise DomainError("ACCOUNT CAPITAL EXHAUSTED")
        ledger = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM account_ledger WHERE account_id=?", (a["id"],)
            )
        ]
        day_net = sum(
            e["realized_pnl"] - e["fees"]
            for e in ledger
            if day(e["timestamp"]) == day(at) and e["event_type"] != "INITIAL_CAPITAL"
        )
        if day_net <= -a["initial_capital"] * risk["daily_loss_pct"] / 100:
            raise DomainError("Daily loss budget exhausted")
        entries = sum(
            e["event_type"] in ("BASKET_OPEN", "PAPER_BUY")
            and day(e["timestamp"]) == day(at)
            for e in ledger
        )
        if entries >= risk["max_trades"]:
            raise DomainError("Daily entry limit reached")
        fee = sum(
            charges(
                p * abs(l["quantity"]), "BUY" if l["quantity"] > 0 else "SELL", costs
            )
            for p, l in zip(prices, legs)
        )
        exit_fee = sum(
            charges(
                p * abs(l["quantity"]), "SELL" if l["quantity"] > 0 else "BUY", costs
            )
            for p, l in zip(prices, legs)
        )
        for inst, price in zip(insts, prices):
            if price <= 0 or price % inst["tick_paise"]:
                raise DomainError("Invalid observed tick")
            if inst["expiry"] and day(at) >= inst["expiry"]:
                raise DomainError("Expiry-day entries are disabled")
        margin = 0
        if b["strategy"] == "future":
            if len(legs) != 1 or insts[0]["kind"] != "FUTURE":
                raise DomainError("Futures ticket requires one futures leg")
            q = legs[0]["quantity"]
            stop = b["stop_paise"]
            if (
                not stop
                or (q > 0 and stop >= prices[0])
                or (q < 0 and stop <= prices[0])
            ):
                raise DomainError("Directional futures stop required")
            m = c.execute(
                "SELECT * FROM margin_evidence WHERE instrument_id=? AND available_at<=? AND valid_from<=? AND valid_until>? ORDER BY available_at DESC,id DESC LIMIT 1",
                (insts[0]["id"], at, at, at),
            ).fetchone()
            if not m:
                raise DomainError("Point-in-time margin evidence unavailable")
            margin = m["initial_per_lot"] * (abs(q) // insts[0]["lot_size"])
            debit = 0
            worst = abs(prices[0] - stop) * abs(q)
        else:
            debit = sum(p * l["quantity"] for p, l in zip(prices, legs))
            if b["strategy"] in ("long_call", "long_put"):
                wanted = "CALL" if b["strategy"] == "long_call" else "PUT"
                if (
                    len(legs) != 1
                    or insts[0]["kind"] != wanted
                    or legs[0]["quantity"] < 0
                ):
                    raise DomainError("Invalid long-option structure")
                worst = debit
            elif b["strategy"] in ("bull_call", "bear_put"):
                if len(legs) != 2:
                    raise DomainError("Two option legs required")
                long = next((j for j, l in enumerate(legs) if l["quantity"] > 0), None)
                if long is None:
                    raise DomainError("Long protection leg required")
                short = 1 - long
                x, y = insts[long], insts[short]
                kind = "CALL" if b["strategy"] == "bull_call" else "PUT"
                if (
                    x["kind"] != kind
                    or y["kind"] != kind
                    or x["expiry"] != y["expiry"]
                    or not x["underlying"]
                    or x["underlying"] != y["underlying"]
                    or legs[long]["quantity"] != -legs[short]["quantity"]
                ):
                    raise DomainError(
                        "Matched underlying, expiry and quantities required"
                    )
                if (kind == "CALL" and x["strike_paise"] >= y["strike_paise"]) or (
                    kind == "PUT" and x["strike_paise"] <= y["strike_paise"]
                ):
                    raise DomainError("Invalid spread strike ordering")
                width = (
                    abs(x["strike_paise"] - y["strike_paise"]) * legs[long]["quantity"]
                )
                if debit < 0 or debit > width:
                    raise DomainError(
                        "Observed spread debit outside defined payoff bounds"
                    )
                worst = debit
            elif b["strategy"] in ("covered_call", "protective_put"):
                if len(legs) != 2:
                    raise DomainError("Stock and option legs required")
                stock = next(
                    (j for j, i in enumerate(insts) if i["kind"] == "SPOT"), None
                )
                if stock is None:
                    raise DomainError("Stock protection required")
                opt = 1 - stock
                q = legs[stock]["quantity"]
                option = insts[opt]
                if q <= 0 or option["underlying"] != insts[stock]["symbol"]:
                    raise DomainError("Underlying mismatch")
                if b["strategy"] == "covered_call":
                    if option["kind"] != "CALL" or legs[opt]["quantity"] != -q:
                        raise DomainError("Covered call must be fully covered")
                    worst = debit
                else:
                    if option["kind"] != "PUT" or legs[opt]["quantity"] != q:
                        raise DomainError("Protective put quantities must match")
                    worst = max(0, debit - option["strike_paise"] * q)
            else:
                raise DomainError("Unknown basket strategy")
        if debit + margin + fee > a["cash_balance"]:
            raise DomainError("INSUFFICIENT VIRTUAL CAPITAL")
        if max(debit, margin) > a["cash_balance"] * risk["max_position_pct"] / 100:
            raise DomainError("Maximum position capital exceeded")
        if worst + fee + exit_fee > a["cash_balance"] * risk["risk_pct"] / 100:
            raise DomainError("Basket maximum/planned loss exceeds risk budget")
        return debit, margin, fee

    @staticmethod
    def costs(c, a, kind):
        if a['mode'] == 'LIVE' and c.execute('SELECT 1 FROM fno_sessions WHERE account_id=?',(a['id'],)).fetchone():
            from .fno import simulation_costs
            return simulation_costs(kind)
        return json.loads(a['cost_json'])

    def _closed_trade(self, c, b, at):
        totals = c.execute(
            "SELECT COALESCE(SUM(l.realized_pnl),0),COALESCE(SUM(l.fees),0) FROM basket_events e JOIN account_ledger l ON l.source=('basket_event:' || e.id || ':' || e.digest) WHERE e.basket_id=?",
            (b["id"],),
        ).fetchone()
        c.execute(
            "INSERT INTO basket_trades(basket_id,timestamp,gross_pnl,fees) VALUES(?,?,?,?)",
            (b["id"], at, totals[0], totals[1]),
        )

    def process(self, account_id, rows):
        from .paper import PaperBroker

        if not rows or not PaperBroker(self.db).reconcile(account_id)["ok"]:
            return
        observations = {r["instrument_id"]: r for r in rows}
        at = rows[0]["timestamp"]
        if any(r["timestamp"] != at for r in rows):
            raise DomainError("Unsynchronized observations")
        with self.db.connect(write=True) as c:
            a = PaperBroker(self.db).account(c, account_id)
            if a["halted"]:
                return
            if a["mode"] == 'LIVE':
                if not c.execute('SELECT 1 FROM fno_sessions WHERE account_id=?',(account_id,)).fetchone():
                    raise DomainError('Live derivative account is not managed')
                for r in rows:
                    q = c.execute('SELECT * FROM quotes WHERE id=?',(r.get('quote_id'),)).fetchone()
                    if not q or q['source'] != 'UPSTOX_V3' or (q['instrument_id'],q['timestamp'],q['price']) != (r['instrument_id'],r['timestamp'],r['open']) or not 0 <= (now()-timestamp(q['timestamp'])).total_seconds() <= 10:
                        raise DomainError('Live derivative fills require a fresh stored stream quote')
            baskets = [
                dict(b)
                for b in c.execute(
                    "SELECT * FROM baskets WHERE account_id=? AND status IN ('PENDING','OPEN','CLOSING') ORDER BY id",
                    (account_id,),
                )
            ]
            for b in baskets:
                a = PaperBroker(self.db).account(c, account_id)
                legs = json.loads(b["legs_json"])
                if b["status"] == "CLOSING":
                    legs = [
                        {
                            "instrument_id": r["instrument_id"],
                            "quantity": r["signed_quantity"],
                        }
                        for r in c.execute(
                            "SELECT * FROM basket_positions WHERE basket_id=?",
                            (b["id"],),
                        )
                    ]
                if (
                    b["status"] == "OPEN"
                    and b["strategy"] == "future"
                    and c.execute(
                        "SELECT 1 FROM basket_positions p JOIN instruments i ON i.id=p.instrument_id WHERE p.basket_id=? AND i.kind='FUTURE'",
                        (b["id"],),
                    ).fetchone()
                ):
                    leg = legs[0]
                    obs = observations.get(leg["instrument_id"])
                    reason = None
                    if obs:
                        p = c.execute(
                            "SELECT p.*,i.lot_size FROM basket_positions p JOIN instruments i ON i.id=p.instrument_id WHERE p.basket_id=?",
                            (b["id"],),
                        ).fetchone()
                        m = c.execute(
                            "SELECT * FROM margin_evidence WHERE instrument_id=? AND available_at<=? AND valid_from<=? AND valid_until>? ORDER BY available_at DESC,id DESC LIMIT 1",
                            (leg["instrument_id"], at, at, at),
                        ).fetchone()
                        lots = abs(leg["quantity"]) // p["lot_size"]
                        equity = (
                            a["cash_balance"]
                            + (obs["open"] - p["basis_price"]) * leg["quantity"]
                        )
                        if not m:
                            reason = "Margin evidence expired: close at observed open"
                        elif m["initial_per_lot"] * lots > p["margin"]:
                            reason = "Margin requirement increased: close rather than under-reserve"
                        elif equity < m["maintenance_per_lot"] * lots:
                            reason = "Maintenance margin breached"
                        elif (
                            leg["quantity"] > 0 and obs["open"] <= b["stop_paise"]
                        ) or (leg["quantity"] < 0 and obs["open"] >= b["stop_paise"]):
                            reason = "Futures stop triggered"
                    if reason:
                        b["status"] = "CLOSING"
                        b["close_requested_at"] = at
                        c.execute(
                            "UPDATE baskets SET status='CLOSING',close_requested_at=? WHERE id=?",
                            (at, b["id"]),
                        )
                        c.execute(
                            "INSERT INTO risk_events(account_id,timestamp,reason) VALUES(?,?,?)",
                            (account_id, at, reason),
                        )
                if b["status"] == "OPEN":
                    continue
                if (
                    b["created_at"]
                    if b["status"] == "PENDING"
                    else b["close_requested_at"]
                ) > at:
                    continue
                if not all(l["instrument_id"] in observations for l in legs):
                    continue
                insts = [
                    dict(
                        c.execute(
                            "SELECT * FROM instruments WHERE id=?",
                            (l["instrument_id"],),
                        ).fetchone()
                    )
                    for l in legs
                ]
                prices = [observations[l["instrument_id"]]["open"] for l in legs]
                if any(i["expiry"] and day(at) > i["expiry"] for i in insts):
                    c.execute(
                        "INSERT INTO risk_events(account_id,timestamp,reason) VALUES(?,?,?)",
                        (
                            account_id,
                            at,
                            "Expired basket: official settlement evidence required",
                        ),
                    )
                    continue
                evidence = [
                    {
                        "instrument_id": l["instrument_id"],
                        "quantity": l["quantity"],
                        "price": p,
                        ("quote_id" if a["mode"] == "LIVE" else "candle_id"): observations[l["instrument_id"]]["id"],
                    }
                    for l, p in zip(legs, prices)
                ]
                if b["status"] == "PENDING":
                    try:
                        debit, margin, fee = self._validate(
                            c, a, b, insts, legs, prices, at
                        )
                    except DomainError as e:
                        c.execute(
                            "UPDATE baskets SET status='REJECTED',reason=? WHERE id=?",
                            (str(e), b["id"]),
                        )
                        c.execute(
                            "INSERT INTO risk_events(account_id,timestamp,reason) VALUES(?,?,?)",
                            (account_id, at, str(e)),
                        )
                        continue
                    for l, i, p in zip(legs, insts, prices):
                        entry_fee = charges(
                            p * abs(l["quantity"]),
                            "BUY" if l["quantity"] > 0 else "SELL",
                            self.costs(c, a, insts[0]["kind"]),
                        )
                        c.execute(
                            "INSERT INTO basket_positions(basket_id,instrument_id,signed_quantity,basis_price,book_cost,margin,entry_fees) VALUES(?,?,?,?,?,?,?)",
                            (
                                b["id"],
                                i["id"],
                                l["quantity"],
                                p,
                                0 if i["kind"] == "FUTURE" else p * l["quantity"],
                                margin if i["kind"] == "FUTURE" else 0,
                                entry_fee,
                            ),
                        )
                    self._event(
                        c,
                        a,
                        b,
                        at,
                        "BASKET_OPEN",
                        -debit - fee,
                        0,
                        fee,
                        {"legs": evidence, "margin": margin},
                    )
                    c.execute(
                        "UPDATE baskets SET status='OPEN',opened_at=? WHERE id=?",
                        (at, b["id"]),
                    )
                else:
                    held = [
                        dict(p)
                        for p in c.execute(
                            "SELECT p.*,i.kind FROM basket_positions p JOIN instruments i ON i.id=p.instrument_id WHERE basket_id=?",
                            (b["id"],),
                        )
                    ]
                    price_map = {l["instrument_id"]: p for l, p in zip(legs, prices)}
                    fee = realized = proceeds = 0
                    for p in held:
                        mark = price_map[p["instrument_id"]]
                        q = p["signed_quantity"]
                        change = (
                            (mark - p["basis_price"]) * q
                            if p["kind"] == "FUTURE"
                            else mark * q - p["book_cost"]
                        )
                        realized += change
                        proceeds += change if p["kind"] == "FUTURE" else mark * q
                        fee += charges(
                            mark * abs(q),
                            "SELL" if q > 0 else "BUY",
                            self.costs(c, a, insts[0]["kind"]),
                        )
                    self._event(
                        c,
                        a,
                        b,
                        at,
                        "BASKET_CLOSE",
                        proceeds - fee,
                        realized,
                        fee,
                        {"legs": evidence},
                    )
                    self._closed_trade(c, b, at)
                    c.execute(
                        "DELETE FROM basket_positions WHERE basket_id=?", (b["id"],)
                    )
                    c.execute(
                        "UPDATE baskets SET status='CLOSED' WHERE id=?", (b["id"],)
                    )

    def settle(self, account_id, skip_physical_final=False):
        from .paper import PaperBroker

        if not PaperBroker(self.db).reconcile(account_id)["ok"]:
            raise DomainError("Accounting error")
        with self.db.connect(write=True) as c:
            a = PaperBroker(self.db).account(c, account_id)
            if a["halted"] or a["mode"] != "REPLAY" or not a["clock"]:
                raise DomainError("Active replay clock required")
            applied = []
            for p in exposure(c, account_id):
                if p["kind"] == "SPOT":
                    continue
                evidence = [
                    dict(e)
                    for e in c.execute(
                        "SELECT * FROM settlement_evidence WHERE instrument_id=? AND available_at<=? AND effective_at<=? ORDER BY effective_at",
                        (p["instrument_id"], a["clock"], a["clock"]),
                    )
                ]
                b = dict(
                    c.execute(
                        "SELECT * FROM baskets WHERE id=?", (p["basket_id"],)
                    ).fetchone()
                )
                for e in evidence:
                    if e["effective_at"] < b["opened_at"]:
                        continue
                    if p["last_settlement_id"]:
                        previous = c.execute(
                            "SELECT effective_at FROM settlement_evidence WHERE id=?",
                            (p["last_settlement_id"],),
                        ).fetchone()[0]
                        if e["effective_at"] <= previous:
                            continue
                    if p["settlement"] != "CASH" and e["final"]:
                        if skip_physical_final:
                            continue
                        raise DomainError(
                            "Physical delivery needs a delivery ledger; cash settlement refused"
                        )
                    if p["kind"] != "FUTURE" and not e["final"]:
                        continue
                    if e["final"] and (
                        not p["expiry"] or day(e["effective_at"]) < p["expiry"]
                    ):
                        raise DomainError("Final settlement predates expiry")
                    q = p["signed_quantity"]
                    pnl = (e["price"] - p["basis_price"]) * q
                    cash = pnl if p["kind"] == "FUTURE" else e["price"] * q
                    a = PaperBroker(self.db).account(c, account_id)
                    self._event(
                        c,
                        a,
                        b,
                        e["available_at"],
                        "BASKET_SETTLEMENT",
                        cash,
                        pnl,
                        0,
                        {
                            "settlement_id": e["id"],
                            "instrument_id": p["instrument_id"],
                            "quantity": q,
                            "final": e["final"],
                            "price": e["price"],
                        },
                    )
                    if e["final"]:
                        c.execute("DELETE FROM basket_positions WHERE id=?", (p["id"],))
                    else:
                        c.execute(
                            "UPDATE basket_positions SET basis_price=?,last_settlement_id=? WHERE id=?",
                            (e["price"], e["id"], p["id"]),
                        )
                        p["basis_price"] = e["price"]
                        p["last_settlement_id"] = e["id"]
                    applied.append(e["id"])
                    if e["final"]:
                        break
                if not c.execute(
                    "SELECT 1 FROM basket_positions WHERE basket_id=?", (b["id"],)
                ).fetchone():
                    c.execute(
                        "UPDATE baskets SET status='CLOSED' WHERE id=?", (b["id"],)
                    )
                    self._closed_trade(c, b, a["clock"])
            return {"applied_evidence": applied}
