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


def chrome() -> None:
    """Design and the logo/user block in the navigation rail."""
    from . import theme
    theme.apply()
    email = user_email()
    theme.brand(email, app_role(email))


def header(title: str, subtitle: str, chips: list[str] | None = None) -> None:
    import si_native
    from . import theme
    if not getattr(si_native, "SINGLE_FILE", False):              # the single-file launcher does this once, before its menu
        st.set_page_config(page_title=f"Service Intelligence · {title}", page_icon="◆", layout="wide")
        chrome()
    base = [f"Registry <b>v{registry().version}</b>", "Live Snowflake data · <b>UTC</b>"]
    theme.topbar(title, subtitle, (chips or []) + base)


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
