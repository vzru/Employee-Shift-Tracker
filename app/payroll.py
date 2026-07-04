"""
Payroll computation: turn stored shifts into per-employee hours and pay for a
date range, applying the configurable Ontario rules (unpaid break deduction and
weekly overtime), then render a CSV.

IMPORTANT LIMITATION (documented in the UI and README): overtime is a WEEKLY
calculation. When an export range does not align to whole work weeks, the 44-hour
threshold is applied only to the hours that fall inside the range for each work
week — so a range that splits a work week can under-count overtime. For accurate
overtime, choose a range aligned to your configured work-week start.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import repo
from .models import Settings
from .timeutil import iso_year_week, parse_iso, week_start_for


@dataclass
class PayrollRow:
    employee_id: str
    name: str
    pay_rate: float
    regular_hours: float = 0.0
    overtime_hours: float = 0.0
    regular_pay: float = 0.0
    overtime_pay: float = 0.0
    total_pay: float = 0.0
    open_shift_count: int = 0          # open shifts in range, excluded from totals
    below_min_wage: bool = False
    # work-week-start -> break-adjusted worked hours (intermediate accumulator)
    _week_hours: dict[date, float] = field(default_factory=dict)


def _hours_between(clock_in_iso: str, clock_out_iso: str) -> float:
    delta = parse_iso(clock_out_iso) - parse_iso(clock_in_iso)
    return delta.total_seconds() / 3600.0


def _break_minutes_for_shift(
    duration_hours: float, shift_id: str, overrides: dict[str, dict],
    settings: Settings,
) -> int:
    """
    Unpaid break minutes to deduct from a shift. A manual per-shift override (from
    adjustments.json) always wins; otherwise the automatic rule applies: deduct
    the configured duration only when break rules are on AND the shift exceeds the
    trigger hours.
    """
    if shift_id in overrides:
        return int(overrides[shift_id].get("minutes", 0))
    b = settings.break_rules
    if b.enabled and duration_hours > b.trigger_hours:
        return int(b.duration_minutes)
    return 0


def _week_keys_for_range(start: date, end: date) -> set[tuple[int, int]]:
    """Every ISO (year, week) whose file might hold a shift clocked in the range."""
    keys: set[tuple[int, int]] = set()
    day = start
    while day <= end:
        # Use noon to avoid any midnight edge ambiguity.
        keys.add(iso_year_week(datetime(day.year, day.month, day.day, 12)))
        day += timedelta(days=1)
    return keys


def compute_payroll(start: date, end: date) -> list[PayrollRow]:
    """
    Compute payroll rows for all employees over [start, end] inclusive (by the
    date of each shift's clock-in).
    """
    settings = repo.load_settings()
    employees = {e.id: e for e in repo.load_employees()}
    rows: dict[str, PayrollRow] = {
        e.id: PayrollRow(
            employee_id=e.id,
            name=e.name,
            pay_rate=e.hourly_rate,
            below_min_wage=e.hourly_rate < settings.min_wage.rate,
        )
        for e in employees.values()
    }

    for (yr, wk) in _week_keys_for_range(start, end):
        overrides = repo.load_adjustments(yr, wk)
        for shift in repo.load_week_shifts(yr, wk):
            ci = parse_iso(shift.clock_in)
            if not (start <= ci.date() <= end):
                continue  # shift's clock-in day is outside the export range
            row = rows.get(shift.employee_id)
            if row is None:
                # Shift references an employee no longer in employees.json.
                row = PayrollRow(
                    employee_id=shift.employee_id,
                    name=f"(unknown id {shift.employee_id})",
                    pay_rate=0.0,
                )
                rows[shift.employee_id] = row

            if shift.clock_out is None:
                row.open_shift_count += 1  # flagged, never counted as hours
                continue

            duration = _hours_between(shift.clock_in, shift.clock_out)
            deduct = _break_minutes_for_shift(
                duration, shift.id, overrides, settings
            )
            adjusted = max(0.0, duration - deduct / 60.0)

            ws = week_start_for(ci, settings.overtime.week_start_weekday)
            row._week_hours[ws] = row._week_hours.get(ws, 0.0) + adjusted

    # Split each work-week's hours into regular vs overtime, then price it.
    ot = settings.overtime
    for row in rows.values():
        for _week_start, hours in row._week_hours.items():
            if ot.enabled and hours > ot.weekly_threshold:
                overtime_h = hours - ot.weekly_threshold
                regular_h = ot.weekly_threshold
            else:
                overtime_h = 0.0
                regular_h = hours
            row.regular_hours += regular_h
            row.overtime_hours += overtime_h

        row.regular_hours = round(row.regular_hours, 2)
        row.overtime_hours = round(row.overtime_hours, 2)
        row.regular_pay = round(row.regular_hours * row.pay_rate, 2)
        row.overtime_pay = round(
            row.overtime_hours * row.pay_rate * ot.multiplier, 2
        )
        row.total_pay = round(row.regular_pay + row.overtime_pay, 2)

    # Stable, human-friendly ordering by name.
    return sorted(rows.values(), key=lambda r: r.name.lower())


def to_csv(rows: list[PayrollRow], start: date, end: date) -> str:
    """Render payroll rows to a CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"Payroll export {start.isoformat()} to {end.isoformat()}"])
    writer.writerow([
        "Employee", "Regular Hours", "Overtime Hours", "Pay Rate",
        "Regular Pay", "Overtime Pay", "Total Pay", "Flags",
    ])
    for r in rows:
        flags = []
        if r.open_shift_count:
            flags.append(f"{r.open_shift_count} OPEN SHIFT(S) EXCLUDED")
        if r.below_min_wage:
            flags.append("RATE BELOW MIN WAGE")
        writer.writerow([
            r.name,
            f"{r.regular_hours:.2f}",
            f"{r.overtime_hours:.2f}",
            f"{r.pay_rate:.2f}",
            f"{r.regular_pay:.2f}",
            f"{r.overtime_pay:.2f}",
            f"{r.total_pay:.2f}",
            "; ".join(flags),
        ])
    writer.writerow([])
    writer.writerow([
        "These are configurable estimates, not legal or payroll advice. "
        "Verify against the current Ontario Employment Standards Act."
    ])
    return buf.getvalue()
