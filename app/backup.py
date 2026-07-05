"""
Daily safety-net backup of the /data folder into the OS temp directory.

This is separate from (and in addition to) the user manually copying the
/data folder next to the exe — it protects against the drive/folder next to
the exe itself being the thing that's lost or corrupted, by keeping a second
dated copy somewhere else (%TEMP%). One snapshot per calendar day; whether
today's snapshot already exists is decided by looking at the most recent
backup folder found, not by tracking state separately.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

from . import paths

BACKUP_DIRNAME = "EmployeeShiftTrackerBackups"
_CHECK_INTERVAL_SECONDS = 3600  # re-check hourly in case the app stays open past midnight

_NAME_RE = re.compile(r"^backup-(\d{4}-\d{2}-\d{2})$")


def backup_root() -> Path:
    """Folder under the OS temp dir that holds one subfolder per backup day."""
    return Path(tempfile.gettempdir()) / BACKUP_DIRNAME


def last_backup_date() -> date | None:
    """The most recent date a backup snapshot exists for, or None if none yet."""
    root = backup_root()
    if not root.exists():
        return None
    dates = [date.fromisoformat(m.group(1)) for m in (
        _NAME_RE.match(entry.name) for entry in root.iterdir()
    ) if m]
    return max(dates) if dates else None


def create_backup() -> Path:
    """Copy the whole /data folder into today's dated snapshot."""
    dest = backup_root() / f"backup-{date.today().isoformat()}"
    if not dest.exists():
        shutil.copytree(paths.data_dir(), dest)
    return dest


def ensure_daily_backup() -> None:
    """Create today's backup unless one already exists."""
    if last_backup_date() != date.today():
        create_backup()


def start_daily_backup_thread() -> None:
    """Run an initial backup check now, then keep checking hourly."""
    def worker() -> None:
        while True:
            try:
                ensure_daily_backup()
            except OSError:
                pass  # e.g. /data momentarily locked by a concurrent write; retry next check
            time.sleep(_CHECK_INTERVAL_SECONDS)

    threading.Thread(target=worker, daemon=True).start()
