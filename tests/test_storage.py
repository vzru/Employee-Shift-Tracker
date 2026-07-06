"""Atomic JSON storage helpers."""

from __future__ import annotations

import json

from app import storage


class TestReadWrite:
    def test_read_missing_returns_default(self, tmp_path):
        assert storage.read_json(tmp_path / "nope.json", default=[]) == []
        assert storage.read_json(tmp_path / "nope.json", default={"a": 1}) == {"a": 1}

    def test_write_then_read_roundtrip(self, tmp_path):
        p = tmp_path / "x.json"
        storage.write_json(p, {"hello": "world", "n": 3})
        assert storage.read_json(p, default=None) == {"hello": "world", "n": 3}

    def test_write_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "a" / "b" / "c.json"
        storage.write_json(p, [1, 2, 3])
        assert p.exists()
        assert storage.read_json(p, default=None) == [1, 2, 3]

    def test_write_is_pretty_and_unicode(self, tmp_path):
        p = tmp_path / "u.json"
        storage.write_json(p, {"name": "Café Déjà"})
        text = p.read_text(encoding="utf-8")
        assert "Café Déjà" in text          # not \u escaped
        assert "\n" in text                  # indent=2 => multiline

    def test_write_overwrites(self, tmp_path):
        p = tmp_path / "o.json"
        storage.write_json(p, {"v": 1})
        storage.write_json(p, {"v": 2})
        assert storage.read_json(p, default=None) == {"v": 2}

    def test_no_temp_files_left_behind(self, tmp_path):
        p = tmp_path / "clean.json"
        storage.write_json(p, {"ok": True})
        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "clean.json"]
        assert leftovers == []


class TestAppendJsonLine:
    def test_appends_one_object_per_line(self, tmp_path):
        p = tmp_path / "log.jsonl"
        storage.append_json_line(p, {"a": 1})
        storage.append_json_line(p, {"b": 2})
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1}
        assert json.loads(lines[1]) == {"b": 2}

    def test_creates_parent_dir(self, tmp_path):
        p = tmp_path / "deep" / "log.jsonl"
        storage.append_json_line(p, {"x": 1})
        assert p.exists()


class TestUpdateJson:
    def test_mutator_receives_default_when_missing(self, tmp_path):
        p = tmp_path / "u.json"
        result = storage.update_json(p, default=[], mutator=lambda cur: cur + [1])
        assert result == [1]
        assert storage.read_json(p, default=None) == [1]

    def test_mutator_can_mutate_in_place_and_return_none(self, tmp_path):
        p = tmp_path / "u.json"
        storage.write_json(p, [1])

        def mutate(cur):
            cur.append(2)          # in place, returns None

        result = storage.update_json(p, default=[], mutator=mutate)
        assert result == [1, 2]

    def test_returns_written_value(self, tmp_path):
        p = tmp_path / "u.json"
        result = storage.update_json(p, default={}, mutator=lambda cur: {"replaced": True})
        assert result == {"replaced": True}
        assert storage.read_json(p, default=None) == {"replaced": True}

    def test_lock_is_reentrant(self, tmp_path):
        # update_json holds the lock while calling read_json (also locking).
        # If the lock weren't reentrant this would deadlock.
        p = tmp_path / "r.json"
        storage.write_json(p, {"n": 1})
        storage.update_json(p, default={}, mutator=lambda cur: {"n": cur["n"] + 1})
        assert storage.read_json(p, default=None) == {"n": 2}
