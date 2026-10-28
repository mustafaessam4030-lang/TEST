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
   marker to follow that shipment, a legend chip to show one state, a stop
   in the journey strip to fly to it; full screen with the expand button.
   Lines between two reported places follow the great circle between them:
   they join the places, they do not say where the shipment was between. */
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
.tm-chips{position:absolute;left:10px;top:48px;right:56px;z-index:2;display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;padding:1px}
.tm-chips::-webkit-scrollbar{display:none}
.tm-chips button{flex:none;display:inline-flex;align-items:center;gap:6px;height:26px;padding:0 10px;border-radius:13px;border:1px solid var(--line);
  background:color-mix(in srgb,var(--tm-pill) 92%,transparent);color:var(--mid);font:500 11.5px var(--s);cursor:pointer;backdrop-filter:blur(4px)}
.tm-chips button b{font-weight:650;color:var(--ink);font-variant-numeric:tabular-nums}
.tm-chips button i{width:7px;height:7px;border-radius:50%}
.tm-chips button[aria-pressed="true"]{background:var(--tm-ink);border-color:var(--tm-ink);color:var(--tm-pill)}
.tm-chips button[aria-pressed="true"] b{color:var(--tm-pill)}
.tm-chips button:focus-visible{outline:2px solid var(--vio);outline-offset:1px}
.tm-trip{position:absolute;left:8px;right:8px;bottom:8px;z-index:2;display:flex;align-items:center;gap:0;overflow-x:auto;scrollbar-width:thin;
  background:color-mix(in srgb,var(--tm-pill) 90%,transparent);border:1px solid var(--line-2);border-radius:12px;padding:5px 6px;
  backdrop-filter:blur(6px);box-shadow:0 4px 16px rgba(0,0,0,.06)}
.tm-trip[hidden]{display:none}
.tm-trip button{flex:none;display:grid;grid-template-columns:auto auto;align-items:center;gap:0 6px;border:0;background:transparent;color:var(--ink);
  border-radius:8px;padding:3px 8px;cursor:pointer;text-align:left;font:inherit}
.tm-trip button:hover,.tm-trip button:focus-visible,.tm-trip button.hl{background:var(--surface-3);outline:none}
.tm-trip button i{grid-row:1/3;width:9px;height:9px;border-radius:50%;border:2px solid var(--tm-c);background:var(--tm-pill)}
.tm-trip button.done i{background:var(--tm-c)}
.tm-trip button.now i{width:11px;height:11px;background:var(--tm-c);box-shadow:0 0 0 3px color-mix(in srgb,var(--tm-c) 25%,transparent)}
.tm-trip button.next i{border-style:dashed;border-color:var(--tm-next)}
.tm-trip b{font-size:11.5px;font-weight:650;white-space:nowrap}
.tm-trip small{font-size:10.5px;color:var(--faint);white-space:nowrap}
.tm-trip button.now small{color:var(--mid)}
.tm-trip .sep{flex:none;width:14px;height:2px;border-radius:1px;background:var(--tm-c)}
.tm-trip .sep.next{background:repeating-linear-gradient(90deg,var(--tm-next) 0 2px,transparent 2px 5px)}
.tm .stopc{cursor:pointer}
.tm .stopc .hit{fill:transparent}
.tm .stopc.hl .stop{stroke-width:3.4}
.tm .vig{pointer-events:none}
.tm.full{position:fixed;inset:20px;height:auto;z-index:120;min-height:0;box-shadow:0 30px 80px rgba(0,0,0,.35)}
.tm-back{position:fixed;inset:0;z-index:119;background:rgba(10,14,22,.45);backdrop-filter:blur(2px)}
@keyframes tmFlow{to{stroke-dashoffset:-18}}
.tm .tm-next{animation:tmFlow 1.4s linear infinite}
@media (max-width:600px){.tm-chips{top:46px}.tm-trip{bottom:6px}}
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
.tm-tip{position:fixed;z-index:130;pointer-events:none;max-width:290px;background:#1D1D1F;color:#fff;font-size:12.5px;line-height:1.42;
  border-radius:8px;padding:7px 10px;box-shadow:0 8px 24px rgba(0,0,0,.25);opacity:0;transition:opacity .12s}
:root[data-theme="dark"] .tm-tip{background:#262D3B}
.tm-tip.show{opacity:1}
.tm-tip small{display:block;color:#B8BCC6;margin-top:3px}
:root{--tm-vig:rgba(30,50,90,.10);--tm-sea:#EEF2F7;--tm-dot:rgba(40,60,100,.12);--tm-grid:rgba(30,60,110,.07);--tm-land:#FFFFFF;--tm-border:rgba(30,45,70,.16);
  --tm-pill:#FFFFFF;--tm-pill-edge:rgba(0,0,0,.08);--tm-ink:#0B1F33;--tm-next:#94A3B8;--tm-accent:var(--teal-dot)}
:root[data-theme="dark"]{--tm-vig:rgba(0,0,0,.45);--tm-sea:#0C1018;--tm-dot:rgba(255,255,255,.07);--tm-grid:rgba(255,255,255,.04);--tm-land:#1A2130;
  --tm-border:rgba(255,255,255,.10);--tm-pill:#1C2331;--tm-pill-edge:rgba(255,255,255,.10);--tm-ink:#E6EAF2;--tm-next:#5B6678}
@media (prefers-reduced-motion:reduce){.tm .pulse{animation:none;display:none}.tm .mk .b{transition:none}.tm .tm-next{animation:none}}`;
  document.head.appendChild(css);

  const NS = "http://www.w3.org/2000/svg";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const day = (d, t) => {
    const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(String(d || ""));
    return m ? `${+m[1]} ${MON[+m[2] - 1]}${t ? " " + t : ""}` : String(d || "");
  };
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  // How long ago the carrier's event was, from its own date (dd/mm/yyyy).
  const ago = (d) => {
    const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(String(d || ""));
    if (!m) return "";
    const then = new Date(+m[3], +m[2] - 1, +m[1]), now = new Date();
    const n = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()) - then) / 864e5);
    return n < 0 ? "" : n === 0 ? "today" : n === 1 ? "yesterday" : n + " days ago";
  };
  /* The shortest way over the globe between two reported places (a great
     circle), as points to project: the shape a long flight really draws on
     a flat map. It joins two places; it is not a position between them. */
  function gcPts(a, b) {
    const R = Math.PI / 180, f1 = a.lat * R, l1 = a.lon * R, f2 = b.lat * R, l2 = b.lon * R;
    const d = 2 * Math.asin(Math.sqrt(Math.sin((f2 - f1) / 2) ** 2 + Math.cos(f1) * Math.cos(f2) * Math.sin((l2 - l1) / 2) ** 2));
    if (!(d > 1e-4)) return [a, b];
    const n = Math.max(2, Math.min(64, Math.ceil(d / R / 1.5)));
    const out = [];
    for (let i = 0; i <= n; i++) {
      const t = i / n, A = Math.sin((1 - t) * d) / Math.sin(d), B = Math.sin(t * d) / Math.sin(d);
      const x = A * Math.cos(f1) * Math.cos(l1) + B * Math.cos(f2) * Math.cos(l2);
      const y = A * Math.cos(f1) * Math.sin(l1) + B * Math.cos(f2) * Math.sin(l2);
      const z = A * Math.sin(f1) + B * Math.sin(f2);
      out.push({lat: Math.atan2(z, Math.hypot(x, y)) / R, lon: Math.atan2(y, x) / R});
    }
    return out;
  }
  function gcPath(a, b) {
    const W = world.viewBox[2];
    let d = "", prev = null;
    for (const p of gcPts(a, b)) {
      const m = proj(p), q = scr(m);
      // Across the date line the map is cut, and so is the line.
      d += (prev && Math.abs(m[0] - prev[0]) < W / 2 ? "L" : "M") + q[0].toFixed(1) + " " + q[1].toFixed(1);
      prev = m;
    }
    return d;
  }

  let world = null, loading = null, root = null, svg = null, L = {}, size = {w: 0, h: 0};
  let cam = null, anim = 0, data = {items: [], selected: null}, fitKey = "", host = null;
  // filt: the state the markers are narrowed to (a legend chip); hl: the
  // journey stop under the pointer; revealT0: when the route on show began
  // drawing itself in.
  let filt = null, hl = -1, revealT0 = 0, revealRaf = 0, back = null, slotMark = null, flying = false;
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
    // A place it came back to (A → B → A) is listed once, where it was
    // first reached; the return is drawn as a last leg back to it.
    const n = nowOf(it);
    if (n && n.ev && pts.length) add(Object.assign({}, n.pt, {date: n.ev.date, time: n.ev.time, text: n.ev.text}), "stop");
    return {done: pts, origin: placed(w.origin) ? w.origin : null,
            dest: placed(w.destination) ? w.destination : null,
            destCC: w.destination && w.destination.cc};
  }

  // The journey as a list: where it has been (done), the newest reported
  // place (now), and the destination still ahead (next). The destination is
  // left out when a stop already is it (Kotoka for Accra).
  function stopsOf(it) {
    if (!it) return [];
    const r = routeOf(it), n = nowOf(it);
    // Each stop shows the event that first reached it; the newest place
    // shows the newest event there (DHL "Delivered", after "Arrived").
    const out = r.done.map((d) => ({p: d.p, kind: d.kind, state: "done", ev: d.p.text ? d.p : null}));
    if (n) {
      for (let i = out.length - 1; i >= 0; i--) {
        if (out[i].p.lat === n.pt.lat && out[i].p.lon === n.pt.lon) { out[i].state = "now"; out[i].ev = n.ev || out[i].ev; break; }
      }
    }
    const near = (a, b) => Math.abs(a.lat - b.lat) < 0.3 && Math.abs(a.lon - b.lon) < 0.3;
    if (!out.length && r.origin) out.push({p: r.origin, kind: "origin", state: "plan"});
    if (r.dest) {
      const arrived = it.k === "arrived";
      const there = arrived ? out.some((o) => near(o.p, r.dest)) : out.length && near(out[out.length - 1].p, r.dest);
      if (!there) out.push({p: r.dest, kind: "dest", state: arrived ? "done" : "next"});
    }
    return out;
  }

  /* ── build once ── */
  function mount(box) {
    if (root && (root.parentNode === box || slotMark)) return;     // (full screen: on <body> for now)
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
        <button type="button" data-z="full" aria-label="Full screen" title="Full screen (Esc to close)" aria-pressed="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7"/></svg></button>
      </div>
      <div class="tm-chips" role="toolbar" aria-label="Show shipments by state"></div>
      <div class="tm-msg" hidden></div><div class="tm-hint" hidden>Ctrl + scroll to zoom</div>
      <div class="tm-trip" role="list" aria-label="Where the carrier reported the shipment" hidden></div>`;
    svg = el("svg", {role: "presentation"});
    root.insertBefore(svg, root.firstChild);
    const defs = el("defs", {}, svg);
    defs.innerHTML = `<pattern id="tmDots" width="9" height="9" patternUnits="userSpaceOnUse"><circle class="dotp" cx="1.5" cy="1.5" r=".9"/></pattern>
      <filter id="tmShadow" x="-40%" y="-40%" width="180%" height="180%"><feDropShadow dx="0" dy="1.5" stdDeviation="2" flood-color="#0B1F33" flood-opacity=".18"/></filter>
      <radialGradient id="tmVig" cx="50%" cy="45%" r="75%"><stop offset="55%" stop-color="var(--tm-vig)" stop-opacity="0"/><stop offset="100%" style="stop-color:var(--tm-vig)"/></radialGradient>`;
    el("rect", {class: "sea", width: "100%", height: "100%"}, svg);
    L.geo = el("g", {}, svg);
    L.grid = el("path", {class: "grid", "vector-effect": "non-scaling-stroke"}, L.geo);
    L.land = el("g", {class: "land"}, L.geo);
    el("rect", {class: "vig", width: "100%", height: "100%", fill: "url(#tmVig)"}, svg);
    L.route = el("g", {}, svg);
    L.marks = el("g", {}, svg);
    L.labels = el("g", {}, svg);
    root.querySelector(".tm-ui").addEventListener("click", (e) => {
      const b = e.target.closest("[data-z]"); if (!b || !cam) return;
      const z = b.dataset.z;
      if (z === "in") zoomAt(size.w / 2, size.h / 2, 0.5, true);
      else if (z === "out") zoomAt(size.w / 2, size.h / 2, 2, true);
      else if (z === "all") flyTo(boundsAll(), true);
      else if (z === "full") setFull(!root.classList.contains("full"));
      else flyTo(boundsSel() || boundsAll(), true);
    });
    // Legend chips: the states on the map, each one narrowing the markers.
    root.querySelector(".tm-chips").addEventListener("click", (e) => {
      const b = e.target.closest("[data-f]"); if (!b) return;
      filt = b.dataset.f === "all" || filt === b.dataset.f ? null : b.dataset.f;
      paintChrome(); if (cam) flyTo(boundsAll(), true);
    });
    // The journey strip: a stop flies the map to it; the pointer on one
    // marks it on the map.
    const trip = root.querySelector(".tm-trip");
    trip.addEventListener("click", (e) => {
      const b = e.target.closest("[data-i]"); if (!b || !cam) return;
      const st = stopsOf(selItem())[+b.dataset.i]; if (!st) return;
      const m = proj(st.p), r = fullS() / 9 * Math.min(size.w, size.h) / 2;
      flyTo([m[0] - r, m[1] - r, m[0] + r, m[1] + r], true);
    });
    trip.addEventListener("pointerover", (e) => { const b = e.target.closest("[data-i]"); setHl(b ? +b.dataset.i : -1); });
    trip.addEventListener("pointerleave", () => setHl(-1));
    trip.addEventListener("focusin", (e) => { const b = e.target.closest("[data-i]"); setHl(b ? +b.dataset.i : -1); });
    trip.addEventListener("focusout", () => setHl(-1));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && root.classList.contains("full")) { e.stopPropagation(); setFull(false); }
    }, true);
    // Focus that leaves the full-screen map comes back to it; a page change
    // (Back, a link, search) closes it.
    document.addEventListener("focusin", (e) => {
      if (root.classList.contains("full") && !root.contains(e.target)) root.focus();
    });
    addEventListener("hashchange", () => { if (root.classList.contains("full")) setFull(false); });
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
  const selItem = () => data.items.find((i) => i.ref === data.selected) || null;
  function setHl(i) {
    if (hl === i) return;
    hl = i;
    root.querySelectorAll(".tm-trip button").forEach((b) => b.classList.toggle("hl", +b.dataset.i === i));
    draw();
  }
  // Full screen: the same map, larger, over the page; Esc or the button
  // brings it back. The page under it does not scroll meanwhile.
  function setFull(on) {
    // Moved to <body> while full: a page's own transform would otherwise
    // pin a fixed element to the page instead of the window.
    if (on && !slotMark) { slotMark = document.createComment("map"); root.parentNode.insertBefore(slotMark, root); document.body.appendChild(root); }
    if (!on && slotMark) { slotMark.parentNode.insertBefore(root, slotMark); slotMark.remove(); slotMark = null; }
    root.classList.toggle("full", on);
    // While full it is a dialog: search and other shortcuts stand back, and
    // Tab stays inside it.
    if (on) { root.setAttribute("role", "dialog"); root.setAttribute("aria-modal", "true"); }
    else { root.removeAttribute("role"); root.removeAttribute("aria-modal"); }
    root.querySelector('[data-z="full"]').setAttribute("aria-pressed", String(on));
    if (on && !back) { back = document.createElement("div"); back.className = "tm-back"; back.addEventListener("click", () => setFull(false)); document.body.appendChild(back); }
    if (!on && back) { back.remove(); back = null; }
    document.documentElement.style.overflow = on ? "hidden" : "";
    if (on) root.focus(); else root.querySelector('[data-z="full"]').focus();
    measure(); if (cam) { cam = fitCam(boundsSel() || boundsAll()); draw(); }
  }
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
  const PAD = {top: 92, bottom: 88, left: 56, right: 66};
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
      flying = k < 1;
      draw(k < 1);
      if (k < 1) anim = requestAnimationFrame(step);
    };
    flying = true;
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
      flying = k < 1;
      draw(k < 1);
      if (k < 1) anim = requestAnimationFrame(step);
    };
    flying = true;
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
    data.items.forEach((it) => { const n = nowOf(it); if (n && (!filt || it.k === filt)) pts.push(proj(n.pt)); });
    const sel = boundsSel();
    if (sel) pts.push([sel[0], sel[1]], [sel[2], sel[3]]);
    return boundsOf(pts) || boundsOf([proj({lat: 38, lon: -18}), proj({lat: -12, lon: 56})]);
  }

  /* ── pointer: drag, Ctrl + wheel, double click ── */
  function wire() {
    let drag = null, hintT = 0;
    root.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest(".tm-ui,.tm-pick,.tm-pop,.mk,.tm-chips,.tm-trip,.stopc")) return;
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
      if (!(e.ctrlKey || e.metaKey || root.classList.contains("full"))) {   // the page scrolls as usual
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
    data.items.forEach((it) => {
      if (it.ref === data.selected || (filt && it.k !== filt)) return;
      const n = nowOf(it); if (n) pts.push({it, at: scr(proj(n.pt))});
    });
    const groups = [];
    pts.forEach((p) => {
      const g = groups.find((g) => Math.hypot(g.at[0] - p.at[0], g.at[1] - p.at[1]) < 16);
      if (g) { g.items.push(p.it); } else groups.push({at: p.at, items: [p.it]});
    });
    // The shipment on show: where it has been (solid), what is left to its
    // destination (dotted), or only the route its carrier names (dotted).
    let selNow = null;
    const rv = reduced() ? 1 : Math.min(1, (performance.now() - revealT0) / 1100);
    // While the camera flies it redraws every frame anyway; the reveal only
    // schedules its own frames once it is still.
    if (rv < 1 && !revealRaf && !flying) revealRaf = requestAnimationFrame(() => { revealRaf = 0; draw(); });
    if (sel) {
      const r = routeOf(sel), n = nowOf(sel), stops = stopsOf(sel);
      selNow = n;
      const P = r.done.map((d) => scr(proj(d.p)));
      let dpath = "";
      for (let i = 1; i < r.done.length; i++) dpath += gcPath(r.done[i - 1].p, r.done[i].p);
      if (dpath) {
        // The way it has come draws itself in when a shipment is chosen.
        const glow = el("path", {class: "tm-glow", d: dpath, stroke: sel.color}, L.route);
        const done = el("path", {class: "tm-done", d: dpath, stroke: sel.color}, L.route);
        if (rv < 1) {
          const len = done.getTotalLength();
          [glow, done].forEach((x) => { x.style.strokeDasharray = len + " " + len; x.style.strokeDashoffset = String(len * (1 - rv)); });
        }
      }
      const D = r.dest ? scr(proj(r.dest)) : null;
      const fromP = n ? n.pt : r.done.length ? r.done[r.done.length - 1].p : null;
      let ahead = null;
      if (D && sel.k !== "arrived") {
        const f = fromP && scr(proj(fromP));
        if (f && Math.hypot(f[0] - D[0], f[1] - D[1]) > 4) ahead = el("path", {class: "tm-next", d: gcPath(fromP, r.dest)}, L.route);
        else if (!f && r.origin) ahead = el("path", {class: "tm-plan", d: gcPath(r.origin, r.dest), stroke: sel.color}, L.route);
        if (ahead) ahead.style.opacity = String(Math.max(0, rv * 1.6 - .6));
      }
      // Stops: rings you can point at (the carrier's event there); the
      // origin and destination: labelled.
      r.done.forEach((d, i) => {
        const p = P[i];
        const g = el("g", {class: "stopc" + (hl === i ? " hl" : "")}, L.route);
        el("circle", {class: "hit", cx: p[0], cy: p[1], r: 9}, g);
        el("circle", {class: "stop", cx: p[0], cy: p[1], r: hl === i ? 6.5 : i === 0 ? 5 : 3.6, stroke: sel.color}, g);
        g.addEventListener("pointerenter", (e) => showTip(e, `<b>${esc(d.p.code ? d.p.name + " (" + d.p.code + ")" : d.p.name)}</b>, ${esc(d.p.country || d.p.cc)}` +
          (d.p.text ? `<small>${esc(d.p.text)} — ${esc(day(d.p.date, d.p.time))}${d.p.said && d.p.said !== d.p.name ? `<br>Carrier wrote “${esc(d.p.said)}”` : ""}</small>`
                    : `<small>Origin, as the carrier names it</small>`)));
        g.addEventListener("pointerleave", hideTip);
      });
      const O = r.done.length ? r.done[0].p : r.origin;
      const dIdx = stops.findIndex((x) => x.kind === "dest");
      if (D) el("circle", {class: "end", cx: D[0], cy: D[1], r: hl >= 0 && hl === dIdx ? 8 : 5.5}, L.route);
      if (!r.done.length && O) { const o = scr(proj(O)); el("circle", {class: "end", cx: o[0], cy: o[1], r: hl === 0 ? 8 : 5}, L.route); }
      // Labels, most important first: where it is now, where it goes,
      // where it came from. One too close to another place is left out
      // (the journey strip and the tooltip still name it).
      const Q = n ? scr(proj(n.pt)) : null;
      const used = (Q ? [[Q[0] - 17, Q[1] - 17, 34, 34]] : [])
        .concat(groups.map((g) => [g.at[0] - 10, g.at[1] - 10, 20, 20]));
      const near = (a, b) => a && b && Math.hypot(a[0] - b[0], a[1] - b[1]) < 22;
      const seen = (p) => p && p[0] >= 0 && p[1] >= 0 && p[0] <= size.w && p[1] <= size.h;
      if (seen(Q) && !moving) label(Q[0], Q[1], short(n.pt), n.ev ? day(n.ev.date, n.ev.time) : "Arrived", used, 17);
      if (seen(D) && !near(D, Q) && rv >= 1) {
        label(D[0], D[1], short(r.dest), sel.k === "arrived" ? "Arrived" : sel.date ? "ETA " + day(sel.date) : "Destination", used, 7);
      }
      const Op = O ? scr(proj(O)) : null;
      if (seen(Op) && !near(Op, Q) && !near(Op, D) && rv >= 1) label(Op[0], Op[1], short(O), "Origin", used, 7);
      // An aircraft points the way it goes: toward the destination while
      // on the way, along the last leg once there. A direction between two
      // reported places — not a heading anyone reported.
      if (n && sel.mode === "air") {
        let a = null, b = null;
        if (r.dest && sel.k !== "arrived") { const g = gcPts(n.pt, r.dest); if (g.length > 1) { a = n.pt; b = g[1]; } }
        if (!a) {
          let k = -1;
          r.done.forEach((d, i) => { if (d.p.lat === n.pt.lat && d.p.lon === n.pt.lon) k = i; });
          if (k > 0) { const g = gcPts(r.done[k - 1].p, n.pt); a = g[g.length - 2]; b = n.pt; }
        }
        if (a && b) {
          const pa = scr(proj(a)), pb = scr(proj(b));
          if (Math.hypot(pb[0] - pa[0], pb[1] - pa[1]) > .5) selNow = Object.assign({}, n, {turn: Math.atan2(pb[1] - pa[1], pb[0] - pa[0]) * 180 / Math.PI + 90});
        }
      }
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
      if (sel.icon) el("path", {d: sel.icon, fill: sel.color,
        transform: (selNow.turn != null ? `rotate(${selNow.turn.toFixed(1)}) ` : "") + "translate(-9 -9) scale(.75)"}, b);
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
    // Legend chips: only the states on the map, with their counts; each
    // narrows the markers to that state.
    const placedItems = data.items.filter(nowOf);
    const count = {};
    placedItems.forEach((i) => { count[i.k] = (count[i.k] || 0) + 1; });
    if (filt && !count[filt]) filt = null;
    const legend = [["onway", "var(--teal-dot)", "On the way"], ["arrived", "var(--grn-dot)", "Arrived"],
                    ["processing", "var(--blu)", "Tracking"], ["human", "var(--y-dot)", "Waiting"],
                    ["failed", "var(--red)", "Exception"]].filter((l) => count[l[0]]);
    const chips = root.querySelector(".tm-chips");
    const html = `<button type="button" data-f="all" aria-pressed="${!filt}" title="Each marker is the last place the carrier reported, not a live GPS position">All <b>${placedItems.length}</b> of ${data.items.length} placed</button>` +
      legend.map((l) => `<button type="button" data-f="${l[0]}" aria-pressed="${filt === l[0]}"><i style="background:${l[1]}"></i>${l[2]} <b>${count[l[0]]}</b></button>`).join("");
    if (chips._h !== html) { chips._h = html; chips.innerHTML = html; }
    chips.hidden = !data.items.length;
    // The journey strip of the shipment on show.
    const trip = root.querySelector(".tm-trip");
    const stops = stopsOf(sel);
    const tripHtml = stops.map((st, i) => {
      const p = st.p, ev = st.ev;
      const when = st.kind === "dest" ? (st.state === "done" ? "Arrived" : sel.date ? "ETA " + day(sel.date) : "Destination")
        : ev ? day(ev.date, ev.time) + (st.state === "now" && ago(ev.date) ? " · " + ago(ev.date) : "") : "Origin";
      const title = [p.name + (p.country ? ", " + p.country : ""), ev && ev.text].filter(Boolean).join(" — ");
      return (i ? `<span class="sep${st.state === "next" ? " next" : ""}" aria-hidden="true"></span>` : "") +
        `<button type="button" role="listitem" data-i="${i}" class="${st.state}" title="${esc(title)}">` +
        `<i></i><b>${esc(short(p))}</b><small>${esc(when)}</small></button>`;
    }).join("");
    trip.hidden = !stops.length;
    if (trip._h !== tripHtml) {
      trip._h = tripHtml; trip.innerHTML = tripHtml;
      if (sel) trip.style.setProperty("--tm-c", sel.color);
      // The newest place in view, with what follows it.
      const now = trip.querySelector(".now");
      if (now) trip.scrollLeft = Math.max(0, now.offsetLeft + now.offsetWidth - trip.clientWidth + 90);
    }
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
    // Follow the shipment on show: fly to its route when it changes, or
    // when what is known about where it is changes.
    const key = keyOf();
    if (key !== fitKey) hl = -1;                 // a new shipment: no stop marked
    paintChrome();
    measure();
    if (!shown()) { cam = null; return; }        // hidden: fitted when it shows (ResizeObserver)
    if (!cam) { cam = fitCam(boundsSel() || boundsAll()); fitKey = key; revealT0 = performance.now(); draw(); }
    else if (key !== fitKey) { fitKey = key; revealT0 = performance.now(); flyTo(boundsSel() || boundsAll(), false); }
    else draw();
  }

  window.ctTrackMap = {paint, nowOf: (it) => nowOf(it), ago};
  document.dispatchEvent(new Event("ct:trackmap"));     // the console paints it now
})();
