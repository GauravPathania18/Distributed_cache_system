from typing import Any, Dict, Optional

from database.exceptions import DatabaseUnavailableError
from services.circuit_breaker import CircuitBreaker
from services.single_flight import SingleFlight


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
    """

    def __init__(
        self,
        cache,
        database,
        single_flight: SingleFlight = None,
        circuit_breaker: CircuitBreaker = None
    ):

        self.cache = cache
        self.database = database

        self.single_flight = single_flight or SingleFlight()

        self.circuit_breaker = circuit_breaker or CircuitBreaker()

    # --------------------------------------------------
    # GET (cache-aside)
    # --------------------------------------------------

    def get(self, key: str) -> Optional[dict]:
        """Return the value or None (cache + database miss).

        Raises DatabaseUnavailableError when the database is down and the
        key was not already in the cache.
        """

        # 1. Fast path: distributed cache.
        value, node = self.cache.get(key)

        if value is not None:

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

            return None

        # 3. Populate the cache with the database result.
        self.cache.set(key, db_value)

        return {
            "value": db_value,
            "source": "database",
            "node": None
        }

    # --------------------------------------------------
    # SET (write-through)
    # --------------------------------------------------

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> dict:

        # Source of truth first (through the circuit breaker).
        self.circuit_breaker.call(self.database.set, key, value)

        # Then the fast copies.
        nodes = self.cache.set(key, value, ttl=ttl)

        return {
            "key": key,
            "stored_in_database": True,
            "nodes": nodes
        }

    # --------------------------------------------------
    # DELETE (write-invalidate)
    # --------------------------------------------------

    def delete(self, key: str) -> dict:

        database_deleted = self.circuit_breaker.call(self.database.delete, key)

        nodes = self.cache.delete(key)

        return {
            "key": key,
            "deleted": database_deleted or bool(nodes),
            "stored_in_database": database_deleted,
            "nodes": nodes
        }

    # --------------------------------------------------
    # INSPECTION
    # --------------------------------------------------

    def circuit_breaker_status(self) -> dict:
        """Expose circuit breaker state and stats for the status endpoint."""

        return self.circuit_breaker.get_stats()