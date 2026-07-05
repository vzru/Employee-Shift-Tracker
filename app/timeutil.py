"""
Time and work-week helpers.

All timestamps in this app are stored as ISO 8601 strings *without* timezone
(local system time), e.g. ``2026-07-04T09:30:00``. The business runs on one PC in
one location, so local naive time is the least surprising for the operator and
keeps the JSON human-readable.

"Week" everywhere in this app (shift storage bucketing, the admin Shifts page,
and overtime) means the work week defined by ``Settings.overtime.week_start_weekday``
(default Sunday) — see ``week_start_for`` below. This is deliberately NOT the
ISO 8601 week (which is always Monday-start and numbered); weeks are instead
keyed by their start date, so there's no ambiguous week-numbering scheme.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# Format used for stored timestamps and for <input type="datetime-local"> values.
# Seconds included; no timezone suffix (local time).
TS_FORMAT = "%Y-%m-%dT%H:%M:%S"
# HTML datetime-local inputs submit minute precision "YYYY-MM-DDTHH:MM".
TS_FORMAT_MINUTE = "%Y-%m-%dT%H:%M"


def now_local() -> datetime:
    """Current local system time, seconds precision (drop microseconds)."""
    return datetime.now().replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    """Serialize a datetime to the stored string form."""
    return dt.strftime(TS_FORMAT)


def parse_iso(value: str) -> datetime:
    """
    Parse a stored/submitted timestamp. Accepts both second precision
    (from storage) and minute precision (from datetime-local inputs).
    """
    value = value.strip()
    for fmt in (TS_FORMAT, TS_FORMAT_MINUTE):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    # Last resort: let fromisoformat try (handles seconds/fractions).
    return datetime.fromisoformat(value)


def week_start_for(dt: datetime | date, week_start_weekday: int = 6) -> date:
    """
    The date of the start of the work week containing ``dt`` (accepts either a
    ``date`` or a ``datetime``).

    ``week_start_weekday`` uses Python's Monday=0 .. Sunday=6 convention
    (default Sunday=6). This is the single function that defines "week" for
    shift storage bucketing, the admin Shifts page, and overtime.
    """
    d = dt.date() if isinstance(dt, datetime) else dt
    delta = (d.weekday() - week_start_weekday) % 7
    return date.fromordinal(d.toordinal() - delta)


def week_end_for(week_start: date) -> date:
    """The last day (inclusive) of a work week that starts on ``week_start``."""
    return week_start + timedelta(days=6)


def hours_between(clock_in_iso: str, clock_out_iso: str) -> float:
    """Raw duration in hours between two stored timestamps (not break-adjusted)."""
    delta = parse_iso(clock_out_iso) - parse_iso(clock_in_iso)
    return delta.total_seconds() / 3600.0
