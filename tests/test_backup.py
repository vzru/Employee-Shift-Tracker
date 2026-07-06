"""Daily backup: dual location, per-day dedup, retention pruning."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import backup


@pytest.fixture
def backup_env(tmp_path, monkeypatch):
    """Isolate TEMP, LOCALAPPDATA, and the /data folder for backup tests."""
    temp = tmp_path / "temp"
    local = tmp_path / "local"
    data_parent = tmp_path / "app"
    temp.mkdir(); local.mkdir(); (data_parent / "data").mkdir(parents=True)
    (data_parent / "data" / "employees.json").write_text("[]")

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(backup.paths, "app_base_dir", lambda: data_parent)
    return {"temp": temp, "local": local}


def _snap_names(root):
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


class TestBackupRoots:
    def test_two_roots_when_localappdata_set(self, backup_env):
        roots = backup.backup_roots()
        assert len(roots) == 2
        assert roots[0].name == backup.BACKUP_DIRNAME
        assert "backups" in str(roots[1])

    def test_single_root_without_localappdata(self, backup_env, monkeypatch):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert len(backup.backup_roots()) == 1


class TestEnsureDailyBackup:
    def test_creates_today_in_both_locations(self, backup_env):
        backup.ensure_daily_backup()
        today = f"backup-{date.today().isoformat()}"
        for root in backup.backup_roots():
            assert today in _snap_names(root)
            # The data payload was actually copied.
            assert (root / today / "employees.json").exists()

    def test_idempotent_same_day(self, backup_env):
        backup.ensure_daily_backup()
        backup.ensure_daily_backup()
        for root in backup.backup_roots():
            assert len(_snap_names(root)) == 1

    def test_prunes_beyond_retention(self, backup_env):
        root = backup.backup_roots()[0]
        root.mkdir(parents=True)
        # Seed 20 old dated snapshots.
        for i in range(1, 21):
            d = root / f"backup-{(date.today() - timedelta(days=i)).isoformat()}"
            d.mkdir()
        backup.ensure_daily_backup()
        assert len(_snap_names(root)) == backup.RETENTION_DAYS
        # Newest kept, oldest dropped.
        assert f"backup-{date.today().isoformat()}" in _snap_names(root)

    def test_keeps_newest_after_prune(self, backup_env):
        root = backup.backup_roots()[0]
        root.mkdir(parents=True)
        for i in range(1, 21):
            (root / f"backup-{(date.today() - timedelta(days=i)).isoformat()}").mkdir()
        backup.ensure_daily_backup()
        kept = _snap_names(root)
        oldest_kept = date.fromisoformat(min(kept)[len("backup-"):])
        assert (date.today() - oldest_kept).days <= backup.RETENTION_DAYS


class TestSnapshotDates:
    def test_ignores_non_matching_names(self, backup_env):
        root = backup.backup_roots()[0]
        root.mkdir(parents=True)
        (root / "backup-2026-07-01").mkdir()
        (root / "random").mkdir()
        (root / "backup-not-a-date").mkdir()
        dates = backup._snapshot_dates(root)
        assert dates == [date(2026, 7, 1)]

    def test_last_backup_date(self, backup_env):
        root = backup.backup_roots()[0]
        root.mkdir(parents=True)
        (root / "backup-2026-07-01").mkdir()
        (root / "backup-2026-07-05").mkdir()
        assert backup.last_backup_date(root) == date(2026, 7, 5)

    def test_last_backup_date_none_when_empty(self, backup_env):
        assert backup.last_backup_date(backup.backup_roots()[0]) is None
