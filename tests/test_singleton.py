"""Single-instance named-mutex guard."""

from __future__ import annotations

import ctypes
import uuid

import pytest

from app import singleton


@pytest.fixture
def fresh_mutex(monkeypatch):
    """Give each test a unique mutex name and a clean handle slot, and
    release whatever it acquires afterwards so tests don't leak kernel
    objects or interfere with each other."""
    monkeypatch.setattr(singleton, "_MUTEX_NAME",
                        f"EST_test_{uuid.uuid4().hex}")
    monkeypatch.setattr(singleton, "_mutex_handle", None)
    yield
    if singleton._mutex_handle:
        ctypes.WinDLL("kernel32").CloseHandle(singleton._mutex_handle)


def test_first_acquire_succeeds(fresh_mutex):
    assert singleton.acquire() is True


def test_second_acquire_same_name_fails(fresh_mutex):
    assert singleton.acquire() is True
    # A second acquire of the same name (even in-process) sees the existing
    # mutex and reports it's not the sole instance.
    assert singleton.acquire() is False


def test_handle_retained_after_success(fresh_mutex):
    singleton.acquire()
    assert singleton._mutex_handle  # kept referenced so the lock is held
