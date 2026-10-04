"""
The ocean carriers added on the 4th of October.

    CMA CGM, MSC, Grimaldi Lines, COSCO Shipping, Maersk,
    Ocean Network Express (ONE), Hapag-Lloyd

None of their pages has been seen by this code. So what is pinned here is
the part that must hold whatever the pages turn out to look like: the Hub
carrier name picks the carrier; the reference is typed as the Hub holds it
and confirmed on the page letter for letter; an ETA beside the port of
discharge beats one at a transshipment port; a page for a different bill of
lading is never read; and, until OCEAN_WRITE=1, a result is logged and NOT
written to the Hub.

    python test_ocean.py
"""

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_eta as A                                        # noqa: E402

PASS, FAIL, SKIP = [], [], []


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

print("=" * 74)
print("1. THE HUB CARRIER NAME PICKS THE CARRIER")
print("=" * 74)
for carrier, reference, want in (
        ("CMA CGM", "CMDU123456789", "CMA_CGM"),
        ("Mediterranean Shipping Company (MSC)", "MEDUAB123456", "MSC"),
        ("Grimaldi Lines", "GRI0001", "GRIMALDI"),
        ("COSCO Shipping", "LJK010SHAAKI001", "COSCO"),
        ("Maersk Line", "231045678", "MAERSK"),
        ("Ocean Network Express (ONE)", "ONEYAB1234567", "ONE"),
        ("Hapag-Lloyd", "HLCUAB123456", "HAPAG")):
    check("{0} -> {1}".format(carrier, want),
          A.carrier_provider(carrier, reference) == want,
          str(A.carrier_provider(carrier, reference)))
check("A numeric Maersk bill of lading starting 157 is NOT sent to Qatar",
      A.carrier_provider("Maersk Line", "157123456") == "MAERSK")
check("'ONE' inside another carrier's reference is not Ocean Network Express",
      A.carrier_provider("Someone Else", "ONE123") is None)
check("The air carriers are untouched",
      A.carrier_provider("KLM Royal Dutch Airlines", "074-46285514") == "AFKL"
      and A.carrier_provider("Qatar Airways", "157 - 50601530") == "QATAR")
check("All seven are portals, typed verbatim and identity-checked",
      all(A.PORTALS[k].get("ocean") and A.PORTALS[k].get("verbatim")
          and A.PORTALS[k].get("verify_identity") for k in A.OCEAN_PORTALS))
check("COSCO opens the shipment straight from its own link pattern",
      "trackingType=BILLOFLADING&number={0}" in A.PORTALS["COSCO"]["deep_link"])
check("Maersk takes the reference as a path",
      A.PORTALS["MAERSK"]["deep_link"].endswith("/tracking/{0}"))
check("Writing to the Hub is OFF for ocean carriers by default",
      A.OCEAN_WRITE is False)

print()
print("=" * 74)
print("2. READING AN OCEAN RESULT")
print("=" * 74)
VOYAGE = ("Bill of Lading MEDUAB123456\n"
          "Port of Loading SHANGHAI\n"
          "Port of Transshipment ALGECIRAS  ETA 01/10/2026\n"
          "Port of Discharge ALEXANDRIA  ETA 12/10/2026\n" + "x" * 200)
read = A._read_ocean_page(Page(VOYAGE), "MSC") or {}
check("The ETA at the port of discharge is taken, not the transshipment one",
      read.get("eta") == "12/10/2026", str(read))
check("...and the label it came from is kept for the log",
      read.get("eta_source") == "ETA")
check("No arrival is invented for a shipment still at sea",
      read.get("ata") is None)
ARRIVED = ("Container MSCU1234567\nPort of Discharge ALEXANDRIA\n"
           "Actual Time of Arrival 11/10/2026\nDischarged 12/10/2026\n" + "x" * 200)
arrived = A._read_ocean_page(Page(ARRIVED), "MSC") or {}
check("An actual arrival at the port of discharge is an ATA",
      arrived.get("ata") == "11/10/2026", str(arrived))
check("A page with no labelled arrival reads nothing",
      A._read_ocean_page(Page("Bill of Lading X1 departed 01/10/2026 "
                              + "x" * 200), "MSC") is None)
check("A carrier saying it has no such bill of lading is a no-result",
      (A._read_ocean_page(Page("No data found for this reference. "
                               + "x" * 200), "MSC") or {}).get("no_result"))

print()
print("=" * 74)
print("3. IDENTITY, LETTER FOR LETTER")
print("=" * 74)
check("A bill of lading is found as written",
      A.awb_on_page(Page("B/L MEDUAB123456 " + "x" * 50), "MEDUAB123456"))
check("...ignoring spacing and dashes",
      A.awb_on_page(Page("B/L MEDU AB-123456"), "MEDUAB123456"))
check("A different bill of lading with the same digits is NOT this one",
      not A.awb_on_page(Page("B/L MSKUAB123456"), "MEDUAB123456"))
check("Air waybills are still matched on their digits",
      A.awb_on_page(Page("074 4628 5514"), "074-46285514"))


# ─────────────────────────────────────────────────────────────────────────
# TWO CARRIER SITES: ONE OPENED BY ADDRESS, ONE SEARCHED
# ─────────────────────────────────────────────────────────────────────────
PORT = int(os.environ.get("OCEAN_STUB_PORT", "9711"))
GOOD = "MEDUAB123456"


def voyage_page(reference):
    return ("<!doctype html><html><body><h1>Tracking</h1>"
            "<p>Bill of Lading " + reference + "</p>"
            "<table><tr><td>Port of Transshipment ALGECIRAS</td><td>ETA 01/10/2026</td></tr>"
            "<tr><td>Port of Discharge ALEXANDRIA</td><td>ETA 12/10/2026</td></tr></table>"
            "<p>" + "x" * 200 + "</p></body></html>")


SEARCH = ("<!doctype html><html><body><h1>Track a shipment</h1>"
          "<form onsubmit=\"event.preventDefault();location.href='/result?ref='"
          "+encodeURIComponent(document.getElementById('r').value)\">"
          "<input id='r' type='text' placeholder='Container / Bill of Lading number'>"
          "<button type='submit'>Search</button></form>"
          "<p>" + "x" * 200 + "</p></body></html>")


class Carrier(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/tracking/"):
            reference = unquote(parsed.path.split("/tracking/", 1)[1])
            body = voyage_page(reference) if reference == GOOD else (
                "<html><body>No data found for this reference." + "x" * 200 +
                "</body></html>")
        elif parsed.path.startswith("/result"):
            reference = parse_qs(parsed.query).get("ref", [""])[0]
            body = voyage_page(reference)
        else:
            body = SEARCH
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
print("4. END TO END, THROUGH get_provider_result()")
print("=" * 74)
server = ThreadingHTTPServer(("127.0.0.1", PORT), Carrier)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:{0}".format(PORT)

A.save_page_text = lambda *args, **kwargs: None
A.take_screenshot = lambda *args, **kwargs: None
A.PAGE_SETTLE_MAX_SECONDS = 1
A.PORTALS["MAERSK"] = dict(A.PORTALS["MAERSK"], urls=[BASE + "/tracking/"],
                           deep_link=BASE + "/tracking/{0}", wait=6)
A.PORTALS["MSC"] = dict(A.PORTALS["MSC"], urls=[BASE + "/search"], wait=6)

try:
    from playwright.sync_api import sync_playwright
except Exception as error:
    sync_playwright = None
    WHY = str(error)[:80]

NAMES = ("read-only by default", "written when enabled", "searched carrier",
         "wrong bill of lading", "grimaldi", "lazy tab")
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
            context = browser.new_context()
            pages = {"DHL": context.new_page()}
            ship = {"bol_awb": GOOD, "carrier": "Maersk Line",
                    "provider": "MAERSK", "current_eta": "10/10/2026"}

            held = None
            try:
                A.get_provider_result(pages, ship)
            except A.SkipShipment as error:
                held = str(error)
            check("By default a Maersk result is read and NOT written",
                  held is not None and "Not written" in (held or ""), held)
            check("...and the message says what was read, and from which label",
                  "eta 12/10/2026 (from 'ETA')" in (held or ""), held)
            check("A carrier's tab is opened the first time it is needed",
                  "MAERSK" in pages)

            A.OCEAN_WRITE = True
            result = A.get_provider_result(pages, ship)
            check("With OCEAN_WRITE on, the result is handed on to be written",
                  (result or {}).get("eta") == "12/10/2026", str(result))

            searched = A.get_provider_result(
                pages, {"bol_awb": GOOD, "carrier": "MSC", "provider": "MSC",
                        "current_eta": ""})
            check("A carrier with no deep link is searched from its page",
                  (searched or {}).get("eta") == "12/10/2026", str(searched))

            wrong = None
            try:
                A.get_provider_result(pages, dict(ship, bol_awb="MEDUZZ999999"))
            except A.SkipShipment as error:
                wrong = str(error)
            check("A bill of lading the carrier does not have is reported, "
                  "not read off another page", wrong is not None, wrong)

            grimaldi = None
            try:
                A.get_provider_result(pages, {"bol_awb": "GRI0001",
                                              "carrier": "Grimaldi Lines",
                                              "provider": "GRIMALDI",
                                              "current_eta": ""})
            except A.SkipShipment as error:
                grimaldi = str(error)
            check("Grimaldi is recognised and skipped with the real reason",
                  grimaldi is not None and "no tracking address" in grimaldi,
                  grimaldi)
            A.OCEAN_WRITE = False
            browser.close()

server.shutdown()
print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(
    len(PASS), len(FAIL), ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
