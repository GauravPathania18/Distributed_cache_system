"""
One-command launcher + terminal UI for DCS.

    python tui.py

Starts the 3 cache nodes and the router, each in its own visible
console window, waits for the cluster to come up, then presents an
interactive menu for cluster control and for querying the cache and
the SQLite source of truth side by side.

On boot the drop folder (datasets/) is auto-seeded: every file that
is not registered yet - or whose content changed - is imported into
its own data/datasets/<name>.db, once. Already-imported files are
skipped, so data persists until the db/registry files are deleted.

Menu highlights:

    d  Datasets - list / import / delete dataset files
    6  Key inspector - reads EVERY node directly plus the database
       and tells you whether the cache is consistent, stale, cold
       or holding a key the database does not have.
    7  List keys / prefixes across every dataset store
    8  Run arbitrary read-only SQL against one store
"""

import atexit
import json
import os
import signal
import subprocess
import sys
import time

from urllib.parse import quote, urlparse

import requests

from cluster.health_monitor import check_node
from database import DatasetRegistry, MultiDatasetRepository
from database.exceptions import (
    DatabaseQueryError,
    DatabaseUnavailableError,
)
from database.registry import slugify
from import_dataset import import_file, list_datasets, seed_drop_folder
from router.config import nodes_from_env


# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------

ROOT = os.path.dirname(os.path.abspath(__file__))

ROUTER_HOST = "127.0.0.1"
ROUTER_PORT = 8000
ROUTER_URL = f"http://{ROUTER_HOST}:{ROUTER_PORT}"

DEFAULT_CAPACITY = 100

BOOT_TIMEOUT = 60
HTTP_TIMEOUT = 5.0
MAX_SQL_ROWS = 50
VALUE_LIMIT = 600

GREEN, YELLOW, RED, CYAN, DIM, BOLD, RESET = (
    "32", "33", "31", "36", "2", "1", "0"
)


def node_port(url: str) -> str:
    return urlparse(url).port or 80


def db_path() -> str:
    path = os.environ.get("CACHE_DB_PATH", "data/cache.db")

    if os.path.isabs(path):
        return path

    return os.path.normpath(os.path.join(ROOT, path))


def repository() -> MultiDatasetRepository:
    path = db_path()
    return MultiDatasetRepository(
        db_path=path, registry=DatasetRegistry(db_path=path)
    )


# ---------------------------------------------------------
# COLORS
# ---------------------------------------------------------

USE_COLOR = False


def init_color():
    global USE_COLOR

    if not sys.stdout.isatty():
        return

    if os.name == "nt":
        os.system("")

    USE_COLOR = True


def paint(text, color) -> str:

    if not USE_COLOR:
        return str(text)

    return f"\033[{color}m{text}\033[{RESET}"


def clear():

    os.system("cls" if os.name == "nt" else "clear")


# ---------------------------------------------------------
# HTTP
# ---------------------------------------------------------

def http(method: str, url: str, body=None, timeout=HTTP_TIMEOUT) -> dict:
    """
    Normalized request helper.

    Returns {"ok", "status", "data", "error"} so callers never have
    to deal with exceptions from downed processes.
    """

    try:

        response = requests.request(
            method,
            url,
            json=body,
            timeout=timeout
        )

    except requests.RequestException as error:
        return {"ok": False, "status": 0, "data": None,
                "error": str(error.__class__.__name__)}

    try:
        data = response.json()
    except ValueError:
        data = response.text

    return {
        "ok": 200 <= response.status_code < 300,
        "status": response.status_code,
        "data": data,
        "error": None if 200 <= response.status_code < 300 else _detail(data),
    }


def _detail(data):

    if isinstance(data, dict) and "detail" in data:
        return str(data["detail"])

    return "HTTP error"


# ---------------------------------------------------------
# PROCESS CONTROL
# ---------------------------------------------------------

def project_python() -> str:
    """Prefer the project venv so the windows run the right deps."""

    candidates = [
        os.path.join(ROOT, ".venv", "Scripts", "python.exe"),
        os.path.join(ROOT, ".venv", "bin", "python"),
    ]

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate

    return sys.executable


def spawn(label: str, command: list) -> dict:

    kwargs = {"cwd": ROOT}

    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **kwargs)

    return {"label": label, "command": command, "process": process}


def is_alive(child) -> bool:
    return child["process"].poll() is None


def alive_children(children) -> list:
    return [child for child in children if is_alive(child)]


def ensure_running(children) -> list:
    """
    Start anything that is not already answering. Processes already
    running outside this UI are left alone (and never stopped by it).
    """

    started = []

    python = project_python()

    if not check_node(ROUTER_URL) and not _spawned(children, "router"):
        children.append(spawn("router", [
            python, "-m", "uvicorn", "router.router:app",
            "--port", str(ROUTER_PORT)
        ]))
        started.append("router  :8000")

    for url in nodes_from_env():

        label = f"node :{node_port(url)}"

        if check_node(url) or _spawned(children, label):
            continue

        children.append(spawn(label, [
            python, "main.py",
            "--port", str(node_port(url)),
            "--capacity", str(DEFAULT_CAPACITY)
        ]))
        started.append(label)

    return started


def _spawned(children, label) -> bool:
    return any(child["label"] == label for child in children)


def stop_all(children):

    for child in children:

        if not is_alive(child):
            continue

        try:
            child["process"].terminate()
        except OSError:
            pass

    deadline = time.time() + 3

    for child in children:

        process = child["process"]

        if process.poll() is not None:
            continue

        try:
            process.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass


_STOPPED = {"done": False}


def _emergency_stop():

    if _STOPPED["done"] or not _STOPPED.get("children"):
        return

    _STOPPED["done"] = True
    stop_all(_STOPPED["children"])


# ---------------------------------------------------------
# BOOT
# ---------------------------------------------------------

def wait_for(predicate, label, timeout=BOOT_TIMEOUT, interval=0.5) -> bool:

    print(f"  {label} ", end="", flush=True)

    deadline = time.time() + timeout

    while time.time() < deadline:

        if predicate():
            print(paint("OK", GREEN))
            return True

        print(".", end="", flush=True)
        time.sleep(interval)

    print(paint(" TIMEOUT", RED))
    return False


def active_nodes() -> list:

    status = http("GET", f"{ROUTER_URL}/cluster/status", timeout=1.5)

    if not status["ok"]:
        return []

    return status["data"].get("active_nodes", [])


def auto_seed() -> list:
    """Import unregistered files from the datasets/ drop folder.

    Runs before the cluster starts, so a fresh Kaggle CSV dropped
    into datasets/ is imported on the very next boot - once.
    Already-imported files are skipped; changed files re-import.
    Failures print but never block the launcher.
    """

    try:
        results = seed_drop_folder(registry=DatasetRegistry(db_path=db_path()))
    except Exception as error:
        print(paint(f"  dataset auto-seed failed: {error}", RED))
        print()
        return []

    for record in results:

        status = record.get("status")

        if status in ("imported", "reimported"):
            print(
                f"  {paint(status, GREEN)} "
                f"{os.path.basename(record['file'])} -> "
                f"{record['name']} ({record['rows']} rows)"
            )
        else:
            print(
                paint(
                    f"  seed failed {os.path.basename(record.get('file', '?'))}"
                    f": {record.get('error')}",
                    RED
                )
            )

    if results:
        print()

    return results


def boot(children):

    clear()
    print(paint("DCS - Distributed Cache  |  one-command launcher", BOLD))
    print("=" * 58)

    auto_seed()

    started = ensure_running(children)

    if started:
        for label in started:
            print(f"  {paint('started', GREEN)} {label}  (own window)")
    else:
        print(f"  {paint('attached', CYAN)} all processes already running")

    print()

    wait_for(lambda: check_node(ROUTER_URL),
             "waiting for router", timeout=30)

    expected = len(nodes_from_env())

    wait_for(lambda: len(active_nodes()) >= expected,
             f"waiting for {expected} node(s) to become active",
             timeout=BOOT_TIMEOUT)

    print()
    print_cluster_summary()
    print()


def print_cluster_summary():

    status = http("GET", f"{ROUTER_URL}/cluster/status", timeout=1.5)

    if not status["ok"]:
        print(paint("  router is not answering", RED))
        return

    states = status["data"].get("node_states", {})

    for url, state in states.items():
        print(f"  {url:<28} {state_color(state)}")

    active = status["data"].get("active_nodes", [])

    print(f"  active: {len(active)}/{len(states)}")

    if len(active) < len(states):
        print(paint("  PUTs return 503 until every node is ready", YELLOW))


def state_color(state: str) -> str:

    if state == "ready":
        return paint(state, GREEN)
    if state in ("starting", "recovering"):
        return paint(state, YELLOW)
    if state in ("failed", "unhealthy"):
        return paint(state, RED)

    return state


# ---------------------------------------------------------
# DISPLAY
# ---------------------------------------------------------

def header(children):

    alive = len(alive_children(children))
    total = len(children)

    status = http("GET", f"{ROUTER_URL}/cluster/status", timeout=1.5)
    breaker = http("GET", f"{ROUTER_URL}/circuit-breaker/status", timeout=1.5)

    print()
    print(paint("  DCS CONTROL PANEL", BOLD))
    print("  " + "-" * 56)

    if status["ok"]:

        data = status["data"]
        states = data.get("node_states", {})
        ready = sum(1 for s in states.values() if s == "ready")
        compact = "  ".join(
            f"{urlparse(u).port}:{state_color(s)}"
            for u, s in states.items()
        )

        print(f"  cluster  {paint(str(ready) + '/' + str(len(states)), BOLD)}"
              f" ready     {compact}")
    else:
        print(f"  cluster  {paint('router offline', RED)}")

    if breaker["ok"]:
        state = breaker["data"].get("state", "?")
        color = GREEN if state == "closed" else YELLOW
        print(f"  breaker  {paint(state, color)}", end="")
    else:
        print(f"  breaker  {paint('unknown', DIM)}", end="")

    print(f"     spawned windows alive "
          f"{paint(f'{alive}/{total}', BOLD if alive == total else RED)}")
    print("  " + "-" * 56)


def menu():

    print("""
  CONTROL
    1   Cluster status & lifecycle
    2   PUT      write through the router
    3   GET      read  through the router
    4   DELETE   invalidate
    5   DB get   read the source of truth through the router
    b   Start anything that is down

  DATASETS
    d   List / import / delete dataset files

  QUERY
    6   Key inspector: every node vs database   <==
    7   List keys across every dataset store
    8   Run SQL on one store (read-only)
    9   Statistics, hot keys & warm-up

    0   Stop everything this UI started & exit
""")


def ask(prompt: str) -> str:

    try:
        return input(f"  {prompt}: ").strip()
    except EOFError:
        return ""


def pause():

    if sys.stdin.isatty():
        try:
            input(paint("\n  Press Enter to continue...", DIM))
        except EOFError:
            pass


def fmt(value, limit=VALUE_LIMIT) -> str:

    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, indent=2, ensure_ascii=False)

    if len(text) > limit:
        text = text[:limit] + "\n  ... [truncated]"

    return text


def render_table(headers, rows) -> None:

    def cell(item):
        text = "" if item is None else str(item)
        return text if len(text) <= 40 else text[:39] + "..."

    headers = [cell(head) for head in headers]
    rows = [[cell(item) for item in row] for row in rows]

    widths = [
        max([len(headers[i])] + [len(row[i]) for row in rows])
        for i in range(len(headers))
    ]

    line = "  " + "  ".join(
        head.ljust(widths[i]) for i, head in enumerate(headers)
    )
    print(paint(line, BOLD))
    print("  " + "-" * min(70, sum(widths) + 2 * len(widths)))

    for row in rows:
        print("  " + "  ".join(
            item.ljust(widths[i]) for i, item in enumerate(row)
        ))


# ---------------------------------------------------------
# LOGIC (pure - unit tested)
# ---------------------------------------------------------

def parse_value(text):
    """JSON if it parses, otherwise the raw string."""

    stripped = text.strip()

    if not stripped:
        return ""

    try:
        return json.loads(stripped)
    except ValueError:
        return text


def compute_verdict(cache_results, db_result) -> tuple:
    """
    cache_results: [(label, status, value)]  status hit|miss|down
    db_result:     ("found", value) | ("missing", None) | ("error", msg)

    Returns (level, message); level in ok | warn | bad | info.
    """

    hits = [(label, value) for label, status, value in cache_results
            if status == "hit"]
    down = [label for label, status, value in cache_results
            if status == "down"]
    note = f"  [{len(down)} node unreachable]" if down else ""

    kind, payload = db_result

    if kind == "error":
        return "bad", f"DATABASE UNAVAILABLE - cache cannot be verified{note}"

    if kind == "missing":
        if hits:
            return ("warn",
                    f"CACHE-ONLY - {len(hits)} node(s) hold a value the "
                    f"database does not have{note}")
        return "info", "NOT FOUND - key is in neither cache nor database"

    db_value = payload

    if not hits:
        return ("info",
                "DB-ONLY - source of truth has the key, the cache is "
                "cold (next GET populates it)")

    matching = [label for label, value in hits if value == db_value]
    stale = [label for label, value in hits if value != db_value]

    if not stale:
        return ("ok",
                f"CONSISTENT - {len(matching)} caching node(s) match "
                f"the database{note}")

    if matching:
        return ("warn",
                f"PARTIAL - {len(matching)} node(s) match, "
                f"{len(stale)} hold stale data{note}")

    return ("bad",
            f"STALE - all {len(stale)} caching node(s) differ from "
            f"the database{note}")


LEVEL_STYLE = {
    "ok": GREEN,
    "warn": YELLOW,
    "bad": RED,
    "info": CYAN,
}


# ---------------------------------------------------------
# ACTIONS
# ---------------------------------------------------------

def action_status(children):

    status = http("GET", f"{ROUTER_URL}/cluster/status")

    if not status["ok"]:
        print(paint(f"  router offline: {status['error']}", RED))
        return

    print()
    print(json.dumps(status["data"], indent=2))

    print_cluster_summary()


def action_put(children):

    key = ask("Key (e.g. user:42)")
    if not key:
        return

    raw = ask("Value (JSON or plain text)")
    value = parse_value(raw)

    ttl_raw = ask("TTL seconds (blank = 300, 0 = no TTL)")

    body = {"value": value}

    if ttl_raw != "0":
        try:
            body["ttl"] = int(ttl_raw) if ttl_raw else 300
        except ValueError:
            body["ttl"] = 300

    response = http("PUT", f"{ROUTER_URL}/cache/{quote(key, safe='')}", body)

    if response["ok"]:
        print(paint("  stored", GREEN), json.dumps(response["data"]))
    else:
        print(paint(f"  FAILED ({response['status']}): "
                    f"{response['error']}", RED))


def action_get(children):

    key = ask("Key")
    if not key:
        return

    response = http("GET", f"{ROUTER_URL}/cache/{quote(key, safe='')}")

    if response["ok"]:

        data = response["data"]
        print(paint(f"  source: {data.get('source')}", GREEN))
        if data.get("node"):
            print(f"  node  : {data['node']}")
        print()
        print("  " + fmt(data.get("value")).replace("\n", "\n  "))

    else:
        print(paint(f"  {response['status']}: {response['error']}",
                    RED if response["status"] != 404 else YELLOW))


def action_delete(children):

    key = ask("Key")
    if not key:
        return

    response = http("DELETE", f"{ROUTER_URL}/cache/{quote(key, safe='')}")

    if response["ok"]:
        print(paint("  deleted", GREEN), json.dumps(response["data"]))
    else:
        print(paint(f"  {response['status']}: {response['error']}", RED))


def action_db_get(children):

    key = ask("Key")
    if not key:
        return

    response = http("GET", f"{ROUTER_URL}/db/{quote(key, safe='')}")

    if response["ok"]:
        print()
        print("  " + fmt(response["data"].get("value")).replace("\n", "\n  "))
    else:
        print(paint(f"  {response['status']}: {response['error']}",
                    RED if response["status"] != 404 else YELLOW))


def action_resume(children):

    started = ensure_running(children)

    if not started:
        print(paint("  everything is already running", GREEN))
    else:
        for label in started:
            print(f"  {paint('started', GREEN)} {label}")
        wait_for(lambda: check_node(ROUTER_URL),
                 "waiting for router", timeout=30)
        expected = len(nodes_from_env())
        wait_for(lambda: len(active_nodes()) >= expected,
                 f"waiting for {expected} node(s)", timeout=BOOT_TIMEOUT)


def action_inspect(children):

    key = ask("Key to inspect (e.g. user:42)")
    if not key:
        return

    encoded = quote(key, safe="")

    print()
    print(paint(f"  CACHE - direct node reads for {key!r}", BOLD))

    cache_results = []

    for url in nodes_from_env():
        label = f":{node_port(url)}"
        response = http("GET", f"{url}/cache/{encoded}", timeout=1.5)

        if response["ok"]:
            cache_results.append((label, "hit", response["data"]["value"]))
            print(f"    {label:<7} {paint('HIT', GREEN)}   "
                  f"{_short(response['data']['value'])}")
        elif response["status"] == 404:
            cache_results.append((label, "miss", None))
            print(f"    {label:<7} {paint('MISS', DIM)}")
        else:
            cache_results.append((label, "down", None))
            print(f"    {label:<7} {paint('DOWN', RED)}   "
                  f"{response['error']}")

    print()
    print(paint("  DATABASE - source of truth (read-only)", BOLD))

    try:
        value = repository().get(key)
        kind, payload = (("found", value) if value is not None
                         else ("missing", None))
    except DatabaseUnavailableError as error:
        kind, payload = "error", str(error)

    if kind == "found":
        print(f"    FOUND   {_short(payload)}")
    elif kind == "missing":
        print(f"    {paint('MISSING', DIM)}")
    else:
        print(f"    {paint('ERROR', RED)}   {payload}")

    print()
    level, message = compute_verdict(cache_results, (kind, payload))
    print(f"  VERDICT  {paint(message, LEVEL_STYLE[level])}")


def _short(value, limit=140) -> str:

    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False
    )

    return text if len(text) <= limit else text[:limit - 3] + "..."


def action_list_keys(children):

    prefix = ask("Key prefix (blank = dataset + prefix summary)")
    limit_raw = ask("Limit (blank = 20)")

    try:
        limit = int(limit_raw) if limit_raw else 20
    except ValueError:
        limit = 20

    limit = max(1, limit)

    store = repository()

    try:

        if prefix:

            total = 0
            collected = {}

            # Match against every store that can hold the prefix -
            # system keys and dataset keys alike.
            for repo in store.repos_matching(prefix):

                total += repo.query(
                    "SELECT COUNT(*) FROM cache_data "
                    "WHERE substr(key, 1, length(?)) = ?",
                    (prefix, prefix)
                )["rows"][0][0]

                page = repo.query(
                    "SELECT key, length(value) AS size FROM cache_data "
                    "WHERE substr(key, 1, length(?)) = ? "
                    "ORDER BY key LIMIT ?",
                    (prefix, prefix, limit),
                    max_rows=limit
                )

                for row in page["rows"]:
                    collected[row[0]] = row[1]

            keys = sorted(collected.items())[:limit]

            print()
            print(paint(f"  {total} key(s) matching {prefix!r}"
                        f" - showing {len(keys)}", BOLD))
            print()

            if keys:
                render_table(
                    ["key", "value bytes"], [list(key) for key in keys]
                )

        else:

            print()
            print(paint("  STORES", BOLD))
            print()

            table = []

            for label, repo in store.repos():

                entry = store.registry.get(label) if label != "system" else None

                table.append([
                    label,
                    (entry or {}).get("prefix") or "-",
                    (entry or {}).get("key_column") or "-",
                    str(repo.count()),
                ])

            render_table(["store", "prefix", "key column", "rows"], table)

            print()
            print(paint(f"  first {limit} keys (merged, sorted)", BOLD))
            print()

            for key in store.keys(limit):
                print(f"    {key}")

    except (ValueError, DatabaseUnavailableError) as error:
        print(paint(f"  database error: {error}", RED))


def action_sql(children):

    store = repository()

    options = [label for label, _ in store.repos()]

    target = ask(f"Run against [{' | '.join(options)}] (blank = system)")
    target = target or "system"

    repo = store.repo_named(target)

    if repo is None:
        print(paint(f"  unknown store {target!r}", RED))
        return

    print(paint(f"  Read-only SQL on {target}. Examples:", DIM))
    print("    SELECT * FROM cache_data LIMIT 10;")
    print("    SELECT key, length(value) AS size FROM cache_data "
          "ORDER BY key LIMIT 10;")
    print()

    sql = ask("SQL")
    if not sql:
        return

    try:
        result = repo.query(sql, max_rows=MAX_SQL_ROWS)
    except (ValueError, DatabaseQueryError,
            DatabaseUnavailableError) as error:
        print(paint(f"  {error}", RED))
        return

    print()
    render_table(result["headers"], result["rows"])

    if result["truncated"]:
        print(paint(f"\n  showing first {MAX_SQL_ROWS} rows only", DIM))


def action_datasets(children):

    store = repository()

    while True:

        entries = list_datasets(store.registry)

        print()
        print(paint("  DATASETS", BOLD))
        print("  " + "-" * 56)

        if not entries:
            print("  (none registered - drop a file into datasets/ "
                  "and import it here or re-run tui.py)")
        else:
            render_table(
                ["dataset", "prefix", "rows", "key column"],
                [
                    [
                        entry["name"],
                        entry["prefix"],
                        str(entry["rows_live"]),
                        entry["key_column"] or "(row #)",
                    ]
                    for entry in entries
                ]
            )

        print()
        print(f"  system store : {store.system.count()} row(s) "
              f"(search cache + manual keys)")
        print(f"  drop folder  : {store.registry.drop_dir}")

        choice = ask("[i]mport file, [d]elete dataset, [b]ack").lower()

        if choice in ("", "b", "q"):
            return

        if choice == "i":

            path = ask("Path to dataset file (e.g. datasets/foo.csv)")
            if not path:
                continue

            name = slugify(path)
            reimport = store.registry.get(name) is not None

            if reimport:
                confirm = ask(
                    f"{name} is registered - reimport? (y/N)"
                ).lower()
                if confirm not in ("y", "yes"):
                    continue

            try:
                result = import_file(
                    path, reimport=reimport, registry=store.registry,
                    verbose=True
                )
                print(paint(f"  registered as {result['name']}", GREEN))
            except (ValueError, DatabaseUnavailableError) as error:
                print(paint(f"  import failed: {error}", RED))

        elif choice == "d":

            name = ask("Dataset name to delete")
            if not name:
                continue

            if store.registry.unregister(name):
                print(paint(f"  deleted {name} (db file removed)", GREEN))
            else:
                print(paint(f"  no dataset named {name!r}", RED))

        else:
            print(paint("  unknown option", RED))


def action_stats(children):

    print()
    print(paint("  ROUTER", BOLD))

    hot = http("GET", f"{ROUTER_URL}/cluster/hot-keys?limit=10&per_node=20")
    warm = http("GET", f"{ROUTER_URL}/cluster/warmup")
    breaker = http("GET", f"{ROUTER_URL}/circuit-breaker/status")

    if hot["ok"]:
        data = hot["data"]
        print(f"    hot keys tracked: {data.get('tracked_keys')}")
        for key, count in data.get("top_keys", [])[:10]:
            print(f"      {count:>6}  {key}")
    else:
        print(paint(f"    hot-keys unavailable: {hot['error']}", RED))

    if warm["ok"]:
        data = warm["data"]
        print(f"    warm-up: enabled={data.get('enabled')} "
              f"limit={data.get('limit')} "
              f"rate={data.get('rate_limit')}/s")
        for url, record in data.get("nodes", {}).items():
            print(f"      {urlparse(url).port}: {record.get('state')} "
                  f"loaded={record.get('keys_loaded')} "
                  f"attempts={record.get('attempts')}")

    if breaker["ok"]:
        data = breaker["data"]
        print(f"    breaker: {data.get('state')} "
              f"(failures {data.get('failure_count')}/"
              f"{data.get('failure_threshold')})")

    for url in nodes_from_env():

        print()
        print(paint(f"  NODE {url}", BOLD))

        window = http("GET", f"{url}/stats/window?limit=5", timeout=1.5)

        if not window["ok"]:
            print(paint(f"    unavailable: {window['error']}", RED))
            continue

        data = window["data"]
        print(f"    hits={data.get('hits')} misses={data.get('misses')} "
              f"hit_rate={data.get('hit_rate')} "
              f"sets={data.get('sets')} evictions={data.get('evictions')}")
        print(f"    tracked_keys={data.get('tracked_keys')} "
              f"window={data.get('window_seconds')}s")

        for key, count in data.get("top_keys", []):
            print(f"      {count:>6}  {key}")


ACTIONS = {
    "1": action_status,
    "2": action_put,
    "3": action_get,
    "4": action_delete,
    "5": action_db_get,
    "6": action_inspect,
    "7": action_list_keys,
    "8": action_sql,
    "9": action_stats,
    "b": action_resume,
    "d": action_datasets,
}


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    init_color()

    children = []
    _STOPPED["children"] = children
    atexit.register(_emergency_stop)

    if os.name == "nt":
        for sig in (signal.SIGBREAK, signal.SIGTERM):
            try:
                signal.signal(sig, lambda *_: (_emergency_stop(),
                                               sys.exit(1)))
            except (OSError, ValueError):
                pass

    try:

        boot(children)

        while True:

            header(children)
            menu()

            try:
                choice = input(paint("  > ", CYAN)).strip().lower()
            except EOFError:
                break

            if choice == "0":
                break

            action = ACTIONS.get(choice)

            if action is None:
                print(paint("  unknown option", RED))
                pause()
                continue

            try:
                action(children)
            except KeyboardInterrupt:
                print()
                continue

            pause()

    except KeyboardInterrupt:
        print()

    finally:
        if not _STOPPED["done"]:
            _STOPPED["done"] = True
            print(paint("\n  stopping everything this UI started...", DIM))
            stop_all(children)


if __name__ == "__main__":
    main()
