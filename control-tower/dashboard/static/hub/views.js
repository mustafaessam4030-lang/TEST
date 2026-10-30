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
.hv-list li.hv-note{padding:0 18px 12px 39px;border-top:0}
.hv .hub-seg{max-width:100%;overflow-x:auto;scrollbar-width:none}
.hv .hub-seg button{white-space:nowrap;flex:none}
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
  // The same id the run page uses for Human Action requests in this tab.
  function humanClient() {
    // signed in: the person's id, as the run page sends it; otherwise this tab's id
    if (window.HubCtx && window.HubCtx.me && window.HubCtx.me.user_id) return window.HubCtx.me.user_id;
    const make = () => Array.from(crypto.getRandomValues(new Uint8Array(8))).map((b) => b.toString(16).padStart(2, "0")).join("");
    try { let id = sessionStorage.getItem("ct_human_client"); if (!id) { id = make(); sessionStorage.setItem("ct_human_client", id); } return id; }
    catch (e) { return make(); }
  }
  // A link to an existing page ("/#po?po=…", "/clearance#bu=EG") opens that page inside the hub.
  // Human Action has no page in the hub: its tasks, with Open and Resume, are on To Do.
  const PAGE_OF = {overview: "ata", shipments: "runs", po: "po", human: "todo", atlas: "atlas", settings: "settings",
                   access: "access", analysis: "history", exceptions: "history", logs: "history"};
  function hubHref(h) {
    const u = String(h || "");
    if (/^\/clearance(\b|$|#)/.test(u)) { const f = u.split("#")[1]; return "/hub#clearance" + (f ? "?" + f : ""); }
    const m = /^\/#([a-z]+)(?:\?(.*))?$/.exec(u);
    if (m && PAGE_OF[m[1]]) return "/hub#" + PAGE_OF[m[1]] + (m[2] ? "?" + m[2] : "");
    return u;
  }
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
        if (r.status !== 200) { body.innerHTML = empty("The shipments could not be loaded", "Check the connection, then change a filter to try again."); return; }
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
            <td>${esc(x.step)}</td><td>${dmy(x.eta)}</td><td>${dmy(x.ata)}</td><td class="n" style="${x.overdue > 0 ? "color:var(--hub-red-ink);font-weight:600" : ""}">${x.overdue == null ? "—" : fmt(x.overdue)}</td>
            <td>${x.ontime ? "Yes" : "No"}</td><td>${esc(x.ffw || "—")}</td><td>${esc(x.agent || "—")}</td></tr>`).join("") +
          `</tbody></table></div><div class="hv-more"><span class="hv-src">Showing ${fmt(Math.min(shown, X.length))} of ${fmt(X.length)} · ${esc("eHub export, data as of ")}${dmy(CACHE.asof)}</span>
          ${X.length > shown ? '<button class="hub-pill" id="hvMore" type="button">Show more</button>' : ""}</div>`;
        const more = document.getElementById("hvMore");
        if (more) more.onclick = () => { shown += 200; draw(); };
      }
      body.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-ref]"); if (tr) openShipment(tr.dataset.ref); });
      body.addEventListener("keydown", (e) => { const tr = e.target.closest("tr[data-ref]"); if (tr && e.key === "Enter") { e.preventDefault(); openShipment(tr.dataset.ref); } });
      document.getElementById("hvQ").addEventListener("input", () => { shown = 100; draw(); });
      document.getElementById("hvBu").onchange = document.getElementById("hvMode").onchange = () => { shown = 100; draw(); };
      document.getElementById("hvStage").onchange = load;
      await load();
      const ref = new URLSearchParams(location.hash.split("?")[1] || "").get("ref");
      if (ref) openShipment(ref);
    }
  };

  let drawer = null;
  let drawerBack = null;
  function closeDrawer() {
    if (!drawer) return;
    const d = drawer; drawer = null;
    d.classList.remove("on"); setTimeout(() => d.remove(), 360);
    document.removeEventListener("keydown", drawerKey, true);
    document.removeEventListener("pointerdown", drawerOutside, true);
    // focus goes back to the row that opened it
    if (drawerBack && drawerBack.isConnected) { try { drawerBack.focus({preventScroll: true}); } catch (e) { /* gone */ } }
    drawerBack = null;
  }
  // a click outside the panel closes it, like Esc
  function drawerOutside(e) { if (drawer && !drawer.contains(e.target) && !e.target.closest("tr[data-ref]")) closeDrawer(); }
  function drawerKey(e) { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeDrawer(); } }
  async function openShipment(ref) {
    closeDrawer();
    drawerBack = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
    const d = document.createElement("aside");
    d.className = "hv-drawer"; d.setAttribute("role", "dialog"); d.setAttribute("aria-modal", "true"); d.setAttribute("aria-label", "Shipment " + ref);
    d.innerHTML = `<header><div><div class="hv-src">Shipment</div><h3 class="hv-mono">${esc(ref)}</h3></div><span class="sp"></span>
      <button class="hv-x" type="button" aria-label="Close">✕</button></header><div class="body">${empty("Loading…")}</div>`;
    document.body.appendChild(d); drawer = d;
    requestAnimationFrame(() => d.classList.add("on"));
    d.querySelector(".hv-x").onclick = closeDrawer;
    d.querySelector(".hv-x").focus();
    document.addEventListener("keydown", drawerKey, true);
    setTimeout(() => document.addEventListener("pointerdown", drawerOutside, true), 0);
    const r = await get("/api/hub/shipment?ref=" + encodeURIComponent(ref));
    const body = d.querySelector(".body");
    if (r.status === 404) { body.innerHTML = empty("No shipment with that AWB/BOL", "Neither the eHub export nor the current run has it."); return; }
    if (r.status === 403) { body.innerHTML = denied("this shipment"); return; }
    if (r.status !== 200 || !r.data) { body.innerHTML = empty("Could not load the shipment"); return; }
    const s = r.data, val = (f) => f.value == null || f.value === "" ? "—" : f.kind === "date" ? dmy(f.value) : f.kind === "bool" ? (f.value ? "Yes" : "No") : fmt(f.value);
    let html = "";
    if (s.export) for (const row of s.export.rows) {
      const ach = String(row.achieved || "");
      html += `<section><h4>From the eHub export</h4><dl class="hv-kv">${row.fields.map((f) => `<dt>${esc(f.label)}</dt><dd>${val(f)}</dd>`).join("")}
        ${ach ? `<dt>Step targets achieved (1–9)</dt><dd><div class="hv-steps" title="${esc(ach)}">${[...ach].map((c) => `<i class="${c === "Y" ? "y" : c === "N" ? "n" : ""}"></i>`).join("")}</div></dd>` : ""}
        </dl><p class="hv-src">${esc(s.export.source)}</p></section>`;
    } else html += `<section><h4>From the eHub export</h4><p class="hv-src">Not in the loaded export (or not in your business units).</p></section>`;
    if (s.run) html += `<section><h4>From the ATA run</h4><dl class="hv-kv">${s.run.fields.map((f) => `<dt>${esc(f.label)}</dt><dd>${val(f)}</dd>`).join("")}</dl>
      <p class="hv-src">${esc(s.run.source)} · ${pill("/hub#runs?ref=" + encodeURIComponent(ref), "Open in the run")}</p></section>`;
    else html += `<section><h4>From the ATA run</h4><p class="hv-src">Not in the current run.</p></section>`;
    html += `<section><h4>Timeline</h4>${s.timeline.length ? `<ul class="hv-tl">${s.timeline.map((t) => `<li><b>${esc(t.label)}</b> · ${dmy(t.date)}<small>${esc(t.source)}</small></li>`).join("")}</ul>`
      : '<p class="hv-src">No dated events.</p>'}</section>`;
    html += `<div class="hv-links">${pill("/hub#clearance", "Open Clearance")}${pill("/hub#atlas", "Ask ATLAS")}</div>`;
    body.innerHTML = html;
  }

  /* ── To Do & Approvals (stage E) ──────────────────────────────────────── */
  V.todo = {
    title: "To Do & Approvals",
    async render(el) {
      stop();
      el.innerHTML = head("To Do & Approvals", "What needs a person across the platform: Human Actions, run failures, BOE approvals, clearance follow-ups. Highest priority first.") + `<div id="hvTodo">${empty("Loading…")}</div>`;
      const box = el.querySelector("#hvTodo");
      let r = await get("/api/todo");
      if (r.status !== 200 || !r.data) { box.innerHTML = empty("To Do could not be loaded", "Check the connection, then open To Do again."); return; }
      let t = r.data;
      const LBL = {overdue: "Overdue", urgent: "Urgent", pending_approval: "Waiting for approval", open: "Open", completed: "Completed (last 7 days)"};
      let filter = "";
      function draw() {
        const X = (t.items || []).filter((i) => !filter || i.state === filter);
        box.innerHTML = `<div class="hv-tiles">${Object.keys(LBL).map((k) => tile(LBL[k], fmt((t.by_state || {})[k] || 0))).join("")}</div>
          <div class="hv-card"><div class="hv-pad" style="padding-top:14px"><div class="hub-seg" role="group" aria-label="Show">
            ${[["", "All"]].concat(Object.entries(LBL)).map(([k, l]) => `<button type="button" data-f="${k}" aria-pressed="${k === filter}">${esc(l.split(" (")[0])}</button>`).join("")}</div></div>
          ${X.length ? `<ul class="hv-list">${X.map((i) => `<li><span class="hv-dot ${i.state === "completed" ? "green" : esc(i.level)}"></span><div class="hv-li-b"><b>${esc(i.title)}</b>
            <span>${esc(i.detail || "")}</span><span>${esc(LBL[i.state] || i.state)} · ${esc(i.source || "")}${i.due ? " · due " + dmy(i.due) : ""}</span></div>
            ${i.act && i.act.action_id ? `<span class="hv-links"><button class="hub-pill primary" type="button" data-hop="open" data-i="${esc(i.id)}">Open</button>
              <button class="hub-pill" type="button" data-hop="resume" data-i="${esc(i.id)}">Resume</button></span>`
              : i.href ? `<a class="hub-pill" href="${esc(hubHref(i.href))}">Open</a>` : ""}</li>
            ${i.act && NOTE[i.id] ? `<li class="hv-note" role="status"><span class="hv-src">${esc(NOTE[i.id])}</span></li>` : ""}`).join("")}</ul>` : empty("Nothing here", filter ? "No task in this state." : "Nothing needs a person right now.")}</div>`;
        box.querySelectorAll("[data-f]").forEach((b) => { b.onclick = () => { filter = b.dataset.f; draw(); }; });
        box.querySelectorAll("[data-hop]").forEach((b) => { b.onclick = () => human(b.dataset.hop, (t.items || []).find((x) => x.id === b.dataset.i)); });
      }
      // A Human Action, from the list: the same scoped request the run page sends (/api/human, open or
      // resume); the server and the run check it. Nothing is typed into the carrier page.
      const NOTE = {};
      async function human(op, item) {
        if (!item || !item.act) return;
        NOTE[item.id] = op === "open" ? "Asking the run to bring the carrier page to the front…" : "Sending Resume…"; draw();
        let res = null;
        try {
          const x = await fetch("/api/human", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({op, run_id: item.act.run_id, action_id: item.act.action_id, client_id: humanClient()})});
          res = await x.json().catch(() => null);
          if (x.status === 403) res = {message: "Your access level cannot act on Human Actions."};
        } catch (e) { res = {message: "Could not reach the server."}; }
        NOTE[item.id] = (res && res.message) || "";
        // A person not at the tower PC sees the carrier page in the run page's own live viewer.
        if (res && res.accepted && op === "open" && (res.live || !/^(localhost|127\.)/.test(location.hostname)) && window.HubCtx && window.HubCtx.viewer)
          window.HubCtx.viewer(item.act.action_id);
        r = await get("/api/todo"); if (r.data) t = r.data;
        draw();
      }
      draw();
    }
  };

  // Views whose records carry no business unit refuse a BU-limited account (the server hides them too).
  for (const id of ["todo"]) {
    const render = V[id].render;
    V[id].render = function (el, ctx) {
      if (ctx && ctx.data && ctx.data() && ctx.data().scope) { stop(); el.innerHTML = empty("Not available for your account", "Its records are not limited by business unit."); return; }
      return render.call(this, el, ctx);
    };
  }
  V._leave = () => { stop(); closeDrawer(); };
  V._css = css;
  V._openShipment = openShipment;
  V._hubHref = hubHref;
})();
