"""
Time and ISO-week helpers.

All timestamps in this app are stored as ISO 8601 strings *without* timezone
(local system time), e.g. ``2026-07-04T09:30:00``. The business runs on one PC in
one location, so local naive time is the least surprising for the operator and
keeps the JSON human-readable. Shift files are bucketed by ISO week (weeks start
Monday) using ``date.isocalendar()``.
"""

from __future__ import annotations

from datetime import datetime, date

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


def iso_year_week(dt: datetime) -> tuple[int, int]:
    """
    Return the ISO (year, week) a datetime falls in. Note the ISO year can differ
    from the calendar year around Jan 1 (e.g. 2026-12-31 may be ISO week 1 of
    2027, or vice-versa) — this is intentional and correct for payroll weeks.
    """
    iso = dt.isocalendar()
    return iso.year, iso.week


def week_start_for(dt: datetime, week_start_weekday: int = 0) -> date:
    """
    The date of the start of the work week containing ``dt``.

    ``week_start_weekday`` uses Python's Monday=0 .. Sunday=6 convention
    (default Monday). Used by weekly-overtime bucketing where the configurable
    work-week start may differ from the ISO Monday boundary.
    """
    d = dt.date()
    delta = (d.weekday() - week_start_weekday) % 7
    return date.fromordinal(d.toordinal() - delta)
