"""DSP -> Snowflake daily sync.

    python main.py login           # once: sign in to DSP in a browser window
    python main.py run             # daily: export assets and load to Snowflake
    python main.py run --no-load   # export only (writes the CSV, skips Snowflake)
    python main.py load FILE.csv   # load an existing CSV (defaults to today's date)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
load_dotenv(HERE / ".env")

from auth import LoginRequired, get_session  # noqa: E402

API_URL = os.environ.get("DSP_API_URL", "https://prod-bff-dsp.cat.com")
PROFILE_DIR = Path(os.environ.get("DSP_PROFILE_DIR", HERE / "browser-profile"))
DOWNLOAD_DIR = Path(os.environ.get("DSP_DOWNLOAD_DIR", HERE / "downloads"))
KEEP_DAYS = int(os.environ.get("DSP_KEEP_DAYS", "30"))
TABLE = os.environ.get("SNOWFLAKE_TABLE", "DSP_ASSETS")
WITH_LOCATION = os.environ.get("DSP_WITH_LOCATION", "true").lower() == "true"

log = logging.getLogger("dsp_sync")


def setup_logging() -> None:
    (HERE / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(HERE / "logs" / "dsp_sync.log")],
    )


def cmd_login(_args) -> None:
    session = get_session(PROFILE_DIR, API_URL, interactive=True, timeout_s=600)
    print(f"Signed in. Dealer code {session.dealer_code}. The daily job can now run headless.")


def cmd_run(args) -> None:
    from dsp_api import DspClient

    today = date.today()
    session = get_session(PROFILE_DIR, API_URL)
    csv_path = DOWNLOAD_DIR / f"dsp_assets_{today:%Y-%m-%d}.csv"
    DspClient(session, API_URL).export_assets(csv_path, with_location=WITH_LOCATION)
    prune_downloads()
    if not args.no_load:
        load_file(csv_path, today)


def cmd_load(args) -> None:
    load_file(Path(args.file), date.fromisoformat(args.date) if args.date else date.today())


def load_file(path: Path, snapshot_date: date) -> None:
    from snowflake_load import connect, load_snapshot, read_export

    df = read_export(path)
    log.info("Read %d rows x %d columns from %s", len(df), len(df.columns), path)
    conn = connect()
    try:
        load_snapshot(conn, df, TABLE, snapshot_date)
    finally:
        conn.close()


def prune_downloads() -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in DOWNLOAD_DIR.glob("dsp_assets_*.csv"):
        if f.stat().st_mtime < cutoff:
            f.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="sign in to DSP interactively (once, or when the session expires)")
    run = sub.add_parser("run", help="export assets from DSP and load them into Snowflake")
    run.add_argument("--no-load", action="store_true", help="download the CSV only")
    load = sub.add_parser("load", help="load an existing export CSV into Snowflake")
    load.add_argument("file")
    load.add_argument("--date", help="snapshot date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    setup_logging()
    try:
        {"login": cmd_login, "run": cmd_run, "load": cmd_load}[args.cmd](args)
    except LoginRequired as e:
        log.error("%s", e)
        return 2
    except Exception:
        log.exception("DSP sync failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
