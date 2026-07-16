"""Pydantic models and their defaults."""

from __future__ import annotations

from app.models import (
    AdminData, AutoClockoutSettings, BreakSettings, Employee, MinWageSettings,
    OvertimeSettings, Role, Settings, Shift,
)


class TestEmployee:
    def test_name_property(self):
        e = Employee(id="1", first_name="Jane", last_name="Doe")
        assert e.name == "Jane Doe"

    def test_name_strips_when_last_blank(self):
        e = Employee(id="1", first_name="Cher", last_name="")
        assert e.name == "Cher"

    def test_short_name_basic(self):
        e = Employee(id="1", first_name="Jane", last_name="Doe")
        assert e.short_name == "Jane D."

    def test_short_name_drops_middle(self):
        # Middle names/initials live in first_name and must be dropped.
        assert Employee(id="1", first_name="Emma M", last_name="Blais").short_name == "Emma B."
        assert Employee(id="2", first_name="Taylor Lynn", last_name="Campbell").short_name == "Taylor C."
        assert Employee(id="3", first_name="Tri Dund", last_name="Nguyen").short_name == "Tri N."

    def test_short_name_two_taylors_stay_distinct(self):
        a = Employee(id="1", first_name="Taylor Lynn", last_name="Campbell")
        b = Employee(id="2", first_name="Taylor", last_name="Vachon")
        assert a.short_name == "Taylor C." and b.short_name == "Taylor V."
        assert a.short_name != b.short_name

    def test_short_name_uppercases_initial(self):
        assert Employee(id="1", first_name="jane", last_name="doe").short_name == "jane D."

    def test_short_name_no_last(self):
        assert Employee(id="1", first_name="Cher", last_name="").short_name == "Cher"

    def test_short_name_no_first(self):
        assert Employee(id="1", first_name="", last_name="Prince").short_name == "Prince"

    def test_short_name_uses_preferred(self):
        # "Kulbir" who goes by "Kevin" shows as "Kevin J." on the kiosk.
        e = Employee(id="1", first_name="Kulbir K", last_name="Johal", preferred_name="Kevin")
        assert e.short_name == "Kevin J."
        assert e.name == "Kulbir K Johal"   # legal name unchanged

    def test_preferred_name_defaults_empty(self):
        assert Employee(id="1", first_name="A", last_name="B").preferred_name == ""

    def test_blank_preferred_falls_back_to_first(self):
        e = Employee(id="1", first_name="Jane", last_name="Doe", preferred_name="   ")
        assert e.short_name == "Jane D."

    def test_defaults(self):
        e = Employee(id="1", first_name="A", last_name="B")
        assert e.active is True
        assert e.roles == []
        assert e.vacation_pay_percent == 4.0

    def test_custom_vacation_percent(self):
        e = Employee(id="1", first_name="A", last_name="B", vacation_pay_percent=6.0)
        assert e.vacation_pay_percent == 6.0


class TestRoleAndShift:
    def test_role_default_rate(self):
        r = Role(id="r", title="Cook", department="Kitchen")
        assert r.hourly_rate == 0.0

    def test_shift_open_defaults(self):
        s = Shift(id="s", employee_id="e", clock_in="2026-07-04T09:00:00")
        assert s.clock_out is None
        assert s.hours is None
        assert s.auto_clocked_out is False
        assert s.voided is False
        assert s.role_id is None


class TestSettings:
    def test_settings_defaults(self):
        s = Settings()
        assert s.break_rules.enabled is True
        assert s.break_rules.duration_minutes == 30
        assert s.break_rules.trigger_hours == 5.0
        assert s.overtime.enabled is True
        assert s.overtime.multiplier == 1.5
        assert s.overtime.weekly_threshold == 44.0
        assert s.overtime.week_start_weekday == 6
        assert s.min_wage.rate == 17.60
        assert s.auto_clockout.enabled is True
        assert s.auto_clockout.threshold_hours == 24.0
        assert s.role_catalog == {"General": ["General"]}

    def test_admin_data_default_unconfigured(self):
        a = AdminData()
        assert a.password_hash is None
        assert isinstance(a.settings, Settings)

    def test_component_models_independent_defaults(self):
        assert BreakSettings().duration_minutes == 30
        assert OvertimeSettings().weekly_threshold == 44.0
        assert MinWageSettings().rate == 17.60
        assert AutoClockoutSettings().threshold_hours == 24.0
