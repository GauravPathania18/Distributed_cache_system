import threading
import time

from typing import Any, Callable

from database.exceptions import DatabaseUnavailableError


class CircuitBreaker:
    """
    Prevents the service from hammering a database that keeps failing.

    Three states:

        CLOSED   -> normal operation, every call passes through and is
                    counted as a success or a failure.
        OPEN     -> after `failure_threshold` consecutive failures the
                    circuit opens: calls are rejected immediately without
                    touching the database (fast fail).
        OPEN     -> after `recovery_timeout` seconds the circuit moves to
                    HALF-OPEN.
        HALF_OPEN -> a single probe call is allowed through. If it
                    succeeds the circuit closes again; if it fails the
                    circuit re-opens.

    The breaker sits between the cache-aside service and the database,
    so the collapse of the storage layer never reaches the HTTP layer
    as a wall of slow, hanging requests.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        success_threshold: int = 1
    ):

        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")

        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be > 0")

        if success_threshold < 1:
            raise ValueError("success_threshold must be >= 1")

        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self._state = self.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None

        self._lock = threading.Lock()

    # --------------------------------------------------
    # PUBLIC CALL INTERFACE
    # --------------------------------------------------

    def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Run `func(*args, **kwargs)` through the breaker."""

        if self._should_reject():

            raise DatabaseUnavailableError(
                "database unavailable: circuit breaker is open"
            )

        try:

            result = func(*args, **kwargs)

            self._record_success()

            return result

        except DatabaseUnavailableError:
            self._record_failure()
            raise
        except Exception as error:
            self._record_failure()
            raise DatabaseUnavailableError(
                f"database unavailable: unexpected error {error!r}"
            )

    # --------------------------------------------------
    # STATE TRANSITIONS
    # --------------------------------------------------

    def _should_reject(self) -> bool:

        with self._lock:

            if self._state == self.OPEN:

                if self._last_failure_time is not None:

                    elapsed = time.monotonic() - self._last_failure_time

                    if elapsed >= self.recovery_timeout:

                        # Probe time: allow exactly one call.
                        self._state = self.HALF_OPEN
                        self._success_count = 0

                        return False

                return True

            return False

    def _record_success(self) -> None:

        with self._lock:

            if self._state == self.HALF_OPEN:

                self._success_count += 1

                if self._success_count >= self.success_threshold:

                    self._state = self.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._last_failure_time = None

            else:

                # CLOSED: clear the failure streak.
                self._failure_count = 0

    def _record_failure(self) -> None:

        with self._lock:

            if self._state == self.HALF_OPEN:

                # The probe failed: straight back to OPEN.
                self._state = self.OPEN
                self._failure_count = self.failure_threshold
                self._last_failure_time = time.monotonic()

            else:

                self._failure_count += 1
                self._last_failure_time = time.monotonic()

                if self._failure_count >= self.failure_threshold:

                    self._state = self.OPEN
                    self._success_count = 0

    # --------------------------------------------------
    # INSPECTION / TESTING
    # --------------------------------------------------

    def get_state(self) -> str:
        """Current state as 'closed' | 'open' | 'half_open'."""

        with self._lock:
            return self._state

    def get_stats(self) -> dict:

        with self._lock:

            return {
                "state": self._state,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "success_threshold": self.success_threshold,
                "last_failure_time": self._last_failure_time
            }

    def reset(self) -> None:

        with self._lock:

            self._state = self.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None