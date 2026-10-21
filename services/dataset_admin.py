"""
Layer 9 - dataset & entry administration for the web UI.

One place owns every mutation a dataset can suffer from the UI:

    create(name)              -> isolated SQLite file + manifest entry
    add_entry(name, value)    -> conflict-free key, write-through
    delete_entry(name, key)   -> row delete + cache evict cascade
    delete_dataset(name)      -> prefix flush + purge + file removal
    list() / entries(name)    -> browse

Keeping the copies consistent is the whole point. Every mutation
touches up to four places:

    1. database rows    source of truth - row_count/schema re-synced
    2. cache nodes      evicted (one key) or prefix-flushed (all)
    3. search caches    q:<entity>: / s:<entity>: result sets purged,
                        or the Search tab would serve stale pages
    4. replication log  matching ops dropped, so a lagging replica
                        cannot resurrect a flushed row on next sync

Key rules (conflict-free by construction):

    key_column set in the manifest -> <prefix><value[key_column]>
    value has an "id" field         -> <prefix><id>
    otherwise                       -> <prefix><next free number>

Repeated ids upsert (POST reports created: False) instead of
colliding with the cache-node schema - nodes never see keys, only
the router's write-through path.
"""

import os

from database.registry import detect_schema, slugify
from database.repository import DatabaseRepository


# Rows sampled when (re)detecting a dataset's schema after a change.
SCHEMA_SAMPLE = 50


class DatasetAdmin:
    """Registry + store + cache mutations for datasets (Layer 9)."""

    def __init__(self, registry, database, cache_service,
                 distributed_cache, replication):

        self.registry = registry
        self.database = database
        self.cache_service = cache_service
        self.distributed_cache = distributed_cache
        self.replication = replication

    # --------------------------------------------------
    # READ
    # --------------------------------------------------

    def list(self) -> list:
        """Every registered dataset with its live row count."""

        out = []

        for name, entry in sorted(self.registry.entries().items()):

            record = dict(entry)
            record["live_rows"] = self._repo(name, entry).count()
            record["exists"] = os.path.exists(entry["db_path"])

            out.append(record)

        return out

    def get(self, name: str) -> dict:
        """One dataset (raises KeyError when unknown)."""

        entry = self._entry(name)
        name = entry["name"]

        record = dict(entry)
        record["live_rows"] = self._repo(name, entry).count()
        record["exists"] = os.path.exists(entry["db_path"])

        return record

    def entries(self, name: str, limit: int = 50, offset: int = 0) -> dict:
        """Paged key/value rows of one dataset (sorted by key)."""

        entry = self._entry(name)
        name = entry["name"]
        repo = self._repo(name, entry)

        keys = repo.keys()
        page = keys[offset:offset + limit]

        return {
            "name": name,
            "prefix": entry.get("prefix") or f"{name}:",
            "total": len(keys),
            "offset": offset,
            "limit": limit,
            "rows": [
                {"key": key, "value": repo.get(key)}
                for key in page
            ],
        }

    # --------------------------------------------------
    # CREATE / DELETE DATASET
    # --------------------------------------------------

    def create(self, name, key_column=None) -> dict:
        """
        Register a brand-new dataset: its own data/datasets/<name>.db,
        its own <name>: key prefix, its own search entity (once the
        first entry gives it a schema). Raises ValueError on a bad
        or duplicate name.
        """

        name = slugify(str(name or "").strip())

        if not name:
            raise ValueError("dataset name is required")

        if name in self.registry.entries():
            raise ValueError(f"dataset {name!r} already exists")

        # register() enforces reserved names/prefixes itself.
        entry = self.registry.register(name, key_column=key_column)

        # Materialize the isolated file now: the user asked for a
        # new space, it should exist even before the first entry.
        DatabaseRepository(db_path=entry["db_path"])

        return self.get(name)

    def delete_dataset(self, name: str) -> dict:
        """Remove a dataset everywhere: nodes, log, search caches,
        manifest and its SQLite file."""

        entry = self._entry(name)
        name = entry["name"]
        prefix = entry.get("prefix") or f"{name}:"

        # Flush nodes BEFORE unregistering - the manifest still has
        # to tell us which prefix to flush.
        flushed = self.distributed_cache.flush_prefix(prefix)
        dropped = self.replication.drop_prefix(prefix)
        purged = self.purge_search_caches(name)

        self.registry.unregister(name)

        return {
            "name": name,
            "prefix": prefix,
            "flushed": flushed,
            "dropped_log_entries": dropped,
            "purged": purged,
            "deleted": True,
        }

    # --------------------------------------------------
    # ENTRIES
    # --------------------------------------------------

    def add_entry(self, name: str, value) -> dict:
        """
        Add (or upsert, when the id already exists) one entry.

        Goes through the normal write-through path: database row
        first, then fan-out to primary + replicas, so cache nodes
        are populated exactly like any other key. Search result
        caches for the entity are purged afterwards.
        """

        if not isinstance(value, dict):
            raise ValueError("entry value must be a JSON object")

        entry = self._entry(name)
        name = entry["name"]
        repo = self._repo(name, entry)
        key = self._key_for(entry, value, repo)

        created = not repo.exists(key)

        self.cache_service.set(key, value)

        stats = self._sync_stats(name)
        purged = self.purge_search_caches(name)

        return {
            "name": name,
            "key": key,
            "created": created,
            "row_count": stats.get("row_count"),
            "schema": stats.get("schema"),
            "purged": purged,
        }

    def delete_entry(self, name: str, key: str) -> dict:
        """
        Delete one entry everywhere: the row is removed (DB), every
        node copy is invalidated (evict cascade), search caches for
        the entity are purged, stats re-synced.

        Raises KeyError when neither the row nor a cache copy
        exists.
        """

        entry = self._entry(name)
        name = entry["name"]
        prefix = entry.get("prefix") or f"{name}:"

        if not key.startswith(prefix):
            raise ValueError(
                f"key {key!r} does not belong to dataset {name!r} "
                f"(expected prefix {prefix!r})"
            )

        result = self.cache_service.delete(key)

        if not result["deleted"]:
            raise KeyError(key)

        stats = self._sync_stats(name)
        purged = self.purge_search_caches(name)

        return {
            "name": name,
            "key": key,
            "deleted": True,
            "nodes": result["nodes"],
            "row_count": stats.get("row_count"),
            "purged": purged,
        }

    # --------------------------------------------------
    # EXTERNAL MUTATION HOOK (router PUT / DELETE endpoints)
    # --------------------------------------------------

    def after_key_change(self, key: str) -> dict:
        """
        Called after a raw /cache PUT or /db DELETE touched a row
        that belongs to a dataset: re-sync stats and purge that
        entity's search caches. Returns {} for non-dataset keys
        (the overwhelmingly common case - no work done).
        """

        entry = self.registry.entry_for(key)

        if entry is None:
            return {}

        name = entry["name"]

        return {
            "dataset": name,
            "row_count": self._sync_stats(name).get("row_count"),
            "purged": self.purge_search_caches(name),
        }

    # --------------------------------------------------
    # SEARCH-CACHE PURGE
    # --------------------------------------------------

    def purge_search_caches(self, name: str) -> dict:
        """
        Drop every cached search/suggest result for one entity:

            q:<name>:...  cached result sets
            s:<name>:...  cached autocomplete

        In all three places: store rows, node copies, replication log.
        """

        purged = {}

        for kind in ("q", "s"):

            prefix = f"{kind}:{name}:"

            rows = self.database.delete_prefix(prefix)
            flushed = self.distributed_cache.flush_prefix(prefix)
            dropped = self.replication.drop_prefix(prefix)

            purged[prefix] = {
                "rows": rows,
                "nodes": flushed,
                "log": dropped,
            }

        return purged

    # --------------------------------------------------
    # INTERNALS
    # --------------------------------------------------

    def _entry(self, name: str) -> dict:
        """Manifest entry for a (possibly un-slugified) dataset name."""

        entry = self.registry.get(slugify(str(name or "")))

        if entry is None:
            raise KeyError(name)

        return entry

    def _repo(self, name: str, entry: dict):
        return self.database.repo_named(name) or self.database.system

    def _key_for(self, entry: dict, value: dict, repo) -> str:
        """Conflict-free key derivation (see module docstring)."""

        prefix = entry.get("prefix") or f"{entry['name']}:"

        key_column = entry.get("key_column")

        if key_column:

            if key_column not in value or value[key_column] in (None, ""):
                raise ValueError(
                    f"entry must include the key field {key_column!r} "
                    f"(declared when the dataset was created)"
                )

            return f"{prefix}{value[key_column]}"

        if "id" in value and value["id"] not in (None, ""):
            return f"{prefix}{value['id']}"

        return f"{prefix}{self._next_sequence(repo, prefix)}"

    @staticmethod
    def _next_sequence(repo, prefix: str) -> int:
        """One past the highest numeric suffix already in use."""

        highest = 0

        for key in repo.keys():

            if not key.startswith(prefix):
                continue

            suffix = key[len(prefix):]

            if suffix.isdigit():
                highest = max(highest, int(suffix))

        return highest + 1

    def _sample_rows(self, repo, limit: int = SCHEMA_SAMPLE) -> list:
        """First N dict-shaped rows, for schema detection."""

        rows = []

        for key in repo.keys(limit):

            value = repo.get(key)

            if isinstance(value, dict):
                rows.append(value)

        return rows

    def _sync_stats(self, name: str) -> dict:
        """
        Re-count rows and re-detect the schema after a mutation, so
        the Search tab's facets/columns track what was written.

        An empty dataset keeps its previous schema: a temporary
        zero-row state must not wipe the entity's columns.
        """

        entry = self.registry.get(name)

        if entry is None:
            return {}

        repo = self._repo(name, entry)

        fields = {"row_count": repo.count()}

        sample = self._sample_rows(repo)

        if sample:
            fields["schema"] = detect_schema(sample)

        # Persisted manifest entry (row_count + schema refreshed).
        return self.registry.update(name, **fields)
