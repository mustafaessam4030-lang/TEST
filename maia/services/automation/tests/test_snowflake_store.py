"""Snowflake as Maia's store.

Two layers of proof:

  * pure tests — configuration, secret handling, reconnect, the mirror rule,
    the PERSIST mapping — that need no driver at all
  * emulator tests — the repository's real SQL (MERGE, PARSE_JSON, history,
    claims, runs) executed against `fakesnow`, a local Snowflake emulator. They
    are skipped when it is not installed. They prove the SQL is coherent; the
    live check against the real account is `scripts/snowflake/snowflake_setup.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.core.errors import AutomationError, ErrorCode
from app.models.schemas import EquipmentRecord, RunRecord, RunStatus
from app.repositories import snowflake_config
from app.repositories.memory_repo import MemoryEquipmentRepository
from app.repositories.mirrored_repo import MirroredRepository
from app.repositories.snowflake_repo import SnowflakeEquipmentRepository, _needs_reconnect

SQL_DIR = Path(__file__).resolve().parents[3] / "sql" / "snowflake"
SERIAL = "JAZ01865"
SECRET = "Sn0wfl@ke-Pa55-DO-NOT-LEAK"


def _record(**overrides) -> EquipmentRecord:
    data = {
        "serial_number": SERIAL, "source_system": "cat_sis",
        "retrieved_at": datetime(2026, 9, 22, 11, 30, tzinfo=timezone.utc),
        "automation_run_id": "run_01SNOW", "equipment_model": "C32",
        "machine_serial_number": SERIAL, "machine_build_date": "2014-08-02",
        "engine_serial_number": "PRH04588", "engine_build_date": "2014-06-30",
        "quality": {"score": 0.83, "required_present": 1, "required_total": 1},
        "specifications": [{"name": "Operating weight", "value": 36200, "unit": "kg"}],
        "parts_data": {"group_count": 1, "groups": [
            {"title": f"Product - Entire Group ({SERIAL})", "rows": [{"cells": ["1000"]}]}]},
        "field_provenance": {"machine_serial_number": {"selector_id": "detail.machine_serial",
                                                      "value_source": "dom"}},
    }
    data.update(overrides)
    return EquipmentRecord(**data)


# ── configuration: where the connection comes from ──────────────────────────
def _settings_with_file(tmp_path: Path, monkeypatch, body: str, **env) -> Settings:
    cfg = tmp_path / "snowflake.txt"
    cfg.write_text(body, encoding="utf-8")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(snowflake_config_file=str(cfg))


def test_config_file_is_read_and_explicit_env_wins(tmp_path, monkeypatch) -> None:
    settings = _settings_with_file(tmp_path, monkeypatch, """
        # comments and blank lines are fine
        account = xy12345.west-europe.azure
        username = MAIA_SVC
        authenticator = externalbrowser
        warehouse = FILE_WH
        database = MANTRAC_DB
        schema = MAIA
    """, MAIA_SNOWFLAKE_WAREHOUSE="ENV_WH")
    cfg = snowflake_config.load(settings)
    assert cfg.account == "xy12345.west-europe.azure"
    assert cfg.user == "MAIA_SVC"                      # alias accepted
    assert cfg.warehouse == "ENV_WH"                   # explicit env beats the file
    assert (cfg.database, cfg.schema) == ("MANTRAC_DB", "MAIA")
    assert cfg.role == "MAIA_APP"                      # default when neither sets it
    assert cfg.auth_method == "sso" and cfg.problems() == []
    kw = cfg.connect_kwargs()
    assert kw["authenticator"] == "externalbrowser"
    assert kw["client_session_keep_alive"] is True
    assert "password" not in kw


def test_password_never_appears_in_anything_printable(tmp_path, monkeypatch) -> None:
    settings = _settings_with_file(tmp_path, monkeypatch,
                                   f"account=a\nuser=u\npassword={SECRET}\n")
    cfg = snowflake_config.load(settings)
    assert cfg.auth_method == "password"
    kw = cfg.connect_kwargs()
    assert kw["password"] == SECRET                    # the driver gets it …
    for printable in (repr(kw), str(kw), repr(cfg), str(cfg.summary())):
        assert SECRET not in printable                 # … nothing else does
    assert cfg.secrets() == (SECRET,)


def test_password_can_come_from_an_env_reference(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MY_SF_PW", SECRET)
    monkeypatch.setenv("MAIA_SNOWFLAKE_PASSWORD_REF", "env://MY_SF_PW")
    settings = _settings_with_file(tmp_path, monkeypatch, "account=a\nuser=u\n")
    assert snowflake_config.load(settings).connect_kwargs()["password"] == SECRET


def test_keypair_auth_and_missing_key_is_reported(tmp_path, monkeypatch) -> None:
    settings = _settings_with_file(tmp_path, monkeypatch,
                                   "account=a\nuser=u\nprivate_key_file=nope.p8\n")
    cfg = snowflake_config.load(settings)
    assert cfg.auth_method == "keypair"
    assert any("private key file not found" in p for p in cfg.problems())
    key = tmp_path / "maia.p8"
    key.write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
    settings = _settings_with_file(tmp_path, monkeypatch,
                                   f"account=a\nuser=u\nprivate_key_file={key}\n"
                                   f"private_key_passphrase={SECRET}\n")
    cfg = snowflake_config.load(settings)
    assert cfg.problems() == []
    kw = cfg.connect_kwargs()
    assert kw["private_key_file"] == str(key) and kw["private_key_file_pwd"] == SECRET
    assert SECRET not in repr(kw)


def test_incomplete_config_refuses_to_start(tmp_path, monkeypatch) -> None:
    from app.repositories.factory import build_repository

    settings = _settings_with_file(tmp_path, monkeypatch, "account=a\n")
    settings.repository = "snowflake"
    with pytest.raises(RuntimeError) as exc:
        build_repository(settings)
    assert "user is not set" in str(exc.value)
    assert "no way to sign in" in str(exc.value)


def test_example_file_is_never_read_as_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(snowflake_config, "REPO_ROOT", tmp_path)
    (tmp_path / "snowflake.example.txt").write_text("account=EXAMPLE\n", encoding="utf-8")
    assert snowflake_config.find_config_file("snowflake.txt") is None
    (tmp_path / "snowflake.txt.txt").write_text("account=real\n", encoding="utf-8")
    assert snowflake_config.find_config_file("snowflake.txt").name == "snowflake.txt.txt"


def test_factory_mirrors_to_the_local_folder_by_default(tmp_path, monkeypatch) -> None:
    from app.repositories.factory import build_repository

    settings = _settings_with_file(tmp_path, monkeypatch,
                                   "account=a\nuser=u\nauthenticator=externalbrowser\n")
    settings.repository = "snowflake"
    settings.local_store_dir = str(tmp_path / "store")
    repo = build_repository(settings)
    assert isinstance(repo, MirroredRepository)
    assert isinstance(repo.primary, SnowflakeEquipmentRepository)
    settings.local_artifacts = False
    assert isinstance(build_repository(settings), SnowflakeEquipmentRepository)


# ── the driver boundary ─────────────────────────────────────────────────────
class _Err(Exception):
    def __init__(self, msg: str, errno: int | None = None) -> None:
        super().__init__(msg)
        self.errno = errno


def test_only_a_lost_session_triggers_a_reconnect() -> None:
    assert _needs_reconnect(_Err("Authentication token has expired.", 390114))
    assert _needs_reconnect(_Err("Connection is closed"))
    assert not _needs_reconnect(_Err("SQL compilation error: invalid identifier", 904))


class _FakeCursor:
    def __init__(self, conn) -> None:
        self.conn, self.rowcount, self.description = conn, 1, [("ONE",)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.conn.calls += 1
        if self.conn.fail_with:
            err, self.conn.fail_with = self.conn.fail_with, None
            raise err

    def fetchone(self):
        return (1,)

    def fetchall(self):
        return [(1,)]


class _FakeConn:
    def __init__(self, fail_with=None) -> None:
        self.calls, self.fail_with, self.closed = 0, fail_with, False

    def cursor(self):
        return _FakeCursor(self)

    def is_closed(self):
        return self.closed

    def close(self):
        self.closed = True


def _repo_with(conns: list[_FakeConn]) -> SnowflakeEquipmentRepository:
    repo = SnowflakeEquipmentRepository({}, secrets=(SECRET,))
    repo._connect = lambda: repo._conn if repo._conn and not repo._conn.closed else (  # type: ignore[method-assign]
        setattr(repo, "_conn", conns.pop(0)) or repo._conn)
    return repo


@pytest.mark.asyncio
async def test_expired_session_is_reopened_once_and_the_statement_retried() -> None:
    first = _FakeConn(fail_with=_Err("Authentication token has expired.", 390114))
    second = _FakeConn()
    repo = _repo_with([first, second])
    assert await repo._execute("SELECT 1", fetch="one") == (1,)
    assert first.closed and second.calls == 1


@pytest.mark.asyncio
async def test_driver_errors_become_database_error_with_secrets_masked() -> None:
    repo = _repo_with([_FakeConn(fail_with=_Err(f"bad thing near '{SECRET}'", 1003))])
    with pytest.raises(AutomationError) as exc:
        await repo._execute("SELECT 1")
    assert exc.value.code is ErrorCode.DATABASE_ERROR
    assert SECRET not in str(exc.value.details) and "***" in exc.value.details["driver_error"]


@pytest.mark.asyncio
async def test_a_warehouse_failure_at_persist_is_persistence_failed() -> None:
    """DATABASE_ERROR alone reads 'retry later'. At PERSIST it means the data
    was retrieved and then lost, and the caller must hear exactly that."""
    from app.services.equipment_service import EquipmentService

    class Down:
        async def upsert(self, record, *, extraction=None):
            raise AutomationError(ErrorCode.DATABASE_ERROR, "Data store operation failed.",
                                  details={"driver_error": "warehouse suspended"})

    service = EquipmentService.__new__(EquipmentService)
    service.repo = Down()
    with pytest.raises(AutomationError) as exc:
        await service._persist(_record(), None, serial=SERIAL, source="cat_sis")
    assert exc.value.code is ErrorCode.PERSISTENCE_FAILED
    assert exc.value.details["error_code"] == "DATABASE_ERROR"
    assert "warehouse suspended" in exc.value.details["error"]


# ── the mirror rule ─────────────────────────────────────────────────────────
class _Broken(MemoryEquipmentRepository):
    async def upsert(self, record, *, extraction=None):
        raise OSError("disk full")

    async def save_run(self, run):
        raise OSError("disk full")


class _CountingRuns(MemoryEquipmentRepository):
    def __init__(self) -> None:
        super().__init__()
        self.saves: list[str] = []

    async def save_run(self, run):
        self.saves.append(run.status.value)
        await super().save_run(run)


@pytest.mark.asyncio
async def test_primary_failure_fails_the_write_even_if_the_mirror_would_work() -> None:
    mirror = MemoryEquipmentRepository()
    repo = MirroredRepository(primary=_Broken(), mirror=mirror)
    with pytest.raises(OSError):
        await repo.upsert(_record())
    assert await mirror.get_current(SERIAL, "cat_sis") is None   # nothing half-written


@pytest.mark.asyncio
async def test_mirror_failure_never_fails_a_write_the_primary_holds() -> None:
    primary = MemoryEquipmentRepository()
    repo = MirroredRepository(primary=primary, mirror=_Broken())
    assert await repo.upsert(_record()) is True
    assert (await repo.get_current(SERIAL, "cat_sis"))["serial_number"] == SERIAL


@pytest.mark.asyncio
async def test_reads_come_from_the_primary_only() -> None:
    primary, mirror = MemoryEquipmentRepository(), MemoryEquipmentRepository()
    await mirror.upsert(_record())                   # only the mirror has it
    repo = MirroredRepository(primary=primary, mirror=mirror)
    assert await repo.get_current(SERIAL, "cat_sis") is None
    assert await repo.known_serials() == []


@pytest.mark.asyncio
async def test_run_heartbeat_stays_local_and_the_outcome_reaches_the_warehouse() -> None:
    from app.models.schemas import StepRecord

    primary, mirror = _CountingRuns(), _CountingRuns()
    repo = MirroredRepository(primary=primary, mirror=mirror)
    run = RunRecord(automation_run_id="r1", serial_number=SERIAL, source="cat_sis",
                    started_at=datetime.now(timezone.utc), status=RunStatus.RUNNING)
    await repo.save_run(run)                                       # start
    for name in ("LOGIN", "SEARCH", "EXTRACT"):
        run.steps_executed.append(StepRecord(seq=len(run.steps_executed) + 1, step=name, status="OK", duration_ms=5))
        await repo.save_run(run)                                   # heartbeat
    run.status = RunStatus.SUCCESS
    await repo.save_run(run)                                       # outcome
    assert mirror.saves == ["RUNNING"] * 4 + ["SUCCESS"]
    assert primary.saves == ["RUNNING", "SUCCESS"]
    assert (await repo.get_run("r1"))["status"] == "SUCCESS"
