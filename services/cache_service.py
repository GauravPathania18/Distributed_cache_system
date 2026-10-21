from typing import Any, Dict, Optional

from cache.hot_keys import HotKeyManager
from database.exceptions import DatabaseUnavailableError
from services.circuit_breaker import CircuitBreaker
from services.single_flight import SingleFlight
from services.telemetry import RequestTelemetry


class CacheService:
    """
    Layer 5 - Cache-Aside orchestrator.

    The cache nodes and the database do not know about each other.
    This service owns the policy that decides when to read from the
    cache, when to fall back to the database, and when to invalidate.

        GET  -> cache HIT? return
             -> MISS -> database -> populate cache -> return

        SET  -> database (source of truth) -> cache (fast copy)

        DELETE -> database -> invalidate cache

    It also performs single-flight loading so that concurrent misses
    for the same key only hit the database once (stampede protection).

    Layer 5.7: every database call goes through a CircuitBreaker. When
    the database is unavailable the service raises DatabaseUnavailableError
    (or returns a "database_unavailable" result for GET), so the HTTP layer
    can answer 503 instead of handing back an unhandled 500.

    Layer 6.3: every key the application touches is recorded into the
    router-side HotKeyManager (one Counter bump per request, never the
    database) so warm-up knows what is hot even after nodes restart.
    """

    def __init__(
        self,
        cache,
        database,
        single_flight: SingleFlight = None,
        circuit_breaker: CircuitBreaker = None,
        hot_keys: HotKeyManager = None,
        replication=None,
        telemetry: RequestTelemetry = None
    ):

        self.cache = cache
        self.database = database

        self.single_flight = single_flight or SingleFlight()

        self.circuit_breaker = circuit_breaker or CircuitBreaker()

        # Layer 6.3 cluster-wide hot-key ranking. Auto-created when
        # not injected so existing constructors keep working.
        self.hot_keys = hot_keys if hot_keys is not None else HotKeyManager()

        # Layer 7.3 - router-side replication coordinator. Optional
        # so existing constructors/tests keep working without it.
        self.replication = replication

        # Layer 8 - cache-vs-database read accounting. Optional for
        # the same reason.
        self.telemetry = (
            telemetry if telemetry is not None else RequestTelemetry()
        )

    # --------------------------------------------------
    # GET (cache-aside)
    # --------------------------------------------------

    def get(self, key: str) -> Optional[dict]:
        """Return the value or None (cache + database miss).

        Raises DatabaseUnavailableError when the database is down and the
        key was not already in the cache.
        """

        self.hot_keys.record(key)

        # 1. Fast path: distributed cache.
        value, node = self.cache.get(key)

        if value is not None:

            self.telemetry.record_get("cache")

            return {
                "value": value,
                "source": "cache",
                "node": node
            }

        # 2. Slow path: single-flight + circuit breaker.
        return self.single_flight.do(key, lambda: self._load_from_database(key))

    # --------------------------------------------------
    # INTERNAL: DB LOAD THROUGH BREAKER
    # --------------------------------------------------

    def _load_from_database(self, key: str) -> Optional[dict]:

        # Double-check: another thread may have populated the cache
        # while we were waiting for the leader to finish.
        value, node = self.cache.get(key)

        if value is not None:

            self.telemetry.record_get("cache")

            return {
                "value": value,
                "source": "cache",
                "node": node
            }

        try:

            db_value = self.circuit_breaker.call(self.database.get, key)

        except DatabaseUnavailableError:

            raise

        if db_value is None:

            self.telemetry.record_get("miss")

            return None

        # 3. Populate the cache with the database result.
        self.cache.set(key, db_value)

        self.telemetry.record_get("database")

        return {
            "value": db_value,
            "source": "database",
            "node": None
        }

    # --------------------------------------------------
    # SET (write-through)
    # --------------------------------------------------

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> dict:

        self.hot_keys.record(key)

        # Layer 7.3 - allocate a cluster-wide sequence and record the
        # mutation before fanning out, so a crash mid-write still
        # leaves the log describing what was intended.
        sequence = None

        if self.replication is not None:
            sequence = self.replication.next_sequence()

        # Source of truth first (through the circuit breaker).
        self.circuit_breaker.call(self.database.set, key, value)

        # Then the fast copies, carrying the sequence.
        nodes = self.cache.set(key, value, ttl=ttl, sequence=sequence)

        if self.replication is not None:

            self.replication.record(key, "set", value, ttl, sequence)

            for node in nodes:
                self.replication.ack(node, sequence)

        self.telemetry.record_set()

        return {
            "key": key,
            "stored_in_database": True,
            "nodes": nodes
        }

    # --------------------------------------------------
    # DELETE (write-invalidate)
    # --------------------------------------------------

    def delete(self, key: str) -> dict:

        self.hot_keys.record(key)

        sequence = None

        if self.replication is not None:
            sequence = self.replication.next_sequence()

        database_deleted = self.circuit_breaker.call(self.database.delete, key)

        nodes = self.cache.delete(key, sequence=sequence)

        if self.replication is not None:

            self.replication.record(key, "delete", None, None, sequence)

            for node in nodes:
                self.replication.ack(node, sequence)

        self.telemetry.record_delete()

        return {
            "key": key,
            "deleted": database_deleted or bool(nodes),
            "stored_in_database": database_deleted,
            "nodes": nodes
        }

    # --------------------------------------------------
    # EVICT (Layer 9 - cache-only invalidation)
    # --------------------------------------------------

    def evict(self, key: str) -> dict:
        """
        Remove `key` from every cache node that holds it - the
        database row is deliberately kept.

        The other half of the deletion split: evict = "make the
        next read go to the database (and repopulate)", delete =
        "remove the row everywhere".  No replication log entry is
        written: the row itself did not change, so a replay of an
        older set would only restore a still-correct copy.
        """

        self.hot_keys.record(key)

        nodes = self.cache.delete(key)

        self.telemetry.record_delete()

        return {
            "key": key,
            "evicted": bool(nodes),
            "nodes": nodes
        }

    # --------------------------------------------------
    # INSPECTION
    # --------------------------------------------------

    def circuit_breaker_status(self) -> dict:
        """Expose circuit breaker state and stats for the status endpoint."""

        return self.circuit_breaker.get_stats()