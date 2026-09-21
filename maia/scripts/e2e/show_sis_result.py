#!/usr/bin/env python3
"""Show what a saved SIS lookup actually captured.

    python scripts/e2e/show_sis_result.py --serial JAZ01865
    python scripts/e2e/show_sis_result.py --serial JAZ01865 --run-id run_01… --full

Use it when the lookup went through Maia rather than the command line: it reads
the files in logs/sis-results/ and reports the same facts `sis_lookup.py`
prints — the paths, the field count, and whether each required field was
captured. It reads only; it never contacts SIS.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "automation"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import store_report  # noqa: E402
from app.config import get_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", help="required unless --list is given")
    ap.add_argument("--run-id", default=None, help="default: the most recent run")
    ap.add_argument("--full", action="store_true", help="also print the .txt in full")
    ap.add_argument("--list", action="store_true", help="list every saved lookup")
    args = ap.parse_args()

    settings = get_settings()
    store = Path(settings.local_store_dir)
    if not store.is_absolute():
        store = ROOT / store

    if not args.serial and not args.list:
        ap.error("--serial is required (or use --list to see what is saved)")

    if args.list:
        saved = sorted(store.glob("*_run_*.json"), key=lambda p: p.stat().st_mtime)
        print(f"\n  {len(saved)} lookup(s) in {store}")
        for path in saved:
            print(f"    {path.name}")
        return 0

    serial = args.serial.strip().upper()
    path = store_report.find(store, serial, args.run_id)
    if path is None:
        print(f"\n✗ no saved lookup for {serial} in {store}")
        print("  Run one:  python scripts/e2e/sis_lookup.py --serial " + serial)
        return 1

    print("\n" + store_report.render(path))
    if args.full:
        readable = path.with_suffix(".txt")
        if readable.exists():
            print("\n" + readable.read_text(encoding="utf-8"))
        else:
            print(f"\n  (no .txt beside {path.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
