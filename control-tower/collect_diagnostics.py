"""
Collect what is needed to diagnose failed runs into ONE zip — and nothing
secret.

    collect_diagnostics.bat        (or: python collect_diagnostics.py)

The zip (C:\\Automation\\diagnostics-<date>.zip) holds:

  versions.txt     Python, Playwright, PyMuPDF, openpyxl, Windows, this release
  eta/             the last 3 run logs, tracking_results.csv, the human-action
                   events, the dashboard's last published run state
  po/jobs.txt      every PO job: state, failure, each field read and from where
  po/sweeps/       the last 3 automatic runs' list decisions
  po/pdf/          up to 6 Bill of Entry PDFs whose extraction failed, each with
                   what the reader saw (po read --dump-layout)

Never included: credentials.txt, the dashboard access key, .env files,
browser profiles, cookies, screenshots. Every text line that names a
password, token, secret, cookie or Authorization header is replaced.
"""

import json
import os
import platform
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get("ATA_BASE_FOLDER") or r"C:\Automation")
PO_DIR = Path(os.environ.get("PO_DATA_DIR") or HERE / "ml" / "data" / "po")
RUNTIME = Path(os.environ.get("ATA_RUNTIME_DIR") or HERE / "dashboard" / ".runtime")
SECRET = re.compile(r"(passw|token|secret|cookie|authoriz|api[_-]?key|access[_-]?key|"
                    r"set-cookie|bearer)", re.I)
NEVER = ("credentials.txt", "access_key", ".env")


def scrub(text):
    return "\n".join("[line removed: it names a credential]" if SECRET.search(line) else line
                     for line in text.splitlines())


def run(args, timeout=120):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=str(HERE))
        return (out.stdout or "") + (out.stderr or "")
    except Exception as error:                                   # noqa: BLE001
        return "could not run {0}: {1}".format(" ".join(map(str, args)), error)


def version(module):
    try:
        mod = __import__(module)
        return getattr(mod, "__version__", None) or getattr(mod, "VersionBind", None) or "installed"
    except Exception as error:                                   # noqa: BLE001
        return "NOT INSTALLED ({0})".format(type(error).__name__)


def playwright_version():
    try:
        from importlib.metadata import version as v
        return v("playwright")
    except Exception as error:                                   # noqa: BLE001
        return "NOT INSTALLED ({0})".format(type(error).__name__)


def newest(folder, pattern, n):
    try:
        return sorted(Path(folder).glob(pattern), key=lambda p: p.stat().st_mtime)[-n:]
    except OSError:
        return []


def add_text(z, name, text):
    z.writestr(name, scrub(text))


def add_file(z, name, path, limit=8 * 1024 * 1024):
    path = Path(path)
    if not path.is_file() or path.name in NEVER or path.name.startswith(".env"):
        return False
    data = path.read_bytes()
    if len(data) > limit:
        data = data[-limit:]                       # the end of a long log matters most
    if path.suffix.lower() in (".log", ".csv", ".json", ".jsonl", ".txt"):
        add_text(z, name, data.decode("utf-8", errors="replace"))
    else:
        z.writestr(name, data)
    return True


def po_jobs(z):
    lines, failed_pdfs = [], []
    jobs = newest(PO_DIR / "jobs", "*.json", 200)
    for path in jobs:
        try:
            r = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        fail = r.get("failure") or {}
        lines.append("=" * 78)
        lines.append("{0}  {1}  state={2}  source={3}".format(
            r.get("po_id"), r.get("reference"), r.get("state"),
            (r.get("provenance") or {}).get("source") or r.get("source")))
        if fail:
            lines.append("  failure: {0} / {1}: {2}".format(fail.get("code") or fail.get("category"),
                                                           fail.get("stage"),
                                                           str(fail.get("detail"))[:400]))
        for reason in ((r.get("validation") or {}).get("reasons") or [])[:8]:
            lines.append("  blocked: " + str(reason)[:300])
        for name, f in (r.get("fields") or {}).items():
            value = f.get("value")
            if isinstance(value, list):
                value = "{0} line(s)".format(len(value))
            lines.append("  {0:<20} {1:<10} {2!s:<26} {3}".format(
                name, f.get("status"), value, str(f.get("evidence") or f.get("note") or "")[:120]))
        doc = r.get("document") or {}
        sha = doc.get("sha256")
        if sha and r.get("state") in ("EXTRACTION_FAILED", "VALIDATION_FAILED", "NEEDS_REVIEW",
                                      "PDF_UNREADABLE"):
            failed_pdfs.append((sha, r.get("reference")))
    add_text(z, "po/jobs.txt", "\n".join(lines) or "no PO job recorded in {0}".format(PO_DIR))
    for path in newest(PO_DIR / "sweeps", "sweep-*.json", 3):
        add_file(z, "po/sweeps/" + path.name, path)
    add_file(z, "po/sweeps/run-last.log", PO_DIR / "sweeps" / "run-last.log")
    seen = set()
    for sha, ref in failed_pdfs:
        if sha in seen or len(seen) >= 6:
            continue
        pdf = PO_DIR / "documents" / (sha + ".pdf")
        if not pdf.is_file():
            continue
        seen.add(sha)
        tag = re.sub(r"[^A-Za-z0-9_-]", "_", str(ref or sha[:12]))[:40]
        add_file(z, "po/pdf/{0}.pdf".format(tag), pdf)
        add_text(z, "po/pdf/{0}.read.txt".format(tag),
                 run([sys.executable, "-m", "po", "read", str(pdf), "--dump-layout"]))


def main():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = (BASE if BASE.is_dir() else HERE) / "diagnostics-{0}.zip".format(stamp)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = HERE / "RELEASE_MANIFEST.txt"
        release = manifest.read_text(encoding="utf-8").splitlines()[0] if manifest.is_file() \
            else "no RELEASE_MANIFEST.txt"
        add_text(z, "versions.txt", "\n".join([
            "collected: " + stamp, "release: " + release,
            "python: {0} ({1})".format(sys.version.split()[0], sys.executable),
            "playwright: " + playwright_version(), "pymupdf: " + str(version("fitz")),
            "openpyxl: " + str(version("openpyxl")),
            "windows: " + platform.platform(), "folder: " + str(HERE),
        ]))
        for path in newest(BASE / "logs", "run_*.log", 3):
            add_file(z, "eta/logs/" + path.name, path)
        add_file(z, "eta/human_actions.jsonl", BASE / "logs" / "human_actions.jsonl")
        add_file(z, "eta/tracking_results.csv", BASE / "tracking_results.csv")
        add_file(z, "eta/last_run_state.json", RUNTIME / "state.json")
        po_jobs(z)
    print("\n  Done. Send this file:\n    {0}\n".format(target))
    print("  It holds logs, results and the failed Bill of Entry PDFs — no passwords,")
    print("  keys or cookies (credentials.txt and the access key are never included).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
