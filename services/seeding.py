"""
Layer 6.5 - cold-start cache seeding.

A fresh cluster has no traffic history, so the Layer 6.3 warm-up
has nothing to reload: nodes would come up empty and every first
request for a dataset row would be a database miss. Seeding answers
one question BEFORE the traffic arrives - which rows deserve the
empty cache?

    popular (default)
        Rank each dataset by the column its schema already flags as
        the popularity signal - the same default sort the search UI
        shows (score, aggregate_rating, release_year, ...) - and take
        the top rows. With nothing learned yet, "what the data says
        is most notable" is the best available predictor of "what
        gets asked for first". A dataset with no numeric or date
        column carries no such signal and is sampled instead.
    random
        Uniform sample of rows - for diversity, or when randomness
        is preferred over ranking.

Selection only produces KEYS. CacheWarmup does the actual work
(ownership filter, throttled database read, direct write), so a
seed behaves exactly like a hot key that the DATA nominated instead
of traffic.

Seeds are a starting set, not a reservation. Normal cache-aside
takes over afterwards: a miss reads the database and the LRU
evicts the least-recently-used entry, so a seeded row nobody asks
for is replaced by a row somebody does - and every access moves a
key back to the MRU front, which is how a frequently used entry
keeps its priority.
"""

import requests

from database.exceptions import DatabaseQueryError, DatabaseUnavailableError

from services.search import build_entities


DEFAULT_STRATEGY = "popular"
STRATEGIES = ("popular", "random")

DEFAULT_PER_DATASET = 250
DEFAULT_FILL_RATIO = 0.6
DEFAULT_FALLBACK_CAPACITY = 100


def normalize_strategy(value) -> str:
    """Unknown or blank strategy -> the default ("popular")."""

    strategy = str(value or "").strip().lower()

    return strategy if strategy in STRATEGIES else DEFAULT_STRATEGY


def popularity_order(spec):
    """ORDER BY expression for a dataset's "most used" rows, or None.

    None means the schema carries no popularity signal: the entity's
    default sort fell back to an ascending first column, which ranks
    nothing worth seeding.

    The expression is the JSON projection the search layer already
    validated (columns containing a quote or backslash are excluded
    there), so interpolating it into ORDER BY is safe - it is the
    very expression the UI's default sort runs.
    """

    if not spec:
        return None

    column = (spec.get("sorts") or {}).get(spec.get("default_sort"))

    if not column or spec.get("default_dir") != "desc":
        return None

    return (spec.get("fields") or {}).get(column) or None


def dataset_keys(repo, spec, strategy, limit) -> list:
    """`limit` seed keys from one dataset's store.

    "popular" with a detected signal orders by that column
    descending (hottest rows first); anything else samples uniformly
    with ORDER BY RANDOM().

    A statement the query layer rejects (one odd dataset) skips that
    dataset quietly. A storage failure is an outage, not an oddity,
    so DatabaseUnavailableError propagates - warm-up turns it into a
    failed attempt that holds the READY gate, exactly as a failed
    hot-key read would.
    """

    expression = popularity_order(spec) if strategy == "popular" else None

    if expression is None:
        sql = "SELECT key FROM cache_data ORDER BY RANDOM() LIMIT ?"
    else:
        sql = (
            "SELECT key FROM cache_data "
            f"ORDER BY {expression} DESC LIMIT ?"
        )

    try:
        result = repo.query(sql, (limit,), max_rows=limit)

    except DatabaseUnavailableError:
        raise

    except (DatabaseQueryError, ValueError):
        return []

    return [row[0] for row in result["rows"]]


def interleave(groups) -> list:
    """Round-robin merge so every slice stays dataset-diverse.

    Each node keeps only the keys IT owns (about 1/N of the pool)
    and stops at its budget, so a dataset-major pool would hand the
    first node nothing but the first dataset. Interleaved, any
    prefix - and any ownership slice - still draws from every
    dataset, in popularity order within each.
    """

    merged = []

    for index in range(max((len(group) for group in groups), default=0)):

        for group in groups:

            if index < len(group):
                merged.append(group[index])

    return merged


def seed_keys(database, strategy=DEFAULT_STRATEGY,
              per_dataset=DEFAULT_PER_DATASET) -> list:
    """Candidate seed keys for every registered dataset.

    One query per dataset, ordered round-robin across datasets.

    Empty when there is nothing to seed from: no registry (a plain
    DatabaseRepository or a fresh install), no datasets, or a
    non-positive limit. Warm-up then loads nothing and behaves
    exactly as before - seeding can only ever add to the old
    behaviour, never change it.
    """

    registry = getattr(database, "registry", None)
    repo_for = getattr(database, "repo_for", None)

    if registry is None or repo_for is None:
        return []

    entries = registry.entries()

    if not entries or per_dataset <= 0:
        return []

    strategy = normalize_strategy(strategy)

    specs = build_entities(entries)

    groups = []

    for name in sorted(entries):

        prefix = entries[name].get("prefix") or ""

        # A prefixless entry is not a dataset (its keys are the
        # system store's own) - never seed cache artifacts.
        if not prefix:
            continue

        keys = dataset_keys(
            repo_for(prefix), specs.get(name), strategy, per_dataset
        )

        if keys:
            groups.append(keys)

    return interleave(groups)


def fetch_capacity(node: str, timeout: float = 1.0) -> int:
    """The node's real LRU capacity from GET /stats (0 = unknown).

    The node process answers /stats even while it is gated out of
    the ring - the recovery gate blocks ROUTING, not the HTTP
    server. A node that is down (the usual reason it is recovering)
    reports 0 and the caller falls back to its configured guess, so
    a seed stays bounded even when the node cannot be reached.
    """

    try:

        response = requests.get(f"{node}/stats", timeout=timeout)

        if response.status_code == 200:
            capacity = int(response.json().get("capacity") or 0)
            if capacity > 0:
                return capacity

    except Exception:
        # Unreachable node, timeout or a non-JSON body: this read is
        # best-effort metadata, and bounded-by-guess beats
        # unbounded.
        pass

    return 0


def seed_budget(capacity, fill_ratio) -> int:
    """How many keys one node may be seeded with.

    A fraction of the node's real capacity - never all of it: the
    rest of the LRU must stay free for the traffic the seeds exist
    to attract, and anything past capacity would only evict the
    seeds again on the next insert.
    """

    ratio = min(max(float(fill_ratio or 0), 0.0), 1.0)

    return max(0, int((capacity or 0) * ratio))


class CacheSeeder:
    """
    Layer 6.5 - the cold-start seed pool for CacheWarmup.

    Owns the policy (what to load, and how much of it); warm-up owns
    the mechanics (ownership, throttling, writing). Wiring a seeder
    into CacheWarmup is what turns seeding on: no seeder, no
    behaviour change.
    """

    def __init__(
        self,
        database,
        strategy=DEFAULT_STRATEGY,
        per_dataset=DEFAULT_PER_DATASET,
        fill_ratio=DEFAULT_FILL_RATIO,
        fallback_capacity=DEFAULT_FALLBACK_CAPACITY,
        capacity_fetcher=None
    ):

        self.database = database

        self.strategy = normalize_strategy(strategy)
        self.per_dataset = int(per_dataset)
        self.fill_ratio = float(fill_ratio)
        self.fallback_capacity = int(fallback_capacity)

        # callable(node) -> capacity. None = fetch_capacity (the
        # node's own /stats), same as tests inject for everything.
        self.capacity_fetcher = capacity_fetcher or fetch_capacity

    def candidates(self) -> list:
        """Popularity-ordered keys, interleaved across datasets."""

        return seed_keys(
            self.database,
            strategy=self.strategy,
            per_dataset=self.per_dataset
        )

    def budget(self, node: str) -> int:
        """Keys this node may be seeded with (capacity x fill_ratio).

        The node's real capacity when it answers /stats, the
        configured fallback when it does not - either way the number
        is known BEFORE the first write, so seeding can never
        overflow a node and evict its own seeds.
        """

        try:
            capacity = int(self.capacity_fetcher(node) or 0)
        except Exception:
            capacity = 0

        if capacity <= 0:
            capacity = self.fallback_capacity

        return seed_budget(capacity, self.fill_ratio)

    def status(self) -> dict:
        """Config snapshot for /cluster/warmup."""

        return {
            "strategy": self.strategy,
            "per_dataset": self.per_dataset,
            "fill_ratio": self.fill_ratio,
            "fallback_capacity": self.fallback_capacity,
        }
