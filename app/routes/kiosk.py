"""
Kiosk (employee-facing) routes. No login. The dashboard is sectioned by
department; each employee gets one tappable card per role they hold (a
multi-role employee, e.g. a cashier in Bowling who's also a bartender in
Restaurant, gets one card per role, in each department's section). Clicking a
card opens a confirm dialog (client-side, Alpine.js) pre-filled with the
current time, which the employee may edit before confirming the clock in/out.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .. import audit, repo
from ..deps import templates
from ..timeutil import now_local, parse_iso, to_iso, TS_FORMAT_MINUTE

router = APIRouter()


@router.get("/")
def dashboard(request: Request, ok: str | None = None, err: str | None = None):
    employees = [e for e in repo.load_employees() if e.active]
    open_map = repo.open_shifts_by_employee()  # employee_id -> open Shift

    cards = []
    for e in employees:
        open_shift = open_map.get(e.id)
        matched_open_role = False
        for role in e.roles:
            is_open_role = open_shift is not None and open_shift.role_id == role.id
            matched_open_role = matched_open_role or is_open_role
            cards.append({
                "employee_id": e.id,
                "role_id": role.id,
                "name": e.name,
                "role_title": role.title,
                "department": role.department,
                "clocked_in": is_open_role,
                # A different role's shift is open, so this card can't clock in.
                "blocked": open_shift is not None and not is_open_role,
                "since": open_shift.clock_in if is_open_role else None,
            })
        if open_shift is not None and not matched_open_role:
            # The open shift's role was since edited/removed from the employee.
            # Still show a card (from the shift's own snapshot) so they can
            # clock out — never get stuck with no way to close an open shift.
            cards.append({
                "employee_id": e.id,
                "role_id": open_shift.role_id or "",
                "name": e.name,
                "role_title": open_shift.role_title or "(role removed)",
                "department": open_shift.department or "(no department)",
                "clocked_in": True,
                "blocked": False,
                "since": open_shift.clock_in,
            })

    # Section by department (alphabetical); clocked-in cards first within a
    # section, then by name/role.
    by_department: dict[str, list[dict]] = {}
    for c in cards:
        by_department.setdefault(c["department"], []).append(c)
    for dept_cards in by_department.values():
        dept_cards.sort(key=lambda c: (not c["clocked_in"], c["name"].lower(), c["role_title"].lower()))
    sections = [
        {"department": dept, "cards": by_department[dept]}
        for dept in sorted(by_department.keys(), key=str.lower)
    ]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "sections": sections,
            # Value for the datetime-local input default (minute precision).
            "now_value": now_local().strftime(TS_FORMAT_MINUTE),
            "ok": ok,
            "err": err,
        },
    )


@router.post("/clock")
def clock(
    request: Request,
    employee_id: str = Form(...),
    timestamp: str = Form(...),
    role_id: str | None = Form(None),
):
    """
    Toggle the employee's clock state. The SERVER decides the action from the
    current state (authoritative — avoids races if two kiosks act at once).
    ``role_id`` is required to clock IN (which role is being worked); it's
    ignored when clocking out, since that just closes whatever shift is open.
    """
    employee = repo.get_employee(employee_id)
    if employee is None:
        return RedirectResponse("/?err=Unknown+employee", status_code=303)

    # Normalize the (possibly edited) timestamp to stored second precision.
    try:
        stamp = to_iso(parse_iso(timestamp))
    except (ValueError, TypeError):
        return RedirectResponse("/?err=Invalid+time", status_code=303)

    try:
        if repo.find_open_shift(employee_id) is not None:
            shift = repo.clock_out(employee_id, stamp)
            audit.log(
                "clock_out", "kiosk", employee_id=employee_id, name=employee.name,
                shift_id=shift.id, timestamp=stamp,
            )
            msg = f"{employee.name}+clocked+out"
        else:
            if not role_id:
                return RedirectResponse("/?err=Choose+a+role", status_code=303)
            shift = repo.clock_in(employee_id, stamp, role_id)
            audit.log(
                "clock_in", "kiosk", employee_id=employee_id, name=employee.name,
                shift_id=shift.id, role_id=role_id, role_title=shift.role_title,
                department=shift.department, timestamp=stamp,
            )
            msg = f"{employee.name}+clocked+in"
    except ValueError as exc:
        return RedirectResponse(f"/?err={str(exc).replace(' ', '+')}", status_code=303)

    return RedirectResponse(f"/?ok={msg}", status_code=303)
