"""
Low-level JSON persistence for payroll data.

Design goals (this is payroll data — corruption is unacceptable):

* ATOMIC writes: serialize to a temp file in the same directory, flush+fsync,
  then ``os.replace`` it over the target. os.replace is atomic on Windows and
  POSIX, so a reader never sees a half-written file and a crash mid-write leaves
  the previous good file intact.

* SERIALIZED writes: a single module-level re-entrant lock guards every write so
  two simultaneous clock-ins can't interleave a read-modify-write and clobber
  each other. Reads also take the lock so they never observe a torn state.

Everything is stored as plain, human-readable JSON (indent=2) so the files are
usable without the app.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

# One global lock for all data-file access. Re-entrant so a write helper can call
# a read helper while holding it. The app is a single small local server, so a
# coarse global lock is simplest and plenty fast.
_LOCK = threading.RLock()


def ensure_dir(path: Path) -> None:
    """Create a directory (and parents) if it does not already exist."""
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    """
    Read and parse a JSON file. Returns ``default`` if the file does not exist.

    Takes the lock so a concurrent write can't be observed half-applied.
    """
    with _LOCK:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)


def write_json(path: Path, data: Any) -> None:
    """
    Atomically write ``data`` as pretty JSON to ``path``.

    Writes to a temp file in the SAME directory (so os.replace stays on one
    filesystem and is atomic), fsyncs it, then replaces the target.
    """
    with _LOCK:
        ensure_dir(path.parent)
        # Create the temp file in the target directory for an atomic same-volume
        # replace. delete=False because we hand the path to os.replace ourselves.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())  # force bytes to disk before the swap
            os.replace(tmp_name, path)  # atomic on Windows + POSIX
        except BaseException:
            # Clean up the temp file on any failure so we don't litter /data.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


def update_json(path: Path, default: Any, mutator) -> Any:
    """
    Atomic read-modify-write under a single lock hold.

    ``mutator`` receives the current data (or ``default`` if the file is absent),
    mutates and/or returns the new value, and the result is written back. Returns
    the value that was written. This is the safe primitive for "add a shift" /
    "toggle clock state" style operations under concurrency.
    """
    with _LOCK:
        current = read_json(path, default)
        result = mutator(current)
        new_value = result if result is not None else current
        write_json(path, new_value)
        return new_value
