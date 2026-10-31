/* ATLAS Control Tower — daily operations scene.

   An isometric yard in which shipments flow left to right along one road,
   through five zones: in transit → arrived (quay) → customs → cleared,
   awaiting delivery (bonded yard) → completed (out-gate). The picture is an
   illustration; the numbers on it are real. Every zone carries a glass label
   with the exact count handed in, and the number of trucks, containers and
   pallets drawn in a zone follows that count on one shared scale
   (1 object ≈ N shipments, at least one when the count is above zero, none
   when it is zero, at most six). The scale is printed under the note.

     const scene = HubOpsScene.mount(containerEl);
     scene.setData({zones: [{id, label, count, tone, hint}, …], note});
     const off = scene.on("select", zoneId => …);   // click / Enter on a zone
     scene.setActive(zoneId | null);                  // others dim slightly
     scene.destroy();

   Zone ids: transit, arrived, customs, awaiting, done (others are ignored).
   Tones: blue, green, amber, red, teal, purple.

   Drawn in SVG with our own isometric projection (no libraries). Colours come
   from the hub tokens in hub.css and are re-read when the theme changes.
   Animation runs on requestAnimationFrame with transforms and opacity only;
   it pauses while the tab is hidden or the scene is off-screen, and the scene
   is still with prefers-reduced-motion. */
(function () {
  "use strict";
  if (window.HubOpsScene) return;

  const NS = "http://www.w3.org/2000/svg";
  // projection: yaw 30° about the vertical, ~34° down-look
  const YAW = 33 * Math.PI / 180, CX = Math.cos(YAW), SX = Math.sin(YAW), KY = 0.56, KZ = 0.83, S = 10;
  const VX = [CX * S, SX * KY * S], VY = [-SX * S, CX * KY * S], VZ = [0, -KZ * S];
  const W = 160, D = 42;                     // yard, world units
  const R0 = 25, R1 = 33, LA = 27, LB = 31;  // road edges, lane centres
  const SEA_X = 64, SEA_Y = 13;              // the sea: x < 64, y < 13
  const MAXU = 6;
  const TONES = ["blue", "green", "amber", "red", "teal", "purple"];
  const ZONES = [
    { id: "transit", x0: 0, x1: 32, label: "In transit", tone: "blue", anchor: [15, 6, 6] },
    { id: "arrived", x0: 32, x1: 64, label: "Arrived, under clearance", tone: "amber", anchor: [43, 10, 17.5] },
    { id: "customs", x0: 64, x1: 96, label: "At customs", tone: "amber", anchor: [75, 18, 7] },
    { id: "awaiting", x0: 96, x1: 128, label: "Cleared, awaiting delivery", tone: "teal", anchor: [112, 8, 10] },
    { id: "done", x0: 128, x1: 160, label: "Completed", tone: "green", anchor: [145, 18, 7] },
  ];
  const ZDEF = {};
  ZONES.forEach(z => { ZDEF[z.id] = z; });

  // ── maths & colour ──────────────────────────────────────────────────────
  const r1 = n => Math.round(n * 10) / 10;
  const P = (x, y, z) => [(x * CX - y * SX) * S, ((x * SX + y * CX) * KY - (z || 0) * KZ) * S];
  const pts = arr => arr.map(p => r1(p[0]) + "," + r1(p[1])).join(" ");
  const dpath = arr => "M" + arr.map(p => r1(p[0]) + " " + r1(p[1])).join("L") + "Z";
  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const ease = t => (t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t));
  const seg = (t, a, b) => ease((t - a) / (b - a));
  const mod = (a, n) => ((a % n) + n) % n;

  function rgb(c) {
    c = String(c || "").trim();
    if (c[0] === "#") {
      if (c.length === 4) c = "#" + c[1] + c[1] + c[2] + c[2] + c[3] + c[3];
      return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
    }
    const m = c.match(/[\d.]+/g);
    return m && m.length >= 3 ? m.slice(0, 3).map(Number) : [128, 128, 128];
  }
  function mix(a, b, t) {
    const x = rgb(a), y = rgb(b);
    return "#" + [0, 1, 2].map(i => Math.round(x[i] + (y[i] - x[i]) * t).toString(16).padStart(2, "0")).join("");
  }
  function lum(c) { const [r, g, b] = rgb(c); return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255; }

  function hull(points) {           // monotone chain, counter-clockwise
    const p = points.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    const lo = [], up = [];
    for (const q of p) { while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
    for (let i = p.length - 1; i >= 0; i--) { const q = p[i]; while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
    return lo.slice(0, -1).concat(up.slice(0, -1));
  }

  function palette(el) {
    const cs = getComputedStyle(el);
    const v = (n, d) => (cs.getPropertyValue(n) || "").trim() || d;
    const land = v("--hub-land", "#fbfbfa");
    const dark = lum(land) < 0.45;
    const p = {
      dark,
      land, landSide: v("--hub-land-side", "#dcdcd6"), landEdge: v("--hub-land-edge", "#c9c9c2"),
      road: v("--hub-road", "#e9e9ec"), tree: v("--hub-tree", "#7cc46a"),
      water: v("--hub-water", "#dfe9f3"), water2: v("--hub-water-2", "#cfdeec"),
      blue: v("--hub-blue", "#0071e3"), green: v("--hub-green", "#28a745"), amber: v("--hub-amber", "#e08a00"),
      red: v("--hub-red", "#e5383b"), teal: v("--hub-teal", "#0a9fb5"), purple: v("--hub-purple", "#7d5bd6"),
      ink: v("--hub-ink", "#1d1d1f"), ink3: v("--hub-ink-3", "#6e6e73"), ink4: v("--hub-ink-4", "#a1a1a6"),
      bg: v("--hub-bg", "#f5f5f7"),
    };
    p.shadeInk = dark ? "#04070f" : "#3a4762";
    p.kf = dark ? 0.16 : 0.055;               // front-face shade
    p.ks = dark ? 0.32 : 0.15;                // side-face shade
    // the ground reads a touch darker than the land token so white buildings stand on it
    p.ground = dark ? land : mix(land, p.road, 0.5);
    p.road = dark ? p.road : mix(p.road, p.shadeInk, 0.035);
    p.tree = mix(p.tree, dark ? land : "#c9d3c0", dark ? 0.12 : 0.22);
    p.water = dark ? mix(p.water, p.blue, 0.16) : mix(p.water, p.water2, 0.55);
    p.wall = dark ? "#3a4d70" : "#ffffff";
    p.roof = dark ? "#33456a" : "#f1f3f6";
    p.white = dark ? "#c3ccdb" : "#ffffff";   // vehicles
    p.glass = dark ? "#0c1524" : "#33445f";
    p.tyre = dark ? "#070c16" : "#2b2f37";
    p.steel = dark ? "#55678a" : "#b9c3d1";
    p.yellow = dark ? "#d9a514" : "#f5b914";
    p.card = dark ? "#a98159" : "#d9aa72";
    p.hull = dark ? "#2d5288" : mix(p.blue, "#8a9ab4", 0.55);
    p.window = dark ? "#22385e" : mix(p.blue, "#ffffff", 0.74);
    p.crane = dark ? "#6f86ad" : "#e4e9f0";
    p.paving = mix(p.ground, p.road, dark ? 0.55 : 0.45);
    p.curb = dark ? mix(land, p.landSide, 0.35) : mix(land, "#ffffff", 0.5);
    p.lawn = mix(p.ground, p.tree, dark ? 0.14 : 0.15);
    p.mark = dark ? "rgba(255,255,255,.22)" : "#ffffff";
    p.paint = dark ? "rgba(255,255,255,.12)" : "rgba(60,72,100,.16)";
    p.shadow = dark ? "rgba(0,0,0,.34)" : "rgba(38,52,86,.085)";
    p.door = dark ? "#1a2740" : mix(p.blue, "#ffffff", 0.82);
    p.blueSoft = dark ? mix(p.blue, "#0b1220", 0.25) : p.blue;
    return p;
  }

  const fmt = n => { try { return new Intl.NumberFormat("en-GB").format(n); } catch (e) { return String(n); } };
  function niceStep(v) {
    if (v <= 1) return 1;
    const e = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.5, 2, 2.5, 5, 7.5, 10]) { const c = m * e; if (c >= v && Number.isInteger(c)) return c; }
    return 10 * e;
  }

  // ── component CSS (once) ────────────────────────────────────────────────
  const CSS = `
.hop{position:relative;width:100%;font-family:var(--hub-font);color:var(--hub-ink);-webkit-tap-highlight-color:transparent}
.hop-stage{position:relative;width:100%;aspect-ratio:16/9;overflow:hidden;border-radius:inherit}
.hop-svg{position:absolute;inset:0;width:100%;height:100%;display:block;overflow:visible}
.hop-z{transition:opacity .45s var(--hub-e,ease)}
.hop.has-active .hop-z:not(.is-on){opacity:.6}
.hop-pad{opacity:0;transition:opacity .35s var(--hub-e,ease);pointer-events:none}
.hop-pad.is-hov,.hop-pad.is-on{opacity:1}
.hop-hit{fill:transparent;cursor:pointer;pointer-events:all;outline:none}
.hop-labels{position:absolute;left:0;top:0;width:100%;height:0;pointer-events:none;z-index:2}
.hop-label{position:absolute;left:0;top:0;display:flex;flex-direction:column;align-items:flex-start;gap:2px;
  min-width:116px;max-width:208px;padding:9px 13px 10px;margin:0;border-radius:var(--hub-r,14px);
  border:1px solid var(--hub-glass-edge);background:var(--hub-glass);box-shadow:var(--hub-shadow-lg);
  -webkit-backdrop-filter:saturate(180%) blur(20px);backdrop-filter:saturate(180%) blur(20px);
  color:var(--hub-ink);font:inherit;text-align:left;cursor:pointer;pointer-events:auto;
  transition:border-color .25s,opacity .35s var(--hub-e,ease)}
.hop-label:hover,.hop-label.is-hov{z-index:3}
.hop-label:focus-visible{outline:3px solid var(--hub-focus);outline-offset:3px;border-radius:var(--hub-r,14px);z-index:3}
.hop-label.is-on{border-color:color-mix(in srgb,var(--tone) 55%,transparent)}
.hop.has-active .hop-label:not(.is-on){opacity:.72}
.hop-top{display:flex;align-items:center;gap:7px;line-height:1}
.hop-dot{width:8px;height:8px;border-radius:50%;background:var(--tone);flex:none}
.hop-count{font-size:21px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.05}
.hop-label.is-zero .hop-count{color:var(--hub-ink-3)}
.hop-label.is-zero .hop-dot{background:var(--hub-ink-4)}
.hop-name{font-size:12px;font-weight:500;color:var(--hub-ink-2);line-height:1.3}
.hop-hint{font-size:11.5px;line-height:1.35;color:var(--hub-ink-3);max-height:0;opacity:0;overflow:hidden;
  transition:max-height .35s var(--hub-e,ease),opacity .25s,margin .35s var(--hub-e,ease)}
.hop-label.is-hov .hop-hint,.hop-label:focus-visible .hop-hint{max-height:64px;opacity:1;margin-top:3px}
.hop-num{display:none;min-width:17px;height:17px;padding:0 4px;border-radius:9px;font-size:10.5px;font-weight:600;
  align-items:center;justify-content:center;background:var(--hub-surface-2);color:var(--hub-ink-2);margin-left:auto}
.hop-stem{position:absolute;left:50%;top:100%;width:1px;height:var(--stem,18px);margin-left:-.5px;
  background:linear-gradient(var(--hub-ink-4),color-mix(in srgb,var(--hub-ink-4) 30%,transparent));pointer-events:none}
.hop-stem::after{content:"";position:absolute;left:-3px;bottom:-4px;width:7px;height:7px;border-radius:50%;
  background:var(--tone);box-shadow:0 0 0 2px var(--hub-surface)}
.hop-label.is-zero .hop-stem::after{background:var(--hub-ink-4)}
.hop-note{position:absolute;left:12px;bottom:10px;max-width:calc(100% - 24px);padding:5px 11px;border-radius:999px;
  font-size:11.5px;line-height:1.35;color:var(--hub-ink-3);background:var(--hub-glass);border:1px solid var(--hub-glass-edge);
  -webkit-backdrop-filter:saturate(180%) blur(14px);backdrop-filter:saturate(180%) blur(14px);z-index:1}
.hop-note b{font-weight:500;color:var(--hub-ink-2)}
.hop-note:empty{display:none}
.hop-pins{position:absolute;inset:0;pointer-events:none;display:none}
.hop-pin{position:absolute;left:0;top:0;width:19px;height:19px;margin:-19px 0 0 -9.5px;border-radius:50%;display:grid;place-items:center;
  font-size:10.5px;font-weight:600;color:#fff;background:var(--tone);box-shadow:0 0 0 2px var(--hub-surface),var(--hub-shadow)}
.hop-pin.is-zero{background:var(--hub-ink-4)}
.hop.is-narrow .hop-stage{aspect-ratio:var(--hop-ar,16/10)}
.hop.is-narrow .hop-pins{display:block}
.hop.is-narrow .hop-labels{position:static;height:auto;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;padding-top:10px}
.hop.is-narrow .hop-label{position:relative;transform:none!important;max-width:none;min-width:0;width:100%;padding:9px 11px;box-shadow:var(--hub-shadow,none)}
.hop.is-narrow .hop-stem{display:none}
.hop.is-narrow .hop-num{display:inline-flex}
.hop.is-narrow .hop-count{font-size:19px}
.hop.is-narrow .hop-note{position:static;display:block;margin-top:8px;border-radius:10px;max-width:none}
.hop.is-compact .hop-label{min-width:0;max-width:168px;padding:7px 10px 8px}
.hop.is-compact .hop-count{font-size:17px}
.hop.is-compact .hop-name{font-size:11px}
@media (prefers-reduced-motion:reduce){.hop-z,.hop-pad,.hop-hint,.hop-label{transition:none}}
`;
  function injectCSS() {
    if (document.getElementById("hubops-css")) return;
    const st = document.createElement("style");
    st.id = "hubops-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // ── SVG building blocks ─────────────────────────────────────────────────
  function E(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function face(g, corners, fill, extra) {
    const a = { points: pts(corners.map(c => P(c[0], c[1], c[2]))), fill, stroke: fill, "stroke-width": 0.6, "stroke-linejoin": "round" };
    if (extra) Object.assign(a, extra);
    return E("polygon", a, g);
  }
  function Mat(p, base) { return { t: base, f: mix(base, p.shadeInk, p.kf), s: mix(base, p.shadeInk, p.ks) }; }
  function box(g, x0, y0, z0, x1, y1, z1, m) {
    face(g, [[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]], m.f);
    face(g, [[x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]], m.s);
    face(g, [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], m.t);
  }
  function prism(g, foot, z0, z1, m) {
    let cx = 0, cy = 0;
    foot.forEach(q => { cx += q[0]; cy += q[1]; });
    cx /= foot.length; cy /= foot.length;
    for (let i = 0; i < foot.length; i++) {
      const a = foot[i], b = foot[(i + 1) % foot.length];
      let nx = b[1] - a[1], ny = a[0] - b[0];
      if (nx * ((a[0] + b[0]) / 2 - cx) + ny * ((a[1] + b[1]) / 2 - cy) < 0) { nx = -nx; ny = -ny; }
      const L = Math.hypot(nx, ny) || 1; nx /= L; ny /= L;
      if (nx * SX + ny * CX <= 0.01) continue;
      const w = Math.abs(nx) / (Math.abs(nx) + Math.abs(ny));
      face(g, [[a[0], a[1], z0], [b[0], b[1], z0], [b[0], b[1], z1], [a[0], a[1], z1]], mix(m.f, m.s, w));
    }
    face(g, foot.map(q => [q[0], q[1], z1]), m.t);
  }
  // shadow cast on the ground by a footprint of height h (light from back-left, high)
  function shadowD(foot, h) {
    const dx = h * 0.42, dy = h * 0.2;
    return dpath(hull(foot.concat(foot.map(q => [q[0] + dx, q[1] + dy]))).map(q => P(q[0], q[1], 0)));
  }
  const rect = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  function ellipseFoot(cx, cy, rx, ry, n) {
    const out = [];
    for (let i = 0; i < (n || 12); i++) { const a = (i / (n || 12)) * Math.PI * 2; out.push([cx + rx * Math.cos(a), cy + ry * Math.sin(a)]); }
    return out;
  }

  // a scene object: one <g> with its world bounding box, for depth sorting
  function makeItem(zs, b, cls) {
    const g = E("g", cls ? { class: cls } : null, zs.g);
    const it = { g, b: b.slice() };
    zs.items.push(it);
    return it;
  }

  // ── vehicles ────────────────────────────────────────────────────────────
  // u: along the truck (0 = front bumper, negative backwards); v: across (+ = visible side)
  function truck(p, g, ox, oy, dir, variant, withShadow) {
    const T = dir === "x" ? (u, v, z) => [ox + u, oy + v, z] : (u, v, z) => [ox + v, oy + u, z];
    const bx = (u0, u1, v0, v1, z0, z1, m) => {
      const a = T(u0, v0, z0), b = T(u1, v1, z1);
      box(g, Math.min(a[0], b[0]), Math.min(a[1], b[1]), z0, Math.max(a[0], b[0]), Math.max(a[1], b[1]), z1, m);
    };
    const quad = (list) => list.map(q => P(...T(q[0], q[1], q[2])));
    const L = 5.5;
    if (withShadow) {
      const foot = [T(-L, -1.05, 0), T(0, -1.05, 0), T(0, 1.05, 0), T(-L, 1.05, 0)].map(q => [q[0], q[1]]);
      E("path", { d: shadowD(foot, 2.6), fill: p.shadow }, g);
    }
    const white = Mat(p, p.white);
    const cab = variant === 0 ? Mat(p, p.blueSoft) : white;
    const body = variant === 2 ? Mat(p, p.dark ? mix(p.white, p.blue, 0.22) : mix(p.blue, "#ffffff", 0.84)) : white;
    bx(-L, -1.75, -1.05, 1.05, 0.55, 3.1, body);
    bx(-1.6, 0, -1, 1, 0.5, 2.5, cab);
    // wheels on the visible side
    let wd = "";
    for (const u of [-0.85, -3.75, -4.75]) {
      const ring = [];
      for (let i = 0; i < 10; i++) { const a = (i / 10) * Math.PI * 2; ring.push(P(...T(u + 0.46 * Math.cos(a), 1.07, 0.46 + 0.46 * Math.sin(a)))); }
      wd += dpath(ring);
    }
    E("path", { d: wd, fill: p.tyre }, g);
    // windscreen + side window
    E("path", {
      d: dpath(quad([[0.02, -0.82, 1.5], [0.02, 0.82, 1.5], [0.02, 0.82, 2.3], [0.02, -0.82, 2.3]])) +
        dpath(quad([[-1.05, 1.01, 1.55], [-0.3, 1.01, 1.55], [-0.3, 1.01, 2.3], [-1.05, 1.01, 2.3]])),
      fill: p.glass,
    }, g);
    // livery stripe on the box
    if (variant !== 2) {
      E("path", { d: dpath(quad([[-5.25, 1.06, 0.95], [-2.0, 1.06, 0.95], [-2.0, 1.06, 1.25], [-5.25, 1.06, 1.25]])), fill: p.blueSoft, opacity: 0.9 }, g);
    }
  }
  function truckBox(ox, oy, dir) {
    return dir === "x" ? [ox - 5.5, oy - 1.07, 0, ox, oy + 1.07, 3.1] : [ox - 1.07, oy - 5.5, 0, ox + 1.07, oy, 3.1];
  }

  function container(p, g, x0, y0, z0, color, len) {
    const L = len || 6, m = Mat(p, color);
    box(g, x0, y0, z0, x0 + L, y0 + 2.4, z0 + 2.5, m);
    let d = "";
    for (let u = 0.6; u < L - 0.3; u += 0.6) {
      const a = P(x0 + u, y0 + 2.42, z0 + 0.2), b = P(x0 + u, y0 + 2.42, z0 + 2.3);
      d += "M" + r1(a[0]) + " " + r1(a[1]) + "L" + r1(b[0]) + " " + r1(b[1]);
    }
    E("path", { d, stroke: p.shadeInk, "stroke-opacity": p.dark ? 0.25 : 0.12, "stroke-width": 0.9, fill: "none" }, g);
  }

  function palletStack(p, g, x0, y0, kind) {
    box(g, x0, y0, 0, x0 + 2.2, y0 + 2.2, 0.32, Mat(p, p.blueSoft));
    const c = kind === 1 ? (p.dark ? mix(p.blue, "#ffffff", 0.15) : mix(p.blue, "#ffffff", 0.18)) : p.card;
    box(g, x0 + 0.1, y0 + 0.1, 0.32, x0 + 2.1, y0 + 2.1, kind === 2 ? 1.25 : 1.75, Mat(p, c));
    if (kind === 2) box(g, x0 + 0.35, y0 + 0.35, 1.25, x0 + 1.85, y0 + 1.85, 2.0, Mat(p, p.card));
  }

  // forklift facing -y (into the warehouse), local origin at centre of its body footprint
  function forklift(p, g, x, y) {
    const load = E("g", null, g);
    box(load, x - 1.0, y - 3.3, 0.22, x + 1.0, y - 1.2, 0.5, Mat(p, p.blueSoft));
    box(load, x - 0.9, y - 3.2, 0.5, x + 0.9, y - 1.3, 1.85, Mat(p, p.card));
    const steel = p.dark ? "#0b111d" : "#3a4150";
    const mast = [P(x - 0.55, y - 1.05, 0.1), P(x - 0.55, y - 1.05, 2.7), P(x + 0.55, y - 1.05, 0.1), P(x + 0.55, y - 1.05, 2.7)];
    E("path", { d: `M${r1(mast[0][0])} ${r1(mast[0][1])}L${r1(mast[1][0])} ${r1(mast[1][1])}M${r1(mast[2][0])} ${r1(mast[2][1])}L${r1(mast[3][0])} ${r1(mast[3][1])}`, stroke: steel, "stroke-width": 2.2, "stroke-linecap": "round" }, g);
    const yb = Mat(p, p.yellow);
    box(g, x - 0.72, y - 0.95, 0.2, x + 0.72, y + 0.95, 1.2, yb);
    box(g, x - 0.6, y + 0.45, 1.2, x + 0.6, y + 0.95, 1.65, Mat(p, mix(p.yellow, p.shadeInk, 0.25)));
    const posts = [[-0.6, -0.75], [0.6, -0.75], [-0.6, 0.85], [0.6, 0.85]].map(q => [P(x + q[0], y + q[1], 1.2), P(x + q[0], y + q[1], 2.35)]);
    E("path", { d: posts.map(s => `M${r1(s[0][0])} ${r1(s[0][1])}L${r1(s[1][0])} ${r1(s[1][1])}`).join(""), stroke: steel, "stroke-width": 1.1 }, g);
    box(g, x - 0.68, y - 0.85, 2.35, x + 0.68, y + 0.95, 2.48, Mat(p, p.yellow));
    let wd = "";
    for (const v of [-0.55, 0.6]) {
      const ring = [];
      for (let i = 0; i < 8; i++) { const a = (i / 8) * Math.PI * 2; ring.push(P(x + 0.74, y + v + 0.32 * Math.cos(a), 0.32 + 0.32 * Math.sin(a))); }
      wd += dpath(ring);
    }
    E("path", { d: wd, fill: p.tyre }, g);
    return load;
  }

  function tree(p, zs, x, y, s, grad) {
    s = s || 1;
    const it = makeItem(zs, [x - 1.1 * s, y - 1.1 * s, 0, x + 1.1 * s, y + 1.1 * s, 2.9 * s]);
    const a = P(x, y, 0), b = P(x, y, 1.0 * s);
    E("path", { d: `M${r1(a[0])} ${r1(a[1])}L${r1(b[0])} ${r1(b[1])}`, stroke: p.dark ? "#4a3f36" : "#a08670", "stroke-width": 2.4 * s, "stroke-linecap": "round" }, it.g);
    const c = P(x, y, 1.0 * s + 1.05 * s);
    E("circle", { cx: r1(c[0]), cy: r1(c[1]), r: r1(1.25 * s * S), fill: `url(#${grad})` }, it.g);
    zs.shadow.push(dpath(ellipseFoot(x + 0.75 * s, y + 0.45 * s, 1.35 * s, 1.0 * s, 12).map(q => P(q[0], q[1], 0))));
  }

  // ── depth sorting (painter's order over world boxes) ────────────────────
  function cmp(a, b) {
    const A = a.b, B = b.b, e = 1e-6;
    if (A[3] <= B[0] + e) return -1; if (B[3] <= A[0] + e) return 1;
    if (A[4] <= B[1] + e) return -1; if (B[4] <= A[1] + e) return 1;
    if (A[5] <= B[2] + e) return -1; if (B[5] <= A[2] + e) return 1;
    return a.dep - b.dep;
  }
  function sortItems(zs) {
    const it = zs.items, n = it.length;
    for (const o of it) {
      const b = o.b;
      o.sx0 = b[0] * CX - b[4] * SX; o.sx1 = b[3] * CX - b[1] * SX;
      o.sy0 = (b[0] * SX + b[1] * CX) * KY - b[5] * KZ; o.sy1 = (b[3] * SX + b[4] * CX) * KY - b[2] * KZ;
      o.dep = ((b[0] + b[3]) * SX + (b[1] + b[4]) * CX) * 0.5 + (b[2] + b[5]) * 0.25;
      o.inN = 0; o.out = []; o.done = false;
    }
    for (let i = 0; i < n; i++) {
      const a = it[i];
      for (let j = i + 1; j < n; j++) {
        const b = it[j];
        if (a.sx0 >= b.sx1 || b.sx0 >= a.sx1 || a.sy0 >= b.sy1 || b.sy0 >= a.sy1) continue;
        if (cmp(a, b) < 0) { a.out.push(b); b.inN++; } else { b.out.push(a); a.inN++; }
      }
    }
    const order = [];
    while (order.length < n) {
      let pick = null;
      for (const o of it) if (!o.done && o.inN === 0 && (!pick || o.dep < pick.dep)) pick = o;
      if (!pick) for (const o of it) if (!o.done && (!pick || o.dep < pick.dep)) pick = o;   // cycle: nearest-last
      pick.done = true; order.push(pick);
      for (const q of pick.out) q.inN--;
    }
    let same = zs.order && zs.order.length === n;
    if (same) for (let i = 0; i < n; i++) if (zs.order[i] !== order[i]) { same = false; break; }
    if (!same) { for (const o of order) zs.g.appendChild(o.g); zs.order = order; }
  }

  // ── the zones ───────────────────────────────────────────────────────────
  // each builder adds static items, painted ground marks, and animators {f(t)}
  function convoy(p, zs, n, xa, xb, speed, phase) {
    if (!n) return;
    const lanes = [Math.ceil(n / 2), Math.floor(n / 2)];
    const len = xb - xa;
    let k = 0;
    lanes.forEach((m, li) => {
      for (let j = 0; j < m; j++, k++) {
        const ly = li ? LB : LA;
        const it = makeItem(zs, truckBox(xa, ly, "x"));
        truck(p, it.g, xa, ly, "x", (k + li) % 3, true);
        const off = (j / m) * len + li * len * 0.37 + phase;
        // a lone truck never leaves the zone empty: while it fades out at the end
        // its twin fades in at the start (opacities sum to one)
        let twin = null;
        if (n === 1) {
          twin = makeItem(zs, truckBox(xa, ly, "x"));
          truck(p, twin.g, xa, ly, "x", (k + li) % 3, true);
        }
        const F = 3.5;
        const put = (o, x, op) => {
          const dx = x - xa;
          o.g.setAttribute("transform", `translate(${r1(dx * VX[0])} ${r1(dx * VX[1])})`);
          o.g.setAttribute("opacity", r1(clamp(op, 0, 1) * 100) / 100);
          o.b[0] = x - 5.5; o.b[3] = x;
        };
        zs.anim.push((t, rest) => {
          if (rest) {
            put(it, Math.min(xb - 1, xa + 5.5 + j * 6.4 + li * 3.2), 1);
            if (twin) put(twin, xa, 0);
            return;
          }
          const x = xa + mod(t * speed + off, len);
          const o = Math.min(1, (x - xa) / F, (xb - x) / F);
          put(it, x, o);
          if (twin) {
            if (x > xb - F) put(twin, xa + (x - (xb - F)), 1 - o);
            else if (x < xa + F) put(twin, xb - F + (x - xa), 1 - o);
            else put(twin, xa, 0);
          }
        });
      }
    });
  }

  function buildTransit(c, zs, n) {
    const p = c.p;
    // port office + beacon
    let it = makeItem(zs, [5, 16, 0, 13, 21, 3.6]);
    box(it.g, 5, 16, 0, 13, 21, 3.6, Mat(p, p.wall));
    box(it.g, 5, 20.99, 2.9, 13, 21.02, 3.25, { t: p.blueSoft, f: p.blueSoft, s: p.blueSoft });
    let wd1 = "";
    for (let x = 6; x < 12; x += 1.6) wd1 += dpath([P(x, 21.03, 1.2), P(x + 1.15, 21.03, 1.2), P(x + 1.15, 21.03, 2.4), P(x, 21.03, 2.4)]);
    E("path", { d: wd1, fill: p.window }, it.g);
    zs.shadow.push(shadowD(rect(5, 16, 13, 21), 3.6));
    it = makeItem(zs, [16.6, 16.6, 0, 17.4, 17.4, 7]);
    box(it.g, 16.75, 16.75, 0, 17.25, 17.25, 6.4, Mat(p, p.wall));
    box(it.g, 16.5, 16.5, 6.4, 17.5, 17.5, 7, Mat(p, p.blueSoft));
    zs.shadow.push(shadowD(rect(16.75, 16.75, 17.25, 17.25), 6.4));
    [[22, 17.5, 1], [24.6, 19.4, 0.8], [4, 37.4, 1], [6.5, 39.3, 0.8], [26.5, 36.6, 0.9], [2.5, 22.5, 0.75]]
      .forEach(q => { if (!(c.narrow && q[2] < 0.9)) tree(p, zs, q[0], q[1], q[2], c.grad); });
    if (!n) return;
    // 1: a vessel at sea; 3+: a plane overhead; the rest: trucks on the inbound road
    const nTrucks = n >= 3 ? n - 2 : n - 1;
    // vessel
    {
      const g = E("g", null, zs.g);
      const it2 = { g, b: [1, 4.6, 0, 17, 9.4, 5.5] };
      zs.items.push(it2);
      const x0 = 1, L = 16, y0 = 4.8, y1 = 9.2, yc = 7;
      E("path", { d: shadowD([[x0, y0], [x0 + L - 2.6, y0], [x0 + L, yc], [x0 + L - 2.6, y1], [x0, y1]], 1.2), fill: p.shadow }, g);
      const wake = [];
      for (let i = 0; i < 3; i++) { const a = P(x0 - 1 - i * 1.6, yc - 1 + i * 0.1, 0), b = P(x0 - 2.3 - i * 1.6, yc - 1.3 + i * 0.1, 0); wake.push(`M${r1(a[0])} ${r1(a[1])}L${r1(b[0])} ${r1(b[1])}`); }
      E("path", { d: wake.join(""), stroke: p.dark ? "rgba(255,255,255,.18)" : "#ffffff", "stroke-width": 2, "stroke-linecap": "round" }, g);
      prism(g, [[x0, y0], [x0 + L - 2.6, y0], [x0 + L, yc], [x0 + L - 2.6, y1], [x0, y1]], 0, 1.7, Mat(p, p.hull));
      const cols = [p.blueSoft, mix(p.teal, p.white, 0.2), p.dark ? "#8d9bb3" : "#dfe4ec"];
      [0, 1, 2].forEach(i => box(g, x0 + 4.6 + i * 3.1, y0 + 0.6, 1.7, x0 + 7.4 + i * 3.1, y1 - 0.6, 3.3 - (i === 2 ? 0.8 : 0), Mat(p, cols[i])));
      box(g, x0 + 0.6, y0 + 0.7, 1.7, x0 + 3.4, y1 - 0.7, 4.6, Mat(p, p.wall));
      E("path", { d: dpath([P(x0 + 3.42, y0 + 0.9, 3.7), P(x0 + 3.42, y1 - 0.9, 3.7), P(x0 + 3.42, y1 - 0.9, 4.2), P(x0 + 3.42, y0 + 0.9, 4.2)]), fill: p.glass }, g);
      box(g, x0 + 1.2, yc - 0.5, 4.6, x0 + 2.2, yc + 0.5, 5.5, Mat(p, p.blueSoft));
      let last = "";
      // at anchorage, waiting for a berth: a gentle swell, never hidden
      zs.anim.push((t, rest) => {
        const dx = 6, bob = rest ? 0 : Math.sin(t * 0.9) * 0.12;
        const tr = `translate(${r1(dx * VX[0])} ${r1(dx * VX[1] + bob * VZ[1])})`;
        if (tr !== last) { g.setAttribute("transform", tr); last = tr; }
        it2.b[0] = 1 + dx; it2.b[3] = 17 + dx;
      });
    }
    // plane
    if (n >= 3) {
      const g = E("g", null, c.sky);
      const sh = E("g", null, c.groundDyn);
      const z = 11, y = 21, xr = 0;
      const foot = [[xr - 3, y - 0.35], [xr + 3.2, y - 0.35], [xr + 3.8, y], [xr + 3.2, y + 0.35], [xr - 3, y + 0.35]];
      const wingB = [[xr + 1.5, y], [xr - 0.3, y], [xr - 1.4, y - 3.9], [xr - 0.5, y - 3.9]];
      const wingF = wingB.map(q => [q[0], 2 * y - q[1]]);
      const tailB = [[xr - 2.0, y], [xr - 2.9, y], [xr - 3.4, y - 1.5], [xr - 2.9, y - 1.5]];
      const tailF = tailB.map(q => [q[0], 2 * y - q[1]]);
      const wingC = mix(p.white, p.shadeInk, p.dark ? 0.08 : 0.03);
      face(g, wingB.map(q => [q[0], q[1], z]), wingC);
      face(g, tailB.map(q => [q[0], q[1], z + 0.2]), wingC);
      prism(g, foot, z - 0.35, z + 0.45, Mat(p, p.white));
      face(g, [[xr - 3, y, z + 0.45], [xr - 2.1, y, z + 0.45], [xr - 2.7, y, z + 2], [xr - 3.25, y, z + 2]], p.blueSoft);
      face(g, wingF.map(q => [q[0], q[1], z]), wingC);
      face(g, tailF.map(q => [q[0], q[1], z + 0.2]), wingC);
      E("path", { d: [foot, wingB, wingF].map(f => dpath(f.map(q => P(q[0], q[1], 0)))).join(""), fill: p.shadow }, sh);
      let last = "";
      zs.anim.push((t, rest) => {
        const cyc = 26, u = rest ? 0.42 : mod(t + 3, cyc) / cyc;
        const x = -14 + u * 52;
        const o = Math.min(1, u / 0.1, (1 - u) / 0.1);
        const tr = `translate(${r1(x * VX[0])} ${r1(x * VX[1])})`;
        if (tr !== last) { g.setAttribute("transform", tr); sh.setAttribute("transform", tr); last = tr; }
        const oo = String(r1(clamp(o, 0, 1) * 100) / 100);
        g.setAttribute("opacity", oo);
        const sx = x + 4.6;   // the shadow falls this far along x
        sh.setAttribute("opacity", r1(clamp(o, 0, 1) * clamp(Math.min((sx - 2) / 4, (60 - sx) / 6), 0, 1) * 100) / 100);
      });
    }
    convoy(p, zs, nTrucks, 5, 31, 1.7, 0);
  }

  function buildArrived(c, zs, n) {
    const p = c.p;
    // painted container slots on the apron
    const slots = [];
    for (const yy of [14.6, 17.9, 21.2]) for (const xx of [48.6, 55.6]) slots.push([xx, yy]);
    c.paint.push(...slots.map(s => dpath(rect(s[0] - 0.25, s[1] - 0.25, s[0] + 6.25, s[1] + 2.65).map(q => P(q[0], q[1], 0)))));
    // crane
    const cm = Mat(p, p.crane);
    const legs = [[39.4, 13.4], [46.1, 13.4], [39.4, 20.1], [46.1, 20.1]];
    legs.forEach(l => {
      const it = makeItem(zs, [l[0], l[1], 0, l[0] + 0.5, l[1] + 0.5, 13]);
      box(it.g, l[0], l[1], 0, l[0] + 0.5, l[1] + 0.5, 13, cm);
      box(it.g, l[0] - 0.15, l[1] - 0.15, 0, l[0] + 0.65, l[1] + 0.65, 0.5, Mat(p, p.steel));
      zs.shadow.push(shadowD(rect(l[0], l[1], l[0] + 0.5, l[1] + 0.5), 13));
    });
    let it = makeItem(zs, [39.4, 13.4, 12.2, 46.6, 13.9, 13]); box(it.g, 39.4, 13.4, 12.2, 46.6, 13.9, 13, cm);
    it = makeItem(zs, [39.4, 20.1, 12.2, 46.6, 20.6, 13]); box(it.g, 39.4, 20.1, 12.2, 46.6, 20.6, 13, cm);
    it = makeItem(zs, [39.4, 13.9, 12.2, 39.9, 20.1, 13]); box(it.g, 39.4, 13.9, 12.2, 39.9, 20.1, 13, cm);
    it = makeItem(zs, [46.1, 13.9, 12.2, 46.6, 20.1, 13]); box(it.g, 46.1, 13.9, 12.2, 46.6, 20.1, 13, cm);
    it = makeItem(zs, [41.6, 0.8, 13, 44.4, 22.6, 13.9]); box(it.g, 41.6, 0.8, 13, 44.4, 22.6, 13.9, cm);
    it = makeItem(zs, [40.6, 19.4, 13.9, 45.4, 23, 16]); box(it.g, 40.6, 19.4, 13.9, 45.4, 23, 16, Mat(p, p.wall));
    E("path", { d: dpath([P(40.6, 23.02, 14.5), P(45.4, 23.02, 14.5), P(45.4, 23.02, 15.0), P(40.6, 23.02, 15.0)]), fill: p.blueSoft }, it.g);
    E("path", { d: dpath([P(41.6, 22.62, 13.35), P(44.4, 22.62, 13.35), P(44.4, 22.62, 13.6), P(41.6, 22.62, 13.6)]), fill: p.blueSoft }, it.g);
    zs.shadow.push(shadowD(rect(40.6, 14, 45.4, 23), 7));
    // docked ship (only when something has arrived)
    if (n > 0) {
      const y0 = 4.2, y1 = 10.6, yc = 7.4, x0 = 33.4, L = 25.5;
      it = makeItem(zs, [x0, y0, 0, x0 + L, y1, 1.9]);
      prism(it.g, [[x0, y0], [x0 + L - 4, y0], [x0 + L, yc], [x0 + L - 4, y1], [x0, y1]], 0, 1.9, Mat(p, p.hull));
      zs.shadow.push(shadowD([[x0, y0], [x0 + L - 4, y0], [x0 + L, yc], [x0 + L - 4, y1], [x0, y1]], 1));
      it = makeItem(zs, [x0 + 0.6, y0 + 0.8, 1.9, x0 + 4.4, y1 - 0.8, 6.6]);
      box(it.g, x0 + 0.6, y0 + 0.8, 1.9, x0 + 4.4, y1 - 0.8, 6.0, Mat(p, p.wall));
      E("path", { d: dpath([P(x0 + 4.42, y0 + 1.1, 4.9), P(x0 + 4.42, y1 - 1.1, 4.9), P(x0 + 4.42, y1 - 1.1, 5.5), P(x0 + 4.42, y0 + 1.1, 5.5)]), fill: p.glass }, it.g);
      box(it.g, x0 + 1.4, yc - 0.55, 6.0, x0 + 2.6, yc + 0.55, 6.6, Mat(p, p.blueSoft));
      const cols = [p.blueSoft, mix(p.teal, p.white, 0.15), p.dark ? "#8d9bb3" : "#e3e7ee", mix(p.amber, p.white, 0.3)];
      [[47.2, 0, 2], [52.4, 1, 1]].forEach((b, i) => {
        const bi = makeItem(zs, [b[0], y0 + 0.9, 1.9, b[0] + 4.8, y1 - 0.9, 1.9 + b[2] * 1.25]);
        box(bi.g, b[0], y0 + 0.9, 1.9, b[0] + 4.8, y1 - 0.9, 1.9 + b[2] * 1.25, Mat(p, cols[(i + 1) % cols.length]));
      });
      // crane cycle: pick a box off the ship, set it down on the quay, it is taken away
      const asm = makeItem(zs, [40, 6.2, 2, 46, 8.6, 12.9]);
      const trolley = E("g", null, asm.g);
      box(trolley, 41.4, -1.0, 11.95, 44.6, 1.0, 12.95, Mat(p, p.steel));
      const cables = E("path", { stroke: p.dark ? "#9fb0cc" : "#5b6578", "stroke-width": 0.7, fill: "none" }, asm.g);
      const hook = E("g", null, asm.g);
      const cont = E("g", null, hook);
      container(p, cont, 40, -1.2, 0, mix(p.amber, p.white, p.dark ? 0.1 : 0.22));
      box(hook, 40.2, -1.05, 2.5, 45.8, 1.05, 2.85, Mat(p, p.steel));
      const Ys = 7.4, Yq = 16.8, Zs = 1.9, Zhi = 8.4;
      let last = "";
      zs.anim.push((t, rest) => {
        const u = rest ? 0.4 : mod(t, 14) / 14;
        let y, z, carry = true, co = 1;
        if (u < 0.12) { y = Ys; z = Zhi + (Zs - Zhi) * seg(u, 0, 0.12); carry = false; co = Math.min(1, u / 0.04); }
        else if (u < 0.17) { y = Ys; z = Zs; }
        else if (u < 0.3) { y = Ys; z = Zs + (Zhi - Zs) * seg(u, 0.17, 0.3); }
        else if (u < 0.5) { y = Ys + (Yq - Ys) * seg(u, 0.3, 0.5); z = Zhi; }
        else if (u < 0.63) { y = Yq; z = Zhi + (0 - Zhi) * seg(u, 0.5, 0.63); }
        else if (u < 0.68) { y = Yq; z = 0; }
        else { y = Yq + (Ys - Yq) * seg(u, 0.78, 0.96); z = (Zhi) * seg(u, 0.68, 0.78); carry = false; }
        // the container: on the ship before pickup, on the quay after release (then taken away)
        let cy = y, cz = z;
        if (u < 0.12) { cz = Zs; }
        else if (u >= 0.68) { cy = Yq; cz = 0; co = 1 - seg(u, 0.8, 0.9); }
        const tr = `${r1(cy)}|${r1(cz)}|${r1(y)}|${r1(z)}|${r1(co * 100)}`;
        if (tr === last) return;
        last = tr;
        trolley.setAttribute("transform", `translate(${r1(y * VY[0])} ${r1(y * VY[1])})`);
        hook.setAttribute("transform", `translate(${r1(y * VY[0])} ${r1(y * VY[1] + z * VZ[1])})`);
        cont.setAttribute("transform", `translate(${r1((cy - y) * VY[0])} ${r1((cy - y) * VY[1] + (cz - z) * VZ[1])})`);
        cont.setAttribute("opacity", r1(co * 100) / 100);
        const a1 = P(42.2, y, 11.95), b1 = P(42.2, y, z + 2.85), a2 = P(43.8, y, 11.95), b2 = P(43.8, y, z + 2.85);
        cables.setAttribute("d", `M${r1(a1[0])} ${r1(a1[1])}L${r1(b1[0])} ${r1(b1[1])}M${r1(a2[0])} ${r1(a2[1])}L${r1(b2[0])} ${r1(b2[1])}`);
        const lo = Math.min(z, cz);
        asm.b[1] = Math.min(y, cy) - 1.2; asm.b[4] = Math.max(y, cy) + 1.2; asm.b[2] = lo; asm.b[5] = 12.95;
        if (!carry && u >= 0.68) { asm.b[1] = Math.min(y, Yq) - 1.2; }
      });
    }
    // containers on the apron: the count
    const cols = [p.blueSoft, mix(p.teal, p.white, 0.12), p.dark ? "#8d9bb3" : "#e6eaf0", mix(p.blue, p.white, 0.45), mix(p.amber, p.white, 0.25), mix(p.green, p.white, 0.3)];
    const fill = [1, 0, 3, 2, 5, 4];
    for (let i = 0; i < n; i++) {
      const s = slots[fill[i]];
      const ci = makeItem(zs, [s[0], s[1], 0, s[0] + 6, s[1] + 2.4, 2.5]);
      container(p, ci.g, s[0], s[1], 0, cols[i % cols.length]);
      zs.shadow.push(shadowD(rect(s[0], s[1], s[0] + 6, s[1] + 2.4), 2.5));
    }
    [[40, 37.7, 1], [42.5, 39.5, 0.8], [58.5, 36.6, 0.9], [35.2, 22.3, 0.8]]
      .forEach(q => { if (!(c.narrow && q[2] < 0.9)) tree(p, zs, q[0], q[1], q[2], c.grad); });
  }

  function buildCustoms(c, zs, n) {
    const p = c.p;
    // customs house
    let it = makeItem(zs, [67, 14.5, 0, 82, 21.5, 5]);
    box(it.g, 67, 14.5, 0, 82, 21.5, 4.6, Mat(p, p.wall));
    box(it.g, 66.8, 14.3, 4.6, 82.2, 21.7, 5.0, Mat(p, p.roof));
    let d = "";
    for (let x = 68.2; x < 80.5; x += 2.2) d += dpath([P(x, 21.52, 1.7), P(x + 1.6, 21.52, 1.7), P(x + 1.6, 21.52, 3.4), P(x, 21.52, 3.4)]);
    E("path", { d, fill: p.window }, it.g);
    E("path", { d: dpath([P(67, 21.53, 3.9), P(82, 21.53, 3.9), P(82, 21.53, 4.25), P(67, 21.53, 4.25)]), fill: p.blueSoft }, it.g);
    E("path", { d: dpath([P(82.02, 14.5, 3.9), P(82.02, 21.5, 3.9), P(82.02, 21.5, 4.25), P(82.02, 14.5, 4.25)]), fill: mix(p.blueSoft, p.shadeInk, 0.15) }, it.g);
    zs.shadow.push(shadowD(rect(67, 14.5, 82, 21.5), 5));
    it = makeItem(zs, [70, 16, 5, 73, 18.5, 6]); box(it.g, 70, 16, 5, 73, 18.5, 5.9, Mat(p, p.steel));
    it = makeItem(zs, [75.5, 16, 5, 78, 18, 6]); box(it.g, 75.5, 16, 5, 78, 18, 5.7, Mat(p, p.steel));
    // flag
    it = makeItem(zs, [84.5, 19.5, 0, 86.5, 19.8, 7]);
    const fa = P(84.6, 19.6, 0), fb = P(84.6, 19.6, 7);
    E("path", { d: `M${r1(fa[0])} ${r1(fa[1])}L${r1(fb[0])} ${r1(fb[1])}`, stroke: p.steel, "stroke-width": 1.2 }, it.g);
    E("path", { d: dpath([P(84.6, 19.6, 6.9), P(86.6, 19.6, 6.9), P(86.6, 19.6, 5.8), P(84.6, 19.6, 5.8)]), fill: p.blueSoft }, it.g);
    // inspection plaza: booth island, two barriers, a canopy over both lanes
    c.paint.push(dpath([P(88.5, 25.4, 0), P(88.8, 25.4, 0), P(88.8, 32.6, 0), P(88.5, 32.6, 0)]));
    it = makeItem(zs, [84.6, 28.4, 0, 88.2, 29.6, 2.8]);
    box(it.g, 84.6, 28.4, 0, 88.2, 29.6, 0.3, Mat(p, p.curb));
    box(it.g, 85.3, 28.6, 0.3, 87.3, 29.4, 2.6, Mat(p, p.wall));
    E("path", { d: dpath([P(87.32, 28.7, 1.4), P(87.32, 29.3, 1.4), P(87.32, 29.3, 2.2), P(87.32, 28.7, 2.2)]), fill: p.glass }, it.g);
    box(it.g, 85.15, 28.45, 2.6, 87.45, 29.55, 2.8, Mat(p, p.blueSoft));
    zs.shadow.push(shadowD(rect(85.3, 28.6, 87.3, 29.4), 2.6));
    for (const yy of [24.7, 33.0]) for (const xx of [84.6, 87.8]) {
      const pi = makeItem(zs, [xx, yy, 0, xx + 0.35, yy + 0.35, 5.2]);
      box(pi.g, xx, yy, 0, xx + 0.35, yy + 0.35, 5.2, Mat(p, p.wall));
    }
    it = makeItem(zs, [84.3, 24.4, 5.2, 88.5, 33.6, 5.75]);
    box(it.g, 84.3, 24.4, 5.2, 88.5, 33.6, 5.75, Mat(p, p.roof));
    E("path", { d: dpath([P(84.3, 33.62, 5.25), P(88.5, 33.62, 5.25), P(88.5, 33.62, 5.5), P(84.3, 33.62, 5.5)]), fill: p.blueSoft }, it.g);
    zs.shadow.push(dpath(hull([[84.3, 24.4], [88.5, 24.4], [88.5, 33.6], [84.3, 33.6], [86.6, 25.5], [90.8, 25.5], [90.8, 34.7], [86.6, 34.7]]).map(q => P(q[0], q[1], 0))));
    // barrier arms (pivot on the island, arm across the lane)
    const arms = [[28.4, -1], [29.6, 1]].map(([py, dir]) => {
      const ai = makeItem(zs, [88.3, Math.min(py, py + dir * 3.3), 0.9, 88.6, Math.max(py, py + dir * 3.3), 1.3]);
      const arm = E("path", { stroke: p.red, "stroke-width": 2, "stroke-linecap": "round", fill: "none" }, ai.g);
      const stripes = E("path", { stroke: "#ffffff", "stroke-width": 2.1, "stroke-dasharray": "3 3", fill: "none", opacity: p.dark ? 0.85 : 1 }, ai.g);
      return { ai, arm, stripes, py, dir, last: -1 };
    });
    const setArm = (a, ang) => {
      const k = Math.round(ang * 100);
      if (k === a.last) return; a.last = k;
      const ca = Math.cos(ang), sa = Math.sin(ang);
      const A = P(88.45, a.py, 1.1), B = P(88.45, a.py + a.dir * 3.3 * ca, 1.1 + 3.3 * sa);
      const dd = `M${r1(A[0])} ${r1(A[1])}L${r1(B[0])} ${r1(B[1])}`;
      a.arm.setAttribute("d", dd); a.stripes.setAttribute("d", dd);
      const y2 = a.py + a.dir * 3.3 * ca;
      a.ai.b[1] = Math.min(a.py, y2) - 0.05; a.ai.b[4] = Math.max(a.py, y2) + 0.05; a.ai.b[5] = 1.3 + 3.3 * sa;
    };
    arms.forEach(a => setArm(a, 0));
    // queue: lane A gets the odd one; each period the front truck is waved through
    const lanes = [Math.ceil(n / 2), Math.floor(n / 2)];
    const front = 88.0, gap = 6.5, exitX = 96.5;
    lanes.forEach((m, li) => {
      const arm = arms[li];
      if (!m) return;
      const ly = li ? LB : LA, per = 7.5, off = li * 3.6;
      const trucks = [];
      for (let j = 0; j <= m; j++) {
        const ti = makeItem(zs, truckBox(front, ly, "x"));
        truck(p, ti.g, front, ly, "x", (j + li * 2) % 3, true);
        trucks.push({ ti, last: "" });
      }
      zs.anim.push((t, rest) => {
        const T = t + off, cyc = rest ? 0 : Math.floor(T / per), u = rest ? 0 : mod(T, per) / per;
        setArm(arm, (Math.PI / 2 - 0.08) * (seg(u, 0.02, 0.16) - seg(u, 0.62, 0.78)));
        const adv = seg(u, 0.22, 0.62);
        trucks.forEach((tk, j) => {
          // slot index s: 0 = at the barrier; -1 = gone through; m = arriving at the back
          const slot = mod(j - cyc, m + 1);
          let x, o = 1;
          if (slot === 0) { x = front + (exitX - front) * seg(u, 0.14, 0.62); o = 1 - seg(u, 0.45, 0.62); }
          else { x = front - (slot - adv) * gap; if (slot === m) o = seg(u, 0.25, 0.62); }
          const dx = x - front;
          const k = r1(dx) + "|" + r1(o * 100);
          if (k !== tk.last) {
            tk.last = k;
            tk.ti.g.setAttribute("transform", `translate(${r1(dx * VX[0])} ${r1(dx * VX[1])})`);
            tk.ti.g.setAttribute("opacity", r1(o * 100) / 100);
          }
          tk.ti.b[0] = x - 5.5; tk.ti.b[3] = x;
        });
      });
    });
    [[70, 37.0, 0.9], [88, 38.1, 1], [90.6, 39.7, 0.8], [93.5, 18.5, 0.85], [64.8, 22.8, 0.75]]
      .forEach(q => { if (!(c.narrow && q[2] < 0.9)) tree(p, zs, q[0], q[1], q[2], c.grad); });
  }

  function buildAwaiting(c, zs, n) {
    const p = c.p;
    const X0 = 98, X1 = 126, Y0 = 3, Y1 = 14, H = 7.6;
    let it = makeItem(zs, [X0, Y0, 0, X1, Y1, H]);
    box(it.g, X0, Y0, 0, X1, Y1, H, { t: p.roof, f: mix(p.wall, p.shadeInk, p.kf * 0.6), s: mix(p.wall, p.shadeInk, p.ks) });
    // blue trim along the eaves and the corner
    E("path", { d: dpath([P(X0, Y1 + 0.01, H - 0.45), P(X1, Y1 + 0.01, H - 0.45), P(X1, Y1 + 0.01, H), P(X0, Y1 + 0.01, H)]), fill: p.blueSoft }, it.g);
    E("path", { d: dpath([P(X1 + 0.01, Y0, H - 0.45), P(X1 + 0.01, Y1, H - 0.45), P(X1 + 0.01, Y1, H), P(X1 + 0.01, Y0, H)]), fill: mix(p.blueSoft, p.shadeInk, 0.18) }, it.g);
    // skylights
    let d = "";
    for (let x = X0 + 2.5; x < X1 - 3; x += 4.6) d += dpath([P(x, Y0 + 3.2, H), P(x + 2.4, Y0 + 3.2, H), P(x + 2.4, Y1 - 3.2, H), P(x, Y1 - 3.2, H)]);
    E("path", { d, fill: p.dark ? "#22324f" : "#e3e9f2" }, it.g);
    zs.shadow.push(shadowD(rect(X0, Y0, X1, Y1), H));
    // rooftop plant
    [[X0 + 4, Y0 + 1.2], [X0 + 21, Y0 + 1.2]].forEach(q => {
      const ri = makeItem(zs, [q[0], q[1], H, q[0] + 2.4, q[1] + 1.6, H + 1.1]);
      box(ri.g, q[0], q[1], H, q[0] + 2.4, q[1] + 1.6, H + 1.1, Mat(p, p.steel));
    });
    // dock doors
    const doors = [101.5, 107.5, 113.5, 119.5];
    let dd = "", di = "";
    doors.forEach(x => {
      dd += dpath([P(x - 1.75, Y1 + 0.03, 0), P(x + 1.75, Y1 + 0.03, 0), P(x + 1.75, Y1 + 0.03, 4.5), P(x - 1.75, Y1 + 0.03, 4.5)]);
      di += dpath([P(x - 1.5, Y1 + 0.05, 0), P(x + 1.5, Y1 + 0.05, 0), P(x + 1.5, Y1 + 0.05, 4.25), P(x - 1.5, Y1 + 0.05, 4.25)]);
      c.paint.push(dpath([P(x - 2.1, Y1, 0), P(x - 1.95, Y1, 0), P(x - 1.95, 20.4, 0), P(x - 2.1, 20.4, 0)]));
    });
    E("path", { d: dd, fill: p.blueSoft }, it.g);
    E("path", { d: di, fill: p.door }, it.g);
    // pallet slots, painted
    const slotX = [98.6, 101.2, 103.8, 109.3, 115.6, 118.2];
    slotX.forEach(x => c.paint.push(dpath(rect(x - 0.2, 21.7, x + 2.4, 24.3).map(q => P(q[0], q[1], 0)))));
    if (n > 0) {
      // a truck backed onto a door, loading; a second when the yard is busier
      [[101.5, 1], [119.5, 4]].forEach(([x, min]) => {
        if (n < min) return;
        const ti = makeItem(zs, truckBox(x, 19.6, "y"));
        truck(p, ti.g, x, 19.6, "y", 0, false);
        zs.shadow.push(shadowD(rect(x - 1.05, 14.1, x + 1.05, 19.6), 3));
      });
      // forklifts shuttle pallets from the yard into the warehouse
      [[107.5, 1, 0], [113.5, 3, 4.4]].forEach(([x, min, off]) => {
        if (n < min) return;
        const fi = makeItem(zs, [x - 1, 17, 0, x + 1, 21.4, 2.7]);
        const load = forklift(p, fi.g, x, 20.4);
        const sh = E("path", { d: shadowD(rect(x - 0.72, 19.45, x + 0.72, 21.35), 2.4), fill: p.shadow }, fi.g);
        fi.g.insertBefore(sh, fi.g.firstChild);
        let last = "";
        zs.anim.push((t, rest) => {
          const u = rest ? 0.3 : mod(t + off, 9) / 9;
          let y, o = 1, carry = true;
          if (u < 0.1) y = 20.4;
          else if (u < 0.45) y = 20.4 - 4.6 * seg(u, 0.1, 0.45);
          else if (u < 0.55) { y = 15.8 - 1.2 * seg(u, 0.45, 0.55); o = 1 - seg(u, 0.45, 0.55); }
          else if (u < 0.6) { y = 14.6; o = 0; carry = false; }
          else if (u < 0.7) { y = 14.6 + 1.2 * seg(u, 0.6, 0.7); o = seg(u, 0.6, 0.7); carry = false; }
          else { y = 15.8 + 4.6 * seg(u, 0.7, 0.95); carry = false; }
          const dy = y - 20.4;
          const k = r1(dy) + "|" + r1(o * 100) + "|" + carry;
          if (k === last) return;
          last = k;
          fi.g.setAttribute("transform", `translate(${r1(dy * VY[0])} ${r1(dy * VY[1])})`);
          fi.g.setAttribute("opacity", r1(o * 100) / 100);
          load.setAttribute("display", carry ? "inline" : "none");
          fi.b[1] = y - (carry ? 3.3 : 1.1); fi.b[4] = y + 1;
        });
      });
    }
    // the count: pallet stacks waiting in the bonded yard
    const order = [1, 3, 4, 2, 5, 0], kinds = [0, 1, 2, 0, 1, 0];
    for (let i = 0; i < n; i++) {
      const x = slotX[order[i]];
      const pi = makeItem(zs, [x, 21.9, 0, x + 2.2, 24.1, 2.0]);
      palletStack(p, pi.g, x, 21.9, kinds[i]);
      zs.shadow.push(shadowD(rect(x, 21.9, x + 2.2, 24.1), 1.9));
    }
    [[102, 38.1, 1], [104.5, 39.7, 0.8], [121, 36.6, 0.9], [127, 17.5, 0.85]]
      .forEach(q => { if (!(c.narrow && q[2] < 0.9)) tree(p, zs, q[0], q[1], q[2], c.grad); });
  }

  function buildDone(c, zs, n) {
    const p = c.p;
    // out-gate gantry
    const gm = Mat(p, p.wall);
    [24.3, 33.4].forEach(y => {
      const gi = makeItem(zs, [130.6, y, 0, 131.1, y + 0.5, 5.6]);
      box(gi.g, 130.6, y, 0, 131.1, y + 0.5, 5.6, gm);
      zs.shadow.push(shadowD(rect(130.6, y, 131.1, y + 0.5), 5.6));
    });
    let it = makeItem(zs, [130.5, 24.3, 5.6, 131.2, 33.9, 6.3]);
    box(it.g, 130.5, 24.3, 5.6, 131.2, 33.9, 6.3, gm);
    it = makeItem(zs, [131.2, 26.6, 4.4, 131.3, 31.6, 6.2]);
    E("path", { d: dpath([P(131.22, 26.6, 4.5), P(131.22, 31.6, 4.5), P(131.22, 31.6, 5.55), P(131.22, 26.6, 5.55)]), fill: p.green }, it.g);
    // arrows on the sign
    const ar = [];
    [28.1, 30.1].forEach(y => { const a = P(131.24, y - 0.3, 5.0), b = P(131.24, y + 0.35, 5.0); ar.push(`M${r1(a[0])} ${r1(a[1])}L${r1(b[0])} ${r1(b[1])}`); });
    E("path", { d: ar.join(""), stroke: "#ffffff", "stroke-width": 1.4, "stroke-linecap": "round" }, it.g);
    // dispatch office + small canopy
    it = makeItem(zs, [135, 15, 0, 144, 21, 4]);
    box(it.g, 135, 15, 0, 144, 21, 3.6, Mat(p, p.wall));
    box(it.g, 134.8, 14.8, 3.6, 144.2, 21.2, 4.0, Mat(p, p.roof));
    let wd2 = "";
    for (let x = 136; x < 142.5; x += 1.75) wd2 += dpath([P(x, 21.02, 1.3), P(x + 1.3, 21.02, 1.3), P(x + 1.3, 21.02, 2.7), P(x, 21.02, 2.7)]);
    E("path", { d: wd2, fill: p.window }, it.g);
    E("path", { d: dpath([P(144.02, 15, 2.9), P(144.02, 21, 2.9), P(144.02, 21, 3.25), P(144.02, 15, 3.25)]), fill: p.green, opacity: 0.85 }, it.g);
    zs.shadow.push(shadowD(rect(135, 15, 144, 21), 4));
    [[148, 16.5, 1], [150.8, 18.9, 0.8], [155.5, 16.4, 0.95], [157.6, 19.6, 0.8], [134.5, 36.6, 0.9], [150, 38.1, 1], [152.6, 39.7, 0.8]]
      .forEach(q => { if (!(c.narrow && q[2] < 0.9)) tree(p, zs, q[0], q[1], q[2], c.grad); });
    convoy(p, zs, n, 134, 159.5, 1.9, 5);
  }

  const BUILD = { transit: buildTransit, arrived: buildArrived, customs: buildCustoms, awaiting: buildAwaiting, done: buildDone };

  // ── the scene ───────────────────────────────────────────────────────────
  let uid = 0;
  function Scene(container) {
    injectCSS();
    if (container.__hubops) container.__hubops.destroy();
    this.id = ++uid;
    this.el = container;
    this.listeners = { select: [] };
    this.data = { zones: [], note: "" };
    this.active = null;
    this.hover = null;
    this.t = 0;
    this.running = false;
    this.visible = true;
    this.perf = { frames: 0, ms: 0 };

    const root = this.root = document.createElement("div");
    root.className = "hop";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", "Daily operations — illustration with real counts");
    root.innerHTML = '<div class="hop-stage"><svg class="hop-svg" aria-hidden="true" focusable="false" preserveAspectRatio="xMidYMid meet"></svg><div class="hop-pins" aria-hidden="true"></div></div><div class="hop-labels"></div><p class="hop-note"></p>';
    this.stage = root.querySelector(".hop-stage");
    this.svg = root.querySelector(".hop-svg");
    this.pins = root.querySelector(".hop-pins");
    this.labels = root.querySelector(".hop-labels");
    this.noteEl = root.querySelector(".hop-note");
    container.appendChild(root);
    container.__hubops = this;

    // hover / click on the scene areas
    this._onOver = e => { const z = e.target.closest && e.target.closest("[data-hz]"); this._hover(z ? z.getAttribute("data-hz") : null); };
    this._onOut = e => { if (!e.relatedTarget || !this.svg.contains(e.relatedTarget)) this._hover(null); };
    this._onClick = e => { const z = e.target.closest && e.target.closest("[data-hz]"); if (z) this._emit(z.getAttribute("data-hz")); };
    this.svg.addEventListener("pointerover", this._onOver);
    this.svg.addEventListener("pointerout", this._onOut);
    this.svg.addEventListener("click", this._onClick);

    // motion preference, theme, visibility, size
    this.mqMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    this.mqDark = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    this._onMotion = () => this._sync();
    this._onTheme = () => this._themeChanged();
    if (this.mqMotion) this.mqMotion.addEventListener ? this.mqMotion.addEventListener("change", this._onMotion) : this.mqMotion.addListener(this._onMotion);
    if (this.mqDark) this.mqDark.addEventListener ? this.mqDark.addEventListener("change", this._onTheme) : this.mqDark.addListener(this._onTheme);
    this.mo = new MutationObserver(this._onTheme);
    this.mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class", "style"] });
    this._onVis = () => this._sync();
    document.addEventListener("visibilitychange", this._onVis);
    if ("IntersectionObserver" in window) {
      this.io = new IntersectionObserver(es => { this.visible = es[es.length - 1].isIntersecting; this._sync(); }, { rootMargin: "64px" });
      this.io.observe(root);
    }
    if ("ResizeObserver" in window) {
      this.ro = new ResizeObserver(() => this._resize());
      this.ro.observe(root);
    } else {
      this._onWinResize = () => this._resize();
      window.addEventListener("resize", this._onWinResize);
    }
    this._frame = this._frame.bind(this);
    this.narrow = null;
    this.compact = null;
    this._renderLabels();
    this._resize();
    if (!this.zs) { this.narrow = false; this.compact = false; this._build(); }
    this._sync();
  }

  Scene.prototype._themeChanged = function () {
    if (this.dead) return;
    const p = palette(this.root);
    if (this.p && p.land === this.p.land && p.blue === this.p.blue && p.road === this.p.road) return;
    this._build();
  };

  Scene.prototype._units = function () {
    const zs = this.data.zones;
    const max = zs.reduce((m, z) => Math.max(m, z.count), 0);
    const step = niceStep(max / MAXU);
    const u = {};
    zs.forEach(z => { u[z.id] = z.count <= 0 ? 0 : Math.min(MAXU, Math.max(1, Math.round(z.count / step))); });
    return { u, step };
  };

  // If drawing fails (no SVG support, a bad token value…), the scene is left
  // empty and the glass labels, counts and note — plain HTML — still work.
  Scene.prototype._build = function () {
    try { this._draw(); } catch (e) {
      this.zs = []; this.shimmer = [];
      try { while (this.svg.firstChild) this.svg.removeChild(this.svg.firstChild); } catch (e2) { /* ignore */ }
      if (window.console) console.warn("HubOpsScene: scene not drawn", e);
    }
  };
  Scene.prototype._safeTick = function (t, rest) {
    try { this._tick(t, rest); return true; } catch (e) {
      this.zs = []; this.running = false;
      if (window.console) console.warn("HubOpsScene: animation stopped", e);
      return false;
    }
  };

  Scene.prototype._draw = function () {
    const p = this.p = palette(this.root);
    const svg = this.svg;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    const { u } = this._units();
    const gid = "hopT" + this.id + "-" + (this.buildN = (this.buildN || 0) + 1);

    const defs = E("defs", null, svg);
    const rg = E("radialGradient", { id: gid, cx: "0.36", cy: "0.3", r: "0.78" }, defs);
    E("stop", { offset: "0", "stop-color": mix(p.tree, "#ffffff", p.dark ? 0.18 : 0.38) }, rg);
    E("stop", { offset: "0.55", "stop-color": p.tree }, rg);
    E("stop", { offset: "1", "stop-color": mix(p.tree, p.shadeInk, p.dark ? 0.3 : 0.22) }, rg);
    const sg = E("radialGradient", { id: gid + "s", cx: "0.5", cy: "0.5", r: "0.5" }, defs);
    E("stop", { offset: "0", "stop-color": p.dark ? "#000000" : "#2a3550", "stop-opacity": p.dark ? 0.4 : 0.07 }, sg);
    E("stop", { offset: "1", "stop-color": p.dark ? "#000000" : "#2a3550", "stop-opacity": 0 }, sg);

    const world = E("g", null, svg);
    // ── ground ──
    const ground = E("g", null, world);
    const c0 = P(0, D, -2.4), c1 = P(W, D, -2.4), cR = P(W, 0, -2.4);
    E("ellipse", { cx: r1((c0[0] + cR[0]) / 2 + 10), cy: r1((c0[1] + c1[1]) / 2 + 22), rx: r1((cR[0] - c0[0]) * 0.56), ry: r1((c1[1] - P(0, 0)[1]) * 0.42), fill: `url(#${gid}s)` }, ground);
    face(ground, [[0, D, 0], [W, D, 0], [W, D, -2.4], [0, D, -2.4]], p.landSide);
    face(ground, [[W, 0, 0], [W, D, 0], [W, D, -2.4], [W, 0, -2.4]], mix(p.landSide, p.shadeInk, p.dark ? 0.2 : 0.1));
    face(ground, [[0, 0, 0], [SEA_X, 0, 0], [SEA_X, SEA_Y, 0], [0, SEA_Y, 0]], p.water);
    face(ground, [[0, SEA_Y, 0], [SEA_X, SEA_Y, 0], [SEA_X, 0, 0], [W, 0, 0], [W, D, 0], [0, D, 0]], p.ground);
    // paved yards and lawns
    const pave = [rect(32, SEA_Y, 64, R0), rect(64, 22.4, 96, R0), rect(96, 14, 128, R0)];
    E("path", { d: pave.map(r => dpath(r.map(q => P(q[0], q[1], 0)))).join(""), fill: p.paving }, ground);
    E("path", { d: ZONES.map(z => dpath(rect(z.x0 + 1, 35.2, z.x1 - 1, D - 1.2).map(q => P(q[0], q[1], 0)))).join(""), fill: p.lawn }, ground);
    // quay edge
    face(ground, [[0, SEA_Y, 0], [SEA_X, SEA_Y, 0], [SEA_X, SEA_Y + 0.5, 0], [0, SEA_Y + 0.5, 0]], p.curb);
    face(ground, [[SEA_X, 0, 0], [SEA_X + 0.5, 0, 0], [SEA_X + 0.5, SEA_Y + 0.5, 0], [SEA_X, SEA_Y + 0.5, 0]], p.curb);
    // road, kerbs, markings
    face(ground, [[0, R0 - 0.7, 0], [W, R0 - 0.7, 0], [W, R1 + 1.1, 0], [0, R1 + 1.1, 0]], p.curb);
    face(ground, [[0, R0, 0], [W, R0, 0], [W, R1, 0], [0, R1, 0]], p.road);
    let md = "";
    for (let x = 0.6; x < W - 1; x += 4.4) md += dpath([P(x, 28.9, 0), P(x + 2.4, 28.9, 0), P(x + 2.4, 29.1, 0), P(x, 29.1, 0)]);
    md += dpath([P(0, 25.45, 0), P(W, 25.45, 0), P(W, 25.6, 0), P(0, 25.6, 0)]);
    md += dpath([P(0, 32.4, 0), P(W, 32.4, 0), P(W, 32.55, 0), P(0, 32.55, 0)]);
    E("path", { d: md, fill: p.mark }, ground);
    // fence along the back of the land
    const paintG = E("path", { fill: "none", stroke: p.paint, "stroke-width": 0.8 }, ground);
    const shadowG = E("g", null, ground);
    const groundDyn = E("g", null, ground);
    // sea shimmer
    const shimmer = [];
    if (!this.narrow) {
      const sh = E("g", null, groundDyn);
      [[6, 2.2], [20, 11], [27, 3.4], [47, 1.6], [58, 11.6], [12, 11.8], [36, 2.6]].forEach((q, i) => {
        const a = P(q[0], q[1], 0), b = P(q[0] + 2.2, q[1], 0);
        const l = E("path", { d: `M${r1(a[0])} ${r1(a[1])}L${r1(b[0])} ${r1(b[1])}`, stroke: p.dark ? "rgba(255,255,255,.10)" : "#ffffff", "stroke-width": 1.4, "stroke-linecap": "round", opacity: 0.9 }, sh);
        shimmer.push({ l, ph: i * 1.7, last: "" });
      });
    }
    const pads = {};
    ZONES.forEach(z => {
      const tone = (this.data.zones.find(d => d.id === z.id) || z).tone || z.tone;
      pads[z.id] = face(ground, [[z.x0 + 0.3, 0.3, 0], [z.x1 - 0.3, 0.3, 0], [z.x1 - 0.3, D - 0.3, 0], [z.x0 + 0.3, D - 0.3, 0]], p[tone] || p.blue,
        { class: "hop-pad", "fill-opacity": p.dark ? 0.1 : 0.06, stroke: "none" });
    });

    // ── zones ──
    const layer = E("g", null, world);
    const sky = E("g", null, world);
    const hits = E("g", null, world);
    const ctx = { p, grad: gid, paint: [], sky: null, groundDyn, narrow: this.narrow };
    const known = {};
    this.data.zones.forEach(z => { known[z.id] = z; });
    this.zs = [];
    this.anim = [];
    ZONES.forEach(z => {
      const g = E("g", { class: "hop-z", "data-z": z.id }, layer);
      const zs = { id: z.id, g, items: [], shadow: [], anim: [], order: null };
      ctx.sky = E("g", { class: "hop-z", "data-z": z.id }, sky);
      BUILD[z.id](ctx, zs, known[z.id] ? u[z.id] : 0);
      if (zs.shadow.length) E("path", { d: zs.shadow.join(""), fill: p.shadow }, shadowG);
      sortItems(zs);
      this.zs.push(zs);
      // hit area: the zone's footprint, raised a little
      const hp = [];
      for (const zz of [0, 7]) for (const q of rect(z.x0, 0, z.x1, D)) hp.push(P(q[0], q[1], zz));
      const h = E("path", { d: dpath(hull(hp)), class: "hop-hit", "data-hz": z.id }, hits);
      if (!known[z.id]) h.style.pointerEvents = "none";
    });
    paintG.setAttribute("d", ctx.paint.join(""));
    this.pads = pads;
    this.shimmer = shimmer;

    // viewBox: the slab plus headroom for the crane and the labels
    // wide: the far front-left and back-right corners (lawn, trees) run off the frame
    const minX = this.narrow ? P(0, D)[0] - 14 : P(0, 35)[0];
    const maxX = this.narrow ? P(W, 0)[0] + 14 : P(W, 9)[0];
    const top = P(0, 0, 0)[1] - (this.narrow ? 60 : 80), bottom = P(W, D, -2.4)[1] + (this.narrow ? 18 : 10);
    this.vb = { x: minX, y: top, w: maxX - minX, h: bottom - top };
    this._applyViewBox();
    this._applyClasses();
    this._tick(this.t, this.reduced());
  };

  Scene.prototype._applyViewBox = function () {
    if (!this.vb) return;
    const vb = this.vb, sw = this.stage.clientWidth || 1, sh = this.stage.clientHeight || 1;
    let { x, y, w, h } = vb;
    const ar = sw / sh;
    if (w / h < ar) { const nw = h * ar; x -= (nw - w) / 2; w = nw; }
    else { const nh = w / ar; y -= (nh - h) * 0.62; h = nh; }   // spare height goes mostly on top, for the labels
    this.view = { x, y, w, h, s: sw / w };
    this.svg.setAttribute("viewBox", `${r1(x)} ${r1(y)} ${r1(w)} ${r1(h)}`);
  };

  Scene.prototype.reduced = function () { return !!(this.mqMotion && this.mqMotion.matches); };

  Scene.prototype._tick = function (t, rest) {
    for (const zs of this.zs || []) {
      if (!zs.anim.length) continue;
      for (const f of zs.anim) f(t, !!rest);
      sortItems(zs);
    }
    if (rest) return;
    for (const s of this.shimmer || []) {
      const k = Math.sin(t * 0.5 + s.ph);
      const tr = `translate(${r1(k * 6)} 0)`;
      if (tr !== s.last) { s.l.setAttribute("transform", tr); s.l.setAttribute("opacity", r1((0.55 + 0.45 * Math.cos(t * 0.5 + s.ph)) * 100) / 100); s.last = tr; }
    }
  };

  Scene.prototype._frame = function (now) {
    this.raf = 0;
    if (!this.running) return;
    const dt = this.prev ? Math.min(0.1, (now - this.prev) / 1000) : 0;
    this.prev = now;
    this.t += dt;
    const a = performance.now();
    if (!this._safeTick(this.t, false)) return;
    this.perf.frames++; this.perf.ms += performance.now() - a;
    this.raf = requestAnimationFrame(this._frame);
  };

  Scene.prototype._sync = function () {
    if (this.dead) return;
    const run = !this.reduced() && !document.hidden && this.visible;
    if (run && !this.running) { this.running = true; this.prev = 0; this.raf = requestAnimationFrame(this._frame); }
    else if (!run && this.running) { this.running = false; if (this.raf) cancelAnimationFrame(this.raf); this.raf = 0; }
    if (this.reduced()) this._safeTick(0, true);
  };

  Scene.prototype._resize = function () {
    if (this.dead) return;
    const w = this.root.clientWidth;
    if (!w) return;
    const narrow = w < 640, compact = !narrow && w < 980;
    if (narrow !== this.narrow || compact !== this.compact) {
      this.narrow = narrow; this.compact = compact;
      this.root.classList.toggle("is-narrow", narrow);
      this.root.classList.toggle("is-compact", compact);
      if (narrow) {
        const t = P(0, 0, 0)[1] - 60, b = P(W, D, -2.4)[1] + 18;
        const vw = P(W, 0)[0] - P(0, D)[0] + 28;
        this.root.style.setProperty("--hop-ar", (vw / (b - t)).toFixed(3));
      }
      this._build();
    }
    this.width = w;
    this._applyViewBox();
    this._place();
  };

  // ── labels ──
  Scene.prototype._renderLabels = function () {
    const box = this.labels, pins = this.pins;
    box.textContent = ""; pins.textContent = "";
    this.labelEls = {}; this.pinEls = {};
    const zones = ZONES.map(z => this.data.zones.find(d => d.id === z.id)).filter(Boolean);
    zones.forEach((z, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "hop-label" + (z.count ? "" : " is-zero");
      b.dataset.z = z.id;
      b.style.setProperty("--tone", `var(--hub-${z.tone})`);
      b.setAttribute("aria-label", `${z.label}: ${fmt(z.count)} shipment${z.count === 1 ? "" : "s"}`);
      b.setAttribute("aria-pressed", "false");
      b.innerHTML = '<span class="hop-top"><i class="hop-dot"></i><b class="hop-count"></b><span class="hop-num"></span></span><span class="hop-name"></span><span class="hop-hint"></span><span class="hop-stem"></span>';
      b.querySelector(".hop-count").textContent = fmt(z.count);
      b.querySelector(".hop-num").textContent = String(i + 1);
      b.querySelector(".hop-name").textContent = z.label;
      const hint = b.querySelector(".hop-hint");
      if (z.hint) hint.textContent = z.hint; else hint.remove();
      b.querySelectorAll("span,i,b").forEach(s => s.setAttribute("aria-hidden", "true"));
      b.addEventListener("click", () => this._emit(z.id));
      b.addEventListener("pointerenter", () => this._hover(z.id));
      b.addEventListener("pointerleave", () => this._hover(null));
      b.addEventListener("focus", () => this._hover(z.id));
      b.addEventListener("blur", () => this._hover(null));
      box.appendChild(b);
      this.labelEls[z.id] = b;
      const pin = document.createElement("span");
      pin.className = "hop-pin" + (z.count ? "" : " is-zero");
      pin.style.setProperty("--tone", `var(--hub-${z.tone})`);
      pin.textContent = String(i + 1);
      pins.appendChild(pin);
      this.pinEls[z.id] = pin;
    });
    const { step } = this._units();
    const note = this.data.note;
    const scale = !zones.length ? "" : step === 1 ? "each vehicle or load = 1 shipment"
      : `each vehicle or load ≈ ${fmt(step)} shipments; every stage with shipments shows at least one`;
    // short footnote; the scale is a tooltip (it is still true, just not shouted)
    this.noteEl.textContent = note || "";
    if (scale) this.noteEl.title = scale; else this.noteEl.removeAttribute("title");
    this._applyClasses();
  };

  Scene.prototype._place = function () {
    const v = this.view;
    if (!v) return;
    const sw = this.stage.clientWidth;
    const pos = {};
    ZONES.forEach(z => { const a = P(...z.anchor); pos[z.id] = [(a[0] - v.x) * v.s, (a[1] - v.y) * v.s]; });
    for (const id in this.pinEls) this.pinEls[id].style.transform = `translate(${r1(pos[id][0])}px,${r1(pos[id][1])}px)`;
    if (this.narrow) return;
    // place each label above its anchor; lift earlier (higher, left) labels clear of later ones
    const ids = ZONES.map(z => z.id).filter(id => this.labelEls[id]);
    const rects = ids.map(id => {
      const el = this.labelEls[id];
      const w = el.offsetWidth, h = el.offsetHeight;
      return { id, el, w, h, x: pos[id][0], y: pos[id][1], stem: 18 };
    });
    rects.forEach(r => {
      r.l = clamp(r.x - r.w / 2, 8, sw - r.w - 8);
      r.t = r.y - r.stem - r.h;
    });
    const GAP = 8;
    for (let i = rects.length - 2; i >= 0; i--) {
      const a = rects[i];
      for (let j = i + 1; j < rects.length; j++) {
        const b = rects[j];
        const ox = Math.min(a.l + a.w, b.l + b.w) - Math.max(a.l, b.l);
        const oy = Math.min(a.t + a.h, b.t + b.h) - Math.max(a.t, b.t);
        if (ox <= -GAP || oy <= -GAP) continue;
        // lift the earlier (higher, further left) label; if that runs off the top, slide it left
        const room = a.t - 6;
        const lift = oy + GAP;
        if (lift <= room) { a.t -= lift; a.stem += lift; }
        else { a.t -= Math.max(0, room); a.stem += Math.max(0, room); a.l = Math.max(8, a.l - (ox + GAP)); }
      }
    }
    rects.forEach(r => {
      r.el.style.setProperty("--stem", r1(r.stem) + "px");
      r.el.style.transform = `translate(${r1(r.l)}px,${r1(r.t + r.h)}px) translateY(-100%)`;
      const stem = r.el.querySelector(".hop-stem");
      if (stem) stem.style.left = r1(clamp(r.x - r.l, 10, r.w - 10)) + "px";
    });
  };

  Scene.prototype._applyClasses = function () {
    const a = this.active, h = this.hover;
    this.root.classList.toggle("has-active", !!a);
    this.svg.querySelectorAll(".hop-z").forEach(g => g.classList.toggle("is-on", g.getAttribute("data-z") === a));
    for (const id in this.pads || {}) {
      this.pads[id].classList.toggle("is-on", id === a);
      this.pads[id].classList.toggle("is-hov", id === h);
    }
    for (const id in this.labelEls || {}) {
      const el = this.labelEls[id];
      el.classList.toggle("is-on", id === a);
      el.classList.toggle("is-hov", id === h);
      el.setAttribute("aria-pressed", id === a ? "true" : "false");
    }
  };

  Scene.prototype._hover = function (id) {
    if (id && !this.labelEls[id]) id = null;
    if (id === this.hover) return;
    this.hover = id;
    this._applyClasses();
  };

  Scene.prototype._emit = function (id) {
    if (!this.labelEls[id]) return;
    for (const fn of this.listeners.select.slice()) { try { fn(id); } catch (e) { setTimeout(() => { throw e; }); } }
  };

  // ── public API ──
  Scene.prototype.setData = function (data) {
    if (this.dead) return this;
    const seen = {};
    const zones = [];
    ((data && data.zones) || []).forEach(z => {
      if (!z || !ZDEF[z.id] || seen[z.id]) return;
      seen[z.id] = 1;
      const n = Number(z.count);
      zones.push({
        id: z.id,
        label: z.label != null && String(z.label).trim() ? String(z.label) : ZDEF[z.id].label,
        count: Number.isFinite(n) && n > 0 ? Math.floor(n) : 0,
        tone: TONES.indexOf(z.tone) >= 0 ? z.tone : ZDEF[z.id].tone,
        hint: z.hint ? String(z.hint) : "",
      });
    });
    this.data = { zones, note: data && data.note ? String(data.note) : "" };
    if (this.active && !seen[this.active]) this.active = null;
    if (this.hover && !seen[this.hover]) this.hover = null;
    this._build();
    this._renderLabels();
    this._place();
    return this;
  };

  Scene.prototype.on = function (evt, fn) {
    if (typeof fn !== "function" || !this.listeners[evt]) return () => {};
    this.listeners[evt].push(fn);
    return () => { const l = this.listeners[evt], i = l.indexOf(fn); if (i >= 0) l.splice(i, 1); };
  };

  Scene.prototype.setActive = function (id) {
    if (this.dead) return this;
    this.active = id && ZDEF[id] && this.data.zones.some(z => z.id === id) ? id : null;
    this._applyClasses();
    return this;
  };

  Scene.prototype.destroy = function () {
    if (this.dead) return;
    this.dead = true;
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    if (this.mo) this.mo.disconnect();
    if (this.io) this.io.disconnect();
    if (this.ro) this.ro.disconnect();
    if (this._onWinResize) window.removeEventListener("resize", this._onWinResize);
    document.removeEventListener("visibilitychange", this._onVis);
    const off = (mq, fn) => { if (mq) mq.removeEventListener ? mq.removeEventListener("change", fn) : mq.removeListener(fn); };
    off(this.mqMotion, this._onMotion);
    off(this.mqDark, this._onTheme);
    this.listeners = { select: [] };
    if (this.root.parentNode) this.root.parentNode.removeChild(this.root);
    if (this.el.__hubops === this) delete this.el.__hubops;
  };

  // average script time per animation frame, for checks (not part of the API)
  Scene.prototype._perf = function () {
    return { frames: this.perf.frames, avgMs: this.perf.frames ? this.perf.ms / this.perf.frames : 0, nodes: this.root.getElementsByTagName("*").length };
  };

  window.HubOpsScene = {
    mount(containerEl) {
      if (!containerEl || containerEl.nodeType !== 1) throw new TypeError("HubOpsScene.mount: an element is required");
      const s = new Scene(containerEl);
      return {
        setData: d => { s.setData(d); },
        on: (evt, fn) => s.on(evt, fn),
        setActive: id => { s.setActive(id); },
        destroy: () => s.destroy(),
        _perf: () => s._perf(),
      };
    },
  };
})();
