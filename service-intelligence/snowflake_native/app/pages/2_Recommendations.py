"""Page 2 — Recommendation Plan: evidence-backed candidates, saved plan, review workflow and audit trail."""
import streamlit as st

from si_native import ui
from si_native.plans import PlanError, PlanStore, allowed_moves, can
from si_native.recommend import SIGNAL_CLASSES, WEIGHTS, all_candidates, top_opportunity_candidate

ui.header("Recommendation plan", "Ranked findings from the metric registry. Measured facts and hypotheses are labelled separately.")
reg = ui.registry()
an = ui.analytics()
email = ui.user_email()
role = ui.app_role(email)
store = PlanStore(ui.session())
st.caption(f"Signed in as {email} · app role **{role}** · ranking = {WEIGHTS['impact']}×impact + "
           f"{WEIGHTS['urgency']}×urgency + {WEIGHTS['confidence']}×confidence")

tab_top, tab_new, tab_plan = st.tabs(["Top 100 machines today", "Fleet-health findings", "Saved plan"])

with tab_top:
    try:
        src_id = "daily_top_opportunities"
        fresh = an.freshness(src_id)
        if not fresh["latest_data"]:
            st.info("No ranking yet. It is built daily by SI_ADMIN.REFRESH_DAILY (or run "
                    "CALL SERVICE_INTELLIGENCE.SI_ADMIN.BUILD_DAILY_TOP_OPPORTUNITIES(100, 90)).")
        else:
            c1, c2, c3, c4 = st.columns(4)
            total = an.metric("top_opportunities_value", None)
            new = an.metric("top_opportunities_new_today", None)
            tel = an.metric("telemetry_only_opportunities", None)
            c1.metric("OLGA value due, top 100", ui.fmt(total["value"], "value"))
            c2.metric("New since yesterday", ui.fmt(new["value"], "machines"))
            c3.metric("Telemetry-only machines listed", ui.fmt(tel["value"], "machines"))
            c4.metric("Ranking date (UTC)", str(fresh["latest_data"])[:10])
            st.caption("Score (0–100) = 35 × OLGA value due (percentile) + 30 × urgency (due date re-timed from live "
                       "service-meter hours when the machine reports them, else OLGA's date) + 35 × condition (days with the "
                       "same HIGH-severity fault in the last 30). Rebuilt daily; earlier days are kept.")
            view = st.radio("Show", ["Top 100 overall", *SIGNAL_CLASSES], horizontal=True, key="top_view")
            f1, f2, f3 = st.columns(3)
            filters = {}
            for col, dim in zip((f1, f2, f3), ("branch", "product_family", "due_basis")):
                picked = col.multiselect(reg.label(dim), an.dimension_values(src_id, dim), key=f"top_{dim}")
                if picked:
                    filters[dim] = picked
            lst = an.top_list(src_id, filters, 100, None if view == "Top 100 overall" else view)
            rows = lst["rows"]
            if not rows:
                st.info("No machines match these filters.")
            else:
                table = ui.df(rows)
                st.dataframe(table, hide_index=True, use_container_width=True,
                             column_config={"due_value": st.column_config.NumberColumn("OLGA value due", format="%,.0f"),
                                            "score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.1f"),
                                            "is_new_today": st.column_config.CheckboxColumn("New today"),
                                            "reason": st.column_config.TextColumn("Why", width="large")})
                st.download_button("Download list (CSV)", table.to_csv(index=False).encode(),
                                   file_name=f"top_machines_{str(fresh['latest_data'])[:10]}.csv", mime="text/csv")
                ui.evidence_expander(lst["evidence"] + total["evidence"])
                if can(role, "analyst"):
                    picks = st.multiselect("Add to the plan (by serial)", [r["asset_serial"] for r in rows], key="top_pick")
                    if st.button("Save selected to plan", disabled=not picks):
                        for r in rows:
                            if r["asset_serial"] in picks:
                                try:
                                    rid = store.save(top_opportunity_candidate(r, lst["evidence"]), email, role, reg.version)
                                    st.success(f"{r['asset_serial']} saved as {rid}.")
                                except PlanError as exc:
                                    st.warning(f"{r['asset_serial']}: {exc}")
                else:
                    st.caption("Saving to the plan needs the analyst role.")
    except Exception as exc:  # noqa: BLE001
        ui.show_error(exc)

with tab_new:
    try:
        if st.button("Run detectors on the latest data", type="primary") or st.session_state.get("cands") is not None:
            if st.session_state.get("cands") is None:
                with st.spinner("Running detectors (registry queries only)…"):
                    st.session_state["cands"] = all_candidates(an)
            cands = st.session_state["cands"]
            if not cands:
                st.info("No finding passes the thresholds (at least 10 affected assets).")
            for i, c in enumerate(cands):
                with st.container(border=True):
                    st.markdown(f"### {c['priority']} · {c['title']}")
                    st.markdown(c["explanation"])
                    a, b, cc = st.columns(3)
                    a.metric("Score", c["score"])
                    b.metric("Affected assets", c["measured"]["assets"])
                    cc.metric("Seen in last 7 days", c["measured"]["assets_seen_last_7_days"])
                    st.markdown(f"**Problem:** {c['problem']}")
                    st.markdown(f"**Expected impact (hypothesis):** {c['expected_impact']}")
                    st.markdown("**Proposed actions:**\n" + "\n".join(f"{n}. {s}" for n, s in enumerate(c["actions"], 1)))
                    st.markdown(f"**Risks:** {c['risks']}  \n**Confidence:** {c['confidence']} · "
                                f"**Owner:** not assigned (the source has no owner data)")
                    st.caption("Score breakdown: " + ", ".join(f"{k} {v}" for k, v in c["score_breakdown"].items() if k != "weights"))
                    ui.evidence_expander(c["evidence"], "Evidence (same queries as the Analysis page)")
                    if can(role, "analyst"):
                        if st.button("Save to plan", key=f"save_{i}"):
                            try:
                                rid = store.save(c, email, role, reg.version)
                                st.success(f"Saved as {rid}.")
                            except PlanError as exc:
                                st.warning(str(exc))
                    else:
                        st.caption("Saving to the plan needs the analyst role (ask an admin to add you to SI_APP.APP_USERS).")
            if st.button("Re-run with fresh data"):
                st.session_state["cands"] = None
                st.cache_data.clear()
                st.rerun()
    except Exception as exc:  # noqa: BLE001
        ui.show_error(exc)

with tab_plan:
    try:
        rows = store.list()
        if not rows:
            st.info("The plan is empty. Save a finding from the first tab.")
        else:
            st.dataframe(ui.df(rows), hide_index=True, use_container_width=True)
            rid = st.selectbox("Open recommendation", [r["rec_id"] for r in rows])
            rec = store.get(rid)
            st.markdown(f"### {rec['rec_id']} · {rec['title']}")
            st.markdown(f"Status **{rec['status']}** · priority **{rec['priority']}** · score {rec['score']} · "
                        f"owner {rec.get('owner') or 'not assigned'} · data as of {rec['data_as_of']} UTC · "
                        f"{rec['analysis_version']} · created by {rec['created_by']} at {rec['created_at']}")
            st.markdown(rec.get("explanation") or "")
            st.markdown(f"**Expected impact (hypothesis):** {rec.get('expected_impact')}")
            ev = rec.get("evidence") or {}
            st.json(ev.get("measured") or {})
            ui.evidence_expander(ev.get("queries") or [])
            moves = allowed_moves(rec["status"], role)
            if moves:
                c1, c2 = st.columns([1, 2])
                to = c1.selectbox("Change status to", moves)
                note = c2.text_input("Note (required for reject / block)")
                if st.button("Apply"):
                    if to in ("rejected", "blocked") and not note.strip():
                        st.warning("Add a note explaining why.")
                    else:
                        try:
                            store.move(rid, to, email, role, rec["version"], note or None)
                            st.success(f"{rid} is now {to}.")
                            st.rerun()
                        except PlanError as exc:
                            st.warning(str(exc))
            if can(role, "approver"):
                owner = st.text_input("Assign owner (team or person)", value=rec.get("owner") or "")
                if st.button("Assign") and owner.strip():
                    try:
                        store.assign(rid, owner.strip(), email, role, rec["version"])
                        st.rerun()
                    except PlanError as exc:
                        st.warning(str(exc))
            if can(role, "analyst"):
                cm = st.text_input("Comment")
                if st.button("Add comment") and cm.strip():
                    store.comment(rid, email, role, cm.strip())
                    st.rerun()
            st.markdown("**Audit trail**")
            st.dataframe(ui.df(rec["history"]), hide_index=True, use_container_width=True)
    except Exception as exc:  # noqa: BLE001
        ui.show_error(exc)
