"""The Snowflake repository's real SQL, run against a local Snowflake emulator.

`fakesnow` executes Snowflake SQL on DuckDB. Passing here proves the MERGE,
PARSE_JSON, history, claim and run statements are coherent together; the live
check against the real account is `scripts/snowflake/snowflake_setup.py check`.
Skipped when fakesnow is not installed.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from app.models.schemas import EquipmentRecord, RecordStatus, RunRecord, RunStatus
from app.repositories.base import ExtractionArtifact
from app.repositories.snowflake_repo import SnowflakeEquipmentRepository
from tests.test_snowflake_store import SERIAL, SQL_DIR, _record

fakesnow = pytest.importorskip("fakesnow", reason="fakesnow not installed")


@pytest.fixture
def warehouse():
    import snowflake.connector
    from snowflake.connector.util_text import split_statements

    with fakesnow.patch():
        conn = snowflake.connector.connect(database="MAIA_PROD", schema="CORE")
        for name in ("001_core_schema.sql", "003_equipment_details_columns.sql",
                     "004_parts_and_cortex.sql"):
            text = (SQL_DIR / name).read_text(encoding="utf-8")
            for stmt, _ in split_statements(io.StringIO(text), remove_comments=True):
                stmt = stmt.strip()
                up = stmt.upper()
                # The emulator does not model clustering, retention or comments;
                # the setup script treats those as optional on a real account too.
                if not stmt or up.startswith(("CREATE DATABASE", "CREATE SCHEMA", "USE ",
                                              "COMMENT ON", "GRANT ")) \
                        or "CLUSTER BY" in up or "DATA_RETENTION" in up:
                    continue
                conn.cursor().execute(stmt)
        yield SnowflakeEquipmentRepository({"database": "MAIA_PROD", "schema": "CORE"})


@pytest.mark.asyncio
async def test_a_record_reads_back_exactly_as_it_was_validated(warehouse) -> None:
    from app.services.equipment_service import EquipmentService

    art = ExtractionArtifact(fields={"Machine Serial Number": SERIAL, "unmapped": "kept"},
                             final_url="https://sis2.cat.com/#/detail", page_title="SIS")
    assert await warehouse.upsert(_record(), extraction=art) is True
    row = await warehouse.get_current(SERIAL, "cat_sis")
    back = EquipmentRecord.model_validate(EquipmentService._clean_row(row))
    original = _record()
    # quality, provenance, specifications and parts all survive the round trip —
    # the columns alone would have lost quality and provenance.
    assert back.quality.score == pytest.approx(0.83)
    assert back.field_provenance == original.field_provenance
    assert back.specifications == original.specifications
    assert back.parts_data == original.parts_data
    assert back.engine_serial_number == "PRH04588"
    assert isinstance(row["retrieved_at"], datetime)     # a real timestamp, not text


@pytest.mark.asyncio
async def test_history_records_changes_not_polls(warehouse) -> None:
    assert await warehouse.upsert(_record()) is True
    assert await warehouse.upsert(_record()) is False                       # same data
    assert await warehouse.upsert(_record(equipment_model="C32B")) is True  # changed
    history = await warehouse.history(SERIAL)
    assert len(history) == 2
    assert {h["snapshot"]["equipment_model"] for h in history} == {"C32", "C32B"}
    assert (await warehouse.get_current(SERIAL, "cat_sis"))["equipment_model"] == "C32B"


@pytest.mark.asyncio
async def test_not_found_is_stored_and_never_offered_as_a_suggestion(warehouse) -> None:
    await warehouse.upsert(_record())
    await warehouse.mark_not_found("ZZZ00000", "cat_sis", "run_nf")
    row = await warehouse.get_current("ZZZ00000", "cat_sis")
    assert row["status"] == RecordStatus.NOT_FOUND.value
    assert await warehouse.known_serials() == [SERIAL]


@pytest.mark.asyncio
async def test_claims_block_a_second_run_until_released(warehouse) -> None:
    assert await warehouse.claim_idempotency("k", "run_a") is True
    assert await warehouse.claim_idempotency("k", "run_b") is False
    assert await warehouse.find_active_run("k") == "run_a"
    await warehouse.release_idempotency("k")
    assert await warehouse.claim_idempotency("k", "run_c") is True


@pytest.mark.asyncio
async def test_an_expired_claim_is_replaced_not_duplicated(warehouse) -> None:
    assert await warehouse.claim_idempotency("k", "run_old", ttl_s=-5) is True
    assert await warehouse.claim_idempotency("k", "run_new") is True
    rows = await warehouse._execute(
        "SELECT RUN_ID FROM AUTOMATION_RUN_CLAIMS WHERE CLAIM_KEY = 'k'", fetch="all")
    assert [r[0] for r in rows] == ["run_new"]


@pytest.mark.asyncio
async def test_run_audit_round_trips(warehouse) -> None:
    from app.models.schemas import StepRecord

    run = RunRecord(automation_run_id="run_audit", serial_number=SERIAL, source="cat_sis",
                    started_at=datetime.now(timezone.utc), status=RunStatus.RUNNING)
    await warehouse.save_run(run)
    run.steps_executed.append(StepRecord(seq=1, step="PERSIST", status="OK", duration_ms=12))
    run.status, run.completed_at = RunStatus.SUCCESS, datetime.now(timezone.utc)
    await warehouse.save_run(run)
    got = await warehouse.get_run("run_audit")
    assert got["status"] == "SUCCESS"
    assert got["steps_executed"][0]["step"] == "PERSIST"
    assert await warehouse.health() is True


# ── the setup script, end to end ────────────────────────────────────────────
def _load_setup_script():
    import importlib.util

    path = SQL_DIR.parents[1] / "scripts" / "snowflake" / "snowflake_setup.py"
    spec = importlib.util.spec_from_file_location("snowflake_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_creates_tables_checks_and_copies_only_sis_results(
        tmp_path, monkeypatch, capsys) -> None:
    import asyncio
    import sys

    from app.config import get_settings
    from app.repositories.local_json_repo import LocalJsonRepository

    secret = "setup-pw-never-printed"
    monkeypatch.setenv("MAIA_SNOWFLAKE_ACCOUNT", "acct")
    monkeypatch.setenv("MAIA_SNOWFLAKE_USER", "svc")
    monkeypatch.setenv("MAIA_SNOWFLAKE_PASSWORD_REF", "env://TEST_SF_PW")
    monkeypatch.setenv("TEST_SF_PW", secret)
    monkeypatch.setenv("MAIA_SNOWFLAKE_CONFIG_FILE", str(tmp_path / "none.txt"))
    monkeypatch.setenv("MAIA_LOCAL_STORE_DIR", str(tmp_path / "store"))
    get_settings.cache_clear()

    store = LocalJsonRepository(tmp_path / "store")
    asyncio.run(store.upsert(_record()))
    asyncio.run(store.upsert(_record(serial_number="FIX00001", source_system="local_fixture",
                                     machine_serial_number="FIX00001")))

    setup = _load_setup_script()
    # The emulator has no CURRENT_WAREHOUSE(); everything else is real.
    monkeypatch.setattr(setup, "context", lambda repo: dict(zip(
        ("database", "schema"), setup.run(repo, "SELECT CURRENT_DATABASE(), CURRENT_SCHEMA()",
                                          "one")), user="svc", role="MAIA_APP", warehouse="WH"))
    try:
        with fakesnow.patch(create_database_on_connect=True, create_schema_on_connect=True):
            monkeypatch.setattr(sys, "argv", ["snowflake_setup.py"])
            assert setup.main() == 0
            monkeypatch.setattr(sys, "argv", ["snowflake_setup.py", "show", "--serial", SERIAL])
            assert setup.main() == 0
            monkeypatch.setattr(sys, "argv", ["snowflake_setup.py", "show",
                                              "--serial", "FIX00001"])
            assert setup.main() == 1          # the fixture never reached the warehouse
    finally:
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "1 saved, 0 already present, 1 skipped" in out
    assert "READY" in out and "machine_serial_number  JAZ01865" in out
    assert secret not in out


# ── the whole lookup, with Snowflake as the store ───────────────────────────
@pytest.mark.asyncio
async def test_a_lookup_lands_in_snowflake_and_the_next_one_is_answered_from_it(
        warehouse, tmp_path) -> None:
    from app.adapters.registry import SourceRegistry
    from app.config import Settings
    from app.domain.freshness import FreshnessPolicy
    from app.models.schemas import EquipmentSearchRequest, Freshness
    from app.repositories.local_json_repo import LocalJsonRepository
    from app.repositories.mirrored_repo import MirroredRepository
    from app.services.equipment_service import EquipmentService
    from tests.conftest import FakeAdapter, FakePool

    local = LocalJsonRepository(tmp_path / "store")
    repo = MirroredRepository(primary=warehouse, mirror=local)
    registry = SourceRegistry()
    registry.register("cat_sis", "Caterpillar SIS",
                      {"_behaviour": "ok", "selector_version": "test-v1"},
                      FakeAdapter, precedence=10)
    service = EquipmentService(
        repo=repo, registry=registry, pool=FakePool(),
        freshness=FreshnessPolicy({"sources": {"cat_sis": {"ttl_days": 90}}}),
        settings=Settings(allow_live_automation=True, repository="snowflake",
                          run_deadline_ms=5000, step_timeout_ms=2000))

    first = await service.lookup(EquipmentSearchRequest(serial_number=SERIAL))
    assert first.persisted is True and first.cache.hit is False

    # In the warehouse: the current row, one history version, the run audit
    # with its outcome, and no claim left behind.
    row = await warehouse.get_current(SERIAL, "cat_sis")
    assert row["automation_run_id"] == first.automation_run_id
    assert row["machine_serial_number"] == SERIAL
    assert len(await warehouse.history(SERIAL)) == 1
    run = await warehouse.get_run(first.automation_run_id)
    assert run["status"] == "SUCCESS" and run["steps_executed"]
    claims = await warehouse._execute("SELECT COUNT(*) FROM AUTOMATION_RUN_CLAIMS",
                                      fetch="one")
    assert claims[0] == 0
    # …and the evidence folder next to it.
    assert list((tmp_path / "store").glob(f"{SERIAL}_*.json"))

    # Break the source: the second ask must come from Snowflake alone.
    registry.entry("cat_sis").config["_behaviour"] = "timeout"
    second = await service.lookup(EquipmentSearchRequest(serial_number=SERIAL))
    assert second.cache.hit is True
    assert second.attribution.freshness is Freshness.FRESH
    assert second.attribution.source_label == "Caterpillar SIS"
    assert second.data.engine_serial_number == first.data.engine_serial_number
    assert second.data.quality.score == pytest.approx(first.data.quality.score)


# ── Cortex on top of Snowflake: SIS once, then Snowflake, parts in their table ─
@pytest.mark.asyncio
async def test_cortex_turn_persists_to_snowflake_then_reads_it_back_without_sis(
        warehouse, tmp_path, caplog) -> None:
    import logging

    from app.adapters.registry import SourceRegistry
    from app.agent.state import ConversationContext
    from app.config import Settings
    from app.cortex.client import CortexRuntime
    from app.cortex.service import MaiaCortexService
    from app.domain.freshness import FreshnessPolicy
    from app.repositories.local_json_repo import LocalJsonRepository
    from app.repositories.mirrored_repo import MirroredRepository
    from app.services.equipment_service import EquipmentService
    from tests.conftest import SIS_SEARCHES, FakeAdapter, FakePool
    from tests.test_cortex import FakeExecutor, _cfg, grounded_reply

    SIS_SEARCHES.clear()
    warehouse.source_labels["cat_sis"] = "Caterpillar SIS"
    repo = MirroredRepository(primary=warehouse, mirror=LocalJsonRepository(tmp_path / "s"))
    registry = SourceRegistry()
    registry.register("cat_sis", "Caterpillar SIS",
                      {"_behaviour": "ok", "selector_version": "test-v1"}, FakeAdapter,
                      precedence=10)
    settings = Settings(allow_live_automation=True, repository="snowflake",
                        run_deadline_ms=5000, step_timeout_ms=2000, cortex_enabled=True,
                        cortex_mode="complete")
    service = EquipmentService(repo=repo, registry=registry, pool=FakePool(),
                               freshness=FreshnessPolicy({"sources": {"cat_sis":
                                                          {"ttl_days": 90}}}),
                               settings=settings)
    cortex = FakeExecutor(reply=grounded_reply)          # stands in for AI_COMPLETE
    runtime = CortexRuntime(settings, repo, sql_executor=cortex)
    runtime._cfg = _cfg()
    maia = MaiaCortexService(runtime=runtime, equipment_service=service, repo=repo)

    caplog.set_level(logging.INFO, logger="app")
    first = await maia.answer(f"Get equipment {SERIAL}")
    assert first["status"] == "SUCCESS" and first["origin"] == "sis"
    assert SIS_SEARCHES == [SERIAL]
    parts = await warehouse._execute(
        "SELECT PART_NUMBER, PART_NAME, GROUP_NAME, SOURCE, AUTOMATION_RUN_ID "
        "FROM EQUIPMENT_PARTS WHERE SERIAL_NUMBER = %(sn)s", {"sn": SERIAL}, fetch="all")
    assert parts == [("1000", "Engine", "Entire Group", "Caterpillar SIS",
                      first["automation_run_id"])]
    product = await warehouse._execute(
        "SELECT PRODUCT, NORMALIZED_DATA:serial_number::STRING FROM EQUIPMENT_DATA "
        "WHERE SERIAL_NUMBER = %(sn)s", {"sn": SERIAL}, fetch="one")
    assert product == ("Entire Group (JAZ01865)", SERIAL)

    second = await maia.answer(f"Get equipment {SERIAL}",
                               ConversationContext.model_validate(first["context"]))
    assert second["status"] == "SUCCESS" and second["origin"] == "store"
    assert SIS_SEARCHES == [SERIAL]                       # no second browser run
    assert len(cortex.calls) == 2                         # Cortex analysed both turns
    # The proof, as the service logs it:
    decisions = [getattr(r, "extra_fields", {}).get("action") for r in caplog.records
                 if r.getMessage() == "equipment.decision"]
    assert decisions[:2] == ["no_cache", "use_cache"]
