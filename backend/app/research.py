from .core import DomainError
from .data import analysis_frame
from .indicators import compute, regime


def study(rows, horizon=5):
    if len(rows) < 60:
        raise DomainError("At least 60 actual candles required")
    f = compute(analysis_frame(rows))
    # Future returns belong ONLY to outcome analysis, never to strategy inputs.
    outcome = (f["close"].shift(-horizon) / f["close"] - 1) * 100
    events = {
        "breakout": f["breakout"],
        "breakdown": f["breakdown"],
        "vwap_reclaim": (f["close"] > f["vwap"])
        & (f["close"].shift() <= f["vwap"].shift()),
        "bollinger_squeeze": f["bb_width"] < f["bb_width"].rolling(50).quantile(0.25),
        "support_test": abs(f["low"] - f["support"]) < f["atr"] * 0.2,
    }
    results = []
    for name, mask in events.items():
        valid = outcome[mask].dropna()
        results.append(
            {
                "event": name,
                "observations": len(valid),
                "positive_outcome_pct": (
                    float((valid > 0).mean() * 100) if len(valid) else None
                ),
                "mean_forward_return_pct": float(valid.mean()) if len(valid) else None,
            }
        )
    distribution = {}
    for i in range(50, len(f)):
        label = regime(f.iloc[: i + 1])["regime"]
        distribution[label] = distribution.get(label, 0) + 1
    return {
        "horizon_bars": horizon,
        "events": results,
        "regime_distribution": distribution,
        "warnings": [
            "Descriptive, overlapping observations; not causal evidence or a profitability promise.",
            "Corporate-action adjustments, survivorship and calendar completeness require source review.",
        ],
    }
