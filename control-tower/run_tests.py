"""
Run every test suite in the project and print one summary.

    python run_tests.py                     every suite
    python run_tests.py test_po.py ...      just these (not written as a full result)
    python run_tests.py --timeout 300       per-suite limit in seconds

Exits non-zero if anything fails, so it can be used as a gate before a run.

Bounded and diagnosable: every suite runs in its own process group with a
time limit (CT_SUITE_TIMEOUT_S, default 900 s; the whole run is limited by
CT_TOTAL_TIMEOUT_S, default 7200 s). A suite that exceeds it is killed — the
suite and everything it started (browsers, servers, worker processes) — and
reported as TIMEOUT with its last output lines, which name the check it was
on. Output goes to a file per suite, never a pipe, so a process a suite
leaves behind cannot block the runner. A suite whose processes outlive it is
reported. A test_*.py that is not in SUITES is an error, never silently
skipped.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent

SUITES = [
    ("test_automation.py",      "carriers, routing, portals"),
    ("test_dhl_data.py",        "DHL event log extraction"),
    ("test_afkl_page.py",       "AFKL myCargo result page"),
    ("test_afkl_navigation.py", "AFKL direct shipment URL"),
    ("test_afkl_nav_ladder.py", "AFKL navigation fallback ladder"),
    ("test_afkl_resources.py", "AFKL ladder's footprint on the server"),
    ("test_afkl_search.py",    "AFKL through the header search, current layout"),
    ("test_ocean.py",          "ocean carriers: routing, reading, write + read-back"),
    ("test_human_loop.py",     "human in the loop: wait, open, resume, verify"),
    ("test_flight_status.py",  "AFKL flight status card, and the two forms"),
    ("test_ata_field.py",       "ATA field lookup and guards"),
    ("test_coe_fallback.py",    "COE view fallback"),
    ("test_hub_nav.py",         "hub navigation"),
    ("test_view_select.py",     "Hub view selection cannot end the run"),
    ("test_hub_waits.py",       "wait and readiness logic"),
    ("test_logging.py",         "logging and redaction"),
    ("test_dashboard_access.py", "dashboard access key: never hardcoded, always off loopback"),
    ("test_release.py",         "release hygiene: compiles, imports, no secrets / paid AI / runtime state"),
    ("test_assistant.py",       "dashboard assistant"),
    ("test_atlas_copilot.py",  "ATLAS copilot: grounded answers, insights, safe UI actions"),
    ("test_human_queue.py",    "Human Action Queue: states, choosing, safe points, main()"),
    ("test_atlas_operations.py", "ATLAS + queue: conversation, operations, security boundary"),
    ("test_intelligence.py",   "ATLAS learning: verified signals, plans, evidence, vision, stars"),
    ("test_atlas_state.py",    "ATLAS state engine + Potato Garden: transitions, verification, safety"),
    ("test_failure_intelligence.py", "ATLAS failure intelligence: why, recovery plan, learning"),
    ("test_work_list.py",      "ATLAS work list: every failure planned, the run never stops"),
    ("test_po.py",              "PO Automation: eHub → Bill Entry → extract → validate → template → save → Graph"),
    ("test_po_hardening.py",    "PO hardening: idempotency, recovery, email reconciliation, numbers"),
    ("test_po_engine.py",       "PO transaction engine: invariants, crash points, races, properties"),
    ("test_po_icums.py",        "PO on the real ICUMS BOE layout: the business's Duty Template, cell for cell"),
    ("test_local_live_view.py", "local dashboard: Open Session shows the paused carrier page in your browser"),
    ("test_verification.py",    "real eHub verification: worker evidence, levels, never from the cloud"),
    ("test_carrier_access.py",  "carrier access: verification is not access; restriction stops, diagnosed"),
    ("test_carrier_access_e2e.py", "verification completed, carrier still restricted: main() + ATLAS"),
    ("test_ui.py",              "intro, ML panel, assistant, feedback"),
    ("test_atlas_research.py",  "ATLAS research: shipment intelligence, error investigation, sources"),
    ("test_converse.py",        "ATLAS conversation: any language, grounded, labelled web, fail closed"),
    ("test_chat_ui.py",         "ATLAS conversation in a real browser: open, think, answer, a11y, mobile"),
    ("test_ml.py",              "learning layer, on its own"),
    ("test_ml_integration.py",  "learning layer, as the automation sees it"),
    ("test_atlas.py",           "ATLAS identity and attribution"),
    ("test_captcha.py",         "human verification, AFKL URL and AWB prefix"),
    ("test_airlines.py",        "airlines from the carrier sheet, their pages"),
    ("test_carrier_profile.py", "carriers' own browser profile, its limits"),
    ("test_learning_gates.py",  "learning data, readiness, shadow scorecard, approval"),
    ("test_carrier_intel.py",   "carrier page changes, carrier health, morning briefing"),
    ("test_server_install.py",  "Remote Desktop server installer, disconnect, health check"),
    ("test_recovery.py",        "safe error recovery, bounded and attributed"),
    ("test_platform.py",        "remote platform: auth, roles, audit, workers, runs"),
    ("test_remote_session.py",  "remote Human Action end to end, in a real browser"),
]


SUMMARY = re.compile(r"(\d+) passed, (\d+) failed(?:, (\d+) skipped)?")
DEFAULT_SUITE_TIMEOUT_S = 900
DEFAULT_TOTAL_TIMEOUT_S = 7200


def _suite_env():
    # ATLAS's learning store is real operational data: each suite gets an
    # empty one of its own, so a test can neither read nor write the real
    # store, nor see what another suite recorded. Every suite's data is
    # labelled TEST, so none of it can ever be mixed into a production store.
    return dict(os.environ, ATLAS_INTEL_DIR=tempfile.mkdtemp(prefix="ct_atlas_intel_"),
                ATLAS_DATA_ORIGIN="test",
                PO_DATA_DIR=tempfile.mkdtemp(prefix="ct_po_"),
                # A run started by a suite never starts the PO automatic run
                # against the real eHub; the suites that test it set it.
                PO_AUTO=os.environ.get("PO_AUTO_IN_TESTS", "0"),
                # No suite reaches a real search service or a real local
                # model: the research suite points ATLAS at its own stand-ins.
                ATLAS_SEARCH="0", ATLAS_SEARCH_URL="",
                ATLAS_LLM_PROVIDER="none",
                # A suite never reads or creates this installation's
                # dashboard key file.
                DASHBOARD_ACCESS_KEY_FILE=os.path.join(
                    tempfile.mkdtemp(prefix="ct_key_"), "access_key"),
                PYTHONUNBUFFERED="1")


def _kill_tree(proc):
    """Kill the suite and every process it started."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _leftovers(proc):
    """POSIX: processes of the suite's group still alive after it exited
    (they are killed; the suite is reported). Windows cannot tell; None."""
    if os.name == "nt":
        return None
    try:
        os.killpg(proc.pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        rows = subprocess.run(["ps", "-o", "pid=,stat=,args=", "-g", str(proc.pid)],
                              capture_output=True, text=True, timeout=5).stdout.split("\n")
        rows = [r.split(None, 2) for r in rows if r.strip()]
        # A zombie (finished, not yet reaped) runs nothing and holds nothing.
        live = [" ".join([r[0]] + r[2:])[:140] for r in rows
                if len(r) >= 2 and not r[1].startswith("Z")]
    except (OSError, subprocess.SubprocessError):
        live = ["(could not list them)"]
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return live[:6] or False


def run_suite(path, timeout, log_dir):
    out_path = Path(log_dir) / (path.stem + ".log")
    started = time.monotonic()
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" \
        else {"start_new_session": True}
    with open(out_path, "w", encoding="utf-8", errors="replace") as out:
        proc = subprocess.Popen([sys.executable, str(path)], cwd=str(HERE), env=_suite_env(),
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                **kwargs)
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
        leftovers = None if timed_out else _leftovers(proc)
    seconds = time.monotonic() - started
    text = out_path.read_text(encoding="utf-8", errors="replace")
    match = None
    for candidate in reversed(text.splitlines()):
        match = SUMMARY.search(candidate)
        if match:
            break
    passed, failed, skipped = (int(match.group(1)), int(match.group(2)),
                               int(match.group(3) or 0)) if match else (0, 0, 0)
    if timed_out:
        status = "TIMEOUT"
    elif match is None:
        status = "ERROR"          # crashed, or never printed its summary
    elif proc.returncode != 0 or failed:
        status = "FAILED" if failed else "ERROR"
    else:
        status = "OK"
    return {"suite": path.name, "status": status, "passed": passed, "failed": failed,
            "skipped": skipped, "seconds": round(seconds, 1), "returncode": proc.returncode,
            "left_processes": leftovers or False, "log": str(out_path),
            "fail_lines": [l.strip() for l in text.splitlines()
                           if l.strip().startswith("FAIL ")][:12],
            "tail": text.splitlines()[-25:] if status != "OK" else []}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Control Tower test suites")
    parser.add_argument("suites", nargs="*", help="run only these suite files")
    parser.add_argument("--timeout", type=float,
                        default=float(os.environ.get("CT_SUITE_TIMEOUT_S") or
                                      DEFAULT_SUITE_TIMEOUT_S))
    parser.add_argument("--total-timeout", type=float,
                        default=float(os.environ.get("CT_TOTAL_TIMEOUT_S") or
                                      DEFAULT_TOTAL_TIMEOUT_S))
    args = parser.parse_args(argv)

    listed = [name for name, _d in SUITES]
    described = dict(SUITES)
    on_disk = sorted(p.name for p in HERE.glob("test_*.py"))
    unlisted = [n for n in on_disk if n not in listed]
    chosen = args.suites or listed
    unknown = [n for n in chosen if n not in described]
    complete = not args.suites
    log_dir = tempfile.mkdtemp(prefix="ct_test_logs_")
    started = time.monotonic()

    print("=" * 78)
    print("CONTROL TOWER — {0}".format("FULL TEST SUITE" if complete else "SELECTED SUITES"))
    print("  per-suite limit {0:.0f} s, whole run {1:.0f} s; suite logs: {2}".format(
        args.timeout, args.total_timeout, log_dir))
    print("=" * 78)
    results = []
    for name in unknown:
        results.append({"suite": name, "status": "ERROR", "passed": 0, "failed": 0,
                        "skipped": 0, "seconds": 0, "fail_lines": [],
                        "tail": ["not a suite in run_tests.SUITES"]})
        print("  {0:<30} ERROR — not a suite in run_tests.SUITES".format(name))
    for name in [n for n in chosen if n in described]:
        path = HERE / name
        left = args.total_timeout - (time.monotonic() - started)
        if not path.exists():
            results.append({"suite": name, "status": "MISSING", "passed": 0, "failed": 0,
                            "skipped": 0, "seconds": 0, "fail_lines": [], "tail": []})
        elif left <= 0:
            results.append({"suite": name, "status": "NOT RUN", "passed": 0, "failed": 0,
                            "skipped": 0, "seconds": 0, "fail_lines": [],
                            "tail": ["the whole-run limit was reached before this suite"]})
        else:
            if sys.stdout.isatty():
                print("  {0:<30} running…".format(name), end="\r", flush=True)
            results.append(run_suite(path, min(args.timeout, left), log_dir))
        r = results[-1]
        flag = "" if r["status"] == "OK" else r["status"]
        print("  {0:<30} {1:>4} passed {2:>3} failed {3:>2} skipped {4:>6.1f}s  {5:<8} {6}".format(
            name, r["passed"], r["failed"], r["skipped"], r["seconds"], flag,
            described.get(name, "")))
        if r.get("left_processes"):
            print("        WARNING: processes started by this suite were still running after it "
                  "exited; they were killed")
            for name in r["left_processes"] if isinstance(r["left_processes"], list) else []:
                print("          left running: " + name)
        for line in r["fail_lines"]:
            print("        " + line)
        if r["status"] in ("TIMEOUT", "ERROR"):
            print("        last output ({0}):".format(r.get("log", "")))
            for line in r["tail"][-12:]:
                print("          | " + line[:150])
    if complete and unlisted:
        for name in unlisted:
            results.append({"suite": name, "status": "NOT IN RUNNER", "passed": 0, "failed": 0,
                            "skipped": 0, "seconds": 0, "fail_lines": [], "tail": []})
            print("  {0:<30} NOT IN RUNNER — add it to SUITES in run_tests.py".format(name))

    total = {k: sum(r[k] for r in results) for k in ("passed", "failed", "skipped")}
    broken = [r["suite"] for r in results if r["status"] != "OK"]
    seconds = time.monotonic() - started
    print("=" * 78)
    print("  ATLAS evidence report for THIS machine (read-only, no browser):")
    print("      python verify_atlas.py")
    print("  Runtime proof (spawns the real `python update_eta.py`):")
    print("      python proof_runtime.py")
    print("  Real-site diagnostics (run them on the automation PC):")
    print("      python diagnose_afkl.py 057-05765454")
    print("      python diagnose_ocean.py MAERSK 231045678")
    print("      python diagnose_flight_status.py AF0877 04/09/2026")
    print("      python diagnose_server.py A   then B, C, D, then report")
    print("=" * 78)
    slow = sorted((r for r in results if r["seconds"]), key=lambda r: -r["seconds"])[:3]
    print("  slowest: " + ", ".join("{0} {1:.0f}s".format(r["suite"], r["seconds"]) for r in slow))
    print("  {0} suites, {1} passed, {2} failed, {3} skipped, {4} suite(s) with problems, "
          "{5:.0f}s".format(len(results), total["passed"], total["failed"], total["skipped"],
                           len(broken), seconds))
    for r in results:
        if r["status"] != "OK":
            print("    {0}: {1}".format(r["suite"], r["status"]))
    print("=" * 78)
    # The PO readiness gate reads this (python -m po readiness) — only a
    # complete run is written, so a partial run can never look like green.
    if complete:
        try:
            (HERE / "test_results.json").write_text(json.dumps({
                "complete": True, "passed": total["passed"], "failed": total["failed"],
                "skipped": total["skipped"], "broken": broken, "seconds": round(seconds, 1),
                "suites": [{k: r.get(k) for k in ("suite", "status", "passed", "failed",
                                                  "skipped", "seconds")} for r in results],
                "python": sys.version.split()[0], "platform": sys.platform,
                "at": datetime.now().astimezone().isoformat(timespec="seconds")}, indent=1),
                encoding="utf-8")
        except OSError:
            pass
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
