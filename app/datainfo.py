"""
Writes a human-readable README into the /data folder on first run, documenting
the on-disk schema so the JSON files are usable WITHOUT the app.
"""

from __future__ import annotations

from . import paths, storage

DATA_README = """\
# Data folder - Employee Shift Tracker

This folder holds all of your live payroll data as plain, human-readable JSON.
It lives NEXT TO the application (the .exe) and persists between runs. Everything
here is usable without the app - you can open the files in any text editor.

Back up your data by copying this entire `data` folder somewhere safe.

## Files

### employees.json
An array of employees. Employee NAMES are stored ONLY in this file. Each
employee has a list of ROLES; one employee can hold several, each with its own
department and hourly rate (e.g. a cashier in Bowling who's also a bartender
in Restaurant). "active": false means the employee is HIDDEN from the kiosk
but kept here for the record (employees are deactivated, never deleted, so
historical shifts always resolve to a name). "vacation_pay_percent" is that
employee's vacation pay accrual rate (Ontario ESA minimum 4%; 6% once they
reach 5 years of service), used by the payroll export.
    [
      {
        "id": "a1b2c3d4e5f6", "first_name": "Jane", "last_name": "Doe", "active": true,
        "vacation_pay_percent": 4.0,
        "roles": [
          { "id": "r1", "title": "Cashier", "department": "Bowling", "hourly_rate": 17.6 },
          { "id": "r2", "title": "Bartender", "department": "Restaurant", "hourly_rate": 18.5 }
        ]
      }
    ]

### admin.json
The admin password hash (bcrypt - never plaintext) and the payroll settings
(break deduction, overtime, minimum wage). Do not edit the hash by hand; reset
the password with:  EmployeeShiftTracker.exe --reset-admin

### secret.json
A random key used to sign the admin login session cookie for THIS installation.
Keep it private. Deleting it just logs admins out.

### <YYYY>/week-<YYYY-MM-DD>/shifts.json
Shifts are organised by WORK WEEK, keyed by the week's start date (folder name
is the start date itself, so there's no ambiguous week-numbering scheme). The
work week's start day (default Sunday) is configurable in Admin > Settings and
also drives overtime calculation. The <YYYY> folder uses the week START date's
year, even for a week that runs into the next calendar year.

Each shift stores IDs only (never the name), plus a SNAPSHOT of the role that
was picked at clock-in (title/department/hourly_rate) so a later change to
that role's rate never rewrites the pay of shifts already worked, and the
raw "hours" (clock_out - clock_in, not break-adjusted) once clocked out:
    [
      {
        "id": "f6e5d4c3b2a1",
        "employee_id": "a1b2c3d4e5f6",
        "clock_in":  "2026-07-06T09:00:00",
        "clock_out": "2026-07-06T17:30:00",
        "role_id": "r1",
        "role_title": "Cashier",
        "department": "Bowling",
        "hourly_rate": 17.6,
        "hours": 8.5,
        "auto_clocked_out": false,
        "voided": false
      }
    ]
A shift with "clock_out": null is still OPEN (the person has not clocked out);
"hours" stays null until it's closed. Timestamps are local system time in ISO
8601 (no timezone) - daylight saving time is IGNORED, so an overnight shift on
the March/November changeover night is off by one real hour; correct it in
Admin > Shifts if it matters. Shifts recorded before roles existed have null role_id/
role_title/department/hourly_rate. "auto_clocked_out": true means the shift
was closed by the automatic-clock-out safety net (Admin > Settings), not a
real clock-out — the recorded clock_out is whenever the app happened to
notice it was left open past the configured threshold (default 24h), not
necessarily the true end time. "voided": true is a SOFT DELETE — the shift is
kept here for the record but excluded from payroll, the Shifts summary, and
clock-state logic; an admin can restore it. Shifts are voided, never removed
from disk.

### <YYYY>/week-<YYYY-MM-DD>/adjustments.json  (optional)
Per-shift unpaid-break overrides, kept out of shifts.json so shift records stay
minimal. Keyed by shift id:
    { "f6e5d4c3b2a1": { "minutes": 0 } }
`minutes` is the unpaid break to deduct for that one shift regardless of the
automatic rule (0 = no break was taken).

### audit.log
An append-only audit trail, NOT shown anywhere in the app — one compact JSON
object per line (JSON Lines), oldest first. Records employee adds/edits/
deletes, shift edits/deletes (with old -> new values), and every clock in/out.
    {"ts": "2026-07-06T09:00:00", "action": "clock_in", "actor": "kiosk", "employee_id": "a1b2c3d4e5f6", "name": "Jane Doe", "shift_id": "f6e5d4c3b2a1", "timestamp": "2026-07-06T09:00:00"}
`actor` is "admin" for changes made from the admin panel, or "kiosk" for
self-service clock in/out. This file is never rewritten, only appended to.

## Reminder
Payroll figures produced by the app are configurable ESTIMATES, not legal or
payroll advice. Verify against the current Ontario Employment Standards Act.
"""


def ensure_data_readme() -> None:
    """Create /data and write the README if it isn't there yet."""
    storage.ensure_dir(paths.data_dir())
    readme = paths.data_dir() / "README.md"
    if not readme.exists():
        # Not JSON, so write directly (still atomic via storage helper pattern).
        readme.write_text(DATA_README, encoding="utf-8")
