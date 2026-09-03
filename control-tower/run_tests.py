"""
Run every test in the project and print one summary.

    python run_tests.py

Exits non-zero if anything fails, so it can be used as a gate before a run.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

SUITES = [
    ("test_automation.py",      "carriers, routing, portals"),
    ("test_dhl_data.py",        "DHL event log extraction"),
    ("test_afkl_page.py",       "AFKL myCargo result page"),
    ("test_afkl_navigation.py", "AFKL direct shipment URL"),
    ("test_ata_field.py",       "ATA field lookup and guards"),
    ("test_coe_fallback.py",    "COE view fallback"),
    ("test_hub_nav.py",         "hub navigation"),
    ("test_hub_waits.py",       "wait and readiness logic"),
    ("test_logging.py",         "logging and redaction"),
    ("test_assistant.py",       "dashboard assistant"),
    ("test_ml.py",              "learning layer, on its own"),
    ("test_ml_integration.py",  "learning layer, as the automation sees it"),
]


def main():
    total_passed = total_failed = 0
    broken = []

    print("=" * 74)
    print("CONTROL TOWER — FULL TEST SUITE")
    print("=" * 74)

    for name, description in SUITES:
        path = HERE / name
        if not path.exists():
            print("  {0:<26} MISSING".format(name))
            broken.append(name)
            continue
        result = subprocess.run(
            [sys.executable, str(path)], cwd=str(HERE),
            capture_output=True, text=True)
        line = ""
        for candidate in reversed(result.stdout.splitlines()):
            if "passed," in candidate:
                line = candidate.strip()
                break
        passed = failed = 0
        if line:
            try:
                parts = line.replace(",", "").split()
                passed = int(parts[0])
                failed = int(parts[2])
            except (ValueError, IndexError):
                pass
        total_passed += passed
        total_failed += failed
        if result.returncode != 0 or failed:
            broken.append(name)
            print("  {0:<26} {1:>4} passed  {2:>3} FAILED   {3}".format(
                name, passed, failed, description))
            for candidate in result.stdout.splitlines():
                if candidate.strip().startswith("FAIL"):
                    print("        " + candidate.strip())
            if result.stderr.strip():
                print("        " + result.stderr.strip().splitlines()[-1])
        else:
            print("  {0:<26} {1:>4} passed              {2}".format(
                name, passed, description))

    print("=" * 74)
    print("  Runtime proof (spawns the real `python update_eta.py`):")
    print("      python proof_runtime.py")
    print("  Live ML demonstration against a stand-in Manage page:")
    print("      python demo_ml_live.py")
    print("=" * 74)
    print("  {0} passed, {1} failed, {2} suite(s) with problems".format(
        total_passed, total_failed, len(broken)))
    print("=" * 74)
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
