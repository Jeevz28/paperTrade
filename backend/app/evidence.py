import hashlib
import io
import json
import sqlite3
from decimal import InvalidOperation
from datetime import date
import pandas as pd
from .core import DomainError, timestamp, money, now


def import_evidence(db, content, kind, source, policy):
    try:
        return _import_evidence(db, content, kind, source, policy)
    except DomainError:
        raise
    except (
        ValueError,
        TypeError,
        OverflowError,
        InvalidOperation,
        sqlite3.IntegrityError,
    ) as e:
        raise DomainError(
            "Invalid evidence values, duplicate records or constraints"
        ) from e


def _import_evidence(db, content, kind, source, policy):
    if len(content) > 5_000_000 or not source.strip():
        raise DomainError("Source required; evidence limit 5 MB")
    tables = {
        "delivery": (
            "delivery_evidence",
            (
                "instrument_id",
                "underlying_id",
                "effective_at",
                "available_at",
                "delivery_at",
                "reference_price",
                "fees",
            ),
        ),
        "margin": (
            "margin_evidence",
            (
                "instrument_id",
                "available_at",
                "valid_from",
                "valid_until",
                "initial_per_lot",
                "maintenance_per_lot",
            ),
        ),
        "settlement": (
            "settlement_evidence",
            ("instrument_id", "effective_at", "available_at", "price", "final"),
        ),
        "option_history": (
            "option_history",
            ("instrument_id", "timestamp", "iv", "open_interest"),
        ),
        "calendar": ("session_calendar", ("session_date", "opens_at", "closes_at")),
    }
    if kind not in tables:
        raise DomainError("Unknown evidence type")
    table, fields = tables[kind]
    try:
        frame = pd.read_csv(io.BytesIO(content))
    except Exception as e:
        raise DomainError("Invalid evidence CSV") from e
    if not set(fields).issubset(frame.columns) or not 1 <= len(frame) <= 10000:
        raise DomainError("Required columns: " + ",".join(fields))
    sha = hashlib.sha256(content).hexdigest()
    provenance = json.dumps({"source": source, "sha256": sha})
    records = []
    for raw in frame.to_dict("records"):
        r = {k: raw[k] for k in fields}
        for key in (
            "available_at",
            "valid_from",
            "valid_until",
            "effective_at",
            "timestamp",
            "opens_at",
            "closes_at",
            "delivery_at",
        ):
            if key in r:
                r[key] = timestamp(r[key]).isoformat()
        policy.check(r.get("available_at", r.get("timestamp", r.get("closes_at"))))
        if "instrument_id" in r:
            if int(r["instrument_id"]) != float(r["instrument_id"]):
                raise DomainError("Instrument ID must be integer")
            insts = db.rows(
                "SELECT * FROM instruments WHERE id=?", (int(r["instrument_id"]),)
            )
            if not insts:
                raise DomainError("Unknown instrument")
            inst = insts[0]
            r["instrument_id"] = inst["id"]
            if kind == "margin" and inst["kind"] != "FUTURE":
                raise DomainError("Margin evidence requires a futures contract")
            if kind == "option_history" and inst["kind"] not in ("CALL", "PUT"):
                raise DomainError("Option history requires an option")
        if kind == "delivery":
            from .risk import day

            underlying = db.rows(
                "SELECT * FROM instruments WHERE id=? AND kind='SPOT'",
                (r["underlying_id"],),
            )
            if (
                not underlying
                or inst["settlement"] != "PHYSICAL"
                or inst["kind"] not in ("FUTURE", "CALL", "PUT")
                or inst["underlying"] != underlying[0]["symbol"]
            ):
                raise DomainError(
                    "Delivery requires a physical stock derivative and matching registered stock"
                )
            if (
                day(r["effective_at"]) < inst["expiry"]
                or r["available_at"] < r["effective_at"]
                or r["delivery_at"] < r["effective_at"]
            ):
                raise DomainError("Invalid delivery chronology")
            r["reference_price"] = money(r["reference_price"])
            r["fees"] = money(r["fees"])
            r["underlying_id"] = underlying[0]["id"]
        elif kind == "margin":
            for key in ("initial_per_lot", "maintenance_per_lot"):
                r[key] = money(r[key])
            if (
                r["valid_from"] >= r["valid_until"]
                or r["available_at"] > r["valid_until"]
            ):
                raise DomainError("Invalid margin validity")
        elif kind == "settlement":
            r["price"] = money(r["price"])
            if r["final"] not in (0, 1) or r["available_at"] < r["effective_at"]:
                raise DomainError(
                    "Invalid settlement publication chronology or final flag"
                )
            r["final"] = int(r["final"])
            if inst["kind"] not in ("FUTURE", "CALL", "PUT"):
                raise DomainError("Settlement requires a derivative")
        elif kind == "option_history":
            if (
                not 0 < float(r["iv"]) <= 5
                or float(r["open_interest"]) < 0
                or int(r["open_interest"]) != float(r["open_interest"])
            ):
                raise DomainError(
                    "IV uses decimal units; OI must be nonnegative integer"
                )
            r["iv"] = float(r["iv"])
            r["open_interest"] = int(r["open_interest"])
        else:
            r["session_date"] = date.fromisoformat(str(r["session_date"])).isoformat()
            if r["opens_at"] >= r["closes_at"]:
                raise DomainError("Invalid session")
        records.append(tuple(r[k] for k in fields) + (provenance,))
    with db.connect(write=True) as c:
        c.executemany(
            f"INSERT INTO {table}("
            + ",".join(fields)
            + ",source) VALUES("
            + ",".join("?" for _ in range(len(fields) + 1))
            + ")",
            records,
        )
        c.execute(
            "INSERT INTO data_quality(timestamp,filename,status,details) VALUES(?,?,?,?)",
            (
                now().isoformat(),
                kind + ".csv",
                "ACCEPTED",
                json.dumps({"records": len(records), "sha256": sha, "source": source}),
            ),
        )
    return {"rows": len(records), "sha256": sha, "kind": kind}


def option_series(db, instrument_id, as_of):
    rows = db.rows(
        "SELECT * FROM option_history WHERE instrument_id=? AND timestamp<=? ORDER BY timestamp",
        (instrument_id, as_of),
    )
    if not rows:
        return {
            "rows": [],
            "iv_percentile": None,
            "oi_change": None,
            "reason": "INSUFFICIENT OPTION DATA",
        }
    prior = rows[:-1]
    latest = rows[-1]
    return {
        "rows": rows,
        "iv_percentile": (
            100 * sum(x["iv"] <= latest["iv"] for x in prior) / len(prior)
            if len(prior) >= 20
            else None
        ),
        "oi_change": (
            latest["open_interest"] - prior[-1]["open_interest"] if prior else None
        ),
        "history_observations": len(prior),
        "reason": (
            None
            if len(prior) >= 20
            else "At least 20 preceding observations required for percentile"
        ),
    }
