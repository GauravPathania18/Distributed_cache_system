"""
Dataset registry - one JSON manifest describing every imported
dataset and where its data physically lives.

    data/registry.json          the manifest (this file's index)
    data/datasets/<name>.db     one SQLite file per dataset
    data/cache.db               the system repository (search-cache
                                keys q:/s:, manual scratch keys)

All paths derive from CACHE_DB_PATH so tests - which point that
environment variable at a throwaway directory - get a hermetic
registry, datasets directory and system database for free.
"""

import json
import os
import re
import time

from pathlib import Path


# `q:` and `s:` prefixes belong to Layer 8 search-cache entries; a
# dataset named "q" would make those keys ambiguous to route.
RESERVED_NAMES = ("q", "s")
RESERVED_PREFIXES = ("q:", "s:")

SAMPLE_ROWS = 5000
SCHEMA_SAMPLE_ROWS = 1000
MAX_DISTINCT = 30

# Auto-detected key values longer than this are treated as free
# text (overviews, bios, ...), never as identifiers.
KEY_MAX_LENGTH = 64

_KEY_SAFE = re.compile(r"[^A-Za-z0-9_.:+-]")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ].*)?$")

_ID_EXACT = ("id", "uuid", "guid", "sku", "key", "pk")

SUPPORTED_EXTENSIONS = (
    ".csv", ".tsv", ".tab", ".json", ".jsonl", ".ndjson",
    ".parquet", ".pq", ".xlsx", ".xlsm", ".zip",
)


# ---------------------------------------------------------
# NAMING + KEY HYGIENE
# ---------------------------------------------------------

def slugify(text: str) -> str:
    """File stem -> dataset name: lowercase, underscores, no spaces.

    "Zomato Restaurant Dataset.csv" -> "zomato_restaurant_dataset"
    "2020 data.csv"                 -> "d_2020_data" (no leading digit)
    """

    text = os.path.splitext(os.path.basename(str(text)))[0]

    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    text = re.sub(r"_+", "_", text)

    if not text:
        text = "dataset"

    if text[0].isdigit():
        text = f"d_{text}"

    return text


def safe_key(value) -> str:
    """URL-path-safe fragment of a raw key value.

    Cache keys are interpolated raw into node URLs ("/cache/{key}"),
    so a slash would split the path and a space/percent would break
    the request. Spaces become "+", everything outside the safe set
    is dropped. "The Matrix (1999)" -> "The+Matrix+1999".
    """

    text = str(value if value is not None else "").strip()

    text = text.replace(" ", "+")

    return _KEY_SAFE.sub("", text)


def coerce(value):
    """Normalize one record value into a JSON-safe Python value.

    CSV cells are always strings - plain numbers and booleans become
    their real types. numpy scalars (pandas) unwrap via .item(),
    NaN becomes None, datetimes/Decimals/UUIDs stringify. JSON
    records mostly pass through untouched.
    """

    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass

    if isinstance(value, float) and value != value:
        return None

    if isinstance(value, (dict, list, tuple)):
        return value

    if value is None or isinstance(value, (str, int, float, bool)):
        pass

    else:
        return str(value)

    if not isinstance(value, str):
        return value

    stripped = value.strip()

    if stripped in ("True", "true"):
        return True

    if stripped in ("False", "false"):
        return False

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
# SCHEMA + KEY DETECTION
# ---------------------------------------------------------

def detect_schema(sample_rows, max_distinct: int = MAX_DISTINCT) -> dict:
    """Per-column type + low-cardinality values from a sample.

    Types: "string" | "number" | "bool" | "date" (ISO 8086-ish
    YYYY-MM-DD). `values` lists the distinct string forms for
    columns with at most `max_distinct` distinct non-empty values -
    these power enum facets in the UI. Number columns never store
    values (they get range filters instead).
    """

    if not sample_rows:
        return {}

    columns = list(sample_rows[0].keys())

    sample = sample_rows[:SCHEMA_SAMPLE_ROWS]

    schema = {}

    for column in columns:

        present = [
            coerced for coerced in (coerce(row.get(column)) for row in sample)
            if coerced not in (None, "")
        ]

        if not present:
            dtype = "string"
            distinct = 0
            values = None
        else:
            if all(isinstance(v, bool) for v in present):
                dtype = "bool"
            elif all(isinstance(v, (int, float)) for v in present):
                dtype = "number"
            elif all(
                isinstance(v, str) and _ISO_DATE.match(v.strip())
                for v in present
            ):
                dtype = "date"
            elif all(isinstance(v, str) for v in present):
                lowered = {str(v).strip().lower() for v in present}
                dtype = "bool" if lowered <= {"true", "false"} else "string"
            else:
                dtype = "string"

            seen = {str(v) for v in present}
            distinct = len(seen)

            # Only strings keep their distinct forms - they power
            # enum facets. Numbers/dates get range filters, bools a
            # toggle, so storing their forms would just bloat the
            # manifest.
            values = (
                sorted(seen)
                if dtype == "string" and distinct <= max_distinct
                else None
            )

        schema[column] = {
            "type": dtype,
            "distinct": distinct,
            "values": values,
        }

    return schema


def _normalize_column(column) -> str:
    return re.sub(r"[^a-z0-9]", "", str(column).lower())


def detect_key_column(columns, sample_rows, requested=None) -> "str | None":
    """Pick the column that builds cache keys - or None for row index.

    Priority:
        1. explicitly requested column (hard error when missing)
        2. an "id" column (any casing/spelling)
        3. uuid / guid / sku / key / pk
        4. any *\_id / id\_* style column ("show_id", "User_ID",
           "Restaurant ID") in column order
        5. a short column that is 100% unique in the sample
        6. None -> keys become <prefix><row number>, every column
           stays in the value (lossless for id-less exports)
    """

    if requested is not None:
        if requested not in columns:
            raise ValueError(
                f"key column {requested!r} not found "
                f"(available: {', '.join(columns)})"
            )
        return requested

    normalized = {}

    for column in columns:
        normalized.setdefault(_normalize_column(column), column)

    if "id" in normalized:
        return normalized["id"]

    for candidate in _ID_EXACT:
        if candidate in normalized:
            return normalized[candidate]

    for column in columns:
        norm = _normalize_column(column)
        if len(norm) > 2 and norm.endswith("id"):
            return column

    sample = (sample_rows or [])[:SAMPLE_ROWS]

    if sample:
        for column in columns:
            seen = set()
            ok = True
            for row in sample:
                value = row.get(column)
                if value in (None, ""):
                    ok = False
                    break
                text = str(value)
                if len(text) > KEY_MAX_LENGTH:
                    ok = False
                    break
                seen.add(text)
            if ok and len(seen) == len(sample):
                return column

    return None


# ---------------------------------------------------------
# REGISTRY
# ---------------------------------------------------------

class DatasetRegistry:
    """JSON manifest of imported datasets, hot-reloadable.

    entries() re-reads registry.json whenever its mtime/size
    changes, so a router process picks up datasets imported by
    another process (run.py --seed, import_dataset.py) without a
    restart. `revision()` bumps on
    every change and lets caches (the multi-repository's file
    handles, the search entity builder) refresh cheaply.
    """

    def __init__(self, db_path: str = None, drop_dir: str = None):

        resolved = db_path or os.environ.get(
            "CACHE_DB_PATH", "data/cache.db"
        )

        self.base_dir = os.path.dirname(os.path.abspath(resolved))
        self.registry_path = os.path.join(self.base_dir, "registry.json")
        self.datasets_dir = os.path.join(self.base_dir, "datasets")
        self.drop_dir = drop_dir or os.environ.get("DATASETS_DIR", "datasets")

        self._entries = {}
        self._stamp = None
        self._revision = 0

    # ---- loading -------------------------------------------

    def _stat(self):
        try:
            info = os.stat(self.registry_path)
        except OSError:
            return None
        return (info.st_mtime_ns, info.st_size)

    def _load(self) -> dict:

        try:
            with open(self.registry_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return {}

        if not isinstance(payload, dict):
            return {}

        datasets = payload.get("datasets")

        return dict(datasets) if isinstance(datasets, dict) else {}

    def refresh(self) -> int:
        """Re-read when the file changed; return the revision."""

        stamp = self._stat()

        if stamp != self._stamp:
            self._entries = self._load()
            self._stamp = stamp
            self._revision += 1

        return self._revision

    def revision(self) -> int:
        return self.refresh()

    def entries(self) -> dict:
        self.refresh()
        return dict(self._entries)

    def get(self, name):
        return self.entries().get(name)

    def names(self) -> list:
        return sorted(self.entries())

    # ---- mutation ------------------------------------------

    def dataset_db_path(self, name: str) -> str:
        return os.path.join(self.datasets_dir, f"{name}.db")

    def register(
        self,
        name,
        prefix=None,
        db_path=None,
        source_file=None,
        file_format=None,
        key_column=None,
        row_count=0,
        schema=None,
        source_size=None,
        source_mtime=None,
    ) -> dict:
        """Add or replace one manifest entry and persist it."""

        name = slugify(name)

        if name in RESERVED_NAMES:
            raise ValueError(
                f"dataset name {name!r} is reserved - the "
                f"'{name}:' prefix belongs to search cache keys"
            )

        prefix = f"{name}:" if prefix is None else prefix

        if prefix.startswith(RESERVED_PREFIXES):
            raise ValueError(
                f"prefix {prefix!r} is reserved for search cache keys"
            )

        if db_path is None:
            db_path = self.dataset_db_path(name)

        db_path = os.path.abspath(db_path)

        entry = {
            "name": name,
            "prefix": prefix,
            "db_path": db_path,
            "source_file": source_file,
            "format": file_format,
            "key_column": key_column,
            "row_count": int(row_count or 0),
            "schema": schema or {},
            "source_size": source_size,
            "source_mtime": source_mtime,
            "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        datasets = self.entries()
        datasets[name] = entry

        self._save(datasets)

        return entry

    def unregister(self, name, remove_file: bool = True) -> bool:
        """Drop one entry; by default also delete its SQLite file."""

        datasets = self.entries()

        if name not in datasets:
            return False

        entry = datasets.pop(name)

        self._save(datasets)

        if remove_file:
            try:
                os.remove(entry["db_path"])
            except OSError:
                pass

        return True

    def update(self, name, **fields) -> dict:
        """
        Partial update of one manifest entry (Layer 9 dataset admin).

        Keeps every field not named in `fields` - used after entry
        mutations to refresh row_count / schema without re-running
        a full import. Raises KeyError for an unknown dataset.
        """

        datasets = self.entries()

        if name not in datasets:
            raise KeyError(name)

        entry = dict(datasets[name])
        entry.update(fields)

        datasets[name] = entry
        self._save(datasets)

        return entry

    def _save(self, datasets: dict) -> None:

        Path(self.base_dir).mkdir(parents=True, exist_ok=True)

        payload = {
            "version": 1,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "datasets": datasets,
        }

        temporary = self.registry_path + ".tmp"

        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

        os.replace(temporary, self.registry_path)

        self._entries = dict(datasets)
        self._stamp = self._stat()
        self._revision += 1

    # ---- routing -------------------------------------------

    def prefix_for(self, key: str) -> str:
        """Longest registered prefix that owns `key` ("" = system)."""

        best = ""

        for entry in self.entries().values():

            prefix = entry.get("prefix") or ""

            if prefix and key.startswith(prefix) and len(prefix) > len(best):
                best = prefix

        return best

    def entry_for(self, key: str):
        """The manifest entry whose prefix owns `key` (or None)."""

        best = None
        best_len = 0

        for entry in self.entries().values():

            prefix = entry.get("prefix") or ""

            if prefix and key.startswith(prefix) and len(prefix) > best_len:
                best = entry
                best_len = len(prefix)

        return best

    # ---- drop folder ---------------------------------------

    def scan_drop_folder(self, drop_dir: str = None) -> list:
        """Supported files in the drop folder that need importing.

        A file is listed when its dataset name is unregistered, or
        when it is the registered source and its size/mtime changed
        (re-import). A different file that slugifies to an existing
        name is skipped so two same-stem files cannot thrash.
        """

        drop_dir = drop_dir or self.drop_dir

        if not os.path.isdir(drop_dir):
            return []

        entries = self.entries()

        found = []

        for filename in sorted(os.listdir(drop_dir), key=str.lower):

            if filename.startswith("."):
                continue

            path = os.path.join(drop_dir, filename)

            if not os.path.isfile(path):
                continue

            extension = os.path.splitext(filename)[1].lower()

            if extension not in SUPPORTED_EXTENSIONS:
                continue

            name = slugify(filename)

            existing = entries.get(name)

            if existing is not None:

                source = os.path.basename(existing.get("source_file") or "")

                if source != filename:
                    continue

                try:
                    info = os.stat(path)
                except OSError:
                    continue

                if (
                    info.st_size == existing.get("source_size")
                    and info.st_mtime == existing.get("source_mtime")
                ):
                    continue

            found.append(path)

        return found
