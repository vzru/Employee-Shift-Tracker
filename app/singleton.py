"""
Single-instance guard: refuses to let a second copy of the app run at the same
time on this PC.

Why this matters: storage.py's write lock (a threading.RLock) only serializes
writes WITHIN one process. A second server process has its own independent
lock, so two processes racing to read-modify-write the same shift file could
silently drop one of their changes (e.g. two clock-ins landing at nearly the
same moment on two different servers). Rather than coordinate writes across
processes, refuse to start a second server in the first place.

Implementation: a named Windows mutex (a kernel object), the standard Windows
single-instance pattern. The FIRST process to create the name owns it; any
later process sees ERROR_ALREADY_EXISTS and bows out. Unlike the earlier
file-lock approach this is machine-wide, so it also catches a second copy of
the exe launched from a DIFFERENT folder — not just the same one. The mutex is
released automatically by the OS the instant the owning process exits or is
killed, so a crash can never leave a stale lock behind (no PID file to go
stale).
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

# Distinctive, app-specific name. No "Global\\" prefix, so it lives in the
# per-session namespace — one instance per logged-in Windows session, which is
# exactly right for a single-user till PC (and needs no special privileges).
_MUTEX_NAME = "EmployeeShiftTracker_SingleInstance_b4302809"
_ERROR_ALREADY_EXISTS = 183

# Kept referenced for the process lifetime: the mutex is owned only while this
# handle is open. Losing the reference would let the OS release it early.
_mutex_handle: int | None = None


def acquire() -> bool:
    """
    Try to become the sole running instance. Returns True if we are the first
    (and the app should start), False if another instance already holds it.

    Fails OPEN: if the mutex can't be created at all (a very unusual
    environment), allow the app to start rather than brick the till over a
    guard that's only a safety net.
    """
    global _mutex_handle

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    last_error = ctypes.get_last_error()

    if not handle:
        return True  # couldn't create it; don't block startup over a safety net

    if last_error == _ERROR_ALREADY_EXISTS:
        # The name already existed: another instance owns it. Release our handle
        # to the existing mutex and report that we're not the first.
        kernel32.CloseHandle(handle)
        return False

    _mutex_handle = handle
    return True
