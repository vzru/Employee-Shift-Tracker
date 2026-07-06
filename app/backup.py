"""
Daily safety-net backups of the /data folder.

This is separate from (and in addition to) the user manually copying the
/data folder next to the exe — it protects against the drive/folder next to
the exe itself being the thing that's lost or corrupted, by keeping dated
copies elsewhere. One snapshot per calendar day per location; whether today's
snapshot already exists is decided by looking at the dated folders actually
present, not by tracking state separately.

Two locations, because they fail differently:
  * %TEMP%          — handy, but Windows Disk Cleanup / Storage Sense may
                      wipe it at any time.
  * %LOCALAPPDATA%  — durable per-user app data; survives temp cleanup.

Old snapshots are pruned per-location, keeping the newest RETENTION_DAYS.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

from . import paths

BACKUP_DIRNAME = "EmployeeShiftTrackerBackups"
RETENTION_DAYS = 14                  # dated snapshots kept per location
_CHECK_INTERVAL_SECONDS = 3600       # re-check hourly in case the app stays open past midnight

_NAME_RE = re.compile(r"^backup-(\d{4}-\d{2}-\d{2})$")


def backup_roots() -> list[Path]:
    """The folders that each hold one dated subfolder per backup day."""
    roots = [Path(tempfile.gettempdir()) / BACKUP_DIRNAME]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        roots.append(Path(local_appdata) / "EmployeeShiftTracker" / "backups")
    return roots


def _snapshot_dates(root: Path) -> list[date]:
    """Dates of the valid dated snapshots under ``root``, unsorted."""
    if not root.exists():
        return []
    dates = []
    for entry in root.iterdir():
        m = _NAME_RE.match(entry.name)
        if not m:
            continue
        try:
            dates.append(date.fromisoformat(m.group(1)))
        except ValueError:
            continue  # regex-shaped but not a real date (e.g. month 13)
    return dates


def last_backup_date(root: Path) -> date | None:
    """The most recent date a snapshot exists for under ``root``, or None."""
    dates = _snapshot_dates(root)
    return max(dates) if dates else None


def _prune(root: Path) -> None:
    """Delete the oldest dated snapshots beyond RETENTION_DAYS."""
    for stale in sorted(_snapshot_dates(root), reverse=True)[RETENTION_DAYS:]:
        shutil.rmtree(root / f"backup-{stale.isoformat()}", ignore_errors=True)


def ensure_daily_backup() -> None:
    """Create today's snapshot in each location that doesn't have one, then prune."""
    today = date.today()
    for root in backup_roots():
        if last_backup_date(root) != today:
            dest = root / f"backup-{today.isoformat()}"
            if not dest.exists():
                shutil.copytree(paths.data_dir(), dest)
        _prune(root)


def start_daily_backup_thread() -> None:
    """Run an initial backup check now, then keep checking hourly."""
    def worker() -> None:
        while True:
            try:
                ensure_daily_backup()
            except Exception:
                # Never let one failed attempt (locked file, permissions, a
                # weird folder name) kill the thread — backups must keep
                # retrying for the life of the process.
                pass
            time.sleep(_CHECK_INTERVAL_SECONDS)

    threading.Thread(target=worker, daemon=True).start()
