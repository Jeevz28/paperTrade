from abc import ABC, abstractmethod
from collections import deque
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
import gzip
import io
import json
import os
import re
import threading
import time
from urllib.parse import quote
import httpx
import pandas as pd
from .core import DomainError, TIMEFRAMES, UTC, now, timestamp, money
from .data import candles


class MarketDataProvider(ABC):
    @abstractmethod
    def get_candles(self, **kwargs): ...


class SQLiteProvider(MarketDataProvider):
    def __init__(self, db):
        self.db = db

    def get_candles(self, **kwargs):
        return candles(self.db, **kwargs)


class CSVProvider(MarketDataProvider):
    def __init__(self, content):
        self.content = content

    def get_candles(self, **kwargs):
        return pd.read_csv(io.BytesIO(self.content)).to_dict("records")


class UpstoxProvider(MarketDataProvider):
    """Narrow read-only REST adapter. No broker SDK or arbitrary URL entrypoint."""

    API = "https://api.upstox.com"
    PATHS = (
        r"/v3/feed/market-data-feed/authorize",
        r"/v2/market-quote/quotes",
        r"/v2/option/chain",
        r"/v2/option/contract",
        r"/v2/market/status/NSE",
        r"/v3/historical-candle/intraday/[^/]+/minutes/1",
        r"/v3/historical-candle/[^/]+/(minutes|hours|days)/[0-9]+/[0-9-]+/[0-9-]+",
    )

    def __init__(self, policy, transport=None):
        self.policy = policy
        self.transport = transport
        self.history = deque()
        self.lock = threading.Lock()
        self.blocked_until = 0

    def enabled(self):
        if os.getenv("UPSTOX_ENABLED", "false").lower() != "true" or not os.getenv(
            "UPSTOX_ACCESS_TOKEN"
        ):
            raise DomainError("Upstox disabled or access token missing")

    def _get(self, path, params=None):
        self.enabled()
        if (
            not any(re.fullmatch(p, path) for p in self.PATHS)
            or ".." in path
            or "%2f" in path.lower()
        ):
            raise DomainError("Market-data path is not allowlisted")
        with self.lock:
            current = time.monotonic()
            while self.history and current - self.history[0] > 1800:
                self.history.popleft()
            if (
                current < self.blocked_until
                or len(self.history) >= 1900
                or sum(current - t < 60 for t in self.history) >= 450
                or sum(current - t < 1 for t in self.history) >= 40
            ):
                raise DomainError("Market-data rate limit reached; retry later")
            self.history.append(current)
        try:
            with httpx.Client(
                timeout=15, follow_redirects=False, transport=self.transport
            ) as client:
                response = client.get(
                    self.API + path,
                    params=params,
                    headers={
                        "Authorization": "Bearer " + os.environ["UPSTOX_ACCESS_TOKEN"],
                        "Accept": "application/json",
                    },
                )
            if response.status_code == 429:
                try:
                    delay = min(
                        300, max(1, float(response.headers.get("Retry-After", "30")))
                    )
                except ValueError:
                    delay = 30
                self.blocked_until = time.monotonic() + delay
                raise DomainError("Upstox rate limited the request; cooldown active")
            if response.status_code in (401, 403):
                raise DomainError(
                    "Upstox authorization expired or entitlement denied; refresh token outside the app"
                )
            if response.status_code != 200:
                raise DomainError(
                    f"Upstox market-data request failed ({response.status_code})"
                )
            payload = response.json()
            if payload.get("status") != "success":
                raise DomainError(
                    "Upstox returned an unsuccessful market-data response"
                )
            return payload["data"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            if isinstance(e, DomainError):
                raise
            raise DomainError(
                "Market-data provider unavailable or malformed response; execution stopped"
            ) from None

    @staticmethod
    def key(value):
        if not re.fullmatch(r"NSE_(EQ|FO|INDEX)\|[A-Za-z0-9 _.-]+", value):
            raise DomainError("Invalid NSE instrument key")
        return value

    def get_candles(self, instrument_key, start, end, timeframe="5m"):
        self.key(instrument_key)
        if timeframe not in TIMEFRAMES:
            raise DomainError("Unsupported timeframe")
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last or (last - first).days > 3660:
            raise DomainError("Invalid date range (maximum ten years)")
        self.policy.check(
            (datetime.combine(last, datetime.max.time(), ZoneInfo("Asia/Kolkata"))).isoformat()
        )
        unit, interval = (
            ("days", 1)
            if timeframe == "1d"
            else (
                ("hours", 1)
                if timeframe == "1h"
                else ("minutes", TIMEFRAMES[timeframe] // 60)
            )
        )
        results = []
        cursor = first
        while cursor <= last:
            stop = min(last, cursor + timedelta(days=27))
            path = f'/v3/historical-candle/{quote(instrument_key,safe="")}/{unit}/{interval}/{stop}/{cursor}'
            data = self._get(path)
            for r in data.get("candles", []):
                if len(r) < 6:
                    raise DomainError("Malformed historical candle response")
                results.append(
                    {
                        "timestamp": r[0],
                        "open": r[1],
                        "high": r[2],
                        "low": r[3],
                        "close": r[4],
                        "volume": r[5],
                        "open_interest": r[6] if len(r) > 6 else None,
                    }
                )
            cursor = stop + timedelta(days=1)
        return sorted(results, key=lambda r: timestamp(r["timestamp"]))

    def get_intraday(self, instrument_key):
        self.policy.require_live()
        self.key(instrument_key)
        data = self._get(f'/v3/historical-candle/intraday/{quote(instrument_key, safe="")}/minutes/1')
        result = []
        for r in data.get("candles", []):
            if len(r) < 6:
                raise DomainError("Malformed intraday candle response")
            result.append(dict(timestamp=r[0], open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5]))
        return sorted(result, key=lambda r: timestamp(r['timestamp']))

    def get_latest_quote(self, instrument_key):
        self.policy.require_live()
        self.key(instrument_key)
        status = self._get("/v2/market/status/NSE")
        if status.get("status") != "NORMAL_OPEN":
            raise DomainError("NSE normal session is not open")
        data = self._get("/v2/market-quote/quotes", {"instrument_key": instrument_key})
        r = next(
            (v for v in data.values() if v.get("instrument_token") == instrument_key),
            None,
        )
        if not r:
            raise DomainError("TRADE NOT EXECUTED - VALID MARKET PRICE UNAVAILABLE")
        try:
            at = datetime.fromtimestamp(int(r["last_trade_time"]) / 1000, UTC)
            feed = (
                timestamp(r["timestamp"])
                if not str(r["timestamp"]).isdigit()
                else datetime.fromtimestamp(int(r["timestamp"]) / 1000, UTC)
            )
            age = (now() - at).total_seconds()
            feed_age = (now() - feed).total_seconds()
            price = money(r["last_price"])
            if not 0 <= age <= 10 or not 0 <= feed_age <= 10 or price <= 0:
                raise ValueError()
        except (ValueError, KeyError, OverflowError):
            raise DomainError(
                "TRADE NOT EXECUTED - VALID MARKET PRICE UNAVAILABLE (stale or malformed quote)"
            ) from None
        return {
            "instrument_key": instrument_key,
            "price_paise": price,
            "timestamp": at.isoformat(),
            "feed_timestamp": feed.isoformat(),
            "received_at": now().isoformat(),
            "source": "UPSTOX_REST",
            "raw": r,
        }

    def get_option_chain(self, instrument_key, expiry):
        self.policy.require_live()
        self.key(instrument_key)
        date.fromisoformat(expiry)
        return self._get(
            "/v2/option/chain",
            {"instrument_key": instrument_key, "expiry_date": expiry},
        )

    def get_option_contracts(self, instrument_key, expiry):
        self.policy.require_live()
        self.key(instrument_key)
        date.fromisoformat(expiry)
        return self._get(
            "/v2/option/contract",
            {"instrument_key": instrument_key, "expiry_date": expiry},
        )

    def get_instruments(self):
        self.enabled()
        try:
            with httpx.Client(
                timeout=30, follow_redirects=False, transport=self.transport
            ) as c:
                r = c.get(
                    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
                )
            if r.status_code != 200:
                raise DomainError("Instrument master unavailable")
            return json.loads(gzip.decompress(r.content))
        except (httpx.HTTPError, ValueError, OSError):
            raise DomainError("Instrument master unavailable") from None
