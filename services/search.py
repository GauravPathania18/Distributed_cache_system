import math
import re
import time

from database.exceptions import DatabaseUnavailableError


# Only these characters may appear inside a generated cache key.
# `/` is forbidden outright: DistributedCache interpolates keys raw
# into "/cache/{key}" URLs, so a slash would split the path.
_SAFE = re.compile(r"[^A-Za-z0-9_.+-]")

_DIR_WHITELIST = ("asc", "desc")

_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100

# Query params every entity accepts regardless of its schema.
_RESERVED_PARAMS = ("q", "page", "page_size", "sort", "dir")

# Column-name hints for a sensible default sort (all descending).
_DEFAULT_SORT_HINTS = (
    "rating", "score", "review", "price", "cost", "vote", "sales",
    "revenue", "amount", "total", "rank", "year", "budget", "income",
    "profit", "count", "popularity", "duration",
)


def _safe(value) -> str:
    """
    Canonical, URL-path-safe fragment of a user-supplied value.

    Lower-cased so `Electronics` and `electronics` share one cache
    key (the search is case-insensitive anyway), spaces become `+`,
    and anything outside the safe set is dropped.
    """

    text = str(value if value is not None else "").strip().lower()

    text = text.replace(" ", "+")

    return _SAFE.sub("", text)


def prettify(name: str) -> str:
    """Dataset name -> display label ("netflix_titles" -> "Netflix Titles")."""

    text = str(name).replace("_", " ").replace("-", " ")

    return " ".join(word.capitalize() for word in text.split())


# ---------------------------------------------------------
# ENTITY BUILDING (schema-driven - nothing is hardcoded)
# ---------------------------------------------------------

def _param_name(column: str, used: set) -> str:

    base = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_") or "field"

    if base in _RESERVED_PARAMS:
        base += "_f"

    candidate = base
    counter = 2

    while candidate in used:
        candidate = f"{base}_{counter}"
        counter += 1

    used.add(candidate)

    return candidate


def _range_params(column: str, used: set) -> tuple:

    base = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_") or "field"

    if base in _RESERVED_PARAMS:
        base += "_f"

    low, high = f"{base}_min", f"{base}_max"
    counter = 2

    while low in used or high in used:
        low, high = f"{base}_{counter}_min", f"{base}_{counter}_max"
        counter += 1

    used.add(low)
    used.add(high)

    return low, high


def _default_sort(sorts: dict, schema: dict) -> tuple:
    """(sort key, direction) for a freshly built entity.

    `sorts` maps the accepted (lower-cased) sort key to its raw
    column; the returned default must be one of those keys because
    _normalize() lower-cases incoming sort params before matching.
    """

    for hint in _DEFAULT_SORT_HINTS:
        for key, column in sorts.items():
            if (hint in column.lower()
                    and schema[column]["type"] in ("number", "date")):
                return key, "desc"

    for key, column in sorts.items():
        if schema[column]["type"] == "number":
            return key, "desc"

    return next(iter(sorts)), "asc"


def build_entities(entries: dict) -> dict:
    """Registry entries -> search specs generated from their schemas.

    Each entry becomes one entity whose filters/sorts/keyword fields
    are derived from the detected column types:

        string with few values -> enum facet (chips)
        number                 -> min/max range
        date                   -> from/to text range
        bool                   -> toggle
        other strings          -> keyword search + autocomplete

    `fields` maps an output column name to the SQL expression that
    produces it; `sorts` is the ORDER BY whitelist, which is why the
    column can be interpolated safely.
    """

    out = {}

    for name in sorted(entries):

        entry = entries[name]

        schema = entry.get("schema") or {}

        prefix = entry.get("prefix") or f"{name}:"

        fields = {}

        for column in schema:
            # JSON path expressions cannot carry quotes; such
            # columns stay in the value but are not queryable.
            if '"' in column or "\\" in column:
                continue
            fields[column] = f"json_extract(value, '$.\"{column}\"')"

        if not fields:
            continue

        id_expr = f"substr(key, {len(prefix) + 1})"

        text = [
            column for column in fields
            if schema[column]["type"] == "string"
        ][:8]

        used = set()
        filters = {}

        for column in fields:

            if len(filters) >= 12:
                break

            meta = schema[column]
            dtype = meta.get("type") or "string"
            values = meta.get("values")
            expr = fields[column]

            if dtype == "string" and values:

                param = _param_name(column, used)

                filters[param] = {
                    "field": column,
                    "kind": "enum",
                    "values": values,
                }

            elif dtype in ("number", "date"):

                low, high = _range_params(column, used)

                filters[low] = {
                    "field": column,
                    "kind": "range",
                    "min_param": low,
                    "max_param": high,
                    "dtype": dtype,
                }

            elif dtype == "bool":

                param = _param_name(column, used)

                filters[param] = {
                    "field": column,
                    "kind": "bool",
                    "test": f"({expr} = 1 OR lower({expr}) = 'true')",
                }

        # Sort keys are lower-cased + uniquified versions of the
        # column name: _normalize() lower-cases the incoming sort
        # param, so "Aggregate rating" must be reachable as
        # "aggregate_rating" (columns stay raw in `sorts`' values).
        sort_used = set()
        sorts = {
            _param_name(column, sort_used): column for column in fields
        }

        default_sort, default_dir = _default_sort(sorts, schema)

        suggest = [
            column for column in fields
            if schema[column]["type"] == "string"
        ][:6]

        out[name] = {
            "prefix": prefix,
            "id_expr": id_expr,
            "fields": fields,
            "schema": schema,
            "text": text,
            "filters": filters,
            "sorts": sorts,
            "default_sort": default_sort,
            "default_dir": default_dir,
            "suggest": suggest,
            "label": prettify(name),
        }

    return out


class SearchService:
    """
    Layer 8 - cached search over the cache_data table(s).

    A search is not a special case bolted beside the cache: it IS a
    cache entry. Every unique combination of

        entity + keyword + filters + sort + dir + page + page_size

    becomes one cache key of the form

        q:products:kw=bluetooth;category=;pmin=0;...;page=1;size=20

    and the result set is written through the normal write-through
    path (database row + fan-out to the owning nodes + replication
    sequence + ack). So:

        first search   -> one SQL query, then stored and replicated
        every repeat   -> pure cache hit, zero SQL

    Identical concurrent searches collapse to a single SQL query via
    the same SingleFlight that protects point-key reads.

    Entities come from the dataset registry (one entity per imported
    dataset, filters generated from its schema) or - in tests - from
    an explicit `entities` dict. SQL runs against the physical file
    that owns the entity's prefix (via MultiDatasetRepository.repo_for)
    or against the plain repository the service was given.

    Reads go straight to DistributedCache (NOT CacheService.get)
    because CacheService's miss path performs a point lookup by
    exact key, which cannot run SQL. Writes reuse CacheService.set
    so result sets get the full write-through + Layer 7 treatment.
    """

    def __init__(
        self,
        cache_service,
        distributed_cache,
        single_flight,
        circuit_breaker,
        database,
        telemetry,
        ttl: int = 300,
        page_size_default: int = _DEFAULT_PAGE_SIZE,
        page_size_max: int = _MAX_PAGE_SIZE,
        entities: dict = None,
        registry=None
    ):

        self.cache_service = cache_service
        self.distributed_cache = distributed_cache
        self.single_flight = single_flight
        self.circuit_breaker = circuit_breaker
        self.database = database
        self.telemetry = telemetry

        self.ttl = ttl
        self.page_size_default = page_size_default
        self.page_size_max = page_size_max

        self._static_entities = entities
        self.registry = registry

        self._entity_cache = {"revision": None, "specs": {}}

    # --------------------------------------------------
    # PUBLIC API
    # --------------------------------------------------

    def entities(self) -> dict:
        """What the UI needs to build its filter sidebars."""

        out = {}

        for name, spec in self._spec_map().items():

            schema = spec.get("schema") or {}

            columns = list(spec.get("fields") or {})

            filters = {}

            for param, details in spec["filters"].items():

                item = {
                    "kind": details["kind"],
                    "field": details["field"],
                }

                if details["kind"] == "range":
                    item["min_param"] = details["min_param"]
                    item["max_param"] = details["max_param"]
                    item["dtype"] = details.get("dtype", "number")

                elif details["kind"] == "enum":
                    item["values"] = details.get("values") or []

                filters[param] = item

            fields_meta = {}

            for column in columns:
                if schema:
                    fields_meta[column] = (
                        schema.get(column, {}).get("type") or "string"
                    )
                else:
                    fields_meta[column] = "string"

            out[name] = {
                "prefix": spec["prefix"],
                "label": spec.get("label") or prettify(name),
                "columns": columns,
                "fields": fields_meta,
                "filters": filters,
                "sorts": list(spec["sorts"]),
                "default_sort": spec["default_sort"],
                "default_dir": spec["default_dir"],
                "text": list(spec["text"]),
                "suggest": list(spec["suggest"]),
            }

        return out

    def search(self, entity: str, params: dict) -> dict:
        """
        Run (or serve from cache) one search.

        Raises ValueError for an unknown entity. Raises
        DatabaseUnavailableError when the database is down and the
        result set was not already cached.
        """

        spec = self._spec(entity)

        normal = self._normalize(spec, params)

        cache_key = self._cache_key(entity, normal)

        started = time.monotonic()

        # 1. Fast path: is this exact result set already cached?
        value, node = self.distributed_cache.get(cache_key)

        if value is not None:

            self.telemetry.record_search(hit=True)

            return {
                **value,
                "source": "cache",
                "node": node,
                "cache_key": cache_key,
                "took_ms": self._ms(time.monotonic() - started),
            }

        # 2. Slow path: one SQL per distinct query, coalesced.
        def compute():

            # Double-check: another thread may have finished while we
            # were queued behind the leader.
            cached, cached_node = self.distributed_cache.get(cache_key)

            if cached is not None:

                self.telemetry.record_search(hit=True)

                return {
                    **cached,
                    "source": "cache",
                    "node": cached_node,
                    "cache_key": cache_key,
                }

            envelope = self._run(spec, normal)

            # Write-through: database row + fan-out + replication.
            self.cache_service.set(cache_key, envelope, ttl=self.ttl)

            self.telemetry.record_search(hit=False)

            return {
                **envelope,
                "source": "database",
                "node": None,
                "cache_key": cache_key,
            }

        result = self.single_flight.do(cache_key, compute)

        result["took_ms"] = self._ms(time.monotonic() - started)

        return result

    def suggest(self, entity: str, field: str, q: str = "") -> dict:
        """
        Distinct-value autocomplete for one facet field. Cached like
        a search so hammering the search box stays free.
        """

        spec = self._spec(entity)

        if field not in spec["suggest"]:

            raise ValueError(
                f"suggest field not available for {entity}: {field}"
            )

        expr = spec["fields"][field]

        q = (q or "").strip()

        cache_key = (
            f"s:{entity}:f={_safe(field)};kw={_safe(q)};limit=15"
        )

        started = time.monotonic()

        value, node = self.distributed_cache.get(cache_key)

        if value is not None:

            self.telemetry.record_search(hit=True)

            return {
                **value,
                "source": "cache",
                "cache_key": cache_key,
                "took_ms": self._ms(time.monotonic() - started),
            }

        repo = self._repo(spec["prefix"])

        sql = (
            f"SELECT DISTINCT {expr} AS v FROM cache_data "
            f"WHERE key LIKE ? AND {expr} IS NOT NULL"
        )

        args = [spec["prefix"] + "%"]

        if q:

            sql += f" AND lower({expr}) LIKE ?"
            args.append(f"%{q.lower()}%")

        sql += " ORDER BY v LIMIT 15"

        result = self.circuit_breaker.call(
            repo.query, sql, tuple(args), 15
        )

        values = [row[0] for row in result["rows"]]

        envelope = {
            "entity": entity,
            "field": field,
            "values": values,
            "cached_at": time.time(),
        }

        self.cache_service.set(cache_key, envelope, ttl=self.ttl)

        self.telemetry.record_search(hit=False)

        return {
            **envelope,
            "source": "database",
            "cache_key": cache_key,
            "took_ms": self._ms(time.monotonic() - started),
        }

    def browse(self, prefix: str = "", limit: int = 50) -> dict:
        """
        Key listing for the inspector. Deliberately NOT cached: an
        inspector that shows a stale key list is worse than useless.

        With the multi-dataset store the listing merges every
        repository that can hold matching keys (all of them when no
        prefix is given).
        """

        limit = max(1, min(int(limit or 50), 500))

        sql = "SELECT key, length(value) AS size FROM cache_data"

        args = []

        if prefix:

            sql += " WHERE substr(key, 1, length(?)) = ?"
            args.extend([prefix, prefix])

        sql += " ORDER BY key LIMIT ?"
        args.append(limit)

        merged = {}
        truncated = False

        for repo in self._repos(prefix):

            result = self.circuit_breaker.call(
                repo.query, sql, tuple(args), limit
            )

            if result["truncated"]:
                truncated = True

            for row in result["rows"]:
                merged[row[0]] = row[1]

        keys = sorted(merged.items())

        truncated = truncated or len(keys) > limit

        return {
            "prefix": prefix,
            "limit": limit,
            "truncated": truncated,
            "keys": [
                {"key": key, "size": size} for key, size in keys[:limit]
            ],
        }

    # --------------------------------------------------
    # SQL
    # --------------------------------------------------

    def _run(self, spec: dict, normal: dict) -> dict:
        """Execute the count + page queries and build the envelope."""

        db_started = time.monotonic()

        repo = self._repo(spec["prefix"])

        where, args = self._where(spec, normal)

        count_sql = f"SELECT COUNT(*) AS n FROM cache_data WHERE {where}"

        counted = self.circuit_breaker.call(
            repo.query, count_sql, tuple(args), 1
        )

        total = counted["rows"][0][0] if counted["rows"] else 0

        page = normal["page"]
        page_size = normal["page_size"]

        order = self._order(spec, normal)

        # Aliases are quoted: real-world columns carry spaces and
        # mixed case ("Restaurant Name"), which are invalid as bare
        # SQL identifiers. Quoting keeps the JSON/header key equal to
        # the raw column name (SQLite strips the quotes in the result
        # description), so the UI still reads it verbatim.
        select = ", ".join(
            [f'{expr} AS "{name}"' for name, expr in spec["fields"].items()]
        )

        page_sql = (
            f"SELECT key, {spec['id_expr']} AS id, {select} "
            f"FROM cache_data WHERE {where} {order} LIMIT ? OFFSET ?"
        )

        page_args = args + [page_size, (page - 1) * page_size]

        fetched = self.circuit_breaker.call(
            repo.query, page_sql, tuple(page_args), page_size
        )

        headers = fetched["headers"]

        items = [dict(zip(headers, row)) for row in fetched["rows"]]

        return {
            "entity": spec["name"],
            "query": {
                "q": normal["q"],
                "filters": normal["filters"],
                "sort": normal["sort"],
                "dir": normal["dir"],
            },
            "total": total,
            "page": page,
            "page_size": page_size,
            "page_count": max(1, math.ceil(total / page_size)) if total else 1,
            "items": items,
            "db_took_ms": self._ms(time.monotonic() - db_started),
            "cached_at": time.time(),
        }

    def _where(self, spec: dict, normal: dict):
        """Build the WHERE clause + positional args (never identifiers)."""

        clauses = ["key LIKE ?"]
        args = [spec["prefix"] + "%"]

        text_options = [
            f"lower({spec['fields'][field]}) LIKE ?"
            for field in spec["text"]
            if field in spec["fields"]
        ]

        if not text_options:
            text_options = [f"lower({spec['id_expr']}) LIKE ?"]

        # Keyword: every whitespace-separated term must match at
        # least one text field (AND across terms, OR across fields).
        for term in [t for t in normal["q"].split() if t]:

            clauses.append("(" + " OR ".join(text_options) + ")")

            args.extend([f"%{term.lower()}%"] * len(text_options))

        for param, details in spec["filters"].items():

            kind = details["kind"]

            expr = spec["fields"][details["field"]]

            if kind == "enum":

                value = normal["filters"].get(param)

                if value not in (None, ""):

                    clauses.append(f"{expr} = ?")
                    args.append(value)

            elif kind == "range":

                low = normal["filters"].get(details["min_param"])
                high = normal["filters"].get(details["max_param"])

                if low is not None:

                    clauses.append(f"{expr} >= ?")
                    args.append(low)

                if high is not None:

                    clauses.append(f"{expr} <= ?")
                    args.append(high)

            elif kind == "min":

                value = normal["filters"].get(param)

                if value is not None:

                    clauses.append(f"{expr} >= ?")
                    args.append(value)

            elif kind == "bool":

                if normal["filters"].get(param):

                    clauses.append(details["test"])

        return " AND ".join(clauses), args

    def _order(self, spec: dict, normal: dict) -> str:
        """ORDER BY from the per-entity whitelist (never user input)."""

        column = spec["sorts"][normal["sort"]]

        direction = "DESC" if normal["dir"] == "desc" else "ASC"

        expr = spec["fields"][column]

        return f"ORDER BY {expr} {direction}"

    # --------------------------------------------------
    # ENTITY RESOLUTION
    # --------------------------------------------------

    def _spec_map(self) -> dict:
        """Effective entity specs: explicit dict, else the registry."""

        if self._static_entities is not None:
            return self._static_entities

        if self.registry is None:
            return {}

        revision = self.registry.revision()

        if self._entity_cache["revision"] != revision:

            self._entity_cache = {
                "revision": revision,
                "specs": build_entities(self.registry.entries()),
            }

        return self._entity_cache["specs"]

    def _repo(self, prefix: str):
        """Repository that physically owns `prefix` keys."""

        resolver = getattr(self.database, "repo_for", None)

        return resolver(prefix) if resolver else self.database

    def _repos(self, prefix: str = "") -> list:
        """Repositories that can hold keys matching `prefix`."""

        resolver = getattr(self.database, "repos_matching", None)

        return resolver(prefix) if resolver else [self.database]

    def _spec(self, entity: str) -> dict:

        specs = self._spec_map()

        spec = specs.get(entity)

        if spec is None:

            raise ValueError(
                f"unknown entity: {entity} "
                f"(expected one of {sorted(specs)})"
            )

        spec = dict(spec)
        spec["name"] = entity

        return spec

    def _normalize(self, spec: dict, params: dict) -> dict:
        """Coerce, clamp and canonicalize every accepted parameter."""

        raw = params or {}

        page = self._int(raw.get("page"), 1, minimum=1)

        page_size = self._int(
            raw.get("page_size"),
            self.page_size_default,
            minimum=1,
            maximum=self.page_size_max
        )

        sort = str(raw.get("sort") or spec["default_sort"]).strip().lower()

        if sort not in spec["sorts"]:
            sort = spec["default_sort"]

        direction = str(raw.get("dir") or spec["default_dir"]).strip().lower()

        if direction not in _DIR_WHITELIST:
            direction = spec["default_dir"]

        filters = {}

        for param, details in spec["filters"].items():

            kind = details["kind"]

            if kind == "range":

                if details.get("dtype", "number") == "number":

                    low = self._number(raw.get(details["min_param"]))
                    high = self._number(raw.get(details["max_param"]))

                else:

                    low = self._text(raw.get(details["min_param"]))
                    high = self._text(raw.get(details["max_param"]))

                filters[details["min_param"]] = low
                filters[details["max_param"]] = high

            elif kind == "min":

                filters[param] = self._number(raw.get(param))

            elif kind == "bool":

                filters[param] = self._truthy(raw.get(param))

            else:

                value = raw.get(param)

                filters[param] = str(value).strip() if value not in (
                    None, ""
                ) else None

        return {
            "q": str(raw.get("q") or "").strip(),
            "page": page,
            "page_size": page_size,
            "sort": sort,
            "dir": direction,
            "filters": filters,
        }

    def _cache_key(self, entity: str, normal: dict) -> str:

        parts = [f"kw={_safe(normal['q'])}"]

        for name in sorted(normal["filters"]):

            value = normal["filters"][name]

            if value is None or value is False:
                value = ""

            parts.append(f"{name}={_safe(value)}")

        parts.append(f"sort={normal['sort']}")
        parts.append(f"dir={normal['dir']}")
        parts.append(f"page={normal['page']}")
        parts.append(f"size={normal['page_size']}")

        return f"q:{entity}:" + ";".join(parts)

    # --------------------------------------------------
    # INTERNALS
    # --------------------------------------------------

    @staticmethod
    def _int(value, default, minimum=None, maximum=None):

        try:

            number = int(value)

        except (TypeError, ValueError):

            return default

        if minimum is not None and number < minimum:
            number = minimum

        if maximum is not None and number > maximum:
            number = maximum

        return number

    @staticmethod
    def _number(value):

        if value in (None, ""):
            return None

        try:

            return float(value)

        except (TypeError, ValueError):

            return None

    @staticmethod
    def _text(value):

        if value in (None, ""):
            return None

        return str(value).strip() or None

    @staticmethod
    def _truthy(value) -> bool:

        if isinstance(value, bool):
            return value

        return str(value).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _ms(seconds: float) -> float:

        return round(seconds * 1000, 2)
