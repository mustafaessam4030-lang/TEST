/* ATLAS Control Tower — HubMap3D: a tilted "clay" world map (Apple Maps 3D style).

   One self-contained component: vanilla JS, canvas 2D, no tiles, no libraries.
   Land comes from /static/maps/world.json (Natural Earth outlines already in
   Web-Mercator viewBox units); the flat Mercator plane is drawn through our own
   perspective camera (zoom, pitch 0–60°, bearing). Land is a gently extruded
   slab (darker side walls, light top, soft drop shadow), countries in `focus`
   are tinted and raised a little, routes are a bold line (solid where travelled,
   dashed where planned) and places are round pins with a label and a bottom
   sheet. Colours come from the hub.css tokens (--hub-*) and follow the theme.

   Only what the caller gives is drawn: a pin is the place it was given, a route
   joins the points it was given (a great-circle curve through each of them).
   Nothing is moved, guessed or interpolated in time.

     const map = HubMap3D.mount(el, {worldUrl: "/static/maps/world.json"});
     map.setData({focus: ["EG"], pins: [...], routes: [...]});
     map.on("select", pin => {}); map.on("action", (pin, event) => {});
     map.select(id | null); map.flyTo({lat, lon, zoom, pitch, bearing}, {duration});
     map.fit(); map.destroy();
*/
(function () {
  "use strict";
  if (typeof window === "undefined" || (window.HubMap3D && window.HubMap3D.version)) return;

  const DEG = Math.PI / 180;
  const TONES = ["blue", "green", "amber", "red", "teal"];
  const SEVERITY = { red: 5, amber: 4, blue: 3, teal: 2, green: 1 };
  const MIN_ZOOM = 0.7, MAX_ZOOM = 48, MAX_PITCH = 60, PITCH_3D = 40;
  const FOV = 40 * DEG;          // vertical field of view of the camera
  const FIT = 0.94;              // zoom 1: the whole world fits with a small margin
  const NARROW = 560;            // container width under which the sheet is a phone sheet

  /* ───────────────────────── styles (injected once) ───────────────────────── */
  const CSS = `
.hm3-root{position:relative;width:100%;height:100%;min-height:300px;overflow:hidden;box-sizing:border-box;
  background:var(--hub-water,#dfe9f3);color:var(--hub-ink,#1d1d1f);font:14px/1.4 var(--hub-font,system-ui,sans-serif);
  touch-action:pan-y;-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;outline:none;isolation:isolate}
.hm3-root *,.hm3-root *::before,.hm3-root *::after{box-sizing:border-box}
.hm3-root:focus-visible{box-shadow:inset 0 0 0 2px var(--hub-blue,#0071e3)}
.hm3-root.hm3-full{touch-action:none}
.hm3-root.hm3-pseudo{position:fixed;left:0;top:0;right:0;bottom:0;width:auto;height:auto;min-height:0;z-index:2147483000;border-radius:0}
.hm3-root:fullscreen{width:100vw;height:100vh}
.hm3-cv{position:absolute;left:0;top:0;width:100%;height:100%;display:block}
.hm3-over{cursor:grab}
.hm3-root.hm3-drag .hm3-over{cursor:grabbing}
.hm3-root.hm3-hot .hm3-over{cursor:pointer}
.hm3-sr{position:absolute!important;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);clip-path:inset(50%);white-space:nowrap}
.hm3-pins{position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;overflow:hidden}
.hm3-pinbtn{position:absolute;left:0;top:0;margin:0;padding:0;border:0;border-radius:50%;background:transparent;color:transparent;
  font-size:0;pointer-events:none;outline:none}
.hm3-pinbtn:focus-visible{box-shadow:0 0 0 2.5px var(--hub-surface,#fff),0 0 0 5px var(--hub-blue,#0071e3)}
.hm3-glass{background:var(--hub-glass,rgba(255,255,255,.72));border:1px solid var(--hub-glass-edge,rgba(255,255,255,.65));
  -webkit-backdrop-filter:saturate(180%) blur(20px);backdrop-filter:saturate(180%) blur(20px)}
.hm3-ctrls{position:absolute;top:12px;right:12px;z-index:3;display:flex;flex-direction:column;gap:8px;align-items:flex-end}
.hm3-grp{display:flex;flex-direction:column;border-radius:12px;overflow:hidden;
  box-shadow:var(--hub-shadow,0 1px 2px rgba(0,0,0,.04),0 4px 14px rgba(0,0,0,.05))}
.hm3-btn{width:38px;height:38px;display:flex;align-items:center;justify-content:center;border:0;margin:0;padding:0;background:transparent;
  color:var(--hub-ink-2,#424245);cursor:pointer;font:650 12.5px/1 var(--hub-font,system-ui,sans-serif);letter-spacing:.01em}
.hm3-btn:hover{background:rgba(128,128,128,.12)}
.hm3-btn:disabled{opacity:.35;cursor:default;background:transparent}
.hm3-btn:focus-visible{outline:2px solid var(--hub-blue,#0071e3);outline-offset:-3px;border-radius:11px}
.hm3-grp .hm3-btn+.hm3-btn{border-top:1px solid var(--hub-line,rgba(0,0,0,.08))}
.hm3-btn svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;overflow:visible}
.hm3-needle{transition:transform .2s var(--hub-e,ease)}
.hm3-note{position:absolute;left:50%;top:14px;transform:translateX(-50%);z-index:2;padding:7px 14px;border-radius:999px;
  font-size:12.5px;font-weight:500;color:var(--hub-ink-2,#424245);white-space:nowrap;pointer-events:none;
  box-shadow:var(--hub-shadow,0 4px 14px rgba(0,0,0,.06))}
.hm3-sheet{position:absolute;left:12px;bottom:12px;z-index:4;width:380px;max-width:calc(100% - 24px);max-height:calc(100% - 24px);
  overflow:auto;overscroll-behavior:contain;padding:16px 16px 14px;border-radius:var(--hub-r-lg,20px);
  box-shadow:var(--hub-shadow-lg,0 10px 40px rgba(0,0,0,.12));color:var(--hub-ink,#1d1d1f);cursor:auto;touch-action:pan-y;
  -webkit-user-select:text;user-select:text;opacity:0;transform:translateY(14px) scale(.985);transform-origin:50% 100%;
  transition:opacity .22s var(--hub-e,ease),transform .32s var(--hub-e,ease)}
.hm3-sheet.hm3-show{opacity:1;transform:none}
.hm3-narrow .hm3-sheet{left:0;right:0;bottom:0;width:auto;max-width:none;max-height:74%;border-radius:20px 20px 0 0;
  padding-top:6px;border-left:0;border-right:0;border-bottom:0;transform:translateY(40px)}
.hm3-narrow .hm3-sheet.hm3-show{transform:none}
.hm3-grab{display:none;height:22px;margin:0 -16px 2px;cursor:grab;touch-action:none}
.hm3-grab::before{content:"";display:block;width:36px;height:5px;border-radius:3px;margin:6px auto 0;background:var(--hub-ink-4,#a1a1a6);opacity:.55}
.hm3-narrow .hm3-grab{display:block}
.hm3-narrow .hm3-ctrls{top:10px;right:10px;gap:6px}
.hm3-narrow .hm3-btn{width:34px;height:34px}
.hm3-narrow .hm3-btn svg{width:15px;height:15px}
.hm3-head{display:flex;align-items:flex-start;gap:10px}
.hm3-head>div{flex:1;min-width:0}
.hm3-title{margin:0;font-size:19px;line-height:1.25;font-weight:700;letter-spacing:-.01em;color:var(--hub-ink,#1d1d1f);overflow-wrap:anywhere}
.hm3-sub{margin-top:2px;font-size:13px;color:var(--hub-ink-3,#6e6e73);overflow-wrap:anywhere}
.hm3-x{flex:none;width:28px;height:28px;border-radius:50%;border:0;padding:0;margin:0;display:flex;align-items:center;justify-content:center;
  background:rgba(120,120,128,.14);color:var(--hub-ink-3,#6e6e73);cursor:pointer}
.hm3-x svg{width:11px;height:11px;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;fill:none}
.hm3-x:hover{background:rgba(120,120,128,.22)}
.hm3-x:focus-visible,.hm3-act:focus-visible,.hm3-rowbtn:focus-visible{outline:2px solid var(--hub-blue,#0071e3);outline-offset:2px}
.hm3-acts{display:flex;gap:8px;margin-top:14px}
.hm3-act{flex:1 1 0;min-width:0;height:38px;padding:0 12px;border-radius:999px;border:0;margin:0;cursor:pointer;
  font:600 14px/1 var(--hub-font,system-ui,sans-serif);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  background:rgba(120,120,128,.12);color:var(--hub-blue,#0071e3);transition:filter .15s}
.hm3-act.hm3-primary{flex-grow:1.35;background:var(--hub-blue,#0071e3);color:#fff}
.hm3-act:hover{filter:brightness(.96)}.hm3-act.hm3-primary:hover{filter:brightness(1.08)}
.hm3-stats{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(0,1fr);margin-top:16px}
.hm3-stat{min-width:0;padding-right:10px}
.hm3-stat+.hm3-stat{border-left:1px solid var(--hub-line,rgba(0,0,0,.08));padding-left:12px}
.hm3-k{font-size:10.5px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--hub-ink-3,#6e6e73);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hm3-v{margin-top:3px;font-size:15.5px;font-weight:650;color:var(--hub-ink,#1d1d1f);font-variant-numeric:tabular-nums;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hm3-t-blue{color:var(--hub-blue,#0071e3)!important}.hm3-t-green{color:var(--hub-green,#28a745)!important}
.hm3-t-amber{color:var(--hub-amber,#e08a00)!important}.hm3-t-red{color:var(--hub-red,#e5383b)!important}
.hm3-t-teal{color:var(--hub-teal,#0a9fb5)!important}
.hm3-bars{margin-top:14px;padding-top:12px;border-top:1px solid var(--hub-line,rgba(0,0,0,.08))}
.hm3-bh{display:flex;align-items:baseline;justify-content:space-between;gap:8px;font-size:13.5px;font-weight:600}
.hm3-live{font-size:12px;font-weight:600;color:var(--hub-green,#28a745)}
.hm3-plot{display:flex;align-items:flex-end;gap:4px;height:54px;margin-top:10px}
.hm3-bar{flex:1 1 0;min-width:0;border-radius:4px 4px 2px 2px;background:var(--hub-blue,#0071e3);opacity:.26;
  transform:scaleY(0);transform-origin:50% 100%;transition:transform .55s var(--hub-e,cubic-bezier(.2,.8,.2,1))}
.hm3-bar.hm3-hi{opacity:1}
.hm3-in .hm3-bar{transform:none}
.hm3-xl{display:flex;gap:4px;margin-top:5px}
.hm3-xl span{flex:1 1 0;min-width:0;text-align:center;font-size:10px;color:var(--hub-ink-3,#6e6e73);white-space:nowrap;overflow:visible}
.hm3-rows{margin:12px 0 0;padding:8px 0 0;list-style:none;border-top:1px solid var(--hub-line,rgba(0,0,0,.08))}
.hm3-row{display:flex;align-items:center;gap:8px;min-height:28px;font-size:13px}
.hm3-dot{flex:none;width:7px;height:7px;border-radius:50%;background:var(--hub-ink-4,#a1a1a6)}
.hm3-dot.hm3-t-blue{background:var(--hub-blue,#0071e3)}.hm3-dot.hm3-t-green{background:var(--hub-green,#28a745)}
.hm3-dot.hm3-t-amber{background:var(--hub-amber,#e08a00)}.hm3-dot.hm3-t-red{background:var(--hub-red,#e5383b)}
.hm3-dot.hm3-t-teal{background:var(--hub-teal,#0a9fb5)}
.hm3-ref{flex:none;font:500 12px/1.3 var(--hub-mono,ui-monospace,monospace);color:var(--hub-ink-2,#424245)}
.hm3-rt{flex:1;min-width:0;color:var(--hub-ink-3,#6e6e73);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hm3-more{font-size:12px;color:var(--hub-ink-3,#6e6e73);padding-top:2px}
.hm3-rowbtn{display:flex;align-items:center;gap:8px;width:100%;min-height:34px;border:0;margin:0;padding:0 4px;border-radius:8px;
  background:transparent;color:var(--hub-ink,#1d1d1f);font:500 13.5px/1.2 var(--hub-font,system-ui,sans-serif);text-align:left;cursor:pointer}
.hm3-rowbtn:hover{background:rgba(120,120,128,.10)}
.hm3-rowbtn .hm3-rt{color:var(--hub-ink,#1d1d1f)}
.hm3-rowbtn b{font-weight:600;color:var(--hub-ink-3,#6e6e73);font-variant-numeric:tabular-nums}
.hm3-foot{margin-top:12px;font-size:11.5px;line-height:1.4;color:var(--hub-ink-3,#6e6e73)}
@media (prefers-reduced-motion:reduce){.hm3-sheet,.hm3-bar,.hm3-needle{transition:none!important}}
`;
  function injectCss() {
    if (document.getElementById("hubmap3d-css")) return;
    const s = document.createElement("style");
    s.id = "hubmap3d-css";
    s.textContent = CSS;
    (document.head || document.documentElement).appendChild(s);
  }

  /* ───────────────────────── small helpers ───────────────────────── */
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function easeInOut(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }
  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
  function easeOutBack(t) { const c1 = 1.3, c3 = c1 + 1; return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2); }
  function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
  function num(v) { return typeof v === "number" ? v : (typeof v === "string" && v.trim() !== "" ? Number(v) : NaN); }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }
  let uidN = 0;
  function uid(p) { uidN += 1; return p + "-" + uidN + "-" + Math.random().toString(36).slice(2, 7); }
  function mq(q) { try { return window.matchMedia ? window.matchMedia(q) : null; } catch (e) { return null; } }
  function onMq(m, fn) { if (!m) return function () {}; if (m.addEventListener) { m.addEventListener("change", fn); return function () { m.removeEventListener("change", fn); }; } if (m.addListener) { m.addListener(fn); return function () { m.removeListener(fn); }; } return function () {}; }
  function fmtCount(n) {
    if (n == null || !isFinite(n)) return "";
    n = Math.round(n);
    if (Math.abs(n) >= 10000) return Math.round(n / 1000) + "k";
    if (Math.abs(n) >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return String(n);
  }
  function tone(t) { return TONES.indexOf(t) >= 0 ? t : "blue"; }

  // colours: normalise any CSS colour through a 2D context, keep [r,g,b,a]
  let cctx = null;
  function parseColor(c, fallback) {
    if (!cctx) cctx = document.createElement("canvas").getContext("2d");
    if (!cctx) {   // no canvas: hex only
      const m = /^#([0-9a-f]{6})$/i.exec(String(c || "").trim()) || /^#([0-9a-f]{6})$/i.exec(fallback) || ["", "888888"];
      return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16), 1];
    }
    const probe = "#010203";
    cctx.fillStyle = probe;
    cctx.fillStyle = String(c || "").trim() || fallback;
    let s = cctx.fillStyle;
    if (s === probe && String(c).replace(/\s/g, "").toLowerCase() !== probe) { cctx.fillStyle = fallback; s = cctx.fillStyle; }
    if (s.charAt(0) === "#") {
      const h = s.slice(1);
      return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), 1];
    }
    const m = s.match(/rgba?\(([^)]+)\)/);
    if (m) {
      const p = m[1].split(",").map(Number);
      return [p[0] | 0, p[1] | 0, p[2] | 0, p.length > 3 ? p[3] : 1];
    }
    return parseColor(fallback, "#888888");
  }
  function css(c, a) {
    const al = (c[3] == null ? 1 : c[3]) * (a == null ? 1 : a);
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + Math.round(al * 1000) / 1000 + ")";
  }
  function mix(a, b, t) { return [Math.round(lerp(a[0], b[0], t)), Math.round(lerp(a[1], b[1], t)), Math.round(lerp(a[2], b[2], t)), lerp(a[3], b[3], t)]; }
  function lum(c) { return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255; }

  /* ───────────────────────── projection and world ───────────────────────── */
  // Web Mercator in world.json viewBox units: x from longitude, y so that lat_top -> 0
  // and lat_bottom -> viewBox height (the same projection the outlines were built with).
  function makeProj(W, H, latTop, latBot) {
    const my = function (lat) { return Math.log(Math.tan(Math.PI / 4 + clamp(lat, -85, 85) * DEG / 2)); };
    const y0 = my(latTop), ky = H / (y0 - my(latBot));
    return {
      W: W, H: H,
      x: function (lon) { return (lon + 180) / 360 * W; },
      y: function (lat) { return (y0 - my(lat)) * ky; },
      lon: function (x) { return x / W * 360 - 180; },
      lat: function (y) { return (2 * Math.atan(Math.exp(y0 - y / ky)) - Math.PI / 2) / DEG; }
    };
  }
  const DEFAULT_PROJ = makeProj(2000, 1094.3, 78, -56);

  function parsePath(d) {
    const rings = [];
    const toks = d.match(/[MLHVZmlhvz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g) || [];
    let cur = null, x = 0, y = 0, sx = 0, sy = 0, cmd = "M", i = 0;
    function flush() { if (cur && cur.length >= 6) rings.push(new Float32Array(cur)); cur = null; }
    while (i < toks.length) {
      const t = toks[i];
      if (/^[A-Za-z]$/.test(t)) {
        cmd = t; i++;
        if (cmd === "Z" || cmd === "z") { flush(); x = sx; y = sy; }
        continue;
      }
      if (cmd === "M" || cmd === "m") {
        const nx = +toks[i], ny = +toks[i + 1]; i += 2;
        flush();
        if (cmd === "m") { x += nx; y += ny; } else { x = nx; y = ny; }
        sx = x; sy = y; cur = [x, y];
        cmd = cmd === "m" ? "l" : "L";
      } else if (cmd === "L" || cmd === "l") {
        const nx = +toks[i], ny = +toks[i + 1]; i += 2;
        if (cmd === "l") { x += nx; y += ny; } else { x = nx; y = ny; }
        if (!cur) cur = [sx, sy];
        cur.push(x, y);
      } else if (cmd === "H" || cmd === "h") {
        const n = +toks[i]; i += 1; x = cmd === "h" ? x + n : n;
        if (!cur) cur = [sx, sy];
        cur.push(x, y);
      } else if (cmd === "V" || cmd === "v") {
        const n = +toks[i]; i += 1; y = cmd === "v" ? y + n : n;
        if (!cur) cur = [sx, sy];
        cur.push(x, y);
      } else { i++; }
    }
    flush();
    return rings;
  }

  // Douglas–Peucker on a closed ring (flat [x,y,...]); tol in world units
  const LOD_TOL = [0, 0.6, 1.2, 2.5];
  function simplify(pts, tol) {
    const n = pts.length >> 1;
    if (n < 5) return pts;
    const keep = new Uint8Array(n);
    keep[0] = 1; keep[n - 1] = 1;
    const stack = [0, n - 1], t2 = tol * tol;
    while (stack.length) {
      const b = stack.pop(), a = stack.pop();
      const ax = pts[a * 2], ay = pts[a * 2 + 1], dx = pts[b * 2] - ax, dy = pts[b * 2 + 1] - ay, L2 = dx * dx + dy * dy;
      let md = -1, mi = -1;
      for (let i = a + 1; i < b; i++) {
        const px = pts[i * 2] - ax, py = pts[i * 2 + 1] - ay;
        const d2 = L2 > 1e-12 ? Math.pow(px * dy - py * dx, 2) / L2 : px * px + py * py;
        if (d2 > md) { md = d2; mi = i; }
      }
      if (md > t2) { keep[mi] = 1; stack.push(a, mi, mi, b); }
    }
    let m = 0;
    for (let i = 0; i < n; i++) m += keep[i];
    if (m < 3 || m === n) return m === n ? pts : null;
    const out = new Float32Array(m * 2);
    for (let i = 0, k = 0; i < n; i++) if (keep[i]) { out[k++] = pts[i * 2]; out[k++] = pts[i * 2 + 1]; }
    return out;
  }

  function parseWorld(j) {
    const pr = (j && j.projection) || {}, vb = (j && j.viewBox) || [0, 0, 2000, 1094.3];
    const W = +pr.width || +vb[2] || 2000, H = +vb[3] || 1094.3;
    const proj = makeProj(W, H, isFinite(+pr.lat_top) ? +pr.lat_top : 78, isFinite(+pr.lat_bottom) ? +pr.lat_bottom : -56);
    const rings = [], countries = [];
    ((j && j.countries) || []).forEach(function (c, ci) {
      const rs = parsePath(String(c.d || ""));
      const idx = [];
      let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
      rs.forEach(function (pts) {
        // a ring that crosses the 180° meridian jumps across the whole map: keep it continuous
        // (the far side is drawn just past the edge) instead of a band across the world
        let shift = 0;
        for (let k = 2; k < pts.length; k += 2) {
          const jump = pts[k] + shift - pts[k - 2];
          if (jump > W / 2) shift -= W; else if (jump < -W / 2) shift += W;
          pts[k] += shift;
        }
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
        for (let k = 0; k < pts.length; k += 2) {
          const px = pts[k], py = pts[k + 1];
          if (px < x0) x0 = px; if (px > x1) x1 = px; if (py < y0) y0 = py; if (py > y1) y1 = py;
        }
        const lod = [pts];
        for (let l = 1; l < LOD_TOL.length; l++) lod.push(simplify(lod[l - 1], LOD_TOL[l]));
        rings.push({ pts: pts, lod: lod, bb: [x0, y0, x1, y1], ci: ci });
        idx.push(rings.length - 1);
        if (x0 < bx0) bx0 = x0; if (y0 < by0) by0 = y0; if (x1 > bx1) bx1 = x1; if (y1 > by1) by1 = y1;
      });
      countries.push({ cc: String(c.cc || "").toUpperCase(), name: String(c.name || ""), rings: idx, bb: [bx0, by0, bx1, by1] });
    });
    return { proj: proj, rings: rings, countries: countries };
  }

  const worldCache = {};
  function loadWorld(url) {
    if (!worldCache[url]) {
      const p = fetch(url, { credentials: "same-origin" })
        .then(function (r) { if (!r.ok) throw new Error("world map HTTP " + r.status); return r.json(); })
        .then(parseWorld);
      worldCache[url] = p;
      p.catch(function () { if (worldCache[url] === p) delete worldCache[url]; });
    }
    return worldCache[url];
  }

  function inRing(pts, x, y) {
    let c = false;
    for (let i = 0, j = pts.length - 2; i < pts.length; j = i, i += 2) {
      const xi = pts[i], yi = pts[i + 1], xj = pts[j], yj = pts[j + 1];
      if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) c = !c;
    }
    return c;
  }
  function countryAt(world, x, y) {
    if (!world) return "";
    for (let i = 0; i < world.countries.length; i++) {
      const c = world.countries[i], b = c.bb;
      if (x < b[0] || x > b[2] || y < b[1] || y > b[3]) continue;
      let inside = false;
      for (let k = 0; k < c.rings.length; k++) if (inRing(world.rings[c.rings[k]].pts, x, y)) inside = !inside;
      if (inside) return c.cc;
    }
    return "";
  }

  // great-circle points between two given places (the curve passes through both)
  function gcPath(a, b) {
    const la1 = a.lat * DEG, lo1 = a.lon * DEG, la2 = b.lat * DEG, lo2 = b.lon * DEG;
    const v1 = [Math.cos(la1) * Math.cos(lo1), Math.cos(la1) * Math.sin(lo1), Math.sin(la1)];
    const v2 = [Math.cos(la2) * Math.cos(lo2), Math.cos(la2) * Math.sin(lo2), Math.sin(la2)];
    const dot = clamp(v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2], -1, 1);
    const om = Math.acos(dot), so = Math.sin(om);
    const steps = clamp(Math.ceil(om / DEG / 1.5), 1, 120);
    const out = [];
    for (let k = 1; k <= steps; k++) {
      const t = k / steps;
      if (k === steps) { out.push({ lat: b.lat, lon: b.lon }); break; }
      if (so < 1e-6) { out.push({ lat: lerp(a.lat, b.lat, t), lon: lerp(a.lon, b.lon, t) }); continue; }
      const s1 = Math.sin((1 - t) * om) / so, s2 = Math.sin(t * om) / so;
      const x = s1 * v1[0] + s2 * v2[0], y = s1 * v1[1] + s2 * v2[1], z = s1 * v1[2] + s2 * v2[2];
      out.push({ lat: Math.atan2(z, Math.sqrt(x * x + y * y)) / DEG, lon: Math.atan2(y, x) / DEG });
    }
    return out;
  }

  /* ───────────────────────── icons ───────────────────────── */
  const ICON = {
    plus: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10"/></svg>',
    minus: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10"/></svg>',
    compass: '<svg viewBox="0 0 16 16" aria-hidden="true" style="overflow:visible"><g class="hm3-needle">' +
      '<path d="M8 1.6 10.4 8H5.6Z" style="fill:var(--hub-red,#e5383b);stroke:none"/>' +
      '<path d="M8 14.4 5.6 8h4.8Z" style="fill:currentColor;stroke:none;opacity:.45"/></g></svg>',
    full: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10"/></svg>',
    unfull: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 2.5V6H2.5M13.5 6H10V2.5M10 13.5V10h3.5M2.5 10H6v3.5"/></svg>',
    fit: '<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="2.3"/><path d="M8 1.5v2.2M8 12.3v2.2M1.5 8h2.2M12.3 8h2.2"/><circle cx="8" cy="8" r="4.9"/></svg>',
    close: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 2l8 8M10 2l-8 8"/></svg>'
  };

  /* ═════════════════════════ mount ═════════════════════════ */
  function mount(container, opts) {
    if (!container || !container.appendChild) throw new Error("HubMap3D.mount: a container element is required");
    opts = opts || {};
    injectCss();

    const worldUrl = opts.worldUrl || "/static/maps/world.json";
    let proj = DEFAULT_PROJ, world = null, worldVer = 0, worldFailed = false;
    let destroyed = false;
    const cleanups = [];
    const listeners = { select: [], action: [], cluster: [] };

    /* ── DOM ── */
    const root = el("div", "hm3-root");
    root.tabIndex = 0;
    root.setAttribute("role", "region");
    root.setAttribute("aria-roledescription", "map");
    root.setAttribute("aria-label", opts.label || "Map");
    const help = el("div", "hm3-sr", "Arrow keys move the map, plus and minus zoom, Shift with the arrow keys rotates and tilts. Tab moves between places. Escape closes the details.");
    help.id = uid("hm3-help");
    root.setAttribute("aria-describedby", help.id);
    const cvBase = el("canvas", "hm3-cv hm3-base"), cvOver = el("canvas", "hm3-cv hm3-over");
    cvBase.setAttribute("aria-hidden", "true"); cvOver.setAttribute("aria-hidden", "true");
    const bctx = cvBase.getContext("2d"), octx = cvOver.getContext("2d");
    const shadowCv = document.createElement("canvas"), sctx = shadowCv.getContext("2d");
    const noCanvas = !bctx || !octx || !sctx || typeof window.Path2D !== "function";
    const pinLayer = el("div", "hm3-pins");
    const note = el("div", "hm3-note hm3-glass"); note.hidden = true;
    const live = el("div", "hm3-sr"); live.setAttribute("aria-live", "polite");
    const sheet = el("section", "hm3-sheet hm3-glass"); sheet.hidden = true;
    sheet.setAttribute("aria-label", "Place details");

    const ctrls = el("div", "hm3-ctrls");
    function mkBtn(html, label, onClick) {
      const b = el("button", "hm3-btn"); b.type = "button"; b.innerHTML = html;
      b.setAttribute("aria-label", label); b.title = label;
      b.addEventListener("click", function (e) { e.stopPropagation(); onClick(); });
      return b;
    }
    function grp() { const g = el("div", "hm3-grp hm3-glass"); for (let i = 0; i < arguments.length; i++) g.appendChild(arguments[i]); ctrls.appendChild(g); return g; }
    const bIn = mkBtn(ICON.plus, "Zoom in", function () { zoomBy(2); });
    const bOut = mkBtn(ICON.minus, "Zoom out", function () { zoomBy(0.5); });
    const bDim = mkBtn("2D", "Switch to 2D view", toggle3D);
    const bCompass = mkBtn(ICON.compass, "Reset to north", function () { flyCam({ bearing: 0 }, 450); });
    const bFull = mkBtn(ICON.full, "Full screen", toggleFull);
    const bFit = mkBtn(ICON.fit, "Show all places", function () { fit(); });
    grp(bIn, bOut); grp(bDim); grp(bCompass); grp(bFull); grp(bFit);
    const needle = bCompass.querySelector(".hm3-needle");

    root.appendChild(help); root.appendChild(cvBase); root.appendChild(cvOver); root.appendChild(pinLayer);
    root.appendChild(note); root.appendChild(ctrls); root.appendChild(sheet); root.appendChild(live);
    container.appendChild(root);

    /* ── state ── */
    const reduceMq = mq("(prefers-reduced-motion: reduce)");
    let reduce = !!(reduceMq && reduceMq.matches);
    const initPitch = isFinite(+opts.pitch) ? clamp(+opts.pitch, 0, MAX_PITCH) : PITCH_3D;
    const cam = { x: proj.W / 2, y: proj.H / 2, zoom: 1, pitch: initPitch, bearing: 0 };
    let pitch3D = initPitch > 5 ? initPitch : PITCH_3D;
    const V = { W: 300, H: 300, s: 1, cb: 1, sb: 0, cp: 1, sp: 0, D: 500, cx: 150, cy: 150, ryLim: 1e9, hTop: 4, hF: 8, ps: 1 };
    let cssW = 0, cssH = 0, dpr = 1, narrow = false;
    let T = null, themeVer = 0;
    let countLabel = "shipments";
    let focus = new Set(), pins = [], pinById = {}, routes = [], dataVer = 0, hasData = false;
    const born = {}, routeBorn = {};
    let selKey = null, selPinId = null, selCluster = null, selT0 = 0;
    let userMoved = false, autoFitted = false;
    let raf = 0, baseDirty = true, lastKey = "";
    let anim = null, inertia = null, moving = false, wasMoving = false;
    let items = [], heads = [];
    const perf = [];

    /* ── theme ── */
    function readTheme() {
      const cs = getComputedStyle(root);
      function v(n, d) { const x = cs.getPropertyValue(n); return (x && x.trim()) || d; }
      const t = {
        water: parseColor(v("--hub-water", "#dfe9f3"), "#dfe9f3"),
        water2: parseColor(v("--hub-water-2", "#cfdeec"), "#cfdeec"),
        land: parseColor(v("--hub-land", "#fbfbfa"), "#fbfbfa"),
        side: parseColor(v("--hub-land-side", "#dcdcd6"), "#dcdcd6"),
        edge: parseColor(v("--hub-land-edge", "#c9c9c2"), "#c9c9c2"),
        focus: parseColor(v("--hub-land-focus", "#eef4ff"), "#eef4ff"),
        border: parseColor(v("--hub-border", "#d6d6d0"), "#d6d6d0"),
        bg: parseColor(v("--hub-bg", "#f5f5f7"), "#f5f5f7"),
        surface: parseColor(v("--hub-surface", "#ffffff"), "#ffffff"),
        ink: parseColor(v("--hub-ink", "#1d1d1f"), "#1d1d1f"),
        ink3: parseColor(v("--hub-ink-3", "#6e6e73"), "#6e6e73"),
        font: v("--hub-font", "system-ui,sans-serif"),
        tone: {
          blue: parseColor(v("--hub-blue", "#0071e3"), "#0071e3"),
          green: parseColor(v("--hub-green", "#28a745"), "#28a745"),
          amber: parseColor(v("--hub-amber", "#e08a00"), "#e08a00"),
          red: parseColor(v("--hub-red", "#e5383b"), "#e5383b"),
          teal: parseColor(v("--hub-teal", "#0a9fb5"), "#0a9fb5")
        }
      };
      t.dark = lum(t.water) < 0.3;
      t.black = [0, 0, 0, 1];
      t.white = [255, 255, 255, 1];
      T = t; themeVer++; baseDirty = true;
      labelW.clear();
    }
    let themeRaf = 0;
    function themeChanged() {
      if (themeRaf || destroyed) return;
      themeRaf = requestAnimationFrame(function () { themeRaf = 0; if (!destroyed) { readTheme(); schedule(); } });
    }
    if (window.MutationObserver) {
      const mo = new MutationObserver(themeChanged);
      mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class", "style"] });
      cleanups.push(function () { mo.disconnect(); });
    }
    cleanups.push(onMq(mq("(prefers-color-scheme: dark)"), themeChanged));
    cleanups.push(onMq(reduceMq, function () { reduce = !!(reduceMq && reduceMq.matches); }));

    /* ── size ── */
    function resize() {
      const w = root.clientWidth, h = root.clientHeight;
      const d = Math.min(window.devicePixelRatio || 1, 2);
      if (w === cssW && h === cssH && d === dpr) return false;
      cssW = w; cssH = h; dpr = d;
      const bw = Math.max(1, Math.round(w * d)), bh = Math.max(1, Math.round(h * d));
      cvBase.width = bw; cvBase.height = bh; cvOver.width = bw; cvOver.height = bh;
      shadowCv.width = Math.max(1, Math.ceil(w / 6)); shadowCv.height = Math.max(1, Math.ceil(h / 6));
      const nw = w < NARROW;
      if (nw !== narrow) { narrow = nw; root.classList.toggle("hm3-narrow", nw); }
      computeView(); baseDirty = true;
      return true;
    }
    let ro = null;
    if (window.ResizeObserver) {
      ro = new ResizeObserver(function () { if (resize()) schedule(); });
      ro.observe(root);
      cleanups.push(function () { ro.disconnect(); });
    }
    function onWinResize() { if (resize()) schedule(); }
    window.addEventListener("resize", onWinResize);
    cleanups.push(function () { window.removeEventListener("resize", onWinResize); });

    /* ── camera ── */
    function baseScale() { return Math.min((cssW || 300) / proj.W, (cssH || 300) / proj.H) * FIT; }
    function computeView() {
      V.W = cssW || 300; V.H = cssH || 300;
      V.s = baseScale() * cam.zoom;
      const b = cam.bearing * DEG, p = cam.pitch * DEG;
      V.cb = Math.cos(b); V.sb = Math.sin(b); V.cp = Math.cos(p); V.sp = Math.sin(p);
      V.D = (V.H / 2) / Math.tan(FOV / 2);
      V.cx = V.W / 2; V.cy = V.H / 2;
      const yb = V.H / 2 + 80;      // ground just below the bottom edge: nothing nearer is drawn
      V.ryLim = (yb * V.D) / (V.D * V.cp + yb * V.sp);
      const lz = Math.log2(Math.max(1, cam.zoom));
      V.hTop = clamp(3.8 + 1.9 * lz, 3.8, 11);
      V.hF = V.hTop * 1.75 + 1;
      V.ps = clamp(0.9 + 0.06 * lz, 0.9, 1.14);
    }
    function clampCam() {
      cam.zoom = clamp(cam.zoom, MIN_ZOOM, MAX_ZOOM);
      cam.pitch = clamp(cam.pitch, 0, MAX_PITCH);
      cam.bearing = ((cam.bearing % 360) + 540) % 360 - 180;
      cam.x = clamp(cam.x, 0, proj.W);
      cam.y = clamp(cam.y, proj.H * 0.02, proj.H * 0.98);
    }
    function changed() { clampCam(); computeView(); schedule(); }
    const tmp = [0, 0, 0, 0];
    function project(wx, wy, h, out) {
      const dx = (wx - cam.x) * V.s, dy = (wy - cam.y) * V.s;
      const rx = dx * V.cb + dy * V.sb, ry = -dx * V.sb + dy * V.cb;
      const Z = Math.max(1, V.D - ry * V.sp - h * V.cp), k = V.D / Z;
      out[0] = V.cx + rx * k; out[1] = V.cy + (ry * V.cp - h * V.sp) * k; out[2] = k; out[3] = ry;
      return out;
    }
    function unproject(sx, sy, h) {
      h = h || 0;
      const X = sx - V.cx, Y = sy - V.cy;
      const den = Math.max(1e-3, V.D * V.cp + Y * V.sp);
      const ry = (Y * V.D - Y * h * V.cp + h * V.D * V.sp) / den;
      const Z = V.D - ry * V.sp - h * V.cp;
      const rx = X * Z / V.D;
      const dx = rx * V.cb - ry * V.sb, dy = rx * V.sb + ry * V.cb;
      return [cam.x + dx / V.s, cam.y + dy / V.s];
    }
    function camKey() {
      return cam.x.toFixed(4) + "|" + cam.y.toFixed(4) + "|" + cam.zoom.toFixed(5) + "|" + cam.pitch.toFixed(3) + "|" +
        cam.bearing.toFixed(3) + "|" + cssW + "x" + cssH + "@" + dpr + "|" + themeVer + "|" + worldVer + "|" + dataVer;
    }

    /* ── animations: camera flights, inertia ── */
    function stopMotion() {
      if (anim && anim.resolve) anim.resolve(false);
      anim = null; inertia = null;
    }
    function flyCam(to, dur, around) {
      stopMotion();
      const from = { x: cam.x, y: cam.y, zoom: cam.zoom, pitch: cam.pitch, bearing: cam.bearing };
      const tgt = {
        x: to.x != null ? to.x : from.x, y: to.y != null ? to.y : from.y,
        zoom: to.zoom != null ? clamp(to.zoom, MIN_ZOOM, MAX_ZOOM) : from.zoom,
        pitch: to.pitch != null ? clamp(to.pitch, 0, MAX_PITCH) : from.pitch,
        bearing: to.bearing != null ? to.bearing : from.bearing
      };
      let db = ((tgt.bearing - from.bearing) % 360 + 540) % 360 - 180;
      return new Promise(function (resolve) {
        if (reduce || !dur || dur <= 0) {
          if (around) zoomAround(around[0], around[1], tgt.zoom);
          else { cam.x = tgt.x; cam.y = tgt.y; cam.zoom = tgt.zoom; }
          cam.pitch = tgt.pitch; cam.bearing = from.bearing + db;
          changed(); resolve(true); return;
        }
        // a long jump zooms out a little on the way (screen distance at the smaller zoom)
        const distPx = Math.hypot(tgt.x - from.x, tgt.y - from.y) * baseScale() * Math.min(from.zoom, tgt.zoom);
        const bump = around ? 0 : Math.max(0, Math.log2(distPx / (V.W * 0.8))) * 0.55;
        anim = { from: from, to: tgt, db: db, t0: now(), dur: dur, bump: bump, around: around, resolve: resolve };
        schedule();
      });
    }
    function stepAnim(t) {
      let active = false;
      if (anim) {
        const a = anim, p = clamp((t - a.t0) / a.dur, 0, 1), e = easeInOut(p);
        const lz = lerp(Math.log2(a.from.zoom), Math.log2(a.to.zoom), e) - a.bump * Math.sin(Math.PI * e);
        if (a.around) {
          zoomAround(a.around[0], a.around[1], Math.pow(2, lz));
        } else {
          cam.x = lerp(a.from.x, a.to.x, e); cam.y = lerp(a.from.y, a.to.y, e); cam.zoom = Math.pow(2, lz);
        }
        cam.pitch = lerp(a.from.pitch, a.to.pitch, e);
        cam.bearing = a.from.bearing + a.db * e;
        clampCam(); computeView();
        if (p >= 1) { anim = null; if (a.resolve) a.resolve(true); } else active = true;
      }
      if (inertia) {
        const dt = Math.min(48, t - inertia.t);
        inertia.t = t;
        cam.x += inertia.vx * dt; cam.y += inertia.vy * dt;
        const f = Math.exp(-dt / 260);
        inertia.vx *= f; inertia.vy *= f;
        clampCam(); computeView();
        if (Math.hypot(inertia.vx, inertia.vy) * V.s < 0.02) inertia = null; else active = true;
      }
      return active;
    }
    function zoomAround(sx, sy, z) {
      const g0 = unproject(sx, sy, 0);
      cam.zoom = clamp(z, MIN_ZOOM, MAX_ZOOM);
      computeView();
      const g1 = unproject(sx, sy, 0);
      cam.x += g0[0] - g1[0]; cam.y += g0[1] - g1[1];
      clampCam(); computeView();
    }
    function zoomBy(f, sx, sy) {
      userMoved = true;
      const z = clamp((anim && anim.to ? anim.to.zoom : cam.zoom) * f, MIN_ZOOM, MAX_ZOOM);
      flyCam({ zoom: z }, 280, [sx != null ? sx : V.cx, sy != null ? sy : V.cy]);
    }
    function toggle3D() {
      userMoved = true;
      if (cam.pitch > 5) { pitch3D = cam.pitch; flyCam({ pitch: 0 }, 550); }
      else flyCam({ pitch: pitch3D || PITCH_3D }, 550);
    }

    /* ── land geometry: projected, clipped and decimated once per camera, as Path2D ──
       The outlines are projected once, on the ground (h = 0). The slab's top and its middle
       layer are the same path under an affine transform: for a plane lifted by h the
       perspective shift is linear in screen y (exact to first order; under ~1 px for the
       few pixels the slabs are lifted). One projection per frame instead of one per layer. */
    const G = { n: 0, pts: 0, path: null, fpath: null, key: "", tmp: new Float64Array(8192) };
    function visibleBox() {
      const m = 90, c = [[-m, -m], [V.W + m, -m], [V.W + m, V.H + m], [-m, V.H + m], [V.W / 2, -m]];
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      for (let i = 0; i < c.length; i++) {
        const g = unproject(c[i][0], c[i][1], 0);
        if (g[0] < x0) x0 = g[0]; if (g[0] > x1) x1 = g[0]; if (g[1] < y0) y0 = g[1]; if (g[1] > y1) y1 = g[1];
      }
      return [x0, y0, x1, y1];
    }
    function buildGeom() {
      if (!world) { G.n = 0; G.path = G.fpath = null; return; }
      // detail: the coarsest outline whose error stays under ~1 px (2 px while the map moves)
      const budget = moving ? 2 : 1;
      let lv = 0;
      for (let l = LOD_TOL.length - 1; l > 0; l--) if (LOD_TOL[l] * V.s <= budget) { lv = l; break; }
      const key = camKey() + "|" + Array.from(focus).join(",") + "|" + lv + (moving ? "m" : "");
      if (key === G.key) return;
      G.key = key;
      const bb = visibleBox(), lim = V.ryLim, tol = moving ? 0.9 : 0.6;
      const s = V.s, cb = V.cb, sb = V.sb, cxw = cam.x, cyw = cam.y, D = V.D, sp = V.sp, cp = V.cp;
      const path = new Path2D();
      let fpath = null, n = 0, total = 0;
      const rings = world.rings, countries = world.countries;
      for (let r = 0; r < rings.length; r++) {
        const R = rings[r], b = R.bb;
        if (b[2] < bb[0] || b[0] > bb[2] || b[3] < bb[1] || b[1] > bb[3]) continue;
        if ((b[2] - b[0]) * s < 0.8 && (b[3] - b[1]) * s < 0.8) continue;   // smaller than a pixel
        const pts = R.lod[lv] || R.lod[lv - 1] || R.pts, np = pts.length >> 1;
        if (G.tmp.length < np * 2 + 8) G.tmp = new Float64Array(np * 2 + 64);
        const out = G.tmp;
        let w = 0, lx = 1e9, ly = 1e9;
        // the last point first: it opens the closing edge of the near-plane clip
        let dx = (pts[np * 2 - 2] - cxw) * s, dy = (pts[np * 2 - 1] - cyw) * s;
        let prx = dx * cb + dy * sb, pry = -dx * sb + dy * cb, pin = pry <= lim;
        for (let i = 0; i <= np; i++) {
          let rx, ry, cin;
          if (i < np) {
            dx = (pts[i * 2] - cxw) * s; dy = (pts[i * 2 + 1] - cyw) * s;
            rx = dx * cb + dy * sb; ry = -dx * sb + dy * cb; cin = ry <= lim;
          } else { rx = prx; ry = pry; cin = pin; }
          for (let pass = 0; pass < 2; pass++) {
            let ex, ey;
            if (pass === 0) { if (i === np || cin === pin) continue; const t = (lim - pry) / (ry - pry); ex = prx + (rx - prx) * t; ey = lim; }
            else { if (i === np || !cin) continue; ex = rx; ey = ry; }
            const k = D / (D - ey * sp), X = ex * k, Y = ey * cp * k;
            if (Math.abs(X - lx) + Math.abs(Y - ly) >= tol) { out[w++] = X; out[w++] = Y; lx = X; ly = Y; }
          }
          if (i < np) { prx = rx; pry = ry; pin = cin; }
        }
        const cnt = w >> 1;
        if (cnt < 3) continue;
        const foc = focus.has(countries[R.ci].cc);
        if (foc && !fpath) fpath = new Path2D();
        path.moveTo(out[0], out[1]);
        for (let j = 2; j < w; j += 2) path.lineTo(out[j], out[j + 1]);
        path.closePath();
        if (foc) {
          fpath.moveTo(out[0], out[1]);
          for (let j = 2; j < w; j += 2) fpath.lineTo(out[j], out[j + 1]);
          fpath.closePath();
        }
        n++; total += cnt;
      }
      G.n = n; G.pts = total; G.path = n ? path : null; G.fpath = fpath;
    }
    // the ground-projected paths, lifted to height h (see above)
    function atHeight(ctx, h, scale, ox, oy) {
      const ax = 1 + h * V.cp / V.D, ay = 1 - h * (V.sp * V.sp / Math.max(0.05, V.cp) - V.cp) / V.D;
      ctx.setTransform(scale * ax, 0, 0, scale * ay, scale * (V.cx + (ox || 0)), scale * (V.cy - h * V.sp + (oy || 0)));
    }

    // a straight line on the ground (straight lines stay straight in perspective), clipped to the near limit
    function groundLine(ctx, x0, y0, x1, y1, h) {
      const s = V.s;
      let dx = (x0 - cam.x) * s, dy = (y0 - cam.y) * s;
      let ax = dx * V.cb + dy * V.sb, ay = -dx * V.sb + dy * V.cb;
      dx = (x1 - cam.x) * s; dy = (y1 - cam.y) * s;
      let bx = dx * V.cb + dy * V.sb, by = -dx * V.sb + dy * V.cb;
      const lim = V.ryLim;
      if (ay > lim && by > lim) return;
      if (ay > lim) { const t = (lim - ay) / (by - ay); ax += (bx - ax) * t; ay = lim; }
      if (by > lim) { const t = (lim - by) / (ay - by); bx += (ax - bx) * t; by = lim; }
      const ka = V.D / (V.D - ay * V.sp - h * V.cp), kb = V.D / (V.D - by * V.sp - h * V.cp);
      ctx.moveTo(V.cx + ax * ka, V.cy + (ay * V.cp - h * V.sp) * ka);
      ctx.lineTo(V.cx + bx * kb, V.cy + (by * V.cp - h * V.sp) * kb);
    }

    // a polygon lying on the ground (world units), clipped to the near limit
    function groundPoly(ctx, w, h) {
      const n = w.length >> 1, lim = V.ryLim, P = [];
      let px = 0, py = 0, pin = false;
      for (let i = 0; i <= n; i++) {
        const j = (i % n) * 2, dx = (w[j] - cam.x) * V.s, dy = (w[j + 1] - cam.y) * V.s;
        const rx = dx * V.cb + dy * V.sb, ry = -dx * V.sb + dy * V.cb, cin = ry <= lim;
        if (i > 0) {
          if (cin !== pin) { const t = (lim - py) / (ry - py); P.push(px + (rx - px) * t, lim); }
          if (cin && i < n) P.push(rx, ry);
        } else if (cin) P.push(rx, ry);
        px = rx; py = ry; pin = cin;
      }
      ctx.beginPath();
      for (let k = 0; k < P.length; k += 2) {
        const kk = V.D / (V.D - P[k + 1] * V.sp - h * V.cp);
        const X = V.cx + P[k] * kk, Y = V.cy + (P[k + 1] * V.cp - h * V.sp) * kk;
        if (k) ctx.lineTo(X, Y); else ctx.moveTo(X, Y);
      }
      ctx.closePath();
    }

    /* ── base layer: water, graticule, shadow, clay land ── */
    function renderBase() {
      const ctx = bctx, W = V.W, H = V.H;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const g = ctx.createLinearGradient(0, 0, 0, H);
      g.addColorStop(0, css(T.dark ? T.water2 : T.water));
      g.addColorStop(1, css(T.dark ? T.water : T.water2));
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);

      // graticule: every 20° of longitude and latitude, inside the map's extent
      ctx.beginPath();
      for (let lon = -180; lon <= 180; lon += 20) { const x = proj.x(lon); groundLine(ctx, x, proj.y(77.5), x, proj.y(-55.5), 0); }
      for (let lat = -40; lat <= 60; lat += 20) { const y = proj.y(lat); groundLine(ctx, 0, y, proj.W, y, 0); }
      ctx.strokeStyle = css(T.dark ? T.white : T.ink, T.dark ? 0.045 : 0.05);
      ctx.lineWidth = 1;
      ctx.stroke();

      buildGeom();
      if (G.path) {
        const pf = 0.45 + 0.55 * (cam.pitch / MAX_PITCH);
        // soft drop shadow: the footprint drawn at 1/6 size and scaled up (the scaling is the blur)
        const q = shadowCv.width / W;
        sctx.setTransform(1, 0, 0, 1, 0, 0);
        sctx.clearRect(0, 0, shadowCv.width, shadowCv.height);
        atHeight(sctx, 0, q, 3 * pf, 7 * pf);
        sctx.fillStyle = T.dark ? "rgba(0,0,0,.6)" : "rgba(52,66,92,.24)";
        sctx.fill(G.path);
        ctx.imageSmoothingEnabled = true;
        ctx.drawImage(shadowCv, 0, 0, shadowCv.width / q, shadowCv.height / q);

        // slab: side walls (the footprint at the ground), then the top
        ctx.lineJoin = "round";
        ctx.fillStyle = css(T.side);
        atHeight(ctx, 0, dpr);
        ctx.fill(G.path);
        if (!moving) {   // the coast line at the foot of the wall (a detail the eye misses while moving)
          ctx.strokeStyle = css(T.edge, T.dark ? 0.9 : 0.75);
          ctx.lineWidth = 0.7;
          ctx.stroke(G.path);
        }
        if (V.hTop * V.sp > 4.5) {   // tall walls: a middle layer closes the gaps at sharp corners
          atHeight(ctx, V.hTop * 0.5, dpr);
          ctx.fill(G.path);
        }
        atHeight(ctx, V.hTop, dpr);
        ctx.fillStyle = css(T.land);
        ctx.fill(G.path);
        ctx.strokeStyle = css(T.border, T.dark ? 0.95 : 0.9);
        ctx.lineWidth = 0.65;
        ctx.stroke(G.path);

        // countries in focus: tinted and raised a little
        if (G.fpath) {
          ctx.fillStyle = css(mix(T.side, T.tone.blue, T.dark ? 0.12 : 0.1));
          ctx.fill(G.fpath);
          atHeight(ctx, (V.hTop + V.hF) / 2, dpr);
          ctx.fill(G.fpath);
          atHeight(ctx, V.hF, dpr);
          ctx.fillStyle = css(T.focus);
          ctx.fill(G.fpath);
          ctx.strokeStyle = css(T.tone.blue, T.dark ? 0.45 : 0.32);
          ctx.lineWidth = 0.8;
          ctx.stroke(G.fpath);
        }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      }
      // the outline stops at lat_top: fade the land into the water over the last few degrees
      const yN = proj.y(Math.min(85, proj.lat(0) - 0.01)), yF = proj.y(proj.lat(0) - 9);
      project(cam.x, yN, V.hTop, tmp); const nx = tmp[0], ny = tmp[1];
      project(cam.x, yF, V.hTop, tmp);
      if (isFinite(nx + ny + tmp[0] + tmp[1]) && Math.hypot(tmp[0] - nx, tmp[1] - ny) > 0.5) {
        const pg = ctx.createLinearGradient(nx, ny, tmp[0], tmp[1]);
        pg.addColorStop(0, css(T.dark ? T.water2 : T.water, 1));
        pg.addColorStop(0.25, css(T.dark ? T.water2 : T.water, 0.85));
        pg.addColorStop(1, css(T.dark ? T.water2 : T.water, 0));
        ctx.fillStyle = pg;
        groundPoly(ctx, [-proj.W, -proj.H, proj.W * 2, -proj.H, proj.W * 2, yF, -proj.W, yF], V.hTop);
        ctx.fill();
      }
      // atmospheric haze towards the far edge in 3D
      if (cam.pitch > 2) {
        const a = (cam.pitch / MAX_PITCH) * (T.dark ? 0.5 : 0.6);
        const hz = ctx.createLinearGradient(0, 0, 0, H * 0.42);
        hz.addColorStop(0, css(T.dark ? T.water2 : T.bg, a));
        hz.addColorStop(1, css(T.dark ? T.water2 : T.bg, 0));
        ctx.fillStyle = hz;
        ctx.fillRect(0, 0, W, H * 0.42);
      }
    }

    /* ── overlay: routes, pins, labels ── */
    const labelW = new Map();
    function measure(ctx, text, font) {
      const k = font + "|" + text;
      let w = labelW.get(k);
      if (w == null) { ctx.font = font; w = ctx.measureText(text).width; if (labelW.size > 2000) labelW.clear(); labelW.set(k, w); }
      return w;
    }
    function pinHeight(p) { return p.cc && focus.has(p.cc) ? V.hF : V.hTop; }

    function drawRoutes(ctx, t) {
      let active = false;
      for (let i = 0; i < routes.length; i++) {
        const R = routes[i];
        let prog = 1;
        if (!reduce) {
          const p = clamp((t - (routeBorn[R.id] || 0)) / 1300, 0, 1);
          prog = easeInOut(p);
          if (p < 1) active = true;
        }
        const Lcut = R.L * prog, col = T.tone[R.tone];
        // travelled (solid, with a darker casing)
        const L1 = Math.min(Lcut, R.Ls);
        if (L1 > 0) {
          ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.setLineDash([]);
          routePath(ctx, R, 0, L1);
          ctx.strokeStyle = css(mix(col, T.black, 0.28), 0.9); ctx.lineWidth = 7.5; ctx.stroke();
          ctx.strokeStyle = css(col); ctx.lineWidth = 5; ctx.stroke();
        }
        // planned (dashed)
        if (Lcut > R.Ls) {
          routePath(ctx, R, R.Ls, Lcut);
          ctx.lineCap = "round"; ctx.setLineDash([7, 8]);
          ctx.strokeStyle = css(T.dark ? T.water2 : T.white, 0.85); ctx.lineWidth = 6; ctx.stroke();
          ctx.strokeStyle = css(col, 0.9); ctx.lineWidth = 3.4; ctx.stroke();
          ctx.setLineDash([]);
        }
        // the given points, as small stops; the reached one a little stronger
        for (let k = 0; k < R.stops.length; k++) {
          const st = R.stops[k];
          if (st.L > Lcut + 0.001) continue;
          project(st.x, st.y, V.hTop, tmp);
          if (tmp[3] > V.ryLim || tmp[0] < -20 || tmp[0] > V.W + 20 || tmp[1] < -20 || tmp[1] > V.H + 20) continue;
          const big = k === R.reached || k === 0 || k === R.stops.length - 1;
          const r = k === R.reached ? 5.2 : big ? 4.2 : 3.2;
          ctx.beginPath(); ctx.arc(tmp[0], tmp[1], r, 0, Math.PI * 2);
          ctx.fillStyle = k === R.reached ? css(col) : css(T.surface);
          ctx.fill();
          ctx.lineWidth = k === R.reached ? 2.2 : 1.8;
          ctx.strokeStyle = k === R.reached ? css(T.white) : css(col);
          ctx.stroke();
        }
      }
      return active;
    }
    function routePath(ctx, R, L0, L1) {
      ctx.beginPath();
      const h = V.hTop, P = R.pts, C = R.cum, lim = V.ryLim;
      let pen = false;
      for (let i = 0; i + 1 < C.length; i++) {
        const a = C[i], b = C[i + 1];
        if (b < L0 || a > L1) continue;
        const span = b - a || 1e-9;
        const t0 = clamp((L0 - a) / span, 0, 1), t1 = clamp((L1 - a) / span, 0, 1);
        const x0 = lerp(P[i * 2], P[i * 2 + 2], t0), y0 = lerp(P[i * 2 + 1], P[i * 2 + 3], t0);
        const x1 = lerp(P[i * 2], P[i * 2 + 2], t1), y1 = lerp(P[i * 2 + 1], P[i * 2 + 3], t1);
        project(x0, y0, h, tmp);
        if (tmp[3] > lim) { pen = false; continue; }
        const ax = tmp[0], ay = tmp[1];
        project(x1, y1, h, tmp);
        if (tmp[3] > lim) { pen = false; continue; }
        if (!pen) ctx.moveTo(ax, ay);
        ctx.lineTo(tmp[0], tmp[1]);
        pen = true;
      }
    }

    function prio(a, b) {
      if (a.sel !== b.sel) return a.sel ? -1 : 1;
      const ca = a.p.count == null ? -1 : a.p.count, cb = b.p.count == null ? -1 : b.p.count;
      if (ca !== cb) return cb - ca;
      const sa = SEVERITY[a.p.tone] || 0, sb2 = SEVERITY[b.p.tone] || 0;
      if (sa !== sb2) return sb2 - sa;
      return a.p.index - b.p.index;
    }
    function layout() {
      const vis = [];
      for (let i = 0; i < pins.length; i++) {
        const p = pins[i];
        project(p.x, p.y, pinHeight(p), tmp);
        if (tmp[3] > V.ryLim || tmp[0] < -40 || tmp[0] > V.W + 40 || tmp[1] < -20 || tmp[1] > V.H + 70) continue;
        vis.push({ p: p, x: tmp[0], y: tmp[1], k: tmp[2], sel: p.id === selPinId });
      }
      vis.sort(prio);
      const out = [], thr = 30 * V.ps;
      for (let i = 0; i < vis.length; i++) {
        const a = vis[i];
        let host = null;
        if (!a.sel) {
          for (let j = 0; j < out.length; j++) {
            const it = out[j];
            if (it.sel) continue;
            if (Math.abs(it.x - a.x) < thr && Math.abs(it.y - a.y) < thr && Math.hypot(it.x - a.x, it.y - a.y) < thr) { host = it; break; }
          }
        }
        if (host) { host.pins.push(a.p); continue; }
        out.push({ key: "p:" + a.p.id, lead: a.p, pins: [a.p], x: a.x, y: a.y, k: a.k, sel: a.sel });
      }
      for (let i = 0; i < out.length; i++) {
        const it = out[i];
        it.cluster = it.pins.length > 1;
        if (it.cluster) {
          it.key = "c:" + it.lead.id;
          let sum = 0, any = false, worst = it.lead.tone;
          for (let j = 0; j < it.pins.length; j++) {
            const q = it.pins[j];
            if (q.count != null) { sum += q.count; any = true; }
            if ((SEVERITY[q.tone] || 0) > (SEVERITY[worst] || 0)) worst = q.tone;
          }
          it.count = any ? sum : it.pins.length;
          it.tone = worst;
          it.label = it.lead.label ? it.lead.label + " +" + (it.pins.length - 1) : it.pins.length + " places";
        } else {
          it.count = it.lead.count; it.tone = it.lead.tone; it.label = it.lead.label;
        }
        if (selCluster && it.cluster && it.pins.indexOf(selCluster.lead) >= 0) it.sel = true;
      }
      return out;
    }

    function teardrop(ctx, cx, cy, R, tipY) {
      const d = tipY - cy, al = Math.acos(clamp(R / d, -1, 1));
      ctx.beginPath();
      ctx.moveTo(cx, tipY);
      ctx.arc(cx, cy, R, Math.PI / 2 + al, Math.PI * 2.5 - al, false);
      ctx.closePath();
    }
    function drawPin(ctx, it, s, alpha, dropDy) {
      const col = T.tone[it.tone] || T.tone.blue;
      const r = (it.cluster ? 14.5 : 13) * s, ring = 2.3 * s, tail = 7.5 * s;
      const ax = it.x, ay = it.y;
      const cy = ay - tail - r + dropDy;
      ctx.globalAlpha = alpha;
      // contact shadow on the ground
      ctx.fillStyle = "rgba(0,0,0," + (T.dark ? 0.4 : 0.2) + ")";
      ctx.beginPath(); ctx.ellipse(ax, ay, 5.5 * s, 2.1 * s, 0, 0, Math.PI * 2); ctx.fill();
      if (it.sel) {
        ctx.fillStyle = css(col, T.dark ? 0.22 : 0.16);
        ctx.beginPath(); ctx.ellipse(ax, ay, 19 * s, 19 * s * Math.max(0.35, V.cp), 0, 0, Math.PI * 2); ctx.fill();
      }
      if (it.cluster) {   // a second disc behind: several places here
        ctx.save();
        ctx.shadowColor = "rgba(0,0,0,.18)"; ctx.shadowBlur = 5 * s; ctx.shadowOffsetY = 1.5 * s;
        ctx.beginPath(); ctx.arc(ax + 6.5 * s, cy - 4.5 * s, r * 0.92 + ring, 0, Math.PI * 2);
        ctx.fillStyle = css(T.white); ctx.fill();
        ctx.restore();
        ctx.beginPath(); ctx.arc(ax + 6.5 * s, cy - 4.5 * s, r * 0.92, 0, Math.PI * 2);
        ctx.fillStyle = css(mix(col, T.white, 0.35)); ctx.fill();
      }
      ctx.save();
      ctx.shadowColor = T.dark ? "rgba(0,0,0,.55)" : "rgba(20,30,50,.28)";
      ctx.shadowBlur = 9 * s; ctx.shadowOffsetY = 2.5 * s;
      teardrop(ctx, ax, cy, r + ring, ay + dropDy);
      ctx.fillStyle = css(T.white);
      ctx.fill();
      ctx.restore();
      teardrop(ctx, ax, cy, r, ay + dropDy - ring * 1.7);
      const gr = ctx.createLinearGradient(0, cy - r, 0, cy + r);
      gr.addColorStop(0, css(mix(col, T.white, 0.12)));
      gr.addColorStop(1, css(mix(col, T.black, 0.06)));
      ctx.fillStyle = gr;
      ctx.fill();
      const txt = it.count != null ? fmtCount(it.count) : "";
      if (txt) {
        let fs = 12.5 * s;
        let font = "700 " + fs.toFixed(1) + "px " + T.font;
        let w = measure(ctx, txt, font);
        if (w > r * 2 - 6 * s) { fs = fs * (r * 2 - 6 * s) / w; font = "700 " + fs.toFixed(1) + "px " + T.font; }
        ctx.font = font; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillStyle = "#fff";
        ctx.fillText(txt, ax, cy + 0.5 * s);
      } else {
        ctx.beginPath(); ctx.arc(ax, cy, r * 0.34, 0, Math.PI * 2); ctx.fillStyle = "#fff"; ctx.fill();
      }
      ctx.globalAlpha = 1;
      return { x: ax, y: cy, r: r + ring };
    }

    function renderOver(t) {
      const ctx = octx;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cvOver.width, cvOver.height);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      let active = drawRoutes(ctx, t);
      items = layout();
      // far to near, the selected one last
      const order = items.slice().sort(function (a, b) { return (a.sel - b.sel) || (a.y - b.y); });
      heads = [];
      for (let i = 0; i < order.length; i++) {
        const it = order[i];
        let s = V.ps * clamp(Math.sqrt(it.k), 0.82, 1.12);
        if (it.sel) {
          const p = reduce ? 1 : clamp((t - selT0) / 320, 0, 1);
          if (p < 1) active = true;
          s *= 1 + 0.24 * easeOutBack(p);
        }
        let dy = 0, al = 1;
        if (!reduce) {
          const b0 = born[it.lead.id] || 0, p = clamp((t - b0) / 520, 0, 1);
          if (p < 1) { active = true; dy = -(1 - easeOutBack(p)) * 34 * s; al = clamp(p * 2.2, 0, 1); }
        }
        const h = drawPin(ctx, it, s, al, dy);
        it.hx = h.x; it.hy = h.y; it.hr = h.r; it.s = s; it.alpha = al;
        heads.push(it);
      }
      // labels, by priority: every pin keeps its label; one that would overlap a pin or another
      // label moves to the next free spot (below, right, left, above, then further down)
      const placed = [];
      const byPrio = items.slice().sort(function (a, b) {
        if (a.sel !== b.sel) return a.sel ? -1 : 1;
        return (b.count || 0) - (a.count || 0);
      });
      for (let i = 0; i < byPrio.length; i++) {
        const it = byPrio[i];
        if (!it.label) continue;
        const fs = (it.sel ? 12.5 : 11.5) * Math.min(1.08, Math.max(0.92, it.s / V.ps * 0.95));
        const font = (it.sel ? "700 " : "600 ") + fs.toFixed(1) + "px " + T.font;
        let text = String(it.label);
        let w = measure(ctx, text, font);
        if (w > 170) { while (text.length > 4 && measure(ctx, text + "…", font) > 170) text = text.slice(0, -1); text += "…"; w = measure(ctx, text, font); }
        const pw = w + 14, ph = fs + 9;
        const hr = it.hr || 14, cands = [
          [it.x - pw / 2, it.y + 4],
          [it.hx + hr + 3, it.hy - ph / 2],
          [it.hx - hr - 3 - pw, it.hy - ph / 2],
          [it.x - pw / 2, it.hy - hr - 3 - ph]
        ];
        for (let k = 1; k <= 6; k++) cands.push([it.x - pw / 2, it.y + 4 + k * (ph + 3)]);
        let rx = 0, ry = 0, found = false;
        for (let c = 0; c < cands.length && !found; c++) {
          rx = clamp(cands[c][0], 2, Math.max(2, V.W - pw - 2)); ry = clamp(cands[c][1], 2, Math.max(2, V.H - ph - 2));
          let hit = false;
          for (let j = 0; j < placed.length && !hit; j++) { const q = placed[j]; hit = rx < q[2] && rx + pw > q[0] && ry < q[3] && ry + ph > q[1]; }
          for (let j = 0; j < heads.length && !hit; j++) {
            const hd = heads[j];
            if (hd === it) continue;
            const nx = clamp(hd.hx, rx, rx + pw), ny = clamp(hd.hy, ry, ry + ph);
            hit = Math.hypot(nx - hd.hx, ny - hd.hy) < hd.hr;
          }
          found = !hit;
        }
        if (!found) { rx = clamp(cands[0][0], 2, Math.max(2, V.W - pw - 2)); ry = clamp(cands[0][1], 2, Math.max(2, V.H - ph - 2)); }
        placed.push([rx, ry, rx + pw, ry + ph]);
        ctx.globalAlpha = it.alpha;
        ctx.save();
        ctx.shadowColor = T.dark ? "rgba(0,0,0,.45)" : "rgba(20,30,50,.14)";
        ctx.shadowBlur = 6; ctx.shadowOffsetY = 1;
        roundRect(ctx, rx, ry, pw, ph, ph / 2);
        ctx.fillStyle = css(T.surface, T.dark ? 0.9 : 0.94);
        ctx.fill();
        ctx.restore();
        ctx.font = font; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillStyle = css(T.ink);
        ctx.fillText(text, rx + pw / 2, ry + ph / 2 + 0.5);
        ctx.globalAlpha = 1;
      }
      syncButtons();
      return active;
    }
    function roundRect(ctx, x, y, w, h, r) {
      ctx.beginPath();
      ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r);
      ctx.lineTo(x + w, y + h - r); ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
      ctx.lineTo(x + r, y + h); ctx.arcTo(x, y + h, x, y + h - r, r);
      ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r);
      ctx.closePath();
    }

    /* ── focusable buttons over the pins (keyboard and screen readers) ── */
    const btns = new Map();
    function syncButtons() {
      const seen = new Set();
      const list = items.slice().sort(function (a, b) { return a.lead.index - b.lead.index; });
      let prev = null;
      for (let i = 0; i < list.length; i++) {
        const it = list[i];
        seen.add(it.key);
        let b = btns.get(it.key);
        if (!b) {
          b = el("button", "hm3-pinbtn"); b.type = "button"; b.tabIndex = 0;
          b._key = it.key;
          b.addEventListener("click", function (e) { e.stopPropagation(); const cur = findItem(b._key); if (cur) activate(cur, true); });
          b.addEventListener("focus", function () { const cur = findItem(b._key); if (cur) live.textContent = ""; });
          btns.set(it.key, b);
          b._st = {};
        }
        // keep the DOM order the same as the data order (stable Tab sequence)
        const want = prev ? prev.nextSibling : pinLayer.firstChild;
        if (want !== b) pinLayer.insertBefore(b, want);
        prev = b;
        const size = Math.round(it.hr * 2 + 2);
        const label = tipFor(it);
        const tf = "translate(" + Math.round(it.hx - size / 2) + "px," + Math.round(it.hy - size / 2) + "px)";
        const st = b._st;
        if (st.tf !== tf) { b.style.transform = tf; st.tf = tf; }
        if (st.size !== size) { b.style.width = size + "px"; b.style.height = size + "px"; st.size = size; }
        if (st.label !== label) { b.setAttribute("aria-label", label); st.label = label; }
        const pressed = it.sel ? "true" : "false";
        if (st.pressed !== pressed) { b.setAttribute("aria-pressed", pressed); st.pressed = pressed; }
      }
      btns.forEach(function (b, k) {
        if (seen.has(k)) return;
        const hadFocus = document.activeElement === b;
        b.remove(); btns.delete(k);
        if (hadFocus) root.focus({ preventScroll: true });
      });
    }
    // "<label>: <count> <countLabel>" (a group lists its places after it)
    function tipFor(it) {
      const head = (it.label || it.lead.label || it.lead.id) + (it.count != null ? ": " + fmtNum(it.count) + " " + countLabel : "");
      return it.cluster ? head + " (" + it.pins.length + " places: " + it.pins.map(function (p) { return p.label || p.id; }).join(", ") + ")" : head;
    }
    function fmtNum(n) { return Math.round(n) === n ? String(n) : String(Math.round(n * 10) / 10); }
    function findItem(key) { for (let i = 0; i < items.length; i++) if (items[i].key === key) return items[i]; return null; }

    /* ── frame loop ── */
    function schedule() { if (!raf && !destroyed) raf = requestAnimationFrame(frame); }
    function frame() {
      raf = 0;
      if (destroyed || noCanvas) return;
      const t = now();
      if (!T) readTheme();
      resize();
      let active = stepAnim(t);
      moving = !!(anim || inertia || (gest && gest.moved));
      if (wasMoving && !moving) baseDirty = true;   // settle: redraw at full detail
      wasMoving = moving;
      const k = camKey();
      if (k !== lastKey) { baseDirty = true; lastKey = k; }
      const t0 = now();
      if (baseDirty) { renderBase(); baseDirty = false; }
      active = renderOver(t) || active;
      perf.push(now() - t0); if (perf.length > 600) perf.shift();
      syncControls();
      if (active) schedule();
    }
    const ctlState = {};
    function syncControls() {
      const nb = Math.round(cam.bearing * 10) / 10;
      if (ctlState.b !== nb) { needle.style.transform = "rotate(" + (-nb) + "deg)"; needle.style.transformOrigin = "8px 8px"; ctlState.b = nb; }
      const is3d = cam.pitch > 5 ? "3d" : "2d";
      if (ctlState.d !== is3d) {
        bDim.textContent = is3d === "3d" ? "2D" : "3D";
        const l = is3d === "3d" ? "Switch to 2D view" : "Switch to 3D view";
        bDim.setAttribute("aria-label", l); bDim.title = l; ctlState.d = is3d;
      }
      const zi = cam.zoom >= MAX_ZOOM - 1e-6, zo = cam.zoom <= MIN_ZOOM + 1e-6;
      if (ctlState.zi !== zi) { bIn.disabled = zi; ctlState.zi = zi; }
      if (ctlState.zo !== zo) { bOut.disabled = zo; ctlState.zo = zo; }
    }

    /* ── selection and the sheet ── */
    function emit(name) {
      const args = Array.prototype.slice.call(arguments, 1), ls = (listeners[name] || []).slice();
      for (let i = 0; i < ls.length; i++) { try { ls[i].apply(null, args); } catch (e) { setTimeout(function () { throw e; }); } }
    }
    function publicPin(p) { return p ? p.src : null; }
    function activate(it, user) {
      if (it.cluster) { openCluster(it, user); return; }
      selectPin(it.lead.id, user, true);
    }
    function selectPin(id, user, gentle) {
      if (id == null) { closeSheet(user); return true; }
      const p = pinById[id];
      if (!p) return false;
      const was = selPinId;
      selPinId = p.id; selCluster = null; selKey = "p:" + p.id;
      if (was !== p.id) selT0 = now();
      renderSheet(p, was !== p.id);
      showSheet();
      live.textContent = (p.sheet && p.sheet.title) || p.label || "";
      ensureVisible(p, gentle);
      schedule();
      if (user && was !== p.id) emit("select", publicPin(p));
      return true;
    }
    function openCluster(it, user) {
      const was = selPinId;
      selPinId = null; selCluster = { lead: it.lead, pins: it.pins.slice() }; selKey = it.key; selT0 = now();
      renderClusterSheet(selCluster);
      showSheet();
      live.textContent = it.pins.length + " places";
      schedule();
      if (user) { if (was) emit("select", null); emit("cluster", it.pins.map(publicPin)); }
    }
    function closeSheet(user) {
      const had = selPinId != null || selCluster != null;
      const was = selPinId;
      selPinId = null; selCluster = null; selKey = null;
      hideSheet();
      schedule();
      if (user && had && was != null) emit("select", null);
    }
    let hideTimer = 0;
    function showSheet() {
      clearTimeout(hideTimer);
      sheet.style.transform = "";
      if (sheet.hidden) {
        sheet.hidden = false;
        if (reduce) sheet.classList.add("hm3-show");
        else { void sheet.offsetWidth; sheet.classList.add("hm3-show"); }
      } else sheet.classList.add("hm3-show");
    }
    function hideSheet() {
      if (sheet.hidden) return;
      const hadFocus = sheet.contains(document.activeElement);
      sheet.classList.remove("hm3-show");
      clearTimeout(hideTimer);
      if (reduce) sheet.hidden = true;
      else hideTimer = setTimeout(function () { if (!sheet.classList.contains("hm3-show")) sheet.hidden = true; }, 330);
      if (hadFocus) root.focus({ preventScroll: true });
    }
    // keep the chosen pin in view and clear of the sheet
    function ensureVisible(p, gentle) {
      project(p.x, p.y, pinHeight(p), tmp);
      const sx = tmp[0], sy = tmp[1] - 24;
      let target;
      if (narrow) {
        const sh = Math.min(V.H * 0.74, sheet.offsetHeight || V.H * 0.5);
        const ok = sx > 50 && sx < V.W - 50 && sy > 70 && sy < V.H - sh - 30 && tmp[3] <= V.ryLim;
        if (ok && gentle) return;
        target = [V.W / 2, Math.max(90, (V.H - sh) * 0.55)];
      } else {
        const sw = Math.min(380, V.W - 24) + 24, shh = (sheet.offsetHeight || 300) + 24;
        const under = sx < sw && sy > V.H - shh;
        const ok = !under && sx > 60 && sx < V.W - 70 && sy > 60 && sy < V.H - 40 && tmp[3] <= V.ryLim;
        if (ok && gentle) return;
        target = [sw + (V.W - sw) / 2, V.H * 0.48];
      }
      const g = unproject(target[0], target[1], pinHeight(p));
      flyCam({ x: cam.x + (p.x - g[0]), y: cam.y + (p.y - g[1]) }, 600);
    }

    function sheetBase(title, subtitle) {
      sheet.textContent = "";
      const grab = el("div", "hm3-grab"); grab.setAttribute("aria-hidden", "true");
      sheet.appendChild(grab);
      bindGrab(grab);
      const head = el("div", "hm3-head"), tx = el("div");
      const h = el("h3", "hm3-title", title || "");
      h.id = uid("hm3-t");
      tx.appendChild(h);
      if (subtitle) tx.appendChild(el("div", "hm3-sub", subtitle));
      head.appendChild(tx);
      const x = el("button", "hm3-x"); x.type = "button"; x.innerHTML = ICON.close;
      x.setAttribute("aria-label", "Close"); x.title = "Close";
      x.addEventListener("click", function (e) { e.stopPropagation(); closeSheet(true); });
      head.appendChild(x);
      sheet.appendChild(head);
      sheet.setAttribute("aria-labelledby", h.id);
    }
    function renderSheet(p, fresh) {
      const sh = p.sheet || {};
      sheetBase(sh.title || p.label || p.id, sh.subtitle || "");
      const acts = Array.isArray(sh.actions) ? sh.actions.filter(function (a) { return a && a.label; }) : [];
      if (acts.length) {
        const row = el("div", "hm3-acts");
        acts.slice(0, 3).forEach(function (a) {
          const b = el("button", "hm3-act" + (a.primary ? " hm3-primary" : ""), a.label);
          b.type = "button";
          b.addEventListener("click", function (e) { e.stopPropagation(); emit("action", publicPin(p), a.event != null ? a.event : a.label); });
          row.appendChild(b);
        });
        sheet.appendChild(row);
      }
      const stats = Array.isArray(sh.stats) ? sh.stats.filter(Boolean).slice(0, 4) : [];
      if (stats.length) {
        const row = el("div", "hm3-stats");
        stats.forEach(function (s) {
          const c = el("div", "hm3-stat");
          c.appendChild(el("div", "hm3-k", s.k != null ? s.k : ""));
          const v = el("div", "hm3-v" + (s.tone && TONES.indexOf(s.tone) >= 0 ? " hm3-t-" + s.tone : ""), s.v != null ? s.v : "—");
          v.title = String(s.v != null ? s.v : "");
          c.appendChild(v);
          row.appendChild(c);
        });
        sheet.appendChild(row);
      }
      const bars = sh.bars;
      if (bars && Array.isArray(bars.items) && bars.items.length) {
        const box = el("div", "hm3-bars");
        const bh = el("div", "hm3-bh");
        bh.appendChild(el("span", "", bars.title || ""));
        if (bars.live) bh.appendChild(el("span", "hm3-live", "Live"));
        box.appendChild(bh);
        const plot = el("div", "hm3-plot"), xl = el("div", "hm3-xl");
        const its = bars.items.slice(0, 48);
        let max = 0; its.forEach(function (b) { const v = num(b.v); if (v > max) max = v; });
        const step = its.length <= 8 ? 1 : Math.ceil(its.length / 6);
        plot.setAttribute("role", "img");
        plot.setAttribute("aria-label", (bars.title ? bars.title + ": " : "") + its.map(function (b) { return (b.label || "") + " " + (isFinite(num(b.v)) ? num(b.v) : 0); }).join(", "));
        its.forEach(function (b, i) {
          const v = Math.max(0, num(b.v) || 0);
          const bar = el("div", "hm3-bar" + (b.hi ? " hm3-hi" : ""));
          bar.style.height = Math.max(5, max > 0 ? v / max * 100 : 0) + "%";
          bar.style.transitionDelay = (reduce ? 0 : Math.min(i * 28, 500)) + "ms";
          plot.appendChild(bar);
          xl.appendChild(el("span", "", i % step === 0 || b.hi ? (b.label || "") : ""));
        });
        box.appendChild(plot); box.appendChild(xl);
        sheet.appendChild(box);
        if (reduce || !fresh) box.classList.add("hm3-in");
        else requestAnimationFrame(function () { requestAnimationFrame(function () { box.classList.add("hm3-in"); }); });
      }
      const rows = Array.isArray(sh.rows) ? sh.rows.filter(Boolean) : [];
      if (rows.length) {
        const ul = el("ul", "hm3-rows");
        rows.slice(0, 6).forEach(function (r) {
          const li = el("li", "hm3-row");
          li.appendChild(el("span", "hm3-dot" + (r.tone && TONES.indexOf(r.tone) >= 0 ? " hm3-t-" + r.tone : "")));
          if (r.ref != null && r.ref !== "") li.appendChild(el("span", "hm3-ref", r.ref));
          const tx = el("span", "hm3-rt", r.text != null ? r.text : ""); tx.title = String(r.text != null ? r.text : "");
          li.appendChild(tx);
          ul.appendChild(li);
        });
        if (rows.length > 6) ul.appendChild(el("li", "hm3-more", "+" + (rows.length - 6) + " more"));
        sheet.appendChild(ul);
      }
      if (sh.note) sheet.appendChild(el("div", "hm3-foot", sh.note));
      sheet.scrollTop = 0;
    }
    function renderClusterSheet(c) {
      let total = 0, any = false;
      c.pins.forEach(function (p) { if (p.count != null) { total += p.count; any = true; } });
      sheetBase(c.pins.length + " places here", any ? fmtCount(total) + " in all · zoom in to separate them" : "Zoom in to separate them");
      const row = el("div", "hm3-acts");
      const z = el("button", "hm3-act hm3-primary", "Zoom in"); z.type = "button";
      z.addEventListener("click", function (e) { e.stopPropagation(); zoomToPins(c.pins); });
      row.appendChild(z);
      sheet.appendChild(row);
      const ul = el("ul", "hm3-rows");
      const list = c.pins.slice().sort(function (a, b) { return (b.count || 0) - (a.count || 0); });
      list.slice(0, 12).forEach(function (p) {
        const li = el("li");
        const b = el("button", "hm3-rowbtn"); b.type = "button";
        b.appendChild(el("span", "hm3-dot hm3-t-" + p.tone));
        b.appendChild(el("span", "hm3-rt", p.label || p.id));
        if (p.count != null) b.appendChild(el("b", "", fmtCount(p.count)));
        b.addEventListener("click", function (e) {
          e.stopPropagation();
          const sel = selectPin(p.id, true, false);
          if (sel) zoomUntilApart(p);
        });
        li.appendChild(b); ul.appendChild(li);
      });
      if (list.length > 12) ul.appendChild(el("li", "hm3-more", "+" + (list.length - 12) + " more"));
      sheet.appendChild(ul);
      sheet.scrollTop = 0;
    }
    function zoomToPins(ps) {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      ps.forEach(function (p) { x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y); });
      userMoved = true;
      fitBox([x0, y0, x1, y1], { duration: 700, maxZoom: Math.max(cam.zoom * 2, Math.min(MAX_ZOOM, cam.zoom * 8)), minZoom: cam.zoom * 1.6 });
    }
    function zoomUntilApart(p) {
      const z = Math.min(MAX_ZOOM, Math.max(cam.zoom * 3, 6));
      userMoved = true;
      flyCam({ x: p.x, y: p.y, zoom: z }, 700);
    }
    // phone sheet: drag the handle down to close
    function bindGrab(grab) {
      let y0 = null, id = null;
      grab.addEventListener("pointerdown", function (e) { y0 = e.clientY; id = e.pointerId; try { grab.setPointerCapture(id); } catch (er) { /* ignore */ } sheet.style.transition = "none"; });
      grab.addEventListener("pointermove", function (e) { if (y0 == null || e.pointerId !== id) return; sheet.style.transform = "translateY(" + Math.max(0, e.clientY - y0) + "px)"; });
      function end(e) {
        if (y0 == null || e.pointerId !== id) return;
        const dy = e.clientY - y0; y0 = null; sheet.style.transition = "";
        if (dy > 60) closeSheet(true); else sheet.style.transform = "";
      }
      grab.addEventListener("pointerup", end); grab.addEventListener("pointercancel", end);
    }

    /* ── pointer, wheel, keyboard ── */
    const ptrs = new Map();
    let gest = null;
    function local(e) { const r = cvOver.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
    function hitTest(x, y) {
      let best = null, bd = Infinity;
      for (let i = heads.length - 1; i >= 0; i--) {
        const it = heads[i], d = Math.hypot(it.hx - x, it.hy - y);
        if (d <= it.hr + 5 && d < bd) { best = it; bd = d; }
        const da = Math.hypot(it.x - x, it.y - y + 4);   // the tail and its tip count too
        if (da < 7 && da < bd) { best = it; bd = da; }
      }
      return best;
    }
    function startPan(id) {
      const p = ptrs.get(id);
      gest = { mode: "pan", id: id, g0: unproject(p.x, p.y, 0), x0: p.x, y0: p.y, moved: false, t0: now(), samples: [] };
    }
    function startPinch() {
      const ids = Array.from(ptrs.keys()).slice(0, 2), a = ptrs.get(ids[0]), b = ptrs.get(ids[1]);
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      gest = { mode: "pinch", ids: ids, d0: Math.max(10, Math.hypot(b.x - a.x, b.y - a.y)), a0: Math.atan2(b.y - a.y, b.x - a.x),
        z0: cam.zoom, b0: cam.bearing, g0: unproject(mx, my, 0), rot: false, moved: true };
    }
    function onDown(e) {
      if (e.button !== 0 && e.button !== 2) return;
      if (e.pointerType === "mouse") { try { root.focus({ preventScroll: true }); } catch (er) { /* ignore */ } }
      stopMotion();
      const p = local(e);
      ptrs.set(e.pointerId, { x: p[0], y: p[1], type: e.pointerType });
      try { cvOver.setPointerCapture(e.pointerId); } catch (er) { /* ignore */ }
      if (ptrs.size === 1) {
        if (e.pointerType === "mouse" && (e.button === 2 || e.ctrlKey || e.metaKey)) {
          gest = { mode: "rotate", id: e.pointerId, x0: p[0], y0: p[1], b0: cam.bearing, p0: cam.pitch, moved: false };
        } else startPan(e.pointerId);
      } else if (ptrs.size === 2 && e.pointerType !== "mouse") {
        startPinch();
      }
    }
    function onMove(e) {
      const p = local(e);
      if (!ptrs.has(e.pointerId)) {
        if (e.pointerType === "mouse" && !gest) {
          const it = hitTest(p[0], p[1]), tip = it ? tipFor(it) : "";
          root.classList.toggle("hm3-hot", !!it);
          if (cvOver.title !== tip) cvOver.title = tip;   // native tooltip
        }
        return;
      }
      const P = ptrs.get(e.pointerId); P.x = p[0]; P.y = p[1];
      if (!gest) return;
      if (gest.mode === "pan" && gest.id === e.pointerId) {
        if (!gest.moved && Math.hypot(p[0] - gest.x0, p[1] - gest.y0) < 4) return;
        if (!gest.moved) { gest.moved = true; root.classList.add("hm3-drag"); userMoved = true; }
        const g = unproject(p[0], p[1], 0);
        cam.x += gest.g0[0] - g[0]; cam.y += gest.g0[1] - g[1];
        changed();
        const t = now();
        gest.samples.push([t, cam.x, cam.y]);
        while (gest.samples.length && t - gest.samples[0][0] > 110) gest.samples.shift();
      } else if (gest.mode === "rotate" && gest.id === e.pointerId) {
        const dx = p[0] - gest.x0, dy = p[1] - gest.y0;
        if (!gest.moved && Math.hypot(dx, dy) < 3) return;
        gest.moved = true; userMoved = true; root.classList.add("hm3-drag");
        cam.bearing = gest.b0 - dx * 0.35;
        cam.pitch = clamp(gest.p0 - dy * 0.35, 0, MAX_PITCH);
        if (cam.pitch > 5) pitch3D = cam.pitch;
        changed();
      } else if (gest.mode === "pinch") {
        const a = ptrs.get(gest.ids[0]), b = ptrs.get(gest.ids[1]);
        if (!a || !b) return;
        userMoved = true;
        const d = Math.max(10, Math.hypot(b.x - a.x, b.y - a.y)), ang = Math.atan2(b.y - a.y, b.x - a.x);
        let da = ((ang - gest.a0) / DEG + 540) % 360 - 180;
        if (!gest.rot && Math.abs(da) > 12) { gest.rot = true; gest.a0 = ang; gest.b0 = cam.bearing; da = 0; }
        cam.zoom = clamp(gest.z0 * d / gest.d0, MIN_ZOOM, MAX_ZOOM);
        if (gest.rot) cam.bearing = gest.b0 - da;
        clampCam(); computeView();
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, g = unproject(mx, my, 0);
        cam.x += gest.g0[0] - g[0]; cam.y += gest.g0[1] - g[1];
        changed();
      }
    }
    function onUp(e, cancelled) {
      if (!ptrs.has(e.pointerId)) return;
      ptrs.delete(e.pointerId);
      const g = gest;
      if (g && g.mode === "pinch") {
        if (ptrs.size === 1) { startPan(Array.from(ptrs.keys())[0]); gest.moved = true; }
        else if (!ptrs.size) gest = null;
        return;
      }
      if (ptrs.size) return;
      gest = null;
      root.classList.remove("hm3-drag");
      schedule();
      if (!g || cancelled) return;
      if (!g.moved) {
        if (g.mode === "pan" || g.mode === "rotate") {
          const p = local(e), it = hitTest(p[0], p[1]);
          if (it) activate(it, true);
          else if (selPinId != null || selCluster) closeSheet(true);
        }
        return;
      }
      if (g.mode === "pan" && !reduce && g.samples.length > 1) {
        const a = g.samples[0], b = g.samples[g.samples.length - 1], dt = b[0] - a[0];
        if (dt > 8 && now() - b[0] < 60) {
          inertia = { vx: (b[1] - a[1]) / dt, vy: (b[2] - a[2]) / dt, t: now() };
          const sp = Math.hypot(inertia.vx, inertia.vy) * V.s;
          if (sp < 0.15) inertia = null;
          else if (sp > 4) { inertia.vx *= 4 / sp; inertia.vy *= 4 / sp; }
          schedule();
        }
      }
    }
    function onWheel(e) {
      e.preventDefault();
      stopMotion(); userMoved = true;
      const p = local(e);
      let dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
      const f = clamp(Math.exp(-dy * (e.ctrlKey ? 0.012 : 0.0028)), 0.5, 2);
      zoomAround(p[0], p[1], cam.zoom * f);
      schedule();
    }
    function onDbl(e) {
      e.preventDefault();
      const p = local(e);
      if (hitTest(p[0], p[1])) return;
      zoomBy(e.shiftKey ? 0.5 : 2, p[0], p[1]);
    }
    function onKey(e) {
      if (e.defaultPrevented) return;
      const k = e.key;
      if (k === "Escape" || k === "Esc") {
        if (selPinId != null || selCluster) { closeSheet(true); e.preventDefault(); return; }
        if (root.classList.contains("hm3-pseudo")) { setPseudo(false); e.preventDefault(); }
        return;
      }
      const tgt = e.target;
      if (tgt !== root && !(tgt && tgt.classList && tgt.classList.contains("hm3-pinbtn"))) return;
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      let handled = true;
      const step = 110;
      if (k === "+" || k === "=" || k === "Add") zoomBy(2);
      else if (k === "-" || k === "_" || k === "Subtract") zoomBy(0.5);
      else if (k.indexOf("Arrow") === 0) {
        userMoved = true;
        if (e.shiftKey) {
          if (k === "ArrowLeft") flyCam({ bearing: cam.bearing - 15 }, 220);
          else if (k === "ArrowRight") flyCam({ bearing: cam.bearing + 15 }, 220);
          else if (k === "ArrowUp") flyCam({ pitch: Math.min(MAX_PITCH, cam.pitch + 10) }, 220);
          else flyCam({ pitch: Math.max(0, cam.pitch - 10) }, 220);
        } else {
          const dx = k === "ArrowLeft" ? -step : k === "ArrowRight" ? step : 0;
          const dy = k === "ArrowUp" ? -step : k === "ArrowDown" ? step : 0;
          const g0 = unproject(V.cx, V.cy, 0), g1 = unproject(V.cx + dx, V.cy + dy, 0);
          flyCam({ x: cam.x + g1[0] - g0[0], y: cam.y + g1[1] - g0[1] }, 220);
        }
      } else handled = false;
      if (handled) e.preventDefault();
    }
    function onCtx(e) { e.preventDefault(); }
    const pm = function (e) { onMove(e); }, pu = function (e) { onUp(e, false); }, pc = function (e) { onUp(e, true); };
    cvOver.addEventListener("pointerdown", onDown);
    cvOver.addEventListener("pointermove", pm);
    cvOver.addEventListener("pointerup", pu);
    cvOver.addEventListener("pointercancel", pc);
    cvOver.addEventListener("lostpointercapture", pc);
    cvOver.addEventListener("pointerleave", function (e) { if (e.pointerType === "mouse" && !gest) root.classList.remove("hm3-hot"); });
    cvOver.addEventListener("wheel", onWheel, { passive: false });
    cvOver.addEventListener("dblclick", onDbl);
    cvOver.addEventListener("contextmenu", onCtx);
    root.addEventListener("keydown", onKey);
    // Safari's own pinch gesture events would zoom the page: keep them on the map
    function noGesture(e) { e.preventDefault(); }
    root.addEventListener("gesturestart", noGesture);

    /* ── full screen ── */
    function fsEl() { return document.fullscreenElement || document.webkitFullscreenElement || null; }
    function setPseudo(on) {
      root.classList.toggle("hm3-pseudo", on);
      syncFull();
      setTimeout(function () { if (resize()) schedule(); }, 0);
    }
    function syncFull() {
      const on = fsEl() === root || root.classList.contains("hm3-pseudo");
      root.classList.toggle("hm3-full", on);
      bFull.innerHTML = on ? ICON.unfull : ICON.full;
      const l = on ? "Exit full screen" : "Full screen";
      bFull.setAttribute("aria-label", l); bFull.title = l;
      if (resize()) schedule();
    }
    function toggleFull() {
      if (fsEl() === root) { const ex = document.exitFullscreen || document.webkitExitFullscreen; if (ex) ex.call(document); return; }
      if (root.classList.contains("hm3-pseudo")) { setPseudo(false); return; }
      const req = root.requestFullscreen || root.webkitRequestFullscreen;
      if (!req) { setPseudo(true); return; }
      try {
        const r = req.call(root);
        if (r && r.catch) r.catch(function () { setPseudo(true); });
      } catch (er) { setPseudo(true); }
    }
    document.addEventListener("fullscreenchange", syncFull);
    document.addEventListener("webkitfullscreenchange", syncFull);
    cleanups.push(function () {
      document.removeEventListener("fullscreenchange", syncFull);
      document.removeEventListener("webkitfullscreenchange", syncFull);
    });

    /* ── data ── */
    function setData(d) {
      if (destroyed) return;
      d = d || {};
      const t = now();
      countLabel = d.countLabel != null && String(d.countLabel).trim() !== "" ? String(d.countLabel).trim() : "shipments";
      focus = new Set((Array.isArray(d.focus) ? d.focus : []).map(function (c) { return String(c || "").toUpperCase(); }).filter(Boolean));
      const seenIds = {};
      let newCount = 0;
      pins = [];
      pinById = {};
      (Array.isArray(d.pins) ? d.pins : []).forEach(function (src, i) {
        if (!src) return;
        const lat = num(src.lat), lon = num(src.lon);
        if (!isFinite(lat) || !isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 360) return;
        let id = src.id != null && src.id !== "" ? String(src.id) : "pin-" + i;
        while (seenIds[id]) id += "'";
        seenIds[id] = 1;
        const c = num(src.count);
        const p = {
          id: id, src: src, index: i, lat: lat, lon: lon,
          x: proj.x(((lon + 540) % 360) - 180), y: proj.y(lat),
          label: src.label != null ? String(src.label) : "", tone: tone(src.tone),
          count: isFinite(c) ? c : null, sheet: src.sheet || null, cc: ""
        };
        p.cc = countryAt(world, p.x, p.y);
        pins.push(p); pinById[id] = p;
        if (born[id] == null) { born[id] = t + Math.min(newCount, 24) * 35; newCount++; }
      });
      routes = [];
      (Array.isArray(d.routes) ? d.routes : []).forEach(function (r, i) {
        const R = buildRoute(r, i);
        if (R) { routes.push(R); if (routeBorn[R.id] == null) routeBorn[R.id] = t + 150; }
      });
      hasData = pins.length > 0 || routes.length > 0;
      dataVer++;
      G.key = "";
      baseDirty = true;
      updateNote();
      // keep the selection when its place is still there; refresh what the sheet says
      if (selPinId != null) {
        if (pinById[selPinId]) renderSheet(pinById[selPinId], false);
        else closeSheet(false);
      } else if (selCluster) closeSheet(false);
      if (hasData && !autoFitted && !userMoved && opts.autoFit !== false) {
        autoFitted = true;
        fit({ duration: world ? 800 : 0 });
      }
      schedule();
    }
    function buildRoute(r, i) {
      if (!r || !Array.isArray(r.points)) return null;
      const pts = r.points.filter(function (q) { return q && isFinite(num(q.lat)) && isFinite(num(q.lon)); })
        .map(function (q) { return { lat: clamp(num(q.lat), -85, 85), lon: num(q.lon) }; });
      if (pts.length < 2) return null;
      const ll = [pts[0]], idx = [0];
      for (let k = 1; k < pts.length; k++) {
        const seg = gcPath(pts[k - 1], pts[k]);
        for (let j = 0; j < seg.length; j++) ll.push(seg[j]);
        idx.push(ll.length - 1);
      }
      // continuous longitudes (no jump across the date line)
      let prev = ((ll[0].lon + 540) % 360) - 180;
      const P = new Float64Array(ll.length * 2), C = new Float64Array(ll.length);
      for (let k = 0; k < ll.length; k++) {
        let lon = ll[k].lon;
        if (k === 0) lon = prev;
        else { while (lon - prev > 180) lon -= 360; while (lon - prev < -180) lon += 360; }
        prev = lon;
        P[k * 2] = proj.x(lon); P[k * 2 + 1] = proj.y(ll[k].lat);
        if (k) C[k] = C[k - 1] + Math.hypot(P[k * 2] - P[k * 2 - 2], P[k * 2 + 1] - P[k * 2 - 1]);
      }
      const reached = clamp(Math.floor(num(r.reached) || 0), 0, pts.length - 1);
      const stops = idx.map(function (j) { return { x: P[j * 2], y: P[j * 2 + 1], L: C[j] }; });
      return {
        id: r.id != null ? String(r.id) : "route-" + i, src: r, tone: tone(r.tone), pts: P, cum: C, L: C[C.length - 1] || 0,
        Ls: C[idx[reached]], reached: reached, stops: stops, raw: pts
      };
    }
    function updateNote() {
      if (noCanvas) { note.textContent = "This browser cannot draw the map"; note.hidden = false; return; }
      if (worldFailed) { note.textContent = "Map outline unavailable"; note.hidden = false; return; }
      if (!pins.length) { note.textContent = "No places reported yet"; note.hidden = false; return; }
      note.hidden = true;
    }
    function reproject() {
      pins.forEach(function (p) { p.x = proj.x(((p.lon + 540) % 360) - 180); p.y = proj.y(p.lat); p.cc = countryAt(world, p.x, p.y); });
      routes = routes.map(function (R, i) { return buildRoute(R.src, i) || R; });
    }

    /* ── fit ── */
    function dataBox() {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      pins.forEach(function (p) { x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y); });
      routes.forEach(function (R) {
        for (let k = 0; k < R.pts.length; k += 2) {
          x0 = Math.min(x0, R.pts[k]); x1 = Math.max(x1, R.pts[k]); y0 = Math.min(y0, R.pts[k + 1]); y1 = Math.max(y1, R.pts[k + 1]);
        }
      });
      return isFinite(x0) ? [x0, y0, x1, y1] : null;
    }
    function fitBox(b, o) {
      o = o || {};
      if (!cssW || !cssH) resize();
      const save = { x: cam.x, y: cam.y, zoom: cam.zoom };
      const padT = 70, padB = 50 + (narrow ? 0 : 0), padX = 70;
      const bw = Math.max(b[2] - b[0], 1e-6), bh = Math.max(b[3] - b[1], 1e-6);
      let z = Math.min((V.W - padX * 2) / (bw * baseScale()), (V.H - padT - padB) / (bh * baseScale()));
      z = clamp(z, o.minZoom || MIN_ZOOM, Math.min(o.maxZoom || 7, MAX_ZOOM));
      cam.x = (b[0] + b[2]) / 2; cam.y = (b[1] + b[3]) / 2; cam.zoom = z;
      clampCam(); computeView();
      // in 3D the box is a trapezoid: shrink and re-centre until it sits inside the padded view
      const pts = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]];
      for (let it = 0; it < 24; it++) {
        let sx0 = Infinity, sy0 = Infinity, sx1 = -Infinity, sy1 = -Infinity, bad = false;
        for (let k = 0; k < 4; k++) {
          project(pts[k][0], pts[k][1], V.hTop, tmp);
          if (tmp[3] > V.ryLim) bad = true;
          sx0 = Math.min(sx0, tmp[0]); sx1 = Math.max(sx1, tmp[0]); sy0 = Math.min(sy0, tmp[1] - 40); sy1 = Math.max(sy1, tmp[1] + 22);
        }
        // centre the projected box
        const g0 = unproject((sx0 + sx1) / 2, (sy0 + sy1) / 2, V.hTop), g1 = unproject(V.cx + (padX - padX) / 2, V.cy + (padT - padB) / 2, V.hTop);
        cam.x += g0[0] - g1[0]; cam.y += g0[1] - g1[1];
        clampCam(); computeView();
        const fits = !bad && (sx1 - sx0) <= V.W - padX * 2 + 1 && (sy1 - sy0) <= V.H - padT - padB + 1;
        if (fits || cam.zoom <= (o.minZoom || MIN_ZOOM)) break;
        cam.zoom = Math.max(o.minZoom || MIN_ZOOM, cam.zoom * 0.9);
        computeView();
      }
      const target = { x: cam.x, y: cam.y, zoom: cam.zoom };
      if (o.bearing != null) target.bearing = o.bearing;
      cam.x = save.x; cam.y = save.y; cam.zoom = save.zoom;
      computeView();
      return flyCam(target, o.duration != null ? o.duration : 800);
    }
    // the whole outline (zoom about 1), centred for the current tilt
    function fitWorld(duration) {
      return fitBox([0, proj.y(74), proj.W, proj.y(-54)], { duration: duration, maxZoom: 1.25, minZoom: MIN_ZOOM, bearing: 0 });
    }
    function fit(o) {
      o = o || {};
      const b = dataBox();
      if (!b) return fitWorld(o.duration != null ? o.duration : 800);
      const span = Math.max(b[2] - b[0], (b[3] - b[1]) * 1.3);
      const pad = Math.max(span * 0.08, 4);
      return fitBox([b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad], { duration: o.duration, maxZoom: 7 });
    }

    /* ── start ── */
    resize();
    readTheme();
    computeView();
    updateNote();
    if (!noCanvas) note.hidden = true;   // until setData says otherwise (or the outline fails)
    schedule();
    loadWorld(worldUrl).then(function (w) {
      if (destroyed) return;
      world = w; proj = w.proj; worldVer++;
      reproject();
      G.key = ""; baseDirty = true;
      computeView();
      if (!userMoved && !hasData) fitWorld(0);
      schedule();
    }).catch(function (err) {
      if (destroyed) return;
      worldFailed = true; updateNote();
      if (window.console && console.warn) console.warn("HubMap3D: world outline not loaded:", err && err.message ? err.message : err);
    });

    /* ── public API ── */
    const api = {
      setData: setData,
      on: function (name, fn) {
        if (!listeners[name] || typeof fn !== "function") return function () {};
        listeners[name].push(fn);
        return function () { api.off(name, fn); };
      },
      off: function (name, fn) {
        const l = listeners[name]; if (!l) return;
        const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1);
      },
      select: function (id) {
        if (destroyed) return false;
        if (id == null) { closeSheet(false); return true; }
        return selectPin(String(id), false, true);
      },
      flyTo: function (t, o) {
        if (destroyed || !t) return Promise.resolve(false);
        const to = {};
        if (isFinite(num(t.lat)) && isFinite(num(t.lon))) { to.x = proj.x(((num(t.lon) + 540) % 360) - 180); to.y = proj.y(clamp(num(t.lat), -85, 85)); }
        if (isFinite(num(t.zoom))) to.zoom = num(t.zoom);
        if (isFinite(num(t.pitch))) { to.pitch = num(t.pitch); if (to.pitch > 5) pitch3D = clamp(to.pitch, 0, MAX_PITCH); }
        if (isFinite(num(t.bearing))) to.bearing = num(t.bearing);
        userMoved = true;
        const dur = o && isFinite(num(o.duration)) ? num(o.duration) : 1000;
        return flyCam(to, dur);
      },
      fit: function (o) { if (destroyed) return Promise.resolve(false); userMoved = true; return fit(o); },
      getView: function () {
        return { lat: proj.lat(cam.y), lon: proj.lon(cam.x), zoom: cam.zoom, pitch: cam.pitch, bearing: cam.bearing };
      },
      destroy: function () {
        if (destroyed) return;
        destroyed = true;
        if (raf) cancelAnimationFrame(raf);
        if (themeRaf) cancelAnimationFrame(themeRaf);
        clearTimeout(hideTimer);
        stopMotion();
        cleanups.forEach(function (f) { try { f(); } catch (e) { /* ignore */ } });
        if (fsEl() === root) { const ex = document.exitFullscreen || document.webkitExitFullscreen; if (ex) try { ex.call(document); } catch (e) { /* ignore */ } }
        listeners.select = []; listeners.action = []; listeners.cluster = [];
        btns.clear();
        if (root.parentNode) root.parentNode.removeChild(root);
      },
      element: root
    };
    Object.defineProperty(api, "_perf", {
      enumerable: false,
      value: function () {
        const s = perf.slice().sort(function (a, b) { return a - b; });
        return { frames: s.length, median: s.length ? s[s.length >> 1] : 0, p95: s.length ? s[Math.floor(s.length * 0.95)] : 0, rings: G.n, points: G.pts };
      }
    });
    return api;
  }

  window.HubMap3D = { mount: mount, version: "1.0.0" };
})();
