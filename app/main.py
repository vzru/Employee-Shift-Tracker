"""
Application entry point.

Creates the FastAPI app, wires up sessions/static/routes, and — when run
directly — picks a free local port, starts uvicorn, and opens the default web
browser to the app. Also ensures the /data folder exists next to the exe.
"""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import datainfo, paths, security, storage
from .deps import _RedirectException, redirect
from .routes import admin as admin_routes
from .routes import kiosk as kiosk_routes

# Loopback only; the port is chosen at runtime (see run()).
HOST = "127.0.0.1"
PREFERRED_PORTS = [8765, 8766, 8767, 8000, 8080]


def create_app() -> FastAPI:
    # Make sure /data exists (next to the exe) and document its schema.
    storage.ensure_dir(paths.data_dir())
    datainfo.ensure_data_readme()

    app = FastAPI(title="Employee Shift Tracker", docs_url=None, redoc_url=None)

    # Signed session cookies for the admin login (secret persisted in /data).
    app.add_middleware(
        SessionMiddleware,
        secret_key=security.get_session_secret(),
        same_site="lax",
        https_only=False,  # local http only
    )

    # Serve bundled CSS/JS from /static.
    app.mount(
        "/static",
        StaticFiles(directory=str(paths.static_dir())),
        name="static",
    )

    # Convert the auth guard's redirect signal into an actual redirect response.
    @app.exception_handler(_RedirectException)
    async def _on_redirect(request: Request, exc: _RedirectException):
        return redirect("/admin/login")

    app.include_router(kiosk_routes.router)
    app.include_router(admin_routes.router)
    return app


app = create_app()


# --- Standalone launch -------------------------------------------------------

def _find_free_port() -> int:
    """Return the first preferred port that is free, else an OS-assigned one."""
    for port in PREFERRED_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue  # busy, try the next
    # Fall back to any free ephemeral port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def _open_browser_when_ready(url: str) -> None:
    """Open the default browser shortly after the server starts accepting."""
    def worker():
        # Give uvicorn a moment to bind before opening the tab.
        import time
        for _ in range(50):  # up to ~5s
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex((HOST, _open_browser_when_ready.port)) == 0:
                    break
            time.sleep(0.1)
        webbrowser.open(url)

    threading.Thread(target=worker, daemon=True).start()


def run() -> None:
    port = _find_free_port()
    url = f"http://{HOST}:{port}/"
    _open_browser_when_ready.port = port  # type: ignore[attr-defined]
    print(f"Employee Shift Tracker running at {url}")
    print(f"Data folder: {paths.data_dir()}")
    print("Close this window to stop the server.")
    _open_browser_when_ready(url)
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


def main() -> None:
    """CLI dispatch: support `--reset-admin` for the packaged exe, else run."""
    if "--reset-admin" in sys.argv[1:]:
        from .admin_reset import reset_admin_cli
        raise SystemExit(reset_admin_cli())
    run()


if __name__ == "__main__":
    main()
