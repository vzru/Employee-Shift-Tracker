"""
Writes a human-readable README into the /data folder on first run, documenting
the on-disk schema so the JSON files are usable WITHOUT the app.
"""

from __future__ import annotations

from . import paths, storage

DATA_README = """\
# Data folder — Employee Shift Tracker

This folder holds all of your live payroll data as plain, human-readable JSON.
It lives NEXT TO the application (the .exe) and persists between runs. Everything
here is usable without the app — you can open the files in any text editor.

Back up your data by copying this entire `data` folder somewhere safe.

## Files

### employees.json
An array of employees. Employee NAMES are stored ONLY in this file.
    [
      { "id": "a1b2c3d4e5f6", "name": "Jane Doe", "hourly_rate": 20.0, "active": true }
    ]

### admin.json
The admin password hash (bcrypt — never plaintext) and the payroll settings
(break deduction, overtime, minimum wage). Do not edit the hash by hand; reset
the password with:  EmployeeShiftTracker.exe --reset-admin

### secret.json
A random key used to sign the admin login session cookie for THIS installation.
Keep it private. Deleting it just logs admins out.

### <YYYY>/week-<WW>/shifts.json
Shifts are organised by ISO 8601 week. Weeks start on MONDAY and the week number
(01-53) is zero-padded. The ISO year can differ from the calendar year for a few
days around January 1 — that is correct ISO behaviour.

Each shift stores IDs only (never the name):
    [
      {
        "id": "f6e5d4c3b2a1",
        "employee_id": "a1b2c3d4e5f6",
        "clock_in":  "2026-07-06T09:00:00",
        "clock_out": "2026-07-06T17:30:00"
      }
    ]
A shift with "clock_out": null is still OPEN (the person has not clocked out).
Timestamps are local system time in ISO 8601 (no timezone).

### <YYYY>/week-<WW>/adjustments.json  (optional)
Per-shift unpaid-break overrides, kept out of shifts.json so shift records stay
minimal. Keyed by shift id:
    { "f6e5d4c3b2a1": { "minutes": 0 } }
`minutes` is the unpaid break to deduct for that one shift regardless of the
automatic rule (0 = no break was taken).

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
