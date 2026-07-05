"""
One-time migration: persist the roles schema change to disk.

1. employees.json: a flat "hourly_rate" per employee becomes a single
   "General"/"General" role at that rate (matches app/repo.py's in-memory
   _migrate_employee, but writes the result back so the file itself is
   updated, not just healed silently on next save).
2. Every week's shifts.json: shifts recorded before roles existed get their
   role_id/role_title/department/hourly_rate backfilled from the employee's
   (now-migrated) role, and their "hours" field computed from clock_in/
   clock_out.

Usage:  .venv\\Scripts\\python.exe tools\\migrate_roles.py "D:\\path\\to\\data"

Safe to re-run: only employees missing "roles" and shifts missing "role_id"
are touched. A second run finds nothing left to do.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.timeutil import hours_between  # noqa: E402


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def migrate(data_dir: Path) -> None:
    employees_file = data_dir / "employees.json"
    employees = _read_json(employees_file, [])

    # Employee id -> the single role synthesized from its legacy hourly_rate
    # (or its first role, if already migrated) — used to backfill old shifts.
    default_role_by_employee: dict[str, dict] = {}
    changed_employees = False

    for emp in employees:
        if "roles" not in emp and "hourly_rate" in emp:
            role = {
                "id": _new_id(), "title": "General", "department": "General",
                "hourly_rate": emp.pop("hourly_rate"),
            }
            emp["roles"] = [role]
            changed_employees = True
            print(f"  employee {emp['id']} ({emp.get('first_name', '')} "
                  f"{emp.get('last_name', '')}): synthesized 'General' role at ${role['hourly_rate']:.2f}")
        if emp.get("roles"):
            default_role_by_employee[emp["id"]] = emp["roles"][0]

    if changed_employees:
        _write_json(employees_file, employees)
    else:
        print("  employees.json: no legacy hourly_rate fields found.")

    shifts_backfilled = 0
    for shifts_file in sorted(data_dir.glob("[0-9][0-9][0-9][0-9]/week-*/shifts.json")):
        shifts = _read_json(shifts_file, [])
        changed = False
        for shift in shifts:
            if "role_id" in shift:
                continue  # already has the new fields
            role = default_role_by_employee.get(shift["employee_id"])
            if role is not None:
                shift["role_id"] = role["id"]
                shift["role_title"] = role["title"]
                shift["department"] = role["department"]
                shift["hourly_rate"] = role["hourly_rate"]
            else:
                shift["role_id"] = None
                shift["role_title"] = None
                shift["department"] = None
                shift["hourly_rate"] = None
            if shift.get("clock_out"):
                shift["hours"] = round(hours_between(shift["clock_in"], shift["clock_out"]), 2)
            else:
                shift["hours"] = None
            changed = True
            shifts_backfilled += 1
        if changed:
            _write_json(shifts_file, shifts)
            print(f"  {shifts_file.relative_to(data_dir)}: backfilled {len(shifts)} shift(s)")

    print(f"Done. Migrated employees: {changed_employees}. Shifts backfilled: {shifts_backfilled}.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-data-folder>")
        sys.exit(1)
    migrate(Path(sys.argv[1]).resolve())
