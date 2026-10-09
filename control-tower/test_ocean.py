"""
The ocean carriers added on the 4th of October.

    CMA CGM, MSC, Grimaldi Lines, COSCO Shipping, Maersk,
    Ocean Network Express (ONE), Hapag-Lloyd

What is pinned here: the Hub carrier name picks the carrier; the reference
is typed as the Hub holds it and confirmed on the page letter for letter; an
ETA beside the port of discharge beats one at a transshipment port; a page
for a different bill of lading is never read; a result is WRITTEN to the
Hub (the read-only gate of the 4th was lifted on the 6th, after MSC's page
was confirmed on a real run) and is a success only when the Hub reads the
date back; OCEAN_WRITE=0 still stops writing.

Section 5 drives the real code from the carrier page to the read-back. Its
Hub is a dictionary at the page boundary (open Manage / fill / save / the
field's value) — it proves the logic, NOT a write to the real eHub.

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
check("Writing to the Hub is ON for ocean carriers by default",
      A.OCEAN_WRITE is True)
_saved_env = os.environ.get("OCEAN_WRITE")
for _value, _want in (("0", False), ("off", False), ("1", True), ("", True)):
    os.environ["OCEAN_WRITE"] = _value
    _flag = os.environ.get("OCEAN_WRITE", "1").strip().lower() not in (
        "0", "false", "no", "off")
    check("OCEAN_WRITE={0!r} -> writing {1}".format(_value, "on" if _want else "off"),
          _flag is _want)
if _saved_env is None:
    os.environ.pop("OCEAN_WRITE", None)
else:
    os.environ["OCEAN_WRITE"] = _saved_env
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")
check("The flag in update_eta.py is the one tested above (default on)",
      'OCEAN_WRITE = os.environ.get("OCEAN_WRITE", "1").strip().lower() not in (' in SRC)

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


print()
print("=" * 74)
print("3b. THE OPERATOR'S OWN HUB ROWS, AND WHICH PAGE EACH ONE NEEDS")
print("=" * 74)
for carrier, reference, want in (
        ("Grimaldi", "S330348776", "GRIMALDI"),
        ("Grimaldi", "ANRB76464", "GRIMALDI"),
        ("MSC", "MEDUWO942017", "MSC"),
        ("Maersk", "274599284", "MAERSK"),
        ("CMA CGM", "NAM8681835", "CMA_CGM"),
        ("Hapag-Lloyd", "HLCUTA12609EPQF2", "HAPAG")):
    check("{0} {1} -> {2}".format(carrier, reference, want),
          A.carrier_provider(carrier, reference) == want,
          str(A.carrier_provider(carrier, reference)))
check("A Grimaldi S-reference is not mistaken for a DHL K-reference",
      not A.is_dhl_k_reference("S330348776"))
check("HLCU1234567 is a container number; HLCUTA12609EPQF2 is not",
      A.is_container_number("HLCU1234567")
      and not A.is_container_number("HLCUTA12609EPQF2"))
booking = A.portal_config_for("HAPAG", "HLCUTA12609EPQF2")
container = A.portal_config_for("HAPAG", "HLCU1234567")
check("A Hapag-Lloyd bill of lading goes to the booking page",
      "track-by-booking" in booking["urls"][0], booking["urls"][0])
check("A Hapag-Lloyd container goes to 'Tracing by Container'",
      "track-by-container" in container["urls"][0]
      and "Container" in container["box_label"], container["urls"][0])
shipment_box = A.portal_config_for("GRIMALDI", "S330348776")
equipment_box = A.portal_config_for("GRIMALDI", "GRIU1234567")
check("A Grimaldi shipment number goes in 'Shipment #'",
      "Shipment" in shipment_box["box_label"])
check("A Grimaldi container goes in 'Equipment #'",
      "Equipment" in equipment_box["box_label"])
check("Grimaldi needs a person for every search; nobody else does",
      [k for k in A.OCEAN_PORTALS if A.PORTALS[k].get("needs_person")]
      == ["GRIMALDI"])
check("The generic reference box no longer matches 'Enter code'",
      not __import__("re").search(A.OCEAN_REFERENCE_BOX, "Enter code", 2))


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


# The run of the 6th: MSC, MEDUAHP69377, "Estimated arrival - ETA 10/11/2026"
# read from the 'ETA' label. The layout below is NOT MSC's page — only the
# words the run's log recorded.
MSC_REF, MSC_ETA, STALE_REF = "MEDUAHP69377", "10/11/2026", "MEDUAHP00000"


def msc_page(reference):
    return ("<!doctype html><html><body><h1>Track a shipment</h1>"
            "<p>Bill of Lading: " + reference + "</p>"
            "<p>Port of Discharge TEMA</p><p>ETA " + MSC_ETA + "</p>"
            "<p>" + "x" * 200 + "</p></body></html>")


SEARCH = ("<!doctype html><html><body><h1>Track a shipment</h1>"
          "<form onsubmit=\"event.preventDefault();location.href='/result?ref='"
          "+encodeURIComponent(document.getElementById('r').value)\">"
          "<input id='r' type='text' placeholder='Container / Bill of Lading number'>"
          "<button type='submit'>Search</button></form>"
          "<p>" + "x" * 200 + "</p></body></html>")


# GNET as the operator's screenshot shows it: labels BESIDE the boxes, a
# security-code image, an "Enter code" box and Search. The "person" is a
# script that, once a reference has been typed and left alone, checks the
# code box is still empty, types the code a human would read, and presses
# Search. With ?person=0 nobody comes.
GNET = ("<!doctype html><html><body><h2>Container Tracking</h2>"
        "<form id='f' action='/gresult'><table>"
        "<tr><td>Equipment #</td><td><input name='equip' type='text'></td>"
        "<td>Shipment #</td><td><input name='ship' type='text'></td></tr>"
        "<tr><td>From Date</td><td><input name='from' type='text'></td>"
        "<td>To Date</td><td><input name='to' type='text'></td></tr>"
        "<tr><td>Security Code</td><td><img alt='code' src='data:,'></td>"
        "<td><input name='code' type='text' placeholder='Enter code'></td>"
        "<td><input type='hidden' name='untouched' value='?'>"
        "<button type='submit'>Search</button></td></tr></table></form>"
        "<script>"
        "var person = location.search.indexOf('person=0') < 0, last='', since=0;"
        "setInterval(function(){"
        " var f=document.getElementById('f'), v=f.equip.value+'|'+f.ship.value;"
        " if (v==='|') return;"
        " if (v!==last){last=v; since=Date.now(); return;}"
        " if (!person || Date.now()-since<1500) return;"
        " person=false;"
        " f.untouched.value = f.code.value==='' ? '1' : '0';"
        " f.code.value='7Q4K'; f.submit();"
        "}, 250);"
        "</script><p>" + "x" * 200 + "</p></body></html>")
GNET_SEEN = []


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
        elif parsed.path.startswith("/gnet"):
            body = GNET
        elif parsed.path.startswith("/gresult"):
            query = {k: v[0] for k, v in parse_qs(
                parsed.query, keep_blank_values=True).items()}
            GNET_SEEN.append(query)
            body = voyage_page(query.get("ship") or query.get("equip"))
        elif parsed.path.startswith("/result"):
            reference = parse_qs(parsed.query).get("ref", [""])[0]
            if reference == MSC_REF:
                body = msc_page(MSC_REF)
            elif reference == STALE_REF:
                body = msc_page(MSC_REF)       # another shipment's result
            else:
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


class HubBoundary(object):
    """
    The Hub at the page boundary, for section 5: which Manage page is open,
    the value a date field holds, and Save. Everything above it —
    update_internal_shipment, update_one_view, verify_saved_date and the
    outcome rules — is the production code.
    """

    def __init__(self):
        self.held, self.pending, self.opened, self.saves = {}, {}, [], 0
        self.mode = "persist"            # persist | drop | unreadable
        self.current = None

    def install(self):
        hub = self

        class Field(object):
            def __init__(self, name):
                self.name = name

            @property
            def first(self):
                return self

            def wait_for(self, **kw):
                pass

            def input_value(self):
                return hub.held.get((hub.current, self.name), "")

        def open_manage(page, view, bol, table_page):
            hub.current = bol
            hub.opened.append((view, bol))
            return table_page

        def fill(page, field, value):
            hub.pending[(hub.current, field)] = value

        def save(page):
            hub.saves += 1
            if hub.mode != "drop":
                hub.held.update(hub.pending)
            hub.pending.clear()

        A.click_manage_in_view = open_manage
        A.select_shipment_info_tab = lambda page, view, field=None: True
        A.fill_date_field = fill
        A.save_manage_page = save
        A.ensure_filtered_page = lambda *args, **kwargs: None
        A.field_candidates = lambda page, name: (
            [] if hub.mode == "unreadable" else [(name, Field(name))])
        A.find_field_ignoring_visibility = lambda page, name: None
        A.page_is_settled = lambda page: True
        A.all_scopes = lambda page: [page]
        A.ml_episode_begin = lambda *args, **kwargs: None
        A.ml_episode_end = lambda *args, **kwargs: None
        A.ml_record = lambda *args, **kwargs: None


def ocean_write_chain(pages):
    """
    MSC carrier result -> ETA extracted -> identity -> write -> read-back ->
    SUCCESS only when the read-back matches. main()'s rule is: a shipment is
    SUCCESS when update_internal_shipment returns; any exception is not.
    """
    print()
    print("=" * 74)
    print("5. OCEAN WRITE CHAIN: CARRIER PAGE -> HUB WRITE -> READ-BACK")
    print("=" * 74)
    hub = HubBoundary()
    hub.install()
    A.DRY_RUN = False
    A.VERIFY_AFTER_SAVE = True
    A.OCEAN_WRITE = True
    msc = {"bol_awb": MSC_REF, "carrier": "MSC", "provider": "MSC",
           "current_eta": "05/11/2026", "table_page": 1}

    result = A.get_provider_result(pages, msc)
    check("MSC MEDUAHP69377: ETA extracted = 10/11/2026 from the 'ETA' label",
          result.get("eta") == MSC_ETA and result.get("eta_source") == "ETA", str(result))
    check("...the result is marked: success only on a matching read-back",
          result.get("read_back_required") is True)
    action = A.update_internal_shipment(object(), msc, result)
    check("The ETA is written to the COE view of THAT shipment",
          ("COE", MSC_REF) in hub.opened and hub.saves == 1, str(hub.opened))
    check("Read back from the Hub after Save: 10/11/2026",
          hub.held.get((MSC_REF, "ETA")) == MSC_ETA and hub.opened.count(("COE", MSC_REF)) == 2,
          "{0} {1}".format(hub.held, hub.opened))
    check("Read-back matches -> update_internal_shipment returns (main(): SUCCESS)",
          "COE ETA updated with 10/11/2026 and saved" in (action or {}).get("coe", ""),
          str(action))
    check("No ATA was written: none was published", (MSC_REF, "ATA") not in hub.held)

    # The Hub drops the value: a mismatch is a failure, never a success.
    hub2 = HubBoundary()
    hub2.install()
    hub2.mode = "drop"
    dropped = None
    try:
        A.update_internal_shipment(object(), msc, dict(result))
    except Exception as error:
        dropped = error
    check("Saved but the Hub reads back something else -> NOT a success",
          dropped is not None and "not verified" in str(dropped), repr(dropped))

    # The read-back cannot be performed: for an ocean write, NOT a success.
    hub3 = HubBoundary()
    hub3.install()
    hub3.mode = "unreadable"
    unread = None
    try:
        A.update_internal_shipment(object(), msc, dict(result))
    except Exception as error:
        unread = error
    check("Saved but the read-back could not be performed -> WriteUnverified, "
          "not a success", isinstance(unread, A.WriteUnverified), repr(unread))
    check("...declaring the stage and what was and was not confirmed",
          isinstance(unread, A.WriteUnverified)
          and unread.failure["stage"] == "hub_read_back"
          and unread.failure["observed"] == {"written": MSC_ETA, "read_back": None},
          str(getattr(unread, "failure", None)))
    check("...and classified as a read-back failure",
          A.classify_failure(unread) != A.SUCCESS if unread else False)

    # VERIFY_AFTER_SAVE=0 does not let an ocean write skip its read-back.
    hub4 = HubBoundary()
    hub4.install()
    A.VERIFY_AFTER_SAVE = False
    try:
        A.update_internal_shipment(object(), msc, dict(result))
    finally:
        A.VERIFY_AFTER_SAVE = True
    check("VERIFY_AFTER_SAVE=0: an ocean write is still read back",
          hub4.opened.count(("COE", MSC_REF)) == 2, str(hub4.opened))

    # An air result (no read_back_required) keeps its existing behaviour.
    hub5 = HubBoundary()
    hub5.install()
    hub5.mode = "unreadable"
    air = A.update_internal_shipment(object(), dict(msc, bol_awb="074-46285514"),
                                     {"eta": MSC_ETA, "ata": None})
    check("An air carrier's unreadable read-back is unchanged (saved, unverified)",
          "updated with" in (air or {}).get("coe", ""), str(air))

    # WRONG SHIPMENT. The carrier shows MEDUAHP69377's result for a request
    # about MEDUAHP00000: nothing is read, nothing is written.
    hub6 = HubBoundary()
    hub6.install()
    stale = None
    try:
        r = A.get_provider_result(pages, dict(msc, bol_awb=STALE_REF))
        A.update_internal_shipment(object(), dict(msc, bol_awb=STALE_REF), r)
    except Exception as error:
        stale = error
    check("A result page for another bill of lading is never written",
          isinstance(stale, A.SkipShipment) and not hub6.held and hub6.saves == 0,
          "{0!r} {1}".format(stale, hub6.held))

    # A date that is not a date never reaches the Hub.
    checked = A.validate_arrival_result(
        {"provider": "MSC", "tracking_status": "Arrived", "eta": MSC_ETA,
         "ata": "31/12/2099"}, "MSC", MSC_REF)
    check("An 'actual arrival' in the future is dropped before the write",
          checked.get("ata") is None and checked.get("eta") == MSC_ETA, str(checked))


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

NAMES = ("written by default", "stop switch", "searched carrier",
         "wrong bill of lading", "grimaldi shipment", "grimaldi container",
         "grimaldi code untouched", "grimaldi nobody", "grimaldi unattended",
         "lazy tab", "ocean write chain")
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

            result = A.get_provider_result(pages, ship)
            check("By default a Maersk result is handed on to be written",
                  (result or {}).get("eta") == "12/10/2026", str(result))
            check("...marked so the write counts only once it is read back",
                  (result or {}).get("read_back_required") is True, str(result))
            check("A carrier's tab is opened the first time it is needed",
                  "MAERSK" in pages)

            A.OCEAN_WRITE = False
            held = None
            try:
                A.get_provider_result(pages, ship)
            except A.SkipShipment as error:
                held = str(error)
            check("With OCEAN_WRITE=0 (the operator's stop switch) it is read and "
                  "NOT written", held is not None and "Not written" in (held or ""), held)
            check("...and the message says what was read, from which label, and "
                  "which setting stopped it", "eta 12/10/2026 (from 'ETA')" in (held or "")
                  and "OCEAN_WRITE=0" in (held or ""), held)
            A.OCEAN_WRITE = True

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

            A.PORTALS["GRIMALDI"] = dict(
                A.PORTALS["GRIMALDI"], urls=[BASE + "/gnet?person=1"], wait=6)
            A.CAPTCHA_POLL_MS = 500
            os.environ["CAPTCHA_WAIT_MS"] = "20000"
            grimaldi = A.get_provider_result(
                pages, {"bol_awb": "S330348776", "carrier": "Grimaldi",
                        "provider": "GRIMALDI", "current_eta": ""})
            seen = GNET_SEEN[-1] if GNET_SEEN else {}
            check("Grimaldi S330348776: typed in Shipment #, read after the "
                  "person searched", (grimaldi or {}).get("eta") == "12/10/2026"
                  and seen.get("ship") == "S330348776"
                  and seen.get("equip") == "", "{0} {1}".format(grimaldi, seen))
            check("The security code box was empty when the person reached it "
                  "— the run never typed in it", seen.get("untouched") == "1",
                  str(seen))

            GNET_SEEN[:] = []
            boxed = A.get_provider_result(
                pages, {"bol_awb": "GRIU1234567", "carrier": "Grimaldi Lines",
                        "provider": "GRIMALDI", "current_eta": ""})
            seen = GNET_SEEN[-1] if GNET_SEEN else {}
            check("A Grimaldi container is typed in Equipment #",
                  (boxed or {}).get("eta") == "12/10/2026"
                  and seen.get("equip") == "GRIU1234567"
                  and seen.get("ship") == "" and seen.get("untouched") == "1",
                  "{0} {1}".format(boxed, seen))

            A.PORTALS["GRIMALDI"]["urls"] = [BASE + "/gnet?person=0"]
            os.environ["CAPTCHA_WAIT_MS"] = "3000"
            GNET_SEEN[:] = []
            nobody = None
            try:
                A.get_provider_result(
                    pages, {"bol_awb": "ANRB76464", "carrier": "Grimaldi",
                            "provider": "GRIMALDI", "current_eta": ""})
            except A.CaptchaRequired as error:
                nobody = str(error)
            check("Nobody types the code: HUMAN VERIFICATION REQUIRED, nothing "
                  "submitted", nobody is not None and not GNET_SEEN,
                  "{0} {1}".format(nobody, GNET_SEEN))

            os.environ["CAPTCHA_WAIT_MS"] = "0"
            before = pages["GRIMALDI"].url
            pages["GRIMALDI"].goto("about:blank")
            unattended = None
            try:
                A.get_provider_result(
                    pages, {"bol_awb": "S330221931", "carrier": "Grimaldi",
                            "provider": "GRIMALDI", "current_eta": ""})
            except A.CaptchaRequired as error:
                unattended = str(error)
            check("An unattended run (CAPTCHA_WAIT_MS=0) does not even open "
                  "GNET", unattended is not None
                  and pages["GRIMALDI"].url == "about:blank",
                  "{0} {1}".format(unattended, before))
            os.environ.pop("CAPTCHA_WAIT_MS", None)
            ocean_write_chain(pages)
            browser.close()

server.shutdown()
print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(
    len(PASS), len(FAIL), ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
