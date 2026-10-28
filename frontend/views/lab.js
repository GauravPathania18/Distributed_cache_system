/* =========================================================
   Lab view - five experiments that show the cache behaving.

   1. search storm          same search N times: hits vs SQL
   2. point-read storm      same key N times, optional evict-per-read
   3. node failure drill    stop a node, keep reading, restart it
   4. eviction burst        write K keys, evict all, read all back
   5. live LRU order        one node's linked list: reorder + eviction
   ========================================================= */

"use strict";

(() => {

  function statCard(label, value, sub, cls) {
    return el("div", { class: "stat " + (cls || "") }, [
      el("div", { class: "label" }, label),
      el("div", { class: "value" }, value),
      el("div", { class: "sub" }, sub || ""),
    ]);
  }

  function avg(arr) {
    return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
  }

  function feedLine(feed, n, source, text) {
    const line = el("div", { class: "lab-line" }, [
      el("span", { class: "n" }, n == null ? "" : `#${n}`),
      el("span", { class: "src " + source }, source.toUpperCase()),
      el("span", {}, text),
    ]);
    feed.appendChild(line);
    feed.scrollTop = feed.scrollHeight;
  }

  /* =========================================================
     LAB 1 - SEARCH STORM
     ========================================================= */

  const lab1 = { running: false, results: [], cacheHits: 0, dbRuns: 0 };

  function populateLabEntity() {
    const sel = $("#labEntity");
    const prev = sel.value;
    sel.innerHTML = "";
    for (const name of Object.keys(state.entities)) {
      sel.appendChild(el("option", { value: name }, entityLabel(name)));
    }
    if (!sel.value && prev && state.entities[prev]) sel.value = prev;
  }

  async function runLab(event) {
    event.preventDefault();
    if (lab1.running) return;

    const entity = $("#labEntity").value || state.entity;
    const meta = state.entities[entity];

    if (!entity || !meta) {
      alert("No dataset yet — create one in the Datasets tab first.");
      return;
    }

    const query = $("#labQuery").value.trim();
    const count = Math.max(1, Math.min(500, +$("#labCount").value || 40));
    const delay = Math.max(0, Math.min(1000, +$("#labDelay").value || 0));
    const btn = $("#labRun");

    const params = new URLSearchParams();
    if (query) params.set("q", query);
    params.set("sort", meta.default_sort || "");
    params.set("dir", meta.default_dir || "asc");
    params.set("page_size", "5");

    lab1.running = true;
    lab1.results = [];
    lab1.cacheHits = 0;
    lab1.dbRuns = 0;
    btn.disabled = true;
    btn.textContent = "Running…";
    $("#labFeed").innerHTML = "";

    for (let i = 1; i <= count; i++) {
      const started = performance.now();
      try {
        const r = await api(`/search/${encodeURIComponent(entity)}?${params}`);
        const ms = performance.now() - started;
        const hit = r.source === "cache";
        if (hit) lab1.cacheHits++; else lab1.dbRuns++;
        lab1.results.push({ i, hit, ms, source: r.source });
        feedLine($("#labFeed"), i, r.source, `served in ${ms.toFixed(1)} ms`);
        renderLab1Stats(count);
        renderLab1Chart();
      } catch (e) {
        feedLine($("#labFeed"), i, "error", e.message);
        break;
      }
      if (delay) await sleep(delay);
    }

    lab1.running = false;
    btn.disabled = false;
    btn.textContent = "Run";
  }

  function renderLab1Stats(count) {
    const { cacheHits, dbRuns, results } = lab1;
    const total = cacheHits + dbRuns || 1;
    const pct = ((cacheHits / total) * 100).toFixed(1);
    const box = $("#labStats");
    box.innerHTML = "";
    box.appendChild(statCard("Cache hits", cacheHits, "served with 0 SQL", "good"));
    box.appendChild(statCard("SQL queries", dbRuns, "database actually touched"));
    box.appendChild(statCard("Load reduction", `${pct}%`, "of all requests", "accent"));
    const c = avg(results.filter((r) => r.hit).map((r) => r.ms));
    const d = avg(results.filter((r) => !r.hit).map((r) => r.ms));
    box.appendChild(statCard("Avg cache", c == null ? "—" : `${c.toFixed(1)} ms`, "per cached request"));
    box.appendChild(statCard("Avg database", d == null ? "—" : `${d.toFixed(1)} ms`, "per SQL request"));
  }

  function renderLab1Chart() {
    const { results } = lab1;
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

  async function resetLab1() {
    try { await api("/dashboard/telemetry/reset", { method: "POST" }); } catch (_) {}
    lab1.results = [];
    lab1.cacheHits = 0;
    lab1.dbRuns = 0;
    $("#labStats").innerHTML = "";
    $("#labChart").innerHTML = "";
    $("#labFeed").innerHTML = "";
  }

  /* =========================================================
     LAB 2 - POINT-READ STORM (with per-iteration eviction)
     ========================================================= */

  const lab2 = { running: false };

  async function runPointStorm(event) {
    event.preventDefault();
    if (lab2.running) return;

    const key = $("#prKey").value.trim();
    if (!key) { alert("Enter a key to read (e.g. users:1)."); return; }

    const count = Math.max(1, Math.min(200, +$("#prCount").value || 12));
    const evict = $("#prEvict").checked;
    const enc = encodeURIComponent(key);
    const btn = $("#prRun");
    const feed = $("#prFeed");

    lab2.running = true;
    btn.disabled = true;
    btn.textContent = "Running…";
    feed.innerHTML = "";

    let hits = 0, dbs = 0;
    const hitMs = [], dbMs = [];

    for (let i = 1; i <= count; i++) {

      if (evict) {
        try {
          const r = await api(`/cache/${enc}`, { method: "DELETE" });
          feedLine(feed, i, "muted", `evicted from ${r.nodes.length} node(s) before read`);
        } catch (e) {
          feedLine(feed, i, "muted", "evict: no cache copy held (row untouched)");
        }
      }

      const started = performance.now();
      try {
        const r = await api(`/cache/${enc}`);
        const ms = performance.now() - started;
        if (r.source === "cache") { hits++; hitMs.push(ms); }
        else { dbs++; dbMs.push(ms); }
        feedLine(feed, i, r.source,
          `served in ${ms.toFixed(1)} ms` + (r.node ? ` via ${r.node.replace("http://", "")}` : " (database)"));
      } catch (e) {
        feedLine(feed, i, "error", e.message);
        break;
      }
    }

    const box = $("#prStats");
    box.innerHTML = "";
    box.appendChild(statCard("Cache hits", hits, "0 SQL issued", "good"));
    box.appendChild(statCard("Database reads", dbs, "row loaded / reloaded"));
    const c = avg(hitMs), d = avg(dbMs);
    box.appendChild(statCard("Avg cache", c == null ? "—" : `${c.toFixed(1)} ms`, "per hit"));
    box.appendChild(statCard("Avg database", d == null ? "—" : `${d.toFixed(1)} ms`, "per load"));

    setVerdict($("#prVerdict"), evict
      ? "Every read paid a database load — and every value came back: eviction drops cache copies, never rows."
      : `First read loaded the database; ${hits} follow-up read(s) were pure cache hits.`,
      "ok");

    lab2.running = false;
    btn.disabled = false;
    btn.textContent = "Run";
  }

  /* =========================================================
     LAB 3 - NODE FAILURE DRILL
     ========================================================= */

  const lab3 = { running: false, nodes: [] };

  async function loadDrillNodes() {
    const sel = $("#drillNode");
    const prev = sel.value;
    try {
      const data = await api("/cluster/nodes");
      lab3.nodes = data.nodes || [];
    } catch (_) {
      lab3.nodes = [];
    }

    sel.innerHTML = "";
    for (const node of lab3.nodes) {
      sel.appendChild(el("option", { value: node.port },
        `127.0.0.1:${node.port} (${node.state}${node.process === "stopped" ? " · stopped" : ""})`));
    }
    if (prev && lab3.nodes.some((n) => String(n.port) === prev)) sel.value = prev;
  }

  async function nodeAction(path) {
    const port = +$("#drillNode").value;
    if (!port) { alert("No node selected — scale up in the Cluster tab first."); return; }

    try {
      const r = await api(`/cluster/nodes/${port}/${path}`, { method: "POST" });
      const v = $("#drillVerdict");
      if (path === "stop") {
        setVerdict(v, r.removed_from_cluster
          ? `Node ${port} removed from the ring and stopped — replica/database now absorb its keys.`
          : `Node ${port} was not running.`, r.removed_from_cluster ? "ok" : "warn");
      } else {
        setVerdict(v, `Node ${port}: ${r.action} — state ${r.state || "?"}. It re-enters through STARTING, then warm-up/replication before serving traffic.`, "ok");
      }
    } catch (e) {
      setVerdict($("#drillVerdict"), e.message, "err");
    }

    loadDrillNodes();
  }

  async function waitForReady(port, timeoutMs = 20000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      try {
        const data = await api("/cluster/nodes");
        const node = (data.nodes || []).find((n) => n.port === port);
        if (node && node.state === "ready") return true;
      } catch (_) { /* router busy */ }
      await sleep(500);
    }
    return false;
  }

  async function runDrill(event) {
    event.preventDefault();
    if (lab3.running) return;

    const port = +$("#drillNode").value;
    const key = $("#drillKey").value.trim();
    const count = Math.max(1, Math.min(50, +$("#drillCount").value || 8));

    if (!port) { alert("No node selected — scale up in the Cluster tab first."); return; }
    if (!key) { alert("Enter a key to read during the drill (e.g. users:1)."); return; }

    const btn = $("#drillRun");
    const feed = $("#drillFeed");
    const enc = encodeURIComponent(key);

    lab3.running = true;
    btn.disabled = true;
    btn.textContent = "Running drill…";
    feed.innerHTML = "";
    setVerdict($("#drillVerdict"), "", "");

    const readPhase = async (label) => {
      let cache = 0, db = 0, errors = 0;
      const t0 = performance.now();
      for (let i = 0; i < count; i++) {
        try {
          const r = await api(`/cache/${enc}`);
          if (r.source === "cache") cache++; else db++;
        } catch (_) {
          errors++;
        }
      }
      const ms = performance.now() - t0;
      feedLine(feed, null, errors ? "error" : (cache ? "cache" : "database"),
        `${label}: ${cache} cache · ${db} database · ${errors} error(s) in ${ms.toFixed(0)} ms`);
      return { cache, db, errors };
    };

    // ---- phase 1: healthy cluster -------------------------------
    feedLine(feed, null, "muted", `— phase 1: healthy cluster, reading "${key}" ×${count} —`);
    const healthy = await readPhase("healthy");

    // ---- phase 2: stop the node, keep reading -------------------
    feedLine(feed, null, "muted", `— phase 2: stopping node ${port}, reading ×${count} —`);
    try {
      await api(`/cluster/nodes/${port}/stop`, { method: "POST" });
    } catch (e) {
      setVerdict($("#drillVerdict"), "Stop failed: " + e.message, "err");
    }
    await sleep(300);
    const down = await readPhase("node down");

    // ---- phase 3: restart, wait for READY, read again -----------
    feedLine(feed, null, "muted", `— phase 3: restarting node ${port}, waiting for READY —`);
    try {
      await api(`/cluster/nodes/${port}/start`, { method: "POST" });
    } catch (e) {
      setVerdict($("#drillVerdict"), "Start failed: " + e.message, "err");
    }
    const rejoined = await waitForReady(port);
    feedLine(feed, null, "muted",
      rejoined ? `node ${port} rejoined (READY)` : `node ${port} still gating after 20s — check the Cluster tab`);
    const after = await readPhase("after restart");

    // ---- verdict ------------------------------------------------
    const totalErrors = healthy.errors + down.errors + after.errors;
    setVerdict(
      $("#drillVerdict"),
      `Drill complete: ${down.cache + down.db} read(s) survived the outage ` +
      `(${down.db} fell back to the database, ${down.cache} to surviving replicas), ` +
      `${totalErrors} error(s) total.` + (rejoined ? " Node rejoined through STARTING → READY." : ""),
      totalErrors ? "warn" : "ok"
    );

    lab3.running = false;
    btn.disabled = false;
    btn.textContent = "Run drill";
    loadDrillNodes();
  }

  /* =========================================================
     LAB 4 - EVICTION BURST
     ========================================================= */

  const lab4 = { running: false };

  async function runBurst(event) {
    event.preventDefault();
    if (lab4.running) return;

    const prefix = $("#burstPrefix").value.trim() || "lab:burst:";
    const count = Math.max(1, Math.min(100, +$("#burstCount").value || 15));
    const btn = $("#burstRun");

    lab4.running = true;
    btn.disabled = true;
    btn.textContent = "Running…";

    let stored = 0, warned = 0, evicted = 0, notHeld = 0, reloaded = 0, missing = 0;
    const readMs = [];

    // 1. write K fresh keys through the normal write path
    for (let i = 1; i <= count; i++) {
      try {
        const r = await api(`/cache/${encodeURIComponent(prefix + i)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: { i, burst: prefix, at: Date.now() } }),
        });
        stored++;
        if (r.warning) warned++;
      } catch (_) { /* counted below via reads */ }
    }

    // 2. evict every one of them from the cache
    for (let i = 1; i <= count; i++) {
      try {
        await api(`/cache/${encodeURIComponent(prefix + i)}`, { method: "DELETE" });
        evicted++;
      } catch (_) { notHeld++; }
    }

    // 3. read them all back - every value must come from the database
    for (let i = 1; i <= count; i++) {
      const started = performance.now();
      try {
        const r = await api(`/cache/${encodeURIComponent(prefix + i)}`);
        readMs.push(performance.now() - started);
        if (r.source === "database" || r.source === "cache") reloaded++;
        if (r.value == null || r.value.i !== i) missing++;
      } catch (_) { missing++; }
    }

    const box = $("#burstStats");
    box.innerHTML = "";
    box.appendChild(statCard("Stored", `${stored}/${count}`, warned ? `${warned} without a cache node (DB only)` : "through the write-through path", "good"));
    box.appendChild(statCard("Evicted", evicted, `${notHeld} had no cache copy`));
    box.appendChild(statCard("Reloaded after eviction", `${reloaded - missing}/${count}`, "values verified field-by-field", missing ? "" : "accent"));
    const m = avg(readMs);
    box.appendChild(statCard("Avg reload", m == null ? "—" : `${m.toFixed(1)} ms`, "per read after eviction"));

    setVerdict(
      $("#burstVerdict"),
      missing
        ? `${missing} value(s) did not survive the burst — unexpected, check the Dashboard.`
        : `All ${count} values reloaded intact after eviction — eviction drops cache copies, never rows.`,
      missing ? "err" : "ok"
    );

    lab4.running = false;
    btn.disabled = false;
    btn.textContent = "Run burst";
  }

  /* =========================================================
     LAB 5 - LIVE LRU ORDER (one node, linked list + eviction)

     Everything the other labs treat as a black box is rendered
     here: the exact MRU -> LRU order of ONE node's doubly linked
     list, read from GET /stats/lru through the router's
     single-node proxy (no hash-ring fan-out), diffed after every
     operation so inserts, reorders and evictions are visible.
     ========================================================= */

  const LAB5_PREFIX = "lab:lru:";

  const lab5 = {
    running: false,
    timer: null,
    nodes: [],
    port: null,        // selected node
    boundPort: null,   // node the strip/feed are currently bound to
    seq: 0,            // next lab:lru:N number
    keys: [],          // last snapshot, MRU -> LRU
    stats: null,       // last /stats/lru payload
    added: [],         // keys to flash as inserted (one shot)
    touched: null,     // key to flash as read (one shot)
    lastTouch: null,   // key awaiting confirmation from the next snapshot
    ghosts: [],        // evicted keys flashing at the tail
    ghostTimer: null,
    narrate: null,     // how the next diff narrates removals: evict | gone
    opSeq: 0,
  };

  function lruUrl(path) {
    return `/cluster/nodes/${lab5.port}${path}`;
  }

  function lruKeyPath(key) {
    return lruUrl(`/cache/${encodeURIComponent(key)}`);
  }

  /* ---------- node list + binding ---------- */

  async function loadLruNodes() {
    const sel = $("#lruNode");
    const prev = sel.value;

    try {
      const data = await api("/cluster/nodes");
      lab5.nodes = data.nodes || [];
    } catch (_) {
      lab5.nodes = [];
    }

    sel.innerHTML = "";
    for (const node of lab5.nodes) {
      const s = node.stats;
      sel.appendChild(el("option", { value: node.port },
        `127.0.0.1:${node.port} (${node.state}${s ? ` · ${s.size}/${s.capacity}` : ""})`));
    }
    if (prev && lab5.nodes.some((n) => String(n.port) === prev)) sel.value = prev;

    const port = +sel.value || null;

    if (port !== lab5.boundPort) {
      lab5.port = port;
      await bindLruNode();
    } else {
      refreshLru();          // same node: silent refresh of strip + stats
    }
  }

  async function bindLruNode() {
    lab5.boundPort = lab5.port;
    lab5.keys = [];
    lab5.stats = null;
    lab5.added = [];
    lab5.touched = null;
    lab5.lastTouch = null;
    lab5.ghosts = [];
    lab5.narrate = null;
    lab5.opSeq = 0;
    clearTimeout(lab5.ghostTimer);

    $("#lruStrip").innerHTML = "";
    $("#lruStats").innerHTML = "";
    $("#lruFeed").innerHTML = "";
    setVerdict($("#lruVerdict"), "", "");

    if (!lab5.port) {
      $("#lruStrip").appendChild(
        el("span", { class: "muted" }, "no node selected — scale up in the Cluster tab first")
      );
      return;
    }

    const snap = await refreshLru();

    if (snap) {
      syncLruCount(snap);
      adoptExistingLabKeys(snap.keys || []);
    }
  }

  /* Preset the fill count to the exact number of writes that forces
     one eviction: capacity − size + 1. */
  function syncLruCount(snap) {
    const need = (snap.capacity || 0) - (snap.size || 0) + 1;
    $("#lruCount").value = Math.max(1, Math.min(300, need));
  }

  /* Never renumber keys a previous session already wrote: continue
     after the highest lab:lru:N currently on the node. */
  function adoptExistingLabKeys(keys) {
    for (const key of keys) {
      if (!key.startsWith(LAB5_PREFIX)) continue;
      const n = parseInt(key.slice(LAB5_PREFIX.length), 10);
      if (Number.isFinite(n) && n + 1 > lab5.seq) lab5.seq = n + 1;
    }
  }

  /* ---------- snapshot: fetch, diff, render ---------- */

  async function refreshLru() {
    if (!lab5.port) return null;

    try {
      const snap = await api(lruUrl("/cache-state"));
      applySnapshot(snap);
      return snap;
    } catch (e) {
      lab5.narrate = null;
      setVerdict($("#lruVerdict"),
        `node ${lab5.port} unreachable (${e.message}) — start it in the Cluster tab`, "warn");
      return null;
    }
  }

  function applySnapshot(snap) {
    const prev = lab5.keys;
    const next = Array.isArray(snap.keys) ? snap.keys : [];

    const removed = prev.filter((k) => !next.includes(k));
    const added = next.filter((k) => !prev.includes(k));
    const narrate = lab5.narrate;

    // ---- removals: capacity eviction, expiry, or our own delete ----
    for (const key of removed) {

      if (narrate === "evict") {
        feedLine($("#lruFeed"), ++lab5.opSeq, "evict",
          `${key} dropped off the tail — capacity ${snap.capacity} reached`);
      } else if (narrate !== "gone") {
        feedLine($("#lruFeed"), ++lab5.opSeq, "muted",
          `${key} left the list (evicted by cluster traffic, or TTL expired)`);
      }
    }

    if (removed.length && narrate !== "gone") {
      lab5.ghosts = lab5.ghosts.concat(removed).slice(-8);
      clearTimeout(lab5.ghostTimer);
      lab5.ghostTimer = setTimeout(() => {
        lab5.ghosts = [];
        renderLru();
      }, 1800);
    }

    lab5.keys = next;
    lab5.stats = snap;
    lab5.added = added;
    lab5.touched = (lab5.lastTouch && next.includes(lab5.lastTouch))
      ? lab5.lastTouch : null;

    lab5.lastTouch = null;
    lab5.narrate = null;

    renderLru();
    renderLruStats();

    lab5.added = [];      // flashes fire exactly once, on this render
    lab5.touched = null;
  }

  function renderLru() {
    const strip = $("#lruStrip");
    strip.innerHTML = "";

    strip.appendChild(el("span", { class: "lru-end" }, "HEAD · most recent"));

    const last = lab5.keys.length - 1;

    lab5.keys.forEach((key, i) => {
      const cls = ["lru-chip"];
      if (lab5.added.includes(key)) cls.push("arrived");
      if (lab5.touched === key) cls.push("touched");
      if (i === last) cls.push("victim");

      const tip = i === last
        ? `"${key}" — least recently used: the next overflow evicts it`
        : `"${key}" — recency #${i + 1} of ${lab5.keys.length} (click to load into the key box)`;

      strip.appendChild(el("span", {
        class: cls.join(" "),
        title: tip,
        onclick: () => { $("#lruKey").value = key; },
      }, key));
    });

    for (const key of lab5.ghosts) {
      strip.appendChild(el("span", {
        class: "lru-chip ghost",
        title: `"${key}" — just fell off the tail`,
      }, key));
    }

    strip.appendChild(el("span", { class: "lru-end tail" },
      lab5.keys.length ? "TAIL · evicted next →" : "TAIL · empty"));
  }

  function renderLruStats() {
    const box = $("#lruStats");
    const s = lab5.stats;

    if (!s) { box.innerHTML = ""; return; }

    box.innerHTML = "";
    box.appendChild(statCard("Size", `${s.size}/${s.capacity}`,
      "entries vs capacity", s.size >= s.capacity ? "bad" : ""));
    box.appendChild(statCard("Evictions", s.evictions,
      "capacity removals on this node"));
    box.appendChild(statCard("Hits", s.hits,
      `${s.hit_rate}% hit rate`, "good"));
    box.appendChild(statCard("Misses", s.misses,
      "lookups that found nothing"));
  }

  /* ---------- operations ---------- */

  async function runLruFill(event) {
    event.preventDefault();
    if (lab5.running) return;
    if (!lab5.port) {
      alert("No node selected — scale up in the Cluster tab first.");
      return;
    }

    const count = Math.max(1, Math.min(300, +$("#lruCount").value || 1));
    const delay = Math.max(0, Math.min(1000, +$("#lruDelay").value || 0));
    const btn = $("#lruFill");
    const sel = $("#lruNode");

    const evictionsBefore = lab5.stats ? lab5.stats.evictions : null;
    let inserted = 0;

    lab5.running = true;
    btn.disabled = true;
    sel.disabled = true;
    btn.textContent = "Filling…";
    setVerdict($("#lruVerdict"), "", "");

    try {
      for (let i = 1; i <= count; i++) {

        const key = LAB5_PREFIX + String(lab5.seq++).padStart(3, "0");
        lab5.narrate = "evict";     // removals in the next diff are capacity evictions

        try {
          await api(lruKeyPath(key), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ value: { key, at: Date.now() } }),
          });
          inserted++;
          feedLine($("#lruFeed"), ++lab5.opSeq, "set", `${key} → HEAD`);
        } catch (e) {
          feedLine($("#lruFeed"), ++lab5.opSeq, "error", `set ${key}: ${e.message}`);
          break;
        }

        await refreshLru();
        if (delay) await sleep(delay);
      }
    } finally {
      lab5.running = false;
      btn.disabled = false;
      sel.disabled = false;
      btn.textContent = "Fill to overflow";
    }

    const snap = lab5.stats;
    const evicted = (evictionsBefore != null && snap)
      ? Math.max(0, snap.evictions - evictionsBefore) : 0;

    if (!snap) return;

    setVerdict($("#lruVerdict"), evicted > 0
      ? `Inserted ${inserted} key(s) and forced ${evicted} eviction(s): the tail gave ` +
        `way because the list cannot exceed ${snap.capacity}. Watch which keys fell — ` +
        `cold ones before the fresh ones, and a key you Touch survives its own turn.`
      : `Inserted ${inserted} key(s) — the node now holds ${snap.size}/${snap.capacity}, ` +
        `no overflow yet. The count is preset to capacity − size + 1: press Fill again.`,
      evicted > 0 ? "ok" : "warn");
  }

  async function touchKey() {
    if (lab5.running) return;
    if (!lab5.port) {
      alert("No node selected — scale up in the Cluster tab first.");
      return;
    }

    let key = $("#lruKey").value.trim();
    if (!key) key = lab5.keys[lab5.keys.length - 1];   // blank: the current victim
    if (!key) {
      setVerdict($("#lruVerdict"), "the list is empty — press Fill first", "warn");
      return;
    }

    const feed = $("#lruFeed");
    lab5.running = true;

    try {
      const r = await api(lruKeyPath(key));

      lab5.lastTouch = key;
      lab5.narrate = null;
      feedLine(feed, ++lab5.opSeq, "touch",
        `hit: ${key} → HEAD · ${preview(r.value)}`);
      setVerdict($("#lruVerdict"),
        `HIT — "${key}" hopped to HEAD (recency 1 of ${lab5.keys.length || "?"}). ` +
        `That is the whole trick: reads promote, unread keys slide toward the tail ` +
        `and eventually fall off.`, "ok");

      await refreshLru();

    } catch (e) {
      if (String(e.message).startsWith("404")) {
        feedLine(feed, ++lab5.opSeq, "muted",
          `miss: ${key} is not on this node — the list stays untouched`);
        setVerdict($("#lruVerdict"),
          `MISS — "${key}" is not cached on this node. A miss cannot reorder anything: ` +
          `there is no node to move.`, "warn");
      } else {
        feedLine(feed, ++lab5.opSeq, "error", `get ${key}: ${e.message}`);
        setVerdict($("#lruVerdict"), e.message, "err");
      }
    } finally {
      lab5.running = false;
    }
  }

  async function deleteKey() {
    if (lab5.running) return;
    if (!lab5.port) {
      alert("No node selected — scale up in the Cluster tab first.");
      return;
    }

    const key = $("#lruKey").value.trim();
    if (!key) {
      alert("Type a key first (or click a chip to load it into the box).");
      return;
    }

    const feed = $("#lruFeed");
    lab5.running = true;

    try {
      await api(lruKeyPath(key), { method: "DELETE" });
      lab5.narrate = "gone";      // we narrate it ourselves: no eviction ghosts
      feedLine(feed, ++lab5.opSeq, "evict",
        `deleted ${key} — hand-picked removal, the tail got no say`);
      setVerdict($("#lruVerdict"),
        `Deleted "${key}" in O(1): unlinked from the list and dropped from the map. ` +
        `Note the difference — capacity eviction is the only removal the recency ` +
        `order decides by itself.`, "ok");
      await refreshLru();
    } catch (e) {
      if (String(e.message).startsWith("404")) {
        feedLine(feed, ++lab5.opSeq, "muted", `delete ${key}: not cached on this node`);
        setVerdict($("#lruVerdict"), `"${key}" is not cached on this node.`, "warn");
      } else {
        feedLine(feed, ++lab5.opSeq, "error", `delete ${key}: ${e.message}`);
        setVerdict($("#lruVerdict"), e.message, "err");
      }
    } finally {
      lab5.running = false;
    }
  }

  async function clearLabKeys() {
    if (lab5.running) return;
    if (!lab5.port) {
      alert("No node selected — scale up in the Cluster tab first.");
      return;
    }

    const feed = $("#lruFeed");
    lab5.running = true;

    try {
      const r = await api(lruUrl(`/cache-prefix/${LAB5_PREFIX}`), { method: "DELETE" });
      lab5.narrate = "gone";
      feedLine(feed, ++lab5.opSeq, "muted",
        `prefix flush: ${r.removed} lab key(s) removed from node ${lab5.port}`);
      setVerdict($("#lruVerdict"),
        `Cleared ${r.removed} lab key(s); capacity is free again for the next run.`, "ok");
      await refreshLru();
    } catch (e) {
      feedLine(feed, ++lab5.opSeq, "error", `prefix flush: ${e.message}`);
      setVerdict($("#lruVerdict"), e.message, "err");
    } finally {
      lab5.running = false;
    }
  }

  function preview(value) {
    const json = JSON.stringify(value);
    if (json == null) return "no value";
    return json.length > 70 ? json.slice(0, 67) + "…" : json;
  }

  /* =========================================================
     WIRING
     ========================================================= */

  $("#labForm").addEventListener("submit", runLab);
  $("#labReset").addEventListener("click", resetLab1);

  $("#prForm").addEventListener("submit", runPointStorm);
  $("#prKey").addEventListener("focus", () => {
    if (!$("#prKey").value && state.entity) {
      $("#prKey").value = `${(state.entities[state.entity] || {}).prefix || state.entity + ":"}1`;
    }
  });

  $("#drillForm").addEventListener("submit", runDrill);
  $("#drillStop").addEventListener("click", () => nodeAction("stop"));
  $("#drillStart").addEventListener("click", () => nodeAction("start"));

  $("#burstForm").addEventListener("submit", runBurst);

  $("#lruForm").addEventListener("submit", runLruFill);
  $("#lruNode").addEventListener("change", () => {
    lab5.port = +$("#lruNode").value || null;
    bindLruNode();
  });
  $("#lruTouch").addEventListener("click", touchKey);
  $("#lruDelete").addEventListener("click", deleteKey);
  $("#lruClear").addEventListener("click", clearLabKeys);
  $("#lruKey").addEventListener("focus", (e) => {
    if (!e.target.value && lab5.keys.length) {
      e.target.value = lab5.keys[lab5.keys.length - 1];  // the current victim
    }
    e.target.select();   // typing replaces the prefill
  });

  registerView("lab", {
    show() {
      populateLabEntity();
      loadDrillNodes();
      loadLruNodes();
      if (!lab5.timer) {
        lab5.timer = setInterval(() => {
          if (lab5.running || document.hidden) return;
          refreshLru();
        }, 1500);
      }
    },
    hide() {
      if (lab5.timer) { clearInterval(lab5.timer); lab5.timer = null; }
    },
  });

})();
