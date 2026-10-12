from dataclasses import dataclass
import math
from .indicators import regime


@dataclass
class Signal:
    strategy: str
    direction: str
    entry_price: float
    stop_loss: float
    target: float
    reason: str
    market_regime: str
    timestamp: str


class Strategy:
    name = "base"
    description = ""

    def condition(self, f, p):
        return False

    def signal(self, f, p=None):
        if len(f) < 51:
            return None
        r = f.iloc[-1]
        p = p or {}
        if not math.isfinite(r["atr"]) or r["atr"] <= 0 or not self.condition(f, p):
            return None
        distance = r["atr"] * float(p.get("atr_stop", 1.5))
        if distance <= 0 or distance >= r["close"]:
            return None
        return Signal(
            self.name,
            "LONG",
            float(r["close"]),
            float(r["close"] - distance),
            float(r["close"] + 2 * distance),
            self.description,
            regime(f)["regime"],
            str(r["timestamp"]),
        )


class Box(Strategy):
    name = "box"
    description = "Sideways range, repeated support touches, bullish rejection"

    def condition(self, f, p):
        r = f.iloc[-1]
        recent = f.iloc[-20:-1]
        touches = ((recent["low"] - r["support"]).abs() < r["atr"] * 0.3).sum()
        return (
            r["adx"] < 20
            and not r["breakout"]
            and not r["breakdown"]
            and touches >= 2
            and r["resistance"] - r["support"] > r["atr"] * 2
            and r["low"] <= r["support"] + r["atr"] * 0.3
            and r["close"] > r["open"]
        )


class Breakout(Strategy):
    name = "breakout"
    description = (
        "Close above prior 20-bar resistance, relative volume >1.5, extension <=2 ATR"
    )

    def condition(self, f, p):
        r = f.iloc[-1]
        return (
            r["breakout"]
            and r["relative_volume"] > 1.5
            and r["close"] - r["resistance"] < 2 * r["atr"]
        )


class Retest(Strategy):
    name = "retest"
    description = (
        "Prior breakout followed by retest and bullish close above that fixed level"
    )

    def condition(self, f, p):
        r = f.iloc[-1]
        candidates = f.iloc[-6:-1]
        breakout = candidates[
            candidates["breakout"] & (candidates["relative_volume"] > 1.5)
        ]
        if breakout.empty:
            return False
        level = breakout.iloc[-1]["resistance"]
        return (
            r["low"] <= level + r["atr"] * 0.2
            and r["close"] > level
            and r["close"] > r["open"]
            and r["close"] > r["vwap"]
        )


class Trend(Strategy):
    name = "trend"
    description = "EMA20 crosses above EMA50 with ADX >=25 and close above VWAP"

    def condition(self, f, p):
        r, b = f.iloc[-1], f.iloc[-2]
        return (
            r["ema_20"] > r["ema_50"]
            and b["ema_20"] <= b["ema_50"]
            and r["adx"] >= 25
            and r["close"] > r["vwap"]
        )


class Pullback(Strategy):
    name = "pullback"
    description = "Bullish EMA trend, pullback to EMA20 then bullish reclaim"

    def condition(self, f, p):
        r = f.iloc[-1]
        return (
            r["ema_20"] > r["ema_50"]
            and r["adx"] >= 25
            and r["low"] <= r["ema_20"]
            and r["close"] > r["ema_20"]
            and r["close"] > r["open"]
        )


class VWAP(Strategy):
    name = "vwap"
    description = "Same-session VWAP reclaim with relative volume >1.2"

    def condition(self, f, p):
        r, b = f.iloc[-1], f.iloc[-2]
        return (
            r["timestamp"].tz_convert("Asia/Kolkata").date()
            == b["timestamp"].tz_convert("Asia/Kolkata").date()
            and b["close"] <= b["vwap"]
            and r["close"] > r["vwap"]
            and r["relative_volume"] > 1.2
        )


class Bollinger(Strategy):
    name = "bollinger"
    description = "Prior lower-quartile bandwidth, expanding band breakout with volume"

    def condition(self, f, p):
        r, b = f.iloc[-1], f.iloc[-2]
        return (
            b["bb_width"] <= f["bb_width"].iloc[-50:-1].quantile(0.25)
            and r["bb_width"] > b["bb_width"]
            and r["close"] > r["bb_upper"]
            and r["relative_volume"] > 1.2
        )


class ORB(Strategy):
    name = "orb"
    description = (
        "Completed 5/15/30-minute NSE opening range breakout and volume confirmation"
    )

    def condition(self, f, p):
        minutes = int(p.get("opening_minutes", 15))
        if minutes not in (5, 15, 30):
            raise ValueError("Opening range must be 5, 15 or 30 minutes")
        local = f["timestamp"].dt.tz_convert("Asia/Kolkata")
        r = f.iloc[-1]
        elapsed = local.dt.hour * 60 + local.dt.minute - 555
        seconds = int(
            (f["timestamp"].iloc[-1] - f["timestamp"].iloc[-2]).total_seconds()
        )
        session = local.dt.date == local.iloc[-1].date()
        opening = f[session & (elapsed >= 0) & (elapsed < minutes)]
        if (
            opening.empty
            or elapsed.iloc[-1] < minutes
            or seconds > minutes * 60
            or len(opening) * seconds < minutes * 60
        ):
            return False
        level = opening["high"].max()
        return (
            r["close"] > level
            and f.iloc[-2]["close"] <= level
            and r["relative_volume"] > 1.2
        )


class Momentum(Strategy):
    name = "momentum"
    description = "RSI 55-70, ADX >=25, ROC positive, volume-confirmed resistance break"

    def condition(self, f, p):
        r = f.iloc[-1]
        return (
            55 < r["rsi"] < 70
            and r["adx"] >= 25
            and r["roc"] > 0
            and r["relative_volume"] > 1.5
            and r["breakout"]
        )


class MeanReversion(Strategy):
    name = "mean_reversion"
    description = "Low-trend regime, z-score below -2, bullish lower-band rejection"

    def condition(self, f, p):
        r = f.iloc[-1]
        return (
            r["adx"] < 20
            and r["z_score"] < -2
            and r["low"] < r["bb_lower"]
            and r["close"] > r["open"]
        )


class SupportResistance(Strategy):
    name = "support_resistance"
    description = "Previous completed session high breakout with volume"

    def condition(self, f, p):
        dates = f["timestamp"].dt.tz_convert("Asia/Kolkata").dt.date
        prior = f[dates < dates.iloc[-1]]
        if prior.empty:
            return False
        last_day = prior["timestamp"].dt.tz_convert("Asia/Kolkata").dt.date.max()
        level = prior[
            prior["timestamp"].dt.tz_convert("Asia/Kolkata").dt.date == last_day
        ]["high"].max()
        return (
            f.iloc[-1]["close"] > level
            and f.iloc[-2]["close"] <= level
            and f.iloc[-1]["relative_volume"] > 1.2
        )


REGISTRY = {
    s.name: s
    for s in [
        Box(),
        Breakout(),
        Retest(),
        Trend(),
        Pullback(),
        VWAP(),
        Bollinger(),
        ORB(),
        Momentum(),
        MeanReversion(),
        SupportResistance(),
    ]
}
