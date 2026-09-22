#!/usr/bin/env python3
"""Print Maia's evaluation report.

    python scripts/eval/run_agent_eval.py [--json]

Every figure is computed from the cases in services/automation/tests/eval/,
never written by hand. It needs no network, no credentials and no browser.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "automation"))

from tests.eval.runner import METRIC_NAMES, evaluate  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    report = await evaluate()
    if args.json:
        print(json.dumps({
            "cases": len(report.outcomes),
            "passed": report.passed,
            "metrics": {METRIC_NAMES.get(k, k): {"passed": p, "attempted": a,
                                                 "pct": round(p / a * 100, 1) if a else 0.0}
                        for k, (p, a) in report.metrics.items()},
            "hallucination_rate_pct": round(
                report.hallucination_rate[0] / max(1, report.hallucination_rate[1]) * 100, 1),
            "failures": [{"case": o.name, "problems": o.failures}
                         for o in report.outcomes if not o.passed],
        }, indent=2))
    else:
        print(report.render())
    return 0 if report.passed == len(report.outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
