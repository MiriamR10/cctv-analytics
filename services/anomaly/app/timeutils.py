from datetime import UTC, datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

from app.config import settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Normalize to aware UTC. Naive values (e.g. from SQLite) are treated as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@cache
def local_tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def floor_to_window(value: datetime, window_minutes: int) -> datetime:
    value = as_utc(value)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    window = timedelta(minutes=window_minutes)
    return epoch + ((value - epoch) // window) * window
