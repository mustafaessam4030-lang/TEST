/* Where shipments clear: the Overview's map of the eHub export by BU.
   Counts come from GET /api/clearance/map (dashboard/clearance.py
   map_summary), outlines from /static/maps/region.json (Natural Earth).
   Bubble size: open shipments; colour: the share of them past due. A BU is
   placed only where the server says which country it is. The export has no
   origin, port or position, so no routes are drawn. Clicking a BU opens
   Clearance filtered to it. Read-only; styled with the platform's tokens. */
(function () {
  const box = document.querySelector("[data-clearmap]");
  if (!box || box.dataset.ready) return;
  box.dataset.ready = "1";
  const css = document.createElement("style");
  css.textContent = `
.cm-head{margin:0 0 14px;font-size:14.5px;line-height:1.45;color:var(--ink)}
.cm-head b{font-weight:650}
.cm-wrap{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(240px,1fr);gap:20px;align-items:start}
.cm-map{position:relative;border-radius:14px;overflow:hidden;min-height:140px;border:1px solid var(--line-2);background:var(--cm-sea)}
.cm-map svg{width:100%;height:auto;max-height:450px;display:block}
.cm-map .sea{fill:url(#cmDots)}
.cm-map .dotp{fill:var(--cm-dot)}
.cm-map .grid{fill:none;stroke:var(--cm-grid);stroke-width:.6}
.cm-map .land{fill:var(--cm-land);stroke:var(--cm-border);stroke-width:.5;stroke-linejoin:round}
.cm-map .land.bu{stroke:var(--cm-bu-edge);stroke-width:.9;transition:filter .2s var(--e)}
.cm-map .land.bu.hl{filter:brightness(.94)}
.cm-map a{cursor:pointer;outline:none}
.cm-map .b{transform-box:fill-box;transform-origin:center;transition:transform .22s var(--e)}
.cm-map a:hover .b,.cm-map a:focus-visible .b,.cm-map a.hl .b{transform:scale(1.14)}
.cm-map .ring{fill:none;stroke-width:1.4;opacity:.45}
.cm-map .dot{stroke:var(--cm-ring);stroke-width:2;filter:url(#cmShadow)}
.cm-map a:focus-visible .dot{stroke:var(--vio);stroke-width:3}
.cm-map .ok{fill:var(--grn-dot);stroke:var(--grn-dot)} .cm-map .mid{fill:var(--y-dot);stroke:var(--y-dot)}
.cm-map .bad{fill:var(--red);stroke:var(--red)} .cm-map .none{fill:var(--nomode);stroke:var(--nomode)}
.cm-map .dot.ok,.cm-map .dot.mid,.cm-map .dot.bad,.cm-map .dot.none{stroke:var(--cm-ring)}
.cm-map .lead{stroke:var(--cm-lead);stroke-width:.9;fill:none}
.cm-map .pill rect{fill:var(--cm-pill);stroke:var(--cm-pill-edge);stroke-width:.6;filter:url(#cmShadow)}
.cm-map .pill text{font:700 10.5px var(--s);fill:var(--ink);letter-spacing:.02em}
.cm-map .pill text tspan.n{font-weight:500;fill:var(--mid);letter-spacing:0}
.cm-map .grow{animation:cmIn .6s var(--e) both}
@keyframes cmIn{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.cm-map .grow{animation:none}.cm-map .b{transition:none}}
:root{--cm-sea:#F3F5F9;--cm-dot:rgba(40,60,100,.13);--cm-grid:rgba(30,60,110,.06);--cm-land:#FFFFFF;--cm-border:rgba(30,45,70,.17);
  --cm-bu-edge:rgba(20,30,50,.34);--cm-ring:#FFFFFF;--cm-pill:#FFFFFF;--cm-pill-edge:rgba(0,0,0,.08);--cm-lead:rgba(20,30,50,.35)}
:root[data-theme="dark"]{--cm-sea:#0C1018;--cm-dot:rgba(255,255,255,.07);--cm-grid:rgba(255,255,255,.04);--cm-land:#1A2130;--cm-border:rgba(255,255,255,.09);
  --cm-bu-edge:rgba(255,255,255,.30);--cm-ring:#0B0E14;--cm-pill:#1C2331;--cm-pill-edge:rgba(255,255,255,.10);--cm-lead:rgba(255,255,255,.30)}
.cm-leg .sz{display:inline-flex;align-items:center;gap:4px}
.cm-leg .sz b{display:inline-block;border-radius:50%;border:1.5px solid var(--faint);background:transparent}
.cm-leg .sh{display:inline-flex;align-items:center;gap:6px}
.cm-leg .sh u{display:inline-block;width:56px;height:8px;border-radius:4px;border:1px solid var(--line);
  background:linear-gradient(90deg,color-mix(in srgb,var(--red) 12%,var(--cm-land)),color-mix(in srgb,var(--red) 58%,var(--cm-land)))}
.cm-msg{padding:28px 16px;text-align:center;color:var(--faint);font-size:13px}
.cm-msg a{color:var(--vio)}
.cm-list{display:grid;gap:2px;font-size:13px;max-height:420px;overflow:auto;align-content:start}
.cm-list .mh{font:600 11px/1.2 var(--s);letter-spacing:.06em;text-transform:uppercase;color:var(--faint);padding:0 8px 6px}
.cm-list a{display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:2px 10px;align-items:center;padding:7px 8px;border-radius:9px;
  color:inherit;text-decoration:none;transition:background-color .15s var(--e)}
.cm-list a:hover,.cm-list a:focus-visible,.cm-list a.hl{background:var(--surface-3);outline:none}
.cm-list b{font-weight:650}
.cm-list small{display:block;color:var(--faint);font-size:11.5px}
.cm-list .n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cm-list .n em{font-style:normal;color:var(--red);font-weight:600}
.cm-list .bar{grid-column:2/4;height:4px;border-radius:2px;background:var(--surface-3);overflow:hidden;margin-top:3px}
.cm-list .bar i{display:block;height:100%;border-radius:2px;background:var(--line-hi);position:relative}
.cm-list .bar i s{position:absolute;left:0;top:0;bottom:0;background:var(--red);border-radius:2px}
.cm-leg{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--faint);margin-top:12px}
.cm-leg i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.cm-tip{position:fixed;z-index:95;pointer-events:none;max-width:280px;background:#1D1D1F;color:#fff;font-size:12.5px;line-height:1.4;
  border-radius:8px;padding:7px 10px;box-shadow:0 8px 24px rgba(0,0,0,.25);opacity:0;transition:opacity .12s}
:root[data-theme="dark"] .cm-tip{background:#262D3B}
.cm-tip.show{opacity:1}
.cm-tip small{display:block;color:#B8BCC6;margin-top:3px}
@media (max-width:1100px){.cm-wrap{grid-template-columns:minmax(0,1fr)}}`;
  document.head.appendChild(css);
  const body = box.querySelector("[data-clearmap-body]") || box;
  const note = box.querySelector("[data-clearmap-note]");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const pct = (a, b) => (b ? Math.round(100 * a / b) : 0);
  const step = (s) => (s === "Awaiting Reciept" ? "Awaiting Receipt" : s);
  const tip = document.createElement("div");
  tip.className = "cm-tip"; tip.setAttribute("role", "tooltip");
  document.body.appendChild(tip);
  let REGION = null, last = 0;

  function tipFor(b, place) {
    return `<b>${esc(b.bu)}</b>${place ? " · " + esc(place.country) : " · country not known from the BU code"}: ` +
      `${fmt(b.open)} open, ${fmt(b.late)} past due${b.open ? ` (${pct(b.late, b.open)}%)` : ""}, ${fmt(b.n - b.open)} completed` +
      `<small>${b.top_step ? `Most past due at ${esc(step(b.top_step))} (${fmt(b.top_n)}). ` : ""}Click to open ${esc(b.bu)} on Clearance.</small>`;
  }
  function paint(d) {
    if (!d || d.empty) {
      body.innerHTML = `<div class="cm-msg">No eHub export is loaded yet. <a href="/clearance">Load one on Clearance</a>, and this map shows where shipments clear.</div>`;
      if (note) note.textContent = "";
      return;
    }
    const places = d.places || {}, bus = d.bus || [];
    const link = (bu) => "/clearance#bu=" + encodeURIComponent(bu);
    const topBu = bus.find((b) => b.late);
    const head = !d.open ? `All <b>${fmt(d.total)}</b> shipments in the export are completed.`
      : !d.late ? `<b>${fmt(d.open)}</b> open shipments, none past its step's due date.`
      : `<b>${fmt(d.late)}</b> of ${fmt(d.open)} open shipments are past their step's due date` +
        (topBu ? `; <b>${fmt(topBu.late)}</b> of them are in <b>${esc(topBu.bu)}</b>${places[topBu.bu] ? ` (${esc(places[topBu.bu].country)})` : ""}` : "") +
        (d.top_step ? `, most waiting at <b>${esc(step(d.top_step))}</b> (${fmt(d.top_n)})` : "") + ".";
    const off = bus.filter((b) => !places[b.bu]).map((b) => b.bu);
    if (note) note.textContent = `eHub export, data as of ${d.asof || "an unknown date"}` + (off.length ? ` · not on the map: ${off.join(", ")}` : "");
    const tone = (b) => !b.open ? "none" : b.late / b.open > 0.5 ? "bad" : b.late / b.open >= 0.2 ? "mid" : "ok";
    const maxOpen = Math.max(1, ...bus.map((b) => b.open));
    const list = `<div class="mh">BU · open · past due</div>` + bus.map((b) =>
      `<a href="${link(b.bu)}" data-bu="${esc(b.bu)}" data-tip="${esc(tipFor(b, places[b.bu]))}"><b>${esc(b.bu)}</b>` +
      `<span>${esc(places[b.bu] ? places[b.bu].country : "not on the map")}<small>${fmt(b.n)} shipments</small></span>` +
      `<span class="n">${fmt(b.open)} · <em>${fmt(b.late)}</em></span>` +
      `<span class="bar" aria-hidden="true"><i style="width:${(100 * b.open / maxOpen).toFixed(1)}%"><s style="width:${b.open ? (100 * b.late / b.open).toFixed(1) : 0}%"></s></i></span></a>`).join("");
    let map = `<div class="cm-msg">The map could not be loaded; the list beside it is complete.</div>`;
    if (REGION && REGION.countries) {
      const byIso = {}; bus.forEach((b) => { if (places[b.bu]) byIso[places[b.bu].iso] = b; });
      const size = (b) => (b.open ? 6 + 20 * Math.sqrt(b.open / maxOpen) : 4.5);
      const toneVar = {ok: "--grn-dot", mid: "--y-dot", bad: "--red", none: "--nomode"};
      let land = "";
      const spots = [];
      REGION.countries.forEach((c) => {
        const b = byIso[c.id];
        if (!b) { land += `<path class="land" d="${c.d}"><title>${esc(c.name)}</title></path>`; return; }
        // The country is shaded by its share of open shipments past due.
        const share = b.open ? b.late / b.open : 0, mix = 14 + Math.round(44 * share);
        land += `<path class="land bu" data-bu="${esc(b.bu)}" style="fill:color-mix(in srgb,var(${toneVar[tone(b)]}) ${mix}%,var(--cm-land))" d="${c.d}"><title>${esc(c.name)}</title></path>`;
        spots.push({b, x: c.label[0], y: c.label[1], r: size(b)});
      });
      // Labels: below the bubble when there is room, else above, right or left;
      // moved ones get a thin line back to their bubble.
      const placed = [];
      const hit = (r) => placed.some((q) => r.x < q.x + q.w + 3 && q.x < r.x + r.w + 3 && r.y < q.y + q.h + 3 && q.y < r.y + r.h + 3) ||
        spots.some((p) => r.x < p.x + p.r && p.x - p.r < r.x + r.w && r.y < p.y + p.r && p.y - p.r < r.y + r.h);
      const order = spots.slice().sort((p, q) => q.r - p.r);
      order.forEach((p) => {
        const n = fmt(p.b.open), w = 14 + 7 * p.b.bu.length + 6.2 * (n.length + 1), h = 17;
        const tries = [[p.x - w / 2, p.y + p.r + 5], [p.x - w / 2, p.y - p.r - 5 - h], [p.x + p.r + 6, p.y - h / 2], [p.x - p.r - 6 - w, p.y - h / 2]];
        for (let k = 1; k <= 6; k++) tries.push([p.x - w / 2, p.y + p.r + 5 + k * (h + 4)], [p.x + p.r + 6, p.y - h / 2 + k * (h + 4)]);
        let pick = tries.find(([x, y]) => !hit({x, y, w, h})) || tries[0];
        p.pill = {x: pick[0], y: pick[1], w, h, far: tries.indexOf(pick) > 1};      // not straight below or above: draw a line
        placed.push(p.pill);
      });
      let dots = "", leads = "";
      order.forEach((p, i) => {
        const b = p.b, t = tone(b), n = fmt(b.open), L = p.pill;
        if (L.far) {
          const cx = Math.max(L.x, Math.min(p.x, L.x + L.w)), cy = Math.max(L.y, Math.min(p.y, L.y + L.h));
          leads += `<path class="lead" d="M${p.x.toFixed(1)} ${p.y.toFixed(1)}L${cx.toFixed(1)} ${cy.toFixed(1)}"></path>`;
        }
        dots += `<a href="${link(b.bu)}" data-bu="${esc(b.bu)}" aria-label="${esc(b.bu)}: ${fmt(b.open)} open, ${fmt(b.late)} past due" data-tip="${esc(tipFor(b, places[b.bu]))}">` +
          `<g class="grow" style="animation-delay:${(i * 50)}ms;transform-origin:${p.x}px ${p.y}px"><g class="b">` +
          `<circle class="ring ${t}" cx="${p.x}" cy="${p.y}" r="${(p.r + 4.5).toFixed(1)}"></circle>` +
          `<circle class="dot ${t}" cx="${p.x}" cy="${p.y}" r="${p.r.toFixed(1)}"></circle></g>` +
          `<g class="pill"><rect x="${L.x.toFixed(1)}" y="${L.y.toFixed(1)}" width="${L.w.toFixed(1)}" height="${L.h}" rx="8.5"></rect>` +
          `<text x="${(L.x + L.w / 2).toFixed(1)}" y="${(L.y + 12).toFixed(1)}" text-anchor="middle">${esc(b.bu)} <tspan class="n">${n}</tspan></text></g></g></a>`;
      });
      let vb = REGION.viewBox;
      if (spots.length) {        // framed on the BUs that are placed and their labels
        const xs = [], ys = [];
        spots.forEach((p) => { xs.push(p.x - p.r, p.x + p.r, p.pill.x, p.pill.x + p.pill.w); ys.push(p.y - p.r, p.y + p.r, p.pill.y, p.pill.y + p.pill.h); });
        const pad = 46, aspect = 1.6;
        let x0 = Math.min(...xs) - pad, w = Math.max(...xs) + pad - x0, y0 = Math.min(...ys) - pad, h = Math.max(...ys) + pad - y0;
        if (w / h < aspect) { const nw = h * aspect; x0 -= (nw - w) / 2; w = nw; } else { const nh = w / aspect; y0 -= (nh - h) / 2; h = nh; }
        vb = [x0, y0, w, h].map((v) => Math.round(v));
      }
      map = `<svg viewBox="${vb.join(" ")}" role="img" aria-label="Map: open shipments by BU"><defs>` +
        `<pattern id="cmDots" width="7" height="7" patternUnits="userSpaceOnUse"><circle class="dotp" cx="1.2" cy="1.2" r=".7"></circle></pattern>` +
        `<filter id="cmShadow" x="-50%" y="-50%" width="200%" height="200%"><feDropShadow dx="0" dy="1.2" stdDeviation="1.4" flood-color="#0B1F33" flood-opacity=".22"/></filter>` +
        `<filter id="cmLand" x="-5%" y="-5%" width="110%" height="110%"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#0B1F33" flood-opacity=".10"/></filter></defs>` +
        `<rect class="sea" x="${vb[0]}" y="${vb[1]}" width="${vb[2]}" height="${vb[3]}"></rect>` +
        `${REGION.graticule ? `<path class="grid" d="${REGION.graticule}"></path>` : ""}<g filter="url(#cmLand)">${land}</g>${leads}${dots}</svg>`;
    }
    body.innerHTML = `<p class="cm-head">${head}</p><div class="cm-wrap"><div class="cm-map">${map}</div><div class="cm-list">${list}</div></div>
      <div class="cm-leg"><span class="sz"><b style="width:8px;height:8px"></b><b style="width:14px;height:14px"></b><b style="width:20px;height:20px"></b>&nbsp;open shipments</span><span class="sh"><u></u>country shade: share of its open shipments past due</span><span><i style="background:var(--grn-dot)"></i>under 20% past due</span>
      <span><i style="background:var(--y-dot)"></i>20–50%</span><span><i style="background:var(--red)"></i>over 50%</span>
      <span><i style="background:var(--nomode)"></i>none open</span><span>The export has no origin or position, so no routes are drawn.</span></div>`;
  }
  async function load() {
    last = Date.now();
    try {
      const r = await fetch("/api/clearance/map", {credentials: "same-origin"});
      if (r.status === 401 || r.status === 403 || r.status === 404) { box.hidden = true; return; }   // not for this role / server
      if (!r.ok) throw new Error("HTTP " + r.status);
      const d = await r.json();
      if (!REGION && !d.empty) {
        try { const m = await fetch("/static/maps/region.json", {credentials: "same-origin"}); if (m.ok) REGION = await m.json(); } catch (e) { /* list only */ }
      }
      box.hidden = false;
      paint(d);
    } catch (e) {
      body.innerHTML = `<div class="cm-msg">The Clearance figures could not be loaded (${esc(e.message || e)}).</div>`;
    }
  }
  body.addEventListener("mousemove", (e) => {
    const t = e.target.closest("[data-tip]");
    if (!t) { tip.classList.remove("show"); return; }
    tip.innerHTML = t.getAttribute("data-tip");
    const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8), y = Math.min(e.clientY + 14, innerHeight - tip.offsetHeight - 8);
    tip.style.left = x + "px"; tip.style.top = y + "px"; tip.classList.add("show");
  });
  body.addEventListener("mouseleave", () => tip.classList.remove("show"));
  // A BU under the pointer, in the list or on the map, is lit in both.
  function light(bu) { body.querySelectorAll("[data-bu]").forEach((el) => el.classList.toggle("hl", !!bu && el.dataset.bu === bu)); }
  body.addEventListener("mouseover", (e) => { const t = e.target.closest("[data-bu]"); light(t ? t.dataset.bu : null); });
  body.addEventListener("focusin", (e) => { const t = e.target.closest("[data-bu]"); light(t ? t.dataset.bu : null); });
  body.addEventListener("mouseleave", () => light(null));
  load();
  // The export changes a few times a day: look again every 5 minutes and when the tab comes back.
  setInterval(() => { if (!document.hidden) load(); }, 300000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && Date.now() - last > 60000) load(); });
})();
