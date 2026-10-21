/* =========================================================
   DCS web UI - vanilla JS single-page app
   No framework, no build step. Talks to the router API only.

   Structure: this file holds the shared utilities, the tab/view
   registry and the Search + Dashboard views. The other four views
   live in views/*.js and register themselves through
   registerView(name, {show, hide}).
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

function fmtNum(v) {
  if (v == null) return "—";
  if (typeof v !== "number") return String(v);
  return Number.isInteger(v) ? v.toLocaleString() : String(v);
}

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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Shared feedback line: a .verdict div + kind (ok | warn | err). */
function setVerdict(box, msg, kind) {
  box.className = "verdict " + (kind || "ok");
  box.textContent = msg || "";
  if (!msg) box.className = "verdict";
}

/* ---------------------------------------------------------
   State
   --------------------------------------------------------- */

const state = {
  view: "search",
  entity: null,               // current dataset entity (first by default)
  entities: {},               // from /search/entities (dataset registry)
  query: {},                  // per-entity query state, built from metadata
  lastResult: null,
};

/* ---------------------------------------------------------
   View registry (tabs call showView; views register hooks)
   --------------------------------------------------------- */

const VIEWS = {};

function registerView(name, hooks = {}) {
  VIEWS[name] = hooks;
}

function showView(name) {
  const prev = VIEWS[state.view];
  if (prev && prev.hide) prev.hide();

  state.view = name;
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));

  const hooks = VIEWS[name];
  if (hooks && hooks.show) hooks.show();
}

registerView("search", {
  show() { if (!state.lastResult) runSearch(); },
});

registerView("dashboard", {
  show() { refreshDashboard(); state.dashTimer = setInterval(refreshDashboard, 3000); },
  hide() { if (state.dashTimer) { clearInterval(state.dashTimer); state.dashTimer = null; } },
});

state.dashTimer = null;

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
    ENTITY METADATA (shared by Search, Lab, Datasets)
   --------------------------------------------------------- */

async function reloadEntities() {
  try {
    const meta = await api("/search/entities");
    state.entities = meta.entities || {};
  } catch (_) {
    state.entities = {};
  }

  const names = Object.keys(state.entities);

  // Drop the selection when its dataset disappeared.
  if (!names.includes(state.entity)) state.entity = names[0] || null;

  names.forEach(ensureQuery);

  renderEntitySwitch();
  renderFacets();

  return state.entities;
}

/* ---------------------------------------------------------
    SEARCH VIEW
   --------------------------------------------------------- */

const ENTITY_ICON  = { products: "🛍️", users: "👤", orders: "📦" };
const FALLBACK_LABEL = { products: "Products", users: "Users", orders: "Orders" };

const TITLE_PRIORITY = ["name", "title", "names", "label", "restaurant_name", "movie"];

function prettify(name) {
  return String(name ?? "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function entityLabel(name) {
  const meta = state.entities[name] || {};
  return meta.label || FALLBACK_LABEL[name] || prettify(name);
}

function defaultQuery(meta) {
  const q = {
    q: "",
    sort: meta.default_sort || (meta.sorts || [])[0] || "",
    dir: meta.default_dir || "asc",
    page: 1,
    page_size: 12,
  };
  for (const [param, filter] of Object.entries(meta.filters || {})) {
    if (filter.kind === "range") {
      q[filter.min_param] = "";
      q[filter.max_param] = "";
    } else if (filter.kind === "bool") {
      q[param] = false;
    } else {
      q[param] = "";
    }
  }
  return q;
}

function ensureQuery(entity) {
  if (!state.query[entity]) {
    state.query[entity] = defaultQuery(state.entities[entity] || {});
  }
  return state.query[entity];
}

function renderEntitySwitch() {
  const wrap = $("#entitySwitch");
  wrap.innerHTML = "";
  for (const name of Object.keys(state.entities)) {
    const label = entityLabel(name);
    const shown = label.length > 26 ? label.slice(0, 25) + "…" : label;
    wrap.appendChild(el("button", {
      type: "button",
      class: "entity-btn" + (name === state.entity ? " active" : ""),
      title: label,
      onclick: () => {
        state.entity = name;
        ensureQuery(name);
        renderEntitySwitch();
        renderFacets();
        runSearch();
      },
    }, [ENTITY_ICON[name] || null, shown].filter(Boolean).join(" ")));
  }
}

function renderFacets() {
  const box = $("#facets");
  box.innerHTML = "";
  if (!state.entity) return;
  const meta = state.entities[state.entity];
  if (!meta) return;
  const q = ensureQuery(state.entity);

  const group = (title, ...kids) => {
    const g = el("div", { class: "facet-group" }, [el("div", { class: "facet-title" }, title), ...kids]);
    box.appendChild(g);
  };

  const filters = Object.entries(meta.filters || {});

  for (const [param, filter] of filters) {
    const title = prettify(filter.field);

    if (filter.kind === "enum") {
      const values = (filter.values || []).slice(0, 14);
      if (!values.length) continue;
      group(title, el("div", { class: "chips" },
        [chip("", "All"), ...values.map((v) => chip(v, v))].map((b) => {
          b.classList.toggle("active", (q[param] || "") === b.dataset.val);
          b.onclick = () => { q[param] = b.dataset.val; q.page = 1; renderFacets(); runSearch(); };
          return b;
        })));
    } else if (filter.kind === "range") {
      const isNumber = (filter.dtype || "number") === "number";
      group(title, rangeRow(q, filter.min_param, filter.max_param,
        isNumber ? "min" : "from", isNumber ? "max" : "to", isNumber));
    } else if (filter.kind === "bool") {
      group(title, toggleRow(q, param, "Yes only"));
    } else if (filter.kind === "min") {
      group(title, numberRow(q, param, "min"));
    }
  }

  if (!filters.length) {
    group("Filters", el("div", { class: "muted" }, "none — use keyword search & sort"));
  }

  // Sort control (shared)
  const sortSel = el("select", {
    onchange: (e) => { q.sort = e.target.value; q.page = 1; runSearch(); },
  }, (meta.sorts || []).map((s) =>
    el("option", { value: s, ...(s === q.sort ? { selected: "" } : {}) }, prettify(s))));

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
  function rangeRow(obj, minK, maxK, minPh, maxPh, isNumber) {
    const type = isNumber ? "number" : "text";
    const a = el("input", { type, placeholder: minPh, value: obj[minK] || "", oninput: debounce((e) => { obj[minK] = e.target.value; obj.page = 1; runSearch(); }, 350) });
    const b = el("input", { type, placeholder: maxPh, value: obj[maxK] || "", oninput: debounce((e) => { obj[maxK] = e.target.value; obj.page = 1; runSearch(); }, 350) });
    return el("div", { class: "facet-row" }, [a, b]);
  }
  function numberRow(obj, key, ph) {
    return el("div", { class: "facet-row" }, [
      el("input", { type: "number", placeholder: ph, value: obj[key] || "", oninput: debounce((e) => { obj[key] = e.target.value; obj.page = 1; runSearch(); }, 350) }),
    ]);
  }
  function toggleRow(obj, key, label) {
    const cb = el("input", { type: "checkbox", ...(obj[key] ? { checked: "" } : {}), onchange: (e) => { obj[key] = e.target.checked; obj.page = 1; runSearch(); } });
    return el("label", { class: "toggle" }, [cb, label]);
  }
}

function renderEmptyDatasets() {
  const grid = $("#results");
  $("#cacheStatus").innerHTML = "";
  $("#resultsMeta").textContent = "";
  $("#pager").innerHTML = "";
  grid.innerHTML = "";
  grid.appendChild(el("div", { class: "empty" }, [
    el("div", {}, "No datasets yet."),
    el("div", { class: "muted", style: "margin-top:6px" },
      "Create one from the Datasets tab (a few clicks), or import a file:"),
    el("pre", { class: "json" }, "python import_dataset.py --file datasets/your_file.csv\npython import_dataset.py --seed"),
  ]));
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
  if (!entity || !state.entities[entity]) { renderEmptyDatasets(); return; }
  ensureQuery(entity);
  const statusBox = $("#cacheStatus"), metaBox = $("#resultsMeta"), grid = $("#results");
  statusBox.innerHTML = ""; metaBox.textContent = "Searching…"; grid.innerHTML = "";
  try {
    const result = await api(`/search/${encodeURIComponent(entity)}?${buildQuery(entity)}`);
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
  const meta = state.entities[entity] || {};
  const columns = meta.columns || Object.keys(meta.fields || {});
  const types = meta.fields || {};

  const kids = [el("div", { class: "card-top" }, [
    el("span", { class: "card-id" }, String(it.id ?? it.key ?? "")),
  ])];

  const lower = columns.map((c) => c.toLowerCase());
  let titleIndex = lower.findIndex((c) => TITLE_PRIORITY.includes(c));
  if (titleIndex < 0) titleIndex = columns.findIndex((c) => types[c] === "string");
  const titleField = titleIndex >= 0 ? columns[titleIndex] : null;

  if (titleField && it[titleField] != null && it[titleField] !== "") {
    kids.push(el("div", { class: "card-title" }, String(it[titleField])));
  }

  let shown = 0;

  for (let i = 0; i < columns.length && shown < 6; i++) {

    const column = columns[i];
    if (column === titleField) continue;

    const value = it[column];
    if (value === null || value === undefined || value === "") continue;

    shown++;

    if (typeof value === "boolean") {
      kids.push(el("div", { class: "tags" }, [
        el("span", { class: "tag " + (value ? "stock-ok" : "stock-out") },
          value ? "yes" : "no"),
      ]));
    } else if (typeof value === "number") {
      kids.push(el("div", { class: "kv" }, [
        el("span", { class: "k" }, prettify(column)),
        el("span", { class: "v" }, fmtNum(value)),
      ]));
    } else {
      const text = String(value);
      kids.push(el("div", { class: "kv" }, [
        el("span", { class: "k" }, prettify(column)),
        el("span", { class: "v", title: text }, text.length > 90 ? text.slice(0, 89) + "…" : text),
      ]));
    }
  }

  const fullKey = it.key || `${meta.prefix || ""}${it.id ?? ""}`;
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
    `${t.search_cache ?? 0} search · ${t.gets_cache ?? 0} point`, "accent"));
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
  if (!Object.keys(states).length) {
    box.appendChild(el("div", { class: "muted" }, "no nodes — scale up in the Cluster tab"));
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
   Init (views/*.js have registered themselves by now)
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
    if (!state.entity) return;
    const q = ensureQuery(state.entity);
    q.q = $("#q").value.trim();
    q.page = 1;
    runSearch();
  });
  $("#clearBtn").addEventListener("click", () => {
    $("#q").value = "";
    if (!state.entity) return;
    const q = ensureQuery(state.entity);
    for (const k of Object.keys(q)) if (!["sort", "dir", "page", "page_size"].includes(k)) q[k] = typeof q[k] === "boolean" ? false : "";
    q.page = 1;
    renderFacets();
    runSearch();
  });
  $("#q").addEventListener("input", debounce((e) => {
    if (!state.entity) return;
    const q = ensureQuery(state.entity);
    q.q = e.target.value.trim();
    q.page = 1;
    runSearch();
  }, 400));

  // Entity metadata (one entity per dataset), then paint.
  await reloadEntities();

  refreshClusterPill();
  setInterval(refreshClusterPill, 5000);

  showView("search");
}

document.addEventListener("DOMContentLoaded", init);
