"""
Repository layer: typed read/write helpers for each data file, plus the
clock-in / clock-out domain logic. Everything goes through storage.py so writes
are atomic and serialized.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Optional

from . import audit, paths, storage
from .models import AdminData, Employee, Settings, Shift
from .timeutil import hours_between, now_local, parse_iso, to_iso, week_start_for

# How many work weeks back the open-shift scan looks (clock actions, kiosk/admin
# page sweeps, and the admin's 12h+ alert). Generous enough to cover any
# realistic forgotten clock-out while auto-clockout is enabled, without reading
# the whole history on every page load.
_OPEN_SCAN_WEEKS = 6

# A currently-open shift (within the routine scan window) running at least this
# long is surfaced to the admin as a specific warning (it may be a forgotten
# clock-out). Matches the kiosk's 12-hour card highlight, and fires before
# auto-clockout's 24h default.
LONG_OPEN_HOURS = 12.0


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


def _recent_week_keys(count: int = _OPEN_SCAN_WEEKS, wsw: int | None = None) -> list[date]:
    """Start dates of the current work week and the prior ``count-1`` weeks.

    ``wsw`` (week-start weekday) can be passed by callers that already loaded
    settings, to avoid re-reading admin.json.
    """
    if wsw is None:
        wsw = _week_start_weekday()
    cursor = now_local()
    keys = [week_start_for(cursor, wsw)]
    for _ in range(count - 1):
        cursor -= timedelta(days=7)
        keys.append(week_start_for(cursor, wsw))
    return keys


def find_open_shift(employee_id: str, wsw: int | None = None) -> Optional[tuple[date, Shift]]:
    """
    Locate an employee's currently-open shift (clock_out is None), scanning the
    most recent weeks. Returns (week_start, shift) or None. Voided shifts are
    ignored — a voided shift is treated as if removed for clock-state purposes.
    """
    for week_start in _recent_week_keys(wsw=wsw):
        for s in load_week_shifts(week_start):
            if s.employee_id == employee_id and s.clock_out is None and not s.voided:
                return (week_start, s)
    return None


def open_shifts_by_employee(wsw: int | None = None) -> dict[str, Shift]:
    """Map employee_id -> their open Shift, across recent weeks (for the kiosk).
    Voided shifts are ignored."""
    result: dict[str, Shift] = {}
    for week_start in _recent_week_keys(wsw=wsw):
        for s in load_week_shifts(week_start):
            if s.clock_out is None and not s.voided and s.employee_id not in result:
                result[s.employee_id] = s
    return result


def long_open_shifts(
    open_map: dict[str, Shift], min_hours: float = LONG_OPEN_HOURS,
) -> list[dict]:
    """
    From an employee_id -> open Shift map (as returned by
    sweep_and_open_shifts, covering the recent scan window), the shifts that
    have been open at least ``min_hours`` — a likely forgotten clock-out.
    Returns dicts with the employee id, shift location and how long it's been
    open, sorted longest-open first. Pure (no I/O); names are resolved by the
    caller.
    """
    now = now_local()
    out: list[dict] = []
    for shift in open_map.values():
        hours_open = (now - parse_iso(shift.clock_in)).total_seconds() / 3600.0
        if hours_open >= min_hours:
            out.append({
                "employee_id": shift.employee_id,
                "shift_id": shift.id,
                "clock_in": shift.clock_in,
                "hours_open": hours_open,
                "role_title": shift.role_title,
                "department": shift.department,
            })
    out.sort(key=lambda x: x["hours_open"], reverse=True)
    return out


def find_misaligned_week_folders() -> list[str]:
    """
    Week folders on disk whose start date doesn't fall on the configured
    week-start weekday. This happens when Settings > "Week starts on" is
    changed after shifts exist: lookups compute keys under the NEW scheme, so
    files stored under the old scheme silently stop being found by the Shifts
    page and payroll. Surfaced as a warning banner in the admin panel; the fix
    is to re-bucket the files (tools/migrate_weeks.py).
    """
    wsw = _week_start_weekday()
    misaligned: list[str] = []
    root = paths.data_dir()
    if not root.exists():
        return misaligned
    for year_dir in root.iterdir():
        if not (year_dir.is_dir() and year_dir.name.isdigit()):
            continue
        for week_dir in year_dir.iterdir():
            if not (week_dir.is_dir() and week_dir.name.startswith("week-")):
                continue
            try:
                start = date.fromisoformat(week_dir.name[len("week-"):])
            except ValueError:
                continue  # not a week folder we recognize
            if start.weekday() != wsw:
                misaligned.append(f"{year_dir.name}/{week_dir.name}")
    return sorted(misaligned)


def sweep_and_open_shifts(
    settings: Optional[Settings] = None,
) -> tuple[list[Shift], dict[str, Shift]]:
    """
    In ONE pass over the recent weeks: auto-close any shift left open past
    Settings.auto_clockout.threshold_hours, and return
    (closed_shifts, open_by_employee) for the remaining state — so a page can
    sweep and read the current open shifts without scanning the files twice.

    Only weeks that actually have a stale shift are rewritten, and each such
    write goes through storage.update_json (atomic, re-checked under the lock)
    so a concurrent clock action can't be clobbered. There's no background
    timer; callers invoke this opportunistically on page load. Closed shifts
    are logged to the audit trail as actor "system".
    """
    if settings is None:
        settings = load_settings()
    ac = settings.auto_clockout
    wsw = settings.overtime.week_start_weekday
    now = now_local()
    now_iso = to_iso(now)
    closed: list[Shift] = []
    open_map: dict[str, Shift] = {}

    for week_start in _recent_week_keys(wsw=wsw):
        raw = _load_week_shifts_raw(week_start)  # single read per week

        # Detect stale open shifts (only if auto-clockout is enabled).
        stale_ids = {
            s["id"] for s in raw
            if ac.enabled and s.get("clock_out") is None and not s.get("voided")
            and (now - parse_iso(s["clock_in"])).total_seconds() / 3600.0 > ac.threshold_hours
        }

        if stale_ids:
            def mutator(current: list[dict], _ids=stale_ids) -> list[dict]:
                for s in current:
                    # Re-check clock_out under the lock so a clock-out that
                    # landed between our read and this write isn't overwritten.
                    if s["id"] in _ids and s.get("clock_out") is None:
                        s["clock_out"] = now_iso
                        s["hours"] = round(hours_between(s["clock_in"], now_iso), 2)
                        s["auto_clocked_out"] = True
                return current

            raw = storage.update_json(paths.shifts_file(week_start), default=[], mutator=mutator)

        for s in raw:
            if s.get("voided"):
                continue
            if s.get("clock_out") is None:
                open_map.setdefault(s["employee_id"], Shift(**s))
            elif s["id"] in stale_ids and s.get("auto_clocked_out"):
                closed.append(Shift(**s))

    for shift in closed:
        audit.log(
            "shift_auto_clocked_out", "system",
            shift_id=shift.id, employee_id=shift.employee_id,
            clock_in=shift.clock_in, clock_out=shift.clock_out, hours=shift.hours,
        )
    return closed, open_map


def auto_close_stale_shifts(settings: Optional[Settings] = None) -> list[Shift]:
    """Close stale open shifts and return them (see sweep_and_open_shifts).
    Kept for callers that only need the sweep, not the open map."""
    closed, _ = sweep_and_open_shifts(settings)
    return closed


def clock_in(
    employee_id: str, timestamp_iso: str, role_id: str,
    wsw: int | None = None, employee: Optional[Employee] = None,
) -> Shift:
    """
    Open a new shift for the employee, working ``role_id``, at
    ``timestamp_iso``. Refuses if the employee already has an open shift. The
    role's title/department/hourly_rate are SNAPSHOTTED onto the shift so a
    later edit to the role never rewrites the pay of shifts already worked.
    The new shift is filed in the work week (see
    Settings.overtime.week_start_weekday) of its clock-in time. Atomic under
    the storage lock.

    ``wsw`` and ``employee`` may be passed by a caller that already loaded
    them (e.g. the kiosk /clock handler) to avoid re-reading settings/employees.
    """
    if wsw is None:
        wsw = _week_start_weekday()
    if find_open_shift(employee_id, wsw) is not None:
        raise ValueError("Employee is already clocked in.")

    if employee is None:
        employee = get_employee(employee_id)
    if employee is None:
        raise ValueError("Unknown employee.")
    role = next((r for r in employee.roles if r.id == role_id), None)
    if role is None:
        raise ValueError("Unknown role.")

    when = parse_iso(timestamp_iso)
    week_start = week_start_for(when, wsw)
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


def clock_out(
    employee_id: str, timestamp_iso: str,
    wsw: int | None = None,
    located: Optional[tuple[date, Shift]] = None,
) -> Shift:
    """
    Close the employee's open shift at ``timestamp_iso``. Validates that the
    clock-out is not before the clock-in, and fills in the shift's raw "hours"
    (clock_out - clock_in, not break-adjusted). Atomic under the storage lock.

    ``located`` may be passed by a caller that already found the open shift
    (the kiosk /clock handler) to skip re-scanning for it.
    """
    found = located if located is not None else find_open_shift(employee_id, wsw)
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


def set_shift_voided(week_start: date, shift_id: str, voided: bool) -> None:
    """
    Soft-delete (or restore) a shift by flipping its ``voided`` flag. The record
    stays on disk for the historical trail; voided shifts are excluded from
    payroll, the Shifts summary, and clock-state logic (see find_open_shift).
    """
    def mutator(current: list[dict]) -> list[dict]:
        for s in current:
            if s["id"] == shift_id:
                s["voided"] = voided
        return current

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
