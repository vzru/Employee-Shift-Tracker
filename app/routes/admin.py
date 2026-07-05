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
    BreakSettings, Employee, MinWageSettings, OvertimeSettings, Role, Settings,
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
        "admin_setup.html", {"request": request, "err": err}
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
        "admin_login.html", {"request": request, "err": err}
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
    employees = repo.load_employees()
    open_map = repo.open_shifts_by_employee()
    settings = repo.load_settings()
    below = [
        e for e in employees
        if e.active and any(r.hourly_rate < settings.min_wage.rate for r in e.roles)
    ]
    return templates.TemplateResponse(
        "admin_home.html",
        {
            "request": request,
            "employee_count": len([e for e in employees if e.active]),
            "clocked_in_count": len(open_map),
            "below_min_wage": below,
            "min_wage": settings.min_wage.rate,
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
        "admin_employees.html",
        {
            "request": request,
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
    )
    employees.append(new_employee)
    repo.save_employees(employees)
    audit.log(
        "employee_added", "admin",
        employee_id=new_employee.id, first_name=first_name, last_name=last_name,
        active=new_employee.active, roles=[r.model_dump() for r in roles],
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
            if e.first_name != new_first:
                changes["first_name"] = [e.first_name, new_first]
            if e.last_name != new_last:
                changes["last_name"] = [e.last_name, new_last]
            if e.active != new_active:
                changes["active"] = [e.active, new_active]
            old_roles = [r.model_dump() for r in e.roles]
            new_roles = [r.model_dump() for r in roles]
            if old_roles != new_roles:
                changes["roles"] = [old_roles, new_roles]
            e.first_name = new_first
            e.last_name = new_last
            e.active = new_active
            e.roles = roles
    repo.save_employees(employees)
    if changes:
        audit.log("employee_modified", "admin", employee_id=employee_id, changes=changes)
    return redirect("/admin/employees?ok=Employee+updated")


@router.post("/employees/{employee_id}/delete")
def employee_delete(
    request: Request, employee_id: str, _: bool = Depends(require_admin),
):
    employees = repo.load_employees()
    victim = next((e for e in employees if e.id == employee_id), None)
    employees = [e for e in employees if e.id != employee_id]
    repo.save_employees(employees)
    if victim is not None:
        audit.log(
            "employee_deleted", "admin", employee_id=employee_id,
            first_name=victim.first_name, last_name=victim.last_name,
        )
    return redirect("/admin/employees?ok=Employee+removed")


# --- Shifts ------------------------------------------------------------------

@router.get("/shifts")
def shifts_page(
    request: Request,
    week_start: str | None = None,
    ok: str | None = None,
    err: str | None = None,
    _: bool = Depends(require_admin),
):
    wsw = repo.load_settings().overtime.week_start_weekday
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
            "clock_in_input": _to_input(s.clock_in),
            "clock_out_input": _to_input(s.clock_out) if s.clock_out else "",
            "break_override": overrides.get(s.id, {}).get("minutes"),
            "role_title": s.role_title or "—",
            "department": s.department or "—",
        })

    return templates.TemplateResponse(
        "admin_shifts.html",
        {
            "request": request,
            "week_start": ws, "week_end": we,
            "prev_week_start": ws - timedelta(days=7),
            "next_week_start": ws + timedelta(days=7),
            "shifts": view,
            "summary": _employee_summary(view),
            "open_count": sum(1 for v in view if v["open"]),
            "ok": ok, "err": err,
        },
    )


def _to_input(iso: str) -> str:
    """Convert a stored timestamp to a datetime-local input value."""
    return parse_iso(iso).strftime(TS_FORMAT_MINUTE)


def _employee_summary(view: list[dict]) -> list[dict]:
    """Per-employee totals for the week: raw hours (unadjusted for break rules,
    matching the per-shift "Hours" column), shift count, and open-shift count."""
    by_employee: dict[str, dict] = {}
    for v in view:
        agg = by_employee.setdefault(v["employee_id"], {
            "employee_id": v["employee_id"],
            "name": v["name"],
            "total_hours": 0.0,
            "shift_count": 0,
            "open_count": 0,
        })
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
    ws = date.fromisoformat(week_start)

    # Snapshot the pre-edit state for the audit log.
    before = next(
        (s for s in repo.load_week_shifts(ws) if s.id == shift_id), None
    )
    before_override = repo.load_adjustments(ws).get(shift_id, {}).get("minutes")

    try:
        ci = to_iso(parse_iso(clock_in))
        co = to_iso(parse_iso(clock_out)) if clock_out else None
        repo.update_shift(ws, shift_id, ci, co)
    except ValueError as exc:
        return redirect(f"/admin/shifts?week_start={week_start}&err={str(exc).replace(' ', '+')}")

    # Break override: empty => clear (auto rule); otherwise fixed minutes.
    ov = break_override.strip() if break_override else ""
    new_override = int(ov) if ov else None
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


@router.post("/shifts/delete")
def shift_delete(
    request: Request,
    week_start: str = Form(...),
    shift_id: str = Form(...),
    _: bool = Depends(require_admin),
):
    ws = date.fromisoformat(week_start)
    before = next(
        (s for s in repo.load_week_shifts(ws) if s.id == shift_id), None
    )
    repo.delete_shift(ws, shift_id)
    repo.set_break_override(ws, shift_id, None)  # clear any override too
    if before is not None:
        audit.log(
            "shift_deleted", "admin", shift_id=shift_id,
            employee_id=before.employee_id,
            clock_in=before.clock_in, clock_out=before.clock_out,
        )
    return redirect(f"/admin/shifts?week_start={week_start}&ok=Shift+deleted")


# --- Settings ----------------------------------------------------------------

@router.get("/settings")
def settings_page(
    request: Request, ok: str | None = None, err: str | None = None,
    _: bool = Depends(require_admin),
):
    return templates.TemplateResponse(
        "admin_settings.html",
        {"request": request, "s": repo.load_settings(), "ok": ok, "err": err},
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
        "admin_payroll.html",
        {
            "request": request,
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
        "open_shifts": sum(r.open_shift_count for r in rows),
    }


@router.get("/payroll/export")
def payroll_export(
    request: Request,
    start: str = "",
    end: str = "",
    _: bool = Depends(require_admin),
):
    parsed_start = date.fromisoformat(start)
    parsed_end = date.fromisoformat(end)
    rows = payroll.compute_payroll(parsed_start, parsed_end)
    csv_text = payroll.to_csv(rows, parsed_start, parsed_end)
    filename = f"payroll_{start}_to_{end}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
