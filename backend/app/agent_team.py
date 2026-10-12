"""Cooperating rule-based analysis agents; no trained AI or profit guarantee.

All prices, including indicator ATR, stay in paise. Callers supply completed
OHLC bars in chronological order (unmarked bars are asserted completed by the
caller). Explicit incomplete, future, or not-yet-available bars are ignored.
This module writes only its three analysis tables, never capital or risk rules.
"""
import json
import math
from numbers import Integral

import pandas as pd

from .core import now, timestamp
from .indicators import compute


ROLES = ("market", "trend", "pattern", "strategy", "risk-review", "coordinator")
MIN_REAL_TRADES = 5
PROXY_HORIZON = 5


def _safe(value):
    """Normalize numpy/pandas scalars and missing values for strict JSON."""
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if hasattr(value, "item"):
        return _safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json(value):
    return json.dumps(_safe(value), allow_nan=False, sort_keys=True)


def _frame(rows):
    records = rows.to_dict("records") if isinstance(rows, pd.DataFrame) else list(rows)
    accepted = []
    previous = None
    cutoff = now()
    for original in records:
        r = dict(original)
        if any(k in r and r[k] in (False, 0) for k in ("completed", "complete", "is_complete", "closed")):
            continue
        at = timestamp(r["timestamp"])
        if at > cutoff or (r.get("available_at") and timestamp(r["available_at"]) > cutoff):
            continue
        if previous is not None and at <= previous:
            raise ValueError("Completed bars must be strictly chronological and unique")
        previous = at
        for key in ("open", "high", "low", "close"):
            if isinstance(r[key], bool) or not isinstance(r[key], Integral) or r[key] <= 0:
                raise ValueError("OHLC prices must be positive integer paise")
        if not r["low"] <= min(r["open"], r["close"]) <= max(r["open"], r["close"]) <= r["high"]:
            raise ValueError("Invalid OHLC bounds")
        volume = float(r.get("volume", 0))
        if not math.isfinite(volume) or volume < 0:
            raise ValueError("Volume must be finite and nonnegative")
        accepted.append({**{k: r[k] for k in ("open", "high", "low", "close")},
                         "volume": volume, "timestamp": at})
    return pd.DataFrame(accepted)


def _choices(f, i):
    """Read only indices <= i, including lagged support/resistance indicators."""
    if i < 59:
        return []
    r = f.iloc[i]
    if not all(pd.notna(r[k]) for k in ("atr", "adx", "ema_20", "ema_50")) or r.atr <= 0:
        return []
    market = "TREND" if r.adx >= 20 else "RANGE"
    choices = []
    if market == "TREND":
        if r.close > r.resistance:
            choices.append(("breakout", 1, r.resistance, 2.25))
        elif r.close < r.support:
            choices.append(("breakout", -1, r.support, 2.25))
        if r.close > r.ema_20 > r.ema_50 and r.ema_20 > f.iloc[i - 3].ema_20:
            choices.append(("trend", 1, r.ema_20, 2.0))
        elif r.close < r.ema_20 < r.ema_50 and r.ema_20 < f.iloc[i - 3].ema_20:
            choices.append(("trend", -1, r.ema_20, 2.0))
    elif pd.notna(r.z_score):
        if r.z_score < -1.5 and r.close > f.iloc[i - 1].close:
            choices.append(("range_reversal", 1, r.low, 1.25))
        elif r.z_score > 1.5 and r.close < f.iloc[i - 1].close:
            choices.append(("range_reversal", -1, r.high, 1.25))
    result = []
    for name, direction, level, rr in choices:
        floor, ceiling, buffer = {"trend": (1.5, 3.0, .5),
                                  "breakout": (1.0, 2.5, .5),
                                  "range_reversal": (.75, 1.5, .25)}[name]
        factor = min(ceiling, max(floor, abs(r.close - level) / r.atr + buffer))
        atr = max(1, int(round(r.atr)))
        if r.close - atr * factor * (rr if direction < 0 else 1) <= 0:
            continue
        result.append(dict(name=name, direction=direction, atr=atr, regime=market,
                           stop_factor=round(float(factor), 6), target_rr=rr,
                           reason={"trend": "EMA20/50 alignment with directional EMA slope",
                                   "breakout": "Close beyond the prior 20-bar range",
                                   "range_reversal": "Low ADX, stretched z-score and observed reversal"}[name],
                           features=_safe({"timestamp": r.timestamp.isoformat(),
                               "close_paise": r.close, "adx": r.adx, "ema_20_paise": r.ema_20,
                               "ema_50_paise": r.ema_50, "z_score": r.z_score,
                               "support_paise": r.support, "resistance_paise": r.resistance,
                               "stop_reference_paise": level, "stop_buffer_atr": buffer,
                               "stop_factor_bounds": [floor, ceiling],
                               "relative_volume": r.relative_volume})))
    return result


def _proxy(f, name, market):
    """Nonoverlapping next-open, five-bar prior outcomes; adverse-first OHLC.

    The current decision bar is excluded even as an outcome. Fixed 10bps
    round-trip friction is a proxy assumption, not actual derivative costs.
    """
    outcomes, last_exit = [], -1
    for i in range(59, len(f) - PROXY_HORIZON - 1):
        if i <= last_exit:
            continue
        sig = next((s for s in _choices(f, i) if s["name"] == name and s["regime"] == market), None)
        if not sig:
            continue
        entry = float(f.iloc[i + 1].open)
        distance = sig["atr"] * sig["stop_factor"]
        direction, rr = sig["direction"], sig["target_rr"]
        stop, target = entry - direction * distance, entry + direction * distance * rr
        end = i + PROXY_HORIZON
        exit_price = float(f.iloc[end].close)
        for j in range(i + 1, end + 1):
            bar = f.iloc[j]
            # Gap losses may exceed one R; targets never receive a better fill.
            if (direction > 0 and bar.low <= stop) or (direction < 0 and bar.high >= stop):
                exit_price = min(stop, bar.open) if direction > 0 else max(stop, bar.open)
                end = j
                break
            if (direction > 0 and bar.high >= target) or (direction < 0 and bar.low <= target):
                exit_price, end = target, j
                break
        outcomes.append((direction * (exit_price - entry) - entry * .001) / distance)
        last_exit = end
    n = len(outcomes)
    mean = sum(outcomes) / n if n else 0.0
    deviation = math.sqrt(sum((r - mean) ** 2 for r in outcomes) / (n - 1)) if n > 1 else 0.0
    # Penalize small samples; this is not a confidence interval or optimization.
    score = mean - deviation / math.sqrt(n) - 1 / math.sqrt(n) if n >= 3 else -2.0
    return dict(samples=n, mean_r=mean if n else None, score=score,
                evidence="adequate_proxy" if n >= 3 else "insufficient_data",
                horizon_bars=PROXY_HORIZON, round_trip_cost_bps=10,
                evaluated_before=f.iloc[-1].timestamp.isoformat(),
                caveat="Chronological underlying-price proxy, not derivative returns or trained/optimal AI")


class AgentTeam:
    def __init__(self, db):
        self.db = db
        self.ensure_schema()

    def ensure_schema(self):
        with self.db.connect(write=True) as c:
            for sql in (
                """CREATE TABLE IF NOT EXISTS agent_messages (
                    id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, role TEXT NOT NULL,
                    status TEXT NOT NULL, message TEXT NOT NULL, timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS trade_reviews (
                    id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, basket_id INTEGER NOT NULL,
                    strategy TEXT NOT NULL, regime TEXT NOT NULL, timestamp TEXT NOT NULL,
                    net_pnl INTEGER NOT NULL, evidence_ok INTEGER NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(account_id,basket_id))""",
                """CREATE TABLE IF NOT EXISTS decision_context (
                    account_id INTEGER NOT NULL, basket_id INTEGER NOT NULL, timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(account_id,basket_id))""",
                "CREATE INDEX IF NOT EXISTS agent_messages_account ON agent_messages(account_id,id)",
            ):
                c.execute(sql)

    def _messages(self, account_id, messages):
        with self.db.connect(write=True) as c:
            c.executemany("INSERT INTO agent_messages(account_id,role,status,message,timestamp,payload) VALUES(?,?,?,?,?,?)",
                          [(account_id, role, status, message, now().isoformat(), _json(payload))
                           for role, status, message, payload in messages])

    def message(self, account, role, status, message, payload=None):
        """Append an independent risk/execution (or analysis) message.

        No role upsert: earlier decisions remain auditable. The caller owns the
        supplied role/status vocabulary; this method performs no execution.
        """
        self._messages(account, [(role, status, message, {} if payload is None else payload)])

    def _evidence(self, account_id, as_of):
        evidence = self.db.rows("""SELECT strategy,regime,COUNT(*) AS trades,
                    SUM(net_pnl) AS net_pnl,SUM(net_pnl<0) AS losses
                    FROM trade_reviews r WHERE account_id=? AND evidence_ok=1
                    AND NOT EXISTS (SELECT 1 FROM basket_trades t WHERE t.basket_id=r.basket_id
                                    AND julianday(t.timestamp)>julianday(?))
                    GROUP BY strategy,regime""", (account_id, as_of))
        return [{**e, "status": "adequate_real_trades" if e["trades"] >= MIN_REAL_TRADES
                 else "insufficient_data"} for e in evidence]

    def evaluate(self, account_id, rows, excluded=()):
        """Return an advisory signal or None; never execute or resize a trade."""
        self.review_closed(account_id)
        try:
            f = _frame(rows)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self._messages(account_id, [(role, "INVALID_DATA", str(exc), {"candidates": []}) for role in ROLES])
            return None
        if len(f) < 60:
            self._messages(account_id, [(role, "INSUFFICIENT_DATA", f"{len(f)}/60 completed bars; insufficient data",
                                         {"bars": len(f), "candidates": []}) for role in ROLES])
            return None
        f = compute(f)
        candidates = _choices(f, len(f) - 1)
        evidence = self._evidence(account_id, f.iloc[-1].timestamp.isoformat())
        excluded = {excluded} if isinstance(excluded, str) else set(excluded)
        for s in candidates:
            s["proxy"] = _proxy(f, s["name"], s["regime"])
            e = next((e for e in evidence if (e["strategy"], e["regime"]) == (s["name"], s["regime"])), {})
            veto = e.get("trades", 0) >= MIN_REAL_TRADES and e["net_pnl"] < 0 and e["losses"] / e["trades"] >= .8
            s["eligible"] = s["name"] not in excluded and not veto
            s["review_evidence"] = e or {"trades": 0, "status": "insufficient_data"}
            s["exclusion"] = "caller_excluded" if s["name"] in excluded else "negative_real_trade_evidence" if veto else None
        candidates.sort(key=lambda s: (-s["proxy"]["score"], s["name"], -s["direction"]))
        chosen = next((s for s in candidates if s["eligible"]), None)
        r = f.iloc[-1]
        self._messages(account_id, [
            ("market", "READY", "Completed bars in integer paise; zero volume is supported", {"bars": len(f), "as_of": r.timestamp.isoformat(), "atr_paise": r.atr}),
            ("trend", "READY", "ADX and EMA20/50 alignment and slope", {"adx": r.adx, "ema_20_paise": r.ema_20, "ema_50_paise": r.ema_50}),
            ("pattern", "READY", "Prior-range breaks or stretched range reversals", {"support_paise": r.support, "resistance_paise": r.resistance, "z_score": r.z_score}),
            ("strategy", "RANKED" if candidates else "NO_SIGNAL", "Conservative chronological prior-data proxy ranking; insufficient data uses deterministic priority", {"candidates": candidates}),
            ("risk-review", "REVIEWED" if any(e["trades"] >= MIN_REAL_TRADES for e in evidence) else "INSUFFICIENT_DATA", "Veto requires >=5 ledger-backed closed paper trades, >=80% losses and negative aggregate net; hard risk and capital unchanged", {"evidence": evidence, "minimum_trades": MIN_REAL_TRADES}),
            ("coordinator", "SELECTED" if chosen else "WAITING", "Cooperating rule-based agents; no trained AI or guarantee. " + (chosen["reason"] if chosen else "No eligible candidate; insufficient or excluded evidence."), {"selected": chosen["name"] if chosen else None}),
        ])
        if not chosen:
            return None
        signal = {k: chosen[k] for k in ("name", "direction", "atr", "regime", "stop_factor", "target_rr", "reason", "features")}
        signal["features"] = {**signal["features"], "proxy": chosen["proxy"], "review_evidence": chosen["review_evidence"]}
        return _safe(signal)

    def record_decision(self, account_id, basket_id, signal, rows=None):
        """Freeze first decision context; underlying expectations are not option stops.

        Call after inserting fno_tickets to capture actual managed contract levels.
        Repeated calls preserve the original snapshot, including on restart.
        """
        features = signal.get("features", {})
        recent = []
        if rows is not None:
            f = _frame(rows)
            as_of = features.get("timestamp")
            if not f.empty and as_of:
                f = f[f.timestamp <= timestamp(as_of)]
            recent = f.tail(20).to_dict("records")
        entry = features.get("close_paise") or (recent[-1]["close"] if recent else None)
        distance = int(round(signal["atr"] * signal.get("stop_factor", 1.5)))
        payload = dict(signal=signal, recent_bars=recent, expected=dict(
            basis="underlying paise; advisory, not a derivative price forecast",
            reference_paise=entry, stop_distance_paise=distance,
            stop_paise=round(entry - signal["direction"] * distance) if entry else None,
            target_paise=round(entry + signal["direction"] * distance * signal.get("target_rr", 2)) if entry else None))
        with self.db.connect(write=True) as c:
            b = c.execute("SELECT id FROM baskets WHERE id=? AND account_id=?", (basket_id, account_id)).fetchone()
            if not b:
                raise ValueError("Basket does not belong to account")
            ticket = c.execute("SELECT stop_paise,target_paise,signal_at FROM fno_tickets WHERE basket_id=?", (basket_id,)).fetchone()
            payload["managed_contract"] = dict(ticket) if ticket else None
            c.execute("INSERT OR IGNORE INTO decision_context(account_id,basket_id,timestamp,payload) VALUES(?,?,?,?)",
                      (account_id, basket_id, now().isoformat(), _json(payload)))

    def review_closed(self, account_id):
        """Idempotent factual reviews, one per closed basket (not per leg)."""
        inserted = []
        with self.db.connect(write=True) as c:
            closed = c.execute("""SELECT b.*,t.signal,t.stop_paise AS managed_stop,t.target_paise,
                      t.reason AS exit_reason,d.payload AS context
                      FROM baskets b JOIN fno_tickets t ON t.basket_id=b.id
                      LEFT JOIN decision_context d ON d.basket_id=b.id AND d.account_id=b.account_id
                      WHERE b.account_id=? AND b.status='CLOSED'
                      AND NOT EXISTS(SELECT 1 FROM trade_reviews r WHERE r.account_id=b.account_id AND r.basket_id=b.id)
                      ORDER BY b.id""", (account_id,)).fetchall()
            for b in closed:
                trades = c.execute("SELECT * FROM basket_trades WHERE basket_id=? ORDER BY id", (b["id"],)).fetchall()
                if not trades:
                    continue
                ledger = c.execute("""SELECT l.* FROM account_ledger l JOIN basket_events e
                        ON l.source=('basket_event:' || e.id || ':' || e.digest)
                        WHERE e.basket_id=? AND l.account_id=? ORDER BY l.event_id""", (b["id"], account_id)).fetchall()
                gross, fees = sum(t["gross_pnl"] for t in trades), sum(t["fees"] for t in trades)
                net = gross - fees
                context = json.loads(b["context"]) if b["context"] else {}
                signal = context.get("signal", {})
                market = signal.get("regime", "UNKNOWN")
                verified = (len(trades) == 1 and bool(ledger)
                            and any(l["event_type"] == "BASKET_OPEN" for l in ledger)
                            and any(l["event_type"] in ("BASKET_CLOSE", "BASKET_SETTLEMENT", "BASKET_DELIVERY") for l in ledger)
                            and sum(l["realized_pnl"] for l in ledger) == gross
                            and sum(l["fees"] for l in ledger) == fees
                            and signal.get("name") == b["signal"] and market != "UNKNOWN")
                observations = []
                closed_at = max(timestamp(t["timestamp"]) for t in trades)
                for leg in json.loads(b["legs_json"]):
                    prices = []
                    if b["opened_at"]:
                        # Normalize timestamps before comparing: offsets need not match.
                        quotes = c.execute("SELECT timestamp,price FROM quotes WHERE instrument_id=? AND julianday(timestamp)>=julianday(?) AND julianday(timestamp)<=julianday(?) ORDER BY julianday(timestamp),id",
                                           (leg["instrument_id"], b["opened_at"], closed_at.isoformat())).fetchall()
                        prices = [q["price"] for q in quotes]
                    direction = 1 if leg["quantity"] > 0 else -1
                    observations.append(dict(instrument_id=leg["instrument_id"], quantity=leg["quantity"],
                        samples=len(prices), first_paise=prices[0] if prices else None,
                        last_paise=prices[-1] if prices else None, min_paise=min(prices) if prices else None,
                        max_paise=max(prices) if prices else None,
                        observed_stop_cross=any(p <= b["managed_stop"] if direction > 0 else p >= b["managed_stop"] for p in prices),
                        observed_target_cross=any(p >= b["target_paise"] if direction > 0 else p <= b["target_paise"] for p in prices)))
                classification = "fees_exceed_nonnegative_gross" if net < 0 <= gross else "negative_gross_with_fees" if net < 0 else "nonnegative_net"
                loss_observations = []
                if net < 0:
                    if any(o["observed_stop_cross"] for o in observations):
                        loss_observations.append("managed_stop_cross_observed")
                    if any(o["samples"] >= 2 and (o["last_paise"] - o["first_paise"]) * o["quantity"] < 0 for o in observations):
                        loss_observations.append("adverse_sampled_contract_move")
                    if gross >= 0:
                        loss_observations.append("fees_exceeded_gross_gain")
                    if not any(o["samples"] >= 2 for o in observations):
                        loss_observations.append("insufficient_price_path_data")
                review = dict(basket_id=b["id"], strategy=b["signal"], regime=market,
                    classification=classification, gross_pnl_paise=gross, fees_paise=fees, net_pnl_paise=net,
                    closed_at=closed_at.isoformat(), loss_observations=loss_observations,
                    evidence_ok=bool(verified), ledger_event_ids=[l["event_id"] for l in ledger],
                    trade_ids=[t["id"] for t in trades], observations=observations,
                    decision_context=context, recorded_exit_reason=b["exit_reason"] or b["reason"],
                    evidence_status="ledger_backed" if verified else "insufficient_data",
                    path_status="observed_quotes_only" if any(o["samples"] for o in observations) else "insufficient_data",
                    interpretation="Observed accounting, price path and saved pattern context only; no inferred cause. Quote extrema are sampled, not a complete path.")
                c.execute("INSERT OR IGNORE INTO trade_reviews(account_id,basket_id,strategy,regime,timestamp,net_pnl,evidence_ok,payload) VALUES(?,?,?,?,?,?,?,?)",
                          (account_id, b["id"], b["signal"], market, now().isoformat(), net, int(verified), _json(review)))
                inserted.append(review)
        if inserted:
            self._messages(account_id, [("risk-review", "REVIEWED", f"Recorded {len(inserted)} closed paper-trade reviews; causal attribution unavailable", {"basket_ids": [r["basket_id"] for r in inserted]})])
        return inserted

    def status(self, account_id):
        """JSON-safe persisted state; read-only and survives process restarts."""
        messages = self.db.rows("SELECT role,status,message,timestamp,payload FROM agent_messages WHERE account_id=? ORDER BY id DESC LIMIT 60", (account_id,))
        for m in messages:
            m["payload"] = json.loads(m["payload"])
        reviews = self.db.rows("SELECT payload,timestamp FROM trade_reviews WHERE account_id=? ORDER BY id DESC LIMIT 100", (account_id,))
        latest = self.db.rows("SELECT payload FROM agent_messages WHERE account_id=? AND role='strategy' ORDER BY id DESC LIMIT 1", (account_id,))
        candidates = json.loads(latest[0]["payload"]).get("candidates", []) if latest else []
        return _safe(dict(messages=messages, reviews=[{**json.loads(r["payload"]), "timestamp": r["timestamp"]} for r in reviews], candidates=candidates))
