"""
A portal whose tracking box only the general search finds (MSC on 14 Oct:
every one of 11 lookups waited the full 45 s for a named box first). After
the first such lookup in a run, later ones wait briefly. Real Chromium, a
local page, no carrier site.
"""
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_eta as A                                       # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


PAGE = (b"<html><body><h1>Track a shipment</h1>"
        b"<input type='text' placeholder='Search...'><button>Search</button></body></html>")


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)


server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:{0}".format(server.server_address[1])

LOG = []
A.write_log = lambda m, *a, **k: LOG.append(str(m))
A.PORTAL_FORM_READY_MS = 6000
A.CATCHALL_KNOWN_WAIT_MS = 500
A.PAGE_SETTLE_MAX_SECONDS = 1
A._pagecheck_store = lambda: None
A._CATCHALL_BOX_OK.clear()
config = {"label": "Stub ocean", "urls": [BASE + "/track"],
          "placeholder": A.OCEAN_REFERENCE_BOX, "button": A.OCEAN_SEARCH_BUTTON}

from playwright.sync_api import sync_playwright              # noqa: E402
exe = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=str(exe[-1]) if exe else None)
    page = browser.new_page()
    t = time.time()
    first = A.open_portal(page, config, "MEDU1234567")
    first_s = time.time() - t
    t = time.time()
    second = A.open_portal(page, config, "MEDU7654321")
    second_s = time.time() - t
    check("First lookup: the full wait, then the general search finds the box",
          first is not None and first_s >= 6, round(first_s, 1))
    check("...and the run notes it for this portal",
          any("found only by the general search" in x for x in LOG), LOG[-3:])
    check("Second lookup: the box wait is the short one (the other page steps take the same time)",
          second is not None and first_s - second_s >= 5, (round(first_s, 1), round(second_s, 1), LOG[-6:]))
    A._CATCHALL_BOX_OK.clear()
    gc = dict(config, label="Stub human", needs_person=True)
    try:
        A.open_portal(page, gc, "S330000001")
    except Exception:
        pass
    check("A portal with a person's security step is never remembered this way",
          "Stub human" not in A._CATCHALL_BOX_OK)
    browser.close()

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
