"""
Repository layer: typed read/write helpers for each data file, plus the
clock-in / clock-out domain logic. Everything goes through storage.py so writes
are atomic and serialized.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Optional

from . import paths, storage
from .models import AdminData, Employee, Settings, Shift
from .timeutil import hours_between, now_local, parse_iso, week_start_for

# How many work weeks back to scan when locating open shifts (covers shifts
# that span a week boundary, e.g. an overnight shift started late on the last
# day of the work week).
_OPEN_SCAN_WEEKS = 3


def new_id() -> str:
    """Short opaque id for employees and shifts."""
    return uuid.uuid4().hex[:12]


# --- Employees ---------------------------------------------------------------

def load_employees() -> list[Employee]:
    raw = storage.read_json(paths.employees_file(), default=[])
    return [Employee(**_migrate_employee(e)) for e in raw]


def _migrate_employee(e: dict) -> dict:
    """
    Back-compat for employees.json written before two schema changes:

    1. A single "name" field split into first_name/last_name. Splits on the
       first space; a single-word name (e.g. "Victor") becomes
       first_name="Victor", last_name="".
    2. A single flat "hourly_rate" replaced by a list of Roles (title,
       department, rate). A legacy flat rate becomes one "General" role in a
       "General" department, so existing employees keep working unchanged
       until an admin edits them to add real roles/departments.
    """
    if "name" in e and "first_name" not in e:
        e = dict(e)
        first, _, last = e.pop("name").partition(" ")
        e["first_name"] = first
        e["last_name"] = last
    if "roles" not in e and "hourly_rate" in e:
        e = dict(e)
        e["roles"] = [{
            "id": new_id(), "title": "General", "department": "General",
            "hourly_rate": e.pop("hourly_rate"),
        }]
    return e


def save_employees(employees: list[Employee]) -> None:
    storage.write_json(
        paths.employees_file(), [e.model_dump() for e in employees]
    )


def get_employee(employee_id: str) -> Optional[Employee]:
    for e in load_employees():
        if e.id == employee_id:
            return e
    return None


# --- Admin / settings --------------------------------------------------------

def load_admin() -> AdminData:
    raw = storage.read_json(paths.admin_file(), default=None)
    if raw is None:
        return AdminData()  # first run: no password, default settings
    return AdminData(**raw)


def save_admin(admin: AdminData) -> None:
    storage.write_json(paths.admin_file(), admin.model_dump())


def load_settings() -> Settings:
    return load_admin().settings


def save_settings(settings: Settings) -> None:
    admin = load_admin()
    admin.settings = settings
    save_admin(admin)


# --- Shifts ------------------------------------------------------------------

def _load_week_shifts_raw(week_start: date) -> list[dict]:
    return storage.read_json(paths.shifts_file(week_start), default=[])


def load_week_shifts(week_start: date) -> list[Shift]:
    return [Shift(**s) for s in _load_week_shifts_raw(week_start)]


def _week_start_weekday() -> int:
    return load_settings().overtime.week_start_weekday


def _recent_week_keys(count: int = _OPEN_SCAN_WEEKS) -> list[date]:
    """Start dates of the current work week and the prior ``count-1`` weeks."""
    wsw = _week_start_weekday()
    cursor = now_local()
    keys = [week_start_for(cursor, wsw)]
    for _ in range(count - 1):
        cursor -= timedelta(days=7)
        keys.append(week_start_for(cursor, wsw))
    return keys


def find_open_shift(employee_id: str) -> Optional[tuple[date, Shift]]:
    """
    Locate an employee's currently-open shift (clock_out is None), scanning the
    most recent weeks. Returns (week_start, shift) or None.
    """
    for week_start in _recent_week_keys():
        for s in load_week_shifts(week_start):
            if s.employee_id == employee_id and s.clock_out is None:
                return (week_start, s)
    return None


def open_shifts_by_employee() -> dict[str, Shift]:
    """Map employee_id -> their open Shift, across recent weeks (for the kiosk)."""
    result: dict[str, Shift] = {}
    for week_start in _recent_week_keys():
        for s in load_week_shifts(week_start):
            if s.clock_out is None and s.employee_id not in result:
                result[s.employee_id] = s
    return result


def clock_in(employee_id: str, timestamp_iso: str, role_id: str) -> Shift:
    """
    Open a new shift for the employee, working ``role_id``, at
    ``timestamp_iso``. Refuses if the employee already has an open shift. The
    role's title/department/hourly_rate are SNAPSHOTTED onto the shift so a
    later edit to the role never rewrites the pay of shifts already worked.
    The new shift is filed in the work week (see
    Settings.overtime.week_start_weekday) of its clock-in time. Atomic under
    the storage lock.
    """
    if find_open_shift(employee_id) is not None:
        raise ValueError("Employee is already clocked in.")

    employee = get_employee(employee_id)
    if employee is None:
        raise ValueError("Unknown employee.")
    role = next((r for r in employee.roles if r.id == role_id), None)
    if role is None:
        raise ValueError("Unknown role.")

    when = parse_iso(timestamp_iso)
    week_start = week_start_for(when, _week_start_weekday())
    shift = Shift(
        id=new_id(),
        employee_id=employee_id,
        clock_in=timestamp_iso,
        clock_out=None,
        role_id=role.id,
        role_title=role.title,
        department=role.department,
        hourly_rate=role.hourly_rate,
    )

    def mutator(current: list[dict]) -> list[dict]:
        current.append(shift.model_dump())
        return current

    storage.update_json(paths.shifts_file(week_start), default=[], mutator=mutator)
    return shift


def clock_out(employee_id: str, timestamp_iso: str) -> Shift:
    """
    Close the employee's open shift at ``timestamp_iso``. Validates that the
    clock-out is not before the clock-in, and fills in the shift's raw "hours"
    (clock_out - clock_in, not break-adjusted). Atomic under the storage lock.
    """
    found = find_open_shift(employee_id)
    if found is None:
        raise ValueError("Employee is not clocked in.")
    week_start, shift = found

    if parse_iso(timestamp_iso) < parse_iso(shift.clock_in):
        raise ValueError("Clock-out time cannot be before clock-in time.")

    hours = round(hours_between(shift.clock_in, timestamp_iso), 2)

    def mutator(current: list[dict]) -> list[dict]:
        for s in current:
            if s["id"] == shift.id:
                s["clock_out"] = timestamp_iso
                s["hours"] = hours
        return current

    storage.update_json(paths.shifts_file(week_start), default=[], mutator=mutator)
    shift.clock_out = timestamp_iso
    shift.hours = hours
    return shift


def update_shift(
    week_start: date, shift_id: str,
    clock_in_iso: str, clock_out_iso: Optional[str],
) -> None:
    """Admin correction of a shift's times within a known week file. Recomputes
    the stored "hours" field to match the corrected times."""
    if clock_out_iso and parse_iso(clock_out_iso) < parse_iso(clock_in_iso):
        raise ValueError("Clock-out time cannot be before clock-in time.")

    hours = round(hours_between(clock_in_iso, clock_out_iso), 2) if clock_out_iso else None

    def mutator(current: list[dict]) -> list[dict]:
        for s in current:
            if s["id"] == shift_id:
                s["clock_in"] = clock_in_iso
                s["clock_out"] = clock_out_iso if clock_out_iso else None
                s["hours"] = hours
        return current

    storage.update_json(
        paths.shifts_file(week_start), default=[], mutator=mutator
    )


def delete_shift(week_start: date, shift_id: str) -> None:
    def mutator(current: list[dict]) -> list[dict]:
        return [s for s in current if s["id"] != shift_id]

    storage.update_json(
        paths.shifts_file(week_start), default=[], mutator=mutator
    )


# --- Per-shift break overrides (adjustments.json per week) -------------------

def _adjustments_file(week_start: date):
    return paths.week_dir(week_start) / "adjustments.json"


def load_adjustments(week_start: date) -> dict[str, dict]:
    return storage.read_json(_adjustments_file(week_start), default={})


def set_break_override(
    week_start: date, shift_id: str, minutes: Optional[int]
) -> None:
    """
    Set (or clear, when minutes is None) the manual unpaid-break minutes for a
    single shift. Stored separately so shifts.json keeps exactly its four fields.
    """
    def mutator(current: dict) -> dict:
        if minutes is None:
            current.pop(shift_id, None)
        else:
            current[shift_id] = {"minutes": int(minutes)}
        return current

    storage.update_json(
        _adjustments_file(week_start), default={}, mutator=mutator
    )
