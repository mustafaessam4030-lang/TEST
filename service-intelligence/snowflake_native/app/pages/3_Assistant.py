"""Page 3 — AI Service Assistant: questions answered with the same registry metrics as the dashboard."""
import json

import streamlit as st

from si_native import ui
from si_native.assistant import answer, cortex_plan, rules_plan
from si_native.plans import PlanStore
from si_native.recommend import all_candidates

ui.header("Service assistant", "Ask about fleet health and opportunity. Answers come only from approved metrics; "
          "every figure links to its query.")
an = ui.analytics()
email = ui.user_email()
store = PlanStore(ui.session())
use_cortex = st.sidebar.toggle("Use Cortex to interpret free-form questions", value=False,
                               help="Off: rule-based interpretation only. On: if the rules cannot match a metric, "
                                    "Cortex (claude-sonnet-4-6) proposes one; it is validated before use. Costs Cortex credits.")
st.sidebar.caption("Conversation context lasts for this browser session only.")

if "chat" not in st.session_state:
    st.session_state["chat"] = []
    st.session_state["ctx"] = None

examples = ["Assets with high-severity faults last month by product family",
            "Compare repeat faults with the previous period",
            "What are the strongest data-supported opportunities for improvement?",
            "Lost opportunity this year by branch",
            "What data is missing before we can answer reliably?"]
cols = st.columns(len(examples))
clicked = None
for c, ex in zip(cols, examples):
    if c.button(ex, use_container_width=True):
        clicked = ex

for turn in st.session_state["chat"]:
    with st.chat_message(turn["role"]):
        st.markdown(turn["text"])
        if turn.get("table"):
            st.dataframe(ui.df(turn["table"]), hide_index=True)

question = st.chat_input("Ask a question about service performance") or clicked
if question:
    st.session_state["chat"].append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        outcome, plan, query_ids = "error", None, []
        try:
            plan = rules_plan(question, an, st.session_state["ctx"])
            if plan.intent == "help" and use_cortex:
                plan = cortex_plan(ui.session(), question, an) or plan
            res = answer(plan, an, store, lambda: all_candidates(an))
            outcome = res["outcome"]
            query_ids = [e.get("query_id") for e in res["evidence"]]
            st.markdown(res["text"])
            if res["table"]:
                st.dataframe(ui.df(res["table"]), hide_index=True)
            for n in res["notes"]:
                st.caption(n)
            if res["definitions"] or res["limitations"]:
                with st.expander("Definitions, filters and limitations"):
                    for d in res["definitions"]:
                        st.markdown(f"**{d['metric']}** — {d['definition']}  \nSource: {d['source']} · "
                                    f"`{d['expression']}`" + (f" where `{d['filter']}`" if d["filter"] else ""))
                    st.markdown(f"Interpreted as: `{json.dumps(res['plan'])}`")
                    for lim in res["limitations"]:
                        st.markdown(f"- {lim}")
            if res["evidence"]:
                ui.evidence_expander(res["evidence"])
            if res["followups"]:
                st.caption("Try next: " + " · ".join(res["followups"]))
            st.session_state["chat"].append({"role": "assistant", "text": res["text"], "table": res["table"]})
            if plan.intent == "metric":
                st.session_state["ctx"] = plan
        except Exception as exc:  # noqa: BLE001
            ui.show_error(exc)
            st.session_state["chat"].append({"role": "assistant", "text": "I could not answer that; nothing was estimated."})
        try:
            ui.session().sql("INSERT INTO SERVICE_INTELLIGENCE.SI_APP.CHAT_LOG (user_email, question, planner, plan, query_ids, outcome) "
                             "SELECT ?, ?, ?, PARSE_JSON(?), PARSE_JSON(?), ?",
                             params=[email, question[:2000], plan.planner if plan else None,
                                     json.dumps(plan.public() if plan else None), json.dumps(query_ids), outcome]).collect()
        except Exception:  # noqa: BLE001 - logging must not break the answer
            pass
