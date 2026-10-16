"""
A carrier's shipment page whose first connection drops (DHL Aviation, 14 Oct:
ERR_CONNECTION_RESET, then the run fell back to a home page with no box). The
page is tried once more. Real Chromium, a local server, no carrier site.
"""
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_eta as A                                       # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


HITS = {"n": 0}
LOG = []
PAGE = b"<html><body>AWB 615-62310555 Arrived ACC 12 Oct 2026</body></html>"


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        HITS["n"] += 1
        retried = any("trying the shipment page again" in x for x in LOG)
        if self.path.startswith("/drop-once") and not retried or self.path.startswith("/always"):
            # Close the connection without an answer: Chromium reports
            # ERR_EMPTY_RESPONSE (a dropped connection).
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.send_response(200 if not self.path.startswith("/missing") else 404)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)


server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:{0}".format(server.server_address[1])
A.write_log = lambda m, *a, **k: LOG.append(str(m))
A.PAGE_SETTLE_MAX_SECONDS = 1
A.accept_cookie_banner = lambda *a, **k: True
config = {"label": "Stub aviation"}

from playwright.sync_api import sync_playwright              # noqa: E402
exe = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=str(exe[-1]) if exe else None)
    page = browser.new_page()
    ok = A.open_deep_link(page, config, BASE + "/drop-once/615-62310555")
    check("The connection drops once: tried again, and the shipment page opens",
          ok and "615-62310555" in A._page_text(page) and
          any("trying the shipment page again" in x for x in LOG), LOG[-3:])
    del LOG[:]
    ok = A.open_deep_link(page, config, BASE + "/always/615-1")
    check("It keeps dropping: two tries, then the run moves on (no endless retry)",
          not ok and sum("could not open" in x for x in LOG) == 2, LOG)
    del LOG[:]
    ok = A.open_deep_link(page, config, "http://127.0.0.1:9/never")
    check("A refused connection is not retried (not a dropped one)",
          not ok and sum("could not open" in x for x in LOG) == 1, LOG)
    del LOG[:]
    ok = A.open_deep_link(page, dict(config, ocean=True), BASE + "/always/MAEU1")
    check("An ocean carrier is not asked twice (its refusal may be bot protection)",
          not ok and sum("could not open" in x for x in LOG) == 1, LOG)
    browser.close()

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
