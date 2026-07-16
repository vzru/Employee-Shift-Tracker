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
        assert "Jane D." in r.text          # kiosk shows the shortened name
        assert "Jane Doe" not in r.text     # full name hidden on the public kiosk
        assert "Restaurant" in r.text

    def test_kiosk_hides_middle_name(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee(first="Emma M", last="Blais")
        r = client.get("/")
        assert "Emma B." in r.text
        assert "Emma M" not in r.text       # middle initial dropped

    def test_kiosk_uses_preferred_name(self, client, make_employee, settings_writer):
        settings_writer()
        make_employee(first="Kulbir K", last="Johal", preferred_name="Kevin")
        r = client.get("/")
        assert "Kevin J." in r.text
        assert "Kulbir" not in r.text

    def test_audit_keeps_full_name(self, client, make_employee, settings_writer, data_dir):
        settings_writer()
        make_employee(first="Emma M", last="Blais")
        client.post("/clock", data={
            "employee_id": "e1", "role_id": "r1", "timestamp": _now_minute()})
        # Records keep the full name even though the kiosk displays the short one.
        assert "Emma M Blais" in (data_dir / "audit.log").read_text()

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


class TestClockSafety:
    ROLES2 = [
        {"id": "r1", "title": "Cook", "department": "Restaurant", "hourly_rate": 20.0},
        {"id": "r2", "title": "Server", "department": "Restaurant", "hourly_rate": 18.0},
    ]

    def _clock(self, client, when, role="r1"):
        return client.post("/clock", data={
            "employee_id": "e1", "role_id": role,
            "timestamp": when.strftime(timeutil.TS_FORMAT_MINUTE)})

    def test_reopen_after_accidental_clockout(self, client, make_employee, make_shift, settings_writer):
        settings_writer(clock_safety_enabled=True, clock_buffer_minutes=15)
        make_employee()
        now = timeutil.now_local()
        ci = timeutil.to_iso(now - timedelta(hours=3))
        co = timeutil.to_iso(now - timedelta(minutes=5))
        sid = make_shift(ci, co)
        r = self._clock(client, now)
        assert "clocked+in" in r.headers["location"]
        found = repo.find_open_shift("e1")
        assert found is not None and found[1].id == sid      # continued the same shift
        ws = timeutil.week_start_for(timeutil.parse_iso(ci), 6)
        assert len(repo.load_week_shifts(ws)) == 1            # no new shift created

    def test_new_shift_when_role_differs(self, client, make_employee, make_shift, settings_writer):
        settings_writer(clock_safety_enabled=True, clock_buffer_minutes=15)
        make_employee(roles=self.ROLES2)
        now = timeutil.now_local()
        make_shift(timeutil.to_iso(now - timedelta(hours=3)),
                   timeutil.to_iso(now - timedelta(minutes=5)), role_id="r1")
        r = self._clock(client, now, role="r2")
        assert "clocked+in" in r.headers["location"]
        found = repo.find_open_shift("e1")
        assert found is not None and found[1].role_id == "r2"  # fresh shift, different role

    def test_clockout_within_buffer_removes_shift(self, client, make_employee, make_shift, settings_writer):
        settings_writer(clock_safety_enabled=True, clock_buffer_minutes=15)
        make_employee()
        now = timeutil.now_local()
        # Opened 5 REAL minutes ago -> genuine mis-tap within the window.
        make_shift(timeutil.to_iso(now - timedelta(minutes=5)), None)
        assert repo.find_open_shift("e1") is not None
        r = self._clock(client, now)                           # clock out
        assert "undone" in r.headers["location"]
        assert repo.find_open_shift("e1") is None
        ws = timeutil.week_start_for(now, 6)
        assert len(repo.load_week_shifts(ws)) == 0             # stray shift removed

    def test_clockout_after_buffer_closes_normally(self, client, make_employee, make_shift, settings_writer):
        settings_writer(clock_safety_enabled=True, clock_buffer_minutes=15)
        make_employee()
        now = timeutil.now_local()
        # Opened 30 REAL minutes ago -> past the window -> normal close.
        make_shift(timeutil.to_iso(now - timedelta(minutes=30)), None)
        r = self._clock(client, now)
        assert "clocked+out" in r.headers["location"]
        ws = timeutil.week_start_for(now, 6)
        shifts = repo.load_week_shifts(ws)
        assert len(shifts) == 1 and shifts[0].clock_out is not None

    def test_edited_clockout_time_cannot_delete_real_shift(self, client, make_employee, make_shift, settings_writer):
        # A real long shift must NOT be deletable by editing the clock-out time
        # to near the clock-in: the delete decision uses real elapsed time, not
        # the employee-submitted timestamp.
        settings_writer(clock_safety_enabled=True, clock_buffer_minutes=15)
        make_employee()
        now = timeutil.now_local()
        ci = now - timedelta(hours=3)
        make_shift(timeutil.to_iso(ci), None)                  # open for 3 hours
        # Employee edits the clock-out field to just 5 minutes after clock-in.
        r = self._clock(client, ci + timedelta(minutes=5))
        assert "clocked+out" in r.headers["location"]          # closed, NOT deleted
        ws = timeutil.week_start_for(ci, 6)
        shifts = repo.load_week_shifts(ws)
        assert len(shifts) == 1 and shifts[0].clock_out is not None

    def test_disabled_records_normally(self, client, make_employee, settings_writer):
        settings_writer(clock_safety_enabled=False)
        make_employee()
        now = timeutil.now_local()
        self._clock(client, now)
        r = self._clock(client, now + timedelta(minutes=2))
        assert "clocked+out" in r.headers["location"]          # not removed
        ws = timeutil.week_start_for(now, 6)
        assert len(repo.load_week_shifts(ws)) == 1


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
