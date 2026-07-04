"""
Development entry point for the local admin password reset.

Usage (from the repo root, with the venv active or via the venv python):
    python reset_admin.py

On the packaged app (no Python on the target PC), use instead:
    EmployeeShiftTracker.exe --reset-admin
"""

from app.admin_reset import reset_admin_cli

if __name__ == "__main__":
    raise SystemExit(reset_admin_cli())
