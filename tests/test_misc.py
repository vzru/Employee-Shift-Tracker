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


def _grab_port() -> tuple[socket.socket, int]:
    """Bind an OS-assigned port and keep it open (so it stays occupied).
    Returns the listening socket and its port; caller closes it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if exclusive is not None:
        s.setsockopt(socket.SOL_SOCKET, exclusive, 1)
    s.bind((main.HOST, 0))
    s.listen(1)
    return s, s.getsockname()[1]


class TestMain:
    # These monkeypatch PREFERRED_PORTS to dynamically-found ports so they don't
    # depend on 8765 being free (e.g. when the app is running during testing).

    def test_find_free_port_returns_bindable(self):
        port = main._find_free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((main.HOST, port))  # actually free right now

    def test_find_free_port_prefers_first_free(self, monkeypatch):
        busy_sock, busy = _grab_port()
        try:
            monkeypatch.setattr(main, "PREFERRED_PORTS", [busy])
            # Only preferred port is busy -> falls back to an ephemeral port.
            assert main._find_free_port() != busy
        finally:
            busy_sock.close()

    def test_find_free_port_skips_busy_preferred(self, monkeypatch):
        busy_sock, busy = _grab_port()
        free_sock, free = _grab_port()
        free_sock.close()  # release `free` so it's pickable; keep `busy` held
        try:
            monkeypatch.setattr(main, "PREFERRED_PORTS", [busy, free])
            assert main._port_is_free(busy) is False
            assert main._find_free_port() == free
        finally:
            busy_sock.close()

    def test_port_is_free_true_for_unused(self):
        sock, port = _grab_port()
        sock.close()  # now free
        assert main._port_is_free(port) is True

    def test_create_app_builds(self, data_dir):
        app = main.create_app()
        assert app.title == "Employee Shift Tracker"
