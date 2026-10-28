"""
Multi-dataset key/value store - one interface, many SQLite files.

The system keeps physical separation between datasets:

    data/cache.db               system repository - search-cache
                                entries (q:/s: prefixes), manual
                                scratch keys, anything unmatched
    data/datasets/<name>.db     one file per registered dataset

MultiDatasetRepository exposes the exact DatabaseRepository
interface (get/set/delete/exists/count/keys/iter_keys/query/
set_many/clear) and routes each key by its longest matching
dataset prefix; unmatched keys fall through to the system store.
Every layer above (cache service, warm-up, replication, router
endpoints, the run.py launcher) therefore works unchanged, but
searches and SQL
console sessions can still target one physical file via
repo_for() / repo_named() / repos_matching().
"""

import heapq
import itertools

from .registry import DatasetRegistry
from .repository import DatabaseRepository


class MultiDatasetRepository:

    def __init__(self, db_path: str = "data/cache.db", timeout: int = 5,
                 registry: DatasetRegistry = None):

        self.db_path = db_path
        self.timeout = timeout

        self.registry = (
            registry if registry is not None
            else DatasetRegistry(db_path=db_path)
        )

        self.system = DatabaseRepository(db_path=db_path, timeout=timeout)

        self._repos = {}
        self._prefixes = {}
        self._revision = -1

    # ---- registry sync -------------------------------------

    def _sync(self):
        """Create/drop per-dataset repositories after manifest changes."""

        revision = self.registry.revision()

        if revision == self._revision:
            return

        entries = self.registry.entries()

        for name in [name for name in self._repos if name not in entries]:
            del self._repos[name]
            del self._prefixes[name]

        for name, entry in entries.items():

            if name not in self._repos:
                self._repos[name] = DatabaseRepository(
                    db_path=entry["db_path"], timeout=self.timeout
                )

            self._prefixes[name] = entry.get("prefix") or ""

        self._revision = revision

    def _repo_for_key(self, key: str) -> DatabaseRepository:

        self._sync()

        best = None
        best_len = 0

        for name, prefix in self._prefixes.items():

            if prefix and key.startswith(prefix) and len(prefix) > best_len:
                best = self._repos[name]
                best_len = len(prefix)

        return best or self.system

    # ---- per-dataset access --------------------------------

    def repo_for(self, prefix: str) -> DatabaseRepository:
        """Repository owning every key under `prefix` (search SQL)."""

        self._sync()

        best = None
        best_len = 0

        for name, entry_prefix in self._prefixes.items():

            if (entry_prefix and prefix.startswith(entry_prefix)
                    and len(entry_prefix) > best_len):
                best = self._repos[name]
                best_len = len(entry_prefix)

        return best or self.system

    def repos_matching(self, prefix: str = "") -> list:
        """Repositories that can hold keys matching `prefix`.

        Empty prefix -> every repository (system included). A
        prefix that partially or fully covers dataset prefixes
        matches those datasets; nothing matching -> system.
        """

        self._sync()

        if not prefix:
            return [self.system] + list(self._repos.values())

        matched = [
            self._repos[name]
            for name, entry_prefix in self._prefixes.items()
            if entry_prefix
            and (entry_prefix.startswith(prefix)
                 or prefix.startswith(entry_prefix))
        ]

        return matched or [self.system]

    def repo_named(self, name):
        """Repository for one dataset name, or the system store.

        `name` of "" or "system" selects the system repository;
        unknown dataset names return None so callers can report.
        """

        self._sync()

        if not name or name == "system":
            return self.system

        return self._repos.get(name)

    def repos(self):
        """(label, repository) pairs: system first, then by name."""

        self._sync()

        yield "system", self.system

        for name in sorted(self._repos):
            yield name, self._repos[name]

    # ---- DatabaseRepository interface ----------------------

    def get(self, key):
        return self._repo_for_key(key).get(key)

    def set(self, key, value) -> None:
        self._repo_for_key(key).set(key, value)

    def delete(self, key) -> bool:
        return self._repo_for_key(key).delete(key)

    def delete_prefix(self, prefix: str) -> int:
        """Delete every key under `prefix` in the store that owns it."""
        return self.repo_for(prefix).delete_prefix(prefix)

    def exists(self, key) -> bool:
        return self._repo_for_key(key).exists(key)

    def set_many(self, items) -> int:
        """Batch upsert grouped by owning repository."""

        self._sync()

        groups = {}

        for key, value in items:
            groups.setdefault(self._repo_for_key(key), []).append((key, value))

        return sum(
            repo.set_many(batch) for repo, batch in groups.items()
        )

    def count(self) -> int:

        self._sync()

        return self.system.count() + sum(
            repo.count() for repo in self._repos.values()
        )

    def keys(self, limit=None) -> list:

        self._sync()

        streams = [self.system.keys(limit)] + [
            repo.keys(limit) for repo in self._repos.values()
        ]

        merged = heapq.merge(*streams)

        if limit is None:
            return list(merged)

        return list(itertools.islice(merged, limit))

    def iter_keys(self, batch_size: int = 500):

        self._sync()

        streams = [self.system.iter_keys(batch_size)] + [
            repo.iter_keys(batch_size) for repo in self._repos.values()
        ]

        yield from heapq.merge(*streams)

    def clear(self) -> None:

        self._sync()

        self.system.clear()

        for repo in self._repos.values():
            repo.clear()

    def query(self, sql: str, params=(), max_rows: int = 50) -> dict:
        """Read-only SQL with best-effort repository inference.

        A query mentioning a registered prefix in its parameters or
        its text runs against that dataset's file; anything else
        runs against the system store. Callers that know their
        target should use repo_for() / repo_named() instead.
        """

        self._sync()

        repo = self.system

        if self._prefixes:

            best_len = 0

            for value in list(params) + [sql]:

                if not isinstance(value, str):
                    continue

                for name, prefix in self._prefixes.items():

                    if prefix and prefix in value and len(prefix) > best_len:
                        repo = self._repos[name]
                        best_len = len(prefix)

        return repo.query(sql, params, max_rows)
