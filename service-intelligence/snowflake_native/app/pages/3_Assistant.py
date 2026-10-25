"""Page 3 — AI Service Assistant: a conversational assistant (Cortex) that answers from live, read-only queries."""
import json

import streamlit as st

from si_native import theme, ui
from si_native.agent import MODEL, ask, cortex_complete
from si_native.assistant import answer, rules_plan
from si_native.plans import PlanStore
from si_native.recommend import all_candidates

ui.header("Service assistant", "Ask anything about fleet health, faults, machines and opportunities. Answers come from live data.",
          [f"Model <b>{MODEL}</b>"])
an = ui.analytics()
email = ui.user_email()
store = PlanStore(ui.session())

if "chat" not in st.session_state:
    st.session_state["chat"] = []
    st.session_state["ctx"] = None
with st.sidebar:
    if st.button("＋", help="New chat"):
        st.session_state["chat"] = []
        st.session_state["ctx"] = None
        st.rerun()

EXAMPLES = ["Which 10 machines should we act on first today, and why?",
            "How many machines had high-severity faults last month, by product family?",
            "Which branches have the most OLGA value overdue?",
            "What are the most common repeat faults on large off-highway trucks?"]


def show_details(turn: dict) -> None:
    results = turn.get("results") or []
    if not results:
        return
    with st.expander("How I got this"):
        for r in results:
            title = r.get("metric") or r.get("purpose") or r.get("list") or r.get("tool")
            st.markdown(f"**{title}** · " + ("approved metric" if r.get("tool") == "metric" else
                                             "daily ranking" if r.get("tool") == "top_list" else "ad-hoc read-only query"))
            if r.get("tool") == "metric":
                theme.kv({"Value": r.get("value"), "Period": r.get("period"), "Previous": r.get("previous_value"),
                          "Change %": r.get("change_pct"), "Records": r.get("records")})
            if r.get("rows"):
                theme.table(r["rows"][:200], max_height=300)
                if r.get("truncated"):
                    st.caption("Only the first 200 rows were read.")
        ui.evidence_expander([e for r in results for e in r.get("evidence", [])], "SQL and query IDs")


def render(turn: dict) -> None:
    with st.chat_message(turn["role"]):
        st.markdown(turn["text"])
        if turn.get("table"):
            theme.table(turn["table"], max_height=360)
        if turn.get("unverified"):
            st.caption("⚠️ I could not match these figures to the query results, so treat them with care: "
                       + ", ".join(turn["unverified"][:6]))
        show_details(turn)


def rules_fallback(question: str) -> dict:
    """Used only when Cortex is unavailable: the original rule-based assistant over the approved metrics."""
    plan = rules_plan(question, an, st.session_state["ctx"])
    res = answer(plan, an, store, lambda: all_candidates(an))
    if plan.intent == "metric":
        st.session_state["ctx"] = plan
    return {"text": res["text"], "table": res["table"], "results": [], "evidence": res["evidence"], "unverified": [],
            "kind": res["outcome"], "plan": plan.public()}


if not st.session_state["chat"]:
    theme.cover("AI assistant · live Snowflake data", "Mantrac service assistant",
                "Ask in your own words. I look up faults, machine hours, OLGA opportunities, the daily top-100 ranking "
                "and lost business, then explain what it means. Every figure links to the query behind it.",
                [("Cortex", "model"), ("Read-only", "queries")], "sparkle")
    theme.html_block('<div class="si-welcome"><h2>How can I help you today?</h2></div>')
clicked = None
if not st.session_state["chat"]:
    _, mid, _ = st.columns([1, 4, 1])
    with mid:
        g1, g2 = st.columns(2)
        for i, ex in enumerate(EXAMPLES):
            if (g1 if i % 2 == 0 else g2).button(ex, key=f"ex_{i}", use_container_width=True):
                clicked = ex

for turn in st.session_state["chat"]:
    render(turn)

question = st.chat_input("Message the service assistant…") or clicked
if question:
    history = list(st.session_state["chat"])
    st.session_state["chat"].append({"role": "user", "text": question})
    render({"role": "user", "text": question})
    with st.chat_message("assistant"):
        out, planner = None, "cortex-agent"
        status = st.status("Thinking…", expanded=False) if hasattr(st, "status") else None
        try:
            out = ask(question, history, an, lambda msgs, n: cortex_complete(ui.session(), msgs, n),
                      lambda spec: ui.CachedRunner().run(spec, email),
                      on_status=(lambda s: status.update(label=s)) if status else (lambda s: None))
        except Exception:  # noqa: BLE001 - Cortex not available to this role/region: fall back to the rules
            planner = "rules"
            try:
                out = rules_fallback(question)
                out["text"] += "\n\n_(Cortex was not available, so I used the built-in question rules.)_"
            except Exception as exc:  # noqa: BLE001
                ui.show_error(exc)
        if status:
            status.update(label="Done", state="complete")
        if out is None:
            out = {"text": "I couldn't answer that; nothing was estimated.", "results": [], "evidence": [], "kind": "error"}
        st.markdown(out["text"])
        if out.get("table"):
            theme.table(out["table"], max_height=360)
        if out.get("unverified"):
            st.caption("⚠️ I could not match these figures to the query results, so treat them with care: "
                       + ", ".join(out["unverified"][:6]))
        turn = {"role": "assistant", "text": out["text"], "table": out.get("table"), "results": out.get("results"),
                "unverified": out.get("unverified"), "data_note": out.get("data_note")}
        show_details(turn)
    st.session_state["chat"].append(turn)
    try:
        steps = [{k: r.get(k) for k in ("tool", "metric_id", "purpose", "period", "list")} for r in out.get("results") or []]
        ui.session().sql("INSERT INTO SERVICE_INTELLIGENCE.SI_APP.CHAT_LOG (user_email, question, planner, plan, query_ids, outcome) "
                         "SELECT ?, ?, ?, PARSE_JSON(?), PARSE_JSON(?), ?",
                         params=[email, question[:2000], planner, json.dumps(out.get("plan") or steps, default=str),
                                 json.dumps([e.get("query_id") for e in out.get("evidence") or []]), out.get("kind")]).collect()
    except Exception:  # noqa: BLE001 - logging must not break the answer
        pass
theme.user_card()
