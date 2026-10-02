"""Clock, time zone and business-day math (Section 8).

Every timestamp is stored in UTC. Local time is used only for display,
quiet hours and business-day math.
"""

import datetime as dt
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/Los_Angeles"
UTC = dt.timezone.utc


def utcnow() -> dt.datetime:
    return dt.datetime.now(UTC)


def to_local(value: dt.datetime, tz: str = DEFAULT_TIMEZONE) -> dt.datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(tz))


def local_to_utc(value: dt.datetime, tz: str = DEFAULT_TIMEZONE) -> dt.datetime:
    """Treat a naive datetime as local wall-clock time in `tz` and convert it to UTC."""
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return value.replace(tzinfo=ZoneInfo(tz)).astimezone(UTC)


def local_date(value: dt.datetime | dt.date, tz: str = DEFAULT_TIMEZONE) -> dt.date:
    if isinstance(value, dt.datetime):
        return to_local(value, tz).date()
    return value


def local_datetime_at(day: dt.date, at: dt.time, tz: str = DEFAULT_TIMEZONE) -> dt.datetime:
    """The UTC instant of local wall-clock time `at` on `day`."""
    return local_to_utc(dt.datetime.combine(day, at), tz)


def is_business_day(day: dt.date) -> bool:
    return day.weekday() < 5


def business_days_between(start: dt.datetime | dt.date, end: dt.datetime | dt.date, tz: str = DEFAULT_TIMEZONE) -> int:
    """Count dates d with start date < d <= end date that are Monday to Friday, in the shop's local dates."""
    start_day = local_date(start, tz)
    end_day = local_date(end, tz)
    if end_day <= start_day:
        return 0
    count = 0
    day = start_day + dt.timedelta(days=1)
    while day <= end_day:
        if is_business_day(day):
            count += 1
        day += dt.timedelta(days=1)
    return count


def add_business_days(day: dt.date, n: int) -> dt.date:
    """Step forward 1 calendar day at a time, counting only Monday to Friday, until n days are counted."""
    if n < 1:
        raise ValueError("n must be 1 or more")
    counted = 0
    while counted < n:
        day += dt.timedelta(days=1)
        if is_business_day(day):
            counted += 1
    return day
