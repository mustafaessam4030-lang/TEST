"""
Selecting a Hub view must never be able to end the run.

On the 23rd the run died before looking at a single shipment:

    FATAL ERROR: TimeoutError: Locator.click: Timeout 5000ms exceeded.
      - waiting for get_by_role("link", name=re.compile(r"^\\s*(?:BU|Business
        \\s+Unit)\\s*[-–—/:|]?\\s*Shipments?...")).first

The BU view control was found visible, then the Hub re-rendered its menu
before the click arrived. The first click timed out, the second — a force
click sitting unguarded inside the first one's except — timed out too, and
its exception went straight up to main().

These checks drive the real select_shipments_view() against a local page
that behaves the same way: a control that goes stale, and a control whose
click starts a postback slower than the click timeout.

    python test_view_select.py
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_eta as A                                        # noqa: E402

PASS, FAIL, SKIP = [], [], []
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format(
        "PASS" if condition else "FAIL", name,
        "  ({0})".format(detail) if detail and not condition else ""))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP  {0}  ({1})".format(name, why))


print("=" * 74)
print("1. THE CLICK THAT KILLED THE RUN IS GUARDED")
print("=" * 74)
view = SRC.split("def select_shipments_view")[1].split("\ndef ")[0]
check("The unguarded force click inside an except is gone",
      "except Exception:\n                control.click(timeout=5000, force=True)"
      not in view)
check("Step 2 clicks through a helper that cannot raise",
      "_click_view_control(" in view)
check("...and only calls the view selected once the table is really back",
      "if clicked and _shipments_table_ready(page):" in view)
check("A failed step 2 falls through instead of returning or raising",
      "trying the next way of selecting" in view)
check("No path through view selection waits on the table unguarded",
      "find_shipments_table(page).wait_for(state=\"visible\", timeout=15000)"
      not in view)
check("...all three go through the one guarded wait",
      view.count("_shipments_table_ready(page)") == 3)
helper = SRC.split("def _click_view_control")[1].split("\ndef ")[0]
check("The click does not wait for the postback's navigation",
      "no_wait_after=True" in helper)
check("A stale control is looked for again, once",
      "first_visible(candidates, 1800)" in helper and "for attempt in (1, 2)" in helper)
check("The retry keeps the force click the old code relied on",
      "force=(attempt == 2)" in helper)
loop = SRC.split("for table_page in range(1, MAX_TABLE_PAGES + 1):")[1][:2600]
check("Opening a results page gets one more try before it ends the run",
      "Reloading the \"\n                        \"Hub and trying once more" in loop
      or "trying once more" in loop)
check("...and only one: a second failure is still allowed to stop the run",
      loop.count("ensure_filtered_page(internal_page, SOURCE_VIEW") == 2)
check("...while a genuine end of pages is still a clean stop, not an error",
      loop.count("except SkipShipment as") == 2)
# THE ROW IN THE OTHER VIEW. On the 23rd the shipment was found on BU page 1
# and then never found in the COE view: the matcher was an exact substring,
# so the same air waybill written another way in another table was a
# different shipment, and the run re-opened the Hub for every page up to
# ten looking for it.
ROW = "074-46285514\tKLM Royal Dutch Airlines\t20/09/2026\tUnder Clearance"
for written in ("074-46285514", "07446285514", "074 4628 5514",
                "074 - 46285514"):
    check("The row is found when the other view writes it {0!r}".format(written),
          A.reference_in_row("074-46285514",
                             written + "\tKLM\t20/09/2026\tUnder Clearance"))
check("Qatar's '157 - 50601530' still matches as it always did",
      A.reference_in_row("157 - 50601530", "157 - 50601530\tQatar"))
check("A different air waybill is never matched",
      not A.reference_in_row("074-46285514", "074-46285515\tKLM"))
check("...nor one that merely contains these digits",
      not A.reference_in_row("074-46285514", "5074462855149\tKLM"))
check("...nor the air waybill glued to the date in the next cell",
      not A.reference_in_row("074-46285514", "074-4628551\t420/09/2026"))
check("A short reference must stand alone — K179801 is not K1798010",
      not A.reference_in_row("K179801", "K1798010\tKLM")
      and A.reference_in_row("K179801", "K179801\tKLM"))
check("find_row_by_bol uses the tolerant matcher",
      "reference_in_row(bol_awb, row.inner_text())" in SRC)
check("A row not on its expected page says what that page DOES carry",
      "that \"\n                          \"page carries" in SRC
      or "page carries: {3}" in SRC)

ready = SRC.split("def _shipments_table_ready")[1].split("\ndef ")[0]
check("The table wait never raises", "return False" in ready
      and "except Exception" in ready)


# ─────────────────────────────────────────────────────────────────────────
# A HUB THAT MISBEHAVES THE SAME WAY
# ─────────────────────────────────────────────────────────────────────────
PORT = int(os.environ.get("VIEW_STUB_PORT", "9677"))
SLOW_SECONDS = 4

TABLE = ("<table><thead><tr><th>BOL/AWB Number</th><th>Carrier Name</th>"
         "<th>ETA</th><th>Status</th></tr></thead><tbody><tr><td>{0}</td>"
         "<td>KLM</td><td>14/09/2026</td><td>Under Clearance</td></tr>"
         "</tbody></table>")

PAGE = """<!doctype html><html><head><title>Hub</title></head><body>
<nav><a id="bu" href="{href}">BU Shipments</a>
     <a id="coe" href="#">COE Shipments</a></nav>
{table}
</body></html>"""


class Hub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/slow"):
            # A postback slower than the click timeout.
            time.sleep(SLOW_SECONDS)
            body = PAGE.format(href="/slow", table=TABLE.format("BU-ROW"))
        elif self.path.startswith("/nav"):
            body = PAGE.format(href="/slow", table=TABLE.format("COE-ROW"))
        else:
            body = PAGE.format(href="/slow", table=TABLE.format("COE-ROW"))
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


def launch(playwright):
    last = None
    options = [{"headless": True, "channel": "msedge"},
               {"headless": True, "channel": "chrome"}, {"headless": True}]
    roots = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")) \
        if Path("/opt/pw-browsers").is_dir() else []
    options += [{"headless": True, "executable_path": str(r)} for r in roots]
    for option in options:
        try:
            return playwright.chromium.launch(**option), None
        except Exception as error:
            last = str(error).split("\n")[0][:100]
    return None, last


server = ThreadingHTTPServer(("127.0.0.1", PORT), Hub)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:{0}/nav".format(PORT)

A.write_log = lambda *args, **kwargs: None
A.take_screenshot = lambda *args, **kwargs: None
A.VIEW_CLICK_TIMEOUT_MS = 1500
A.HUB_TABLE_REFRESH_MAX_MS = 2000

print()
print("=" * 74)
print("2. THE SAME FAILURE, ON A REAL BROWSER")
print("=" * 74)

try:
    from playwright.sync_api import sync_playwright
except Exception as error:
    sync_playwright = None
    WHY = str(error)[:80]

NAMES = ("the old click really does time out here",
         "a stale control is found again and clicked",
         "a control that never comes back returns False",
         "a slow postback does not raise",
         "the run carries on to the table")
if sync_playwright is None:
    for name in NAMES:
        skip(name, WHY)
else:
    with sync_playwright() as playwright:
        browser, why = launch(playwright)
        if browser is None:
            for name in NAMES:
                skip(name, why)
        else:
            page = browser.new_page()
            page.goto(URL)

            # Prove the stub reproduces the fault: a plain click on the BU
            # link waits for the slow postback and times out, exactly as the
            # run of the 23rd did.
            raised = None
            try:
                page.locator("#bu").click(timeout=1500)
            except Exception as error:
                raised = error
            check("The old kind of click really does time out on this page",
                  raised is not None and "Timeout" in type(raised).__name__
                  + str(raised), repr(raised)[:100])
            page.goto(URL)

            # The exact state on the 23rd: the locator the click was given
            # no longer resolves to anything.
            stale = page.locator("#gone")
            fresh = [page.get_by_role("link", name="BU Shipments")]
            landed = A._click_view_control(page, stale, fresh, "BU")
            check("A stale control is looked for again and the fresh one clicked",
                  landed is True)
            page.goto(URL)

            nothing = [page.locator("#never-there")]
            outcome = A._click_view_control(page, stale, nothing, "BU")
            check("A control that never comes back returns False, not an error",
                  outcome is False)
            page.goto(URL)

            # End to end, through the function main() calls.
            escaped = None
            started = time.time()
            try:
                A.VIEW_SELECTION.clear()
                A.select_shipments_view(page, "BU")
            except Exception as error:
                escaped = error
            took = time.time() - started
            check("A postback slower than the click timeout does not raise",
                  escaped is None, repr(escaped)[:120])
            check("...and view selection finishes in bounded time",
                  took < 30, "{0:.1f}s".format(took))
            check("...and the run carries on with a shipments table",
                  A._shipments_table_ready(page, timeout_ms=8000))
            check("...having recorded how the view was obtained",
                  A.VIEW_SELECTION.get("BU") in ("selected", "fallback"),
                  str(A.VIEW_SELECTION))
            browser.close()

server.shutdown()

print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(
    len(PASS), len(FAIL), ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
