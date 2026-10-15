import threading
import time


class RequestTelemetry:
    """
    Layer 8 - router-side read telemetry.

    The router answers every GET with a `source` of `cache` or
    `database`, but before Layer 8 it kept no aggregate of that, so
    "how much load did the cache take off the database?" had no
    number to point at. This class is that number.

    It counts REAL OPERATIONS, not HTTP requests:

        a cache hit              -> one `gets_cache`
        an actual database query -> one `gets_database`
        a key missing everywhere -> one `gets_miss`

    Because concurrent misses for the same key are coalesced by
    SingleFlight into a single database query, N followers waiting on
    one leader still record exactly ONE database read. That is the
    honest measure of database load - counting per-request would
    overstate it.

    `db_reads_avoided / (db_reads_avoided + db_reads)` is the cache
    quality ratio this project exists to improve.
    """

    def __init__(self):

        self._lock = threading.Lock()

        self._counters = {
            "gets_cache": 0,
            "gets_database": 0,
            "gets_miss": 0,
            "search_cache": 0,
            "search_database": 0,
            "sets": 0,
            "deletes": 0,
        }

        self.started_at = time.time()

    # --------------------------------------------------
    # RECORDING
    # --------------------------------------------------

    def record_get(self, source: str) -> None:
        """Record one point-key read: 'cache', 'database' or 'miss'."""

        with self._lock:

            if source == "cache":
                self._counters["gets_cache"] += 1

            elif source == "database":
                self._counters["gets_database"] += 1

            else:
                self._counters["gets_miss"] += 1

    def record_search(self, hit: bool) -> None:
        """Record one search: cached result set vs a real SQL run."""

        with self._lock:

            if hit:
                self._counters["search_cache"] += 1
            else:
                self._counters["search_database"] += 1

    def record_set(self) -> None:

        with self._lock:
            self._counters["sets"] += 1

    def record_delete(self) -> None:

        with self._lock:
            self._counters["deletes"] += 1

    def reset(self) -> None:
        """Zero the counters (demo / test helper)."""

        with self._lock:

            for name in self._counters:
                self._counters[name] = 0

            self.started_at = time.time()

    # --------------------------------------------------
    # REPORTING
    # --------------------------------------------------

    def stats(self) -> dict:
        """
        Raw counters plus the derived cache-quality ratios.

        `hit_rate_pct` and `db_load_reduction_pct` are the same
        ratio; the second name exists because it is the claim this
        project makes.
        """

        with self._lock:

            counters = dict(self._counters)

            started_at = self.started_at

        cache_reads = counters["gets_cache"] + counters["search_cache"]

        db_reads = (
            counters["gets_database"] + counters["search_database"]
        )

        served = cache_reads + db_reads

        if served:

            hit_rate_pct = round(cache_reads / served * 100, 2)

        else:

            hit_rate_pct = 0.0

        return {
            **counters,
            "cache_reads": cache_reads,
            "db_reads": db_reads,
            "db_reads_avoided": cache_reads,
            "served_reads": served,
            "hit_rate_pct": hit_rate_pct,
            "db_load_reduction_pct": hit_rate_pct,
            "uptime_s": round(time.time() - started_at, 3),
        }
