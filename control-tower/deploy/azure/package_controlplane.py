"""
Build the zip that is deployed to Azure App Service.

    python deploy/azure/package_controlplane.py            -> dist/ata-controlplane.zip
    az webapp deploy --resource-group RG --name APP --src-path dist/ata-controlplane.zip --type zip

It holds only what the control plane runs: controlplane/, dashboard/ (the
page and the ATLAS copilot it serves), intelligence/ and ml/ (code, never
data), and a requirements.txt with the PostgreSQL driver. Not included: the
automation (update_eta.py), carrier tools, run output, learning data, local
databases, test fixtures — none of it belongs on the web server.
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "dist" / "ata-controlplane.zip"
PACKAGES = ("controlplane", "dashboard", "intelligence", "ml")
SKIP_DIRS = {"__pycache__", ".runtime", "data", "models"}
SKIP_SUFFIX = {".pyc", ".db", ".db-wal", ".db-shm", ".jsonl", ".log"}
REQUIREMENTS = """# The control plane is standard library only, except the PostgreSQL driver.
psycopg[binary]>=3.1,<4
"""


def keep(path):
    parts = set(path.relative_to(ROOT).parts)
    return not (parts & SKIP_DIRS) and path.suffix not in SKIP_SUFFIX and path.is_file()


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for package in PACKAGES:
            for path in sorted((ROOT / package).rglob("*")):
                if keep(path):
                    z.write(path, path.relative_to(ROOT).as_posix())
                    count += 1
        z.writestr("requirements.txt", REQUIREMENTS)
        z.writestr("ml/data/.keep", "")
        count += 2
    print("Wrote {0} ({1} files, {2:.1f} MB)".format(OUT, count, OUT.stat().st_size / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
