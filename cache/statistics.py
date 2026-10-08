import threading
import time

from collections import Counter


DEFAULT_MAX_TRACKED_KEYS = 100_000
DEFAULT_WINDOW_SECONDS = 300
DEFAULT_TOP_KEYS_LIMIT = 10


class CacheStatistics:
    """
    Layer 6.2 - node-local cache statistics + hot-key tracking.

    Every cache node owns one instance. It counts operations and
    tracks per-key access frequency entirely in memory - nothing
    is ever written to the database just to measure cache usage.

        GET     -> record_hit(key) / record_miss(key)
        SET     -> record_set()
        DELETE  -> record_delete()
        eviction-> record_eviction()   (via LRUCache on_eviction hook)

    Two safety properties:

        Bounded key tracking:
            key_access never holds more than `max_tracked_keys`
            entries. Once full, brand-new keys are not tracked
            (counted in `untracked`); known keys keep counting.

        Time windows:
            counters describe the CURRENT window only. A rotation
            moves them into `last_window` and resets everything,
            so hot-key lists reflect recent workload instead of
            the lifetime of the process. Rotation runs automatically
            every `window_seconds` when auto_rotate=True / start().

    thread-safe: every public method takes one lock.
    """

    def __init__(
        self,
        max_tracked_keys=DEFAULT_MAX_TRACKED_KEYS,
        window_seconds=DEFAULT_WINDOW_SECONDS,
        auto_rotate=False
    ):

        if max_tracked_keys <= 0:
            raise ValueError("max_tracked_keys must be greater than 0")

        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than 0")

        self.max_tracked_keys = max_tracked_keys
        self.window_seconds = window_seconds

        self.lock = threading.Lock()

        # Current-window operation counters.
        self.hits = 0
        self.misses = 0
        self.sets = 0
        self.deletes = 0
        self.evictions = 0

        # Accesses to keys dropped by the tracking bound.
        self.untracked = 0

        # key -> access count (bounded).
        self.key_access = Counter()

        self.window_started_at = time.time()

        # Snapshot of the previous window (None until first rotation).
        self.last_window = None

        # Background rotation.
        self._stop_event = threading.Event()
        self._thread_lock = threading.Lock()
        self._thread = None

        if auto_rotate:
            self.start()

    # -----------------------------------------
    # EVENTS
    # -----------------------------------------

    def record_hit(self, key: str) -> None:
        """One cache HIT for `key`."""

        with self.lock:
            self.hits += 1
            self._note_access(key)

    def record_miss(self, key: str) -> None:
        """One cache MISS for `key` (still an access - it was requested)."""

        with self.lock:
            self.misses += 1
            self._note_access(key)

    def record_set(self) -> None:
        with self.lock:
            self.sets += 1

    def record_delete(self) -> None:
        with self.lock:
            self.deletes += 1

    def record_eviction(self, key: str = None) -> None:
        """
        One capacity eviction.

        `key` is supplied by the LRUCache on_eviction hook; it is
        accepted (but not tracked) so the hook signature stays
        useful for future evicted-key analysis.
        """

        with self.lock:
            self.evictions += 1

    def _note_access(self, key: str) -> None:
        """
        Bump one key's counter, respecting the tracking bound.

        Caller must hold self.lock.
        """

        if key in self.key_access:
            self.key_access[key] += 1

        elif len(self.key_access) < self.max_tracked_keys:
            self.key_access[key] += 1

        else:
            self.untracked += 1

    # -----------------------------------------
    # QUERY
    # -----------------------------------------

    def snapshot(self, include_keys=False) -> dict:
        """
        Current-window counters.

        hit_rate = hits / total_gets  (0.0 - 1.0, 0 when no GETs).

        include_keys=True adds the raw key_access dict - it can be
        large, so it is off by default.
        """

        with self.lock:

            total_gets = self.hits + self.misses

            hit_rate = (
                self.hits / total_gets
                if total_gets > 0
                else 0.0
            )

            result = {
                "hits": self.hits,
                "misses": self.misses,
                "sets": self.sets,
                "deletes": self.deletes,
                "evictions": self.evictions,
                "total_gets": total_gets,
                "hit_rate": hit_rate,
                "untracked": self.untracked,
                "tracked_keys": len(self.key_access),
                "max_tracked_keys": self.max_tracked_keys,
                "window_seconds": self.window_seconds,
                "window_started_at": self.window_started_at,
                "last_window": self.last_window
            }

            if include_keys:
                result["key_access"] = dict(self.key_access)

            return result

    def top_keys(self, limit=DEFAULT_TOP_KEYS_LIMIT) -> list:
        """The `limit` most accessed keys of the current window."""

        with self.lock:
            return self.key_access.most_common(limit)

    # -----------------------------------------
    # WINDOW ROTATION
    # -----------------------------------------

    def rotate(self, top_keys_limit=DEFAULT_TOP_KEYS_LIMIT) -> dict:
        """
        Close the current window and start a fresh one.

        The finished window is stored in `last_window` (counters +
        its own top keys) and every counter, including key_access,
        is reset.
        """

        with self.lock:

            total_gets = self.hits + self.misses

            hit_rate = (
                self.hits / total_gets
                if total_gets > 0
                else 0.0
            )

            now = time.time()

            self.last_window = {
                "hits": self.hits,
                "misses": self.misses,
                "sets": self.sets,
                "deletes": self.deletes,
                "evictions": self.evictions,
                "total_gets": total_gets,
                "hit_rate": hit_rate,
                "untracked": self.untracked,
                "tracked_keys": len(self.key_access),
                "window_started_at": self.window_started_at,
                "window_ended_at": now,
                "top_keys": self.key_access.most_common(top_keys_limit)
            }

            self.hits = 0
            self.misses = 0
            self.sets = 0
            self.deletes = 0
            self.evictions = 0
            self.untracked = 0
            self.key_access.clear()
            self.window_started_at = now

            return self.last_window

    # -----------------------------------------
    # AUTOMATIC ROTATION
    # -----------------------------------------

    def start(self) -> None:
        """Start the background window-rotation thread (idempotent)."""

        with self._thread_lock:

            if self._thread is not None and self._thread.is_alive():
                return

            self._stop_event.clear()

            self._thread = threading.Thread(
                target=self._rotation_loop,
                name="cache-statistics-rotation",
                daemon=True
            )

            self._thread.start()

    def stop(self) -> None:
        """Stop the rotation thread and wait for it to exit."""

        with self._thread_lock:

            self._stop_event.set()

            if self._thread is not None:
                self._thread.join(timeout=5)
                self._thread = None

    def _rotation_loop(self) -> None:

        while not self._stop_event.wait(self.window_seconds):
            self.rotate()
