// ATLAS intro — the 30 s product film from the design handoff, played by a
// small clock of our own. Built with esbuild + Preact into ONE file
// (dashboard/static/intro/film.js); nothing is fetched from the network.
//
// Source: design_handoff_atlas_film/atlas-film-v2.jsx (Piece), unchanged
// apart from the asset path. The handoff's player, tweaks panel and authoring
// runtime are not used.
import { h as __h, render, Fragment as __Frag } from 'preact';

// The film's own code uses `h` as a variable name, so the JSX factory is __h.
const React = { Fragment: __Frag, createElement: __h };
const ASSETS = '/static/intro/atlas/';
const SCENES = [['Enter', 3], ['Observe', 4], ['Diagnose', 4], ['Plan', 5], ['Recover', 4],
                ['Human', 4], ['Takeover', 3], ['Learn', 3]];
const CUE_MAP = {};
let _acc = 0;
for (const [name, dur] of SCENES) { CUE_MAP[name] = _acc; _acc += dur; }
const TOTAL = _acc;                       // 30 s
const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
let NOW = { T: 0, CUES: CUE_MAP };
const useComposition = () => NOW;

// ATLAS — 30s product film. White world, one camera, one continuous clock (T / CUES).

const C = {
  ink: '#0E1A33', ink2: '#33405C', mute: '#6A7388', faint: '#A9B0BF', line: '#E4E8EF', route: '#CFD5E0', soft: '#F6F8FB',
  yellow: '#FFC72C', yline: '#F2B705', yd: '#8F6700', blue: '#2F6BFF', green: '#13A26B', amber: '#E58E0B', red: '#E5484D',
};
const TXT = { [C.blue]: '#2459D6', [C.green]: '#0D8A58', [C.amber]: '#A86400', [C.red]: '#C8323A', [C.mute]: '#5B6478', [C.yd]: '#8F6700' };
const FONT = "-apple-system, 'SF Pro Display', 'Helvetica Neue', Helvetica, Arial, sans-serif";

const EZ = {
  out: (p) => 1 - (1 - p) ** 3,
  out5: (p) => 1 - (1 - p) ** 5,
  io: (p) => (p < 0.5 ? 4 * p ** 3 : 1 - (-2 * p + 2) ** 3 / 2),
  sine: (p) => -(Math.cos(Math.PI * p) - 1) / 2,
  qout: (p) => p * (2 - p),
  lin: (p) => p,
  spr: (p) => (p >= 1 ? 1 : 1 - Math.exp(-6.5 * p) * Math.cos(10 * p)),
  soft: (p) => (p >= 1 ? 1 : 1 - Math.exp(-8 * p) * Math.cos(5.5 * p)),
};
const tw = (T, s, e, a = 0, b = 1, ease = EZ.io) => a + (b - a) * ease(clamp((T - s) / (e - s), 0, 1));
const pop = (T, s, d = 0.6) => tw(T, s, s + d, 0, 1, EZ.spr);
const inn = (T, s, d = 0.45) => tw(T, s, s + d, 0, 1, EZ.out);
const outp = (T, s, d = 0.4) => 1 - tw(T, s, s + d, 0, 1, EZ.sine);
const win = (T, a, b, d = 0.35) => Math.min(inn(T, a, d), outp(T, b, d));
const wob = (T, t0, amp, f = 11, k = 5) => (T < t0 ? 0 : amp * Math.exp(-k * (T - t0)) * Math.sin(f * (T - t0)));
const lerp = (a, b, p) => a + (b - a) * p;
const L2 = (a, b, p) => [lerp(a[0], b[0], p), lerp(a[1], b[1], p)];
function trk(T, keys) {
  if (T <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i++) {
    const [t1, v1, e] = keys[i], [t0, v0] = keys[i - 1];
    if (T < t1) {
      const p = (e || EZ.io)(clamp((T - t0) / Math.max(t1 - t0, 1e-4), 0, 1));
      return Array.isArray(v0) ? v0.map((a, j) => a + (v1[j] - a) * p) : v0 + (v1 - v0) * p;
    }
  }
  return keys[keys.length - 1][1];
}
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const mixC = (a, b, t) => { const A = hex(a), B = hex(b), k = clamp(t, 0, 1); return '#' + A.map((v, i) => Math.round(v + (B[i] - v) * k).toString(16).padStart(2, '0')).join(''); };
const colorAt = (T, keys, d = 0.3) => { let c = keys[0][1]; for (let i = 1; i < keys.length; i++) c = mixC(c, keys[i][1], tw(T, keys[i][0], keys[i][0] + d, 0, 1, EZ.sine)); return c; };

// ── world geometry ───────────────────────────────────────────────────────────
function bz(r, t) { const m = 1 - t, a = m * m * m, b = 3 * m * m * t, c = 3 * m * t * t, d = t * t * t; return [a * r[0][0] + b * r[1][0] + c * r[2][0] + d * r[3][0], a * r[0][1] + b * r[1][1] + c * r[2][1] + d * r[3][1]]; }
const dpath = (r) => `M${r[0]} C${r[1]} ${r[2]} ${r[3]}`;
const RT = {
  inL: [[-420, 470], [-120, 470], [160, 470], [380, 470]],
  air: [[380, 470], [700, 220], [1220, 220], [1540, 470]],
  ocean: [[380, 470], [760, 470], [1160, 470], [1540, 470]],
  road: [[380, 470], [700, 720], [1220, 720], [1540, 470]],
  outR: [[1540, 470], [1800, 470], [2060, 470], [2340, 470]],
};
const FAR = [
  { p: [[-420, 140], [-80, 140], [120, 470], [380, 470]], j: [[0, 0], [60, -80], [-50, 60], [0, 0]] },
  { p: [[-420, 820], [-80, 820], [120, 470], [380, 470]], j: [[0, 0], [50, 90], [-60, -50], [0, 0]] },
  { p: [[1540, 470], [1800, 470], [2000, 140], [2340, 140]], j: [[0, 0], [40, 70], [-70, -60], [0, 0]] },
  { p: [[1540, 470], [1800, 470], [2000, 820], [2340, 820]], j: [[0, 0], [50, -60], [-60, 70], [0, 0]] },
  { p: [[-420, 140], [300, -140], [1620, -140], [2340, 140]], j: [[0, 0], [140, 60], [-110, -70], [0, 0]] },
  { p: [[-420, 820], [300, 1100], [1620, 1100], [2340, 820]], j: [[0, 0], [120, -70], [-140, 60], [0, 0]] },
  { p: [[-420, 140], [-470, 380], [-470, 580], [-420, 820]], j: [[0, 0], [-50, 40], [40, -30], [0, 0]] },
  { p: [[2340, 140], [2390, 380], [2390, 580], [2340, 820]], j: [[0, 0], [50, -40], [-40, 30], [0, 0]] },
];
const farPath = (f, g) => f.p.map((pt, i) => [pt[0] + f.j[i][0] * (1 - g), pt[1] + f.j[i][1] * (1 - g)]);
const FAR_SHIP = [{ f: 0, t0: 0.2, v: 0.08, id: 'ATA-1051' }, { f: 2, t0: 0.5, v: 0.07, id: 'ATA-1063' }, { f: 4, t0: 0.3, v: 0.05, id: 'ATA-1072' }, { f: 5, t0: 0.62, v: 0.05, id: 'ATA-1088' }, { f: 3, t0: 0.15, v: 0.07, id: 'ATA-1094' }];
const AMB = [
  { id: 'ATA-1041', sub: 'Air', r: 'air', t0: 0.64, v: 0.04 },
  { id: 'ATA-1042', sub: 'Ocean', r: 'ocean', t0: 0.22, v: 0.055 },
  { id: 'ATA-1043', sub: 'Road', r: 'road', t0: 0.45, v: 0.03 },
  { id: 'ATA-1044', sub: 'Ocean', r: 'ocean', t0: 0.7, v: 0.05 },
];
const TS = 0.4 + 0.055 * 2.1 + 0.055 * 0.125;      // where ATA-1045 stops
const AA = bz(RT.air, TS);
const HA = bz(RT.road, 0.38);                      // where ATA-1046 stops
const DC = [AA[0] + 120, AA[1] + 30];
const CHIP = [0, 1, 2].map((i) => [DC[0] - 200 + 200 * i, DC[1] + 175]);
const PC = [AA[0] + 420, AA[1] + 120];
const ROW = [0, 1, 2, 3].map((i) => [AA[0] + 20, AA[1] - 20 + 66 * i]);
const BC = [AA[0] + 430, AA[1] + 60];
const RC = [AA[0] + 430, AA[1] + 330];
const BROWROW = (i) => [BC[0], BC[1] - 150 + 36 + 60 + 22 + 44 * i];
const HB = [HA[0] + 400, HA[1] + 120];
const ATL_D = [AA[0] - 330, AA[1] + 170], ATL_P = [AA[0] - 420, AA[1] + 190], ATL_R = [AA[0] - 300, AA[1] + 200], ATL_H = [HA[0] - 260, HA[1] + 200];
const SP = [ATL_P[0] + 70, ATL_P[1] - 20];
const STAGES = [[0.52, 'Extraction'], [0.66, 'Validation'], [0.8, 'Hub write'], [0.92, 'Read-back']];
const STRATS = ['Retry navigation', 'Re-open carrier page', 'Verify shipment page', 'Continue if verified'];
const CHAIN = [['Carrier selected', 0], ['Tracking opened', 0], ['Navigation error', 1]];
const LOOP = ['Observe', 'Diagnose', 'Plan', 'Recover', 'Verify', 'Learn'];

const POSES = {
  hello: { src: ASSETS + 'w-hello.png', neck: 0.6, px: 0.5 },
  monitor: { src: ASSETS + 'w-monitor.png', neck: 0.66, px: 0.47 },
  analyze: { src: ASSETS + 'w-analyze.png', neck: 0.63, px: 0.5 },
  recover: { src: ASSETS + 'w-recover.png', neck: 0.6, px: 0.55 },
  success: { src: ASSETS + 'w-success.png', neck: 0.63, px: 0.5 },
  action: { src: ASSETS + 'w-action.png', neck: 0.6, px: 0.5 },
};
const HALO = 'radial-gradient(closest-side, #000 70%, transparent 99%)';

// ── atoms ────────────────────────────────────────────────────────────────────
function Check({ size = 18, color = C.green, s = 1, o = 1 }) {
  return (
    <div style={{ width: size, height: size, borderRadius: '50%', background: color, display: 'flex', alignItems: 'center', justifyContent: 'center', transform: `scale(${s})`, opacity: o, flex: 'none' }}>
      <svg width={size * 0.62} height={size * 0.62} viewBox="0 0 12 12"><path d="M2.5 6.4l2.3 2.3 4.7-5" fill="none" stroke="#fff" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"></path></svg>
    </div>
  );
}
function Cross({ size = 18, color = C.red, s = 1, o = 1 }) {
  return (
    <div style={{ width: size, height: size, borderRadius: '50%', background: color, display: 'flex', alignItems: 'center', justifyContent: 'center', transform: `scale(${s})`, opacity: o, flex: 'none' }}>
      <svg width={size * 0.55} height={size * 0.55} viewBox="0 0 12 12"><path d="M3.5 3.5l5 5M8.5 3.5l-5 5" fill="none" stroke="#fff" strokeWidth="1.9" strokeLinecap="round"></path></svg>
    </div>
  );
}
function Pill({ c, text, o = 1, s = 1, size = 11.5 }) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 7, padding: '5px 11px', borderRadius: 999, background: '#fff', border: `1px solid ${mixC(c, '#ffffff', 0.7)}`, fontFamily: FONT, fontSize: size, fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', color: TXT[c] || c, whiteSpace: 'nowrap', opacity: o, transform: `scale(${s})`, boxShadow: '0 2px 8px rgba(14,26,51,0.06)' }}>
      <div style={{ width: 7, height: 7, borderRadius: '50%', background: c, flex: 'none' }}></div>
      {text}
    </div>
  );
}
function Card({ cx, cy, w = 168, h = 54, id, sub, dot, o = 1, s = 1, border = C.line, lift = 1, check = 0, children }) {
  if (o <= 0.003) return null;
  return (
    <div style={{ position: 'absolute', left: cx - w / 2, top: cy - h / 2, width: w, height: h, opacity: o, transform: `scale(${s})`, background: '#fff', borderRadius: 12, border: `1px solid ${border}`, boxShadow: `0 ${4 + 8 * lift}px ${14 + 24 * lift}px rgba(14,26,51,${0.05 + 0.05 * lift}), 0 1px 2px rgba(14,26,51,0.05)`, boxSizing: 'border-box', overflow: 'hidden', fontFamily: FONT }}>
      <div style={{ height: 52, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 14px', gap: 10 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: C.ink, whiteSpace: 'nowrap' }}>{id}</div>
          <div style={{ fontSize: 12, color: C.mute, whiteSpace: 'nowrap' }}>{sub}</div>
        </div>
        <div style={{ position: 'relative', width: 18, height: 18, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ width: 9, height: 9, borderRadius: '50%', background: dot, opacity: 1 - clamp(check, 0, 1) }}></div>
          {check > 0.01 && <div style={{ position: 'absolute', inset: 0 }}><Check size={18} s={check} /></div>}
        </div>
      </div>
      {children}
    </div>
  );
}
function Pin({ p, c, o = 1 }) {
  if (o <= 0.003) return null;
  return <div style={{ position: 'absolute', left: p[0] - 5, top: p[1] - 5, width: 10, height: 10, borderRadius: '50%', background: '#fff', border: `2px solid ${c}`, boxSizing: 'border-box', opacity: o }}></div>;
}
function Browser({ cx, cy, w, h, s = 1, o = 1, url, lift = 1, children }) {
  if (o <= 0.003) return null;
  return (
    <div style={{ position: 'absolute', left: cx - w / 2, top: cy - h / 2, width: w, height: h, opacity: o, transform: `scale(${s})`, background: '#fff', borderRadius: 14, border: `1px solid ${C.line}`, boxShadow: `0 ${10 + 20 * lift}px ${30 + 50 * lift}px rgba(14,26,51,${0.08 + 0.06 * lift}), 0 1px 2px rgba(14,26,51,0.06)`, overflow: 'hidden', fontFamily: FONT, boxSizing: 'border-box' }}>
      <div style={{ height: 36, background: C.soft, borderBottom: `1px solid ${C.line}`, display: 'flex', alignItems: 'center', gap: 12, padding: '0 14px' }}>
        <div style={{ display: 'flex', gap: 6 }}>
          {[0, 1, 2].map((i) => <div key={i} style={{ width: 9, height: 9, borderRadius: '50%', background: '#DCE0E7' }}></div>)}
        </div>
        <div style={{ flex: 1, height: 22, borderRadius: 6, background: '#fff', border: `1px solid ${C.line}`, display: 'flex', alignItems: 'center', padding: '0 10px', fontSize: 12, color: C.mute }}>{url}</div>
      </div>
      {children}
    </div>
  );
}

// ── ATLAS: the approved character renders, with a separable head for acting ──
function AtlasChar({ segs, x, y, s, lean, head, sqx, sqy }) {
  const B = 330 * s;
  return (
    <React.Fragment>
      <div style={{ position: 'absolute', left: x - B * 0.2, top: y + B * 0.36, width: B * 0.4, height: B * 0.07, borderRadius: '50%', background: 'radial-gradient(closest-side, rgba(14,26,51,0.16), rgba(14,26,51,0))' }}></div>
      <div style={{ position: 'absolute', left: x - B / 2, top: y - B / 2, width: B, height: B, transformOrigin: '50% 80%', transform: `rotate(${lean}deg) scale(${sqx}, ${sqy})` }}>
        <div style={{ position: 'absolute', inset: 0, WebkitMaskImage: HALO, maskImage: HALO }}>
          {segs.map(([pose, o], i) => {
            const P = POSES[pose], n = P.neck * 100;
            const bodyM = `linear-gradient(to bottom, transparent ${n - 1.5}%, #000 ${n + 0.5}%)`;
            const headM = `linear-gradient(to bottom, #000 ${n + 1}%, transparent ${n + 4.5}%)`;
            return (
              <div key={i + pose} style={{ position: 'absolute', inset: 0, opacity: o }}>
                <img src={P.src} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', WebkitMaskImage: bodyM, maskImage: bodyM }} />
                <img src={P.src} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', WebkitMaskImage: headM, maskImage: headM, transformOrigin: `${P.px * 100}% ${n}%`, transform: `translateX(${head * 0.7}px) rotate(${head}deg)` }} />
              </div>
            );
          })}
        </div>
      </div>
    </React.Fragment>
  );
}

// ── the film ─────────────────────────────────────────────────────────────────
function Piece({ sightLines, statusPill }) {
  const { T, CUES } = useComposition();
  const c = { E: CUES.Enter, O: CUES.Observe, D: CUES.Diagnose, P: CUES.Plan, R: CUES.Recover, H: CUES.Human, K: CUES.Takeover, L: CUES.Learn };
  const { E, O, D, P, R, H, K, L } = c;

  // camera
  const [cx, cy, z] = trk(T, [
    [0, [960, 560, 1.1]], [E + 2.8, [960, 540, 1.0], EZ.io],
    [O + 2.6, [1010, 520, 1.04], EZ.sine], [O + 3.9, [AA[0] + 20, AA[1] + 200, 1.2], EZ.io],
    [D + 1.3, [AA[0] + 70, AA[1] + 130, 1.55], EZ.io], [D + 4, [AA[0] + 85, AA[1] + 130, 1.6], EZ.sine],
    [P + 0.9, [AA[0] + 20, AA[1] + 140, 1.32], EZ.io], [P + 3.8, [AA[0] + 30, AA[1] + 140, 1.34], EZ.sine],
    [R + 0.9, [AA[0] + 150, AA[1] + 150, 1.36], EZ.io], [R + 3.9, [AA[0] + 160, AA[1] + 150, 1.4], EZ.sine],
    [H + 0.9, [HA[0] + 180, HA[1] + 60, 1.1], EZ.io], [H + 4.0, [HA[0] + 190, HA[1] + 60, 1.12], EZ.sine],
    [K + 0.45, [HA[0] + 300, HA[1] - 20, 1.38], EZ.out], [K + 2.0, [1660, HA[1] - 60, 1.38], EZ.io], [K + 2.9, [1350, 520, 1.22], EZ.io],
    [L + 0.05, [1350, 520, 1.22]], [L + 1.3, [960, 560, 0.72], EZ.io], [L + 2.6, [960, 700, 0.82], EZ.io],
  ]);
  const toScreen = (p) => [960 + (p[0] - cx) * z, 540 + (p[1] - cy) * z];

  // network focus / dim
  const nf = 1 - 0.72 * win(T, D + 0.3, H + 0.3, 0.6) - 0.3 * win(T, K + 0.2, K + 2.0, 0.3) - 0.5 * tw(T, L + 1.9, L + 2.5, 0, 1, EZ.sine);
  const g = tw(T, L + 1.85, L + 2.6, 0, 1, EZ.io);
  const scanX = tw(T, O + 0.2, O + 2.2, 200, 1750, EZ.sine);

  // ATA-1045 (the failure)
  const t45 = (t) => {
    if (t < O) return 0.4;
    const u = t - O;
    if (u < 2.1) return 0.4 + 0.055 * u;
    if (t < H + 0.8) return 0.4 + 0.1155 + 0.006875 * EZ.qout(clamp((u - 2.1) / 0.25, 0, 1));
    return (TS + 0.06 * (t - (H + 0.8))) % 1;
  };
  const k45 = tw(T, D + 0.3, D + 1.1, 0, 1, EZ.io) - tw(T, H, H + 0.8, 0, 1, EZ.io);
  const ws45 = trk(T, [[D + 0.3, DC], [P + 0.1, DC], [P + 0.8, PC], [R, PC], [R + 0.6, RC, EZ.soft]]);
  const [w45, h45] = trk(T, [[D + 0.3, [168, 54]], [D + 1.1, [400, 150], EZ.soft], [P + 0.1, [400, 150]], [P + 0.8, [340, 150]], [H, [340, 150]], [H + 0.8, [168, 54]]]);
  const pt45 = bz(RT.air, t45(T));
  const cen45 = L2([pt45[0], pt45[1] - 41], ws45, k45);
  const tt45 = t45(T), ef45 = T > H + 0.8 ? clamp((1 - tt45) / 0.05, 0, 1) * clamp(tt45 / 0.05, 0, 1) : 1;
  const c45 = colorAt(T, [[0, C.blue], [O + 2.55, C.red], [R + 0.9, C.blue], [R + 3.2, C.green]]);
  const s45 = 1 + wob(T, P + 4.45, 0.05) + wob(T, R + 3.2, 0.03) + wob(T, D + 1.1, 0.02);

  // ATA-1046 (human action → takeover)
  const t46 = (t) => (t < H + 0.3 ? 0.05 : t < K + 0.3 ? 0.05 + 0.33 * EZ.out(clamp((t - H - 0.3) / 0.8, 0, 1)) : t < K + 1.9 ? 0.38 + 0.57 * EZ.io(clamp((t - K - 0.3) / 1.6, 0, 1)) : 0.95 + 0.03 * EZ.out(clamp((t - K - 1.9) / 1.5, 0, 1)));
  const card46 = (t) => { const p = bz(RT.road, t46(t)); return [p[0], p[1] - 41]; };
  const pt46 = bz(RT.road, t46(T)), cen46 = card46(T);
  const c46 = colorAt(T, [[0, C.blue], [H + 1.15, C.amber], [H + 3.3, C.blue], [K + 1.95, C.green]]);
  const o46 = inn(T, H + 0.25, 0.3) * outp(T, L + 0.3, 0.6);

  // ATLAS position, acting
  const apos = (t) => {
    let p = trk(t, [
      [0, [2300, 830]], [E + 1.0, [2300, 830]], [E + 2.15, [960, 830], EZ.out5],
      [O + 2.62, [960, 830]], [O + 2.8, [952, 840], EZ.out], [O + 3.1, [958, 826], EZ.soft],
      [D, [958, 826]], [D + 0.2, [972, 836], EZ.sine], [D + 1.3, ATL_D, EZ.io],
      [D + 2.4, ATL_D], [D + 2.9, [ATL_D[0] + 26, ATL_D[1] - 4], EZ.soft],
      [P + 0.1, [ATL_D[0] + 26, ATL_D[1] - 4]], [P + 0.8, ATL_P, EZ.io],
      [P + 3.65, ATL_P], [P + 3.9, [ATL_P[0] - 18, ATL_P[1] + 4], EZ.sine], [P + 4.25, [ATL_P[0] + 34, ATL_P[1] - 4], EZ.out], [P + 4.8, [ATL_P[0] + 20, ATL_P[1]], EZ.soft],
      [R + 0.2, [ATL_P[0] + 20, ATL_P[1]]], [R + 1.0, ATL_R, EZ.io],
      [R + 3.05, ATL_R], [R + 3.2, [ATL_R[0], ATL_R[1] + 8], EZ.sine], [R + 3.45, [ATL_R[0], ATL_R[1] - 16], EZ.out], [R + 3.9, ATL_R, EZ.soft],
      [H + 1.3, ATL_R], [H + 1.45, [ATL_R[0] + 8, ATL_R[1] - 6], EZ.sine], [H + 2.1, ATL_H, EZ.io], [H + 2.45, [ATL_H[0] + 16, ATL_H[1] - 3], EZ.soft],
      [H + 3.5, [ATL_H[0] + 16, ATL_H[1] - 3]], [H + 3.8, ATL_H, EZ.soft],
    ]);
    const f = tw(t, K + 0.05, K + 0.5, 0, 1, EZ.io);
    if (f > 0) { const q = bz(RT.road, t46(t - 0.18)); p = L2(p, [q[0] - 250, q[1] + 175], f); }
    const l = tw(t, L, L + 1.2, 0, 1, EZ.io);
    if (l > 0) p = L2(p, [960, 600], l);
    return p;
  };
  const alive = 1 - win(T, O + 2.32, O + 2.6, 0.06) - 0.6 * win(T, D + 2.4, P + 0.2) - 0.7 * win(T, H + 2.9, H + 3.3, 0.1);
  const [ax0, ay0] = apos(T);
  const ay = ay0 + Math.sin(T * 2.2) * 4 * clamp(alive, 0, 1), ax = ax0;
  const pa = apos(T - 0.1), pb = apos(T - 0.16);
  const vx = (pa[0] - pb[0]) / 0.06;
  const leanAct = trk(T, [[0, 0], [O + 2.62, 0], [O + 2.8, -3, EZ.spr], [D, -3], [D + 0.3, 0], [D + 2.4, 0], [D + 2.9, 4, EZ.soft], [P + 0.1, 4], [P + 0.8, 0], [P + 3.65, 0], [P + 3.9, -5, EZ.sine], [P + 4.2, 7, EZ.out], [P + 4.8, 0, EZ.soft], [H + 2.1, 0], [H + 2.45, 5, EZ.soft], [H + 3.5, 5], [H + 3.8, -2, EZ.soft], [H + 4.0, 0], [K, 0], [K + 0.2, -4, EZ.sine], [K + 0.5, 5, EZ.out], [K + 2.0, 2], [K + 2.3, -2, EZ.soft], [K + 2.6, 0, EZ.soft]]);
  const lean = clamp(vx * 0.0045 + leanAct, -10, 10);
  const as = trk(T, [[0, 1], [D + 2.4, 1], [D + 2.9, 1.04, EZ.soft], [P + 0.1, 1.04], [P + 0.8, 1], [L, 1], [L + 1.2, 1.22, EZ.io]]);
  const SEG = [['hello', -99], ['monitor', O + 0.3], ['analyze', D + 0.6], ['recover', P + 3.95], ['monitor', R + 0.7], ['success', R + 3.25], ['monitor', H + 0.6], ['action', H + 1.55], ['monitor', H + 3.55], ['recover', K + 0.25], ['success', K + 2.0], ['monitor', K + 2.6], ['analyze', L + 0.3], ['hello', L + 2.0]];
  const segs = [];
  let swap = 0;
  SEG.forEach(([pose, a], i) => {
    const nx = SEG[i + 1] ? SEG[i + 1][1] : 999;
    if (T < a || T >= nx + 0.22) return;
    segs.push([pose, i === 0 ? 1 : clamp((T - a) / 0.22, 0, 1)]);
  });
  SEG.forEach(([, a], i) => { if (i) swap += wob(T, a, 0.035, 14, 7); });
  const sq = wob(T, E + 2.05, -0.05) + wob(T, R + 3.35, 0.035) + wob(T, K + 2.0, 0.03) + wob(T, P + 4.1, 0.03);
  const sqy = (1 + swap) * (1 + sq), sqx = (1 + swap) * (1 - sq * 0.8);

  // gaze → head turn + sight line
  const GAZE = [
    [0, () => [960, 400]], [E + 2.2, () => [380, 470]], [E + 2.75, () => [1540, 470]],
    [O + 0.25, () => [scanX, 420]], [O + 2.62, () => [AA[0], AA[1] - 41]],
    [D + 1.25, () => CHIP[0]], [D + 1.7, () => CHIP[1]], [D + 2.15, () => CHIP[2]], [D + 2.7, () => DC],
    [P + 0.9, () => ROW[0]], [P + 1.2, () => ROW[1]], [P + 1.5, () => ROW[2]], [P + 1.8, () => ROW[3]], [P + 2.3, () => ROW[0]], [P + 2.95, () => ROW[1]], [P + 4.0, () => PC],
    [R + 0.8, () => BC], [R + 2.0, () => BROWROW(0)], [R + 2.28, () => BROWROW(1)], [R + 2.56, () => BROWROW(2)], [R + 2.84, () => BROWROW(3)], [R + 3.2, () => RC],
    [H + 0.3, () => [1540, 470]], [H + 1.2, () => [HA[0], HA[1] - 41]], [H + 1.9, () => HB], [H + 3.55, () => [1540, 470]],
    [K + 0.1, () => cen46], [L + 0.2, () => [960, 300]], [L + 1.9, () => [960, 1000]],
  ];
  let gi = 0;
  GAZE.forEach(([t], i) => { if (T >= t) gi = i; });
  const gp = clamp((T - GAZE[gi][0]) / 0.45, 0, 1);
  const gz = gi > 0 ? L2(GAZE[gi - 1][1](), GAZE[gi][1](), EZ.soft(gp)) : GAZE[0][1]();
  const head = clamp((gz[0] - ax) / 70, -6, 6) * (1 - 0.5 * tw(T, L + 0.2, L + 1.0));
  const sl = clamp(0.35 * win(T, O + 0.3, O + 2.2, 0.3) + win(T, O + 2.62, D + 1.2, 0.2) + 0.8 * win(T, D + 1.25, D + 3.2, 0.3) + 0.8 * win(T, P + 2.3, P + 3.9, 0.25) + 0.7 * win(T, R + 2.0, R + 3.1, 0.2) + 0.8 * win(T, H + 1.2, H + 3.5, 0.2), 0, 1);
  const slC = colorAt(T, [[0, C.blue], [O + 2.6, C.red], [P, C.blue], [H + 1.2, C.amber], [H + 3.3, C.blue]]);
  const B = 330 * as;
  const visor = [ax, ay - 0.1 * B];

  // ATLAS status pill
  const PILLS = [[E + 2.25, 'Ready', C.mute], [O + 0.25, 'Observing', C.blue], [O + 2.6, 'Anomaly detected', C.red], [D + 0.9, 'Analyzing', C.blue], [P + 0.6, 'Planning recovery', C.blue], [P + 4.0, 'Recovering', C.blue], [R + 3.2, 'Recovery verified', C.green], [H + 1.2, 'Requesting help', C.amber], [H + 3.3, 'Verification confirmed', C.green], [K + 0.2, 'Taking over', C.blue], [K + 1.95, 'Success', C.green], [K + 2.6, 'Monitoring', C.blue], [L + 0.3, 'Learning', C.yd], [L + 1.9, null]];

  // ── render helpers ──
  const route = (r, col, w = 2, op = 1, p = 1, key) => <path key={key} d={dpath(r)} fill="none" stroke={col} strokeWidth={w} strokeLinecap="round" opacity={op} pathLength="1" strokeDasharray="1 1" strokeDashoffset={1 - p}></path>;
  const segPath = (r, a, b, col, w, op, key) => (b > a && op > 0.002 ? <path key={key} d={dpath(r)} fill="none" stroke={col} strokeWidth={w} strokeLinecap="round" opacity={op} pathLength="1" strokeDasharray={`0 ${a} ${b - a} 3`}></path> : null);

  const pIn = tw(T, E + 0.1, E + 0.7, 0, 1, EZ.io);
  const pBr = [0, 1, 2].map((j) => tw(T, E + 0.7 + j * 0.08, E + 1.55 + j * 0.08, 0, 1, EZ.io));
  const pOut = tw(T, E + 1.55, E + 2.0, 0, 1, EZ.io);
  const brC = mixC(C.yline, C.route, tw(T, E + 2.3, E + 3.1, 0, 1, EZ.sine));
  const airErr = win(T, O + 2.55, H + 0.8, 0.3), airSpread = 0.6 * tw(T, O + 2.55, D + 0.8, 0, 1, EZ.out);
  const airC = colorAt(T, [[0, C.red], [R + 0.9, C.blue], [R + 3.2, C.green]]);
  const roadG = win(T, K + 1.95, L + 0.6, 0.3), rgp = tw(T, K + 1.95, K + 2.5, 0, 1, EZ.io);
  const farP = (i) => tw(T, L + 0.1 + i * 0.06, L + 1.1 + i * 0.06, 0, 1, EZ.io);

  const learnSrc = [[380, 470], [1540, 470], [-420, 140], [2340, 140], [-420, 820], [2340, 820], bz(RT.ocean, 0.3), bz(RT.air, 0.8), bz(RT.road, 0.7)];
  const aScreen = toScreen([ax, ay]);
  const feedP = tw(T, L + 1.5, L + 1.85, 0, 1, EZ.io);
  const ringP = tw(T, L + 1.85, L + 2.8, 0, 1, EZ.out);

  return (
    <div data-screen-label={`t=${Math.floor(T)}s`} style={{ position: 'absolute', inset: 0, overflow: 'hidden', fontFamily: FONT }}>
      {/* The vignette is drawn by the page around the stage (VIGNETTE), so it
          reaches the window's edges whatever their shape. */}
      <div style={{ position: 'absolute', left: 0, top: 0, width: 1920, height: 1080, transformOrigin: '0 0', transform: `translate(960px,540px) scale(${z}) translate(${-cx}px,${-cy}px)` }}>

        {/* network */}
        <svg width="1920" height="1080" style={{ position: 'absolute', left: 0, top: 0, overflow: 'visible' }}>
          <g opacity={nf}>
            {FAR.map((f, i) => route(farPath(f, g), '#DCE1EA', 1.5, 1, farP(i), 'f' + i))}
            {route(RT.inL, C.yline, 2.5, 1, pIn, 'inL')}
            {route(RT.air, brC, 2, 1, pBr[0], 'air')}
            {route(RT.ocean, brC, 2, 1, pBr[1], 'oc')}
            {route(RT.road, brC, 2, 1, pBr[2], 'rd')}
            {route(RT.outR, C.yline, 2.5, 1, pOut, 'out')}
            {['air', 'ocean', 'road'].map((r, j) => [0, 1, 2].map((k) => {
              const t = ((Math.max(0, T - O) * 0.22 + k / 3 + j * 0.17) % 1);
              const hide = r === 'air' ? 1 - win(T, O + 2.55, R + 0.9, 0.2) : 1;
              const p = bz(RT[r], t);
              return <circle key={r + k} cx={p[0]} cy={p[1]} r="3" fill={r === 'air' && T > R + 0.9 && T < H + 0.8 ? airC : C.blue} opacity={0.5 * Math.sin(t * Math.PI) * inn(T, O, 0.6) * hide}></circle>;
            }))}
            {FAR.map((f, i) => [0, 1].map((k) => {
              const t = ((T * 0.12 + k / 2 + i * 0.13) % 1), p = bz(farPath(f, g), t);
              return <circle key={'fp' + i + k} cx={p[0]} cy={p[1]} r="2.6" fill={C.blue} opacity={0.4 * Math.sin(t * Math.PI) * farP(i)}></circle>;
            }))}
            {[[-420, 140], [2340, 140], [-420, 820], [2340, 820]].map((p, i) => <circle key={'fn' + i} cx={p[0]} cy={p[1]} r={6 * pop(T, L + 0.6 + i * 0.05)} fill="#fff" stroke={C.faint} strokeWidth="2"></circle>)}
            {[['AIR', bz(RT.air, 0.5), -18, 0], ['OCEAN', bz(RT.ocean, 0.5), -16, 1], ['ROAD', bz(RT.road, 0.5), 30, 2]].map(([n, p, dy, j]) => {
              const s = pop(T, E + 1.05 + j * 0.08, 0.5);
              return (
                <g key={n} opacity={clamp(s, 0, 1)}>
                  <circle cx={p[0]} cy={p[1]} r={5 * s} fill="#fff" stroke={brC} strokeWidth="2"></circle>
                  <text x={p[0]} y={p[1] + dy} textAnchor="middle" fill={C.mute} fontSize="12" fontWeight="600" letterSpacing="3" fontFamily={FONT}>{n}</text>
                </g>
              );
            })}
            {[['HUB', [380, 470], E + 0.65], ['DESTINATION', [1540, 470], E + 1.6]].map(([n, p, t]) => {
              const s = pop(T, t, 0.6);
              return (
                <g key={n} opacity={clamp(s, 0, 1)}>
                  <circle cx={p[0]} cy={p[1]} r={10 * s} fill="#fff" stroke={C.yline} strokeWidth="2.5"></circle>
                  <circle cx={p[0]} cy={p[1]} r={3.5 * s} fill={C.yline}></circle>
                  <text x={p[0]} y={p[1] + 34} textAnchor="middle" fill={C.ink2} fontSize="12" fontWeight="600" letterSpacing="3" fontFamily={FONT}>{n}</text>
                </g>
              );
            })}
          </g>
          {segPath(RT.air, Math.max(0, TS - airSpread), Math.min(1, TS + airSpread), airC, 3, airErr * (T < R + 0.9 ? 1 : 0.9), 'airx')}
          {segPath(RT.road, 0.95 - 0.95 * rgp, 0.95, C.green, 3, roadG, 'rdg')}
          {segPath(RT.outR, 0, tw(T, K + 2.1, K + 2.5, 0, 1, EZ.io), C.green, 3, roadG, 'outg')}
          {/* takeover stages */}
          {STAGES.map(([st, n], i) => {
            const p = bz(RT.road, st), o = pop(T, K + i * 0.07, 0.5) * outp(T, L + 0.6);
            if (o <= 0.003) return null;
            const pass = EZ.spr(clamp((t46(T) - st) / 0.035, 0, 1));
            const col = mixC(C.faint, C.green, pass);
            return (
              <g key={n} opacity={clamp(o, 0, 1)}>
                <circle cx={p[0]} cy={p[1]} r={13 * clamp(o, 0, 1.1)} fill="#fff" stroke={col} strokeWidth="2"></circle>
                <circle cx={p[0]} cy={p[1]} r={13 * clamp(pass, 0, 1.2)} fill={C.green} opacity={clamp(pass, 0, 1)}></circle>
                <path d={`M${p[0] - 5} ${p[1] + 0.5}l3.5 3.5 6.5-7`} fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" opacity={clamp(pass, 0, 1)}></path>
                <text x={p[0]} y={p[1] + 36} textAnchor="middle" fill={pass > 0.5 ? TXT[C.green] : C.mute} fontSize="13" fontWeight="600" letterSpacing="2.4" fontFamily={FONT}>{n.toUpperCase()}</text>
              </g>
            );
          })}
          {/* takeover data stream */}
          {T > K + 0.3 && T < K + 1.95 && [0, 1, 2, 3].map((k) => {
            const t = Math.min(1, t46(T) + ((T * 1.6 + k / 4) % 1) * 0.12), p = bz(RT.road, t);
            return <circle key={'ds' + k} cx={p[0]} cy={p[1]} r="3.5" fill={C.blue} opacity="0.8"></circle>;
          })}
          {/* learn: signals into ATLAS, feedback wave */}
          {learnSrc.map((s, i) => {
            const st = L + 0.5 + i * 0.07, p = clamp((T - st) / 0.6, 0, 1);
            if (p <= 0 || p >= 1) return null;
            const q = L2(s, [ax, ay], p * p);
            return <circle key={'ls' + i} cx={q[0]} cy={q[1]} r="5" fill={C.green} opacity={Math.sin(p * Math.PI) * 0.9}></circle>;
          })}
          {ringP > 0 && ringP < 1 && <circle cx={ax} cy={ay} r={60 + ringP * 1500} fill="none" stroke={C.yline} strokeWidth={3 / z} opacity={0.55 * (1 - ringP)}></circle>}
          {[0, 1].map((k) => { const ph = ((T * 1.2 + k * 0.5) % 1), o = win(T, O + 2.55, D + 0.6, 0.2) * (1 - k45); return o > 0 ? <circle key={'er' + k} cx={pt45[0]} cy={pt45[1]} r={8 + ph * 28} fill="none" stroke={C.red} strokeWidth="1.5" opacity={(1 - ph) * 0.6 * o}></circle> : null; })}
        </svg>

        {/* scanning field */}
        {T > O && T < O + 2.6 && (
          <div style={{ position: 'absolute', left: scanX - 160, top: 110, width: 320, height: 640, opacity: win(T, O + 0.1, O + 2.0, 0.3), background: 'linear-gradient(90deg, rgba(47,107,255,0) 0%, rgba(47,107,255,0.07) 50%, rgba(47,107,255,0) 100%)' }}>
            <div style={{ position: 'absolute', left: 159, top: 0, bottom: 0, width: 1.5, background: 'linear-gradient(180deg, rgba(47,107,255,0), rgba(47,107,255,0.35), rgba(47,107,255,0))' }}></div>
          </div>
        )}

        {/* ambient shipments */}
        {AMB.map((a, i) => {
          const t = (a.t0 + a.v * Math.max(0, T - O)) % 1, p = bz(RT[a.r], t);
          const ef = clamp(t / 0.05, 0, 1) * clamp((1 - t) / 0.05, 0, 1);
          const ap = pop(T, E + 2.3 + i * 0.1, 0.6);
          const chk = T > O ? EZ.spr(clamp((scanX - p[0]) / 160, 0, 1)) : 0;
          const glint = win(T, K + 2.05 + i * 0.08, K + 2.4 + i * 0.08, 0.15);
          const o = ef * clamp(ap, 0, 1) * nf;
          return (
            <React.Fragment key={a.id}>
              <Pin p={p} c={C.blue} o={o} />
              <Card cx={p[0]} cy={p[1] - 41} id={a.id} sub={a.sub} dot={C.blue} o={o} s={0.6 + 0.4 * ap} check={chk} lift={0.5} border={mixC(C.line, C.green, glint)} />
            </React.Fragment>
          );
        })}
        {FAR_SHIP.map((f, i) => {
          const t = (f.t0 + f.v * T) % 1, p = bz(farPath(FAR[f.f], g), t), o = farP(f.f) * clamp(t / 0.06, 0, 1) * clamp((1 - t) / 0.06, 0, 1) * nf;
          return (
            <React.Fragment key={f.id}>
              <Pin p={p} c={C.blue} o={o} />
              <Card cx={p[0]} cy={p[1] - 41} id={f.id} sub="In transit" dot={C.green} o={o} lift={0.4} check={1} />
            </React.Fragment>
          );
        })}

        {/* ATA-1046 */}
        <Pin p={pt46} c={c46} o={o46 * (T < K + 2.6 ? 1 : 0.6)} />
        {T > K + 0.4 && T < K + 1.9 && [0.05, 0.1].map((d, i) => { const q = card46(T - d); return <Card key={'gh' + i} cx={q[0]} cy={q[1]} id="ATA-1046" sub="Air · Carrier portal" dot={c46} o={0.18 - i * 0.08} lift={0} />; })}
        <Card cx={cen46[0]} cy={cen46[1]} id="ATA-1046" sub="Air · Carrier portal" dot={c46} o={o46} s={1 + wob(T, H + 1.15, 0.05) + wob(T, H + 3.3, 0.04) + wob(T, K + 1.95, 0.05)} border={colorAt(T, [[0, C.line], [H + 1.15, '#F6CF92'], [H + 3.3, C.line], [K + 1.95, '#A9E0C6']])} lift={T > H + 1.1 ? 1 : 0.5} check={pop(T, K + 1.95, 0.5)} />
        {(() => {
          const o = win(T, H + 1.15, H + 3.3, 0.25), s = pop(T, H + 1.15, 0.5);
          return o > 0 ? <div style={{ position: 'absolute', left: cen46[0] - 200, width: 400, top: cen46[1] - 70, display: 'flex', justifyContent: 'center', opacity: o, transform: `translateY(${(1 - s) * 10}px)` }}><Pill c={C.amber} text="Human action required" size={12.5} /></div> : null;
        })()}

        {/* diagnose: event chain assembles from the card */}
        <svg width="1920" height="1080" style={{ position: 'absolute', left: 0, top: 0, overflow: 'visible' }}>
          {[0, 1].map((i) => {
            const st = D + 1.1 + (i + 1) * 0.45, p = tw(T, st + 0.3, st + 0.55, 0, 1, EZ.io), cl = tw(T, P, P + 0.4, 0, 1, EZ.io);
            const x1 = CHIP[i][0] + 94, x2 = CHIP[i + 1][0] - 94, y = CHIP[i][1];
            return p > 0 && cl < 1 ? (
              <g key={'cn' + i} opacity={1 - cl}>
                <line x1={x1} y1={y} x2={x1 + (x2 - x1) * p} y2={y} stroke={i === 1 ? C.red : C.faint} strokeWidth="1.5"></line>
                {p > 0.95 && <path d={`M${x2 - 6} ${y - 4}l5 4-5 4`} fill="none" stroke={i === 1 ? C.red : C.faint} strokeWidth="1.5"></path>}
              </g>
            ) : null;
          })}
        </svg>
        {CHAIN.map(([text, err], i) => {
          const st = D + 1.1 + i * 0.45;
          if (T < st || T > P + 0.5) return null;
          const e = clamp((T - st) / 0.55, 0, 1), cl = tw(T, P, P + 0.5, 0, 1, EZ.io);
          let pos = L2(DC, CHIP[i], EZ.spr(e));
          pos = L2(pos, cen45, cl);
          const sc = (0.4 + 0.6 * EZ.soft(e)) * (1 - 0.6 * cl), rot = (1 - EZ.soft(e)) * (i - 1) * 10;
          const shake = err ? wob(T, st + 0.35, 8, 30, 7) : 0;
          const look = win(T, st + 0.1, st + 0.5, 0.1) * (1 - cl);
          const col = err ? C.red : C.ink;
          return (
            <div key={text} style={{ position: 'absolute', left: pos[0] - 94 + shake, top: pos[1] - 24, width: 188, height: 48, transform: `scale(${sc}) rotate(${rot}deg)`, opacity: inn(T, st, 0.15) * (1 - cl), background: err ? '#FFF6F6' : '#fff', border: `1px solid ${err ? '#F3B4B6' : mixC(C.line, C.blue, look * 0.6)}`, borderRadius: 12, boxShadow: '0 8px 24px rgba(14,26,51,0.08)', display: 'flex', alignItems: 'center', gap: 10, padding: '0 14px', boxSizing: 'border-box', fontFamily: FONT }}>
              {err ? <Cross size={18} s={pop(T, st + 0.3, 0.4)} /> : <Check size={18} color="#8A93A6" s={pop(T, st + 0.3, 0.4)} />}
              <div style={{ fontSize: 15, fontWeight: 500, color: err ? TXT[C.red] : col, whiteSpace: 'nowrap' }}>{text}</div>
            </div>
          );
        })}

        {/* plan: strategies */}
        {(() => {
          const o = win(T, P + 2.25, P + 3.9, 0.25);
          if (o <= 0) return null;
          const p = trk(T, [[P + 2.3, ROW[0]], [P + 2.95, ROW[0]], [P + 3.2, ROW[1], EZ.soft]]);
          return <div style={{ position: 'absolute', left: p[0] - 180, top: p[1] - 34, width: 360, height: 68, borderRadius: 15, border: `2px solid ${C.yellow}`, opacity: o, boxSizing: 'border-box' }}></div>;
        })()}
        {STRATS.map((text, i) => {
          const st = P + 0.85 + i * 0.32;
          if (T < st || T > R + 0.5) return null;
          const e = clamp((T - st) / 0.6, 0, 1);
          let pos = L2(SP, ROW[i], EZ.spr(e)), sc = 0.35 + 0.65 * EZ.soft(e), o = inn(T, st, 0.2);
          let border = C.line, bg = '#fff', badge = null;
          if (i === 0) {
            pos = [pos[0] + wob(T, P + 2.75, 12, 26, 6) - 14 * tw(T, P + 2.75, P + 3.1, 0, 1, EZ.out), pos[1]];
            o *= 1 - 0.55 * tw(T, P + 2.8, P + 3.2, 0, 1, EZ.sine);
            if (T > P + 2.75) { border = mixC(C.line, '#F3B4B6', inn(T, P + 2.75, 0.2)); badge = <Cross size={20} s={pop(T, P + 2.75, 0.4)} />; }
          }
          if (i === 1) {
            const sel = inn(T, P + 3.35, 0.25);
            border = mixC(C.line, C.yellow, sel); bg = mixC('#ffffff', '#FFF8E0', sel);
            sc *= 1 + wob(T, P + 3.35, 0.06);
            if (T > P + 3.35) badge = <Check size={20} color={C.yline} s={pop(T, P + 3.35, 0.4)} />;
            const sd = tw(T, P + 3.95, P + 4.45, 0, 1, EZ.io);
            pos = L2(pos, cen45, sd); sc *= 1 - 0.6 * sd; o *= 1 - clamp((sd - 0.75) / 0.25, 0, 1);
          }
          if (i > 1) o *= 1 - 0.4 * tw(T, P + 3.35, P + 3.7);
          const ex = tw(T, R, R + 0.45, 0, 1, EZ.io);
          pos = [pos[0] - 60 * ex, pos[1]]; o *= 1 - ex;
          return (
            <div key={text} style={{ position: 'absolute', left: pos[0] - 170, top: pos[1] - 27, width: 340, height: 54, transform: `scale(${sc})`, opacity: o, background: bg, border: `1px solid ${border}`, borderRadius: 12, boxShadow: '0 8px 24px rgba(14,26,51,0.08)', display: 'flex', alignItems: 'center', gap: 14, padding: '0 16px', boxSizing: 'border-box', fontFamily: FONT }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: C.mute, letterSpacing: '0.06em', fontVariantNumeric: 'tabular-nums' }}>{'0' + (i + 1)}</div>
              <div style={{ flex: 1, fontSize: 16, fontWeight: 500, color: C.ink, whiteSpace: 'nowrap' }}>{text}</div>
              {badge}
            </div>
          );
        })}
        {/* strategy trail */}
        {[0.06, 0.12, 0.18].map((d, k) => {
          const sd = tw(T, P + 3.95, P + 4.45, 0, 1, EZ.io) - d;
          if (sd <= 0 || sd >= 0.95) return null;
          const q = L2(ROW[1], cen45, sd);
          return <div key={'tr' + k} style={{ position: 'absolute', left: q[0] - 5, top: q[1] - 5, width: 10, height: 10, borderRadius: '50%', background: C.yellow, opacity: 0.7 - k * 0.2 }}></div>;
        })}

        {/* ATA-1045 */}
        <Pin p={pt45} c={c45} o={ef45 * inn(T, E + 2.4, 0.4) * (1 - k45) * (T < O + 2.55 ? nf : 1)} />
        {(() => {
          const eo = clamp((h45 - 90) / 50, 0, 1), recO = inn(T, R + 0.9, 0.3), W = w45 - 28;
          const ext = [inn(T, R + 3.6, 0.2), inn(T, R + 3.85, 0.2)];
          const flow = (T * 0.8) % 1;
          return (
            <Card cx={cen45[0]} cy={cen45[1]} w={w45} h={h45} id="ATA-1045" sub="AFKL Cargo · Air" dot={c45} o={ef45 * inn(T, E + 2.4, 0.4) * (k45 > 0.02 || T > O + 2.4 ? 1 : nf) * (1 - 0.4 * tw(T, L + 1.9, L + 2.5))} s={s45} lift={0.5 + 0.5 * k45} border={colorAt(T, [[0, C.line], [O + 2.55, '#F3B4B6'], [R + 0.9, '#BCD0FF'], [R + 3.2, '#A9E0C6'], [H + 0.8, C.line]])}>
              {eo > 0.01 && (
                <div style={{ padding: '4px 14px 0', opacity: eo, display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <svg width={W} height="34" style={{ overflow: 'visible' }}>
                    <line x1="6" y1="10" x2={W / 2} y2="10" stroke={C.route} strokeWidth="2"></line>
                    <line x1={W / 2} y1="10" x2={W - 6} y2="10" stroke={c45} strokeWidth="2.5" strokeDasharray={recO < 0.5 ? '5 5' : 'none'}></line>
                    {recO > 0.5 && <circle cx={W / 2 + (W / 2 - 6) * flow} cy="10" r="3.5" fill={c45}></circle>}
                    {recO < 0.5 && <path d={`M${W * 0.75 - 5} 5l10 10M${W * 0.75 + 5} 5l-10 10`} stroke={C.red} strokeWidth="2" strokeLinecap="round" opacity={1 - recO * 2}></path>}
                    {[[6, 'Hub', 'start'], [W / 2, 'AFKL', 'middle'], [W - 6, 'Destination', 'end']].map(([x, n, a], j) => (
                      <g key={n}>
                        <circle cx={x} cy="10" r="5" fill="#fff" stroke={j === 0 ? C.route : c45} strokeWidth="2"></circle>
                        <text x={x} y="32" textAnchor={a} dx={j === 0 ? -6 : j === 2 ? 6 : 0} fill={C.mute} fontSize="11" fontFamily={FONT}>{n}</text>
                      </g>
                    ))}
                  </svg>
                  <div style={{ position: 'relative', height: 26 }}>
                    <div style={{ position: 'absolute', left: 0, top: 0, opacity: 1 - recO }}><Pill c={C.red} text="AFKL navigation error" /></div>
                    <div style={{ position: 'absolute', left: 0, top: 0, opacity: recO * (1 - inn(T, R + 3.2, 0.25)) }}><Pill c={C.blue} text="Recovering" /></div>
                    <div style={{ position: 'absolute', left: 0, top: 0, opacity: inn(T, R + 3.2, 0.25), display: 'flex', alignItems: 'center', gap: 14 }}>
                      <Pill c={C.green} text="Verified" />
                      {['Extraction', 'Validation'].map((n, j) => (
                        <div key={n} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: C.ink2, opacity: ext[j], transform: `translateX(${(1 - ext[j]) * -8}px)` }}>
                          <Check size={15} s={pop(T, R + 3.6 + j * 0.25, 0.4)} />{n}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </Card>
          );
        })()}
        {(() => {
          const o = win(T, O + 2.55, D + 0.3, 0.2) * (1 - k45), s = pop(T, O + 2.55, 0.5);
          return o > 0 ? <div style={{ position: 'absolute', left: cen45[0] + 92, top: cen45[1] - 13, opacity: o, transform: `scale(${0.7 + 0.3 * s})`, transformOrigin: 'left center' }}><Pill c={C.red} text="Error" /></div> : null;
        })()}
        {/* impact ripple when the strategy lands */}
        {(() => {
          const p = clamp((T - (P + 4.45)) / 0.6, 0, 1);
          if (p <= 0 || p >= 1) return null;
          const g2 = 6 + p * 30;
          return <div style={{ position: 'absolute', left: cen45[0] - w45 / 2 - g2, top: cen45[1] - h45 / 2 - g2, width: w45 + g2 * 2, height: h45 + g2 * 2, borderRadius: 12 + g2, border: `2px solid ${C.yellow}`, opacity: 1 - p, boxSizing: 'border-box' }}></div>;
        })()}

        {/* recover: carrier page reopens */}
        {(() => {
          const f = tw(T, R + 0.15, R + 0.85, 0, 1, EZ.soft) - tw(T, H, H + 0.6, 0, 1, EZ.io);
          if (f <= 0.005) return null;
          const pos = L2(cen45, BC, f), load = tw(T, R + 0.8, R + 1.7, 0, 1, EZ.io), sk = win(T, R + 0.7, R + 1.75, 0.2);
          const rows = [['Carrier', 'AFKL Cargo'], ['Reference', '057-4821 3690'], ['Shipment page', 'ATA-1045 · Tracking'], ['Data', '6 events · 2 pcs · 412 kg']];
          const ver = inn(T, R + 3.2, 0.3);
          return (
            <Browser cx={pos[0]} cy={pos[1]} w={460} h={300} s={0.3 + 0.7 * clamp(f, 0, 1.05)} o={clamp(f * 3, 0, 1)} url="afklcargo.com / tracking / ATA-1045">
              <div style={{ height: 2, background: C.soft }}><div style={{ height: 2, width: `${load * 100}%`, background: C.blue, opacity: 1 - inn(T, R + 1.75, 0.3) }}></div></div>
              <div style={{ height: 58, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 20px' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  <div style={{ fontSize: 17, fontWeight: 600, color: C.ink }}>AFKL Cargo</div>
                  <div style={{ fontSize: 12.5, color: C.mute }}>Shipment tracking</div>
                </div>
                <div style={{ position: 'relative', width: 130, height: 28, display: 'flex', justifyContent: 'flex-end' }}>
                  <div style={{ position: 'absolute', right: 0, opacity: 1 - ver }}><Pill c={C.blue} text={T < R + 1.75 ? 'Loading' : 'Checking'} /></div>
                  <div style={{ position: 'absolute', right: 0, opacity: ver, transform: `scale(${0.8 + 0.2 * pop(T, R + 3.2, 0.5)})` }}><Pill c={C.green} text="Verified" /></div>
                </div>
              </div>
              <div style={{ position: 'relative', padding: '0 20px' }}>
                {sk > 0 && (
                  <div style={{ position: 'absolute', left: 20, right: 20, top: 6, display: 'flex', flexDirection: 'column', gap: 22, opacity: sk }}>
                    {[0.7, 0.55, 0.8, 0.6].map((wd, i) => <div key={i} style={{ height: 12, width: `${wd * 100}%`, borderRadius: 6, background: `linear-gradient(90deg, #EEF1F5 ${(T * 60 % 100) - 30}%, #F7F9FB ${(T * 60 % 100)}%, #EEF1F5 ${(T * 60 % 100) + 30}%)` }}></div>)}
                  </div>
                )}
                {rows.map(([k, v], i) => {
                  const ro = inn(T, R + 1.75 + i * 0.1, 0.35), hl = win(T, R + 1.95 + i * 0.28, R + 2.3 + i * 0.28, 0.12);
                  return (
                    <div key={k} style={{ height: 44, display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderTop: i ? `1px solid ${C.line}` : 'none', opacity: ro, transform: `translateY(${(1 - ro) * 8}px)`, background: `rgba(47,107,255,${0.05 * hl})`, margin: '0 -20px', padding: '0 20px' }}>
                      <div style={{ display: 'flex', gap: 16, alignItems: 'baseline' }}>
                        <div style={{ width: 104, fontSize: 13, color: C.mute }}>{k}</div>
                        <div style={{ fontSize: 15, color: C.ink, fontWeight: 500 }}>{v}</div>
                      </div>
                      {T > R + 2.05 + i * 0.28 && <Check size={20} s={pop(T, R + 2.05 + i * 0.28, 0.45)} />}
                    </div>
                  );
                })}
              </div>
            </Browser>
          );
        })()}
        {/* data flows from carrier page into ATA */}
        {T > R + 3.4 && T < R + 4.2 && [0, 1, 2, 3, 4].map((k) => {
          const p = clamp((T - (R + 3.4 + k * 0.1)) / 0.4, 0, 1);
          if (p <= 0 || p >= 1) return null;
          const q = L2([BC[0] - 60 + k * 30, BC[1] + 150], [RC[0] - 60 + k * 30, RC[1] - 75], EZ.io(p));
          return <div key={'df' + k} style={{ position: 'absolute', left: q[0] - 4, top: q[1] - 4, width: 8, height: 8, borderRadius: '50%', background: C.blue, opacity: Math.sin(p * Math.PI) }}></div>;
        })}

        {/* human: session brought forward */}
        {(() => {
          const f = tw(T, H + 1.7, H + 2.35, 0, 1, EZ.soft) - tw(T, H + 3.6, H + 4.0, 0, 1, EZ.io);
          if (f <= 0.005) return null;
          const pos = L2(cen46, HB, f), ok = inn(T, H + 3.3, 0.3), ph = (T * 0.9) % 1;
          return (
            <Browser cx={pos[0]} cy={pos[1]} w={460} h={290} s={0.25 + 0.75 * clamp(f, 0, 1.05)} o={clamp(f * 3, 0, 1)} lift={1.3} url="Carrier portal · ATA-1046">
              <div style={{ height: 50, display: 'flex', alignItems: 'center', gap: 8, padding: '0 20px', borderBottom: `1px solid ${C.line}` }}>
                <div style={{ width: 7, height: 7, borderRadius: '50%', background: C.yline }}></div>
                <div style={{ fontSize: 13, color: C.ink2 }}>Session prepared by ATLAS</div>
              </div>
              <div style={{ position: 'relative', height: 202 }}>
                <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14, opacity: 1 - ok }}>
                  <div style={{ position: 'relative', width: 64, height: 64 }}>
                    <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', border: `2px solid ${C.amber}`, transform: `scale(${1 + ph * 0.6})`, opacity: (1 - ph) * 0.6 }}></div>
                    <div style={{ position: 'absolute', inset: 0, borderRadius: '50%', background: '#FFF4E2', border: `2px solid ${C.amber}`, overflow: 'hidden', boxSizing: 'border-box' }}>
                      <div style={{ position: 'absolute', left: 21, top: 13, width: 18, height: 18, borderRadius: '50%', background: C.amber }}></div>
                      <div style={{ position: 'absolute', left: 12, top: 36, width: 36, height: 30, borderRadius: '18px 18px 0 0', background: C.amber }}></div>
                    </div>
                  </div>
                  <div style={{ fontSize: 21, fontWeight: 600, color: C.ink }}>Verification pending</div>
                  <div style={{ fontSize: 13.5, color: C.mute }}>Operator completes this step</div>
                </div>
                <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14, opacity: ok }}>
                  <Check size={64} s={pop(T, H + 3.3, 0.5)} />
                  <div style={{ fontSize: 21, fontWeight: 600, color: C.ink }}>Verification confirmed</div>
                  <div style={{ fontSize: 13.5, color: C.mute }}>Session handed back to ATLAS</div>
                </div>
              </div>
            </Browser>
          );
        })()}

        {/* sight line: what ATLAS is looking at */}
        {sightLines && sl > 0.01 && (
          <svg width="1920" height="1080" style={{ position: 'absolute', left: 0, top: 0, overflow: 'visible' }}>
            <line x1={visor[0]} y1={visor[1]} x2={gz[0]} y2={gz[1]} stroke={slC} strokeWidth={1.4} strokeDasharray="2 6" strokeLinecap="round" opacity={0.55 * sl}></line>
            <circle cx={gz[0]} cy={gz[1]} r={16 + 2 * Math.sin(T * 5)} fill="none" stroke={slC} strokeWidth="1.5" opacity={0.6 * sl}></circle>
          </svg>
        )}

        {/* ATLAS */}
        <AtlasChar segs={segs} x={ax} y={ay} s={as} lean={lean} head={head} sqx={sqx} sqy={sqy} />
        {statusPill && PILLS.map(([t, text, col], i) => {
          if (!text) return null;
          const nx = PILLS[i + 1][0], o = win(T, t, nx - 0.1, 0.2);
          if (o <= 0) return null;
          const s = pop(T, t, 0.5);
          return <div key={text} style={{ position: 'absolute', left: ax - 200, width: 400, top: ay - B * 0.5 - 14, display: 'flex', justifyContent: 'center', opacity: o, transform: `translateY(${(1 - clamp(s, 0, 1)) * 6}px) scale(${0.85 + 0.15 * s})` }}><Pill c={col} text={text} /></div>;
        })}
        {(() => {
          const o = win(T, H + 2.3, H + 3.3, 0.25), s = pop(T, H + 2.3, 0.6);
          if (o <= 0) return null;
          return (
            <div style={{ position: 'absolute', left: ax + B * 0.26, top: ay - B * 0.42, opacity: o, transform: `scale(${0.7 + 0.3 * s})`, transformOrigin: '0% 100%', background: '#fff', border: `1px solid ${C.line}`, borderRadius: '18px 18px 18px 4px', padding: '14px 20px', boxShadow: '0 12px 32px rgba(14,26,51,0.10)', fontSize: 20, fontWeight: 500, color: C.ink, whiteSpace: 'nowrap', fontFamily: FONT }}>
              I need you for one step.
            </div>
          );
        })()}
      </div>

      {/* ── screen-space type ── */}
      {(() => {
        const o = outp(T, O + 0.6, 0.45), up = tw(T, O + 0.6, O + 1.05, 0, -18, EZ.io), bar = tw(T, E + 1.85, E + 2.4, 0, 56, EZ.out);
        if (o <= 0) return null;
        return (
          <div style={{ position: 'absolute', left: 150, top: 770, opacity: o, transform: `translateY(${up}px)`, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ width: bar, height: 4, borderRadius: 2, background: C.yellow }}></div>
            <div style={{ display: 'flex', overflow: 'hidden', height: 112, fontSize: 104, fontWeight: 600, color: C.ink, letterSpacing: '0.02em', lineHeight: '112px' }}>
              {'ATLAS'.split('').map((ch, i) => <span key={i} style={{ display: 'inline-block', transform: `translateY(${(1 - EZ.out5(clamp((T - (E + 1.95 + i * 0.05)) / 0.7, 0, 1))) * 112}px)` }}>{ch}</span>)}
            </div>
            <div style={{ fontSize: 30, color: C.mute, opacity: inn(T, E + 2.3, 0.5), transform: `translateY(${(1 - inn(T, E + 2.3, 0.5)) * 10}px)` }}>Operational Intelligence</div>
          </div>
        );
      })()}
      {[[R + 3.2, H + 0.4, 'Recovery verified'], [K + 2.0, K + 2.9, 'Success']].map(([a, b, text]) => {
        const o = win(T, a, b, 0.3), s = pop(T, a, 0.6);
        if (o <= 0) return null;
        return (
          <div key={text} style={{ position: 'absolute', left: 0, right: 0, top: 930, display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 18, opacity: o, transform: `translateY(${(1 - clamp(s, 0, 1)) * 14}px)` }}>
            <Check size={40} s={s} />
            <div style={{ fontSize: 44, fontWeight: 500, color: C.ink, letterSpacing: '-0.005em' }}>{text}</div>
          </div>
        );
      })}
      {/* learn loop */}
      {(() => {
        const o = outp(T, L + 1.85, 0.3);
        if (T < L + 0.9 || o <= 0) return null;
        return (
          <div style={{ position: 'absolute', left: 385, top: 930, width: 1150, height: 40, display: 'flex', alignItems: 'center', opacity: o }}>
            {LOOP.map((w, i) => {
              const s = clamp((T - (L + 0.95 + i * 0.1)) / 0.5, 0, 1), last = i === LOOP.length - 1;
              return (
                <React.Fragment key={w}>
                  <div style={{ width: 150, textAlign: 'center', fontSize: 24, fontWeight: 600, letterSpacing: '0.14em', textTransform: 'uppercase', color: last ? C.yd : C.ink, opacity: clamp(s * 2, 0, 1), transform: `translateY(${(1 - EZ.spr(s)) * 16}px)` }}>
                    {w}
                    {last && <div style={{ margin: '6px auto 0', width: 40 * inn(T, L + 1.5, 0.3), height: 3, borderRadius: 2, background: C.yellow }}></div>}
                  </div>
                  {!last && <div style={{ width: 50, textAlign: 'center', fontSize: 22, color: C.faint, opacity: clamp(s * 2, 0, 1) }}>→</div>}
                </React.Fragment>
              );
            })}
          </div>
        );
      })()}
      {feedP > 0 && feedP < 1 && (() => { const q = L2([1460, 930], aScreen, feedP); return <div style={{ position: 'absolute', left: q[0] - 7, top: q[1] - 7, width: 14, height: 14, borderRadius: '50%', background: C.yellow, boxShadow: `0 0 0 6px rgba(255,199,44,0.25)` }}></div>; })()}
      {/* lockup */}
      {(() => {
        const l = inn(T, L + 2.0, 0.6), t1 = inn(T, L + 2.12, 0.6), t2 = inn(T, L + 2.25, 0.6);
        if (l <= 0) return null;
        return (
          <div style={{ position: 'absolute', left: 0, right: 0, top: 690, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 18 }}>
            <img src={ASSETS + 'mantrac-logo.png'} style={{ width: 240, height: 64, opacity: l, transform: `translateY(${(1 - l) * 12}px)` }} />
            <div style={{ fontSize: 46, fontWeight: 600, color: C.ink, letterSpacing: '0.3em', paddingLeft: '0.3em', opacity: t1, transform: `translateY(${(1 - t1) * 12}px)` }}>ATA CONTROL TOWER</div>
            <div style={{ fontSize: 24, color: C.mute, letterSpacing: '0.04em', opacity: t2 }}>ATLAS · Operational Intelligence</div>
          </div>
        );
      })()}
      {[...new Set(Object.values(POSES).map((p) => p.src))].map((s) => <link key={s} rel="preload" as="image" href={s} />)}
    </div>
  );
}



// ── the player ──────────────────────────────────────────────────────────
// One 1920×1080 stage, scaled to fit the window, re-rendered per animation
// frame from the clock. stop() unmounts everything: after the intro nothing
// of the film keeps running.
function preload(timeoutMs) {
  const urls = ['hello', 'monitor', 'analyze', 'recover', 'success', 'action']
    .map((n) => ASSETS + 'w-' + n + '.png').concat([ASSETS + 'mantrac-logo.png']);
  const all = Promise.all(urls.map((u) => new Promise((ok) => {
    const img = new Image();
    img.onload = img.onerror = () => (img.decode ? img.decode().catch(() => {}).then(ok) : ok());
    img.src = u;
  })));
  return Promise.race([all, new Promise((ok) => setTimeout(ok, timeoutMs))]);
}

function start(host, opts) {
  opts = opts || {};
  const stage = document.createElement('div');
  stage.className = 'film-stage';
  stage.style.cssText = 'position:absolute;left:50%;top:50%;width:1920px;height:1080px;' +
    'transform-origin:0 0;overflow:hidden;';
  host.appendChild(stage);
  const fit = () => {
    // Landscape: the whole 1920-wide frame. Portrait: the network lives
    // between x≈240 and x≈1680, so the outer margins are cropped and the
    // picture is shown bigger rather than as a thin strip.
    const portrait = host.clientWidth < host.clientHeight;
    const s = Math.min(host.clientWidth / (portrait ? 1440 : 1920), host.clientHeight / 1080);
    stage.style.transform = 'scale(' + s + ') translate(-50%,-50%)';
  };
  fit();
  addEventListener('resize', fit);
  let raf = 0, t0 = 0, stopped = false, ended = false;
  let broken = false;
  const draw = (T) => {
    NOW = { T, CUES: CUE_MAP };
    try {
      render(__h(Piece, { sightLines: opts.sightLines !== false, statusPill: opts.statusPill !== false }), stage);
    } catch (error) {
      // A frame that cannot be drawn must never trap the operator in the
      // intro: report it once and go straight to the way out.
      if (!broken && window.console) console.error('ATLAS intro:', error);
      broken = true;
    }
  };
  const finish = () => {
    if (ended) return;
    ended = true;
    draw(TOTAL);
    if (opts.onEnd) opts.onEnd();
  };
  const tick = (now) => {
    if (stopped) return;
    if (!t0) t0 = now;
    const T = Math.min(TOTAL, (now - t0) / 1000);
    draw(T);
    if (opts.onProgress) opts.onProgress(T / TOTAL);
    if (T >= TOTAL || broken) { finish(); return; }
    raf = requestAnimationFrame(tick);
  };
  draw(0);
  if (opts.still) { finish(); }
  else preload(opts.waitMs || 4000).then(() => { if (!stopped) raf = requestAnimationFrame(tick); });
  return {
    total: TOTAL,
    stop() {
      stopped = true;
      cancelAnimationFrame(raf);
      removeEventListener('resize', fit);
      render(null, stage);
      stage.remove();
    },
    seek(T) { t0 = performance.now() - T * 1000; draw(T); },
  };
}

// The film's backdrop, for the page to paint behind the (transparent) stage.
const VIGNETTE = 'radial-gradient(ellipse 80% 70% at 50% 55%, #FFFFFF 40%, #F6F8FB 100%)';
window.AtlasIntro = { start, total: TOTAL, scenes: SCENES, vignette: VIGNETTE };
