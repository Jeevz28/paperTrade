"""Chronological daily research. Profiles are evidence, never promises of profit."""

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
from .core import DomainError, now, timestamp
from .data import analysis_frame
from .indicators import compute, regime
from .strategies import REGISTRY
from .backtesting import run
from .activity import record
from .bhavcopy import daily_times, prices_volume, IST

DAILY_STRATEGIES = tuple(n for n in REGISTRY if n not in {"vwap", "orb"})


def acceptable(result, minimum=5):
    m = result["metrics"]
    return (
        m["total_trades"] >= minimum
        and m["closed_trade_net_pnl_paise"] > 0
        and m["net_pnl_paise"] > 0
        and m["max_drawdown_pct"] <= 5
        and (m["profit_factor"] is None or m["profit_factor"] >= 1.2)
    )


def group_trades(result):
    groups = defaultdict(list)
    for trade in result["trades"]:
        groups[trade["regime"]].append(trade["net_pnl"])
    return {
        k: {
            "trades": len(v),
            "net_pnl_paise": sum(v),
            "expectancy_paise": sum(v) / len(v),
        }
        for k, v in groups.items()
    }


def validate_series(rows):
    if len(rows) < 600:
        raise DomainError(
            "At least 600 daily bars per security are required for training, validation and holdout"
        )
    if len(rows) > 1500:
        rows = rows[-1500:]
    for a, b in zip(rows, rows[1:]):
        if abs(b["open"] / a["close"] - 1) > 0.15:
            raise DomainError(
                "Price gap above 15%: review corporate actions before learning; raw bhavcopy is not adjusted"
            )
    return rows


def study(db, config, policy, progress=lambda n: None):
    source = db.rows(
        "SELECT b.*,i.* FROM bhav_securities b JOIN instruments i ON i.id=b.instrument_id WHERE b.instrument_id=? AND b.dataset_id=?",
        (config["instrument_id"], config["dataset_id"]),
    )
    if not source:
        raise DomainError("Choose a security from an imported NSE bhavcopy dataset")
    inst = source[0]
    rows = db.rows(
        "SELECT * FROM candles WHERE dataset_id=? AND instrument_id=? ORDER BY timestamp",
        (config["dataset_id"], config["instrument_id"]),
    )
    if config.get("train_until"):
        rows = [
            r
            for r in rows
            if timestamp(r["timestamp"]).astimezone(IST).date().isoformat()
            <= config["train_until"]
        ]
    rows = validate_series(rows)
    policy.check(rows[-1]["available_at"])
    costs = config["costs"]
    if costs["slippage_bps"] <= 0 or not any(
        costs[k] > 0 for k in costs if k not in {"slippage_bps", "gst_pct"}
    ):
        raise DomainError(
            "Learning requires positive slippage and at least one fee/tax assumption; configure realistic costs"
        )
    n = len(rows)
    a, b = n // 2, 3 * n // 4
    windows = [rows[:a], rows[a:b], rows[b:]]
    frames = [compute(analysis_frame(w)) for w in windows]
    stress = {
        k: (
            min(1000, v * 2)
            if k.endswith("_bps")
            else (v * 2 if k == "brokerage_paise" else v)
        )
        for k, v in costs.items()
    }
    candidates = []

    def simulate(index, name, atr, cost=costs):
        return run(
            windows[index],
            inst,
            name,
            config["initial_capital_paise"],
            config["risk"],
            cost,
            {"atr_stop": atr, "max_quantity": config["max_quantity"]},
            _computed=frames[index],
        )

    for j, name in enumerate(DAILY_STRATEGIES):
        for atr in (1.5, 2.0):
            train = simulate(0, name, atr)
            validation = simulate(1, name, atr)
            candidates.append(
                {
                    "strategy": name,
                    "atr_stop": atr,
                    "training": train["metrics"],
                    "validation": validation["metrics"],
                    "regimes": group_trades(validation),
                    "qualifies": acceptable(train) and acceptable(validation),
                }
            )
        progress(int((j + 1) / len(DAILY_STRATEGIES) * 65))
    # Freeze the two finalists for each regime BEFORE inspecting holdout results.
    finalists = {}
    labels = sorted({label for c in candidates for label in c["regimes"]})
    for label in labels:
        eligible = [
            c
            for c in candidates
            if c["qualifies"]
            and c["regimes"].get(label, {}).get("trades", 0) >= 3
            and c["regimes"][label]["net_pnl_paise"] > 0
        ]
        ranked = sorted(
            eligible,
            key=lambda c: (
                -c["regimes"][label]["expectancy_paise"],
                c["strategy"],
                c["atr_stop"],
            ),
        )
        # Alternatives must be different strategies, not different stops for one strategy.
        distinct = []
        for c in ranked:
            if c["strategy"] not in {x["strategy"] for x in distinct}:
                distinct.append(c)
        finalists[label] = distinct[:2]
    tested = {}
    winners = []
    for label, chosen in finalists.items():
        for c in chosen:
            key = (c["strategy"], c["atr_stop"])
            if key not in tested:
                test = simulate(2, *key)
                stressed = simulate(2, *key, cost=stress)
                tested[key] = (test, stressed)
            test, stressed = tested[key]
            group = group_trades(test).get(label, {"trades": 0, "net_pnl_paise": 0})
            stress_group = group_trades(stressed).get(
                label, {"trades": 0, "net_pnl_paise": 0}
            )
            passed = (
                acceptable(test)
                and acceptable(stressed)
                and group["trades"] >= 3
                and group["net_pnl_paise"] > 0
                and stress_group["trades"] >= 3
                and stress_group["net_pnl_paise"] > 0
            )
            winners.append(
                {
                    "regime": label,
                    "strategy": key[0],
                    "atr_stop": key[1],
                    "approved": passed,
                    "validation_score": c["regimes"][label]["expectancy_paise"],
                    "holdout": test["metrics"],
                    "stress": stressed["metrics"],
                    "regime_holdout": group,
                    "reason": (
                        "Passed holdout and doubled-cost checks"
                        if passed
                        else "Failed holdout, regime sample or cost-stress check"
                    ),
                }
            )
    progress(95)
    return {
        "timeframe": "1d",
        "isin": inst["isin"],
        "ticker": inst["ticker"],
        "series": inst["series"],
        "config": config,
        "candidates": candidates,
        "winners": winners,
        "approved": any(w["approved"] for w in winners),
        "trained_through": rows[-1]["available_at"],
        "windows": [
            {"start": w[0]["timestamp"], "end": w[-1]["available_at"], "bars": len(w)}
            for w in windows
        ],
        "method": "Disjoint chronological 50% training / 25% validation / 25% holdout; separate indicator warm-up in each; top two distinct strategies per regime frozen before holdout.",
        "limitations": "Raw unadjusted daily data. Statistical selection bias remains; small samples and passing holdout do not guarantee future profit. Open positions are marked, not force-closed.",
    }


def profile(db, profile_id):
    found = db.rows("SELECT * FROM learning_profiles WHERE id=?", (profile_id,))
    if not found:
        raise DomainError("Learning profile not found")
    p = found[0]
    p["evidence"] = json.loads(p.pop("payload"))
    return p


def validate_binding(db, p, account, instrument, max_quantity):
    evidence = p["evidence"]
    if not evidence["approved"]:
        raise DomainError("No strategy passed this profile's holdout checks")
    config = evidence["config"]
    if (
        json.loads(account["risk_json"]) != config["risk"]
        or json.loads(account["cost_json"]) != config["costs"]
    ):
        raise DomainError(
            "Account risk and costs must match the learned profile; retrain for different settings"
        )
    if (
        account["initial_capital"] != config["initial_capital_paise"]
        or max_quantity > config["max_quantity"]
    ):
        raise DomainError("Match training capital and do not exceed its quantity cap")
    if account["mode"] == "LIVE":
        if (
            instrument["kind"] != "SPOT"
            or instrument["provider_key"] != "NSE_EQ|" + evidence["isin"]
        ):
            raise DomainError(
                "Live SPOT instrument must have the same verified NSE_EQ ISIN as the profile"
            )
    elif (
        account["dataset_id"] != p["dataset_id"]
        or instrument["id"] != p["instrument_id"]
    ):
        raise DomainError("Replay must use the profile's source instrument and dataset")


def daily_context(db, rule, account, at):
    p = profile(db, json.loads(rule["parameters"]).get("learning_profile_id"))
    inst = db.rows("SELECT * FROM instruments WHERE id=?", (rule["instrument_id"],))[0]
    validate_binding(db, p, account, inst, rule["max_quantity"])
    if timestamp(at) <= timestamp(p["trained_through"]):
        raise DomainError("Profile cannot be used before its holdout period has ended")
    if timestamp(at) - timestamp(p["trained_through"]) > timedelta(days=90):
        raise DomainError(
            "Learning evidence is older than 90 days; import recent history and retrain"
        )
    rows = db.rows(
        "SELECT * FROM candles WHERE dataset_id=? AND instrument_id=? AND available_at<=? ORDER BY timestamp",
        (p["dataset_id"], p["instrument_id"], at),
    )
    if account["mode"] == "LIVE":
        cached = db.rows(
            "SELECT * FROM learning_daily_cache WHERE profile_id=?", (p["id"],)
        )
        if (
            not cached
            or timestamp(cached[0]["fetched_at"]).astimezone(IST).date()
            != timestamp(at).astimezone(IST).date()
        ):
            raise DomainError("Waiting for today's verified daily-history refresh")
        merged = {r["timestamp"]: r for r in rows}
        for row in json.loads(cached[0]["payload"]):
            if row["available_at"] <= at:
                merged[row["timestamp"]] = row
        rows = sorted(merged.values(), key=lambda r: r["timestamp"])
    if not rows or timestamp(at) - timestamp(rows[-1]["available_at"]) > timedelta(
        days=7
    ):
        raise DomainError("Daily price history is stale; no entry")
    recent = rows[-300:]
    if len(recent) < 200:
        raise DomainError("At least 200 completed daily bars are required")
    for a, b in zip(recent, recent[1:]):
        if abs(b["open"] / a["close"] - 1) > 0.15:
            raise DomainError(
                "Recent price discontinuity; review corporate actions before paper entries"
            )
    return p, recent


def learned_rank(p, rows, excluded):
    trend = regime(compute(analysis_frame(rows)))
    ranking = []
    for w in p["evidence"]["winners"]:
        matches = w["regime"] == trend["regime"]
        eligible = matches and w["approved"] and w["strategy"] not in excluded
        ranking.append(
            {
                "strategy": w["strategy"],
                "atr_stop": w["atr_stop"],
                "eligible": eligible,
                "score": w["validation_score"],
                "trades": w["regime_holdout"]["trades"],
                "net_pnl_paise": w["regime_holdout"]["net_pnl_paise"],
                "drawdown_pct": w["holdout"]["max_drawdown_pct"],
                "regime": w["regime"],
                "reason": (
                    "Loss quarantine"
                    if w["strategy"] in excluded
                    else (w["reason"] if matches else "Different daily trend")
                ),
            }
        )
    return (
        sorted(ranking, key=lambda x: (-x["score"], x["strategy"], x["atr_stop"])),
        trend,
    )


class LearningJobs:
    def __init__(self, db, policy, provider):
        self.db, self.policy, self.provider = db, policy, provider
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="daily-learning"
        )
        self.refresher = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="daily-history"
        )
        self.refresh_lock = threading.Lock()
        self.refresh_times = {}
        with db.connect(write=True) as c:
            c.execute(
                "UPDATE learning_jobs SET status='INTERRUPTED',error='Process restarted' WHERE status IN ('QUEUED','RUNNING')"
            )

    def submit(self, config):
        with self.db.connect(write=True) as c:
            if (
                c.execute(
                    "SELECT COUNT(*) FROM learning_jobs WHERE status IN ('QUEUED','RUNNING')"
                ).fetchone()[0]
                >= 3
            ):
                raise DomainError("Learning queue full")
            id = c.execute(
                "INSERT INTO learning_jobs(created_at,status,parameters) VALUES(?,'QUEUED',?)",
                (now().isoformat(), json.dumps(config)),
            ).lastrowid
        self.executor.submit(self._run, id, config)
        return {"id": id, "status": "QUEUED"}

    def _run(self, id, config):
        def progress(n):
            with self.db.connect(write=True) as c:
                c.execute(
                    "UPDATE learning_jobs SET progress=?,status='RUNNING' WHERE id=?",
                    (n, id),
                )

        try:
            progress(1)
            result = study(self.db, config, self.policy, progress)
            with self.db.connect(write=True) as c:
                pid = c.execute(
                    "INSERT INTO learning_profiles(job_id,dataset_id,instrument_id,created_at,trained_through,payload) VALUES(?,?,?,?,?,?)",
                    (
                        id,
                        config["dataset_id"],
                        config["instrument_id"],
                        now().isoformat(),
                        result["trained_through"],
                        json.dumps(result),
                    ),
                ).lastrowid
                c.execute(
                    "UPDATE learning_jobs SET progress=100,status='COMPLETED',result=? WHERE id=?",
                    (
                        json.dumps({"profile_id": pid, "approved": result["approved"]}),
                        id,
                    ),
                )
            record(
                self.db,
                "learning",
                "COMPLETED",
                {"profile_id": pid, "approved": result["approved"]},
            )
        except Exception as e:
            reason = str(e) if isinstance(e, DomainError) else type(e).__name__
            with self.db.connect(write=True) as c:
                c.execute(
                    "UPDATE learning_jobs SET status='FAILED',error=? WHERE id=?",
                    (reason, id),
                )

    def refresh_for_account(self, account_id):
        rules = self.db.rows(
            "SELECT parameters FROM automation_rules WHERE account_id=? AND strategy='learned_daily' AND enabled=1",
            (account_id,),
        )
        if not rules:
            return
        pid = json.loads(rules[0]["parameters"])["learning_profile_id"]
        with self.refresh_lock:
            last = self.refresh_times.get(pid)
            if last and (now() - last).total_seconds() < 600:
                return
            self.refresh_times[pid] = now()
        self.refresher.submit(self._refresh, pid)

    def _refresh(self, pid):
        try:
            self.policy.require_live()
            p = profile(self.db, pid)
            current = now()
            cached = self.db.rows(
                "SELECT fetched_at FROM learning_daily_cache WHERE profile_id=?", (pid,)
            )
            if (
                cached
                and timestamp(cached[0]["fetched_at"]).astimezone(IST).date()
                == current.astimezone(IST).date()
            ):
                return
            end = current.astimezone(IST).date() - timedelta(days=1)
            raw = self.provider.get_candles(
                instrument_key="NSE_EQ|" + p["evidence"]["isin"],
                timeframe="1d",
                start=(end - timedelta(days=370)).isoformat(),
                end=end.isoformat(),
            )
            rows = []
            seen = set()
            for r in raw:
                day = timestamp(r["timestamp"]).astimezone(IST).date()
                if day > end or day < end - timedelta(days=370) or day in seen:
                    raise DomainError(
                        "Daily provider returned duplicate or out-of-range dates"
                    )
                seen.add(day)
                start, available = daily_times(day.isoformat())
                prices, volume = prices_volume(
                    [r[k] for k in ("open", "high", "low", "close")], r["volume"]
                )
                rows.append(
                    dict(
                        timestamp=start,
                        available_at=available,
                        timeframe="1d",
                        open=prices[0],
                        high=prices[1],
                        low=prices[2],
                        close=prices[3],
                        volume=volume,
                    )
                )
            if not rows:
                raise DomainError("Provider returned no completed daily prices")
            rows.sort(key=lambda r: r["timestamp"])
            # Mismatched overlap may represent adjusted prices or the wrong security.
            old = {
                r["timestamp"]: r
                for r in self.db.rows(
                    "SELECT * FROM candles WHERE dataset_id=? AND instrument_id=?",
                    (p["dataset_id"], p["instrument_id"]),
                )
            }
            for r in rows:
                if r["timestamp"] in old and any(
                    abs(r[k] - old[r["timestamp"]][k]) > 1
                    for k in ("open", "high", "low", "close")
                ):
                    raise DomainError(
                        "Provider and CSV prices disagree; review adjustments before live use"
                    )
            with self.db.connect(write=True) as c:
                c.execute(
                    "INSERT INTO learning_daily_cache VALUES(?,?,?) ON CONFLICT(profile_id) DO UPDATE SET fetched_at=excluded.fetched_at,payload=excluded.payload",
                    (pid, current.isoformat(), json.dumps(rows)),
                )
            record(
                self.db,
                "learning",
                "DAILY_HISTORY_REFRESHED",
                {"profile_id": pid, "bars": len(rows)},
            )
        except Exception as e:
            record(
                self.db,
                "learning",
                "DAILY_REFRESH_BLOCKED",
                {
                    "profile_id": pid,
                    "reason": (
                        str(e) if isinstance(e, DomainError) else type(e).__name__
                    ),
                },
            )

    def shutdown(self):
        self.refresher.shutdown(wait=True, cancel_futures=True)
        self.executor.shutdown(wait=True, cancel_futures=True)
