"""Query execution: one place where SQL reaches Snowflake.

Every query is a registry-built `Spec` (checked again here), runs with bind parameters, carries a
QUERY_TAG (visible in Snowflake QUERY_HISTORY for cost and audit), and returns its query id so
results can be traced as evidence.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .metrics import Registry, Spec, check_spec

APP_TAG = "service_intelligence"


@dataclass
class Result:
    rows: list[dict]
    query_id: str | None
    elapsed_ms: int
    spec: Spec
    meta: dict = field(default_factory=dict)

    def scalar(self, key: str = "value"):
        return self.rows[0].get(key) if self.rows else None


class DataError(RuntimeError):
    """A query failed; the message is safe to show (no SQL, no credentials)."""


def plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


class SnowparkRunner:
    """Runs specs on the Streamlit-in-Snowflake session (owner's rights, the app's warehouse)."""

    def __init__(self, session, registry: Registry, statement_timeout_s: int = 60):
        self.session = session
        self.registry = registry
        try:
            session.sql(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {int(statement_timeout_s)}").collect()
        except Exception:   # noqa: BLE001 - the warehouse-level timeout (120 s) still applies
            pass

    def run(self, spec: Spec, user: str = "unknown") -> Result:
        check_spec(spec, self.registry)
        tag = json.dumps({"app": APP_TAG, "purpose": spec.purpose, "metric": spec.metric_id, "user": user})[:2000]
        t0 = time.monotonic()
        try:
            self.session.query_tag = tag
            job = self.session.sql(spec.sql, params=list(spec.params)).collect_nowait()
            rows = [{k.lower(): plain(v) for k, v in r.as_dict().items()} for r in job.result()]
            qid = job.query_id
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if "timeout" in text.lower() or "canceled" in text.lower():
                raise DataError("The query took too long and was stopped. Narrow the period or filters.") from exc
            if "does not exist or not authorized" in text:
                raise DataError("The app's role cannot read a required table. Ask an admin to check the grants.") from exc
            if "suspended" in text.lower():
                raise DataError("The warehouse is suspended and could not resume. Try again shortly.") from exc
            raise DataError("The query failed in Snowflake. The error was logged in query history.") from exc
        finally:
            try:
                self.session.query_tag = None
            except Exception:  # noqa: BLE001
                pass
        return Result(rows, qid, int((time.monotonic() - t0) * 1000), spec)


class FakeRunner:
    """Test double: returns canned rows per purpose/metric and records every spec it was asked to run."""

    def __init__(self, registry: Registry, responder):
        self.registry = registry
        self.responder = responder
        self.calls: list[Spec] = []

    def run(self, spec: Spec, user: str = "test") -> Result:
        check_spec(spec, self.registry)
        self.calls.append(spec)
        return Result(self.responder(spec), f"fake-{len(self.calls)}", 0, spec)
