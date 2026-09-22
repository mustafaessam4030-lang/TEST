"""Snowflake Cortex as Maia's runtime intelligence.

What is proven here, without a Snowflake account:
  * configuration and the exact CORTEX_UNAVAILABLE reasons (no silent fallback)
  * the AI_COMPLETE statement and the Cortex Agents REST requests we send
    (URL, bearer headers, generic client-side tools, tool_result round trip)
  * the controlled tools: filters, serial lock, refresh gating, findings
  * verification: a value Cortex introduces is blocked
  * the whole turn, first from SIS and then from the store with no browser

What is NOT proven here: that a given Snowflake account has Cortex enabled.
That is `POST /v1/cortex/check` / `snowflake_setup.py check` on the real account.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from app.adapters.registry import SourceRegistry
from app.agent.state import ConversationContext
from app.config import Settings
from app.core.errors import AutomationError, ErrorCode
from app.cortex.client import CortexRuntime
from app.cortex.errors import classify_sql_error
from app.cortex.service import MaiaCortexService, parse_parts_filters
from app.cortex.verify import CORTEX_SCHEMA, claimed_tokens
from app.domain.freshness import FreshnessPolicy
from app.models.schemas import EquipmentRecord
from app.repositories.memory_repo import MemoryEquipmentRepository
from app.services.equipment_service import EquipmentService
from app.tools.equipment_tools import ToolContext, run_tool, tool_specs
from tests.conftest import SIS_SEARCHES, FakeAdapter, FakePool

SERIAL = "JAZ01865"
PAT = "pat-SECRET-never-printed"


# ── fixtures ────────────────────────────────────────────────────────────────
def _settings(**over: Any) -> Settings:
    base = {"allow_live_automation": True, "repository": "memory",
            "run_deadline_ms": 5000, "step_timeout_ms": 2000,
            "snowflake_config_file": "/nonexistent/snowflake.txt"}
    return Settings(**{**base, **over})


def _service(repo: Any, behaviour: str = "ok") -> EquipmentService:
    registry = SourceRegistry()
    registry.register("cat_sis", "Caterpillar SIS",
                      {"_behaviour": behaviour, "selector_version": "test-v1"},
                      FakeAdapter, precedence=10)
    return EquipmentService(repo=repo, registry=registry, pool=FakePool(),
                            freshness=FreshnessPolicy({"sources": {"cat_sis": {"ttl_days": 90}}}),
                            settings=_settings())


def _rich_record(**over: Any) -> EquipmentRecord:
    cols = ["Group Part", "Part Number", "Part Name", "Quantity Required", "Where Used",
            "Service Article"]
    def row(*cells):
        return {"cells": list(cells), "values": dict(zip(cols, cells))}
    data = {
        "serial_number": SERIAL, "source_system": "cat_sis",
        "retrieved_at": datetime.now(timezone.utc), "automation_run_id": "run_RICH",
        "equipment_model": "C32", "machine_serial_number": SERIAL,
        "machine_build_date": "2014-08-02", "engine_serial_number": "PRH04588",
        "engine_build_date": "2014-06-30",
        "parts_data": {
            "entire_group_title": f"Product - Entire Group ({SERIAL})", "group_count": 2,
            "groups": [
                {"title": f"Product - Entire Group ({SERIAL})", "is_entire_group": True,
                 "columns": cols, "rows": [
                     row("", "444-5867", "General AR", "1", "", ""),
                     row("", "256-3170", "Engine AR", "1", "", "")]},
                {"title": "Exhaust Group (PRH04588)", "columns": cols, "rows": [
                    row("EXH", "269-7022", "Muffler", "4", "Exhaust stack", "M0081234"),
                    row("EXH", "286-4915", "Clamp", "4", "Muffler mount", ""),
                    row("EXH", "421-8926", "Gasket", "2", "Turbo outlet", ""),
                    row("EXH", "444-5867", "General AR", "1", "", "")]},
            ]},
    }
    data.update(over)
    return EquipmentRecord(**data)


class FakeExecutor:
    """Stands in for the Snowflake connection: records every AI_COMPLETE."""

    def __init__(self, reply: Any = None, error: str | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.reply, self.error = reply, error

    def _run_sync(self, sql: str, params: dict | None, fetch: str | None) -> Any:
        self.calls.append((sql, params or {}))
        if self.error:
            raise AutomationError(ErrorCode.DATABASE_ERROR, "Data store operation failed.",
                                  details={"driver_error": self.error})
        prompt = (params or {}).get("prompt", "")
        reply = self.reply(prompt) if callable(self.reply) else self.reply
        return (json.dumps(reply) if isinstance(reply, dict) else reply,)


def grounded_reply(prompt: str) -> dict[str, Any]:
    """A well-behaved model: answers only with the facts it was given."""
    facts = re.findall(r"^(F\d+) \[(DIRECT|DERIVED)\] (.+)$", prompt, re.M)
    direct = [(i, s) for i, e, s in facts if e == "DIRECT"][:4]
    return {"answer": "Here is what SIS holds: " + "; ".join(s for _, s in direct),
            "cited_fact_ids": [i for i, _ in direct], "derived_findings": [],
            "inferred": [{"statement": "The machine looks like a genset application.",
                          "basis_fact_ids": [direct[0][0]] if direct else []}],
            "missing_fields": [], "confidence": "high"}


def _runtime(executor: Any = None, **settings: Any) -> CortexRuntime:
    s = _settings(cortex_enabled=True, cortex_mode="complete", **settings)
    rt = CortexRuntime(s, None, sql_executor=executor)
    rt._cfg = _cfg()
    return rt


def _cfg(**over: Any) -> Any:
    from app.repositories.snowflake_config import SnowflakeSettings

    base = {"account": "xy12345.eu-west-1", "user": "MAIA_SVC",
            "authenticator": "externalbrowser", "warehouse": "WH", "database": "DB",
            "schema": "CORE", "role": "MAIA_APP"}
    base.update(over)
    return SnowflakeSettings(**base)


# ── 1. configuration: CORTEX_UNAVAILABLE says exactly what is missing ────────
def test_cortex_disabled_is_reported_not_substituted() -> None:
    rt = CortexRuntime(_settings(), None)
    with pytest.raises(AutomationError) as exc:
        rt.mode()
    assert exc.value.code is ErrorCode.CORTEX_UNAVAILABLE
    assert exc.value.details["reason"] == "disabled"
    assert "MAIA_CORTEX_ENABLED" in exc.value.details["fix"]


def test_enabled_without_snowflake_says_what_to_configure() -> None:
    rt = CortexRuntime(_settings(cortex_enabled=True), None)
    with pytest.raises(AutomationError) as exc:
        rt.mode()
    assert exc.value.details["reason"] == "not_configured"
    assert "account is not set" in exc.value.details["detail"]


def test_auto_mode_picks_agents_with_a_pat_and_sql_with_sso() -> None:
    rt = CortexRuntime(_settings(cortex_enabled=True), None)
    rt._cfg = _cfg()
    assert rt.mode() == "complete"                # SSO: no bearer token for REST
    rt._cfg = _cfg(authenticator=None, pat=PAT)
    assert rt.mode() == "agent"


def test_agent_mode_forced_with_sso_explains_the_auth_gap() -> None:
    rt = CortexRuntime(_settings(cortex_enabled=True, cortex_mode="agent"), None)
    rt._cfg = _cfg()
    with pytest.raises(AutomationError) as exc:
        rt.mode()
    assert exc.value.details["reason"] == "agent_auth"
    assert "programmatic access token" in exc.value.details["fix"]


def test_status_never_contains_a_secret() -> None:
    rt = CortexRuntime(_settings(cortex_enabled=True), None)
    rt._cfg = _cfg(authenticator=None, pat=PAT)
    status = rt.status()
    assert status["mode"] == "agent" and status["configured"] is True
    assert PAT not in json.dumps(status)


def test_standard_snowflake_env_vars_are_honoured(monkeypatch) -> None:
    from app.repositories import snowflake_config

    for key, value in {"SNOWFLAKE_ACCOUNT": "org-acct", "SNOWFLAKE_USER": "svc",
                       "SNOWFLAKE_PASSWORD": "pw-secret", "SNOWFLAKE_WAREHOUSE": "WH1",
                       "SNOWFLAKE_DATABASE": "DB1", "SNOWFLAKE_SCHEMA": "S1",
                       "SNOWFLAKE_ROLE": "R1", "SNOWFLAKE_CORTEX_ENABLED": "true"}.items():
        monkeypatch.setenv(key, value)
    settings = Settings(snowflake_config_file="/nonexistent")
    cfg = snowflake_config.load(settings)
    assert (cfg.account, cfg.user, cfg.warehouse, cfg.database, cfg.schema, cfg.role) == (
        "org-acct", "svc", "WH1", "DB1", "S1", "R1")
    assert cfg.auth_method == "password" and cfg.problems() == []
    assert settings.cortex_enabled is True
    assert "pw-secret" not in repr(cfg.connect_kwargs())


@pytest.mark.parametrize("text,reason", [
    ("SQL compilation error: Unknown function AI_COMPLETE", "function_missing"),
    ("Insufficient privileges to operate on function 'AI_COMPLETE'", "privilege"),
    ("Model mistral-large2 is unavailable in your region", "model_unavailable"),
    ("Something odd", "unknown"),
])
def test_driver_errors_map_to_actionable_reasons(text: str, reason: str) -> None:
    assert classify_sql_error(text) == reason


# ── 2. AI_COMPLETE: the statement we send ───────────────────────────────────
@pytest.mark.asyncio
async def test_ai_complete_uses_named_args_and_a_json_schema() -> None:
    ex = FakeExecutor(reply={"answer": "ok", "cited_fact_ids": [], "derived_findings": [],
                             "inferred": [], "missing_fields": [], "confidence": "high"})
    out = await _runtime(ex).complete("hello", schema=CORTEX_SCHEMA)
    sql, args = ex.calls[0]
    assert "AI_COMPLETE(model => %(model)s, prompt => %(prompt)s" in sql
    assert "response_format => PARSE_JSON(%(fmt)s)::OBJECT" in sql
    assert json.loads(args["fmt"]) == {"type": "json", "schema": CORTEX_SCHEMA}
    assert json.loads(args["params"])["temperature"] == 0
    assert args["model"] == "mistral-large2"
    assert out["answer"] == "ok"


@pytest.mark.asyncio
async def test_ai_complete_failure_is_cortex_unavailable_with_the_reason() -> None:
    ex = FakeExecutor(error="SQL compilation error: Unknown function AI_COMPLETE")
    with pytest.raises(AutomationError) as exc:
        await _runtime(ex).complete("hello")
    assert exc.value.code is ErrorCode.CORTEX_UNAVAILABLE
    assert exc.value.details["reason"] == "function_missing"
    assert "SNOWFLAKE.CORTEX_USER" in exc.value.details["fix"]


# ── 3. Cortex Agents REST: the requests we send ─────────────────────────────
class ScriptedAgent:
    """A Cortex Agent over MockTransport: calls get_equipment_data, then answers
    citing the facts the gateway returned."""

    def __init__(self, *, first_status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.first_status = first_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if self.first_status != 200:
            return httpx.Response(self.first_status, json={"message": "nope"})
        results = [c for m in body["messages"] for c in m["content"]
                   if c.get("type") == "tool_result"]
        if not results:
            serial = re.search(r"confirmed by the gateway\): (\S+)",
                               body["messages"][0]["content"][0]["text"]).group(1)
            return httpx.Response(200, json={"role": "assistant", "content": [
                {"type": "tool_use", "tool_use": {"tool_use_id": "toolu_1",
                 "name": "get_equipment_data", "input": {"serial_number": serial},
                 "client_side_execute": True}}], "metadata": {}})
        facts = results[0]["tool_result"]["content"][0]["json"]["facts"]
        direct = [f for f in facts if f["evidence"] == "DIRECT"][:3]
        final = {"answer": "From SIS: " + "; ".join(f["statement"] for f in direct),
                 "cited_fact_ids": [f["id"] for f in direct], "derived_findings": [],
                 "inferred": [], "missing_fields": [], "confidence": "high"}
        return httpx.Response(200, json={"role": "assistant", "content": [
            {"type": "text", "text": json.dumps(final)}], "metadata": {"run_id": "r"}})


def _agent_runtime(agent: ScriptedAgent, repo: Any) -> CortexRuntime:
    rt = CortexRuntime(_settings(cortex_enabled=True, cortex_mode="agent"), repo,
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(agent)))
    rt._cfg = _cfg(authenticator=None, pat=PAT)
    return rt


@pytest.mark.asyncio
async def test_agent_run_request_and_client_side_tool_round_trip() -> None:
    repo = MemoryEquipmentRepository()
    await repo.upsert(_rich_record())
    agent = ScriptedAgent()
    maia = MaiaCortexService(runtime=_agent_runtime(agent, repo),
                             equipment_service=_service(repo), repo=repo)
    out = await maia.answer(f"Maia, get equipment {SERIAL}")

    first, second = agent.requests
    assert str(first.url) == "https://xy12345.eu-west-1.snowflakecomputing.com" \
                             "/api/v2/cortex/agent:run"
    assert first.headers["authorization"] == f"Bearer {PAT}"
    assert first.headers["x-snowflake-authorization-token-type"] == "PROGRAMMATIC_ACCESS_TOKEN"
    body = json.loads(first.content)
    assert body["stream"] is False and "models" not in body          # "auto" model
    assert all(t["tool_spec"]["type"] == "generic" and "tool_resources" not in t
               for t in body["tools"])                                # client-side tools
    sent = json.loads(second.content)["messages"]
    assert sent[1]["role"] == "assistant" and sent[1]["content"][0]["type"] == "tool_use"
    result = sent[2]["content"][0]["tool_result"]
    assert result["tool_use_id"] == "toolu_1" and result["status"] == "success"

    assert out["status"] == "SUCCESS" and out["cortex"]["mode"] == "agent"
    assert "machine_build_date = 2014-08-02" in out["answer"]
    assert out["verification"]["passed"] is True
    assert all(f["evidence"] == "DIRECT" for f in out["facts"])
    assert PAT not in json.dumps(out)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reason", [(401, "auth_rejected"), (403, "privilege"),
                                           (404, "agent_api_missing")])
async def test_agent_http_errors_are_cortex_unavailable(status: int, reason: str) -> None:
    repo = MemoryEquipmentRepository()
    await repo.upsert(_rich_record())
    maia = MaiaCortexService(runtime=_agent_runtime(ScriptedAgent(first_status=status), repo),
                             equipment_service=_service(repo), repo=repo)
    out = await maia.answer(f"get equipment {SERIAL}")
    assert out["status"] == "CORTEX_UNAVAILABLE"
    assert out["cortex"]["reason"] == reason and out["cortex"]["fix"]
    # The verified values are still shown, labelled as the gateway's, not as AI.
    assert any("PRH04588" in f["statement"] for f in out["facts"])
    assert "no AI answer" in out["answer"]


def test_keypair_jwt_matches_the_documented_claims(tmp_path) -> None:
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.cortex.auth import bearer_headers

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "k.p8"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    cfg = _cfg(authenticator=None, private_key_file=str(path), user="maia_svc")
    headers = bearer_headers(cfg)
    assert headers["X-Snowflake-Authorization-Token-Type"] == "KEYPAIR_JWT"
    token = headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"])
    assert claims["sub"] == "XY12345.MAIA_SVC"
    assert claims["iss"].startswith("XY12345.MAIA_SVC.SHA256:")
    assert claims["exp"] - claims["iat"] <= 3600
    assert token not in repr(headers) and token not in str(headers)


# ── 4. tools: deterministic, locked to the confirmed serial ─────────────────
async def _ctx(record: EquipmentRecord | None = None, *, fresh: bool = False,
               behaviour: str = "ok") -> ToolContext:
    repo = MemoryEquipmentRepository()
    if record is not None:
        await repo.upsert(record)
    return ToolContext(service=_service(repo, behaviour), repo=repo,
                       allowed_serials={SERIAL}, wants_fresh=fresh)


def test_generic_tool_specs_have_no_sql_or_credential_arguments() -> None:
    names = {t["tool_spec"]["name"] for t in tool_specs()}
    assert names == {"get_equipment_data", "get_equipment_build_info", "get_equipment_parts",
                     "get_automation_history", "search_equipment", "analyze_equipment",
                     "refresh_equipment_from_sis"}
    args = {p for t in tool_specs() for p in t["tool_spec"]["input_schema"]["properties"]}
    assert not args & {"sql", "query", "url", "selector", "password", "username", "token"}


@pytest.mark.asyncio
async def test_parts_in_the_exhaust_group() -> None:
    ctx = await _ctx(_rich_record())
    out = await run_tool(ctx, "get_equipment_parts", {"serial_number": SERIAL,
                                                     "group_part": "exhaust"})
    assert out["data"]["matched"] == 4
    assert {r["part_number"] for r in out["data"]["rows"]} == {
        "269-7022", "286-4915", "421-8926", "444-5867"}
    assert out["facts"][0]["evidence"] == "DERIVED"          # the count is computed
    assert all(f["evidence"] == "DIRECT" for f in out["facts"][1:])


@pytest.mark.asyncio
async def test_parts_requiring_quantity_4_and_where_a_part_is_used() -> None:
    ctx = await _ctx(_rich_record())
    q4 = await run_tool(ctx, "get_equipment_parts", {"serial_number": SERIAL,
                                                    "quantity_required": 4})
    assert {r["part_number"] for r in q4["data"]["rows"]} == {"269-7022", "286-4915"}
    used = await run_tool(ctx, "get_equipment_parts", {"serial_number": SERIAL,
                                                      "part_number": "286-4915"})
    assert used["data"]["rows"][0]["where_used"] == "Muffler mount"


@pytest.mark.asyncio
async def test_a_missing_quantity_column_is_said_not_invented() -> None:
    record = _rich_record()
    for g in record.parts_data["groups"]:
        g["columns"] = ["Part Number", "Part Name"]
        for r in g["rows"]:
            r["values"] = {"Part Number": r["cells"][1], "Part Name": r["cells"][2]}
    ctx = await _ctx(record)
    out = await run_tool(ctx, "get_equipment_parts", {"serial_number": SERIAL,
                                                     "quantity_required": 4})
    assert out["data"]["matched"] == 0
    assert any("did not publish a quantity" in f["statement"] for f in out["facts"])


@pytest.mark.asyncio
async def test_the_model_cannot_switch_to_a_serial_nobody_confirmed() -> None:
    ctx = await _ctx(_rich_record())
    out = await run_tool(ctx, "get_equipment_data", {"serial_number": "JAZ01868"})
    assert out["status"] == "REFUSED" and "Do not substitute" in out["message"]
    assert SIS_SEARCHES.count("JAZ01868") == 0


@pytest.mark.asyncio
async def test_refresh_is_gated_by_the_gateway_not_the_model() -> None:
    SIS_SEARCHES.clear()
    ctx = await _ctx(_rich_record())                  # fresh copy, no refresh asked
    out = await run_tool(ctx, "refresh_equipment_from_sis", {"serial_number": SERIAL})
    assert out["data"]["refresh"].startswith("not performed") and SIS_SEARCHES == []
    ctx = await _ctx(_rich_record(), fresh=True)      # the user asked for fresh data
    out = await run_tool(ctx, "refresh_equipment_from_sis", {"serial_number": SERIAL})
    assert out["data"]["refresh"] == "performed" and SIS_SEARCHES == [SERIAL]
    again = await run_tool(ctx, "refresh_equipment_from_sis", {"serial_number": SERIAL})
    assert again["status"] == "REFUSED"               # at most one browser run a turn


@pytest.mark.asyncio
async def test_analysis_finds_duplicates_gaps_and_changes() -> None:
    repo = MemoryEquipmentRepository()
    await repo.upsert(_rich_record(automation_run_id="run_OLD"))
    await repo.upsert(_rich_record(automation_run_id="run_NEW", equipment_model="C32B"))
    ctx = ToolContext(service=_service(repo), repo=repo, allowed_serials={SERIAL})
    out = await run_tool(ctx, "analyze_equipment", {"serial_number": SERIAL})
    data = out["data"]
    assert data["duplicate_parts"] == {"444-5867": ["Entire Group", "Exhaust Group"]}
    assert data["engine_before_machine_days"] == 33
    assert "equipment_model" in data["changed_since_previous"]
    assert all(f["evidence"] == "DERIVED" for f in out["facts"])


def test_parts_questions_are_parsed_deterministically() -> None:
    assert parse_parts_filters("What parts are in the exhaust group?") == {"group_part": "exhaust"}
    assert parse_parts_filters("Show me the parts that require quantity 4.") == {
        "quantity_required": 4.0}
    assert parse_parts_filters("Where is part 286-4915 used?") == {"part_number": "286-4915"}


# ── 5. verification: no value may appear that the tools did not return ─────
def test_claimed_tokens_normalise_dates() -> None:
    assert {"2014-08-02", "286-4915", "JAZ01865"} <= claimed_tokens(
        "JAZ01865 was built 08/02/2014; part 286-4915.")


@pytest.mark.asyncio
async def test_a_hallucinated_part_number_is_blocked() -> None:
    repo = MemoryEquipmentRepository()
    await repo.upsert(_rich_record())
    lying = FakeExecutor(reply={"answer": "The exhaust uses part 999-9999 and 269-7022.",
                                "cited_fact_ids": ["F1"], "derived_findings": [],
                                "inferred": [], "missing_fields": [], "confidence": "high"})
    maia = MaiaCortexService(runtime=_runtime(lying), equipment_service=_service(repo),
                             repo=repo)
    out = await maia.answer(f"What parts are in the exhaust group of {SERIAL}?")
    assert out["verification"]["passed"] is False
    assert out["verification"]["blocked_tokens"] == ["999-9999"]
    assert "999-9999" not in out["answer"] and out["confidence"] == "low"


@pytest.mark.asyncio
async def test_inferred_statements_are_labelled_inferred() -> None:
    repo = MemoryEquipmentRepository()
    await repo.upsert(_rich_record())
    maia = MaiaCortexService(runtime=_runtime(FakeExecutor(reply=grounded_reply)),
                             equipment_service=_service(repo), repo=repo)
    out = await maia.answer(f"Maia, find equipment {SERIAL}")
    assert out["status"] == "SUCCESS" and out["verification"]["passed"]
    assert out["inferred"][0]["evidence"] == "INFERRED"
    assert {e["type"] for e in out["evidence"]} <= {"DIRECT", "DERIVED"}


# ── 6. the whole turn: SIS once, then the store, with Cortex both times ─────
@pytest.mark.asyncio
async def test_first_ask_goes_to_sis_second_is_answered_from_the_store() -> None:
    SIS_SEARCHES.clear()
    repo = MemoryEquipmentRepository()
    ex = FakeExecutor(reply=grounded_reply)
    maia = MaiaCortexService(runtime=_runtime(ex), equipment_service=_service(repo), repo=repo)

    first = await maia.answer(f"Get equipment {SERIAL}")
    assert first["status"] == "SUCCESS" and first["origin"] == "sis"
    assert SIS_SEARCHES == [SERIAL] and first["sis_runs"] == 1
    assert (await repo.get_current(SERIAL, "cat_sis"))["automation_run_id"] == \
        first["automation_run_id"]                                     # persisted
    assert len(ex.calls) == 1                                          # Cortex analysed

    second = await maia.answer(f"Get equipment {SERIAL}", ConversationContext.model_validate(
        first["context"]))
    assert second["status"] == "SUCCESS" and second["origin"] == "store"
    assert SIS_SEARCHES == [SERIAL] and second["sis_runs"] == 0         # NO Playwright
    assert len(ex.calls) == 2                                          # Cortex again
    assert [t["origin"] for t in second["tools"]][:1] == ["store"]


@pytest.mark.asyncio
async def test_follow_up_about_the_engine_uses_the_same_machine() -> None:
    repo = MemoryEquipmentRepository()
    await repo.upsert(_rich_record())
    maia = MaiaCortexService(runtime=_runtime(FakeExecutor(reply=grounded_reply)),
                             equipment_service=_service(repo), repo=repo)
    first = await maia.answer(f"Maia, find equipment {SERIAL}")
    follow = await maia.answer("what about the engine?",
                               ConversationContext.model_validate(first["context"]))
    assert follow["serial_number"] == SERIAL
    assert "get_equipment_build_info" in [t["tool"] for t in follow["tools"]]


@pytest.mark.asyncio
async def test_a_nonexistent_serial_is_questioned_never_substituted() -> None:
    SIS_SEARCHES.clear()
    repo = MemoryEquipmentRepository()
    await repo.upsert(_rich_record())
    ex = FakeExecutor(reply=grounded_reply)
    maia = MaiaCortexService(runtime=_runtime(ex), equipment_service=_service(repo), repo=repo)
    out = await maia.answer("Get equipment JAZ01856")
    assert out["status"] == "CLARIFICATION_NEEDED"
    assert out["candidates"] == [SERIAL] and "JAZ01856" in out["answer"]
    assert out["tools"] == [] and ex.calls == [] and SIS_SEARCHES == []


@pytest.mark.asyncio
@pytest.mark.parametrize("behaviour,status", [("login_failed", "LOGIN_FAILED"),
                                              ("website_changed", "WEBSITE_CHANGED"),
                                              ("not_found", "NOT_FOUND")])
async def test_tool_errors_are_reported_and_cortex_invents_nothing(behaviour: str,
                                                                   status: str) -> None:
    repo = MemoryEquipmentRepository()
    ex = FakeExecutor(reply=grounded_reply)
    maia = MaiaCortexService(runtime=_runtime(ex), equipment_service=_service(repo, behaviour),
                             repo=repo)
    out = await maia.answer(f"Get equipment {SERIAL}")
    assert out["status"] == status
    assert out["facts"] == [] and ex.calls == []                # nothing to analyse


@pytest.mark.asyncio
async def test_persistence_failure_is_not_a_success() -> None:
    class Broken(MemoryEquipmentRepository):
        async def upsert(self, record, *, extraction=None):
            raise OSError("disk full")

    repo = Broken()
    ex = FakeExecutor(reply=grounded_reply)
    maia = MaiaCortexService(runtime=_runtime(ex), equipment_service=_service(repo), repo=repo)
    out = await maia.answer(f"Get equipment {SERIAL}")
    assert out["status"] == "PERSISTENCE_FAILED" and ex.calls == []


# ── 7. the HTTP surface ─────────────────────────────────────────────────────
def test_llm_route_is_cortex_and_says_so_when_unavailable(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("MAIA_REPOSITORY", "memory")
    monkeypatch.delenv("MAIA_CORTEX_ENABLED", raising=False)
    monkeypatch.delenv("SNOWFLAKE_CORTEX_ENABLED", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(app) as c:
            r = c.post("/v1/llm/messages", json={"messages": [{"role": "user",
                                                               "content": "hi"}]})
            assert r.status_code == 503
            assert r.json()["error"] == "CORTEX_UNAVAILABLE"
            assert r.json()["reason"] == "disabled"
            assert c.get("/v1/llm/status").json()["provider"] == "snowflake-cortex"
            s = c.get("/v1/cortex/status").json()
            assert s["configured"] is False and s["fix"]
    finally:
        get_settings.cache_clear()
