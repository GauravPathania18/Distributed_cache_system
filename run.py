"""
One-command launcher for DCS.

    python run.py                    # 3 nodes (capacity 5000) + router
    python run.py --nodes 1          # single node + router
    python run.py --seed             # import datasets/ drop folder first
    python run.py --capacity 20      # tiny nodes (Lab 5 eviction demos)

What it does:

    0. (with --seed) imports every new or changed file sitting in
       datasets/ once, before the cluster starts.
    1. Spawns N cache nodes from the port pool (NODE_PORT_BASE..)
       through services.process_manager, so they carry PID files and
       can be stopped from the web UI's Cluster tab like any other
       node. A port that already answers /health is adopted instead
       of double-spawned.
    2. Runs the router IN THIS PROCESS - Ctrl+C stops everything,
       because the router's lifespan hook (process_manager.stop_all)
       is the same registry this launcher spawned into.

The web UI at http://127.0.0.1:8000/ (redirects to /ui) is the
control panel: scale/stop nodes, manage datasets, inspect keys and
run the labs. Node logs land in data/logs/node-<port>.log.
"""

import argparse
import os
import sys
import time

import uvicorn

from router.config import NODE_CAPACITY, NODE_PORT_BASE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the DCS cluster (nodes + router) in one command."
    )
    parser.add_argument(
        "--nodes", type=int, default=3,
        help="cache nodes to spawn (default 3, from NODE_PORT_BASE)"
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="router port (default 8000)"
    )
    parser.add_argument(
        "--capacity", type=int, default=NODE_CAPACITY,
        help=f"per-node cache capacity (default {NODE_CAPACITY})"
    )
    parser.add_argument(
        "--seed", action="store_true",
        help="import new/changed files in the datasets/ drop folder first"
    )
    return parser.parse_args()


def seed_drop_folder() -> None:
    """Import new/changed drop-folder files once, before boot."""

    from import_dataset import seed_drop_folder as _seed

    try:
        results = _seed(verbose=False)
    except Exception as error:
        print(f"[SEED] drop-folder import failed: {error!r}")
        return

    for record in results:

        name = os.path.basename(record.get("file", "?"))

        if record.get("status") in ("imported", "reimported"):
            print(
                f"[SEED] {record['status']}: {name} -> "
                f"{record['name']} ({record['rows']} rows)"
            )
        else:
            print(f"[SEED] failed {name}: {record.get('error')}")

    if not results:
        print("[SEED] drop folder empty or fully imported")


def spawn_nodes(count: int, capacity: int) -> list:
    """Spawn `count` nodes from the pool; adopt ports already answering."""

    from cluster.health_monitor import check_node
    from services.process_manager import process_manager

    started = []

    for index in range(count):

        port = NODE_PORT_BASE + index

        if check_node(f"http://127.0.0.1:{port}"):
            print(f"  node :{port} already answering - adopting it")
            continue

        record = process_manager.start(port, capacity=capacity)

        if record.get("started") or record.get("already_running"):
            started.append(port)
            print(f"  node :{port} started (capacity {capacity}, pid {record.get('pid')})")
        else:
            print(f"  node :{port} FAILED: {record.get('error')}")

    return started


def main() -> None:

    args = parse_args()

    if args.seed:
        seed_drop_folder()

    print(f"Starting {args.nodes} node(s) from port {NODE_PORT_BASE} "
          f"at capacity {args.capacity}...")

    spawn_nodes(max(0, args.nodes), args.capacity)

    print()
    print(f"Router: http://127.0.0.1:{args.port}/  (web UI at /ui)")
    print("Waiting for the cluster to become routable - "
          "watch the router log below.")
    print("Ctrl+C stops the router and every node this launcher spawned.")

    # Give freshly spawned nodes a moment so the router's first
    # health pass does not log three connection errors.
    time.sleep(0.5)

    # The router runs in THIS process: its lifespan hook stops the
    # nodes spawned above on exit (graceful Ctrl+C, no orphans).
    uvicorn.run(
        "router.router:app",
        host="127.0.0.1",
        port=args.port,
        log_level="info"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] launcher stopped.")
        sys.exit(0)
