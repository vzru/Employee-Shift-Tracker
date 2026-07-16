"""
Payroll engine tests: break rules, weekly overtime with chronological
allocation across rates, vacation pay, compliance flags, holiday pay, and the
range/voided/open filters.
"""

from __future__ import annotations

from datetime import date

from app import payroll

RANGE = (date(2026, 6, 28), date(2026, 7, 4))  # one Sunday-start work week


def _row(rows, name="Jane Doe"):
    return next(r for r in rows if r.name == name)


class TestBasics:
    def test_single_shift_no_rules(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee(rate=20.0)
        make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")  # 8h
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.regular_hours == 8.0
        assert r.regular_pay == 160.0
        assert r.overtime_pay == 0.0
        assert r.total_pay == 160.0

    def test_open_shift_excluded_but_flagged(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-29T09:00:00", None)
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.total_pay == 0.0
        assert r.open_shift_count == 1

    def test_voided_shift_fully_ignored(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00", voided=True)
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.total_pay == 0.0
        assert r.short_shift_count == 0

    def test_range_filters_by_clock_in_date(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee(rate=20.0)
        make_shift("2026-06-27T09:00:00", "2026-06-27T17:00:00")  # day before range
        make_shift("2026-06-28T09:00:00", "2026-06-28T17:00:00")  # first day of range
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.regular_hours == 8.0

    def test_money_is_exact_cents(self, make_employee, make_shift, settings_writer):
        # 3 x (0.1h at $19.99) — float summing would drift; Decimal must not.
        settings_writer()
        make_employee(rate=19.99)
        for day in (29, 30, 1):
            month = 6 if day > 20 else 7
            make_shift(f"2026-{month:02d}-{day:02d}T09:00:00",
                       f"2026-{month:02d}-{day:02d}T09:06:00", rate=19.99)
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.total_pay == 6.0  # 0.3h * 19.99 = 5.997 -> 6.00


class TestBreakRules:
    def test_break_deducted_over_trigger(self, make_employee, make_shift, settings_writer):
        settings_writer(break_enabled=True, break_minutes=30, break_trigger=5.0)
        make_employee(rate=20.0)
        make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")  # 8h > 5h trigger
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.regular_hours == 7.5
        assert r.regular_pay == 150.0

    def test_no_break_under_trigger(self, make_employee, make_shift, settings_writer):
        settings_writer(break_enabled=True)
        make_employee(rate=20.0)
        make_shift("2026-06-29T09:00:00", "2026-06-29T13:00:00")  # 4h < 5h trigger
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.regular_hours == 4.0

    def test_override_wins(self, data_dir, make_employee, make_shift, settings_writer):
        import json
        settings_writer(break_enabled=True, break_minutes=30, break_trigger=5.0)
        make_employee(rate=20.0)
        sid = make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        week_dir = data_dir / "2026" / "week-2026-06-28"
        (week_dir / "adjustments.json").write_text(json.dumps({sid: {"minutes": 0}}))
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.regular_hours == 8.0  # override says no break was taken


class TestOvertime:
    def test_shift_straddling_threshold_splits(self, make_employee, make_shift, settings_writer):
        settings_writer(ot_enabled=True, ot_threshold=44.0, ot_multiplier=1.5)
        make_employee(rate=20.0)
        # 5 x 8h = 40h regular, then a 6h shift: 4h regular + 2h OT.
        for day in (28, 29, 30):
            make_shift(f"2026-06-{day}T08:00:00", f"2026-06-{day}T16:00:00")
        for day in (1, 2):
            make_shift(f"2026-07-{day:02d}T08:00:00", f"2026-07-{day:02d}T16:00:00")
        make_shift("2026-07-03T08:00:00", "2026-07-03T14:00:00")
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.regular_hours == 44.0
        assert r.overtime_hours == 2.0
        assert r.regular_pay == 880.0
        assert r.overtime_pay == 60.0   # 2h * $20 * 1.5

    def test_ot_priced_at_rate_actually_worked(self, make_employee, make_shift, settings_writer):
        # Chronological allocation: the LATER (higher-rate) shift is the OT one.
        settings_writer(ot_enabled=True, ot_threshold=10.0)
        make_employee(roles=[
            {"id": "r1", "title": "Cook", "department": "Restaurant", "hourly_rate": 20.0},
            {"id": "r2", "title": "Manager", "department": "Bowling", "hourly_rate": 30.0},
        ])
        make_shift("2026-06-29T08:00:00", "2026-06-29T18:00:00", rate=20.0)  # 10h -> all regular
        make_shift("2026-06-30T08:00:00", "2026-06-30T12:00:00", rate=30.0,
                   role_id="r2", role_title="Manager", department="Bowling")  # 4h -> all OT
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.regular_pay == 200.0          # 10h * $20
        assert r.overtime_pay == 180.0         # 4h * $30 * 1.5
        assert r.overtime_hours == 4.0

    def test_ot_disabled(self, make_employee, make_shift, settings_writer):
        settings_writer(ot_enabled=False)
        make_employee(rate=20.0)
        for day in range(28, 31):
            make_shift(f"2026-06-{day}T00:00:00", f"2026-06-{day}T20:00:00")  # 60h total
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.overtime_hours == 0.0
        assert r.regular_hours == 60.0


class TestVacationAndFlags:
    def test_vacation_pay_percent(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee(rate=20.0, vacation_pay_percent=4.0)
        make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")  # $160
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.vacation_pay == 6.40          # 4% of $160
        assert r.total_pay == 160.0            # wages exclude vacation

    def test_auto_clocked_out_flagged(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-29T09:00:00", "2026-06-30T09:30:00", auto_clocked_out=True)
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.auto_clocked_out_count == 1

    def test_short_shift_flagged(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-29T09:00:00", "2026-06-29T11:00:00")  # 2h < 3h
        make_shift("2026-06-30T09:00:00", "2026-06-30T12:00:00")  # exactly 3h: not short
        r = _row(payroll.compute_payroll(*RANGE))
        assert r.short_shift_count == 1

    def test_flags_reach_csv(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee(rate=20.0)
        make_shift("2026-06-29T09:00:00", "2026-06-29T11:00:00")
        make_shift("2026-06-30T09:00:00", "2026-07-01T09:30:00", auto_clocked_out=True)
        rows = payroll.compute_payroll(*RANGE)
        csv_text = payroll.to_csv(rows, *RANGE)
        assert "AUTO-CLOSED" in csv_text
        assert "3-HOUR RULE" in csv_text
        assert "Vacation Pay" in csv_text

    def test_csv_has_new_sections_and_totals(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee(rate=20.0)
        make_shift("2026-06-29T09:00:00", "2026-06-29T13:00:00")  # 4h paid, $80
        csv_text = payroll.to_csv(payroll.compute_payroll(*RANGE), *RANGE)
        # New header/metadata + columns.
        assert "Generated " in csv_text
        assert "Total Payable" in csv_text and "Total Hours" in csv_text
        # Grand-totals row.
        assert "TOTAL" in csv_text
        # Itemized timesheet section with the shift's date and times.
        assert "Itemized shifts" in csv_text
        assert "2026-06-29" in csv_text and "09:00" in csv_text and "13:00" in csv_text

    def test_shift_details_populated_and_sorted(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-30T09:00:00", "2026-06-30T17:00:00")
        make_shift("2026-06-29T09:00:00", "2026-06-29T12:00:00")
        r = _row(payroll.compute_payroll(*RANGE))
        assert [d.date for d in r.shift_details] == ["2026-06-29", "2026-06-30"]
        assert r.shift_details[0].paid_hours == 3.0
        assert r.shift_details[0].clock_out == "12:00"


class TestFlaggedShifts:
    def test_open_shift_flagged(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-29T09:00:00", None)
        r = _row(payroll.compute_payroll(*RANGE))
        assert len(r.flagged_shifts) == 1
        fs = r.flagged_shifts[0]
        assert fs.reasons == ["Open — no clock-out"]
        assert fs.clock_out is None and fs.hours is None
        assert fs.week_start == "2026-06-28"

    def test_short_shift_flagged(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-29T09:00:00", "2026-06-29T11:00:00")  # 2h
        fs = _row(payroll.compute_payroll(*RANGE)).flagged_shifts
        assert len(fs) == 1
        assert "Under 3h" in fs[0].reasons
        assert fs[0].hours == 2.0

    def test_auto_closed_flagged(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-29T09:00:00", "2026-06-30T09:30:00", auto_clocked_out=True)
        fs = _row(payroll.compute_payroll(*RANGE)).flagged_shifts
        assert len(fs) == 1
        assert "Auto clock-out" in fs[0].reasons

    def test_clean_shift_not_flagged(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")  # 8h, clean
        assert _row(payroll.compute_payroll(*RANGE)).flagged_shifts == []

    def test_row_to_dict_shape(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee()
        make_shift("2026-06-29T09:00:00", "2026-06-29T11:00:00")  # short
        d = payroll.row_to_dict(_row(payroll.compute_payroll(*RANGE)))
        assert d["name"] == "Jane Doe"
        assert d["flag_count"] == 1
        assert isinstance(d["flagged_shifts"], list)
        assert d["flagged_shifts"][0]["reasons"] == ["Under 3h"]

    def test_flag_count_sums_all_issues(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee(rate=5.0)  # below min wage
        make_shift("2026-06-29T09:00:00", None)                    # open
        make_shift("2026-06-30T09:00:00", "2026-06-30T11:00:00")   # short
        d = payroll.row_to_dict(_row(payroll.compute_payroll(*RANGE)))
        # 1 open + 1 short + below_min_wage(1) = 3
        assert d["flag_count"] == 3


class TestHolidayPay:
    def test_esa_formula(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee(rate=20.0, vacation_pay_percent=4.0)
        # Canada Day Wed 2026-07-01 -> its week starts Sun Jun 28; the window
        # is the 4 weeks May 31 .. Jun 27. Put two 8h shifts inside it.
        make_shift("2026-06-15T09:00:00", "2026-06-15T17:00:00")
        make_shift("2026-06-22T09:00:00", "2026-06-22T17:00:00")
        rows, start, end = payroll.compute_holiday_pay(date(2026, 7, 1))
        assert (start, end) == (date(2026, 5, 31), date(2026, 6, 27))
        r = rows[0]
        assert r.regular_wages == 320.0
        assert r.vacation_pay == 12.80
        assert r.holiday_pay == 16.64          # (320 + 12.80) / 20

    def test_wages_outside_window_ignored(self, make_employee, make_shift, settings_writer):
        settings_writer()
        make_employee(rate=20.0)
        # In the holiday's own week — must NOT count.
        make_shift("2026-06-29T09:00:00", "2026-06-29T17:00:00")
        rows, _, _ = payroll.compute_holiday_pay(date(2026, 7, 1))
        assert rows == []
