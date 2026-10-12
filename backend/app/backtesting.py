from dataclasses import asdict
import json
import math
import uuid
import numpy as np
import pandas as pd
from .core import DomainError, now, timestamp
from .data import analysis_frame, candles
from .indicators import compute, regime
from .paper import PaperBroker
from .risk import charges, size_position, day, check_entry
from .strategies import REGISTRY


def metrics(trades, curve, initial):
    values = [t["net_pnl"] for t in trades]
    wins = [x for x in values if x > 0]
    losses = [x for x in values if x < 0]
    equity = pd.Series([initial] + [x["equity_paise"] for x in curve], dtype=float)
    dd = (equity / equity.cummax() - 1) * 100
    daily = pd.Series(dtype=float)
    if curve:
        d = pd.DataFrame(curve)
        d["date"] = (
            pd.to_datetime(d["timestamp"], utc=True)
            .dt.tz_convert("Asia/Kolkata")
            .dt.date
        )
        end = d.groupby("date")["equity_paise"].last()
        daily = end.pct_change()
        daily.iloc[0] = end.iloc[0] / initial - 1
    std = daily.std(ddof=1)
    downside = np.sqrt(np.mean(np.minimum(daily, 0) ** 2)) if len(daily) else 0
    sharpe = (
        float(daily.mean() / std * np.sqrt(252))
        if len(daily) >= 20 and std > 0
        else None
    )
    sortino = (
        float(daily.mean() / downside * np.sqrt(252))
        if len(daily) >= 20 and downside > 0
        else None
    )
    periods = {}
    for unit in ("month", "year"):
        grouped = {}
        for x in curve:
            grouped[x["timestamp"][: 7 if unit == "month" else 4]] = x["equity_paise"]
        previous = initial
        ret = {}
        for k, v in grouped.items():
            ret[k] = (v / previous - 1) * 100
            previous = v
        periods[unit + "ly_returns_pct"] = ret
    longest_win = longest_loss = ws = ls = 0
    for x in values:
        ws = ws + 1 if x > 0 else 0
        ls = ls + 1 if x < 0 else 0
        longest_win = max(longest_win, ws)
        longest_loss = max(longest_loss, ls)
    gross = [t.get("gross_pnl", t["net_pnl"] + t.get("fees", 0)) for t in trades]
    return {
        "total_trades": len(values),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": len(wins) / len(values) * 100 if values else None,
        "average_win_paise": sum(wins) / len(wins) if wins else None,
        "average_loss_paise": sum(losses) / len(losses) if losses else None,
        "expectancy_paise": sum(values) / len(values) if values else None,
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "gross_profit_paise": sum(x for x in gross if x > 0),
        "gross_loss_paise": sum(x for x in gross if x < 0),
        "closed_trade_net_pnl_paise": sum(values),
        "net_pnl_paise": int(equity.iloc[-1] - initial),
        "max_drawdown_pct": float(-dd.min()),
        "sharpe": sharpe,
        "sortino": sortino,
        "largest_win_paise": max(wins) if wins else None,
        "largest_loss_paise": min(losses) if losses else None,
        "consecutive_wins": longest_win,
        "consecutive_losses": longest_loss,
        **periods,
    }


def run(
    rows,
    instrument,
    strategy,
    initial=100000,
    risk=None,
    costs=None,
    parameters=None,
    *,
    _computed=None
):
    from .schemas import RiskConfig, CostConfig

    risk = RiskConfig.model_validate(risk or {}).model_dump()
    costs = CostConfig.model_validate(costs or {}).model_dump()
    if strategy not in REGISTRY:
        raise DomainError("Unknown strategy")
    if len(rows) < 52:
        raise DomainError("At least 52 candles required for warm-up and execution")
    if instrument["kind"] in ("FUTURE", "INDEX"):
        raise DomainError("Execution for this instrument type is disabled")
    f = compute(analysis_frame(rows)) if _computed is None else _computed
    engine = REGISTRY[strategy]
    cash = initial
    pos = None
    pending = None
    trades = []
    curve = []
    rejected = []
    daily_pnl = {}
    daily_entries = {}
    for i, row in enumerate(rows):
        at = row["timestamp"]
        price = row["open"]
        today = day(at)
        if instrument["expiry"] and today > instrument["expiry"]:
            if pos:
                raise DomainError(
                    "Open derivative crossed expiry: settlement evidence required"
                )
            continue
        # Only the OPEN is used at this event; current high/low/close are not visible yet.
        if pos and (price <= pos["stop"] or price >= pos["target"]):
            fee = charges(price * pos["qty"], "SELL", costs)
            net = (price - pos["entry"]) * pos["qty"] - pos["fees"] - fee
            cash += price * pos["qty"] - fee
            trades.append(
                {
                    "entry_time": pos["time"],
                    "exit_time": at,
                    "entry_price": pos["entry"],
                    "exit_price": price,
                    "quantity": pos["qty"],
                    "net_pnl": net,
                    "fees": pos["fees"] + fee,
                    "regime": pos["regime"],
                    "strategy": strategy,
                }
            )
            daily_pnl[today] = (
                daily_pnl.get(today, 0) + (price - pos["entry"]) * pos["qty"] - fee
            )
            pos = None
        if pending and not pos:
            stop = (
                int(round(pending.stop_loss * 100 / instrument["tick_paise"]))
                * instrument["tick_paise"]
            )
            target = (
                int(round(pending.target * 100 / instrument["tick_paise"]))
                * instrument["tick_paise"]
            )
            qty = size_position(
                cash, cash, price, stop, instrument["lot_size"], risk, costs
            )
            if parameters and "max_quantity" in parameters:
                qty = min(
                    qty,
                    int(parameters["max_quantity"])
                    // instrument["lot_size"]
                    * instrument["lot_size"],
                )
            allowed = (
                qty > 0
                and target > price
                and daily_pnl.get(today, 0) > -initial * risk["daily_loss_pct"] / 100
                and daily_entries.get(today, 0) < risk["max_trades"]
                and (not instrument["expiry"] or today < instrument["expiry"])
            )
            if allowed:
                try:
                    check_entry(
                        {"halted": 0, "initial_capital": initial, "cash_balance": cash},
                        [],
                        [],
                        [],
                        price,
                        qty,
                        stop,
                        instrument,
                        cash,
                        risk,
                        costs,
                        at,
                    )
                except DomainError:
                    allowed = False
            if allowed:
                fee = charges(price * qty, "BUY", costs)
                cash -= price * qty + fee
                daily_pnl[today] = daily_pnl.get(today, 0) - fee
                daily_entries[today] = daily_entries.get(today, 0) + 1
                pos = {
                    "qty": qty,
                    "entry": price,
                    "fees": fee,
                    "stop": stop,
                    "target": target,
                    "time": at,
                    "regime": pending.market_regime,
                }
            else:
                rejected.append(
                    {
                        "timestamp": at,
                        "reason": "Unaffordable, invalid gap entry, expiry, or risk budget exhausted",
                    }
                )
        pending = None
        equity = cash + (row["close"] * pos["qty"] if pos else 0)
        curve.append({"timestamp": row["available_at"], "equity_paise": equity})
        if not pos and i < len(rows) - 1:
            pending = engine.signal(f.iloc[: i + 1], parameters)
    result = metrics(trades, curve, initial)
    peak = initial
    for x in curve:
        peak = max(peak, x["equity_paise"])
        x["drawdown_pct"] = (1 - x["equity_paise"] / peak) * 100
    return {
        "metrics": result,
        "equity_curve": curve,
        "trades": trades,
        "rejected": rejected,
        "open_position": pos,
        "cash_paise": cash,
        "initial_capital_paise": initial,
        "warnings": [
            "SIMULATED. Candle-open fills and open-only protective checks, not intrabar or tick execution.",
            "Costs are user-configured, not a verified current tax schedule.",
        ]
        + (["Too few closed trades for reliable inference"] if len(trades) < 30 else [])
        + (["Open position is marked, not forcibly liquidated"] if pos else []),
    }


def walk_forward(
    rows, instrument, strategy, initial, risk, costs, train=100, validation=50, test=50
):
    if min(train, validation, test) < 52:
        raise DomainError("Each window requires at least 52 bars including warm-up")
    windows = []
    for start in range(0, len(rows) - train - validation - test + 1, test):
        a = rows[start : start + train]
        b = rows[start + train : start + train + validation]
        c = rows[start + train + validation : start + train + validation + test]
        candidates = [{"atr_stop": v} for v in (1.0, 1.5, 2.0)]
        training = [
            run(a, instrument, strategy, initial, risk, costs, p) for p in candidates
        ]
        scores = [
            run(b, instrument, strategy, initial, risk, costs, p) for p in candidates
        ]
        best = max(
            range(len(scores)), key=lambda i: scores[i]["metrics"]["net_pnl_paise"]
        )
        out = run(c, instrument, strategy, initial, risk, costs, candidates[best])
        windows.append(
            {
                "train_start": a[0]["timestamp"],
                "validation_start": b[0]["timestamp"],
                "test_start": c[0]["timestamp"],
                "test_end": c[-1]["available_at"],
                "selected_parameters": candidates[best],
                "in_sample": training[best]["metrics"],
                "validation": scores[best]["metrics"],
                "out_of_sample": out["metrics"],
                "warnings": out["warnings"],
            }
        )
    if not windows:
        raise DomainError("Insufficient data for a full train/validation/test window")
    return {
        "windows": windows,
        "method": "Chronological disjoint train/validation/test within each fold; select ATR stop on validation only. Each window has its own warm-up and isolated capital; no compounded performance claim.",
    }
