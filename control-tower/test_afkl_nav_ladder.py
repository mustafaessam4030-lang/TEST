"""
The AFKL navigation ladder.

The rule this pins is the one that cost a shipment: a failure to REACH the
carrier is not a statement about the air waybill. 057-05765454 was reported as
having no result when the truth was that the page had never loaded.

    python test_afkl_nav_ladder.py
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_eta as A                                        # noqa: E402

PASS, FAIL = [], []
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


print("=" * 74)
print("1. A NAVIGATION FAILURE IS NOT 'AWB NOT FOUND'")
print("=" * 74)
check("There is a distinct outcome for it",
      A.AFKL_NAVIGATION_ERROR == "AFKL NAVIGATION ERROR")
check("...separate from NO RESULT", A.AFKL_NAVIGATION_ERROR != A.NO_RESULT)
error = A.AfklNavigationError("057-05765454", [
    {"attempt": 1, "strategy": "existing page",
     "error": "net::ERR_HTTP2_PROTOCOL_ERROR", "outcome": "navigation exception"}])
check("classify_failure returns it, not NO RESULT",
      A.classify_failure(error) == A.AFKL_NAVIGATION_ERROR,
      A.classify_failure(error))
check("The original transport error is preserved in the message",
      "ERR_HTTP2_PROTOCOL_ERROR" in str(error), str(error)[:160])
check("The air waybill is named", "057-05765454" in str(error))
check("It is NOT a SkipShipment, so it cannot be swallowed as a skip",
      not isinstance(error, A.SkipShipment))
check("The run loop handles it as a navigation error",
      "except AfklNavigationError as error:" in SRC
      and 'outcome=AFKL_NAVIGATION_ERROR' in SRC)
check("...and records FAILED rather than pretending it processed",
      'save_result(shipment, dhl_result, "No update",\n                                    "FAILED"' in SRC)

print()
print("=" * 74)
print("2. THE LADDER IS BOUNDED AND ORDERED")
print("=" * 74)
body = SRC.split("def open_afkl_detail")[1].split("\ndef _afkl_side_browser")[0]
check("Attempt 1 is the existing page", '"existing page", 1)' in body)
check("Attempt 2 is a fresh context", '"fresh context", 2)' in body)
check("Attempt 3 disables HTTP/2",
      '"--disable-http2"' in body and "http2_disabled=True" in body)
check("Attempt 4 is the branded Edge channel", 'channel="msedge"' in body)
check("There is no attempt 5", ", 5," not in body and '"5"' not in body)
check("Each strategy runs once — no loop around them",
      "for attempt in range" not in body and "while " not in body)
check("Attempt 3 only runs for transport errors",
      "if transport:" in body)
check("The AWB is never altered between attempts",
      body.count("build_afkl_detail_url(tracking_number)") == 1)
check("The homepage is never used as a workaround",
      "homepage" not in body.lower())
check("singlesearch is not the primary route",
      "singlesearch" not in body)

print()
print("=" * 74)
print("3. EVERY ATTEMPT IS DIAGNOSED")
print("=" * 74)
for field in ("attempt", "strategy", "channel", "http2_disabled", "url",
              "error", "status", "final_url", "dom_content_loaded",
              "loaded", "awb_verified", "elapsed_ms", "outcome"):
    check("The record carries {0!r}".format(field),
          '"{0}"'.format(field) in SRC.split("def _afkl_attempt")[1][:2500])
check("Each attempt is logged as one structured line",
      "AFKL nav | attempt=" in SRC)

print()
print("=" * 74)
print("4. SUCCESS MEANS THE SHIPMENT PAGE, NOT A 200")
print("=" * 74)
check("Identity is confirmed before an attempt counts as successful",
      "page_is_afkl_detail(page, tracking_number)" in SRC)
check("A different shipment's page is refused",
      not A.page_is_afkl_detail(
          type("P", (), {"text": "EN ROUTE 057-99999999 Progress details "
                                 "Flight schedule" + "x" * 200,
                         "main_frame": None, "frames": [],
                         "locator": lambda self, s: type("L", (), {
                             "first": property(lambda s2: s2),
                             "inner_text": lambda s2, timeout=None: s2.owner.text,
                             "count": lambda s2: 1,
                             "is_visible": lambda s2, timeout=None: True})(),
                         "wait_for_timeout": lambda self, ms: None})(),
          "057-05765454"))
check("A loaded-but-unconfirmed page is reported as such, not as success",
      "could not be confirmed on it" in SRC)

print()
print("=" * 74)
print("5. THE URL AND THE PARSER ARE UNTOUCHED")
print("=" * 74)
check("The direct detail URL is still the primary route",
      A.build_afkl_detail_url("05705765454")
      == "https://www.afklcargo.com/mycargo/shipment/detail/057-05765454")
check("Normalisation is unchanged",
      A.build_afkl_detail_url("057-05765454")
      == A.build_afkl_detail_url("05705765454"))
check("The parser still exists", hasattr(A, "_read_afkl_page"))
check("The ETA/ATA rule is untouched",
      'if re.search(r"Estimated\\s*:", stripped, re.I):' in SRC)
check("Side browsers are closed when the run ends",
      "close_afkl_side_browsers()" in SRC
      and SRC.index("close_afkl_side_browsers()\n            browser.close()") > 0)

print()
print("=" * 74)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 74)
sys.exit(1 if FAIL else 0)
