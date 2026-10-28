/* Tracking map: where each shipment of the run was last reported, on a real
   map. Outlines from /static/maps/world.json (Natural Earth, Mercator);
   places from each shipment's record.where (dashboard/geo.py), which holds
   only the places the carrier's own events named, matched to an airport,
   port or town. Nothing is placed between two events and nothing is moved
   with time: a marker is the newest place the carrier reported, at town or
   airport precision, and the map says so.

   The console calls ctTrackMap.paint({items, selected, onSelect}); each item
   is {ref, carrier, where, k (journey), label, date, color, ring, icon}.
   Read-only. Drag to move, Ctrl/⌘ + scroll or the buttons to zoom, click a
   marker to follow that shipment. */
(function () {
  const css = document.createElement("style");
  css.textContent = `
.tm{position:relative;height:100%;min-height:360px;border-radius:14px;overflow:hidden;border:1px solid var(--line-2);
  background:var(--tm-sea);touch-action:none;user-select:none;-webkit-user-select:none;cursor:grab;outline:none}
.tm.drag{cursor:grabbing}
.tm svg{position:absolute;inset:0;width:100%;height:100%;display:block}
.tm .sea{fill:url(#tmDots)}
.tm .dotp{fill:var(--tm-dot)}
.tm .grid{fill:none;stroke:var(--tm-grid);stroke-width:1}
.tm .land path{fill:var(--tm-land);stroke:var(--tm-border);stroke-width:.7;stroke-linejoin:round}
.tm .land path.dest{fill:color-mix(in srgb,var(--tm-accent) 13%,var(--tm-land));stroke:color-mix(in srgb,var(--tm-accent) 45%,var(--tm-border))}
.tm .tm-done{fill:none;stroke-width:3;stroke-linecap:round}
.tm .tm-next{fill:none;stroke-width:2;stroke-dasharray:2 7;stroke-linecap:round;stroke:var(--tm-next)}
.tm .tm-plan{fill:none;stroke-width:2;stroke-dasharray:2 7;stroke-linecap:round}
.tm .tm-glow{fill:none;stroke-width:9;stroke-linecap:round;opacity:.14}
.tm .stop{fill:var(--tm-pill);stroke-width:2}
.tm .end{fill:var(--tm-pill);stroke:var(--tm-ink);stroke-width:2.2}
.tm .lab{pointer-events:none}
.tm .lab rect{fill:var(--tm-pill);stroke:var(--tm-pill-edge);stroke-width:.8;filter:url(#tmShadow)}
.tm .lab text{font:600 11px var(--s);fill:var(--ink)}
.tm .lab text .s{font-weight:500;fill:var(--mid)}
.tm .mk{cursor:pointer;outline:none}
.tm .mk .hit{fill:transparent}
.tm .mk .b{transition:transform .18s var(--e)}
.tm .mk:hover .b,.tm .mk:focus-visible .b{transform:scale(1.18)}
.tm .mk .rim{stroke:var(--tm-pill);stroke-width:2}
.tm .mk:focus-visible .rim{stroke:var(--vio);stroke-width:3}
.tm .mk .n{font:700 10.5px var(--s);fill:var(--tm-pill);text-anchor:middle;dominant-baseline:central;pointer-events:none}
.tm .mk.sel .face{fill:var(--tm-pill);filter:url(#tmShadow)}
.tm .pulse{fill:none;stroke-width:2;animation:tmPulse 2s var(--e) infinite;transform-box:fill-box;transform-origin:center}
@keyframes tmPulse{from{opacity:.7;transform:scale(.7)}to{opacity:0;transform:scale(1.9)}}
.tm-ui{position:absolute;right:10px;top:10px;display:grid;gap:6px;z-index:2}
.tm-ui button{width:30px;height:30px;border-radius:9px;border:1px solid var(--line);background:var(--tm-pill);color:var(--ink);
  font:600 15px/1 var(--s);cursor:pointer;display:grid;place-items:center;box-shadow:0 2px 8px rgba(0,0,0,.08)}
.tm-ui button:hover{background:var(--surface-3)}
.tm-ui button:focus-visible{outline:2px solid var(--vio);outline-offset:1px}
.tm-ui svg{position:static;width:15px;height:15px}
.tm-pick{position:absolute;left:10px;top:10px;z-index:2;max-width:calc(100% - 60px)}
.tm-pick select{max-width:100%;height:30px;border-radius:9px;border:1px solid var(--line);background:var(--tm-pill);color:var(--ink);
  font:500 12.5px var(--s);padding:0 26px 0 10px;box-shadow:0 2px 8px rgba(0,0,0,.08);cursor:pointer}
.tm-foot{position:absolute;left:8px;bottom:8px;max-width:calc(100% - 16px);z-index:2;display:flex;flex-wrap:wrap;align-items:center;gap:2px 10px;
  font-size:11px;line-height:1.5;color:var(--mid);pointer-events:none;background:color-mix(in srgb,var(--tm-pill) 88%,transparent);
  border:1px solid var(--line-2);border-radius:8px;padding:3px 9px;backdrop-filter:blur(4px)}
.tm-foot b{font-weight:600;color:var(--ink)}
.tm-foot i{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:4px;vertical-align:0}
.tm-msg{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:2;max-width:min(320px,86%);text-align:center;
  background:var(--tm-pill);border:1px solid var(--line);border-radius:12px;padding:12px 14px;font-size:12.5px;line-height:1.45;color:var(--mid);
  box-shadow:0 8px 24px rgba(0,0,0,.10);pointer-events:none}
.tm-msg b{display:block;color:var(--ink);font-size:13px;margin-bottom:2px}
.tm-msg[hidden],.tm-hint[hidden]{display:none}
.tm-hint{position:absolute;left:50%;top:14px;transform:translateX(-50%);z-index:3;background:rgba(20,24,32,.82);color:#fff;
  font-size:12px;border-radius:8px;padding:5px 10px;pointer-events:none}
.tm-pop{position:absolute;z-index:4;min-width:200px;max-width:280px;background:var(--tm-pill);border:1px solid var(--line);border-radius:11px;
  box-shadow:0 12px 30px rgba(0,0,0,.16);padding:4px;font-size:12.5px}
.tm-pop button{display:block;width:100%;text-align:left;border:0;background:transparent;color:var(--ink);border-radius:8px;padding:6px 8px;cursor:pointer;font:inherit}
.tm-pop button:hover,.tm-pop button:focus-visible{background:var(--surface-3);outline:none}
.tm-pop small{color:var(--faint);margin-left:6px}
.tm-tip{position:fixed;z-index:95;pointer-events:none;max-width:290px;background:#1D1D1F;color:#fff;font-size:12.5px;line-height:1.42;
  border-radius:8px;padding:7px 10px;box-shadow:0 8px 24px rgba(0,0,0,.25);opacity:0;transition:opacity .12s}
:root[data-theme="dark"] .tm-tip{background:#262D3B}
.tm-tip.show{opacity:1}
.tm-tip small{display:block;color:#B8BCC6;margin-top:3px}
:root{--tm-sea:#EEF2F7;--tm-dot:rgba(40,60,100,.12);--tm-grid:rgba(30,60,110,.07);--tm-land:#FFFFFF;--tm-border:rgba(30,45,70,.16);
  --tm-pill:#FFFFFF;--tm-pill-edge:rgba(0,0,0,.08);--tm-ink:#0B1F33;--tm-next:#94A3B8;--tm-accent:var(--teal-dot)}
:root[data-theme="dark"]{--tm-sea:#0C1018;--tm-dot:rgba(255,255,255,.07);--tm-grid:rgba(255,255,255,.04);--tm-land:#1A2130;
  --tm-border:rgba(255,255,255,.10);--tm-pill:#1C2331;--tm-pill-edge:rgba(255,255,255,.10);--tm-ink:#E6EAF2;--tm-next:#5B6678}
@media (prefers-reduced-motion:reduce){.tm .pulse{animation:none;display:none}.tm .mk .b{transition:none}}`;
  document.head.appendChild(css);

  const NS = "http://www.w3.org/2000/svg";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const day = (d, t) => {
    const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(String(d || ""));
    return m ? `${+m[1]} ${MON[+m[2] - 1]}${t ? " " + t : ""}` : String(d || "");
  };
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  let world = null, loading = null, root = null, svg = null, L = {}, size = {w: 0, h: 0};
  let cam = null, anim = 0, data = {items: [], selected: null}, fitKey = "", host = null;
  const tip = document.createElement("div");
  tip.className = "tm-tip"; tip.setAttribute("role", "tooltip");
  document.body.appendChild(tip);

  /* ── projection: the same Mercator as tools/build_world_map.py ── */
  function mercY(lat) {
    const W = world.projection.width, l = Math.max(Math.min(lat, 85), -85);
    return W / (2 * Math.PI) * Math.log(Math.tan(Math.PI / 4 + l * Math.PI / 360));
  }
  function proj(p) {
    const W = world.projection.width;
    return [(p.lon + 180) / 360 * W, mercY(world.projection.lat_top) - mercY(p.lat)];
  }
  const placed = (p) => p && typeof p.lat === "number" && typeof p.lon === "number";
  const scr = (m) => [(m[0] - cam.x) / cam.s + size.w / 2, (m[1] - cam.y) / cam.s + size.h / 2];
  const fullS = () => Math.max(world.viewBox[2] / size.w, world.viewBox[3] / size.h);

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  /* ── what to draw for one shipment ── */
  // The newest place the carrier reported; for an arrival with no placed
  // event, the destination it arrived at. Never a point in between.
  function nowOf(it) {
    const w = it.where || {};
    if (w.last && placed(w.last.pt)) return {pt: w.last.pt, ev: w.last};
    if (it.k === "arrived" && placed(w.destination)) return {pt: w.destination, ev: null, arrived: true};
    return null;
  }
  function routeOf(it) {
    const w = it.where || {};
    const pts = [];
    const add = (p, kind) => {
      if (!placed(p)) return;
      const last = pts[pts.length - 1];
      if (last && Math.abs(last.p.lat - p.lat) < 1e-3 && Math.abs(last.p.lon - p.lon) < 1e-3) return;
      pts.push({p, kind});
    };
    const visited = (w.path || []).filter(placed);
    if (visited.length) add(w.origin, "origin");
    visited.forEach((p) => add(p, "stop"));
    return {done: pts, origin: placed(w.origin) ? w.origin : null,
            dest: placed(w.destination) ? w.destination : null,
            destCC: w.destination && w.destination.cc};
  }

  /* ── build once ── */
  function mount(box) {
    if (root && root.parentNode === box) return;
    box.innerHTML = "";
    root = document.createElement("div");
    root.className = "tm"; root.tabIndex = 0;
    root.setAttribute("aria-label", "Map: where each shipment was last reported by its carrier");
    box.appendChild(root);
    root.innerHTML = `<div class="tm-pick"><select aria-label="Shipment to show"></select></div>
      <div class="tm-ui">
        <button type="button" data-z="in" aria-label="Zoom in" title="Zoom in">+</button>
        <button type="button" data-z="out" aria-label="Zoom out" title="Zoom out">−</button>
        <button type="button" data-z="fit" aria-label="Fit the shipment" title="Fit the shipment's route"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button>
        <button type="button" data-z="all" aria-label="Show all shipments" title="Show all shipments"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg></button>
      </div>
      <div class="tm-msg" hidden></div><div class="tm-hint" hidden>Ctrl + scroll to zoom</div><div class="tm-foot"></div>`;
    svg = el("svg", {role: "presentation"});
    root.insertBefore(svg, root.firstChild);
    const defs = el("defs", {}, svg);
    defs.innerHTML = `<pattern id="tmDots" width="9" height="9" patternUnits="userSpaceOnUse"><circle class="dotp" cx="1.5" cy="1.5" r=".9"/></pattern>
      <filter id="tmShadow" x="-40%" y="-40%" width="180%" height="180%"><feDropShadow dx="0" dy="1.5" stdDeviation="2" flood-color="#0B1F33" flood-opacity=".18"/></filter>`;
    el("rect", {class: "sea", width: "100%", height: "100%"}, svg);
    L.geo = el("g", {}, svg);
    L.grid = el("path", {class: "grid", "vector-effect": "non-scaling-stroke"}, L.geo);
    L.land = el("g", {class: "land"}, L.geo);
    L.route = el("g", {}, svg);
    L.marks = el("g", {}, svg);
    L.labels = el("g", {}, svg);
    root.querySelector(".tm-ui").addEventListener("click", (e) => {
      const b = e.target.closest("[data-z]"); if (!b || !cam) return;
      const z = b.dataset.z;
      if (z === "in") zoomAt(size.w / 2, size.h / 2, 0.5, true);
      else if (z === "out") zoomAt(size.w / 2, size.h / 2, 2, true);
      else if (z === "all") flyTo(boundsAll(), true);
      else flyTo(boundsSel() || boundsAll(), true);
    });
    root.querySelector(".tm-pick select").addEventListener("change", (e) => {
      if (data.onSelect) data.onSelect(e.target.value);
    });
    wire();
    // The card can be measured while its page is hidden (0 × 0): the
    // camera is fitted only once it has a real size, and kept in bounds on
    // every resize after that.
    new ResizeObserver(() => {
      measure();
      if (!world || !shown()) return;
      if (!cam) { cam = fitCam(boundsSel() || boundsAll()); fitKey = keyOf(); }
      else cam = clampCam(cam);
      draw();
    }).observe(root);
    measure();
    if (world) buildLand();
  }
  const shown = () => size.w >= 40 && size.h >= 40;
  function keyOf() {
    const sel = data.items.find((i) => i.ref === data.selected);
    return sel ? JSON.stringify([sel.ref, sel.where && sel.where.last,
      sel.where && sel.where.destination && sel.where.destination.name, sel.k]) : "all";
  }
  function measure() {
    const r = root.getBoundingClientRect();
    size = {w: Math.max(1, r.width), h: Math.max(1, r.height)};
  }
  function buildLand() {
    if (!root || L.land.childNodes.length) return;
    L.grid.setAttribute("d", world.graticule);
    const frag = document.createDocumentFragment();
    for (const c of world.countries) {
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", c.d); p.setAttribute("vector-effect", "non-scaling-stroke");
      if (c.cc) p.dataset.cc = c.cc;
      frag.appendChild(p);
    }
    L.land.appendChild(frag);
  }

  /* ── camera ── */
  function clampCam(c) {
    const S = fullS(), s = Math.min(Math.max(c.s, S / 60), S);
    const vw = size.w * s, vh = size.h * s, W = world.viewBox[2], H = world.viewBox[3];
    const x = vw >= W ? W / 2 : Math.min(Math.max(c.x, vw / 2), W - vw / 2);
    const y = vh >= H ? H / 2 : Math.min(Math.max(c.y, vh / 2), H - vh / 2);
    return {x, y, s};
  }
  // Screen space the overlays take: the list at the top, the controls on the
  // right, the legend at the bottom. Fitting keeps places clear of them.
  const PAD = {top: 70, bottom: 64, left: 60, right: 70};
  function fitCam(b) {
    const w = Math.max(b[2] - b[0], 1), h = Math.max(b[3] - b[1], 1);
    const iw = Math.max(size.w - PAD.left - PAD.right, 60), ih = Math.max(size.h - PAD.top - PAD.bottom, 60);
    let s = Math.max(w / iw, h / ih);
    s = Math.max(s, fullS() / 14);               // never closer than a region
    const cx = PAD.left + iw / 2 - size.w / 2, cy = PAD.top + ih / 2 - size.h / 2;
    return clampCam({x: (b[0] + b[2]) / 2 - cx * s, y: (b[1] + b[3]) / 2 - cy * s, s});
  }
  function flyTo(b, user) {
    if (!b) return;
    const to = fitCam(b);
    if (!cam || reduced()) { cam = to; return draw(); }
    const from = cam, t0 = performance.now(), dur = user ? 520 : 760;
    cancelAnimationFrame(anim);
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      cam = {x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e,
             s: Math.exp(Math.log(from.s) + (Math.log(to.s) - Math.log(from.s)) * e)};
      draw(k < 1);
      if (k < 1) anim = requestAnimationFrame(step);
    };
    anim = requestAnimationFrame(step);
  }
  function zoomAt(px, py, f, smooth) {
    const mx = cam.x + (px - size.w / 2) * cam.s, my = cam.y + (py - size.h / 2) * cam.s;
    const s = cam.s * f;
    const to = clampCam({x: mx - (px - size.w / 2) * s, y: my - (py - size.h / 2) * s, s});
    if (!smooth || reduced()) { cam = to; return draw(); }
    flyToCam(to);
  }
  function flyToCam(to) {
    const from = cam, t0 = performance.now();
    cancelAnimationFrame(anim);
    const step = (t) => {
      const k = Math.min(1, (t - t0) / 300), e = 1 - Math.pow(1 - k, 3);
      cam = {x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e,
             s: Math.exp(Math.log(from.s) + (Math.log(to.s) - Math.log(from.s)) * e)};
      draw(k < 1);
      if (k < 1) anim = requestAnimationFrame(step);
    };
    anim = requestAnimationFrame(step);
  }
  function boundsOf(points) {
    if (!points.length) return null;
    const xs = points.map((m) => m[0]), ys = points.map((m) => m[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }
  function boundsSel() {
    const it = data.items.find((i) => i.ref === data.selected);
    if (!it) return null;
    const r = routeOf(it), n = nowOf(it), pts = r.done.map((d) => proj(d.p));
    if (r.dest) pts.push(proj(r.dest));
    if (!r.done.length && r.origin) pts.push(proj(r.origin));
    if (n) pts.push(proj(n.pt));
    return boundsOf(pts);
  }
  function boundsAll() {
    const pts = [];
    data.items.forEach((it) => { const n = nowOf(it); if (n) pts.push(proj(n.pt)); });
    const sel = boundsSel();
    if (sel) pts.push([sel[0], sel[1]], [sel[2], sel[3]]);
    return boundsOf(pts) || boundsOf([proj({lat: 38, lon: -18}), proj({lat: -12, lon: 56})]);
  }

  /* ── pointer: drag, Ctrl + wheel, double click ── */
  function wire() {
    let drag = null, hintT = 0;
    root.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest(".tm-ui,.tm-pick,.tm-pop,.mk")) return;
      closePop();
      drag = {x: e.clientX, y: e.clientY, cam: cam, moved: false};
      root.setPointerCapture(e.pointerId);
    });
    root.addEventListener("pointermove", (e) => {
      if (!drag || !cam) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      drag.moved = true; root.classList.add("drag"); hideTip();
      cancelAnimationFrame(anim);
      cam = clampCam({x: drag.cam.x - dx * drag.cam.s, y: drag.cam.y - dy * drag.cam.s, s: drag.cam.s});
      draw(true);
    });
    const end = () => { if (drag) { drag = null; root.classList.remove("drag"); draw(); } };
    root.addEventListener("pointerup", end);
    root.addEventListener("pointercancel", end);
    root.addEventListener("wheel", (e) => {
      if (!cam) return;
      if (!(e.ctrlKey || e.metaKey)) {                      // the page scrolls as usual
        const h = root.querySelector(".tm-hint"); h.hidden = false;
        clearTimeout(hintT); hintT = setTimeout(() => { h.hidden = true; }, 1100);
        return;
      }
      e.preventDefault();
      const r = root.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(e.deltaY * 0.0022), false);
    }, {passive: false});
    root.addEventListener("dblclick", (e) => {
      if (!cam || e.target.closest(".tm-ui,.tm-pick,.mk")) return;
      const r = root.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, 0.5, true);
    });
    root.addEventListener("keydown", (e) => {
      if (!cam || e.target !== root) return;
      const k = {ArrowLeft: [-60, 0], ArrowRight: [60, 0], ArrowUp: [0, -60], ArrowDown: [0, 60]}[e.key];
      if (k) { e.preventDefault(); cam = clampCam({x: cam.x + k[0] * cam.s, y: cam.y + k[1] * cam.s, s: cam.s}); draw(); }
      else if (e.key === "+" || e.key === "=") zoomAt(size.w / 2, size.h / 2, 0.5, true);
      else if (e.key === "-") zoomAt(size.w / 2, size.h / 2, 2, true);
    });
  }

  /* ── tooltip and the list for shipments reported at one place ── */
  function showTip(e, html) {
    tip.innerHTML = html; tip.classList.add("show");
    const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
    const y = e.clientY + 16 + tip.offsetHeight > innerHeight ? e.clientY - tip.offsetHeight - 12 : e.clientY + 16;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTip() { tip.classList.remove("show"); }
  function tipFor(it) {
    const n = nowOf(it);
    const ev = n && n.ev;
    const where = n ? (n.pt.code ? `${n.pt.name} (${n.pt.code})` : n.pt.name) + `, ${n.pt.country || n.pt.cc}` : "";
    return `<b>${esc(it.ref)}</b> · ${esc(it.carrier || "")}<br>${esc(it.label || "")}` +
      (ev ? `<small>Last reported: ${esc(ev.text)} — ${esc(where)}, ${esc(day(ev.date, ev.time))}` +
            (ev.said && ev.said !== n.pt.name ? `<br>Carrier wrote “${esc(ev.said)}”` : "") + `</small>`
          : n && n.arrived ? `<small>Arrived at ${esc(where)}</small>` : "");
  }
  function closePop() { const p = root && root.querySelector(".tm-pop"); if (p) p.remove(); }
  function openPop(group, x, y) {
    closePop();
    const pop = document.createElement("div");
    pop.className = "tm-pop"; pop.setAttribute("role", "menu");
    pop.innerHTML = group.map((it) => `<button type="button" role="menuitem" data-ref="${esc(it.ref)}">${esc(it.ref)}<small>${esc(it.carrier || "")} · ${esc(it.label || "")}</small></button>`).join("");
    root.appendChild(pop);
    pop.style.left = Math.min(Math.max(8, x - 100), size.w - pop.offsetWidth - 8) + "px";
    pop.style.top = Math.min(y + 14, size.h - pop.offsetHeight - 8) + "px";
    pop.addEventListener("click", (e) => {
      const b = e.target.closest("[data-ref]"); if (!b) return;
      closePop(); if (data.onSelect) data.onSelect(b.dataset.ref);
    });
    const f = pop.querySelector("button"); if (f) f.focus();
  }

  /* ── drawing ── */
  function arc(a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy);
    if (len < 1) return `M${a[0].toFixed(1)} ${a[1].toFixed(1)}`;
    let nx = -dy / len, ny = dx / len;
    if (ny > 0) { nx = -nx; ny = -ny; }                    // bend upwards, like a flight path
    const bend = Math.min(len * 0.2, 90);
    const c = [(a[0] + b[0]) / 2 + nx * bend, (a[1] + b[1]) / 2 + ny * bend];
    return `M${a[0].toFixed(1)} ${a[1].toFixed(1)}Q${c[0].toFixed(1)} ${c[1].toFixed(1)} ${b[0].toFixed(1)} ${b[1].toFixed(1)}`;
  }
  // A pill beside its point: right, left, above or below, whichever is
  // inside the map and clear of the pills and markers already placed.
  function label(x, y, title, sub, used, gap) {
    const g = el("g", {class: "lab"}, L.labels);
    const t = el("text", {x: 0, y: 0}, g);
    t.textContent = title;
    if (sub) { const s = el("tspan", {class: "s", dx: 6}, t); s.textContent = sub; }
    const w = t.getComputedTextLength() + 16, h = 22, r = gap || 8;
    const fits = (b) => b[0] >= 6 && b[1] >= 46 && b[0] + b[2] <= size.w - 46 && b[1] + b[3] <= size.h - 34;
    const clear = (b) => !used.some((u) => b[0] < u[0] + u[2] + 3 && u[0] < b[0] + b[2] + 3 && b[1] < u[1] + u[3] + 3 && u[1] < b[1] + b[3] + 3);
    const tries = [[x + r + 4, y - h / 2], [x - r - 4 - w, y - h / 2], [x - w / 2, y - r - 4 - h], [x - w / 2, y + r + 4],
                   [x + r + 4, y - r - h], [x - r - 4 - w, y + r]].map((p) => [p[0], p[1], w, h]);
    const box = tries.find((b) => fits(b) && clear(b)) || tries.find(fits) || tries[0];
    box[0] = Math.min(Math.max(box[0], 6), size.w - w - 6);
    box[1] = Math.min(Math.max(box[1], 46), size.h - h - 34);
    g.insertBefore(el("rect", {x: box[0], y: box[1], width: w, height: h, rx: 7}), t);   // the pill behind its text
    t.setAttribute("x", box[0] + 8); t.setAttribute("y", box[1] + 15);
    used.push(box);
    return box;
  }
  const short = (p) => p.code || p.name;

  function draw(moving) {
    if (!world || !root || !cam || !shown()) return;
    L.geo.setAttribute("transform", `matrix(${1 / cam.s} 0 0 ${1 / cam.s} ${size.w / 2 - cam.x / cam.s} ${size.h / 2 - cam.y / cam.s})`);
    L.route.textContent = ""; L.marks.textContent = ""; L.labels.textContent = "";
    hideTip();                                   // its marker is being redrawn
    const sel = data.items.find((i) => i.ref === data.selected) || null;

    // Destination country, tinted for the shipment on show.
    const destCC = sel && sel.where && sel.where.destination && sel.where.destination.cc;
    if (L.land._cc !== destCC) {
      L.land.querySelectorAll("path.dest").forEach((p) => p.classList.remove("dest"));
      if (destCC) L.land.querySelectorAll(`path[data-cc="${destCC}"]`).forEach((p) => p.classList.add("dest"));
      L.land._cc = destCC;
    }

    // Every shipment with a reported place: one marker each, gathered where
    // they would overlap; the shipment on show is drawn on top, with its mode.
    const pts = [];
    data.items.forEach((it) => { if (it.ref === data.selected) return; const n = nowOf(it); if (n) pts.push({it, at: scr(proj(n.pt))}); });
    const groups = [];
    pts.forEach((p) => {
      const g = groups.find((g) => Math.hypot(g.at[0] - p.at[0], g.at[1] - p.at[1]) < 16);
      if (g) { g.items.push(p.it); } else groups.push({at: p.at, items: [p.it]});
    });
    // The shipment on show: where it has been (solid), what is left to its
    // destination (dotted), or only the route its carrier names (dotted).
    let selNow = null;
    if (sel) {
      const r = routeOf(sel), n = nowOf(sel);
      selNow = n;
      const P = r.done.map((d) => scr(proj(d.p)));
      let dpath = "";
      for (let i = 1; i < P.length; i++) dpath += arc(P[i - 1], P[i]);
      if (dpath) {
        el("path", {class: "tm-glow", d: dpath, stroke: sel.color}, L.route);
        el("path", {class: "tm-done", d: dpath, stroke: sel.color}, L.route);
      }
      const D = r.dest ? scr(proj(r.dest)) : null;
      const from = n ? scr(proj(n.pt)) : P.length ? P[P.length - 1] : null;
      if (D && sel.k !== "arrived") {
        if (from && Math.hypot(from[0] - D[0], from[1] - D[1]) > 4) el("path", {class: "tm-next", d: arc(from, D)}, L.route);
        else if (!from && r.origin) el("path", {class: "tm-plan", d: arc(scr(proj(r.origin)), D), stroke: sel.color}, L.route);
      }
      // Stops: small rings; the origin and destination: labelled.
      r.done.forEach((d, i) => {
        const p = P[i];
        el("circle", {class: "stop", cx: p[0], cy: p[1], r: i === 0 ? 5 : 3.6, stroke: sel.color}, L.route);
      });
      const O = r.done.length ? r.done[0].p : r.origin;
      if (D) el("circle", {class: "end", cx: D[0], cy: D[1], r: 5.5}, L.route);
      if (!r.done.length && O) { const o = scr(proj(O)); el("circle", {class: "end", cx: o[0], cy: o[1], r: 5}, L.route); }
      // Labels, most important first: where it is now, where it goes,
      // where it came from. One too close to another place is left out
      // (the side card and the tooltip still name it).
      const Q = n ? scr(proj(n.pt)) : null;
      const used = (Q ? [[Q[0] - 17, Q[1] - 17, 34, 34]] : [])
        .concat(groups.map((g) => [g.at[0] - 10, g.at[1] - 10, 20, 20]));
      const near = (a, b) => a && b && Math.hypot(a[0] - b[0], a[1] - b[1]) < 22;
      const seen = (p) => p && p[0] >= 0 && p[1] >= 0 && p[0] <= size.w && p[1] <= size.h;
      if (seen(Q) && !moving) label(Q[0], Q[1], short(n.pt), n.ev ? day(n.ev.date, n.ev.time) : "Arrived", used, 17);
      if (seen(D) && !near(D, Q)) {
        label(D[0], D[1], short(r.dest), sel.k === "arrived" ? "Arrived" : sel.date ? "ETA " + day(sel.date) : "Destination", used, 7);
      }
      const Op = O ? scr(proj(O)) : null;
      if (seen(Op) && !near(Op, Q) && !near(Op, D)) label(Op[0], Op[1], short(O), "Origin", used, 7);
    }

    groups.forEach((g) => {
      if (g.at[0] < -20 || g.at[1] < -20 || g.at[0] > size.w + 20 || g.at[1] > size.h + 20) return;
      const one = g.items.length === 1, it = g.items[0];
      const a = el("g", {class: "mk", tabindex: 0, role: "button", transform: `translate(${g.at[0].toFixed(1)} ${g.at[1].toFixed(1)})`,
                         "aria-label": one ? `${it.ref}, ${it.label}` : `${g.items.length} shipments here`}, L.marks);
      el("circle", {class: "hit", r: 14}, a);
      const b = el("g", {class: "b"}, a);
      el("circle", {class: "rim", r: one ? 6.5 : 10, fill: one ? it.ring : "var(--tm-ink)"}, b);
      if (!one) { const t = el("text", {class: "n"}, b); t.textContent = g.items.length; }
      const pick = (e) => {
        hideTip();
        if (one) { if (data.onSelect) data.onSelect(it.ref); return; }
        const same = g.items.every((x) => { const n = nowOf(x).pt, m = nowOf(it).pt; return n.lat === m.lat && n.lon === m.lon; });
        // Zoom in to separate them; where zooming cannot (the same place, or
        // already as close as the map goes), list them instead.
        const b = boundsOf(g.items.map((x) => proj(nowOf(x).pt)));
        if (same || fitCam(b).s >= cam.s * 0.9) openPop(g.items, g.at[0], g.at[1]);
        else flyTo(b, true);
        if (e) e.stopPropagation();
      };
      a.addEventListener("click", pick);
      a.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(e); } });
      a.addEventListener("pointerenter", (e) => showTip(e, one ? tipFor(it)
        : `<b>${g.items.length} shipments</b><small>${g.items.slice(0, 6).map((x) => esc(x.ref)).join(", ")}${g.items.length > 6 ? "…" : ""}</small>`));
      a.addEventListener("pointermove", (e) => { if (tip.classList.contains("show")) { const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8); tip.style.left = x + "px"; tip.style.top = (e.clientY + 16) + "px"; } });
      a.addEventListener("pointerleave", hideTip);
    });
    if (sel && selNow) {
      const q = scr(proj(selNow.pt));
      const a = el("g", {class: "mk sel", tabindex: 0, role: "img", transform: `translate(${q[0].toFixed(1)} ${q[1].toFixed(1)})`,
                         "aria-label": `${sel.ref}: last reported at ${selNow.pt.name}`}, L.marks);
      if (sel.live) el("circle", {class: "pulse", r: 15, stroke: sel.ring}, a);
      const b = el("g", {class: "b"}, a);
      el("circle", {class: "face", r: 15, stroke: sel.ring, "stroke-width": 2.4}, b);
      if (sel.icon) el("path", {d: sel.icon, fill: sel.color, transform: "translate(-9 -9) scale(.75)"}, b);
      a.addEventListener("pointerenter", (e) => showTip(e, tipFor(sel)));
      a.addEventListener("pointerleave", hideTip);
    }
  }

  function paintChrome() {
    if (!root) return;
    const sel = data.items.find((i) => i.ref === data.selected) || null;
    const select = root.querySelector(".tm-pick select");
    const opts = data.items.map((it) => `<option value="${esc(it.ref)}"${it.ref === data.selected ? " selected" : ""}>${esc(it.ref)} · ${esc(it.carrier || "")}${nowOf(it) ? "" : " · no place reported"}</option>`).join("");
    if (select._h !== opts) { select._h = opts; select.innerHTML = opts; }
    select.parentNode.hidden = !data.items.length;
    const nPlaced = data.items.filter(nowOf).length;
    // Only the states on the map are in its legend.
    const shown = new Set(data.items.filter(nowOf).map((i) => i.k));
    const legend = [["onway", "var(--teal-dot)", "On the way"], ["arrived", "var(--grn-dot)", "Arrived"],
                    ["processing", "var(--blu)", "Tracking"], ["human", "var(--y-dot)", "Waiting"],
                    ["failed", "var(--red)", "Exception"]].filter((l) => shown.has(l[0]));
    const foot = root.querySelector(".tm-foot");
    const html = `<span title="Each marker is the last place the carrier reported, not a live GPS position"><b>${nPlaced} of ${data.items.length}</b> placed</span>` +
      legend.map((l) => `<span><i style="background:${l[1]}"></i>${l[2]}</span>`).join("");
    if (foot._h !== html) { foot._h = html; foot.innerHTML = html; }
    const msg = root.querySelector(".tm-msg");
    let text = "";
    if (!data.items.length) text = "<b>No shipment yet</b>The first one picked up from the Hub appears here.";
    else if (sel && !nowOf(sel)) {
      const r = routeOf(sel);
      text = r.dest || r.origin
        ? `<b>${esc(sel.ref)}: route only</b>${esc(sel.carrier || "The carrier")} names ${r.origin ? esc(short(r.origin)) + " → " : ""}${r.dest ? esc(short(r.dest)) : ""}, but no place the shipment has reached yet.`
        : `<b>${esc(sel.ref)}: no place reported</b>${esc(sel.carrier || "The carrier")} gives status and dates for this shipment, not a place, so it is not on the map.`;
    }
    if (msg._h !== text) { msg._h = text; msg.innerHTML = text; }
    msg.hidden = !text;
  }

  function paint(d) {
    data = {items: d.items || [], selected: d.selected || null, onSelect: d.onSelect};
    host = d.el || host;
    if (!host) return;
    mount(host);
    if (!world) {
      if (!loading) loading = fetch("/static/maps/world.json", {credentials: "same-origin"})
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error("map " + r.status))))
        .then((j) => { world = j; buildLand(); paint(data); })
        .catch(() => { loading = null; const m = root.querySelector(".tm-msg"); m._h = ""; m.hidden = false; m.innerHTML = "<b>The map could not load</b>Reload the page to try again."; });
      paintChrome();
      return;
    }
    paintChrome();
    // Follow the shipment on show: fly to its route when it changes, or
    // when what is known about where it is changes.
    const key = keyOf();
    measure();
    if (!shown()) { cam = null; return; }        // hidden: fitted when it shows (ResizeObserver)
    if (!cam) { cam = fitCam(boundsSel() || boundsAll()); fitKey = key; draw(); }
    else if (key !== fitKey) { fitKey = key; flyTo(boundsSel() || boundsAll(), false); }
    else draw();
  }

  window.ctTrackMap = {paint, nowOf: (it) => nowOf(it)};
  document.dispatchEvent(new Event("ct:trackmap"));     // the console paints it now
})();
