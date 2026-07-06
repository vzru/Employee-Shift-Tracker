"""Path resolution (dev mode — not frozen)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app import paths


def test_not_frozen_in_dev():
    assert paths._is_frozen() is False


def test_data_dir_under_app_base(data_dir):
    assert paths.data_dir() == data_dir
    assert paths.data_dir().name == "data"


def test_named_files(data_dir):
    assert paths.employees_file() == data_dir / "employees.json"
    assert paths.admin_file() == data_dir / "admin.json"


def test_week_dir_format(data_dir):
    wd = paths.week_dir(date(2026, 6, 28))
    assert wd == data_dir / "2026" / "week-2026-06-28"
    assert paths.shifts_file(date(2026, 6, 28)) == wd / "shifts.json"


def test_week_dir_uses_week_start_year_across_new_year(data_dir):
    # A week that starts in 2025 files under 2025 even if it runs into 2026.
    assert paths.week_dir(date(2025, 12, 28)).parent.name == "2025"


def test_templates_and_static_exist():
    # Bundled assets resolve to the real package dir in dev.
    assert (paths.templates_dir() / "base.html").exists()
    assert paths.static_dir().is_dir()


def test_resource_dir_is_package_dir():
    assert paths.resource_dir() == Path(paths.__file__).resolve().parent
