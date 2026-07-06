"""Hidden append-only audit log."""

from __future__ import annotations

import json

from app import audit, paths


def _entries(data_dir):
    lines = (data_dir / "audit.log").read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(x) for x in lines]


def test_log_writes_entry_with_metadata(data_dir):
    audit.log("clock_in", "kiosk", employee_id="e1", shift_id="s1")
    entries = _entries(data_dir)
    assert len(entries) == 1
    e = entries[0]
    assert e["action"] == "clock_in"
    assert e["actor"] == "kiosk"
    assert e["employee_id"] == "e1"
    assert e["shift_id"] == "s1"
    assert "ts" in e  # timestamp stamped automatically


def test_log_appends(data_dir):
    audit.log("employee_added", "admin", employee_id="e1")
    audit.log("employee_modified", "admin", employee_id="e1")
    entries = _entries(data_dir)
    assert [e["action"] for e in entries] == ["employee_added", "employee_modified"]


def test_log_file_location(data_dir):
    audit.log("clock_out", "kiosk")
    assert (paths.data_dir() / "audit.log").exists()
