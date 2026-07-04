"""
Shared web dependencies: the Jinja2 templates object and the admin-auth guard.
Kept in its own module so route modules can import it without cycling through
main.py.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import paths, security

# Single templates instance, pointed at the bundled/dev templates directory.
templates = Jinja2Templates(directory=str(paths.templates_dir()))


def require_admin(request: Request):
    """
    FastAPI dependency: allow the request through only if an admin session is
    active. Otherwise raise a redirect to the admin login page.

    Raising the RedirectResponse (a Starlette Response) short-circuits the request
    cleanly for HTML routes.
    """
    if not security.is_logged_in(request.session):
        raise _RedirectException()
    return True


class _RedirectException(Exception):
    """Signals that the caller should be redirected to /admin/login."""


def redirect(url: str, status_code: int = 303) -> RedirectResponse:
    """Helper for POST-redirect-GET (303 keeps the redirect a GET)."""
    return RedirectResponse(url=url, status_code=status_code)
