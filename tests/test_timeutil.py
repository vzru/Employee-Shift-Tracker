"""Work-week and time helper tests."""

from __future__ import annotations

from datetime import date, datetime

from app.timeutil import hours_between, parse_iso, to_iso, week_start_for


class TestWeekStartFor:
    def test_sunday_scheme_midweek(self):
        # Wed Jul 1 2026 -> week starts Sun Jun 28.
        assert week_start_for(date(2026, 7, 1), 6) == date(2026, 6, 28)

    def test_sunday_scheme_on_sunday(self):
        # A Sunday is its own week start.
        assert week_start_for(date(2026, 6, 28), 6) == date(2026, 6, 28)

    def test_sunday_scheme_on_saturday(self):
        # Saturday belongs to the week that started 6 days earlier.
        assert week_start_for(date(2026, 7, 4), 6) == date(2026, 6, 28)

    def test_monday_scheme(self):
        # Same Wednesday under a Monday-start scheme.
        assert week_start_for(date(2026, 7, 1), 0) == date(2026, 6, 29)

    def test_accepts_datetime(self):
        assert week_start_for(datetime(2026, 7, 1, 23, 59), 6) == date(2026, 6, 28)

    def test_overnight_shift_files_under_clockin_week(self):
        # Sat 11pm clock-in vs Sun 1am clock-out: attribution is by clock-in.
        ci = datetime(2026, 7, 4, 23, 0)   # Saturday
        co = datetime(2026, 7, 5, 1, 0)    # Sunday (next work week)
        assert week_start_for(ci, 6) == date(2026, 6, 28)
        assert week_start_for(co, 6) == date(2026, 7, 5)

    def test_year_boundary(self):
        # Thu Jan 1 2026 belongs to the week starting Sun Dec 28 2025.
        assert week_start_for(date(2026, 1, 1), 6) == date(2025, 12, 28)


class TestParseAndFormat:
    def test_roundtrip_seconds(self):
        assert to_iso(parse_iso("2026-07-04T09:30:15")) == "2026-07-04T09:30:15"

    def test_minute_precision_accepted(self):
        # datetime-local inputs submit without seconds.
        assert parse_iso("2026-07-04T09:30") == datetime(2026, 7, 4, 9, 30)

    def test_hours_between(self):
        assert hours_between("2026-07-04T09:00:00", "2026-07-04T17:30:00") == 8.5

    def test_hours_between_overnight(self):
        assert hours_between("2026-07-04T23:00:00", "2026-07-05T01:00:00") == 2.0
