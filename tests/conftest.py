"""
Shared fixtures. Every test gets an isolated temp /data folder (via monkeypatch
of paths.app_base_dir) so tests never touch real data and never see each
other's files. Route tests build a fresh FastAPI app against that same folder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import paths, security, timeutil


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point the whole app at a fresh temp /data for this one test."""
    monkeypatch.setattr(paths, "app_base_dir", lambda: tmp_path)
    d = tmp_path / "data"
    d.mkdir()
    return d


# --- Data builders -----------------------------------------------------------

@pytest.fixture
def make_employee(data_dir: Path):
    """Append an employee record to employees.json and return its dict."""
    def _make(emp_id="e1", first="Jane", last="Doe", rate=20.0, active=True,
              vacation_pay_percent=4.0, roles=None):
        emp = {
            "id": emp_id, "first_name": first, "last_name": last,
            "active": active, "vacation_pay_percent": vacation_pay_percent,
            "roles": roles if roles is not None else [
                {"id": "r1", "title": "Cook", "department": "Restaurant",
                 "hourly_rate": rate},
            ],
        }
        f = data_dir / "employees.json"
        existing = json.loads(f.read_text()) if f.exists() else []
        existing.append(emp)
        f.write_text(json.dumps(existing))
        return emp
    return _make


@pytest.fixture
def make_shift(data_dir: Path):
    """Write a shift into the correct week file (Sunday-start by default)."""
    def _make(clock_in: str, clock_out: str | None, emp_id="e1", shift_id=None,
              rate=20.0, role_id="r1", role_title="Cook", department="Restaurant",
              auto_clocked_out=False, voided=False, week_start_weekday=6):
        _make.counter = getattr(_make, "counter", 0) + 1
        sid = shift_id or f"s{_make.counter}"
        ws = timeutil.week_start_for(timeutil.parse_iso(clock_in), week_start_weekday)
        week_dir = data_dir / f"{ws.year:04d}" / f"week-{ws.isoformat()}"
        week_dir.mkdir(parents=True, exist_ok=True)
        f = week_dir / "shifts.json"
        shifts = json.loads(f.read_text()) if f.exists() else []
        shifts.append({
            "id": sid, "employee_id": emp_id,
            "clock_in": clock_in, "clock_out": clock_out,
            "role_id": role_id, "role_title": role_title,
            "department": department, "hourly_rate": rate,
            "hours": (round(timeutil.hours_between(clock_in, clock_out), 2)
                      if clock_out else None),
            "auto_clocked_out": auto_clocked_out, "voided": voided,
        })
        f.write_text(json.dumps(shifts))
        return sid
    return _make


def _settings_dict(break_enabled=False, break_minutes=30, break_trigger=5.0,
                   ot_enabled=False, ot_threshold=44.0, ot_multiplier=1.5,
                   week_start_weekday=6, min_wage=17.60,
                   auto_enabled=False, auto_threshold=24.0):
    return {
        "break_rules": {"enabled": break_enabled, "duration_minutes": break_minutes,
                        "trigger_hours": break_trigger},
        "overtime": {"enabled": ot_enabled, "multiplier": ot_multiplier,
                     "weekly_threshold": ot_threshold,
                     "week_start_weekday": week_start_weekday},
        "min_wage": {"rate": min_wage},
        "auto_clockout": {"enabled": auto_enabled, "threshold_hours": auto_threshold},
        "role_catalog": {"Restaurant": ["Cook"]},
    }


@pytest.fixture
def settings_writer(data_dir: Path):
    """Write admin.json with specific settings (password unset by default)."""
    def _write(password_hash=None, **kw):
        (data_dir / "admin.json").write_text(json.dumps({
            "password_hash": password_hash,
            "settings": _settings_dict(**kw),
        }))
    return _write


# --- App / client fixtures ---------------------------------------------------

@pytest.fixture
def client(data_dir):
    """A TestClient over a fresh app bound to the isolated data dir.

    follow_redirects is off so tests can assert on 303 targets; server
    exceptions propagate so any accidental 500 fails the test loudly.
    """
    from fastapi.testclient import TestClient
    from app.main import create_app
    return TestClient(create_app(), follow_redirects=False)


ADMIN_PASSWORD = "test-password-123"


@pytest.fixture
def admin_client(data_dir, client):
    """A TestClient already logged in as admin.

    Writes admin.json (hash + default settings) then logs in so the session
    cookie is set. Tests can still overwrite settings afterwards via
    repo.save_settings without disturbing the login (it preserves the hash).
    """
    (data_dir / "admin.json").write_text(json.dumps({
        "password_hash": security.hash_password(ADMIN_PASSWORD),
        "settings": _settings_dict(),
    }))
    resp = client.post("/admin/login", data={"password": ADMIN_PASSWORD})
    assert resp.status_code == 303
    return client
