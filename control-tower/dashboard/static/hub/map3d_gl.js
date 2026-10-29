/* ATLAS Control Tower — the 3D map (WebGL, three.js), with the 2D map as fallback.

   Loaded after hub/map3d.js. It keeps the same calls (HubMap3D.mount, setData,
   on("select"|"action"), select, flyTo, fit, destroy), so the page that uses
   the map does not change. three.js is the self-hosted copy in
   /static/vendor (r169, see CONTROL_TOWER_PLAN.md); nothing comes from outside.

   What is drawn is only what the page gives: land from /static/maps/world.json,
   a pin where the caller placed one, a route through the points it gave.

   Which map is used (logged to the console as "[hub map] mode: …"):
     3D  when WebGL is available and three.js loads;
     2D  (hub/map3d.js) — once, never switching back on its own — when WebGL is
         unavailable, three.js or the WebGL context fails, or the median frame
         time over the first 2 seconds of drawing is above 50 ms.
   The 2D/3D button (top-down / tilted view) works both ways in either map. */
(function () {
  "use strict";
  if (typeof window === "undefined" || !window.HubMap3D || window.HubMap3D.gl) return;

  const Map2D = window.HubMap3D;
  const VENDOR = "/static/vendor/three.module.min.js";
  const SLOW_MS = 50, PROBE_MS = 2000;
  const PITCH_3D = 50, PITCH_2D = 0.5, MAX_PITCH = 65, FOV = 35;
  const FLY_MS = 1000;
  const TONES = ["blue", "green", "amber", "red", "teal"];
  const DEG = Math.PI / 180;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);   // ease in/out
  const num = (v) => (v == null || v === "" ? NaN : +v);
  const log = (level, msg) => { try { (console[level] || console.log).call(console, "[hub map] " + msg); } catch (e) { /* no console */ } };

  function hasWebGL() {
    try {
      const c = document.createElement("canvas");
      const gl = c.getContext("webgl2") || c.getContext("webgl");
      if (!gl) return null;
      const kind = (typeof WebGL2RenderingContext !== "undefined" && gl instanceof WebGL2RenderingContext) ? "WebGL2" : "WebGL";
      const lose = gl.getExtension && gl.getExtension("WEBGL_lose_context");
      if (lose) lose.loseContext();
      return kind;
    } catch (e) { return null; }
  }

  /* ───────────────────────── styles (injected once) ───────────────────────── */
  const CSS = `
.hg-root{position:relative;width:100%;height:100%;min-height:300px;overflow:hidden;background:var(--hub-water,#dfe9f3);
  color:var(--hub-ink,#1d1d1f);font:14px/1.4 var(--hub-font,system-ui,sans-serif);touch-action:pan-y;
  -webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;outline:none;isolation:isolate}
.hg-root *,.hg-root *::before,.hg-root *::after{box-sizing:border-box}
.hg-root:focus-visible{box-shadow:inset 0 0 0 2px var(--hub-blue,#0071e3)}
.hg-root:fullscreen{width:100vw;height:100vh}
.hg-cv{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab}
.hg-root.hg-drag .hg-cv{cursor:grabbing}
.hg-ov{position:absolute;inset:0;z-index:1;pointer-events:none;overflow:hidden}
.hg-pin{position:absolute;left:0;top:0;border:0;margin:0;padding:0;border-radius:50%;background:transparent;color:#fff;
  font:700 12px/1 var(--hub-font,system-ui,sans-serif);font-variant-numeric:tabular-nums;display:flex;align-items:center;
  justify-content:center;pointer-events:auto;cursor:pointer;text-shadow:0 1px 1px rgba(0,0,0,.25);will-change:transform}
.hg-pin:focus-visible{outline:none;box-shadow:0 0 0 2.5px var(--hub-surface,#fff),0 0 0 5px var(--hub-blue,#0071e3)}
.hg-lab{position:absolute;left:0;top:0;padding:2px 7px;border-radius:999px;font:650 11px/16px var(--hub-font,system-ui,sans-serif);
  letter-spacing:.02em;white-space:nowrap;color:var(--hub-ink,#1d1d1f);background:var(--hub-glass,rgba(255,255,255,.8));
  border:1px solid var(--hub-glass-edge,rgba(255,255,255,.65));box-shadow:0 1px 3px rgba(0,0,0,.12);will-change:transform}
.hg-pin[hidden],.hg-lab[hidden]{display:none}
.hg-glass{background:var(--hub-glass,rgba(255,255,255,.72));border:1px solid var(--hub-glass-edge,rgba(255,255,255,.65));
  -webkit-backdrop-filter:saturate(180%) blur(20px);backdrop-filter:saturate(180%) blur(20px)}
.hg-ctrls{position:absolute;top:12px;right:12px;z-index:3;display:flex;flex-direction:column;gap:8px;align-items:flex-end}
.hg-grp{display:flex;flex-direction:column;border-radius:12px;overflow:hidden;box-shadow:var(--hub-shadow,0 4px 14px rgba(0,0,0,.08))}
.hg-btn{width:38px;height:38px;display:flex;align-items:center;justify-content:center;border:0;margin:0;padding:0;background:transparent;
  color:var(--hub-ink-2,#424245);cursor:pointer;font:650 12.5px/1 var(--hub-font,system-ui,sans-serif)}
.hg-btn:hover{background:rgba(128,128,128,.12)}
.hg-btn:focus-visible{outline:2px solid var(--hub-blue,#0071e3);outline-offset:-2px}
.hg-btn svg{width:17px;height:17px}
.hg-grp .hg-btn+.hg-btn{border-top:1px solid var(--hub-line,rgba(0,0,0,.08))}
.hg-needle{transition:transform .2s}
.hg-note{position:absolute;left:12px;top:12px;z-index:2;padding:6px 11px;border-radius:999px;font-size:12px;color:var(--hub-ink-2,#424245)}
.hg-note[hidden]{display:none}
.hg-sheet{position:absolute;left:12px;bottom:12px;z-index:4;width:340px;max-width:calc(100% - 24px);max-height:calc(100% - 24px);
  overflow:auto;border-radius:20px;padding:14px 16px 14px;box-shadow:var(--hub-shadow-lg,0 10px 40px rgba(0,0,0,.14));
  transform:translateY(calc(100% + 24px));opacity:0;transition:transform .42s cubic-bezier(.2,.8,.2,1),opacity .3s;pointer-events:none}
.hg-sheet.hg-show{transform:none;opacity:1;pointer-events:auto}
.hg-root.hg-narrow .hg-sheet{left:8px;right:8px;bottom:8px;width:auto;max-width:none;max-height:72%}
.hg-grab{width:36px;height:5px;border-radius:3px;background:var(--hub-ink-4,#a1a1a6);opacity:.5;margin:-4px auto 10px;display:none}
.hg-root.hg-narrow .hg-grab{display:block}
.hg-head{display:flex;align-items:flex-start;gap:10px}
.hg-head>div{flex:1;min-width:0}
.hg-title{margin:0;font-size:19px;font-weight:700;letter-spacing:-.01em;line-height:1.2}
.hg-sub{margin-top:2px;font-size:12.5px;color:var(--hub-ink-3,#6e6e73)}
.hg-x{flex:none;width:30px;height:30px;border-radius:50%;border:0;background:var(--hub-surface-2,#f0f0f3);color:var(--hub-ink-2,#424245);
  cursor:pointer;display:flex;align-items:center;justify-content:center}
.hg-x svg{width:14px;height:14px}
.hg-x:focus-visible,.hg-act:focus-visible{outline:2px solid var(--hub-blue,#0071e3);outline-offset:2px}
.hg-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:12px 0 4px}
.hg-stat{background:var(--hub-surface-2,#f5f5f7);border-radius:12px;padding:7px 8px}
.hg-k{font-size:9.5px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;color:var(--hub-ink-3,#6e6e73);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hg-v{font-size:17px;font-weight:700;font-variant-numeric:tabular-nums;margin-top:1px}
.hg-t-red{color:var(--hub-red,#e5383b)}.hg-t-green{color:var(--hub-green,#28a745)}.hg-t-amber{color:var(--hub-amber,#e08a00)}
.hg-t-blue{color:var(--hub-blue,#0071e3)}.hg-t-teal{color:var(--hub-teal,#0a9fb5)}
.hg-bars{margin-top:12px}
.hg-bh{font-size:11.5px;font-weight:600;color:var(--hub-ink-3,#6e6e73);margin-bottom:6px}
.hg-plot{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:10px;align-items:end;height:92px}
.hg-col{display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;gap:3px;min-width:0}
.hg-bv{font-size:11.5px;font-weight:650;font-variant-numeric:tabular-nums;color:var(--hub-ink-2,#424245)}
.hg-bar{width:100%;max-width:44px;border-radius:7px 7px 3px 3px;background:var(--hub-blue,#0071e3);opacity:.35;height:4px;
  transition:height .55s cubic-bezier(.2,.8,.2,1)}
.hg-bar.hg-hi{opacity:1}
.hg-xl{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:10px;margin-top:4px;font-size:10.5px;color:var(--hub-ink-3,#6e6e73);text-align:center}
.hg-acts{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.hg-act{height:34px;padding:0 14px;border-radius:999px;border:0;background:var(--hub-surface-2,#f0f0f3);color:var(--hub-ink,#1d1d1f);
  font:600 13px/1 var(--hub-font,system-ui,sans-serif);cursor:pointer}
.hg-act.hg-primary{background:var(--hub-blue,#0071e3);color:#fff}
.hg-rows{list-style:none;margin:12px 0 0;padding:0;font-size:12.5px}
.hg-rows li{display:flex;gap:8px;padding:5px 0;border-top:1px solid var(--hub-line-2,rgba(0,0,0,.05))}
.hg-ref{font-family:var(--hub-mono,ui-monospace,monospace);font-size:12px}
.hg-rt{color:var(--hub-ink-3,#6e6e73);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hg-foot{margin-top:12px;font-size:11.5px;color:var(--hub-ink-3,#6e6e73)}
.hg-sr{position:absolute!important;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
@media (prefers-reduced-motion:reduce){.hg-sheet,.hg-bar,.hg-needle{transition:none!important}}`;

  const ICON = {
    plus: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M10 4v12M4 10h12"/></svg>',
    minus: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 10h12"/></svg>',
    compass: '<svg viewBox="0 0 20 20"><g class="hg-needle"><path d="M10 2.5l3 7.5h-6z" fill="#e5383b"/><path d="M10 17.5l-3-7.5h6z" fill="currentColor" opacity=".45"/></g></svg>',
    home: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="10" cy="10" r="3.2"/><path d="M10 2v3M10 15v3M2 10h3M15 10h3" stroke-linecap="round"/></svg>',
    full: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M3 7.5V3h4.5M17 7.5V3h-4.5M3 12.5V17h4.5M17 12.5V17h-4.5"/></svg>',
    close: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 5l10 10M15 5L5 15"/></svg>'
  };
  function injectCss() {
    if (document.getElementById("hubmapgl-css")) return;
    const s = document.createElement("style"); s.id = "hubmapgl-css"; s.textContent = CSS;
    document.head.appendChild(s);
  }
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }

  /* ───────────────────────── world outline ───────────────────────── */
  function makeProj(W, H, latTop, latBot) {
    const my = (lat) => Math.log(Math.tan(Math.PI / 4 + clamp(lat, -85, 85) * DEG / 2));
    const y0 = my(latTop), ky = H / (y0 - my(latBot));
    return { W, H, x: (lon) => (lon + 180) / 360 * W, y: (lat) => (y0 - my(lat)) * ky };
  }
  function parsePath(d) {
    const rings = [], toks = d.match(/[MLHVZmlhvz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g) || [];
    let cur = null, x = 0, y = 0, sx = 0, sy = 0, cmd = "M", i = 0;
    const flush = () => { if (cur && cur.length >= 6) rings.push(cur); cur = null; };
    while (i < toks.length) {
      const t = toks[i];
      if (/^[A-Za-z]$/.test(t)) { cmd = t; i++; if (cmd === "Z" || cmd === "z") { flush(); x = sx; y = sy; } continue; }
      if (cmd === "M" || cmd === "m") {
        const nx = +toks[i], ny = +toks[i + 1]; i += 2; flush();
        if (cmd === "m") { x += nx; y += ny; } else { x = nx; y = ny; }
        sx = x; sy = y; cur = [x, y]; cmd = cmd === "m" ? "l" : "L";
      } else if (cmd === "L" || cmd === "l") {
        const nx = +toks[i], ny = +toks[i + 1]; i += 2;
        if (cmd === "l") { x += nx; y += ny; } else { x = nx; y = ny; }
        (cur = cur || [sx, sy]).push(x, y);
      } else if (cmd === "H" || cmd === "h") {
        const n = +toks[i]; i += 1; x = cmd === "h" ? x + n : n; (cur = cur || [sx, sy]).push(x, y);
      } else if (cmd === "V" || cmd === "v") {
        const n = +toks[i]; i += 1; y = cmd === "v" ? y + n : n; (cur = cur || [sx, sy]).push(x, y);
      } else i++;
    }
    flush();
    return rings;
  }
  function simplify(pts, tol) {          // Douglas–Peucker, flat [x,y,…]
    const n = pts.length >> 1;
    if (n < 6) return pts;
    const keep = new Uint8Array(n); keep[0] = keep[n - 1] = 1;
    const st = [0, n - 1], t2 = tol * tol;
    while (st.length) {
      const b = st.pop(), a = st.pop();
      const ax = pts[a * 2], ay = pts[a * 2 + 1], dx = pts[b * 2] - ax, dy = pts[b * 2 + 1] - ay, L2 = dx * dx + dy * dy;
      let md = -1, mi = -1;
      for (let i = a + 1; i < b; i++) {
        const px = pts[i * 2] - ax, py = pts[i * 2 + 1] - ay;
        const d2 = L2 > 1e-12 ? Math.pow(px * dy - py * dx, 2) / L2 : px * px + py * py;
        if (d2 > md) { md = d2; mi = i; }
      }
      if (md > t2) { keep[mi] = 1; st.push(a, mi, mi, b); }
    }
    const out = [];
    for (let i = 0; i < n; i++) if (keep[i]) out.push(pts[i * 2], pts[i * 2 + 1]);
    return out.length >= 8 ? out : null;
  }
  const area = (p) => { let s = 0; for (let i = 0, n = p.length; i < n; i += 2) { const j = (i + 2) % n; s += p[i] * p[j + 1] - p[j] * p[i + 1]; } return s / 2; };
  function inside(px, py, p) {
    let c = false;
    for (let i = 0, j = p.length - 2; i < p.length; j = i, i += 2) {
      if ((p[i + 1] > py) !== (p[j + 1] > py) && px < (p[j] - p[i]) * (py - p[i + 1]) / (p[j + 1] - p[i + 1]) + p[i]) c = !c;
    }
    return c;
  }
  // One country -> outer rings with their holes, in world.json units (antimeridian rings kept continuous).
  function countryPolys(d, W, tol) {
    const rings = [];
    for (const r of parsePath(String(d || ""))) {
      let shift = 0;
      for (let k = 2; k < r.length; k += 2) {
        const jump = r[k] + shift - r[k - 2];
        if (jump > W / 2) shift -= W; else if (jump < -W / 2) shift += W;
        r[k] += shift;
      }
      const s = simplify(r, tol);
      if (s && Math.abs(area(s)) > tol * tol * 2) rings.push(s);
    }
    if (!rings.length) return [];
    let big = rings[0];
    for (const r of rings) if (Math.abs(area(r)) > Math.abs(area(big))) big = r;
    const sign = Math.sign(area(big));
    const outers = rings.filter((r) => Math.sign(area(r)) === sign).map((r) => ({ ring: r, holes: [] }));
    for (const r of rings) {
      if (Math.sign(area(r)) === sign) continue;
      const host = outers.find((o) => inside(r[0], r[1], o.ring));
      if (host) host.holes.push(r); else outers.push({ ring: r, holes: [] });
    }
    return outers;
  }

  /* ───────────────────────── the 3D map ───────────────────────── */
  function GLMap(THREE, host, opts, hooks) {
    injectCss();
    const reduceMq = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    const reduce = () => !!(reduceMq && reduceMq.matches);

    const root = el("div", "hg-root"); root.tabIndex = 0; root.dataset.mode = "3d";
    root.setAttribute("role", "region"); root.setAttribute("aria-label", "Map: where shipments are");
    const cv = el("canvas", "hg-cv"); cv.setAttribute("aria-hidden", "true");
    const ov = el("div", "hg-ov");
    const note = el("div", "hg-note hg-glass", "Loading the map…");
    const live = el("div", "hg-sr"); live.setAttribute("aria-live", "polite");
    const sheet = el("div", "hg-sheet hg-glass"); sheet.setAttribute("role", "dialog"); sheet.setAttribute("aria-modal", "false");
    sheet.hidden = true;
    root.append(cv, ov, note, live, sheet);
    host.textContent = ""; host.appendChild(root);

    const renderer = new THREE.WebGLRenderer({ canvas: cv, antialias: true, alpha: false, powerPreference: "default" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(FOV, 1, 1, 60000);
    const hemi = new THREE.HemisphereLight(0xffffff, 0xb9c2cf, 1.15);
    const sun = new THREE.DirectionalLight(0xffffff, 1.05);
    sun.position.set(-0.55, 1, -0.75);
    scene.add(hemi, sun);
    scene.fog = new THREE.Fog(0xdfe9f3, 3000, 9000);

    const W0 = 2000, H0 = 1094.3;
    let proj = makeProj(W0, H0, 78, -56), OX = W0 / 2, OZ = H0 / 2;
    const wx = (lon) => proj.x(lon) - OX, wz = (lat) => proj.y(lat) - OZ;

    // camera state: target on the ground, distance, pitch from vertical, bearing
    const cam = { x: 0, z: 0, dist: 1500, pitch: PITCH_3D, bearing: 0 };
    let pitch3D = PITCH_3D, overview = null, tween = null, intro = null;
    let cssW = 300, cssH = 300, destroyed = false, raf = 0, dirty = true, visible = true, t0 = 0;

    /* materials (colours from the hub tokens) */
    const mat = {
      water: new THREE.MeshBasicMaterial({ color: 0xdfe9f3 }),
      shadow: new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, depthWrite: false, opacity: 0.22 }),
      landTop: new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.95, metalness: 0, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
      landSide: new THREE.MeshStandardMaterial({ color: 0xdcdcd6, roughness: 1, metalness: 0 }),
      border: new THREE.LineBasicMaterial({ color: 0xd6d6d0, transparent: true, opacity: 0.85 }),
      focusBorder: new THREE.LineBasicMaterial({ color: 0xc0c8d6, transparent: true, opacity: 0.95 }),
      stem: new THREE.MeshStandardMaterial({ color: 0xf2f2f2, roughness: 0.5 }),
      pinShadow: null
    };
    const tone = {};
    const col = new THREE.Color();
    function cssVar(name, fb) {
      const v = getComputedStyle(root).getPropertyValue(name).trim();
      return v || fb;
    }
    function rgbOf(css) { const c = new THREE.Color(); try { c.setStyle(css); } catch (e) { c.set(0x888888); } return c; }
    let dark = false;
    function readTheme() {
      const water = rgbOf(cssVar("--hub-water", "#dfe9f3"));
      const land = rgbOf(cssVar("--hub-land", "#fbfbfa"));
      const side = rgbOf(cssVar("--hub-land-side", "#dcdcd6"));
      const focus = rgbOf(cssVar("--hub-land-focus", "#eef4ff"));
      const border = rgbOf(cssVar("--hub-border", "#d6d6d0"));
      const blue = rgbOf(cssVar("--hub-blue", "#0071e3"));
      dark = water.getHSL({ h: 0, s: 0, l: 0 }).l < 0.3;
      scene.background = water; scene.fog.color.copy(water); mat.water.color.copy(water);
      renderer.setClearColor(water);
      mat.landTop.color.copy(land).lerp(water, dark ? 0.3 : 0.18);        // muted: countries without shipments
      mat.landSide.color.copy(side).lerp(water, 0.25);
      mat.border.color.copy(border); mat.border.opacity = dark ? 0.55 : 0.8;
      mat.focusBorder.color.copy(border).lerp(blue, 0.35);
      mat.shadow.opacity = dark ? 0.55 : 0.2;
      hemi.color.set(0xffffff); hemi.groundColor.copy(side);
      hemi.intensity = dark ? 1.5 : 1.75; sun.intensity = dark ? 0.9 : 1.45;
      for (const t of TONES) tone[t] = rgbOf(cssVar("--hub-" + t, "#888"));
      focusTop = focus.clone().lerp(blue, dark ? 0.08 : 0.04);
      focusSel = focus.clone().lerp(blue, dark ? 0.32 : 0.22);
      focusSide = side.clone().lerp(blue, 0.12);
      for (const f of Object.values(focusMeshes)) paintFocus(f);
      for (const p of pins) paintPin(p);
      for (const r of routes) r.mat.uniforms.uColor.value.copy(srgb(tone[r.tone] || tone.blue));
      dirty = true; schedule();
    }
    let focusTop = new THREE.Color(0xeef4ff), focusSel = focusTop.clone(), focusSide = new THREE.Color(0xdcdcd6);
    const srgb = (c) => { const o = { r: 0, g: 0, b: 0 }; c.getRGB(o, THREE.SRGBColorSpace); return new THREE.Vector3(o.r, o.g, o.b); };

    /* world: water, soft shadow / ambient occlusion, land, borders */
    const waterMesh = new THREE.Mesh(new THREE.PlaneGeometry(40000, 40000), mat.water);
    waterMesh.rotation.x = -Math.PI / 2; waterMesh.position.y = -0.05;
    scene.add(waterMesh);
    const landGroup = new THREE.Group(); scene.add(landGroup);       // scaled in y so the relief stays a few pixels at every zoom
    const focusGroup = new THREE.Group(); scene.add(focusGroup);
    let byCountry = {}, focusMeshes = {}, focusSet = new Set(), worldReady = false, worldFailed = false;
    const LAND_PX = 6, FOCUS_PX = 11, LAND_H = 1.5;    // relief in screen pixels; geometry height in units

    function consolidate(g) {       // caps first, sides second: two draw calls for any number of shapes
      const pos = g.attributes.position.array, nor = g.attributes.normal.array;
      const caps = [], sides = [];
      for (const gr of g.groups) (gr.materialIndex === 0 ? caps : sides).push(gr);
      const total = pos.length, P = new Float32Array(total), N = new Float32Array(total);
      let o = 0;
      const copy = (list) => { for (const gr of list) { const a = gr.start * 3, b = (gr.start + gr.count) * 3; P.set(pos.subarray(a, b), o); N.set(nor.subarray(a, b), o); o += b - a; } };
      copy(caps); const capCount = o / 3; copy(sides);
      const out = new THREE.BufferGeometry();
      out.setAttribute("position", new THREE.BufferAttribute(P, 3));
      out.setAttribute("normal", new THREE.BufferAttribute(N, 3));
      out.addGroup(0, capCount, 0); out.addGroup(capCount, o / 3 - capCount, 1);
      g.dispose();
      return out;
    }
    function shapesOf(polys) {
      return polys.map((p) => {
        const pts = []; for (let i = 0; i < p.ring.length; i += 2) pts.push(new THREE.Vector2(p.ring[i] - OX, OZ - p.ring[i + 1]));
        const s = new THREE.Shape(pts);
        for (const h of p.holes) { const hp = []; for (let i = 0; i < h.length; i += 2) hp.push(new THREE.Vector2(h[i] - OX, OZ - h[i + 1])); s.holes.push(new THREE.Path(hp)); }
        return s;
      });
    }
    function extrude(polys) {
      const g = new THREE.ExtrudeGeometry(shapesOf(polys), { depth: 1, curveSegments: 1, bevelEnabled: true, bevelThickness: 0.25,
        bevelSize: 0.3, bevelOffset: -0.3, bevelSegments: 1 });
      g.rotateX(-Math.PI / 2); g.translate(0, 0.25, 0);
      return consolidate(g);
    }
    function borderGeom(polys) {
      const v = [];
      for (const p of polys) for (const r of [p.ring].concat(p.holes)) {
        for (let i = 0; i < r.length; i += 2) {
          const j = (i + 2) % r.length;
          v.push(r[i] - OX, LAND_H + 0.01, r[i + 1] - OZ, r[j] - OX, LAND_H + 0.01, r[j + 1] - OZ);
        }
      }
      const g = new THREE.BufferGeometry(); g.setAttribute("position", new THREE.Float32BufferAttribute(v, 3));
      return g;
    }
    function shadowTexture(all) {
      const S = 1.024, c = document.createElement("canvas");
      c.width = Math.round(W0 * S); c.height = Math.round(H0 * S);
      const x = c.getContext("2d");
      if (!x) return null;
      const path = (dx, dy) => {
        x.beginPath();
        for (const p of all) for (const r of [p.ring].concat(p.holes)) {
          x.moveTo((r[0] + dx) * S, (r[1] + dy) * S);
          for (let i = 2; i < r.length; i += 2) x.lineTo((r[i] + dx) * S, (r[i + 1] + dy) * S);
          x.closePath();
        }
      };
      // drawn far off the canvas, only the blurred shadow lands on it (works in every browser)
      const OFF = 20000;
      for (const [blur, dx, dy, a] of [[14, 2.5, 4, 0.55], [4, 0.6, 1, 0.7]]) {
        x.save(); x.shadowColor = "rgba(0,0,0," + a + ")"; x.shadowBlur = blur; x.shadowOffsetX = OFF * S; x.shadowOffsetY = 0;
        path(dx - OFF, dy); x.fillStyle = "#000"; x.fill("evenodd"); x.restore();
      }
      const t = new THREE.CanvasTexture(c); t.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy());
      return t;
    }
    function paintFocus(f) {
      f.top.color.copy(f.selected ? focusSel : focusTop);
      f.side.color.copy(focusSide);
    }
    function buildWorld(j) {
      const t = performance.now();
      const pr = (j && j.projection) || {}, vb = (j && j.viewBox) || [0, 0, W0, H0];
      proj = makeProj(+pr.width || +vb[2] || W0, +vb[3] || H0, isFinite(+pr.lat_top) ? +pr.lat_top : 78, isFinite(+pr.lat_bottom) ? +pr.lat_bottom : -56);
      OX = proj.W / 2; OZ = proj.H / 2;
      const all = [], base = [];
      byCountry = {};
      for (const c of (j && j.countries) || []) {
        const polys = countryPolys(c.d, proj.W, 0.6);
        if (!polys.length) continue;
        byCountry[c.cc] = { polys, name: c.name };
        for (const p of polys) all.push(p);
      }
      for (const [cc, c] of Object.entries(byCountry)) if (!focusSet.has(cc)) for (const p of c.polys) base.push(p);
      const landMesh = new THREE.Mesh(extrude(base), [mat.landTop, mat.landSide]);
      landGroup.add(landMesh, new THREE.LineSegments(borderGeom(base), mat.border));
      const tex = shadowTexture(all);
      if (tex) {
        mat.shadow.map = tex; mat.shadow.needsUpdate = true;
        const sh = new THREE.Mesh(new THREE.PlaneGeometry(proj.W, proj.H), mat.shadow);
        sh.rotation.x = -Math.PI / 2; sh.position.set(0, 0.02, 0); sh.renderOrder = -1;
        scene.add(sh);
      }
      buildFocus();
      worldReady = true;
      log("info", "3D land built in " + Math.round(performance.now() - t) + " ms (" + Object.keys(byCountry).length + " countries)");
    }
    function buildFocus() {
      for (const f of Object.values(focusMeshes)) { focusGroup.remove(f.group); f.mesh.geometry.dispose(); f.line.geometry.dispose(); }
      focusMeshes = {};
      for (const cc of focusSet) {
        const c = byCountry[cc]; if (!c) continue;
        const top = mat.landTop.clone(), side = mat.landSide.clone();
        const mesh = new THREE.Mesh(extrude(c.polys), [top, side]);
        const line = new THREE.LineSegments(borderGeom(c.polys), mat.focusBorder);
        const group = new THREE.Group(); group.add(mesh, line);
        let x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
        for (const p of c.polys) for (let i = 0; i < p.ring.length; i += 2) {
          x0 = Math.min(x0, p.ring[i] - OX); x1 = Math.max(x1, p.ring[i] - OX); z0 = Math.min(z0, p.ring[i + 1] - OZ); z1 = Math.max(z1, p.ring[i + 1] - OZ);
        }
        const f = { cc, group, mesh, line, top, side, selected: false, lift: 1, bbox: [x0, z0, x1, z1] };
        paintFocus(f); focusGroup.add(group); focusMeshes[cc] = f;
      }
    }

    /* pins: a 3D marker standing on the land, its number and label in HTML (crisp, clickable, keyboard) */
    const pinGeo = (() => {
      const head = new THREE.SphereGeometry(1, 28, 20); head.translate(0, 2.55, 0);
      const tip = new THREE.ConeGeometry(0.74, 1.55, 28, 1, true); tip.rotateX(Math.PI); tip.translate(0, 1.62, 0);
      const stem = new THREE.CylinderGeometry(0.11, 0.11, 1.0, 10); stem.translate(0, 0.5, 0);
      return { head, tip, stem };
    })();
    const pinShadowTex = (() => {
      const c = document.createElement("canvas"); c.width = c.height = 64;
      const x = c.getContext("2d"); if (!x) return null;
      const g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
      g.addColorStop(0, "rgba(0,0,0,.45)"); g.addColorStop(1, "rgba(0,0,0,0)");
      x.fillStyle = g; x.fillRect(0, 0, 64, 64);
      return new THREE.CanvasTexture(c);
    })();
    mat.pinShadow = new THREE.MeshBasicMaterial({ map: pinShadowTex, transparent: true, depthWrite: false, opacity: 0.8 });
    const shadowGeo = new THREE.PlaneGeometry(2.6, 2.6); shadowGeo.rotateX(-Math.PI / 2);
    let pins = [], routes = [], selected = null, countLabel = "shipments";
    const pinLayer = new THREE.Group(); scene.add(pinLayer);
    function paintPin(p) {
      const c = tone[p.tone] || tone.blue || new THREE.Color(0x0071e3);
      p.mat.color.copy(c); p.mat.emissive.copy(c).multiplyScalar(dark ? 0.32 : 0.18);
    }
    function makePin(d, i) {
      const m = new THREE.MeshStandardMaterial({ color: 0x0071e3, roughness: 0.32, metalness: 0.05 });
      const g = new THREE.Group();
      g.add(new THREE.Mesh(pinGeo.head, m), new THREE.Mesh(pinGeo.tip, m), new THREE.Mesh(pinGeo.stem, mat.stem));
      const sh = new THREE.Mesh(shadowGeo, mat.pinShadow); sh.position.y = 0.03;
      g.add(sh);
      const p = {
        id: String(d.id), label: d.label != null ? String(d.label) : "", count: isFinite(num(d.count)) ? num(d.count) : null,
        tone: TONES.indexOf(d.tone) >= 0 ? d.tone : "blue", lat: num(d.lat), lon: num(d.lon), cc: d.cc || null, sheet: d.sheet || null,
        x: wx(num(d.lon)), z: wz(num(d.lat)), group: g, mat: m, i, drop: 1, sx: 0, sy: 0, r: 13
      };
      const b = el("button", "hg-pin"); b.type = "button";
      const txt = p.count != null ? p.count.toLocaleString() : "";
      b.textContent = txt;
      if (txt.length >= 4) b.style.fontSize = "10px";
      const aria = p.label + (p.count != null ? ": " + txt + " " + countLabel : "");
      b.setAttribute("aria-label", aria); b.title = aria;
      b.addEventListener("click", (e) => { e.stopPropagation(); selectPin(p, true); });
      const lab = el("div", "hg-lab", p.label); lab.setAttribute("aria-hidden", "true");
      if (!p.label) lab.hidden = true;
      ov.append(b, lab);
      p.btn = b; p.lab = lab;
      paintPin(p);
      pinLayer.add(g);
      return p;
    }
    function clearPins() {
      for (const p of pins) { pinLayer.remove(p.group); p.mat.dispose(); p.btn.remove(); p.lab.remove(); }
      pins = [];
    }

    /* routes: a thick ribbon with a soft glow; solid where reported, dashed to the destination, flowing */
    const VS = `attribute float side; attribute float along; attribute vec3 perp;
uniform float uWidth; varying float vSide; varying float vAlong;
void main(){ vec3 p = position + perp * side * uWidth; vSide = side; vAlong = along;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0); }`;
    const FS = `uniform vec3 uColor; uniform float uTime; uniform float uReach; uniform float uPx; uniform float uFlow;
varying float vSide; varying float vAlong;
void main(){
  float a = abs(vSide);
  float core = 1.0 - smoothstep(0.36, 0.5, a);
  float glow = exp(-a * a * 5.0) * 0.32;
  float px = vAlong * uPx;
  float planned = step(uReach, vAlong);
  float dash = step(0.42, fract((px - uTime * uFlow * 22.0) / 20.0));
  float streak = (1.0 - planned) * smoothstep(0.82, 1.0, fract((px - uTime * uFlow * 70.0) / 150.0));
  vec3 c = mix(uColor, vec3(1.0), streak * 0.55 * core);
  float alpha = planned > 0.5 ? max(core * dash * 0.9, glow * 0.4) : max(core, glow);
  if (alpha < 0.01) discard;
  gl_FragColor = vec4(c, alpha);
}`;
    function gc(a, b, n) {          // great-circle points between two lat/lon
      const v = (p) => [Math.cos(p.lat * DEG) * Math.cos(p.lon * DEG), Math.cos(p.lat * DEG) * Math.sin(p.lon * DEG), Math.sin(p.lat * DEG)];
      const A = v(a), B = v(b), d = Math.acos(clamp(A[0] * B[0] + A[1] * B[1] + A[2] * B[2], -1, 1));
      const out = [];
      for (let i = 0; i <= n; i++) {
        const t = i / n;
        let x, y, z;
        if (d < 1e-6) { x = A[0]; y = A[1]; z = A[2]; } else {
          const s1 = Math.sin((1 - t) * d) / Math.sin(d), s2 = Math.sin(t * d) / Math.sin(d);
          x = s1 * A[0] + s2 * B[0]; y = s1 * A[1] + s2 * B[1]; z = s1 * A[2] + s2 * B[2];
        }
        out.push({ lat: Math.atan2(z, Math.hypot(x, y)) / DEG, lon: Math.atan2(y, x) / DEG, t });
      }
      return out;
    }
    function makeRoute(d) {
      const pts = (d.points || []).filter((p) => isFinite(num(p.lat)) && isFinite(num(p.lon))).map((p) => ({ lat: num(p.lat), lon: num(p.lon) }));
      if (pts.length < 2) return null;
      const reached = clamp(isFinite(num(d.reached)) ? num(d.reached) : pts.length - 1, 0, pts.length - 1);
      const P = [];
      let reachAlong = 0, along = 0, lastX = null;
      for (let k = 0; k < pts.length - 1; k++) {
        const seg = gc(pts[k], pts[k + 1], 40);
        const legLen = Math.hypot(wx(pts[k + 1].lon) - wx(pts[k].lon), wz(pts[k + 1].lat) - wz(pts[k].lat));
        const hMax = Math.min(70, legLen * 0.16);
        for (let i = k === 0 ? 0 : 1; i < seg.length; i++) {
          let x = wx(seg[i].lon);
          if (lastX != null) { while (x - lastX > proj.W / 2) x -= proj.W; while (x - lastX < -proj.W / 2) x += proj.W; }
          const q = { x, y: Math.sin(Math.PI * seg[i].t) * hMax, z: wz(seg[i].lat) };
          if (P.length) along += Math.hypot(q.x - P[P.length - 1].x, q.y - P[P.length - 1].y, q.z - P[P.length - 1].z);
          q.along = along; P.push(q); lastX = x;
        }
        if (k + 1 === reached) reachAlong = along;
      }
      if (reached === 0) reachAlong = 0;
      const n = P.length, pos = new Float32Array(n * 6), side = new Float32Array(n * 2), al = new Float32Array(n * 2), perp = new Float32Array(n * 6), idx = [];
      for (let i = 0; i < n; i++) {
        const a = P[Math.max(0, i - 1)], b = P[Math.min(n - 1, i + 1)];
        let tx = b.x - a.x, tz = b.z - a.z; const L = Math.hypot(tx, tz) || 1; tx /= L; tz /= L;
        for (let s = 0; s < 2; s++) {
          const o = (i * 2 + s);
          pos.set([P[i].x, P[i].y, P[i].z], o * 3); perp.set([-tz, 0, tx], o * 3);
          side[o] = s ? 1 : -1; al[o] = P[i].along;
        }
        if (i < n - 1) { const q = i * 2; idx.push(q, q + 1, q + 2, q + 1, q + 3, q + 2); }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      g.setAttribute("side", new THREE.BufferAttribute(side, 1));
      g.setAttribute("along", new THREE.BufferAttribute(al, 1));
      g.setAttribute("perp", new THREE.BufferAttribute(perp, 3));
      g.setIndex(idx);
      const t = TONES.indexOf(d.tone) >= 0 ? d.tone : "blue";
      const m = new THREE.ShaderMaterial({
        vertexShader: VS, fragmentShader: FS, transparent: true, depthWrite: false, side: THREE.DoubleSide,
        uniforms: { uColor: { value: srgb(tone[t] || new THREE.Color(0x0071e3)) }, uTime: { value: 0 }, uReach: { value: reachAlong + 1e-3 },
          uPx: { value: 1 }, uWidth: { value: 3 }, uFlow: { value: 1 } }
      });
      const mesh = new THREE.Mesh(g, m); mesh.renderOrder = 2; mesh.frustumCulled = false;
      scene.add(mesh);
      return { mesh, mat: m, tone: t };
    }
    function clearRoutes() { for (const r of routes) { scene.remove(r.mesh); r.mesh.geometry.dispose(); r.mat.dispose(); } routes = []; }

    /* controls */
    const ctrls = el("div", "hg-ctrls");
    function btn(html, label, fn) {
      const b = el("button", "hg-btn"); b.type = "button"; b.innerHTML = html;
      b.setAttribute("aria-label", label); b.title = label;
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      return b;
    }
    const zoomGrp = el("div", "hg-grp hg-glass");
    zoomGrp.append(btn(ICON.plus, "Zoom in", () => zoomBy(1 / 1.6)), btn(ICON.minus, "Zoom out", () => zoomBy(1.6)));
    const bDim = btn("2D", "Top-down view (2D)", () => toggleDim());
    const dimGrp = el("div", "hg-grp hg-glass"); dimGrp.appendChild(bDim);
    const bNorth = btn(ICON.compass, "North up", () => flyCam({ bearing: 0 }, 500));
    const northGrp = el("div", "hg-grp hg-glass"); northGrp.appendChild(bNorth);
    const bHome = btn(ICON.home, "Show every place", () => { closeSheet(true); });
    const bFull = btn(ICON.full, "Full screen", () => {
      try { if (document.fullscreenElement === root) document.exitFullscreen(); else if (root.requestFullscreen) root.requestFullscreen(); } catch (e) { /* not allowed */ }
    });
    const homeGrp = el("div", "hg-grp hg-glass"); homeGrp.append(bHome, bFull);
    ctrls.append(zoomGrp, dimGrp, northGrp, homeGrp);
    root.appendChild(ctrls);
    function setDimLabel() {
      const flat = cam.pitch < 5 && !(tween && tween.to.pitch > 5);
      bDim.textContent = flat ? "3D" : "2D";
      const l = flat ? "Tilted view (3D)" : "Top-down view (2D)";
      bDim.setAttribute("aria-label", l); bDim.title = l;
    }
    function toggleDim() {
      const goFlat = !(cam.pitch < 5);
      if (!goFlat) flyCam({ pitch: pitch3D }, 700); else { pitch3D = Math.max(20, cam.pitch); flyCam({ pitch: PITCH_2D }, 700); }
      setTimeout(setDimLabel, 0);
    }

    /* camera */
    const tmpV = new THREE.Vector3(), ground = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
    function minDist() { return 40; }
    function maxDist() { return 5200; }
    function applyCam() {
      cam.pitch = clamp(cam.pitch, PITCH_2D, MAX_PITCH);
      cam.dist = clamp(cam.dist, minDist(), maxDist());
      cam.x = clamp(cam.x, -proj.W * 0.75, proj.W * 0.75); cam.z = clamp(cam.z, -proj.H * 0.75, proj.H * 0.75);
      const p = cam.pitch * DEG, b = cam.bearing * DEG, h = cam.dist * Math.sin(p);
      camera.position.set(cam.x + h * Math.sin(b), cam.dist * Math.cos(p), cam.z + h * Math.cos(b));
      camera.up.set(-Math.sin(b), 0, -Math.cos(b)).lerp(new THREE.Vector3(0, 1, 0), Math.min(1, cam.pitch / 10)).normalize();
      camera.lookAt(cam.x, 0, cam.z);
      camera.near = Math.max(0.5, cam.dist * 0.02); camera.far = cam.dist * 12 + 2000;
      scene.fog.near = cam.dist * 1.6; scene.fog.far = cam.dist * 6;
      camera.updateProjectionMatrix();
      const nd = bNorth.querySelector(".hg-needle"); if (nd) nd.style.transform = "rotate(" + (-cam.bearing) + "deg)";
      nd && (nd.style.transformOrigin = "10px 10px");
    }
    function groundAt(px, py, out) {
      ndc.set(px / cssW * 2 - 1, -(py / cssH) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      return ray.ray.intersectPlane(ground, out);
    }
    function flyCam(to, ms) {
      const from = { x: cam.x, z: cam.z, dist: cam.dist, pitch: cam.pitch, bearing: cam.bearing };
      const target = Object.assign({}, from, to);
      let db = ((target.bearing - from.bearing) % 360 + 540) % 360 - 180; target.bearing = from.bearing + db;
      return new Promise((res) => {
        if (tween && tween.res) tween.res(false);
        tween = { from, to: target, t0: performance.now(), ms: reduce() ? 0 : (ms == null ? FLY_MS : ms), res };
        intro = null; schedule();
      });
    }
    function zoomBy(f, px, py) {
      const nd = clamp(cam.dist * f, minDist(), maxDist());
      if (px == null) { flyCam({ dist: nd }, 300); return; }
      const hit = groundAt(px, py, new THREE.Vector3());
      if (hit) { const k = nd / cam.dist; cam.x = hit.x + (cam.x - hit.x) * k; cam.z = hit.z + (cam.z - hit.z) * k; }
      cam.dist = nd; tween = null; dirty = true; schedule();
    }
    function fitBox(x0, z0, x1, z1, pitch, pad) {
      const vt = Math.tan(FOV * DEG / 2), aspect = cssW / cssH;
      const w = (x1 - x0) * pad, h = (z1 - z0) * pad;
      const dW = (w / 2) / (vt * aspect), dH = (h / 2) / vt * (1 + 0.75 * Math.sin(pitch * DEG));
      return { x: (x0 + x1) / 2, z: (z0 + z1) / 2 + h * 0.06 * Math.sin(pitch * DEG), dist: clamp(Math.max(dW, dH), 120, maxDist()), pitch, bearing: 0 };
    }
    function computeOverview() {
      let x0 = Infinity, z0 = Infinity, x1 = -Infinity, z1 = -Infinity;
      for (const p of pins) { if (!isFinite(p.x) || !isFinite(p.z)) continue; x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x); z0 = Math.min(z0, p.z); z1 = Math.max(z1, p.z); }
      if (!isFinite(x0)) { x0 = -proj.W / 2; x1 = proj.W / 2; z0 = -proj.H / 2; z1 = proj.H / 2; }
      const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2, w = Math.max(x1 - x0, 260), h = Math.max(z1 - z0, 200);
      return fitBox(cx - w / 2, cz - h / 2, cx + w / 2, cz + h / 2, pitch3D, 1.35);
    }

    /* selection and the sheet */
    const pub = (p) => p && { id: p.id, label: p.label, count: p.count, tone: p.tone, lat: p.lat, lon: p.lon, cc: p.cc };
    function selectPin(p, user) {
      if (!p) return false;
      const was = selected;
      selected = p;
      for (const f of Object.values(focusMeshes)) { f.selected = !!(p.cc && f.cc === p.cc); paintFocus(f); }
      renderSheet(p);
      sheet.hidden = false;
      requestAnimationFrame(() => sheet.classList.add("hg-show"));
      live.textContent = (p.sheet && p.sheet.title) || p.label;
      // fly to it: the country (or a region around the pin), tilted, the pin above and beside the card
      const f = p.cc && focusMeshes[p.cc];
      const pitch = 56;
      let v;
      if (f) { const [a, b, c, d] = f.bbox; const pad = 0.85 * Math.max(c - a, d - b, 70); v = fitBox(p.x - pad, p.z - pad, p.x + pad, p.z + pad, pitch, 1.15); }
      else v = { dist: 260 };
      const dist = clamp(v.dist, 150, 900), vt = Math.tan(FOV * DEG / 2), narrow = root.classList.contains("hg-narrow");
      const side = narrow ? 0 : dist * vt * (cssW / cssH) * 0.3;          // the card sits on the left: the pin goes right
      const fwd = dist * (narrow ? 0.2 : 0.08);                            // and a little up the screen
      const b = cam.bearing * DEG;
      flyCam({ x: p.x - side * Math.cos(b) + fwd * Math.sin(b), z: p.z + side * Math.sin(b) + fwd * Math.cos(b), dist, pitch }, FLY_MS)
        .then(setDimLabel);
      if (user && was !== p) hooks.emit("select", pub(p));
      dirty = true; schedule();
      return true;
    }
    function closeSheet(user) {
      const was = selected;
      selected = null;
      for (const f of Object.values(focusMeshes)) { f.selected = false; paintFocus(f); }
      sheet.classList.remove("hg-show");
      setTimeout(() => { if (!selected) sheet.hidden = true; }, reduce() ? 0 : 420);
      overview = computeOverview();
      flyCam(cam.pitch < 5 ? Object.assign({}, overview, { pitch: PITCH_2D }) : overview, FLY_MS);
      if (user && was) hooks.emit("select", null);
      if (was && was.btn && user) { try { root.focus({ preventScroll: true }); } catch (e) { /* old browser */ } }
    }
    function renderSheet(p) {
      const sh = p.sheet || {};
      sheet.textContent = "";
      sheet.appendChild(el("div", "hg-grab"));
      const head = el("div", "hg-head"), tx = el("div");
      const h = el("h3", "hg-title", sh.title || p.label || p.id); h.id = "hg-t-" + Math.random().toString(36).slice(2, 8);
      tx.appendChild(h);
      if (sh.subtitle) tx.appendChild(el("div", "hg-sub", sh.subtitle));
      const x = el("button", "hg-x"); x.type = "button"; x.innerHTML = ICON.close; x.setAttribute("aria-label", "Close"); x.title = "Close";
      x.addEventListener("click", (e) => { e.stopPropagation(); closeSheet(true); });
      head.append(tx, x); sheet.appendChild(head);
      sheet.setAttribute("aria-labelledby", h.id);
      const stats = Array.isArray(sh.stats) ? sh.stats.filter(Boolean).slice(0, 4) : [];
      if (stats.length) {
        const row = el("div", "hg-stats");
        for (const s of stats) {
          const c = el("div", "hg-stat");
          c.append(el("div", "hg-k", s.k != null ? s.k : ""), el("div", "hg-v" + (TONES.indexOf(s.tone) >= 0 ? " hg-t-" + s.tone : ""), s.v != null ? s.v : "—"));
          row.appendChild(c);
        }
        sheet.appendChild(row);
      }
      const bars = sh.bars;
      if (bars && Array.isArray(bars.items) && bars.items.length) {
        const box = el("div", "hg-bars"), plot = el("div", "hg-plot"), xl = el("div", "hg-xl");
        box.appendChild(el("div", "hg-bh", bars.title || ""));
        const its = bars.items.slice(0, 8);
        const max = Math.max(0, ...its.map((b) => num(b.v) || 0));
        plot.setAttribute("role", "img");
        plot.setAttribute("aria-label", (bars.title ? bars.title + ": " : "") + its.map((b) => (b.label || "") + " " + (num(b.v) || 0)).join(", "));
        const fills = [];
        for (const b of its) {
          const v = Math.max(0, num(b.v) || 0), colm = el("div", "hg-col");
          const bar = el("div", "hg-bar" + (b.hi ? " hg-hi" : ""));
          colm.append(el("div", "hg-bv", v.toLocaleString()), bar);
          plot.appendChild(colm); xl.appendChild(el("span", "", b.label || ""));
          fills.push([bar, max > 0 ? Math.max(4, v / max * 64) : 4]);
        }
        box.append(plot, xl); sheet.appendChild(box);
        const grow = () => fills.forEach(([b, hpx]) => { b.style.height = hpx + "px"; });
        if (reduce()) grow(); else requestAnimationFrame(() => requestAnimationFrame(grow));
      }
      const acts = Array.isArray(sh.actions) ? sh.actions.filter((a) => a && a.label).slice(0, 3) : [];
      if (acts.length) {
        const row = el("div", "hg-acts");
        for (const a of acts) {
          const b = el("button", "hg-act" + (a.primary ? " hg-primary" : ""), a.label); b.type = "button";
          b.addEventListener("click", (e) => { e.stopPropagation(); hooks.emit("action", pub(p), a.event != null ? a.event : a.label); });
          row.appendChild(b);
        }
        sheet.appendChild(row);
      }
      const rows = Array.isArray(sh.rows) ? sh.rows.filter(Boolean) : [];
      if (rows.length) {
        const ul = el("ul", "hg-rows");
        for (const r of rows.slice(0, 6)) {
          const li = el("li");
          if (r.ref != null && r.ref !== "") li.appendChild(el("span", "hg-ref", r.ref));
          li.appendChild(el("span", "hg-rt", r.text != null ? r.text : ""));
          ul.appendChild(li);
        }
        sheet.appendChild(ul);
      }
      if (sh.note) sheet.appendChild(el("div", "hg-foot", sh.note));
      sheet.scrollTop = 0;
    }

    /* pointer, wheel, keys */
    const ptrs = new Map();
    let drag = null;
    cv.addEventListener("contextmenu", (e) => e.preventDefault());
    cv.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0 && e.button !== 2) return;
      cv.setPointerCapture && cv.setPointerCapture(e.pointerId);
      ptrs.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      tween = null; intro = null;
      const rotate = e.button === 2 || e.ctrlKey || e.shiftKey || e.altKey;
      if (ptrs.size === 1) {
        const hit = groundAt(e.offsetX, e.offsetY, new THREE.Vector3());
        drag = { mode: rotate ? "rotate" : "pan", x: e.offsetX, y: e.offsetY, hit, moved: false };
      } else if (ptrs.size === 2) {
        const [a, b] = [...ptrs.values()];
        drag = { mode: "pinch", d: Math.hypot(a.x - b.x, a.y - b.y), ang: Math.atan2(b.y - a.y, b.x - a.x), dist: cam.dist, bearing: cam.bearing, moved: true };
      }
      root.classList.add("hg-drag");
    });
    cv.addEventListener("pointermove", (e) => {
      if (!ptrs.has(e.pointerId) || !drag) return;
      ptrs.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      if (drag.mode === "pinch" && ptrs.size >= 2) {
        const [a, b] = [...ptrs.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y), ang = Math.atan2(b.y - a.y, b.x - a.x);
        cam.dist = clamp(drag.dist * drag.d / Math.max(d, 1), minDist(), maxDist());
        cam.bearing = drag.bearing - (ang - drag.ang) / DEG;
      } else if (drag.mode === "rotate") {
        cam.bearing += (e.offsetX - drag.x) * 0.3; cam.pitch = clamp(cam.pitch - (e.offsetY - drag.y) * 0.25, PITCH_2D, MAX_PITCH);
        drag.x = e.offsetX; drag.y = e.offsetY; setDimLabel();
      } else if (drag.mode === "pan" && drag.hit) {
        applyCam();
        const now = groundAt(e.offsetX, e.offsetY, new THREE.Vector3());
        if (now) { cam.x += drag.hit.x - now.x; cam.z += drag.hit.z - now.z; }
      }
      if (Math.abs(e.offsetX - (drag.x || 0)) + Math.abs(e.offsetY - (drag.y || 0)) > 3) drag.moved = true;
      dirty = true; schedule();
    });
    const up = (e) => {
      ptrs.delete(e.pointerId);
      if (!ptrs.size) { drag = null; root.classList.remove("hg-drag"); }
    };
    cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
    cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomBy(Math.exp(clamp(e.deltaY * (e.deltaMode === 1 ? 16 : 1), -200, 200) * 0.0018), e.offsetX, e.offsetY);
    }, { passive: false });
    root.addEventListener("keydown", (e) => {
      if (e.target !== root) return;
      const step = cam.dist * 0.08, b = cam.bearing * DEG;
      const mv = (dx, dz) => { cam.x += dx * Math.cos(b) + dz * Math.sin(b); cam.z += -dx * Math.sin(b) + dz * Math.cos(b); tween = null; dirty = true; schedule(); };
      if (e.key === "ArrowLeft") mv(-step, 0); else if (e.key === "ArrowRight") mv(step, 0);
      else if (e.key === "ArrowUp") mv(0, -step); else if (e.key === "ArrowDown") mv(0, step);
      else if (e.key === "+" || e.key === "=") zoomBy(1 / 1.4); else if (e.key === "-") zoomBy(1.4);
      else return;
      e.preventDefault();
    });
    const onKey = (e) => {
      if (e.key !== "Escape" || !selected || e.defaultPrevented) return;
      // a dialog opened over the page (the shipment list) closes first
      const modal = [...document.querySelectorAll('[aria-modal="true"]')].some((m) => !m.hidden && m.getClientRects().length > 0 && !root.contains(m));
      if (modal) return;
      e.preventDefault(); closeSheet(true);
    };
    document.addEventListener("keydown", onKey, true);      // before the page's own Esc handlers close their dialog

    /* drawing */
    function schedule() { if (!raf && !destroyed) raf = requestAnimationFrame(frame); }
    const routesAnimate = () => routes.length && visible && !document.hidden && !reduce();
    const probe = { last: 0, elapsed: 0, deltas: [], done: false };
    function frame(now) {
      raf = 0;
      if (destroyed) return;
      if (!t0) t0 = now;
      let active = false;
      if (tween) {
        const k = tween.ms ? clamp((now - tween.t0) / tween.ms, 0, 1) : 1, e = ease(k);
        for (const key of ["x", "z", "dist", "pitch", "bearing"]) cam[key] = lerp(tween.from[key], tween.to[key], e);
        if (k >= 1) { const r = tween.res; tween = null; r && r(true); setDimLabel(); } else active = true;
      }
      if (intro) {
        const k = clamp((now - intro.t0) / intro.ms, 0, 1), e = ease(k);
        for (const key of ["x", "z", "dist", "pitch"]) cam[key] = lerp(intro.from[key], intro.to[key], e);
        if (k >= 1) intro = null; else active = true;
      }
      applyCam();
      // relief and pins keep a constant on-screen size at every zoom
      const vt = Math.tan(FOV * DEG / 2), wpp = 2 * cam.dist * vt / cssH;      // world units per pixel at the target
      const ls = Math.max(0.02, LAND_PX * wpp / LAND_H), fs = Math.max(0.02, FOCUS_PX * wpp / LAND_H);
      landGroup.scale.y = ls; focusGroup.scale.y = 1;
      for (const f of Object.values(focusMeshes)) {
        const goal = f.selected ? 1.5 : 1;
        f.lift += (goal - f.lift) * (reduce() ? 1 : 0.18);
        if (Math.abs(goal - f.lift) > 0.01) active = true;
        f.group.scale.y = fs * f.lift;
      }
      const landTop = LAND_H * fs;
      for (const p of pins) {
        if (!isFinite(p.x) || !isFinite(p.z)) { p.group.visible = false; continue; }
        const dpp = 2 * camera.position.distanceTo(tmpV.set(p.x, 0, p.z)) * vt / cssH;
        let s = 12.5 * dpp * (p === selected ? 1.22 : 1);
        if (p.drop < 1) { p.drop = clamp((now - t0 - 120 - p.i * 70) / 520, 0, 1); active = active || p.drop < 1; }
        const dk = reduce() ? 1 : p.drop, bounce = dk < 1 ? (1 - ease(dk)) * 46 * dpp : 0;
        p.group.position.set(p.x, landTop * (p.cc && focusMeshes[p.cc] ? focusMeshes[p.cc].lift : 1) + bounce, p.z);
        p.group.scale.setScalar(s); p.r = s / dpp; p.group.visible = dk > 0 || reduce();
      }
      const pxPerWorld = 1 / wpp;
      for (const r of routes) {
        r.mat.uniforms.uWidth.value = 6.5 * wpp; r.mat.uniforms.uPx.value = pxPerWorld;
        r.mat.uniforms.uTime.value = reduce() ? 0 : now / 1000; r.mat.uniforms.uFlow.value = reduce() ? 0 : 1;
        r.mesh.position.y = landTop;
      }
      renderer.render(scene, camera);
      overlay();
      dirty = false;
      // speed check: the first 2 s of drawing, the loop kept running
      if (!probe.done && !document.hidden) {
        if (probe.last) { const d = now - probe.last; if (d < 1000) { probe.deltas.push(d); probe.elapsed += d; } }
        probe.last = now;
        if (probe.elapsed >= PROBE_MS) {
          probe.done = true;
          const s = probe.deltas.slice().sort((a, b) => a - b), med = s.length ? s[s.length >> 1] : 0;
          root.dataset.medianFrameMs = med.toFixed(1);
          root.dataset.triangles = String(renderer.info.render.triangles);
          if (med > SLOW_MS && !opts.keep3d) { hooks.slow(med); return; }
          log(med > SLOW_MS ? "warn" : "info", "mode: 3d kept — median frame " + med.toFixed(1) + " ms over the first 2 s (limit " + SLOW_MS + " ms" +
            (med > SLOW_MS ? "; kept only because of ?map=3d" : "") + "), " + renderer.info.render.triangles + " triangles");
        } else active = true;
      }
      if (active || dirty || routesAnimate()) schedule();
    }
    const tmpP = new THREE.Vector3();
    function project(x, y, z) { tmpP.set(x, y, z).project(camera); return [(tmpP.x + 1) / 2 * cssW, (1 - tmpP.y) / 2 * cssH, tmpP.z]; }
    function overlay() {
      const placed = [], heads = [];
      const order = pins.slice().sort((a, b) => (b === selected) - (a === selected) || (b.count || 0) - (a.count || 0));
      for (const p of order) {
        if (!p.group.visible) { p.btn.hidden = true; p.lab.hidden = true; continue; }
        const s = p.group.scale.x, base = p.group.position;
        const [hx, hy, hz] = project(base.x, base.y + 2.55 * s, base.z);
        const [bx, by] = project(base.x, base.y, base.z);
        const off = hz > 1 || hx < -40 || hy < -40 || hx > cssW + 40 || hy > cssH + 40;
        p.btn.hidden = off; p.lab.hidden = off || !p.label;
        if (off) continue;
        const r = Math.max(10, p.r);
        p.btn.style.width = p.btn.style.height = (2 * r) + "px";
        p.btn.style.transform = "translate(" + (hx - r).toFixed(1) + "px," + (hy - r).toFixed(1) + "px)";
        p.btn.style.zIndex = String(1000 + Math.round(hy));
        p.lab.style.zIndex = String(1000 + Math.round(hy));
        placed.push([hx - r, hy - r, 2 * r, 2 * r]);
        heads.push([p, hx, hy, bx, by, r]);
      }
      // labels after every head is placed, so no label lands on another pin
      for (const [p, hx, hy, bx, by, r] of heads) {
        if (!p.label) continue;
        const lw = p.lab.offsetWidth || 30, lh = p.lab.offsetHeight || 18;
        const cands = [[bx - lw / 2, by + 5], [hx + r + 4, hy - lh / 2], [hx - r - 4 - lw, hy - lh / 2], [hx - lw / 2, hy - r - 4 - lh], [bx - lw / 2, by + 5 + lh + 2]];
        let pick = cands[0];
        for (const c of cands) {
          const hit = placed.some((q) => c[0] < q[0] + q[2] && c[0] + lw > q[0] && c[1] < q[1] + q[3] && c[1] + lh > q[1]);
          if (!hit) { pick = c; break; }
        }
        placed.push([pick[0], pick[1], lw, lh]);
        p.lab.style.transform = "translate(" + pick[0].toFixed(1) + "px," + pick[1].toFixed(1) + "px)";
      }
    }

    /* size, theme, visibility */
    function resize() {
      const w = root.clientWidth || 300, h = root.clientHeight || 300;
      if (w === cssW && h === cssH) return;
      cssW = w; cssH = h;
      renderer.setSize(w, h, false); camera.aspect = w / h;
      root.classList.toggle("hg-narrow", w < 560);
      dirty = true; schedule();
    }
    const ro = window.ResizeObserver ? new ResizeObserver(resize) : null;
    ro ? ro.observe(root) : window.addEventListener("resize", resize);
    const io = window.IntersectionObserver ? new IntersectionObserver((es) => { visible = es[es.length - 1].isIntersecting; if (visible) schedule(); }) : null;
    io && io.observe(root);
    const onVis = () => { probe.last = 0; if (!document.hidden) schedule(); };     // time in a hidden tab is not a frame
    document.addEventListener("visibilitychange", onVis);
    const mo = new MutationObserver(() => readTheme());
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
    const darkMq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    const onScheme = () => readTheme();
    darkMq && (darkMq.addEventListener ? darkMq.addEventListener("change", onScheme) : darkMq.addListener(onScheme));
    cv.addEventListener("webglcontextlost", (e) => { e.preventDefault(); hooks.lost(); });

    /* data */
    let pending = null;
    function setData(d) {
      d = d || {};
      if (!worldReady) { pending = d; return; }
      countLabel = d.countLabel ? String(d.countLabel) : "shipments";
      const nf = new Set((d.focus || []).map(String));
      const changed = nf.size !== focusSet.size || [...nf].some((c) => !focusSet.has(c));
      if (changed) {        // rebuild the land: focus countries are their own raised meshes
        focusSet = nf;
        landGroup.children.slice().forEach((c) => { landGroup.remove(c); c.geometry.dispose(); });
        const base = [];
        for (const [cc, c] of Object.entries(byCountry)) if (!focusSet.has(cc)) for (const p of c.polys) base.push(p);
        landGroup.add(new THREE.Mesh(extrude(base), [mat.landTop, mat.landSide]), new THREE.LineSegments(borderGeom(base), mat.border));
        buildFocus();
      }
      const keep = selected && selected.id;
      clearPins(); clearRoutes();
      const first = !overview;
      (d.pins || []).forEach((pd, i) => { if (pd && pd.id != null && isFinite(num(pd.lat)) && isFinite(num(pd.lon))) pins.push(makePin(pd, i)); });
      if (!first) for (const p of pins) p.drop = 1; else for (const p of pins) p.drop = reduce() ? 1 : 0;
      for (const rd of d.routes || []) { const r = makeRoute(rd || {}); if (r) routes.push(r); }
      note.hidden = true;
      if (!pins.length) { note.textContent = "No places to show yet"; note.hidden = false; }
      if (first) {
        overview = computeOverview();
        if (reduce()) Object.assign(cam, overview);
        else {
          Object.assign(cam, overview, { dist: overview.dist * 1.45, pitch: 22, z: overview.z + overview.dist * 0.1 });
          intro = { from: Object.assign({}, cam), to: Object.assign({}, overview), t0: performance.now(), ms: 1600 };
        }
      }
      selected = null;
      if (keep) { const p = pins.find((x) => x.id === keep); if (p) { selected = p; renderSheet(p); for (const f of Object.values(focusMeshes)) { f.selected = !!(p.cc && f.cc === p.cc); paintFocus(f); } } else closeSheet(false); }
      setDimLabel();
      dirty = true; schedule();
    }

    readTheme();
    resize();
    applyCam();
    fetch(opts.worldUrl || "/static/maps/world.json", { credentials: "same-origin" })
      .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then((j) => {
        if (destroyed) return;
        buildWorld(j);
        note.hidden = true;
        setData(pending || { pins: [] });
        pending = null;
      })
      .catch((e) => {
        if (destroyed) return;
        worldFailed = true; worldReady = true;
        note.textContent = "Map outline unavailable"; note.hidden = false;
        log("warn", "world outline not loaded: " + (e && e.message ? e.message : e));
        setData(pending || { pins: [] });
      });

    return {
      root,
      setData,
      select(id) {
        if (id == null) { if (selected) closeSheet(false); return true; }
        const p = pins.find((x) => x.id === String(id));
        return p ? selectPin(p, false) : false;
      },
      flyTo(t, o) {
        if (!t) return Promise.resolve(false);
        const to = {};
        if (isFinite(num(t.lat)) && isFinite(num(t.lon))) { to.x = wx(num(t.lon)); to.z = wz(num(t.lat)); }
        if (isFinite(num(t.zoom))) to.dist = (overview ? overview.dist : cam.dist) / Math.max(0.1, num(t.zoom));
        if (isFinite(num(t.pitch))) to.pitch = num(t.pitch);
        if (isFinite(num(t.bearing))) to.bearing = num(t.bearing);
        return flyCam(to, o && isFinite(num(o.duration)) ? num(o.duration) : FLY_MS);
      },
      fit() { overview = computeOverview(); return flyCam(overview, FLY_MS); },
      getView() { return Object.assign({ mode: "3d" }, cam); },
      destroy() {
        destroyed = true;
        if (raf) cancelAnimationFrame(raf);
        document.removeEventListener("keydown", onKey, true);
        document.removeEventListener("visibilitychange", onVis);
        darkMq && (darkMq.removeEventListener ? darkMq.removeEventListener("change", onScheme) : darkMq.removeListener(onScheme));
        ro ? ro.disconnect() : window.removeEventListener("resize", resize);
        io && io.disconnect(); mo.disconnect();
        clearPins(); clearRoutes();
        scene.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
        for (const m of Object.values(mat)) m && m.dispose && m.dispose();
        renderer.dispose();
        root.remove();
      }
    };
  }

  /* ───────────────────────── the map the page gets ───────────────────────── */
  function mount(host, opts) {
    opts = opts || {};
    const L = { select: [], action: [], cluster: [] };
    const emit = (name, ...a) => (L[name] || []).slice().forEach((f) => { try { f(...a); } catch (e) { console.error(e); } });
    let impl = null, mode = null, data = null, destroyed = false, switched = false;

    function setMode(m, reason, level) {
      mode = m; host.dataset.mapMode = m;
      log(level || "info", "mode: " + m + " — " + reason);
    }
    function use2D(reason) {
      if (destroyed || mode === "2d") return;
      switched = true;
      if (impl) { try { impl.destroy(); } catch (e) { /* already gone */ } }
      host.textContent = "";
      impl = Map2D.mount(host, opts);
      for (const name of Object.keys(L)) impl.on(name, (...a) => emit(name, ...a));
      if (data) impl.setData(data);
      setMode("2d", reason, "warn");
    }
    // ?map=2d or ?map=3d in the page address overrides the choice (support and screenshots); 3d still needs WebGL
    let ask = "";
    try { ask = (new URLSearchParams(location.search).get("map") || "").toLowerCase(); } catch (e) { /* old browser */ }
    if (ask === "2d") opts = Object.assign({}, opts, { force2d: true });
    if (ask === "3d") opts = Object.assign({}, opts, { keep3d: true });
    const kind = opts.force2d ? null : hasWebGL();
    if (!kind) use2D(opts.force2d ? "2D requested (?map=2d)" : "WebGL unavailable in this browser");
    else {
      host.dataset.mapMode = "loading";
      import(VENDOR).then((THREE) => {
        if (destroyed || switched) return;
        try {
          impl = GLMap(THREE, host, opts, {
            emit,
            slow: (ms) => use2D("median frame " + ms.toFixed(1) + " ms over the first 2 s is above " + SLOW_MS + " ms; switched once, will not switch back"),
            lost: () => use2D("the WebGL context was lost")
          });
          setMode("3d", kind + " and three.js r" + THREE.REVISION + " loaded; " + (opts.keep3d ? "kept by ?map=3d, no speed check" : "checking speed for 2 s"));
          if (data) impl.setData(data);
        } catch (e) {
          use2D("the 3D map could not start: " + (e && e.message ? e.message : e));
        }
      }).catch((e) => use2D("three.js did not load: " + (e && e.message ? e.message : e)));
    }

    const api = {
      setData(d) { data = d; if (impl) impl.setData(d); },
      on(name, fn) {
        if (!L[name] || typeof fn !== "function") return () => {};
        L[name].push(fn);
        return () => api.off(name, fn);
      },
      off(name, fn) { const l = L[name]; if (!l) return; const i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); },
      select(id) { return impl ? impl.select(id) : false; },
      flyTo(t, o) { return impl && impl.flyTo ? impl.flyTo(t, o) : Promise.resolve(false); },
      fit() { return impl && impl.fit ? impl.fit() : Promise.resolve(false); },
      get mode() { return mode; },
      destroy() { destroyed = true; if (impl) impl.destroy(); impl = null; }
    };
    return api;
  }

  window.HubMap2D = Map2D;
  window.HubMap3D = { version: Map2D.version, gl: true, mount, hasWebGL };
})();
