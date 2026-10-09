"""
Release hygiene, on every test run: what would ship is clean.

    python test_release.py

- every Python file compiles; every product module imports
- third-party imports are declared in requirements.txt (no hidden dependency)
- no secret, retired key or paid AI API in any file that would ship
- runtime state (dashboard/.runtime, run logs, ml/data, test results,
  builds) is ignored by Git and excluded from the release package
- the release rules in make_release.py hold for every tracked file

Run from a Git checkout it checks the tracked files as they are on disk now
(so an uncommitted problem is caught); from an extracted release it checks
the files present, minus local runtime state.
"""

import ast
import importlib
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("ATLAS_INTEL_DIR", tempfile.mkdtemp(prefix="ct_rel_intel_"))
os.environ.setdefault("PO_DATA_DIR", tempfile.mkdtemp(prefix="ct_rel_po_"))
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")

import make_release as M  # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not ok else ""))


def rule(title):
    print("\n" + "=" * 74 + "\n" + title + "\n" + "=" * 74)


def git(*args):
    return subprocess.run(["git"] + list(args), cwd=str(HERE), capture_output=True, text=True)


IN_GIT = git("rev-parse", "--is-inside-work-tree").stdout.strip() == "true"
if IN_GIT:
    FILES = [f for f in git("ls-files", "--", ".").stdout.splitlines()
             if (HERE / f).is_file()]
else:
    FILES = [p.relative_to(HERE).as_posix() for p in HERE.rglob("*") if p.is_file()]
    FILES = [f for f in FILES if not M.excluded(f)]
SHIPPED = [f for f in FILES if not M.excluded(f)]
PY = [f for f in SHIPPED if f.endswith(".py")]

rule("1. EVERY PYTHON FILE COMPILES")
bad = []
for rel in PY:
    try:
        py_compile.compile(str(HERE / rel), cfile=os.path.join(tempfile.gettempdir(),
                                                                "ct_rel_compile.pyc"),
                           doraise=True)
    except py_compile.PyCompileError as error:
        bad.append("{0}: {1}".format(rel, str(error)[:120]))
check("{0} Python files compile".format(len(PY)), not bad, bad[:5])

rule("2. EVERY PRODUCT MODULE IMPORTS")
PACKAGES = ("dashboard", "intelligence", "po", "controlplane", "worker", "ml")
failed = []
modules = []
for rel in PY:
    parts = rel[:-3].split("/")
    if parts[0] in PACKAGES and parts[-1] not in ("__main__",) and "templates" not in parts:
        modules.append(".".join(p for p in parts if p != "__init__"))
for name in sorted(set(modules)):
    try:
        importlib.import_module(name)
    except Exception as error:                      # noqa: BLE001
        failed.append("{0}: {1}: {2}".format(name, type(error).__name__, str(error)[:100]))
check("{0} modules of {1} import cleanly".format(len(set(modules)), "/".join(PACKAGES)),
      not failed, failed[:5])
for script in ("update_eta", "human_queue", "remote_session", "make_release", "run_tests"):
    try:
        importlib.import_module(script)
        ok = True
    except Exception as error:                      # noqa: BLE001
        ok, failed = False, [str(error)[:120]]
    check("{0}.py imports".format(script), ok, failed[:1])

rule("3. NO HIDDEN THIRD-PARTY DEPENDENCY")
stdlib = getattr(sys, "stdlib_module_names", None)
if stdlib is None:
    SKIP.append("third-party import check (needs Python 3.10+)")
    print("  (Python < 3.10: skipped)")
else:
    local = set()
    for p in SHIPPED:
        top = p.split("/")[0]
        local.add(top[:-3] if top.endswith(".py") else top)
        if p.startswith("dashboard/") and p.endswith(".py"):
            local.add(p.split("/")[-1][:-3])     # the flattened-layout fallback imports
    # Declared: required in requirements.txt, or optional there with a fallback.
    declared = {"playwright", "openpyxl", "fitz", "pymupdf", "psycopg", "psutil"}
    requirements = (HERE / "requirements.txt").read_text(encoding="utf-8").lower()
    third = {}
    for rel in PY:
        if rel.startswith("test_") or rel.startswith("fixtures/"):
            continue
        tree = ast.parse((HERE / rel).read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module and \
                not node.level else []
            for name in names:
                top = name.split(".")[0]
                # searxng_local.run() executes in SearXNG's own environment
                # (searxng\venv), which installs these from SearXNG's
                # requirements; the tower's Python never imports them there.
                if rel == "intelligence/searxng_local.py" and top in ("yaml", "certifi"):
                    continue
                if top not in stdlib and top not in local:
                    third.setdefault(top, set()).add(rel)
    undeclared = sorted(t for t in third if t not in declared)
    check("Every third-party import in the product is a known, declared dependency",
          not undeclared, {t: sorted(third[t])[:2] for t in undeclared})
    check("requirements.txt declares playwright, openpyxl and pymupdf, names the optional "
          "psutil and psycopg (and nothing paid)",
          all(x in requirements for x in ("playwright", "openpyxl", "pymupdf", "psutil",
                                          "psycopg"))
          and not any(x in requirements for x in ("anthropic", "openai", "google-generativeai")))
    check("...and no AI SDK is imported anywhere",
          not ({"anthropic", "openai", "google", "ollama", "langchain"} & set(third)),
          sorted(set(third) & {"anthropic", "openai", "google", "ollama", "langchain"}))

rule("4. NO SECRET, RETIRED KEY OR PAID AI API IN WHAT WOULD SHIP")
findings = []
for rel in SHIPPED:
    if Path(rel).suffix.lower() in M.TEXT_SUFFIXES:
        findings += M.scan_text(rel, (HERE / rel).read_text(encoding="utf-8", errors="replace"))
check("{0} shipped files scanned: no findings".format(len(SHIPPED)), not findings, findings[:6])
check("The scanner itself detects a planted private key, token and paid endpoint",
      M.scan_text("x.py", "-----BEGIN RSA PRIVATE KEY-----") and
      M.scan_text("x.py", "ghp_" + "a" * 36) and
      M.scan_text("x.py", "https://api." + "anthropic.com/v1") and
      M.scan_text("x.py", "mantrac" + "2026"))

rule("5. RUNTIME STATE IS NEVER TRACKED OR SHIPPED")
tracked_runtime = [f for f in FILES if M.excluded(f)]
check("No runtime artifact is tracked by Git", not tracked_runtime, tracked_runtime[:6])
for sample in ("dashboard/.runtime/state.json", "dashboard/.runtime/access_key",
               "C:\\Automation/logs/run_1.log", "ml/data/telemetry.jsonl",
               "ml/data/po/jobs/x.json", "controlplane/data/ata.sqlite3", "test_results.json",
               "dist/ata-controlplane.zip", "release/ATA-Control-Tower-x.zip",
               "po/__pycache__/x.cpython-311.pyc", ".env", "credentials.txt"):
    check("Excluded from the release: {0}".format(sample), bool(M.excluded(sample)))
if IN_GIT:
    for sample in ("dashboard/.runtime/state.json", "dashboard/.runtime/access_key",
                   "C:\\Automation/logs/run_1.log", "ml/data/telemetry.test.jsonl",
                   "test_results.json", "release/x.zip", "dist/x.zip"):
        check("Ignored by Git: {0}".format(sample),
              git("check-ignore", "-q", "--no-index", sample).returncode == 0)
else:
    SKIP.append("Git ignore checks (not a Git checkout)")
for keep in ("update_eta.py", "po/templates/DUTY_REQUEST_V1.xlsx", "requirements.txt",
             "START_HERE.md", "dashboard/static/index.html", "fixtures/carrier_stub.py",
             "deploy/azure/main.parameters.example.json"):
    check("Shipped: {0}".format(keep), keep in SHIPPED and not M.excluded(keep))

print()
print("{0} passed, {1} failed{2}".format(
    len(PASS), len(FAIL), ", {0} skipped ({1})".format(len(SKIP), "; ".join(SKIP)) if SKIP else ""))
sys.exit(1 if FAIL else 0)
