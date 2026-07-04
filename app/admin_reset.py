"""
Local, terminal-only admin password reset.

Runs interactively in a console (checked via isatty) and resets the admin
password directly in admin.json. There is deliberately NO network or URL path to
this — it can only be invoked from a terminal on the PC:

  * In development:  python reset_admin.py
  * From the exe:    EmployeeShiftTracker.exe --reset-admin

Both resolve /data the same way the app does (next to the exe when frozen), so
the reset targets the same data the app uses.
"""

from __future__ import annotations

import getpass
import sys

from . import paths, security


def reset_admin_cli() -> int:
    # Enforce terminal-only use: refuse if stdin isn't an interactive tty. This
    # blocks any attempt to drive it non-interactively / remotely.
    if not (sys.stdin and sys.stdin.isatty()):
        print("This tool must be run in an interactive terminal.", file=sys.stderr)
        return 2

    print("=== Employee Shift Tracker — Admin Password Reset ===")
    print(f"Data folder: {paths.data_dir()}")
    print()

    while True:
        pw1 = getpass.getpass("New admin password (min 6 chars): ")
        if len(pw1) < 6:
            print("  Too short — try again.\n")
            continue
        pw2 = getpass.getpass("Confirm new password: ")
        if pw1 != pw2:
            print("  Passwords did not match — try again.\n")
            continue
        break

    security.set_admin_password(pw1)
    print("\nAdmin password has been reset. You can now log in at /admin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(reset_admin_cli())
