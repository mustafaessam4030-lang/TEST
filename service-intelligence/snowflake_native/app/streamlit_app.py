"""Page 1 — Service Overview and Analysis (home page of the Streamlit-in-Snowflake app)."""
import streamlit as st

from si_native import theme, ui

ui.header("Service analysis", "Fleet health from Cat telematics and parts & service opportunity from OLGA.")
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


try:
    c1, c2, c3 = st.columns([2.3, 2, 1.3])
    with c1:
        subject = st.radio("Subject", list(SUBJECTS), horizontal=True, label_visibility="collapsed")
    metric_ids = SUBJECTS[subject]
    with c2:
        focus_id = st.selectbox("Metric in focus", metric_ids, format_func=lambda i: reg.metric(i).name)
    focus = reg.metric(focus_id)
    src = reg.source_of(focus)
    as_of = an.as_of(src.id)
    with c3:
        period = ui.period_picker("analysis", as_of.date(), 30 if subject == "Fleet health" else 90)

    filters = {}
    with st.expander("Filters", expanded=False):
        fcols = st.columns(4)
        for col, dim in zip(fcols, src.dimensions[:4]):
            try:
                options = an.dimension_values(src.id, dim)
            except Exception as exc:  # noqa: BLE001
                ui.show_error(exc)
                options = []
            picked = col.multiselect(reg.label(dim), options, key=f"f_{src.id}_{dim}")
            if picked:
                filters[dim] = picked

    # every metric of the subject; those sharing the focus metric's source honour the filters
    vals, cards = {}, []
    for mid in metric_ids:
        m = reg.metric(mid)
        ic, color = STYLE.get(mid, ("activity", "orange"))
        try:
            r = an.metric(mid, period, filters if m.source == src.id else {}, compare=(m.kind == "period"))
            vals[mid] = r["value"]
            delta = f"{r['change']:+.1%}" if r["change"] is not None else None
            sub = period.label if m.kind == "period" else "latest snapshot"
            cards.append(theme.tile(m.name, ui.fmt(r["value"], m.unit), sub, ic, color, delta, tone(r["change"], m.direction),
                                    m.description))
        except Exception as exc:  # noqa: BLE001
            vals[mid] = None
            cards.append(theme.tile(m.name, "—", str(exc)[:60], ic, color))

    g, t = st.columns([1.05, 2], gap="medium")
    with g:
        with theme.card("gauge"):
            if subject == "Fleet health":
                hi, rep = vals.get("high_severity_fault_assets"), vals.get("repeat_high_fault_assets")
                ratio = float(rep) / float(hi) if hi and rep is not None else None
                theme.section("Repeat faults", period.label)
                theme.gauge(ratio, f"{ratio:.0%}" if ratio is not None else "—", "repeat their fault",
                            [(theme.RAIL, "Assets with HIGH faults", ui.fmt(hi, "assets")),
                             (theme.ACCENT, "…repeating (3+ days)", ui.fmt(rep, "assets")),
                             ("#D4D4D8", "Assets reporting (7 days)", ui.fmt(vals.get("assets_reporting_7d"), "assets"))])
            else:
                cr = vals.get("capture_rate")
                theme.section("Capture rate", period.label)
                theme.gauge(float(cr) if cr is not None else None, ui.fmt(cr, "ratio"), "captured",
                            [(theme.RAIL, "Open opportunity value", ui.fmt(vals.get("open_opportunity_value"), "value")),
                             (theme.ACCENT, "Past due value", ui.fmt(vals.get("past_due_opportunity_value"), "value")),
                             ("#D4D4D8", "Lost opportunity", ui.fmt(vals.get("lost_opportunity"), "value"))])
    with t:
        theme.section(subject, f"{len(metric_ids)} metrics · hover a tile for its definition")
        theme.tiles(cards)

    res = an.metric(focus_id, period, filters, compare=focus.kind == "period")
    for note in res["notes"]:
        st.warning(note)
    left, right = st.columns([3, 2], gap="medium")
    with left:
        with theme.card("trend"):
            if focus.kind == "period":
                grain = "month" if focus.grain == "month" or (period.end - period.start).days > 120 else "week"
                theme.section(f"{focus.name} over time", f"by {grain} · first and last buckets can be partial")
                s = an.series(focus_id, period, grain, filters)
                if s["rows"]:
                    theme.area_chart(ui.df(s["rows"])[["bucket", "value"]], "bucket", "value", height=300)
                else:
                    st.info("No data in this period.")
            else:
                f = an.freshness(src.id)
                theme.section(focus.name, "snapshot")
                theme.kv({"Value": ui.fmt(res["value"], focus.unit), "Records": f"{res['n']:,}",
                          "Latest data": str(f["latest_data"])[:16], "Loaded": str(f["loaded_at"] or "n/a")[:16]})
    with right:
        with theme.card("breakdown"):
            dim = st.selectbox("Break down by", src.dimensions, format_func=reg.label)
            b = an.breakdown(focus_id, dim, period, filters, limit=15)
            if b["rows"]:
                bd = ui.df(b["rows"])
                bd["segment"] = bd["segment"].astype(str)
                theme.bar_chart(bd.head(8), "segment", "value", height=240)
            else:
                st.info("No data for this breakdown.")

    if b["rows"]:
        with theme.card("table"):
            theme.section(f"{focus.name} by {reg.label(dim).lower()}", f"{ui.fmt(res['value'], focus.unit)} in total · "
                          f"{res['n']:,} records")
            theme.table([{"segment": r["segment"], "value": r["value"], "n": r["n"]} for r in b["rows"]],
                        [("segment", reg.label(dim), "avatar"), ("value", focus.name, "num1" if focus.unit == "ratio" else "num"),
                         ("n", "Records", "num")], max_height=420)
            if focus.expression.startswith("COUNT(DISTINCT"):
                st.caption("Distinct counts per segment can add up to more than the total.")

    f = an.freshness(src.id)
    st.caption(f"Source: {f['label']} · latest data {f['latest_data']} · loaded {f['loaded_at'] or 'n/a'} (UTC)")
    with st.expander(f"Drill down: records behind {focus.name}"):
        d = an.drilldown(focus_id, period, filters, limit=200)
        if d["rows"]:
            theme.table(d["rows"], max_height=420)
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
theme.user_card()
