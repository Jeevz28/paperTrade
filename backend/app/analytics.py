import math
from collections import defaultdict
from zoneinfo import ZoneInfo
from .core import timestamp


def summarize(trades):
    n = len(trades)
    if not n:
        return {
            "trades": 0,
            "net_pnl_paise": 0,
            "win_rate": None,
            "win_rate_95pct_interval": None,
            "expectancy_paise": None,
        }
    values = [t["net_pnl"] for t in trades]
    wins = sum(x > 0 for x in values)
    p = wins / n
    z = 1.959963984540054
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return {
        "trades": n,
        "net_pnl_paise": sum(values),
        "win_rate": p,
        "win_rate_95pct_interval": [max(0, center - radius), min(1, center + radius)],
        "expectancy_paise": sum(values) / n,
        "mean_holding_minutes": sum(
            (timestamp(t["exit_time"]) - timestamp(t["entry_time"])).total_seconds()
            / 60
            for t in trades
        )
        / n,
    }


def stratify(trades):
    dimensions = {
        name: defaultdict(list)
        for name in ("strategy", "regime", "entry_hour_IST", "weekday", "instrument")
    }
    for t in trades:
        dt = timestamp(t["entry_time"]).astimezone(ZoneInfo("Asia/Kolkata"))
        labels = {
            "strategy": t.get("strategy", "unknown"),
            "regime": t.get("regime", "unknown"),
            "entry_hour_IST": f"{dt.hour:02}:00",
            "weekday": dt.strftime("%A"),
            "instrument": str(t.get("symbol", t.get("instrument_id", "unknown"))),
        }
        for name, key in labels.items():
            dimensions[name][key].append(t)
    return {
        "overall": summarize(trades),
        "groups": {
            name: [
                {"group": key, **summarize(items)}
                for key, items in sorted(groups.items())
            ]
            for name, groups in dimensions.items()
        },
        "warning": "Wilson intervals assume independent trials. Overlapping or serially dependent trades reduce effective sample size; this is descriptive, not a profitability guarantee.",
    }
