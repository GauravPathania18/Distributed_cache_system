/* =========================================================
   DCS web UI - vanilla JS single-page app
   No framework, no build step. Talks to the router API only.
   ========================================================= */

"use strict";

/* ---------------------------------------------------------
   Utilities
   --------------------------------------------------------- */

const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function money(v) { return v == null ? "—" : "₹" + Number(v).toFixed(2); }
function num(v)   { return v == null ? "—" : v; }

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) {
    const detail = body && body.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
      : (detail || res.statusText);
    throw new Error(`${res.status}: ${msg}`);
  }
  return body;
}

function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/* ---------------------------------------------------------
   State
   --------------------------------------------------------- */

const state = {
  view: "search",
  entity: "products",
  entities: {},              // from /search/entities
  query: {
    products: { q: "", category: "", pmin: "", pmax: "", rating: "", instock: false, sort: "rating", dir: "desc", page: 1, page_size: 12 },
    users:    { q: "", city: "", amin: "", amax: "", smin: "", smax: "", active: false, sort: "score", dir: "desc", page: 1, page_size: 12 },
    orders:   { q: "", status: "", tmin: "", tmax: "", qmin: "", qmax: "", sort: "created_at", dir: "desc", page: 1, page_size: 12 },
  },
  lastResult: null,
  dashTimer: null,
  lab: { running: false, results: [], cacheHits: 0, dbRuns: 0 },
};

/* ---------------------------------------------------------
   Tab routing
   --------------------------------------------------------- */

function showView(name) {
  state.view = name;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));

  if (state.dashTimer) { clearInterval(state.dashTimer); state.dashTimer = null; }
  if (name === "dashboard") { refreshDashboard(); state.dashTimer = setInterval(refreshDashboard, 3000); }
  if (name === "search" && !state.lastResult) runSearch();
}

/* ---------------------------------------------------------
   Cluster pill (top-right)
   --------------------------------------------------------- */

async function refreshClusterPill() {
  const dot = $("#clusterDot"), text = $("#clusterText");
  try {
    const s = await api("/cluster/status");
    const total = s.total_nodes, ready = (s.active_nodes || []).length;
    text.textContent = `${ready}/${total} nodes`;
    dot.className = "dot " + (ready === total ? "ok" : ready === 0 ? "bad" : "warn");
  } catch (e) {
    text.textContent = "router down";
    dot.className = "dot bad";
  }
}

/* ---------------------------------------------------------
   SEARCH VIEW
   --------------------------------------------------------- */

const ENTITY_LABEL = { products: "Products", users: "Users", orders: "Orders" };
const ENTITY_ICON  = { products: "🛍️", users: "👤", orders: "📦" };

function renderEntitySwitch() {
  const wrap = $("#entitySwitch");
  wrap.innerHTML = "";
  for (const name of Object.keys(state.entities)) {
    wrap.appendChild(el("button", {
      type: "button",
      class: "entity-btn" + (name === state.entity ? " active" : ""),
      onclick: () => { state.entity = name; state.query[name].page = 1; renderEntitySwitch(); renderFacets(); runSearch(); },
    }, `${ENTITY_ICON[name] || ""} ${ENTITY_LABEL[name] || name}`));
  }
}

const CATEGORY_CHIPS = ["electronics","books","clothing","toys","garden","sports","grocery","furniture","beauty","tools"];
const CITY_CHIPS = ["Berlin","Mumbai","Tokyo","Lisbon","Nairobi","Toronto","Lyon","Osaka","Bogota","Warsaw","Cairo","Seoul"];
const STATUS_CHIPS = ["pending","shipped","delivered","cancelled","refunded"];

function renderFacets() {
  const meta = state.entities[state.entity];
  if (!meta) return;
  const q = state.query[state.entity];
  const box = $("#facets");
  box.innerHTML = "";

  const group = (title, ...kids) => {
    const g = el("div", { class: "facet-group" }, [el("div", { class: "facet-title" }, title), ...kids]);
    box.appendChild(g);
  };

  if (state.entity === "products") {
    group("Category", el("div", { class: "chips" },
      [chip("", "All"), ...CATEGORY_CHIPS.map((c) => chip(c, c))].map((b) => {
        b.classList.toggle("active", (q.category || "") === b.dataset.val);
        b.onclick = () => { q.category = b.dataset.val; q.page = 1; renderFacets(); runSearch(); };
        return b;
      })));
    group("Price range", rangeRow(q, "pmin", "pmax", "min ₹", "max ₹"));
    group("Minimum rating", selectRow(q, "rating", [["", "any"], ["3", "3+"], ["4", "4+"], ["4.5", "4.5+"]]));
    group("Availability", toggleRow(q, "instock", "In stock only"));
  } else if (state.entity === "users") {
    group("City", el("div", { class: "chips" },
      [chip("", "All"), ...CITY_CHIPS.map((c) => chip(c, c))].map((b) => {
        b.classList.toggle("active", (q.city || "") === b.dataset.val);
        b.onclick = () => { q.city = b.dataset.val; q.page = 1; renderFacets(); runSearch(); };
        return b;
      })));
    group("Age range", rangeRow(q, "amin", "amax", "min", "max"));
    group("Score range", rangeRow(q, "smin", "smax", "min", "max"));
    group("Status", toggleRow(q, "active", "Active only"));
  } else {
    group("Order status", el("div", { class: "chips" },
      [chip("", "All"), ...STATUS_CHIPS.map((c) => chip(c, c))].map((b) => {
        b.classList.toggle("active", (q.status || "") === b.dataset.val);
        b.onclick = () => { q.status = b.dataset.val; q.page = 1; renderFacets(); runSearch(); };
        return b;
      })));
    group("Total range", rangeRow(q, "tmin", "tmax", "min ₹", "max ₹"));
    group("Quantity range", rangeRow(q, "qmin", "qmax", "min", "max"));
  }

  // Sort control (shared)
  const sortSel = el("select", {
    onchange: (e) => { q.sort = e.target.value; q.page = 1; runSearch(); },
  }, (meta.sorts || []).map((s) =>
    el("option", { value: s, ...(s === q.sort ? { selected: "" } : {}) }, s)));

  const dirSel = el("select", {
    onchange: (e) => { q.dir = e.target.value; q.page = 1; runSearch(); },
  }, [
    el("option", { value: "desc", ...(q.dir === "desc" ? { selected: "" } : {}) }, "desc"),
    el("option", { value: "asc",  ...(q.dir === "asc"  ? { selected: "" } : {}) }, "asc"),
  ]);

  group("Sort by", el("div", { class: "facet-row" }, [sortSel, dirSel]));

  function chip(val, label) {
    return el("button", { type: "button", class: "chip", "data-val": val }, label);
  }
  function rangeRow(obj, minK, maxK, minPh, maxPh) {
    const a = el("input", { type: "number", placeholder: minPh, value: obj[minK] || "", oninput: debounce((e) => { obj[minK] = e.target.value; obj.page = 1; runSearch(); }, 350) });
    const b = el("input", { type: "number", placeholder: maxPh, value: obj[maxK] || "", oninput: debounce((e) => { obj[maxK] = e.target.value; obj.page = 1; runSearch(); }, 350) });
    return el("div", { class: "facet-row" }, [a, b]);
  }
  function selectRow(obj, key, options) {
    return el("div", { class: "facet-row" }, [
      el("select", { onchange: (e) => { obj[key] = e.target.value; obj.page = 1; runSearch(); } },
        options.map(([v, l]) => el("option", { value: v, ...(obj[key] === v ? { selected: "" } : {}) }, l))),
    ]);
  }
  function toggleRow(obj, key, label) {
    const cb = el("input", { type: "checkbox", ...(obj[key] ? { checked: "" } : {}), onchange: (e) => { obj[key] = e.target.checked; obj.page = 1; runSearch(); } });
    return el("label", { class: "toggle" }, [cb, label]);
  }
}

function buildQuery(entity) {
  const q = { ...state.query[entity] };
  const params = new URLSearchParams();
  if (q.q) params.set("q", q.q);
  for (const [k, v] of Object.entries(q)) {
    if (["q", "sort", "dir", "page", "page_size"].includes(k)) continue;
    if (v === "" || v === false || v == null) continue;
    params.set(k, v === true ? "true" : v);
  }
  params.set("sort", q.sort);
  params.set("dir", q.dir);
  params.set("page", q.page);
  params.set("page_size", q.page_size);
  return params;
}

async function runSearch() {
  const entity = state.entity;
  const statusBox = $("#cacheStatus"), metaBox = $("#resultsMeta"), grid = $("#results");
  statusBox.innerHTML = ""; metaBox.textContent = "Searching…"; grid.innerHTML = "";
  try {
    const result = await api(`/search/${entity}?${buildQuery(entity)}`);
    state.lastResult = result;
    renderCacheStatus(result);
    renderMeta(result);
    renderResults(result);
    renderPager(result);
  } catch (e) {
    statusBox.innerHTML = "";
    metaBox.textContent = "";
    grid.innerHTML = "";
    $("#pager").innerHTML = "";
    grid.appendChild(el("div", { class: "error-box" }, `Search failed — ${e.message}`));
  }
}

function renderCacheStatus(r) {
  const box = $("#cacheStatus");
  const hit = r.source === "cache";
  box.className = "cache-status " + (hit ? "hit" : "miss");
  box.innerHTML = "";
  box.appendChild(el("span", { class: "badge " + r.source }, hit ? "⚡ CACHE" : "🗄 DATABASE"));
  box.appendChild(el("span", {}, hit
    ? `Served from cache — 0 SQL queries issued. Node ${r.node || "?"}`
    : `Ran SQL (took ${r.db_took_ms ?? "?"} ms) — result set now cached & replicated to your nodes.`));
  box.appendChild(el("span", { class: "muted" }, `this request: ${r.took_ms} ms`));
  if (r.cache_key) box.appendChild(el("div", { class: "cache-key" }, `cache key:  ${r.cache_key}`));
}

function renderMeta(r) {
  const first = r.page * r.page_size - r.items.length + 1;
  const last  = r.page * r.page_size - r.page_size + r.items.length;
  $("#resultsMeta").textContent =
    `${r.total.toLocaleString()} result(s) · showing ${first}–${last} · page ${r.page} of ${r.page_count}`;
}

function renderResults(r) {
  const grid = $("#results");
  grid.innerHTML = "";
  if (!r.items.length) { grid.appendChild(el("div", { class: "empty" }, "No results — try a broader search.")); return; }
  for (const item of r.items) grid.appendChild(cardFor(r.entity, item));
}

function cardFor(entity, it) {
  const kids = [el("div", { class: "card-top" }, [
    el("span", { class: "card-id" }, it.id ?? it.key ?? ""),
  ])];

  if (entity === "products") {
    kids.push(el("div", { class: "card-title" }, it.name));
    kids.push(el("div", {}, [
      el("span", { class: "price" }, money(it.price)),
      el("span", { class: "rating" }, `  ★ ${num(it.rating)}`),
    ]));
    kids.push(el("div", { class: "tags" }, [
      el("span", { class: "tag" }, it.category),
      el("span", { class: "tag " + (it.stock > 0 ? "stock-ok" : "stock-out") },
        it.stock > 0 ? `${it.stock} in stock` : "out of stock"),
    ]));
  } else if (entity === "users") {
    kids.push(el("div", { class: "card-title" }, it.name));
    kids.push(el("div", { class: "kv" }, [el("span", { class: "k" }, "email"), el("span", { class: "v" }, it.email)]));
    kids.push(el("div", { class: "kv" }, [el("span", { class: "k" }, "city"), el("span", { class: "v" }, it.city)]));
    kids.push(el("div", { class: "kv" }, [el("span", { class: "k" }, "age"), el("span", { class: "v" }, num(it.age))]));
    kids.push(el("div", { class: "kv" }, [el("span", { class: "k" }, "score"), el("span", { class: "v" }, num(it.score))]));
    kids.push(el("div", { class: "tags" }, [
      el("span", { class: "tag " + (it.active ? "stock-ok" : "stock-out") }, it.active ? "active" : "inactive"),
    ]));
  } else {
    kids.push(el("div", { class: "card-title" }, it.item));
    kids.push(el("div", {}, [
      el("span", { class: "price" }, money(it.total)),
      el("span", { class: "rating" }, `  ×${num(it.quantity)}`),
    ]));
    kids.push(el("div", { class: "kv" }, [el("span", { class: "k" }, "user"), el("span", { class: "v" }, num(it.user_id))]));
    kids.push(el("div", { class: "kv" }, [el("span", { class: "k" }, "created"), el("span", { class: "v" }, it.created_at)]));
    kids.push(el("div", { class: "tags" }, [el("span", { class: "tag" }, it.status)]));
  }

  const fullKey = it.key || `${state.entity === "products" ? "product" : state.entity === "users" ? "user" : "order"}:${it.id}`;
  kids.push(el("div", { class: "card-actions" }, [
    el("button", { type: "button", class: "link-btn", onclick: () => inspectKey(fullKey) }, "Inspect key →"),
  ]));

  return el("div", { class: "card" }, kids);
}

function renderPager(r) {
  const box = $("#pager");
  box.innerHTML = "";
  const go = (p) => { state.query[r.entity].page = p; runSearch(); };
  box.appendChild(el("button", { class: "page-btn", disabled: r.page <= 1 ? "" : null, onclick: () => go(r.page - 1) }, "‹ Prev"));
  const window = 5;
  let start = Math.max(1, r.page - window), end = Math.min(r.page_count, r.page + window);
  for (let p = start; p <= end; p++) {
    box.appendChild(el("button", { class: "page-btn" + (p === r.page ? " active" : ""), onclick: () => go(p) }, String(p)));
  }
  box.appendChild(el("button", { class: "page-btn", disabled: r.page >= r.page_count ? "" : null, onclick: () => go(r.page + 1) }, "Next ›"));
  box.appendChild(el("span", { class: "muted", style: "margin-left:10px" }, `${r.page_size}/page`));
  const sizeSel = el("select", { onchange: (e) => { state.query[r.entity].page_size = +e.target.value; state.query[r.entity].page = 1; runSearch(); } },
    [12, 24, 48].map((n) => el("option", { value: n, ...(n === r.page_size ? { selected: "" } : {}) }, `${n} / page`)));
  box.appendChild(sizeSel);
}

/* ---------------------------------------------------------
   DASHBOARD VIEW
   --------------------------------------------------------- */

async function refreshDashboard() {
  try {
    const d = await api("/dashboard/aggregate");
    renderStatCards(d);
    renderHitRateBars(d);
    renderDonut(d);
    renderHotKeys(d);
    renderNodeStates(d);
    renderBreakerRepl(d);
  } catch (e) {
    $("#statCards").innerHTML = `<div class="error-box">Dashboard failed — ${esc(e.message)}</div>`;
  }
}

function renderStatCards(d) {
  const t = d.telemetry || {};
  const a = d.aggregate || {};
  const box = $("#statCards");
  box.innerHTML = "";
  const card = (label, value, sub, cls) => el("div", { class: "stat " + (cls || "") }, [
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, value),
    el("div", { class: "sub" }, sub || ""),
  ]);
  box.appendChild(card("DB load reduction", `${t.db_load_reduction_pct ?? 0}%`,
    `${(t.db_reads_avoided ?? 0).toLocaleString()} reads served without SQL`, "good"));
  box.appendChild(card("SQL queries issued", (t.db_reads ?? 0).toLocaleString(),
    `${(t.search_database ?? 0)} from search · ${t.gets_database ?? 0} point reads`));
  box.appendChild(card("Reads avoided", (t.db_reads_avoided ?? 0).toLocaleString(),
    `${(t.search_cache ?? 0)} search · ${t.gets_cache ?? 0} point`, "accent"));
  box.appendChild(card("Replication sequence", d.replication?.current_sequence ?? 0,
    `${d.replication?.log_size ?? 0} log entries`));
  box.appendChild(card("Cache tier hit rate", `${a.hit_rate_pct ?? 0}%`,
    `${(a.hits ?? 0).toLocaleString()} hits / ${(a.misses ?? 0).toLocaleString()} misses (lifetime)`));
  box.appendChild(card("Live nodes", `${d.cluster?.active_nodes?.length ?? 0}/${d.cluster?.total_nodes ?? 0}`,
    d.circuit_breaker ? `breaker ${d.circuit_breaker.state}` : ""));
}

function renderHitRateBars(d) {
  const box = $("#hitRateBars");
  box.innerHTML = "";
  const nodes = d.nodes || {};
  for (const [url, snap] of Object.entries(nodes)) {
    if (snap.error) {
      box.appendChild(el("div", { class: "bar-row" }, [
        el("span", { class: "muted" }, url.replace("http://", "")),
        el("span", { class: "muted" }, `unreachable (${snap.error})`),
        el("span", { class: "bar-val" }, "—"),
      ]));
      continue;
    }
    const L = snap.lifetime || {};
    const pct = L.hit_rate ?? 0;
    box.appendChild(el("div", { class: "bar-row" }, [
      el("span", { class: "node-url" }, url.replace("http://", "")),
      el("div", { class: "bar-track" }, el("div", { class: "bar-fill", style: `width:${pct}%` })),
      el("span", { class: "bar-val" }, `${pct.toFixed(1)}%`),
    ]));
  }
}

function renderDonut(d) {
  const t = d.telemetry || {};
  const cache = t.cache_reads ?? 0, db = t.db_reads ?? 0;
  const total = cache + db || 1;
  const cacheDeg = (cache / total) * 360;
  const box = $("#sourceDonut");
  box.innerHTML = "";
  box.appendChild(el("div", {
    html: `<svg width="130" height="130" viewBox="0 0 42 42">
      <circle cx="21" cy="21" r="15.9" fill="none" stroke="#d29922" stroke-width="7"></circle>
      <circle cx="21" cy="21" r="15.9" fill="none" stroke="#2ea043" stroke-width="7"
        stroke-dasharray="${cacheDeg} ${360 - cacheDeg}" stroke-dashoffset="25"></circle>
    </svg>`,
  }));
  box.appendChild(el("div", { class: "legend" }, [
    el("div", {}, [el("span", { class: "swatch", style: "background:#2ea043" }), el("span", {}, `Cache (${cache.toLocaleString()})`)]),
    el("div", {}, [el("span", { class: "swatch", style: "background:#d29922" }), el("span", {}, `Database (${db.toLocaleString()})`)]),
    el("div", { class: "muted" }, `hit rate ${t.hit_rate_pct ?? 0}%`),
  ]));
}

function renderHotKeys(d) {
  const table = $("#hotKeysTable");
  const keys = d.top_keys || [];
  table.innerHTML = "<tr><th>#</th><th>Key</th><th>Accesses</th></tr>" +
    (keys.length ? keys.map(([k, n], i) =>
      `<tr><td>${i + 1}</td><td class="name">${esc(k)}</td><td>${n}</td></tr>`).join("")
      : `<tr><td colspan="3" class="muted">no traffic yet</td></tr>`);
}

function renderNodeStates(d) {
  const box = $("#nodeStates");
  const states = d.cluster?.node_states || {};
  box.innerHTML = "";
  for (const [url, st] of Object.entries(states)) {
    box.appendChild(el("div", { class: "node-row" }, [
      el("span", { class: "node-url" }, url.replace("http://", "")),
      el("span", { class: "state-pill state-" + st }, st),
    ]));
  }
}

function renderBreakerRepl(d) {
  const box = $("#breakerRepl");
  const cb = d.circuit_breaker || {};
  const rp = d.replication || {};
  box.innerHTML = "";
  box.appendChild(el("div", { class: "node-row" }, [
    el("span", {}, "Circuit breaker"),
    el("span", { class: "state-pill " + (cb.state === "closed" ? "state-ready" : cb.state === "open" ? "state-failed" : "state-unhealthy") }, cb.state || "?"),
  ]));
  box.appendChild(el("div", { class: "kv" }, [el("span", { class: "k" }, "failures"), el("span", { class: "v" }, `${cb.failure_count ?? 0}/${cb.failure_threshold ?? "-"}`)]));
  box.appendChild(el("div", { class: "node-row" }, [
    el("span", {}, "Replication"),
    el("span", { class: "state-pill " + (rp.enabled ? "state-ready" : "state-failed") }, rp.enabled ? "enabled" : "off"),
  ]));
  box.appendChild(el("div", { class: "kv" }, [el("span", { class: "k" }, "sequence"), el("span", { class: "v" }, rp.current_sequence ?? 0)]));
  box.appendChild(el("div", { class: "kv" }, [el("span", { class: "k" }, "log"), el("span", { class: "v" }, `${rp.log_size ?? 0}/${rp.log_capacity ?? 0}`)]));
  box.appendChild(el("div", { class: "kv" }, [el("span", { class: "k" }, "failovers"), el("span", { class: "v" }, (rp.failovers || []).length)]));
}

/* ---------------------------------------------------------
   INSPECTOR VIEW
   --------------------------------------------------------- */

function inspectKey(key) {
  $("#inspectKey").value = key;
  showView("inspector");
  runInspect();
}

async function runInspect() {
  const key = $("#inspectKey").value.trim();
  const box = $("#inspectCompare");
  box.innerHTML = "";
  if (!key) return;

  const enc = encodeURIComponent(key);

  const card = (title, promise) => {
    const c = el("div", { class: "compare-card" }, [el("h4", {}, title), el("div", { class: "spinner" }, "loading…")]);
    box.appendChild(c);
    return promise.then((data) => {
      c.innerHTML = "";
      c.appendChild(el("h4", {}, title));
      c.appendChild(el("pre", { class: "json" }, JSON.stringify(data, null, 2)));
    }).catch((e) => {
      c.innerHTML = "";
      c.appendChild(el("h4", {}, title));
      c.appendChild(el("div", { class: "error-box" }, e.message));
    });
  };

  card(`GET /cache/${key}  (via cache layer)`, api(`/cache/${enc}`));
  card(`GET /db/${key}  (bypasses cache)`, api(`/db/${enc}`));
}

async function runBrowse() {
  const prefix = $("#browsePrefix").value.trim();
  const limit = $("#browseLimit").value || 25;
  const out = $("#browseOut");
  out.innerHTML = `<div class="spinner">loading…</div>`;
  try {
    const r = await api(`/browse/prefix?prefix=${encodeURIComponent(prefix)}&limit=${limit}`);
    out.innerHTML = `<table class="table"><tr><th>Key</th><th>Value size (bytes)</th></tr>` +
      (r.keys.length ? r.keys.map((k) =>
        `<tr><td>${esc(k.key)}</td><td>${k.size}</td></tr>`).join("")
        : `<tr><td colspan="2" class="muted">no keys match</td></tr>`) + `</table>` +
      (r.truncated ? `<div class="muted">truncated at ${r.limit} — raise the limit for more</div>` : "");
  } catch (e) {
    out.innerHTML = `<div class="error-box">${esc(e.message)}</div>`;
  }
}

/* ---------------------------------------------------------
   CACHE LAB VIEW
   --------------------------------------------------------- */

async function runLab(event) {
  event.preventDefault();
  if (state.lab.running) return;

  const query = $("#labQuery").value.trim();
  const count = Math.max(1, Math.min(500, +$("#labCount").value || 40));
  const delay = Math.max(0, Math.min(1000, +$("#labDelay").value || 0));
  const btn = $("#labRun");

  state.lab.running = true;
  state.lab.results = [];
  state.lab.cacheHits = 0;
  state.lab.dbRuns = 0;
  btn.disabled = true;
  btn.textContent = "Running…";
  $("#labFeed").innerHTML = "";

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  for (let i = 1; i <= count; i++) {
    const started = performance.now();
    try {
      const r = await api(`/search/products?q=${encodeURIComponent(query)}&sort=price&dir=asc&page_size=5`);
      const ms = performance.now() - started;
      const hit = r.source === "cache";
      if (hit) state.lab.cacheHits++; else state.lab.dbRuns++;
      state.lab.results.push({ i, hit, ms, source: r.source });
      appendLabLine(i, r.source, ms);
      renderLabStats(count);
      renderLabChart();
    } catch (e) {
      appendLabLine(i, "error", 0, e.message);
      break;
    }
    if (delay) await sleep(delay);
  }

  state.lab.running = false;
  btn.disabled = false;
  btn.textContent = "Run";
}

function appendLabLine(i, source, ms, err) {
  const feed = $("#labFeed");
  const line = el("div", { class: "lab-line" }, [
    el("span", { class: "n" }, `#${i}`),
    el("span", { class: "src " + source }, err ? "ERROR" : source.toUpperCase()),
    el("span", {}, err || `served in ${ms.toFixed(1)} ms`),
    el("span", { class: "ms" }, ""),
  ]);
  feed.appendChild(line);
  feed.scrollTop = feed.scrollHeight;
}

function renderLabStats(count) {
  const { cacheHits, dbRuns, results } = state.lab;
  const total = cacheHits + dbRuns || 1;
  const pct = ((cacheHits / total) * 100).toFixed(1);
  const avgCache = avg(results.filter((r) => r.hit).map((r) => r.ms));
  const avgDb = avg(results.filter((r) => !r.hit).map((r) => r.ms));
  const box = $("#labStats");
  box.innerHTML = "";
  const card = (label, value, sub, cls) => el("div", { class: "stat " + (cls || "") }, [
    el("div", { class: "label" }, label),
    el("div", { class: "value" }, value),
    el("div", { class: "sub" }, sub || ""),
  ]);
  box.appendChild(card("Cache hits", cacheHits, "served with 0 SQL", "good"));
  box.appendChild(card("SQL queries", dbRuns, "database actually touched"));
  box.appendChild(card("Load reduction", `${pct}%`, "of all requests", "accent"));
  box.appendChild(card("Avg cache", avgCache == null ? "—" : `${avgCache.toFixed(1)} ms`, "per cached request"));
  box.appendChild(card("Avg database", avgDb == null ? "—" : `${avgDb.toFixed(1)} ms`, "per SQL request"));

  function avg(arr) { return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null; }
}

function renderLabChart() {
  const { results } = state.lab;
  const box = $("#labChart");
  if (!results.length) { box.innerHTML = ""; return; }

  const W = 640, H = 180, pad = 28;
  const maxN = results.length;
  const plotW = W - pad * 2, plotH = H - pad * 2;
  const x = (n) => pad + (maxN <= 1 ? plotW / 2 : ((n - 1) / (maxN - 1)) * plotW);
  const y = (v) => H - pad - (v / maxN) * plotH;

  let cacheLine = "", dbLine = "";
  let c = 0, d = 0;
  results.forEach((r, idx) => {
    if (r.hit) c++; else d++;
    cacheLine += `${idx === 0 ? "M" : "L"}${x(idx + 1).toFixed(1)},${y(c).toFixed(1)} `;
    dbLine += `${idx === 0 ? "M" : "L"}${x(idx + 1).toFixed(1)},${y(d).toFixed(1)} `;
  });

  box.innerHTML = `
  <svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px">
    <line x1="${pad}" y1="${H - pad}" x2="${W - pad}" y2="${H - pad}" stroke="#2d3548"/>
    <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${H - pad}" stroke="#2d3548"/>
    <text x="${pad}" y="${H - 8}" fill="#8b98a9" font-size="10">request #1</text>
    <text x="${W - pad}" y="${H - 8}" fill="#8b98a9" font-size="10" text-anchor="end">#${maxN}</text>
    <text x="${pad - 6}" y="${pad + 4}" fill="#8b98a9" font-size="10" text-anchor="end">${maxN}</text>
    <text x="${pad - 6}" y="${H - pad + 4}" fill="#8b98a9" font-size="10" text-anchor="end">0</text>
    <path d="${cacheLine}" fill="none" stroke="#2ea043" stroke-width="2.5"/>
    <path d="${dbLine}" fill="none" stroke="#d29922" stroke-width="2.5"/>
  </svg>
  <div class="legend" style="display:flex;gap:18px;justify-content:center;margin-top:6px">
    <span style="color:#2ea043">■ cumulative cache hits (${c})</span>
    <span style="color:#d29922">■ cumulative SQL queries (${d})</span>
  </div>`;
}

async function resetLabCounters() {
  try { await api("/dashboard/telemetry/reset", { method: "POST" }); } catch (_) {}
  state.lab = { running: false, results: [], cacheHits: 0, dbRuns: 0 };
  $("#labStats").innerHTML = "";
  $("#labChart").innerHTML = "";
  $("#labFeed").innerHTML = "";
}

/* ---------------------------------------------------------
   Init
   --------------------------------------------------------- */

async function init() {
  // Tabs
  $("#tabs").addEventListener("click", (e) => {
    const btn = e.target.closest(".tab");
    if (btn) showView(btn.dataset.view);
  });

  // Search form
  $("#searchForm").addEventListener("submit", (e) => {
    e.preventDefault();
    state.query[state.entity].q = $("#q").value.trim();
    state.query[state.entity].page = 1;
    runSearch();
  });
  $("#clearBtn").addEventListener("click", () => {
    $("#q").value = "";
    const q = state.query[state.entity];
    for (const k of Object.keys(q)) if (!["sort", "dir", "page", "page_size"].includes(k)) q[k] = typeof q[k] === "boolean" ? false : "";
    q.page = 1;
    renderFacets();
    runSearch();
  });
  $("#q").addEventListener("input", debounce((e) => {
    state.query[state.entity].q = e.target.value.trim();
    state.query[state.entity].page = 1;
    runSearch();
  }, 400));

  // Inspector
  $("#inspectForm").addEventListener("submit", (e) => { e.preventDefault(); runInspect(); });
  $("#inspectDelete").addEventListener("click", async () => {
    const key = $("#inspectKey").value.trim();
    if (!key) return;
    if (!confirm(`Delete "${key}" from the database and all cache nodes?`)) return;
    try { await api(`/cache/${encodeURIComponent(key)}`, { method: "DELETE" }); runInspect(); }
    catch (e) { alert("Delete failed: " + e.message); }
  });
  $("#browseForm").addEventListener("submit", (e) => { e.preventDefault(); runBrowse(); });

  // Lab
  $("#labForm").addEventListener("submit", runLab);
  $("#labReset").addEventListener("click", resetLabCounters);

  // Load entity metadata, then paint
  try {
    const meta = await api("/search/entities");
    state.entities = meta.entities || {};
  } catch (_) {
    state.entities = { products: { sorts: ["rating", "price"], default_sort: "rating", default_dir: "desc" } };
  }

  renderEntitySwitch();
  renderFacets();
  refreshClusterPill();
  setInterval(refreshClusterPill, 5000);
  runSearch();
}

document.addEventListener("DOMContentLoaded", init);
