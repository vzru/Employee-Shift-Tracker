"""
Kiosk (employee-facing) routes. No login. The dashboard lists every active
employee; those with an open shift are highlighted. Clicking a row opens a
confirm dialog (client-side, Alpine.js) pre-filled with the current time, which
the employee may edit before confirming the clock in/out.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .. import repo
from ..deps import templates
from ..timeutil import now_local, parse_iso, to_iso, TS_FORMAT_MINUTE

router = APIRouter()


@router.get("/")
def dashboard(request: Request, ok: str | None = None, err: str | None = None):
    employees = [e for e in repo.load_employees() if e.active]
    open_map = repo.open_shifts_by_employee()

    # Build view rows with status + open-since time for the dialog.
    rows = []
    for e in employees:
        open_shift = open_map.get(e.id)
        rows.append({
            "id": e.id,
            "name": e.name,
            "clocked_in": open_shift is not None,
            "since": open_shift.clock_in if open_shift else None,
        })

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "rows": rows,
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
):
    """
    Toggle the employee's clock state. The SERVER decides the action from the
    current state (authoritative — avoids races if two kiosks act at once).
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
            repo.clock_out(employee_id, stamp)
            msg = f"{employee.name}+clocked+out"
        else:
            repo.clock_in(employee_id, stamp)
            msg = f"{employee.name}+clocked+in"
    except ValueError as exc:
        return RedirectResponse(f"/?err={str(exc).replace(' ', '+')}", status_code=303)

    return RedirectResponse(f"/?ok={msg}", status_code=303)
