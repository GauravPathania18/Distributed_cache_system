"""
import_dataset: format detection, record mapping, per-dataset
import into its own SQLite file + registry entry, and drop-folder
seeding (the flow run.py --seed runs on boot).
"""

import json
import os
import zipfile

import pytest

from database import DatasetRegistry, MultiDatasetRepository
from database.repository import DatabaseRepository
from import_dataset import (
    detect_format,
    entry_from_record,
    import_file,
    list_datasets,
    main,
    read_rows,
    seed_drop_folder,
)


def make_registry(tmp_path, drop_dir=None):
    return DatasetRegistry(
        db_path=str(tmp_path / "cache.db"), drop_dir=drop_dir
    )


def make_store(tmp_path, registry):
    return MultiDatasetRepository(
        db_path=str(tmp_path / "cache.db"), registry=registry
    )


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return str(path)


# =========================================================
# FORMAT DETECTION + READING
# =========================================================

def test_detect_format_supports_every_extension():

    assert detect_format("a.csv") == "csv"
    assert detect_format("a.tsv") == "tsv"
    assert detect_format("a.tab") == "tsv"
    assert detect_format("a.json") == "json"
    assert detect_format("a.jsonl") == "jsonl"
    assert detect_format("a.ndjson") == "jsonl"
    assert detect_format("a.parquet") == "parquet"
    assert detect_format("a.pq") == "parquet"
    assert detect_format("a.xlsx") == "excel"
    assert detect_format("a.xlsm") == "excel"
    assert detect_format("a.zip") == "zip"
    assert detect_format("DATA.CSV") == "csv"

    with pytest.raises(ValueError):
        detect_format("a.txt")


def test_read_rows_csv_and_tsv(tmp_path):

    csv_path = write(tmp_path / "d.csv", "id,name\n1,Ada\n2,Bob\n")
    tsv_path = write(tmp_path / "d.tsv", "id\tname\n1\tAda\n")

    assert list(read_rows(csv_path)) == [
        {"id": "1", "name": "Ada"},
        {"id": "2", "name": "Bob"},
    ]
    assert list(read_rows(tsv_path)) == [{"id": "1", "name": "Ada"}]


def test_read_rows_json_list_object_and_jsonl(tmp_path):

    list_path = write(tmp_path / "d.json", '[{"id": 1}, {"id": 2}]')
    object_path = write(tmp_path / "o.json", '{"id": 7}')
    jsonl_path = write(
        tmp_path / "d.jsonl", '{"id": 1}\n\n{"id": 2}\n'
    )

    assert list(read_rows(list_path)) == [{"id": 1}, {"id": 2}]
    assert list(read_rows(object_path)) == [{"id": 7}]
    assert list(read_rows(jsonl_path)) == [{"id": 1}, {"id": 2}]


def test_read_rows_rejects_bad_json(tmp_path):

    scalar = write(tmp_path / "s.json", "5")
    broken = write(tmp_path / "b.jsonl", "{not json}\n")

    with pytest.raises(ValueError):
        list(read_rows(scalar))

    with pytest.raises(ValueError):
        list(read_rows(broken))


def test_read_rows_missing_file(tmp_path):

    with pytest.raises(ValueError):
        list(read_rows(str(tmp_path / "nope.csv")))


# =========================================================
# RECORD MAPPING
# =========================================================

def test_entry_from_record_splits_key_and_value():

    key, value = entry_from_record(
        {"id": 7, "name": "Gaurav", "age": 21},
        key_column="id",
        prefix="user:",
        row_number=1,
    )

    assert key == "user:7"
    assert value == {"name": "Gaurav", "age": 21}
    assert "id" not in value


def test_entry_from_record_row_index_keeps_every_column():

    key, value = entry_from_record(
        {"name": "Ada", "city": "London"},
        key_column=None,
        prefix="p:",
        row_number=3,
    )

    assert key == "p:3"
    assert value == {"name": "Ada", "city": "London"}


def test_entry_from_record_sanitized_keys_keep_the_column():

    key, value = entry_from_record(
        {"title": "The Matrix (1999)", "year": 1999},
        key_column="title",
        prefix="m:",
        row_number=1,
    )

    assert key == "m:The+Matrix+1999"

    # Not a plain id drop: the sanitized column stays in the value.
    assert value["title"] == "The Matrix (1999)"
    assert value["year"] == 1999


def test_entry_from_record_reports_bad_records():

    with pytest.raises(ValueError):
        entry_from_record(
            {"name": "no id"}, key_column="id", prefix="", row_number=3
        )

    with pytest.raises(ValueError):
        entry_from_record(
            {"id": ""}, key_column="id", prefix="", row_number=4
        )

    with pytest.raises(ValueError):
        entry_from_record(
            "not an object", key_column="id", prefix="", row_number=5
        )


# =========================================================
# END-TO-END IMPORT
# =========================================================

def test_import_csv_into_its_own_database(tmp_path):

    csv_file = write(
        tmp_path / "users.csv",
        "id,name,age\n1,Gaurav,21\n2,Alex,20\n",
    )

    registry = make_registry(tmp_path)

    summary = import_file(csv_file, registry=registry, verbose=False)

    assert summary["name"] == "users"
    assert summary["prefix"] == "users:"
    assert summary["key_column"] == "id"
    assert summary["rows"] == 2
    assert summary["duplicates"] == 0
    assert summary["db_path"].endswith("users.db")

    entry = registry.get("users")
    assert entry["row_count"] == 2
    assert entry["key_column"] == "id"
    assert entry["format"] == "csv"

    # Physical file exists and holds the rows.
    repo = DatabaseRepository(db_path=entry["db_path"])
    assert repo.count() == 2
    assert repo.get("users:1") == {"name": "Gaurav", "age": 21}
    assert repo.get("users:2") == {"name": "Alex", "age": 20}

    # ...and the multi-repository facade routes to it.
    store = make_store(tmp_path, registry)
    assert store.get("users:1") == {"name": "Gaurav", "age": 21}
    assert store.system.count() == 0

    # No temp file left behind.
    assert not os.path.exists(entry["db_path"] + ".import")


def test_import_preserves_booleans(tmp_path):

    csv_file = write(
        tmp_path / "users.csv",
        "id,name,active\n1,Gaurav,True\n2,Alex,False\n",
    )

    registry = make_registry(tmp_path)
    import_file(csv_file, registry=registry, verbose=False)

    repo = DatabaseRepository(
        db_path=registry.get("users")["db_path"]
    )

    assert repo.get("users:1") == {"name": "Gaurav", "active": True}
    assert repo.get("users:2") == {"name": "Alex", "active": False}


def test_import_auto_detects_show_id_style_columns(tmp_path):

    csv_file = write(
        tmp_path / "netflix.csv",
        "show_id,title\ns1,Beef\ns2,Dark\n",
    )

    registry = make_registry(tmp_path)
    summary = import_file(csv_file, registry=registry, verbose=False)

    assert summary["key_column"] == "show_id"
    assert summary["prefix"] == "netflix:"

    repo = DatabaseRepository(
        db_path=registry.get("netflix")["db_path"]
    )

    assert repo.get("netflix:s1") == {"title": "Beef"}
    assert repo.get("netflix:s2") == {"title": "Dark"}


def test_import_without_key_column_uses_row_numbers(tmp_path):

    # No id-ish column and no unique column -> lossless row keys.
    csv_file = write(
        tmp_path / "people.csv",
        "name,city\nMary,Paris\nBob,Paris\nMary,Rome\n",
    )

    registry = make_registry(tmp_path)
    summary = import_file(csv_file, registry=registry, verbose=False)

    assert summary["key_column"] is None

    repo = DatabaseRepository(
        db_path=registry.get("people")["db_path"]
    )

    assert repo.get("people:1") == {"name": "Mary", "city": "Paris"}
    assert repo.get("people:3") == {"name": "Mary", "city": "Rome"}


def test_import_respects_limit(tmp_path):

    csv_file = write(tmp_path / "users.csv", "id,name\n1,a\n2,b\n3,c\n")

    registry = make_registry(tmp_path)
    summary = import_file(
        csv_file, registry=registry, limit=1, verbose=False
    )

    assert summary["rows"] == 1
    assert registry.get("users")["row_count"] == 1

    repo = DatabaseRepository(
        db_path=registry.get("users")["db_path"]
    )
    assert repo.get("users:1") == {"name": "a"}
    assert repo.get("users:2") is None


def test_import_dry_run_writes_nothing(tmp_path):

    csv_file = write(tmp_path / "users.csv", "id,name\n1,Gaurav\n")

    registry = make_registry(tmp_path)
    summary = import_file(
        csv_file, registry=registry, dry_run=True, verbose=False
    )

    assert summary["dry_run"] is True
    assert summary["key_column"] == "id"
    assert registry.get("users") is None
    assert not os.path.exists(
        registry.dataset_db_path("users")
    )


def test_import_requires_reimport_for_registered_dataset(tmp_path):

    csv_file = write(tmp_path / "users.csv", "id,name\n1,a\n2,b\n")

    registry = make_registry(tmp_path)
    import_file(csv_file, registry=registry, verbose=False)

    # Same file again -> refuse...
    with pytest.raises(ValueError):
        import_file(csv_file, registry=registry, verbose=False)

    # ...unless --reimport replaces the data.
    write(tmp_path / "users.csv", "id,name\n1,a\n2,b\n3,c\n")

    summary = import_file(
        csv_file, registry=registry, reimport=True, verbose=False
    )

    assert summary["rows"] == 3
    assert registry.get("users")["row_count"] == 3

    repo = DatabaseRepository(
        db_path=registry.get("users")["db_path"]
    )
    assert repo.get("users:3") == {"name": "c"}


def test_import_counts_duplicate_keys(tmp_path):

    csv_file = write(tmp_path / "dupes.csv", "id,name\n1,a\n1,b\n2,c\n")

    registry = make_registry(tmp_path)
    summary = import_file(csv_file, registry=registry, verbose=False)

    assert summary["rows"] == 3
    assert summary["duplicates"] == 1

    repo = DatabaseRepository(
        db_path=registry.get("dupes")["db_path"]
    )
    assert repo.count() == 2  # last value wins
    assert repo.get("dupes:1") == {"name": "b"}


def test_import_rejects_reserved_prefix_and_user_errors(tmp_path):

    registry = make_registry(tmp_path)

    good = write(tmp_path / "ok.csv", "id,x\n1,a\n")
    empty = write(tmp_path / "empty.csv", "")
    weird = write(tmp_path / "notes.txt", "hi")

    with pytest.raises(ValueError):
        import_file(good, registry=registry, prefix="q:", verbose=False)

    with pytest.raises(ValueError):
        import_file(str(tmp_path / "missing.csv"), registry=registry)

    with pytest.raises(ValueError):
        import_file(weird, registry=registry)

    with pytest.raises(ValueError):
        import_file(empty, registry=registry, verbose=False)

    with pytest.raises(ValueError):
        import_file(
            good, registry=registry, key_column="nope", verbose=False
        )

    # Nothing got registered by any of the failures.
    assert registry.names() == []


def test_failed_import_never_destroys_existing_data(tmp_path):

    csv_file = write(tmp_path / "users.csv", "id,name\n1,a\n2,b\n")

    registry = make_registry(tmp_path)
    import_file(csv_file, registry=registry, verbose=False)

    entry = registry.get("users")
    db_file = entry["db_path"]

    # Corrupt the source: third record has an empty key field.
    write(tmp_path / "users.csv", "id,name\n1,a\n2,b\n,row\n")

    with pytest.raises(ValueError):
        import_file(
            csv_file, registry=registry, reimport=True, verbose=False
        )

    # Old data intact, no temp file left behind.
    repo = DatabaseRepository(db_path=db_file)
    assert repo.count() == 2
    assert repo.get("users:2") == {"name": "b"}
    assert not os.path.exists(db_file + ".import")
    assert registry.get("users")["row_count"] == 2


def test_import_json_dataset(tmp_path):

    json_file = write(
        tmp_path / "products.json",
        '[{"sku": "p1", "price": 49.5}, {"sku": "p2", "price": 19}]',
    )

    registry = make_registry(tmp_path)
    summary = import_file(json_file, registry=registry, verbose=False)

    assert summary["key_column"] == "sku"
    assert summary["prefix"] == "products:"

    repo = DatabaseRepository(
        db_path=registry.get("products")["db_path"]
    )
    assert repo.get("products:p1") == {"price": 49.5}
    assert repo.get("products:p2") == {"price": 19}


def test_import_jsonl_dataset(tmp_path):

    jsonl_file = write(
        tmp_path / "logs.jsonl",
        '{"request_id": "r-1", "status": 200}\n'
        '{"request_id": "r-2", "status": 503}\n',
    )

    registry = make_registry(tmp_path)
    summary = import_file(jsonl_file, registry=registry, verbose=False)

    assert summary["key_column"] == "request_id"

    repo = DatabaseRepository(
        db_path=registry.get("logs")["db_path"]
    )
    assert repo.get("logs:r-1") == {"status": 200}
    assert repo.get("logs:r-2") == {"status": 503}


def test_import_zip_with_inner_csv(tmp_path):

    with zipfile.ZipFile(tmp_path / "bundle.zip", "w") as archive:
        archive.writestr("data.csv", "id,x\n1,a\n2,b\n")

    registry = make_registry(tmp_path)
    summary = import_file(
        str(tmp_path / "bundle.zip"), registry=registry, verbose=False
    )

    assert summary["name"] == "bundle"
    assert summary["rows"] == 2

    repo = DatabaseRepository(
        db_path=registry.get("bundle")["db_path"]
    )
    assert repo.get("bundle:1") == {"x": "a"}


def test_import_parquet_dataset(tmp_path):

    pd = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")

    pd.DataFrame(
        [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
    ).to_parquet(tmp_path / "pqdata.parquet")

    registry = make_registry(tmp_path)
    summary = import_file(
        str(tmp_path / "pqdata.parquet"),
        registry=registry,
        verbose=False,
    )

    assert summary["format"] == "parquet"
    assert summary["rows"] == 2

    repo = DatabaseRepository(
        db_path=registry.get("pqdata")["db_path"]
    )
    assert repo.get("pqdata:1") == {"v": "a"}


def test_import_excel_dataset(tmp_path):

    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")

    pd.DataFrame(
        [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
    ).to_excel(tmp_path / "sheet.xlsx", index=False)

    registry = make_registry(tmp_path)
    summary = import_file(
        str(tmp_path / "sheet.xlsx"), registry=registry, verbose=False
    )

    assert summary["format"] == "excel"

    repo = DatabaseRepository(
        db_path=registry.get("sheet")["db_path"]
    )
    assert repo.get("sheet:1") == {"v": "a"}
    assert repo.get("sheet:2") == {"v": "b"}


# =========================================================
# SEEDING THE DROP FOLDER
# =========================================================

def test_seed_imports_new_files_once_then_changed_files(tmp_path):

    drop = tmp_path / "drop"
    drop.mkdir()

    write(drop / "alpha.csv", "id,x\n1,a\n2,b\n")
    write(drop / "beta.csv", "id,y\n1,c\n")
    write(drop / "notes.txt", "ignored")

    registry = make_registry(tmp_path, drop_dir=str(drop))

    first = seed_drop_folder(registry=registry)

    assert sorted(r["status"] for r in first) == [
        "imported", "imported"
    ]
    assert sorted(registry.names()) == ["alpha", "beta"]

    # Second run: everything already imported -> nothing to do.
    assert seed_drop_folder(registry=registry) == []

    # Changed file -> re-imported; unchanged -> skipped.
    write(drop / "alpha.csv", "id,x\n1,a\n2,b\n3,c\n")

    third = seed_drop_folder(registry=registry)

    assert [r["status"] for r in third] == ["reimported"]
    assert third[0]["name"] == "alpha"
    assert registry.get("alpha")["row_count"] == 3

    # A broken file is reported, not raised.
    write(drop / "broken.csv", "")

    fourth = seed_drop_folder(registry=registry)

    assert fourth[0]["status"] == "failed"
    assert fourth[0]["name"] == "broken"
    assert "no records" in fourth[0]["error"]
    assert "broken" not in registry.names()


def test_seed_imports_an_explicit_drop_dir(tmp_path):

    drop = tmp_path / "inbox"
    drop.mkdir()
    write(drop / "gamma.csv", "id,z\n1,g\n")

    registry = make_registry(tmp_path)

    results = seed_drop_folder(drop_dir=str(drop), registry=registry)

    assert [r["status"] for r in results] == ["imported"]
    assert registry.get("gamma")["row_count"] == 1


# =========================================================
# LISTING + CLI
# =========================================================

def test_list_datasets_reports_live_rows(tmp_path):

    csv_file = write(tmp_path / "users.csv", "id,name\n1,a\n2,b\n")

    registry = make_registry(tmp_path)
    import_file(csv_file, registry=registry, verbose=False)

    listings = list_datasets(registry)

    assert len(listings) == 1

    entry = listings[0]

    assert entry["name"] == "users"
    assert entry["rows_live"] == 2
    assert entry["db_bytes"] > 0
    assert entry["key_column"] == "id"


def test_cli_list_and_error_paths(tmp_path, capsys):

    assert main(["--list"]) == 0
    capsys.readouterr()

    assert main(["--file", str(tmp_path / "missing.csv")]) == 1
    capsys.readouterr()

    assert main([]) == 1
    capsys.readouterr()


def test_cli_import_then_delete(tmp_path, capsys):

    csv_file = write(tmp_path / "cli_data.csv", "id,x\n1,a\n")

    assert main([
        "--file", str(csv_file),
        "--dry-run",
    ]) == 0

    out = capsys.readouterr().out
    assert "Dry run" in out

    assert main(["--file", str(csv_file)]) == 0
    capsys.readouterr()

    # The CLI uses the shared registry (CACHE_DB_PATH env in tests).
    shared = DatasetRegistry()
    assert "cli_data" in shared.names()

    assert main(["--delete", "cli_data"]) == 0
    capsys.readouterr()

    assert main(["--delete", "cli_data"]) == 1
