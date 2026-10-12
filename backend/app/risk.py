from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from .core import DomainError, timestamp


def charges(notional, side, cfg):
    n = Decimal(notional)

    def bps(key):
        return n * Decimal(str(cfg.get(key, 0))) / 10000

    brokerage = Decimal(cfg.get("brokerage_paise", 0)) + bps("brokerage_bps")
    if cfg.get("brokerage_cap_paise") is not None:
        brokerage = min(brokerage, Decimal(cfg["brokerage_cap_paise"]))
    exchange = bps("exchange_bps")
    sebi = bps("sebi_bps")
    gst = (brokerage + exchange + sebi) * Decimal(str(cfg.get("gst_pct", 0))) / 100
    tax = bps("stt_buy_bps" if side == "BUY" else "stt_sell_bps")
    stamp = bps("stamp_buy_bps") if side == "BUY" else 0
    total = brokerage + exchange + sebi + gst + tax + stamp + bps("slippage_bps")
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def size_position(equity, cash, price, stop, lot_size, risk, costs):
    if price <= stop or stop <= 0 or equity <= 0:
        return 0
    budget = int(equity * risk["risk_pct"] / 100)
    qty = min(
        int(budget / (price - stop)),
        cash // price,
        int(equity * risk["max_position_pct"] / 100) // price,
    )
    qty = qty // lot_size * lot_size
    while qty > 0:
        entry = charges(price * qty, "BUY", costs)
        exit_cost = charges(stop * qty, "SELL", costs)
        if (
            price * qty + entry <= cash
            and (price - stop) * qty + entry + exit_cost <= budget
        ):
            break
        qty -= lot_size
    return qty


def day(value):
    return timestamp(value).astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat()


def check_entry(
    account, positions, orders, ledger, price, qty, stop, inst, equity, risk, costs, at
):
    if account["halted"]:
        raise DomainError(account["halt_reason"] or "PAPER TRADING HALTED")
    if equity is None:
        raise DomainError("PRICE UNAVAILABLE for an existing position")
    if equity <= 0:
        raise DomainError("ACCOUNT CAPITAL EXHAUSTED - PAPER TRADING HALTED")
    if inst["kind"] in ("INDEX", "FUTURE"):
        raise DomainError(
            "Index is not tradable; futures execution requires a validated margin/settlement model and is disabled"
        )
    if inst["expiry"] and day(at) >= inst["expiry"]:
        raise DomainError("Expiry-day entries disabled; settlement is not inferred")
    if qty % inst["lot_size"]:
        raise DomainError("Quantity must respect the full contract lot size")
    if any(p["instrument_id"] == inst["id"] for p in positions):
        raise DomainError("Pyramiding disabled; close the existing position first")
    if len(positions) >= risk["max_positions"]:
        raise DomainError("Maximum concurrent positions reached")
    if stop is None or not 0 < stop < price:
        raise DomainError(
            "Long entry requires a valid stop below the observed fill price"
        )
    fees = charges(qty * price, "BUY", costs)
    if qty * price + fees > account["cash_balance"]:
        raise DomainError("TRADE REJECTED - INSUFFICIENT VIRTUAL CAPITAL")
    if qty * price > equity * risk["max_position_pct"] / 100:
        raise DomainError("Maximum position size exceeded")
    exposure = (price - stop) * qty + fees + charges(stop * qty, "SELL", costs)
    if exposure > equity * risk["risk_pct"] / 100:
        raise DomainError(
            "Risk per trade exceeded, including simulated round-trip costs"
        )
    today = day(at)
    todays = [
        x
        for x in ledger
        if day(x["timestamp"]) == today and x["event_type"] != "INITIAL_CAPITAL"
    ]
    net = sum(x["realized_pnl"] - x["fees"] for x in todays)
    total_book = sum(p["cost_basis"] for p in positions)
    unrealized = equity - account["cash_balance"] - total_book
    # Conservative fixed daily budget; profits do not replenish loss allowance.
    if (
        min(net, 0) + min(unrealized, 0)
        <= -account["initial_capital"] * risk["daily_loss_pct"] / 100
    ):
        raise DomainError("Daily loss limit reached")
    entries = sum(
        x["side"] == "BUY" and x["status"] == "FILLED" and day(x["filled_at"]) == today
        for x in orders
    )
    if entries >= risk["max_trades"]:
        raise DomainError("Maximum daily entries reached")
