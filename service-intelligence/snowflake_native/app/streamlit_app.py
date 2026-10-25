"""Page 1 — Service Overview and Analysis (home page of the Streamlit-in-Snowflake app)."""
from datetime import timedelta

import streamlit as st

from si_native import theme, ui
from si_native.metrics import Period

ui.header("Service analysis", "", show_title=False)
reg = ui.registry()
an = ui.analytics()

SUBJECTS = {
    "Fleet health": ["high_severity_fault_assets", "repeat_high_fault_assets", "high_severity_fault_events",
                     "fault_assets", "assets_reporting_7d"],
    "Parts & service opportunity": ["open_opportunity_value", "opportunities_due_90d", "past_due_opportunity_value",
                                    "lost_opportunity", "capture_rate"],
}
STYLE = {"high_severity_fault_assets": ("alert", "pink"), "repeat_high_fault_assets": ("repeat", "orange"),
         "high_severity_fault_events": ("activity", "peach"), "fault_assets": ("truck", "sky"),
         "assets_reporting_7d": ("signal", "mint"), "open_opportunity_value": ("wallet", "orange"),
         "opportunities_due_90d": ("calendar", "sky"), "past_due_opportunity_value": ("clock", "pink"),
         "lost_opportunity": ("down", "peach"), "capture_rate": ("percent", "mint")}


def tone(change, direction):
    if not change or direction not in ("lower_is_better", "higher_is_better"):
        return "flat"
    return "good" if (change < 0) == (direction == "lower_is_better") else "bad"


def ask_from_box() -> None:
    q = (st.session_state.get("askbox") or "").strip()
    if q:
        st.session_state["askbox"] = ""
        st.session_state["chat_prefill"] = q
        st.session_state["si_page_next"] = "Assistant"


try:
    as_of = an.as_of("fault_events")
    p30 = Period(as_of.date() - timedelta(days=29), as_of.date() + timedelta(days=1))
    head = ui.run_parallel([
        lambda: an.metric("high_severity_fault_assets", p30, {}, compare=True),
        lambda: an.metric("repeat_high_fault_assets", p30, {}, compare=True),
        lambda: an.metric("high_severity_fault_events", p30, {}, compare=False),
        lambda: an.metric("assets_reporting_7d", None),
        lambda: an.metric("top_opportunities_value", None),
        lambda: an.metric("top_opportunities_new_today", None),
        lambda: an.top_list("daily_top_opportunities", {}, 100),
    ])
    hi, rep, ev, rp, topv, new, top = [None if isinstance(h, Exception) else h for h in head]

    # ---- hero: ask box + three headline numbers -------------------------------------------------------
    a, b, c, d = st.columns([1.5, 1, 1, 1])
    with a:
        with st.container(key="askbox_wrap") if theme._keyed() else st.container():
            st.text_input("Ask", key="askbox", placeholder="Ask anything…", label_visibility="collapsed", on_change=ask_from_box)
    hero = [(b, "Machines with HIGH faults · 30 days", hi, "high_severity_fault_assets", "assets"),
            (c, "OLGA value due · today's top 100", topv, "top_opportunities_value", "value"),
            (d, "Machines reporting · 7 days", rp, "assets_reporting_7d", "assets")]
    for i, (col, label, r, mid, unit) in enumerate(hero):
        with col:
            if r is None:
                theme.html_block(theme.big_kpi(label, "—"))
                continue
            delta = f"{r['change']:+.0%}" if r.get("change") is not None else None
            theme.html_block(theme.big_kpi(label, ui.fmt(r["value"], unit), delta, tone(r.get("change"), reg.metric(mid).direction),
                                           r["period"].label if r.get("period") else "latest data", i * 120))

    # ---- card row: gauge · orange ranking card · assistant · top branch ---------------------------------
    rows = top["rows"] if top else []
    branch_val = {}
    for r in rows:
        branch_val[r.get("branch") or "(not set)"] = branch_val.get(r.get("branch") or "(not set)", 0) + float(r.get("due_value") or 0)
    best_branch = max(branch_val.items(), key=lambda x: x[1]) if branch_val else ("—", 0)

    c1, c2, c3, c4 = st.columns([1.7, 1, 1, 1], gap="small")
    with c1:
        with theme.hit("card_speedo"):
            hv, rv = (hi or {}).get("value"), (rep or {}).get("value")
            ratio = float(rv) / float(hv) if hv and rv is not None else None
            theme.html_block(theme.speedo("Repeat faults", ratio, ui.fmt(rv, "assets"), "Machines with a HIGH fault on 3+ days",
                                          f"{ratio:.0%} of HIGH" if ratio is not None else "—", bool(ratio and ratio > .3),
                                          f"of {ui.fmt(hv, 'assets')} machines with HIGH faults · last 30 days",
                                          [("HIGH fault events", ui.fmt((ev or {}).get("value"), "events")),
                                           ("reporting this week", ui.fmt((rp or {}).get("value"), "assets"))]))
            if st.button("Open details", key="open_speedo"):
                ui.open_dialog("Assets with repeat high-severity faults", ui.metric_detail, "repeat_high_fault_assets", an, p30, {})
    with c2:
        with theme.hit("card_orange"):
            theme.html_block(theme.orange_card("Top 100 machines today", "Open the ranking", ui.fmt((topv or {}).get("value"), "value"),
                                               f"OLGA value due\n· {ui.fmt((new or {}).get('value'), 'machines')} new today"))
            if st.button("Open ranking", key="open_orange"):
                ui.go_to("Recommendations")
    with c3:
        with theme.hit("card_note"):
            theme.html_block(theme.note_card("Ask the assistant", "Live data", "Which machines should we act on first today, and why?",
                                             "Cortex · read-only"))
            if st.button("Ask", key="open_note"):
                ui.go_to("Assistant", chat_prefill="Which machines should we act on first today, and why?")
    with c4:
        with theme.hit("card_map"):
            theme.html_block(theme.map_card("Top branch on today's list", ui.fmt(best_branch[1], "value"),
                                            f"{best_branch[0]} · OLGA value due"))
            if st.button("Open branch", key="open_map") and best_branch[0] != "—":
                ui.go_to("Recommendations", top_branch=[best_branch[0]])

    # ---- second row: trend bars · due this week -------------------------------------------------------------
    left, right = st.columns([1.15, 1.6], gap="medium")
    with left:
        with theme.card("bars"):
            t1, t2 = st.columns([1.6, 1])
            period_metrics = [m.id for m in reg.metrics.values() if m.kind == "period"]
            mid = t1.selectbox("Trend", period_metrics, format_func=lambda i: reg.metric(i).name, key="trend_metric",
                               label_visibility="collapsed")
            months = t2.selectbox("Range", [3, 6, 12], index=1, format_func=lambda n: f"Last {n} months", key="trend_range",
                                  label_visibility="collapsed")
            src = reg.source_of(reg.metric(mid))
            end = an.as_of(src.id).date() + timedelta(days=1)
            per = Period((end - timedelta(days=31 * months)).replace(day=1), end)
            s = an.series(mid, per, "month", {})
            if s["rows"]:
                df = ui.df(s["rows"])[["bucket", "value"]]
                df["bucket"] = [str(x)[:7] for x in df["bucket"]]
                theme.bars(df, "bucket", "value", height=250)
                st.caption("By month (UTC); the latest month can be partial.")
            else:
                st.info("No data in this range.")
    with right:
        with theme.card("week"):
            theme.section("Due this week", "machines on today's top 100 by due date")
            today = as_of.date()
            days = []
            for k in range(8):
                day = today + timedelta(days=k)
                n = sum(1 for r in rows if str(r.get("earliest_due") or "")[:10] == day.isoformat())
                days.append((f"{day:%a}, {day:%d}", n, k == 0))
            overdue = sum(1 for r in rows if r.get("days_to_due") is not None and float(r["days_to_due"]) < 0)
            nxt = None
            if rows:
                r0 = rows[0]
                due = r0.get("days_to_due")
                nxt = (f"Next up: {r0.get('model')} · {r0.get('asset_serial')}",
                       str(r0.get("reason") or "")[:140],
                       [f"Score {float(r0.get('score') or 0):.0f}", str(r0.get("branch") or "—"),
                        (f"Overdue {abs(int(float(due)))}d" if due is not None and float(due) < 0 else "Due soon"),
                        f"{overdue} overdue on the list"])
            theme.week(days, nxt)

    # ---- explore: every metric, clickable --------------------------------------------------------------------
    st.write("")
    e1, e2, e3, e4 = st.columns([2.3, 2, 1.3, .7])
    with e1:
        subject = st.radio("Subject", list(SUBJECTS), horizontal=True, label_visibility="collapsed")
    metric_ids = SUBJECTS[subject]
    with e2:
        focus_id = st.selectbox("Metric in focus", metric_ids, format_func=lambda i: reg.metric(i).name)
    focus = reg.metric(focus_id)
    src = reg.source_of(focus)
    with e3:
        period = ui.period_picker("analysis", an.as_of(src.id).date(), 30 if subject == "Fleet health" else 90)
    with e4:
        st.write("")
        show_filters = getattr(st, "toggle", st.checkbox)("Filters", key="show_filters")
    filters = {}
    if show_filters:
        with theme.card("filters"):
            fcols = st.columns(4)
            dims = src.dimensions[:4]
            values = ui.run_parallel([lambda d=d: an.dimension_values(src.id, d) for d in dims])
            for col, dim, options in zip(fcols, dims, values):
                picked = col.multiselect(reg.label(dim), [] if isinstance(options, Exception) else options, key=f"f_{src.id}_{dim}")
                if picked:
                    filters[dim] = picked

    results = ui.run_parallel([lambda m=m: an.metric(m, period, filters if reg.metric(m).source == src.id else {},
                                                     compare=reg.metric(m).kind == "period") for m in metric_ids])
    cols = st.columns(5)
    for k, (col, m_id, r) in enumerate(zip(cols, metric_ids, results)):
        m = reg.metric(m_id)
        ic, color = STYLE.get(m_id, ("activity", "orange"))
        with col:
            with theme.hit(f"tile_{m_id}"):
                if isinstance(r, Exception):
                    theme.html_block(theme.tile(m.name, "—", str(r)[:60], ic, color))
                else:
                    delta = f"{r['change']:+.1%}" if r["change"] is not None else None
                    theme.html_block(theme.tile(m.name, ui.fmt(r["value"], m.unit), period.label if m.kind == "period" else "latest snapshot",
                                                ic, color, delta, tone(r["change"], m.direction), m.description, "Details"))
                if st.button("Open details", key=f"open_{m_id}"):
                    ui.open_dialog(m.name, ui.metric_detail, m_id, an, period, filters if m.source == src.id else {})

    res = results[metric_ids.index(focus_id)]
    if isinstance(res, Exception):
        raise res
    for note in res["notes"]:
        st.warning(note)
    with theme.card("breakdown"):
        h1, h2 = st.columns([3, 1])
        with h2:
            dim = st.selectbox("Break down by", src.dimensions, format_func=reg.label)
        with h1:
            theme.section(f"{focus.name} by {reg.label(dim).lower()}", f"{ui.fmt(res['value'], focus.unit)} in total · {res['n']:,} records")
        b = an.breakdown(focus_id, dim, period, filters, limit=15)
        if b["rows"]:
            g1, g2 = st.columns([1, 1.2], gap="medium")
            with g1:
                bd = ui.df(b["rows"])
                bd["segment"] = bd["segment"].astype(str)
                theme.bar_chart(bd.head(8), "segment", "value", height=300)
            with g2:
                theme.table([{"segment": r["segment"], "value": r["value"], "n": r["n"]} for r in b["rows"]],
                            [("segment", reg.label(dim), "avatar"), ("value", focus.name, "num1" if focus.unit == "ratio" else "num"),
                             ("n", "Records", "num")], max_height=320)
            if focus.expression.startswith("COUNT(DISTINCT"):
                st.caption("Distinct counts per segment can add up to more than the total.")
        else:
            st.info("No data for this breakdown.")

    f = an.freshness(src.id)
    st.caption(f"Source: {f['label']} · latest data {f['latest_data']} · loaded {f['loaded_at'] or 'n/a'} (UTC) · registry v{reg.version}")
    with st.expander(f"Drill down: records behind {focus.name}"):
        if st.button("Load the records", key="load_drill"):
            dd = an.drilldown(focus_id, period, filters, limit=200)
            if dd["rows"]:
                theme.table(dd["rows"], max_height=420)
                if dd["truncated"]:
                    st.caption("Showing the first 200 rows; narrow the filters to see the rest.")
            else:
                st.info("No records.")
    with st.expander("Definition and limitations"):
        st.markdown(f"**Definition:** {focus.description}  \n**Calculation:** `{focus.expression}`"
                    + (f"  \n**Records included:** `{focus.filter}`" if focus.filter else "")
                    + f"  \n**Kind:** {focus.kind} · **Unit:** {focus.unit} · **Source:** `{src.table}`")
        for lim in focus.limitations:
            st.markdown(f"- {lim}")
    ui.evidence_expander(res["evidence"] + b["evidence"])
except Exception as exc:  # noqa: BLE001
    ui.show_error(exc)
