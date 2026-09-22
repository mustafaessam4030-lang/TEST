#!/usr/bin/env python3
"""Connect Maia to Snowflake: check, create the tables, copy local results in.

    python scripts/snowflake/snowflake_setup.py            # = setup (all of the below)
    python scripts/snowflake/snowflake_setup.py check      # connection + tables + write probe
    python scripts/snowflake/snowflake_setup.py apply      # create/upgrade the tables (idempotent)
    python scripts/snowflake/snowflake_setup.py backfill   # copy logs/sis-results/*.json in
    python scripts/snowflake/snowflake_setup.py show --serial JAZ01865
    python scripts/snowflake/snowflake_setup.py cortex     # one tiny live Cortex call

The connection comes from snowflake.txt (or MAIA_SNOWFLAKE_* variables) — see
snowflake.example.txt. Only the account, user, auth METHOD, role, warehouse,
database and schema are ever printed; a password, passphrase or token never is.

Only records whose source is Caterpillar SIS are copied. The offline test
fixture is never written to the warehouse.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "automation"))

from app.config import get_settings  # noqa: E402
from app.core.errors import AutomationError  # noqa: E402
from app.repositories import snowflake_config  # noqa: E402

SQL_DIR = ROOT / "sql" / "snowflake"
# Order matters: 003 adds columns to a table 001 creates.
CORE_FILES = ("001_core_schema.sql", "003_equipment_details_columns.sql",
              "004_parts_and_cortex.sql")
VIEWS_FILE = "002_views_and_governance.sql"

REQUIRED_TABLES = ("EQUIPMENT_DATA", "EQUIPMENT_DATA_HISTORY", "EQUIPMENT_PARTS",
                   "AUTOMATION_RUNS", "AUTOMATION_RUN_CLAIMS")
REQUIRED_COLUMNS = ("SERIAL_NUMBER", "SOURCE_SYSTEM", "RAW_DATA", "DATA_HASH", "STATUS",
                    "RETRIEVED_AT", "MACHINE_SERIAL_NUMBER", "MACHINE_BUILD_DATE",
                    "ENGINE_SERIAL_NUMBER", "ENGINE_BUILD_DATE", "PARTS_DATA", "PRODUCT",
                    "NORMALIZED_DATA")
# Only real SIS results go to the warehouse. `local_fixture` is a test page.
WAREHOUSE_SOURCES = ("cat_sis",)
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

OK, WARN, FAIL = "✓", "!", "✗"


def say(mark: str, text: str) -> None:
    print(f"  {mark} {text}")


# ── connection ──────────────────────────────────────────────────────────────
def open_repo() -> tuple[Any, Any]:
    settings = get_settings()
    cfg = snowflake_config.load(settings)
    print("\n  MAIA — Snowflake\n  " + "─" * 66)
    s = cfg.summary()
    say("·", f"config    : {s['config_file'] or 'environment variables'}")
    say("·", f"account   : {s['account'] or '(not set)'}")
    say("·", f"user      : {s['user'] or '(not set)'}    sign-in: {s['auth']}")
    say("·", f"role/wh   : {s['role']} / {s['warehouse']}")
    say("·", f"target    : {s['database']}.{s['schema']}")
    problems = cfg.problems()
    if problems:
        for p in problems:
            say(FAIL, p)
        print("\n  Fill in snowflake.txt (copy snowflake.example.txt) and run this again.\n")
        raise SystemExit(2)
    for name in ("database", "schema"):
        if not IDENT.match(str(getattr(cfg, name))):
            say(FAIL, f"{name} {getattr(cfg, name)!r} must be a plain identifier")
            raise SystemExit(2)
    try:
        import snowflake.connector  # noqa: F401
    except ImportError:
        say(FAIL, "the Snowflake driver is not installed")
        print("      → .venv\\Scripts\\python.exe -m pip install snowflake-connector-python\n")
        raise SystemExit(2)
    from app.repositories.snowflake_repo import SnowflakeEquipmentRepository

    if cfg.auth_method == "sso":
        say("·", "a browser window opens for company sign-in (first time only)")
    return SnowflakeEquipmentRepository(cfg.connect_kwargs(), secrets=cfg.secrets()), cfg


def run(repo: Any, sql: str, fetch: str | None = None) -> Any:
    return repo._run_sync(sql, None, fetch)


def context(repo: Any) -> dict[str, Any]:
    row = run(repo, "SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), "
                    "CURRENT_DATABASE(), CURRENT_SCHEMA()", "one")
    return dict(zip(("user", "role", "warehouse", "database", "schema"), row or ()))


def ensure_schema(repo: Any, cfg: Any) -> bool:
    ctx = context(repo)
    if ctx.get("schema"):
        return True
    say(WARN, f"{cfg.database}.{cfg.schema} is not usable yet — trying to create it")
    try:
        run(repo, f"CREATE DATABASE IF NOT EXISTS {cfg.database}")
        run(repo, f"CREATE SCHEMA IF NOT EXISTS {cfg.database}.{cfg.schema}")
        run(repo, f"USE SCHEMA {cfg.database}.{cfg.schema}")
        say(OK, f"created {cfg.database}.{cfg.schema}")
        return True
    except AutomationError as err:
        say(FAIL, f"cannot create it with role {ctx.get('role')}: "
                  f"{err.details.get('driver_error', err.message)}")
        print(f"      → ask your Snowflake admin to run:\n"
              f"          CREATE SCHEMA {cfg.database}.{cfg.schema};\n"
              f"          GRANT USAGE ON DATABASE {cfg.database} TO ROLE {ctx.get('role')};\n"
              f"          GRANT ALL ON SCHEMA {cfg.database}.{cfg.schema} "
              f"TO ROLE {ctx.get('role')};")
        return False


# ── apply ───────────────────────────────────────────────────────────────────
def statements(path: Path, database: str, schema: str) -> list[str]:
    from snowflake.connector.util_text import split_statements

    text = path.read_text(encoding="utf-8")
    # The files are written for MAIA_PROD.CORE; run them where this deployment
    # actually lives. Identifiers were validated above.
    text = text.replace("MAIA_PROD.CORE", f"{database}.{schema}").replace("MAIA_PROD", database)
    out = []
    for stmt, _ in split_statements(io.StringIO(text), remove_comments=True):
        stmt = stmt.strip().rstrip(";").strip()
        if not stmt:
            continue
        head = " ".join(stmt.split()[:3]).upper()
        # The connection already points at the target; never switch or create
        # databases from a migration file.
        if head.startswith(("USE ", "CREATE DATABASE", "CREATE SCHEMA")):
            continue
        out.append(stmt)
    return out


def optional_statement(stmt: str) -> bool:
    """Tuning and governance: good to have, never a reason to stop."""
    up = " ".join(stmt.split()).upper()
    if "ADD COLUMN" in up:
        return False             # a missing column breaks writes; that is fatal
    return (" CLUSTER BY " in up or "DATA_RETENTION_TIME" in up
            or up.startswith(("CREATE ROLE", "GRANT ", "REVOKE ", "CREATE MASKING",
                              "CREATE TAG", "ALTER TAG", "CREATE ROW ACCESS",
                              "COMMENT ON", "ALTER TABLE")))


def governance_statement(stmt: str) -> bool:
    """Roles, grants and row policies change who can see what, account-wide.
    They are an administrator's decision, so they only run when asked."""
    up = " ".join(stmt.split()).upper()
    return (up.startswith(("CREATE ROLE", "GRANT ", "REVOKE ", "CREATE ROW ACCESS"))
            or "ROW ACCESS POLICY" in up)


def apply(repo: Any, cfg: Any, with_views: bool = True, with_grants: bool = False) -> bool:
    print("\n  Tables")
    if not ensure_schema(repo, cfg):
        return False
    ok = True
    files = list(CORE_FILES) + ([VIEWS_FILE] if with_views else [])
    for name in files:
        done = skipped = held = 0
        for stmt in statements(SQL_DIR / name, cfg.database, cfg.schema):
            if governance_statement(stmt) and not with_grants:
                held += 1
                continue
            try:
                run(repo, stmt)
                done += 1
            except AutomationError as err:
                first = " ".join(stmt.split())[:70]
                if name == VIEWS_FILE or optional_statement(stmt):
                    skipped += 1
                    reason = str(err.details.get("driver_error", "")).splitlines()[0][:80]
                    say(WARN, f"{name}: skipped `{first}…` ({reason})")
                else:
                    ok = False
                    say(FAIL, f"{name}: `{first}…` — {err.details.get('driver_error')}")
        say(OK if ok else FAIL, f"{name}: {done} statement(s) applied"
                                + (f", {skipped} optional skipped" if skipped else "")
                                + (f", {held} grant/policy statement(s) left for an admin "
                                   "(--with-grants)" if held else ""))
    return ok


# ── check ───────────────────────────────────────────────────────────────────
def check(repo: Any, cfg: Any) -> bool:
    print("\n  Connection")
    try:
        ctx = context(repo)
    except AutomationError as err:
        say(FAIL, f"could not connect: {err.details.get('driver_error', err.message)}")
        print("      → check account/user in snowflake.txt, VPN, and that the user "
              "may use this role and warehouse")
        return False
    say(OK, f"signed in as {ctx['user']}  role {ctx['role']}  warehouse {ctx['warehouse']}")
    if not ctx.get("warehouse"):
        say(FAIL, f"warehouse {cfg.warehouse} is not usable by role {ctx['role']}")
        return False
    if not ctx.get("schema"):
        say(FAIL, f"{cfg.database}.{cfg.schema} does not exist or role {ctx['role']} "
                  "cannot use it — run:  snowflake_setup.py apply")
        return False
    say(OK, f"using {ctx['database']}.{ctx['schema']}")

    print("\n  Tables")
    rows = run(repo, "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                     "WHERE TABLE_SCHEMA = CURRENT_SCHEMA()", "all") or []
    present = {str(r[0]).upper() for r in rows}
    missing = [t for t in REQUIRED_TABLES if t not in present]
    for t in REQUIRED_TABLES:
        say(OK if t in present else FAIL, t)
    if missing:
        print("      → run:  snowflake_setup.py apply")
        return False
    cols = run(repo, "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                     "WHERE TABLE_SCHEMA = CURRENT_SCHEMA() AND TABLE_NAME = 'EQUIPMENT_DATA'",
               "all") or []
    have = {str(r[0]).upper() for r in cols}
    lacking = [c for c in REQUIRED_COLUMNS if c not in have]
    say(OK if not lacking else FAIL, "EQUIPMENT_DATA columns"
        + (f" — missing {', '.join(lacking)}  → run: snowflake_setup.py apply" if lacking else ""))
    if lacking:
        return False

    print("\n  Write access")
    async def probe() -> bool:
        key = "maia-setup-probe"
        claimed = await repo.claim_idempotency(key, "setup-probe", ttl_s=30)
        await repo.release_idempotency(key)
        return claimed
    try:
        asyncio.run(probe())
        say(OK, "can write and delete (AUTOMATION_RUN_CLAIMS probe)")
    except AutomationError as err:
        say(FAIL, f"cannot write: {err.details.get('driver_error', err.message)}")
        print(f"      → GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "
              f"{cfg.database}.{cfg.schema} TO ROLE {ctx['role']};")
        return False
    count = run(repo, "SELECT COUNT(*) FROM EQUIPMENT_DATA WHERE STATUS <> 'NOT_FOUND'", "one")
    say(OK, f"{count[0] if count else 0} machine(s) stored in EQUIPMENT_DATA")
    return True


# ── cortex ──────────────────────────────────────────────────────────────────
def cortex(repo: Any) -> bool:
    """One minimal live Cortex call (a few tokens), through the same code path
    Maia uses. Proves the account, role, region and model actually work."""
    from app.cortex.client import CortexRuntime

    settings = get_settings()
    print("\n  Snowflake Cortex (Maia's runtime intelligence)")
    runtime = CortexRuntime(settings, repo)
    status = runtime.status()
    if not status["configured"]:
        say(FAIL, f"not usable: {status['reason']}")
        print(f"      → {status['fix']}")
        return False
    mode = status["mode"]
    say("·", f"mode {mode} · model "
             f"{status['model'] if mode == 'complete' else status['agent_model']}")
    try:
        if mode == "agent":
            msg = asyncio.run(runtime.agent_run(
                [{"role": "user", "content": [{"type": "text", "text": "Reply with: OK"}]}],
                tools=[], instructions={"response": "Reply with exactly: OK"}))
            reply = " ".join(i.get("text", "") for i in msg.get("content") or []
                             if i.get("type") == "text")
        else:
            reply = asyncio.run(runtime.complete("Reply with exactly: OK", max_tokens=5))
    except AutomationError as err:
        say(FAIL, f"Cortex call failed: {err.details.get('reason')} — "
                  f"{str(err.details.get('detail') or '')[:120]}")
        print(f"      → {err.details.get('fix')}")
        return False
    say(OK, f"Cortex answered ({str(reply).strip()[:20]!r})")
    return True


# ── backfill ────────────────────────────────────────────────────────────────
def local_documents(store: Path) -> list[dict[str, Any]]:
    docs = []
    for path in sorted(store.glob("*.json")):
        if path.name.startswith("_") or path.name == "index.json":
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        record = doc.get("record") if isinstance(doc, dict) else None
        if not isinstance(record, dict) or not record.get("serial_number"):
            continue
        docs.append(doc)
    # Oldest first, so the newest lookup of a serial ends up as its current row
    # and each earlier one becomes a history version.
    return sorted(docs, key=lambda d: str(d["record"].get("retrieved_at") or ""))


def backfill(repo: Any, dry_run: bool = False) -> bool:
    from app.models.schemas import EquipmentRecord, RecordStatus
    from app.repositories.base import ExtractionArtifact

    settings = get_settings()
    store = Path(settings.local_store_dir)
    store = store if store.is_absolute() else ROOT / store
    results = store / "results" if (store / "results").is_dir() else store
    print(f"\n  Copy local results → Snowflake   ({results})")
    docs = local_documents(results)
    if not docs:
        say(OK, "nothing to copy (no saved lookups yet)")
        return True
    written = unchanged = skipped = 0
    for doc in docs:
        rec = doc["record"]
        if rec.get("source_system") not in WAREHOUSE_SOURCES:
            skipped += 1        # never the test fixture
            continue
        if rec.get("status") == RecordStatus.NOT_FOUND.value:
            skipped += 1
            continue
        try:
            record = EquipmentRecord.model_validate(rec)
        except Exception as exc:  # noqa: BLE001 - an old file shape is skipped, not guessed at
            skipped += 1
            say(WARN, f"{rec.get('serial_number')}: not a valid record ({str(exc)[:80]})")
            continue
        raw = doc.get("raw_data") or {}
        extraction = ExtractionArtifact(
            fields=raw.get("extracted_fields") or {},
            specifications=raw.get("specifications") or [],
            parts_data=raw.get("parts_data"),
            final_url=doc.get("final_url"), page_title=doc.get("page_title"),
            selector_version=doc.get("selector_version"))
        if dry_run:
            say("·", f"would copy {record.serial_number} ({record.retrieved_at:%Y-%m-%d %H:%M})")
            continue
        changed = asyncio.run(repo.upsert(record, extraction=extraction))
        written += int(changed)
        unchanged += int(not changed)
        say(OK, f"{record.serial_number}  {'saved' if changed else 'already there'}")
    say(OK, f"{written} saved, {unchanged} already present, {skipped} skipped "
            "(fixture / not-found / unreadable)")
    return True


# ── show ────────────────────────────────────────────────────────────────────
def show(repo: Any, serial: str) -> bool:
    from app.agent.entities import normalize_serial

    serial = normalize_serial(serial)
    rows = asyncio.run(repo.get_any_source(serial))
    print(f"\n  {serial} in Snowflake")
    if not rows:
        say(FAIL, "no row in EQUIPMENT_DATA")
        return False
    for rec in rows:
        say(OK, f"source {rec.get('source_system')}   status {rec.get('status')}   "
                f"retrieved {rec.get('retrieved_at')}")
        for key in ("equipment_model", "machine_serial_number", "machine_build_date",
                    "engine_serial_number", "engine_build_date"):
            print(f"      {key:<22} {rec.get(key) if rec.get(key) is not None else '—'}")
        parts = rec.get("parts_data") or {}
        groups = parts.get("groups") if isinstance(parts, dict) else None
        print(f"      {'parts groups':<22} {len(groups) if isinstance(groups, list) else '—'}")
        quality = (rec.get("quality") or {}).get("score")
        print(f"      {'quality':<22} {quality if quality is not None else '—'}")
        hist = asyncio.run(repo.history(serial, rec.get("source_system"), 50))
        print(f"      {'versions in history':<22} {len(hist)}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="setup",
                    choices=("setup", "check", "apply", "backfill", "show", "cortex"))
    ap.add_argument("--serial")
    ap.add_argument("--no-views", action="store_true",
                    help="skip the reporting views and grants (002)")
    ap.add_argument("--with-grants", action="store_true",
                    help="also create roles, grants and the row access policy (admin only)")
    ap.add_argument("--dry-run", action="store_true", help="backfill: list, do not write")
    args = ap.parse_args()
    # Every failure is reported below in plain words; the service's structured
    # log lines would only repeat them.
    logging.getLogger("app").setLevel(logging.CRITICAL)

    repo, cfg = open_repo()
    try:
        # Connect once up front, so a wrong account or a refused sign-in is
        # reported as that — not as a failure of whatever step ran first.
        try:
            context(repo)
        except AutomationError as err:
            say(FAIL, f"could not connect: {err.details.get('driver_error', err.message)}")
            print("      → check account and user in snowflake.txt, VPN, and that this "
                  "user may use the role and warehouse")
            print()
            return 1
        if args.command == "check":
            ok = check(repo, cfg)
        elif args.command == "apply":
            ok = apply(repo, cfg, with_views=not args.no_views, with_grants=args.with_grants) and check(repo, cfg)
        elif args.command == "cortex":
            ok = cortex(repo)
        elif args.command == "backfill":
            ok = backfill(repo, dry_run=args.dry_run)
        elif args.command == "show":
            if not args.serial:
                ap.error("show needs --serial")
            ok = show(repo, args.serial)
        else:
            ok = (apply(repo, cfg, with_views=not args.no_views, with_grants=args.with_grants)
                  and check(repo, cfg) and backfill(repo))
            # Cortex is reported, not required for the tables to be ready.
            cortex(repo)
    finally:
        repo.close()
    print("  " + "─" * 66)
    print("  READY — Maia saves every lookup to Snowflake." if ok and args.command in
          ("setup", "check", "apply") else ("  done." if ok else "  NOT READY — see ✗ above."))
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
