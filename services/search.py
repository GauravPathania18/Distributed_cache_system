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


# ---------------------------------------------------------
# ENTITY REGISTRY
# ---------------------------------------------------------

# `fields` maps an output column name to the SQL expression that
# produces it. `filters` maps an accepted query-parameter name to a
# dict with:
#     field - the field name (looked up in `fields`)
#     kind  - "enum" | "range" | "min" | "bool"
#     min_param / max_param - query params for a "range"
#     test  - SQL boolean for a "bool" filter
# `sorts` is the ORDER BY whitelist: a sort name may only be one of
# these keys, which is why the column can be interpolated safely.

ENTITIES = {
    "products": {
        "prefix": "product:",
        "id_expr": "substr(key, 9)",
        "fields": {
            "name": "json_extract(value,'$.name')",
            "price": "json_extract(value,'$.price')",
            "category": "json_extract(value,'$.category')",
            "stock": "json_extract(value,'$.stock')",
            "rating": "json_extract(value,'$.rating')",
        },
        "text": ["name"],
        "filters": {
            "category": {"field": "category", "kind": "enum"},
            "price": {
                "field": "price",
                "kind": "range",
                "min_param": "pmin",
                "max_param": "pmax",
            },
            "rating": {"field": "rating", "kind": "min"},
            "instock": {
                "field": "stock",
                "kind": "bool",
                "test": "json_extract(value,'$.stock') > 0",
            },
        },
        "sorts": {
            "rating": "rating",
            "price": "price",
            "name": "name",
            "stock": "stock",
        },
        "default_sort": "rating",
        "default_dir": "desc",
        "suggest": ["category"],
    },
    "users": {
        "prefix": "user:",
        "id_expr": "substr(key, 6)",
        "fields": {
            "name": "json_extract(value,'$.name')",
            "email": "json_extract(value,'$.email')",
            "age": "json_extract(value,'$.age')",
            "city": "json_extract(value,'$.city')",
            "signup_date": "json_extract(value,'$.signup_date')",
            "active": "json_extract(value,'$.active')",
            "score": "json_extract(value,'$.score')",
        },
        "text": ["name", "city"],
        "filters": {
            "city": {"field": "city", "kind": "enum"},
            "age": {
                "field": "age",
                "kind": "range",
                "min_param": "amin",
                "max_param": "amax",
            },
            "score": {
                "field": "score",
                "kind": "range",
                "min_param": "smin",
                "max_param": "smax",
            },
            "active": {
                "field": "active",
                "kind": "bool",
                "test": "json_extract(value,'$.active') IN (1, 'true')",
            },
        },
        "sorts": {
            "score": "score",
            "age": "age",
            "name": "name",
            "signup_date": "signup_date",
        },
        "default_sort": "score",
        "default_dir": "desc",
        "suggest": ["city"],
    },
    "orders": {
        "prefix": "order:",
        "id_expr": "substr(key, 7)",
        "fields": {
            "user_id": "json_extract(value,'$.user_id')",
            "item": "json_extract(value,'$.item')",
            "quantity": "json_extract(value,'$.quantity')",
            "total": "json_extract(value,'$.total')",
            "status": "json_extract(value,'$.status')",
            "created_at": "json_extract(value,'$.created_at')",
        },
        "text": ["item"],
        "filters": {
            "status": {"field": "status", "kind": "enum"},
            "total": {
                "field": "total",
                "kind": "range",
                "min_param": "tmin",
                "max_param": "tmax",
            },
            "quantity": {
                "field": "quantity",
                "kind": "range",
                "min_param": "qmin",
                "max_param": "qmax",
            },
        },
        "sorts": {
            "total": "total",
            "quantity": "quantity",
            "created_at": "created_at",
            "item": "item",
        },
        "default_sort": "created_at",
        "default_dir": "desc",
        "suggest": ["status", "item"],
    },
}


class SearchService:
    """
    Layer 8 - cached search over the cache_data table.

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
        page_size_max: int = _MAX_PAGE_SIZE
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

    # --------------------------------------------------
    # PUBLIC API
    # --------------------------------------------------

    def entities(self) -> dict:
        """What the UI needs to build its filter sidebars."""

        out = {}

        for name, spec in ENTITIES.items():

            out[name] = {
                "prefix": spec["prefix"],
                "filters": {
                    param: {
                        "kind": details["kind"],
                        "field": details["field"],
                        **(
                            {"min_param": details["min_param"],
                             "max_param": details["max_param"]}
                            if details["kind"] == "range" else {}
                        ),
                    }
                    for param, details in spec["filters"].items()
                },
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
            self.database.query, sql, tuple(args), 15
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
        """

        limit = max(1, min(int(limit or 50), 500))

        sql = "SELECT key, length(value) AS size FROM cache_data"

        args = []

        if prefix:

            sql += " WHERE substr(key, 1, length(?)) = ?"
            args.extend([prefix, prefix])

        sql += " ORDER BY key LIMIT ?"
        args.append(limit)

        result = self.circuit_breaker.call(
            self.database.query, sql, tuple(args), limit
        )

        headers = result["headers"]

        return {
            "prefix": prefix,
            "limit": limit,
            "truncated": result["truncated"],
            "keys": [
                dict(zip(headers, row)) for row in result["rows"]
            ],
        }

    # --------------------------------------------------
    # SQL
    # --------------------------------------------------

    def _run(self, spec: dict, normal: dict) -> dict:
        """Execute the count + page queries and build the envelope."""

        db_started = time.monotonic()

        where, args = self._where(spec, normal)

        count_sql = f"SELECT COUNT(*) AS n FROM cache_data WHERE {where}"

        counted = self.circuit_breaker.call(
            self.database.query, count_sql, tuple(args), 1
        )

        total = counted["rows"][0][0] if counted["rows"] else 0

        page = normal["page"]
        page_size = normal["page_size"]

        order = self._order(spec, normal)

        select = ", ".join(
            [f"{expr} AS {name}" for name, expr in spec["fields"].items()]
        )

        page_sql = (
            f"SELECT key, {spec['id_expr']} AS id, {select} "
            f"FROM cache_data WHERE {where} {order} LIMIT ? OFFSET ?"
        )

        page_args = args + [page_size, (page - 1) * page_size]

        fetched = self.circuit_breaker.call(
            self.database.query, page_sql, tuple(page_args), page_size
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

        # Keyword: every whitespace-separated term must match at
        # least one text field (AND across terms, OR across fields).
        for term in [t for t in normal["q"].split() if t]:

            options = [
                f"lower({spec['fields'][field]}) LIKE ?"
                for field in spec["text"]
            ]

            clauses.append("(" + " OR ".join(options) + ")")

            args.extend([f"%{term.lower()}%"] * len(options))

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
    # NORMALIZATION + KEYS
    # --------------------------------------------------

    def _spec(self, entity: str) -> dict:

        spec = ENTITIES.get(entity)

        if spec is None:

            raise ValueError(
                f"unknown entity: {entity} "
                f"(expected one of {sorted(ENTITIES)})"
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

                low = self._number(raw.get(details["min_param"]))
                high = self._number(raw.get(details["max_param"]))

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
    def _truthy(value) -> bool:

        if isinstance(value, bool):
            return value

        return str(value).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _ms(seconds: float) -> float:

        return round(seconds * 1000, 2)
