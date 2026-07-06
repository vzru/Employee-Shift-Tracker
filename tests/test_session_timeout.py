"""Admin session idle-timeout behaviour (30 min sliding window)."""

from __future__ import annotations

import itsdangerous
from starlette.middleware.sessions import SessionMiddleware

from app import main, security


def test_session_max_age_is_30_minutes():
    assert main.SESSION_IDLE_SECONDS == 30 * 60


def test_session_middleware_wired_with_idle_max_age(data_dir):
    # The app must actually pass max_age into SessionMiddleware — that's what
    # makes the login expire after 30 min of inactivity (Starlette re-stamps
    # the signed cookie on every response, so max_age is a sliding window).
    app = main.create_app()
    session_mw = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
    assert session_mw.kwargs["max_age"] == main.SESSION_IDLE_SECONDS


def test_active_session_stays_logged_in(admin_client):
    # A normal follow-up request keeps the session valid.
    assert admin_client.get("/admin").status_code == 200


def test_cookie_older_than_max_age_is_rejected(data_dir):
    # A token stamped beyond the window fails signature verification, so the
    # middleware would treat it as logged out. (Uses a generous 2s gap vs a
    # 1s max_age so integer-second timestamp granularity can't make it flaky.)
    import time
    signer = itsdangerous.TimestampSigner(security.get_session_secret())
    token = signer.sign(b"payload")
    assert signer.unsign(token, max_age=main.SESSION_IDLE_SECONDS) == b"payload"
    time.sleep(2)
    try:
        signer.unsign(token, max_age=1)
        expired = False
    except itsdangerous.SignatureExpired:
        expired = True
    assert expired
