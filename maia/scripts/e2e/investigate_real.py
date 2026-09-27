#!/usr/bin/env python3
"""REAL SIS integration check: Maia's own chat path, against the live Caterpillar SIS.

    python scripts/e2e/investigate_real.py --serial JAZ01865
    python scripts/e2e/investigate_real.py --serial JAZ01865 --ask "Check the troubleshooting for JAZ01865"
    python scripts/e2e/investigate_real.py --serial JAZ01865 --expect 36-1-5 --expect "Cylinder #1 Injector"

What runs — the same code the chat uses, nothing mocked:
  1. the sentence goes through Maia's intent router
  2. InvestigationRunner → EquipmentService.lookup (the existing SIS adapter, browser
     pool and saved session) with the Troubleshooting and 3D Model sections, FORCE_REFRESH
  3. the result is saved by the existing store; Maia answers from that saved run
  4. Parts, "Analyze" and the 3D step are answered from the run just made

`--expect` only CHECKS whether a value appears in what was read from the live page;
nothing is ever filled in from it.

It refuses any base_url that is not Caterpillar SIS, never uses sample or fixture data,
and never prints a username, password, cookie or token. The full discovery report is
saved next to the result: logs/sis-results/<SERIAL>_<RUN>/investigation.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "automation"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.adapters.browser import BrowserPool  # noqa: E402
from app.analysis.investigation import InvestigationService  # noqa: E402
from app.analysis.snapshots import LocalStoreSnapshots  # noqa: E402
from app.analysis.troubleshooting_analyzer import analyze_troubleshooting  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.repositories.factory import build_local_store  # noqa: E402
from app.services.troubleshooting_service import InvestigationRunner  # noqa: E402
from sis_lookup import build  # noqa: E402  (same wiring as SIS-LOOKUP, refuses non-SIS)

LINE = "═" * 64
_SECRET_PARAM = re.compile(r"([?&](?:[^=&#]*(?:token|auth|code|session|sig|key|state)[^=&#]*)=)[^&#]*",
                           re.I)


def safe_url(url: str | None) -> str:
    return _SECRET_PARAM.sub(r"\1<redacted>", url or "") or "—"


def pf(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def verdicts(snap, inv: dict | None) -> dict[str, str]:
    """PASS only from what the live run saved. Mapping is VERIFIED only when a
    component was matched AND the viewer highlighted it."""
    real = snap is not None and snap.source_system == "cat_sis" \
        and "cat.com" in str(snap.metadata.get("final_url") or "")
    groups = ((snap.parts_data or {}).get("groups") if snap else None) or []
    part_rows = sum(len(g.get("rows") or []) for g in groups)
    tr = (inv or {}).get("troubleshooting") or {}
    codes = [e for e in analyze_troubleshooting(tr)["entries"] if e.get("code")] if tr else []
    m = (inv or {}).get("model_3d") or {}
    reached_3d = bool((m.get("tab") or {}).get("opened"))
    mapping = (inv or {}).get("mapping") or []
    verified = any(x.get("status") == "VERIFIED" for x in mapping) \
        and bool(((inv or {}).get("highlight") or {}).get("ok"))
    return {
        "Caterpillar SIS run": pf(real),
        "Parts retrieved": f"{pf(real and part_rows > 0)} ({part_rows} part rows)",
        "Troubleshooting page reached": pf(real and bool((tr.get("tab") or {}).get("opened"))),
        "Troubleshooting data extracted": f"{pf(real and tr.get('status') == 'CAPTURED' and bool(codes))}"
                                          f" ({len(codes)} code records"
                                          f"{'; ' + tr['reason'] if tr.get('reason') else ''})",
        "3D Model reached": pf(real and reached_3d),
        "3D viewer inspected": f"{pf(real and reached_3d and m.get('status') not in (None, 'NOT_AVAILABLE'))}"
                               f" ({m.get('status') or 'not run'})",
        "Component mapping": "VERIFIED" if real and verified else "NOT AVAILABLE",
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--serial", default="JAZ01865")
    ap.add_argument("--ask", help='the sentence to send (default: "Check the troubleshooting for <serial>")')
    ap.add_argument("--expect", action="append", default=[],
                    help="a value to look for in the live troubleshooting rows (check only)")
    ap.add_argument("--headless", action="store_true", help="hide the browser")
    args = ap.parse_args()
    serial = args.serial.strip().upper()
    sentence = args.ask or f"Check the troubleshooting for {serial}"

    settings = get_settings()
    settings.allow_live_automation = True
    settings.headless = bool(args.headless)
    store = build_local_store(settings)
    print(f"{LINE}\n  REAL SIS INTEGRATION TEST (live Caterpillar SIS — not the replica)\n{LINE}")
    print(f"  store   : {store.results}\n  sentence: {sentence}\n  browser : "
          f"{'hidden' if args.headless else 'VISIBLE — watch it; type an MFA code there if SIS asks'}\n")

    source = LocalStoreSnapshots(store.results)
    pool = BrowserPool(headless=settings.headless, max_contexts=1,
                       artifact_dir=settings.artifact_dir, executable_path=settings.chromium_path)
    await pool.start()
    try:
        runner = InvestigationRunner(build(settings, store, pool), source,
                                     requested_by="investigate_real.py")
        out = await runner.ask(sentence, live=True)
    except Exception as exc:                              # noqa: BLE001 — report, never hide
        out = {"status": "SIS_FAILED", "reason": f"{type(exc).__name__}: {str(exc)[:300]}",
               "trace": []}
    finally:
        await pool.stop()

    trace = out.get("trace") or []
    route = next((t for t in trace if t["step"] == "route"), {})
    look = next((t for t in trace if t["step"] == "sis_lookup"), {})
    run_id = look.get("run_id")
    snap = next((s for s in reversed(source.snapshots(serial)) if s.run_id == run_id), None) \
        if run_id else None
    inv = snap.investigation if snap else None
    tr = (inv or {}).get("troubleshooting") or {}
    entries = [e for e in analyze_troubleshooting(tr)["entries"] if e.get("code")] if tr else []
    if snap and inv:
        folder = store.results / snap.file[:-5]
        folder.mkdir(exist_ok=True)
        (folder / "investigation.json").write_text(json.dumps(inv, indent=2, default=str),
                                                   encoding="utf-8")

    print(f"\n{LINE}\n  WHAT HAPPENED\n{LINE}")
    print(f"  1. detected intent   : {route.get('intent')}")
    print(f"  2. extracted serial  : {route.get('serial')} (from the {route.get('serial_source') or '—'})")
    print(f"  3. service called    : InvestigationRunner.ask → {look.get('tool', '—')}"
          f"(source={look.get('source', '—')}, investigate={look.get('investigate')}, "
          f"mode={look.get('mode', '—')})")
    if look.get("ok") is False:
        print(f"     ✗ {out.get('error_code')}: {out.get('reason')}")
    print(f"  4. SIS page reached  : {safe_url((snap.metadata.get('final_url') if snap else None))}")
    print(f"     Troubleshooting tab: {safe_url(tr.get('url')) if tr else '—'}"
          f" · opened={bool((tr.get('tab') or {}).get('opened'))}")
    for sec in tr.get("sections") or []:
        print(f"       · {sec['section']}: SIS lists {sec['count_displayed']}, read {sec['items_read']}"
              f"{' — ' + sec['error'] if sec.get('error') else ''}")
    print(f"  5. records extracted : {len(entries)} troubleshooting code records "
          f"(+{len([e for e in analyze_troubleshooting(tr)['entries'] if not e.get('code')]) if tr else 0}"
          " symptom rows)")
    print("  6. sample record     :")
    for e in entries[:3]:
        print(f"       raw       : {' | '.join(e['lines'])}")
        print(f"       code      : {e['code']}\n       system    : {e['system'] or '(not shown by SIS)'}"
              f"{'  [' + e['system_method'] + ']' if e.get('system_method') else ''}")
        print(f"       component : {e['component']}\n       condition : {e['condition']}"
              f"  [{e['split_method']}]\n")
    if args.expect:
        text = "\n".join(" | ".join(e["lines"]) for e in
                         (analyze_troubleshooting(tr)["entries"] if tr else []))
        for value in args.expect:
            print(f"     expected '{value}': {'FOUND on the live page' if value in text else 'NOT FOUND'}")
    print(f"  7. final Maia response:\n")
    print("     " + (out.get("text") or out.get("reason") or "(none)").replace("\n", "\n     "))

    # the other modes, answered from the run just made (no second browser)
    if snap:
        maia = InvestigationService(source)
        for q in (f"Show me parts for {serial}", f"Open 3D Model for {serial}", f"Analyze {serial}"):
            a = maia.handle(q)
            print(f"\n\n>>> {q}   [{a.get('intent')} · {a.get('status')} · from run {run_id}]\n")
            print(a.get("text") or "")

    m = (inv or {}).get("model_3d") or {}
    viewer = m.get("viewer") or {}
    print(f"\n{LINE}\n  3D VIEWER DISCOVERY (what the real SIS viewer exposes)\n{LINE}")
    print(f"  tab opened      : {bool((m.get('tab') or {}).get('opened'))}")
    print(f"  canvases/WebGL  : {len(viewer.get('canvases') or [])}")
    print(f"  viewer libraries: {', '.join(sorted((viewer.get('libs') or {}).keys())) or 'none detected'}")
    print(f"  model files     : {len(viewer.get('model_resources') or [])}")
    print(f"  component names : {len(m.get('component_names') or [])} "
          f"(source: {m.get('component_names_source') or 'none'})")
    print(f"  component tree  : {len(viewer.get('tree_elements') or [])} tree-like DOM element(s)")
    print(f"  toolbar         : {', '.join((viewer.get('toolbar') or [])[:12]) or 'none found'}")
    print(f"  click probe     : {(m.get('click_probe') or {}).get('new_text') or 'no new text'}")
    print(f"  status          : {m.get('status') or 'not inspected'}")

    v = verdicts(snap, inv)
    print(f"\n{LINE}\n  REAL SIS INTEGRATION\n  {'-' * 20}")
    print(f"  Equipment: {serial}   Run ID: {run_id or '—'}")
    for key, value in v.items():
        print(f"  {key + ':':<32} {value}")
    if v["Component mapping"] == "NOT AVAILABLE" and m.get("status") in ("VISUAL_ONLY",
                                                                        "API_DETECTED_NO_NAMES"):
        print("  → 3D model available, but component-level mapping is not exposed by the viewer.")
    if snap and inv:
        print(f"\n  discovery report: {store.results / snap.file[:-5] / 'investigation.json'}")
    print(LINE)
    return 0 if v["Caterpillar SIS run"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
