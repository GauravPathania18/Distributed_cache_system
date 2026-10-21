"""
Import one external dataset file into its own SQLite database.

Kaggle, OpenML, data.gov, ... all hand you a file. This script
turns each record into one cache_data row inside a per-dataset
SQLite file and registers the result:

    key   = <name>:<key column value>   (auto-detected column)
    value = the rest of the record, as JSON

    data/datasets/<name>.db   the dataset's own physical file
    data/registry.json        the manifest the router and TUI read

One file -> one database -> one searchable entity. No hardcoding
anywhere: the schema is sniffed from the data and drives the
search filters/sorts in the web UI.

Supported formats: CSV, TSV, JSON, JSONL, Parquet, Excel (xlsx)
and Zip (a zip containing any of those).

Examples:

    python import_dataset.py --file datasets/netflix_titles.csv
    python import_dataset.py --file data.xlsx --key-column sku --limit 5000
    python import_dataset.py --file users.csv --name people --reimport
    python import_dataset.py --seed           # import datasets/ drop folder
    python import_dataset.py --list
    python import_dataset.py --delete netflix_titles
    python import_dataset.py --file big.csv --dry-run
"""

import argparse
import csv
import io
import itertools
import json
import os
import sys
import time
import zipfile

from database.exceptions import DatabaseUnavailableError
from database.registry import (
    DatasetRegistry,
    SAMPLE_ROWS,
    coerce,
    detect_key_column,
    detect_schema,
    safe_key,
    slugify,
)
from database.repository import DatabaseRepository


BATCH_SIZE = 500

FORMAT_BY_EXTENSION = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".tab": "tsv",
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".parquet": "parquet",
    ".pq": "parquet",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".zip": "zip",
}


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a CSV/TSV/JSON/JSONL/Parquet/Excel/Zip "
                    "dataset into its own SQLite file"
    )

    parser.add_argument("--file", default=None, help="Path to the dataset file")
    parser.add_argument("--name", default=None,
                        help="Dataset name (default: file name slug)")
    parser.add_argument("--prefix", default=None,
                        help="Key prefix (default: '<name>:')")
    parser.add_argument("--key-column", default=None,
                        help="Column used for cache keys (default: auto)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Import at most N records")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and report without writing anything")
    parser.add_argument("--reimport", action="store_true",
                        help="Replace an already-registered dataset")
    parser.add_argument("--seed", action="store_true",
                        help="Import every unregistered file in the drop folder")
    parser.add_argument("--list", action="store_true", dest="list_datasets",
                        help="List registered datasets")
    parser.add_argument("--delete", metavar="NAME", default=None,
                        help="Unregister a dataset and delete its file")
    parser.add_argument("--drop-dir", default=None,
                        help="Drop folder for --seed (default: datasets/)")

    return parser


def parse_args(argv=None):
    return build_parser().parse_args(argv)


# ---------------------------------------------------------
# FILE READING
# ---------------------------------------------------------

def detect_format(path: str) -> str:
    """File name -> format name (raises ValueError when unknown)."""

    extension = os.path.splitext(str(path))[1].lower()

    file_format = FORMAT_BY_EXTENSION.get(extension)

    if file_format is None:
        supported = ", ".join(sorted(FORMAT_BY_EXTENSION))
        raise ValueError(
            f"unsupported file type {extension!r} (supported: {supported})"
        )

    return file_format


def _read_delimited(handle, delimiter: str):
    yield from csv.DictReader(handle, delimiter=delimiter)


def _read_json(handle):
    data = json.load(handle)

    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise ValueError(
            "JSON file must contain a list of records "
            "(or a single record object)"
        )

    yield from data


def _read_jsonl(handle):
    for line_number, line in enumerate(handle, start=1):

        line = line.strip()

        if not line:
            continue

        try:
            yield json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON on line {line_number}: {error}")


def _read_pandas(source, file_format: str):
    """Parquet / Excel records via pandas (lazy import)."""

    try:
        import pandas as pd
    except ImportError:
        raise ValueError(
            "reading Parquet/Excel needs pandas "
            "(pip install -r requirements.txt)"
        )

    try:

        if file_format == "parquet":
            try:
                import pyarrow  # noqa: F401
            except ImportError:
                raise ValueError(
                    "reading Parquet needs pyarrow "
                    "(pip install -r requirements.txt)"
                )
            frame = pd.read_parquet(source)
        else:
            try:
                import openpyxl  # noqa: F401
            except ImportError:
                raise ValueError(
                    "reading Excel needs openpyxl "
                    "(pip install -r requirements.txt)"
                )
            frame = pd.read_excel(source)

    except ImportError as error:
        raise ValueError(f"missing engine for {file_format}: {error}")

    for record in frame.to_dict("records"):
        yield {str(column): value for column, value in record.items()}


def _zip_member(path: str) -> str:
    """First supported data file inside a zip archive."""

    with zipfile.ZipFile(path) as archive:

        for name in archive.namelist():

            if name.endswith("/") or name.startswith("__MACOSX/"):
                continue

            if os.path.basename(name).startswith("."):
                continue

            extension = os.path.splitext(name)[1].lower()

            if extension in FORMAT_BY_EXTENSION and extension != ".zip":
                return name

    raise ValueError("no supported data file found inside the zip archive")


def read_rows(path: str, file_format: str = None):
    """Yield one dict per record, for every supported format."""

    if not os.path.isfile(path):
        raise ValueError(f"file not found: {path}")

    file_format = file_format or detect_format(path)

    if file_format == "zip":

        with zipfile.ZipFile(path) as archive:

            member = _zip_member(path)
            inner_format = detect_format(member)

            with archive.open(member) as raw:

                if inner_format in ("csv", "tsv"):
                    text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
                    yield from _read_delimited(
                        text, "\t" if inner_format == "tsv" else ","
                    )
                elif inner_format == "json":
                    text = io.TextIOWrapper(raw, encoding="utf-8")
                    yield from _read_json(text)
                elif inner_format == "jsonl":
                    text = io.TextIOWrapper(raw, encoding="utf-8")
                    yield from _read_jsonl(text)
                else:
                    yield from _read_pandas(io.BytesIO(raw.read()), inner_format)

        return

    if file_format in ("csv", "tsv"):
        with open(path, newline="", encoding="utf-8-sig") as handle:
            yield from _read_delimited(
                handle, "\t" if file_format == "tsv" else ","
            )

    elif file_format == "json":
        with open(path, encoding="utf-8") as handle:
            yield from _read_json(handle)

    elif file_format == "jsonl":
        with open(path, encoding="utf-8") as handle:
            yield from _read_jsonl(handle)

    else:  # parquet / excel
        yield from _read_pandas(path, file_format)


# ---------------------------------------------------------
# RECORD MAPPING
# ---------------------------------------------------------

def entry_from_record(record, key_column, prefix, row_number):
    """One dataset record -> one (key, json-safe value) pair."""

    if not isinstance(record, dict):
        raise ValueError(
            f"record {row_number} is not an object "
            f"(got {type(record).__name__})"
        )

    record = {str(column): value for column, value in record.items()}

    if key_column is None:

        key = f"{prefix}{row_number}"
        fields = record.items()

    else:

        if key_column not in record:
            raise ValueError(
                f"record {row_number} has no {key_column!r} field"
            )

        raw = record[key_column]

        if raw in (None, "") or (
            isinstance(raw, str) and not raw.strip()
        ):
            raise ValueError(
                f"record {row_number} has an empty {key_column!r}"
            )

        fragment = safe_key(raw) or str(row_number)

        key = f"{prefix}{fragment}"

        # Sanitized keys ("The Matrix" -> "The+Matrix") keep the
        # original column so no information is lost.
        keep_key_column = fragment != str(raw).strip()

        fields = [
            (column, value) for column, value in record.items()
            if column != key_column or keep_key_column
        ]

    value = {column: coerce(cell) for column, cell in fields}

    return key, value


# ---------------------------------------------------------
# IMPORT
# ---------------------------------------------------------

def import_file(
    file,
    name=None,
    prefix=None,
    key_column=None,
    limit=None,
    dry_run=False,
    reimport=False,
    registry: DatasetRegistry = None,
    verbose=True,
) -> dict:
    """Import one file into its own SQLite file + registry entry.

    Writes into a temporary database first and swaps it into place
    on success, so a failed import never destroys an existing
    dataset. Raises ValueError for user-level problems (missing
    file, unknown format, bad key column, empty file).
    """

    started = time.time()

    registry = registry or DatasetRegistry()

    if not os.path.isfile(file):
        raise ValueError(f"file not found: {file}")

    file_format = detect_format(file)

    dataset_name = slugify(name or file)

    existing = registry.get(dataset_name)

    if existing is not None and not reimport and not dry_run:
        raise ValueError(
            f"dataset {dataset_name!r} is already registered - "
            f"pass --reimport to replace it"
        )

    sample = list(itertools.islice(read_rows(file, file_format), SAMPLE_ROWS))

    if not sample:
        raise ValueError("the file contains no records")

    if not isinstance(sample[0], dict):
        raise ValueError(
            "the first record is not an object "
            f"(got {type(sample[0]).__name__})"
        )

    columns = list(sample[0].keys())

    resolved_key = detect_key_column(columns, sample, key_column)

    resolved_prefix = f"{dataset_name}:" if prefix is None else prefix

    if resolved_prefix.startswith(("q:", "s:")):
        raise ValueError(
            f"prefix {resolved_prefix!r} is reserved for search cache keys"
        )

    schema_sample = [
        {
            column: value for column, value in record.items()
            if column != resolved_key
        }
        if isinstance(record, dict) else record
        for record in sample
    ]

    schema = detect_schema(schema_sample)

    summary = {
        "file": os.path.abspath(file),
        "format": file_format,
        "name": dataset_name,
        "prefix": resolved_prefix,
        "key_column": resolved_key,
        "columns": columns,
        "schema": schema,
        "rows": 0,
        "duplicates": 0,
        "db_path": (
            existing["db_path"] if existing
            else registry.dataset_db_path(dataset_name)
        ),
        "dry_run": dry_run,
    }

    if verbose:
        _print_plan(summary)

    if dry_run:
        if verbose:
            print("Dry run       : nothing was written")
        return summary

    stats = os.stat(file)

    final_path = summary["db_path"]
    temporary_path = final_path + ".import"

    store = DatabaseRepository(db_path=temporary_path)

    seen = set()
    samples = []
    batch = []

    try:

        for row_number, record in enumerate(
            read_rows(file, file_format), start=1
        ):

            if limit is not None and row_number > limit:
                break

            key, value = entry_from_record(
                record, resolved_key, resolved_prefix, row_number
            )

            if key in seen:
                summary["duplicates"] += 1

            seen.add(key)

            if len(samples) < 3:
                samples.append(key)

            batch.append((key, value))
            summary["rows"] += 1

            if len(batch) >= BATCH_SIZE:
                store.set_many(batch)
                batch = []

        if batch:
            store.set_many(batch)

    except Exception:
        _discard(temporary_path)
        raise

    os.replace(temporary_path, final_path)

    summary["db_path"] = final_path

    registry.register(
        dataset_name,
        prefix=resolved_prefix,
        db_path=final_path,
        source_file=os.path.abspath(file),
        file_format=file_format,
        key_column=resolved_key,
        row_count=summary["rows"],
        schema=schema,
        source_size=stats.st_size,
        source_mtime=stats.st_mtime,
    )

    summary["sample_keys"] = samples
    summary["elapsed"] = round(time.time() - started, 2)

    if verbose:
        _print_result(summary)

    return summary


def _discard(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def seed_drop_folder(
    drop_dir: str = None,
    registry: DatasetRegistry = None,
    limit=None,
    verbose=False,
) -> list:
    """Import every unregistered (or changed) file in the drop folder.

    This is what `tui.py` runs on boot: drop a Kaggle CSV into
    datasets/ and the next start imports it once. Already-imported
    files are skipped; a file whose size/mtime changed re-imports.
    """

    registry = registry or DatasetRegistry()

    results = []

    for path in registry.scan_drop_folder(drop_dir):

        dataset_name = slugify(os.path.basename(path))

        already_registered = registry.get(dataset_name) is not None

        try:

            record = import_file(
                path,
                name=dataset_name,
                limit=limit,
                reimport=already_registered,
                registry=registry,
                verbose=verbose,
            )

            record["status"] = (
                "reimported" if already_registered else "imported"
            )

        except (ValueError, DatabaseUnavailableError) as error:

            record = {
                "file": path,
                "name": dataset_name,
                "status": "failed",
                "error": str(error),
                "rows": 0,
            }

        results.append(record)

    return results


# ---------------------------------------------------------
# REPORTING
# ---------------------------------------------------------

def _print_plan(summary: dict) -> None:

    key_note = "" if summary["key_column"] else " (row number)"

    print(f"File          : {summary['file']}")
    print(f"Format        : {summary['format']}")
    print(f"Dataset       : {summary['name']}")
    print(f"Columns       : {', '.join(summary['columns'])}")
    print(f"Key column    : {summary['key_column'] or '-'}{key_note}")
    print(f"Key prefix    : {summary['prefix']}")
    print()


def _print_result(summary: dict) -> None:

    print(f"Records read  : {summary['rows']}")

    if summary["duplicates"]:
        print(f"Duplicate keys: {summary['duplicates']} (last value wins)")

    print(f"Rows written  : {summary['rows']}")
    print(f"Database      : {summary['db_path']}")
    print(f"Elapsed       : {summary.get('elapsed', 0)}s")

    if summary.get("sample_keys"):
        print(f"Sample keys   : {', '.join(summary['sample_keys'])}")


def list_datasets(registry: DatasetRegistry = None) -> list:
    """One info dict per registered dataset (rows read live)."""

    registry = registry or DatasetRegistry()

    out = []

    for name in registry.names():

        entry = registry.get(name)

        rows = 0
        size = 0

        try:
            if os.path.exists(entry["db_path"]):
                rows = DatabaseRepository(db_path=entry["db_path"]).count()
                size = os.path.getsize(entry["db_path"])
        except DatabaseUnavailableError:
            pass

        out.append({**entry, "rows_live": rows, "db_bytes": size})

    return out


# ---------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------

def main(argv=None) -> int:

    parser = build_parser()

    args = parser.parse_args(argv)

    registry = DatasetRegistry(drop_dir=args.drop_dir)

    try:

        if args.list_datasets:
            for entry in list_datasets(registry):
                print(
                    f"{entry['name']:<32} {entry['prefix']:<20} "
                    f"rows={entry['rows_live']:<8} "
                    f"key={entry['key_column'] or '(row #)':<16} "
                    f"{os.path.basename(entry['source_file'] or '-')}"
                )
            return 0

        if args.delete:
            if registry.unregister(args.delete):
                print(f"deleted dataset {args.delete!r}")
                return 0
            print(f"no dataset named {args.delete!r}")
            return 1

        if args.seed:
            results = seed_drop_folder(
                drop_dir=args.drop_dir,
                registry=registry,
                limit=args.limit,
                verbose=True,
            )
            if not results:
                print("drop folder is empty or fully imported")
            failed = [r for r in results if r["status"] == "failed"]
            return 1 if failed else 0

        if args.file:
            import_file(
                args.file,
                name=args.name,
                prefix=args.prefix,
                key_column=args.key_column,
                limit=args.limit,
                dry_run=args.dry_run,
                reimport=args.reimport,
                registry=registry,
                verbose=True,
            )
            return 0

        parser.print_help()
        return 1

    except (ValueError, DatabaseUnavailableError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
