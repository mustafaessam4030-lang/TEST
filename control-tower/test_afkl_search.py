"""
AFKL through the site's own header search, and the layout it lands on.

Two things the operator showed on the 23rd, with screenshots:

  1. the direct shipment address now lands on the carrier's "THIS PAGE HAS
     MOVED" page — HTTP 200, the full site header, and no shipment. That is
     what every AFKL lookup hit for a week: "page loaded but ... could not be
     confirmed on it";
  2. typing the air waybill into the search box in the site header does
     work, and lands on a layout the reader had never seen:

        MUC ✈ ACC
        DELIVERY  OK NOTIFIED  074-46285514
        20 SEP 23:00 - 1 piece ready to be picked up at ACC
        20 SEP 22:58 - 1 piece received at ACC from KL0589
        On Time: Your shipment has been delivered before LAT

These checks read that exact page, and drive the real get_portal_result()
against a local site that behaves like both screenshots.

    python test_afkl_search.py
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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


class Page(object):
    def __init__(self, text):
        self.text = text

    def locator(self, selector):
        return self

    def inner_text(self, timeout=None):
        return self.text

    @property
    def frames(self):
        return []

    @property
    def main_frame(self):
        return None


A.write_log = lambda *args, **kwargs: None

# The operator's screenshot, the way innerText hands it back.
AWB = "074-46285514"
RESULT_TAIL = """DELIVERY
OK NOTIFIED
074-46285514
File a claim
Set notifications
Print
Checked-in
MUC
AMS
ACC
1/1
175 kg
Delivered
20 SEP 23:00 - 1 piece ready to be picked up at ACC
20 SEP 22:58 - 1 piece received at ACC from KL0589
On Time: Your shipment has been delivered before LAT
"""

print("=" * 74)
print("1. THE LAYOUT THE HEADER SEARCH LANDS ON")
print("=" * 74)
for label, head in (("with the plane as a glyph", "MUC ✈ ACC\n"),
                    ("with the plane dropped", "MUC  ACC\n"),
                    ("with the plane as a line break", "MUC\nACC\n")):
    read = A._read_afkl_page(Page(head + RESULT_TAIL), "AFKL") or {}
    check("The screenshot reads ATA 20/09/2026 " + label,
          read.get("ata") == "20/09/2026", str(read))
check("The destination is ACC, from the route header",
      A.afkl_route_destination("MUC ✈ ACC\n" + RESULT_TAIL) == "ACC")
check("...not AMS, which the progress bar lists after MUC",
      A.afkl_route_destination("MUC\nACC\n" + RESULT_TAIL) == "ACC")
read = A._read_afkl_page(Page("MUC ✈ ACC\n" + RESULT_TAIL), "AFKL") or {}
check("The arrival is the RCF — received from the flight — not the later "
      "ready-for-pick-up", read.get("ata") == "20/09/2026"
      and read.get("tracking_status") == "Arrived", str(read))
check("'Delivered before LAT' is not read as a delivery",
      read.get("tracking_status") != "Delivered")
check("An arrived shipment gets no invented ETA", read.get("eta") is None)

TRANSIT = ("MUC ✈ ACC\nIN TRANSIT\n074-46285514\nChecked-in\nMUC\nAMS\nACC\n"
           "19 SEP 08:10 - 1 piece received at AMS from truck\n"
           "19 SEP 21:40 - 1 piece departed from AMS on KL0589\n"
           "20 SEP 06:00 - 1 piece expected to arrive at ACC\n")
transit = A._read_afkl_page(Page(TRANSIT), "AFKL") or {}
check("A piece received in transit at AMS is NOT an arrival",
      transit.get("ata") is None, str(transit))
check("...while an arrival expected at the destination is an ETA",
      transit.get("eta") == "20/09/2026", str(transit))
check("The flight on the milestones is kept for the flight status card",
      (transit.get("flight_leg") or {}).get("flight") == "KL0589")

# THE RUN OF THE 23rd AT 16:12. The same shipment, found this time — and
# reported as ETA 26/09/2026, ATA None, for a shipment that had arrived on
# 20/09. The current result page carries a Flight schedule section below the
# milestones, the reader skipped itself whenever it saw those words, and the
# generic label reader took the nearest date to "ETA".
WITH_SCHEDULE = ("MUC \u2708 ACC\n" + RESULT_TAIL +
                 "Flight schedule\nMUC - AMS  KL1234  19 SEP\n"
                 "AMS - ACC  KL0589  20 SEP\n"
                 "Delivery ETA subject to local handling 26 SEP\n")
scheduled = A._read_afkl_page(Page(WITH_SCHEDULE), "AFKL") or {}
check("A result page with a Flight schedule section is still read by its "
      "milestones: ATA 20/09/2026", scheduled.get("ata") == "20/09/2026",
      str(scheduled))
check("...and the stray 26 SEP near 'ETA' is NOT reported as an ETA",
      scheduled.get("eta") != "26/09/2026", str(scheduled))

SPLIT = ("MUC \u2708 ACC\nDELIVERY\n074-46285514\nChecked-in\nMUC\nAMS\nACC\n"
         "20 SEP 23:00\n1 piece ready to be picked up at ACC\n"
         "20 SEP 22:58 -\n1 piece received at ACC from KL0589\n")
split = A._read_afkl_page(Page(SPLIT), "AFKL") or {}
check("Milestones whose date and event render on separate lines are read too",
      split.get("ata") == "20/09/2026", str(split))

NOTHING = ("MUC \u2708 ACC\nBOOKED\n074-46285514\nChecked-in\nMUC\nAMS\nACC\n"
           "18 SEP 10:00 - 1 piece booked on KL1234\n"
           "Some other ETA text 26 SEP " + "x" * 200 + "\n")
nothing = A._read_afkl_page(Page(NOTHING), "AFKL")
check("The current layout with no arrival and no expected arrival reads "
      "NOTHING, rather than guessing a date from the page",
      nothing is None, str(nothing))

OLD = ("EN ROUTE 074-05978372\nFlight schedule\n"
       "AMS - CAI   KL0553   06 SEP 09:40 - 06 SEP 14:25\n"
       "Progress details\nCAI\nARRIVAL 4 pcs Estimated: 07 SEP 14:25\n" + "x" * 200)
old = A._read_afkl_page(Page(OLD), "AFKL") or {}
check("The older layout still reads exactly as before",
      old.get("eta") == "07/09/2026" and old.get("ata") is None, str(old))
check("A page with no milestone lines is not read by the new reader",
      A._read_afkl_milestones("nothing dated here", "AFKL") is None)

print()
print("=" * 74)
print("2. THE MOVED PAGE IS NAMED, NOT MISTAKEN FOR A SLOW ONE")
print("=" * 74)
MOVED = ("Home Products Sending your shipments Network and Fleet About Us "
         "Contact\nOperational disruptions may affect our services.\n"
         "THIS PAGE HAS MOVED\n" + "x" * 200)
ok, why = A.afkl_detail_verdict(Page(MOVED), AWB)
check("The moved page is refused", ok is False)
check("...and the reason says the address no longer shows shipments",
      "has moved" in why, why)
check("A result page carrying the air waybill is still confirmed",
      A.afkl_detail_verdict(Page("MUC ✈ ACC\n" + RESULT_TAIL), AWB)[0])
check("The header search is tried before the direct address",
      SRC.index("landed = open_afkl_by_search(page, config, tracking_number)")
      < SRC.index("landed = open_afkl_detail(page, config, tracking_number)\n"
                  "            if landed is not None:"))
check("...and the direct address only when no search box was found",
      "if landed is None:\n                landed = open_afkl_detail" in SRC)
search = SRC.split("def open_afkl_by_search")[1].split("\ndef ")[0]
check("The search never touches the flight status card",
      "is_flight_status_field" in SRC.split("def find_afkl_header_search")[1]
      .split("\ndef ")[0])
check("Human verification is waited for, never solved",
      "await_human_verification(page, tracking_number, label)" in search)
check("Every tab is checked for the result, not only the one searched from",
      "_afkl_result_anywhere(page, tracking_number)" in search)
check("The log names the box the air waybill was typed into",
      "describe_search_box(box)" in search)
check("A home page that does not answer raises, instead of falling back",
      "raise AfklNavigationError(tracking_number, [{" in search)
check("One search cannot open two tabs: the button only follows a silent Enter",
      "if not wait_for_any(page, [(\"the search answered\", something_happened)]"
      in search)


# ─────────────────────────────────────────────────────────────────────────
# A SITE THAT BEHAVES LIKE BOTH SCREENSHOTS
# ─────────────────────────────────────────────────────────────────────────
PORT = int(os.environ.get("AFKL_SEARCH_PORT", "9688"))
HEADER = """<header><nav>
  <a href="/">Home</a> <a href="#">Products</a> <a href="#">Contact</a>
  <form id="site-search" onsubmit="go(event)">
    <input type="search" id="q" aria-label="Search">
    <button type="submit" aria-label="Search">&#128269;</button>
  </form>
</nav></header>
<script>
function go(e){ e.preventDefault();
  var v = document.getElementById('q').value;
  var target = '/search?q=' + encodeURIComponent(v);
  // The live site opened the result in a tab of its own on the 23rd.
  if (location.pathname.indexOf('/newtab') === 0) { window.open(target, '_blank'); return; }
  setTimeout(function(){ location.href = target; }, 300); }
</script>"""
MOVED_HTML = ("<!doctype html><html><body>" + HEADER +
              "<div>Operational disruptions may affect our services.</div>"
              "<h1>THIS PAGE HAS MOVED</h1><p>" + "x" * 300 +
              "</p></body></html>")


def result_html(awb):
    lines = "".join(
        "<li>{0}</li>".format(line) for line in (
            "20 SEP 23:00 - 1 piece ready to be picked up at ACC",
            "20 SEP 22:58 - 1 piece received at ACC from KL0589"))
    return ("<!doctype html><html><body>" + HEADER +
            "<h1>MUC <svg width='10' height='10'></svg> ACC</h1>"
            "<div>DELIVERY <span>OK NOTIFIED</span> " + awb + "</div>"
            "<div>File a claim</div><div>Set notifications</div>"
            "<div>Checked-in</div><div>MUC</div><div>AMS</div><div>ACC</div>"
            "<ul>" + lines + "</ul>"
            "<p>On Time: Your shipment has been delivered before LAT</p>"
            "</body></html>")


class Site(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/dead"):
            # A carrier that accepts the connection and never answers.
            time.sleep(8)
            return
        if parsed.path.startswith("/search"):
            awb = parse_qs(parsed.query).get("q", [""])[0]
            body = result_html(awb) if awb == AWB else (
                "<!doctype html><html><body>" + HEADER +
                "<p>No results found for this air waybill.</p>" + "x" * 300 +
                "</body></html>")
        else:
            body = MOVED_HTML            # the home page and the old address
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
    if Path("/opt/pw-browsers").is_dir():
        options += [{"headless": True, "executable_path": str(binary)}
                    for binary in sorted(Path("/opt/pw-browsers").glob(
                        "chromium-*/chrome-linux/chrome"))]
    for option in options:
        try:
            return playwright.chromium.launch(**option), None
        except Exception as error:
            last = str(error).split("\n")[0][:100]
    return None, last


print()
print("=" * 74)
print("3. END TO END, THROUGH get_portal_result()")
print("=" * 74)
server = ThreadingHTTPServer(("127.0.0.1", PORT), Site)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:{0}".format(PORT)

A.save_page_text = lambda *args, **kwargs: None
A.take_screenshot = lambda *args, **kwargs: None
A.PAGE_SETTLE_MAX_SECONDS = 1
A.AFKL_SEARCH_READY_MS = 5000
A.AFKL_DETAIL_READY_MS = 6000
A.PORTALS["AFKL"] = dict(A.PORTALS["AFKL"], search_url=BASE + "/",
                         urls=[BASE + "/mycargo/shipment/singlesearch"],
                         wait=6, attempts=1)
real_detail_url = A.build_afkl_detail_url
A.build_afkl_detail_url = lambda number: (
    BASE + "/mycargo/shipment/detail/" + number
    if real_detail_url(number) else None)

try:
    from playwright.sync_api import sync_playwright
except Exception as error:
    sync_playwright = None
    WHY = str(error)[:80]

NAMES = ("the old address is the moved page", "the header search is used",
         "the arrival is read", "a wrong shipment is not read")
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

            page.goto(A.build_afkl_detail_url(AWB))
            ok, why = A.afkl_detail_verdict(page, AWB)
            check("The old direct address lands on the moved page",
                  not ok and "has moved" in why, why)

            ladder = []
            real_open_detail = A.open_afkl_detail

            def watched(*args, **kwargs):
                ladder.append(True)
                return real_open_detail(*args, **kwargs)
            A.open_afkl_detail = watched

            started = time.time()
            result = None
            error = None
            try:
                result = A.get_portal_result(page, "AFKL", AWB)
            except Exception as problem:
                error = problem
            took = time.time() - started
            check("The shipment is found through the header search",
                  result is not None, repr(error)[:140])
            check("...and its arrival is read: ATA 20/09/2026",
                  (result or {}).get("ata") == "20/09/2026", str(result))
            check("...without ever trying the dead direct address",
                  not ladder, "{0} ladder call(s)".format(len(ladder)))
            check("...in well under the old two minutes",
                  took < 30, "{0:.1f}s".format(took))

            other = None
            try:
                A.get_portal_result(page, "AFKL", "074-99999999")
            except Exception as problem:
                other = problem
            check("A shipment the carrier does not have is reported as such, "
                  "not read off the wrong page",
                  isinstance(other, A.SkipShipment), repr(other)[:140])
            # ── the result opens in a tab of its own ─────────────────────
            print()
            print("=" * 74)
            print("4. THE RESULT IN A NEW TAB, AND A SITE THAT DOES NOT ANSWER")
            print("=" * 74)
            ladder.clear()
            A.PORTALS["AFKL"] = dict(A.PORTALS["AFKL"],
                                     search_url=BASE + "/newtab")
            tabs_before = len(page.context.pages)
            popup_result, popup_error = None, None
            try:
                popup_result = A.get_portal_result(page, "AFKL", AWB)
            except Exception as problem:
                popup_error = problem
            check("A result that opens in a new tab is found there",
                  (popup_result or {}).get("ata") == "20/09/2026",
                  repr(popup_error)[:140] if popup_error else str(popup_result))
            check("...still without touching the dead direct address",
                  not ladder)
            check("The tab it opened is held, not forgotten",
                  len(A.AFKL_HELD_PAGES) >= 1, str(len(A.AFKL_HELD_PAGES)))
            A.release_afkl_helpers(keep_page=page)
            check("...and closed at the next lookup, so tabs cannot pile up",
                  len(page.context.pages) == tabs_before
                  and not A.AFKL_HELD_PAGES,
                  "{0} -> {1} tabs".format(tabs_before,
                                           len(page.context.pages)))

            # ── the carrier does not answer at all ───────────────────────
            ladder.clear()
            A.NAVIGATION_TIMEOUT_MS = 3000
            A.log_reachability = lambda *args, **kwargs: None
            A.PORTALS["AFKL"] = dict(A.PORTALS["AFKL"],
                                     search_url=BASE + "/dead")
            dead_error = None
            started = time.time()
            try:
                A.get_portal_result(page, "AFKL", AWB)
            except Exception as problem:
                dead_error = problem
            took = time.time() - started
            check("A carrier whose home page does not answer is a navigation "
                  "error", isinstance(dead_error, A.AfklNavigationError),
                  repr(dead_error)[:140])
            check("...and the dead direct address is NOT then hammered too",
                  not ladder, "{0} ladder call(s)".format(len(ladder)))
            check("...so it gives up in seconds, not minutes",
                  took < 15, "{0:.1f}s".format(took))

            A.open_afkl_detail = real_open_detail
            browser.close()

server.shutdown()
print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(
    len(PASS), len(FAIL), ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
