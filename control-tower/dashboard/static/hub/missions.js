/* ATLAS Missions — Mission Control in the hub (intelligence/missions, ATLAS_MISSIONS_PLAN.md).

   /hub#missions            the list, and a box to ask for a mission
   /hub#missions?id=<id>    one mission: the proposal (approve / review / continue normally),
                            then its task graph, evidence, hypotheses, conflicts and report

   Approving starts a read-only investigation (the plan can be edited first: steps removed,
   profile, deadline). A step that changes something becomes an action card: each is approved
   on its own, by someone holding that action's own permission, and counts as done only when
   the engine's read-back confirms it. Live missions follow /api/missions/<id>/stream (SSE),
   falling back to polling every 1.5 s, and redraw only what changed. */
(function () {
  "use strict";
  const V = window.HubViews = window.HubViews || {};
  const CSS = `
.ms{display:flex;flex-direction:column;gap:16px}
.ms-h{display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap}
.ms-h h2{margin:0;font:var(--hub-t-title);letter-spacing:-.024em}
.ms-h p{margin:4px 0 0;color:var(--hub-ink-3);font-size:13.5px;max-width:70ch}
.ms-ask{padding:16px 18px;display:grid;gap:10px}
.ms-ask textarea{width:100%;min-height:64px;resize:vertical;border:0;border-radius:12px;padding:12px 14px;font:inherit;color:var(--hub-ink);
  background:color-mix(in srgb,var(--hub-surface-2) 80%,transparent);box-shadow:inset 0 0 0 .5px var(--hub-line)}
.ms-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.ms-row .sp{flex:1}
.ms-msg{font-size:13px;color:var(--hub-ink-3)}
.ms-sec{font:600 12px/1 var(--hub-font);letter-spacing:.06em;text-transform:uppercase;color:var(--hub-ink-3);margin:6px 2px -4px}
.ms-list{list-style:none;margin:0;padding:6px}
.ms-list a{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:4px 12px;align-items:center;padding:12px;border-radius:12px;color:var(--hub-ink)}
.ms-list a:hover{background:var(--hub-surface-2)}
.ms-list b{font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ms-list small{grid-column:2;color:var(--hub-ink-3);font-size:12px}
.ms-list time{grid-row:1/3;grid-column:3;font-size:12px;color:var(--hub-ink-3);font-variant-numeric:tabular-nums}
.ms-pill{display:inline-flex;align-items:center;gap:6px;height:22px;padding:0 9px;border-radius:999px;font:600 11px/1 var(--hub-font);letter-spacing:.02em;white-space:nowrap}
.ms-pill.lv-ADVANCED{background:var(--hub-blue-wash);color:var(--hub-blue)}
.ms-pill.lv-DEEP_MISSION{background:var(--hub-accent-wash);color:var(--hub-accent-ink)}
.ms-pill.lv-CRITICAL_INCIDENT,.ms-pill.sv-CRITICAL,.ms-pill.sv-SEVERE{background:var(--hub-red-wash);color:var(--hub-red-ink)}
.ms-pill.sv-HIGH{background:var(--hub-amber-wash);color:var(--hub-amber-ink)}
.ms-pill.sv-NORMAL,.ms-pill.st{background:var(--hub-surface-2);color:var(--hub-ink-2)}
.ms-pill.st-running,.ms-pill.st-queued{background:var(--hub-accent-wash);color:var(--hub-accent-ink)}
.ms-pill.st-done{background:var(--hub-green-wash);color:var(--hub-green-ink)}
.ms-pill.st-failed,.ms-pill.st-insufficient{background:var(--hub-red-wash);color:var(--hub-red-ink)}
.ms-card{padding:18px 20px}
.ms-card h3{margin:0 0 10px;font:600 16px/1.3 var(--hub-font);letter-spacing:-.01em}
.ms-card ul{margin:0;padding-left:18px}
.ms-card li{margin:4px 0}
.ms-kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:12px}
.ms-kv div span{display:block;font-size:11.5px;color:var(--hub-ink-3)}
.ms-kv div b{font-size:15px;font-variant-numeric:tabular-nums}
.ms-plan{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:6px}
.ms-plan li{display:grid;grid-template-columns:22px minmax(0,1fr) auto;gap:10px;align-items:baseline;padding:8px 10px;border-radius:10px;background:color-mix(in srgb,var(--hub-surface-2) 60%,transparent)}
.ms-plan li i{font:600 11px var(--hub-mono);color:var(--hub-ink-3);font-style:normal}
.ms-plan li small{color:var(--hub-ink-3);font-size:12px}
.ms-cta{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
.ms-cta .hub-pill{height:38px;padding:0 18px;font-size:14px}
.ms-cta .danger{background:var(--hub-red)!important;color:#fff!important}
.ms-bar{height:6px;border-radius:6px;background:var(--hub-line);overflow:hidden}
.ms-bar b{display:block;height:100%;background:var(--hub-accent);border-radius:6px;transition:width .6s var(--hub-e)}
.ms-graph{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(150px,1fr);gap:10px;overflow-x:auto;padding:2px 2px 6px}
.ms-col{display:grid;gap:8px;align-content:start}
.ms-node{padding:10px 12px;border-radius:12px;font-size:12.5px;background:color-mix(in srgb,var(--hub-surface-2) 70%,transparent);box-shadow:inset 0 0 0 .5px var(--hub-line);transition:background-color .4s var(--hub-e),box-shadow .4s var(--hub-e)}
.ms-node b{display:block;font-size:13px;margin-bottom:2px}
.ms-node small{color:var(--hub-ink-3);display:block}
.ms-node.s-running{box-shadow:inset 0 0 0 1.5px var(--hub-accent),0 0 0 4px var(--hub-accent-wash);animation:msPulse 1.6s ease-in-out infinite}
.ms-node.s-done{box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--hub-green) 55%,transparent)}
.ms-node.s-failed{box-shadow:inset 0 0 0 1px var(--hub-red)}
.ms-node.s-cancelled{opacity:.5}
.ms-node .m{display:inline-block;margin-top:5px;font:500 10.5px var(--hub-mono);padding:2px 6px;border-radius:5px;background:var(--hub-blue-wash);color:var(--hub-blue)}
@keyframes msPulse{50%{box-shadow:inset 0 0 0 1.5px var(--hub-accent),0 0 0 8px transparent}}
.ms-grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:16px;align-items:start}
@media (max-width:1100px){.ms-grid{grid-template-columns:1fr}}
.ms-ev{list-style:none;margin:0;padding:0;display:grid;gap:6px;max-height:520px;overflow:auto}
.ms-ev li{padding:9px 11px;border-radius:10px;background:color-mix(in srgb,var(--hub-surface-2) 55%,transparent);font-size:13px}
.ms-ev li small{display:block;color:var(--hub-ink-3);font-size:11.5px;margin-top:3px}
.ms-ev li em{font-style:normal;font:600 10.5px var(--hub-font);letter-spacing:.03em;margin-right:6px;color:var(--hub-ink-3)}
.ms-ev li .ok{color:var(--hub-green-ink)} .ms-ev li .bad{color:var(--hub-red-ink)}
.ms-hyp{display:grid;gap:8px}
.ms-hyp div{padding:10px 12px;border-radius:10px;font-size:13px;background:color-mix(in srgb,var(--hub-surface-2) 55%,transparent);border-left:3px solid var(--hub-ink-4)}
.ms-hyp div.supported{border-left-color:var(--hub-green)} .ms-hyp div.rejected{opacity:.6;border-left-color:var(--hub-red)} .ms-hyp div.open{border-left-color:var(--hub-blue)}
.ms-hyp b{font-weight:600} .ms-hyp small{display:block;color:var(--hub-ink-3);margin-top:3px}
.ms-acts{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.ms-acts a{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:10px;align-items:center;padding:10px 12px;border-radius:10px;color:var(--hub-ink);background:color-mix(in srgb,var(--hub-surface-2) 55%,transparent)}
.ms-acts a:hover{background:var(--hub-surface-2)}
.ms-acts small{color:var(--hub-ink-3)}
.ms-cls{font:600 10.5px var(--hub-font);letter-spacing:.04em;padding:3px 7px;border-radius:6px;background:var(--hub-surface-2);color:var(--hub-ink-2)}
.ms-cls.APPROVAL_REQUIRED{background:var(--hub-amber-wash);color:var(--hub-amber-ink)}
.ms-grade{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.ms-grade b{font:700 22px var(--hub-font)}
.ms-grade .Strong{color:var(--hub-green-ink)} .ms-grade .Moderate{color:var(--hub-accent-ink)} .ms-grade .Weak,.ms-grade .Insufficient{color:var(--hub-red-ink)}
.ms-note{font-size:12.5px;color:var(--hub-ink-3)}
.ms-banner{padding:14px 18px;border-radius:14px;background:var(--hub-red-wash);color:var(--hub-red-ink);font-weight:600;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.ms-tl{list-style:none;margin:0;padding:0;display:grid;gap:4px;font-size:12.5px;max-height:260px;overflow:auto}
.ms-tl time{color:var(--hub-ink-3);font-variant-numeric:tabular-nums;margin-right:8px}
.ms-tl li[data-k]::before{content:attr(data-k);font:600 10px var(--hub-font);letter-spacing:.04em;text-transform:uppercase;margin-right:6px;padding:2px 6px;border-radius:5px;background:var(--hub-surface-2);color:var(--hub-ink-3)}
.ms-tl li[data-k="replan"]::before,.ms-tl li[data-k="plan"]::before{background:var(--hub-blue-wash);color:var(--hub-blue)}
.ms-tl li[data-k^="action"]::before{background:var(--hub-amber-wash);color:var(--hub-amber-ink)}
.ms-tl li[data-k="challenged"]::before,.ms-tl li[data-k="deadline"]::before,.ms-tl li[data-k="guard"]::before{background:var(--hub-red-wash);color:var(--hub-red-ink)}
.ms-guard{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.ms-guard li{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:10px;align-items:baseline;padding:10px 12px;border-radius:10px;font-size:13px;background:color-mix(in srgb,var(--hub-surface-2) 55%,transparent)}
.ms-guard li b{font:700 10.5px var(--hub-font);letter-spacing:.05em;text-transform:uppercase;padding:3px 7px;border-radius:6px}
.ms-guard li b.blocked{background:var(--hub-red-wash);color:var(--hub-red-ink)} .ms-guard li b.held{background:var(--hub-amber-wash);color:var(--hub-amber-ink)} .ms-guard li b.ran{background:var(--hub-green-wash);color:var(--hub-green-ink)}
.ms-guard time{font-size:11.5px;color:var(--hub-ink-3);font-variant-numeric:tabular-nums}
.ms-plan li label{display:contents;cursor:pointer}
.ms-plan li input{margin:0;accent-color:var(--hub-accent)}
.ms-plan li.off{opacity:.45}
.ms-plan li.off b{text-decoration:line-through}
.ms-edit{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-top:12px;font-size:13px}
.ms-edit select,.ms-edit input{font:inherit;border:0;border-radius:9px;padding:6px 10px;background:var(--hub-surface-2);color:var(--hub-ink);box-shadow:inset 0 0 0 .5px var(--hub-line)}
.ms-edit input{width:72px}
.ms-ac{display:grid;gap:10px}
.ms-ac article{padding:12px 14px;border-radius:12px;background:color-mix(in srgb,var(--hub-surface-2) 55%,transparent);box-shadow:inset 0 0 0 .5px var(--hub-line);display:grid;gap:6px;transition:box-shadow .4s var(--hub-e)}
.ms-ac article.st-proposed{box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--hub-amber) 60%,transparent)}
.ms-ac article.st-verified,.ms-ac article.st-completed{box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--hub-green) 55%,transparent)}
.ms-ac article.st-not_verified,.ms-ac article.st-failed{box-shadow:inset 0 0 0 1px var(--hub-red)}
.ms-ac h4{margin:0;font:600 14px/1.35 var(--hub-font)}
.ms-ac dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:3px 10px;margin:0;font-size:12.5px}
.ms-ac dt{color:var(--hub-ink-3)} .ms-ac dd{margin:0}
.ms-out{font-weight:600}
.ms-out.verified,.ms-out.completed{color:var(--hub-green-ink)} .ms-out.not_verified,.ms-out.failed{color:var(--hub-red-ink)} .ms-out.awaiting_outcome{color:var(--hub-accent-ink)}
.ms-tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.ms-tbl th{text-align:left;font-weight:600;color:var(--hub-ink-3);padding:4px 6px;border-bottom:.5px solid var(--hub-line)}
.ms-tbl td{padding:5px 6px;border-bottom:.5px solid var(--hub-line);font-variant-numeric:tabular-nums}
.ms-notes textarea{width:100%;min-height:44px;resize:vertical;border:0;border-radius:10px;padding:8px 10px;font:inherit;color:var(--hub-ink);background:var(--hub-surface-2);box-shadow:inset 0 0 0 .5px var(--hub-line)}
.ms-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;padding:14px 16px}
.ms-stats div span{display:block;font-size:11.5px;color:var(--hub-ink-3)} .ms-stats div b{font-size:18px;font-variant-numeric:tabular-nums}
.ms-live{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--hub-ink-3)}
.ms-models{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.ms-models article{padding:10px 12px;border-radius:12px;background:color-mix(in srgb,var(--hub-surface-2) 55%,transparent);box-shadow:inset 0 0 0 .5px var(--hub-line);font-size:12.5px;display:grid;gap:3px}
.ms-models b{font:600 13px var(--hub-mono)} .ms-models small{color:var(--hub-ink-3)}
.ms-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--hub-ink-4)}
.ms-dot.ok{background:var(--hub-green);box-shadow:0 0 0 3px var(--hub-green-wash)}
.ms-agree{display:inline-flex;align-items:center;gap:4px;margin-left:6px;font:600 10.5px var(--hub-font);padding:2px 7px;border-radius:999px;background:var(--hub-blue-wash);color:var(--hub-blue)}
.ms-live i{width:7px;height:7px;border-radius:50%;background:var(--hub-green);box-shadow:0 0 0 3px var(--hub-green-wash)}
`;
  let timer = null, current = null, source = null;
  const LEVEL = {ADVANCED: "Advanced", DEEP_MISSION: "Deep Mission", CRITICAL_INCIDENT: "Critical incident"};
  const STATUS = {proposed: "Waiting for approval", queued: "Queued", running: "Running", paused: "Paused", done: "Done",
    insufficient: "Insufficient evidence", failed: "Failed", cancelled: "Cancelled", declined: "Continued normally"};
  const VSTATUS = {awaiting_approval: "Actions waiting for approval", awaiting_outcome: "Waiting for the read-back",
    done_verified: "Actions confirmed by read-back", done_unverified: "An action was NOT confirmed", no_action_taken: "No action taken"};
  const ASTATUS = {proposed: "Waiting for approval", executing: "Executing", awaiting_outcome: "Waiting for the read-back", verified: "Confirmed by read-back",
    not_verified: "Not confirmed", failed: "Refused by the engine", rejected: "Rejected", completed: "Done"};
  const CLASS = {VERIFIED_LIVE: "Verified live", HISTORICAL: "From the records", CALCULATED: "Calculated", PLATFORM_RULE: "Platform rule",
    GENERAL_KNOWLEDGE: "Logistics knowledge", HYPOTHESIS: "Hypothesis", EXTERNAL_UNVERIFIED: "External, unverified"};

  function css() {
    if (document.getElementById("ms-css")) return;
    const s = document.createElement("style"); s.id = "ms-css"; s.textContent = CSS; document.head.appendChild(s);
  }
  const H = window.HubCtx || {};
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const when = (t) => { if (!t) return ""; const d = new Date(t * 1000); return d.toLocaleString("en-GB", {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}); };
  async function post(url, body) {
    const r = await fetch(url, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRF-Token": window.__csrf || ""},
      body: JSON.stringify(body || {})});
    let d = {}; try { d = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok && !d.message) d.message = "The server answered " + r.status + ".";
    return d;
  }
  // the session's CSRF token (accounts on): /api/auth/me carries it
  fetch("/api/auth/me", {credentials: "same-origin"}).then((r) => r.ok ? r.json() : null).then((d) => { if (d && d.csrf) window.__csrf = d.csrf; }).catch(() => {});

  const put = (el, html) => { if (el && el.__h !== html) { el.innerHTML = html; el.__h = html; } };
  const pill = (cls, text) => `<span class="ms-pill ${cls}">${esc(text)}</span>`;
  const param = (k) => new URLSearchParams((location.hash.split("?")[1] || "")).get(k);

  function stop() { if (timer) { clearTimeout(timer); timer = null; } if (source) { source.close(); source = null; } current = null; }
  const mins = (s) => s == null ? "" : s <= 0 ? "deadline reached" : s < 60 ? Math.round(s) + " s left" : Math.round(s / 60) + " min left";

  /* ── the list ── */
  async function list(el) {
    put(el, `<div class="ms"><div class="ms-h"><div><h2>Missions</h2>
      <p>Investigations ATLAS proposes when a question needs more than one look: specialists, records checked against each other, every number recomputed. Nothing starts until you approve it, and nothing is changed.</p></div></div>
      <section class="hv-card ms-ask" aria-labelledby="msAskT"><h3 id="msAskT" class="sr">Ask for a mission</h3>
        <textarea id="msQ" placeholder="What should ATLAS investigate? e.g. Why is 157-12345675 stuck at customs?" aria-label="What should ATLAS investigate?"></textarea>
        <div class="ms-row"><button class="hub-pill primary" id="msPropose" type="button">Propose a plan</button>
          <button class="hub-pill" id="msDrill" type="button" title="Same plan, no billed model, nothing changed">Drill</button>
          <span class="sp"></span><button class="hub-pill" id="msEmer" type="button" hidden>Emergency Mode…</button></div>
        <div class="ms-msg" id="msMsg" role="status"></div></section>
      <div id="msStats"></div><div id="msLists"></div><div id="msModels"></div></div>`);
    fetch("/api/missions/registry", {credentials: "same-origin"}).then((r) => r.ok ? r.json() : null).then((reg) => {
      if (!reg || !reg.models) return;
      const rows = reg.models.filter((m) => m.provider !== "code");
      const where = (m) => m.provider === "ollama" ? "On the tower (Ollama)" : "Snowflake Cortex";
      const best = (m) => Object.entries(m.caps || {}).filter(([k, v]) => v >= 2).sort((a, b) => b[1] - a[1]).map(([k]) => k).slice(0, 4).join(", ");
      const up = rows.filter((m) => m.health && m.health.ok).length;
      put(el.querySelector("#msModels"), `<div class="ms-sec">Models working together · ${fmt(up)} of ${fmt(rows.length)} answering now</div>
        <section class="hv-card ms-card"><p class="ms-note" style="margin:0 0 10px">Several models work on the same step: different families suggest causes and review each other, drafts are fact-checked against the records, and numbers always come from ATLAS's own code. The tower runs one local model at a time.</p>
        <div class="ms-models">${rows.map((m) => `<article><b><span class="ms-dot ${m.health && m.health.ok ? "ok" : ""}"></span>${esc(m.id)}</b>
          <small>${esc(where(m))} · family ${esc(m.family || (m.id.startsWith("claude") ? "claude" : m.id.split(/[-:]/)[0]))}${m.size_gb ? " · " + esc(m.size_gb) + " GB" : ""}</small>
          <span>Good for: ${esc(best(m) || "light tasks")}</span><small>${esc(m.health ? m.health.detail : "")}</small></article>`).join("")}</div></section>`);
    }).catch(() => {});
    fetch("/api/missions/analytics", {credentials: "same-origin"}).then((r) => r.ok ? r.json() : null).then((a) => {
      if (!a || !a.missions) return;
      const pct = (x) => x == null ? "—" : Math.round(x * 100) + "%";
      put(el.querySelector("#msStats"), `<section class="hv-card ms-stats" aria-label="Mission analytics">
        <div><span>Missions</span><b>${fmt(a.missions)}</b></div><div><span>Median time to report</span><b>${a.median_minutes_to_report == null ? "—" : esc(a.median_minutes_to_report) + " min"}</b></div>
        <div><span>Actions executed</span><b>${fmt(a.actions.executed)}</b></div><div><span>Approved by a person or policy</span><b>${pct(a.actions.approval_compliance)}</b></div>
        <div><span>Confirmed by read-back</span><b>${pct(a.actions.verified_rate)}</b></div><div><span>Model calls</span><b>${fmt(a.model_calls)}</b></div></section>`);
    }).catch(() => {});
    const ask = async (extra) => {
      const q = el.querySelector("#msQ").value.trim(); const msg = el.querySelector("#msMsg");
      if (!q) { msg.textContent = "Say what to investigate."; return; }
      msg.textContent = "Reading the records…";
      const d = await post(extra && extra.emergency ? "/api/missions/emergency" : "/api/missions/propose", {question: q, profile: extra && extra.profile, level: extra && extra.level});
      if (d.mission) { location.hash = "#missions?id=" + d.mission.id; return; }
      msg.textContent = d.message || "Nothing to start.";
    };
    el.querySelector("#msPropose").onclick = () => ask();
    el.querySelector("#msDrill").onclick = () => ask({profile: "drill", level: "DEEP_MISSION"});
    const emer = el.querySelector("#msEmer");
    emer.onclick = () => {
      if (emer.dataset.armed) { ask({emergency: true}); return; }
      emer.dataset.armed = "1"; emer.textContent = "Activate Emergency Mode now"; emer.classList.add("danger");
      el.querySelector("#msMsg").textContent = "Emergency Mode starts a critical-incident mission at once, with the emergency resource profile. Press again to confirm.";
    };
    const draw = async () => {
      let d;
      try { const r = await fetch("/api/missions", {credentials: "same-origin"}); d = await r.json(); if (!r.ok) throw new Error(d.error || r.status); }
      catch (e) { put(el.querySelector("#msLists"), (H.errorBox || ((a, b) => `<p>${esc(a)} ${esc(b)}</p>`))("Missions could not be loaded", String(e.message || e), false)); return; }
      emer.hidden = !(d.can && d.can.emergency);
      const ms = d.missions || [];
      const group = (title, rows) => rows.length ? `<div class="ms-sec">${esc(title)}</div><section class="hv-card"><ul class="ms-list">${rows.map((m) =>
        `<li><a href="/hub#missions?id=${esc(m.id)}">${pill("lv-" + m.level, LEVEL[m.level] || m.level)}<b>${esc(m.title)}</b>
          <small>${esc(STATUS[m.status] || m.status)}${m.phase && m.status === "running" ? " · " + esc(m.phase) : ""}${m.grade ? " · confidence " + esc(m.grade) : ""}${m.verification_status ? " · " + esc(VSTATUS[m.verification_status] || m.verification_status) : ""}${m.assigned_to ? " · with " + esc(m.assigned_to) : ""}${m.origin === "auto" ? " · suggested by ATLAS" : ""}${m.origin === "replay" ? " · replay" : ""}${m.drill ? " · drill" : ""}</small>
          <time>${esc(when(m.created_at))}</time></a></li>`).join("")}</ul></section>` : "";
      put(el.querySelector("#msLists"),
        group("Waiting for approval", ms.filter((m) => m.status === "proposed" && m.origin !== "auto")) +
        group("Suggested by ATLAS (Emergency Mode, not started)", ms.filter((m) => m.status === "proposed" && m.origin === "auto")) +
        group("Running", ms.filter((m) => ["queued", "running", "paused"].includes(m.status))) +
        group("Finished", ms.filter((m) => ["done", "insufficient", "failed", "cancelled", "declined"].includes(m.status))) ||
        `<section class="hv-card hub-empty"><b>No missions yet</b>Ask for one above, or ask ATLAS a "why" question about a shipment: it offers a plan when one is worth it.</section>`);
      if (ms.some((m) => ["queued", "running"].includes(m.status)) && current === "list") timer = setTimeout(draw, 2500);
    };
    current = "list";
    draw();
  }

  /* ── one mission ── */
  function depthOf(tasks) {
    const by = Object.fromEntries(tasks.map((t) => [t.id, t])), memo = {};
    const d = (id) => memo[id] ?? (memo[id] = Math.max(0, ...((by[id] && by[id].depends_on) || []).map((x) => d(x) + 1)));
    const cols = [];
    tasks.forEach((t) => { const k = d(t.id); (cols[k] = cols[k] || []).push(t); });
    return cols;
  }
  function proposalCard(m, can) {
    const p = m.plan || {};
    const critical = m.level === "CRITICAL_INCIDENT";
    const allowed = critical ? can.emergency : can.run;
    const est = p.estimate || {};
    return `<section class="hv-card ms-card" aria-labelledby="msPT"><h3 id="msPT">ATLAS suggests ${critical ? "Emergency Mode" : "a " + esc((LEVEL[m.level] || "").toLowerCase())}</h3>
      <ul>${(m.reasons || []).map((r) => `<li>${esc(r[0].toUpperCase() + r.slice(1))}.</li>`).join("")}</ul>
      <div class="ms-kv"><div><span>Specialists</span><b>${fmt((p.specialists || []).length)}</b></div>
        <div><span>Models</span><b>${esc(Object.keys(p.models || {}).join(", ") || "ATLAS's own code")}</b></div>
        <div><span>Sources</span><b>${esc((p.websites || []).join(" · "))}</b></div>
        <div><span>Estimated use</span><b>${est.usage && est.usage[1] ? fmt(est.usage[0]) + "–" + fmt(est.usage[1]) + " tokens" : "no billed model"}</b></div>
        <div><span>Profile</span><b>${esc(p.profile_label || m.profile)}</b></div></div>
      <details id="msPlan" ${param("review") ? "open" : ""}><summary class="ms-note" style="cursor:pointer;margin-top:12px">The plan, step by step</summary>
        <ol class="ms-plan">${(p.plan || []).map((t, i) => `<li data-step="${esc(t.id)}"><label>${t.required === false && allowed ? `<input type="checkbox" checked data-step="${esc(t.id)}" aria-label="Include ${esc(t.specialist)}">` : `<i>${i + 1}</i>`}<span><b>${esc(t.specialist)}</b> — ${esc(t.objective)}${t.depends_on && t.depends_on.length ? `<small> · after ${esc(t.depends_on.join(", "))}</small>` : ""}${t.required ? `<small> · required</small>` : ""}</span><small>${esc(t.by)}</small></label></li>`).join("")}</ol>
        ${allowed ? `<div class="ms-edit"><label>Profile <select id="msProf" aria-label="Resource profile">${["normal", "deep", "emergency"].filter((k) => k !== "emergency" || critical).map((k) => `<option value="${k}" ${k === m.profile ? "selected" : ""}>${esc({normal: "Normal", deep: "Deep", emergency: "Emergency"}[k])}</option>`).join("")}</select></label>
          <label>Deadline <input id="msDl" type="number" min="2" max="240" value="${esc(p.deadline_min || 15)}" aria-label="Deadline in minutes"> min</label>
          <span class="ms-note">Untick a step to leave it out; required steps stay.</span></div>` : ""}
        ${p.items && p.items.length ? `<p class="ms-note" style="margin-top:10px">One task per shipment for ${fmt(p.items.length)} shipment(s), longest past due first (at most ${fmt((p.fanout || {}).max || 25)}); the rest are counted by the Data Analyst.</p>` : ""}
        ${p.planner ? `<p class="ms-note">Plan: ${esc(p.planner.note)}</p>` : ""}
        <ul class="ms-note" style="margin-top:10px">${(p.limits || []).map((l) => `<li>${esc(l)}</li>`).join("")}</ul></details>
      ${(m.similar || p.similar || []).length ? `<p class="ms-note" style="margin-top:10px"><b>Seen before:</b> ${(m.similar || p.similar).map((x) => `<a href="/hub#missions?id=${esc(x.mission_id)}">${esc(x.text.slice(0, 120))}</a>`).join(" · ")}</p>` : ""}
      <div class="ms-cta">${allowed ? `<button class="hub-pill primary${critical ? " danger" : ""}" type="button" data-act="approve">${critical ? "Activate Emergency Mode" : "Enter Deep Mission"}</button>
        <button class="hub-pill" type="button" data-act="drill">Run as a drill</button>` : `<span class="ms-note">${critical ? "An Admin" : "An Operator or Admin"} can start it.</span>`}
        <button class="hub-pill" type="button" data-act="review">Review the plan</button>
        <button class="hub-pill" type="button" data-act="decline">Continue normally</button></div>
      <div class="ms-msg" id="msAct" role="status"></div></section>`;
  }
  function liveCards(m, can) {
    const live = ["queued", "running", "paused"].includes(m.status);
    const pr = m.progress || {done: 0, total: 0};
    const cols = depthOf(m.tasks || []);
    const graph = `<div class="ms-graph" aria-label="Task graph">${cols.map((col) => `<div class="ms-col">${col.map((t) =>
      `<div class="ms-node s-${esc(t.status)}" title="${esc(t.objective)}"><b>${esc(t.title)}</b><small>${esc(t.status === "done" ? (t.summary || "done") : t.status === "failed" ? (t.error || "failed") : t.status)}</small>${t.model ? `<span class="m">${esc(t.model)}</span>` : ""}</div>`).join("")}</div>`).join("")}</div>`;
    const u = m.usage || {};
    const ctl = live && can.run ? `<div class="ms-row">${m.status === "paused" ? `<button class="hub-pill" data-act="resume" type="button">Resume</button>` : `<button class="hub-pill" data-act="pause" type="button">Pause</button>`}
      <button class="hub-pill" data-act="cancel" type="button">Cancel</button></div>` : "";
    return `<section class="hv-card ms-card"><div class="ms-row"><h3 style="margin:0">Progress</h3><span class="sp"></span>${ctl}</div>
      <div class="ms-bar" style="margin:12px 0" role="progressbar" aria-valuenow="${pr.done}" aria-valuemax="${pr.total}"><b style="width:${pr.total ? Math.round(100 * pr.done / pr.total) : 0}%"></b></div>
      ${graph}
      <div class="ms-kv"><div><span>Steps done</span><b>${fmt(pr.done)} / ${fmt(pr.total)}</b></div><div><span>Model calls</span><b>${fmt(u.model_calls)}${u.failed_calls ? ` <small class="ms-note">(${fmt(u.failed_calls)} failed)</small>` : ""}</b></div>
        <div><span>Models used</span><b>${esc((u.models || []).join(", ") || "none")}</b></div><div><span>Tokens (estimated)</span><b>${fmt(u.tokens)} of ${fmt(m.budget_tokens)}</b></div></div>
      <div class="ms-msg" id="msAct" role="status"></div></section>`;
  }
  function evidenceCard(m) {
    const ev = m.evidence || [];
    return `<section class="hv-card ms-card"><h3>Evidence <span class="ms-note">${fmt(ev.length)} findings, each with its source</span></h3>
      <ul class="ms-ev">${ev.map((e) => `<li><em>${esc(CLASS[e.class] || e.class)}</em>${esc(e.claim)}
        <small>${esc(e.source)} · ${esc(e.agent)}${e.verification === "recomputed" ? ' · <span class="ok">recomputed ✓</span>' : e.verification === "mismatch" ? ' · <span class="bad">no longer matches the source</span>' : ""}</small></li>`).join("") || `<li>No findings yet.</li>`}</ul></section>`;
  }
  function hypCard(m) {
    const order = {supported: 0, open: 1, inconclusive: 2, rejected: 3};
    const hs = (m.hypotheses || []).slice().sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));
    const LBL = {supported: "Supported", open: "Open, not tested", inconclusive: "Not enough evidence", rejected: "Rejected"};
    return `<section class="hv-card ms-card"><h3>Hypotheses</h3><div class="ms-hyp">${hs.map((h) =>
      `<div class="${esc(h.status)}"><b>${esc(h.label)}</b> · ${esc(LBL[h.status] || h.status)}${h.proposed_by && h.proposed_by.length > 1 ? `<span class="ms-agree">${fmt(h.proposed_by.length)} models agree</span>` : ""}<small>${esc((h.why || []).slice(0, 2).join("; ") || (h.needed || []).slice(0, 1).join("") || "")}</small></div>`).join("") || `<div>Built once the facts are in.</div>`}</div></section>`;
  }
  function reportCard(m) {
    const r = m.report;
    if (!r) return "";
    const acts = r.actions || [];
    const inc = r.incident;
    const byTitle = Object.fromEntries((m.actions || []).map((x) => [x.title, x]));
    const ran = (m.actions || []).some((x) => x.executed_at);
    return `<section class="hv-card ms-card" aria-labelledby="msRT"><h3 id="msRT">Report</h3>
      <div class="ms-grade"><span class="ms-note">Confidence</span><b class="${esc(r.grade)}">${esc(r.grade)}</b><span class="ms-note">${esc((r.grade_reasons || []).join("; "))}</span></div>
      <p style="font-size:15px;line-height:1.55;margin:12px 0">${esc(r.narrative)}</p>
      <p class="ms-note">Worded by ${esc(r.narrative_by)}. ${esc(r.verification)}${r.narrative_rejected ? " " + esc(r.narrative_rejected) : ""}</p>
      ${r.review && (r.review.reviewers || []).length ? `<p class="ms-note">Independent review by ${fmt(r.review.reviewers.length)} model${r.review.reviewers.length > 1 ? "s" : ""}${r.review.split ? " (split)" : ""}: ${r.review.reviewers.map((x) => esc(x.model) + (x.agree ? " agrees" : " disagrees")).join(" · ")}${(r.review.concerns || []).length ? ". Concerns: " + esc(r.review.concerns.join("; ")) : "."}</p>` :
        r.review && (r.review.reviewed_by || r.review.note) ? `<p class="ms-note">Independent review: ${esc(r.review.reviewed_by ? r.review.reviewed_by + (r.review.agree ? " agrees." : " raised concerns: " + (r.review.concerns || []).join("; ")) : r.review.note)}</p>` : ""}
      ${r.wording && (r.wording.members || []).length > 1 ? `<p class="ms-note">Drafted by ${fmt(r.wording.members.length)} models in parallel (${esc(r.wording.members.join(", "))}); ${r.wording.chosen ? esc(r.wording.chosen) + "'s draft passed the fact check" : "none passed the fact check"}${(r.wording.rejected || []).length ? "; rejected: " + esc(r.wording.rejected.map((x) => x.model + " (" + x.why + ")").join("; ")) : ""}.</p>` : ""}
      ${(r.contradictions || []).length || (r.mismatches || []).length ? `<h3 style="margin-top:16px">Conflicts</h3><ul>${(r.contradictions || []).concat(r.mismatches || []).map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
      ${inc ? `<h3 style="margin-top:16px">Incident plan</h3><ul><li><b>Recommended:</b> ${esc(inc.recommended)}</li>${(inc.verification_criteria || []).map((c) => `<li>Verify: ${esc(c)}</li>`).join("")}<li>Rollback: ${esc(inc.rollback)}</li></ul>` : ""}
      <h3 style="margin-top:16px">Next steps <span class="ms-note">${ran ? "approved steps ran through their own engines; see Approvals and outcomes" : "none executed"}</span></h3>
      <ul class="ms-acts">${acts.map((a) => { const card = byTitle[a.title]; const st = card ? (ASTATUS[card.status] || card.status) : a.status;
        return `<li><a href="${esc(a.href)}"><span class="ms-cls ${esc(a.class)}">${esc(a.class.replace("_", " "))}</span><span>${esc(a.title)}<br><small>${esc(a.why)}</small></span><small class="ms-out ${esc(card ? card.status : "")}">${esc(st)}</small></a></li>`; }).join("") || "<li class='ms-note'>None.</li>"}</ul>
      <ul class="ms-note" style="margin-top:12px">${(r.limits || []).map((l) => `<li>${esc(l)}</li>`).join("")}</ul></section>`;
  }
  function actionsCard(m) {
    const acts = m.actions || [];
    if (!acts.length) return "";
    const off = !m.actions_enabled;
    return `<section class="hv-card ms-card" aria-labelledby="msAT"><h3 id="msAT">Approvals and outcomes</h3>
      ${off ? `<p class="ms-note">Actions are proposals only on this server (ATLAS_MISSION_ACTIONS is off): approve is refused and nothing is executed. You can do a step yourself from its page.</p>` : `<p class="ms-note">Each action is approved on its own, by someone with its permission. It counts as done only when the engine's own record confirms it.</p>`}
      <div class="ms-ac">${acts.map((a) => `<article class="st-${esc(a.status)}" data-aid="${esc(a.id)}">
        <div class="ms-row"><span class="ms-cls ${esc(a.class)}">${esc(String(a.class).replace("_", " "))}</span><span class="sp"></span><span class="ms-out ${esc(a.status)}">${esc(ASTATUS[a.status] || a.status)}</span></div>
        <h4>${a.href ? `<a href="${esc(a.href)}">${esc(a.title)}</a>` : esc(a.title)}</h4>
        <dl><dt>Why</dt><dd>${esc(a.why)}</dd><dt>Permission</dt><dd>${esc(a.permission)}</dd><dt>Done when</dt><dd>${esc(a.criterion)}</dd>
          ${a.decided_by ? `<dt>Decided</dt><dd>${esc(a.decided_by)} · ${esc(when(a.decided_at))}${a.policy ? " · policy " + esc(a.policy) : ""}</dd>` : ""}
          ${a.engine_ref ? `<dt>Engine</dt><dd>${esc(a.engine_ref)}</dd>` : ""}
          ${a.outcome_detail ? `<dt>Outcome</dt><dd>${esc(Object.entries(a.outcome_detail).filter(([k, v]) => v != null && typeof v !== "object").map(([k, v]) => k.replace(/_/g, " ") + ": " + v).join(" · "))}</dd>` : ""}</dl>
        ${a.status === "proposed" && a.can_decide ? `<div class="ms-row"><button class="hub-pill primary" type="button" data-aact="approve" data-aid="${esc(a.id)}"${off ? " disabled" : ""}>Approve</button><button class="hub-pill" type="button" data-aact="reject" data-aid="${esc(a.id)}">Reject</button></div>` :
          a.status === "proposed" ? `<p class="ms-note">Someone with ${esc(a.permission)} can approve this.</p>` : ""}
        ${a.kind === "queue_human" && a.status === "verified" && m.can && m.can.run ? `<div class="ms-row"><button class="hub-pill" type="button" data-aact="done" data-aid="${esc(a.id)}">Mark done</button></div>` : ""}
      </article>`).join("")}</div><div class="ms-msg" id="msAAct" role="status"></div></section>`;
  }
  function conflictsCard(m) {
    const c = m.conflicts || [];
    if (!c.length) return "";
    return `<section class="hv-card ms-card"><h3>Findings in conflict</h3><p class="ms-note">Kept side by side; ATLAS does not pick one silently.</p>
      <ul>${c.map((x) => `<li><b>${esc(x.kind === "mismatch" ? "No longer matches the source" : "Sources disagree")}:</b> ${esc(x.text)}</li>`).join("")}</ul></section>`;
  }
  function usageCard(m) {
    const rows = Object.entries(m.usage_by_agent || {});
    if (!rows.length) return "";
    return `<section class="hv-card ms-card"><h3>Model use by agent <span class="ms-note">from the ledger; usage estimated</span></h3>
      <table class="ms-tbl"><thead><tr><th>Agent</th><th>Calls</th><th>Failed</th><th>Usage</th><th>Models</th></tr></thead><tbody>${rows.map(([k, v]) =>
        `<tr><td>${esc(k)}</td><td>${fmt(v.calls)}</td><td>${fmt(v.failed)}</td><td>${fmt(v.usage)}</td><td>${esc((v.models || []).join(", "))}</td></tr>`).join("")}</tbody></table></section>`;
  }
  function memoryCard(m) {
    const mem = (m.memory || []).filter((x) => x.kind === "lesson");
    if (!mem.length) return "";
    const can = m.can && m.can.propose_knowledge;
    const ST = {draft: "Draft", proposed_to_kb: "Sent to knowledge review", rejected: "Dropped"};
    return `<section class="hv-card ms-card"><h3>Lessons <span class="ms-note">drafts: knowledge only after a reviewer approves</span></h3>
      <div class="ms-ac">${mem.map((x) => `<article><p style="margin:0;font-size:13px">${esc(x.text)}</p><div class="ms-row"><span class="ms-note">${esc(ST[x.status] || x.status)}${x.proposal_id ? " · " + esc(x.proposal_id) : ""}</span><span class="sp"></span>
        ${x.status === "draft" && can ? `<button class="hub-pill" type="button" data-mact="promote" data-mid="${esc(x.id)}">Propose as knowledge</button><button class="hub-pill" type="button" data-mact="reject" data-mid="${esc(x.id)}">Drop</button>` : ""}</div></article>`).join("")}</div></section>`;
  }
  function guardCard(m) {
    // What the agents tried, and what ATLAS did: blocked attempts, held actions, executed ones.
    const ev = (m.events || []).filter((e) => ["guard", "challenged", "policy", "action_executed", "action_verified", "action_not_verified", "action_failed"].includes(e.kind));
    if (!ev.length) return "";
    const tag = (k) => k === "guard" || k === "challenged" || k === "action_failed" || k === "action_not_verified" ? ["blocked", k === "guard" ? "Blocked" : k === "challenged" ? "Challenged" : "Not confirmed"]
      : k === "policy" ? ["held", "Policy"] : ["ran", k === "action_verified" ? "Confirmed" : "Executed"];
    const blocked = ev.filter((e) => e.kind === "guard").length;
    return `<section class="hv-card ms-card" aria-labelledby="msGT"><h3 id="msGT">Guardrails <span class="ms-note">what the agents tried, and what ATLAS did · ${fmt(blocked)} blocked</span></h3>
      <ul class="ms-guard">${ev.slice().reverse().map((e) => { const [c, l] = tag(e.kind); return `<li><b class="${c}">${esc(l)}</b><span>${esc(e.text)}</span><time>${esc(new Date(e.at * 1000).toLocaleTimeString("en-GB"))}</time></li>`; }).join("")}</ul></section>`;
  }
  function notesList(m) {
    return (m.notes || []).map((n) => `<li><time>${esc(when(n.at))}</time><b>${esc(n.by)}</b>: ${esc(n.text)}</li>`).join("") || `<li class="ms-note">No notes yet.</li>`;
  }

  async function detail(el, id) {
    let first = true;
    const draw = async () => {
      let m, can = {};
      try {
        const [r, l] = await Promise.all([fetch("/api/missions/" + encodeURIComponent(id), {credentials: "same-origin"}), fetch("/api/missions", {credentials: "same-origin"})]);
        m = await r.json(); if (!r.ok) throw new Error(m.error || r.status);
        can = ((await l.json()) || {}).can || {};
      } catch (e) {
        put(el, (H.errorBox || ((a) => `<p>${esc(a)}</p>`))("This mission could not be loaded", String(e.message || e), false)); return;
      }
      if (first) {
        put(el, `<div class="ms"><div class="ms-h"><div><a href="/hub#missions" class="ms-note">‹ Missions</a><h2 id="msTitle"></h2><p id="msSub"></p></div></div>
          <div id="msBanner"></div><div id="msTop"></div><div id="msActs"></div><div class="ms-grid"><div id="msLeft" class="ms"></div><div id="msRight" class="ms"></div></div>
          <div class="ms-grid" id="msTeam" hidden><section class="hv-card ms-card ms-notes"><h3>Notes</h3><ol class="ms-tl" id="msNotes"></ol>
            <textarea id="msNote" placeholder="Add a note for the team" aria-label="Add a note"></textarea><div class="ms-row" style="margin-top:8px"><button class="hub-pill" type="button" id="msNoteGo">Add note</button></div></section>
            <section class="hv-card ms-card"><h3>Hand over or replay</h3><div class="ms-row"><input id="msTo" placeholder="Work email" aria-label="Hand over to" style="flex:1;min-width:160px;border:0;border-radius:9px;padding:8px 10px;background:var(--hub-surface-2);color:var(--hub-ink);font:inherit">
              <button class="hub-pill" type="button" id="msAssign">Hand over</button></div>
              <div class="ms-row" style="margin-top:10px"><button class="hub-pill" type="button" id="msReplay">Replay against today's records</button></div><div class="ms-msg" id="msTeamMsg" role="status"></div></section></div></div>`);
        first = false;
        const team = el.querySelector("#msTeam"), tmsg = () => el.querySelector("#msTeamMsg");
        el.querySelector("#msNoteGo").onclick = async () => {
          const t = el.querySelector("#msNote"); if (!t.value.trim()) return;
          const d = await post(`/api/missions/${encodeURIComponent(id)}/note`, {text: t.value}); if (d.ok) t.value = ""; tmsg().textContent = d.message || ""; refresh();
        };
        el.querySelector("#msAssign").onclick = async () => {
          const d = await post(`/api/missions/${encodeURIComponent(id)}/assign`, {to: el.querySelector("#msTo").value}); tmsg().textContent = d.message || ""; refresh();
        };
        el.querySelector("#msReplay").onclick = async () => {
          const d = await post(`/api/missions/${encodeURIComponent(id)}/replay`, {}); if (d.mission) { location.hash = "#missions?id=" + d.mission.id; return; } tmsg().textContent = d.message || "";
        };
        team.hidden = false;
      }
      el.querySelector("#msTitle").textContent = m.title;
      put(el.querySelector("#msSub"), `${pill("lv-" + m.level, LEVEL[m.level] || m.level)} ${pill("sv-" + m.severity, "Severity " + (m.severity || "").toLowerCase())} ${pill("st st-" + m.status, STATUS[m.status] || m.status)}
        ${m.verification_status ? pill("st", VSTATUS[m.verification_status] || m.verification_status) : ""}
        <span class="ms-note"> ${esc(m.phase || "")}${m.approved_by ? " · approved by " + esc(m.approved_by) + " " + esc(when(m.approved_at)) : ""}${m.deadline_left != null ? " · " + esc(mins(m.deadline_left)) : ""}${m.assigned_to ? " · with " + esc(m.assigned_to) : ""}${m.parent_id ? ` · replay of <a href="/hub#missions?id=${esc(m.parent_id)}">${esc(m.parent_id)}</a>` : ""}${m.drill ? " · drill: no billed model" : ""}</span>
        ${source ? `<span class="ms-live"><i></i>live</span>` : ""}`);
      put(el.querySelector("#msNotes"), notesList(m));
      put(el.querySelector("#msBanner"), m.escalation && m.escalation.suggested && m.status !== "proposed" ? `<div class="ms-banner">Escalation to Emergency Mode suggested: ${esc(m.escalation.why)}.
        ${can.emergency ? `<button class="hub-pill" type="button" data-act="escalate">Open as a critical incident</button>` : ""}</div>` : "");
      if (m.status === "proposed") {
        put(el.querySelector("#msTop"), proposalCard(m, can));
        put(el.querySelector("#msLeft"), ""); put(el.querySelector("#msRight"), ""); put(el.querySelector("#msActs"), actionsCard(m));
      } else if (m.status === "declined") {
        put(el.querySelector("#msTop"), `<section class="hv-card ms-card"><h3>Continued normally</h3><p class="ms-note">ATLAS will not propose this again for 24 hours. You can still ask for a mission on the Missions page.</p></section>`);
      } else {
        put(el.querySelector("#msTop"), reportCard(m) + liveCards(m, can));
        put(el.querySelector("#msActs"), guardCard(m) + actionsCard(m) + conflictsCard(m));
        put(el.querySelector("#msLeft"), evidenceCard(m) + usageCard(m));
        put(el.querySelector("#msRight"), hypCard(m) + memoryCard(m) + `<section class="hv-card ms-card"><h3>Timeline <span class="ms-note">the Commander's activity</span></h3><ol class="ms-tl">${(m.events || []).slice().reverse().map((e) => `<li data-k="${esc(e.kind)}"><time>${esc(new Date(e.at * 1000).toLocaleTimeString("en-GB"))}</time>${esc(e.text)}</li>`).join("")}</ol></section>`);
      }
      el.querySelectorAll("[data-act]").forEach((b) => b.onclick = async () => {
        const act = b.dataset.act, msg = el.querySelector("#msAct");
        if (act === "review") { const d = el.querySelector("#msPlan"); if (d) { d.open = true; d.scrollIntoView({behavior: "smooth", block: "center"}); } return; }
        if (act === "escalate") { const d = await post("/api/missions/emergency", {question: m.question}); if (d.mission) location.hash = "#missions?id=" + d.mission.id; return; }
        b.disabled = true;
        let body = {};
        if (act === "approve" || act === "drill") {
          const remove = [...el.querySelectorAll('.ms-plan input[type=checkbox][data-step]')].filter((c) => !c.checked).map((c) => c.dataset.step);
          const prof = el.querySelector("#msProf"), dl = el.querySelector("#msDl");
          body = {remove, deadline_min: dl ? Number(dl.value) || undefined : undefined, profile: act === "drill" ? "drill" : (prof ? prof.value : undefined)};
        }
        const d = await post(`/api/missions/${encodeURIComponent(id)}/${act === "drill" ? "approve" : act}`, body);
        if (msg) msg.textContent = d.message || "";
        b.disabled = false;
        refresh();
      });
      el.querySelectorAll('.ms-plan input[type=checkbox][data-step]').forEach((c) => c.onchange = () => c.closest("li").classList.toggle("off", !c.checked));
      el.querySelectorAll("[data-aact]").forEach((b) => b.onclick = async () => {
        b.disabled = true;
        const d = await post(`/api/missions/${encodeURIComponent(id)}/actions/${encodeURIComponent(b.dataset.aid)}/${b.dataset.aact}`, {});
        const msg = el.querySelector("#msAAct"); if (msg) msg.textContent = d.message || "";
        b.disabled = false; refresh();
      });
      el.querySelectorAll("[data-mact]").forEach((b) => b.onclick = async () => {
        b.disabled = true;
        const d = await post(`/api/missions/${encodeURIComponent(id)}/memory/${encodeURIComponent(b.dataset.mid)}/${b.dataset.mact}`, {});
        const msg = el.querySelector("#msAAct") || el.querySelector("#msAct"); if (msg) msg.textContent = d.message || "";
        b.disabled = false; refresh();
      });
      const watching = ["queued", "running", "paused"].includes(m.status) || ["awaiting_approval", "awaiting_outcome"].includes(m.verification_status);
      if (watching && current === id) follow(m);
    };
    const refresh = () => { clearTimeout(timer); timer = null; draw(); };
    // Live: the mission's stream says when it changed; without it (or if it drops), poll every 1.5 s.
    const follow = () => {
      if (source || timer) return;
      if (window.EventSource && !follow.failed) {
        source = new EventSource("/api/missions/" + encodeURIComponent(id) + "/stream");
        let seen = null;
        source.addEventListener("mission", (ev) => { let v = null; try { v = JSON.parse(ev.data).version; } catch (e) { /* ignore */ } if (v !== seen) { seen = v; draw(); } });
        source.onerror = () => {
          if (source) { source.close(); source = null; }
          // a stream that ended (mission finished, or its 10-minute cap) or failed: poll from here on
          follow.failed = true;
          if (current === id && !timer) timer = setTimeout(() => { timer = null; draw(); }, 1500);
        };
        return;
      }
      timer = setTimeout(() => { timer = null; draw(); }, 1500);
    };
    current = id;
    draw();
  }

  V.missions = {
    title: "Missions",
    async render(el) {
      css(); stop();
      const id = param("id");
      if (id && /^[a-f0-9]{10}$/.test(id)) detail(el, id); else list(el);
    }
  };
  // leaving the view stops its refresh
  const leave = V._leave;
  V._leave = () => { stop(); if (leave) leave(); };
})();
