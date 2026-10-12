"""Bounded, causal strategy selection. All executions remain in PaperBroker."""

import json
from .backtesting import run
from .core import DomainError
from .strategies import REGISTRY
from .activity import record

WARMUP = 200
WINDOW = 300
REASSESS = 20
COOLDOWN = 10
QUARANTINE = 100
MAX_DRAWDOWN = 5


def rank(rows, instrument, account, parameters, excluded):
    ranking = []
    for name in sorted(REGISTRY):
        try:
            result = run(
                rows[-WINDOW:],
                instrument,
                name,
                account["initial_capital"],
                json.loads(account["risk_json"]),
                json.loads(account["cost_json"]),
                parameters,
            )
            m = result["metrics"]
            pnl = m["closed_trade_net_pnl_paise"]
            factor = m["profit_factor"]
            eligible = (
                m["total_trades"] >= 3
                and pnl > 0
                and m["net_pnl_paise"] > 0
                and m["max_drawdown_pct"] <= MAX_DRAWDOWN
                and (factor is None or factor >= 1.2)
                and name not in excluded
            )
            # Penalize observed equity drawdown in the same units as net P&L.
            score = (
                m["net_pnl_paise"]
                - account["initial_capital"] * m["max_drawdown_pct"] / 100
            )
            ranking.append(
                {
                    "strategy": name,
                    "eligible": eligible,
                    "trades": m["total_trades"],
                    "net_pnl_paise": pnl,
                    "drawdown_pct": m["max_drawdown_pct"],
                    "score": score,
                    "reason": (
                        "Loss quarantine"
                        if name in excluded
                        else (
                            "Passed historical checks"
                            if eligible
                            else "Insufficient or unfavorable evidence"
                        )
                    ),
                }
            )
        except DomainError as e:
            ranking.append(
                {"strategy": name, "eligible": False, "reason": str(e), "score": 0}
            )
    return sorted(ranking, key=lambda x: (-x["score"], x["strategy"]))


def choose(db, rule, account, rows, exposed, selector=None, event_at=None):
    """Persist every observed bar once; reconfiguration cannot erase loss history."""
    saved = db.rows("SELECT payload FROM adaptive_state WHERE rule_id=?", (rule["id"],))
    s = (
        json.loads(saved[0]["payload"])
        if saved
        else {
            "sequence": 0,
            "last_trade": 0,
            "selected": None,
            "peak": account["initial_capital"],
            "cooldown_until": 0,
            "excluded": {},
            "ranking": [],
            "assessed": -REASSESS,
        }
    )
    at = rows[-1]["available_at"]
    previous = s["selected"]
    if s.get("last_bar") != at:
        s["sequence"] += 1
        s["last_bar"] = at
    seq = s["sequence"]
    inst = db.rows("SELECT * FROM instruments WHERE id=?", (rule["instrument_id"],))[0]
    cap = min(rule["max_quantity"], s.get("quantity_cap", rule["max_quantity"]))
    trades = db.rows(
        "SELECT t.* FROM paper_trades t WHERE t.account_id=? AND t.id>? AND t.exit_time<=? "
        "AND EXISTS(SELECT 1 FROM paper_orders o WHERE o.account_id=t.account_id "
        "AND o.instrument_id=t.instrument_id AND o.filled_at=t.entry_time "
        "AND o.side='BUY' AND o.strategy=t.strategy AND o.request_key LIKE ?) ORDER BY t.id",
        (account["id"], s["last_trade"], event_at or at, f'auto:{rule["id"]}:%'),
    )
    for trade in trades:
        s["last_trade"] = trade["id"]
        if trade["net_pnl"] < 0:
            s["cooldown_until"] = seq + COOLDOWN
            s["excluded"][trade["strategy"]] = seq + QUARANTINE
            s["selected"] = None
            s["assessed"] = -REASSESS
            cap = min(
                cap,
                max(
                    inst["lot_size"],
                    (trade["quantity"] // 2 // inst["lot_size"]) * inst["lot_size"],
                ),
            )
            s["last_loss"] = {
                "trade_id": trade["id"],
                "net_pnl_paise": trade["net_pnl"],
                "strategy": trade["strategy"],
            }
    s["quantity_cap"] = cap
    equity = account["equity_paise"]
    if equity is not None:
        s["peak"] = max(s["peak"], equity)
        s["drawdown_pct"] = max(0, (1 - equity / s["peak"]) * 100)
        if s["drawdown_pct"] >= MAX_DRAWDOWN:
            s["halted"] = True
    fingerprint = [rule["instrument_id"], rule["parameters"], cap, rows[0]["timestamp"]]
    if s.get("configuration") != fingerprint:
        s["selected"] = None
        s["assessed"] = -REASSESS
        s["ranking"] = []
        s["configuration"] = fingerprint
    if s.get("halted"):
        status, reason = (
            "ADAPTIVE_HALTED",
            "5% account equity drawdown reached. Adaptive entries remain disabled for this account.",
        )
    elif equity is None:
        status, reason = "ADAPTIVE_WAIT", "Fresh marks are required for every position."
    elif exposed:
        status, reason = (
            "EXPOSURE_OR_PENDING_ORDER",
            "Existing exposure retains its original strategy and protective exits.",
        )
    elif seq < s["cooldown_until"]:
        status, reason = "LOSS_COOLDOWN", "Pausing entries after a closed losing trade."
    elif len(rows) < WARMUP:
        status, reason = (
            "WARMUP",
            f"Adaptive selection requires {WARMUP} completed bars.",
        )
    else:
        if seq - s["assessed"] >= (1 if selector else REASSESS):
            excluded = {name for name, until in s["excluded"].items() if seq < until}
            params = {
                "atr_stop": json.loads(rule["parameters"]).get("atr_stop", 1.5),
                "max_quantity": cap,
            }
            if selector:
                s["ranking"], s["trend"] = selector(excluded)
            else:
                s["ranking"] = rank(rows, inst, account, params, excluded)
            eligible = [r for r in s["ranking"] if r["eligible"]]
            s["selected"] = eligible[0]["strategy"] if eligible else None
            s["selected_atr_stop"] = (
                eligible[0].get("atr_stop", params["atr_stop"]) if eligible else None
            )
            s["assessed"] = seq
        status = "SELECTED" if s["selected"] else "NO_QUALIFYING_STRATEGY"
        reason = (
            "Highest score among qualifying historical simulations; waiting for its entry signal."
            if s["selected"]
            else "No strategy passes the historical checks. No entry will be placed."
        )
    s.update(
        status=status,
        reason=reason,
        cooldown_remaining=max(0, s["cooldown_until"] - seq),
        bars=len(rows),
        required=WARMUP,
        changed=previous != s["selected"],
        instrument_id=rule["instrument_id"],
    )
    with db.connect(write=True) as c:
        c.execute(
            "INSERT INTO adaptive_state(rule_id,payload) VALUES(?,?) ON CONFLICT(rule_id) DO UPDATE SET payload=excluded.payload",
            (rule["id"], json.dumps(s)),
        )
    if s["changed"]:
        record(
            db,
            "automation",
            "STRATEGY_SELECTION_CHANGED",
            {
                "previous": previous,
                "selected": s["selected"],
                "reason": reason,
                "bar_available_at": at,
            },
            account["id"],
        )
    return s["selected"] if status == "SELECTED" else None, s
