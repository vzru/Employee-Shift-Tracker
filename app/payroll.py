"""
Payroll computation: turn stored shifts into per-employee hours and pay for a
date range, applying the configurable Ontario rules (unpaid break deduction,
weekly overtime, vacation pay accrual), then render a CSV.

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

MONEY: pay is accumulated in Decimal and rounded to cents (half-up) only at
the end, so float drift can never leak into a dollar figure. Hours stay float
(they're durations, and 2-decimal rounding there matches the Shifts page).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from . import repo
from .models import Settings
from .timeutil import hours_between, parse_iso, week_start_for

# Ontario ESA "three-hour rule": an employee who regularly works more than
# three hours a day but is sent home early must still be paid at least three
# hours. Closed shifts shorter than this are flagged (not auto-adjusted — the
# rule has eligibility conditions only the operator can judge).
SHORT_SHIFT_HOURS = 3.0

_CENT = Decimal("0.01")


def _money(value: Decimal) -> float:
    """Round a Decimal dollar amount to cents (half-up) for display/CSV."""
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _dec(value: float) -> Decimal:
    """Float -> Decimal via str() so we get the shortest decimal repr."""
    return Decimal(str(value))


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
class FlaggedShift:
    """A shift behind a payroll flag, so the preview can list the specifics
    (like the Shifts page) instead of only a count."""
    reasons: list[str]              # e.g. ["Open — no clock-out"], ["Auto clock-out", "Under 3h"]
    role_title: str
    department: str
    clock_in: str                  # ISO string
    clock_out: str | None          # ISO string, or None if still open
    hours: float | None            # raw duration, or None if open
    week_start: str                # ISO date of the work week (to find it on the Shifts page)


@dataclass
class PayrollRow:
    employee_id: str
    name: str
    regular_hours: float = 0.0
    overtime_hours: float = 0.0
    regular_pay: float = 0.0
    overtime_pay: float = 0.0
    total_pay: float = 0.0             # regular + overtime wages (excl. vacation)
    vacation_pay_percent: float = 0.0  # employee's accrual rate (ESA min 4%)
    vacation_pay: float = 0.0          # percent of total wages, accrued this range
    open_shift_count: int = 0          # open shifts in range, excluded from totals
    auto_clocked_out_count: int = 0    # auto-closed shifts in range — times need verifying
    short_shift_count: int = 0         # closed shifts under 3h — ESA 3-hour rule may apply
    below_min_wage: bool = False       # any current role paid below minimum wage
    # Total worked hours per work-week, sorted by week_start ascending.
    weekly_hours: list[WeeklyHours] = field(default_factory=list)
    # Hours/pay broken out by role, sorted by (department, role_title).
    role_pay: list[RolePay] = field(default_factory=list)
    # The individual shifts behind the flags above (open / auto-closed / short),
    # so the preview can show details on demand.
    flagged_shifts: list[FlaggedShift] = field(default_factory=list)
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
            vacation_pay_percent=e.vacation_pay_percent,
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

            shift_week = week_start_for(ci, wsw).isoformat()

            if shift.clock_out is None:
                row.open_shift_count += 1  # flagged, never counted as hours
                row.flagged_shifts.append(FlaggedShift(
                    reasons=["Open — no clock-out"],
                    role_title=shift.role_title or "(no role)",
                    department=shift.department or "(no department)",
                    clock_in=shift.clock_in, clock_out=None, hours=None,
                    week_start=shift_week,
                ))
                continue

            duration = hours_between(shift.clock_in, shift.clock_out)

            # Flags that need the operator's eye — counted here so the payroll
            # preview and CSV warn even when the Shifts page wasn't visited:
            # an uncorrected auto-clockout (bogus ~24h end time) or a shift the
            # ESA three-hour rule may top up.
            reasons: list[str] = []
            if shift.auto_clocked_out:
                row.auto_clocked_out_count += 1
                reasons.append("Auto clock-out")
            if duration < SHORT_SHIFT_HOURS:
                row.short_shift_count += 1
                reasons.append("Under 3h")
            if reasons:
                row.flagged_shifts.append(FlaggedShift(
                    reasons=reasons,
                    role_title=shift.role_title or "(no role)",
                    department=shift.department or "(no department)",
                    clock_in=shift.clock_in, clock_out=shift.clock_out,
                    hours=round(duration, 2), week_start=shift_week,
                ))

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
    ot_multiplier = _dec(ot.multiplier)
    for row in rows.values():
        row.weekly_hours = sorted(
            (
                WeeklyHours(week_start=ws, hours=round(sum(item[1] for item in items), 2))
                for ws, items in row._week_shifts.items()
            ),
            key=lambda wh: wh.week_start,
        )

        # Money accumulates in Decimal; only the final row fields are floats.
        reg_pay = Decimal(0)
        ot_pay = Decimal(0)
        # (role_title, department) -> [hours_float, pay_Decimal]
        role_acc: dict[tuple[str, str], list] = {}

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

                shift_reg_pay = _dec(reg_h) * _dec(rate)
                shift_ot_pay = _dec(ot_h) * _dec(rate) * ot_multiplier
                row.regular_hours += reg_h
                row.overtime_hours += ot_h
                reg_pay += shift_reg_pay
                ot_pay += shift_ot_pay

                acc = role_acc.setdefault((role_title, department), [0.0, Decimal(0)])
                acc[0] += hrs
                acc[1] += shift_reg_pay + shift_ot_pay

        row.role_pay = sorted(
            (
                RolePay(role_title=k[0], department=k[1],
                        hours=round(v[0], 2), pay=_money(v[1]))
                for k, v in role_acc.items()
            ),
            key=lambda rp: (rp.department.lower(), rp.role_title.lower()),
        )

        total = reg_pay + ot_pay
        vacation = total * _dec(row.vacation_pay_percent) / Decimal(100)

        row.regular_hours = round(row.regular_hours, 2)
        row.overtime_hours = round(row.overtime_hours, 2)
        row.regular_pay = _money(reg_pay)
        row.overtime_pay = _money(ot_pay)
        row.total_pay = _money(total)
        row.vacation_pay = _money(vacation)

    # Stable, human-friendly ordering by name.
    return sorted(rows.values(), key=lambda r: r.name.lower())


# --- Public holiday pay (Ontario ESA) -----------------------------------------

@dataclass
class HolidayPayRow:
    name: str
    regular_wages: float       # regular (non-OT) wages in the 4 prior work weeks
    vacation_pay: float        # vacation pay accrued on those wages
    holiday_pay: float         # (regular_wages + vacation_pay) / 20
    open_shift_count: int      # open shifts in the window — wages under-counted


def compute_holiday_pay(holiday: date) -> tuple[list[HolidayPayRow], date, date]:
    """
    Ontario ESA public-holiday pay estimate: (regular wages earned in the four
    work weeks before the work week containing the holiday, plus the vacation
    pay payable on those wages) divided by 20. Overtime pay is excluded from
    "regular wages" per the ESA definition. Eligibility (the "last and first"
    rule etc.) is NOT checked — the operator decides who qualifies.

    Returns (rows, window_start, window_end) so the UI can show the window.
    """
    wsw = repo.load_settings().overtime.week_start_weekday
    holiday_week_start = week_start_for(holiday, wsw)
    start = holiday_week_start - timedelta(days=28)
    end = holiday_week_start - timedelta(days=1)

    rows = []
    for r in compute_payroll(start, end):
        if r.regular_pay <= 0 and not r.open_shift_count:
            continue  # nothing earned in the window
        reg = _dec(r.regular_pay)
        vac = reg * _dec(r.vacation_pay_percent) / Decimal(100)
        rows.append(HolidayPayRow(
            name=r.name,
            regular_wages=r.regular_pay,
            vacation_pay=_money(vac),
            holiday_pay=_money((reg + vac) / Decimal(20)),
            open_shift_count=r.open_shift_count,
        ))
    return rows, start, end


def row_to_dict(r: PayrollRow) -> dict:
    """Plain JSON-able view of a payroll row for the interactive preview
    (client-side sorting + the flagged-shift detail popup)."""
    return {
        "employee_id": r.employee_id,
        "name": r.name,
        "regular_hours": r.regular_hours,
        "overtime_hours": r.overtime_hours,
        "regular_pay": r.regular_pay,
        "overtime_pay": r.overtime_pay,
        "total_pay": r.total_pay,
        "vacation_pay": r.vacation_pay,
        "vacation_pay_percent": r.vacation_pay_percent,
        "open_shift_count": r.open_shift_count,
        "auto_clocked_out_count": r.auto_clocked_out_count,
        "short_shift_count": r.short_shift_count,
        "below_min_wage": r.below_min_wage,
        # Total issues, for sorting the Flags column.
        "flag_count": (r.open_shift_count + r.auto_clocked_out_count
                       + r.short_shift_count + (1 if r.below_min_wage else 0)),
        "flagged_shifts": [
            {
                "reasons": fs.reasons,
                "role_title": fs.role_title,
                "department": fs.department,
                "clock_in": fs.clock_in,
                "clock_out": fs.clock_out,
                "hours": fs.hours,
                "week_start": fs.week_start,
            }
            for fs in r.flagged_shifts
        ],
    }


def to_csv(rows: list[PayrollRow], start: date, end: date) -> str:
    """Render payroll rows to a CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"Payroll export {start.isoformat()} to {end.isoformat()}"])
    writer.writerow([
        "Employee", "Regular Hours", "Overtime Hours",
        "Regular Pay", "Overtime Pay", "Total Wages",
        "Vacation Pay %", "Vacation Pay", "Flags",
    ])
    for r in rows:
        flags = []
        if r.open_shift_count:
            flags.append(f"{r.open_shift_count} OPEN SHIFT(S) EXCLUDED")
        if r.auto_clocked_out_count:
            flags.append(
                f"{r.auto_clocked_out_count} AUTO-CLOSED SHIFT(S) - VERIFY TIMES"
            )
        if r.short_shift_count:
            flags.append(
                f"{r.short_shift_count} SHIFT(S) UNDER {SHORT_SHIFT_HOURS:g}H - 3-HOUR RULE MAY APPLY"
            )
        if r.below_min_wage:
            flags.append("A ROLE IS RATED BELOW MIN WAGE")
        writer.writerow([
            r.name,
            f"{r.regular_hours:.2f}",
            f"{r.overtime_hours:.2f}",
            f"{r.regular_pay:.2f}",
            f"{r.overtime_pay:.2f}",
            f"{r.total_pay:.2f}",
            f"{r.vacation_pay_percent:g}",
            f"{r.vacation_pay:.2f}",
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
