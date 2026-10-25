"""Page 2 — Recommendation Plan: daily top-100 machines, fleet-health findings, saved plan and audit trail."""
import streamlit as st

from si_native import theme, ui
from si_native.plans import PlanError, PlanStore, allowed_moves, can
from si_native.recommend import SIGNAL_CLASSES, all_candidates, top_opportunity_candidate

ui.header("Recommendation plan", "The machines to act on first, rebuilt every day from OLGA and live telemetry.")
reg = ui.registry()
an = ui.analytics()
email = ui.user_email()
role = ui.app_role(email)
store = PlanStore(ui.session())

tab_top, tab_new, tab_plan = st.tabs(["Top 100 machines today", "Fleet-health findings", "Saved plan"])

TOP_COLUMNS = [("overall_rank", "#", "num"), ("score", "Score", "score"), ("signal_class", "Signal", "badge"),
               ("asset_serial", "Serial", "text"), ("model", "Model", "text"), ("branch", "Branch", "text"),
               ("due_value", "OLGA value due", "money"), ("top_job", "Top job", "text"),
               ("earliest_due", "Due", "date"), ("repeat_fault", "Repeat fault", "text"),
               ("is_new_today", "", "bool"), ("reason", "Why", "wide")]

with tab_top:
    try:
        src_id = "daily_top_opportunities"
        fresh = an.freshness(src_id)
        if not fresh["latest_data"]:
            st.info("No ranking yet. It is built daily by SI_ADMIN.REFRESH_DAILY (or run "
                    "CALL SERVICE_INTELLIGENCE.SI_ADMIN.BUILD_DAILY_TOP_OPPORTUNITIES(100, 90)).")
        else:
            DIMS = ("branch", "product_family", "due_basis")
            got = ui.run_parallel([lambda: an.metric("top_opportunities_value", None),
                                   lambda: an.metric("top_opportunities_new_today", None),
                                   lambda: an.metric("telemetry_only_opportunities", None)]
                                  + [lambda d=d: an.dimension_values(src_id, d) for d in DIMS])
            for g in got:
                if isinstance(g, Exception):
                    raise g
            total, new, tel = got[:3]
            options = dict(zip(DIMS, got[3:]))
            run_date = str(fresh["latest_data"])[:10]
            theme.stat3([("OLGA value due · top 100", ui.fmt(total["value"], "value")),
                         ("New since yesterday", ui.fmt(new["value"], "machines")),
                         ("Telemetry-only machines (OLGA doesn't list them)", ui.fmt(tel["value"], "machines"))])
            c0, c1, c2, c3 = st.columns([2.4, 1, 1, 1])
            with c0:
                view = st.radio("Show", ["Top 100 overall", *SIGNAL_CLASSES], horizontal=True, key="top_view")
            filters = {}
            for col, dim in zip((c1, c2, c3), DIMS):
                picked = col.multiselect(reg.label(dim), options[dim], key=f"top_{dim}")
                if picked:
                    filters[dim] = picked
            lst = an.top_list(src_id, filters, 100, None if view == "Top 100 overall" else view)
            rows = lst["rows"]
            if not rows:
                st.info("No machines match these filters.")
            else:
                cols = TOP_COLUMNS if view == "Top 100 overall" else [("class_rank", "#", "num"), *TOP_COLUMNS[1:]]
                layout = st.radio("Layout", ["List", "Cards", "Table"], horizontal=True, key="top_layout", label_visibility="collapsed")
                main, side = st.columns([3.2, 1], gap="medium")
                with main:
                    theme.section(view, f"{len(rows)} machines · ranking of {run_date}")
                    if layout == "List":
                        n_show = st.session_state.get("top_n_list", 25)
                        money = lambda v: ui.fmt(v, "value")  # noqa: E731
                        theme.list_head()
                        for k, r in enumerate(rows[:n_show]):
                            with theme.hit(f"row_{r['asset_serial']}"):
                                theme.html_block(theme.list_row(r, money, min(k, 15) * 25))
                                if st.button("Open details", key=f"row_open_{r['asset_serial']}"):
                                    ui.open_dialog(f"{r.get('model')} · {r['asset_serial']}", ui.machine_detail, r,
                                                   an.as_of("fault_events").date(), lst["evidence"], store, role)
                        if n_show < len(rows) and st.button(f"Show more ({len(rows) - n_show} left)", key="more_list"):
                            st.session_state["top_n_list"] = n_show + 25
                            st.rerun()
                    elif layout == "Cards":
                        n_show = st.session_state.get("top_n_show", 12)
                        money = lambda v: ui.fmt(v, "value")  # noqa: E731
                        shown = rows[:n_show]
                        for start in range(0, len(shown), 3):
                            cols = st.columns(3)
                            for k, (col, r) in enumerate(zip(cols, shown[start:start + 3])):
                                with col:
                                    with theme.hit(f"mc_{r['asset_serial']}"):
                                        theme.html_block(theme.machine_card(r, money, (start + k) % 12 * 40))
                                        if st.button("Open details", key=f"mc_open_{r['asset_serial']}"):
                                            ui.open_dialog(f"{r.get('model')} · {r['asset_serial']}", ui.machine_detail, r,
                                                           an.as_of("fault_events").date(), lst["evidence"], store, role)
                        if n_show < len(rows) and st.button(f"Show more ({len(rows) - n_show} left)"):
                            st.session_state["top_n_show"] = n_show + 12
                            st.rerun()
                    else:
                        theme.table(rows, cols, max_height=620)
                with side:
                    by_class, by_branch = {}, {}
                    for r in rows:
                        by_class[r.get("signal_class")] = by_class.get(r.get("signal_class"), 0) + 1
                        by_branch[r.get("branch")] = by_branch.get(r.get("branch"), 0) + float(r.get("due_value") or 0)
                    colors = {"OLGA + telemetry": ("truck", "orange"), "OLGA only": ("wrench", "pink"), "Telemetry only": ("signal", "mint")}
                    theme.panel("In this list", [(*colors.get(k, ("box", "sky")), str(k), "machines", f"{v:,}")
                                                 for k, v in sorted(by_class.items(), key=lambda x: -x[1])])
                    st.write("")
                    theme.panel("Top branches", [("box", "sky", str(k), "OLGA value due", ui.fmt(v, "value"))
                                                 for k, v in sorted(by_branch.items(), key=lambda x: -x[1])[:6]])
                a, b = st.columns([1, 3])
                a.download_button("Download list (CSV)", ui.df(rows).to_csv(index=False).encode(),
                                  file_name=f"top_machines_{run_date}.csv", mime="text/csv")
                ui.evidence_expander(lst["evidence"] + total["evidence"])
                if can(role, "analyst"):
                    with theme.card("addplan"):
                        theme.section("Add machines to the plan", "creates one open plan item per machine, with its evidence")
                        p1, p2 = st.columns([4, 1])
                        picks = p1.multiselect("Add to the plan (by serial)", [r["asset_serial"] for r in rows], key="top_pick")
                        p2.write("")
                        if p2.button("Save selected to plan", type="primary", disabled=not picks):
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
        theme.section("Fleet-health findings", "repeat HIGH-severity faults across many machines (last 30 days)")
        if st.button("Run detectors on the latest data", type="primary") or st.session_state.get("cands") is not None:
            if st.session_state.get("cands") is None:
                with st.spinner("Running detectors (registry queries only)…"):
                    st.session_state["cands"] = all_candidates(an)
            cands = st.session_state["cands"]
            if not cands:
                st.info("No finding passes the thresholds (at least 10 affected assets).")
            for i, c in enumerate(cands):
                tone = {"High": "red", "Medium": "yel"}.get(c["priority"], "gry")
                theme.html_block(
                    f'<div class="si-rec">{theme.badge(c["priority"] + " priority", tone)} {theme.badge("score " + str(c["score"]), "orange")} '
                    f'{theme.badge(c["confidence"] + " confidence", "gry")}<h4>{theme.esc(c["title"])}</h4>'
                    f'<p>{theme.esc(c["explanation"])}</p><p><b>Problem:</b> {theme.esc(c["problem"])}</p>'
                    f'<p><b>Expected impact (hypothesis):</b> {theme.esc(c["expected_impact"])}</p>'
                    '<p><b>Proposed actions:</b></p><ol>' + "".join(f"<li>{theme.esc(s)}</li>" for s in c["actions"]) + "</ol>"
                    f'<p><b>Risks:</b> {theme.esc(c["risks"])}</p></div>')
                theme.tiles([theme.tile("Affected assets", f"{c['measured']['assets']:,}", "last 30 days", "truck", "orange"),
                             theme.tile("Seen in last 7 days", f"{c['measured']['assets_seen_last_7_days']:,}", "assets", "clock", "sky"),
                             theme.tile("Main product family", str(c["measured"]["main_product_family"]), "", "list", "mint")])
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
            st.info("The plan is empty. Save machines from the first tab or findings from the second.")
        else:
            counts = {}
            for r in rows:
                counts[r.get("status")] = counts.get(r.get("status"), 0) + 1
            theme.tiles([theme.tile("Plan items", f"{len(rows):,}", "saved", "list", "orange")] +
                        [theme.tile(str(k).capitalize(), f"{v:,}", "items", "star", "sky") for k, v in sorted(counts.items())])
            theme.table(rows, [("rec_id", "ID", "text"), ("status", "Status", "badge"), ("priority", "Priority", "badge"),
                               ("score", "Score", "score"), ("title", "Title", "wide"), ("owner", "Owner", "text"),
                               ("created_by", "Created by", "text"), ("created_at", "Created", "date")]
                        if "rec_id" in rows[0] else None, max_height=360)
            rid = st.selectbox("Open recommendation", [r["rec_id"] for r in rows])
            rec = store.get(rid)
            with theme.card("rec"):
                theme.html_block(f'{theme.badge(rec["status"], "orange")} {theme.badge(str(rec["priority"]) + " priority", "gry")}'
                                 f'<h3 style="margin:.4rem 0 .2rem">{theme.esc(rec["rec_id"])} · {theme.esc(rec["title"])}</h3>')
                st.caption(f"Score {rec['score']} · owner {rec.get('owner') or 'not assigned'} · data as of {rec['data_as_of']} UTC · "
                           f"{rec['analysis_version']} · created by {rec['created_by']} at {rec['created_at']}")
                st.markdown(rec.get("explanation") or "")
                st.markdown(f"**Expected impact (hypothesis):** {rec.get('expected_impact')}")
                ev = rec.get("evidence") or {}
                if ev.get("measured"):
                    theme.kv(ev["measured"])
                ui.evidence_expander(ev.get("queries") or [])
                moves = allowed_moves(rec["status"], role)
                if moves:
                    c1, c2, c3 = st.columns([1, 2, 1])
                    to = c1.selectbox("Change status to", moves)
                    note = c2.text_input("Note (required for reject / block)")
                    c3.write("")
                    if c3.button("Apply", type="primary"):
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
            theme.section("Audit trail", "append-only")
            theme.table(rec["history"], max_height=300)
    except Exception as exc:  # noqa: BLE001
        ui.show_error(exc)
theme.user_card()
