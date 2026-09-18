import threading

from typing import Any, Callable, Dict


class Flight:
    """A single in-flight (or completed) execution for one key.

    The leading thread holds this object; follower threads wait on the
    shared event and then read back the leader's result or exception.
    """

    def __init__(self, key: str):

        self.key = key
        self.event = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None

    def set_result(self, result: Any) -> None:

        self.result = result
        self.event.set()

    def set_error(self, error: BaseException) -> None:

        self.error = error
        self.event.set()

    def get(self) -> Any:
        """Block until the leader finishes, then return result or re-raise."""

        self.event.wait()

        if self.error is not None:
            raise self.error

        return self.result


class SingleFlight:
    """Coalesces concurrent calls for the same key into a single execution.

    For a given key, the first caller becomes the *leader*: it runs the
    function once while every other caller becomes a *follower* that just
    waits on the shared Flight event and then reads the leader's result
    (or re-raises the leader's exception). This is the classic stampede
    protection primitive used inside large cache services.

    Implementation notes:
      - Followers never call the expensive function themselves.
      - The leader's exception (e.g. DatabaseUnavailableError) is stored
        and re-raised in every follower, so a failure is not re-tried by
        everyone at once.
      - Concurrency is per-key: different keys do not block each other.
      - This is process-local only (one router process). Cross-process
        stampede protection would require a distributed lock later.
    """

    def __init__(self):

        # Guards the _in_flight map.
        self._lock = threading.Lock()

        # key -> Flight for executions that are still running.
        self._in_flight: Dict[str, Flight] = {}

    # --------------------------------------------------
    # PUBLIC API
    # --------------------------------------------------

    def do(self, key: str, function: Callable[[], Any]) -> Any:

        with self._lock:

            flight = self._in_flight.get(key)

            if flight is None:

                flight = Flight(key)
                self._in_flight[key] = flight

                leader = True

            else:

                leader = False

        if not leader:

            # This thread is a follower: just wait for the leader.
            return flight.get()

        # ---- Leader path ----
        try:

            result = function()

            flight.set_result(result)

            return result

        except BaseException as error:

            flight.set_error(error)

            raise

        finally:

            # Wake followers before removing feels optional, but the Event
            # already guarantees they read the shared result. Drop the
            # flight from the map so the next request can start a new one.
            with self._lock:

                self._in_flight.pop(key, None)

    # --------------------------------------------------
    # INSPECTION / TESTING
    # --------------------------------------------------

    def in_flight_keys(self) -> list:

        with self._lock:
            return list(self._in_flight.keys())