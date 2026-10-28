"""
proof_runtime.py starts the real `python update_eta.py` and checks, from the
log it writes, that the learning layer loads, reports itself honestly and
falls back when it must. It must pass here too, so it cannot fall behind the
code (it once failed after the 20-shipment learning gate and model approval
were added).
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


try:
    import playwright                                         # noqa: F401
except ImportError:
    print("  (Playwright not installed: skipped)")
    print("\n0 passed, 0 failed")
    sys.exit(0)

run = subprocess.run([sys.executable, os.path.join(HERE, "proof_runtime.py")], cwd=HERE,
                     capture_output=True, text=True, timeout=840)
out = run.stdout + run.stderr
m = re.search(r"(\d+) runtime checks, (\d+) failed", out)
failed = [line.strip() for line in out.splitlines() if line.strip().startswith("FAILED:")]
check("proof_runtime.py finishes and reports its checks", bool(m), out[-400:])
check("...with none failed", bool(m) and m.group(2) == "0", failed[:5])
check("...and at least 40 checks ran", bool(m) and int(m.group(1)) >= 40, m.group(1) if m else None)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
