"""Visual design shared by the three pages (pure presentation, no data access).

Design: an app frame around a rounded canvas, a floating black pill navigation rail with icon buttons (orange
circle = current page, label on hover, light/dark switch), white rounded cards, colourful cover cards, pastel icon
tiles, a gauge card, orange cover banners, entrance animations and the Mantrac logo.

The light or dark design is chosen with the switch in the rail and forced with CSS regardless of the viewer's
Snowsight theme, so tables and charts are drawn by the helpers below instead of theme-dependent widgets.
Cards and tiles become clickable by placing an invisible Streamlit button over them (see `hit`).
"""
from __future__ import annotations

import base64
import html
import math
import zlib
from pathlib import Path
from urllib.parse import quote

import streamlit as st

LOGO_FILE = Path(__file__).resolve().parent.parent / "assets" / "mantrac_logo.svg"
NAV = [("Analysis", "chart"), ("Recommendations", "target"), ("Assistant", "chat")]
YELLOW = "#FCBC00"
RAIL = "#0B0B0D"

MODES = {
    "light": {"frame": "#E4E3E8", "canvas": "#F3F2F5", "card": "#FFFFFF", "card2": "#F6F5F8", "ink": "#17171A",
              "muted": "#6E6E78", "line": "#E4E2EA", "line2": "#F0EFF3", "accent": "#F26B1D", "accent2": "#FF9A4D",
              "soft": "#FFE4D2", "accentink": "#B8460B", "shadow": "rgba(14,14,16,.07)", "grid": "#EFEEF3"},
    "dark": {"frame": "#000000", "canvas": "#121215", "card": "#1A1A1F", "card2": "#22222A", "ink": "#F3F3F5",
             "muted": "#9C9CA8", "line": "#2B2B33", "line2": "#24242C", "accent": "#FF7A2E", "accent2": "#FFA05C",
             "soft": "#3A2416", "accentink": "#FFB07A", "shadow": "rgba(0,0,0,.45)", "grid": "#26262E"},
}
# tile/badge colours: (light background, foreground, dark background)
PASTELS = {"orange": ("#FFE2CC", "#C2410C", "rgba(255,122,46,.16)"), "pink": ("#FCDDE6", "#BE3A5F", "rgba(242,100,130,.16)"),
           "mint": ("#D6F3E7", "#0F7A57", "rgba(52,199,140,.16)"), "peach": ("#FFE9D6", "#B5581B", "rgba(255,160,92,.16)"),
           "sky": ("#DCEBFC", "#2463A8", "rgba(76,141,240,.18)"), "sun": ("#FFF0C2", "#8A6400", "rgba(252,188,0,.16)"),
           "slate": ("#E7E9EE", "#4B5563", "rgba(160,170,190,.16)")}
COVERS = [("linear-gradient(135deg,#FFB47E 0%,#FF7A3D 100%)", "#FFFFFF"),
          ("linear-gradient(135deg,#FFDC8F 0%,#FFAE3D 100%)", "#FFFFFF"),
          ("linear-gradient(135deg,#A6E8D3 0%,#2FB58C 100%)", "#FFFFFF"),
          ("linear-gradient(135deg,#B3D7FF 0%,#4C8DF0 100%)", "#FFFFFF"),
          ("linear-gradient(135deg,#FFB8AE 0%,#EF5B52 100%)", "#FFFFFF"),
          ("linear-gradient(135deg,#D5D9E2 0%,#7C8597 100%)", "#FFFFFF")]

ICONS = {   # 24px stroke icons (lucide-style paths)
    "alert": '<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    "repeat": '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "signal": '<path d="M2 20h.01"/><path d="M7 20v-4"/><path d="M12 20v-8"/><path d="M17 20V8"/><path d="M22 4v16"/>',
    "wallet": '<path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
    "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "down": '<path d="m22 17-8.5-8.5-5 5L2 7"/><path d="M16 17h6v-6"/>',
    "percent": '<path d="M19 5 5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
    "truck": '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62L18.3 9.38a1 1 0 0 0-.78-.38H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
    "star": '<path d="M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/>',
    "sparkle": '<path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"/>',
    "list": '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
    "target": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "chat": '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/><path d="M8 12h.01"/><path d="M12 12h.01"/><path d="M16 12h.01"/>',
    "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "box": '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
}
ICONS.update({
    "bolt": '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "shovel": '<path d="M2 22 7 17"/><path d="m9 15 7-7"/><path d="M14 4l6 6-3 3a4 4 0 0 1-6-6z"/><path d="M5 13l6 6"/>',
})


_BLOB_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-100 -100 200 200"><path fill="#000" d="M44.8,-58.3C57.6,-49.5,'
             '67.3,-35.6,71.4,-20.1C75.5,-4.6,74,12.5,66.8,26.4C59.6,40.3,46.7,51,32.2,58.8C17.7,66.6,1.6,71.5,-15.8,70.6C-33.2,69.7,'
             '-51.9,63,-62.7,49.6C-73.5,36.2,-76.4,16.1,-73.2,-2.3C-70,-20.7,-60.7,-37.4,-47.4,-46.4C-34.1,-55.4,-17.1,-56.7,-0.4,'
             '-56.2C16.2,-55.7,32,-67.1,44.8,-58.3Z"/></svg>')
BLOB = "data:image/svg+xml;utf8," + quote(_BLOB_SVG)

MOTION_JS = r"""
(function () {
  const W = window.parent, D = W.document;
  if (!D) return;
  const fmt = (raw, v) => {
    const dec = (raw.split('.')[1] || '').length;
    let s = Math.abs(v).toFixed(dec);
    if (raw.includes(',')) { const p = s.split('.'); p[0] = Number(p[0]).toLocaleString('en-US'); s = p.join('.'); }
    return (v < 0 ? '-' : '') + s;
  };
  function count(el) {
    el.dataset.sidone = '1';
    const txt = el.textContent, m = txt.match(/-?[0-9][0-9,]*(\.[0-9]+)?/);
    if (!m) return;
    const raw = m[0], target = parseFloat(raw.replace(/,/g, ''));
    if (!isFinite(target)) return;
    const pre = txt.slice(0, m.index), post = txt.slice(m.index + raw.length);
    const delay = parseInt(el.dataset.delay || '0', 10), dur = 1600;
    el.setAttribute('data-v', pre + fmt(raw, 0) + post);
    el.classList.add('si-anim');
    const t0 = W.performance.now() + delay;
    const step = (t) => {
      const k = Math.max(0, Math.min(1, (t - t0) / dur)), e = 1 - Math.pow(1 - k, 4);
      el.setAttribute('data-v', pre + fmt(raw, target * e) + post);
      if (k < 1) W.requestAnimationFrame(step); else el.classList.remove('si-anim');
    };
    W.requestAnimationFrame(step);
  }
  function pill(group, sel) {
    const on = group.querySelector(sel);
    if (!on) return;
    group.setAttribute('data-sipill', '1');
    const gr = group.getBoundingClientRect(), r = on.getBoundingClientRect();
    if (!r.width) return;
    const x = (r.left - gr.left) + 'px', w = r.width + 'px';
    if (group.style.getPropertyValue('--px') !== x) group.style.setProperty('--px', x);
    if (group.style.getPropertyValue('--pw') !== w) group.style.setProperty('--pw', w);
  }
  function tick() {
    D.querySelectorAll('.si-count:not([data-sidone])').forEach(count);
    D.querySelectorAll('[class*="st-key-topbar"] [data-testid="stRadioGroup"], [class*="st-key-topbar"] [role="radiogroup"]').forEach(g => {
      const lab = [...g.querySelectorAll('label')].find(l => { const i = l.querySelector('input'); return i && i.checked; });
      if (lab) { lab.setAttribute('data-sion', '1'); g.querySelectorAll('label[data-sion]').forEach(o => { if (o !== lab) o.removeAttribute('data-sion'); });
                 pill(g, 'label[data-sion]'); }
    });
    D.querySelectorAll('[data-testid="stTabs"] [role="tablist"]').forEach(g => pill(g, '[role="tab"][aria-selected="true"]'));
  }
  tick();
  W.setInterval(tick, 120);
})();
"""


def motion() -> None:
    """Counting numbers and sliding menu/tab pills (a tiny script in a hidden component; harmless if scripts are blocked)."""
    try:
        import streamlit.components.v1 as components
        components.html(f"<script>{MOTION_JS}</script>", height=0)
    except Exception:  # noqa: BLE001 - the page works without motion
        pass


# App icon: a speedometer (gauge arc, ticks and needle) on the orange tile
APP_ICON = ('<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M4.2 17.5a8.5 8.5 0 1 1 15.6 0" stroke-width="2.2"/>'
            '<path d="M7 9.2l.9.9M12 6.4v1.3M17 9.2l-.9.9" stroke-width="1.6" opacity=".85"/>'
            '<g class="ndl" style="transform: rotate(38deg)"><path d="M12 15 L12 8.6" stroke-width="2.2"/></g>'
            '<circle cx="12" cy="15" r="1.9" fill="#fff" stroke="none"/></svg>')


ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "vela"


def asset_bytes(name: str) -> bytes:
    """The VELA AI icon files (embedded in the single-file build, else read from app/assets/vela)."""
    import si_native
    emb = getattr(si_native, "EMBEDDED_ASSETS", None)
    if emb and name in emb:
        return base64.b64decode(emb[name])
    f = ASSET_DIR / name
    return f.read_bytes() if f.exists() else b""


def asset_uri(name: str) -> str:
    mime = {"webp": "image/webp", "png": "image/png"}[name.rsplit(".", 1)[1]]
    return f"data:{mime};base64," + base64.b64encode(asset_bytes(name)).decode()


def ai_icon():
    """The VELA AI icon as an image for chat avatars; None if it cannot be loaded."""
    try:
        import io
        from PIL import Image
        return Image.open(io.BytesIO(asset_bytes("vela_emblem_128.webp")))
    except Exception:  # noqa: BLE001
        return None


def thinking_html(label: str) -> str:
    return (f'<div class="si-thinking"><img src="{asset_uri("vela_emblem_128.webp")}" alt=""><span>VELA AI · {esc(label)}</span>'
            f'<span class="si-typing"><span></span><span></span><span></span></span></div>')


def ai_hero() -> None:
    """VELA AI introduction on an empty chat."""
    html_block(f'<div class="si-aihero"><img src="{asset_uri("vela_emblem_320.webp")}" alt="VELA AI"><div>'
               '<div class="eyebrow">VELA AI · service intelligence analyst</div><h2>How can I help you today?</h2>'
               '<p>Ask in your own words — English or Arabic. I query live Snowflake data (faults, machine hours, OLGA '
               'opportunities, the daily ranking, lost business), explain what it means and show the evidence. '
               'Figures come only from query results; anything I assume is marked as an assumption.</p></div></div>')


def mode() -> str:
    return "dark" if st.session_state.get("si_dark") else "light"


def c(name: str) -> str:
    return MODES[mode()][name]


def _icon_uri(name: str, color: str) -> str:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" '
           f'stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')
    return "data:image/svg+xml;utf8," + quote(svg)


def _pastel_css() -> str:
    dark = mode() == "dark"
    return "\n".join(f'.si-badge.{k} {{ background: {v[2] if dark else v[0]}; color: {v[1] if not dark else "var(--ink)"}; }}'
                     for k, v in PASTELS.items())


def css() -> str:
    m = MODES[mode()]
    root = "; ".join(f"--{k}: {v}" for k, v in m.items())
    stagger = "\n".join(f".si-tiles > :nth-child({i}), .si-panel .it:nth-child({i + 1}) {{ animation-delay: {i * 45}ms; }}"
                        for i in range(1, 13))
    return f"""
<style>
:root {{ {root}; --rail: {RAIL}; }}
@keyframes siUp {{ from {{ opacity: 0; transform: translateY(10px) scale(.985); filter: blur(8px); }} to {{ opacity: 1; transform: none; filter: none; }} }}
@keyframes siReveal {{ from {{ clip-path: inset(100% 0 0 0); }} to {{ clip-path: inset(0 0 0 0); }} }}
@keyframes siFade {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
@keyframes siPop {{ from {{ opacity: 0; transform: scale(.96); }} to {{ opacity: 1; transform: none; }} }}
html, body, .stApp, [data-testid="stAppViewContainer"] {{ background: var(--frame) !important; color: var(--ink) !important;
  font-family: Inter, "Segoe UI", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif; }}
[data-testid="stHeader"] {{ background: transparent !important; height: 0 !important; }}
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] {{ display: none !important; }}
/* no grey-out while Streamlit reruns: the page stays crisp and changes in place */
[data-stale="true"], .stale-element, [data-testid="stElementContainer"][data-stale="true"] {{ opacity: 1 !important; filter: none !important; transition: none !important; }}
.block-container, [data-testid="stMainBlockContainer"] {{ background: var(--canvas); border-radius: 34px; margin: 14px auto;
  padding: 2rem 2.2rem 6rem !important; max-width: none !important; min-height: calc(100vh - 28px); animation: siFade .35s ease both;
  transition: background .3s; }}
.stApp p, .stApp li, .stApp label, .stApp span, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp td, .stApp th,
[data-testid="stMarkdownContainer"] {{ color: var(--ink); }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ color: var(--muted) !important; }}
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label {{ color: var(--muted) !important; font-size: .78rem !important; font-weight: 500; }}
hr {{ border-color: var(--line) !important; }}
::-webkit-scrollbar {{ width: 10px; height: 10px; }} ::-webkit-scrollbar-thumb {{ background: var(--line); border-radius: 10px; }}

/* ---------- top bar: brand chip, pill menu, light/dark switch, avatar ---------- */
section[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"], [data-testid="stSidebarNav"] {{ display: none !important; }}
[class*="st-key-topbar"] {{ position: sticky; top: 0; z-index: 60; background: var(--canvas); padding: 2px 0 14px; margin-bottom: 6px; }}
[class*="st-key-topbar"] [data-testid="stHorizontalBlock"] {{ align-items: center; }}
.si-brandchip {{ display: inline-flex; align-items: center; gap: 10px; background: var(--card); border-radius: 999px; padding: 6px 16px 6px 6px;
  box-shadow: 0 1px 2px var(--shadow); animation: siFade .4s ease both; }}
.si-brandchip img.mantrac {{ height: 24px; border-radius: 5px; display: block; margin-left: 4px; opacity: .95; }}
.si-brandchip .appicon {{ width: 36px; height: 36px; border-radius: 12px; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(145deg, #FFA45C 0%, #F26B1D 55%, #D9520A 100%); box-shadow: 0 4px 12px rgba(242,107,29,.35); flex: none; }}
.si-brandchip .appicon svg {{ width: 24px; height: 24px; }}
.si-brandchip .appicon .ndl {{ transform-origin: 12px 15px; animation: siNeedle 1.4s cubic-bezier(.3,1.4,.4,1) both; }}
.si-brandchip b {{ font-weight: 650; font-size: .95rem; color: var(--ink); white-space: nowrap; }}
[class*="st-key-topbar"] [data-testid="stRadio"] {{ display: flex; justify-content: center; }}
[class*="st-key-topbar"] [data-testid="stRadioGroup"], [class*="st-key-topbar"] [role="radiogroup"] {{ background: var(--card) !important;
  border-radius: 999px; padding: 5px !important; gap: 2px !important; flex-wrap: nowrap !important; width: fit-content; margin: 0 auto;
  box-shadow: 0 1px 2px var(--shadow); }}
[class*="st-key-topbar"] [data-testid="stRadioOption"], [class*="st-key-topbar"] [role="radiogroup"] label {{ border: none !important;
  background: transparent !important; padding: 9px 22px !important; border-radius: 999px !important; margin: 0 !important;
  transition: background .35s cubic-bezier(.2,.7,.2,1), transform .15s; }}
[class*="st-key-topbar"] [data-testid="stRadioOption"]:hover, [class*="st-key-topbar"] [role="radiogroup"] label:hover {{ background: var(--card2) !important; transform: none; }}
[class*="st-key-topbar"] [data-testid="stRadioOption"]:has(input:checked), [class*="st-key-topbar"] [role="radiogroup"] label:has(input:checked) {{
  background: var(--ink) !important; box-shadow: 0 6px 16px var(--shadow); }}
[class*="st-key-topbar"] [data-testid="stRadioOption"] p, [class*="st-key-topbar"] [role="radiogroup"] label p {{ font-size: .86rem; font-weight: 500; white-space: nowrap; }}
[class*="st-key-topbar"] [role="radiogroup"] label > div:first-child:not(:only-child) {{ display: none !important; }}
[class*="st-key-topbar"] [role="radiogroup"] label:has(input:checked) *, [class*="st-key-topbar"] [data-testid="stRadioOption"]:has(input:checked) * {{ color: var(--canvas) !important; }}
[data-testid="stMain"] [role="radiogroup"] label {{ background: var(--card); border: 1px solid var(--line); border-radius: 999px; padding: 7px 15px !important; margin-right: 4px; }}
[data-testid="stMain"] [role="radiogroup"] label:has(input:checked) {{ background: var(--ink); border-color: var(--ink); }}
[data-testid="stMain"] [role="radiogroup"] label:has(input:checked) * {{ color: var(--canvas) !important; }}
[data-testid="stMain"] [role="radiogroup"] label > div:first-child:not(:only-child) {{ display: none !important; }}
[class*="st-key-modes"] {{ max-width: 96px !important; justify-content: flex-start !important; }}
[class*="st-key-modes"] {{ background: var(--card); border-radius: 999px; padding: 4px; width: fit-content !important; gap: 0 !important;
  box-shadow: 0 1px 2px var(--shadow); }}
[class*="st-key-modes"], [class*="st-key-modes"] > [data-testid="stVerticalBlock"] {{ flex-direction: row !important; flex-wrap: nowrap !important; gap: 2px !important; }}
[class*="st-key-modes"] [data-testid="stElementContainer"], [class*="st-key-modes"] .element-container {{ width: auto !important; }}
[class*="st-key-modes"] [data-testid="stColumn"], [class*="st-key-modes"] [data-testid="column"] {{ width: auto !important; flex: 0 0 auto !important; min-width: 0 !important; }}
[class*="st-key-modes"] .stButton button {{ width: 40px !important; height: 40px !important; min-height: 40px !important; border-radius: 50% !important;
  padding: 0 !important; border: none !important; background: transparent !important; color: var(--muted) !important; font-size: 1rem; }}
[class*="st-key-modes"] .stButton button p {{ color: inherit !important; }}
[class*="st-key-modes"] [class*="st-key-mode_{mode()}"] .stButton button {{ background: var(--ink) !important; color: var(--canvas) !important; }}
[class*="st-key-modes"] [class*="st-key-mode_{mode()}"] .stButton button * {{ color: var(--canvas) !important; }}
.si-avatar {{ width: 42px; height: 42px; border-radius: 50%; background: linear-gradient(135deg, #FFB47E, #FF7A2E); color: #fff !important;
  display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: .8rem; position: relative; margin-left: auto;
  box-shadow: 0 4px 12px rgba(255,122,46,.35); }}
.si-avatar::after {{ content: attr(data-tip); position: absolute; right: 0; top: 50px; background: var(--ink); color: var(--canvas);
  font-size: .72rem; font-weight: 500; padding: 6px 11px; border-radius: 9px; white-space: nowrap; opacity: 0; transition: opacity .15s; pointer-events: none; }}
.si-avatar:hover::after {{ opacity: 1; }}
@property --si-s {{ syntax: "<length>"; inherits: false; initial-value: 0px; }}
.si-ink {{ position: fixed; inset: 0; z-index: 9999; pointer-events: none;
  -webkit-mask-image: url("{BLOB}"), linear-gradient(#000, #000); mask-image: url("{BLOB}"), linear-gradient(#000, #000);
  -webkit-mask-repeat: no-repeat; mask-repeat: no-repeat;
  -webkit-mask-size: var(--si-s) var(--si-s), 100% 100%; mask-size: var(--si-s) var(--si-s), 100% 100%;
  -webkit-mask-position: calc(100vw - 170px - var(--si-s) / 2) calc(90px - var(--si-s) / 2), 0 0;
  mask-position: calc(100vw - 170px - var(--si-s) / 2) calc(90px - var(--si-s) / 2), 0 0;
  -webkit-mask-composite: xor; mask-composite: exclude; animation: siInk 1.1s cubic-bezier(.7,0,.2,1) forwards; }}
@keyframes siInk {{ 0% {{ --si-s: 0px; }} 100% {{ --si-s: 420vmax; }} }}

/* ---------- form controls ---------- */
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"],
[data-testid="stSelectbox"] [role="group"], [data-testid="stMultiSelect"] [role="group"], .stApp input, .stApp textarea {{
  background: var(--card) !important; color: var(--ink) !important; border-color: var(--line) !important; border-radius: 14px !important; }}
[data-baseweb="select"] *, [data-testid="stSelectbox"] *, [data-testid="stMultiSelect"] * {{ color: var(--ink); }}
[data-baseweb="select"] > div > div, [data-testid="stSelectbox"] [role="group"] > *, [data-testid="stMultiSelect"] [role="group"] > * {{ background: transparent !important; }}
[data-baseweb="select"] svg, [data-testid="stSelectbox"] svg, [data-testid="stMultiSelect"] svg {{ color: var(--muted) !important; fill: var(--muted) !important; }}
[data-baseweb="popover"] ul, [data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"], [role="listbox"], .react-aria-Popover {{ background: var(--card) !important; }}
[data-baseweb="popover"] li, [data-baseweb="popover"] li *, [role="listbox"] [role="option"], [role="listbox"] [role="option"] * {{ color: var(--ink) !important; }}
[data-baseweb="popover"] li:hover, [role="option"]:hover, [role="option"][data-focused], [aria-selected="true"][role="option"] {{ background: var(--card2) !important; }}
[data-baseweb="tag"], [data-testid="stMultiSelectTag"] {{ background: var(--soft) !important; border-radius: 999px !important; }}
[data-baseweb="tag"] *, [data-testid="stMultiSelectTag"] * {{ color: var(--ink) !important; fill: var(--ink) !important; }}
[data-testid="stMain"] [data-testid="stRadioGroup"], .main [role="radiogroup"] {{ gap: 6px; flex-wrap: wrap; }}
[data-testid="stMain"] [data-testid="stRadioOption"], .main [role="radiogroup"] label {{ background: var(--card); border: 1px solid var(--line);
  border-radius: 999px; padding: 7px 15px !important; margin-right: 4px; transition: background .2s, transform .12s; }}
[data-testid="stMain"] [data-testid="stRadioOption"]:hover, .main [role="radiogroup"] label:hover {{ transform: translateY(-1px); }}
[data-testid="stMain"] [data-testid="stRadioOption"]:has(input:checked), .main [role="radiogroup"] label:has(input:checked) {{ background: var(--ink); border-color: var(--ink); }}
[data-testid="stMain"] [data-testid="stRadioOption"]:has(input:checked) *, .main [role="radiogroup"] label:has(input:checked) * {{ color: var(--canvas) !important; }}
[data-testid="stMain"] [data-testid="stRadioOption"] > div > div:first-child:not([data-testid]), .main [role="radiogroup"] label > div:first-child {{ display: none !important; }}
[data-testid="stToggle"] label, [data-testid="stCheckbox"] label {{ color: var(--ink) !important; }}

/* buttons: primary = pill in ink colour (like "Upload file"), secondary = card pill */
.stButton > button, .stDownloadButton > button {{ border-radius: 999px !important; padding: .5rem 1.15rem !important; font-weight: 600 !important;
  border: 1px solid var(--line) !important; background: var(--card) !important; color: var(--ink) !important; box-shadow: none !important;
  transition: transform .12s, background .2s, border-color .2s; }}
.stButton > button:hover, .stDownloadButton > button:hover {{ transform: translateY(-1px); border-color: var(--accent) !important; }}
.stButton > button:active {{ transform: scale(.97); }}
.stButton > button p, .stDownloadButton > button p {{ color: inherit !important; }}
.stButton > button[kind="primary"], [data-testid="baseButton-primary"], [data-testid="stBaseButton-primary"], .stDownloadButton > button {{
  background: var(--ink) !important; color: var(--canvas) !important; border-color: var(--ink) !important; }}
.stButton > button[kind="primary"] p, [data-testid="stBaseButton-primary"] p, [data-testid="baseButton-primary"] p, .stDownloadButton > button p {{ color: var(--canvas) !important; }}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button:hover {{ background: var(--accent) !important; border-color: var(--accent) !important; }}
.stButton > button:disabled {{ opacity: .45; }}

/* tabs as a segmented pill */
[data-baseweb="tab-list"], [data-testid="stTabs"] [role="tablist"] {{ background: var(--card); border-radius: 999px; padding: 5px; gap: 4px; width: fit-content; border: none !important; }}
[data-baseweb="tab"], [data-testid="stTabs"] [role="tab"] {{ border-radius: 999px !important; padding: 8px 18px !important; height: auto !important;
  background: transparent !important; border: none !important; transition: background .2s; }}
[data-baseweb="tab"] p, [data-testid="stTabs"] [role="tab"] p {{ color: var(--muted) !important; font-weight: 600; }}
[data-baseweb="tab"][aria-selected="true"], [data-testid="stTabs"] [role="tab"][aria-selected="true"] {{ background: var(--ink) !important; }}
[data-baseweb="tab"][aria-selected="true"] p, [data-testid="stTabs"] [role="tab"][aria-selected="true"] p {{ color: var(--canvas) !important; }}
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"], [data-testid="stTabs"] [role="tablist"] > div:not([role="tab"]),
.react-aria-SelectionIndicator, [data-testid="stTab"] > div:not([data-testid]) {{ display: none !important; }}
[data-testid="stTabs"] [role="tablist"]::after {{ display: none !important; }}
[data-baseweb="tab-panel"], [data-testid="stTabs"] [role="tabpanel"] {{ animation: siFade .3s ease both; padding-top: 14px; }}

/* cards: keyed containers, expanders, alerts, dialogs */
[class*="st-key-card"] {{ background: var(--card) !important; border: none !important; border-radius: 26px !important; padding: 20px 22px !important;
  box-shadow: 0 1px 2px var(--shadow); animation: siUp .45s cubic-bezier(.2,.7,.2,1) both; }}
[data-testid="stExpander"] details, [data-testid="stExpander"] {{ background: var(--card) !important; border: none !important; border-radius: 20px !important; }}
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary * {{ color: var(--ink) !important; font-weight: 600; }}
[data-testid="stExpanderDetails"] {{ border: none !important; }}
[data-testid="stAlert"] {{ border-radius: 16px !important; }}
[data-testid="stAlert"] * {{ color: var(--ink) !important; }}
.stApp pre, .stApp code {{ background: var(--card2) !important; color: var(--accentink) !important; border-radius: 10px; }}
.stApp pre code {{ background: transparent !important; }}
[data-testid="stVegaLiteChart"], .stVegaLiteChart {{ background: transparent !important; }}
[data-testid="stElementToolbar"], [data-testid="stElementToolbar"] > div {{ background: var(--card) !important; border-radius: 10px; }}
[data-testid="stElementToolbar"] button, [data-testid="stElementToolbar"] svg {{ color: var(--muted) !important; background: transparent !important; }}
[data-testid="stDialog"] > div, div[role="dialog"], [data-baseweb="modal"] [role="dialog"] {{ background: var(--canvas) !important;
  border-radius: 28px !important; border: 1px solid var(--line) !important; color: var(--ink) !important;
  animation: siPop .25s cubic-bezier(.2,.7,.2,1) both; }}
[data-testid="stDialog"] > div > div:not([role="dialog"]) {{ background: transparent !important; }}
[data-testid="stDialog"] button[aria-label="Close"], [data-testid="stDialog"] button[aria-label="Close"] svg {{ color: var(--ink) !important; fill: var(--ink) !important; }}
div[role="dialog"] * {{ color: var(--ink); }}
[data-testid="stDialog"] {{ backdrop-filter: blur(6px); }}

/* clickable cards/tiles: an invisible button covers the keyed container */
[class*="st-key-hit_"] {{ position: relative; gap: 0 !important; }}
[class*="st-key-hit_"] [data-testid="stElementContainer"]:has(.stButton), [class*="st-key-hit_"] .element-container:has(.stButton) {{
  position: absolute !important; inset: 0; z-index: 6; margin: 0 !important; width: 100% !important; }}
[class*="st-key-hit_"] .stButton, [class*="st-key-hit_"] .stButton > button {{ width: 100% !important; height: 100% !important; opacity: 0 !important;
  cursor: pointer; min-height: 100%; }}
[class*="st-key-hit_"]:hover .si-mcard, [class*="st-key-hit_"]:hover .si-tile {{ transform: translateY(-4px); box-shadow: 0 16px 32px var(--shadow); }}
[class*="st-key-hit_"]:active .si-mcard, [class*="st-key-hit_"]:active .si-tile {{ transform: scale(.985); }}

/* ---------- chat ---------- */
[data-testid="stChatMessage"] {{ background: transparent !important; padding: .35rem 0 !important; gap: .75rem; animation: siUp .35s ease both; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]), [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {{ flex-direction: row-reverse; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"],
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) [data-testid="stChatMessageContent"] {{
  background: var(--ink); border-radius: 22px 22px 6px 22px; padding: .75rem 1.05rem; max-width: 72%; flex: 0 1 auto !important;
  width: fit-content !important; min-width: fit-content; margin-left: auto !important; margin-right: 0 !important; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]), [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {{
  justify-content: flex-start !important; width: 100% !important; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] div,
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) [data-testid="stChatMessageContent"] div {{ flex: 0 1 auto !important; width: auto !important; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] *,
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) [data-testid="stChatMessageContent"] * {{ color: var(--canvas) !important; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) [data-testid="stChatMessageContent"],
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) [data-testid="stChatMessageContent"] {{
  background: var(--card); border-radius: 22px 22px 22px 6px; padding: 1rem 1.25rem; max-width: 900px; margin-left: 0 !important; margin-right: auto !important; }}
[data-testid="stChatMessageContent"] h1, [data-testid="stChatMessageContent"] h2, [data-testid="stChatMessageContent"] h3 {{
  font-size: 1.02rem !important; font-weight: 650 !important; margin: .2rem 0 .4rem !important; padding: 0 !important; }}
[data-testid="stChatMessageContent"] p, [data-testid="stChatMessageContent"] li {{ font-size: .93rem; line-height: 1.6; }}
[data-testid="stChatMessageContent"] table {{ border-collapse: collapse; margin: .5rem 0; font-size: .86rem; }}
[data-testid="stChatMessageContent"] th {{ color: var(--muted) !important; font-weight: 500; border: none !important; border-bottom: 1px solid var(--line) !important;
  padding: 7px 14px; text-align: left; background: transparent !important; }}
[data-testid="stChatMessageContent"] td {{ border: none !important; border-bottom: 1px solid var(--line2) !important; padding: 8px 14px; }}
[data-testid="stChatMessageAvatarAssistant"], [data-testid="chatAvatarIcon-assistant"] {{ background: {YELLOW} !important; border-radius: 12px !important; }}
[data-testid="stChatMessageAvatarUser"], [data-testid="chatAvatarIcon-user"] {{ background: var(--soft) !important; border-radius: 50% !important; }}
[data-testid="stChatMessageAvatarAssistant"] *, [data-testid="chatAvatarIcon-assistant"] * {{ color: #17171A !important; }}
[data-testid="stChatMessageAvatarUser"] *, [data-testid="chatAvatarIcon-user"] * {{ color: var(--accentink) !important; }}
[data-testid="stChatInput"], [data-testid="stChatInput"] > div {{ background: var(--card) !important; border-radius: 999px !important; border-color: var(--line) !important;
  box-shadow: 0 10px 30px var(--shadow); }}
[data-testid="stChatInput"] textarea {{ background: transparent !important; color: var(--ink) !important; }}
[data-testid="stChatInput"] textarea::placeholder {{ color: var(--muted) !important; opacity: 1; }}
/* the message bar sits on a solid strip at the bottom so answers never show through it */
[data-testid="stBottom"], [data-testid="stBottom"] > div, .stChatFloatingInputContainer {{ background: var(--frame) !important; z-index: 50; }}
[data-testid="stBottom"]::before {{ content: ""; position: absolute; left: 0; right: 0; top: -28px; height: 28px; pointer-events: none;
  background: linear-gradient(to bottom, transparent, var(--frame)); }}
[data-testid="stBottomBlockContainer"] {{ max-width: 1100px; margin: 0 auto; background: var(--frame) !important; padding-top: 10px !important; padding-bottom: 18px !important; }}
.block-container, [data-testid="stMainBlockContainer"] {{ padding-bottom: 9rem !important; flex-shrink: 0 !important; height: auto !important; }}
[data-testid="stChatInputSubmitButton"] {{ background: linear-gradient(135deg, #FFB47E, #FF7A2E) !important; border-radius: 999px !important; }}
[data-testid="stChatInputSubmitButton"] svg {{ fill: #fff !important; color: #fff !important; }}
.si-typing {{ display: inline-flex; gap: 4px; padding: 6px 2px; }}
.si-typing span {{ width: 7px; height: 7px; border-radius: 50%; background: var(--accent); animation: siBlink 1s infinite ease-in-out; }}
.si-typing span:nth-child(2) {{ animation-delay: .15s; }} .si-typing span:nth-child(3) {{ animation-delay: .3s; }}
@keyframes siBlink {{ 0%, 80%, 100% {{ opacity: .25; transform: scale(.8); }} 40% {{ opacity: 1; transform: none; }} }}

/* ---------- HTML components ---------- */
.si-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin: 0 0 22px; flex-wrap: wrap; animation: siFade .4s ease both; }}
.si-top h1 {{ font-size: 2.3rem; font-weight: 500; letter-spacing: -.03em; margin: 0; padding: 0; color: var(--ink); line-height: 1.15; }}
.si-top .sub {{ color: var(--muted); font-size: .9rem; margin-top: 4px; }}
.si-top .right {{ display:flex; gap:10px; align-items:center; flex-wrap: wrap; }}
.si-top .logo {{ height: 40px; border-radius: 9px; overflow: hidden; background: #fff; padding: 3px; box-shadow: 0 2px 8px var(--shadow); }}
.si-top .logo img {{ height: 34px; display:block; }}
.si-chip {{ background: var(--card); border-radius: 999px; padding: 8px 14px; font-size: .78rem; color: var(--muted); white-space: nowrap; }}
.si-chip b {{ color: var(--ink); font-weight: 600; }}
.si-chip .dot {{ display:inline-block; width:7px; height:7px; border-radius:50%; background:#22C55E; margin-right:6px; box-shadow: 0 0 0 3px rgba(34,197,94,.18); }}
.si-cover {{ background: var(--card); border-radius: 28px; overflow: hidden; margin-bottom: 18px; animation: siUp .45s cubic-bezier(.2,.7,.2,1) both; }}
.si-cover .band {{ height: 88px; background: linear-gradient(110deg, #FF8A3D 0%, #FFA866 35%, #FFC98F 65%, #FFE2B8 100%); position: relative; overflow: hidden; }}
.si-cover .band::after {{ content: ""; position: absolute; inset: 0; background: radial-gradient(circle at 85% 20%, rgba(255,255,255,.45), transparent 40%),
  radial-gradient(circle at 15% 120%, rgba(255,255,255,.25), transparent 45%); }}
.si-cover .body {{ display:flex; justify-content:space-between; align-items:flex-end; gap:22px; padding: 0 26px 22px; flex-wrap: wrap; }}
.si-cover .who {{ display:flex; gap:16px; align-items:flex-end; }}
.si-cover .badge-ic {{ width: 72px; height: 72px; border-radius: 22px; background: var(--rail); margin-top: -36px; border: 4px solid var(--card);
  display:flex; align-items:center; justify-content:center; flex: none; position: relative; z-index: 1; }}
.si-cover .badge-ic svg {{ width: 30px; height: 30px; stroke: {YELLOW}; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }}
.si-cover .eyebrow {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .1em; color: var(--accent); font-weight: 700; margin-top: 14px; }}
.si-cover h2 {{ font-size: 1.35rem; margin: 2px 0 2px; padding: 0; font-weight: 650; color: var(--ink); }}
.si-cover p {{ margin: 0; color: var(--muted); font-size: .88rem; max-width: 760px; }}
.si-cover .stats {{ display:flex; gap: 28px; }}
.si-cover .stat .v {{ font-size: 1.6rem; font-weight: 700; color: var(--ink); line-height: 1.1; letter-spacing: -.02em; }}
.si-cover .stat .l {{ font-size: .74rem; color: var(--muted); }}
.si-tiles {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 14px; }}
.si-tile {{ background: var(--card); border-radius: 22px; padding: 16px 18px; margin-bottom: 6px; transition: transform .2s cubic-bezier(.2,.7,.2,1), box-shadow .2s;
  position: relative; animation: siUp .45s cubic-bezier(.2,.7,.2,1) both; height: 100%; }}
.si-tile:hover {{ transform: translateY(-3px); box-shadow: 0 14px 28px var(--shadow); }}
.si-tile .ic {{ width: 38px; height: 38px; border-radius: 12px; display:flex; align-items:center; justify-content:center; }}
.si-tile .ic svg {{ width: 18px; height: 18px; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }}
.si-tile .l {{ font-size: .86rem; font-weight: 600; color: var(--ink); margin-top: 12px; line-height: 1.25; }}
.si-tile .v {{ font-size: 1.6rem; font-weight: 700; letter-spacing: -.02em; color: var(--ink); margin-top: 2px; }}
.si-tile .s {{ font-size: .74rem; color: var(--muted); margin-top: 2px; }}
.si-tile .go {{ font-size: .72rem; color: var(--accent); font-weight: 600; margin-top: 8px; opacity: .0; transition: opacity .2s; }}
.si-tile:hover .go, [class*="st-key-hit_"]:hover .si-tile .go {{ opacity: 1; }}
.si-tile .d {{ position:absolute; top:16px; right:16px; font-size: .7rem; font-weight: 600; border-radius: 999px; padding: 3px 8px; }}
.d.good {{ background: rgba(15,159,110,.14); color: #0F9F6E; }} .d.bad {{ background: rgba(229,72,77,.14); color: #E5484D; }}
.d.flat {{ background: var(--card2); color: var(--muted); }}
.si-sec {{ display:flex; justify-content:space-between; align-items:baseline; margin: 2px 0 12px; gap: 12px; }}
.si-sec h3 {{ font-size: 1.15rem; font-weight: 600; margin: 0; padding: 0; color: var(--ink); }}
.si-sec .sub {{ color: var(--muted); font-size: .78rem; }}
.si-gauge {{ display:flex; align-items:center; gap: 22px; flex-wrap: wrap; }}
.si-gauge svg {{ width: 170px; height: 170px; flex: none; }}
.si-gauge svg .arc {{ animation: siArc 1.1s cubic-bezier(.2,.7,.2,1) both; }}
@keyframes siArc {{ from {{ stroke-dasharray: 0 999; }} }}
.si-gauge .legend {{ flex: 1; min-width: 180px; }}
.si-gauge .row {{ display:flex; justify-content:space-between; gap: 10px; padding: 7px 0; border-bottom: 1px solid var(--line2); font-size: .82rem; }}
.si-gauge .row:last-child {{ border-bottom: none; }}
.si-gauge .dot {{ display:inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; }}
.si-gauge .row b {{ color: var(--ink); }}
.si-table-wrap {{ background: var(--card); border-radius: 18px; overflow: auto; animation: siFade .4s ease both; }}
table.si-table {{ border-collapse: separate; border-spacing: 0; width: 100%; font-size: .83rem; }}
table.si-table th {{ position: sticky; top: 0; background: var(--card); color: var(--muted); font-weight: 500; text-align: left;
  padding: 10px 12px; border-bottom: 1px solid var(--line); white-space: nowrap; font-size: .75rem; z-index: 1; }}
table.si-table th, table.si-table td {{ border-left: none !important; border-right: none !important; border-top: none !important; }}
table.si-table td {{ padding: 11px 12px; border-bottom: 1px solid var(--line2); color: var(--ink); vertical-align: middle; }}
table.si-table tr {{ transition: background .15s; }}
table.si-table tr:hover td {{ background: var(--card2); }}
table.si-table td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
table.si-table td.wide {{ min-width: 300px; color: var(--muted); font-size: .8rem; }}
.si-avt {{ width: 30px; height: 30px; border-radius: 50%; display:inline-flex; align-items:center; justify-content:center; font-size: .7rem; font-weight: 700; }}
.si-bar {{ display:flex; align-items:center; gap:8px; min-width: 120px; }}
.si-bar .track {{ flex:1; height: 6px; border-radius: 99px; background: var(--soft); overflow: hidden; }}
.si-bar .fill {{ height: 100%; border-radius: 99px; background: linear-gradient(90deg, var(--accent2), var(--accent)); }}
.si-bar span {{ font-weight: 700; font-size: .8rem; width: 34px; text-align: right; }}
.si-badge {{ display:inline-block; padding: 3px 9px; border-radius: 999px; font-size: .72rem; font-weight: 600; white-space: nowrap; }}
.si-badge.dark {{ background: var(--ink); color: var(--canvas); }} .si-badge.gry {{ background: var(--card2); color: var(--muted); }}
.si-badge.red {{ background: rgba(229,72,77,.14); color: #E5484D; }} .si-badge.grn {{ background: rgba(15,159,110,.14); color: #0F9F6E; }}
.si-badge.yel {{ background: rgba(252,188,0,.18); color: {"#8A6400" if mode() == "light" else "#FFD666"}; }}
{_pastel_css()}
[data-testid="stElementContainer"]:has(> iframe[height="0"]), [data-testid="stElementContainer"]:has(iframe[height="0"]), .element-container:has(iframe[height="0"]) {{ display: none !important; }}
.si-mcard {{ background: var(--card); border-radius: 24px; padding: 8px; transition: transform .22s cubic-bezier(.2,.7,.2,1), box-shadow .22s;
  animation: siUp .5s cubic-bezier(.2,.7,.2,1) both; }}
.si-mcard .cover {{ height: 118px; border-radius: 18px; position: relative; display:flex; align-items:center; justify-content:center; overflow: hidden; }}
.si-mcard .cover::after {{ content: ""; position: absolute; inset: 0; background: radial-gradient(circle at 80% 10%, rgba(255,255,255,.4), transparent 45%); }}
.si-mcard .cover .tag {{ position:absolute; top:10px; left:10px; background: rgba(11,11,13,.85); color:#fff; font-size: .68rem; font-weight: 600;
  padding: 4px 10px; border-radius: 999px; z-index: 1; backdrop-filter: blur(4px); }}
.si-mcard .cover .rank {{ position:absolute; top:10px; right:10px; background: rgba(255,255,255,.9); color: #17171A; font-size: .72rem;
  font-weight: 700; padding: 4px 10px; border-radius: 999px; z-index: 1; }}
.si-mcard .cover .ic {{ width: 56px; height: 56px; border-radius: 18px; background: rgba(255,255,255,.25); display:flex; align-items:center;
  justify-content:center; backdrop-filter: blur(6px); border: 1px solid rgba(255,255,255,.4); z-index: 1; transition: transform .3s; }}
.si-mcard:hover .cover .ic, [class*="st-key-hit_"]:hover .si-mcard .cover .ic {{ transform: scale(1.08) rotate(-4deg); }}
.si-mcard .cover .ic svg {{ width: 26px; height: 26px; fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }}
.si-mcard .body {{ padding: 12px 10px 8px; }}
.si-mcard .t {{ font-weight: 650; font-size: .93rem; color: var(--ink); line-height: 1.3; }}
.si-mcard .why {{ font-size: .76rem; color: var(--muted); margin-top: 4px; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden; min-height: 2.8em; }}
.si-mcard .chips {{ display:flex; gap:6px; flex-wrap: wrap; margin-top: 8px; min-height: 22px; }}
.si-mcard .foot {{ display:flex; justify-content:space-between; align-items:center; margin-top: 10px; }}
.si-mcard .foot .m {{ font-size: .72rem; color: var(--muted); line-height: 1.3; }} .si-mcard .foot .m b {{ color: var(--ink); font-size: .95rem; }}
.si-mcard .score {{ width: 46px; height: 46px; border-radius: 50%; display:flex; align-items:center; justify-content:center; font-weight: 700;
  font-size: .82rem; color: var(--ink); background: conic-gradient(var(--ring) calc(var(--p) * 1%), var(--card2) 0); position: relative; }}
.si-mcard .score::before {{ content: ""; position:absolute; inset: 5px; background: var(--card); border-radius: 50%; }}
.si-mcard .score span {{ position: relative; }}
.si-panel {{ background: var(--card); border-radius: 24px; padding: 18px 20px; animation: siUp .5s cubic-bezier(.2,.7,.2,1) both; }}
.si-panel h4 {{ margin: 0 0 10px; padding: 0; font-size: 1.1rem; font-weight: 600; color: var(--ink); }}
.si-panel .it {{ display:flex; align-items:center; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--line2); animation: siUp .4s ease both; }}
.si-panel .it:last-child {{ border-bottom: none; }}
.si-panel .it .sq {{ width: 34px; height: 34px; border-radius: 10px; flex: none; display:flex; align-items:center; justify-content:center; }}
.si-panel .it .sq svg {{ width: 16px; height: 16px; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }}
.si-panel .it .n {{ font-size: .84rem; font-weight: 600; color: var(--ink); }} .si-panel .it .s {{ font-size: .72rem; color: var(--muted); }}
.si-panel .it .r {{ margin-left: auto; font-size: .82rem; font-weight: 700; color: var(--ink); }}
.si-points {{ display:grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin: 10px 0 4px; }}
.si-points > div {{ background: var(--card); border-radius: 16px; padding: 12px 14px; }}
.si-points .k {{ font-size: .72rem; color: var(--muted); }} .si-points .v {{ font-size: 1.25rem; font-weight: 700; color: var(--ink); }}
.si-points .bar {{ height: 5px; border-radius: 9px; background: var(--card2); margin-top: 6px; overflow: hidden; }}
.si-points .bar i {{ display:block; height: 100%; border-radius: 9px; background: linear-gradient(90deg, var(--accent2), var(--accent)); }}
.si-welcome {{ text-align:center; padding: 2vh 0 1vh; animation: siFade .5s ease both; }}
.si-welcome h2 {{ font-size: 1.9rem; font-weight: 500; margin: 0; padding: 0; color: var(--ink); letter-spacing: -.02em; }}
.si-welcome p {{ color: var(--muted); margin-top: 6px; }}
.si-kv {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
.si-kv > div {{ background: var(--card2); border-radius: 14px; padding: 10px 12px; }}
.si-kv .k {{ font-size: .72rem; color: var(--muted); }} .si-kv .v {{ font-weight: 650; color: var(--ink); }}
.si-rec {{ background: var(--card); border-radius: 24px; padding: 18px 20px; margin-bottom: 12px; animation: siUp .45s ease both; }}
.si-rec h4 {{ margin: 8px 0 6px; padding: 0; font-size: 1.02rem; color: var(--ink); }}
.si-rec p, .si-rec li {{ margin: 4px 0; color: var(--muted); font-size: .86rem; }} .si-rec b {{ color: var(--ink); }}
.si-roll {{ display: inline-block; }}
.si-count.si-anim {{ position: relative; -webkit-text-fill-color: transparent; }}
.si-count.si-anim::after {{ content: attr(data-v); position: absolute; left: 0; top: 0; white-space: nowrap; -webkit-text-fill-color: currentColor; }}
[data-sipill] {{ position: relative !important; }}
[data-sipill]::before {{ content: ""; position: absolute; top: 5px; bottom: 5px; left: var(--px, 5px); width: var(--pw, 0px); border-radius: 999px;
  background: var(--ink); box-shadow: 0 6px 16px var(--shadow); transition: left .5s cubic-bezier(.65,0,.25,1.15), width .5s cubic-bezier(.65,0,.25,1.15);
  z-index: 0; }}
[data-sipill] > * , [data-sipill] label, [data-sipill] [role="tab"] {{ position: relative; z-index: 1; }}
[data-sipill] [data-testid="stRadioOption"]:has(input:checked), [data-sipill] label:has(input:checked),
[data-sipill] [role="tab"][aria-selected="true"] {{ background: transparent !important; box-shadow: none !important; }}
[data-testid="stVegaLiteChart"], .stVegaLiteChart {{ animation: siReveal 1.1s cubic-bezier(.2,.7,.2,1) both; }}
@keyframes siRoll {{ from {{ transform: translateY(70%); opacity: 0; }} to {{ transform: none; opacity: 1; }} }}
.si-hero {{ display: grid; grid-template-columns: 1.15fr 1fr 1fr 1fr; gap: 18px; align-items: end; margin: 6px 0 18px; }}
.si-big {{ animation: siFade .5s ease both; }}
.si-big .l {{ font-size: .8rem; color: var(--muted); margin-bottom: 4px; }}
.si-big .v {{ font-size: 2.6rem; font-weight: 500; letter-spacing: -.03em; color: var(--ink); line-height: 1; display: flex; align-items: center; gap: 10px; }}
.si-big .p {{ font-size: .7rem; font-weight: 700; border-radius: 999px; padding: 3px 8px; color: #fff; letter-spacing: 0; }}
.si-big .p.good {{ background: #22C55E; }} .si-big .p.bad {{ background: #EF4444; }} .si-big .p.flat {{ background: var(--muted); }}
.si-big .s {{ font-size: .72rem; color: var(--muted); margin-top: 6px; }}
[class*="st-key-askbox"] input {{ font-size: 2.1rem !important; font-weight: 500 !important; letter-spacing: -.02em; background: transparent !important;
  border: none !important; padding: 6px 0 !important; color: var(--ink) !important; }}
[class*="st-key-askbox"] input::placeholder {{ color: var(--ink) !important; opacity: .9; }}
[class*="st-key-askbox"] [data-testid="stTextInputRootElement"], [class*="st-key-askbox"] [data-testid="stTextInput"] div {{ background: transparent !important;
  border-top: none !important; border-left: none !important; border-right: none !important; box-shadow: none !important; outline: none !important; }}
[class*="st-key-askbox"] [data-testid="stTextInputRootElement"] {{ border-bottom: 1px solid var(--line) !important; border-radius: 0 !important; }}
[class*="st-key-askbox"] [data-baseweb="input"], [class*="st-key-askbox"] [data-baseweb="base-input"], [class*="st-key-askbox"] > div > div {{
  background: transparent !important; border: none !important; border-bottom: 1px solid var(--line) !important; border-radius: 0 !important; }}
.si-c {{ background: var(--card); border-radius: 26px; padding: 20px 22px; height: 100%; min-height: 300px; margin-top: 10px; position: relative; overflow: hidden;
  animation: siUp .55s cubic-bezier(.2,.7,.2,1) both; transition: transform .2s, box-shadow .2s; }}
.si-c h4 {{ margin: 0; padding: 0; font-size: 1.2rem; font-weight: 500; color: var(--ink); line-height: 1.25; max-width: 75%; }}
.si-c .plus {{ position: absolute; top: 18px; right: 18px; width: 36px; height: 36px; border-radius: 50%; background: var(--card2);
  display: flex; align-items: center; justify-content: center; color: var(--muted); font-size: 1.1rem; }}
.si-c .plus svg {{ width: 16px; height: 16px; stroke: var(--muted); fill: none; stroke-width: 2; }}
[class*="st-key-hit_"]:hover .si-c {{ transform: translateY(-4px); box-shadow: 0 16px 32px var(--shadow); }}
.si-speedo {{ display: grid; grid-template-columns: 1fr 1.15fr; align-items: end; gap: 10px; margin-top: 18px; }}
.si-speedo .num {{ font-size: 3rem; font-weight: 500; letter-spacing: -.03em; color: var(--ink); display: flex; align-items: center; gap: 10px; line-height: 1; }}
.si-speedo .tag {{ display: inline-block; margin-top: 8px; background: #22C55E; color: #fff; font-size: .7rem; font-weight: 700; padding: 3px 9px; border-radius: 999px; letter-spacing: 0; }}
.si-speedo .tag.warn {{ background: var(--accent); }}
.si-speedo .lab {{ font-size: .76rem; color: var(--muted); margin-bottom: 6px; }} .si-speedo .unit {{ font-size: .74rem; color: var(--muted); margin-top: 6px; }}
.si-speedo svg .needle {{ transform-origin: 100px 100px; animation: siNeedle 1.3s cubic-bezier(.3,1.4,.4,1) both; }}
.si-speedo svg .fill {{ animation: siArc 1.2s cubic-bezier(.2,.7,.2,1) both; }}
@keyframes siNeedle {{ from {{ transform: rotate(-90deg); }} }}
.si-chips2 {{ display: flex; gap: 10px; margin-top: 18px; flex-wrap: wrap; }}
.si-chips2 > div {{ border: 1px solid var(--line); border-radius: 999px; padding: 8px 14px; font-size: .78rem; color: var(--muted); display: flex; gap: 8px; align-items: center; }}
.si-chips2 b {{ color: var(--ink); font-size: .9rem; }}
.si-orange {{ background: linear-gradient(160deg, #FF8A3D 0%, #F26B1D 55%, #E85A0C 100%) !important; color: #fff; }}
.si-orange h4, .si-orange * {{ color: #fff !important; }}
.si-orange .plus {{ background: rgba(255,255,255,.18); }} .si-orange .plus svg {{ stroke: #fff; }}
.si-orange .pill {{ display: inline-flex; align-items: center; gap: 8px; background: #fff; color: #17171A !important; border-radius: 999px;
  padding: 9px 16px; font-size: .8rem; font-weight: 600; margin-top: 18px; box-shadow: 0 6px 16px rgba(0,0,0,.12); }}
.si-orange .pill * {{ color: #17171A !important; }}
.si-orange .huge {{ position: absolute; left: 22px; bottom: 18px; font-size: 3.6rem; font-weight: 300; letter-spacing: -.04em; line-height: 1; display: flex; align-items: flex-end; gap: 10px; }}
.si-orange .huge small {{ font-size: .72rem; font-weight: 500; opacity: .85; letter-spacing: 0; line-height: 1.3; margin-bottom: 8px; }}
.si-orange::after {{ content: ""; position: absolute; right: -40px; top: -40px; width: 180px; height: 180px; border-radius: 50%; background: rgba(255,255,255,.12); }}
.si-bubble {{ position: absolute; left: 22px; right: 22px; bottom: 20px; background: var(--card2); border-radius: 18px; padding: 14px 14px 12px;
  border-top: 6px solid var(--ink); animation: siUp .6s .2s cubic-bezier(.2,.7,.2,1) both; }}
.si-bubble .t {{ font-size: .7rem; color: var(--muted); background: var(--card); border-radius: 999px; padding: 3px 9px; display: inline-block; }}
.si-bubble .h {{ font-size: .86rem; font-weight: 600; color: var(--ink); margin-top: 8px; line-height: 1.3; }}
.si-bubble .f {{ font-size: .72rem; color: var(--muted); margin-top: 6px; }} .si-bubble .f i {{ display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); margin-right: 6px; }}
.si-map {{ background-color: var(--card); background-image:
  linear-gradient(115deg, transparent 47%, var(--line) 47.5%, var(--line) 49%, transparent 49.5%),
  linear-gradient(25deg, transparent 58%, var(--line) 58.5%, var(--line) 60%, transparent 60.5%),
  linear-gradient(170deg, transparent 30%, var(--line) 30.5%, var(--line) 32%, transparent 32.5%),
  linear-gradient(75deg, transparent 70%, var(--line) 70.5%, var(--line) 71.5%, transparent 72%); }}
.si-map .pin {{ position: absolute; left: 50%; top: 42%; transform: translate(-50%,-50%); width: 46px; height: 46px; border-radius: 50%; background: var(--card);
  display: flex; align-items: center; justify-content: center; box-shadow: 0 6px 18px var(--shadow); animation: siPop .5s .3s both; }}
.si-map .pin svg {{ width: 22px; height: 22px; stroke: var(--accent); fill: none; stroke-width: 2; }}
.si-map .foot {{ position: absolute; left: 16px; right: 16px; bottom: 16px; background: var(--card); border-radius: 999px; padding: 6px 6px 6px 6px;
  display: flex; align-items: center; gap: 12px; box-shadow: 0 6px 18px var(--shadow); }}
.si-map .foot .dot {{ width: 44px; height: 44px; border-radius: 50%; background: linear-gradient(135deg, #FFB47E, #E85A0C); flex: none; }}
.si-map .foot .n {{ font-size: 1.5rem; font-weight: 500; color: var(--ink); }} .si-map .foot .s {{ font-size: .7rem; color: var(--muted); line-height: 1.2; }}
.si-map .foot .go {{ margin-left: auto; width: 40px; height: 40px; border-radius: 50%; background: var(--ink); display: flex; align-items: center; justify-content: center; }}
.si-map .foot .go svg {{ width: 16px; height: 16px; stroke: var(--canvas); fill: none; stroke-width: 2; }}
.si-week {{ display: grid; grid-template-columns: repeat(8, 1fr); gap: 8px; margin-top: 16px; }}
.si-week .d {{ text-align: center; font-size: .74rem; color: var(--muted); padding: 6px 0; border-radius: 999px; }}
.si-week .d.today {{ background: var(--ink); color: var(--canvas); font-weight: 600; }}
.si-week .b {{ margin: 12px auto 0; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: .78rem; font-weight: 700;
  color: #fff; animation: siPop .5s cubic-bezier(.2,.7,.2,1) both; }}
.si-week .b.z {{ border: 1.5px dashed var(--line); color: var(--muted); background: transparent; }}
.si-next {{ background: #0E0E10; color: #fff; border-radius: 22px; padding: 16px 18px; margin-top: 16px; box-shadow: 0 16px 34px rgba(0,0,0,.25);
  animation: siUp .6s .25s cubic-bezier(.2,.7,.2,1) both; }}
.si-next * {{ color: #fff !important; }}
.si-next .h {{ font-size: 1.05rem; font-weight: 500; }} .si-next .s {{ font-size: .76rem; opacity: .7 !important; margin-top: 4px; }}
.si-next .tags {{ display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }}
.si-next .tags span {{ background: #FF7A2E; border-radius: 999px; padding: 5px 11px; font-size: .72rem; font-weight: 600; }}
.si-next .tags span.g {{ background: #2A2A30; }}
.si-stat3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 16px; }}
.si-stat3 > div {{ background: var(--card); border-radius: 22px; padding: 18px 22px; animation: siUp .5s cubic-bezier(.2,.7,.2,1) both; }}
.si-stat3 > div:nth-child(2) {{ animation-delay: .06s; }} .si-stat3 > div:nth-child(3) {{ animation-delay: .12s; }}
.si-stat3 .l {{ font-size: .76rem; color: var(--muted); }} .si-stat3 .v {{ font-size: 2.3rem; font-weight: 500; letter-spacing: -.03em; color: var(--ink); margin-top: 8px; }}
.si-stat3 > div.hl {{ background: linear-gradient(160deg, #FF8A3D 0%, #F26B1D 60%, #E85A0C 100%); }}
.si-stat3 > div.hl .l, .si-stat3 > div.hl .v {{ color: #fff !important; }}
.si-listhead, .si-lrow {{ display: grid; grid-template-columns: 2.2fr 1.4fr 1fr 1fr 1.3fr .9fr; gap: 10px; align-items: center; padding: 0 18px; }}
.si-listhead {{ font-size: .72rem; color: var(--muted); padding-bottom: 10px; padding-top: 16px; background: var(--card); border-radius: 22px 22px 0 0;
  border-bottom: 1px solid var(--line); }}
.si-lrow {{ background: var(--card); padding-top: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--line2); font-size: .82rem;
  color: var(--ink); transition: background .15s; animation: siFade .4s ease both; }}
.si-lrow .t {{ font-weight: 600; }} .si-lrow .sub {{ font-size: .7rem; color: var(--muted); margin-top: 2px; }}
.si-lrow .act {{ justify-self: end; background: var(--ink); color: var(--canvas); border-radius: 999px; padding: 5px 12px; font-size: .72rem; font-weight: 600; }}
[class*="st-key-hit_row"]:hover .si-lrow {{ background: var(--card2); }}
[class*="st-key-hit_row"]:first-of-type .si-lrow {{ border-radius: 0; }}
.si-listwrap {{ background: var(--card); border-radius: 24px; padding: 14px 0 6px; overflow: hidden; }}
@media (max-width: 1100px) {{ .si-hero {{ grid-template-columns: 1fr 1fr; }} }}
.si-aihero {{ display: flex; gap: 26px; align-items: center; background: var(--card); border-radius: 28px; padding: 22px 28px;
  margin: 6px 0 18px; animation: siUp .5s cubic-bezier(.2,.7,.2,1) both; }}
.si-aihero img {{ width: 140px; height: 140px; object-fit: contain; flex: none; }}
.si-aihero .eyebrow {{ font-size: .7rem; letter-spacing: .12em; text-transform: uppercase; color: var(--accent); font-weight: 700; }}
.si-aihero h2 {{ font-size: 1.8rem; font-weight: 500; margin: 6px 0; padding: 0; color: var(--ink); }}
.si-aihero p {{ color: var(--muted); max-width: 760px; margin: 0; font-size: .92rem; line-height: 1.55; }}
.si-thinking {{ display: flex; align-items: center; gap: 12px; color: var(--muted); font-size: .86rem; }}
.si-thinking img {{ width: 28px; height: 28px; animation: siPulse 1.4s ease-in-out infinite; }}
@keyframes siPulse {{ 0%, 100% {{ transform: scale(.94); }} 50% {{ transform: scale(1.06); }} }}
.si-sources {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 10px; font-size: .72rem; color: var(--muted); }}
.si-sources .src {{ border: 1px solid var(--line); border-radius: 999px; padding: 3px 9px; color: var(--ink); background: var(--card2); }}
.si-sources .ok {{ color: #0F9F6E; font-weight: 600; }} .si-sources .warn {{ color: #D97706; font-weight: 600; }}
[data-testid="stChatMessageContent"] blockquote {{ border-left: 3px dashed var(--accent) !important; margin: .5rem 0 !important; padding: .4rem .8rem !important;
  background: var(--card2); border-radius: 0 12px 12px 0; }}
[data-testid="stChatMessageContent"] blockquote * {{ color: var(--muted) !important; }}
[data-testid="stChatMessageAvatarCustom"] {{ width: 38px !important; height: 38px !important; background: transparent !important; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarCustom"]) [data-testid="stChatMessageContent"],
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarImage"]) [data-testid="stChatMessageContent"] {{
  background: var(--card); border-radius: 22px 22px 22px 6px; padding: 1rem 1.25rem; max-width: 900px; margin-left: 0 !important; margin-right: auto !important; }}
{stagger}
@media (max-width: 900px) {{ .block-container, [data-testid="stMainBlockContainer"] {{ margin: 8px; padding: 1.2rem 1rem 6rem !important; }} }}
</style>
"""


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def icon(name: str, color: str | None = None) -> str:
    style = f' style="stroke:{color}"' if color else ""
    return f'<svg viewBox="0 0 24 24"{style}>{ICONS.get(name, ICONS["activity"])}</svg>'


def _logo_svg() -> str:
    import si_native
    svg = getattr(si_native, "EMBEDDED_LOGO", None)
    if svg is None and LOGO_FILE.exists():
        svg = LOGO_FILE.read_text(encoding="utf-8")
    return svg or ""


def _uri(svg: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def logo_uri() -> str:
    svg = _logo_svg()
    return _uri(svg) if svg else ""


def mark_uri() -> str:
    """The emblem from the logo (left square), for the top of the rail."""
    svg = _logo_svg()
    return _uri(svg.replace('viewBox="0 0 963.04 262.04"', 'viewBox="122 70 124 124"')) if svg else ""


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def apply() -> None:
    html_block(css())


def _initials(user: str) -> str:
    return "".join(p[0] for p in user.split("@")[0].replace(".", " ").split()[:2]).upper() or "?"


def nav(pages: list[str], user: str, role: str, key: str = "si_page", index: int = 0) -> str:
    """Top bar like the reference design: brand chip, pill menu, light/dark switch and avatar. Returns the chosen page."""
    with st.container(key="topbar") if _keyed() else st.container():
        left, mid, right = st.columns([1.25, 3.2, 1.25])
        with left:
            logo = logo_uri()
            img = f'<img class="mantrac" src="{logo}" alt="Mantrac CAT">' if logo else ""
            html_block(f'<div class="si-brandchip"><span class="appicon">{APP_ICON}</span><b>Service Intelligence</b>{img}</div>')
        with mid:
            page = st.radio("Menu", pages, index=index, key=key, horizontal=True, label_visibility="collapsed")
        with right:
            r1, r2 = st.columns([1, 1])
            with r1:
                with st.container(key="modes") if _keyed() else st.container():
                    if st.button("☀", key="mode_light", help="Light mode") and mode() != "light":
                        _switch_mode(False)
                    if st.button("☾", key="mode_dark", help="Dark mode") and mode() != "dark":
                        _switch_mode(True)
            with r2:
                html_block(f'<div class="si-avatar" data-tip="{esc(user)} · {esc(role)}">{esc(_initials(user))}</div>')
    if st.session_state.pop("_si_ink", False):          # the new mode spreads out from the switch over the old one
        old = MODES["light" if mode() == "dark" else "dark"]["canvas"]
        html_block(f'<div class="si-ink" style="background:{old}"></div>')
    return page


def _keyed() -> bool:
    import inspect
    return "key" in inspect.signature(st.container).parameters


def _switch_mode(dark: bool) -> None:
    st.session_state["si_dark"] = dark
    st.session_state["_si_ink"] = True
    st.rerun()


def user_card() -> None:
    """Kept for page compatibility: the user now sits in the top bar."""


def topbar(title: str, subtitle: str, chips: list[str] | None = None) -> None:
    """Page title (left) with status chips (right). Chips may contain <b>, nothing else."""
    chip_html = "".join(f'<span class="si-chip">{c}</span>' for c in (chips or []))
    html_block(f'<div class="si-top"><div><h1>{roll(title)}</h1><div class="sub">{esc(subtitle)}</div></div>'
               f'<div class="right">{chip_html}</div></div>')


def roll(text, delay_ms: int = 0) -> str:
    """Text that counts up from 0 when it holds a number (done by the motion script; static without it)."""
    return f'<span class="si-roll si-count" data-delay="{int(delay_ms)}">{esc(text)}</span>'


def cover(eyebrow: str, title: str, text: str, stats: list[tuple[str, str]] = (), icon_name: str = "chart") -> None:
    """Card with the orange cover band, an icon badge, a title and headline stats."""
    s = "".join(f'<div class="stat"><div class="v">{esc(v)}</div><div class="l">{esc(lbl)}</div></div>' for v, lbl in stats)
    html_block(f'<div class="si-cover"><div class="band"></div><div class="body"><div class="who"><div class="badge-ic">{icon(icon_name)}</div>'
               f'<div><div class="eyebrow">{esc(eyebrow)}</div><h2>{esc(title)}</h2><p>{esc(text)}</p></div></div>'
               f'<div class="stats">{s}</div></div></div>')


def _pastel(color: str) -> tuple[str, str]:
    bg, fg, dbg = PASTELS.get(color, PASTELS["orange"])
    return (dbg, "#F3F3F5") if mode() == "dark" else (bg, fg)


def tile(label: str, value: str, sub: str = "", icon_name: str = "activity", color: str = "orange",
         delta: str | None = None, tone: str = "flat", tip: str | None = None, go: str | None = None) -> str:
    bg, fg = _pastel(color)
    d = f'<div class="d {tone}">{esc(delta)}</div>' if delta else ""
    title = f' title="{esc(tip)}"' if tip else ""
    g = f'<div class="go">{esc(go)} →</div>' if go else ""
    return (f'<div class="si-tile"{title}>{d}<div class="ic" style="background:{bg}">{icon(icon_name, fg)}</div>'
            f'<div class="l">{esc(label)}</div><div class="v">{esc(value)}</div><div class="s">{esc(sub)}</div>{g}</div>')


def tiles(cards: list[str]) -> None:
    html_block(f'<div class="si-tiles">{"".join(cards)}</div>')


def section(title: str, sub: str = "") -> None:
    html_block(f'<div class="si-sec"><h3>{esc(title)}</h3><span class="sub">{esc(sub)}</span></div>')


def gauge(ratio: float | None, center: str, caption: str, legend: list[tuple[str, str, str]]) -> None:
    """Three-quarter arc gauge (the 'Storage' card). legend = (color, label, value)."""
    r, cx, cy, sweep = 62, 85, 85, 270
    circ = 2 * math.pi * r
    track = circ * sweep / 360
    p = max(0.0, min(1.0, ratio or 0.0))
    arc = (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{c("card2")}" stroke-width="12" stroke-linecap="round" '
           f'stroke-dasharray="{track:.1f} {circ:.1f}" transform="rotate(135 {cx} {cy})"/>'
           f'<defs><linearGradient id="sig" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FFB47E"/>'
           f'<stop offset="1" stop-color="{c("accent")}"/></linearGradient></defs>'
           f'<circle class="arc" cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="url(#sig)" stroke-width="12" stroke-linecap="round" '
           f'stroke-dasharray="{track * p:.1f} {circ:.1f}" transform="rotate(135 {cx} {cy})"/>')
    txt = (f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" font-size="28" font-weight="500" fill="{c("ink")}" '
           f'font-family="Inter, Segoe UI, sans-serif">{esc(center)}</text>'
           f'<text x="{cx}" y="{cy + 22}" text-anchor="middle" font-size="9" fill="{c("muted")}" '
           f'font-family="Inter, Segoe UI, sans-serif">{esc(caption)}</text>')
    rows = "".join(f'<div class="row"><span><span class="dot" style="background:{col}"></span>{esc(lbl)}</span><b>{esc(v)}</b></div>'
                   for col, lbl, v in legend)
    html_block(f'<div class="si-gauge"><svg viewBox="0 0 170 170">{arc}{txt}</svg><div class="legend">{rows}</div></div>')


def _family_style(family: str | None) -> tuple[str, str, str]:
    f = (family or "").upper()
    ic = ("truck" if "TRUCK" in f or "TRACTOR" in f or "DOZER" in f or "GRADER" in f or "SCRAPER" in f else
          "bolt" if "ENGINE" in f or "GENSET" in f or "C1" in f or "C9" in f or "3" in f[:1] else
          "shovel" if "EXCAVATOR" in f or "SHOVEL" in f or "BACKHOE" in f else
          "box" if "LOADER" in f or "HANDLER" in f else "gear")
    grad, fg = COVERS[zlib.crc32(f.encode()) % len(COVERS)]
    return grad, fg, ic


def machine_card(r: dict, fmt_money, delay_ms: int = 0) -> str:
    """'Saved for later'-style card for one ranked machine: colourful cover by product family, tag, rank, score ring."""
    grad, fg, ic = _family_style(r.get("product_family"))
    rank = r.get("overall_rank") or r.get("class_rank")
    score = float(r.get("score") or 0)
    ring = c("accent") if score >= 90 else "#F59E0B" if score >= 75 else "#A1A1AA"
    due = (f"<b>{esc(fmt_money(r.get('due_value')))}</b><br>OLGA value due" if r.get("due_value") else
           f"<b>{esc(r.get('max_fault_days') or 0)} days</b><br>with a HIGH fault")
    chips = []
    if r.get("is_new_today"):
        chips.append(badge("New today", "grn"))
    if r.get("days_to_due") is not None and float(r["days_to_due"]) < 0:
        chips.append(badge(f"Overdue {abs(int(float(r['days_to_due'])))}d", "red"))
    elif r.get("days_to_due") is not None:
        chips.append(badge(f"Due in {int(float(r['days_to_due']))}d", "yel"))
    if r.get("repeat_fault"):
        chips.append(badge("Repeat fault", "orange"))
    return (f'<div class="si-mcard" style="animation-delay:{delay_ms}ms"><div class="cover" style="background:{grad}">'
            f'<span class="tag">{esc(r.get("signal_class"))}</span><span class="rank">#{esc(rank)}</span>'
            f'<div class="ic">{icon(ic, fg)}</div></div><div class="body">'
            f'<div class="t">{esc(r.get("model"))} · {esc(r.get("asset_serial"))}</div>'
            f'<div class="why">{esc(r.get("branch"))} — {esc(r.get("top_job") or r.get("repeat_fault") or r.get("reason"))}</div>'
            f'<div class="chips">{"".join(chips)}</div>'
            f'<div class="foot"><div class="m">{due}</div><div class="score" style="--p:{score:.0f};--ring:{ring}">'
            f'<span>{score:.0f}</span></div></div></div></div>')


def panel(title: str, items: list[tuple[str, str, str, str, str]]) -> None:
    """'Collections'-style side panel. items = (icon, color, name, sub, right)."""
    rows = []
    for ic, color, name, sub, right in items:
        bg, fg = _pastel(color)
        rows.append(f'<div class="it"><div class="sq" style="background:{bg}">{icon(ic, fg)}</div><div><div class="n">{esc(name)}</div>'
                    f'<div class="s">{esc(sub)}</div></div><div class="r">{esc(right)}</div></div>')
    html_block(f'<div class="si-panel"><h4>{esc(title)}</h4>{"".join(rows)}</div>')


def points(items: list[tuple[str, float, float]]) -> None:
    """Score parts as three small cards with bars. items = (label, points, max)."""
    html_block('<div class="si-points">' + "".join(
        f'<div><div class="k">{esc(k)}</div><div class="v">{float(v or 0):.1f}<span style="font-size:.75rem;color:var(--muted)"> / {mx:.0f}</span></div>'
        f'<div class="bar"><i style="width:{max(0, min(100, float(v or 0) / mx * 100)):.0f}%"></i></div></div>'
        for k, v, mx in items) + "</div>")


def card(name: str):
    """A rounded card around Streamlit widgets (keyed container; falls back to a bordered one)."""
    return st.container(key=f"card_{name}") if _keyed() else st.container(border=True)


def hit(name: str):
    """Container whose button (added inside it) is stretched invisibly over the whole card, making it clickable.
    On Streamlit versions without container keys the button simply shows under the card."""
    return st.container(key=f"hit_{name}") if _keyed() else st.container()


def badge(text, tone: str = "orange") -> str:
    return f'<span class="si-badge {tone}">{esc(text)}</span>'


def _num(v, decimals: int = 0) -> str:
    try:
        return f"{float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return esc(v) if v not in (None, "") else "—"


def table(rows: list[dict], columns: list[tuple] | None = None, max_height: int = 520) -> None:
    """Rows as a styled HTML table. columns: (key, label, kind), kind in
    text | wide | num | num1 | money | score | badge | bool | date | avatar. Values are HTML-escaped."""
    if not rows:
        return
    columns = columns or [(k, k.replace("_", " ").capitalize(), "num" if isinstance(rows[0].get(k), (int, float))
                           and not isinstance(rows[0].get(k), bool) else "text") for k in rows[0]]
    head = "".join(f"<th>{esc(lbl)}</th>" for _, lbl, _ in columns)
    tones = {"OLGA + telemetry": "orange", "OLGA only": "yel", "Telemetry only": "dark", "High": "red", "Medium": "yel",
             "Low": "gry", "HIGH": "red", "MEDIUM": "yel", "LOW": "gry", "proposed": "orange", "approved": "grn",
             "completed": "grn", "rejected": "red", "blocked": "red"}
    pastel = [k for k in PASTELS]
    body = []
    for i, r in enumerate(rows):
        cells = []
        for key, _, kind in columns:
            v = r.get(key)
            if kind == "score":
                pct = max(0.0, min(100.0, float(v or 0)))
                cells.append(f'<td><div class="si-bar"><div class="track"><div class="fill" style="width:{pct:.0f}%"></div>'
                             f'</div><span>{pct:.1f}</span></div></td>')
            elif kind in ("num", "num1", "money"):
                cells.append(f'<td class="num">{_num(v, 1 if kind == "num1" else 0)}</td>')
            elif kind == "badge":
                cells.append(f"<td>{badge(v, tones.get(str(v), 'gry')) if v not in (None, '') else '—'}</td>")
            elif kind == "bool":
                cells.append(f"<td>{badge('New', 'grn') if v else ''}</td>")
            elif kind == "date":
                cells.append(f"<td>{esc(str(v)[:10]) if v else '—'}</td>")
            elif kind == "wide":
                cells.append(f'<td class="wide">{esc(v)}</td>')
            elif kind == "avatar":
                bg, fg = _pastel(pastel[i % len(pastel)])
                ini = esc(str(v or "?")[:2].upper())
                cells.append(f'<td><span class="si-avt" style="background:{bg};color:{fg}">{ini}</span>&nbsp; {esc(v)}</td>')
            else:
                cells.append(f"<td>{esc(v) if v not in (None, '') else '—'}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    html_block(f'<div class="si-table-wrap" style="max-height:{max_height}px"><table class="si-table"><thead><tr>{head}'
               f'</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def kv(items: dict) -> None:
    html_block('<div class="si-kv">' + "".join(
        f'<div><div class="k">{esc(str(k).replace("_", " ").capitalize())}</div><div class="v">{esc(v if v is not None else "—")}</div></div>'
        for k, v in items.items()) + "</div>")


def _style(chart):
    return (chart.configure(background="transparent")
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=c("muted"), titleColor=c("muted"), gridColor=c("grid"), domainColor=c("line"),
                            tickColor=c("line"), labelFontSize=11, titleFontSize=11, labelFont="Inter, Segoe UI, sans-serif")
            .configure_legend(labelColor=c("ink"), titleColor=c("muted")))


def area_chart(df, x: str, y: str, title_y: str = "", height: int = 260) -> None:
    import altair as alt
    base = alt.Chart(df).encode(x=alt.X(f"{x}:T", title=None), y=alt.Y(f"{y}:Q", title=title_y or None))
    chart = (base.mark_area(color=alt.Gradient(gradient="linear", stops=[alt.GradientStop(color=c("card"), offset=0),
                                                                           alt.GradientStop(color=c("accent2"), offset=1)],
                                                 x1=1, x2=1, y1=1, y2=0), opacity=.55)
             + base.mark_line(color=c("accent"), strokeWidth=2.6)
             + base.mark_point(color=c("accent"), filled=True, size=50).encode(
                 tooltip=[alt.Tooltip(f"{x}:T"), alt.Tooltip(f"{y}:Q", format=",.0f")]))
    st.altair_chart(_style(chart.properties(height=height)), use_container_width=True, theme=None)


def bar_chart(df, cat: str, val: str, height: int = 300) -> None:
    import altair as alt
    chart = alt.Chart(df).mark_bar(cornerRadiusEnd=8, color=c("accent")).encode(
        y=alt.Y(f"{cat}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
        x=alt.X(f"{val}:Q", title=None),
        tooltip=[alt.Tooltip(f"{cat}:N"), alt.Tooltip(f"{val}:Q", format=",.0f")])
    st.altair_chart(_style(chart.properties(height=height)), use_container_width=True, theme=None)


def big_kpi(label: str, value: str, delta: str | None = None, tone: str = "flat", sub: str = "", delay_ms: int = 0) -> str:
    p = f'<span class="p {tone}">{esc(delta)}</span>' if delta else ""
    return (f'<div class="si-big" style="animation-delay:{delay_ms}ms"><div class="l">{esc(label)}</div>'
            f'<div class="v">{roll(value, delay_ms)}{p}</div><div class="s">{esc(sub)}</div></div>')


def speedo(title: str, ratio: float | None, number: str, label: str, tag: str, tag_warn: bool, unit: str,
           chips: list[tuple[str, str]] = ()) -> str:
    """Semicircle gauge with a needle (the reference design's response-time card)."""
    p = max(0.0, min(1.0, ratio or 0.0))
    r, cx, cy = 80, 100, 100
    half = math.pi * r
    ang = -90 + 180 * p
    arc = (f'<path d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="{c("card2")}" stroke-width="26" stroke-linecap="round"/>'
           f'<defs><linearGradient id="spg" x1="0" x2="1"><stop offset="0" stop-color="#FFB47E"/><stop offset="1" stop-color="#E85A0C"/></linearGradient></defs>'
           f'<path class="fill" d="M20 100 A80 80 0 0 1 180 100" fill="none" stroke="url(#spg)" stroke-width="26" stroke-linecap="round" '
           f'stroke-dasharray="{half * p:.1f} {half:.1f}"/>'
           f'<g class="needle" style="transform: rotate({ang:.1f}deg)"><path d="M100 46 L108 96 L100 90 L92 96 Z" fill="{c("ink")}"/></g>')
    ch = "".join(f'<div><b>{esc(v)}</b>{esc(k)}</div>' for k, v in chips)
    return (f'<div class="si-c"><h4>{esc(title)}</h4><div class="plus">{icon("chart")}</div><div class="si-speedo"><div>'
            f'<div class="lab">{esc(label)}</div><div class="num">{roll(number)}</div><span class="tag{" warn" if tag_warn else ""}">{esc(tag)}</span>'
            f'<div class="unit">{esc(unit)}</div></div><svg viewBox="0 0 200 110">{arc}</svg></div>'
            f'<div class="si-chips2">{ch}</div></div>')


def orange_card(title: str, button: str, number: str, small: str) -> str:
    return (f'<div class="si-c si-orange"><h4>{esc(title)}</h4><div class="plus">{icon("down")}</div>'
            f'<div class="pill">⊕ {esc(button)}</div><div class="huge">{roll(number)}<small>{esc(small)}</small></div></div>')


def note_card(title: str, when: str, headline: str, foot: str) -> str:
    return (f'<div class="si-c"><h4>{esc(title)}</h4><div class="plus">{icon("sparkle")}</div><div class="si-bubble"><span class="t">◷ {esc(when)}</span>'
            f'<div class="h">{esc(headline)}</div><div class="f"><i></i>{esc(foot)}</div></div></div>')


def map_card(title: str, number: str, sub: str) -> str:
    return (f'<div class="si-c si-map"><h4>{esc(title)}</h4><div class="plus">{icon("target")}</div><div class="pin">{icon("target")}</div>'
            f'<div class="foot"><div class="dot"></div><div class="n">{roll(number)}</div><div class="s">{esc(sub)}</div>'
            f'<div class="go">{icon("down")}</div></div></div>')


def week(days: list[tuple[str, int, bool]], next_up: tuple[str, str, list[str]] | None = None) -> None:
    """A calendar strip (like the reference 'Meeting Schedule'): one bubble per day sized by count, plus a dark 'next up' card."""
    mx = max([n for _, n, _ in days] + [1])
    heads = "".join(f'<div class="d{" today" if t else ""}">{esc(lbl)}</div>' for lbl, _, t in days)
    bubbles = ""
    for i, (_, n, _) in enumerate(days):
        size = 30 + 34 * (n / mx) if n else 34
        alpha = .35 + .65 * (n / mx) if n else 0
        style = (f'width:{size:.0f}px;height:{size:.0f}px;background:rgba(242,107,29,{alpha:.2f});animation-delay:{i * 60}ms' if n
                 else f'width:{size:.0f}px;height:{size:.0f}px;animation-delay:{i * 60}ms')
        bubbles += f'<div class="b{"" if n else " z"}" style="{style}">{n if n else ""}</div>'
    nxt = ""
    if next_up:
        h, sub, tags = next_up
        nxt = (f'<div class="si-next"><div class="h">{esc(h)}</div><div class="s">{esc(sub)}</div><div class="tags">'
               + "".join(f'<span class="{"" if i == 0 else "g"}">{esc(t)}</span>' for i, t in enumerate(tags)) + "</div></div>")
    html_block(f'<div class="si-week">{heads}{bubbles}</div>{nxt}')


def stat3(items: list[tuple[str, str]], highlight: int = 0) -> None:
    html_block('<div class="si-stat3">' + "".join(
        f'<div class="{"hl" if i == highlight else ""}"><div class="l">{esc(k)}</div><div class="v">{roll(v, i * 80)}</div></div>'
        for i, (k, v) in enumerate(items)) + "</div>")


LIST_HEAD = ("Machine", "Customer · branch", "Due", "OLGA value", "Signal", "")


def list_head() -> None:
    html_block('<div class="si-listhead">' + "".join(f"<div>{esc(h)}</div>" for h in LIST_HEAD) + "</div>")


def list_row(r: dict, fmt_money, delay_ms: int = 0) -> str:
    tones = {"OLGA + telemetry": "orange", "OLGA only": "yel", "Telemetry only": "dark"}
    due = r.get("days_to_due")
    due_txt = "—" if due is None else (f"Overdue {abs(int(float(due)))}d" if float(due) < 0 else f"in {int(float(due))}d")
    return (f'<div class="si-lrow" style="animation-delay:{delay_ms}ms"><div><div class="t">#{esc(r.get("overall_rank") or r.get("class_rank"))} '
            f'{esc(r.get("model"))} · {esc(r.get("asset_serial"))}</div><div class="sub">{esc(r.get("top_job") or r.get("repeat_fault") or "")}</div></div>'
            f'<div><div>{esc(r.get("customer_number") or "—")}</div><div class="sub">{esc(r.get("branch") or "")}</div></div>'
            f'<div>{badge(due_txt, "red" if due is not None and float(due) < 0 else "gry")}</div>'
            f'<div>{esc(fmt_money(r.get("due_value")) if r.get("due_value") else "—")}</div>'
            f'<div>{badge(r.get("signal_class"), tones.get(r.get("signal_class"), "gry"))}</div>'
            f'<div class="act">Details</div></div>')


def bars(df, x: str, y: str, height: int = 260) -> None:
    """Rounded grey columns with the latest one in orange and its value in a dark pill (the 'Project Deals' chart)."""
    import altair as alt
    d = df.copy()
    d["hl"] = [i == len(d) - 1 for i in range(len(d))]
    base = alt.Chart(d).encode(x=alt.X(f"{x}:O", title=None, axis=alt.Axis(labelAngle=0)),
                               y=alt.Y(f"{y}:Q", title=None))
    col = base.mark_bar(cornerRadius=14, size=26).encode(
        color=alt.condition(alt.datum.hl, alt.value(c("accent")), alt.value(c("card2"))),
        tooltip=[alt.Tooltip(f"{x}:O"), alt.Tooltip(f"{y}:Q", format=",.0f")])
    lab = base.transform_filter(alt.datum.hl).mark_text(dy=-16, fontWeight="bold", color=c("ink"), fontSize=12).encode(
        text=alt.Text(f"{y}:Q", format=",.0f"))
    st.altair_chart(_style((col + lab).properties(height=height)), use_container_width=True, theme=None)
