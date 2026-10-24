"""Page 1 — Service Overview and Analysis (home page of the Streamlit-in-Snowflake app)."""
import streamlit as st

from si_native import ui

ui.header("Service analysis", "Fleet health from Cat telematics and parts & service opportunity from OLGA.")
reg = ui.registry()
an = ui.analytics()

SUBJECTS = {
    "Fleet health": ["high_severity_fault_assets", "repeat_high_fault_assets", "high_severity_fault_events",
                     "fault_assets", "assets_reporting_7d"],
    "Parts & service opportunity": ["open_opportunity_value", "opportunities_due_90d", "past_due_opportunity_value",
                                    "lost_opportunity", "capture_rate"],
}

try:
    subject = st.radio("Subject", list(SUBJECTS), horizontal=True)
    metric_ids = SUBJECTS[subject]
    focus_id = st.selectbox("Metric in focus", metric_ids, format_func=lambda i: reg.metric(i).name)
    focus = reg.metric(focus_id)
    src = reg.source_of(focus)
    as_of = an.as_of(src.id)

    with st.sidebar:
        st.subheader("Filters")
        period = ui.period_picker("analysis", as_of.date(), 30 if subject == "Fleet health" else 90)
        filters = {}
        for dim in src.dimensions[:4]:
            try:
                options = an.dimension_values(src.id, dim)
            except Exception as exc:  # noqa: BLE001
                ui.show_error(exc)
                options = []
            picked = st.multiselect(reg.label(dim), options, key=f"f_{src.id}_{dim}")
            if picked:
                filters[dim] = picked
        st.caption(f"Signed in as {ui.user_email()} · app role **{ui.app_role(ui.user_email())}**")

    f = an.freshness(src.id)
    st.caption(f"**Source:** {f['label']} · latest data **{f['latest_data']}** · loaded **{f['loaded_at'] or 'n/a'}** (UTC) · "
               f"reporting period **{period.label}**")

    # KPI cards: every metric of the subject that shares the focus metric's source honours the filters
    cols = st.columns(len(metric_ids))
    for col, mid in zip(cols, metric_ids):
        m = reg.metric(mid)
        use_filters = filters if m.source == src.id else {}
        try:
            r = an.metric(mid, period, use_filters, compare=(m.kind == "period"))
            delta = f"{r['change']:+.1%} vs previous" if r["change"] is not None else None
            col.metric(m.name, ui.fmt(r["value"], m.unit), delta,
                       delta_color="inverse" if m.direction == "lower_is_better" else
                       "normal" if m.direction == "higher_is_better" else "off",
                       help=m.description + (" · as of latest data" if m.kind == "snapshot" else ""))
        except Exception as exc:  # noqa: BLE001
            col.metric(m.name, "—")
            col.caption(str(exc)[:120])

    st.divider()
    res = an.metric(focus_id, period, filters, compare=focus.kind == "period")
    left, right = st.columns([3, 2])
    with left:
        st.subheader(focus.name)
        st.markdown(f"**{ui.fmt(res['value'], focus.unit)}** · {res['n']:,} records · {focus.description}")
        for note in res["notes"]:
            st.warning(note)
        if focus.kind == "period":
            grain = "month" if focus.grain == "month" or (period.end - period.start).days > 120 else "week"
            s = an.series(focus_id, period, grain, filters)
            if s["rows"]:
                data = ui.df(s["rows"]).set_index("bucket")[["value"]].rename(columns={"value": focus.name})
                st.line_chart(data)
                st.caption(f"By {s['grain']} (UTC). The first and last buckets can be partial.")
            else:
                st.info("No data in this period.")
    with right:
        dim = st.selectbox("Break down by", src.dimensions, format_func=reg.label)
        b = an.breakdown(focus_id, dim, period, filters, limit=15)
        if b["rows"]:
            bd = ui.df(b["rows"]).rename(columns={"segment": reg.label(dim), "value": focus.name, "n": "records"})
            st.bar_chart(bd.set_index(reg.label(dim))[[focus.name]])
            st.dataframe(bd, hide_index=True, use_container_width=True)
            if focus.expression.startswith("COUNT(DISTINCT"):
                st.caption("Distinct counts per segment can add up to more than the total.")
        else:
            st.info("No data for this breakdown.")

    with st.expander(f"Drill down: records behind {focus.name}"):
        d = an.drilldown(focus_id, period, filters, limit=200)
        if d["rows"]:
            st.dataframe(ui.df(d["rows"]), hide_index=True, use_container_width=True)
            if d["truncated"]:
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
