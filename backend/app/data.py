from datetime import timedelta
import hashlib
import io
import json
import math
import sqlite3
import pandas as pd
from .core import DomainError, TIMEFRAMES, money, now, timestamp


def create_instrument(db, item):
    from .schemas import InstrumentInput

    v = InstrumentInput.model_validate(item)
    if v.kind in ("FUTURE", "CALL", "PUT") and not v.expiry:
        raise DomainError("Derivative contracts require expiry")
    if v.kind in ("CALL", "PUT") and (not v.strike or not v.underlying):
        raise DomainError("Options require strike and underlying")
    with db.connect(write=True) as c:
        try:
            cur = c.execute(
                "INSERT INTO instruments(symbol,kind,lot_size,tick_paise,expiry,strike_paise,underlying,settlement,provider_key,metadata_source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    v.symbol,
                    v.kind,
                    v.lot_size,
                    money(v.tick_size),
                    v.expiry.isoformat() if v.expiry else None,
                    money(v.strike) if v.strike else None,
                    v.underlying,
                    v.settlement,
                    v.provider_key,
                    v.metadata_source,
                    now().isoformat(),
                ),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError as e:
            raise DomainError("Instrument already exists or invalid metadata") from e


def import_data(db, content, filename, timeframe, source, policy):
    if timeframe not in TIMEFRAMES:
        raise DomainError("Unsupported timeframe")
    if not source.strip():
        raise DomainError("A legitimate data source / provenance is required")
    if len(content) > 20_000_000:
        raise DomainError("Import limit is 20 MB")
    warnings, errors, records = [], [], []
    try:
        frame = (
            pd.read_parquet(io.BytesIO(content))
            if filename.lower().endswith(".parquet")
            else pd.read_csv(io.BytesIO(content))
        )
        required = {"timestamp", "symbol", "open", "high", "low", "close", "volume"}
        if not required.issubset(frame.columns):
            raise DomainError("Required columns: " + ", ".join(sorted(required)))
        if len(frame) == 0 or len(frame) > 100_000:
            raise DomainError("Import must contain 1 to 100,000 rows")
        instruments = {x["symbol"]: x for x in db.rows("SELECT * FROM instruments")}
        seen, last = set(), {}
        for i, r in enumerate(frame.to_dict("records")):
            try:
                symbol = str(r["symbol"])
                inst = instruments.get(symbol)
                if not inst:
                    raise DomainError(
                        f"Register instrument {symbol} and its contract specifications first"
                    )
                dt = timestamp(r["timestamp"])
                available = dt + timedelta(seconds=TIMEFRAMES[timeframe])
                policy.check(available.isoformat())
                if (
                    inst["expiry"]
                    and dt.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))
                    .date()
                    .isoformat()
                    > inst["expiry"]
                ):
                    raise DomainError("Candle after contract expiry")
                if (
                    "expiry" in r
                    and pd.notna(r["expiry"])
                    and str(r["expiry"])[:10] != inst["expiry"]
                ):
                    raise DomainError("Expiry does not match registered contract")
                if (
                    "strike" in r
                    and pd.notna(r["strike"])
                    and money(r["strike"]) != inst["strike_paise"]
                ):
                    raise DomainError("Strike does not match registered contract")
                if (
                    "option_type" in r
                    and pd.notna(r["option_type"])
                    and {"CE": "CALL", "PE": "PUT"}.get(
                        str(r["option_type"]), str(r["option_type"])
                    )
                    != inst["kind"]
                ):
                    raise DomainError("Option type does not match contract")
                prices = [money(r[k]) for k in ("open", "high", "low", "close")]
                o, h, l, cl = prices
                if min(prices) <= 0 or h < max(o, cl, l) or l > min(o, cl, h):
                    raise DomainError("Invalid OHLC relationship or nonpositive price")
                if any(p % inst["tick_paise"] for p in prices):
                    raise DomainError("Price is not a multiple of instrument tick size")
                vol = float(r["volume"])
                if not math.isfinite(vol) or vol < 0 or vol != int(vol):
                    raise DomainError("Volume must be a nonnegative integer")
                oi = r.get("open_interest")
                if (
                    oi is not None
                    and pd.notna(oi)
                    and (
                        not math.isfinite(float(oi))
                        or float(oi) < 0
                        or float(oi) != int(oi)
                    )
                ):
                    raise DomainError("Invalid open interest")
                key = (symbol, dt)
                if key in seen:
                    raise DomainError("Duplicate candle")
                seen.add(key)
                records.append(
                    (
                        inst["id"],
                        dt.isoformat(),
                        available.isoformat(),
                        timeframe,
                        *prices,
                        int(vol),
                        int(oi) if oi is not None and pd.notna(oi) else None,
                    )
                )
            except (ValueError, TypeError, OverflowError) as e:
                errors.append({"row": i + 2, "reason": str(e)})
        for r in sorted(records, key=lambda x: (x[0], x[1])):
            previous = last.get(r[0])
            if previous:
                a, b = timestamp(previous[1]), timestamp(r[1])
                if (
                    a.date() == b.date()
                    and (b - a).total_seconds() > TIMEFRAMES[timeframe]
                ):
                    warnings.append(
                        {
                            "timestamp": r[1],
                            "reason": "Intraday gap; exchange calendar not inferred",
                        }
                    )
                if abs(r[4] / previous[7] - 1) > 0.15:
                    warnings.append(
                        {
                            "timestamp": r[1],
                            "reason": "Price gap above 15%; inspect corporate actions",
                        }
                    )
            last[r[0]] = r
    except Exception as e:
        errors.append({"row": 0, "reason": str(e)})
    if errors:
        with db.connect(write=True) as c:
            c.execute(
                "INSERT INTO data_quality(timestamp,filename,status,details) VALUES(?,?,?,?)",
                (now().isoformat(), filename, "REJECTED", json.dumps(errors[:500])),
            )
        raise DomainError(
            json.dumps(
                {
                    "message": "Entire import rejected; no rows discarded silently",
                    "errors": errors[:100],
                }
            )
        )
    try:
        with db.connect(write=True) as c:
            digest = hashlib.sha256(content).hexdigest()
            cur = c.execute(
                "INSERT INTO datasets(name,sha256,source,created_at,timeframe,rows,warnings) VALUES(?,?,?,?,?,?,?)",
                (
                    filename,
                    digest,
                    source,
                    now().isoformat(),
                    timeframe,
                    len(records),
                    json.dumps(warnings),
                ),
            )
            dataset = cur.lastrowid
            c.executemany(
                "INSERT INTO candles(dataset_id,instrument_id,timestamp,available_at,timeframe,open,high,low,close,volume,open_interest) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [(dataset, *r) for r in records],
            )
            c.execute(
                "INSERT INTO data_quality(timestamp,filename,status,details) VALUES(?,?,?,?)",
                (now().isoformat(), filename, "ACCEPTED", json.dumps(warnings)),
            )
        return {
            "id": dataset,
            "rows": len(records),
            "warnings": warnings,
            "sha256": digest,
        }
    except sqlite3.IntegrityError as e:
        raise DomainError(
            "Duplicate dataset or overlapping candles; existing data was not changed"
        ) from e


def candles(db, instrument_id=None, timeframe="5m", dataset_id=None, as_of=None):
    sql = "SELECT c.*, i.symbol FROM candles c JOIN instruments i ON i.id=c.instrument_id WHERE 1=1"
    args = []
    for field, val in [
        ("c.instrument_id", instrument_id),
        ("c.timeframe", timeframe),
        ("c.dataset_id", dataset_id),
        ("c.available_at", as_of),
    ]:
        if val is not None:
            sql += f' AND {field} {"<=" if field=="c.available_at" else "="} ?'
            args.append(val)
    return db.rows(sql + " ORDER BY c.timestamp,c.instrument_id", args)


def analysis_frame(rows):
    f = pd.DataFrame(rows)
    if not f.empty:
        for k in ("open", "high", "low", "close"):
            f[k] = f[k] / 100
        f["timestamp"] = pd.to_datetime(f["timestamp"], utc=True)
    return f
