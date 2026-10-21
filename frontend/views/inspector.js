/* =========================================================
   Inspector view - single-key lookup + the deletion split.

   DELETE /cache/{key}  -> evict: node copies dropped, database
                           row KEPT (next read reloads it)
   DELETE /db/{key}     -> delete: database row removed, node
                           copies cascaded, dataset stats and
                           search caches refreshed
   ========================================================= */

"use strict";

(() => {

  function verdict(msg, kind) { setVerdict($("#inspectVerdict"), msg, kind); }

  function inspectKey(key) {
    $("#inspectKey").value = key;
    showView("inspector");     // its show hook re-runs the lookup
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

  /* ---- the deletion split -------------------------------------- */

  async function evictKey() {
    const key = $("#inspectKey").value.trim();
    if (!key) return;
    if (!confirm(`Evict "${key}" from every cache node?\n\nThe database row is KEPT - the next read reloads it.`)) return;

    try {
      const r = await api(`/cache/${encodeURIComponent(key)}`, { method: "DELETE" });
      verdict(
        `Evicted ${r.key} from ${r.nodes.length} node(s) — database row kept (source: ${r.database}).`,
        "ok"
      );
    } catch (e) {
      // 404: no cache node holds the key right now.
      verdict(`${e.message} — nothing to evict; the database row (if any) is untouched.`, "warn");
    }

    runInspect();
  }

  async function deleteKey() {
    const key = $("#inspectKey").value.trim();
    if (!key) return;
    if (!confirm(`Delete "${key}" from the DATABASE?\n\nThe row is removed and every cache node copy is invalidated. For dataset keys the manifest stats and search caches are refreshed too.`)) return;

    try {
      const r = await api(`/db/${encodeURIComponent(key)}`, { method: "DELETE" });
      const ds = r.dataset ? ` · dataset ${r.dataset.dataset} now ${r.dataset.row_count} row(s)` : "";
      verdict(`Deleted ${r.key} from the database; evicted from ${r.nodes.length} node(s).${ds}`, "ok");
    } catch (e) {
      verdict(`${e.message} — key not found anywhere.`, "warn");
    }

    runInspect();
  }

  /* ---- wiring --------------------------------------------------- */

  $("#inspectForm").addEventListener("submit", (e) => { e.preventDefault(); runInspect(); });
  $("#inspectEvict").addEventListener("click", evictKey);
  $("#inspectDelete").addEventListener("click", deleteKey);
  $("#browseForm").addEventListener("submit", (e) => { e.preventDefault(); runBrowse(); });

  registerView("inspector", {
    show() {
      // Re-lookup on entry: a key may have been mutated elsewhere.
      const key = $("#inspectKey").value.trim();
      if (key) runInspect();
    },
  });

  // Used by the Search tab's result cards ("Inspect key →").
  window.inspectKey = inspectKey;

})();
