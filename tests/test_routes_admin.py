"""Admin routes via TestClient: auth, employees, shifts, settings, payroll, holiday."""

from __future__ import annotations

import json

from app import repo
from tests.conftest import ADMIN_PASSWORD


# --- Auth --------------------------------------------------------------------

class TestAuth:
    def test_unauthenticated_redirects_to_login(self, client, settings_writer):
        settings_writer(password_hash="x")  # configured, but no session
        r = client.get("/admin")
        assert r.status_code == 303
        assert "/admin/login" in r.headers["location"]

    def test_first_run_redirects_to_setup(self, client):
        # No admin.json at all => not configured => setup.
        r = client.get("/admin/login")
        assert r.status_code == 303
        assert "/admin/setup" in r.headers["location"]

    def test_setup_sets_password_and_logs_in(self, client):
        r = client.post("/admin/setup", data={"password": "abcdef", "confirm": "abcdef"})
        assert r.status_code == 303
        assert r.headers["location"].endswith("/admin")
        assert repo.load_admin().password_hash is not None

    def test_setup_rejects_mismatch(self, client):
        r = client.post("/admin/setup", data={"password": "abcdef", "confirm": "zzzzzz"})
        assert "do+not+match" in r.headers["location"]

    def test_setup_rejects_short(self, client):
        r = client.post("/admin/setup", data={"password": "abc", "confirm": "abc"})
        assert "at+least+6" in r.headers["location"]

    def test_login_wrong_password(self, client, data_dir, settings_writer):
        from app import security
        settings_writer(password_hash=security.hash_password("right"))
        r = client.post("/admin/login", data={"password": "wrong"})
        assert "Incorrect+password" in r.headers["location"]

    def test_logout(self, admin_client):
        r = admin_client.get("/admin/logout")
        assert "/admin/login" in r.headers["location"]
        # Session cleared: admin home now bounces to login.
        assert "/admin/login" in admin_client.get("/admin").headers["location"]


# --- Admin home --------------------------------------------------------------

class TestAdminHome:
    def test_counts(self, admin_client, make_employee):
        make_employee(emp_id="e1")
        make_employee(emp_id="e2", first="Sam", last="Lee", active=False)
        html = admin_client.get("/admin").text
        assert "Admin panel" in html

    def test_misaligned_week_banner(self, admin_client, data_dir):
        bad = data_dir / "2026" / "week-2026-06-29"  # Monday under Sunday scheme
        bad.mkdir(parents=True)
        (bad / "shifts.json").write_text("[]")
        html = admin_client.get("/admin").text
        assert "don't match" in html
        assert "2026/week-2026-06-29" in html

    def test_below_min_wage_warning(self, admin_client, make_employee):
        make_employee(rate=5.0)  # far below min wage
        html = admin_client.get("/admin").text
        assert "below the minimum wage" in html

    def test_long_open_shift_banner(self, admin_client, make_employee, make_shift):
        import datetime
        make_employee(first="Forgot", last="Out")
        ci = (datetime.datetime.now() - datetime.timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%S")
        make_shift(ci, None)
        html = admin_client.get("/admin").text
        assert "Still clocked in" in html
        assert "Forgot Out" in html

    def test_no_banner_for_short_open_shift(self, admin_client, make_employee, make_shift):
        import datetime
        make_employee()
        ci = (datetime.datetime.now() - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        make_shift(ci, None)
        assert "Still clocked in" not in admin_client.get("/admin").text


# --- Employees ---------------------------------------------------------------

class TestEmployees:
    def test_add_employee_with_vacation(self, admin_client):
        roles = json.dumps([{"title": "Cook", "department": "Restaurant", "hourly_rate": 20}])
        r = admin_client.post("/admin/employees/add", data={
            "first_name": "New", "last_name": "Hire", "roles_json": roles,
            "active": "on", "vacation_pay_percent": "6"})
        assert r.status_code == 303
        emps = repo.load_employees()
        assert len(emps) == 1
        assert emps[0].vacation_pay_percent == 6.0
        assert emps[0].name == "New Hire"

    def test_add_requires_name(self, admin_client):
        roles = json.dumps([{"title": "Cook", "department": "Restaurant", "hourly_rate": 20}])
        r = admin_client.post("/admin/employees/add", data={
            "first_name": "", "last_name": "", "roles_json": roles})
        assert "name+required" in r.headers["location"]

    def test_add_requires_role(self, admin_client):
        r = admin_client.post("/admin/employees/add", data={
            "first_name": "No", "last_name": "Roles", "roles_json": "[]"})
        assert "role+is+required" in r.headers["location"]

    def test_edit_employee_changes_vacation(self, admin_client, make_employee):
        make_employee(emp_id="e1", vacation_pay_percent=4.0)
        roles = json.dumps([{"id": "r1", "title": "Cook", "department": "Restaurant", "hourly_rate": 20}])
        r = admin_client.post("/admin/employees/e1/edit", data={
            "first_name": "Jane", "last_name": "Doe", "roles_json": roles,
            "active": "on", "vacation_pay_percent": "6"})
        assert r.status_code == 303
        assert repo.get_employee("e1").vacation_pay_percent == 6.0

    def test_edit_role_persists(self, admin_client, make_employee):
        # Regression: the getter/spread bug once made role edits silently no-op.
        make_employee(emp_id="e1")
        roles = json.dumps([{"title": "Manager", "department": "Bowling", "hourly_rate": 30}])
        admin_client.post("/admin/employees/e1/edit", data={
            "first_name": "Jane", "last_name": "Doe", "roles_json": roles,
            "active": "on", "vacation_pay_percent": "4"})
        e = repo.get_employee("e1")
        assert e.roles[0].title == "Manager"
        assert e.roles[0].hourly_rate == 30.0

    def test_deactivate_and_reactivate(self, admin_client, make_employee):
        make_employee(emp_id="e1")
        admin_client.post("/admin/employees/e1/set-active", data={"active": "false"})
        assert repo.get_employee("e1").active is False
        admin_client.post("/admin/employees/e1/set-active", data={"active": "true"})
        assert repo.get_employee("e1").active is True


# --- Shifts ------------------------------------------------------------------

class TestShifts:
    def test_shifts_page_renders(self, admin_client, make_employee, make_shift):
        make_employee()
        make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        r = admin_client.get("/admin/shifts?week_start=2026-06-28")
        assert r.status_code == 200
        assert "Jane Doe" in r.text

    def test_edit_shift(self, admin_client, make_employee, make_shift):
        make_employee()
        sid = make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        r = admin_client.post("/admin/shifts/edit", data={
            "week_start": "2026-06-28", "shift_id": sid,
            "clock_in": "2026-06-29T09:00", "clock_out": "2026-06-29T12:00"})
        assert "Shift+updated" in r.headers["location"]
        s = next(s for s in repo.load_week_shifts(__import__("datetime").date(2026, 6, 28)) if s.id == sid)
        assert s.hours == 3.0

    def test_edit_bad_week_is_friendly(self, admin_client):
        r = admin_client.post("/admin/shifts/edit", data={
            "week_start": "garbage", "shift_id": "x", "clock_in": "2026-06-29T09:00"})
        assert r.status_code == 303
        assert "Invalid+week" in r.headers["location"]

    def test_edit_bad_break_override_is_friendly(self, admin_client, make_employee, make_shift):
        make_employee()
        sid = make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        r = admin_client.post("/admin/shifts/edit", data={
            "week_start": "2026-06-28", "shift_id": sid,
            "clock_in": "2026-06-29T09:00", "clock_out": "2026-06-29T17:00",
            "break_override": "abc"})
        assert "whole+number" in r.headers["location"]

    def test_void_shift(self, admin_client, make_employee, make_shift):
        make_employee()
        sid = make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        r = admin_client.post("/admin/shifts/void", data={
            "week_start": "2026-06-28", "shift_id": sid, "voided": "true"})
        assert "Shift+voided" in r.headers["location"]


# --- Settings ----------------------------------------------------------------

class TestSettings:
    def test_settings_page_renders(self, admin_client):
        assert admin_client.get("/admin/settings").status_code == 200

    def test_save_settings(self, admin_client):
        r = admin_client.post("/admin/settings", data={
            "break_duration_minutes": "45", "break_trigger_hours": "5",
            "ot_multiplier": "2", "ot_weekly_threshold": "40", "ot_week_start_weekday": "0",
            "min_wage_rate": "18.00", "role_catalog_json": "{}",
            "auto_clockout_threshold_hours": "12"})
        assert "Settings+saved" in r.headers["location"]
        s = repo.load_settings()
        assert s.overtime.weekly_threshold == 40.0
        assert s.overtime.week_start_weekday == 0
        assert s.min_wage.rate == 18.0
        # Unchecked checkboxes => disabled.
        assert s.break_rules.enabled is False
        assert s.overtime.enabled is False

    def test_change_password(self, admin_client):
        r = admin_client.post("/admin/password", data={
            "current": ADMIN_PASSWORD, "password": "newpass1", "confirm": "newpass1"})
        assert "Password+changed" in r.headers["location"]
        from app import security
        assert security.check_admin_password("newpass1")

    def test_change_password_wrong_current(self, admin_client):
        r = admin_client.post("/admin/password", data={
            "current": "wrong", "password": "newpass1", "confirm": "newpass1"})
        assert "incorrect" in r.headers["location"]


# --- Payroll & holiday -------------------------------------------------------

class TestPayroll:
    def test_preview_renders_with_vacation(self, admin_client, make_employee, make_shift):
        make_employee(rate=20.0)
        make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        r = admin_client.get("/admin/payroll?start=2026-06-28&end=2026-07-04")
        assert r.status_code == 200
        assert "Vacation pay" in r.text

    def test_export_csv(self, admin_client, make_employee, make_shift):
        make_employee(rate=20.0)
        make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        r = admin_client.get("/admin/payroll/export?start=2026-06-28&end=2026-07-04")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "Vacation Pay" in r.text

    def test_export_bad_range_is_friendly(self, admin_client):
        r = admin_client.get("/admin/payroll/export")
        assert r.status_code == 303
        assert "valid+date+range" in r.headers["location"]

    def test_export_end_before_start_friendly(self, admin_client):
        r = admin_client.get("/admin/payroll/export?start=2026-07-10&end=2026-07-01")
        assert r.status_code == 303
        assert "valid+date+range" in r.headers["location"]

    def test_holiday_page(self, admin_client, make_employee, make_shift):
        make_employee(rate=20.0)
        make_shift("2026-06-15T09:00:00", "2026-06-15T17:00:00")  # in the 4wk window
        r = admin_client.get("/admin/holiday?holiday=2026-07-01")
        assert r.status_code == 200
        assert "Holiday pay" in r.text or "holiday pay" in r.text

    def test_holiday_bad_date_friendly(self, admin_client):
        r = admin_client.get("/admin/holiday?holiday=notadate")
        assert r.status_code == 200
        assert "valid holiday date" in r.text
