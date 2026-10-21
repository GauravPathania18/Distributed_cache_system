/* =========================================================
   Datasets view - full dataset & entry administration.

   Every dataset is an isolated SQLite file with its own key
   prefix; entries are added through the conflict-free path (the
   server derives the key: declared key column -> "id" -> next
   free number) and every mutation purges the entity's search
   caches, so the Search tab never serves a stale page.
   ========================================================= */

"use strict";

(() => {

  let selected = null;

  const notice = (msg, kind) => setVerdict($("#dsNotice"), msg, kind);

  /* ---------------------------------------------------------
     LIST
     --------------------------------------------------------- */

  async function refreshList() {
    const box = $("#datasetList");

    let data;
    try {
      data = await api("/datasets");
    } catch (e) {
      box.innerHTML = "";
      box.appendChild(el("div", { class: "error-box" }, e.message));
      return;
    }

    box.innerHTML = "";

    if (!data.datasets.length) {
      box.appendChild(el("div", { class: "empty" },
        "No datasets yet — create one above, or import a file with import_dataset.py."));
      return;
    }

    for (const ds of data.datasets) box.appendChild(datasetCard(ds));

    // Keep the detail pane pointed at a dataset that still exists.
    if (selected && !data.datasets.some((d) => d.name === selected)) {
      selected = null;
      renderDetail(null);
    }
  }

  function datasetCard(ds) {
    const columns = Object.keys(ds.schema || {});

    const kids = [
      el("div", { class: "card-top" }, [
        el("span", { class: "card-title", style: "margin:0" }, prettify(ds.name)),
        el("span", { class: "chip", style: "cursor:default" }, ds.prefix),
      ]),
      el("div", { class: "kv" }, [
        el("span", { class: "k" }, "rows"),
        el("span", { class: "v" }, `${fmtNum(ds.row_count)} (${fmtNum(ds.live_rows)} live)`),
      ]),
      el("div", { class: "kv" }, [
        el("span", { class: "k" }, "columns"),
        el("span", { class: "v" }, columns.length ? columns.slice(0, 4).join(", ") + (columns.length > 4 ? "…" : "") : "— (add an entry to detect)"),
      ]),
    ];

    if (ds.key_column) {
      kids.push(el("div", { class: "kv" }, [
        el("span", { class: "k" }, "key column"),
        el("span", { class: "v" }, ds.key_column),
      ]));
    }

    if (!ds.exists) {
      kids.push(el("div", { class: "tag stock-out", style: "margin-top:8px" }, "⚠ file missing"));
    }

    kids.push(el("div", { class: "ds-actions" }, [
      el("button", {
        type: "button",
        class: "btn" + (selected === ds.name ? " primary" : ""),
        onclick: () => select(ds.name),
      }, "Browse"),
      el("button", {
        type: "button", class: "btn danger",
        onclick: () => deleteDataset(ds.name),
      }, "Delete"),
    ]));

    return el("div", {
      class: "dataset-card" + (selected === ds.name ? " active" : ""),
      onclick: (e) => { if (!e.target.closest("button")) select(ds.name); },
    }, kids);
  }

  /* ---------------------------------------------------------
     DETAIL (entries + add-entry form)
     --------------------------------------------------------- */

  async function select(name) {
    selected = name;
    refreshList();                    // re-render the selection state
    await renderDetail(name);
  }

  async function renderDetail(name) {
    const title = $("#dsDetailTitle");
    const box = $("#dsDetail");
    box.innerHTML = "";

    if (!name) {
      title.textContent = "Entries";
      box.appendChild(el("div", { class: "empty" }, "Select a dataset to browse and edit its entries."));
      return;
    }

    title.textContent = `Entries — ${name}`;

    let page;
    try {
      page = await api(`/datasets/${encodeURIComponent(name)}/entries?limit=100`);
    } catch (e) {
      box.appendChild(el("div", { class: "error-box" }, e.message));
      return;
    }

    // ---- add-entry form ----------------------------------------
    const input = el("textarea", {
      class: "json-input",
      placeholder: '{ "amount": 10 }   — JSON object; the server derives the key',
    });

    const addBtn = el("button", {
      type: "button", class: "btn primary",
      onclick: async () => {
        let value;
        try {
          value = JSON.parse(input.value || "");
        } catch (_) {
          notice("Entry must be valid JSON (a JSON object).", "err");
          return;
        }
        try {
          const r = await api(`/datasets/${encodeURIComponent(name)}/entries`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ value }),
          });
          notice(
            `${r.created ? "Added" : "Upserted"} ${r.key} — ` +
            `dataset now ${r.row_count} row(s); search caches purged.`,
            "ok"
          );
          input.value = "";
          reloadEntities();           // schema/columns may have grown
          renderDetail(name);
          refreshList();
        } catch (e) {
          notice(e.message, "err");
        }
      },
    }, "Add entry");

    box.appendChild(el("div", { class: "inline-form", style: "flex-direction:column;align-items:stretch" }, [
      input,
      el("div", { class: "ds-actions" }, [addBtn]),
    ]));

    // ---- entries table -----------------------------------------
    if (!page.rows.length) {
      box.appendChild(el("div", { class: "empty" }, "No entries yet."));
      return;
    }

    const table = el("table", { class: "table entries-table" });
    table.innerHTML = "<tr><th>Key</th><th>Value</th><th></th></tr>";

    for (const row of page.rows) {
      const value = JSON.stringify(row.value);
      table.appendChild(el("tr", {}, [
        el("td", {}, row.key),
        el("td", { class: "val", title: value }, value.length > 120 ? value.slice(0, 119) + "…" : value),
        el("td", {}, el("button", {
          type: "button", class: "link-btn",
          onclick: () => deleteEntry(name, row.key),
        }, "delete")),
      ]));
    }

    box.appendChild(table);
    box.appendChild(el("div", { class: "muted", style: "margin-top:8px" },
      `${page.total} row(s) total — showing ${page.offset + 1}–${page.offset + page.rows.length}`));
  }

  /* ---------------------------------------------------------
     MUTATIONS
     --------------------------------------------------------- */

  async function deleteEntry(name, key) {
    if (!confirm(`Delete "${key}"? The row leaves the database AND every cache node copy is invalidated.`)) return;

    try {
      const r = await api(
        `/datasets/${encodeURIComponent(name)}/entries?key=${encodeURIComponent(key)}`,
        { method: "DELETE" }
      );
      notice(`Deleted ${r.key} — ${r.row_count} row(s) left; search caches purged.`, "ok");
      reloadEntities();
      renderDetail(name);
      refreshList();
    } catch (e) {
      notice(e.message, "err");
    }
  }

  async function deleteDataset(name) {
    if (!confirm(`Delete dataset "${name}"? Its cached rows are flushed from every node, search caches purged, and its SQLite file removed.`)) return;

    try {
      const r = await api(`/datasets/${encodeURIComponent(name)}`, { method: "DELETE" });
      notice(
        `Deleted dataset ${r.name} (prefix ${r.prefix}) — ` +
        `${Object.keys(r.flushed).length} node(s) flushed, log trimmed, file removed.`,
        "ok"
      );
      if (selected === name) { selected = null; renderDetail(null); }
      reloadEntities();
      refreshList();
    } catch (e) {
      notice(e.message, "err");
    }
  }

  /* ---------------------------------------------------------
     WIRING
     --------------------------------------------------------- */

  $("#datasetCreateForm").addEventListener("submit", async (e) => {
    e.preventDefault();

    const name = $("#dsName").value.trim();
    const keyColumn = $("#dsKeyColumn").value.trim();

    if (!name) { notice("Dataset name is required.", "err"); return; }

    try {
      const r = await api("/datasets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, key_column: keyColumn || null }),
      });

      notice(
        `Created dataset “${r.dataset.name}” — isolated file ${r.dataset.db_path}`,
        "ok"
      );

      $("#dsName").value = "";
      $("#dsKeyColumn").value = "";

      reloadEntities();               // empty schema: no entity yet - fine
      refreshList();
      select(r.dataset.name);
    } catch (err) {
      notice(err.message, "err");
    }
  });

  registerView("datasets", {
    show() {
      notice("", "");
      refreshList();
      if (selected) renderDetail(selected);
      else renderDetail(null);
    },
  });

})();
