"""Visual design shared by the three pages (pure presentation, no data access).

Design: white app frame around a rounded light-grey canvas, a floating black pill navigation rail with icon
buttons (orange circle = current page, black tooltip on hover), white rounded cards, pastel icon tiles,
pastel cover cards, a gauge card, orange cover banners and the Mantrac logo.

The CSS forces this light design even when Snowsight is in dark mode (Streamlit inherits the viewer's
theme), so tables and charts are drawn by the helpers below instead of theme-dependent widgets.
"""
from __future__ import annotations

import base64
import html
import math
from pathlib import Path
from urllib.parse import quote

import streamlit as st

INK, MUTED, LINE, CANVAS, CARD = "#18181B", "#71717A", "#E6E4EC", "#ECEBF0", "#FFFFFF"
RAIL, ACCENT_SOFT, ACCENT, YELLOW = "#0E0E10", "#FFDCC2", "#F2711C", "#FCBC00"
GOOD, BAD = "#0F9F6E", "#E5484D"
PASTELS = {"orange": ("#FFE2CC", "#C2410C"), "pink": ("#F8DDEB", "#B4387A"), "mint": ("#D9F2E6", "#11795A"),
           "peach": ("#FDE6D3", "#B5581B"), "sky": ("#DCEBFB", "#2563A8"), "sun": ("#FFF0C2", "#8A6400")}
LOGO_FILE = Path(__file__).resolve().parent.parent / "assets" / "mantrac_logo.svg"
NAV = [("Analysis", "chart"), ("Recommendations", "target"), ("Assistant", "chat")]

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


def _icon_uri(name: str, color: str) -> str:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" '
           f'stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')
    return "data:image/svg+xml;utf8," + quote(svg)


def _rail_css() -> str:
    """Per-item icon + tooltip for the menu radio (Streamlit renders it as a radio group in the rail)."""
    out = []
    for i, (label, ic) in enumerate(NAV, 1):
        for sel in (f'section[data-testid="stSidebar"] [data-testid="stRadioGroup"] > :nth-child({i}) label',
                    f'section[data-testid="stSidebar"] [data-testid="stRadioGroup"] > label:nth-child({i})',
                    f'section[data-testid="stSidebar"] [role="radiogroup"] > label:nth-child({i})',
                    f'section[data-testid="stSidebar"] [role="radiogroup"] > :nth-child({i}) > label'):
            out.append(f'{sel}::before {{ background-image: url("{_icon_uri(ic, "#E4E4E7")}"); }}')
            out.append(f'{sel}:has(input:checked)::before {{ background-image: url("{_icon_uri(ic, INK)}"); }}')
            out.append(f'{sel}::after {{ content: "{label}"; }}')
    return "\n".join(out)


CSS = f"""
<style>
:root {{ --ink:{INK}; --muted:{MUTED}; --line:{LINE}; --canvas:{CANVAS}; --card:{CARD}; --rail:{RAIL};
        --soft:{ACCENT_SOFT}; --accent:{ACCENT}; --good:{GOOD}; --bad:{BAD}; }}
html, body, .stApp, [data-testid="stAppViewContainer"] {{ background: #FFFFFF !important; color: var(--ink) !important;
  font-family: Inter, "Segoe UI", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif; }}
[data-testid="stHeader"] {{ background: transparent !important; height: 0 !important; }}
[data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stStatusWidget"] {{ display: none !important; }}
.block-container, [data-testid="stMainBlockContainer"] {{ background: var(--canvas); border-radius: 34px;
  margin: 14px 14px 14px 0; padding: 2rem 2.2rem 6rem !important; max-width: none !important; min-height: calc(100vh - 28px); }}
.stApp p, .stApp li, .stApp label, .stApp span, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp td, .stApp th,
[data-testid="stMarkdownContainer"] {{ color: var(--ink); }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ color: var(--muted) !important; }}
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label {{ color: var(--muted) !important; font-size: .78rem !important; font-weight: 500; }}
hr {{ border-color: var(--line) !important; }}

/* ---------- floating black icon rail ---------- */
section[data-testid="stSidebar"] {{ background: transparent !important; border: none !important; width: 104px !important;
  min-width: 104px !important; max-width: 104px !important; transform: none !important; visibility: visible !important; }}
section[data-testid="stSidebar"] > div:first-child, [data-testid="stSidebarContent"] {{ background: transparent !important; overflow: visible !important; }}
[data-testid="stSidebarHeader"], [data-testid="stSidebarCollapseButton"], [data-testid="stSidebarResizeHandle"],
[data-testid="collapsedControl"], [data-testid="stSidebarCollapsedControl"], [data-testid="stSidebarNav"] {{ display: none !important; }}
[data-testid="stSidebarUserContent"] {{ position: fixed; top: 18px; left: 22px; width: 66px; height: calc(100vh - 36px);
  background: var(--rail); border-radius: 999px; padding: 18px 0 !important; box-shadow: 0 12px 34px rgba(14,14,16,.28);
  display: flex; flex-direction: column; align-items: center; overflow: visible !important; z-index: 1000; }}
[data-testid="stSidebarUserContent"] > div, [data-testid="stSidebarUserContent"] [data-testid="stVerticalBlock"] {{ width: 66px !important;
  align-items: center; gap: 10px; overflow: visible !important; }}
[data-testid="stSidebarUserContent"] [data-testid="stElementContainer"], [data-testid="stSidebarUserContent"] .element-container {{ width: auto !important; display: flex; justify-content: center; }}
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] {{ display: none !important; }}
section[data-testid="stSidebar"] [data-testid="stRadioGroup"], section[data-testid="stSidebar"] [role="radiogroup"] {{
  display: flex !important; flex-direction: column !important; gap: 14px !important; align-items: center; margin-top: 18px; }}
section[data-testid="stSidebar"] [data-testid="stRadioGroup"] label, section[data-testid="stSidebar"] [role="radiogroup"] label {{
  width: 44px !important; height: 44px !important; min-height: 44px; border-radius: 50% !important; padding: 0 !important; margin: 0 !important;
  position: relative; display: flex !important; align-items: center; justify-content: center; cursor: pointer; transition: background .15s; }}
section[data-testid="stSidebar"] [data-testid="stRadioGroup"] label > *, section[data-testid="stSidebar"] [role="radiogroup"] label > * {{
  opacity: 0 !important; position: absolute !important; inset: 0; width: 44px !important; height: 44px !important; overflow: hidden; }}
section[data-testid="stSidebar"] [data-testid="stRadioGroup"] label::before, section[data-testid="stSidebar"] [role="radiogroup"] label::before {{
  content: ""; width: 20px; height: 20px; background-repeat: no-repeat; background-size: 20px 20px; pointer-events: none; }}
section[data-testid="stSidebar"] [data-testid="stRadioGroup"] label:hover, section[data-testid="stSidebar"] [role="radiogroup"] label:hover {{ background: #26262B; }}
section[data-testid="stSidebar"] [data-testid="stRadioGroup"] label:has(input:checked), section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {{
  background: var(--soft) !important; box-shadow: 0 0 0 4px rgba(255,220,194,.22); }}
section[data-testid="stSidebar"] [data-testid="stRadioGroup"] label::after, section[data-testid="stSidebar"] [role="radiogroup"] label::after {{
  position: absolute; left: 58px; top: 50%; transform: translateY(-50%); background: var(--rail); color: #fff; font-size: .75rem;
  font-weight: 500; padding: 5px 10px; border-radius: 8px; white-space: nowrap; opacity: 0; pointer-events: none; transition: opacity .15s; z-index: 1001; }}
section[data-testid="stSidebar"] [data-testid="stRadioGroup"] label:hover::after, section[data-testid="stSidebar"] [role="radiogroup"] label:hover::after {{ opacity: 1; }}
{_rail_css()}
.si-mark {{ width: 40px; height: 40px; border-radius: 12px; background: {YELLOW}; overflow: hidden; display: block; }}
.si-mark img {{ width: 40px; height: 40px; display: block; }}
.si-rail-foot {{ position: fixed; left: 33px; bottom: 34px; z-index: 1001; }}
.si-avatar {{ width: 44px; height: 44px; border-radius: 50%; background: #26262B; color: #fff !important; display: flex; align-items: center;
  justify-content: center; font-weight: 700; font-size: .8rem; position: relative; cursor: default; }}
.si-avatar::after {{ content: attr(data-tip); position: absolute; left: 58px; top: 50%; transform: translateY(-50%); background: var(--rail);
  color: #fff; font-size: .72rem; font-weight: 500; padding: 5px 10px; border-radius: 8px; white-space: nowrap; opacity: 0; transition: opacity .15s; }}
.si-avatar:hover::after {{ opacity: 1; }}
section[data-testid="stSidebar"] .stButton > button {{ width: 44px; height: 44px; min-height: 44px; border-radius: 50% !important; padding: 0 !important;
  background: #26262B !important; border: none !important; color: #fff !important; font-size: 1.1rem; }}
section[data-testid="stSidebar"] .stButton > button:hover {{ background: var(--accent) !important; }}
section[data-testid="stSidebar"] .stButton > button p {{ color: #fff !important; }}

/* ---------- form controls ---------- */
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"],
[data-testid="stSelectbox"] [role="group"], [data-testid="stMultiSelect"] [role="group"], .stApp input, .stApp textarea {{
  background: var(--card) !important; color: var(--ink) !important; border-color: var(--line) !important; border-radius: 14px !important; }}
[data-baseweb="select"] *, [data-testid="stSelectbox"] *, [data-testid="stMultiSelect"] * {{ color: var(--ink); }}
[data-baseweb="select"] > div > div, [data-testid="stSelectbox"] [role="group"] > *, [data-testid="stMultiSelect"] [role="group"] > * {{ background: transparent !important; }}
[data-baseweb="select"] svg, [data-testid="stSelectbox"] svg, [data-testid="stMultiSelect"] svg {{ color: var(--muted) !important; fill: var(--muted) !important; }}
[data-baseweb="popover"] ul, [data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"], [role="listbox"], .react-aria-Popover {{ background: var(--card) !important; }}
[data-baseweb="popover"] li, [data-baseweb="popover"] li *, [role="listbox"] [role="option"], [role="listbox"] [role="option"] * {{ color: var(--ink) !important; }}
[data-baseweb="popover"] li:hover, [role="option"]:hover, [role="option"][data-focused], [aria-selected="true"][role="option"] {{ background: #FFF3EA !important; }}
[data-baseweb="tag"], [data-testid="stMultiSelectTag"] {{ background: var(--soft) !important; border-radius: 999px !important; }}
[data-baseweb="tag"] *, [data-testid="stMultiSelectTag"] * {{ color: var(--ink) !important; fill: var(--ink) !important; }}
[data-testid="stMain"] [data-testid="stRadioGroup"], .main [role="radiogroup"] {{ gap: 6px; flex-wrap: wrap; }}
[data-testid="stMain"] [data-testid="stRadioOption"], .main [role="radiogroup"] label {{ background: var(--card); border: 1px solid var(--line);
  border-radius: 999px; padding: 7px 15px !important; margin-right: 4px; }}
[data-testid="stMain"] [data-testid="stRadioOption"]:has(input:checked), .main [role="radiogroup"] label:has(input:checked) {{ background: var(--rail); border-color: var(--rail); }}
[data-testid="stMain"] [data-testid="stRadioOption"]:has(input:checked) *, .main [role="radiogroup"] label:has(input:checked) * {{ color: #fff !important; }}
[data-testid="stMain"] [data-testid="stRadioOption"] > div > div:first-child:not([data-testid]), .main [role="radiogroup"] label > div:first-child {{ display: none !important; }}

/* buttons: primary = black pill (like "Upload file"), secondary = white pill */
.stButton > button, .stDownloadButton > button {{ border-radius: 999px !important; padding: .5rem 1.15rem !important; font-weight: 600 !important;
  border: 1px solid var(--line) !important; background: var(--card) !important; color: var(--ink) !important; box-shadow: none !important;
  transition: transform .12s, background .12s; }}
.stButton > button:hover, .stDownloadButton > button:hover {{ transform: translateY(-1px); border-color: var(--accent) !important; }}
.stButton > button p, .stDownloadButton > button p {{ color: inherit !important; }}
.stButton > button[kind="primary"], [data-testid="baseButton-primary"], [data-testid="stBaseButton-primary"], .stDownloadButton > button {{
  background: var(--rail) !important; color: #fff !important; border-color: var(--rail) !important; }}
.stButton > button[kind="primary"] p, [data-testid="stBaseButton-primary"] p, [data-testid="baseButton-primary"] p, .stDownloadButton > button p {{ color: #fff !important; }}
.stButton > button[kind="primary"]:hover, .stDownloadButton > button:hover {{ background: var(--accent) !important; border-color: var(--accent) !important; }}
.stButton > button:disabled {{ opacity: .45; }}

/* tabs as a segmented pill (baseweb tabs in older Streamlit, role=tab in newer) */
[data-testid="stTabs"] [role="tablist"] {{ background: var(--card); border-radius: 999px; padding: 5px; gap: 4px; width: fit-content; border: none !important; }}
[data-testid="stTabs"] [role="tab"] {{ border-radius: 999px !important; padding: 8px 18px !important; background: transparent !important; border: none !important; }}
[data-testid="stTabs"] [role="tab"] p {{ color: var(--muted) !important; font-weight: 600; }}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{ background: var(--rail) !important; }}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] p {{ color: #fff !important; }}
[data-testid="stTabs"] [role="tablist"] > div:not([role="tab"]) {{ display: none !important; }}
[data-baseweb="tab-list"] {{ background: var(--card); border-radius: 999px; padding: 5px; gap: 4px; width: fit-content; }}
[data-baseweb="tab"] {{ border-radius: 999px !important; padding: 8px 18px !important; height: auto !important; background: transparent !important; }}
[data-baseweb="tab"] p {{ color: var(--muted) !important; font-weight: 600; }}
[data-baseweb="tab"][aria-selected="true"] {{ background: var(--rail) !important; }}
[data-baseweb="tab"][aria-selected="true"] p {{ color: #fff !important; }}
[data-baseweb="tab-highlight"], [data-baseweb="tab-border"] {{ display: none !important; }}

/* cards: keyed containers (st.container(key="card_…")), expanders, alerts */
[class*="st-key-card"] {{ background: var(--card) !important; border: none !important; border-radius: 26px !important; padding: 20px 22px !important;
  box-shadow: 0 1px 2px rgba(14,14,16,.04); }}
[data-testid="stExpander"] details, [data-testid="stExpander"] {{ background: var(--card) !important; border: none !important; border-radius: 20px !important; }}
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary * {{ color: var(--ink) !important; font-weight: 600; }}
[data-testid="stExpanderDetails"] {{ border: none !important; }}
[data-testid="stAlert"] {{ border-radius: 16px !important; }}
[data-testid="stAlert"] * {{ color: var(--ink) !important; }}
.stApp pre, .stApp code {{ background: #F4F3F8 !important; color: #3F3A5A !important; border-radius: 10px; }}
.stApp pre code {{ background: transparent !important; }}
[data-testid="stVegaLiteChart"], .stVegaLiteChart {{ background: transparent !important; }}

/* ---------- chat ---------- */
[data-testid="stChatMessage"] {{ background: transparent !important; padding: .35rem 0 !important; gap: .75rem; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]), [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {{ flex-direction: row-reverse; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"],
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) [data-testid="stChatMessageContent"] {{
  background: var(--rail); border-radius: 22px 22px 6px 22px; padding: .75rem 1.05rem; max-width: 72%; flex: 0 1 auto; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"] *,
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) [data-testid="stChatMessageContent"] * {{ color: #fff !important; }}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) [data-testid="stChatMessageContent"],
[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) [data-testid="stChatMessageContent"] {{
  background: var(--card); border-radius: 22px 22px 22px 6px; padding: 1rem 1.2rem; }}
[data-testid="stChatMessageAvatarAssistant"], [data-testid="chatAvatarIcon-assistant"] {{ background: {YELLOW} !important; color: var(--ink) !important; border-radius: 12px !important; }}
[data-testid="stChatMessageAvatarUser"], [data-testid="chatAvatarIcon-user"] {{ background: var(--soft) !important; color: var(--ink) !important; border-radius: 50% !important; }}
[data-testid="stChatMessageAvatarAssistant"] *, [data-testid="stChatMessageAvatarUser"] * {{ color: var(--ink) !important; }}
[data-testid="stChatInput"], [data-testid="stChatInput"] > div {{ background: var(--card) !important; border-radius: 999px !important; border-color: var(--line) !important; }}
[data-testid="stChatInput"] textarea {{ background: transparent !important; color: var(--ink) !important; }}
[data-testid="stChatInput"] textarea::placeholder {{ color: #A1A1AA !important; opacity: 1; }}
[data-testid="stChatMessageContent"] table {{ border-collapse: collapse; margin: .4rem 0; font-size: .85rem; }}
[data-testid="stChatMessageContent"] th {{ color: var(--muted) !important; font-weight: 500; border-bottom: 1px solid var(--line); padding: 6px 12px; text-align: left; }}
[data-testid="stChatMessageContent"] td {{ border-bottom: 1px solid #F2F1F6; padding: 7px 12px; }}
[data-testid="stBottom"], [data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"], .stChatFloatingInputContainer {{ background: transparent !important; }}
[data-testid="stBottomBlockContainer"] {{ padding-left: 7rem !important; }}
[data-testid="stChatInputSubmitButton"] {{ background: var(--rail) !important; border-radius: 999px !important; }}
[data-testid="stChatInputSubmitButton"] svg {{ fill: #fff !important; color: #fff !important; }}

/* ---------- HTML components ---------- */
.si-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin: 0 0 22px; flex-wrap: wrap; }}
.si-top h1 {{ font-size: 2.3rem; font-weight: 500; letter-spacing: -.03em; margin: 0; padding: 0; color: var(--ink); line-height: 1.15; }}
.si-top .sub {{ color: var(--muted); font-size: .9rem; margin-top: 4px; }}
.si-top .right {{ display:flex; gap:10px; align-items:center; flex-wrap: wrap; }}
.si-top .logo {{ height: 38px; border-radius: 8px; overflow: hidden; background: #fff; padding: 3px; }}
.si-top .logo img {{ height: 32px; display:block; }}
.si-chip {{ background: var(--card); border-radius: 999px; padding: 8px 14px; font-size: .78rem; color: var(--muted); white-space: nowrap; }}
.si-chip b {{ color: var(--ink); font-weight: 600; }}
.si-chip.dark {{ background: var(--rail); color: #fff; }} .si-chip.dark b {{ color: #fff; }}
.si-cover {{ background: var(--card); border-radius: 28px; overflow: hidden; margin-bottom: 18px; }}
.si-cover .band {{ height: 84px; background: linear-gradient(90deg, #FFC9A0 0%, #FFD8B5 40%, #FFE5C4 72%, #FFF1D6 100%); }}
.si-cover .body {{ display:flex; justify-content:space-between; align-items:flex-end; gap:22px; padding: 0 26px 22px; flex-wrap: wrap; }}
.si-cover .who {{ display:flex; gap:16px; align-items:flex-end; }}
.si-cover .badge-ic {{ width: 72px; height: 72px; border-radius: 22px; background: var(--rail); margin-top: -36px; border: 4px solid #fff;
  display:flex; align-items:center; justify-content:center; flex: none; }}
.si-cover .badge-ic svg {{ width: 30px; height: 30px; stroke: {YELLOW}; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }}
.si-cover .eyebrow {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .1em; color: #C2410C; font-weight: 700; margin-top: 14px; }}
.si-cover h2 {{ font-size: 1.35rem; margin: 2px 0 2px; padding: 0; font-weight: 650; color: var(--ink); }}
.si-cover p {{ margin: 0; color: var(--muted); font-size: .88rem; max-width: 760px; }}
.si-cover .stats {{ display:flex; gap: 28px; }}
.si-cover .stat .v {{ font-size: 1.6rem; font-weight: 700; color: var(--ink); line-height: 1.1; letter-spacing: -.02em; }}
.si-cover .stat .l {{ font-size: .74rem; color: var(--muted); }}
.si-tiles {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 14px; }}
.si-tile {{ background: var(--card); border-radius: 22px; padding: 16px 18px; transition: transform .15s, box-shadow .15s; position: relative; }}
.si-tile:hover {{ transform: translateY(-3px); box-shadow: 0 12px 26px rgba(14,14,16,.08); }}
.si-tile .ic {{ width: 38px; height: 38px; border-radius: 12px; display:flex; align-items:center; justify-content:center; }}
.si-tile .ic svg {{ width: 18px; height: 18px; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }}
.si-tile .l {{ font-size: .86rem; font-weight: 600; color: var(--ink); margin-top: 12px; line-height: 1.25; }}
.si-tile .v {{ font-size: 1.55rem; font-weight: 700; letter-spacing: -.02em; color: var(--ink); margin-top: 2px; }}
.si-tile .s {{ font-size: .74rem; color: var(--muted); margin-top: 2px; }}
.si-tile .d {{ position:absolute; top:16px; right:16px; font-size: .7rem; font-weight: 600; border-radius: 999px; padding: 3px 8px; }}
.d.good {{ background: #E3F6EE; color: var(--good); }} .d.bad {{ background: #FDE8E8; color: var(--bad); }} .d.flat {{ background: #F1F1F4; color: var(--muted); }}
.si-sec {{ display:flex; justify-content:space-between; align-items:baseline; margin: 2px 0 12px; gap: 12px; }}
.si-sec h3 {{ font-size: 1.15rem; font-weight: 600; margin: 0; padding: 0; color: var(--ink); }}
.si-sec .sub {{ color: var(--muted); font-size: .78rem; }}
.si-gauge {{ display:flex; align-items:center; gap: 22px; flex-wrap: wrap; }}
.si-gauge svg {{ width: 170px; height: 170px; flex: none; }}
.si-gauge .legend {{ flex: 1; min-width: 180px; }}
.si-gauge .row {{ display:flex; justify-content:space-between; gap: 10px; padding: 7px 0; border-bottom: 1px solid #F2F1F6; font-size: .82rem; }}
.si-gauge .row:last-child {{ border-bottom: none; }}
.si-gauge .dot {{ display:inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; }}
.si-gauge .row b {{ color: var(--ink); }}
.si-table-wrap {{ background: var(--card); border-radius: 18px; overflow: auto; }}
table.si-table {{ border-collapse: separate; border-spacing: 0; width: 100%; font-size: .83rem; }}
table.si-table th {{ position: sticky; top: 0; background: var(--card); color: var(--muted); font-weight: 500; text-align: left;
  padding: 10px 12px; border-bottom: 1px solid var(--line); white-space: nowrap; font-size: .75rem; z-index: 1; }}
table.si-table td {{ padding: 11px 12px; border-bottom: 1px solid #F2F1F6; color: var(--ink); vertical-align: middle; }}
table.si-table tr:hover td {{ background: #FAF9FD; }}
table.si-table td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
table.si-table td.wide {{ min-width: 300px; color: #52525B; font-size: .8rem; }}
.si-avt {{ width: 30px; height: 30px; border-radius: 50%; display:inline-flex; align-items:center; justify-content:center; font-size: .7rem; font-weight: 700; }}
.si-bar {{ display:flex; align-items:center; gap:8px; min-width: 120px; }}
.si-bar .track {{ flex:1; height: 6px; border-radius: 99px; background: #FFEDE0; overflow: hidden; }}
.si-bar .fill {{ height: 100%; border-radius: 99px; background: linear-gradient(90deg, #FDBA74, var(--accent)); }}
.si-bar span {{ font-weight: 700; font-size: .8rem; width: 34px; text-align: right; }}
.si-badge {{ display:inline-block; padding: 3px 9px; border-radius: 999px; font-size: .72rem; font-weight: 600; white-space: nowrap; }}
.si-badge.orange {{ background: #FFEDE0; color: #C2410C; }} .si-badge.yel {{ background: #FFF4D1; color: #8A6400; }}
.si-badge.grn {{ background: #E3F6EE; color: #0B7A55; }} .si-badge.red {{ background: #FDE8E8; color: #B42318; }}
.si-badge.gry {{ background: #F1F1F4; color: #52525B; }} .si-badge.dark {{ background: var(--rail); color: #fff; }}
.si-cards {{ display:grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 16px; }}
.si-mcard {{ background: var(--card); border-radius: 22px; padding: 8px; transition: transform .15s, box-shadow .15s; }}
.si-mcard:hover {{ transform: translateY(-3px); box-shadow: 0 12px 26px rgba(14,14,16,.08); }}
.si-mcard .cover {{ height: 112px; border-radius: 16px; position: relative; display:flex; align-items:center; justify-content:center; }}
.si-mcard .cover .tag {{ position:absolute; top:10px; left:10px; background: var(--rail); color:#fff; font-size: .68rem; font-weight: 600;
  padding: 3px 9px; border-radius: 999px; }}
.si-mcard .cover .rank {{ position:absolute; top:10px; right:10px; background: rgba(255,255,255,.85); color: var(--ink); font-size: .72rem;
  font-weight: 700; padding: 3px 9px; border-radius: 999px; }}
.si-mcard .cover .ic {{ width: 52px; height: 52px; border-radius: 16px; background: rgba(255,255,255,.88); display:flex; align-items:center; justify-content:center; }}
.si-mcard .cover .ic svg {{ width: 24px; height: 24px; fill: none; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }}
.si-mcard .body {{ padding: 12px 10px 8px; }}
.si-mcard .t {{ font-weight: 650; font-size: .92rem; color: var(--ink); line-height: 1.3; }}
.si-mcard .why {{ font-size: .76rem; color: var(--muted); margin-top: 4px; line-height: 1.35; display: -webkit-box; -webkit-line-clamp: 3;
  -webkit-box-orient: vertical; overflow: hidden; min-height: 3.1em; }}
.si-mcard .foot {{ display:flex; justify-content:space-between; align-items:center; margin-top: 10px; }}
.si-mcard .foot .m {{ font-size: .74rem; color: var(--muted); line-height: 1.3; }} .si-mcard .foot .m b {{ color: var(--ink); font-size: .82rem; }}
.si-mcard .score {{ width: 44px; height: 44px; border-radius: 50%; display:flex; align-items:center; justify-content:center; font-weight: 700;
  font-size: .8rem; color: var(--ink); background: conic-gradient(var(--accent) calc(var(--p) * 1%), #FFEDE0 0); position: relative; }}
.si-mcard .score::before {{ content: ""; position:absolute; inset: 5px; background: #fff; border-radius: 50%; }}
.si-mcard .score span {{ position: relative; }}
.si-panel {{ background: var(--card); border-radius: 24px; padding: 18px 20px; }}
.si-panel h4 {{ margin: 0 0 10px; padding: 0; font-size: 1.1rem; font-weight: 600; color: var(--ink); }}
.si-panel .it {{ display:flex; align-items:center; gap: 12px; padding: 9px 0; border-bottom: 1px solid #F2F1F6; }}
.si-panel .it:last-child {{ border-bottom: none; }}
.si-panel .it .sq {{ width: 34px; height: 34px; border-radius: 10px; flex: none; display:flex; align-items:center; justify-content:center; }}
.si-panel .it .sq svg {{ width: 16px; height: 16px; fill: none; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }}
.si-panel .it .n {{ font-size: .84rem; font-weight: 600; color: var(--ink); }} .si-panel .it .s {{ font-size: .72rem; color: var(--muted); }}
.si-panel .it .r {{ margin-left: auto; font-size: .82rem; font-weight: 700; color: var(--ink); }}
.si-welcome {{ text-align:center; padding: 2vh 0 1vh; }}
.si-welcome h2 {{ font-size: 1.9rem; font-weight: 500; margin: 0; padding: 0; color: var(--ink); letter-spacing: -.02em; }}
.si-welcome p {{ color: var(--muted); margin-top: 6px; }}
.si-kv {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; }}
.si-kv div {{ background: #F7F6FB; border-radius: 14px; padding: 10px 12px; }}
.si-kv .k {{ font-size: .72rem; color: var(--muted); }} .si-kv .v {{ font-weight: 650; color: var(--ink); }}
.si-rec {{ background: var(--card); border-radius: 24px; padding: 18px 20px; margin-bottom: 12px; }}
.si-rec h4 {{ margin: 8px 0 6px; padding: 0; font-size: 1.02rem; color: var(--ink); }}
.si-rec p, .si-rec li {{ margin: 4px 0; color: #3F3F46; font-size: .86rem; }}
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
    html_block(CSS)


def brand(user: str, role: str) -> None:
    """Emblem at the top of the rail; the signed-in user sits at its foot (see user_card)."""
    initials = "".join(p[0] for p in user.split("@")[0].replace(".", " ").split()[:2]).upper() or "?"
    mark = mark_uri()
    with st.sidebar:
        img = f'<img src="{mark}" alt="Mantrac">' if mark else ""
        html_block(f'<div class="si-mark" title="Mantrac Service Intelligence">{img}</div>')
    st.session_state["_si_user_html"] = (f'<div class="si-rail-foot"><div class="si-avatar" data-tip="{esc(user)} · {esc(role)}">'
                                         f'{esc(initials)}</div></div>')


def user_card() -> None:
    if st.session_state.get("_si_user_html"):
        with st.sidebar:
            html_block(st.session_state["_si_user_html"])


def topbar(title: str, subtitle: str, chips: list[str] | None = None) -> None:
    """Page title (left) and the Mantrac logo with status chips (right). Chips may contain <b>, nothing else."""
    chip_html = "".join(f'<span class="si-chip">{c}</span>' for c in (chips or []))
    logo = logo_uri()
    logo_html = f'<span class="logo"><img src="{logo}" alt="Mantrac CAT"></span>' if logo else ""
    html_block(f'<div class="si-top"><div><h1>{esc(title)}</h1><div class="sub">{esc(subtitle)}</div></div>'
               f'<div class="right">{chip_html}{logo_html}</div></div>')


def cover(eyebrow: str, title: str, text: str, stats: list[tuple[str, str]] = (), icon_name: str = "chart") -> None:
    """White card with the orange cover band, an icon badge, a title and headline stats."""
    s = "".join(f'<div class="stat"><div class="v">{esc(v)}</div><div class="l">{esc(lbl)}</div></div>' for v, lbl in stats)
    html_block(f'<div class="si-cover"><div class="band"></div><div class="body"><div class="who"><div class="badge-ic">{icon(icon_name)}</div>'
               f'<div><div class="eyebrow">{esc(eyebrow)}</div><h2>{esc(title)}</h2><p>{esc(text)}</p></div></div>'
               f'<div class="stats">{s}</div></div></div>')


def tile(label: str, value: str, sub: str = "", icon_name: str = "activity", color: str = "orange",
         delta: str | None = None, tone: str = "flat", tip: str | None = None) -> str:
    bg, fg = PASTELS.get(color, PASTELS["orange"])
    d = f'<div class="d {tone}">{esc(delta)}</div>' if delta else ""
    title = f' title="{esc(tip)}"' if tip else ""
    return (f'<div class="si-tile"{title}>{d}<div class="ic" style="background:{bg}">{icon(icon_name, fg)}</div>'
            f'<div class="l">{esc(label)}</div><div class="v">{esc(value)}</div><div class="s">{esc(sub)}</div></div>')


def tiles(cards: list[str]) -> None:
    html_block(f'<div class="si-tiles">{"".join(cards)}</div>')


def section(title: str, sub: str = "") -> None:
    html_block(f'<div class="si-sec"><h3>{esc(title)}</h3><span class="sub">{esc(sub)}</span></div>')


def gauge(ratio: float | None, center: str, caption: str, legend: list[tuple[str, str, str]]) -> None:
    """Three-quarter arc gauge like the video's 'Storage' card. legend = (color, label, value)."""
    r, cx, cy, sweep = 62, 85, 85, 270
    circ = 2 * math.pi * r
    track = circ * sweep / 360
    p = max(0.0, min(1.0, ratio or 0.0))
    arc = (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#EFEDF5" stroke-width="12" stroke-linecap="round" '
           f'stroke-dasharray="{track:.1f} {circ:.1f}" transform="rotate(135 {cx} {cy})"/>'
           f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{RAIL}" stroke-width="12" stroke-linecap="round" '
           f'stroke-dasharray="{track * p:.1f} {circ:.1f}" transform="rotate(135 {cx} {cy})"/>')
    txt = (f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" font-size="28" font-weight="500" fill="{INK}" '
           f'font-family="Inter, Segoe UI, sans-serif">{esc(center)}</text>'
           f'<text x="{cx}" y="{cy + 22}" text-anchor="middle" font-size="9" fill="{MUTED}" '
           f'font-family="Inter, Segoe UI, sans-serif">{esc(caption)}</text>')
    rows = "".join(f'<div class="row"><span><span class="dot" style="background:{c}"></span>{esc(lbl)}</span><b>{esc(v)}</b></div>'
                   for c, lbl, v in legend)
    html_block(f'<div class="si-gauge"><svg viewBox="0 0 170 170">{arc}{txt}</svg><div class="legend">{rows}</div></div>')


def machine_cards(rows: list[dict], fmt_money) -> None:
    """'Saved for later'-style cards for ranked machines: pastel cover by signal, tag, rank, score ring."""
    style = {"OLGA + telemetry": ("orange", "truck"), "OLGA only": ("pink", "wrench"), "Telemetry only": ("mint", "signal")}
    out = []
    for r in rows:
        color, ic = style.get(r.get("signal_class"), ("sky", "truck"))
        bg, fg = PASTELS[color]
        rank = r.get("overall_rank") or r.get("class_rank")
        due = f"<b>{esc(fmt_money(r.get('due_value')))}</b><br>OLGA value due" if r.get("due_value") else \
              f"<b>{esc(r.get('max_fault_days') or 0)} fault days</b><br>last 30 days"
        new = ' <span class="si-badge grn">New</span>' if r.get("is_new_today") else ""
        out.append(
            f'<div class="si-mcard"><div class="cover" style="background:{bg}"><span class="tag">{esc(r.get("signal_class"))}</span>'
            f'<span class="rank">#{esc(rank)}</span><div class="ic">{icon(ic, fg)}</div></div><div class="body">'
            f'<div class="t">{esc(r.get("model"))} · {esc(r.get("asset_serial"))}{new}</div>'
            f'<div class="why">{esc(r.get("branch"))} — {esc(r.get("top_job") or r.get("repeat_fault") or r.get("reason"))}</div>'
            f'<div class="foot"><div class="m">{due}</div><div class="score" style="--p:{float(r.get("score") or 0):.0f}">'
            f'<span>{float(r.get("score") or 0):.0f}</span></div></div></div></div>')
    html_block(f'<div class="si-cards">{"".join(out)}</div>')


def panel(title: str, items: list[tuple[str, str, str, str, str]]) -> None:
    """'Collections'-style side panel. items = (icon, color, name, sub, right)."""
    rows = []
    for ic, color, name, sub, right in items:
        bg, fg = PASTELS.get(color, PASTELS["orange"])
        rows.append(f'<div class="it"><div class="sq" style="background:{bg}">{icon(ic, fg)}</div><div><div class="n">{esc(name)}</div>'
                    f'<div class="s">{esc(sub)}</div></div><div class="r">{esc(right)}</div></div>')
    html_block(f'<div class="si-panel"><h4>{esc(title)}</h4>{"".join(rows)}</div>')


def card(name: str):
    """A white rounded card around Streamlit widgets (keyed container; falls back to a bordered one)."""
    try:
        return st.container(key=f"card_{name}")
    except TypeError:                     # Streamlit < 1.39 has no key on containers
        return st.container(border=True)


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
             "Low": "gry", "open": "orange", "approved": "grn", "done": "grn", "rejected": "red", "blocked": "red"}
    pastel = list(PASTELS.values())
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
                bg, fg = pastel[i % len(pastel)]
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
            .configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor="#F0EFF4", domainColor=LINE, tickColor=LINE,
                            labelFontSize=11, titleFontSize=11, labelFont="Inter, Segoe UI, sans-serif")
            .configure_legend(labelColor=INK, titleColor=MUTED))


def area_chart(df, x: str, y: str, title_y: str = "", height: int = 260) -> None:
    import altair as alt
    base = alt.Chart(df).encode(x=alt.X(f"{x}:T", title=None), y=alt.Y(f"{y}:Q", title=title_y or None))
    chart = (base.mark_area(color=alt.Gradient(gradient="linear", stops=[alt.GradientStop(color="#FFFFFF", offset=0),
                                                                           alt.GradientStop(color=ACCENT_SOFT, offset=1)],
                                                 x1=1, x2=1, y1=1, y2=0), opacity=.95)
             + base.mark_line(color=RAIL, strokeWidth=2.2)
             + base.mark_point(color=ACCENT, filled=True, size=45).encode(tooltip=[alt.Tooltip(f"{x}:T"), alt.Tooltip(f"{y}:Q", format=",.0f")]))
    st.altair_chart(_style(chart.properties(height=height)), use_container_width=True, theme=None)


def bar_chart(df, cat: str, val: str, height: int = 300) -> None:
    import altair as alt
    chart = alt.Chart(df).mark_bar(cornerRadiusEnd=8, color=ACCENT).encode(
        y=alt.Y(f"{cat}:N", sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
        x=alt.X(f"{val}:Q", title=None),
        tooltip=[alt.Tooltip(f"{cat}:N"), alt.Tooltip(f"{val}:Q", format=",.0f")])
    st.altair_chart(_style(chart.properties(height=height)), use_container_width=True, theme=None)
