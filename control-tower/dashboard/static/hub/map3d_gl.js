/* ATLAS Control Tower — the 3D map (WebGL, three.js), with the 2D map as fallback.

   Apple Maps visual language in our own code and palette (no Apple assets):
   near-flat matte clay land, a calm sea gradient fading into the sky, round
   pins with a white number on a short stem, a thick blue route, frosted
   controls and a bottom sheet. Loaded after hub/map3d.js. It keeps the same calls (HubMap3D.mount, setData,
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

  /* ───────────────────────── look: our own palette in the Apple Maps visual language ───────────────────────── */
  const PAL = {
    light: { land: "#F2F1EC", focus: "#E8E6DF", sel: "#E1E4EA", seaNear: "#AAD3F2", seaFar: "#C9E4F7", sky: "#E9F2F9",
             border: "#D4D2CB", focusBorder: "#C9C6BD", shadow: 0.11, country: "#A19E95" },
    dark:  { land: "#2B2D31", focus: "#33363B", sel: "#353C48", seaNear: "#1A2433", seaFar: "#1F2B3C", sky: "#141B26",
             border: "#3A3D42", focusBorder: "#474B52", shadow: 0.34, country: "#7E838B" }
  };
  const PIN = {
    light: { red: "#FF3B30", amber: "#FF9500", green: "#34C759", blue: "#0A84FF", teal: "#30B0C7" },
    dark:  { red: "#FF453A", amber: "#FF9F0A", green: "#30D158", blue: "#0A84FF", teal: "#40C8E0" }
  };
  const ROUTE = "#0A84FF";

  /* ───────────────────────── styles (injected once) ───────────────────────── */
  const CSS = `
.hg-root{--hg-font:-apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif;position:relative;width:100%;height:100%;
  min-height:300px;overflow:hidden;background:#C9E4F7;color:#1d1d1f;font:14px/1.4 var(--hg-font);touch-action:pan-y;
  -webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;outline:none;isolation:isolate}
.hg-root.hg-dark{background:#1F2B3C;color:#f2f2f7}
.hg-root *,.hg-root *::before,.hg-root *::after{box-sizing:border-box}
.hg-root:focus-visible{box-shadow:inset 0 0 0 2px #0A84FF}
.hg-cv{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab}
.hg-root.hg-drag .hg-cv{cursor:grabbing}
.hg-ov{position:absolute;inset:0;z-index:1;pointer-events:none;overflow:hidden}
.hg-cty{position:absolute;left:0;top:0;font:500 10px/1.25 var(--hg-font);letter-spacing:.16em;text-transform:uppercase;white-space:pre;text-align:center;
  color:#A19E95;text-shadow:0 0 3px rgba(255,255,255,.9),0 0 1px rgba(255,255,255,.9);will-change:transform}
.hg-dark .hg-cty{color:#7E838B;text-shadow:0 0 3px rgba(0,0,0,.6)}
.hg-pin{position:absolute;left:0;top:0;height:26px;min-width:26px;padding:0 6px;margin:0;border:2px solid #fff;border-radius:13px;
  color:#fff;font:700 11.5px/22px var(--hg-font);font-variant-numeric:tabular-nums;text-align:center;pointer-events:auto;cursor:pointer;
  box-shadow:0 2px 6px rgba(0,0,0,.28),0 0 0 .5px rgba(0,0,0,.08);will-change:transform;transition:box-shadow .2s}
.hg-pin.hg-zero{height:16px;min-width:16px;padding:0;border-width:2.5px;font-size:0;opacity:.9}
.hg-pin.hg-on{box-shadow:0 0 0 3px rgba(10,132,255,.35),0 3px 10px rgba(0,0,0,.3)}
.hg-pin:focus-visible{outline:none;box-shadow:0 0 0 3px #0A84FF,0 2px 6px rgba(0,0,0,.28)}
.hg-lab{position:absolute;left:0;top:0;padding:2px 8px;border-radius:999px;font:700 11px/16px var(--hg-font);letter-spacing:.02em;
  white-space:nowrap;color:#1d1d1f;background:#fff;box-shadow:0 1px 4px rgba(0,0,0,.18);will-change:transform}
.hg-dark .hg-lab{background:#2C2C2E;color:#f2f2f7;box-shadow:0 1px 4px rgba(0,0,0,.5)}
.hg-pin[hidden],.hg-lab[hidden],.hg-cty[hidden]{display:none}
.hg-glass{background:rgba(255,255,255,.7);-webkit-backdrop-filter:saturate(180%) blur(20px);backdrop-filter:saturate(180%) blur(20px);
  box-shadow:0 2px 12px rgba(0,0,0,.12),0 0 0 .5px rgba(0,0,0,.06)}
.hg-dark .hg-glass{background:rgba(44,44,46,.7);box-shadow:0 2px 12px rgba(0,0,0,.4),0 0 0 .5px rgba(255,255,255,.08)}
.hg-ctrls{position:absolute;top:12px;right:12px;z-index:3;display:flex;flex-direction:column;gap:8px;align-items:flex-end}
.hg-grp{display:flex;flex-direction:column;border-radius:12px;overflow:hidden}
.hg-btn{width:40px;height:40px;display:flex;align-items:center;justify-content:center;border:0;margin:0;padding:0;background:transparent;
  color:#3a3a3c;cursor:pointer;font:600 13px/1 var(--hg-font)}
.hg-dark .hg-btn{color:#e5e5ea}
.hg-btn:hover{background:rgba(127,127,127,.12)}
.hg-btn:focus-visible{outline:2px solid #0A84FF;outline-offset:-2px}
.hg-btn svg{width:18px;height:18px}
.hg-grp .hg-btn+.hg-btn{border-top:.5px solid rgba(127,127,127,.25)}
.hg-note{position:absolute;left:12px;top:12px;z-index:2;padding:6px 12px;border-radius:999px;font-size:12px}
.hg-note[hidden]{display:none}
.hg-sheet{position:absolute;left:8px;right:8px;bottom:0;margin:0 auto;max-width:560px;z-index:4;max-height:40%;overflow:auto;
  border-radius:20px 20px 0 0;padding:6px 16px 12px;transform:translateY(calc(100% + 12px));
  transition:transform .55s cubic-bezier(.2,1.25,.35,1);pointer-events:none;overscroll-behavior:contain;touch-action:pan-y}
.hg-sheet.hg-show{transform:none;pointer-events:auto}
.hg-sheet.hg-pull{transition:none}
.hg-grab{width:36px;height:5px;border-radius:3px;background:rgba(127,127,127,.45);margin:0 auto 8px;cursor:grab}
.hg-head{display:flex;align-items:center;gap:10px}
.hg-head>div{flex:1;min-width:0}
.hg-title{margin:0;font:700 18px/1.2 var(--hg-font);letter-spacing:-.01em}
.hg-sub{font-size:12px;color:#86868b;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hg-x{flex:none;width:28px;height:28px;border-radius:50%;border:0;background:rgba(127,127,127,.16);color:inherit;cursor:pointer;
  display:flex;align-items:center;justify-content:center}
.hg-x svg{width:12px;height:12px}
.hg-x:focus-visible,.hg-act:focus-visible{outline:2px solid #0A84FF;outline-offset:2px}
.hg-chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 2px}
.hg-chip{display:inline-flex;align-items:baseline;gap:4px;padding:4px 10px;border-radius:999px;background:rgba(127,127,127,.13);
  font-size:12px;color:#6e6e73;white-space:nowrap}
.hg-dark .hg-chip{color:#aeaeb2}
.hg-chip b{font-size:14px;font-weight:700;color:#1d1d1f;font-variant-numeric:tabular-nums}
.hg-dark .hg-chip b{color:#f2f2f7}
.hg-chip.hg-t-red b{color:#FF3B30}.hg-dark .hg-chip.hg-t-red b{color:#FF453A}
.hg-chip.hg-t-green b{color:#34C759}
.hg-bars{display:grid;grid-template-columns:auto 1fr auto;gap:3px 8px;align-items:center;margin-top:10px;font-size:11px;color:#86868b}
.hg-track{height:6px;border-radius:3px;background:rgba(127,127,127,.15);overflow:hidden}
.hg-fill{height:100%;width:0;border-radius:3px;background:#0A84FF;opacity:.45;transition:width .6s cubic-bezier(.2,.8,.2,1)}
.hg-fill.hg-hi{opacity:1}
.hg-bn{font-weight:650;color:inherit;font-variant-numeric:tabular-nums;text-align:right}
.hg-acts{display:flex;gap:8px;margin-top:12px}
.hg-act{height:34px;padding:0 16px;border-radius:999px;border:0;background:rgba(127,127,127,.16);color:inherit;font:600 13px/1 var(--hg-font);cursor:pointer}
.hg-act.hg-primary{background:#0A84FF;color:#fff}
.hg-sr{position:absolute!important;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
@media (prefers-reduced-motion:reduce){.hg-sheet,.hg-fill,.hg-needle,.hg-pin{transition:none!important}}`;

  const ICON = {
    plus: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M10 4v12M4 10h12"/></svg>',
    minus: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 10h12"/></svg>',
    compass: '<svg viewBox="0 0 20 20"><g class="hg-needle"><path d="M10 2.5l3 7.5h-6z" fill="#FF3B30"/><path d="M10 17.5l-3-7.5h6z" fill="currentColor" opacity=".45"/></g></svg>',
    recenter: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M16.5 3.5L3.5 9l5.5 1.6L10.6 16z"/></svg>',
    close: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M5 5l10 10M15 5L5 15"/></svg>'
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
    const hemi = new THREE.HemisphereLight(0xffffff, 0xf2f1ec, 1.9);
    const sun = new THREE.DirectionalLight(0xffffff, 1.2);
    sun.position.set(-0.8, 1.3, -0.7);                         // a gentle sun from the top left
    scene.add(hemi, sun);
    scene.fog = new THREE.Fog(0xe9f2f9, 3000, 9000);            // the far edge fades into the sky

    const W0 = 2000, H0 = 1094.3;
    let proj = makeProj(W0, H0, 78, -56), OX = W0 / 2, OZ = H0 / 2;
    const wx = (lon) => proj.x(lon) - OX, wz = (lat) => proj.y(lat) - OZ;

    const cam = { x: 0, z: 0, dist: 1500, pitch: PITCH_3D, bearing: 0 };
    let pitch3D = PITCH_3D, overview = null, tween = null, intro = null, wheel = null;
    let cssW = 300, cssH = 300, destroyed = false, raf = 0, dirty = true, visible = true, t0 = 0, dark = false;

    const C = (hex) => new THREE.Color(hex);
    const S3 = (hex) => { const o = { r: 0, g: 0, b: 0 }; new THREE.Color(hex).getRGB(o, THREE.SRGBColorSpace); return new THREE.Vector3(o.r, o.g, o.b); };
    const mat = {
      // sea: a calm gradient, near to far, then the sky (same fade as the land's fog)
      water: new THREE.ShaderMaterial({
        uniforms: { uNear: { value: S3("#AAD3F2") }, uFar: { value: S3("#C9E4F7") }, uSky: { value: S3("#E9F2F9") },
          uD0: { value: 0 }, uD1: { value: 1 }, uF0: { value: 1 }, uF1: { value: 2 } },
        vertexShader: `varying float vD; void main(){ vec4 v = viewMatrix * modelMatrix * vec4(position, 1.0); vD = -v.z; gl_Position = projectionMatrix * v; }`,
        fragmentShader: `uniform vec3 uNear, uFar, uSky; uniform float uD0, uD1, uF0, uF1; varying float vD;
void main(){ vec3 c = mix(uNear, uFar, smoothstep(uD0, uD1, vD)); gl_FragColor = vec4(mix(c, uSky, smoothstep(uF0, uF1, vD)), 1.0); }`
      }),
      shadow: new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, depthWrite: false, opacity: 0.11, fog: false }),
      // matte clay: one colour for top and sides, no dark walls
      landTop: new THREE.MeshStandardMaterial({ color: 0xf2f1ec, roughness: 1, metalness: 0, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
      landSide: new THREE.MeshStandardMaterial({ color: 0xf2f1ec, roughness: 1, metalness: 0 }),
      border: new THREE.LineBasicMaterial({ color: 0xd4d2cb }),
      focusBorder: new THREE.LineBasicMaterial({ color: 0xc9c6bd }),
      stem: new THREE.MeshBasicMaterial({ color: 0xffffff }),
      pinShadow: null
    };
    let pal = PAL.light, pinPal = PIN.light;
    function readTheme() {
      const v = getComputedStyle(root).getPropertyValue("--hub-water").trim();
      const c = new THREE.Color(); try { c.setStyle(v || "#dfe9f3"); } catch (e) { c.set(0xdfe9f3); }
      dark = c.getHSL({ h: 0, s: 0, l: 0 }).l < 0.3;
      pal = dark ? PAL.dark : PAL.light; pinPal = dark ? PIN.dark : PIN.light;
      root.classList.toggle("hg-dark", dark);
      const sky = C(pal.sky);
      scene.background = sky; scene.fog.color.copy(sky); renderer.setClearColor(sky);
      mat.water.uniforms.uNear.value = S3(pal.seaNear); mat.water.uniforms.uFar.value = S3(pal.seaFar); mat.water.uniforms.uSky.value = S3(pal.sky);
      mat.landTop.color.set(pal.land); mat.landSide.color.set(pal.land);
      mat.landSide.emissive.set(pal.land).multiplyScalar(dark ? 0.12 : 0.18);   // walls stay as light as the land
      mat.border.color.set(pal.border); mat.focusBorder.color.set(pal.focusBorder);
      mat.shadow.opacity = pal.shadow;
      hemi.groundColor.set(pal.land);
      hemi.intensity = dark ? 2.0 : 1.95; sun.intensity = dark ? 0.9 : 1.15;
      for (const f of Object.values(focusMeshes)) paintFocus(f);
      for (const p of pins) paintPin(p);
      dirty = true; schedule();
    }

    /* world: sea, soft shadow, land, borders */
    const waterMesh = new THREE.Mesh(new THREE.PlaneGeometry(40000, 40000), mat.water);
    waterMesh.rotation.x = -Math.PI / 2; waterMesh.position.y = -0.05;
    scene.add(waterMesh);
    const landGroup = new THREE.Group(); scene.add(landGroup);
    const focusGroup = new THREE.Group(); scene.add(focusGroup);
    let byCountry = {}, focusMeshes = {}, focusSet = new Set(), worldReady = false, countryLabels = [];
    const LAND_PX = 2.2, FOCUS_PX = 4.2, LAND_H = 1.5;     // almost flat: a few pixels of relief at every zoom

    function consolidate(g) {       // caps first, sides second: two draw calls for any number of shapes
      const pos = g.attributes.position.array, nor = g.attributes.normal.array;
      const caps = [], sides = [];
      for (const gr of g.groups) (gr.materialIndex === 0 ? caps : sides).push(gr);
      const P = new Float32Array(pos.length), N = new Float32Array(pos.length);
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
      const v2 = (r) => { const a = []; for (let i = 0; i < r.length; i += 2) a.push(new THREE.Vector2(r[i] - OX, OZ - r[i + 1])); return a; };
      return polys.map((p) => { const s = new THREE.Shape(v2(p.ring)); for (const h of p.holes) s.holes.push(new THREE.Path(v2(h))); return s; });
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
    function shadowTexture(all) {        // a soft, blurred shadow cast down-right of the land (sun top-left)
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
      const OFF = 20000;          // drawn off the canvas; only its blurred shadow lands on it (works in every browser)
      for (const [blur, dx, dy, a] of [[18, 2.2, 3, 0.55], [5, 0.7, 0.9, 0.55]]) {
        x.save(); x.shadowColor = "rgba(0,0,0," + a + ")"; x.shadowBlur = blur; x.shadowOffsetX = OFF * S;
        path(dx - OFF, dy); x.fillStyle = "#000"; x.fill("evenodd"); x.restore();
      }
      const t = new THREE.CanvasTexture(c); t.anisotropy = Math.min(4, renderer.capabilities.getMaxAnisotropy());
      return t;
    }
    function paintFocus(f) {
      f.top.color.set(f.selected ? pal.sel : pal.focus);
      f.side.color.set(f.selected ? pal.sel : pal.focus);
      f.side.emissive.copy(f.side.color).multiplyScalar(dark ? 0.12 : 0.18);
    }
    function buildWorld(j) {
      const t = performance.now();
      const pr = (j && j.projection) || {}, vb = (j && j.viewBox) || [0, 0, W0, H0];
      proj = makeProj(+pr.width || +vb[2] || W0, +vb[3] || H0, isFinite(+pr.lat_top) ? +pr.lat_top : 78, isFinite(+pr.lat_bottom) ? +pr.lat_bottom : -56);
      OX = proj.W / 2; OZ = proj.H / 2;
      const all = [];
      byCountry = {};
      for (const c of (j && j.countries) || []) {
        const polys = countryPolys(c.d, proj.W, 0.6);
        if (!polys.length) continue;
        const area = polys.reduce((s, p) => s + Math.abs(area2(p.ring)), 0);
        byCountry[c.cc] = { polys, name: c.name, label: Array.isArray(c.label) ? c.label : null, area };
        for (const p of polys) all.push(p);
      }
      buildBase();
      const tex = shadowTexture(all);
      if (tex) {
        mat.shadow.map = tex; mat.shadow.needsUpdate = true;
        const sh = new THREE.Mesh(new THREE.PlaneGeometry(proj.W, proj.H), mat.shadow);
        sh.rotation.x = -Math.PI / 2; sh.position.set(0, 0.02, 0); sh.renderOrder = -1;
        scene.add(sh);
      }
      // country names: subtle, small, letter-spaced; the larger countries first
      countryLabels = Object.entries(byCountry).filter(([, c]) => c.label && c.name && c.area > 250)
        .sort((a, b) => b[1].area - a[1].area).slice(0, 120)
        .map(([cc, c]) => { const e = el("div", "hg-cty", twoLines(c.name)); e.hidden = true; e.setAttribute("aria-hidden", "true"); ov.appendChild(e); return { cc, el: e, x: c.label[0] - OX, z: c.label[1] - OZ, size: Math.sqrt(c.area) }; });
      buildFocus();
      worldReady = true;
      log("info", "3D land built in " + Math.round(performance.now() - t) + " ms (" + Object.keys(byCountry).length + " countries)");
    }
    function twoLines(name) {           // a long name on two lines, split at the space nearest the middle
      if (name.length <= 14 || name.indexOf(" ") < 0) return name;
      let best = -1;
      for (let i = 0; i < name.length; i++) if (name[i] === " " && (best < 0 || Math.abs(i - name.length / 2) < Math.abs(best - name.length / 2))) best = i;
      return name.slice(0, best) + "\n" + name.slice(best + 1);
    }
    const area2 = (p) => { let s = 0; for (let i = 0, n = p.length; i < n; i += 2) { const k = (i + 2) % n; s += p[i] * p[k + 1] - p[k] * p[i + 1]; } return s / 2; };
    function buildBase() {
      landGroup.children.slice().forEach((c) => { landGroup.remove(c); c.geometry.dispose(); });
      const base = [];
      for (const [cc, c] of Object.entries(byCountry)) if (!focusSet.has(cc)) for (const p of c.polys) base.push(p);
      landGroup.add(new THREE.Mesh(extrude(base), [mat.landTop, mat.landSide]), new THREE.LineSegments(borderGeom(base), mat.border));
    }
    function buildFocus() {
      for (const f of Object.values(focusMeshes)) { focusGroup.remove(f.group); f.mesh.geometry.dispose(); f.line.geometry.dispose(); f.top.dispose(); f.side.dispose(); }
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

    /* pins: a round head with a white number (HTML, crisp, clickable), a short stem and a soft ground shadow (3D) */
    const STEM_PX = 13, HEAD_PX = 13;
    const stemGeo = new THREE.CylinderGeometry(0.07, 0.07, 1, 8); stemGeo.translate(0, 0.5, 0);
    const pinShadowTex = (() => {
      const c = document.createElement("canvas"); c.width = c.height = 64;
      const x = c.getContext("2d"); if (!x) return null;
      const g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
      g.addColorStop(0, "rgba(0,0,0,.42)"); g.addColorStop(0.5, "rgba(0,0,0,.16)"); g.addColorStop(1, "rgba(0,0,0,0)");
      x.fillStyle = g; x.fillRect(0, 0, 64, 64);
      return new THREE.CanvasTexture(c);
    })();
    mat.pinShadow = new THREE.MeshBasicMaterial({ map: pinShadowTex, transparent: true, depthWrite: false, fog: false });
    const shadowGeo = new THREE.PlaneGeometry(1, 1); shadowGeo.rotateX(-Math.PI / 2);
    let pins = [], routes = [], selected = null, countLabel = "shipments";
    const pinLayer = new THREE.Group(); scene.add(pinLayer);
    function paintPin(p) { p.btn.style.background = pinPal[p.tone] || pinPal.blue; }
    function makePin(d, i) {
      const g = new THREE.Group();
      const stem = new THREE.Mesh(stemGeo, mat.stem);
      const sh = new THREE.Mesh(shadowGeo, mat.pinShadow); sh.position.y = 0.03;
      g.add(stem, sh);
      const count = isFinite(num(d.count)) ? num(d.count) : null;
      const p = {
        id: String(d.id), label: d.label != null ? String(d.label) : "", count,
        tone: TONES.indexOf(d.tone) >= 0 ? d.tone : "blue", lat: num(d.lat), lon: num(d.lon), cc: d.cc || null, sheet: d.sheet || null,
        x: wx(num(d.lon)), z: wz(num(d.lat)), group: g, stem, shadow: sh, i, drop: 1
      };
      const b = el("button", "hg-pin" + (count === 0 ? " hg-zero" : "")); b.type = "button";
      const txt = count != null ? count.toLocaleString() : "";
      if (count) b.textContent = txt;                 // a zero is a small neutral dot, no number
      const aria = p.label + (count != null ? ": " + txt + " " + countLabel : "");
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
      for (const p of pins) { pinLayer.remove(p.group); p.btn.remove(); p.lab.remove(); }
      pins = [];
    }

    /* routes: a thick blue line with a lighter outline and a soft glow; solid where reported, dashed ahead, flowing */
    const VS = `attribute float side; attribute float along; attribute vec3 perp;
uniform float uWidth; varying float vSide; varying float vAlong;
void main(){ vec3 p = position + perp * side * uWidth; vSide = side; vAlong = along;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0); }`;
    const FS = `uniform vec3 uColor; uniform float uTime; uniform float uReach; uniform float uPx; uniform float uFlow;
varying float vSide; varying float vAlong;
void main(){
  float a = abs(vSide);                                  // 0 at the centre, 1 at the edge of the glow
  float core = 1.0 - smoothstep(0.36, 0.42, a);
  float edge = 1.0 - smoothstep(0.52, 0.58, a);
  float glow = exp(-a * a * 7.0) * 0.28;
  float px = vAlong * uPx;
  float ahead = step(uReach, vAlong);
  float dash = smoothstep(0.38, 0.46, fract((px - uTime * uFlow * 18.0) / 22.0));
  float flow = (1.0 - ahead) * smoothstep(0.85, 1.0, fract((px - uTime * uFlow * 60.0) / 160.0));
  vec3 outline = mix(uColor, vec3(1.0), 0.62);
  vec3 c = mix(outline, mix(uColor, vec3(1.0), flow * 0.35), core);
  float body = ahead > 0.5 ? edge * dash : edge;
  float alpha = max(body, glow * (ahead > 0.5 ? 0.5 : 1.0));
  if (alpha < 0.01) discard;
  gl_FragColor = vec4(c, alpha);
}`;
    function gc(a, b, n) {          // great-circle points between two lat/lon
      const v = (p) => [Math.cos(p.lat * DEG) * Math.cos(p.lon * DEG), Math.cos(p.lat * DEG) * Math.sin(p.lon * DEG), Math.sin(p.lat * DEG)];
      const A = v(a), B = v(b), d = Math.acos(clamp(A[0] * B[0] + A[1] * B[1] + A[2] * B[2], -1, 1));
      const out = [];
      for (let i = 0; i <= n; i++) {
        const t = i / n;
        let x = A[0], y = A[1], z = A[2];
        if (d >= 1e-6) {
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
        const hMax = Math.min(60, legLen * 0.14);
        for (let i = k === 0 ? 0 : 1; i < seg.length; i++) {
          let x = wx(seg[i].lon);
          if (lastX != null) { while (x - lastX > proj.W / 2) x -= proj.W; while (x - lastX < -proj.W / 2) x += proj.W; }
          const q = { x, y: Math.sin(Math.PI * seg[i].t) * hMax, z: wz(seg[i].lat) };
          if (P.length) along += Math.hypot(q.x - P[P.length - 1].x, q.y - P[P.length - 1].y, q.z - P[P.length - 1].z);
          q.along = along; P.push(q); lastX = x;
        }
        if (k + 1 === reached) reachAlong = along;
      }
      const n = P.length, pos = new Float32Array(n * 6), side = new Float32Array(n * 2), al = new Float32Array(n * 2), perp = new Float32Array(n * 6), idx = [];
      for (let i = 0; i < n; i++) {
        const a = P[Math.max(0, i - 1)], b = P[Math.min(n - 1, i + 1)];
        let tx = b.x - a.x, tz = b.z - a.z; const L = Math.hypot(tx, tz) || 1; tx /= L; tz /= L;
        for (let s = 0; s < 2; s++) {
          const o = i * 2 + s;
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
      const m = new THREE.ShaderMaterial({
        vertexShader: VS, fragmentShader: FS, transparent: true, depthWrite: false, side: THREE.DoubleSide,
        uniforms: { uColor: { value: S3(ROUTE) }, uTime: { value: 0 }, uReach: { value: reachAlong + 1e-3 },
          uPx: { value: 1 }, uWidth: { value: 3 }, uFlow: { value: 1 } }
      });
      const mesh = new THREE.Mesh(g, m); mesh.renderOrder = 2; mesh.frustumCulled = false;
      scene.add(mesh);
      return { mesh, mat: m };
    }
    function clearRoutes() { for (const r of routes) { scene.remove(r.mesh); r.mesh.geometry.dispose(); r.mat.dispose(); } routes = []; }

    /* controls: frosted glass, top right — zoom, 2D/3D, compass, recenter */
    const ctrls = el("div", "hg-ctrls");
    function btn(html, label, fn) {
      const b = el("button", "hg-btn"); b.type = "button"; b.innerHTML = html;
      b.setAttribute("aria-label", label); b.title = label;
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      return b;
    }
    const grp = (...bs) => { const g = el("div", "hg-grp hg-glass"); g.append(...bs); return g; };
    const bDim = btn("2D", "Top-down view (2D)", () => toggleDim());
    const bNorth = btn(ICON.compass, "North up", () => flyCam({ bearing: 0 }, 800));
    ctrls.append(grp(btn(ICON.plus, "Zoom in", () => flyCam({ dist: clamp(cam.dist / 1.7, minDist(), maxDist()) }, 800)),
                     btn(ICON.minus, "Zoom out", () => flyCam({ dist: clamp(cam.dist * 1.7, minDist(), maxDist()) }, 800))),
                 grp(bDim), grp(bNorth), grp(btn(ICON.recenter, "Recenter: every place", () => recenter())));
    root.appendChild(ctrls);
    function setDimLabel() {
      const flat = tween ? tween.to.pitch < 5 : cam.pitch < 5;
      bDim.textContent = flat ? "3D" : "2D";
      const l = flat ? "Tilted view (3D)" : "Top-down view (2D)";
      bDim.setAttribute("aria-label", l); bDim.title = l;
    }
    function toggleDim() {
      if (cam.pitch < 5) flyCam({ pitch: pitch3D }, 900); else { pitch3D = Math.max(20, cam.pitch); flyCam({ pitch: PITCH_2D, bearing: 0 }, 900); }
      setDimLabel();
    }
    function recenter() { if (selected) closeSheet(true); else flyCam(cam.pitch < 5 ? Object.assign({}, computeOverview(), { pitch: PITCH_2D }) : computeOverview(), FLY_MS); }

    /* camera: every move eased */
    const tmpV = new THREE.Vector3(), ground = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
    const UP = new THREE.Vector3(0, 1, 0);
    function minDist() { return 40; }
    function maxDist() { return 5200; }
    function applyCam() {
      cam.pitch = clamp(cam.pitch, PITCH_2D, MAX_PITCH);
      cam.dist = clamp(cam.dist, minDist(), maxDist());
      cam.x = clamp(cam.x, -proj.W * 0.75, proj.W * 0.75); cam.z = clamp(cam.z, -proj.H * 0.75, proj.H * 0.75);
      const p = cam.pitch * DEG, b = cam.bearing * DEG, h = cam.dist * Math.sin(p);
      camera.position.set(cam.x + h * Math.sin(b), cam.dist * Math.cos(p), cam.z + h * Math.cos(b));
      camera.up.set(-Math.sin(b), 0, -Math.cos(b)).lerp(UP, Math.min(1, cam.pitch / 10)).normalize();
      camera.lookAt(cam.x, 0, cam.z);
      camera.near = Math.max(0.5, cam.dist * 0.02); camera.far = cam.dist * 12 + 2000;
      scene.fog.near = cam.dist * 1.05; scene.fog.far = cam.dist * 3.4;
      const u = mat.water.uniforms; u.uD0.value = cam.dist * 0.5; u.uD1.value = cam.dist * 2.2; u.uF0.value = scene.fog.near; u.uF1.value = scene.fog.far;
      camera.updateProjectionMatrix();
      const nd = bNorth.querySelector(".hg-needle");
      if (nd) { nd.style.transformOrigin = "10px 10px"; nd.style.transform = "rotate(" + (-cam.bearing) + "deg)"; }
    }
    function groundAt(px, py, out) {
      ndc.set(px / cssW * 2 - 1, -(py / cssH) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      return ray.ray.intersectPlane(ground, out);
    }
    function flyCam(to, ms) {
      const from = { x: cam.x, z: cam.z, dist: cam.dist, pitch: cam.pitch, bearing: cam.bearing };
      const target = Object.assign({}, from, to);
      target.bearing = from.bearing + ((((target.bearing - from.bearing) % 360) + 540) % 360 - 180);
      return new Promise((res) => {
        if (tween && tween.res) tween.res(false);
        tween = { from, to: target, t0: performance.now(), ms: reduce() ? 0 : (ms == null ? FLY_MS : ms), res };
        intro = null; wheel = null; schedule();
      });
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

    /* selection and the bottom sheet */
    const pub = (p) => p && { id: p.id, label: p.label, count: p.count, tone: p.tone, lat: p.lat, lon: p.lon, cc: p.cc };
    function selectPin(p, user) {
      if (!p) return false;
      const was = selected;
      selected = p;
      for (const q of pins) q.btn.classList.toggle("hg-on", q === p);
      for (const f of Object.values(focusMeshes)) { f.selected = !!(p.cc && f.cc === p.cc); paintFocus(f); }
      renderSheet(p);
      sheet.hidden = false;
      requestAnimationFrame(() => requestAnimationFrame(() => sheet.classList.add("hg-show")));
      live.textContent = (p.sheet && p.sheet.title) || p.label;
      // fly to it: tilted, zoomed on its country, the pin in the upper part of the map above the sheet
      const f = p.cc && focusMeshes[p.cc], pitch = 54;
      let dist = 300;
      if (f) { const [a, b, c, d] = f.bbox; const pad = 0.75 * Math.max(c - a, d - b, 70); dist = fitBox(p.x - pad, p.z - pad, p.x + pad, p.z + pad, pitch, 1.1).dist; }
      dist = clamp(dist, 140, 900);
      const fwd = dist * Math.tan(FOV * DEG / 2) * 0.62, b = cam.bearing * DEG;
      flyCam({ x: p.x + fwd * Math.sin(b), z: p.z + fwd * Math.cos(b), dist, pitch }, FLY_MS).then(setDimLabel);
      if (user && was !== p) hooks.emit("select", pub(p));
      dirty = true; schedule();
      return true;
    }
    function closeSheet(user) {
      const was = selected;
      selected = null;
      for (const q of pins) q.btn.classList.remove("hg-on");
      for (const f of Object.values(focusMeshes)) { f.selected = false; paintFocus(f); }
      sheet.classList.remove("hg-show", "hg-pull"); sheet.style.transform = "";
      setTimeout(() => { if (!selected) sheet.hidden = true; }, reduce() ? 0 : 560);
      overview = computeOverview();
      flyCam(cam.pitch < 5 ? Object.assign({}, overview, { pitch: PITCH_2D }) : overview, FLY_MS);
      if (user && was) hooks.emit("select", null);
      if (user && was) { try { root.focus({ preventScroll: true }); } catch (e) { /* old browser */ } }
    }
    function renderSheet(p) {
      const sh = p.sheet || {};
      sheet.textContent = "";
      const grab = el("div", "hg-grab"); grab.setAttribute("aria-hidden", "true");
      sheet.appendChild(grab);
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
        const row = el("div", "hg-chips");
        for (const s of stats) {
          const c = el("span", "hg-chip" + (TONES.indexOf(s.tone) >= 0 ? " hg-t-" + s.tone : ""));
          c.append(el("b", "", s.v != null ? s.v : "—"), document.createTextNode(" " + String(s.k != null ? s.k : "").toLowerCase()));
          row.appendChild(c);
        }
        sheet.appendChild(row);
      }
      const bars = sh.bars;
      if (bars && Array.isArray(bars.items) && bars.items.length) {
        const box = el("div", "hg-bars");
        const its = bars.items.slice(0, 6);
        const max = Math.max(0, ...its.map((b) => num(b.v) || 0));
        box.setAttribute("role", "img");
        box.setAttribute("aria-label", (bars.title ? bars.title + ": " : "") + its.map((b) => (b.label || "") + " " + (num(b.v) || 0)).join(", "));
        const fills = [];
        for (const b of its) {
          const v = Math.max(0, num(b.v) || 0), track = el("div", "hg-track"), fill = el("div", "hg-fill" + (b.hi ? " hg-hi" : ""));
          track.appendChild(fill);
          box.append(el("span", "", b.label || ""), track, el("span", "hg-bn", v.toLocaleString()));
          fills.push([fill, max > 0 ? Math.max(2, v / max * 100) : 0]);
        }
        sheet.appendChild(box);
        const grow = () => fills.forEach(([f, w]) => { f.style.width = w + "%"; });
        if (reduce()) grow(); else setTimeout(grow, 180);
      }
      const acts = Array.isArray(sh.actions) ? sh.actions.filter((a) => a && a.label).slice(0, 2) : [];
      if (acts.length) {
        const row = el("div", "hg-acts");
        for (const a of acts) {
          const b = el("button", "hg-act" + (a.primary ? " hg-primary" : ""), a.label); b.type = "button";
          b.addEventListener("click", (e) => { e.stopPropagation(); hooks.emit("action", pub(p), a.event != null ? a.event : a.label); });
          row.appendChild(b);
        }
        sheet.appendChild(row);
      }
      sheet.scrollTop = 0;
    }
    // swipe the sheet down to close it
    let pull = null;
    sheet.addEventListener("pointerdown", (e) => {
      if (e.target.closest("button") || (sheet.scrollTop > 0 && !e.target.closest(".hg-grab"))) return;
      pull = { y: e.clientY, id: e.pointerId, dy: 0 };
      sheet.setPointerCapture && sheet.setPointerCapture(e.pointerId);
    });
    sheet.addEventListener("pointermove", (e) => {
      if (!pull || e.pointerId !== pull.id) return;
      pull.dy = Math.max(0, e.clientY - pull.y);
      if (pull.dy > 4) { sheet.classList.add("hg-pull"); sheet.style.transform = "translateY(" + pull.dy + "px)"; }
    });
    const endPull = (e) => {
      if (!pull || e.pointerId !== pull.id) return;
      const dy = pull.dy; pull = null;
      sheet.classList.remove("hg-pull");
      if (dy > Math.min(70, sheet.offsetHeight * 0.3)) closeSheet(true); else sheet.style.transform = "";
    };
    sheet.addEventListener("pointerup", endPull); sheet.addEventListener("pointercancel", endPull);

    /* pointer, wheel, keys */
    const ptrs = new Map();
    let drag = null;
    cv.addEventListener("contextmenu", (e) => e.preventDefault());
    cv.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0 && e.button !== 2) return;
      cv.setPointerCapture && cv.setPointerCapture(e.pointerId);
      ptrs.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      tween = null; intro = null; wheel = null;
      const rotate = e.button === 2 || e.ctrlKey || e.shiftKey || e.altKey;
      if (ptrs.size === 1) drag = { mode: rotate ? "rotate" : "pan", x: e.offsetX, y: e.offsetY, hit: groundAt(e.offsetX, e.offsetY, new THREE.Vector3()) };
      else if (ptrs.size === 2) {
        const [a, b] = [...ptrs.values()];
        drag = { mode: "pinch", d: Math.hypot(a.x - b.x, a.y - b.y), ang: Math.atan2(b.y - a.y, b.x - a.x), dist: cam.dist, bearing: cam.bearing };
      }
      root.classList.add("hg-drag");
    });
    cv.addEventListener("pointermove", (e) => {
      if (!ptrs.has(e.pointerId) || !drag) return;
      ptrs.set(e.pointerId, { x: e.offsetX, y: e.offsetY });
      if (drag.mode === "pinch" && ptrs.size >= 2) {
        const [a, b] = [...ptrs.values()];
        cam.dist = clamp(drag.dist * drag.d / Math.max(Math.hypot(a.x - b.x, a.y - b.y), 1), minDist(), maxDist());
        cam.bearing = drag.bearing - (Math.atan2(b.y - a.y, b.x - a.x) - drag.ang) / DEG;
      } else if (drag.mode === "rotate") {
        cam.bearing += (e.offsetX - drag.x) * 0.3; cam.pitch = clamp(cam.pitch - (e.offsetY - drag.y) * 0.25, PITCH_2D, MAX_PITCH);
        drag.x = e.offsetX; drag.y = e.offsetY; setDimLabel();
      } else if (drag.mode === "pan" && drag.hit) {
        applyCam();
        const now = groundAt(e.offsetX, e.offsetY, new THREE.Vector3());
        if (now) { cam.x += drag.hit.x - now.x; cam.z += drag.hit.z - now.z; }
      }
      dirty = true; schedule();
    });
    const up = (e) => { ptrs.delete(e.pointerId); if (!ptrs.size) { drag = null; root.classList.remove("hg-drag"); } };
    cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
    cv.addEventListener("wheel", (e) => {          // eased: the camera glides to the new distance
      e.preventDefault();
      const f = Math.exp(clamp(e.deltaY * (e.deltaMode === 1 ? 16 : 1), -240, 240) * 0.0022);
      const goal = clamp((wheel ? wheel.goal : cam.dist) * f, minDist(), maxDist());
      wheel = { goal, px: e.offsetX, py: e.offsetY };
      tween = null; intro = null; schedule();
    }, { passive: false });
    root.addEventListener("keydown", (e) => {
      if (e.target !== root) return;
      const step = cam.dist * 0.12, b = cam.bearing * DEG;
      const mv = (dx, dz) => flyCam({ x: cam.x + dx * Math.cos(b) + dz * Math.sin(b), z: cam.z - dx * Math.sin(b) + dz * Math.cos(b) }, 450);
      if (e.key === "ArrowLeft") mv(-step, 0); else if (e.key === "ArrowRight") mv(step, 0);
      else if (e.key === "ArrowUp") mv(0, -step); else if (e.key === "ArrowDown") mv(0, step);
      else if (e.key === "+" || e.key === "=") flyCam({ dist: cam.dist / 1.5 }, 600); else if (e.key === "-") flyCam({ dist: cam.dist * 1.5 }, 600);
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
    let camSig = "";
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
      if (wheel) {          // glide toward the wheel's goal, keeping the point under the cursor in place
        applyCam();
        const hit = groundAt(wheel.px, wheel.py, new THREE.Vector3());
        const nd = reduce() ? wheel.goal : lerp(cam.dist, wheel.goal, 0.2);
        if (hit) { const k = nd / cam.dist; cam.x = hit.x + (cam.x - hit.x) * k; cam.z = hit.z + (cam.z - hit.z) * k; }
        cam.dist = nd;
        if (Math.abs(nd - wheel.goal) < wheel.goal * 0.002) wheel = null; else active = true;
      }
      applyCam();
      const vt = Math.tan(FOV * DEG / 2), wpp = 2 * cam.dist * vt / cssH;      // world units per pixel at the target
      landGroup.scale.y = Math.max(0.02, LAND_PX * wpp / LAND_H);
      const fs = Math.max(0.02, FOCUS_PX * wpp / LAND_H);
      for (const f of Object.values(focusMeshes)) {
        const goal = f.selected ? 1.6 : 1;
        f.lift += (goal - f.lift) * (reduce() ? 1 : 0.16);
        if (Math.abs(goal - f.lift) > 0.01) active = true;
        f.group.scale.y = fs * f.lift;
      }
      const top = LAND_H * fs;
      for (const p of pins) {
        if (!isFinite(p.x) || !isFinite(p.z)) { p.group.visible = false; continue; }
        const dpp = 2 * camera.position.distanceTo(tmpV.set(p.x, 0, p.z)) * vt / cssH;
        if (p.drop < 1) { p.drop = clamp((now - t0 - 150 - p.i * 70) / 560, 0, 1); active = active || p.drop < 1; }
        const dk = reduce() ? 1 : p.drop;
        const lift = p.cc && focusMeshes[p.cc] ? focusMeshes[p.cc].lift : 1;
        p.base = top * lift; p.dpp = dpp; p.rise = dk < 1 ? (1 - ease(dk)) * 40 : 0;
        p.group.position.set(p.x, p.base, p.z);
        p.stem.scale.set(dpp * 1.4, (STEM_PX + p.rise) * dpp, dpp * 1.4);
        p.shadow.scale.set(dpp * 22, 1, dpp * 14);
          p.group.visible = dk > 0;
      }
      for (const r of routes) {
        r.mat.uniforms.uWidth.value = 9 * wpp; r.mat.uniforms.uPx.value = 1 / wpp;
        r.mat.uniforms.uTime.value = reduce() ? 0 : now / 1000; r.mat.uniforms.uFlow.value = reduce() ? 0 : 1;
        r.mesh.position.y = top;
      }
      renderer.render(scene, camera);
      overlay();
      dirty = false;
      // speed check: the first 2 s of drawing, with the loop kept running
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
    const hit = (placed, x, y, w, h) => placed.some((q) => x < q[0] + q[2] && x + w > q[0] && y < q[1] + q[3] && y + h > q[1]);
    function overlay() {
      const placed = [], heads = [];
      const sr = selected && sheet.classList.contains("hg-show") ? [sheet.offsetLeft, sheet.offsetTop, sheet.offsetWidth, sheet.offsetHeight] : null;
      if (sr) placed.push(sr);
      const order = pins.slice().sort((a, b) => (b === selected) - (a === selected) || (b.count || 0) - (a.count || 0));
      for (const p of order) {
        if (!p.group.visible) { p.btn.hidden = true; p.lab.hidden = true; continue; }
        const [bx, by, bz] = project(p.x, p.base, p.z);
        const hx = bx, hy = by - (STEM_PX + p.rise) - HEAD_PX * (p.count === 0 ? 0.6 : 1) + 2;
        const under = sr && p !== selected && bx > sr[0] - 20 && bx < sr[0] + sr[2] + 20 && by > sr[1] - 8;
        const off = under || bz > 1 || bx < -40 || by < -40 || bx > cssW + 40 || by > cssH + 60;
        p.btn.hidden = off; p.lab.hidden = off || !p.label;
        if (off) continue;
        const w = p.btn.offsetWidth || 26, h = p.btn.offsetHeight || 26;
        p.btn.style.transform = "translate(" + (hx - w / 2).toFixed(1) + "px," + (hy - h / 2).toFixed(1) + "px)" + (p === selected ? " scale(1.15)" : "");
        const z = String(1000 + Math.round(by) + (p === selected ? 5000 : 0));
        p.btn.style.zIndex = z; p.lab.style.zIndex = z;
        placed.push([hx - w / 2, hy - h / 2, w, h]);
        heads.push([p, hx, hy, bx, by, w, h]);
      }
      for (const [p, hx, hy, bx, by, w, h] of heads) {     // labels after every head, so none lands on a pin
        if (!p.label) continue;
        const lw = p.lab.offsetWidth || 30, lh = p.lab.offsetHeight || 20;
        const cands = [[bx - lw / 2, by + 4], [hx + w / 2 + 4, hy - lh / 2], [hx - w / 2 - 4 - lw, hy - lh / 2], [hx - lw / 2, hy - h / 2 - 4 - lh], [bx - lw / 2, by + 6 + lh]];
        const pick = cands.find((c) => !hit(placed, c[0], c[1], lw, lh)) || cands[0];
        placed.push([pick[0], pick[1], lw, lh]);
        p.lab.style.transform = "translate(" + pick[0].toFixed(1) + "px," + pick[1].toFixed(1) + "px)";
      }
      // country names: only where they fit, never over a pin or a label
      const sig = [cam.x, cam.z, cam.dist, cam.pitch, cam.bearing, cssW, cssH].map((v) => Math.round(v * 10)).join(",") + "|" + pins.length;
      if (sig === camSig && !pins.some((p) => p.drop < 1)) return;
      camSig = sig;
      const wpp = 2 * cam.dist * Math.tan(FOV * DEG / 2) / cssH;
      let shown = 0;
      for (const c of countryLabels) {
        const big = c.size / wpp > (Math.max(...c.el.textContent.split("\n").map((t) => t.length)) * 7 + 26);
        if (!big || shown >= 28) { c.el.hidden = true; continue; }
        const [x, y, z] = project(c.x, 0, c.z);
        const lw = c.el.offsetWidth || c.el.textContent.length * 8, lh = c.el.offsetHeight || 12;
        const ok = z <= 1 && x > 10 && y > 10 && x < cssW - 60 && y < cssH - 10 && !hit(placed, x - lw / 2 - 4, y - lh / 2 - 3, lw + 8, lh + 6);
        c.el.hidden = !ok;
        if (!ok) continue;
        if (c.el.offsetWidth) c.el.style.transform = "translate(" + (x - c.el.offsetWidth / 2).toFixed(1) + "px," + (y - lh / 2).toFixed(1) + "px)";
        placed.push([x - lw / 2 - 4, y - lh / 2 - 3, lw + 8, lh + 6]);
        shown++;
      }
    }

    /* size, theme, visibility */
    function resize() {
      const w = root.clientWidth || 300, h = root.clientHeight || 300;
      if (w === cssW && h === cssH) return;
      cssW = w; cssH = h;
      renderer.setSize(w, h, false); camera.aspect = w / h;
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
      if (nf.size !== focusSet.size || [...nf].some((c) => !focusSet.has(c))) { focusSet = nf; buildBase(); buildFocus(); }
      const keep = selected && selected.id;
      clearPins(); clearRoutes();
      const first = !overview;
      (d.pins || []).forEach((pd, i) => { if (pd && pd.id != null && isFinite(num(pd.lat)) && isFinite(num(pd.lon))) pins.push(makePin(pd, i)); });
      for (const p of pins) p.drop = first && !reduce() ? 0 : 1;
      for (const rd of d.routes || []) { const r = makeRoute(rd || {}); if (r) routes.push(r); }
      note.hidden = !!pins.length;
      if (!pins.length) note.textContent = "No places to show yet";
      if (first) {
        overview = computeOverview();
        if (reduce()) Object.assign(cam, overview);
        else {
          Object.assign(cam, overview, { dist: overview.dist * 1.4, pitch: 24, z: overview.z + overview.dist * 0.1 });
          intro = { from: Object.assign({}, cam), to: Object.assign({}, overview), t0: performance.now(), ms: 1600 };
        }
      }
      selected = null; camSig = "";
      if (keep) {
        const p = pins.find((x) => x.id === keep);
        if (p) { selected = p; p.btn.classList.add("hg-on"); renderSheet(p); for (const f of Object.values(focusMeshes)) { f.selected = !!(p.cc && f.cc === p.cc); paintFocus(f); } }
        else closeSheet(false);
      }
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
        worldReady = true;
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
