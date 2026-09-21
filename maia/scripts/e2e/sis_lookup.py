#!/usr/bin/env python3
"""One real Caterpillar SIS lookup, saved to the local store, then reported.

    python scripts/e2e/sis_lookup.py --serial JAZ01865

It runs the same code the gateway runs — same adapter, same validation, same
repository — so what it proves is what Maia will get. It prints exactly what a
person needs to check afterwards: where the files are, how many fields came
back, and whether each required field was actually captured.

It refuses to run against anything but Caterpillar SIS, and it never prints a
username, a password, a cookie or a token.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "automation"))

from app.adapters.browser import BrowserPool  # noqa: E402
from app.adapters.cat_sis import CatSisAdapter  # noqa: E402
from app.adapters.registry import SourceRegistry  # noqa: E402
from app.adapters import selector_store  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.errors import AutomationError  # noqa: E402
from app.core.secrets import resolve_secret  # noqa: E402
from app.domain.freshness import FreshnessPolicy  # noqa: E402
from app.models.schemas import EquipmentSearchRequest  # noqa: E402
from app.repositories.local_json_repo import LocalJsonRepository  # noqa: E402
from app.services.capture_service import CaptureService  # noqa: E402
from app.services.equipment_service import EquipmentService  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import store_report  # noqa: E402

SIS_HOSTS = ("sis2.cat.com", "sis.cat.com")


def store_dir(settings) -> Path:
    path = Path(settings.local_store_dir)
    return path if path.is_absolute() else ROOT / path


def build(settings, store: LocalJsonRepository, pool: BrowserPool) -> EquipmentService:
    registry = SourceRegistry()
    cfg = settings.source_config("cat_sis")
    captured_path = os.environ.get(
        "MAIA_SIS_SELECTORS", str(Path(settings.sources_dir).parent / "sis_selectors.json"))
    try:
        cfg = selector_store.merge_into_config(cfg, selector_store.load(captured_path))
    except selector_store.SelectorStoreError as exc:
        print(f"  ! captured selectors unreadable ({exc}); using the YAML contract")
    base = str(cfg.get("base_url", ""))
    if not any(host in base for host in SIS_HOSTS):
        raise SystemExit(f"refusing to run: base_url is {base!r}, which is not Caterpillar SIS")
    registry.register("cat_sis", cfg.get("label", "Caterpillar SIS"), cfg,
                      lambda c: CatSisAdapter(c, secret_provider=resolve_secret),
                      enabled=True, precedence=int(cfg.get("precedence", 10)))
    store.source_labels.update({e["source_id"]: e["label"] for e in registry.snapshot()})
    return EquipmentService(repo=store, registry=registry, pool=pool,
                            freshness=FreshnessPolicy(settings.freshness_policy()),
                            settings=settings,
                            capture=CaptureService(registry=registry, settings=settings))


def report(store: LocalJsonRepository, serial: str, run_id: str) -> int:
    """Same report `show_sis_result.py` prints, so the two can never disagree."""
    path = store_report.find(store.results, serial, run_id)
    if path is None:
        print(f"\n✗ nothing was written to {store.results / f'{serial}_{run_id}.json'}")
        return 1
    print("\n" + store_report.render(path))
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--headed", action="store_true",
                    help="show the browser (needed if sign-in asks for MFA)")
    args = ap.parse_args()

    settings = get_settings()
    settings.allow_live_automation = True
    if args.headed:
        settings.headless = False
    store = LocalJsonRepository(store_dir(settings),
                                secrets=tuple(v for v in (os.environ.get("SIS_USERNAME"),
                                                          os.environ.get("SIS_PASSWORD")) if v))
    print(f"  store   : {store.results}")
    print(f"  headless: {settings.headless}")

    pool = BrowserPool(headless=settings.headless, max_contexts=1,
                       artifact_dir=settings.artifact_dir,
                       executable_path=settings.chromium_path)
    await pool.start()
    service = build(settings, store, pool)
    try:
        result = await service.lookup(EquipmentSearchRequest(
            serial_number=args.serial, source="cat_sis", wait=True,
            timeout_ms=120_000, reason="user_request", requested_by="sis_lookup.py"))
    except AutomationError as err:
        # Details can carry a URL and a selector; they never carry a secret.
        print(f"\n✗ {err.code.value}: {err.message}")
        print(f"  run id : {err.details.get('automation_run_id')}")
        print(f"  details: {json.dumps(err.details, default=str)[:600]}")
        return 2
    finally:
        await pool.stop()

    print(f"\n✓ {result.status} in {result.execution_time_ms} ms "
          f"(persisted={result.persisted})")
    return report(store, result.data.serial_number, result.automation_run_id)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
