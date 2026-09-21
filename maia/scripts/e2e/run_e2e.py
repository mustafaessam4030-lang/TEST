#!/usr/bin/env python3
"""
Start the whole Maia stack for a real end-to-end run.

  Maia chat (browser)  →  tool gateway (FastAPI)  →  Playwright worker  →  SIS

One process starts both halves: the API on :8080 and the Maia UI on :5173 with
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


def wire_ui(api_base: str, source: str) -> Path:
    """Publish the chat UI with the gateway address baked in. Source stays untouched."""
    BUILD.mkdir(exist_ok=True)
    html = SOURCE_HTML.read_text(encoding="utf-8")
    html, n = re.subn(r"(apiBase\s*:\s*)'[^']*'", rf"\1'{api_base}'", html, count=1)
    if not n:
        sys.exit("could not find CFG.equipment.apiBase in the UI — was v9 rebuilt?")
    html, m = re.subn(r"(source\s*:\s*)'[^']*'", rf"\1'{source}'", html, count=1)
    if not m:
        sys.exit("could not find CFG.equipment.source in the UI — was v9 rebuilt?")
    target = BUILD / "maia.html"
    target.write_text(html, encoding="utf-8")
    # Serve it under every name a person might already have in a bookmark, and
    # at the bare root, so a stale URL is never mistaken for a dead server.
    for alias in ("index.html", "maya.html"):
        (BUILD / alias).write_text(html, encoding="utf-8")
    # The offline fixture page is served next to the chat so the worker can reach it.
    fixture = ROOT / "scripts" / "capture" / "fixture.html"
    if fixture.exists():
        shutil.copy(fixture, BUILD / "fixture.html")
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


def port_in_use(port: int) -> bool:
    import socket
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


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
    ap.add_argument("--open-browser", action="store_true",
                    help="open the chat once the server is up (never before)")
    ap.add_argument("--no-automation", action="store_true",
                    help="serve the chat and gateway only; do not touch any browser session")
    ap.add_argument("--serial", default="SN123456", help="the serial to suggest in the banner")
    ap.add_argument("--source", default="cat_sis", choices=["cat_sis", "local_fixture"],
                    help="which registered source Maia queries. local_fixture drives a real "
                         "browser against a local page — it is NOT Caterpillar SIS.")
    args = ap.parse_args()

    # Fail early and legibly rather than half-starting on top of a live stack.
    busy = [p for p in (args.api_port, args.ui_port) if port_in_use(p)]
    if busy:
        sys.exit(f"port(s) already in use: {', '.join(map(str, busy))} — "
                 f"stop the running stack first, or pass --api-port/--ui-port")

    api_base = f"http://127.0.0.1:{args.api_port}"
    ui_url = f"http://127.0.0.1:{args.ui_port}/maia.html"
    page = wire_ui(api_base, args.source)

    env = {
        **os.environ,
        "MAIA_ALLOW_LIVE_AUTOMATION": "false" if args.no_automation else "true",
        "MAIA_HEADLESS": "true" if args.headless else "false",
        "MAIA_CORS_ORIGINS": f"http://127.0.0.1:{args.ui_port},http://localhost:{args.ui_port}",
        "MAIA_REPOSITORY": os.environ.get("MAIA_REPOSITORY", "memory"),
        "MAIA_ENABLE_FIXTURE_SOURCE": "true" if args.source == "local_fixture" else "false",
        # The worker resolves credentials from a reference, never a literal.
        "MAIA_SIS_SECRET_REF": os.environ.get("MAIA_SIS_SECRET_REF", "env://SIS"),
        # The fixture page accepts any non-empty credentials; this is a local test
        # page, not a real account, and these are not secrets.
        "MAIA_FIXTURE_USERNAME": "fixture-user",
        "MAIA_FIXTURE_PASSWORD": "fixture-pass",
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",          # never let a Windows code page decide encoding
    }
    if os.environ.get("MAIA_CHROMIUM_PATH"):
        env["MAIA_CHROMIUM_PATH"] = os.environ["MAIA_CHROMIUM_PATH"]

    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(args.api_port), "--log-level", "info"],
        cwd=str(ROOT / "services" / "automation"), env=env)

    try:
        httpd = serve_ui(args.ui_port)
    except OSError as exc:
        api.terminate()                      # never leave a half-started stack behind
        sys.exit(f"could not serve the UI on :{args.ui_port} — {exc}")
    ok = wait_for(f"{api_base}/healthz")

    print("\n" + "═" * 74)
    print("  MAIA END-TO-END STACK")
    print("═" * 74)
    print(f"  tool gateway : {api_base}   {'ready' if ok else 'NOT READY — check the log above'}")
    print(f"  maia chat    : {ui_url}")
    print(f"  worker browser: {'headless' if args.headless else 'VISIBLE — watch it drive SIS'}")
    print(f"  live automation: {'DISABLED (--no-automation)' if args.no_automation else 'enabled'}"
          f"     repository: {env['MAIA_REPOSITORY']}")
    print(f"  source       : {args.source}"
          + ("   ← LOCAL TEST FIXTURE, NOT Caterpillar SIS" if args.source == "local_fixture" else ""))
    print("─" * 74)
    print("  1. open the chat URL above")
    print("  2. click the chat bubble (bottom right)")
    print(f'  3. type:  Maia, find equipment data for serial number {args.serial}')
    print("  4. watch the live panel: internal store → SIS automation → each step")
    print("─" * 74)
    print(f"  run status  : {api_base}/v1/runs/<run_id>")
    print(f"  paused runs : {api_base}/v1/runs")
    print(f"  readiness   : {api_base}/readyz")
    print("  Ctrl-C to stop")
    print("═" * 74 + "\n")

    # Only now, with the port bound, is it safe to open the page. Opening it
    # first is what produces ERR_CONNECTION_REFUSED on a perfectly good start.
    if args.open_browser:
        import webbrowser

        opened = False
        try:
            opened = webbrowser.open(ui_url)
        except Exception:
            opened = False
        if not opened and os.name == "nt":
            try:                                   # Windows default handler
                os.startfile(ui_url)               # type: ignore[attr-defined]
                opened = True
            except Exception:
                opened = False
        # Never leave the person guessing whether a window was supposed to appear.
        if opened:
            print(f"  Opened {ui_url} in your browser.\n")
        else:
            print("  " + "!" * 70)
            print("  Could not open a browser automatically on this machine.")
            print(f"  Open this address yourself:   {ui_url}")
            print("  " + "!" * 70 + "\n")

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
