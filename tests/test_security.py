"""Password hashing and session helpers."""

from __future__ import annotations

from app import security


class TestPasswordHashing:
    def test_hash_verify_roundtrip(self):
        h = security.hash_password("hunter2!")
        assert security.verify_password("hunter2!", h) is True

    def test_verify_wrong_password(self):
        h = security.hash_password("correct")
        assert security.verify_password("wrong", h) is False

    def test_hash_is_not_plaintext(self):
        h = security.hash_password("secret")
        assert "secret" not in h

    def test_verify_malformed_hash_is_false_not_error(self):
        assert security.verify_password("x", "not-a-bcrypt-hash") is False
        assert security.verify_password("x", "") is False


class TestAdminPassword:
    def test_unconfigured_by_default(self, data_dir):
        assert security.is_admin_configured() is False
        assert security.check_admin_password("anything") is False

    def test_set_then_check(self, data_dir):
        security.set_admin_password("s3cret!")
        assert security.is_admin_configured() is True
        assert security.check_admin_password("s3cret!") is True
        assert security.check_admin_password("nope") is False

    def test_set_preserves_settings(self, data_dir, settings_writer):
        settings_writer(min_wage=20.0)
        security.set_admin_password("pw1234")
        from app import repo
        assert repo.load_settings().min_wage.rate == 20.0  # settings untouched


class TestSessionSecret:
    def test_secret_persists_across_calls(self, data_dir):
        s1 = security.get_session_secret()
        s2 = security.get_session_secret()
        assert s1 == s2
        assert len(s1) >= 32

    def test_secret_file_written(self, data_dir):
        security.get_session_secret()
        assert (data_dir / "secret.json").exists()


class TestSessionHelpers:
    def test_login_logout_flags_session(self):
        session: dict = {}
        assert security.is_logged_in(session) is False
        security.login_session(session)
        assert security.is_logged_in(session) is True
        security.logout_session(session)
        assert security.is_logged_in(session) is False

    def test_logout_is_idempotent(self):
        session: dict = {}
        security.logout_session(session)  # no KeyError on empty
        assert security.is_logged_in(session) is False
