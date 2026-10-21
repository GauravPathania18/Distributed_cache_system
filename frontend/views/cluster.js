/* =========================================================
   Cluster view - size the cluster from the browser.

   Scale up spawns real main.py processes on the pool ports
   (NODE_PORT_BASE..+MAX_NODES), scale down deregisters the
   highest-port members from the ring and stops their processes.
   Every card can also start/stop/restart its own node.
   ========================================================= */

"use strict";

(() => {

  let timer = null;
  let lastTotal = 0;

  const notice = (msg, kind) => setVerdict($("#clusterNotice"), msg, kind);

  function statCard(label, value, sub, cls) {
    return el("div", { class: "stat " + (cls || "") }, [
      el("div", { class: "label" }, label),
      el("div", { class: "value" }, value),
      el("div", { class: "sub" }, sub || ""),
    ]);
  }

  function renderScale(data) {
    const box = $("#scaleInfo");
    box.textContent =
      `${data.total} node(s) · ${data.active} active · ` +
      `pool ${data.node_port_base}–${data.node_port_base + data.max_nodes - 1} · ` +
      `capacity ${data.node_capacity} keys/node`;

    lastTotal = data.total;

    const target = $("#scaleTarget");
    target.max = data.max_nodes;

    // Keep the input tracking the live size - unless the user is
    // in the middle of typing a target.
    if (document.activeElement !== target) target.value = data.total;
  }

  function nodeCard(node) {
    const stats = node.stats;
    const used = stats ? stats.size : null;
    const capacity = (stats && stats.capacity) || null;
    const pct = capacity ? Math.min(100, (used / capacity) * 100) : 0;

    const kids = [
      el("div", { class: "card-top" }, [
        el("span", { class: "node-url" }, `127.0.0.1:${node.port}`),
        el("span", { class: "state-pill state-" + node.state }, node.state),
      ]),
      el("div", { class: "kv" }, [
        el("span", { class: "k" }, "process"),
        el("span", { class: "proc-chip proc-" + node.process }, node.process + (node.pid ? ` · pid ${node.pid}` : "")),
      ]),
    ];

    if (stats) {
      kids.push(el("div", { class: "bar-track", style: "margin:8px 0" },
        el("div", { class: "bar-fill", style: `width:${pct}%` })));
      kids.push(el("div", { class: "kv" }, [
        el("span", { class: "k" }, "size / capacity"),
        el("span", { class: "v" }, `${used} / ${capacity}`),
      ]));
      kids.push(el("div", { class: "kv" }, [
        el("span", { class: "k" }, "hits / misses"),
        el("span", { class: "v" }, `${stats.hits} / ${stats.misses}`),
      ]));
      kids.push(el("div", { class: "kv" }, [
        el("span", { class: "k" }, "evictions"),
        el("span", { class: "v" }, String(stats.evictions)),
      ]));
    } else {
      kids.push(el("div", { class: "kv" }, [
        el("span", { class: "k" }, "size / capacity"),
        el("span", { class: "v muted" }, "unreachable"),
      ]));
    }

    const action = (label, cls, path, title) => el("button", {
      type: "button", class: "btn " + cls, title,
      onclick: async (e) => {
        e.target.disabled = true;
        try {
          const r = await api(`/cluster/nodes/${node.port}/${path}`, { method: "POST" });
          const what = path === "stop"
            ? (r.removed_from_cluster ? "removed from the ring, process stopped" : "process was not running")
            : `${r.action}${r.state ? ` — state ${r.state}` : ""}${r.healthy ? "" : " (not healthy yet)"}`;
          notice(`127.0.0.1:${node.port}: ${what}`, "ok");
        } catch (err) {
          notice(`127.0.0.1:${node.port}: ${err.message}`, "err");
        }
        refresh();
      },
    }, label);

    const buttons = [];
    if (node.process === "stopped") buttons.push(action("Start", "", "start", "spawn the node process"));
    else {
      buttons.push(action("Stop", "danger", "stop", "deregister + stop the process"));
      buttons.push(action("Restart", "", "restart", "stop, then start again"));
    }
    kids.push(el("div", { class: "node-actions" }, buttons));

    return el("div", { class: "node-card" }, kids);
  }

  async function refresh() {
    let data;
    try {
      data = await api("/cluster/nodes");
    } catch (e) {
      $("#scaleInfo").textContent = `router unreachable — ${e.message}`;
      return;
    }

    renderScale(data);

    const box = $("#nodeCards");
    box.innerHTML = "";

    if (!data.nodes.length) {
      box.appendChild(el("div", { class: "panel" }, [
        el("div", { class: "empty" }, [
          el("div", {}, "No nodes running."),
          el("div", { class: "muted", style: "margin-top:6px" },
            "Press “+ node” (or Scale) to spawn your first cache node."),
        ]),
      ]));
      return;
    }

    for (const node of data.nodes) box.appendChild(nodeCard(node));
  }

  async function scaleTo(target) {
    const btns = ["#scaleUp", "#scaleDown", "#scaleSet"].map($);
    btns.forEach((b) => { b.disabled = true; });
    notice(`Scaling to ${target} node(s)…`, "warn");

    try {
      const r = await api("/cluster/nodes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target }),
      });

      const bits = [];
      if (r.added.length) bits.push(`+${r.added.length} started`);
      if (r.removed.length) bits.push(`−${r.removed.length} stopped`);
      if (r.failed.length) {
        bits.push(`${r.failed.length} FAILED (${r.failed[0].error || "?"})`);
      }
      const notReady = r.added.filter((a) => a.healthy === false).length;

      let msg = `Cluster at ${r.total} node(s): ${bits.join(", ") || "no change"}.`;
      if (notReady) {
        msg += ` ${notReady} node(s) not healthy yet — they gate through warm-up before serving traffic.`;
        notice(msg, "warn");
      } else {
        notice(msg, r.failed.length ? "err" : "ok");
      }
    } catch (e) {
      notice(`Scale failed — ${e.message}`, "err");
    }

    btns.forEach((b) => { b.disabled = false; });
    refresh();
  }

  // ---- wiring ----------------------------------------------------

  $("#scaleUp").addEventListener("click", () => {
    scaleTo(lastTotal + 1);
  });
  $("#scaleDown").addEventListener("click", () => {
    scaleTo(Math.max(0, lastTotal - 1));
  });
  $("#scaleSet").addEventListener("click", () => {
    scaleTo(Math.max(0, +$("#scaleTarget").value || 0));
  });

  registerView("cluster", {
    show() {
      notice("", "");
      refresh();
      timer = setInterval(refresh, 3000);
    },
    hide() {
      if (timer) { clearInterval(timer); timer = null; }
    },
  });

})();
