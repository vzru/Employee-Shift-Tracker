"""Kiosk routes via TestClient."""

from __future__ import annotations

from datetime import timedelta

from app import repo, timeutil


def _now_minute(offset_hours=0):
    return (timeutil.now_local() + timedelta(hours=offset_hours)).strftime(
        timeutil.TS_FORMAT_MINUTE)


class TestDashboard:
    def test_renders_empty(self, client, settings_writer):
        settings_writer()
        r = client.get("/")
        assert r.status_code == 200
        assert "Clock In / Out" in r.text

    def test_shows_active_employee_card(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee(first="Jane", last="Doe")
        r = client.get("/")
        assert "Jane Doe" in r.text
        assert "Restaurant" in r.text

    def test_hides_inactive_employee(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee(first="Ghost", last="Gone", active=False)
        assert "Ghost Gone" not in client.get("/").text


class TestClockAction:
    def test_clock_in_then_out(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee()
        # Clock in.
        r = client.post("/clock", data={
            "employee_id": "e1", "role_id": "r1", "timestamp": _now_minute()})
        assert r.status_code == 303
        assert "clocked+in" in r.headers["location"]
        assert repo.find_open_shift("e1") is not None
        # Clock out (server decides it's an out now).
        r = client.post("/clock", data={
            "employee_id": "e1", "role_id": "r1", "timestamp": _now_minute()})
        assert "clocked+out" in r.headers["location"]
        assert repo.find_open_shift("e1") is None

    def test_unknown_employee(self, client, settings_writer):
        settings_writer()
        r = client.post("/clock", data={
            "employee_id": "ghost", "role_id": "r1", "timestamp": _now_minute()})
        assert "Unknown+employee" in r.headers["location"]

    def test_clock_in_requires_role(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee()
        r = client.post("/clock", data={"employee_id": "e1", "timestamp": _now_minute()})
        assert "Choose+a+role" in r.headers["location"]

    def test_invalid_time(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee()
        r = client.post("/clock", data={
            "employee_id": "e1", "role_id": "r1", "timestamp": "not-a-time"})
        assert "Invalid+time" in r.headers["location"]

    def test_far_future_rejected(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee()
        r = client.post("/clock", data={
            "employee_id": "e1", "role_id": "r1", "timestamp": _now_minute(48)})
        assert "future" in r.headers["location"]
        assert repo.find_open_shift("e1") is None  # nothing filed

    def test_far_past_rejected(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee()
        r = client.post("/clock", data={
            "employee_id": "e1", "role_id": "r1", "timestamp": _now_minute(-24 * 10)})
        assert "past" in r.headers["location"]
        assert repo.find_open_shift("e1") is None


class TestCrossSiteGuard:
    def test_cross_site_blocked(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee()
        r = client.post("/clock",
                        data={"employee_id": "e1", "role_id": "r1", "timestamp": _now_minute()},
                        headers={"Sec-Fetch-Site": "cross-site"})
        assert "Blocked+cross-site" in r.headers["location"]
        assert repo.find_open_shift("e1") is None

    def test_same_origin_allowed(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee()
        r = client.post("/clock",
                        data={"employee_id": "e1", "role_id": "r1", "timestamp": _now_minute()},
                        headers={"Sec-Fetch-Site": "same-origin"})
        assert "clocked+in" in r.headers["location"]

    def test_none_navigation_allowed(self, client, make_employee, settings_writer):
        # Sec-Fetch-Site: none is a direct user navigation — allowed.
        settings_writer()
        make_employee()
        r = client.post("/clock",
                        data={"employee_id": "e1", "role_id": "r1", "timestamp": _now_minute()},
                        headers={"Sec-Fetch-Site": "none"})
        assert "clocked+in" in r.headers["location"]
