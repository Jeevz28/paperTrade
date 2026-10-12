from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass
import os

UTC = timezone.utc
TIMEFRAMES = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "1d": 86400,
}


class DomainError(ValueError):
    pass


def now():
    return datetime.now(UTC)


def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise DomainError("Timestamps require an explicit timezone, e.g. +05:30")
    return dt.astimezone(UTC)


def money(value):
    d = Decimal(str(value))
    if not d.is_finite():
        raise DomainError("Money must be finite")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Policy:
    mode: str = "education"
    permission: str = ""

    def __post_init__(self):
        if self.mode not in ("education", "licensed_private"):
            raise DomainError("Unknown data-use mode")
        if self.mode == "licensed_private" and not self.permission.strip():
            raise DomainError("Recent data requires a documented permission reference")

    @classmethod
    def environment(cls):
        return cls(
            os.getenv("PAPERLAB_MODE", "education"),
            os.getenv("DATA_PERMISSION_REFERENCE", ""),
        )

    def cutoff(self):
        return now() - timedelta(days=30) if self.mode == "education" else now()

    def check(self, dt):
        if timestamp(dt) > self.cutoff():
            raise DomainError("Data is newer than the permitted cutoff for this mode")

    def require_live(self):
        if self.mode != "licensed_private":
            raise DomainError(
                "Live data disabled in education mode; documented permission required"
            )
