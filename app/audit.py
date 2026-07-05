"""
Hidden, append-only audit trail for administrative and clock actions.

Not surfaced anywhere in the app's UI (no page, no nav link) — this is a plain
text file at /data/audit.log, one compact JSON object per line (JSON Lines),
so it stays human-readable/grep-able like the rest of /data without needing
the whole-file atomic rewrite used for payroll data. Open it directly in a
text editor if you need to review it.

Logged actions: employee_added, employee_modified, employee_deleted,
shift_edited, shift_deleted, clock_in, clock_out.
"""

from __future__ import annotations

from typing import Any

from . import paths, storage
from .timeutil import now_local, to_iso


def log(action: str, actor: str, **fields: Any) -> None:
    """Append one audit entry. ``actor`` is "admin" or "kiosk" (self-service)."""
    entry = {
        "ts": to_iso(now_local()),
        "action": action,
        "actor": actor,
        **fields,
    }
    storage.append_json_line(paths.data_dir() / "audit.log", entry)
