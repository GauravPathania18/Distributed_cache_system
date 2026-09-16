from cluster.consistent_hash import ConsistentHashRing


def main():

    print("=" * 50)
    print("DISTRIBUTED CACHE - LAYER 3.1 CONSISTENT HASHING DEMO")
    print("=" * 50)

    ring = ConsistentHashRing(virtual_nodes=100)

    for node in [
        "localhost:8001",
        "localhost:8002",
        "localhost:8003"
    ]:
        ring.add_node(node)

    print(f"\nHash ring has {len(ring)} virtual-node positions.\n")

    keys = [
        "user:101",
        "user:102",
        "user:103",
        "user:104",
        "user:105",
        "product:101",
        "product:102",
        "product:103"
    ]

    print("KEY DISTRIBUTION:")
    print("-----------------")

    for key in keys:
        node = ring.get_node(key)
        print(f"  {key:15} -> {node}")


if __name__ == "__main__":
    main()