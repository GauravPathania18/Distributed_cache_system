"""
Registry behaviour: manifest persistence, schema + key detection,
drop-folder scanning and prefix routing metadata.
"""

import os

import pytest

from database.registry import (
    DatasetRegistry,
    coerce,
    detect_key_column,
    detect_schema,
    safe_key,
    slugify,
)


def make_registry(tmp_path):
    return DatasetRegistry(db_path=str(tmp_path / "cache.db"))


# =========================================================
# NAMING + KEY HYGIENE
# =========================================================

def test_slugify_normalizes_file_names():

    assert slugify("netflix_titles.csv") == "netflix_titles"
    assert slugify("Zomato Restaurant Dataset.csv") == (
        "zomato_restaurant_dataset"
    )
    assert slugify("People (final).JSON") == "people_final"
    assert slugify("2020 data.csv") == "d_2020_data"
    assert slugify("///") == "dataset"


def test_safe_key_is_url_path_safe():

    assert safe_key("The Matrix (1999)") == "The+Matrix+1999"
    assert safe_key("a/b?c#d") == "abcd"
    assert safe_key("  s123  ") == "s123"
    assert safe_key(None) == ""


def test_coerce_normalizes_csv_cells():

    assert coerce("21") == 21
    assert coerce("3.5") == 3.5
    assert coerce(" 7 ") == 7
    assert coerce("Gaurav") == "Gaurav"
    assert coerce("") == ""
    assert coerce("2026-01-01") == "2026-01-01"
    assert coerce("True") is True
    assert coerce("False") is False

    # Non-strings (JSON records) pass through untouched.
    assert coerce(42) == 42
    assert coerce(None) is None
    assert coerce(True) is True
    assert coerce({"a": 1}) == {"a": 1}
    assert coerce([1, 2]) == [1, 2]


def test_coerce_strings_non_json_scalars():

    import datetime

    assert coerce(datetime.datetime(2024, 1, 2, 3, 4)) == (
        "2024-01-02 03:04:00"
    )
    assert coerce(float("nan")) is None


# =========================================================
# KEY COLUMN DETECTION
# =========================================================

def test_detect_key_column_prefers_requested():

    columns = ["sku", "name"]

    assert detect_key_column(columns, [{"sku": "a"}],
                             requested="sku") == "sku"

    with pytest.raises(ValueError):
        detect_key_column(columns, [{"sku": "a"}], requested="id")


def test_detect_key_column_finds_id_style_columns():

    assert detect_key_column(
        ["name", "id", "age"], [{"name": "a", "id": 1, "age": 3}]
    ) == "id"

    assert detect_key_column(
        ["show_id", "title"], [{"show_id": "s1", "title": "x"}]
    ) == "show_id"

    assert detect_key_column(
        ["User_ID", "Age"], [{"User_ID": 9, "Age": 3}]
    ) == "User_ID"

    assert detect_key_column(
        ["Restaurant ID", "Name"], [{"Restaurant ID": "abc", "Name": "z"}]
    ) == "Restaurant ID"

    assert detect_key_column(
        ["uuid", "label"], [{"uuid": "u", "label": "l"}]
    ) == "uuid"


def test_detect_key_column_falls_back_to_unique_column():

    rows = [{"name": "dup", "code": f"c{i}"} for i in range(5)]

    assert detect_key_column(["name", "code"], rows) == "code"


def test_detect_key_column_rejects_empty_long_and_duplicate_values():

    rows = [
        {"overview": "x" * 100, "slug": ""},
        {"overview": "y" * 100, "slug": ""},
    ]

    assert detect_key_column(["overview", "slug"], rows) is None

    duplicated = [
        {"title": "Same", "city": "Paris"},
        {"title": "Same", "city": "Paris"},
    ]

    assert detect_key_column(["title", "city"], duplicated) is None


# =========================================================
# SCHEMA DETECTION
# =========================================================

def test_detect_schema_types_and_enum_values():

    rows = [
        {"title": "The Matrix", "score": 8.7, "year": 1999,
         "date": "2020-01-01", "active": True},
        {"title": "Speed", "score": 7.1, "year": 1994,
         "date": "2020-02-01", "active": False},
    ]

    schema = detect_schema(rows)

    assert schema["title"]["type"] == "string"
    assert schema["score"]["type"] == "number"
    assert schema["year"]["type"] == "number"
    assert schema["date"]["type"] == "date"
    assert schema["active"]["type"] == "bool"

    assert set(schema["title"]["values"]) == {"The Matrix", "Speed"}
    assert schema["score"]["values"] is None
    assert schema["title"]["distinct"] == 2


def test_detect_schema_caps_distinct_values():

    rows = [{"cat": f"c{i}"} for i in range(40)]

    schema = detect_schema(rows)

    assert schema["cat"]["values"] is None
    assert schema["cat"]["distinct"] == 40


def test_detect_schema_ignores_empty_cells_when_voting():

    rows = [{"n": "42", "blank": ""}, {"n": "7", "blank": None}]

    schema = detect_schema(rows)

    assert schema["n"]["type"] == "number"
    assert schema["blank"]["type"] == "string"
    assert schema["blank"]["values"] is None
    assert schema["blank"]["distinct"] == 0


# =========================================================
# REGISTRY PERSISTENCE
# =========================================================

def test_register_and_reload_roundtrip(tmp_path):

    registry = make_registry(tmp_path)

    entry = registry.register(
        "users",
        row_count=3,
        key_column="id",
        file_format="csv",
        source_file="datasets/users.csv",
        schema={"name": {"type": "string", "distinct": 2,
                         "values": ["Ada", "Bob"]}},
    )

    assert entry["prefix"] == "users:"
    assert entry["db_path"].endswith("users.db")
    assert os.path.isfile(registry.registry_path)

    fresh = DatasetRegistry(db_path=str(tmp_path / "cache.db"))

    assert "users" in fresh.entries()
    assert fresh.get("users")["row_count"] == 3
    assert fresh.get("users")["schema"]["name"]["values"] == ["Ada", "Bob"]


def test_reserved_names_and_prefixes_are_rejected(tmp_path):

    registry = make_registry(tmp_path)

    with pytest.raises(ValueError):
        registry.register("q")

    with pytest.raises(ValueError):
        registry.register("s")

    with pytest.raises(ValueError):
        registry.register("other", prefix="q:")

    registry.register("netflix_titles")
    assert registry.get("netflix_titles") is not None


def test_unregister_removes_entry_and_database_file(tmp_path):

    registry = make_registry(tmp_path)

    db_path = str(tmp_path / "users.db")
    registry.register("users", db_path=db_path)

    with open(db_path, "wb") as handle:
        handle.write(b"x")

    assert registry.unregister("users") is True
    assert registry.get("users") is None
    assert not os.path.exists(db_path)
    assert registry.unregister("users") is False


def test_prefix_for_uses_longest_match(tmp_path):

    registry = make_registry(tmp_path)

    registry.register("a", prefix="a:")
    registry.register("b", prefix="a:b:")

    assert registry.prefix_for("a:b:1") == "a:b:"
    assert registry.prefix_for("a:1") == "a:"
    assert registry.prefix_for("q:kw") == ""

    assert registry.entry_for("a:b:1")["name"] == "b"
    assert registry.entry_for("other:1") is None


def test_revision_reloads_across_instances(tmp_path):

    registry = make_registry(tmp_path)
    other = DatasetRegistry(db_path=str(tmp_path / "cache.db"))

    before = other.revision()

    registry.register("late")

    assert other.revision() > before
    assert "late" in other.entries()


# =========================================================
# DROP FOLDER SCANNING
# =========================================================

def _drop_registry(tmp_path):
    drop = tmp_path / "drop"
    drop.mkdir(exist_ok=True)
    return DatasetRegistry(
        db_path=str(tmp_path / "cache.db"), drop_dir=str(drop)
    ), drop


def test_scan_drop_folder_lists_new_and_changed_files(tmp_path):

    registry, drop = _drop_registry(tmp_path)

    (drop / "alpha.csv").write_text("id\n1\n", encoding="utf-8")
    (drop / "notes.txt").write_text("ignored", encoding="utf-8")
    (drop / ".hidden.csv").write_text("id\n1\n", encoding="utf-8")

    found = registry.scan_drop_folder()
    assert [os.path.basename(path) for path in found] == ["alpha.csv"]

    source = str(drop / "alpha.csv")
    registry.register(
        "alpha",
        source_file=source,
        source_size=os.path.getsize(source),
        source_mtime=os.path.getmtime(source),
    )

    assert registry.scan_drop_folder() == []

    (drop / "alpha.csv").write_text("id\n1\n2\n", encoding="utf-8")

    found = registry.scan_drop_folder()
    assert [os.path.basename(path) for path in found] == ["alpha.csv"]


def test_scan_skips_other_file_slugifying_to_registered_name(tmp_path):

    registry, drop = _drop_registry(tmp_path)

    source = str(drop / "alpha.csv")
    (drop / "alpha.csv").write_text("id\n1\n", encoding="utf-8")

    registry.register(
        "alpha",
        source_file=source,
        source_size=os.path.getsize(source),
        source_mtime=os.path.getmtime(source),
    )

    (drop / "alpha.json").write_text('{"id": 1}', encoding="utf-8")

    assert registry.scan_drop_folder() == []


def test_scan_drop_folder_handles_missing_folder(tmp_path):

    registry, _ = _drop_registry(tmp_path)

    assert registry.scan_drop_folder(str(tmp_path / "nope")) == []


# =========================================================
# LAYER 9 - PARTIAL UPDATES (row_count / schema re-sync)
# =========================================================

def test_update_changes_only_the_named_fields(tmp_path):

    registry = make_registry(tmp_path)

    entry = registry.register("orders", key_column="id")

    assert entry["row_count"] == 0

    updated = registry.update(
        "orders",
        row_count=7,
        schema={"amount": {"type": "number"}}
    )

    assert updated["row_count"] == 7
    assert updated["schema"] == {"amount": {"type": "number"}}

    # Untouched fields keep their values.
    assert updated["prefix"] == "orders:"
    assert updated["key_column"] == "id"
    assert updated["db_path"] == entry["db_path"]

    # ...and the change survives a reload from disk.
    fresh = DatasetRegistry(db_path=str(tmp_path / "cache.db"))

    assert fresh.get("orders")["row_count"] == 7
    assert fresh.get("orders")["key_column"] == "id"
    assert fresh.get("orders")["schema"] == {"amount": {"type": "number"}}


def test_update_unknown_dataset_raises(tmp_path):

    registry = make_registry(tmp_path)

    with pytest.raises(KeyError):
        registry.update("ghost", row_count=1)
