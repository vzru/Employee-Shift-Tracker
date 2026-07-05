"""
One-time migration: re-file shifts.json/adjustments.json from the OLD storage
scheme (ISO 8601 weeks, Monday-start, data/<YYYY>/week-<WW> with a zero-padded
week NUMBER) into the NEW scheme (work weeks keyed by their start DATE,
data/<YYYY>/week-<YYYY-MM-DD>, default Sunday-start, configurable in Admin
Settings).

Usage (run against a real /data folder, e.g. the one next to the exe):

    .venv\\Scripts\\python.exe tools\\migrate_weeks.py "D:\\path\\to\\data"

Safe to re-run: only OLD-style "week-<1-2 digits>" folders are touched, and
each one is deleted only after its shifts/overrides have been written to the
new location. A second run finds no old-style folders left and does nothing.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.timeutil import parse_iso, week_start_for  # noqa: E402

OLD_WEEK_RE = re.compile(r"^week-(\d{1,2})$")


def _week_start_weekday(data_dir: Path) -> int:
    admin_file = data_dir / "admin.json"
    if admin_file.exists():
        admin = json.loads(admin_file.read_text(encoding="utf-8"))
        return admin.get("settings", {}).get("overtime", {}).get("week_start_weekday", 6)
    return 6


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _new_week_dir(data_dir: Path, week_start) -> Path:
    return data_dir / f"{week_start.year:04d}" / f"week-{week_start.isoformat()}"


def migrate(data_dir: Path) -> None:
    wsw = _week_start_weekday(data_dir)
    print(f"Using week_start_weekday={wsw} (0=Mon .. 6=Sun)")

    old_week_dirs = []
    for year_dir in sorted(data_dir.glob("[0-9][0-9][0-9][0-9]")):
        if not year_dir.is_dir():
            continue
        for week_dir in sorted(year_dir.iterdir()):
            if week_dir.is_dir() and OLD_WEEK_RE.match(week_dir.name):
                old_week_dirs.append(week_dir)

    if not old_week_dirs:
        print("No old-style week-<NN> folders found. Nothing to migrate.")
        return

    shifts_moved = 0
    overrides_moved = 0

    for old_dir in old_week_dirs:
        shifts = _read_json(old_dir / "shifts.json", [])
        overrides = _read_json(old_dir / "adjustments.json", {})

        for shift in shifts:
            new_ws = week_start_for(parse_iso(shift["clock_in"]), wsw)
            new_dir = _new_week_dir(data_dir, new_ws)

            current_shifts = _read_json(new_dir / "shifts.json", [])
            if not any(s["id"] == shift["id"] for s in current_shifts):
                current_shifts.append(shift)
                _write_json(new_dir / "shifts.json", current_shifts)
                shifts_moved += 1
                print(f"  shift {shift['id']}: {old_dir.name} -> {new_dir.relative_to(data_dir)}")

            if shift["id"] in overrides:
                current_overrides = _read_json(new_dir / "adjustments.json", {})
                if shift["id"] not in current_overrides:
                    current_overrides[shift["id"]] = overrides[shift["id"]]
                    _write_json(new_dir / "adjustments.json", current_overrides)
                    overrides_moved += 1

        # Only remove the old files/folder after every shift in it has been
        # written to its new home.
        (old_dir / "shifts.json").unlink(missing_ok=True)
        (old_dir / "adjustments.json").unlink(missing_ok=True)
        try:
            old_dir.rmdir()
        except OSError:
            pass  # not empty for some reason; leave it rather than guess
        try:
            old_dir.parent.rmdir()  # remove the <YYYY> folder if now empty
        except OSError:
            pass

    print(f"Done. Moved {shifts_moved} shift(s) and {overrides_moved} break override(s) "
          f"out of {len(old_week_dirs)} old week folder(s).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-data-folder>")
        sys.exit(1)
    migrate(Path(sys.argv[1]).resolve())
