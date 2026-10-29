/* ATLAS Control Tower — the hub's module views (CONTROL_TOWER_PLAN.md stages C–F).

   Each view reads endpoints the tower already serves (or the read-only
   /api/hub/* views over the same records) and draws them in the hub's look.
   Nothing here writes, starts a run or changes a record; actions that do
   (run controls, Human Actions, contact edits) open the existing page that
   owns them. Every number shows where it came from; what has no source says
   so instead of showing something made up.

     window.HubViews[id] = {title, render(el, ctx)}   ctx: {data(), openList, fmt, esc, dmy}
*/
(function () {
  "use strict";
  const CSS = `
.hv{display:flex;flex-direction:column;gap:16px}
.hv-head{display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap}
.hv-head h2{margin:0;font-size:24px;letter-spacing:-.02em}
.hv-head p{margin:2px 0 0;color:var(--hub-ink-3);font-size:13px}
.hv-head .sp{flex:1}
.hv-card{background:var(--hub-surface);border:1px solid var(--hub-line-2);border-radius:var(--hub-r-lg);box-shadow:var(--hub-shadow);overflow:hidden}
.hv-card>h3{margin:0;padding:14px 18px 4px;font-size:15px}
.hv-card>.hv-src{padding:0 18px 10px}
.hv-pad{padding:4px 18px 16px}
.hv-src{font-size:11.5px;color:var(--hub-ink-3)}
.hv-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px}
.hv-tile{background:var(--hub-surface);border:1px solid var(--hub-line-2);border-radius:16px;padding:14px 16px;box-shadow:var(--hub-shadow)}
.hv-tile .k{font-size:12px;color:var(--hub-ink-3)}
.hv-tile .v{font-size:26px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;margin-top:2px}
.hv-tile .d{font-size:11.5px;color:var(--hub-ink-3);margin-top:4px;line-height:1.35}
.hv-tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.hv-tools input,.hv-tools select{height:34px;border-radius:10px;border:1px solid var(--hub-line);background:var(--hub-surface);color:var(--hub-ink);
  padding:0 12px;font:inherit;font-size:13px}
.hv-tools input{flex:1 1 260px;min-width:0}
.hv-tbl{width:100%;border-collapse:collapse;font-size:13px}
.hv-tbl th{position:sticky;top:0;background:var(--hub-surface-2);text-align:left;font-size:11.5px;font-weight:600;color:var(--hub-ink-3);
  padding:8px 12px;white-space:nowrap}
.hv-tbl td{padding:9px 12px;border-top:1px solid var(--hub-line-2);vertical-align:top}
.hv-tbl td.n,.hv-tbl th.n{text-align:right;font-variant-numeric:tabular-nums}
.hv-tbl tr.click{cursor:pointer}
.hv-tbl tr.click:hover td{background:var(--hub-blue-wash)}
.hv-scroll{overflow:auto;max-height:640px}
.hv-mono{font-family:var(--hub-mono);font-size:12.5px}
.hv-more{display:flex;justify-content:center;padding:10px}
.hv-list{list-style:none;margin:0;padding:0}
.hv-list li{display:flex;gap:12px;padding:12px 18px;border-top:1px solid var(--hub-line-2);align-items:flex-start}
.hv-list li:first-child{border-top:0}
.hv-dot{flex:none;width:9px;height:9px;border-radius:50%;margin-top:6px;background:var(--hub-ink-4)}
.hv-dot.red{background:var(--hub-red)}.hv-dot.amber{background:var(--hub-amber)}.hv-dot.green{background:var(--hub-green)}.hv-dot.blue{background:var(--hub-blue)}
.hv-li-b{flex:1;min-width:0}
.hv-li-b b{display:block;font-size:13.5px}
.hv-li-b span{display:block;font-size:12.5px;color:var(--hub-ink-3);margin-top:2px}
.hv-grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:980px){.hv-grid2{grid-template-columns:1fr}}
.hv-kv{display:grid;grid-template-columns:minmax(140px,40%) 1fr;gap:6px 14px;font-size:13px}
.hv-kv dt{color:var(--hub-ink-3)}
.hv-kv dd{margin:0;font-variant-numeric:tabular-nums;word-break:break-word}
.hv-empty{padding:26px 18px;text-align:center;color:var(--hub-ink-3)}
.hv-empty b{display:block;color:var(--hub-ink);font-size:15px;margin-bottom:4px}
.hv-bar{height:6px;border-radius:3px;background:var(--hub-line);overflow:hidden;min-width:60px}
.hv-bar i{display:block;height:100%;background:var(--hub-blue);border-radius:3px}
.hv-drawer{position:fixed;top:0;right:0;bottom:0;width:min(520px,100%);z-index:60;background:var(--hub-surface);box-shadow:var(--hub-shadow-lg);
  transform:translateX(102%);transition:transform .35s var(--hub-e);display:flex;flex-direction:column}
.hv-drawer.on{transform:none}
.hv-drawer header{display:flex;align-items:flex-start;gap:12px;padding:18px 20px 10px;border-bottom:1px solid var(--hub-line-2)}
.hv-drawer header h3{margin:0;font-size:19px}
.hv-drawer header .sp{flex:1}
.hv-drawer .body{overflow:auto;padding:12px 20px 30px;display:flex;flex-direction:column;gap:18px}
.hv-drawer h4{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--hub-ink-3)}
.hv-tl{list-style:none;margin:0;padding:0 0 0 14px;border-left:2px solid var(--hub-line)}
.hv-tl li{position:relative;padding:0 0 12px 10px;font-size:13px}
.hv-tl li::before{content:"";position:absolute;left:-20px;top:4px;width:10px;height:10px;border-radius:50%;background:var(--hub-blue);border:2px solid var(--hub-surface)}
.hv-tl small{display:block;color:var(--hub-ink-3);font-size:11.5px}
.hv-x{width:32px;height:32px;border-radius:50%;border:0;background:var(--hub-surface-2);cursor:pointer;color:var(--hub-ink-2)}
.hv-steps{display:flex;gap:3px;margin-top:4px}
.hv-steps i{width:16px;height:6px;border-radius:2px;background:var(--hub-line)}
.hv-steps i.y{background:var(--hub-green)}.hv-steps i.n{background:var(--hub-red)}
.hv-links{display:flex;gap:8px;flex-wrap:wrap}
@media (prefers-reduced-motion:reduce){.hv-drawer{transition:none}}`;
  function css() {
    if (document.getElementById("hubviews-css")) return;
    const s = document.createElement("style"); s.id = "hubviews-css"; s.textContent = CSS; document.head.appendChild(s);
  }
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const fmt = (n) => (n == null || n === "" ? "—" : typeof n === "number" ? n.toLocaleString("en-US") : esc(n));
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const dmy = (s) => (s && /^\d{4}-\d\d-\d\d/.test(s) ? `${+s.slice(8, 10)} ${MON[+s.slice(5, 7) - 1]} ${s.slice(0, 4)}` : s ? esc(s) : "—");
  async function get(url) {
    try {
      const r = await fetch(url, {credentials: "same-origin"});
      if (r.status === 401) { location.replace("/login?next=" + encodeURIComponent("/hub" + location.hash)); return {status: 401}; }
      let data = null;
      try { data = await r.json(); } catch (e) { data = null; }
      return {status: r.status, data};
    } catch (e) { return {status: 0, data: null}; }
  }
  const empty = (title, text) => `<div class="hv-empty"><b>${esc(title)}</b>${esc(text || "")}</div>`;
  const denied = (what) => empty("You don't have access to " + what, "Ask an administrator if you need it.");
  const head = (title, sub, right) => `<div class="hv-head"><div><h2>${esc(title)}</h2>${sub ? `<p>${sub}</p>` : ""}</div><span class="sp"></span>${right || ""}</div>`;
  const tile = (k, v, d) => `<div class="hv-tile"><div class="k">${esc(k)}</div><div class="v">${v}</div>${d ? `<div class="d">${d}</div>` : ""}</div>`;
  const pill = (href, text, primary) => `<a class="hub-pill${primary ? " primary" : ""}" href="${esc(href)}">${esc(text)}</a>`;
  const V = window.HubViews = window.HubViews || {};
  let live = null;                         // the view's refresh timer, cleared on leaving it
  const stop = () => { if (live) { clearInterval(live); live = null; } };

  /* ── Shipment Tracking (stage C) ─────────────────────────────────────── */
  let CACHE = null;
  V.tracking = {
    title: "Shipment Tracking",
    async render(el, ctx) {
      stop();
      el.innerHTML = head("Shipment Tracking", "Every shipment in the eHub export; open one to see its fields, where each came from, and the ATA run's record for it.") +
        `<div class="hv-card"><div class="hv-pad" style="padding-top:14px"><div class="hv-tools">
          <input id="hvQ" type="search" placeholder="Search AWB/BOL, BU, step, forwarder, agent" aria-label="Search shipments">
          <select id="hvStage" aria-label="Stage"><option value="all">Every stage</option></select>
          <select id="hvBu" aria-label="Business unit"><option value="">Every BU</option></select>
          <select id="hvMode" aria-label="Mode"><option value="">Every mode</option></select>
        </div></div><div id="hvBody">${empty("Loading…")}</div></div>`;
      const d = ctx.data() || {};
      const st = (d.clearance && d.clearance.stages) || [];
      document.getElementById("hvStage").insertAdjacentHTML("beforeend", st.map((s) => `<option value="${esc(s.id)}">${esc(s.label)} (${fmt(s.count)})</option>`).join("") +
        '<option value="pastdue">Open and past due</option><option value="late">Not on time</option>');
      document.getElementById("hvBu").insertAdjacentHTML("beforeend", (d.bus || []).map((b) => b.bu).concat((d.unplaced || []).map((u) => u.bu)).sort()
        .map((b) => `<option>${esc(b)}</option>`).join(""));
      const body = document.getElementById("hvBody");
      let rows = [], shown = 100;
      async function load() {
        const stage = document.getElementById("hvStage").value;
        body.innerHTML = empty("Loading…");
        const r = await get("/api/hub/list?stage=" + encodeURIComponent(stage));
        if (r.status === 403) { body.innerHTML = denied("the eHub export"); return; }
        if (!r.data || r.data.empty) { body.innerHTML = empty("No eHub export loaded", "Shipments appear once an export is loaded on the Clearance page."); return; }
        CACHE = r.data; rows = r.data.rows || [];
        const modes = [...new Set(rows.map((x) => x.mode).filter(Boolean))].sort();
        const sel = document.getElementById("hvMode"), keep = sel.value;
        sel.innerHTML = '<option value="">Every mode</option>' + modes.map((m) => `<option${m === keep ? " selected" : ""}>${esc(m)}</option>`).join("");
        shown = 100; draw();
      }
      function draw() {
        const q = document.getElementById("hvQ").value.trim().toLowerCase().replace(/[\s\-./]/g, "");
        const bu = document.getElementById("hvBu").value, mode = document.getElementById("hvMode").value;
        const X = rows.filter((x) => (!bu || x.bu === bu) && (!mode || x.mode === mode) &&
          (!q || ["awb", "bu", "step", "ffw", "agent", "mode", "status"].some((k) => String(x[k] || "").toLowerCase().replace(/[\s\-./]/g, "").includes(q))));
        if (!X.length) { body.innerHTML = empty("No shipment matches", "Change the search or the filters."); return; }
        body.innerHTML = `<div class="hv-scroll"><table class="hv-tbl"><thead><tr><th>AWB / BOL</th><th>BU</th><th>Mode</th><th>Current step</th><th>ETA</th><th>ATA</th>
          <th class="n">Days past due</th><th>On time</th><th>Forwarder</th><th>Clearing agent</th></tr></thead><tbody>` +
          X.slice(0, shown).map((x) => `<tr class="click" tabindex="0" data-ref="${esc(x.awb)}"><td class="hv-mono">${esc(x.awb)}</td><td>${esc(x.bu)}</td><td>${esc(x.mode)}</td>
            <td>${esc(x.step)}</td><td>${dmy(x.eta)}</td><td>${dmy(x.ata)}</td><td class="n" style="${x.overdue > 0 ? "color:var(--hub-red);font-weight:600" : ""}">${x.overdue == null ? "—" : fmt(x.overdue)}</td>
            <td>${x.ontime ? "Yes" : "No"}</td><td>${esc(x.ffw || "—")}</td><td>${esc(x.agent || "—")}</td></tr>`).join("") +
          `</tbody></table></div><div class="hv-more"><span class="hv-src">Showing ${fmt(Math.min(shown, X.length))} of ${fmt(X.length)} · ${esc("eHub export, data as of ")}${dmy(CACHE.asof)}</span>
          ${X.length > shown ? '<button class="hub-pill" id="hvMore" type="button">Show more</button>' : ""}</div>`;
        const more = document.getElementById("hvMore");
        if (more) more.onclick = () => { shown += 200; draw(); };
      }
      body.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-ref]"); if (tr) openShipment(tr.dataset.ref); });
      body.addEventListener("keydown", (e) => { const tr = e.target.closest("tr[data-ref]"); if (tr && e.key === "Enter") openShipment(tr.dataset.ref); });
      document.getElementById("hvQ").addEventListener("input", () => { shown = 100; draw(); });
      document.getElementById("hvBu").onchange = document.getElementById("hvMode").onchange = () => { shown = 100; draw(); };
      document.getElementById("hvStage").onchange = load;
      await load();
      const ref = new URLSearchParams(location.hash.split("?")[1] || "").get("ref");
      if (ref) openShipment(ref);
    }
  };

  let drawer = null;
  function closeDrawer() {
    if (!drawer) return;
    const d = drawer; drawer = null;
    d.classList.remove("on"); setTimeout(() => d.remove(), 360);
    document.removeEventListener("keydown", drawerKey, true);
  }
  function drawerKey(e) { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeDrawer(); } }
  async function openShipment(ref) {
    closeDrawer();
    const d = document.createElement("aside");
    d.className = "hv-drawer"; d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true"); d.setAttribute("aria-label", "Shipment " + ref);
    d.innerHTML = `<header><div><div class="hv-src">Shipment</div><h3 class="hv-mono">${esc(ref)}</h3></div><span class="sp"></span>
      <button class="hv-x" type="button" aria-label="Close">✕</button></header><div class="body">${empty("Loading…")}</div>`;
    document.body.appendChild(d); drawer = d;
    requestAnimationFrame(() => d.classList.add("on"));
    d.querySelector(".hv-x").onclick = closeDrawer;
    d.querySelector(".hv-x").focus();
    document.addEventListener("keydown", drawerKey, true);
    const r = await get("/api/hub/shipment?ref=" + encodeURIComponent(ref));
    const body = d.querySelector(".body");
    if (r.status === 404) { body.innerHTML = empty("No shipment with that AWB/BOL", "Neither the eHub export nor the current run has it."); return; }
    if (r.status === 403) { body.innerHTML = denied("this shipment"); return; }
    if (!r.data) { body.innerHTML = empty("Could not load the shipment"); return; }
    const s = r.data, val = (f) => f.value == null || f.value === "" ? "—" : f.kind === "date" ? dmy(f.value) : f.kind === "bool" ? (f.value ? "Yes" : "No") : fmt(f.value);
    let html = "";
    if (s.export) for (const row of s.export.rows) {
      const ach = String(row.achieved || "");
      html += `<section><h4>From the eHub export</h4><dl class="hv-kv">${row.fields.map((f) => `<dt>${esc(f.label)}</dt><dd>${val(f)}</dd>`).join("")}
        ${ach ? `<dt>Step targets achieved (1–9)</dt><dd><div class="hv-steps" title="${esc(ach)}">${[...ach].map((c) => `<i class="${c === "Y" ? "y" : c === "N" ? "n" : ""}"></i>`).join("")}</div></dd>` : ""}
        </dl><p class="hv-src">${esc(s.export.source)}</p></section>`;
    } else html += `<section><h4>From the eHub export</h4><p class="hv-src">Not in the loaded export (or not in your business units).</p></section>`;
    if (s.run) html += `<section><h4>From the ATA run</h4><dl class="hv-kv">${s.run.fields.map((f) => `<dt>${esc(f.label)}</dt><dd>${val(f)}</dd>`).join("")}</dl>
      <p class="hv-src">${esc(s.run.source)} · ${pill("/#shipments?ref=" + encodeURIComponent(ref), "Open in the run")}</p></section>`;
    else html += `<section><h4>From the ATA run</h4><p class="hv-src">Not in the current run.</p></section>`;
    html += `<section><h4>Timeline</h4>${s.timeline.length ? `<ul class="hv-tl">${s.timeline.map((t) => `<li><b>${esc(t.label)}</b> · ${dmy(t.date)}<small>${esc(t.source)}</small></li>`).join("")}</ul>`
      : '<p class="hv-src">No dated events.</p>'}</section>`;
    html += `<div class="hv-links">${pill("/clearance", "Open Clearance")}${pill("/#atlas", "Ask ATLAS")}</div>`;
    body.innerHTML = html;
  }

  /* ── ATA Automation workspace (stage D) ───────────────────────────────── */
  V.ata = {
    title: "ATA Automation",
    async render(el) {
      stop();
      el.innerHTML = head("ATA Automation", "The Hub ETA / ATA run as the automation reports it. Start, stop and Human Actions stay on the run page, unchanged.",
        `<div class="hv-links">${pill("/#overview", "Run controls", true)}${pill("/#human", "Human Actions")}${pill("/#logs", "Logs")}</div>`) + `<div id="hvAta">${empty("Loading…")}</div>`;
      const box = el.querySelector("#hvAta");
      let filter = "";
      async function load() {
        const r = await get("/api/state");
        if (r.status === 403) { box.innerHTML = denied("the run"); stop(); return; }
        if (!r.data) { box.innerHTML = empty("The run's state could not be read"); return; }
        const s = r.data, run = s.run || {}, ships = s.shipments || [], by = {};
        for (const x of ships) by[x.state || "unknown"] = (by[x.state || "unknown"] || 0) + 1;
        const prog = s.progress || {};
        const hq = (s.human_queue || []).filter((h) => !/SUCCESS|TIMEOUT|LOST|FAILED|NOT_CONFIRMED/.test(h.status || ""));
        const states = Object.keys(by).sort();
        const X = filter ? ships.filter((x) => (x.state || "unknown") === filter) : ships;
        box.innerHTML = `<div class="hv-tiles">
          ${tile("Run", esc(run.status || "No run yet"), run.run_id ? `Run ${esc(run.run_id)}${run.dry_run ? " · dry run" : ""}` : "")}
          ${tile("Shipments in this run", fmt(ships.length), prog.total ? `processed ${fmt(prog.processed)} of ${fmt(prog.total)}` : "")}
          ${tile("Written to the Hub", fmt((by.updated || 0) + (by.partial || 0)), "read back after writing")}
          ${tile("Failed", fmt(by.failed || 0))}
          ${tile("Waiting for a person", fmt(by.waiting_for_human || 0), hq.length ? `${fmt(hq.length)} in the Human Action queue` : "")}
          ${tile("Heartbeat", run.heartbeat_age == null ? "—" : fmt(run.heartbeat_age) + " s", run.started_at ? "started " + esc(String(run.started_at).replace("T", " ").slice(0, 16)) : "")}
        </div>
        ${s.human_action ? `<div class="hv-card"><h3>A person is needed now</h3><div class="hv-pad"><b>${esc(s.human_action.reference || "")}</b> · ${esc(s.human_action.reason || s.human_action.status || "")}
          <p class="hv-src">Open it from Human Actions; the run waits and resumes by itself.</p>${pill("/#human", "Human Actions", true)}</div></div>` : ""}
        ${s.recovery ? `<div class="hv-card"><h3>Recovering from an error</h3><div class="hv-pad">${esc(s.recovery.summary || s.recovery.error || "")}</div></div>` : ""}
        <div class="hv-card"><h3>Shipments in this run</h3><div class="hv-pad"><div class="hv-tools">
          <select id="hvSt" aria-label="Run state"><option value="">Every state (${fmt(ships.length)})</option>${states.map((k) => `<option value="${esc(k)}"${k === filter ? " selected" : ""}>${esc(k.replace(/_/g, " "))} (${fmt(by[k])})</option>`).join("")}</select>
          <span class="hv-src">Refreshes every 10 s · ${pill("/api/report.pdf", "Run PDF")} ${pill("/api/export.csv", "CSV")}</span></div></div>
          ${X.length ? `<div class="hv-scroll"><table class="hv-tbl"><thead><tr><th>Reference</th><th>Carrier</th><th>State</th><th>Last step</th><th>Carrier ETA</th><th>Carrier ATA</th><th>Outcome</th><th>Updated</th></tr></thead><tbody>
            ${X.slice(0, 300).map((x) => `<tr><td class="hv-mono">${esc(x.reference)}</td><td>${esc(x.carrier || "—")}</td><td>${esc(String(x.state || "").replace(/_/g, " "))}</td>
            <td>${esc(x.step || "—")}</td><td>${esc(x.provider_eta || "—")}</td><td>${esc(x.provider_ata || "—")}</td><td>${esc(x.outcome || x.error || "—")}</td><td>${esc(x.updated || "—")}</td></tr>`).join("")}
            </tbody></table></div>` : empty(ships.length ? "No shipment in that state" : "No shipments in this run", ships.length ? "" : "They appear while a run works through the Hub.")}</div>`;
        const sel = box.querySelector("#hvSt");
        if (sel) sel.onchange = () => { filter = sel.value; load(); };
      }
      await load();
      live = setInterval(() => { if (!document.hidden) load(); }, 10000);
    }
  };

  /* ── To Do & Approvals (stage E) ──────────────────────────────────────── */
  V.todo = {
    title: "To Do & Approvals",
    async render(el) {
      stop();
      el.innerHTML = head("To Do & Approvals", "What needs a person across the platform: Human Actions, run failures, PO approvals, clearance follow-ups. Highest priority first.") + `<div id="hvTodo">${empty("Loading…")}</div>`;
      const box = el.querySelector("#hvTodo");
      const r = await get("/api/todo");
      if (!r.data) { box.innerHTML = empty("The list could not be read"); return; }
      const t = r.data, LBL = {overdue: "Overdue", urgent: "Urgent", pending_approval: "Waiting for approval", open: "Open", completed: "Completed (last 7 days)"};
      let filter = "";
      function draw() {
        const X = (t.items || []).filter((i) => !filter || i.state === filter);
        box.innerHTML = `<div class="hv-tiles">${Object.keys(LBL).map((k) => tile(LBL[k], fmt((t.by_state || {})[k] || 0))).join("")}</div>
          <div class="hv-card"><div class="hv-pad" style="padding-top:14px"><div class="hub-seg" role="group" aria-label="Show">
            ${[["", "All"]].concat(Object.entries(LBL)).map(([k, l]) => `<button type="button" data-f="${k}" aria-pressed="${k === filter}">${esc(l.split(" (")[0])}</button>`).join("")}</div></div>
          ${X.length ? `<ul class="hv-list">${X.map((i) => `<li><span class="hv-dot ${i.state === "completed" ? "green" : esc(i.level)}"></span><div class="hv-li-b"><b>${esc(i.title)}</b>
            <span>${esc(i.detail || "")}</span><span>${esc(LBL[i.state] || i.state)} · ${esc(i.source || "")}${i.due ? " · due " + dmy(i.due) : ""}</span></div>
            ${i.href ? `<a class="hub-pill" href="${esc(i.href)}">Open</a>` : ""}</li>`).join("")}</ul>` : empty("Nothing here", filter ? "No task in this state." : "Nothing needs a person right now.")}</div>`;
        box.querySelectorAll("[data-f]").forEach((b) => { b.onclick = () => { filter = b.dataset.f; draw(); }; });
      }
      draw();
    }
  };

  /* ── Documents (stage E): what exists ─────────────────────────────────── */
  V.documents = {
    title: "Documents",
    async render(el) {
      stop();
      el.innerHTML = head("Documents", "PO documents (Bill of Entry PDFs and the duty templates made from them) and the run's evidence. The eHub export carries no clearance-document list, so missing documents are not claimed here.") +
        `<div class="hv-grid2"><div class="hv-card" id="hvPo"><h3>PO documents</h3>${empty("Loading…")}</div><div class="hv-card" id="hvEv"><h3>Run evidence</h3>${empty("Loading…")}</div></div>`;
      const [po, ev] = await Promise.all([get("/api/po"), get("/api/evidence?limit=40")]);
      const P = el.querySelector("#hvPo"), E = el.querySelector("#hvEv");
      if (po.status === 403) P.innerHTML = "<h3>PO documents</h3>" + denied("PO documents");
      else if (!po.data) P.innerHTML = "<h3>PO documents</h3>" + empty("Could not be read");
      else {
        const jobs = po.data.jobs || [];
        P.innerHTML = `<h3>PO documents</h3><p class="hv-src">${fmt(jobs.length)} most recent jobs · ${pill("/#po", "Open PO Automation")}</p>` + (jobs.length ? `<div class="hv-scroll"><table class="hv-tbl"><thead><tr><th>Reference</th><th>Document</th><th>State</th><th>Validation</th></tr></thead><tbody>
          ${jobs.map((j) => `<tr class="click" data-href="/#po?po=${encodeURIComponent(j.po_id)}"><td class="hv-mono">${esc(j.reference || "—")}</td><td>${esc(j.document || j.doctype || "—")}</td><td>${esc(j.label || j.state)}</td><td>${esc(j.validation || "—")}</td></tr>`).join("")}</tbody></table></div>`
          : empty("No PO documents yet"));
      }
      if (ev.status === 403) E.innerHTML = "<h3>Run evidence</h3>" + denied("run evidence");
      else if (!Array.isArray(ev.data)) E.innerHTML = "<h3>Run evidence</h3>" + empty("Could not be read");
      else E.innerHTML = `<h3>Run evidence</h3><p class="hv-src">Screenshots and page text the run kept, newest first</p>` + (ev.data.length ? `<div class="hv-scroll"><table class="hv-tbl"><thead><tr><th>Reference</th><th>Carrier</th><th>What</th><th>When</th><th></th></tr></thead><tbody>
          ${ev.data.map((x) => `<tr><td class="hv-mono">${esc(x.reference || "—")}</td><td>${esc(x.carrier || x.provider || "—")}</td><td>${esc(x.event || x.step || x.name || x.source || "—")}</td><td>${esc(String(x.at || "").replace("T", " ").slice(0, 16))}</td>
          <td><a href="${esc(x.url)}" target="_blank" rel="noopener">Open</a></td></tr>`).join("")}</tbody></table></div>` : empty("No evidence kept yet"));
      el.querySelectorAll("tr[data-href]").forEach((tr) => { tr.onclick = () => { location.href = tr.dataset.href; }; });
    }
  };

  /* ── Carriers (stage E) ───────────────────────────────────────────────── */
  const pc = (v) => v == null ? "—" : Math.round(v * 100) + "%";
  V.carriers = {
    title: "Carriers",
    async render(el) {
      stop();
      el.innerHTML = head("Carriers", "How each carrier did in the run records: dates read, verified Hub updates, failures, and when a person was needed.") + `<div id="hvCa">${empty("Loading…")}</div>`;
      const box = el.querySelector("#hvCa"), r = await get("/api/hub/carriers");
      if (r.status === 403) { box.innerHTML = denied("carrier results"); return; }
      if (!r.data) { box.innerHTML = empty("Carrier results could not be read"); return; }
      const h = r.data, cs = h.carriers || [];
      box.innerHTML = `<div class="hv-card"><h3>Last ${fmt(h.window_days)} days</h3><p class="hv-src" style="padding:0 18px 10px">${fmt(h.records)} finished shipments in the window · rates shown once a carrier has ${fmt(h.minimum)} or more · run records (telemetry)</p>
        ${cs.length ? `<div class="hv-scroll"><table class="hv-tbl"><thead><tr><th>Carrier</th><th class="n">Shipments</th><th class="n">Dates read</th><th class="n">Verified updates</th><th class="n">Failed</th><th class="n">Needed a person</th><th class="n">Median time</th><th>Top causes</th></tr></thead><tbody>
        ${cs.map((c) => `<tr><td><b>${esc(c.label || c.carrier)}</b>${c.sufficient ? "" : '<div class="hv-src">too few to rate</div>'}</td><td class="n">${fmt(c.counts.shipments)}</td>
          <td class="n">${c.sufficient ? pc(c.rates.dates_read) : fmt(c.counts.dates_read)}</td><td class="n">${c.sufficient ? pc(c.rates.verified_updates) : fmt(c.counts.verified_updates)}</td>
          <td class="n">${fmt(c.counts.failed)}</td><td class="n">${c.sufficient ? pc(c.rates.needed_person) : fmt(c.counts.needed_person)}</td>
          <td class="n">${c.median_seconds == null ? "—" : fmt(c.median_seconds) + " s"}</td><td>${esc((c.top_causes || []).map((x) => x[0] + " (" + x[1] + ")").join(", ") || "—")}</td></tr>`).join("")}</tbody></table></div>`
          : empty("No finished shipments in the window", "Carrier results appear after runs.")}</div>
        <div class="hv-card"><h3>This run</h3>${(h.this_run || []).length ? `<div class="hv-scroll"><table class="hv-tbl"><thead><tr><th>Carrier</th><th class="n">Shipments</th><th>By state</th></tr></thead><tbody>
          ${h.this_run.map((c) => `<tr><td><b>${esc(c.carrier)}</b></td><td class="n">${fmt(c.shipments)}</td><td>${esc(Object.entries(c.by_state).map(([k, v]) => k.replace(/_/g, " ") + " " + v).join(" · "))}</td></tr>`).join("")}</tbody></table></div>`
          : empty("No run in progress")}</div>`;
    }
  };

  /* ── Analytics & KPIs (stage F): the catalogue ────────────────────────── */
  V.analytics = {
    title: "Analytics & KPIs",
    async render(el) {
      stop();
      el.innerHTML = head("Analytics & KPIs", "Each KPI with its definition, numerator and denominator, counted from the loaded eHub export.") + `<div id="hvK">${empty("Loading…")}</div>`;
      const box = el.querySelector("#hvK"), r = await get("/api/hub/kpis");
      if (r.status === 403) { box.innerHTML = denied("the eHub export"); return; }
      if (!r.data || r.data.empty) { box.innerHTML = empty("No eHub export loaded"); return; }
      const k = r.data, unit = (x) => x.value == null ? "—" : x.unit === "%" ? fmt(x.value) + "%" : fmt(x.value) + (x.unit === "days" ? " days" : "");
      box.innerHTML = `<div class="hv-tiles">${k.kpis.map((x) => tile(x.name, unit(x), esc(x.definition) + (x.num != null ? `<br><b>${fmt(x.num)}</b> of ${fmt(x.den)}` : x.den != null ? `<br>from ${fmt(x.den)}` : ""))).join("")}</div>
        <p class="hv-src">${esc(k.source)} · ${esc(k.period)}</p>
        <div class="hv-card"><h3>By business unit</h3><div class="hv-scroll"><table class="hv-tbl"><thead><tr><th>BU</th><th class="n">Shipments</th><th class="n">On time</th><th class="n">Open</th><th class="n">Past due</th><th class="n">Past due % of open</th><th></th></tr></thead><tbody>
          ${k.bus.map((b) => `<tr class="click" data-bu="${esc(b.bu)}"><td><b>${esc(b.bu)}</b></td><td class="n">${fmt(b.n)}</td><td class="n">${b.ontime_pct == null ? "—" : fmt(b.ontime_pct) + "%"}</td><td class="n">${fmt(b.open)}</td>
          <td class="n">${fmt(b.late)}</td><td class="n">${b.late_pct == null ? "—" : fmt(b.late_pct) + "%"}</td><td style="width:22%"><div class="hv-bar"><i style="width:${b.late_pct || 0}%;background:var(--hub-red)"></i></div></td></tr>`).join("")}
        </tbody></table></div></div>
        <div class="hv-card"><h3>Not available yet</h3><ul class="hv-list">${k.not_available.map((x) => `<li><span class="hv-dot"></span><div class="hv-li-b"><b>${esc(x.name)}</b><span>${esc(x.why)}</span></div></li>`).join("")}</ul></div>
        <div class="hv-links"><button class="hub-pill" type="button" id="hvKcsv">Download the catalogue (CSV)</button></div>`;
      box.querySelectorAll("tr[data-bu]").forEach((tr) => { tr.onclick = () => window.HubCtx && window.HubCtx.openList("all", tr.dataset.bu); });
      box.querySelector("#hvKcsv").onclick = () => {
        const q = (v) => /[",\n]/.test(String(v ?? "")) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v ?? "");
        const lines = [["KPI", "Value", "Unit", "Numerator", "Denominator", "Definition", "Source"].join(",")].concat(k.kpis.map((x) => [x.name, x.value, x.unit, x.num, x.den, x.definition, k.source].map(q).join(",")));
        const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["﻿" + lines.join("\r\n")], {type: "text/csv"}));
        a.download = "kpi_catalogue.csv"; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      };
    }
  };

  /* ── Reports (stage F) ────────────────────────────────────────────────── */
  V.reports = {
    title: "Reports",
    render(el) {
      stop();
      const R = [
        ["Run report (PDF)", "The current run: shipments, results, verification, evidence summary.", "/api/report.pdf", "runs.view"],
        ["Run shipments (CSV)", "Every shipment in the current run with its result.", "/api/export.csv", "runs.view"],
        ["Shipment lists (CSV)", "Any stage, BU or past-due list: open it from Overview or Shipment Tracking and choose Download CSV.", "/hub#tracking", "clearance.view"],
        ["KPI catalogue (CSV)", "Every KPI with its definition, numerator and denominator.", "/hub#analytics", "clearance.view"],
        ["Clearance follow-up", "Escalations, contacts and draft mails by agent and BU.", "/clearance", "clearance.view"],
      ];
      el.innerHTML = head("Reports", "The exports the platform already produces. Each opens with the same permissions as its page.") +
        `<div class="hv-card"><ul class="hv-list">${R.map(([t, d, h]) => `<li><span class="hv-dot blue"></span><div class="hv-li-b"><b>${esc(t)}</b><span>${esc(d)}</span></div>${pill(h, "Open")}</li>`).join("")}</ul></div>
        <div class="hv-card"><h3>Scheduled reports</h3><div class="hv-pad hv-src">Not connected: the platform does not send reports on a schedule yet, and nothing here pretends to.</div></div>`;
    }
  };

  /* ── Master Data (stage E) ────────────────────────────────────────────── */
  V.masterdata = {
    title: "Master Data",
    async render(el) {
      stop();
      el.innerHTML = head("Master Data", "Clearance contacts used for escalations and mails. Edit them on the Clearance page; this view only reads them.",
        pill("/clearance", "Edit on Clearance")) + `<div id="hvMd">${empty("Loading…")}</div>`;
      const box = el.querySelector("#hvMd"), r = await get("/api/clearance/contacts");
      if (r.status === 403) { box.innerHTML = denied("clearance contacts"); return; }
      if (!r.data) { box.innerHTML = empty("Contacts could not be read"); return; }
      const NAMES = {agents: "Clearing agents", bus: "Business units", forwarders: "Forwarders", coe: "Centre of excellence"};
      box.innerHTML = `<div class="hv-grid2">${Object.entries(r.data).map(([kind, list]) => {
        const rows = Object.entries(list || {});
        const keys = [...new Set(rows.flatMap(([, c]) => Object.keys(c || {})))].slice(0, 4);
        return `<div class="hv-card"><h3>${esc(NAMES[kind] || kind)} <span class="hv-src">${fmt(rows.length)}</span></h3>${rows.length ? `<div class="hv-scroll" style="max-height:360px"><table class="hv-tbl"><thead><tr><th>Name</th>${keys.map((k) => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>
          ${rows.map(([n, c]) => `<tr><td><b>${esc(n)}</b></td>${keys.map((k) => `<td>${esc(Array.isArray(c[k]) ? c[k].join(", ") : c[k] ?? "—")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty("None on file yet")}</div>`;
      }).join("")}</div>`;
    }
  };

  V._leave = () => { stop(); closeDrawer(); };
  V._css = css;
  V._openShipment = openShipment;
})();
