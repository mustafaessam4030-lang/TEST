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
.cm-map{position:relative;border-radius:12px;background:var(--surface-2);overflow:hidden;min-height:140px}
.cm-map svg{width:100%;height:auto;max-height:420px;display:block}
.cm-map .land{fill:var(--surface-3);stroke:var(--surface-2);stroke-width:.9}
.cm-map .land.bu{fill:var(--line-hi)}
.cm-map a{cursor:pointer}
.cm-map .dot{stroke:var(--surface);stroke-width:2;transition:stroke .15s}
.cm-map a:hover .dot,.cm-map a:focus-visible .dot{stroke:var(--ink)}
.cm-map a:focus-visible{outline:none}
.cm-map .dot.ok{fill:var(--grn-dot)} .cm-map .dot.mid{fill:var(--y-dot)} .cm-map .dot.bad{fill:var(--red)} .cm-map .dot.none{fill:var(--nomode)}
.cm-map text{font:650 11px var(--s);fill:var(--ink);pointer-events:none;paint-order:stroke;stroke:var(--surface-2);stroke-width:3px;stroke-linejoin:round}
.cm-msg{padding:28px 16px;text-align:center;color:var(--faint);font-size:13px}
.cm-msg a{color:var(--vio)}
.cm-list{display:grid;gap:2px;font-size:13px;max-height:420px;overflow:auto;align-content:start}
.cm-list .mh{font:600 11px/1.2 var(--s);letter-spacing:.06em;text-transform:uppercase;color:var(--faint);padding:0 8px 6px}
.cm-list a{display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:8px;align-items:center;padding:6px 8px;border-radius:9px;
  color:inherit;text-decoration:none}
.cm-list a:hover,.cm-list a:focus-visible{background:var(--surface-3);outline:none}
.cm-list b{font-weight:650}
.cm-list small{display:block;color:var(--faint);font-size:11.5px}
.cm-list .n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cm-list .n em{font-style:normal;color:var(--red);font-weight:600}
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
    const list = `<div class="mh">BU · open · past due</div>` + bus.map((b) =>
      `<a href="${link(b.bu)}" data-tip="${esc(tipFor(b, places[b.bu]))}"><b>${esc(b.bu)}</b>` +
      `<span>${esc(places[b.bu] ? places[b.bu].country : "not on the map")}<small>${fmt(b.n)} shipments</small></span>` +
      `<span class="n">${fmt(b.open)} · <em>${fmt(b.late)}</em></span></a>`).join("");
    let map = `<div class="cm-msg">The map could not be loaded; the list beside it is complete.</div>`;
    if (REGION && REGION.countries) {
      const byIso = {}; bus.forEach((b) => { if (places[b.bu]) byIso[places[b.bu].iso] = b; });
      const max = Math.max(1, ...bus.map((b) => b.open));
      let land = "", dots = "";
      const pts = [];
      REGION.countries.forEach((c) => {
        const b = byIso[c.id];
        land += `<path class="land${b ? " bu" : ""}" d="${c.d}"><title>${esc(c.name)}</title></path>`;
        if (!b) return;
        pts.push(c.label);
        const r = b.open ? 6 + 20 * Math.sqrt(b.open / max) : 5;
        dots += `<a href="${link(b.bu)}" aria-label="${esc(b.bu)}: ${fmt(b.open)} open, ${fmt(b.late)} past due" data-tip="${esc(tipFor(b, places[b.bu]))}">` +
          `<circle class="dot ${tone(b)}" cx="${c.label[0]}" cy="${c.label[1]}" r="${r.toFixed(1)}"></circle>` +
          `<text x="${c.label[0]}" y="${(c.label[1] + r + 12).toFixed(1)}" text-anchor="middle">${esc(b.bu)} ${fmt(b.open)}</text></a>`;
      });
      let vb = REGION.viewBox;
      if (pts.length) {          // framed on the BUs that are placed, at the map's own shape
        const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]), pad = 70, aspect = 1.75;
        let x0 = Math.min(...xs) - pad, w = Math.max(...xs) + pad - x0, y0 = Math.min(...ys) - pad, h = Math.max(...ys) + pad - y0;
        if (w / h < aspect) { const nw = h * aspect; x0 -= (nw - w) / 2; w = nw; } else { const nh = w / aspect; y0 -= (nh - h) / 2; h = nh; }
        vb = [x0, y0, w, h].map((v) => Math.round(v));
      }
      map = `<svg viewBox="${vb.join(" ")}" role="img" aria-label="Map: open shipments by BU">${land}${dots}</svg>`;
    }
    body.innerHTML = `<p class="cm-head">${head}</p><div class="cm-wrap"><div class="cm-map">${map}</div><div class="cm-list">${list}</div></div>
      <div class="cm-leg"><span>Bubble size: open shipments</span><span><i style="background:var(--grn-dot)"></i>under 20% of them past due</span>
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
  load();
  // The export changes a few times a day: look again every 5 minutes and when the tab comes back.
  setInterval(() => { if (!document.hidden) load(); }, 300000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && Date.now() - last > 60000) load(); });
})();
