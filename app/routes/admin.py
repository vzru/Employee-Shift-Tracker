"""
Admin (password-protected) routes: first-run setup, login, employee management,
shift correction, Ontario payroll settings, and CSV payroll export.
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from .. import payroll, repo, security
from ..deps import redirect, require_admin, templates
from ..models import (
    BreakSettings, Employee, MinWageSettings, OvertimeSettings, Settings,
)
from ..timeutil import iso_year_week, now_local, parse_iso, to_iso, TS_FORMAT_MINUTE

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
    below = [e for e in employees if e.active and e.hourly_rate < settings.min_wage.rate]
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
            "ok": ok,
            "err": err,
        },
    )


@router.post("/employees/add")
def employee_add(
    request: Request,
    name: str = Form(...),
    hourly_rate: float = Form(0.0),
    active: str | None = Form(None),
    _: bool = Depends(require_admin),
):
    name = name.strip()
    if not name:
        return redirect("/admin/employees?err=Name+required")
    employees = repo.load_employees()
    employees.append(Employee(
        id=repo.new_id(),
        name=name,
        hourly_rate=hourly_rate,
        active=active is not None,
    ))
    repo.save_employees(employees)
    return redirect("/admin/employees?ok=Employee+added")


@router.post("/employees/{employee_id}/edit")
def employee_edit(
    request: Request,
    employee_id: str,
    name: str = Form(...),
    hourly_rate: float = Form(0.0),
    active: str | None = Form(None),
    _: bool = Depends(require_admin),
):
    employees = repo.load_employees()
    for e in employees:
        if e.id == employee_id:
            e.name = name.strip() or e.name
            e.hourly_rate = hourly_rate
            e.active = active is not None
    repo.save_employees(employees)
    return redirect("/admin/employees?ok=Employee+updated")


@router.post("/employees/{employee_id}/delete")
def employee_delete(
    request: Request, employee_id: str, _: bool = Depends(require_admin),
):
    employees = [e for e in repo.load_employees() if e.id != employee_id]
    repo.save_employees(employees)
    return redirect("/admin/employees?ok=Employee+removed")


# --- Shifts ------------------------------------------------------------------

@router.get("/shifts")
def shifts_page(
    request: Request,
    year: int | None = None,
    week: int | None = None,
    ok: str | None = None,
    err: str | None = None,
    _: bool = Depends(require_admin),
):
    # Default to the current ISO week.
    if year is None or week is None:
        year, week = iso_year_week(now_local())

    names = {e.id: e.name for e in repo.load_employees()}
    overrides = repo.load_adjustments(year, week)
    shifts = repo.load_week_shifts(year, week)

    view = []
    for s in shifts:
        # Duration text for closed shifts; open shifts are flagged.
        duration = None
        if s.clock_out:
            hrs = (parse_iso(s.clock_out) - parse_iso(s.clock_in)).total_seconds() / 3600
            duration = round(hrs, 2)
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
        })

    # Prev/next week navigation values.
    ref = datetime.fromisocalendar(year, week, 1)
    from datetime import timedelta
    prev_y, prev_w = iso_year_week(ref - timedelta(days=7))
    next_y, next_w = iso_year_week(ref + timedelta(days=7))

    return templates.TemplateResponse(
        "admin_shifts.html",
        {
            "request": request,
            "year": year, "week": week,
            "shifts": view,
            "open_count": sum(1 for v in view if v["open"]),
            "prev_y": prev_y, "prev_w": prev_w,
            "next_y": next_y, "next_w": next_w,
            "ok": ok, "err": err,
        },
    )


def _to_input(iso: str) -> str:
    """Convert a stored timestamp to a datetime-local input value."""
    return parse_iso(iso).strftime(TS_FORMAT_MINUTE)


@router.post("/shifts/edit")
def shift_edit(
    request: Request,
    year: int = Form(...),
    week: int = Form(...),
    shift_id: str = Form(...),
    clock_in: str = Form(...),
    clock_out: str | None = Form(None),
    break_override: str | None = Form(None),
    _: bool = Depends(require_admin),
):
    try:
        ci = to_iso(parse_iso(clock_in))
        co = to_iso(parse_iso(clock_out)) if clock_out else None
        repo.update_shift(year, week, shift_id, ci, co)
    except ValueError as exc:
        return redirect(f"/admin/shifts?year={year}&week={week}&err={str(exc).replace(' ', '+')}")

    # Break override: empty => clear (auto rule); otherwise fixed minutes.
    ov = break_override.strip() if break_override else ""
    repo.set_break_override(year, week, shift_id, int(ov) if ov else None)

    return redirect(f"/admin/shifts?year={year}&week={week}&ok=Shift+updated")


@router.post("/shifts/delete")
def shift_delete(
    request: Request,
    year: int = Form(...),
    week: int = Form(...),
    shift_id: str = Form(...),
    _: bool = Depends(require_admin),
):
    repo.delete_shift(year, week, shift_id)
    repo.set_break_override(year, week, shift_id, None)  # clear any override too
    return redirect(f"/admin/shifts?year={year}&week={week}&ok=Shift+deleted")


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
    ot_week_start_weekday: int = Form(0),
    # min wage
    min_wage_rate: float = Form(17.60),
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
