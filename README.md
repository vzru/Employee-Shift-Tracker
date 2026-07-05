# Employee Shift Tracker

A small, self-contained shift / hours tracker for a single small business in
Ontario, Canada. It runs as **one Windows `.exe`** (no Python, Node, or other
runtime needed on the machine that runs it — only a web browser). On launch it
starts a local web server and opens your default browser to a kiosk where
employees clock in and out by tapping their name. An admin panel handles
employees, pay rates, shift corrections, Ontario payroll settings, and CSV
payroll export.

> **Disclaimer:** the payroll calculations are **configurable estimates to help
> with bookkeeping, not legal or payroll advice.** Always verify against the
> current [Ontario Employment Standards Act](https://www.ontario.ca/document/your-guide-employment-standards-act-0).

---

## Features

- **Kiosk (no login):** dashboard is sectioned by department; each employee gets
  one tappable card per role they hold (a cashier who's also a bartender gets a
  card in both Bowling and Restaurant). Clocked-in cards show first and green.
  Tap a card → confirm dialog with an **editable** date+time (defaults to now)
  → clock in or out.
- **Roles per employee:** one employee can hold several roles, each with its
  own department and hourly rate. Each shift snapshots which role was worked so
  a later rate change never rewrites past pay.
- **Human-readable storage:** plain JSON under `/data`, usable without the app.
  Employee names live **only** in `employees.json`; shift files store IDs only.
- **Atomic, serialized writes** (temp file + `os.replace` under a lock) so
  simultaneous clock-ins can't corrupt a file.
- **Admin panel (password-protected, bcrypt):** manage employees, roles &
  rates, view and correct shifts, flag open (no clock-out) shifts.
- **Soft deletes (nothing is lost):** employees are *deactivated* (hidden from
  the kiosk, kept for history) rather than deleted; shifts are *voided* (kept
  on disk, excluded from payroll, restorable) rather than removed. Both are
  audit-logged and reversible.
- **Automatic clock-out (configurable):** a shift left open past a threshold
  (default 24h) is closed automatically the next time the kiosk or admin panel
  is opened, and flagged on the Shifts page so the real end time can be
  corrected.
- **Ontario payroll settings (all editable):** unpaid break deduction, weekly
  overtime (>44h at 1.5×), and a minimum-wage reference with warnings.
- **CSV payroll export** for any date range.
- **Local-only admin password reset** — no network backdoor.

---

## Data layout (`/data`, created next to the exe)

```
data/
├─ README.md            # written on first run; documents the schema
├─ employees.json       # [{ id, first_name, last_name, active, roles: [{id, title, department, hourly_rate}] }]  <- names ONLY here
├─ admin.json           # { password_hash (bcrypt), settings {...} }
├─ secret.json          # per-machine session-cookie signing key
├─ audit.log            # hidden append-only audit trail (JSON Lines); not shown in the app
└─ <YYYY>/week-<YYYY-MM-DD>/   # work week keyed by its start date (configurable, default Sunday)
   ├─ shifts.json        # [{ id, employee_id, clock_in, clock_out, role_id, role_title, department, hourly_rate, hours, auto_clocked_out, voided }]
   └─ adjustments.json   # optional per-shift break overrides { shift_id: {minutes} }
```

`/data` is **git-ignored** — real employee names and hours are never pushed.

---

## Develop on this PC

Requires **Python 3.11+**, and (for styling changes) the standalone Tailwind CLI
in `tools/` (already downloaded; git-ignored).

```powershell
# 1. Create the venv and install deps
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. (Only if you change templates/CSS) rebuild the stylesheet
.\tools\build_css.ps1

# 3. Run in development (starts server + opens browser)
.\.venv\Scripts\python.exe main.py
```

The dev server writes `/data` in the repo root (git-ignored). First visit to
`/admin` prompts you to set the admin password.

### Reset the admin password (dev)

```powershell
.\.venv\Scripts\python.exe reset_admin.py
```

---

## Build the Windows `.exe`

```powershell
.\build.ps1
```

This rebuilds the CSS and runs PyInstaller (one-file). The result is:

```
dist\EmployeeShiftTracker.exe
```

### Test the exe

1. Copy **just that one file** somewhere (e.g. a fresh folder or the target PC).
2. Double-click it. A console window shows the local URL; your default browser
   opens automatically. If the preferred port is busy it picks another.
3. A `data\` folder appears **next to the exe** — that's your live payroll data.
   Back it up by copying that folder.
4. Closing the console window stops the server.

### Reset the admin password (on the packaged app / target PC)

Open a terminal (PowerShell) in the folder with the exe and run:

```powershell
.\EmployeeShiftTracker.exe --reset-admin
```

This works **only** from a local terminal — there is no network/URL reset path.

---

## Tech

FastAPI + uvicorn · Jinja2 templates · Tailwind CSS (standalone CLI, no Node) ·
Alpine.js (vendored, offline) · bcrypt via passlib · packaged with PyInstaller.

## Notes on the payroll math

- **Overtime is weekly**, on a configurable work-week (default Sunday start):
  hours over the weekly threshold (default 44) are paid at the multiplier
  (default 1.5×). For accurate overtime, pick export ranges aligned to whole
  work weeks. Genuine managers/supervisors may be **exempt** from overtime.
- **Break deduction** subtracts the configured minutes (default 30) only when a
  shift exceeds the trigger hours (default 5, matching the ESA meal-break
  trigger). A per-shift override on the Shifts page handles exceptions — an
  unpaid break is only lawful if the employee actually took it and was free of
  duties.
- **Open shifts** (no clock-out) are flagged and **excluded** from totals.
