"""
Build the release ZIP of the Control Tower — committed files only, checked.

    python make_release.py                 build release/ATA-Control-Tower-<commit>.zip
    python make_release.py --check         only scan what would be packaged
    python make_release.py --out DIR       write the ZIP elsewhere

What goes in: the files Git tracks under control-tower/ at HEAD (source,
configuration, documentation, tests and their fixtures). The working tree
must be clean, so the package is exactly the commit that was tested.

What never goes in, whatever Git says (EXCLUDED_PARTS / EXCLUDED_SUFFIXES /
EXCLUDED_NAMES): runtime state (dashboard/.runtime, ml/data, controlplane/
data), run logs and screenshots (C:\\Automation, logs/), caches, browser
profiles, generated test results, build output (dist/, release/), local
environment files and credentials. The Ollama model and SearXNG are external
local services and are never part of the package.

After building, the ZIP itself is opened and scanned again: forbidden paths,
secrets (private keys, tokens, the retired dashboard key), and paid AI APIs.
Any finding fails the build and the ZIP is deleted.
"""

import argparse
import hashlib
import io
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFIX = HERE.name                       # "control-tower"

EXCLUDED_PARTS = {".runtime", "__pycache__", "C:\\Automation", "logs", "screenshots",
                  "dist", "release", "node_modules", ".venv", "venv", ".pytest_cache",
                  "htmlcov", "browser-profile", "edge-profile", "user-data-dir",
                  "carrier_profile"}
EXCLUDED_PATHS = ("ml/data/", "ml/models/", "controlplane/data/")
EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".log", ".zip", ".sqlite", ".sqlite3", ".db", ".pem",
                     ".key", ".pfx", ".p12", ".jsonl")
EXCLUDED_NAMES = {"test_results.json", "credentials.txt", ".env", "tracking_results.csv",
                  "access_key", "Thumbs.db", ".DS_Store", "desktop.ini"}

# Secrets: credentials in any form. Test fixtures that only *look* like one
# (a redaction test's input, throwaway test passwords) are listed in ALLOWED.
SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("provider API key", re.compile(r"\bsk-(ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("connection-string secret", re.compile(r"(AccountKey|SharedAccessKey)=[A-Za-z0-9+/=]{20,}")),
    ("retired dashboard key", re.compile("mantrac" + "2026")),
]
PAID_AI = re.compile(r"api\.anthropic\.com|anthropic-version|ANTHROPIC_API_KEY|import anthropic|"
                     r"api\.openai\.com|OPENAI_API_KEY|import openai|"
                     r"generativelanguage\.googleapis|GEMINI_API_KEY|ATLAS_RESEARCH_API_KEY")
# The checks themselves name what they look for.
SCANNER_FILES = {"make_release.py", "test_release.py", "test_atlas_research.py",
                 "test_dashboard_access.py"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".bat", ".ps1", ".json", ".html", ".js", ".jsx",
                 ".css", ".bicep", ".yml", ".yaml", ".cfg", ".toml", ".ini", ".csv", ""}


def excluded(rel):
    """Why `rel` (a path inside control-tower, '/'-separated) must not ship, or None."""
    parts = rel.split("/")
    for part in parts[:-1]:
        if part in EXCLUDED_PARTS:
            return "runtime/build directory '{0}'".format(part)
    if any(rel.startswith(p) for p in EXCLUDED_PATHS):
        return "runtime data"
    name = parts[-1]
    if name in EXCLUDED_NAMES or (name.startswith(".env") and name != ".env.example"):
        return "local state or credentials ({0})".format(name)
    if name.endswith(EXCLUDED_SUFFIXES):
        return "generated or credential file type"
    return None


def scan_text(rel, text):
    """Findings in one file's text: [(rel, kind)]."""
    name = rel.split("/")[-1]
    found = []
    for kind, pattern in SECRET_PATTERNS:
        if name in SCANNER_FILES:
            continue
        if pattern.search(text):
            found.append((rel, kind))
    if name not in SCANNER_FILES and PAID_AI.search(text):
        found.append((rel, "paid AI API reference"))
    return found


def tracked_files():
    """Files Git tracks under control-tower/ at HEAD, relative to it."""
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD", "--", "."],
                         cwd=str(HERE), capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def working_tree_clean():
    out = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=str(HERE),
                         capture_output=True, text=True, check=True).stdout
    return not out.strip(), out


def head_commit():
    return subprocess.run(["git", "rev-parse", "--short=10", "HEAD"], cwd=str(HERE),
                          capture_output=True, text=True, check=True).stdout.strip()


def committed_bytes(rel):
    return subprocess.run(["git", "show", "HEAD:./" + rel], cwd=str(HERE),
                          capture_output=True, check=True).stdout


def plan():
    """(files to ship, files left out with why, findings)."""
    ship, left_out, findings = [], [], []
    for rel in tracked_files():
        why = excluded(rel)
        if why:
            left_out.append((rel, why))
            continue
        ship.append(rel)
        if Path(rel).suffix.lower() in TEXT_SUFFIXES:
            findings += scan_text(rel, committed_bytes(rel).decode("utf-8", errors="replace"))
    return ship, left_out, findings


def inspect_zip(path):
    """Open the built ZIP and check it again, entry by entry."""
    problems, count, total = [], 0, 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            count += 1
            total += info.file_size
            rel = info.filename.split("/", 1)[1] if "/" in info.filename else info.filename
            why = excluded(rel)
            if why:
                problems.append((rel, why))
            if Path(rel).suffix.lower() in TEXT_SUFFIXES:
                problems += scan_text(rel, archive.read(info).decode("utf-8", errors="replace"))
    return problems, count, total


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the Control Tower release ZIP")
    parser.add_argument("--check", action="store_true", help="scan only, build nothing")
    parser.add_argument("--out", default=str(HERE / "release"))
    parser.add_argument("--allow-dirty", action="store_true",
                        help="(--check only) scan HEAD even with uncommitted changes")
    args = parser.parse_args(argv)

    clean, status = working_tree_clean()
    if not clean and not (args.check and args.allow_dirty):
        print("REFUSED: uncommitted changes under control-tower/ — the package must be "
              "exactly a tested commit:\n" + status)
        return 2
    ship, left_out, findings = plan()
    print("Release plan for commit {0}: {1} files, {2} tracked files left out.".format(
        head_commit(), len(ship), len(left_out)))
    for rel, why in left_out:
        print("  left out: {0}  ({1})".format(rel, why))
    if findings:
        print("FINDINGS — nothing is built:")
        for rel, kind in findings:
            print("  {0}: {1}".format(rel, kind))
        return 1
    if args.check:
        print("Scan clean: no secrets, no paid AI API, no runtime artifacts in the plan.")
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = head_commit()
    target = out_dir / "ATA-Control-Tower-{0}.zip".format(commit)
    manifest = io.StringIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(ship):
            data = committed_bytes(rel)
            manifest.write("{0}  {1}\n".format(hashlib.sha256(data).hexdigest(), rel))
            info = zipfile.ZipInfo("{0}/{1}".format(PREFIX, rel), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 << 16)
            archive.writestr(info, data)
        info = zipfile.ZipInfo("{0}/RELEASE_MANIFEST.txt".format(PREFIX),
                               date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, "ATA Control Tower release — commit {0}\nsha256  path\n{1}".format(
            commit, manifest.getvalue()))
    problems, count, total = inspect_zip(target)
    if problems:
        target.unlink()
        print("THE BUILT ZIP FAILED INSPECTION — deleted:")
        for rel, why in problems:
            print("  {0}: {1}".format(rel, why))
        return 1
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print("Built {0}".format(target))
    print("  {0} files ({1} + RELEASE_MANIFEST.txt), {2} bytes uncompressed, {3} bytes on disk"
          .format(count, count - 1, total, target.stat().st_size))
    print("  sha256 {0}".format(digest))
    print("  inspected after building: no runtime artifacts, no secrets, no paid AI API")
    return 0


if __name__ == "__main__":
    sys.exit(main())
