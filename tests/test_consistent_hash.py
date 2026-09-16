from cluster.consistent_hash import ConsistentHashRing


def test_empty_ring_returns_none():

    ring = ConsistentHashRing(virtual_nodes=25)

    assert ring.get_node("user:101") is None


def test_single_node_owns_everything():

    ring = ConsistentHashRing(virtual_nodes=25)

    ring.add_node("node-A")

    for key in ["user:101", "user:102", "product:1", "session:xyz"]:
        assert ring.get_node(key) == "node-A"


def test_same_key_same_node():

    ring = ConsistentHashRing(virtual_nodes=100)

    ring.add_node("node-1")
    ring.add_node("node-2")

    assert ring.get_node("user:101") == ring.get_node("user:101")


def test_all_keys_resolve_to_some_node():

    ring = ConsistentHashRing(virtual_nodes=100)

    ring.add_node("node-1")
    ring.add_node("node-2")
    ring.add_node("node-3")

    for i in range(1000):
        key = f"user:{i}"
        assert ring.get_node(key) in {"node-1", "node-2", "node-3"}


def test_remove_node():

    ring = ConsistentHashRing(virtual_nodes=25)

    ring.add_node("node-1")
    ring.add_node("node-2")

    ring.remove_node("node-2")

    for i in range(200):
        assert ring.get_node(f"key:{i}") == "node-1"


def test_add_node_moves_only_a_fraction_of_keys():

    ring = ConsistentHashRing(virtual_nodes=100)

    ring.add_node("node-1")
    ring.add_node("node-2")
    ring.add_node("node-3")

    keys = [f"user:{i}" for i in range(2000)]

    before = {key: ring.get_node(key) for key in keys}

    # Add a fourth node.
    ring.add_node("node-4")

    after = {key: ring.get_node(key) for key in keys}

    moved = sum(1 for key in keys if before[key] != after[key])
    moved_fraction = moved / len(keys)

    # Adding one node to a four-node cluster should move about 1/4
    # of keys, definitely far less than all of them (modulo hashing).
    assert moved_fraction < 0.5


def test_load_balance_is_reasonable():

    ring = ConsistentHashRing(virtual_nodes=200)

    nodes = ["node-1", "node-2", "node-3"]

    for node in nodes:
        ring.add_node(node)

    counts = {node: 0 for node in nodes}

    for i in range(5000):
        node = ring.get_node(f"key:{i}")
        counts[node] += 1

    total = sum(counts.values())

    for node in nodes:
        share = counts[node] / total
        # With 200 virtual nodes each, shares should be close to 1/3.
        assert 0.2 < share < 0.5


# ---------------------------------------------------------
# get_nodes (replication) tests
# ---------------------------------------------------------


def test_get_nodes_empty_ring():

    ring = ConsistentHashRing(virtual_nodes=25)

    assert ring.get_nodes("user:101", 2) == []


def test_get_nodes_returns_distinct_physical_nodes():

    ring = ConsistentHashRing(virtual_nodes=100)

    ring.add_node("node-1")
    ring.add_node("node-2")
    ring.add_node("node-3")

    for i in range(500):
        nodes = ring.get_nodes(f"key:{i}", 2)

        assert len(nodes) <= 2

        # All returned nodes must be distinct physical nodes
        assert len(nodes) == len(set(nodes))


def test_get_nodes_first_is_primary():

    ring = ConsistentHashRing(virtual_nodes=100)

    ring.add_node("node-1")
    ring.add_node("node-2")

    for i in range(200):
        nodes = ring.get_nodes(f"key:{i}", 2)

        assert len(nodes) >= 1

        # Primary should be the same as get_node()
        assert nodes[0] == ring.get_node(f"key:{i}")


def test_get_nodes_respects_count_limit():

    ring = ConsistentHashRing(virtual_nodes=100)

    ring.add_node("node-1")
    ring.add_node("node-2")
    ring.add_node("node-3")

    nodes = ring.get_nodes("user:101", 2)

    assert len(nodes) == 2


def test_get_nodes_no_duplicates_with_virtual_nodes():

    ring = ConsistentHashRing(virtual_nodes=200)

    ring.add_node("node-1")
    ring.add_node("node-2")

    for i in range(1000):
        nodes = ring.get_nodes(f"key:{i}", 2)

        if len(nodes) == 2:
            assert nodes[0] != nodes[1]


def test_get_nodes_single_node():

    ring = ConsistentHashRing(virtual_nodes=25)

    ring.add_node("node-1")

    nodes = ring.get_nodes("user:101", 2)

    # Only one physical node exists, so we can only return 1
    assert nodes == ["node-1"]


def test_get_nodes_after_remove_node():

    ring = ConsistentHashRing(virtual_nodes=100)

    ring.add_node("node-1")
    ring.add_node("node-2")
    ring.add_node("node-3")

    ring.remove_node("node-2")

    for i in range(200):
        nodes = ring.get_nodes(f"key:{i}", 2)

        for node in nodes:
            assert node in {"node-1", "node-3"}


def test_get_all_nodes():

    ring = ConsistentHashRing(virtual_nodes=25)

    ring.add_node("node-1")
    ring.add_node("node-2")

    all_nodes = ring.get_all_nodes()

    assert set(all_nodes) == {"node-1", "node-2"}


def test_len_counts_physical_nodes():

    ring = ConsistentHashRing(virtual_nodes=50)

    ring.add_node("node-1")
    ring.add_node("node-2")
    ring.add_node("node-3")

    assert len(ring) == 3