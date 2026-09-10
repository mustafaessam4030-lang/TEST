"""
The OTHER form on the myCargo page.

The landing page carries two:

    left    Track a shipment       AWB starts with 074 or 057   -> the shipment
    right   Check flight status    flight number and a date     -> the aircraft

Two things are pinned here. The air waybill path must never touch the right
one — an air waybill typed into the flight number box and submitted with Enter
is what produced "Field is required" and "Please select a valid date" on the
operator's screen, and made the run report "no result" for a shipment that was
never looked up. And when the shipment page carries no arrival date at all,
the right one must be used deliberately, on the flight the air waybill names,
with the answer labelled as the flight's arrival and never as the shipment's.

Everything runs against a local stand-in for the page, so it needs no
internet. Its markup mirrors the real card's placeholders and error text.

    python test_flight_status.py
"""

import os
import re
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

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


# ─────────────────────────────────────────────────────────────────────────
# 1 · READING THE LEG OFF THE SHIPMENT PAGE
# ─────────────────────────────────────────────────────────────────────────
print("=" * 74)
print("1. WHICH FLIGHT, AND WHEN — READ FROM THE AIR WAYBILL PAGE")
print("=" * 74)

SHIPMENT = """EN ROUTE 057-05765454
Flight schedule
BRU - CDG   AF1234   03 SEP 08:00 - 03 SEP 09:20
CDG - JRO   AF0877   04 SEP 10:15 - 04 SEP 20:15
Progress details
Estimated Pick up time JRO :
"""

leg = A.afkl_last_leg(SHIPMENT)
check("The leg that ends where the air waybill ends is the one taken",
      leg and leg["flight"] == "AF0877", str(leg))
check("...with its stations", leg and (leg["origin"], leg["destination"])
      == ("CDG", "JRO"), str(leg))
check("...and its DEPARTURE date, which is what the card asks for",
      leg and leg["date"] == "04/09/2026", str(leg and leg["date"]))
check("A page with no flight schedule yields nothing rather than a guess",
      A.afkl_last_leg("EN ROUTE 057-05765454\nProgress details\n") is None)
check("A leg on another carrier is not turned into an AF/KL flight",
      A.afkl_last_leg("DXB - JRO   EK0705   04 SEP 10:15") is None)
check("KLM in prose is not read as flight KL",
      A.afkl_last_leg("KLM Cargo BRU - JRO service") is None)
check("The air waybill's own digits are not read as a flight number",
      A.afkl_last_leg("057-05765454 BRU - JRO") is None)
check("Without a destination the last leg printed is used",
      (A.afkl_last_leg("AMS - LOS  KL0587  01 SEP 09:00") or {}).get("flight")
      == "KL0587")
check("A string date from the reader is understood",
      A.flight_date_value("04/09/2026") == datetime(2026, 9, 4))
check("...and rubbish is not", A.flight_date_value("soon") is None)


# ─────────────────────────────────────────────────────────────────────────
# 2 · WHAT GOES WHERE — THE RULE, WITHOUT A BROWSER
# ─────────────────────────────────────────────────────────────────────────
print()
print("=" * 74)
print("2. A FLIGHT ARRIVAL IS NOT THE SHIPMENT'S ARRIVAL")
print("=" * 74)

STATUS_LANDED = {"flight": "AF0877", "scheduled_arrival": "04/09/2026",
                 "actual_arrival": "04/09/2026"}
STATUS_DUE = {"flight": "AF0877", "scheduled_arrival": "06/09/2026",
              "actual_arrival": None}

blank = {"provider": "AFKL", "tracking_status": "Estimated arrival",
         "eta": None, "ata": None}
filled = A.apply_flight_status(dict(blank), leg, STATUS_LANDED)
check("A flight that has ACTUALLY landed still only produces an ETA",
      filled["eta"] == "04/09/2026" and filled["ata"] is None, str(filled))
check("...and says where the date came from",
      "flight status" in (filled.get("eta_source") or ""),
      str(filled.get("eta_source")))
check("...and names the flight in the source",
      "AF0877" in (filled.get("eta_source") or ""))
due = A.apply_flight_status(dict(blank), leg, STATUS_DUE)
check("A scheduled arrival becomes an ETA", due["eta"] == "06/09/2026"
      and due["ata"] is None)

already = A.apply_flight_status(
    {"provider": "AFKL", "tracking_status": "Arrived", "eta": None,
     "ata": "01/09/2026"}, leg, STATUS_LANDED)
check("A date the shipment page gave is never overwritten",
      already["ata"] == "01/09/2026" and already["eta"] is None, str(already))
check("...though the flight is still recorded alongside it",
      already.get("flight_status") == STATUS_LANDED)
estimated = A.apply_flight_status(
    {"provider": "AFKL", "tracking_status": "Estimated arrival",
     "eta": "02/09/2026", "ata": None}, leg, STATUS_LANDED)
check("An existing ETA is not overwritten either",
      estimated["eta"] == "02/09/2026")
check("Nothing is invented when the card answered nothing",
      A.apply_flight_status(dict(blank), leg, None)["eta"] is None)
check("Nothing is invented when the card gave no arrival",
      A.apply_flight_status(dict(blank), leg,
                            {"flight": "AF0877", "scheduled_arrival": None,
                             "actual_arrival": None})["eta"] is None)

src = (HERE / "update_eta.py").read_text(encoding="utf-8")
body = src.split("def apply_flight_status")[1].split("\ndef ")[0]
check("apply_flight_status contains no path that writes an ATA",
      'result["ata"]' not in body.replace('result.get("ata")', ""))


# ─────────────────────────────────────────────────────────────────────────
# 2b · THE HUB DECIDES WHICH QUESTION GETS ASKED
# ─────────────────────────────────────────────────────────────────────────
print()
print("=" * 74)
print("2b. WHAT THE HUB CARRIES DECIDES WHICH FORM IS USED")
print("=" * 74)


class Cell(object):
    def __init__(self, text):
        self.text = text

    def inner_text(self):
        return self.text


class Cells(object):
    def __init__(self, texts):
        self.texts = texts

    def count(self):
        return len(self.texts)

    def nth(self, index):
        return Cell(self.texts[index])


class Row(object):
    def __init__(self, texts):
        self.texts = texts

    def locator(self, selector):
        return Cells(self.texts)


class Rows(object):
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def nth(self, index):
        return Row(self.rows[index])


class Table(object):
    """Just enough of a Playwright table for the header map and row reader."""

    def __init__(self, headers, rows):
        self.headers = headers
        self.rows = rows

    def locator(self, selector):
        if "th" in selector:
            return Cells(self.headers)
        if "tbody tr" in selector:
            return Rows(self.rows)
        if selector == "tr":
            return Rows([self.headers])
        return Cells([])


PLAIN = ["BOL/AWB Number", "Carrier Name", "ETA", "Status"]
WITH_FLIGHT = ["BOL/AWB Number", "Carrier Name", "Flight Number",
               "Flight Date", "ETA", "Status"]

A.write_log = lambda *args, **kwargs: None
plain_map = A.build_header_map(Table(PLAIN, []))
check("A Hub with no flight columns still works",
      set(plain_map) == {"bol_awb", "carrier", "eta", "status"},
      str(sorted(plain_map)))
rich_map = A.build_header_map(Table(WITH_FLIGHT, []))
check("A Hub that prints a flight column has it read",
      rich_map.get("flight") == 2 and rich_map.get("flight_date") == 3,
      str(rich_map))

check("Flight numbers are normalised, whatever the Hub types",
      [A.normalise_flight_number(v) for v in
       ("AF 0877", "af0877", "KL-8246", "MP 123")]
      == ["AF0877", "AF0877", "KL8246", "MP123"])
check("...and anything that is not an AF/KL/MP flight is left alone",
      [A.normalise_flight_number(v) for v in
       ("EK0705", "", None, "TBA", "057-05765454")] == [None] * 5)

check("A Hub row naming a flight and a date is asked about directly",
      A.hub_flight_leg({"hub_flight": "AF0877",
                        "hub_flight_date": "04/09/2026"})
      == {"flight": "AF0877", "date": "04/09/2026", "origin": None,
          "destination": None, "source": "the Hub"})
check("A Hub row naming no flight yields nothing",
      A.hub_flight_leg({"bol_awb": "05705765454"}) is None)
check("The Hub's flight outranks the page's",
      A.combine_legs(
          {"flight": "KL8246", "date": "05/09/2026", "source": "the Hub"},
          leg)["flight"] == "KL8246")
check("A Hub flight with no date is dated from the page's own schedule, "
      "but only for the SAME flight",
      A.combine_legs({"flight": "AF0877", "date": None, "source": "the Hub"},
                     leg)["date"] == "04/09/2026")
check("...and never from a different flight's leg",
      A.combine_legs({"flight": "KL8246", "date": None, "source": "the Hub"},
                     leg)["flight"] == "AF0877")
check("With nothing from the Hub, the page's leg is used",
      A.combine_legs(None, leg) is leg)
check("With nothing anywhere, nothing is asked",
      A.combine_legs(None, None) is None)


# ─────────────────────────────────────────────────────────────────────────
# THE STAND-IN PAGE — both forms, as they appear on myCargo
# ─────────────────────────────────────────────────────────────────────────
PORT = int(os.environ.get("MYCARGO_STUB_PORT", "9644"))

PAGE = """<!doctype html><html><head><title>Track and Trace</title></head>
<body><h1>Track and Trace</h1>
<div class="left"><h2>Track a shipment</h2>
  <form id="awbform" onsubmit="track(event)">
    <input type="text" id="awb" placeholder="AWB starts with 074 or 057">
    <button type="submit" id="trackbtn">Track</button>
  </form>
</div>
<div class="right"><h2>Check flight status</h2>
  <form id="flightform" onsubmit="checkflight(event)">
    <input type="text" id="origin" placeholder="Select an origin">
    <input type="text" id="destination" placeholder="Select a destination">
    <input type="text" id="flightno" placeholder="Enter flight number (eg AF3620 or KL8246)">
    <div id="noerr" class="err" hidden>Field is required</div>
    <input type="text" id="flightdate" placeholder="Select a date" autocomplete="off">
    <div id="dateerr" class="err" hidden>Please select a valid date</div>
    <button type="submit" id="flightbtn">Check flight status</button>
  </form>
</div>
<div id="out"></div>
<script>
// The real card only accepts what its picker produces. ACCEPTED is the one
// typed format this stand-in tolerates; everything else is refused the way
// the real one refuses it, without moving.
var ACCEPTED = %ACCEPTED%;
function shows(id, on) { document.getElementById(id).hidden = !on; }
function track(e) {
  e.preventDefault();
  var v = document.getElementById('awb').value;
  document.getElementById('out').innerText = 'TRACKED ' + v;
}
function valid(v) {
  if (!v) return false;
  // ACCEPTED null models the strictest card there is: it refuses every
  // typed format and accepts only what its own picker writes.
  if (ACCEPTED === null) return /^\d{4}-\d{2}-\d{2}$/.test(v);
  return v === ACCEPTED;
}
function checkflight(e) {
  e.preventDefault();
  var n = document.getElementById('flightno').value.trim();
  var d = document.getElementById('flightdate').value.trim();
  shows('noerr', !n);
  shows('dateerr', !valid(d));
  if (!n || !valid(d)) { return; }
  document.getElementById('out').innerHTML =
    '<div>Flight ' + n + ' CDG - JRO</div>' +
    '<div>Scheduled arrival 04 Sep 2026 20:15</div>' +
    '<div>Actual arrival 04 Sep 2026 20:42</div>' +
    '<div>Landed</div>' +
    '<div>' + 'x'.repeat(200) + '</div>';
}
// A real reactive form marks a field touched and validates it on blur,
// not only on submit. The stand-in does the same, so the automation meets
// the same "Please select a valid date" it meets on the real card.
document.getElementById('flightdate').addEventListener('blur', function () {
  var v = document.getElementById('flightdate').value.trim();
  shows('dateerr', v !== '' && !valid(v));
});
// The calendar, for the case where no typed format is taken at all.
document.addEventListener('click', function (ev) {
  if (ev.target.id !== 'flightdate' || ACCEPTED !== null) return;
  if (document.getElementById('cal')) return;
  var cal = document.createElement('div');
  cal.id = 'cal';
  cal.innerHTML = '<div id="calhead">Sep 2026</div>';
  for (var day = 1; day <= 30; day++) {
    var cell = document.createElement('button');
    cell.setAttribute('role', 'gridcell');
    cell.setAttribute('type', 'button');
    cell.textContent = String(day);
    cell.onclick = function (e2) {
      e2.preventDefault();
      document.getElementById('flightdate').value =
        '2026-09-' + ('0' + e2.target.textContent).slice(-2);
      shows('dateerr', false);
      cal.remove();
    };
    cal.appendChild(cell);
  }
  document.body.appendChild(cal);
});
</script>
</body></html>"""

ACCEPTED_FORMAT = {"value": '"04/09/2026"'}


class MyCargo(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        accepted = query.get("accept", [None])[0]
        if accepted == "none":
            value = "null"
        elif accepted:
            value = '"{0}"'.format(accepted)
        else:
            value = ACCEPTED_FORMAT["value"]
        page = PAGE.replace("%ACCEPTED%", value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)


def launch_options():
    for channel in ("msedge", "chrome", None):
        options = {"headless": True}
        if channel:
            options["channel"] = channel
        yield options
    named = os.environ.get("CHROMIUM_PATH")
    roots = [Path(named)] if named else []
    if Path("/opt/pw-browsers").is_dir():
        for pattern in ("chromium-*/chrome-linux/chrome",
                        "chromium-*/chrome-win/chrome.exe"):
            roots.extend(sorted(Path("/opt/pw-browsers").glob(pattern)))
    for binary in roots:
        if binary.exists():
            yield {"headless": True, "executable_path": str(binary)}


def open_browser(playwright):
    last = None
    for options in launch_options():
        try:
            return playwright.chromium.launch(**options), None
        except Exception as error:
            last = str(error).split("\n")[0][:110]
    return None, last


BROWSER_CHECKS = [
    "the air waybill box is the one found",
    "the flight card is left untouched",
    "the card is driven and read",
    "a picky date format is worked through",
    "the calendar is used when nothing typed is accepted",
    "no page is left open",
]

print()
print("=" * 74)
print("3. AGAINST A PAGE WITH BOTH FORMS ON IT")
print("=" * 74)

server = ThreadingHTTPServer(("127.0.0.1", PORT), MyCargo)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:{0}/mycargo/shipment/singlesearch".format(PORT)

A.write_log = lambda *args, **kwargs: None
A.save_page_text = lambda *args, **kwargs: None
A.take_screenshot = lambda *args, **kwargs: None
A.PORTALS["AFKL"] = dict(A.PORTALS["AFKL"], urls=[URL])
A.PAGE_SETTLE_MAX_SECONDS = 1
A.FLIGHT_STATUS_FORM_MS = 4000
A.FLIGHT_STATUS_RESULT_MS = 6000

try:
    from playwright.sync_api import sync_playwright
except Exception as error:
    sync_playwright = None
    WHY = str(error)[:100]

if sync_playwright is None:
    for name in BROWSER_CHECKS:
        skip(name, "playwright is not importable: {0}".format(WHY))
else:
    with sync_playwright() as playwright:
        A._PLAYWRIGHT = playwright
        browser, why = open_browser(playwright)
        if browser is None:
            for name in BROWSER_CHECKS:
                skip(name, "no browser could be launched: {0}".format(why))
        else:
            context = browser.new_context()
            page = context.new_page()
            page.goto(URL)

            # ── the air waybill path, on a page with a decoy form ────────
            field = A.find_portal_input(page, A.PORTALS["AFKL"]["placeholder"])
            check("The air waybill box is the one found",
                  field is not None
                  and field.get_attribute("id") == "awb",
                  field.get_attribute("id") if field else "nothing found")
            check("...and it is not mistaken for a flight field",
                  field is not None and not A.is_flight_status_field(field))
            check("The flight number box IS recognised as one",
                  A.is_flight_status_field(page.locator("#flightno")))
            check("So is the date box",
                  A.is_flight_status_field(page.locator("#flightdate")))

            # The catch-all is the dangerous one: it used to match any
            # visible text input, and on this page the first three are the
            # decoys. It is asked directly, with the placeholder pattern
            # deliberately made useless.
            catch = A.find_portal_input(page, "nothing-matches-this")
            check("Even the catch-all cannot land on the flight card",
                  catch is None or catch.get_attribute("id") == "awb",
                  catch.get_attribute("id") if catch else "none")

            A.submit_portal_awb(page, field, A.PORTALS["AFKL"], "05705765454")
            page.wait_for_timeout(300)
            check("The air waybill was submitted through its own form",
                  "TRACKED" in page.locator("#out").inner_text())
            check("The flight card was left untouched — no 'Field is "
                  "required'", page.locator("#noerr").is_hidden())
            check("...and no 'Please select a valid date'",
                  page.locator("#dateerr").is_hidden())

            refused = None
            try:
                A.submit_portal_awb(page, page.locator("#flightno"),
                                    A.PORTALS["AFKL"], "05705765454")
            except Exception as error:
                refused = error
            check("Handed a flight field, the air waybill path refuses "
                  "outright", isinstance(refused, A.SkipShipment),
                  type(refused).__name__)
            check("...and says which form it was looking at",
                  "flight status form" in str(refused))
            check("...having typed nothing into it",
                  (page.locator("#flightno").input_value() or "") == "")

            # ── the flight card, driven on purpose ───────────────────────
            before = len(context.pages)
            found = A.check_afkl_flight_status(page, leg)
            check("The card is driven and read",
                  found is not None, str(found))
            check("...the ACTUAL arrival is read as actual",
                  found and found["actual_arrival"] == "04/09/2026", str(found))
            check("...and the scheduled arrival separately",
                  found and found["scheduled_arrival"] == "04/09/2026",
                  str(found))
            check("No page is left open afterwards",
                  len(context.pages) == before,
                  "{0} -> {1}".format(before, len(context.pages)))
            check("The caller's own page was not navigated away",
                  page.url.endswith("singlesearch"))

            # ── a card that only takes one format ────────────────────────
            A.PORTALS["AFKL"] = dict(A.PORTALS["AFKL"],
                                     urls=[URL + "?accept=2026-09-04"])
            picky = A.check_afkl_flight_status(page, leg)
            check("A picky date format is worked through",
                  picky is not None and picky["actual_arrival"] == "04/09/2026",
                  str(picky))

            # ── a card that takes no typed date at all ───────────────────
            A.PORTALS["AFKL"] = dict(A.PORTALS["AFKL"],
                                     urls=[URL + "?accept=none"])
            by_calendar = A.check_afkl_flight_status(page, leg)
            check("The calendar is used when nothing typed is accepted",
                  by_calendar is not None
                  and by_calendar["actual_arrival"] == "04/09/2026",
                  str(by_calendar))
            check("...and still nothing is left open",
                  len(context.pages) == before,
                  "{0} pages".format(len(context.pages)))

            # ── a leg the card cannot answer for ─────────────────────────
            A.PORTALS["AFKL"] = dict(A.PORTALS["AFKL"], urls=[URL])
            check("A non-AF/KL/MP flight is not submitted at all",
                  A.check_afkl_flight_status(
                      page, {"flight": "EK0705", "date": "04/09/2026"}) is None)
            check("A leg with no date is not submitted either",
                  A.check_afkl_flight_status(
                      page, {"flight": "AF0877", "date": None}) is None)
            check("Nothing was left open by either refusal",
                  len(context.pages) == before)

            # ── the Hub decides, end to end ──────────────────────────────
            print()
            print("=" * 74)
            print("4. THE HUB'S OWN FLIGHT, WHEN THE AIR WAYBILL GIVES "
                  "NOTHING")
            print("=" * 74)
            real_portal_result = A.get_portal_result

            def nothing_readable(*args, **kwargs):
                raise A.SkipShipment("KLM returned no arrival date that "
                                     "could be read.")

            hub_row = {"bol_awb": "05705765454", "carrier": "KLM",
                       "provider": "AFKL", "hub_flight": "AF 0877",
                       "hub_flight_date": "04/09/2026"}
            try:
                A.get_portal_result = nothing_readable
                answered = A.get_provider_result({"AFKL": page}, hub_row)
                check("The Hub's flight is asked when the air waybill gives "
                      "nothing readable",
                      answered is not None and answered.get("eta")
                      == "04/09/2026", str(answered))
                check("...and it is still only an estimate",
                      answered.get("ata") is None)
                check("...attributed to the flight, not to the shipment",
                      "AF0877" in (answered.get("eta_source") or ""),
                      str(answered.get("eta_source")))

                bare = dict(hub_row)
                bare.pop("hub_flight")
                refused = None
                try:
                    A.get_provider_result({"AFKL": page}, bare)
                except Exception as error:
                    refused = error
                check("A Hub row with no flight still fails the way it always "
                      "did", isinstance(refused, A.SkipShipment),
                      type(refused).__name__)

                undated = dict(hub_row)
                undated.pop("hub_flight_date")
                refused = None
                try:
                    A.get_provider_result({"AFKL": page}, undated)
                except Exception as error:
                    refused = error
                check("A Hub flight with no date is not submitted on a guess",
                      isinstance(refused, A.SkipShipment),
                      type(refused).__name__)

                def unreachable(*args, **kwargs):
                    raise A.AfklNavigationError("057-05765454", [])

                A.get_portal_result = unreachable
                refused = None
                try:
                    A.get_provider_result({"AFKL": page}, hub_row)
                except Exception as error:
                    refused = error
                check("When the carrier cannot be REACHED, the flight card is "
                      "not tried either — it is on the same site",
                      isinstance(refused, A.AfklNavigationError),
                      type(refused).__name__)
            finally:
                A.get_portal_result = real_portal_result

            # ── the switch ───────────────────────────────────────────────
            A.AFKL_FLIGHT_STATUS = False
            check("AFKL_FLIGHT_STATUS=0 turns the whole path off",
                  A.check_afkl_flight_status(page, leg) is None)
            A.AFKL_FLIGHT_STATUS = True

            browser.close()

server.shutdown()

print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(
    len(PASS), len(FAIL),
    ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
