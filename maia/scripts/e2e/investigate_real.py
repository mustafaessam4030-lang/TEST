#!/usr/bin/env python3
"""REAL end-to-end check: Parts + Troubleshooting + 3D Model on Caterpillar SIS.

    python scripts/e2e/investigate_real.py --serial JAZ01865

What it does, with the existing automation and nothing else:
  1. one real SIS lookup (same adapter, same session, same store as Maia), with
     the extra INVESTIGATE step: Troubleshooting tab, then 3D Model tab
  2. Maia's deterministic investigation on the saved result:
     "Analyze …", "Show me parts for …", "Check troubleshooting for …", "Show … in 3D"
  3. a PASS / FAIL table computed from what actually came back

It refuses anything that is not Caterpillar SIS, never uses sample data, and
never prints a username, password, cookie or token. The 3D viewer discovery
report is saved next to the result: logs/sis-results/<SERIAL>_<RUN>/investigation.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "automation"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.adapters.browser import BrowserPool  # noqa: E402
from app.analysis.investigation import InvestigationService  # noqa: E402
from app.analysis.snapshots import LocalStoreSnapshots  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.errors import AutomationError  # noqa: E402
from app.models.schemas import EquipmentSearchRequest, SearchMode  # noqa: E402
from app.repositories.factory import build_local_store  # noqa: E402
from sis_lookup import build  # noqa: E402  (same wiring as SIS-LOOKUP, refuses non-SIS)


def verdicts(result_ok: bool, snap, inv: dict | None, tr_view: dict | None) -> dict[str, str]:
    v: dict[str, str] = {}
    v["REAL SIS"] = "PASS" if result_ok and snap is not None and snap.source_system == "cat_sis" \
        and "cat.com" in str(snap.metadata.get("final_url") or "") else "FAIL"
    rows = ((snap.parts_data or {}).get("groups") if snap else None) or []
    v["PARTS"] = "PASS" if sum(len(g.get("rows") or []) for g in rows) > 0 else "FAIL"
    tr = (inv or {}).get("troubleshooting") or {}
    v["TROUBLESHOOTING"] = {"CAPTURED": "PASS", "COUNTS_ONLY": "PARTIAL (counts, no rows)"}.get(
        tr.get("status"), f"FAIL ({tr.get('reason') or tr.get('status') or 'not read'})")
    m = (inv or {}).get("model_3d") or {}
    opened = bool((m.get("tab") or {}).get("opened"))
    v["3D MODEL"] = "PASS" if opened and (m.get("viewer") or {}).get("canvases") else (
        f"FAIL ({m.get('reason') or m.get('status') or 'not opened'})")
    mapping = (tr_view or {}).get("mapping") or (inv or {}).get("mapping") or []
    status = m.get("status")
    if any(x.get("status") == "VERIFIED" for x in mapping) and (inv or {}).get("highlight", {}).get("ok"):
        v["3D COMPONENT MAPPING"] = "VERIFIED"
    elif status in ("VISUAL_ONLY", "NO_VIEWER_FOUND", "NOT_AVAILABLE") or not m:
        v["3D COMPONENT MAPPING"] = "NOT AVAILABLE"
    else:
        v["3D COMPONENT MAPPING"] = "NEEDS MORE INVESTIGATION"
    return v


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", default="JAZ01865")
    ap.add_argument("--headless", action="store_true", help="hide the browser")
    args = ap.parse_args()

    settings = get_settings()
    settings.allow_live_automation = True
    settings.headless = bool(args.headless)
    store = build_local_store(settings)
    print(f"  store   : {store.results}\n  serial  : {args.serial}\n  browser : "
          f"{'hidden' if args.headless else 'VISIBLE — watch it work; type an MFA code there if asked'}\n")

    pool = BrowserPool(headless=settings.headless, max_contexts=1,
                       artifact_dir=settings.artifact_dir, executable_path=settings.chromium_path)
    await pool.start()
    ok, run_id = False, None
    try:
        service = build(settings, store, pool)
        result = await service.lookup(EquipmentSearchRequest(
            serial_number=args.serial, source="cat_sis", wait=True, timeout_ms=120_000,
            mode=SearchMode.FORCE_REFRESH, reason="user_request",
            requested_by="investigate_real.py",
            investigate=["troubleshooting", "model_3d"]))
        ok, run_id = True, result.automation_run_id
        print(f"✓ SIS run {run_id} finished in {result.execution_time_ms} ms")
    except AutomationError as err:
        print(f"✗ {err.code.value}: {err.message}\n  details: "
              f"{json.dumps(err.details, default=str)[:500]}")
    finally:
        await pool.stop()

    source = LocalStoreSnapshots(store.results)
    snaps = source.snapshots(args.serial)
    snap = next((s for s in reversed(snaps) if s.run_id == run_id), None) if run_id else None
    inv = snap.investigation if snap else None
    if snap and inv:
        folder = store.results / f"{snap.file[:-5]}"
        folder.mkdir(exist_ok=True)
        (folder / "investigation.json").write_text(json.dumps(inv, indent=2, default=str),
                                                   encoding="utf-8")
        print(f"  discovery report: {folder / 'investigation.json'}")

    maia = InvestigationService(source)
    tr_view = None
    for question in (f"Analyze {args.serial}", f"Show me parts for {args.serial}",
                     f"Check troubleshooting for {args.serial}", f"Show {args.serial} in 3D"):
        out = maia.handle(question)
        print(f"\n\n>>> {question}\n    [{out.get('intent')} · {out.get('status')}]\n")
        print(out.get("text") or "")
        if question.startswith("Check troubleshooting"):
            tr_view = out.get("model_3d")

    v = verdicts(ok, snap, inv, tr_view)
    m = (inv or {}).get("model_3d") or {}
    viewer = m.get("viewer") or {}
    print("\n" + "═" * 60 + "\n  3D VIEWER DISCOVERY (what the SIS viewer actually exposes)")
    print(f"  canvases        : {len(viewer.get('canvases') or [])}")
    print(f"  viewer libraries: {', '.join(sorted((viewer.get('libs') or {}).keys())) or 'none detected'}")
    print(f"  model files     : {len(viewer.get('model_resources') or [])}")
    print(f"  component names : {len(m.get('component_names') or [])} "
          f"(source: {m.get('component_names_source') or 'none'})")
    print(f"  click probe     : {(m.get('click_probe') or {}).get('new_text') or 'no new text'}")
    print("═" * 60)
    for key in ("REAL SIS", "PARTS", "TROUBLESHOOTING", "3D MODEL", "3D COMPONENT MAPPING"):
        print(f"  {key:<22} {v[key]}")
    print("═" * 60)
    return 0 if v["REAL SIS"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
