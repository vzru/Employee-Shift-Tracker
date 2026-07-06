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

from . import backup, datainfo, paths, security, singleton, storage
from .deps import _RedirectException, redirect
from .routes import admin as admin_routes
from .routes import kiosk as kiosk_routes

# Loopback only; the port is chosen at runtime (see run()).
HOST = "127.0.0.1"
PREFERRED_PORTS = [8765, 8766, 8767, 8000, 8080]

# Admin session idle timeout. SessionMiddleware re-stamps the signed cookie on
# every response that carries session data, so max_age acts as a SLIDING idle
# window: the admin is logged out only after this many seconds with no request
# from their browser. The kiosk has no session, so it's unaffected.
SESSION_IDLE_SECONDS = 30 * 60


def create_app() -> FastAPI:
    # Make sure /data exists (next to the exe) and document its schema.
    storage.ensure_dir(paths.data_dir())
    datainfo.ensure_data_readme()

    app = FastAPI(title="Employee Shift Tracker", docs_url=None, redoc_url=None)

    # Signed session cookies for the admin login (secret persisted in /data).
    # max_age gives a 30-minute idle logout (see SESSION_IDLE_SECONDS).
    app.add_middleware(
        SessionMiddleware,
        secret_key=security.get_session_secret(),
        max_age=SESSION_IDLE_SECONDS,
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

def _port_is_free(port: int) -> bool:
    """
    True if we could start a server on ``port`` right now.

    Deliberately does NOT set SO_REUSEADDR: on Windows that flag lets a bind
    "succeed" on a port another SO_REUSEADDR socket is already using, so the
    probe would wrongly report a busy port as free. Where available (Windows)
    SO_EXCLUSIVEADDRUSE is set so the bind fails if anyone holds the port at
    all. The probe socket is closed immediately (it never accepts a
    connection, so it leaves no TIME_WAIT behind).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            try:
                s.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            except OSError:
                pass
        try:
            s.bind((HOST, port))
            s.listen(1)
            return True
        except OSError:
            return False


def _find_free_port() -> int:
    """Return the first preferred port that is free, else an OS-assigned one."""
    for port in PREFERRED_PORTS:
        if _port_is_free(port):
            return port
    # Fall back to any free ephemeral port (0 => OS assigns a free one).
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
    if not singleton.acquire():
        print("Employee Shift Tracker is already running on this PC.")
        print("Use the browser tab / console window that's already open instead")
        print("of starting a second copy.")
        try:
            input("Press Enter to close this window...")
        except EOFError:
            pass  # stdin closed (launched without a console) — just exit
        return

    port = _find_free_port()
    url = f"http://{HOST}:{port}/"
    _open_browser_when_ready.port = port  # type: ignore[attr-defined]
    print(f"Employee Shift Tracker running at {url}")
    print(f"Data folder: {paths.data_dir()}")
    print("Close this window to stop the server.")
    backup.start_daily_backup_thread()
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
