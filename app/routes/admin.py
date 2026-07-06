"""
Admin (password-protected) routes: first-run setup, login, employee management,
shift correction, Ontario payroll settings, and CSV payroll export.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from .. import audit, payroll, repo, security
from ..deps import redirect, require_admin, templates
from ..models import (
    AutoClockoutSettings, BreakSettings, Employee, MinWageSettings,
    OvertimeSettings, Role, Settings,
)
from ..timeutil import (
    hours_between, now_local, parse_iso, to_iso, week_end_for, week_start_for,
    TS_FORMAT_MINUTE,
)

router = APIRouter(prefix="/admin")


# --- First-run setup & login -------------------------------------------------

@router.get("/setup")
def setup_form(request: Request, err: str | None = None):
    if security.is_admin_configured():
        return redirect("/admin/login")
    return templates.TemplateResponse(
        request, "admin_setup.html", {"err": err}
    )


@router.post("/setup")
def setup_submit(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(...),
):
    if security.is_admin_configured():
        return redirect("/admin/login")
    if len(password) < 6:
        return redirect("/admin/setup?err=Password+must+be+at+least+6+characters")
    if password != confirm:
        return redirect("/admin/setup?err=Passwords+do+not+match")
    security.set_admin_password(password)
    security.login_session(request.session)
    return redirect("/admin")


@router.get("/login")
def login_form(request: Request, err: str | None = None):
    if not security.is_admin_configured():
        return redirect("/admin/setup")
    return templates.TemplateResponse(
        request, "admin_login.html", {"err": err}
    )


@router.post("/login")
def login_submit(request: Request, password: str = Form(...)):
    if not security.is_admin_configured():
        return redirect("/admin/setup")
    if security.check_admin_password(password):
        security.login_session(request.session)
        return redirect("/admin")
    return redirect("/admin/login?err=Incorrect+password")


@router.get("/logout")
def logout(request: Request):
    security.logout_session(request.session)
    return redirect("/admin/login")


@router.post("/password")
def change_password(
    request: Request,
    current: str = Form(...),
    password: str = Form(...),
    confirm: str = Form(...),
    _: bool = Depends(require_admin),
):
    if not security.check_admin_password(current):
        return redirect("/admin/settings?err=Current+password+incorrect")
    if len(password) < 6:
        return redirect("/admin/settings?err=New+password+too+short")
    if password != confirm:
        return redirect("/admin/settings?err=New+passwords+do+not+match")
    security.set_admin_password(password)
    return redirect("/admin/settings?ok=Password+changed")


# --- Admin home --------------------------------------------------------------

@router.get("")
def admin_home(request: Request, _: bool = Depends(require_admin)):
    settings = repo.load_settings()
    # One pass: auto-close stale shifts and get the current open map.
    _closed, open_map = repo.sweep_and_open_shifts(settings)
    employees = repo.load_employees()
    names = {e.id: e.name for e in employees}
    below = [
        e for e in employees
        if e.active and any(r.hourly_rate < settings.min_wage.rate for r in e.roles)
    ]

    # Safety-net alert: shifts open 12h+ (a likely forgotten clock-out),
    # derived from the sweep's open map — no extra file reads.
    long_open = []
    for item in repo.long_open_shifts(open_map, repo.LONG_OPEN_HOURS):
        long_open.append({
            "name": names.get(item["employee_id"], f"(unknown {item['employee_id']})"),
            "hours_open": item["hours_open"],
            "role_title": item["role_title"] or "—",
            "clock_in": item["clock_in"].replace("T", " "),
        })

    return templates.TemplateResponse(
        request,
        "admin_home.html",
        {
            "employee_count": len([e for e in employees if e.active]),
            "clocked_in_count": len(open_map),
            "below_min_wage": below,
            "min_wage": settings.min_wage.rate,
            # Week folders stored under a different week-start scheme than the
            # current setting — invisible to Shifts/payroll until migrated.
            "misaligned_weeks": repo.find_misaligned_week_folders(),
            "long_open_shifts": long_open,
            "long_open_hours": repo.LONG_OPEN_HOURS,
        },
    )


# --- Employees ---------------------------------------------------------------

@router.get("/employees")
def employees_page(
    request: Request, ok: str | None = None, err: str | None = None,
    _: bool = Depends(require_admin),
):
    settings = repo.load_settings()
    return templates.TemplateResponse(
        request,
        "admin_employees.html",
        {
            "employees": repo.load_employees(),
            "min_wage": settings.min_wage.rate,
            "role_catalog": settings.role_catalog,
            "ok": ok,
            "err": err,
        },
    )


def _parse_roles(raw: str) -> list[Role]:
    """
    Parse the roles editor's hidden JSON field into Role models. Entries
    missing a title or department are dropped (defensive against a stray blank
    row left in the client-side list).
    """
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        return []
    roles = []
    for item in items if isinstance(items, list) else []:
        title = str(item.get("title", "")).strip()
        department = str(item.get("department", "")).strip()
        if not title or not department:
            continue
        try:
            rate = float(item.get("hourly_rate", 0))
        except (TypeError, ValueError):
            rate = 0.0
        roles.append(Role(
            id=item.get("id") or repo.new_id(),
            title=title, department=department, hourly_rate=rate,
        ))
    return roles


@router.post("/employees/add")
def employee_add(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    roles_json: str = Form("[]"),
    active: str | None = Form(None),
    vacation_pay_percent: float = Form(4.0),
    _: bool = Depends(require_admin),
):
    first_name = first_name.strip()
    last_name = last_name.strip()
    if not first_name or not last_name:
        return redirect("/admin/employees?err=First+and+last+name+required")
    roles = _parse_roles(roles_json)
    if not roles:
        return redirect("/admin/employees?err=At+least+one+role+is+required")

    employees = repo.load_employees()
    new_employee = Employee(
        id=repo.new_id(),
        first_name=first_name,
        last_name=last_name,
        active=active is not None,
        roles=roles,
        vacation_pay_percent=max(0.0, vacation_pay_percent),
    )
    employees.append(new_employee)
    repo.save_employees(employees)
    audit.log(
        "employee_added", "admin",
        employee_id=new_employee.id, first_name=first_name, last_name=last_name,
        active=new_employee.active, roles=[r.model_dump() for r in roles],
        vacation_pay_percent=new_employee.vacation_pay_percent,
    )
    return redirect("/admin/employees?ok=Employee+added")


@router.post("/employees/{employee_id}/edit")
def employee_edit(
    request: Request,
    employee_id: str,
    first_name: str = Form(...),
    last_name: str = Form(...),
    roles_json: str = Form("[]"),
    active: str | None = Form(None),
    vacation_pay_percent: float = Form(4.0),
    _: bool = Depends(require_admin),
):
    roles = _parse_roles(roles_json)
    if not roles:
        return redirect("/admin/employees?err=At+least+one+role+is+required")

    employees = repo.load_employees()
    changes: dict[str, list] = {}
    for e in employees:
        if e.id == employee_id:
            new_first = first_name.strip() or e.first_name
            new_last = last_name.strip() or e.last_name
            new_active = active is not None
            new_vacation = max(0.0, vacation_pay_percent)
            if e.first_name != new_first:
                changes["first_name"] = [e.first_name, new_first]
            if e.last_name != new_last:
                changes["last_name"] = [e.last_name, new_last]
            if e.active != new_active:
                changes["active"] = [e.active, new_active]
            if e.vacation_pay_percent != new_vacation:
                changes["vacation_pay_percent"] = [e.vacation_pay_percent, new_vacation]
            old_roles = [r.model_dump() for r in e.roles]
            new_roles = [r.model_dump() for r in roles]
            if old_roles != new_roles:
                changes["roles"] = [old_roles, new_roles]
            e.first_name = new_first
            e.last_name = new_last
            e.active = new_active
            e.vacation_pay_percent = new_vacation
            e.roles = roles
    repo.save_employees(employees)
    if changes:
        audit.log("employee_modified", "admin", employee_id=employee_id, changes=changes)
    return redirect("/admin/employees?ok=Employee+updated")


@router.post("/employees/{employee_id}/set-active")
def employee_set_active(
    request: Request,
    employee_id: str,
    active: str = Form(...),  # "true" | "false"
    _: bool = Depends(require_admin),
):
    """
    Soft delete / restore: flip an employee's active flag instead of removing
    them. Inactive employees are hidden from the kiosk but kept in the admin
    list, and all their shift history is preserved. (Employees are never hard
    deleted — historical shifts must always resolve to a name.)
    """
    want_active = active == "true"
    employees = repo.load_employees()
    target = next((e for e in employees if e.id == employee_id), None)
    if target is None:
        return redirect("/admin/employees?err=Unknown+employee")
    if target.active != want_active:
        target.active = want_active
        repo.save_employees(employees)
        audit.log(
            "employee_activated" if want_active else "employee_deactivated",
            "admin", employee_id=employee_id,
            first_name=target.first_name, last_name=target.last_name,
        )
    verb = "reactivated" if want_active else "deactivated"
    return redirect(f"/admin/employees?ok=Employee+{verb}")


# --- Shifts ------------------------------------------------------------------

@router.get("/shifts")
def shifts_page(
    request: Request,
    week_start: str | None = None,
    ok: str | None = None,
    err: str | None = None,
    _: bool = Depends(require_admin),
):
    settings = repo.load_settings()
    repo.auto_close_stale_shifts(settings)
    wsw = settings.overtime.week_start_weekday
    # Resolve whatever date was passed (any day, not necessarily a week start)
    # to the week containing it; default to today if missing/unparseable.
    try:
        ref = date.fromisoformat(week_start) if week_start else now_local()
    except ValueError:
        ref = now_local()
    ws = week_start_for(ref, wsw)
    we = week_end_for(ws)

    names = {e.id: e.name for e in repo.load_employees()}
    overrides = repo.load_adjustments(ws)
    shifts = repo.load_week_shifts(ws)

    view = []
    for s in shifts:
        # Duration text for closed shifts; open shifts are flagged.
        duration = round(hours_between(s.clock_in, s.clock_out), 2) if s.clock_out else None
        view.append({
            "id": s.id,
            "employee_id": s.employee_id,
            "name": names.get(s.employee_id, f"(unknown {s.employee_id})"),
            "clock_in": s.clock_in,
            "clock_out": s.clock_out,
            "open": s.clock_out is None,
            "duration": duration,
            # Ontario ESA three-hour rule: closed shifts under 3h may owe a
            # top-up (badge in the details popup; also flagged in payroll).
            "short": duration is not None and duration < payroll.SHORT_SHIFT_HOURS,
            "clock_in_input": _to_input(s.clock_in),
            "clock_out_input": _to_input(s.clock_out) if s.clock_out else "",
            "break_override": overrides.get(s.id, {}).get("minutes"),
            "role_title": s.role_title or "—",
            "department": s.department or "—",
            "auto_clocked_out": s.auto_clocked_out,
            "voided": s.voided,
        })

    return templates.TemplateResponse(
        request,
        "admin_shifts.html",
        {
            "week_start": ws, "week_end": we,
            "prev_week_start": ws - timedelta(days=7),
            "next_week_start": ws + timedelta(days=7),
            "shifts": view,
            "summary": _employee_summary(view),
            # Open count excludes voided shifts (they're operationally "removed").
            "open_count": sum(1 for v in view if v["open"] and not v["voided"]),
            "ok": ok, "err": err,
        },
    )


def _to_input(iso: str) -> str:
    """Convert a stored timestamp to a datetime-local input value."""
    return parse_iso(iso).strftime(TS_FORMAT_MINUTE)


def _employee_summary(view: list[dict]) -> list[dict]:
    """Per-employee totals for the week: raw hours (unadjusted for break rules,
    matching the per-shift "Hours" column), shift count, and open-shift count.
    Voided shifts don't count toward any total but are tallied separately so
    the employee still shows up (and their details popup stays reachable to
    un-void from)."""
    by_employee: dict[str, dict] = {}
    for v in view:
        agg = by_employee.setdefault(v["employee_id"], {
            "employee_id": v["employee_id"],
            "name": v["name"],
            "total_hours": 0.0,
            "shift_count": 0,
            "open_count": 0,
            "voided_count": 0,
        })
        if v["voided"]:
            agg["voided_count"] += 1
            continue
        agg["shift_count"] += 1
        if v["open"]:
            agg["open_count"] += 1
        elif v["duration"] is not None:
            agg["total_hours"] += v["duration"]

    summary = sorted(by_employee.values(), key=lambda a: a["name"].lower())
    for agg in summary:
        agg["total_hours"] = round(agg["total_hours"], 2)
    return summary


@router.post("/shifts/edit")
def shift_edit(
    request: Request,
    week_start: str = Form(...),
    shift_id: str = Form(...),
    clock_in: str = Form(...),
    clock_out: str | None = Form(None),
    break_override: str | None = Form(None),
    _: bool = Depends(require_admin),
):
    try:
        ws = date.fromisoformat(week_start)
    except ValueError:
        return redirect("/admin/shifts?err=Invalid+week")

    # Snapshot the pre-edit state for the audit log.
    before = next(
        (s for s in repo.load_week_shifts(ws) if s.id == shift_id), None
    )
    before_override = repo.load_adjustments(ws).get(shift_id, {}).get("minutes")

    # Break override: empty => clear (auto rule); otherwise fixed minutes.
    # Parsed BEFORE the shift is updated so a bad value changes nothing at all.
    ov = break_override.strip() if break_override else ""
    try:
        new_override = int(ov) if ov else None
    except ValueError:
        return redirect(
            f"/admin/shifts?week_start={week_start}&err=Break+override+must+be+a+whole+number+of+minutes"
        )
    if new_override is not None and new_override < 0:
        return redirect(
            f"/admin/shifts?week_start={week_start}&err=Break+override+cannot+be+negative"
        )

    try:
        ci = to_iso(parse_iso(clock_in))
        co = to_iso(parse_iso(clock_out)) if clock_out else None
        repo.update_shift(ws, shift_id, ci, co)
    except ValueError as exc:
        return redirect(f"/admin/shifts?week_start={week_start}&err={str(exc).replace(' ', '+')}")

    repo.set_break_override(ws, shift_id, new_override)

    if before is not None:
        changes: dict[str, list] = {}
        if before.clock_in != ci:
            changes["clock_in"] = [before.clock_in, ci]
        if before.clock_out != co:
            changes["clock_out"] = [before.clock_out, co]
        if before_override != new_override:
            changes["break_override"] = [before_override, new_override]
        if changes:
            audit.log(
                "shift_edited", "admin", shift_id=shift_id,
                employee_id=before.employee_id, changes=changes,
            )

    return redirect(f"/admin/shifts?week_start={week_start}&ok=Shift+updated")


@router.post("/shifts/void")
def shift_void(
    request: Request,
    week_start: str = Form(...),
    shift_id: str = Form(...),
    voided: str = Form(...),  # "true" | "false"
    _: bool = Depends(require_admin),
):
    """
    Soft delete / restore a shift. Voided shifts stay on disk (kept for the
    record) but are excluded from payroll, the summary, and clock-state logic.
    Break overrides are left intact so un-voiding restores the shift exactly.
    """
    try:
        ws = date.fromisoformat(week_start)
    except ValueError:
        return redirect("/admin/shifts?err=Invalid+week")
    want_voided = voided == "true"
    before = next(
        (s for s in repo.load_week_shifts(ws) if s.id == shift_id), None
    )
    repo.set_shift_voided(ws, shift_id, want_voided)
    if before is not None and before.voided != want_voided:
        audit.log(
            "shift_voided" if want_voided else "shift_unvoided", "admin",
            shift_id=shift_id, employee_id=before.employee_id,
            clock_in=before.clock_in, clock_out=before.clock_out,
        )
    verb = "voided" if want_voided else "restored"
    return redirect(f"/admin/shifts?week_start={week_start}&ok=Shift+{verb}")


# --- Settings ----------------------------------------------------------------

@router.get("/settings")
def settings_page(
    request: Request, ok: str | None = None, err: str | None = None,
    _: bool = Depends(require_admin),
):
    return templates.TemplateResponse(
        request,
        "admin_settings.html",
        {"s": repo.load_settings(), "ok": ok, "err": err},
    )


def _parse_role_catalog(raw: str) -> dict[str, list[str]]:
    """
    Parse the Settings roles/departments editor's hidden JSON field
    ({"Department": ["Title", ...]}) into a clean catalog: blank department
    names are dropped, titles are stripped/deduped, empty departments dropped.
    """
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    catalog: dict[str, list[str]] = {}
    if not isinstance(items, dict):
        return catalog
    for dept, titles in items.items():
        dept = str(dept).strip()
        if not dept or not isinstance(titles, list):
            continue
        seen: list[str] = []
        for t in titles:
            t = str(t).strip()
            if t and t not in seen:
                seen.append(t)
        if seen:
            catalog[dept] = seen
    return catalog


@router.post("/settings")
def settings_save(
    request: Request,
    # break
    break_enabled: str | None = Form(None),
    break_duration_minutes: int = Form(30),
    break_trigger_hours: float = Form(5.0),
    # overtime
    ot_enabled: str | None = Form(None),
    ot_multiplier: float = Form(1.5),
    ot_weekly_threshold: float = Form(44.0),
    ot_week_start_weekday: int = Form(6),
    # min wage
    min_wage_rate: float = Form(17.60),
    # roles & departments catalog
    role_catalog_json: str = Form("{}"),
    # automatic clock-out
    auto_clockout_enabled: str | None = Form(None),
    auto_clockout_threshold_hours: float = Form(24.0),
    _: bool = Depends(require_admin),
):
    settings = Settings(
        break_rules=BreakSettings(
            enabled=break_enabled is not None,
            duration_minutes=break_duration_minutes,
            trigger_hours=break_trigger_hours,
        ),
        overtime=OvertimeSettings(
            enabled=ot_enabled is not None,
            multiplier=ot_multiplier,
            weekly_threshold=ot_weekly_threshold,
            week_start_weekday=ot_week_start_weekday,
        ),
        min_wage=MinWageSettings(rate=min_wage_rate),
        role_catalog=_parse_role_catalog(role_catalog_json),
        auto_clockout=AutoClockoutSettings(
            enabled=auto_clockout_enabled is not None,
            threshold_hours=auto_clockout_threshold_hours,
        ),
    )
    repo.save_settings(settings)
    return redirect("/admin/settings?ok=Settings+saved")


# --- Payroll -----------------------------------------------------------------

@router.get("/payroll")
def payroll_page(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    _: bool = Depends(require_admin),
):
    rows = None
    parsed_start = parsed_end = None
    err = None
    if start and end:
        try:
            parsed_start = date.fromisoformat(start)
            parsed_end = date.fromisoformat(end)
            if parsed_end < parsed_start:
                raise ValueError("End date is before start date.")
            rows = payroll.compute_payroll(parsed_start, parsed_end)
        except ValueError as exc:
            err = str(exc)

    return templates.TemplateResponse(
        request,
        "admin_payroll.html",
        {
            "rows": rows,
            "start": start or "",
            "end": end or "",
            "totals": _totals(rows) if rows else None,
            "err": err,
        },
    )


def _totals(rows):
    return {
        "regular_hours": round(sum(r.regular_hours for r in rows), 2),
        "overtime_hours": round(sum(r.overtime_hours for r in rows), 2),
        "total_pay": round(sum(r.total_pay for r in rows), 2),
        "vacation_pay": round(sum(r.vacation_pay for r in rows), 2),
        "open_shifts": sum(r.open_shift_count for r in rows),
        "auto_closed": sum(r.auto_clocked_out_count for r in rows),
    }


@router.get("/holiday")
def holiday_page(
    request: Request,
    holiday: str | None = None,
    _: bool = Depends(require_admin),
):
    """
    Ontario ESA public-holiday pay estimate for a chosen holiday date:
    (regular wages in the 4 work weeks before the week with the holiday,
    plus vacation pay on those wages) / 20. Eligibility is not checked.
    """
    rows = window_start = window_end = None
    err = None
    if holiday:
        try:
            parsed = date.fromisoformat(holiday)
            rows, window_start, window_end = payroll.compute_holiday_pay(parsed)
        except ValueError:
            err = "Enter a valid holiday date."

    return templates.TemplateResponse(
        request,
        "admin_holiday.html",
        {
            "holiday": holiday or "",
            "rows": rows,
            "window_start": window_start,
            "window_end": window_end,
            "err": err,
        },
    )


@router.get("/payroll/export")
def payroll_export(
    request: Request,
    start: str = "",
    end: str = "",
    _: bool = Depends(require_admin),
):
    try:
        parsed_start = date.fromisoformat(start)
        parsed_end = date.fromisoformat(end)
        if parsed_end < parsed_start:
            raise ValueError
    except ValueError:
        return redirect("/admin/payroll?err=Choose+a+valid+date+range+first")
    rows = payroll.compute_payroll(parsed_start, parsed_end)
    csv_text = payroll.to_csv(rows, parsed_start, parsed_end)
    filename = f"payroll_{start}_to_{end}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
