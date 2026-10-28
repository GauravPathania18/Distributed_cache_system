import threading
import time

from concurrent.futures import ThreadPoolExecutor

import pytest

from services.single_flight import SingleFlight


def test_single_execution_collapses_concurrent_calls():

    single_flight = SingleFlight()
    calls = {"count": 0}
    lock = threading.Lock()

    def load():
        with lock:
            calls["count"] += 1
        time.sleep(0.1)
        return "expensive-value"

    with ThreadPoolExecutor(max_workers=5) as executor:

        results = list(
            executor.map(
                lambda _: single_flight.do("key-A", load),
                range(5)
            )
        )

    assert results == ["expensive-value"] * 5
    assert calls["count"] == 1
    assert single_flight.in_flight_keys() == []


def test_result_shared_with_all_followers():

    single_flight = SingleFlight()

    def load():
        return {"computed_by": "leader"}

    followers = [
        threading.Thread(
            target=lambda: results.append(single_flight.do("k", load))
        )
        for results in [[]]
        for _ in range(1)
    ]

    results = []

    def run():
        for i in range(4):
            with ThreadPoolExecutor(max_workers=4) as executor:
                results.append(
                    list(
                        executor.map(
                            lambda _: single_flight.do("k", load),
                            range(4)
                        )
                    )
                )

    run()

    for batch in results:
        for result in batch:
            assert result == {"computed_by": "leader"}


def test_exception_propagated_to_all_followers():

    single_flight = SingleFlight()

    def load():
        raise ValueError("leader exploded")

    with ThreadPoolExecutor(max_workers=4) as executor:

        futures = [
            executor.submit(single_flight.do, "boom", load)
            for _ in range(4)
        ]

        errors = [f.exception() for f in futures]

    assert all(isinstance(error, ValueError) for error in errors)
    assert all(error.args == ("leader exploded",) for error in errors)
    assert single_flight.in_flight_keys() == []


def test_different_keys_are_independent():

    single_flight = SingleFlight()

    calls = {"a": 0, "b": 0}

    def make_loader(key):
        def load():
            time.sleep(0.05)
            calls[key] += 1
            return key
        return load

    with ThreadPoolExecutor(max_workers=6) as executor:

        futures = [
            executor.submit(single_flight.do, "a", make_loader("a"))
            for _ in range(3)
        ] + [
            executor.submit(single_flight.do, "b", make_loader("b"))
            for _ in range(3)
        ]

        results = [f.result() for f in futures]

    assert results.count("a") == 3
    assert results.count("b") == 3
    assert calls["a"] == 1
    assert calls["b"] == 1


def test_event_bases_next_call_new_flight():

    single_flight = SingleFlight()
    calls = []

    def load():
        calls.append(1)
        return "once"

    assert single_flight.do("k", load) == "once"
    assert single_flight.do("k", load) == "once"

    assert calls == [1, 1]
    assert single_flight.in_flight_keys() == []