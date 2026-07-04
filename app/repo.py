"""
Repository layer: typed read/write helpers for each data file, plus the
clock-in / clock-out domain logic. Everything goes through storage.py so writes
are atomic and serialized.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Optional

from . import paths, storage
from .models import AdminData, Employee, Settings, Shift
from .timeutil import iso_year_week, now_local, parse_iso

# How many ISO weeks back to scan when locating open shifts (covers shifts that
# span a week boundary, e.g. an overnight shift started late Sunday).
_OPEN_SCAN_WEEKS = 3


def new_id() -> str:
    """Short opaque id for employees and shifts."""
    return uuid.uuid4().hex[:12]


# --- Employees ---------------------------------------------------------------

def load_employees() -> list[Employee]:
    raw = storage.read_json(paths.employees_file(), default=[])
    return [Employee(**e) for e in raw]


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

def _load_week_shifts_raw(iso_year: int, iso_week: int) -> list[dict]:
    return storage.read_json(paths.shifts_file(iso_year, iso_week), default=[])


def load_week_shifts(iso_year: int, iso_week: int) -> list[Shift]:
    return [Shift(**s) for s in _load_week_shifts_raw(iso_year, iso_week)]


def _recent_week_keys(count: int = _OPEN_SCAN_WEEKS) -> list[tuple[int, int]]:
    """(iso_year, iso_week) for the current week and the prior ``count-1`` weeks."""
    keys: list[tuple[int, int]] = []
    cursor = now_local()
    for _ in range(count):
        keys.append(iso_year_week(cursor))
        cursor -= timedelta(days=7)
    return keys


def find_open_shift(employee_id: str) -> Optional[tuple[int, int, Shift]]:
    """
    Locate an employee's currently-open shift (clock_out is None), scanning the
    most recent weeks. Returns (iso_year, iso_week, shift) or None.
    """
    for (yr, wk) in _recent_week_keys():
        for s in load_week_shifts(yr, wk):
            if s.employee_id == employee_id and s.clock_out is None:
                return (yr, wk, s)
    return None


def open_shifts_by_employee() -> dict[str, Shift]:
    """Map employee_id -> their open Shift, across recent weeks (for the kiosk)."""
    result: dict[str, Shift] = {}
    for (yr, wk) in _recent_week_keys():
        for s in load_week_shifts(yr, wk):
            if s.clock_out is None and s.employee_id not in result:
                result[s.employee_id] = s
    return result


def clock_in(employee_id: str, timestamp_iso: str) -> Shift:
    """
    Open a new shift for the employee at ``timestamp_iso``. Refuses if the
    employee already has an open shift. The new shift is filed in the ISO week of
    its clock-in time. Atomic under the storage lock.
    """
    if find_open_shift(employee_id) is not None:
        raise ValueError("Employee is already clocked in.")

    when = parse_iso(timestamp_iso)
    yr, wk = iso_year_week(when)
    shift = Shift(
        id=new_id(),
        employee_id=employee_id,
        clock_in=timestamp_iso,
        clock_out=None,
    )

    def mutator(current: list[dict]) -> list[dict]:
        current.append(shift.model_dump())
        return current

    storage.update_json(paths.shifts_file(yr, wk), default=[], mutator=mutator)
    return shift


def clock_out(employee_id: str, timestamp_iso: str) -> Shift:
    """
    Close the employee's open shift at ``timestamp_iso``. Validates that the
    clock-out is not before the clock-in. Atomic under the storage lock.
    """
    found = find_open_shift(employee_id)
    if found is None:
        raise ValueError("Employee is not clocked in.")
    yr, wk, shift = found

    if parse_iso(timestamp_iso) < parse_iso(shift.clock_in):
        raise ValueError("Clock-out time cannot be before clock-in time.")

    def mutator(current: list[dict]) -> list[dict]:
        for s in current:
            if s["id"] == shift.id:
                s["clock_out"] = timestamp_iso
        return current

    storage.update_json(paths.shifts_file(yr, wk), default=[], mutator=mutator)
    shift.clock_out = timestamp_iso
    return shift


def update_shift(
    iso_year: int, iso_week: int, shift_id: str,
    clock_in_iso: str, clock_out_iso: Optional[str],
) -> None:
    """Admin correction of a shift's times within a known week file."""
    if clock_out_iso and parse_iso(clock_out_iso) < parse_iso(clock_in_iso):
        raise ValueError("Clock-out time cannot be before clock-in time.")

    def mutator(current: list[dict]) -> list[dict]:
        for s in current:
            if s["id"] == shift_id:
                s["clock_in"] = clock_in_iso
                s["clock_out"] = clock_out_iso if clock_out_iso else None
        return current

    storage.update_json(
        paths.shifts_file(iso_year, iso_week), default=[], mutator=mutator
    )


def delete_shift(iso_year: int, iso_week: int, shift_id: str) -> None:
    def mutator(current: list[dict]) -> list[dict]:
        return [s for s in current if s["id"] != shift_id]

    storage.update_json(
        paths.shifts_file(iso_year, iso_week), default=[], mutator=mutator
    )


# --- Per-shift break overrides (adjustments.json per week) -------------------

def _adjustments_file(iso_year: int, iso_week: int):
    return paths.week_dir(iso_year, iso_week) / "adjustments.json"


def load_adjustments(iso_year: int, iso_week: int) -> dict[str, dict]:
    return storage.read_json(_adjustments_file(iso_year, iso_week), default={})


def set_break_override(
    iso_year: int, iso_week: int, shift_id: str, minutes: Optional[int]
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
        _adjustments_file(iso_year, iso_week), default={}, mutator=mutator
    )
