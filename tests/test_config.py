import pytest

from router.config import DEFAULT_NODES, nodes_from_env


# =========================================================
# NODE LIST (shared by the router and the launcher)
# =========================================================

def test_default_nodes_are_ipv4_loopback(monkeypatch):

    monkeypatch.delenv("CACHE_NODES", raising=False)

    assert nodes_from_env() == [
        "http://127.0.0.1:8001",
        "http://127.0.0.1:8002",
        "http://127.0.0.1:8003",
    ]


def test_default_nodes_are_all_normalised():

    assert all(node.startswith("http://127.0.0.1:") for node in DEFAULT_NODES)


def test_cache_nodes_accepts_full_urls_and_bare_ports(monkeypatch):

    monkeypatch.setenv("CACHE_NODES", "http://localhost:9001, 9002")

    # localhost is normalised to 127.0.0.1 to avoid the ~2s IPv6
    # fallback penalty per connection on Windows.
    assert nodes_from_env() == [
        "http://127.0.0.1:9001",
        "http://127.0.0.1:9002",
    ]


def test_cache_nodes_keeps_remote_hosts_untouched(monkeypatch):

    monkeypatch.setenv("CACHE_NODES", "http://cache-1:8001/")

    assert nodes_from_env() == ["http://cache-1:8001"]


def test_cache_nodes_skips_blank_entries(monkeypatch):

    monkeypatch.setenv("CACHE_NODES", "8001,,8002,  ")

    assert nodes_from_env() == [
        "http://127.0.0.1:8001",
        "http://127.0.0.1:8002",
    ]


def test_empty_cache_nodes_falls_back_to_defaults(monkeypatch):

    monkeypatch.setenv("CACHE_NODES", "")

    assert nodes_from_env() == list(DEFAULT_NODES)
