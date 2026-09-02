"""
DHL data-first readiness tests, driven with tracking number 33 2323 9905.

The decisive test is REGRESSION: a page showing the full Event Log while the
processing banner is still in the DOM. That combination used to report
PROCESSING and refuse extraction.

Run:  python test_dhl_data.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_eta as A

PASS, FAIL = [], []
AWB = "33 2323 9905"


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "" if not detail else "  ({0})".format(detail)))


A.write_log = lambda m: None

# ── Page fixtures shaped like a real DHL Express result ────────────────────
RESULT = """DHL Express
Tracking Results
Waybill Number: 33 2323 9905
Ship date: 18 August 2026
Pieces: 2
Total weight: 41.5 kg
Origin Service Area: DOHA - QATAR
Destination Service Area: CAIRO - EGYPT
Product: EXPRESS WORLDWIDE

Estimated Delivery: 26 August 2026

Event Log
Date          Time     Status Update                        Location
24 August 2026  09:12  Arrived Final Destination            CAIRO - EGYPT
23 August 2026  22:40  Departed Facility                    DOHA - QATAR
20 August 2026  06:05  Processed at DOHA - QATAR            DOHA - QATAR
18 August 2026  14:30  Shipment information received        DOHA - QATAR
"""

BANNER = ("Your request is being processed. Please wait while we retrieve "
          "your shipment details.")

# THE REPORTED BUG: data on screen, banner still in the DOM.
RESULT_WITH_BANNER = BANNER + "\n" + RESULT

ONLY_BANNER = BANNER + "\n" + "x" * 300
SHELL = "DHL Express"
NO_RESULT = ("DHL Express Tracking\nNo results found for the number you "
             "entered. Please check your tracking number." + "x" * 250)
ERROR = "DHL\nService is temporarily unavailable. Error 503" + "x" * 250
COOKIE_ONLY = ("DHL Express\nWe use cookies to improve your experience. "
               "Accept All Cookies\nCookie Settings" + "x" * 250)
DELIVERED = RESULT.replace("Arrived Final Destination", "Delivered") + \
    "\n25 August 2026 11:02 Signed for by: M ABDELRAHMAN  CAIRO - EGYPT"
EXCEPTION = RESULT.replace("Arrived Final Destination", "Customs status updated")
DETAILS_ONLY = """DHL Express
Waybill Number: 33 2323 9905
Ship date: 18 August 2026
Pieces: 2
Origin Service Area: DOHA - QATAR
"""


class Page:
    def __init__(self, text):
        self.text = text
        self.main_frame = self
        self.frames = [self]

    def locator(self, _sel):
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


print("=" * 72)
print("1. REGRESSION — data visible WHILE the processing banner is present")
print("=" * 72)
state, _ = A.detect_dhl_state(Page(RESULT_WITH_BANNER))
check("Reports READY_RESULT, not PROCESSING", state == A.DHL_READY_RESULT,
      "got " + state)
markers = A.dhl_data_markers(RESULT_WITH_BANNER)
check("Data markers found despite the banner", markers["found"] is True)
check("Banner alone (no data) still reports PROCESSING",
      A.detect_dhl_state(Page(ONLY_BANNER))[0] == A.DHL_PROCESSING)
print("       -> this exact combination previously returned PROCESSING")


print()
print("=" * 72)
print("2. READINESS SIGNALS  (requirement 8)")
print("=" * 72)
for label, text, key in [
    ("full result page", RESULT, None),
    ("shipment details only", DETAILS_ONLY, "details"),
    ("timeline present", RESULT, "timeline"),
    ("dated event present", RESULT, "dated_event"),
    ("estimated delivery present", RESULT, "delivery_date"),
]:
    m = A.dhl_data_markers(text)
    ok = m["found"] if key is None else m[key]
    check("Ready via " + label, bool(ok))

check("Blank shell is NOT ready", A.dhl_data_markers(SHELL)["found"] is False)
check("Cookie-only page is NOT ready",
      A.dhl_data_markers(COOKIE_ONLY)["found"] is False)
check("Banner-only page is NOT ready",
      A.dhl_data_markers(ONLY_BANNER)["found"] is False)


print()
print("=" * 72)
print("3. DATE EXTRACTION for {0}".format(AWB))
print("=" * 72)
m = A.dhl_data_markers(RESULT)
label, value = m["delivery_date_value"]
check("Estimated Delivery date read from the page", value == "26/08/2026",
      "{0} -> {1}".format(label, value))
check("Read via the label, not the first date on the page", label == "Estimated Delivery")
check("All dated events found", m["date_count"] >= 5, "{0} dates".format(m["date_count"]))

latest_date, latest_line = A.dhl_latest_event(RESULT)
check("Latest dated EVENT identified (not the forecast)",
      latest_date == "24/08/2026",
      "{0} | {1}".format(latest_date, (latest_line or "")[:52]))
check("Estimated Delivery excluded from latest event",
      "Estimated" not in (latest_line or ""))

check("No date invented on a page without one",
      A.dhl_data_markers(SHELL)["delivery_date_value"] is None)
check("No latest event invented", A.dhl_latest_event(SHELL) == (None, None))
check("Output format is the automation's dd/mm/yyyy",
      A.normalize_date("26 August 2026") == "26/08/2026")


print()
print("=" * 72)
print("4. SHIPMENT STATUS from real event names  (requirement 7)")
print("=" * 72)
for label, text, expected in [
    ("arrived final destination", RESULT, "ARRIVED"),
    ("delivered + signed for", DELIVERED, "DELIVERED"),
    ("customs status updated", EXCEPTION, "EXCEPTION"),
    ("information received only", DETAILS_ONLY, None),
]:
    got = A.dhl_shipment_status(text)
    check("{0} -> {1}".format(label, expected), got == expected, "got {0}".format(got))

check("No events yet is distinguishable from still loading",
      A.dhl_shipment_status(DETAILS_ONLY) is None
      and A.dhl_data_markers(DETAILS_ONLY)["found"] is True)


print()
print("=" * 72)
print("5. TERMINAL STATES STILL WORK")
print("=" * 72)
for label, text, expected in [
    ("unknown tracking number", NO_RESULT, A.DHL_READY_NO_RESULT),
    ("503 error page", ERROR, A.DHL_ERROR),
    ("cookie overlay only", COOKIE_ONLY, A.DHL_COOKIE),
    ("empty shell", SHELL, A.DHL_LOADING),
]:
    got, _ = A.detect_dhl_state(Page(text))
    check("{0} -> {1}".format(label, expected), got == expected, "got " + got)


print()
print("=" * 72)
print("6. COOKIE HANDLING IS BOUNDED  (requirement 2)")
print("=" * 72)
check("Cookie retries capped", A.DHL_COOKIE_MAX_ATTEMPTS <= 3,
      "{0} attempts".format(A.DHL_COOKIE_MAX_ATTEMPTS))
check("Cookie window is short, not the whole budget",
      A.DHL_COOKIE_WINDOW_SECONDS <= 15,
      "{0}s of a {1}s ceiling".format(A.DHL_COOKIE_WINDOW_SECONDS,
                                      A.DHL_READY_MAX_SECONDS))
src = Path(__file__).parent.joinpath("update_eta.py").read_text(encoding="utf-8")
check("Consent is attempted before the readiness wait",
      src.index('accept_cookie_banner(page, "DHL")')
      < src.index("state, elapsed = wait_for_dhl_page(page, tracking_number)"))


print()
print("=" * 72)
print("7. RE-SEARCH ONLY ON STRONG EVIDENCE  (requirement 10)")
print("=" * 72)
check("Re-search is guarded by a data check",
      "Not re-searching" in src and "current_markers" in src)
check("Guard sits before the Track/Search click",
      src.index("Not re-searching")
      < src.index('write_log("Polling window elapsed. Clicking DHL Track/Search again...")'))


print()
print("=" * 72)
print("7b. STALE-PAGE GUARD  (data must belong to THIS waybill)")
print("=" * 72)
state, _ = A.detect_dhl_state(Page(RESULT_WITH_BANNER), tracking_number=AWB)
check("Our own waybill is accepted", state == A.DHL_READY_RESULT, "got " + state)

other = RESULT_WITH_BANNER.replace("33 2323 9905", "44 1111 2222")
state, _ = A.detect_dhl_state(Page(other), tracking_number=AWB)
check("A different waybill is refused", state != A.DHL_READY_RESULT, "got " + state)

check("Waybill match tolerates spacing differences",
      A.page_shows_tracking_number("Waybill Number: 3323239905", AWB) is True)
check("Page echoing no waybill is not called stale",
      A.page_shows_tracking_number("Event Log\n24 August 2026 Arrived", AWB) is True)

print()
print("=" * 72)
print("8. EXISTING PARSERS UNTOUCHED")
print("=" * 72)
import inspect
for fn in ["extract_all_dates", "normalize_date", "extract_date_near_labels",
           "extract_event_log_rows_from_scope", "click_event_log"]:
    check("{0}() still present".format(fn), hasattr(A, fn))
check("extract_event_log_result still returns ETA/ATA keys",
      "eta_dates" in inspect.getsource(A.extract_event_log_result))
check("No new date parser was introduced",
      inspect.getsource(A.dhl_latest_event).count("extract_all_dates") >= 1)


print()
print("=" * 72)
print("EXTRACTED FOR {0}".format(AWB))
print("=" * 72)
m = A.dhl_data_markers(RESULT)
print("  shipment details found : {0}".format(m["details"]))
print("  timeline found         : {0}".format(m["timeline"]))
print("  dated events found     : {0}".format(m["date_count"]))
print("  estimated delivery     : {0} = {1}".format(*m["delivery_date_value"]))
print("  latest dated event     : {0}".format(A.dhl_latest_event(RESULT)[1]))
print("  shipment status        : {0}".format(A.dhl_shipment_status(RESULT)))

print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
for n in FAIL:
    print("  FAILED:", n)
print("=" * 72)
sys.exit(1 if FAIL else 0)
