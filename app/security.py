"""
Admin authentication.

Passwords are hashed with bcrypt via passlib's CryptContext (never stored in
plaintext). The hash lives in admin.json. A signed session cookie
(Starlette SessionMiddleware, backed by itsdangerous) marks a browser as logged
in; the signing secret is generated per-machine and stored in /data so sessions
survive restarts but are not shared between installs.
"""

from __future__ import annotations

import secrets

from passlib.context import CryptContext

from . import paths, repo, storage

# bcrypt only; auto-deprecation left off since we have a single scheme.
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

_SESSION_KEY = "admin_authed"
_SECRET_FILE_KEY = "secret"


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd.verify(plain, hashed)
    except ValueError:
        # Malformed/empty hash — treat as no match rather than raising.
        return False


def is_admin_configured() -> bool:
    """True once a password has been set (i.e. not first run)."""
    return repo.load_admin().password_hash is not None


def set_admin_password(plain: str) -> None:
    admin = repo.load_admin()
    admin.password_hash = hash_password(plain)
    repo.save_admin(admin)


def check_admin_password(plain: str) -> bool:
    admin = repo.load_admin()
    if not admin.password_hash:
        return False
    return verify_password(plain, admin.password_hash)


# --- Session helpers ---------------------------------------------------------

def get_session_secret() -> str:
    """
    Per-machine secret for signing session cookies. Stored in /data/secret.json so
    it persists across restarts. Generated on first use.
    """
    secret_file = paths.data_dir() / "secret.json"
    data = storage.read_json(secret_file, default=None)
    if data and data.get(_SECRET_FILE_KEY):
        return data[_SECRET_FILE_KEY]
    secret = secrets.token_hex(32)
    storage.write_json(secret_file, {_SECRET_FILE_KEY: secret})
    return secret


def login_session(session: dict) -> None:
    session[_SESSION_KEY] = True


def logout_session(session: dict) -> None:
    session.pop(_SESSION_KEY, None)


def is_logged_in(session: dict) -> bool:
    return bool(session.get(_SESSION_KEY))
