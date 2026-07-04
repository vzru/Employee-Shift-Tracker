"""
Path resolution that works both in development and when frozen by PyInstaller.

Two very different kinds of paths are needed:

1. BUNDLED ASSETS (templates, static CSS/JS) — these are packed *into* the
   one-file exe. At runtime PyInstaller unpacks them to a temporary folder whose
   path is exposed as ``sys._MEIPASS``. We must load them from there.

2. THE /data FOLDER (employees.json, shift files, admin.json) — this is real
   payroll data that must PERSIST and sit *next to the exe* so the user can find
   and back it up. It must therefore be resolved relative to the directory of the
   actual executable (``sys.executable``), NOT the temporary _MEIPASS dir which is
   deleted when the app exits.

In development (not frozen) both resolve relative to the project root.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _is_frozen() -> bool:
    """True when running inside a PyInstaller-built exe."""
    return getattr(sys, "frozen", False)


def resource_dir() -> Path:
    """
    Base directory for READ-ONLY bundled assets (templates / static).

    - Frozen: the temporary _MEIPASS extraction dir where PyInstaller unpacked
      the bundle.
    - Dev: the ``app`` package directory (this file's parent).
    """
    if _is_frozen():
        # _MEIPASS is set by the PyInstaller bootloader.
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


def app_base_dir() -> Path:
    """
    Directory that the PERSISTENT /data folder lives beside.

    - Frozen: the folder containing the .exe (directory of sys.executable).
      This deliberately ignores _MEIPASS so data persists across runs.
    - Dev: the repository root (one level above the ``app`` package).
    """
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


# --- Concrete resolved paths -------------------------------------------------

def templates_dir() -> Path:
    return resource_dir() / "templates"


def static_dir() -> Path:
    return resource_dir() / "static"


def data_dir() -> Path:
    """The /data folder. Created on first use elsewhere; not created here."""
    return app_base_dir() / "data"


def employees_file() -> Path:
    return data_dir() / "employees.json"


def admin_file() -> Path:
    return data_dir() / "admin.json"


def week_dir(iso_year: int, iso_week: int) -> Path:
    """/data/<YYYY>/week-<WW> with WW zero-padded to 2 digits."""
    return data_dir() / f"{iso_year:04d}" / f"week-{iso_week:02d}"


def shifts_file(iso_year: int, iso_week: int) -> Path:
    return week_dir(iso_year, iso_week) / "shifts.json"
