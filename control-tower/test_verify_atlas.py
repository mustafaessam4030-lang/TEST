"""
verify_atlas.py is what an operator runs on the server to check the ATLAS
foundation. It must pass here too, so it cannot fall behind the code it
checks (it once reported a false FAIL after model approval was added).
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


run = subprocess.run([sys.executable, os.path.join(HERE, "verify_atlas.py")], cwd=HERE,
                     capture_output=True, text=True, timeout=600)
out = run.stdout + run.stderr
failed = [line.strip() for line in out.splitlines() if line.strip().startswith("FAIL ")]
check("verify_atlas.py exits 0", run.returncode == 0, "exit {0}".format(run.returncode))
check("...with no FAIL line", not failed, failed[:5])
check("...and says every structural guarantee holds", "Every structural guarantee holds" in out, out[-400:])
check("...and writes only to its own temporary folder", "Nothing was written outside" in out, out[-400:])

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
