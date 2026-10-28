import pytest

from database.repository import DatabaseRepository


def make_repo(tmp_path):
    return DatabaseRepository(db_path=str(tmp_path / "test.db"))


def test_set_and_get(tmp_path):

    repo = make_repo(tmp_path)

    repo.set("user:1", {"name": "Gaurav", "age": 21})

    assert repo.get("user:1") == {"name": "Gaurav", "age": 21}


def test_get_missing_returns_none(tmp_path):

    repo = make_repo(tmp_path)

    assert repo.get("nope") is None


def test_set_overwrites(tmp_path):

    repo = make_repo(tmp_path)

    repo.set("A", 1)
    repo.set("A", 2)

    assert repo.get("A") == 2


def test_delete(tmp_path):

    repo = make_repo(tmp_path)

    repo.set("A", 1)

    assert repo.delete("A") is True
    assert repo.get("A") is None
    assert repo.delete("A") is False


def test_exists(tmp_path):

    repo = make_repo(tmp_path)

    repo.set("A", 1)

    assert repo.exists("A") is True
    assert repo.exists("B") is False


def test_clear(tmp_path):

    repo = make_repo(tmp_path)

    repo.set("A", 1)
    repo.set("B", 2)

    repo.clear()

    assert repo.exists("A") is False
    assert repo.exists("B") is False


def test_complex_values_roundtrip(tmp_path):

    repo = make_repo(tmp_path)

    values = {
        "dict": {"nested": {"a": 1, "b": [1, 2, 3]}},
        "list": [1, "two", 3.0, None, True],
        "int": 42,
        "string": "hello",
        "bool": False,
    }

    for key, value in values.items():
        repo.set(key, value)

    for key, value in values.items():
        assert repo.get(key) == value


# =========================================================
# BATCH INSERTS (Layer 6 dataset import)
# =========================================================

def test_set_many_writes_batch(tmp_path):

    repo = make_repo(tmp_path)

    written = repo.set_many([
        ("user:1", {"name": "Gaurav"}),
        ("user:2", {"name": "Alex"}),
    ])

    assert written == 2
    assert repo.get("user:1") == {"name": "Gaurav"}
    assert repo.get("user:2") == {"name": "Alex"}


def test_set_many_upserts_existing_rows(tmp_path):

    repo = make_repo(tmp_path)

    repo.set("user:1", "old")

    repo.set_many([("user:1", "new"), ("user:2", "fresh")])

    assert repo.get("user:1") == "new"
    assert repo.get("user:2") == "fresh"


def test_set_many_with_no_items(tmp_path):

    repo = make_repo(tmp_path)

    assert repo.set_many([]) == 0
    assert repo.exists("anything") is False


# =========================================================
# COUNT & KEYS (dataset verification helpers)
# =========================================================

def test_count_is_zero_on_an_empty_database(tmp_path):

    repo = make_repo(tmp_path)

    assert repo.count() == 0


def test_count_reflects_inserts_updates_and_deletes(tmp_path):

    repo = make_repo(tmp_path)

    repo.set_many([("user:1", {"a": 1}), ("user:2", {"b": 2})])

    assert repo.count() == 2

    # Upserting an existing key must not grow the count.
    repo.set("user:1", {"a": 3})

    assert repo.count() == 2

    repo.delete("user:2")

    assert repo.count() == 1


def test_keys_returns_sorted_keys(tmp_path):

    repo = make_repo(tmp_path)

    repo.set_many([("user:9", 9), ("user:1", 1), ("user:5", 5)])

    assert repo.keys() == ["user:1", "user:5", "user:9"]


def test_keys_respects_the_limit(tmp_path):

    repo = make_repo(tmp_path)

    repo.set_many([(f"user:{index:02d}", index) for index in range(1, 11)])

    assert len(repo.keys()) == 10
    assert repo.keys(limit=3) == ["user:01", "user:02", "user:03"]


def test_keys_is_empty_on_an_empty_database(tmp_path):

    repo = make_repo(tmp_path)

    assert repo.keys() == []


# =========================================================
# READ-ONLY SQL (defense-in-depth for tooling that reuses query())
# =========================================================

def test_query_returns_headers_and_rows(tmp_path):

    repo = make_repo(tmp_path)

    repo.set_many([("user:1", {"city": "Berlin"}),
                   ("user:2", {"city": "Tokyo"})])

    result = repo.query(
        "SELECT key, value FROM cache_data WHERE key LIKE 'user:%' "
        "ORDER BY key"
    )

    assert result["headers"] == ["key", "value"]
    assert [row[0] for row in result["rows"]] == ["user:1", "user:2"]
    assert result["truncated"] is False


def test_query_supports_bound_parameters(tmp_path):

    repo = make_repo(tmp_path)

    repo.set_many([("user:1", 1), ("user:2", 2), ("order:1", 3)])

    result = repo.query(
        "SELECT key FROM cache_data "
        "WHERE substr(key, 1, length(?)) = ? ORDER BY key",
        ("user:", "user:")
    )

    assert [row[0] for row in result["rows"]] == ["user:1", "user:2"]


def test_query_truncates_at_max_rows(tmp_path):

    repo = make_repo(tmp_path)

    repo.set_many([(f"key:{index}", index) for index in range(10)])

    result = repo.query("SELECT key FROM cache_data ORDER BY key",
                        max_rows=3)

    assert len(result["rows"]) == 3
    assert result["truncated"] is True


def test_query_rejects_writes_and_multi_statements(tmp_path):

    repo = make_repo(tmp_path)

    for sql in ("DELETE FROM cache_data",
                "UPDATE cache_data SET value = 'x'",
                "DROP TABLE cache_data",
                "SELECT 1; DELETE FROM cache_data",
                ""):

        with pytest.raises(ValueError):
            repo.query(sql)

    assert repo.count() == 0


def test_query_reports_sql_errors_as_query_errors(tmp_path):

    # A malformed statement (unknown table/column) is a bug in the
    # SQL that was built - NOT a storage outage - so it surfaces as
    # DatabaseQueryError. Reporting it as "database unavailable"
    # would send a misleading 503 + retry advice for something that
    # fails identically forever.
    from database.exceptions import DatabaseQueryError

    repo = make_repo(tmp_path)

    with pytest.raises(DatabaseQueryError):
        repo.query("SELECT * FROM nope")

    with pytest.raises(DatabaseQueryError):
        repo.query("SELECT no_such_column FROM cache_data")