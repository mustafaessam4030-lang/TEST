#!/usr/bin/env python3
"""Maia's analysis from the command line — the same engine the chat uses.

    python scripts/analysis/maia_analyze.py "Analyze JAZ01865"
    python scripts/analysis/maia_analyze.py "compare JAZ01865 and JAZ01866"
    python scripts/analysis/maia_analyze.py "what changed" --serial JAZ01865
    python scripts/analysis/maia_analyze.py "Analyze JAZ01865" --sample   # demo data

Reads verified SIS results from logs/sis-results/. No browser, no model.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "automation"))

from app.analysis.service import AnalysisService  # noqa: E402
from app.analysis.snapshots import LocalStoreSnapshots  # noqa: E402
from app.config import get_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("utterance", nargs="*", default=["Analyze JAZ01865"])
    ap.add_argument("--serial", help="the machine being discussed, for follow-ups")
    ap.add_argument("--sample", action="store_true",
                    help="analyse a clearly marked SAMPLE store instead of real results")
    args = ap.parse_args()
    if args.sample:
        from app.analysis.sample import write_sample_store

        root = Path(tempfile.mkdtemp())
        write_sample_store(root)
        source = LocalStoreSnapshots(root, include_samples=True)
        print("  (SAMPLE data — not a real SIS retrieval)\n")
    else:
        root = Path(get_settings().local_store_dir)
        root = root if root.is_absolute() else ROOT / root
        source = LocalStoreSnapshots(root)
        print(f"  store: {root}\n")
    out = AnalysisService(source).handle(" ".join(args.utterance), active_serial=args.serial)
    if not out.get("handled"):
        print("  Not an analysis request. Try: Analyze JAZ01865 · show parts · what changed ·"
              " data quality · show anomalies · compare JAZ01865 and JAZ01866")
        return 1
    print(out.get("text") or out.get("message"))
    return 0 if out.get("status") == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
