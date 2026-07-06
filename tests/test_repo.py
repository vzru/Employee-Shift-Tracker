"""Repository layer: employees, clock in/out, shifts, auto-close, adjustments."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app import repo, timeutil


# --- Employees ---------------------------------------------------------------

class TestEmployees:
    def test_load_empty(self, data_dir):
        assert repo.load_employees() == []

    def test_roundtrip_and_get(self, make_employee):
        make_employee(emp_id="e1", first="Jane", last="Doe")
        emps = repo.load_employees()
        assert len(emps) == 1
        assert emps[0].name == "Jane Doe"
        assert repo.get_employee("e1").name == "Jane Doe"
        assert repo.get_employee("missing") is None

    def test_migrate_legacy_name_field(self, data_dir):
        (data_dir / "employees.json").write_text(json.dumps([
            {"id": "e1", "name": "John Smith", "roles": []},
        ]))
        e = repo.load_employees()[0]
        assert e.first_name == "John"
        assert e.last_name == "Smith"

    def test_migrate_legacy_single_word_name(self, data_dir):
        (data_dir / "employees.json").write_text(json.dumps([
            {"id": "e1", "name": "Cher", "roles": []},
        ]))
        e = repo.load_employees()[0]
        assert e.first_name == "Cher"
        assert e.last_name == ""

    def test_migrate_legacy_flat_rate_to_role(self, data_dir):
        (data_dir / "employees.json").write_text(json.dumps([
            {"id": "e1", "first_name": "A", "last_name": "B", "hourly_rate": 15.0},
        ]))
        e = repo.load_employees()[0]
        assert len(e.roles) == 1
        assert e.roles[0].hourly_rate == 15.0
        assert e.roles[0].department == "General"

    def test_missing_vacation_percent_defaults(self, data_dir):
        (data_dir / "employees.json").write_text(json.dumps([
            {"id": "e1", "first_name": "A", "last_name": "B", "roles": []},
        ]))
        assert repo.load_employees()[0].vacation_pay_percent == 4.0


# --- Clock in / out ----------------------------------------------------------

class TestClockIn:
    def test_creates_snapshotted_shift_in_right_week(self, make_employee, settings_writer):
        settings_writer()
        make_employee(rate=18.5)
        shift = repo.clock_in("e1", "2026-06-29T09:00:00", "r1")
        assert shift.clock_out is None
        assert shift.role_title == "Cook"
        assert shift.department == "Restaurant"
        assert shift.hourly_rate == 18.5
        # Filed under the Sunday-start week of the clock-in.
        ws = timeutil.week_start_for(timeutil.parse_iso("2026-06-29T09:00:00"), 6)
        stored = repo.load_week_shifts(ws)
        assert len(stored) == 1 and stored[0].id == shift.id

    def test_rejects_double_clock_in(self, make_employee, settings_writer):
        settings_writer()
        make_employee()
        repo.clock_in("e1", "2026-06-29T09:00:00", "r1")
        with pytest.raises(ValueError, match="already clocked in"):
            repo.clock_in("e1", "2026-06-29T10:00:00", "r1")

    def test_unknown_employee(self, data_dir, settings_writer):
        settings_writer()
        with pytest.raises(ValueError, match="Unknown employee"):
            repo.clock_in("ghost", "2026-06-29T09:00:00", "r1")

    def test_unknown_role(self, make_employee, settings_writer):
        settings_writer()
        make_employee()
        with pytest.raises(ValueError, match="Unknown role"):
            repo.clock_in("e1", "2026-06-29T09:00:00", "bad-role")


class TestClockOut:
    def test_closes_and_computes_hours(self, make_employee, settings_writer):
        settings_writer()
        make_employee()
        repo.clock_in("e1", "2026-06-29T09:00:00", "r1")
        shift = repo.clock_out("e1", "2026-06-29T17:30:00")
        assert shift.clock_out == "2026-06-29T17:30:00"
        assert shift.hours == 8.5

    def test_rejects_when_not_clocked_in(self, make_employee, settings_writer):
        settings_writer()
        make_employee()
        with pytest.raises(ValueError, match="not clocked in"):
            repo.clock_out("e1", "2026-06-29T17:00:00")

    def test_rejects_clockout_before_clockin(self, make_employee, settings_writer):
        settings_writer()
        make_employee()
        repo.clock_in("e1", "2026-06-29T09:00:00", "r1")
        with pytest.raises(ValueError, match="before clock-in"):
            repo.clock_out("e1", "2026-06-29T08:00:00")


class TestFindOpenShift:
    def test_finds_and_none(self, make_employee, settings_writer):
        settings_writer()
        make_employee()
        assert repo.find_open_shift("e1") is None
        repo.clock_in("e1", "2026-06-29T09:00:00", "r1")
        found = repo.find_open_shift("e1")
        assert found is not None
        ws, shift = found
        assert shift.clock_out is None

    def test_ignores_voided(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-29T09:00:00", None, voided=True)
        assert repo.find_open_shift("e1") is None

    def test_open_shifts_by_employee_map(self, make_employee, settings_writer):
        settings_writer()
        make_employee(emp_id="e1")
        make_employee(emp_id="e2", first="Sam", last="Lee")
        repo.clock_in("e1", "2026-06-29T09:00:00", "r1")
        m = repo.open_shifts_by_employee()
        assert set(m.keys()) == {"e1"}


# --- Auto-close --------------------------------------------------------------

class TestAutoClose:
    def _open_shift_hours_ago(self, make_employee, make_shift, hours):
        make_employee()
        ci = timeutil.to_iso(timeutil.now_local() - timedelta(hours=hours))
        return make_shift(ci, None)

    def test_closes_stale_shift(self, make_employee, make_shift, settings_writer):
        settings_writer(auto_enabled=True, auto_threshold=24.0)
        self._open_shift_hours_ago(make_employee, make_shift, 30)
        closed = repo.auto_close_stale_shifts()
        assert len(closed) == 1
        assert closed[0].auto_clocked_out is True
        assert closed[0].clock_out is not None
        assert closed[0].hours is not None
        assert repo.find_open_shift("e1") is None

    def test_leaves_fresh_shift_open(self, make_employee, make_shift, settings_writer):
        settings_writer(auto_enabled=True, auto_threshold=24.0)
        self._open_shift_hours_ago(make_employee, make_shift, 3)
        assert repo.auto_close_stale_shifts() == []
        assert repo.find_open_shift("e1") is not None

    def test_disabled_does_nothing(self, make_employee, make_shift, settings_writer):
        settings_writer(auto_enabled=False)
        self._open_shift_hours_ago(make_employee, make_shift, 100)
        assert repo.auto_close_stale_shifts() == []
        assert repo.find_open_shift("e1") is not None

    def test_skips_voided(self, make_employee, make_shift, settings_writer):
        settings_writer(auto_enabled=True, auto_threshold=1.0)
        make_employee()
        ci = timeutil.to_iso(timeutil.now_local() - timedelta(hours=50))
        make_shift(ci, None, voided=True)
        assert repo.auto_close_stale_shifts() == []

    def test_writes_audit_entry(self, data_dir, make_employee, make_shift, settings_writer):
        settings_writer(auto_enabled=True, auto_threshold=24.0)
        self._open_shift_hours_ago(make_employee, make_shift, 30)
        repo.auto_close_stale_shifts()
        log = (data_dir / "audit.log").read_text()
        assert "shift_auto_clocked_out" in log


# --- Misaligned week folders -------------------------------------------------

class TestMisalignedWeeks:
    def test_clean_when_aligned(self, make_shift, settings_writer):
        settings_writer(week_start_weekday=6)
        make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")  # Sunday-start week
        assert repo.find_misaligned_week_folders() == []

    def test_detects_mismatch(self, data_dir, settings_writer):
        settings_writer(week_start_weekday=6)  # Sunday
        # A Monday-dated week folder doesn't match a Sunday scheme.
        bad = data_dir / "2026" / "week-2026-06-29"
        bad.mkdir(parents=True)
        (bad / "shifts.json").write_text("[]")
        assert "2026/week-2026-06-29" in repo.find_misaligned_week_folders()

    def test_ignores_non_week_dirs(self, data_dir, settings_writer):
        settings_writer(week_start_weekday=6)
        (data_dir / "2026" / "notaweek").mkdir(parents=True)
        assert repo.find_misaligned_week_folders() == []


# --- Admin shift edits / voids / overrides -----------------------------------

class TestShiftEdits:
    def test_update_shift_recomputes_hours(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        sid = make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        ws = date(2026, 6, 28)
        repo.update_shift(ws, sid, "2026-06-29T09:00:00", "2026-06-29T12:00:00")
        s = next(s for s in repo.load_week_shifts(ws) if s.id == sid)
        assert s.hours == 3.0

    def test_update_shift_rejects_negative(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        sid = make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        with pytest.raises(ValueError):
            repo.update_shift(date(2026, 6, 28), sid,
                              "2026-06-29T09:00:00", "2026-06-29T08:00:00")

    def test_update_shift_reopen(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        sid = make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        ws = date(2026, 6, 28)
        repo.update_shift(ws, sid, "2026-06-29T09:00:00", None)
        s = next(s for s in repo.load_week_shifts(ws) if s.id == sid)
        assert s.clock_out is None and s.hours is None

    def test_void_and_restore(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        sid = make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        ws = date(2026, 6, 28)
        repo.set_shift_voided(ws, sid, True)
        assert next(s for s in repo.load_week_shifts(ws) if s.id == sid).voided is True
        repo.set_shift_voided(ws, sid, False)
        assert next(s for s in repo.load_week_shifts(ws) if s.id == sid).voided is False


class TestBreakOverrides:
    def test_set_and_load(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        sid = make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        ws = date(2026, 6, 28)
        repo.set_break_override(ws, sid, 0)
        assert repo.load_adjustments(ws)[sid]["minutes"] == 0

    def test_clear_with_none(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        sid = make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        ws = date(2026, 6, 28)
        repo.set_break_override(ws, sid, 15)
        repo.set_break_override(ws, sid, None)
        assert sid not in repo.load_adjustments(ws)


# --- Settings / admin persistence --------------------------------------------

class TestSettingsPersistence:
    def test_first_run_defaults(self, data_dir):
        admin = repo.load_admin()
        assert admin.password_hash is None
        assert admin.settings.overtime.week_start_weekday == 6

    def test_save_and_load_settings(self, data_dir):
        s = repo.load_settings()
        s.min_wage.rate = 19.25
        repo.save_settings(s)
        assert repo.load_settings().min_wage.rate == 19.25

    def test_new_id_is_unique_hex(self):
        ids = {repo.new_id() for _ in range(200)}
        assert len(ids) == 200
        assert all(len(i) == 12 for i in ids)
