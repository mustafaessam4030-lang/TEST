/* ATLAS Control Tower — the Overview's globe.

   A dotted globe that turns slowly, in the hub's own (Apple) colours: land as
   dots on a sphere, a soft rim, the business units as labelled pins and the
   carrier-reported routes as arcs. It draws exactly what the map draws — the
   same pins and routes from the same data (hub.html mapData) — and nothing
   else: no invented places, positions or flights.

   window.HubGlobe.mount(host, {worldUrl}) -> {setData, on, select, destroy, mode}
   Same events as the map: "select" (pin) and "action" (pin, event).
   Canvas 2D, no library. Pins are real buttons (Tab, Enter, screen readers).
   Reduced motion: no turning, no animated focus. Off screen or hidden: no drawing. */
(function () {
  "use strict";
  const DEG = Math.PI / 180;
  const SPIN = 0.045;                       // rad/s when idle (a slow turn)
  const REST_PITCH = 0.32;                  // a little from above, as in the reference
  const ease = (k, dt) => 1 - Math.exp(-dt * k);
  const reduce = () => !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  const CSS = `
.gb{position:absolute;inset:0;overflow:hidden;touch-action:none;user-select:none;-webkit-user-select:none;cursor:grab}
.gb.drag{cursor:grabbing}
.gb canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
.gb-pins{position:absolute;inset:0;pointer-events:none}
.gb-pin{position:absolute;left:0;top:0;pointer-events:auto;display:inline-flex;align-items:center;gap:5px;height:22px;padding:0 9px;border-radius:999px;
  border:0;font:600 11.5px/1 var(--hub-font,-apple-system,system-ui,sans-serif);letter-spacing:.01em;color:var(--hub-ink);cursor:pointer;
  background:var(--hub-glass);-webkit-backdrop-filter:saturate(180%) blur(14px);backdrop-filter:saturate(180%) blur(14px);
  box-shadow:0 0 0 .5px var(--hub-line),0 4px 14px rgba(0,0,0,.12);transform-origin:0 50%;white-space:nowrap;transition:opacity .2s}
.gb-pin b{font-variant-numeric:tabular-nums;font-weight:600}
.gb-pin.t-red b{color:var(--hub-red-ink)} .gb-pin.t-amber b{color:var(--hub-amber-ink)} .gb-pin.t-green b{color:var(--hub-green-ink)} .gb-pin.t-blue b{color:var(--hub-blue)}
.gb-pin.tight{opacity:0!important;pointer-events:none}
.gb-pin.tight:focus-visible{opacity:1!important}
.gb-pin:focus-visible{outline:3px solid var(--hub-focus);outline-offset:2px}
.gb-pin[aria-pressed="true"]{box-shadow:0 0 0 2px var(--hub-accent),0 6px 18px rgba(0,0,0,.18)}
.gb-card{position:absolute;left:14px;top:14px;width:min(280px,calc(100% - 28px));padding:14px 14px 12px;border-radius:18px;
  background:var(--hub-glass);-webkit-backdrop-filter:saturate(180%) blur(20px);backdrop-filter:saturate(180%) blur(20px);
  box-shadow:0 0 0 .5px var(--hub-line),0 10px 30px rgba(0,0,0,.12);color:var(--hub-ink);cursor:default}
.gb-card h3{margin:0;font-size:13.5px;letter-spacing:-.01em}
.gb-card p{margin:2px 0 10px;font-size:11.5px;color:var(--hub-ink-3)}
.gb-row{display:grid;grid-template-columns:34px 1fr 44px;align-items:center;gap:8px;width:100%;padding:5px 4px;border:0;background:none;border-radius:8px;
  font:inherit;font-size:12.5px;color:var(--hub-ink);cursor:pointer;text-align:left}
.gb-row:hover{background:var(--hub-line-2)}
.gb-row:focus-visible{outline:3px solid var(--hub-focus)}
.gb-row s{display:block;height:5px;border-radius:3px;background:var(--hub-line);text-decoration:none;overflow:hidden}
.gb-row s u{display:block;height:100%;border-radius:3px;background:var(--hub-red)}
.gb-row em{font-style:normal;text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.gb-x{position:absolute;right:10px;top:10px;width:24px;height:24px;border-radius:50%;border:0;background:var(--hub-line-2);color:var(--hub-ink-2);cursor:pointer;font-size:14px;line-height:1}
.gb-stats{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:2px 0 10px}
.gb-stats div{background:var(--hub-line-2);border-radius:10px;padding:7px 9px}
.gb-stats span{display:block;font-size:10px;letter-spacing:.05em;color:var(--hub-ink-3);font-weight:600}
.gb-stats b{font-size:16px;font-variant-numeric:tabular-nums}
.gb-stats b.red{color:var(--hub-red-ink)} .gb-stats b.green{color:var(--hub-green-ink)}
.gb-acts{display:flex;flex-wrap:wrap;gap:6px}
.gb-acts button{height:28px;padding:0 12px;border-radius:999px;border:0;background:var(--hub-line-2);color:var(--hub-ink);font:600 12px/1 var(--hub-font,system-ui);cursor:pointer}
.gb-acts button.primary{background:var(--hub-accent);color:var(--hub-on-accent)}
.gb-acts button:focus-visible{outline:3px solid var(--hub-focus);outline-offset:2px}
.gb-rows{margin:0 0 10px;padding:0;list-style:none;font-size:11.5px;color:var(--hub-ink-2)}
.gb-rows li{padding:3px 0;border-top:1px solid var(--hub-line-2)}
.gb-note{margin:8px 0 0;font-size:10.5px;color:var(--hub-ink-3)}
.gb-ctl{position:absolute;right:12px;bottom:12px;display:flex;gap:6px}
.gb-ctl button{width:32px;height:32px;border-radius:50%;border:0;display:grid;place-items:center;color:var(--hub-ink);cursor:pointer;
  background:var(--hub-glass);-webkit-backdrop-filter:saturate(180%) blur(14px);backdrop-filter:saturate(180%) blur(14px);box-shadow:0 0 0 .5px var(--hub-line),0 4px 12px rgba(0,0,0,.1)}
.gb-ctl button:focus-visible{outline:3px solid var(--hub-focus);outline-offset:2px}
.gb-empty{position:absolute;left:50%;bottom:16px;transform:translateX(-50%);font-size:12px;color:var(--hub-ink-3)}
@media (max-width:640px){.gb-card{top:auto;bottom:56px;width:calc(100% - 28px)}.gb-card.list{display:none}}
`;
  function injectCss() {
    if (document.getElementById("hubglobe-css2")) return;
    const s = document.createElement("style"); s.id = "hubglobe-css2"; s.textContent = CSS;
    document.head.appendChild(s);
  }

  /* ── land as dots: the world outline (same file as the map) drawn once, sampled on a sphere ── */
  let LAND = null;
  function landDots(world) {
    const W = world.projection.width, H = world.viewBox[3], latT = world.projection.lat_top, latB = world.projection.lat_bottom;
    const S = 1000 / W, cw = 1000, ch = Math.ceil(H * S);
    const c = document.createElement("canvas"); c.width = cw; c.height = ch;
    const g = c.getContext("2d", {willReadFrequently: true});
    g.scale(S, S);
    const ccs = [];
    world.countries.forEach((k, i) => {
      if (!k.d) return;
      ccs[i + 1] = k.cc;
      g.fillStyle = "rgb(" + ((i + 1) & 255) + "," + ((i + 1) >> 8) + ",0)";
      try { g.fill(new Path2D(k.d), "evenodd"); } catch (e) { /* a path the browser can't read is left out */ }
    });
    const px = g.getImageData(0, 0, cw, ch).data;
    const my = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * DEG / 2)), y0 = my(latT), ky = ch / (y0 - my(latB));
    const lat = [], lon = [], cc = [];
    const STEP = 1.25;                                     // degrees between rows; even spacing along each row
    for (let la = -56 + STEP / 2; la < latT; la += STEP) {
      const n = Math.max(1, Math.round(360 * Math.cos(la * DEG) / STEP));
      const y = Math.floor((y0 - my(la)) * ky);
      if (y < 0 || y >= ch) continue;
      for (let j = 0; j < n; j++) {
        const lo = -180 + (j + 0.5) * 360 / n, x = Math.floor((lo + 180) / 360 * cw);
        const o = (y * cw + x) * 4;
        if (px[o + 3] < 128) continue;
        lat.push(la); lon.push(lo);
        cc.push(px[o + 3] === 255 ? ccs[px[o] + (px[o + 1] << 8)] || "" : "");
      }
    }
    // unit vectors, once
    const n = lat.length, X = new Float32Array(n), Y = new Float32Array(n), Z = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const p = lat[i] * DEG, l = lon[i] * DEG;
      X[i] = Math.cos(p) * Math.sin(l); Y[i] = Math.sin(p); Z[i] = Math.cos(p) * Math.cos(l);
    }
    return {n, X, Y, Z, cc};
  }
  const unit = (lat, lon) => { const p = lat * DEG, l = lon * DEG; return [Math.cos(p) * Math.sin(l), Math.sin(p), Math.cos(p) * Math.cos(l)]; };

  function mount(host, opts) {
    opts = opts || {};
    injectCss();
    const L = {select: [], action: []};
    const emit = (n, ...a) => (L[n] || []).slice().forEach((f) => { try { f(...a); } catch (e) { console.error(e); } });
    const root = document.createElement("div"); root.className = "gb";
    root.setAttribute("role", "region"); root.setAttribute("aria-label", "Globe: business units and carrier-reported places");
    const cv = document.createElement("canvas"); cv.setAttribute("aria-hidden", "true");
    const pinsBox = document.createElement("div"); pinsBox.className = "gb-pins";
    const card = document.createElement("div"); card.className = "gb-card";
    card.hidden = true;                    // shown once there is something to say
    const ctl = document.createElement("div"); ctl.className = "gb-ctl";
    const empty = document.createElement("div"); empty.className = "gb-empty"; empty.hidden = true;
    root.append(cv, pinsBox, card, ctl, empty);
    host.textContent = ""; host.appendChild(root); host.dataset.mapMode = "globe";
    const g = cv.getContext("2d");

    // camera: the point of the globe at the centre of the view
    const cam = {lon: 25, lat: 18, spin: reduce() ? 0 : SPIN, zoom: 1, focus: null};
    let W = 0, H = 0, dpr = 1, cx = 0, cy = 0, R = 0, raf = 0, last = 0, visible = true, destroyed = false, paused = reduce();
    let pins = [], routes = [], focusCC = new Set(), selected = null, pal = null, data = null, flow = 0;

    /* colours from the hub's tokens, so the globe is light or dark with the page */
    function readTheme() {
      const cs = getComputedStyle(document.documentElement), v = (k, d) => (cs.getPropertyValue(k) || "").trim() || d;
      const bg = v("--hub-bg", "#f5f5f7"), dark = lum(bg) < 0.3;
      const navy = dark && document.documentElement.dataset.dark === "midnight";
      pal = dark ? {
        bg0: navy ? "#13203a" : "#1c1c1e", bg1: navy ? "#0b1220" : "#0c0c0d", sea0: "#1d2b44", sea1: "#0d1422", rim: "rgba(120,170,255,.22)", halo: "rgba(10,132,255,.22)",
        dot: [152, 168, 196], focus: [64, 156, 255], arc: v("--hub-blue", "#0a84ff"), line: "rgba(255,255,255,.06)"
      } : {
        bg0: "#ffffff", bg1: "#eef1f6", sea0: "#ffffff", sea1: "#e8edf4", rim: "rgba(0,0,0,.07)", halo: "rgba(0,113,227,.10)",
        dot: [120, 128, 140], focus: [0, 113, 227], arc: v("--hub-blue", "#0071e3"), line: "rgba(0,0,0,.05)"
      };
      const acc = /^#([0-9a-f]{6})$/i.exec(v(dark ? "--hub-accent" : "--hub-accent-ink", dark ? "#ffcd11" : "#7a5c00"));
      if (acc) { const n = parseInt(acc[1], 16); pal.focus = [n >> 16, (n >> 8) & 255, n & 255]; }
      pal.tone = {red: v("--hub-red", "#e5383b"), amber: v("--hub-amber", "#e08a00"), green: v("--hub-green", "#28a745"), blue: v("--hub-blue", "#0071e3")};
      pal.dark = dark;
      dirty();
    }
    function lum(c) {
      const m = /^#?([0-9a-f]{6})$/i.exec(c.trim()); if (!m) return 1;
      const n = parseInt(m[1], 16); return ((n >> 16) * 0.299 + ((n >> 8) & 255) * 0.587 + (n & 255) * 0.114) / 255;
    }

    function resize() {
      const r = root.getBoundingClientRect();
      W = Math.max(1, r.width); H = Math.max(1, r.height); dpr = Math.min(2, window.devicePixelRatio || 1);
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      const mobile = W < 640;
      cx = mobile ? W * 0.5 : W * 0.6; cy = H * 0.53;
      R = (mobile ? Math.min(W * 0.4, H * 0.42) : Math.min(H * 0.41, W * 0.31));
      dirty();
    }

    // orthographic projection about the camera's centre point
    function basis() {
      const l0 = cam.lon * DEG, p0 = cam.lat * DEG;
      return {sl: Math.sin(l0), cl: Math.cos(l0), sp: Math.sin(p0), cp: Math.cos(p0)};
    }
    function proj(x, y, z, b, k) {          // world unit vector (scaled by k) -> [sx, sy, depth]
      const x1 = x * b.cl - z * b.sl, z1 = x * b.sl + z * b.cl;          // turn by longitude
      const y2 = y * b.cp - z1 * b.sp, z2 = y * b.sp + z1 * b.cp;        // tilt by latitude
      const s = R * cam.zoom * (k || 1);
      return [cx + x1 * s, cy - y2 * s, z2 * (k || 1), x1 * (k || 1), y2 * (k || 1)];
    }

    /* ── pins and routes, from the map's own data ── */
    function setData(d) {
      data = d || {};
      for (const p of pins) p.btn.remove();
      pins = []; selected = null;
      focusCC = new Set(data.focus || []);
      for (const pd of data.pins || []) {
        if (!isFinite(pd.lat) || !isFinite(pd.lon)) continue;
        const btn = document.createElement("button"); btn.type = "button"; btn.className = "gb-pin t-" + (pd.tone || "blue");
        btn.setAttribute("aria-pressed", "false");
        const label = document.createElement("span"); label.textContent = pd.label || "";
        btn.append(label);
        if (pd.count) { const b = document.createElement("b"); b.textContent = Number(pd.count).toLocaleString("en-US"); btn.appendChild(b); }
        btn.setAttribute("aria-label", (pd.sheet && pd.sheet.title || pd.label || "Place") + (pd.count ? ", " + pd.count + " " + (data.countLabel || "") : ""));
        const p = {d: pd, id: pd.id, tone: pd.tone || "blue", v: unit(pd.lat, pd.lon), btn, w: 0, q: null};
        btn.addEventListener("click", (e) => { e.stopPropagation(); select(p.id, true); });
        pinsBox.appendChild(btn); pins.push(p);
      }
      routes = [];
      for (const rd of data.routes || []) {
        const pts = (rd.points || []).filter((q) => isFinite(q.lat) && isFinite(q.lon)).map((q) => unit(q.lat, q.lon));
        if (pts.length < 2) continue;
        const reached = Math.max(0, Math.min(pts.length - 1, rd.reached == null ? pts.length - 1 : rd.reached));
        const seg = [];
        for (let i = 0; i < pts.length - 1; i++) seg.push(arc(pts[i], pts[i + 1], i < reached));
        routes.push({id: rd.id, tone: rd.tone || "blue", seg});
      }
      empty.hidden = !!pins.length; empty.textContent = pins.length ? "" : "No places to show yet";
      drawCard();
      if (!cam.focus && pins.length && !selected) aimAt(centre(), false);
      dirty();
    }
    function arc(a, b, done) {              // great circle a→b lifted by its length; points as [x,y,z,k]
      const dot = Math.max(-1, Math.min(1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2])), om = Math.acos(dot);
      const n = Math.max(8, Math.ceil(om / (2 * DEG))), out = [], lift = Math.min(0.35, om * 0.32);
      for (let i = 0; i <= n; i++) {
        const t = i / n, s = Math.sin(om) || 1;
        const f1 = om ? Math.sin((1 - t) * om) / s : 1 - t, f2 = om ? Math.sin(t * om) / s : t;
        out.push([a[0] * f1 + b[0] * f2, a[1] * f1 + b[1] * f2, a[2] * f1 + b[2] * f2, 1 + lift * Math.sin(Math.PI * t)]);
      }
      return {pts: out, done};
    }
    function centre() {                     // the middle of the business units (else of every pin)
      const set = pins.filter((p) => String(p.id).startsWith("bu:"));
      const use = set.length ? set : pins;
      let x = 0, y = 0, z = 0;
      for (const p of use) { x += p.v[0]; y += p.v[1]; z += p.v[2]; }
      const m = Math.hypot(x, y, z) || 1;
      return {lat: Math.asin(y / m) / DEG, lon: Math.atan2(x / m, z / m) / DEG};
    }
    function aimAt(t, animate) {
      cam.focus = {lat: Math.max(-35, Math.min(55, t.lat - 6)), lon: t.lon};
      if (!animate || reduce()) { cam.lat = cam.focus.lat; cam.lon = cam.focus.lon; cam.focus = null; if (cam.zoomTo) cam.zoom = cam.zoomTo; }
      cam.spin = 0; dirty();
    }

    /* ── the glass card: past due by business unit, or the selected pin ── */
    function drawCard() {
      if (selected) {
        card.hidden = false;
        const s = selected.d.sheet || {};
        card.innerHTML = `<button class="gb-x" type="button" aria-label="Close">×</button><h3>${esc(s.title || selected.d.label)}</h3><p>${esc(s.subtitle || "")}</p>` +
          (s.stats && s.stats.length ? `<div class="gb-stats">${s.stats.slice(0, 4).map((x) => `<div><span>${esc(x.k)}</span><b class="${esc(x.tone || "")}">${esc(x.v)}</b></div>`).join("")}</div>` : "") +
          (s.rows && s.rows.length ? `<ul class="gb-rows">${s.rows.slice(0, 4).map((r) => `<li><b>${esc(r.ref)}</b> ${esc(r.text)}</li>`).join("")}</ul>` : "") +
          `<div class="gb-acts">${(s.actions || []).map((a, i) => `<button type="button" data-i="${i}" class="${a.primary ? "primary" : ""}">${esc(a.label)}</button>`).join("")}</div>` +
          (s.note ? `<p class="gb-note">${esc(s.note)}</p>` : "");
        card.querySelector(".gb-x").onclick = () => clear(true);
        card.querySelectorAll(".gb-acts button").forEach((b) => b.onclick = () => emit("action", selected && selected.d, (s.actions[+b.dataset.i] || {}).event));
        card.setAttribute("role", "dialog"); card.setAttribute("aria-label", s.title || "Place");
        card.classList.remove("list");
        return;
      }
      card.removeAttribute("role"); card.removeAttribute("aria-label"); card.classList.add("list");
      const bus = pins.filter((p) => String(p.id).startsWith("bu:")).sort((a, b) => (b.d.count || 0) - (a.d.count || 0));
      if (!bus.length) { card.hidden = true; return; }
      card.hidden = false;
      const total = bus.reduce((s, p) => s + (p.d.count || 0), 0), top = Math.max(1, bus[0].d.count || 0);
      const head = (data && data.card) || {};
      // title only; the total is a tooltip unless the caller asks for a line
      card.innerHTML = `<h3 title="${esc(total.toLocaleString("en-US") + " open shipments past their step's due date")}">${esc(head.title || "Past due by business unit")}</h3>${head.sub ? `<p>${esc(head.sub)}</p>` : ""}` +
        bus.slice(0, 6).map((p) => `<button class="gb-row" type="button" data-id="${esc(p.id)}"><span>${esc(p.d.label)}</span>` +
          `<s><u style="width:${Math.round(100 * (p.d.count || 0) / top)}%${/^[a-z]+$/.test(head.bar || "") ? ";background:var(--hub-" + head.bar + ")" : ""}"></u></s><em>${(p.d.count || 0).toLocaleString("en-US")}</em></button>`).join("");
      card.querySelectorAll(".gb-row").forEach((b) => b.onclick = () => select(b.dataset.id, true));
    }
    function select(id, user) {
      const p = pins.find((x) => x.id === id);
      if (!p) return false;
      if (selected) selected.btn.setAttribute("aria-pressed", "false");
      selected = p; p.btn.setAttribute("aria-pressed", "true");
      aimAt({lat: p.d.lat, lon: p.d.lon}, true);
      cam.zoomTo = Math.max(cam.zoom, 1.7);
      drawCard();
      if (user) { const f = card.querySelector(".gb-acts button"); f && f.focus({preventScroll: true}); }
      emit("select", p.d);
      return true;
    }
    function clear(user) {
      if (!selected) return;
      const back = selected.btn;
      selected.btn.setAttribute("aria-pressed", "false"); selected = null;
      cam.zoomTo = 1;
      drawCard();
      if (!paused) cam.spin = SPIN * 0.001;          // eases back to turning
      if (user && back.isConnected) back.focus({preventScroll: true});
      dirty();
    }

    /* ── controls: turn on/off, back to the business units ── */
    const play = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
    const pause = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h4v14H7zM13 5h4v14h-4z"/></svg>';
    const home = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="8"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg>';
    const bSpin = document.createElement("button"), bHome = document.createElement("button");
    const bIn = document.createElement("button"), bOut = document.createElement("button");
    bSpin.type = bHome.type = bIn.type = bOut.type = "button";
    bIn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';
    bOut.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 12h14"/></svg>';
    bIn.setAttribute("aria-label", "Zoom in"); bOut.setAttribute("aria-label", "Zoom out");
    bIn.onclick = () => { cam.zoomTo = Math.min(3.2, (cam.zoomTo || cam.zoom) * 1.35); dirty(); };
    bOut.onclick = () => { cam.zoomTo = Math.max(0.8, (cam.zoomTo || cam.zoom) / 1.35); dirty(); };
    function spinBtn() { bSpin.innerHTML = paused ? play : pause; bSpin.setAttribute("aria-label", paused ? "Turn the globe" : "Stop turning"); }
    bSpin.onclick = () => { paused = !paused; cam.spin = paused ? 0 : cam.spin; spinBtn(); dirty(); };
    bHome.innerHTML = home; bHome.setAttribute("aria-label", "Back to the business units");
    bHome.onclick = () => { clear(false); cam.zoomTo = 1; if (pins.length) aimAt(centre(), true); };
    spinBtn(); ctl.append(bOut, bIn, bSpin, bHome);

    /* ── drag to turn, with a little glide; a tap on empty space closes the card ── */
    let drag = null, vel = 0;
    root.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".gb-pin,.gb-card,.gb-ctl")) return;
      drag = {x: e.clientX, y: e.clientY, lon: cam.lon, lat: cam.lat, t: performance.now(), moved: false};
      root.setPointerCapture(e.pointerId); root.classList.add("drag"); cam.focus = null; cam.spin = 0; vel = 0;
    });
    root.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const k = 180 / Math.PI / (R * cam.zoom), dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      const now = performance.now(), nlon = drag.lon - dx * k;
      vel = (nlon - cam.lon) / Math.max(1, now - (drag.lt || drag.t)) * 1000; drag.lt = now;
      cam.lon = nlon; cam.lat = Math.max(-60, Math.min(70, drag.lat + dy * k));
      dirty();
    });
    const end = () => {
      if (!drag) return;
      if (!drag.moved) {                      // a tap: on a dot, that place; elsewhere, close the card
        const r0 = root.getBoundingClientRect(), tx = drag.x - r0.left, ty = drag.y - r0.top;
        let best = null, bd = 14 * 14;
        for (const p of pins) if (p.q) { const d2 = (p.q[0] - tx) ** 2 + (p.q[1] - ty) ** 2; if (d2 < bd) { bd = d2; best = p; } }
        if (best) select(best.id, true); else if (selected) clear(true);
      }
      drag = null; root.classList.remove("drag");
      if (!reduce() && Math.abs(vel) > 2) cam.glide = Math.max(-60, Math.min(60, vel));
      dirty();
    };
    root.addEventListener("pointerup", end); root.addEventListener("pointercancel", end);
    const onKey = (e) => { if (e.key === "Escape" && selected && root.contains(document.activeElement)) { e.stopPropagation(); clear(true); } };
    document.addEventListener("keydown", onKey, true);

    /* ── drawing ── */
    function dirty() { if (!raf && !destroyed && visible && !document.hidden) raf = requestAnimationFrame(frame); }
    function frame(now) {
      raf = 0;
      const dt = Math.min(0.05, last ? (now - last) / 1000 : 0.016); last = now;
      let moving = false;
      if (cam.zoomTo && Math.abs(cam.zoomTo - cam.zoom) > 0.002) {
        cam.zoom += (cam.zoomTo - cam.zoom) * (reduce() ? 1 : ease(4, dt)); moving = true;
      }
      if (cam.focus) {
        const k = ease(3.2, dt);
        let dl = ((cam.focus.lon - cam.lon + 540) % 360) - 180;
        cam.lon += dl * k; cam.lat += (cam.focus.lat - cam.lat) * k;
        if (Math.abs(dl) < 0.05 && Math.abs(cam.focus.lat - cam.lat) < 0.05) cam.focus = null;
        moving = true;
      } else if (cam.glide && !drag) {
        cam.lon += cam.glide * dt; cam.glide *= Math.exp(-dt * 3);
        if (Math.abs(cam.glide) < 0.5) cam.glide = 0;
        moving = true;
      } else if (!drag && !selected && !paused && !reduce()) {
        cam.spin += (SPIN - cam.spin) * ease(0.8, dt);
        cam.lon += cam.spin / DEG * dt;
        if (!cam.focus) cam.lat += (REST_PITCH / DEG - cam.lat) * ease(0.4, dt) * 0.15;
        moving = true;
      }
      if (routes.length && !reduce()) { flow = (flow + dt * 0.35) % 1; moving = true; }
      draw();
      if (moving) dirty();
    }
    function draw() {
      if (!pal || !W) return;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, W, H);
      const bgr = g.createLinearGradient(0, 0, 0, H); bgr.addColorStop(0, pal.bg0); bgr.addColorStop(1, pal.bg1);
      g.fillStyle = bgr; g.fillRect(0, 0, W, H);
      const r = R * cam.zoom;
      // halo, sphere, rim
      const halo = g.createRadialGradient(cx, cy, r * 0.92, cx, cy, r * 1.32);
      halo.addColorStop(0, pal.halo); halo.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = halo; g.beginPath(); g.arc(cx, cy, r * 1.32, 0, Math.PI * 2); g.fill();
      const sea = g.createRadialGradient(cx - r * 0.35, cy - r * 0.4, r * 0.1, cx, cy, r);
      sea.addColorStop(0, pal.sea0); sea.addColorStop(1, pal.sea1);
      g.fillStyle = sea; g.beginPath(); g.arc(cx, cy, r, 0, Math.PI * 2); g.fill();
      g.strokeStyle = pal.rim; g.lineWidth = 1; g.stroke();
      const b = basis();
      // a few lines of latitude and longitude, faint
      g.strokeStyle = pal.line; g.lineWidth = 1;
      for (let la = -60; la <= 60; la += 30) line(b, (t) => [la, -180 + t * 360]);
      for (let lo = -180; lo < 180; lo += 30) line(b, (t) => [-85 + t * 170, lo]);
      // land dots, in four depth bands (fewer state changes, a soft edge)
      if (LAND) {
        const bands = [[], [], [], []], fb = [[], [], [], []], X = LAND.X, Y = LAND.Y, Z = LAND.Z, s = r;
        for (let i = 0; i < LAND.n; i++) {
          const x1 = X[i] * b.cl - Z[i] * b.sl, z1 = X[i] * b.sl + Z[i] * b.cl;
          const z2 = Y[i] * b.sp + z1 * b.cp;
          if (z2 <= 0.02) continue;
          const y2 = Y[i] * b.cp - z1 * b.sp, band = z2 > 0.6 ? 3 : z2 > 0.35 ? 2 : z2 > 0.15 ? 1 : 0;
          (focusCC.has(LAND.cc[i]) ? fb : bands)[band].push(cx + x1 * s, cy - y2 * s);
        }
        const size = Math.max(0.9, Math.min(2.1, r / 190));
        const alpha = pal.dark ? [0.22, 0.4, 0.6, 0.78] : [0.18, 0.34, 0.5, 0.66];
        for (const [list, rgb, boost] of [[bands, pal.dot, 1], [fb, pal.focus, 1.25]]) {
          for (let k = 0; k < 4; k++) {
            const a = list[k]; if (!a.length) continue;
            g.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${Math.min(1, alpha[k] * boost)})`;
            g.beginPath();
            const sz = size * (0.75 + 0.25 * (k / 3)) * (boost > 1 ? 1.12 : 1);
            for (let j = 0; j < a.length; j += 2) { g.moveTo(a[j] + sz, a[j + 1]); g.arc(a[j], a[j + 1], sz, 0, Math.PI * 2); }
            g.fill();
          }
        }
      }
      // routes: reported part solid, the rest dashed, a dot flowing along
      for (const rt of routes) {
        const col = pal.tone[rt.tone] || pal.arc;
        for (const sg of rt.seg) {
          const P = sg.pts.map((q) => proj(q[0], q[1], q[2], b, q[3]));
          g.save(); g.strokeStyle = col; g.lineWidth = 2; g.lineCap = "round";
          g.shadowColor = col; g.shadowBlur = pal.dark ? 8 : 4;
          if (!sg.done) g.setLineDash([5, 5]);
          g.beginPath(); let on = false;
          for (const q of P) {
            const shown = q[2] > 0 || q[3] * q[3] + q[4] * q[4] > 1;     // in front, or beside the globe
            if (shown) { on ? g.lineTo(q[0], q[1]) : g.moveTo(q[0], q[1]); on = true; } else on = false;
          }
          g.stroke(); g.restore();
          const q = P[Math.min(P.length - 1, Math.floor(flow * P.length))];
          if (q && (q[2] > 0 || q[3] * q[3] + q[4] * q[4] > 1)) {
            g.fillStyle = "#fff"; g.beginPath(); g.arc(q[0], q[1], 3.2, 0, Math.PI * 2); g.fill();
            g.fillStyle = col; g.beginPath(); g.arc(q[0], q[1], 2, 0, Math.PI * 2); g.fill();
          }
        }
      }
      // pins: a dot on the surface; its label beside it where it fits (biggest first, never on top of another)
      const front = [];
      for (const p of pins) {
        const q = proj(p.v[0], p.v[1], p.v[2], b, 1);
        p.q = q[2] > 0.08 ? q : null;
        if (!p.q) continue;
        front.push(p);
        const col = pal.tone[p.tone] || pal.tone.blue, sel = p === selected;
        g.fillStyle = col; g.globalAlpha = sel ? 0.28 : 0.16; g.beginPath(); g.arc(q[0], q[1], sel ? 13 : 9, 0, Math.PI * 2); g.fill();
        g.globalAlpha = 1; g.fillStyle = "#fff"; g.beginPath(); g.arc(q[0], q[1], 4.5, 0, Math.PI * 2); g.fill();
        g.fillStyle = col; g.beginPath(); g.arc(q[0], q[1], 3, 0, Math.PI * 2); g.fill();
      }
      const taken = front.map((p) => [p.q[0] - 5, p.q[1] - 5, p.q[0] + 5, p.q[1] + 5]);
      if (!card.hidden && card.offsetParent) {          // the glass card: labels keep out from under it
        const cr = card.getBoundingClientRect(), rr = root.getBoundingClientRect();
        taken.push([cr.left - rr.left - 4, cr.top - rr.top - 4, cr.right - rr.left + 4, cr.bottom - rr.top + 4]);
      }
      const hit = (r) => taken.some((t) => r[0] < t[2] && r[2] > t[0] && r[1] < t[3] && r[3] > t[1]);
      front.sort((a, c) => (c === selected) - (a === selected) || (c.d.count || 0) - (a.d.count || 0));
      for (const p of pins) if (!p.q) { p.btn.style.visibility = "hidden"; p.btn.tabIndex = -1; }
      for (const p of front) {
        if (!p.w) p.w = p.btn.offsetWidth || 60;
        const x = p.q[0], y = p.q[1], w = p.w, h = 22;
        const spots = [[x + 9, y - h / 2], [x - 9 - w, y - h / 2], [x - w / 2, y - h - 8], [x - w / 2, y + 8], [x + 9, y - h - 4], [x - 9 - w, y + 4]];
        let at = null;
        for (const [sx, sy] of spots) {
          const r = [sx - 2, sy - 2, sx + w + 2, sy + h + 2];
          if (sx >= 0 && sy >= 0 && sx + w <= W && sy + h <= H && !hit(r)) { at = [sx, sy]; taken.push(r); break; }
        }
        p.btn.style.visibility = "visible"; p.btn.tabIndex = 0;
        p.btn.classList.toggle("tight", !at);
        const [lx, ly] = at || spots[0];
        p.btn.style.transform = `translate(${Math.round(lx)}px,${Math.round(ly)}px)`;
        p.btn.style.opacity = String(Math.min(1, 0.4 + p.q[2]));
      }
    }
    function line(b, f) {
      g.beginPath(); let on = false;
      for (let i = 0; i <= 72; i++) {
        const [la, lo] = f(i / 72), v = unit(la, lo), q = proj(v[0], v[1], v[2], b, 1);
        if (q[2] > 0) { on ? g.lineTo(q[0], q[1]) : g.moveTo(q[0], q[1]); on = true; } else on = false;
      }
      g.stroke();
    }

    /* ── life cycle: theme, size, visibility ── */
    readTheme(); resize();
    const mo = new MutationObserver(readTheme);
    mo.observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme", "data-dark", "class"]});
    const mq = window.matchMedia ? matchMedia("(prefers-color-scheme: dark)") : null;
    mq && mq.addEventListener && mq.addEventListener("change", readTheme);
    const ro = window.ResizeObserver ? new ResizeObserver(resize) : null;
    ro ? ro.observe(root) : window.addEventListener("resize", resize);
    const io = window.IntersectionObserver ? new IntersectionObserver((es) => { visible = es.some((x) => x.isIntersecting); dirty(); }) : null;
    io && io.observe(root);
    const onVis = () => { last = 0; dirty(); };
    document.addEventListener("visibilitychange", onVis);
    if (!LAND) {
      fetch(opts.worldUrl || "/static/maps/world.json", {credentials: "same-origin"}).then((r) => r.ok ? r.json() : Promise.reject(r.status))
        .then((w) => { LAND = LAND || landDots(w); root.dataset.dots = String(LAND.n); dirty(); })
        .catch(() => { empty.hidden = false; empty.textContent = "The world outline did not load; pins are still shown"; });
    } else root.dataset.dots = String(LAND.n);

    const api = {
      mode: "globe",
      setData,
      on(n, f) { if (L[n] && typeof f === "function") L[n].push(f); return () => api.off(n, f); },
      off(n, f) { const l = L[n]; if (!l) return; const i = l.indexOf(f); if (i >= 0) l.splice(i, 1); },
      select(id) { return select(id, false); },
      fit() { bHome.onclick(); return Promise.resolve(true); },
      getView() { return {mode: "globe", lat: cam.lat, lon: cam.lon, zoom: cam.zoom, spinning: !paused && !selected}; },
      destroy() {
        destroyed = true; if (raf) cancelAnimationFrame(raf);
        document.removeEventListener("keydown", onKey, true); document.removeEventListener("visibilitychange", onVis);
        mq && mq.removeEventListener && mq.removeEventListener("change", readTheme);
        mo.disconnect(); ro ? ro.disconnect() : window.removeEventListener("resize", resize); io && io.disconnect();
        root.remove(); delete host.dataset.mapMode;
      }
    };
    return api;
  }

  window.HubGlobe = {mount};
})();
