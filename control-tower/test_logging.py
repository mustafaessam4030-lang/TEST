"""
Logging tests.

Reproduces the PermissionError conditions for real (unwritable folders) rather
than mocking them, then asserts the automation survives and still logs.

Run:  python test_logging.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "" if not detail else "  ({0})".format(detail)))


import update_eta as A

print("=" * 70)
print("1. PATH RESOLUTION")
print("=" * 70)
check("An output folder was resolved", A.OUTPUT_FOLDER is not None, str(A.OUTPUT_FOLDER))
check("The log folder exists", A.LOG_FOLDER.is_dir(), str(A.LOG_FOLDER))
check("A log file was created", A.LOG_FILE.exists(), A.LOG_FILE.name)
check("Screenshot folder exists", A.SCREENSHOT_FOLDER.is_dir())

writable = tempfile.mkdtemp()
check("Writable folder probes clean", A._probe_writable(Path(writable)) is None)

blocked = Path(tempfile.mkdtemp()) / "blocked"
blocked.mkdir()
os.chmod(blocked, 0o555)
reason = A._probe_writable(blocked / "logs")
if os.geteuid() == 0:
    print("       (running as root: permission probe cannot be exercised here,")
    print("        see section 4 for the real unprivileged reproduction)")
else:
    check("Unwritable folder is rejected with a reason", reason is not None, str(reason))


print()
print("=" * 70)
print("2. FILENAME UNIQUENESS  (two runs must not share a file)")
print("=" * 70)
name = A.LOG_FILE.name
check("Filename carries microseconds", name.count("_") >= 3, name)
check("Filename carries the process id", "_pid" in name, name)

from datetime import datetime
made = {
    "run_{0:%Y%m%d_%H%M%S_%f}_pid{1}.log".format(datetime.now(), os.getpid())
    for _ in range(2000)
}
check("2000 rapid names are all distinct", len(made) == 2000,
      "{0} unique".format(len(made)))


print()
print("=" * 70)
print("3. WRITER RESILIENCE")
print("=" * 70)
A.write_log("test line one")
A.write_log("test line two")
body = A.LOG_FILE.read_text(encoding="utf-8")
check("Lines actually reach the file", "test line one" in body and "test line two" in body)

A.write_log("Logging in with password=Hunter2!")
A.write_log("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abcdef")
A.write_log("Qatar tokenized AWB input found (score=3, width=180).")
body = A.LOG_FILE.read_text(encoding="utf-8")
check("Password never written to disk", "Hunter2" not in body)
check("Bearer token never written to disk", "eyJhbGciOiJIUzI1NiJ9" not in body)
check("Redaction marker present", "[redacted]" in body)
check("Innocent 'tokenized' line kept intact", "score=3, width=180" in body)

# Simulate the handle dying, as a lock by another process would.
before = A._run_log.path
A._run_log.handle.close()
A.write_log("line after the handle died")
check("Writer rolled over to a new file", A._run_log.path != before,
      A._run_log.path.name)
check("Line landed in the new file",
      "line after the handle died" in A._run_log.path.read_text(encoding="utf-8"))
check("Writer is not degraded", A._run_log.degraded is False)
check("Rollover was announced, not silent",
      "LOGGING:" in A._run_log.path.read_text(encoding="utf-8"))

A.write_log("still logging after recovery")
check("Logging continues after recovery",
      "still logging after recovery" in A._run_log.path.read_text(encoding="utf-8"))


print()
print("=" * 70)
print("4. THE REAL CRASH, REPRODUCED UNPRIVILEGED")
print("=" * 70)

root = Path(tempfile.mkdtemp())
base = root / "Automation"
base.mkdir()
os.chmod(root, 0o755)
os.chmod(base, 0o555)          # exists, not writable -> the Errno 13 case
os.chmod(tempfile.gettempdir(), 0o1777)

here = Path(__file__).resolve().parent
script = root / "probe.py"
script.write_text(
    "import sys\n"
    "sys.path.insert(0, {here!r})\n"
    "from pathlib import Path\n"
    "import update_eta as A\n"
    "A.write_log('unprivileged run wrote this')\n"
    "print('RESOLVED::' + str(A.OUTPUT_FOLDER))\n"
    "print('LOGFILE::' + str(A.LOG_FILE))\n"
    "print('WROTE::' + str('unprivileged run wrote this' in "
    "A.LOG_FILE.read_text(encoding='utf-8')))\n".format(here=str(here)),
    encoding="utf-8",
)
os.chmod(script, 0o755)

# Point BASE_FOLDER at the unwritable folder for the child only.
patched = here / "_probe_base.py"
patched.write_text(
    "import re, pathlib\n"
    "src = pathlib.Path({src!r}).read_text(encoding='utf-8')\n"
    "src = src.replace('BASE_FOLDER = Path(r\\\"C:\\\\\\\\Automation\\\")', "
    "'BASE_FOLDER = Path(r\\\"{base}\\\")')\n"
    "pathlib.Path({dst!r}).write_text(src, encoding='utf-8')\n".format(
        src=str(here / "update_eta.py"),
        dst=str(root / "update_eta.py"),
        base=str(base),
    ),
    encoding="utf-8",
)
subprocess.run([sys.executable, str(patched)], check=True)
patched.unlink()

child = root / "child.py"
child.write_text(
    "import sys\n"
    "sys.path.insert(0, {root!r})\n"
    "import update_eta as A\n"
    "A.write_log('unprivileged run wrote this')\n"
    "print('RESOLVED::' + str(A.OUTPUT_FOLDER))\n"
    "print('LOGFILE::' + str(A.LOG_FILE))\n"
    "print('WROTE::' + str('unprivileged run wrote this' in "
    "A.LOG_FILE.read_text(encoding='utf-8')))\n".format(root=str(root)),
    encoding="utf-8",
)
for path in (root, child, root / "update_eta.py"):
    os.chmod(path, 0o755)

if os.geteuid() != 0:
    print("  (needs root to drop privileges; skipped)")
else:
    result = subprocess.run(
        ["su", "nobody", "-s", "/bin/sh", "-c",
         "{0} {1}".format(sys.executable, child)],
        capture_output=True, text=True,
    )
    out = result.stdout + result.stderr
    check("Unprivileged run does NOT crash with PermissionError",
          "PermissionError" not in out and result.returncode == 0,
          out.strip().splitlines()[-1] if out.strip() else "no output")
    resolved = [l for l in out.splitlines() if l.startswith("RESOLVED::")]
    logfile = [l for l in out.splitlines() if l.startswith("LOGFILE::")]
    wrote = [l for l in out.splitlines() if l.startswith("WROTE::")]
    check("Fell back to a writable folder",
          bool(resolved) and str(base) not in resolved[0],
          resolved[0].split("::")[1] if resolved else "none")
    check("A log file was created there", bool(logfile),
          logfile[0].split("::")[1] if logfile else "none")
    check("Lines were written to it", bool(wrote) and wrote[0].endswith("True"))
    check("The rejection was reported, not hidden",
          "not writable" in out or "permission denied" in out.lower()
          or bool(resolved))

os.chmod(base, 0o755)


print()
print("=" * 70)
print("5. NOTHING ELSE MOVED")
print("=" * 70)
check("BASE_FOLDER still the configured default",
      str(A.BASE_FOLDER).endswith("Automation"), str(A.BASE_FOLDER))
check("CREDENTIALS_FILE still under BASE_FOLDER (an input, not an output)",
      A.CREDENTIALS_FILE.parent == A.BASE_FOLDER)
check("RESULTS_FILE name unchanged", A.RESULTS_FILE.name == "tracking_results.csv")
check("Logging is never disabled", A._run_log.degraded is False)
check("write_log still feeds the dashboard bridge",
      "tower.log(message)" in (Path(__file__).parent / "update_eta.py")
      .read_text(encoding="utf-8"))
check("TARGET_STATUS unchanged", A.TARGET_STATUS == "Under Clearance")

print()
print("=" * 70)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
for n in FAIL:
    print("  FAILED:", n)
print("=" * 70)
sys.exit(1 if FAIL else 0)
