import numpy as np
import pandas as pd


def wilder(s, n):
    """Wilder recursion with an arithmetic seed, preserving warm-up NaNs."""
    out = pd.Series(np.nan, index=s.index, dtype=float)
    valid = s.dropna()
    if len(valid) < n:
        return out
    start = s.index.get_loc(valid.index[n - 1])
    out.iloc[start] = valid.iloc[:n].mean()
    for i in range(start + 1, len(s)):
        out.iloc[i] = (out.iloc[i - 1] * (n - 1) + s.iloc[i]) / n
    return out


def compute(frame, n=14, fast=12, slow=26, band_period=20, band_std=2.0, swing=3):
    if min(n, fast, slow, band_period, swing) < 1:
        raise ValueError("Periods must be positive")
    f = frame.copy().reset_index(drop=True)
    c, h, l, v = f["close"], f["high"], f["low"], f["volume"]
    for period in sorted({fast, slow, 20, 50}):
        f[f"ema_{period}"] = c.ewm(span=period, adjust=False, min_periods=period).mean()
    f["sma"] = c.rolling(band_period).mean()
    weights = np.arange(1, n + 1)
    f["wma"] = c.rolling(n).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True
    )
    typical = (h + l + c) / 3
    sessions = (
        pd.to_datetime(f["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    )
    denom = v.groupby(sessions).cumsum().replace(0, np.nan)
    f["vwap"] = (typical * v).groupby(sessions).cumsum() / denom
    f["anchored_vwap"] = (typical * v).cumsum() / v.cumsum().replace(0, np.nan)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(
        axis=1
    )
    f["atr"] = wilder(tr, n)
    delta = c.diff()
    gain = wilder(delta.clip(lower=0), n)
    loss = wilder(-delta.clip(upper=0), n)
    f["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    f.loc[(loss == 0) & (gain > 0), "rsi"] = 100
    f.loc[(loss == 0) & (gain == 0), "rsi"] = 50
    f["macd"] = f[f"ema_{fast}"] - f[f"ema_{slow}"]
    f["macd_signal"] = f["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    f["macd_hist"] = f["macd"] - f["macd_signal"]
    f["stochastic"] = (
        100
        * (c - l.rolling(n).min())
        / (h.rolling(n).max() - l.rolling(n).min()).replace(0, np.nan)
    )
    f["stochastic_d"] = f["stochastic"].rolling(3).mean()
    f["roc"] = c.pct_change(n) * 100
    std = c.rolling(band_period).std(ddof=0)
    f["bb_upper"] = f["sma"] + band_std * std
    f["bb_lower"] = f["sma"] - band_std * std
    f["bb_width"] = (f["bb_upper"] - f["bb_lower"]) / f["sma"]
    f["z_score"] = (c - f["sma"]) / std.replace(0, np.nan)
    f["historical_volatility"] = np.log(c / c.shift()).rolling(n).std(ddof=1)
    f["volume_sma"] = v.rolling(20).mean()
    f["relative_volume"] = v / f["volume_sma"].shift().replace(0, np.nan)
    f["volume_breakout"] = f["relative_volume"] > 1.5
    f["obv"] = (np.sign(c.diff()).fillna(0) * v).cumsum()
    up = h.diff()
    down = -l.diff()
    plus = up.where((up > down) & (up > 0), 0)
    minus = down.where((down > up) & (down > 0), 0)
    f["di_plus"] = 100 * wilder(plus, n) / f["atr"].replace(0, np.nan)
    f["di_minus"] = 100 * wilder(minus, n) / f["atr"].replace(0, np.nan)
    dx = (
        100
        * (f["di_plus"] - f["di_minus"]).abs()
        / (f["di_plus"] + f["di_minus"]).replace(0, np.nan)
    )
    f["adx"] = wilder(dx, n)
    # A centred pivot is exposed only swing bars AFTER the candidate pivot.
    candidate_h = h.shift(swing)
    candidate_l = l.shift(swing)
    f["swing_high"] = candidate_h.where(
        candidate_h == h.rolling(2 * swing + 1).max()
    ).ffill()
    f["swing_low"] = candidate_l.where(
        candidate_l == l.rolling(2 * swing + 1).min()
    ).ffill()
    f["resistance"] = h.rolling(20).max().shift()
    f["support"] = l.rolling(20).min().shift()
    f["breakout"] = c > f["resistance"]
    f["breakdown"] = c < f["support"]
    f["higher_high"] = f["swing_high"] > f["swing_high"].shift()
    f["lower_high"] = f["swing_high"] < f["swing_high"].shift()
    f["higher_low"] = f["swing_low"] > f["swing_low"].shift()
    f["lower_low"] = f["swing_low"] < f["swing_low"].shift()
    upper = (h + l) / 2 + 3 * f["atr"]
    lower = (h + l) / 2 - 3 * f["atr"]
    st = pd.Series(np.nan, index=f.index)
    direction = 1
    for i in range(len(f)):
        if pd.isna(upper.iloc[i]):
            continue
        if i and pd.notna(st.iloc[i - 1]):
            if c.iloc[i - 1] <= upper.iloc[i - 1]:
                upper.iloc[i] = min(upper.iloc[i], upper.iloc[i - 1])
            if c.iloc[i - 1] >= lower.iloc[i - 1]:
                lower.iloc[i] = max(lower.iloc[i], lower.iloc[i - 1])
            if c.iloc[i] > upper.iloc[i - 1]:
                direction = 1
            elif c.iloc[i] < lower.iloc[i - 1]:
                direction = -1
        st.iloc[i] = lower.iloc[i] if direction == 1 else upper.iloc[i]
    f["supertrend"] = st
    return f.replace([np.inf, -np.inf], np.nan)


def regime(f):
    if len(f) < 50 or pd.isna(f.iloc[-1].get("adx")):
        return {
            "regime": "UNCERTAIN",
            "confidence": 0,
            "reasons": ["Insufficient indicator warm-up"],
        }
    r = f.iloc[-1]
    atr_pct = r["atr"] / r["close"]
    baseline = (f["atr"] / f["close"]).iloc[-50:-1].median()
    if r["breakout"] and r["relative_volume"] > 1.2:
        label = "BREAKOUT"
    elif r["breakdown"] and r["relative_volume"] > 1.2:
        label = "BREAKDOWN"
    elif atr_pct > baseline * 1.5:
        label = "HIGH_VOLATILITY"
    elif atr_pct < baseline * 0.65:
        label = "LOW_VOLATILITY"
    elif r["adx"] < 20:
        label = "SIDEWAYS"
    else:
        label = ("STRONG_" if r["adx"] >= 25 else "WEAK_") + (
            "BULLISH" if r["ema_20"] > r["ema_50"] else "BEARISH"
        )
    return {
        "regime": label,
        "confidence": min(0.9, 0.4 + abs(float(r["adx"]) - 20) / 100),
        "reasons": [
            f"ADX {r['adx']:.2f}",
            f"EMA20 {r['ema_20']:.2f}; EMA50 {r['ema_50']:.2f}",
            f"ATR/close {atr_pct:.4f}",
        ],
        "confidence_meaning": "Heuristic strength, not a calibrated probability",
    }
