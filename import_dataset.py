"""
Import an external dataset (CSV / TSV / JSON / JSONL) into the
SQLite source of truth.

Kaggle, OpenML, data.gov, ... all hand you a file. This script
turns each record into a cache_data row:

    key   = --prefix + <value of the key column>
    value = the rest of the record, as JSON

Examples:

    python import_dataset.py --file users.csv --key-column id --prefix user:
    python import_dataset.py --file products.json --key-column sku --limit 1000
    python import_dataset.py --file logs.jsonl --key-column request_id --dry-run
    python import_dataset.py --file users.csv --clear
"""

import argparse
import csv
import json
import os
import sys
import time

from database.exceptions import DatabaseUnavailableError
from database.repository import DatabaseRepository


BATCH_SIZE = 500

FORMAT_BY_EXTENSION = {
    ".csv": "csv",
    ".tsv": "tsv",
    ".json": "json",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
}


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Load a CSV/JSON/JSONL dataset into data/cache.db"
    )

    parser.add_argument(
        "--file",
        required=True,
        help="Path to the dataset file"
    )

    parser.add_argument(
        "--key-column",
        default=None,
        help="Column used to build the cache key "
             "(default: 'id' if present, else the first column)"
    )

    parser.add_argument(
        "--prefix",
        default="",
        help="Prefix prepended to every key, e.g. 'user:'"
    )

    parser.add_argument(
        "--value-columns",
        default=None,
        help="Comma-separated subset of columns to store "
             "(default: every column except the key column)"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Import at most N records"
    )

    parser.add_argument(
        "--db",
        default=os.environ.get("CACHE_DB_PATH", "data/cache.db"),
        help="SQLite path (default: CACHE_DB_PATH or data/cache.db)"
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Rows per transaction (default: {BATCH_SIZE})"
    )

    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete all existing rows before importing"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate the file without writing anything"
    )

    return parser.parse_args()


# ---------------------------------------------------------
# FILE READING
# ---------------------------------------------------------

def detect_format(path: str) -> str:

    extension = os.path.splitext(path)[1].lower()

    file_format = FORMAT_BY_EXTENSION.get(extension)

    if file_format is None:

        supported = ", ".join(sorted(FORMAT_BY_EXTENSION))

        sys.exit(
            f"Unsupported file type '{extension}'. "
            f"Supported: {supported}"
        )

    return file_format


def read_rows(path: str, file_format: str):
    """Yield one dict per record."""

    if not os.path.exists(path):
        sys.exit(f"File not found: {path}")

    if file_format in ("csv", "tsv"):

        delimiter = "\t" if file_format == "tsv" else ","

        with open(path, newline="", encoding="utf-8-sig") as handle:
            yield from csv.DictReader(handle, delimiter=delimiter)

    elif file_format == "json":

        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)

        if isinstance(data, dict):
            data = [data]

        if not isinstance(data, list):
            sys.exit(
                "JSON file must contain a list of records "
                "(or a single record object)"
            )

        yield from data

    else:  # jsonl

        with open(path, encoding="utf-8") as handle:

            for line_number, line in enumerate(handle, start=1):

                line = line.strip()

                if not line:
                    continue

                try:
                    yield json.loads(line)

                except json.JSONDecodeError as error:
                    sys.exit(
                        f"Invalid JSON on line {line_number}: {error}"
                    )


def coerce(value):
    """CSV cells are always strings; turn plain numbers into numbers."""

    if not isinstance(value, str):
        return value

    stripped = value.strip()

    try:
        return int(stripped)
    except ValueError:
        pass

    try:
        return float(stripped)
    except ValueError:
        pass

    return value


# ---------------------------------------------------------
# RECORD MAPPING
# ---------------------------------------------------------

def pick_key_column(columns, requested):

    if requested:

        if requested not in columns:
            sys.exit(
                f"Key column '{requested}' not found. "
                f"Available columns: {', '.join(columns)}"
            )

        return requested

    if "id" in columns:
        return "id"

    return columns[0]


def entry_from_record(
    record,
    key_column,
    prefix,
    value_columns,
    row_number
):
    """Turn one dataset record into a (key, value) pair."""

    if not isinstance(record, dict):
        sys.exit(
            f"Record {row_number} is not an object "
            f"(got {type(record).__name__})"
        )

    if key_column not in record:
        sys.exit(
            f"Record {row_number} has no '{key_column}' field. "
            f"Available fields: {', '.join(map(str, record.keys()))}"
        )

    raw_key = record[key_column]

    if raw_key is None or str(raw_key) == "":
        sys.exit(f"Record {row_number} has an empty '{key_column}'")

    key = f"{prefix}{raw_key}"

    if value_columns is not None:

        missing = [column for column in value_columns
                   if column not in record]

        if missing:
            sys.exit(
                f"Record {row_number} is missing columns: "
                f"{', '.join(missing)}"
            )

        fields = value_columns

    else:
        fields = [
            column for column in record
            if column != key_column
        ]

    value = {
        column: coerce(record[column])
        for column in fields
    }

    return key, value


def iter_entries(args):
    """Yield (key, value) pairs from the dataset file."""

    file_format = detect_format(args.file)

    records = read_rows(args.file, file_format)

    value_columns = None

    if args.value_columns:
        value_columns = [
            column.strip()
            for column in args.value_columns.split(",")
            if column.strip()
        ]

    for row_number, record in enumerate(records, start=1):

        yield entry_from_record(
            record,
            args.resolved_key_column,
            args.prefix,
            value_columns,
            row_number
        )

        if args.limit and row_number >= args.limit:
            return


def collect_columns(args):
    """Peek at the first record to resolve the key column."""

    file_format = detect_format(args.file)

    first = next(iter(read_rows(args.file, file_format)), None)

    if first is None:
        sys.exit("The file contains no records")

    if not isinstance(first, dict):
        sys.exit(
            f"The first record is not an object "
            f"(got {type(first).__name__})"
        )

    return list(first.keys())


# ---------------------------------------------------------
# IMPORT
# ---------------------------------------------------------

def import_dataset(args):

    started = time.time()

    columns = collect_columns(args)

    args.resolved_key_column = pick_key_column(columns, args.key_column)

    print(f"File          : {args.file}")
    print(f"Format        : {detect_format(args.file)}")
    print(f"Columns       : {', '.join(columns)}")
    print(f"Key column    : {args.resolved_key_column}"
          f"{' (auto)' if not args.key_column else ''}")
    if args.prefix:
        print(f"Key prefix    : {args.prefix}")
    print()

    repository = DatabaseRepository(db_path=args.db)

    if args.clear and not args.dry_run:
        repository.clear()
        print("Cleared existing rows")

    seen_keys = set()
    duplicates = 0

    total = 0
    batch = []

    try:

        for key, value in iter_entries(args):

            if key in seen_keys:
                duplicates += 1

            seen_keys.add(key)

            batch.append((key, value))
            total += 1

            if len(batch) >= args.batch_size:

                if not args.dry_run:
                    repository.set_many(batch)

                batch = []

        if batch and not args.dry_run:
            repository.set_many(batch)

    except DatabaseUnavailableError as error:
        sys.exit(f"Database error: {error}")

    elapsed = time.time() - started

    # -----------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------

    print(f"Records read  : {total}")

    if duplicates:
        print(f"Duplicate keys: {duplicates} (last value wins)")

    if args.dry_run:
        print("Dry run       : nothing was written")
    else:
        print(f"Rows written  : {total}")
        print(f"Database      : {args.db}")

    print(f"Elapsed       : {elapsed:.2f}s")

    # Sample of what was imported.
    if total:

        samples = []

        for key, _ in iter_entries(args):
            samples.append(key)
            if len(samples) == 3:
                break

        print(f"Sample keys   : {', '.join(samples)}")


# ---------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------

if __name__ == "__main__":

    import_dataset(parse_args())
