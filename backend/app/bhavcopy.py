"""Streaming NSE CM CSV import. Source bars remain immutable daily observations."""

import csv
import hashlib
import io
import json
import re
import sqlite3
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from .core import DomainError, UTC, money, now

IST = ZoneInfo("Asia/Kolkata")
MAX_BYTES = 1_000_000_000
MAX_ROWS = 5_000_000
REQUIRED = {
    "TradDt",
    "Sgmt",
    "Src",
    "FinInstrmTp",
    "FinInstrmId",
    "ISIN",
    "TckrSymb",
    "SctySrs",
    "OpnPric",
    "HghPric",
    "LwPric",
    "ClsPric",
    "TtlTradgVol",
}


def daily_times(day):
    d = date.fromisoformat(day)
    # Conservative publication assumption: complete daily prices become visible
    # at the following local midnight, never at the session's open.
    start = datetime.combine(d, time(9, 15), IST).astimezone(UTC)
    available = datetime.combine(d + timedelta(days=1), time(), IST).astimezone(UTC)
    return start.isoformat(), available.isoformat()


def prices_volume(values, volume):
    prices = [money(v) for v in values]
    o, h, l, close = prices
    if min(prices) <= 0 or h < max(o, close, l) or l > min(o, close, h):
        raise DomainError("Invalid or nonpositive OHLC")
    v = Decimal(str(volume))
    if not v.is_finite() or v < 0 or v != int(v) or v > 10**15:
        raise DomainError("Invalid volume")
    return prices, int(v)


def import_bhavcopy(db, file, filename, source, policy):
    if not source.strip():
        raise DomainError("Data provenance is required")
    file.seek(0, 2)
    if file.tell() > MAX_BYTES:
        raise DomainError("NSE CSV limit is 1 GB")
    file.seek(0)
    digest = hashlib.sha256()
    while block := file.read(1024 * 1024):
        digest.update(block)
    file.seek(0)
    sha = digest.hexdigest()
    reader_file = io.TextIOWrapper(file, encoding="utf-8-sig", newline="")
    count = skipped = duplicates = 0
    row_number = 1
    warnings = [
        "Daily raw prices; corporate actions are not adjusted.",
        "Only NSE CM STK EQ series is imported; other series are counted as excluded.",
        "Publication assumed at following IST midnight; session open assumed 09:15 IST.",
        "Historical instruments use one-paise precision and one-unit research lots, not verified live contract metadata.",
    ]
    try:
        reader = csv.DictReader(reader_file)
        if not REQUIRED.issubset(reader.fieldnames or []):
            raise DomainError(
                "Missing NSE columns: "
                + ", ".join(sorted(REQUIRED - set(reader.fieldnames or [])))
            )
        with db.connect(write=True) as c:
            dataset = c.execute(
                "INSERT INTO datasets(name,sha256,source,created_at,timeframe,rows,warnings) VALUES(?,?,?,?,'1d',0,'[]')",
                (filename, sha, source, now().isoformat()),
            ).lastrowid
            instruments = {}
            dates = {}
            batch = []
            # SQLite uniqueness handles unsorted input without retaining millions of keys.
            for row_number, r in enumerate(reader, 2):
                if row_number > MAX_ROWS + 1:
                    raise DomainError("NSE CSV limit is 5 million source rows")
                if None in r:
                    raise DomainError("CSV row has more fields than its header")
                if (r["Sgmt"], r["Src"], r["FinInstrmTp"], r["SctySrs"]) != (
                    "CM",
                    "NSE",
                    "STK",
                    "EQ",
                ):
                    skipped += 1
                    continue
                isin, ticker = r["ISIN"].strip(), r["TckrSymb"].strip()
                if (
                    not re.fullmatch(r"IN[A-Z0-9]{10}", isin)
                    or not ticker
                    or not r["FinInstrmId"].strip()
                ):
                    raise DomainError("Missing or invalid security identity")
                if r["TradDt"] not in dates:
                    dates[r["TradDt"]] = daily_times(r["TradDt"])
                    policy.check(dates[r["TradDt"]][1])
                start, available = dates[r["TradDt"]]
                p, volume = prices_volume(
                    [r[k] for k in ("OpnPric", "HghPric", "LwPric", "ClsPric")],
                    r["TtlTradgVol"],
                )
                if isin not in instruments:
                    inst = c.execute(
                        "INSERT INTO instruments(symbol,kind,lot_size,tick_paise,metadata_source,created_at) VALUES(?,'SPOT',1,1,?,?)",
                        (
                            f"{ticker}:EQ:{isin}:D{dataset}",
                            source
                            + " | historical precision; verify live metadata separately",
                            now().isoformat(),
                        ),
                    ).lastrowid
                    instruments[isin] = inst
                    c.execute(
                        "INSERT INTO bhav_securities VALUES(?,?,?,'EQ',?)",
                        (inst, dataset, isin, ticker),
                    )
                batch.append(
                    (dataset, instruments[isin], start, available, "1d", *p, volume)
                )
                if len(batch) >= 2000:
                    c.executemany(
                        "INSERT INTO candles(dataset_id,instrument_id,timestamp,available_at,timeframe,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        batch,
                    )
                    count += len(batch)
                    batch.clear()
            c.executemany(
                "INSERT INTO candles(dataset_id,instrument_id,timestamp,available_at,timeframe,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?,?,?)",
                batch,
            )
            count += len(batch)
            if not count:
                raise DomainError("No eligible NSE CM EQ records")
            warnings.append(
                f"Excluded {skipped} non-EQ/non-CM source rows; imported {len(instruments)} securities."
            )
            c.execute(
                "UPDATE datasets SET rows=?,warnings=? WHERE id=?",
                (count, json.dumps(warnings), dataset),
            )
            c.execute(
                "INSERT INTO data_quality(timestamp,filename,status,details) VALUES(?,?,'ACCEPTED',?)",
                (now().isoformat(), filename, json.dumps(warnings)),
            )
        return {
            "id": dataset,
            "rows": count,
            "securities": len(instruments),
            "excluded": skipped,
            "sha256": sha,
            "warnings": warnings,
        }
    except Exception as e:
        reason = (
            "Duplicate file or repeated date/ISIN/series; import rolled back"
            if isinstance(e, sqlite3.IntegrityError)
            else str(e)
        )
        with db.connect(write=True) as c:
            c.execute(
                "INSERT INTO data_quality(timestamp,filename,status,details) VALUES(?,?,'REJECTED',?)",
                (
                    now().isoformat(),
                    filename,
                    json.dumps({"row": row_number, "reason": reason}),
                ),
            )
        raise DomainError(f"NSE import rejected near row {row_number}: {reason}") from e
    finally:
        reader_file.detach()
