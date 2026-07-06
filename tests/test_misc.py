"""Smaller modules: datainfo, deps helpers, main port picker, app factory."""

from __future__ import annotations

import socket

import pytest
from pydantic import BaseModel

from app import datainfo, main, paths
from app.deps import _RedirectException, _json_default, redirect, require_admin


class TestDatainfo:
    def test_writes_readme_once(self, data_dir):
        datainfo.ensure_data_readme()
        readme = paths.data_dir() / "README.md"
        assert readme.exists()
        assert "Employee Shift Tracker" in readme.read_text(encoding="utf-8")

    def test_does_not_overwrite_existing(self, data_dir):
        readme = paths.data_dir() / "README.md"
        datainfo.ensure_data_readme()
        readme.write_text("CUSTOM", encoding="utf-8")
        datainfo.ensure_data_readme()  # second call must not clobber
        assert readme.read_text(encoding="utf-8") == "CUSTOM"


class TestDepsHelpers:
    def test_json_default_serializes_pydantic(self):
        class M(BaseModel):
            a: int
        assert _json_default(M(a=1)) == {"a": 1}

    def test_json_default_rejects_other(self):
        with pytest.raises(TypeError):
            _json_default(object())

    def test_redirect_is_303_get(self):
        r = redirect("/somewhere")
        assert r.status_code == 303
        assert r.headers["location"] == "/somewhere"

    def test_require_admin_raises_when_logged_out(self):
        class Req:
            session: dict = {}
        with pytest.raises(_RedirectException):
            require_admin(Req())

    def test_require_admin_passes_when_logged_in(self):
        class Req:
            session = {"admin_authed": True}
        assert require_admin(Req()) is True


class TestMain:
    def test_find_free_port_returns_bindable(self):
        port = main._find_free_port()
        # We can actually bind it (it's free right now).
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((main.HOST, port))

    def test_find_free_port_prefers_first_when_all_free(self):
        # With nothing bound, it returns the first preferred port.
        # (NB: the probe uses SO_REUSEADDR, so on Windows it can't reliably
        # detect a port another SO_REUSEADDR socket holds — but the
        # single-instance mutex means a 2nd instance never reaches here.)
        assert main._find_free_port() == main.PREFERRED_PORTS[0]

    def test_create_app_builds(self, data_dir):
        app = main.create_app()
        assert app.title == "Employee Shift Tracker"
