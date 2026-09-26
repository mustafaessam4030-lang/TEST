"""
AFKL navigation: the direct shipment page.

The search form was the original route and it is why 057-05765454 was lost on
2 Sep — the form was filled, the result never rendered where the extractor was
looking, and the retry then died on ERR_HTTP2_PROTOCOL_ERROR against the entry
pages. These tests pin the direct route, the identity check that stops a wrong
page reaching extraction, and the rule that a failed navigation can never be
reported as success.

    python test_afkl_navigation.py                 offline, no browser
    python test_afkl_navigation.py --live          also opens the real page

--live is opt-in because it needs the internet and AFKL's site to be up. It is
the check to run on the machine that will do the work.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_eta as A

PASS, FAIL = [], []
SRC = Path("update_eta.py").read_text(encoding="utf-8")
AWB = "057-05765454"


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


print("=" * 72)
print("1. AIR WAYBILL NORMALISATION")
print("=" * 72)
for raw, expected in [
        ("05705765454", "057-05765454"),
        ("057-05765454", "057-05765454"),
        ("057 0576 5454", "057-05765454"),
        ("  057-05765454  ", "057-05765454"),
        ("057/0576/5454", "057-05765454"),
        ("07499887766", "074-99887766")]:
    url = A.build_afkl_detail_url(raw)
    check("{0!r} -> {1}".format(raw, expected),
          url == "https://www.afklcargo.com/mycargo/shipment/detail/" + expected,
          str(url))

print()
print("=" * 72)
print("2. A REFERENCE THAT IS NOT AN AWB GETS NO URL")
print("=" * 72)
for bad in ["", None, "ABC123", "057-1234", "9451291275", "0570576545412345"]:
    check("{0!r} builds no URL".format(bad),
          A.build_afkl_detail_url(bad) is None,
          str(A.build_afkl_detail_url(bad)))
check("A 10-digit DHL reference is not turned into an AFKL URL",
      A.build_afkl_detail_url("9451291275") is None)
check("The AWB itself is never altered, only its punctuation",
      "05705765454" in re.sub(r"\D", "", A.build_afkl_detail_url(AWB)))

print()
print("=" * 72)
print("3. THE DIRECT PAGE IS THE PRIMARY ROUTE")
print("=" * 72)
check("AFKL is configured to use the detail URL",
      A.PORTALS["AFKL"].get("detail_url") is True)
check("The homepage is no longer an AFKL entry point",
      not any("homepage" in u for u in A.PORTALS["AFKL"]["urls"]),
      str(A.PORTALS["AFKL"]["urls"]))
check("singlesearch survives only as the fallback",
      A.PORTALS["AFKL"]["urls"] == [
          "https://www.afklcargo.com/mycargo/shipment/singlesearch"],
      str(A.PORTALS["AFKL"]["urls"]))
check("The driver tries the detail page before the form",
      SRC.index("open_afkl_detail(page, config, tracking_number)")
      < SRC.index("field = open_portal(page, config, tracking_number)"))
check("A transport error retries the SAME url",
      "retrying the SAME url" in SRC or "the same url" in SRC)
check("Astral is untouched by any of this",
      A.PORTALS["ASTRAL"].get("detail_url") is None
      and A.PORTALS["ASTRAL"]["urls"] == ["https://astral-aviation.com/track-cargo/"])


class Page:
    """Enough of a page for the identity check."""

    def __init__(self, text):
        self.text = text
        self.main_frame = self
        self.frames = [self]

    def locator(self, selector):
        page = self

        class L:
            @property
            def first(self):
                return self

            def inner_text(self, timeout=None):
                return page.text

            def count(self):
                return 1

            def is_visible(self, timeout=None):
                return True
        return L()

    def wait_for_timeout(self, ms):
        pass


REAL = """BRU  JRO
EN ROUTE OK ON SCHEDULE 057-05765454
Checked-in BRU CDG JRO Delivered
03 SEP 08:33 - 4 pieces arrived at CDG from AF0457D
03 SEP 02:01 - 4 pieces departed from BRU on AF0457D
Flight schedule
CDG - JRO AF0877 04 SEP 10:15 - 04 SEP 20:15 4 pieces CONFIRMED
Estimated Pick up time JRO: 05 SEP 02:15
Progress details
JRO ARRIVAL 4 pcs Estimated: 04 SEP 20:15 (AF0877)"""

OTHER_SHIPMENT = REAL.replace("057-05765454", "057-99999999")

print()
print("=" * 72)
print("4. THE PAGE MUST BE THE REQUESTED SHIPMENT'S")
print("=" * 72)
check("The real page is accepted", A.page_is_afkl_detail(Page(REAL), AWB))
check("A DIFFERENT shipment's page is refused",
      not A.page_is_afkl_detail(Page(OTHER_SHIPMENT), AWB))
check("A still-booting shell is refused",
      not A.page_is_afkl_detail(Page("Track and Trace"), AWB))
check("An error page carrying the number is still refused",
      not A.page_is_afkl_detail(
          Page("057-05765454 " + "Sorry, something went wrong. " * 12), AWB))
check("The search form itself is not mistaken for a result",
      not A.page_is_afkl_detail(
          Page("Track and Trace\nTrack a shipment\n057-05765454\nTrack\n"
               "Check flight status\nSelect an origin\n" + "x" * 200), AWB))
check("A dashed or undashed AWB both match",
      A.page_is_afkl_detail(Page(REAL), "05705765454"))

print()
print("=" * 72)
print("5. THE EXISTING EXTRACTION STILL RUNS ON THAT PAGE")
print("=" * 72)
result = A._read_afkl_page(Page(REAL), "AFKL")
check("The page is read", result is not None, str(result))
check("ETA is the arrival at destination",
      result and result["eta"] == "04/09/2026", str(result))
check("No ATA is invented while it is still an estimate",
      result and result["ata"] is None, str(result))
check("Status comes from the page",
      result and "Route" in result["tracking_status"], str(result))

print()
print("=" * 72)
print("6. THE ETA/ATA GUARDS ARE UNTOUCHED")
print("=" * 72)
check("ETA still routes to the COE view", "COE_VIEW," in SRC)
check("ATA still routes to the BU view", 'BU_VIEW,\n                "ATA",' in SRC)
check("ETA candidates still exclude ATA",
      "input[id*='ETA' i]:not([id*='ATA' i]):visible" in SRC)
check("ATA candidates still anchor on the ATA label",
      "[starts-with(normalize-space(),'ATA Date')]" in SRC)
check("A missing field still raises rather than writing elsewhere",
      "field was not found on the Manage page." in SRC)
check("The ETA/ATA distinction still comes from the 'Estimated:' prefix",
      'if re.search(r"Estimated\\s*:", stripped, re.I):' in SRC)

print()
print("=" * 72)
print("7. A FAILED NAVIGATION CAN NEVER BE A SUCCESS")
print("=" * 72)
check("An unreachable page is dumped for evidence",
      'save_page_text(page, tracking_number, "afkl_navigation_error")' in SRC)
# This used to assert the opposite. A quiet False let the caller fall through
# to the search form and report "no result" for a shipment whose page had
# never loaded — which is the bug. It now raises, and the raise is the point.
check("Exhausting the ladder RAISES rather than returning quietly",
      "raise AfklNavigationError(tracking_number, attempts)"
      in SRC.split("def open_afkl_detail")[1].split("\ndef ")[0])
check("...and the caller treats that as a navigation error, not a skip",
      "except AfklNavigationError as error:" in SRC)
check("An unreadable result still ends in SkipShipment",
      "returned no arrival date that could be read" in SRC)
check("A no-result page is still recognised",
      (A._read_afkl_page(
          Page("Track and Trace\nNo results found for this air waybill. "
               "Please check the AWB number." + "x" * 200), "AFKL") or {}
       ).get("no_result") is True)
check("Navigation failures are classified, not swallowed",
      "TRANSIENT_NAV_ERRORS" in SRC and "ERR_HTTP2_PROTOCOL_ERROR" in SRC)
check("The bounded retry count is finite",
      isinstance(A.AFKL_DETAIL_ATTEMPTS, int) and 1 <= A.AFKL_DETAIL_ATTEMPTS <= 5)
check("The readiness wait is bounded",
      isinstance(A.AFKL_DETAIL_READY_MS, int) and A.AFKL_DETAIL_READY_MS <= 60000)

if "--live" in sys.argv:
    print()
    print("=" * 72)
    print("8. LIVE — the real page (needs the internet)")
    print("=" * 72)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            try:
                opened = A.open_afkl_detail(page, A.PORTALS["AFKL"], AWB)
                check("The direct URL opens and is the right shipment", opened)
                if opened:
                    live = A._read_afkl_page(page, "AFKL")
                    check("The existing reader gets a result from it",
                          live is not None, str(live))
                    check("...with at least one date",
                          live and (live.get("eta") or live.get("ata")), str(live))
                    print("     live result: {0}".format(live))
            finally:
                browser.close()
    except Exception as error:
        check("Live check ran", False, str(error)[:160])
else:
    print()
    print("  (skipping the live check — re-run with --live to open the real page)")

print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 72)
sys.exit(1 if FAIL else 0)
