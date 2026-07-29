"""Tests for parent/store.py — JSONLStore and StateDB."""
import json
import os
import pytest
import tempfile

from parent.store import JSONLStore, StateDB


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


class TestJSONLStore:
    def test_append_and_read_all(self, tmp_dir):
        store = JSONLStore(path=f"{tmp_dir}/test.jsonl")
        store.append({"a": 1})
        store.append({"b": 2})
        records = store.read_all()
        assert len(records) == 2
        assert records[0] == {"a": 1}
        assert records[1] == {"b": 2}

    def test_read_all_empty_file(self, tmp_dir):
        store = JSONLStore(path=f"{tmp_dir}/empty.jsonl")
        assert store.read_all() == []

    def test_read_all_nonexistent_file(self, tmp_dir):
        store = JSONLStore(path=f"{tmp_dir}/nope.jsonl")
        assert store.read_all() == []

    def test_last_returns_final_record(self, tmp_dir):
        store = JSONLStore(path=f"{tmp_dir}/test.jsonl")
        store.append({"x": 1})
        store.append({"x": 2})
        store.append({"x": 3})
        assert store.last() == {"x": 3}

    def test_last_returns_none_when_empty(self, tmp_dir):
        store = JSONLStore(path=f"{tmp_dir}/test.jsonl")
        assert store.last() is None

    def test_handles_blank_lines(self, tmp_dir):
        path = f"{tmp_dir}/test.jsonl"
        with open(path, "w") as f:
            f.write('{"a": 1}\n\n\n{"b": 2}\n')
        store = JSONLStore(path=path)
        records = store.read_all()
        assert len(records) == 2

    def test_creates_parent_dirs(self, tmp_dir):
        store = JSONLStore(path=f"{tmp_dir}/nested/deep/test.jsonl")
        store.append({"ok": True})
        assert store.read_all() == [{"ok": True}]

    def test_corrupt_lines_skipped(self, tmp_dir):
        """Corrupt JSON lines should be skipped, not crash read_all."""
        path = f"{tmp_dir}/corrupt.jsonl"
        with open(path, "w") as f:
            f.write('{"a": 1}\n')
            f.write('this is not json\n')
            f.write('{"b": 2}\n')
            f.write('{"broken: true\n')
            f.write('{"c": 3}\n')
        store = JSONLStore(path=path)
        records = store.read_all()
        assert len(records) == 3
        assert records[0] == {"a": 1}
        assert records[1] == {"b": 2}
        assert records[2] == {"c": 3}

    def test_serializes_non_json_types(self, tmp_dir):
        """default=str in json.dumps handles Decimal, datetime, etc."""
        from decimal import Decimal
        store = JSONLStore(path=f"{tmp_dir}/test.jsonl")
        store.append({"price": Decimal("123.45")})
        records = store.read_all()
        assert records[0]["price"] == "123.45"


class TestStateDB:
    def test_put_and_get(self, tmp_dir):
        db = StateDB(path=f"{tmp_dir}/test.db")
        db.put("key1", {"val": 42})
        assert db.get("key1") == {"val": 42}
        db.close()

    def test_get_nonexistent_returns_none(self, tmp_dir):
        db = StateDB(path=f"{tmp_dir}/test.db")
        assert db.get("missing") is None
        db.close()

    def test_put_overwrites(self, tmp_dir):
        db = StateDB(path=f"{tmp_dir}/test.db")
        db.put("k", "v1")
        db.put("k", "v2")
        assert db.get("k") == "v2"
        db.close()

    def test_delete(self, tmp_dir):
        db = StateDB(path=f"{tmp_dir}/test.db")
        db.put("k", "v")
        db.delete("k")
        assert db.get("k") is None
        db.close()

    def test_delete_nonexistent_is_noop(self, tmp_dir):
        db = StateDB(path=f"{tmp_dir}/test.db")
        db.delete("nope")  # should not raise
        db.close()

    def test_keys(self, tmp_dir):
        db = StateDB(path=f"{tmp_dir}/test.db")
        db.put("b", 1)
        db.put("a", 2)
        db.put("c", 3)
        assert db.keys() == ["a", "b", "c"]
        db.close()

    def test_keys_empty(self, tmp_dir):
        db = StateDB(path=f"{tmp_dir}/test.db")
        assert db.keys() == []
        db.close()

    def test_wal_mode_enabled(self, tmp_dir):
        db = StateDB(path=f"{tmp_dir}/test.db")
        mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        db.close()

    def test_stores_various_types(self, tmp_dir):
        db = StateDB(path=f"{tmp_dir}/test.db")
        db.put("int", 42)
        db.put("float", 3.14)
        db.put("str", "hello")
        db.put("list", [1, 2, 3])
        db.put("dict", {"nested": True})
        db.put("bool", True)
        db.put("null", None)
        assert db.get("int") == 42
        assert db.get("float") == 3.14
        assert db.get("str") == "hello"
        assert db.get("list") == [1, 2, 3]
        assert db.get("dict") == {"nested": True}
        assert db.get("bool") is True
        assert db.get("null") is None
        db.close()

    def test_creates_parent_dirs(self, tmp_dir):
        db = StateDB(path=f"{tmp_dir}/nested/deep/test.db")
        db.put("k", "v")
        assert db.get("k") == "v"
        db.close()

    def test_persistence_across_instances(self, tmp_dir):
        path = f"{tmp_dir}/test.db"
        db1 = StateDB(path=path)
        db1.put("persist", "yes")
        db1.close()

        db2 = StateDB(path=path)
        assert db2.get("persist") == "yes"
        db2.close()
