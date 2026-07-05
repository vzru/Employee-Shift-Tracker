"""
Payroll computation: turn stored shifts into per-employee hours and pay for a
date range, applying the configurable Ontario rules (unpaid break deduction and
weekly overtime), then render a CSV.

IMPORTANT LIMITATION (documented in the UI and README): overtime is a WEEKLY
calculation. When an export range does not align to whole work weeks, the 44-hour
threshold is applied only to the hours that fall inside the range for each work
week — so a range that splits a work week can under-count overtime. For accurate
overtime, choose a range aligned to your configured work-week start.

RATES AND ROLES: each shift snapshots the role (title/department/hourly_rate)
that was picked at clock-in, so a later rate change never rewrites the pay of
shifts already worked. Within a work week, a given employee's shifts may carry
different rates (different roles). Regular-vs-overtime hours are allocated
CHRONOLOGICALLY by clock-in time: hours are "regular" until the weekly
threshold is reached, and anything after that is "overtime" — priced at
whichever role's rate was actually being worked when those hours accrued (a
shift that itself straddles the threshold is split between the two). Shifts
recorded before roles existed have no rate snapshot and are priced at $0 —
see the migration note in repo.py.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import repo
from .models import Settings
from .timeutil import hours_between, parse_iso, week_start_for


@dataclass
class WeeklyHours:
    week_start: date
    hours: float


@dataclass
class RolePay:
    role_title: str
    department: str
    hours: float
    pay: float


@dataclass
class PayrollRow:
    employee_id: str
    name: str
    regular_hours: float = 0.0
    overtime_hours: float = 0.0
    regular_pay: float = 0.0
    overtime_pay: float = 0.0
    total_pay: float = 0.0
    open_shift_count: int = 0          # open shifts in range, excluded from totals
    below_min_wage: bool = False       # any current role paid below minimum wage
    # Total worked hours per work-week, sorted by week_start ascending.
    weekly_hours: list[WeeklyHours] = field(default_factory=list)
    # Hours/pay broken out by role, sorted by (department, role_title).
    role_pay: list[RolePay] = field(default_factory=list)
    # work-week-start -> [(clock_in, adjusted_hours, rate, role_title, department), ...]
    _week_shifts: dict[date, list[tuple]] = field(default_factory=dict)


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


def _week_keys_for_range(start: date, end: date, week_start_weekday: int) -> set[date]:
    """Every work-week start date whose file might hold a shift clocked in the range."""
    keys: set[date] = set()
    day = start
    while day <= end:
        # Use noon to avoid any midnight edge ambiguity.
        keys.add(week_start_for(datetime(day.year, day.month, day.day, 12), week_start_weekday))
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
            below_min_wage=any(r.hourly_rate < settings.min_wage.rate for r in e.roles),
        )
        for e in employees.values()
    }

    wsw = settings.overtime.week_start_weekday
    for week_start in _week_keys_for_range(start, end, wsw):
        overrides = repo.load_adjustments(week_start)
        for shift in repo.load_week_shifts(week_start):
            if shift.voided:
                continue  # soft-deleted: kept on disk, excluded from payroll
            ci = parse_iso(shift.clock_in)
            if not (start <= ci.date() <= end):
                continue  # shift's clock-in day is outside the export range
            row = rows.get(shift.employee_id)
            if row is None:
                # Shift references an employee no longer in employees.json.
                row = PayrollRow(
                    employee_id=shift.employee_id,
                    name=f"(unknown id {shift.employee_id})",
                )
                rows[shift.employee_id] = row

            if shift.clock_out is None:
                row.open_shift_count += 1  # flagged, never counted as hours
                continue

            duration = hours_between(shift.clock_in, shift.clock_out)
            deduct = _break_minutes_for_shift(
                duration, shift.id, overrides, settings
            )
            adjusted = max(0.0, duration - deduct / 60.0)

            rate = shift.hourly_rate if shift.hourly_rate is not None else 0.0
            role_title = shift.role_title or "(no role)"
            department = shift.department or "(no department)"

            ws = week_start_for(ci, wsw)
            row._week_shifts.setdefault(ws, []).append(
                (ci, adjusted, rate, role_title, department)
            )

    ot = settings.overtime
    for row in rows.values():
        row.weekly_hours = sorted(
            (
                WeeklyHours(week_start=ws, hours=round(sum(item[1] for item in items), 2))
                for ws, items in row._week_shifts.items()
            ),
            key=lambda wh: wh.week_start,
        )

        role_pay_map: dict[tuple[str, str], RolePay] = {}
        for items in row._week_shifts.values():
            running = 0.0
            # Chronological allocation: hours accrue regular-then-overtime in
            # the order they were actually worked, each priced at that
            # shift's own rate.
            for _ci, hrs, rate, role_title, department in sorted(items, key=lambda t: t[0]):
                if ot.enabled:
                    regular_capacity = max(0.0, ot.weekly_threshold - running)
                    reg_h = min(hrs, regular_capacity)
                else:
                    reg_h = hrs
                ot_h = hrs - reg_h
                running += hrs

                reg_pay = reg_h * rate
                ot_pay = ot_h * rate * ot.multiplier
                row.regular_hours += reg_h
                row.overtime_hours += ot_h
                row.regular_pay += reg_pay
                row.overtime_pay += ot_pay

                key = (role_title, department)
                rp = role_pay_map.setdefault(
                    key, RolePay(role_title=role_title, department=department, hours=0.0, pay=0.0)
                )
                rp.hours += hrs
                rp.pay += reg_pay + ot_pay

        row.role_pay = sorted(
            role_pay_map.values(), key=lambda rp: (rp.department.lower(), rp.role_title.lower())
        )

        row.regular_hours = round(row.regular_hours, 2)
        row.overtime_hours = round(row.overtime_hours, 2)
        row.regular_pay = round(row.regular_pay, 2)
        row.overtime_pay = round(row.overtime_pay, 2)
        row.total_pay = round(row.regular_pay + row.overtime_pay, 2)
        for rp in row.role_pay:
            rp.hours = round(rp.hours, 2)
            rp.pay = round(rp.pay, 2)

    # Stable, human-friendly ordering by name.
    return sorted(rows.values(), key=lambda r: r.name.lower())


def to_csv(rows: list[PayrollRow], start: date, end: date) -> str:
    """Render payroll rows to a CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"Payroll export {start.isoformat()} to {end.isoformat()}"])
    writer.writerow([
        "Employee", "Regular Hours", "Overtime Hours",
        "Regular Pay", "Overtime Pay", "Total Pay", "Flags",
    ])
    for r in rows:
        flags = []
        if r.open_shift_count:
            flags.append(f"{r.open_shift_count} OPEN SHIFT(S) EXCLUDED")
        if r.below_min_wage:
            flags.append("A ROLE IS RATED BELOW MIN WAGE")
        writer.writerow([
            r.name,
            f"{r.regular_hours:.2f}",
            f"{r.overtime_hours:.2f}",
            f"{r.regular_pay:.2f}",
            f"{r.overtime_pay:.2f}",
            f"{r.total_pay:.2f}",
            "; ".join(flags),
        ])
    writer.writerow([])
    writer.writerow(["Weekly hours by employee"])
    writer.writerow(["Employee", "Week starting", "Hours"])
    for r in rows:
        for wh in r.weekly_hours:
            writer.writerow([r.name, wh.week_start.isoformat(), f"{wh.hours:.2f}"])

    writer.writerow([])
    writer.writerow(["Pay by role"])
    writer.writerow(["Employee", "Department", "Role", "Hours", "Pay"])
    for r in rows:
        for rp in r.role_pay:
            writer.writerow([r.name, rp.department, rp.role_title, f"{rp.hours:.2f}", f"{rp.pay:.2f}"])

    writer.writerow([])
    writer.writerow([
        "These are configurable estimates, not legal or payroll advice. "
        "Verify against the current Ontario Employment Standards Act."
    ])
    return buf.getvalue()
