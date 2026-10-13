import threading

from collections import Counter


DEFAULT_MAX_KEYS = 10_000
DEFAULT_TOP_LIMIT = 100


class HotKeyManager:
    """
    Layer 6.3 - cluster-wide hot-key ranking.

    The router sees every key the application touches, so it records
    them here (GET/SET/DELETE through CacheService). Because the
    ranking lives in the router process it survives cache-node
    restarts - exactly when warm-up needs it.

    Node-local Layer 6.2 statistics are folded in on demand (merge())
    so traffic that bypassed the router is covered too.

    Bounded two ways:

        record():
            per-request hot path - once `max_keys` distinct keys are
            known, brand-new keys are counted in `untracked` while
            known keys keep counting (the Layer 6.2 policy).

        merge():
            batch import of node rankings - the union is trimmed back
            to `max_keys`, keeping the highest counts.

    Merged counts are additive: an access observed by both the router
    and a node counts twice. Only the relative order matters for
    warm-up, so the overcount is harmless.

    thread-safe: every public method takes one lock.
    """

    def __init__(self, max_keys=DEFAULT_MAX_KEYS):

        if max_keys <= 0:
            raise ValueError("max_keys must be greater than 0")

        self.max_keys = max_keys

        self.lock = threading.Lock()

        # key -> access count (bounded).
        self.access_counts = Counter()

        # Admissions refused by the tracking bound.
        self.untracked = 0

    # -----------------------------------------
    # EVENTS
    # -----------------------------------------

    def record(self, key: str) -> None:
        """One access to `key` seen by the router."""

        with self.lock:

            if key in self.access_counts:
                self.access_counts[key] += 1

            elif len(self.access_counts) < self.max_keys:
                self.access_counts[key] += 1

            else:
                self.untracked += 1

    def merge(self, node_counts) -> int:
        """
        Fold a node's Layer 6.2 key ranking into the cluster view.

        Accepts a Counter, a dict, or the [[key, count], ...] shape
        returned by a node's /stats/top-keys endpoint. Returns the
        number of entries folded in.
        """

        if not node_counts:
            return 0

        if isinstance(node_counts, (Counter, dict)):
            items = list(node_counts.items())
        else:
            items = list(node_counts)

        folded = 0

        with self.lock:

            for entry in items:

                if not entry:
                    continue

                key, count = entry[0], entry[1]

                if not key:
                    continue

                self.access_counts[str(key)] += int(count)
                folded += 1

            # Trim the union back to the bound, keeping the hottest.
            if len(self.access_counts) > self.max_keys:

                keep = self.access_counts.most_common(self.max_keys)
                self.access_counts = Counter(dict(keep))

            return folded

    # -----------------------------------------
    # QUERY
    # -----------------------------------------

    def top_keys(self, limit=DEFAULT_TOP_LIMIT) -> list:
        """The `limit` most accessed keys across the cluster."""

        with self.lock:
            return self.access_counts.most_common(limit)

    def stats(self) -> dict:

        with self.lock:
            return {
                "tracked_keys": len(self.access_counts),
                "max_keys": self.max_keys,
                "untracked": self.untracked
            }

    def clear(self) -> None:

        with self.lock:
            self.access_counts.clear()
            self.untracked = 0
