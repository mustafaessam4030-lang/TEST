"""Page 3 — VELA AI: a service intelligence analyst (Cortex) that answers from live, read-only queries, the AI Brain
(CAT training library, Mantrac repair history, machine profiles, business rules) and photos the user attaches."""
import inspect
import json
import re
import time
import uuid

import streamlit as st

from si_native import theme, ui
from si_native import brain
from si_native.agent import FALLBACK_MODEL, MODEL, ask, cortex_complete
from si_native.assistant import answer, rules_plan
from si_native.plans import PlanStore
from si_native.recommend import all_candidates

ui.header("VELA AI", "Your service intelligence brain: fleet data, CAT training manuals, Mantrac repair history and "
          "photos, with the evidence behind every answer.",
          [f"Model <b>{MODEL}</b>"])
AVATAR = theme.ai_icon()
an = ui.analytics()
email = ui.user_email()
store = PlanStore(ui.session())

if "chat" not in st.session_state:
    st.session_state["chat"] = []
    st.session_state["ctx"] = None
st.session_state.setdefault("chat_id", str(uuid.uuid4()))
if st.session_state["chat"]:
    _, nc = st.columns([6, 1])
    if nc.button("＋ New chat", key="new_chat", use_container_width=True):
        st.session_state["chat"] = []
        st.session_state["ctx"] = None
        st.session_state["chat_id"] = str(uuid.uuid4())
        st.rerun()

EXAMPLES = [("target", "orange", "Which 10 machines should we act on first today, and why?"),
            ("alert", "pink", "Which product families had the most high-severity faults last month?"),
            ("wallet", "sky", "Which branches have the most OLGA value overdue?"),
            ("repeat", "mint", "777G shows low system voltage: what usually causes it and how do we fix it?")]
PHOTO_TYPES = ["jpg", "jpeg", "png", "webp"]
CHAT_FILES = "accept_file" in inspect.signature(st.chat_input).parameters      # Streamlit >= 1.43


@st.cache_data(ttl=3600, show_spinner=False)
def known_values() -> dict:
    """Exact names the model can filter on (branches, product families, ...), so informal wording still matches."""
    wanted = {"branch": ("opportunities", "branch"), "product_family": ("fault_events", "product_family"),
              "smcs_group": ("opportunities", "smcs_group"), "dealer_code": ("fault_events", "dealer_code")}
    got = ui.run_parallel([lambda s=s, d=d: an.dimension_values(s, d) for s, d in wanted.values()])
    out = {k: v[:150] for k, v in zip(wanted, got) if not isinstance(v, Exception)}
    try:
        out["rules"] = ui.CachedRunner().run(brain.rules_spec(), email).rows
    except Exception:  # noqa: BLE001 - the assistant still works without the rules
        pass
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def manual_search(query: str, system, limit: int, visual: bool = False) -> list:
    return brain.kb_search(ui.session(), query, system, limit, visual)


@st.cache_data(ttl=3600, show_spinner=False, max_entries=60)
def training_picture(path: str, page) -> bytes | None:
    """The diagram image, or the PDF page rendered as a picture (None if pypdfium2 is not installed)."""
    data = brain.read_training_file(ui.session(), path)
    return data if brain.media_kind(path) == "image" else brain.pdf_page_png(data, page or 1)


@st.cache_data(ttl=3000, show_spinner=False)
def training_link(path: str, page) -> str:
    return brain.training_link(ui.session(), path, page)


def show_videos(turn: dict) -> None:
    """Videos found on the web: thumbnail cards that open the video, and an in-chat player for the first one."""
    videos = [v for r in turn.get("results") or [] if r.get("tool") == "video" for v in r.get("rows") or []][:3]
    if not videos:
        return
    theme.html_block('<div class="si-videos">' + "".join(
        f'<a class="si-video" href="{theme.esc(v["url"])}" target="_blank" rel="noopener">'
        f'<div class="thumb" style="background-image:url(\'{theme.esc(v["thumb"])}\')"><span class="play">&#9654;</span></div>'
        f'<div class="t">{theme.esc(v["title"])}</div><div class="s">Watch on YouTube ↗</div></a>' for v in videos) + "</div>")
    with st.expander(f"Play here: {videos[0]['title'][:70]}"):
        st.video(videos[0]["url"])
        st.caption("If the player stays blank, Snowflake is blocking the embedded player: use the card above.")


def show_training_pictures(turn: dict) -> None:
    """Pictures from the CAT training material behind the answer (pages, slides, diagrams)."""
    passages = [m for r in turn.get("results") or [] if r.get("tool") == "manual" for m in r.get("rows") or []]
    visuals = brain.training_visuals(passages, 3)
    if not visuals:
        return
    pics = ui.run_parallel([lambda v=v: training_picture(v["path"], v["page"]) for v in visuals])
    st.caption("From the CAT training material")
    cols = st.columns(len(visuals))
    for col, v, pic in zip(cols, visuals, pics):
        with col:
            if isinstance(pic, (bytes, bytearray)):
                st.image(pic, caption=v["caption"], use_container_width=True)
            else:
                st.markdown(f"**{v['caption']}**")
            try:
                st.markdown(f"[Open the {'page' if v['kind'] == 'pdf' else 'picture'} ↗]({training_link(v['path'], v['page'])})")
            except Exception:  # noqa: BLE001
                pass


@st.cache_data(ttl=3600, show_spinner=False)
def web_search(query: str, n: int) -> list:
    return brain.web_search(ui.session(), query, n)


def look(paths: list, question: str) -> str:
    return brain.look(ui.session(), paths, question, (MODEL, FALLBACK_MODEL))


def save_photos(files) -> tuple[list, list]:
    """Prepared JPEGs uploaded to the AI Brain photo stage. Returns (stage paths, small previews for the chat)."""
    paths, previews = [], []
    for f in list(files or [])[:brain.MAX_PHOTOS]:
        try:
            data = brain.prepare_photo(f.getvalue())
            paths.append(brain.save_photo(ui.session(), data, email, st.session_state["chat_id"]))
            previews.append(data)
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Could not upload {getattr(f, 'name', 'a photo')}: {str(exc)[:160]}")
    return paths, previews


def show_details(turn: dict) -> None:
    results = turn.get("results") or []
    if not results:
        return
    with st.expander("How I got this"):
        for r in results:
            title = r.get("metric") or r.get("purpose") or r.get("list") or r.get("tool")
            st.markdown(f"**{title}** · " + KIND.get(r.get("tool"), "read-only query"))
            if r.get("tool") == "photo":
                st.markdown(r.get("findings") or "")
                continue
            if r.get("tool") in ("web", "video"):
                for i, w in enumerate(r.get("rows") or [], 1):
                    st.markdown(f"{i}. [{w.get('title') or w.get('url')}]({w.get('url')})")
                continue
            if r.get("tool") == "manual":
                for i, m in enumerate(r.get("rows") or [], 1):
                    st.markdown(f"{i}. **{m.get('course')}** · {m.get('module')} · p. {m.get('page')} "
                                f"<span style='opacity:.6'>({m.get('file')})</span>", unsafe_allow_html=True)
                continue
            if r.get("tool") == "repairs":
                theme.kv({"Similar jobs": r.get("similar_jobs"), "Median labour hours": r.get("median_labour_hours"),
                          "Jobs with hours": r.get("jobs_with_hours")})
                if r.get("common_parts"):
                    theme.table(r["common_parts"], max_height=240)
            if r.get("tool") == "machine":
                for key in ("fleet", "recent_jobs", "oil_samples", "inspections", "service_letters", "cat_alerts",
                            "fault_codes_90d"):
                    val = r.get(key)
                    if isinstance(val, list) and val:
                        st.caption(key.replace("_", " ").capitalize())
                        theme.table(val, max_height=260)
                    elif isinstance(val, str):
                        st.caption(f"{key.replace('_', ' ').capitalize()}: {val}")
            if r.get("tool") == "metric":
                theme.kv({"Value": r.get("value"), "Period": r.get("period"), "Previous": r.get("previous_value"),
                          "Change %": r.get("change_pct"), "Records": r.get("records")})
            if r.get("rows"):
                theme.table(r["rows"][:200], max_height=300)
                if r.get("truncated"):
                    st.caption("Only the first 200 rows were read.")
        st.markdown("**SQL and query IDs**")                      # no expander inside an expander (older Streamlit)
        ui.evidence_list([e for r in results for e in r.get("evidence", [])])


TABLE_RE = re.compile(r"SERVICE_INTELLIGENCE\.\w+\.(\w+)", re.I)
KIND = {"metric": "approved metric", "top_list": "daily ranking", "manual": "CAT training library (Cortex Search)",
        "repairs": "Mantrac repair history", "machine": "AI Brain machine profile", "photo": "VELA vision",
        "web": "public web (Cortex web search)", "video": "videos on the web (Cortex web search)"}
SOURCE = {"video": "Web videos", "web": "Web search", "manual": "CAT training library", "repairs": "Mantrac repair history", "machine": "Machine profile",
          "photo": "Photo analysis"}


def sources_html(turn: dict) -> str:
    """Where the answer came from (approved metrics, ranking, tables) and whether its figures were verified."""
    results = turn.get("results") or []
    if not results:
        return ""
    names = []
    for r in results:
        if r.get("tool") == "metric":
            names.append(f"Metric · {r.get('metric')}")
        elif r.get("tool") == "top_list":
            names.append("Daily top-100 ranking")
        elif r.get("tool") in SOURCE:
            names.append(SOURCE[r["tool"]])
            continue
        for ev in r.get("evidence") or []:
            for t in TABLE_RE.findall(ev.get("sql") or ""):
                names.append(t.replace("_", " ").title())
    names = list(dict.fromkeys(names))[:6]
    bad = turn.get("unverified") or []
    check = (f'<span class="ok">✓ every figure matches the query results</span>' if not bad else
             f'<span class="warn">⚠ not matched to the results: {theme.esc(", ".join(bad[:4]))}</span>')
    return ('<div class="si-sources">Sources ' + "".join(f'<span class="src">{theme.esc(n)}</span>' for n in names)
            + f' · {check}</div>')


def with_assumptions(text: str) -> str:
    """Lines the model marks 'Assumption:' are shown as a separate note, apart from the verified facts."""
    text = re.sub(r"(?im)^\s*(?:[-*]\s*)?\**assumption\**\s*:\s*\**\s*(.+)$", r"> **Assumption** · \1", text)
    return re.sub(r"(?im)^\s*(?:[-*]\s*)?\**general (?:cat )?knowledge\**\s*:\s*\**\s*(.+)$",
                  r"> **General knowledge** · \1", text)


def show_extras(turn: dict) -> None:
    if turn.get("table"):
        theme.table(turn["table"], max_height=360)
    try:
        show_videos(turn)
    except Exception:  # noqa: BLE001
        pass
    try:
        show_training_pictures(turn)
    except Exception:  # noqa: BLE001 - pictures are extra; the answer stands without them
        pass
    src = sources_html(turn)
    if src:
        theme.html_block(src)
    show_details(turn)


def render(turn: dict) -> None:
    with st.chat_message(turn["role"], avatar=AVATAR if turn["role"] == "assistant" and AVATAR is not None else None):
        if turn.get("images"):
            st.image(turn["images"], width=150)
        st.markdown(with_assumptions(turn["text"]) if turn["role"] == "assistant" else turn["text"])
        if turn["role"] == "assistant":
            show_extras(turn)


def typed(text: str):
    """Reveal the finished answer word by word, like a chat assistant."""
    pause = min(0.018, 2.5 / max(1, len(text.split())))
    for i, word in enumerate(text.split(" ")):
        yield word + " "
        if i % 2 == 0:
            time.sleep(pause)


def rules_fallback(question: str) -> dict:
    """Used only when Cortex is unavailable: the original rule-based assistant over the approved metrics."""
    plan = rules_plan(question, an, st.session_state["ctx"])
    res = answer(plan, an, store, lambda: all_candidates(an))
    if plan.intent == "metric":
        st.session_state["ctx"] = plan
    return {"text": res["text"], "table": res["table"], "results": [], "evidence": res["evidence"], "unverified": [],
            "kind": res["outcome"], "plan": plan.public(), "followups": res.get("followups") or []}


clicked = st.session_state.pop("chat_prefill", None)
if not st.session_state["chat"]:
    theme.ai_hero()
    _, mid, _ = st.columns([1, 5, 1])
    with mid:
        cols = st.columns(2)
        for i, (ic, color, ex) in enumerate(EXAMPLES):
            with cols[i % 2]:
                with theme.hit(f"ex_{i}"):
                    theme.html_block(theme.tile(ex, "", "", ic, color))
                    if st.button(ex, key=f"ex_{i}"):
                        clicked = ex

for turn in st.session_state["chat"]:
    render(turn)

last = st.session_state["chat"][-1] if st.session_state["chat"] else None
if last and last["role"] == "assistant" and last.get("followups") and not clicked:
    fcols = st.columns(len(last["followups"]))
    for i, (col, q) in enumerate(zip(fcols, last["followups"])):
        if col.button(q, key=f"fu_{len(st.session_state['chat'])}_{i}", use_container_width=True):
            clicked = q

files = []
if CHAT_FILES:
    got = st.chat_input("Ask VELA AI… or attach photos of a problem", accept_file="multiple", file_type=PHOTO_TYPES)
    question = (got.text if got else None) or clicked
    files = list(got.files) if got else []
else:                                                 # older Streamlit: a small uploader above the message bar
    up_key = f"photos_{st.session_state.get('photo_round', 0)}"
    with (st.popover("＋ Attach photos") if hasattr(st, "popover") else st.expander("＋ Attach photos")):
        st.file_uploader("Photos of the problem", type=PHOTO_TYPES, accept_multiple_files=True, key=up_key)
    question = st.chat_input("Ask VELA AI…") or clicked
    if question:
        files = list(st.session_state.get(up_key) or [])
        st.session_state["photo_round"] = st.session_state.get("photo_round", 0) + 1
if files and not question:
    question = "What is the problem in this photo and how do we fix it?"
if question:
    history = list(st.session_state["chat"])
    photo_paths, previews = save_photos(files) if files else ([], [])
    user_turn = {"role": "user", "text": question, "images": previews}
    st.session_state["chat"].append(user_turn)
    render(user_turn)
    with st.chat_message("assistant", avatar=AVATAR):
        out, planner = None, "cortex-agent"
        thinking = st.empty()

        def status(label: str) -> None:
            thinking.markdown(theme.thinking_html(label), unsafe_allow_html=True)
        status("Thinking…")
        try:
            out = ask(question, history, an, lambda msgs, n: cortex_complete(ui.session(), msgs, n),
                      lambda spec: ui.CachedRunner().run(spec, email), on_status=status, hints=known_values(),
                      search=manual_search, look=look, photos=photo_paths, parallel=ui.run_parallel,
                      web=web_search)
        except Exception:  # noqa: BLE001 - Cortex not available to this role/region: fall back to the rules
            planner = "rules"
            try:
                out = rules_fallback(question)
                out["text"] += "\n\n_(Cortex was not available, so I used the built-in question rules.)_"
            except Exception as exc:  # noqa: BLE001
                ui.show_error(exc)
        thinking.empty()
        if out is None:
            out = {"text": "I couldn't answer that; nothing was estimated.", "results": [], "evidence": [], "kind": "error"}
        if hasattr(st, "write_stream"):
            st.write_stream(typed(with_assumptions(out["text"])))
        else:
            st.markdown(with_assumptions(out["text"]))
        turn = {"role": "assistant", "text": out["text"], "table": out.get("table"), "results": out.get("results"),
                "unverified": out.get("unverified"), "data_note": out.get("data_note"), "followups": out.get("followups") or []}
        show_extras(turn)
    st.session_state["chat"].append(turn)
    try:
        steps = [{k: r.get(k) for k in ("tool", "metric_id", "purpose", "period", "list")} for r in out.get("results") or []]
        ui.session().sql("INSERT INTO SERVICE_INTELLIGENCE.SI_APP.CHAT_LOG (user_email, question, planner, plan, query_ids, outcome) "
                         "SELECT ?, ?, ?, PARSE_JSON(?), PARSE_JSON(?), ?",
                         params=[email, question[:2000], planner, json.dumps(out.get("plan") or steps, default=str),
                                 json.dumps([e.get("query_id") for e in out.get("evidence") or []]), out.get("kind")]).collect()
    except Exception:  # noqa: BLE001 - logging must not break the answer
        pass
    if turn["followups"]:
        st.rerun()                                     # show the follow-up suggestions under the new answer
theme.user_card()
