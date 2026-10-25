"""Streamlit helpers shared by the three pages (session, user, caching, common widgets)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from .analysis import Analytics, fmt_value
from .data import DataError, Result, SnowparkRunner
from .metrics import Period, QueryError, Registry, Spec
from .plans import PlanStore

APP_DIR = Path(__file__).resolve().parent.parent
CACHE_TTL_S = 900          # data refreshes daily; 15 minutes keeps repeated clicks off the warehouse


@st.cache_resource
def registry() -> Registry:
    import si_native
    embedded = getattr(si_native, "EMBEDDED_REGISTRY", None)     # set by the single-file build
    return Registry(embedded) if embedded else Registry.load(APP_DIR / "metrics.yaml")


@st.cache_resource
def session():
    from snowflake.snowpark.context import get_active_session
    return get_active_session()


def user_email() -> str:
    u = getattr(st, "user", None) or getattr(st, "experimental_user", None)
    email = None
    try:
        email = u.get("email") if hasattr(u, "get") else getattr(u, "email", None)
    except Exception:  # noqa: BLE001
        email = None
    return (email or "unknown").lower()


@st.cache_data(ttl=300, show_spinner=False)
def app_role(email: str) -> str:
    try:
        return PlanStore(session()).app_role(email)
    except Exception:  # noqa: BLE001 - no access to APP_USERS -> least privilege
        return "viewer"


@st.cache_data(ttl=CACHE_TTL_S, show_spinner=False, max_entries=500)
def _cached(sql: str, params: tuple, purpose: str, metric_id: str | None, user: str):
    res = SnowparkRunner(session(), registry()).run(Spec(sql, params, purpose, metric_id), user)
    return res.rows, res.query_id, res.elapsed_ms


class CachedRunner:
    """Same interface as SnowparkRunner; identical specs within the TTL hit the cache (no duplicate queries
    from Streamlit reruns or repeated chat questions)."""

    def run(self, spec: Spec, user: str = "unknown") -> Result:
        rows, qid, ms = _cached(spec.sql, spec.params, spec.purpose, spec.metric_id, user)
        return Result(rows, qid, ms, spec)


def analytics() -> Analytics:
    return Analytics(registry(), CachedRunner(), user_email())


PAGES = ["Analysis", "Recommendations", "Assistant"]
PAGE_FILES = {"Analysis": "streamlit_app.py", "Recommendations": "pages/2_Recommendations.py", "Assistant": "pages/3_Assistant.py"}
TITLE_PAGE = {"Service analysis": "Analysis", "Recommendation plan": "Recommendations", "Service assistant": "Assistant"}


def chrome() -> None:
    """The design (colours, layout, animations) for the current light/dark mode."""
    from . import theme
    theme.apply()
    theme.motion()


def top_nav(key: str = "si_page", index: int = 0) -> str:
    """Brand chip, pill menu, light/dark switch and avatar. Returns the chosen page."""
    from . import theme
    email = user_email()
    return theme.nav(PAGES, email, app_role(email), key, index)


def header(title: str, subtitle: str, chips: list[str] | None = None, show_title: bool = True) -> None:
    import si_native
    from . import theme
    if not getattr(si_native, "SINGLE_FILE", False):              # the single-file launcher draws the bar once, itself
        st.set_page_config(page_title=f"Service Intelligence · {title}", page_icon="◆", layout="wide")
        chrome()
        here = TITLE_PAGE.get(title, "Analysis")
        nxt = st.session_state.pop("si_page_next", None)
        if nxt and nxt != here and getattr(st, "switch_page", None):
            st.switch_page(PAGE_FILES[nxt])
        choice = top_nav(f"nav_{here}", PAGES.index(here))
        if choice != here and getattr(st, "switch_page", None):
            st.switch_page(PAGE_FILES[choice])
    if show_title:
        base = ['<span class="dot"></span>Live Snowflake data', f"Registry <b>v{registry().version}</b>"]
        theme.topbar(title, subtitle, (chips or []) + base)


def run_parallel(fns: list) -> list:
    """Run independent query functions concurrently (each returns a value or raises). Results keep the order of
    `fns`; an exception is returned in place of its result. Anything that fails concurrently is retried in order,
    so a runtime that cannot share the session across threads still gets every answer."""
    out = [None] * len(fns)
    done = [False] * len(fns)
    try:
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        ctx = get_script_run_ctx()

        def wrap(i, fn):
            def inner():
                add_script_run_ctx(threading.current_thread(), ctx)
                out[i] = fn()
                done[i] = True
            return inner
        with ThreadPoolExecutor(max_workers=min(6, len(fns) or 1)) as ex:
            for fut in [ex.submit(wrap(i, fn)) for i, fn in enumerate(fns)]:
                try:
                    fut.result()
                except Exception:  # noqa: BLE001 - retried below
                    pass
    except Exception:  # noqa: BLE001 - no thread support: run in order
        pass
    for i, fn in enumerate(fns):
        if not done[i]:
            try:
                out[i] = fn()
            except Exception as exc:  # noqa: BLE001
                out[i] = exc
    return out


def open_dialog(title: str, fn, *args) -> None:
    """Show `fn(*args)` in a modal dialog (st.dialog), or inline in a card on Streamlit versions without dialogs."""
    from . import theme
    deco = getattr(st, "dialog", None) or getattr(st, "experimental_dialog", None)
    if deco is None:
        with theme.card("inline_detail"):
            fn(*args)
        return
    try:
        deco(title, width="large")(fn)(*args)
    except TypeError:
        deco(title)(fn)(*args)


def go_to(page: str, **state) -> None:
    """Switch page from inside a page (single-file menu or multipage app) and carry state over."""
    import si_native
    st.session_state.update(state)
    if getattr(si_native, "SINGLE_FILE", False):
        st.session_state["si_page_next"] = page
        st.rerun()
    switch = getattr(st, "switch_page", None)
    if switch:
        switch(PAGE_FILES[page])


def run_spec(spec: Spec) -> list[dict]:
    return CachedRunner().run(spec, user_email()).rows


def machine_detail(row: dict, as_of: date, list_evidence: list, store=None, role: str = "viewer") -> None:
    """Everything about one ranked machine: score parts, reason, OLGA jobs, fault history and hours."""
    from . import machine, theme
    from .plans import PlanError, can
    from .recommend import top_opportunity_candidate
    serial = row["asset_serial"]
    theme.html_block(f'<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
                     f'{theme.badge(row.get("signal_class"), "dark")}{theme.badge("Rank #" + str(row.get("overall_rank") or row.get("class_rank")), "orange")}'
                     f'{theme.badge(row.get("branch") or "no branch", "sky")}{theme.badge("Customer " + str(row.get("customer_number") or "—"), "slate")}'
                     f'{theme.badge(row.get("product_family") or "", "gry")}</div>')
    theme.points([("Value (OLGA)", row.get("value_points"), 35), ("Urgency", row.get("urgency_points"), 30),
                  ("Condition (faults)", row.get("condition_points"), 35)])
    theme.html_block(f'<p style="color:var(--muted);font-size:.88rem;margin:.6rem 0">{theme.esc(row.get("reason"))}</p>')
    theme.kv({"Score": f"{float(row.get('score') or 0):.1f}", "OLGA value due": fmt(row.get("due_value"), "value"),
              "Earliest due": str(row.get("earliest_due") or "—")[:10], "Due basis": row.get("due_basis"),
              "Hours now": fmt(row.get("smu_now"), "hours"), "Hours / day": fmt(row.get("hours_per_day"), "hours")})
    t1, t2, t3 = st.tabs(["OLGA jobs", "Faults · 90 days", "Machine hours"])
    jobs, flt, hrs = run_parallel([lambda: run_spec(machine.olga_jobs(serial)), lambda: run_spec(machine.faults(serial, as_of)),
                                   lambda: run_spec(machine.hours(serial, as_of))])
    with t1:
        if isinstance(jobs, Exception):
            show_error(jobs)
        elif jobs:
            theme.table(jobs, [("job", "Job", "text"), ("component", "Component", "text"), ("lines", "Lines", "num"),
                               ("first_due", "First due", "date"), ("last_due", "Last due", "date"),
                               ("total_value", "OLGA value", "money")], max_height=320)
        else:
            st.info("No OLGA opportunities on this machine in the latest snapshot.")
    with t2:
        if isinstance(flt, Exception):
            show_error(flt)
        elif flt:
            theme.table(flt, [("fault_description", "Fault", "text"), ("severity", "Severity", "badge"),
                              ("fault_days", "Days", "num"), ("events", "Events", "num"), ("last_seen", "Last seen", "date")],
                        max_height=320)
        else:
            st.info("No fault events in the last 90 days.")
    with t3:
        if isinstance(hrs, Exception):
            show_error(hrs)
        elif hrs:
            theme.area_chart(df(hrs), "reading_date", "smh", "service-meter hours", height=240)
        else:
            st.info("This machine has not reported service-meter hours in the last 120 days.")
    c1, c2 = st.columns(2)
    if store is not None and can(role, "analyst") and c1.button("Add to plan", key=f"dlg_save_{serial}", type="primary",
                                                                 use_container_width=True):
        try:
            rid = store.save(top_opportunity_candidate(row, list_evidence), user_email(), role, registry().version)
            st.success(f"Saved as {rid}.")
        except PlanError as exc:
            st.warning(str(exc))
    if c2.button("Ask the assistant about it", key=f"dlg_ask_{serial}", use_container_width=True):
        go_to("Assistant", chat_prefill=f"Tell me about machine {serial} ({row.get('model')}, {row.get('branch')}): "
                                        "why is it ranked, what work is due and what should we do first?")


def metric_detail(metric_id: str, an: Analytics, period: Period, filters: dict) -> None:
    """One metric in depth: value vs previous period, trend and its largest segments."""
    from . import theme
    m = an.r.metric(metric_id)
    src = an.r.source_of(m)
    res = an.metric(metric_id, period, filters, compare=m.kind == "period")
    theme.html_block(f'<div style="color:var(--muted);font-size:.86rem">{theme.esc(m.description)}</div>')
    items = {"Value": fmt(res["value"], m.unit), "Records": f"{res['n']:,}",
             "Period": period.label if m.kind == "period" else f"as of {res['as_of']:%d %b %Y}"}
    if res["previous"]:
        items["Previous period"] = fmt(res["previous"]["value"], m.unit)
        items["Change"] = f"{res['change']:+.1%}" if res["change"] is not None else "—"
    theme.kv(items)
    dim = src.dimensions[0]
    jobs = [lambda: an.breakdown(metric_id, dim, period, filters, limit=10)]
    if m.kind == "period":
        grain = "month" if m.grain == "month" or (period.end - period.start).days > 120 else "week"
        jobs.append(lambda: an.series(metric_id, period, grain, filters))
    got = run_parallel(jobs)
    if m.kind == "period" and not isinstance(got[1], Exception) and got[1]["rows"]:
        theme.section("Trend", f"by {got[1]['grain']}")
        theme.area_chart(df(got[1]["rows"])[["bucket", "value"]], "bucket", "value", height=220)
    if not isinstance(got[0], Exception) and got[0]["rows"]:
        theme.section(f"Largest by {an.r.label(dim).lower()}", "top 10")
        bd = df(got[0]["rows"])
        bd["segment"] = bd["segment"].astype(str)
        theme.bar_chart(bd, "segment", "value", height=260)
    for lim in m.limitations:
        st.caption(f"• {lim}")


def show_error(exc: Exception) -> None:
    if isinstance(exc, (DataError, QueryError)):
        st.error(str(exc))
    else:
        st.error("Something went wrong. The details are in Snowflake query history; no data was changed.")


def period_picker(key: str, as_of_date: date, default_days: int = 30) -> Period:
    presets = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "Last 365 days": 365, "Custom": None}
    choice = st.selectbox("Reporting period", list(presets), index=1 if default_days == 30 else 2, key=f"{key}_p")
    end = as_of_date + timedelta(days=1)
    if presets[choice]:
        return Period(end - timedelta(days=presets[choice]), end)
    c1, c2 = st.columns(2)
    s = c1.date_input("From", end - timedelta(days=default_days), key=f"{key}_s")
    e = c2.date_input("To (inclusive)", as_of_date, key=f"{key}_e")
    return Period(s, e + timedelta(days=1))


def evidence_expander(evidence: list[dict], label: str = "Evidence: queries behind this figure") -> None:
    with st.expander(label):
        for ev in evidence:
            st.markdown(f"**{ev.get('purpose')}** · metric `{ev.get('metric_id')}` · query id `{ev.get('query_id')}`")
            st.code(ev.get("sql", ""), language="sql")
            if ev.get("params"):
                st.caption("Bind values: " + json.dumps(ev["params"]))


def df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def fmt(v, unit: str) -> str:
    return fmt_value(v, unit)


def key_of(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:12]
