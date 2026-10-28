import time

import pytest

from database.exceptions import DatabaseUnavailableError
from services.circuit_breaker import CircuitBreaker


# =========================================================
# CLOSED STATE
# =========================================================

def test_closed_allows_requests():

    breaker = CircuitBreaker()
    calls = []

    def db_get(key):
        calls.append(key)
        return f"value-{key}"

    assert breaker.call(db_get, "a") == "value-a"
    assert breaker.get_state() == "closed"
    assert calls == ["a"]


def test_closed_records_failures_without_opening():

    breaker = CircuitBreaker(failure_threshold=3)

    def failing():
        raise DatabaseUnavailableError("down")

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    assert breaker.get_state() == "closed"
    assert breaker.get_stats()["failure_count"] == 2


def test_closed_trips_after_threshold():

    breaker = CircuitBreaker(failure_threshold=3)

    def failing():
        raise DatabaseUnavailableError("down")

    for _ in range(3):

        with pytest.raises(DatabaseUnavailableError):
            breaker.call(failing)

    assert breaker.get_state() == "open"


def test_closed_resets_failure_count_on_success():

    breaker = CircuitBreaker(failure_threshold=3)

    def failing():
        raise DatabaseUnavailableError("down")

    def ok():
        return 1

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    breaker.call(ok)

    assert breaker.get_state() == "closed"
    assert breaker.get_stats()["failure_count"] == 0


# =========================================================
# OPEN STATE
# =========================================================

def test_open_rejects_immediately_without_calling_function():

    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
    calls = []

    def failing():
        calls.append(1)
        raise DatabaseUnavailableError("down")

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    assert breaker.get_state() == "open"

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    # The function must never be invoked while the circuit is open.
    assert calls == [1]


# =========================================================
# HALF-OPEN STATE
# =========================================================

def test_open_transitions_to_half_open_after_timeout():

    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

    def failing():
        raise DatabaseUnavailableError("down")

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    assert breaker.get_state() == "open"

    time.sleep(0.2)

    # The next call should transition to half_open and probe.
    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    assert breaker.get_state() == "open"


def test_half_open_closes_on_success():

    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)

    def failing():
        raise DatabaseUnavailableError("down")

    def ok():
        return "recovered"

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    assert breaker.get_state() == "open"

    time.sleep(0.2)

    # Probe passes.
    breaker.call(ok)

    assert breaker.get_state() == "closed"


def test_half_open_reopens_on_failure():

    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1, success_threshold=2)

    def failing():
        raise DatabaseUnavailableError("down")

    def ok():
        return "ok"

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    assert breaker.get_state() == "open"

    time.sleep(0.2)

    # Probe succeeds -> half_open, success streak starts.
    breaker.call(ok)
    assert breaker.get_state() == "half_open"

    # Probe fails again -> back to open.
    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    assert breaker.get_state() == "open"


# =========================================================
# NON-DATABASE EXCEPTIONS
# =========================================================

def test_unexpected_exception_treated_as_failure():

    breaker = CircuitBreaker(failure_threshold=1)

    def weird():
        raise ValueError("bogus")

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(weird)

    assert breaker.get_state() == "open"


# =========================================================
# STATS / RESET
# =========================================================

def test_stats_report_state_and_counters():

    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=15, success_threshold=1)

    def failing():
        raise DatabaseUnavailableError("down")

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    stats = breaker.get_stats()

    assert stats["state"] == "closed"
    assert stats["failure_count"] == 1
    assert stats["success_count"] == 0
    assert stats["failure_threshold"] == 3
    assert stats["recovery_timeout"] == 15
    assert stats["success_threshold"] == 1
    assert stats["last_failure_time"] is not None


def test_reset_returns_to_closed():

    breaker = CircuitBreaker(failure_threshold=1)

    def failing():
        raise DatabaseUnavailableError("down")

    with pytest.raises(DatabaseUnavailableError):
        breaker.call(failing)

    assert breaker.get_state() == "open"

    breaker.reset()

    assert breaker.get_state() == "closed"
    assert breaker.get_stats()["failure_count"] == 0