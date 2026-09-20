#!/usr/bin/env python3
"""
Start the whole Maya stack for a real end-to-end run.

  Maya chat (browser)  →  tool gateway (FastAPI)  →  Playwright worker  →  SIS

One process starts both halves: the API on :8080 and the Maya UI on :5173 with
CFG.equipment.apiBase already pointed at the API. Nothing is mocked; if SIS
cannot be completed the UI shows the real failure and the real run id.

    python scripts/e2e/run_e2e.py                 # worker browser visible
    python scripts/e2e/run_e2e.py --headless      # worker browser hidden
"""
from __future__ import annotations

import argparse
import functools
import http.server
import os
import re
import shutil
import signal
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
SOURCE_HTML = ROOT / "frontend" / "mantrac-support-v9.html"


def wire_ui(api_base: str) -> Path:
    """Publish the chat UI with the gateway address baked in. Source stays untouched."""
    BUILD.mkdir(exist_ok=True)
    html = SOURCE_HTML.read_text()
    html, n = re.subn(r"(apiBase\s*:\s*)'[^']*'", rf"\1'{api_base}'", html, count=1)
    if not n:
        sys.exit("could not find CFG.equipment.apiBase in the UI — was v9 rebuilt?")
    target = BUILD / "maya.html"
    target.write_text(html)
    return target


def wait_for(url: str, timeout_s: int = 60) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def serve_ui(port: int) -> socketserver.TCPServer:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(BUILD))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-port", type=int, default=8080)
    ap.add_argument("--ui-port", type=int, default=5173)
    ap.add_argument("--headless", action="store_true",
                    help="hide the worker browser (default: visible, so you can watch SIS)")
    ap.add_argument("--serial", default="SN123456", help="the serial to suggest in the banner")
    args = ap.parse_args()

    api_base = f"http://127.0.0.1:{args.api_port}"
    ui_url = f"http://127.0.0.1:{args.ui_port}/maya.html"
    page = wire_ui(api_base)

    env = {
        **os.environ,
        "MAYA_ALLOW_LIVE_AUTOMATION": "true",       # the master switch, on for this run
        "MAYA_HEADLESS": "true" if args.headless else "false",
        "MAYA_CORS_ORIGINS": f"http://127.0.0.1:{args.ui_port},http://localhost:{args.ui_port}",
        "MAYA_REPOSITORY": os.environ.get("MAYA_REPOSITORY", "memory"),
        "PYTHONUNBUFFERED": "1",
    }
    if os.environ.get("MAYA_CHROMIUM_PATH"):
        env["MAYA_CHROMIUM_PATH"] = os.environ["MAYA_CHROMIUM_PATH"]

    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(args.api_port), "--log-level", "info"],
        cwd=str(ROOT / "services" / "automation"), env=env)

    httpd = serve_ui(args.ui_port)
    ok = wait_for(f"{api_base}/healthz")

    print("\n" + "═" * 74)
    print("  MAYA END-TO-END STACK")
    print("═" * 74)
    print(f"  tool gateway : {api_base}   {'ready' if ok else 'NOT READY — check the log above'}")
    print(f"  maya chat    : {ui_url}")
    print(f"  worker browser: {'headless' if args.headless else 'VISIBLE — watch it drive SIS'}")
    print(f"  live automation: enabled     repository: {env['MAYA_REPOSITORY']}")
    print("─" * 74)
    print("  1. open the chat URL above")
    print("  2. click the chat bubble (bottom right)")
    print(f'  3. type:  Maya, find equipment data for serial number {args.serial}')
    print("  4. watch the live panel: internal store → SIS automation → each step")
    print("─" * 74)
    print(f"  run status  : {api_base}/v1/runs/<run_id>")
    print(f"  paused runs : {api_base}/v1/runs")
    print(f"  readiness   : {api_base}/readyz")
    print("  Ctrl-C to stop")
    print("═" * 74 + "\n")

    def shutdown(*_a: object) -> None:
        httpd.shutdown()
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
        shutil.rmtree(BUILD, ignore_errors=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    api.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
