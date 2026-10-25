"""Page 3 — VELA AI: a service intelligence analyst (Cortex) that answers from live, read-only queries."""
import json
import re
import time

import streamlit as st

from si_native import theme, ui
from si_native.agent import MODEL, ask, cortex_complete
from si_native.assistant import answer, rules_plan
from si_native.plans import PlanStore
from si_native.recommend import all_candidates

ui.header("VELA AI", "Your service intelligence analyst: answers from authorized Snowflake data, with the evidence behind them.",
          [f"Model <b>{MODEL}</b>"])
AVATAR = theme.ai_icon()
an = ui.analytics()
email = ui.user_email()
store = PlanStore(ui.session())

if "chat" not in st.session_state:
    st.session_state["chat"] = []
    st.session_state["ctx"] = None
if st.session_state["chat"]:
    _, nc = st.columns([6, 1])
    if nc.button("＋ New chat", key="new_chat", use_container_width=True):
        st.session_state["chat"] = []
        st.session_state["ctx"] = None
        st.rerun()

EXAMPLES = [("target", "orange", "Which 10 machines should we act on first today, and why?"),
            ("alert", "pink", "Which product families had the most high-severity faults last month?"),
            ("wallet", "sky", "Which branches have the most OLGA value overdue?"),
            ("repeat", "mint", "What are the most common repeat faults on large off-highway trucks?")]


@st.cache_data(ttl=3600, show_spinner=False)
def known_values() -> dict:
    """Exact names the model can filter on (branches, product families, ...), so informal wording still matches."""
    wanted = {"branch": ("opportunities", "branch"), "product_family": ("fault_events", "product_family"),
              "smcs_group": ("opportunities", "smcs_group"), "dealer_code": ("fault_events", "dealer_code")}
    got = ui.run_parallel([lambda s=s, d=d: an.dimension_values(s, d) for s, d in wanted.values()])
    return {k: v[:150] for k, v in zip(wanted, got) if not isinstance(v, Exception)}


def show_details(turn: dict) -> None:
    results = turn.get("results") or []
    if not results:
        return
    with st.expander("How I got this"):
        for r in results:
            title = r.get("metric") or r.get("purpose") or r.get("list") or r.get("tool")
            st.markdown(f"**{title}** · " + ("approved metric" if r.get("tool") == "metric" else
                                             "daily ranking" if r.get("tool") == "top_list" else "read-only query"))
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
    return re.sub(r"(?im)^\s*(?:[-*]\s*)?\**assumption\**\s*:\s*\**\s*(.+)$", r"> **Assumption** · \1", text)


def show_extras(turn: dict) -> None:
    if turn.get("table"):
        theme.table(turn["table"], max_height=360)
    src = sources_html(turn)
    if src:
        theme.html_block(src)
    show_details(turn)


def render(turn: dict) -> None:
    with st.chat_message(turn["role"], avatar=AVATAR if turn["role"] == "assistant" and AVATAR is not None else None):
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

question = st.chat_input("Ask VELA AI…") or clicked
if question:
    history = list(st.session_state["chat"])
    st.session_state["chat"].append({"role": "user", "text": question})
    render({"role": "user", "text": question})
    with st.chat_message("assistant", avatar=AVATAR):
        out, planner = None, "cortex-agent"
        thinking = st.empty()

        def status(label: str) -> None:
            thinking.markdown(theme.thinking_html(label), unsafe_allow_html=True)
        status("Analyzing authorized Snowflake data…")
        try:
            out = ask(question, history, an, lambda msgs, n: cortex_complete(ui.session(), msgs, n),
                      lambda spec: ui.CachedRunner().run(spec, email), on_status=status, hints=known_values())
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
