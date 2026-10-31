"""
AFKL myCargo result-page tests, built from the real page for 057-05765454.

That shipment was SKIPPED in the 2 Sep run with "no dates on the page at all"
while its result was on screen the whole time. The cause was not the site and
not the network: every pattern in extract_all_dates required a four-digit year,
and myCargo prints "04 SEP 20:15" and nothing else. These tests pin the page as
it actually renders so that cannot regress.

Run:  python test_afkl_page.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_eta as A

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


# The live page for 057-05765454, BRU -> JRO via CDG, as it renders.
EN_ROUTE = """BRU  JRO
EN ROUTE OK ON SCHEDULE 057-05765454
Checked-in BRU CDG JRO Delivered
02 SEP 09:56 - 4 pieces received at BRU
02 SEP 01:34 - 4 pieces on hand at BRU
Your shipment is estimated to be ready for pick up after: 05 Sep 02:15
On Time: Your shipment has been delivered before LAT
Warehouse address
Cargo Terminal - Kilimanjaro International Airport
Flight schedule
BRU - CDG AF0441M 03 SEP 02:01 - 03 SEP 08:00 4 pieces CONFIRMED
CDG - JRO AF0877 04 SEP 10:15 - 04 SEP 20:15 4 pieces CONFIRMED
Estimated Pick up time JRO: 05 SEP 02:15
Progress details
Station view List view
BRU ACCEPTED 4 pcs 02 SEP 09:56
DEPARTED 4 pcs Estimated: 03 SEP 02:01 (AF0441M)
CDG ARRIVAL 4 pcs Estimated: 03 SEP 08:00 (AF0441M)
DEPARTED 4 pcs Estimated: 04 SEP 10:15 (AF0877)
JRO ARRIVAL 4 pcs Estimated: 04 SEP 20:15 (AF0877)
NOTIFIED 4 pcs Estimated: 05 SEP 02:15"""

ARRIVED = EN_ROUTE.replace("JRO ARRIVAL 4 pcs Estimated: 04 SEP 20:15 (AF0877)",
                           "JRO ARRIVAL 4 pcs 04 SEP 19:42 (AF0877)")


class Page:
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


print("=" * 70)
print("1. THE BUG THAT SKIPPED 057-05765454")
print("=" * 70)
check("A year-less date is invisible to the default parser",
      A.extract_all_dates("JRO ARRIVAL Estimated: 04 SEP 20:15") == [])
check("...and is read when the AFKL reader asks for it",
      any(parsed.startswith("04/09")
          for _, parsed, _ in A.extract_all_dates(
              "JRO ARRIVAL Estimated: 04 SEP 20:15", allow_yearless=True)))
check("'04 SEP 20:15' is 4 September, not 20 September",
      [p for _, p, _ in A.extract_all_dates("04 SEP 20:15", allow_yearless=True)]
      == ["04/09/2026"] or
      [p for _, p, _ in A.extract_all_dates("04 SEP 20:15", allow_yearless=True)][0][:2] == "04",
      str(A.extract_all_dates("04 SEP 20:15", allow_yearless=True)))
check("A full date is never counted twice",
      len(A.extract_all_dates("28 August 2026", allow_yearless=True)) == 1,
      str(A.extract_all_dates("28 August 2026", allow_yearless=True)))

print()
print("=" * 70)
print("2. THE LIVE PAGE")
print("=" * 70)
check("Destination is the last station, not a word from the header",
      A.afkl_destination(EN_ROUTE) == "JRO", A.afkl_destination(EN_ROUTE))

result = A._read_afkl_page(Page(EN_ROUTE), "AFKL")
check("ETA is the arrival at destination", result and result["eta"] == "04/09/2026", str(result))
check("No ATA while every milestone is still an estimate",
      result and result["ata"] is None, str(result))
check("Status is EN ROUTE, not the greyed-out Delivered milestone",
      result and "Route" in result["tracking_status"], str(result))

arrived = A._read_afkl_page(Page(ARRIVED), "AFKL")
check("A bare arrival date becomes the ATA",
      arrived and arrived["ata"] == "04/09/2026", str(arrived))

print()
print("=" * 70)
print("3. PAGES THAT ARE NOT A RESULT")
print("=" * 70)
check("A half-drawn page returns nothing rather than a guess",
      A._read_afkl_page(Page("Track and Trace"), "AFKL") is None)
check("An unknown AWB is recognised",
      (A._read_afkl_page(Page(
          "Track and Trace\nNo results found for this air waybill. "
          "Please check the AWB number." + "x" * 200), "AFKL") or {}
       ).get("no_result") is True)

print()
print("=" * 70)
print("4. THE OLDER LABELLED LAYOUT STILL WORKS")
print("=" * 70)
labelled = ("AIR FRANCE KLM MARTINAIR Cargo\nAir waybill 057-12345678\n"
            "Status  In transit\nOrigin CDG  Destination CAI\n"
            "Estimated Time of Arrival   28 August 2026\n"
            "Flight AF3620   Pieces 4   Weight 512 kg\n")
fallback = A._read_afkl_page(Page(labelled), "AFKL")
check("A labelled layout falls through to the label reader",
      fallback and fallback["eta"] == "28/08/2026", str(fallback))
check("'Estimated Time of Arrival' is never filed as an actual arrival",
      fallback and fallback["ata"] is None, str(fallback))

print()
print("=" * 70)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 70)
sys.exit(1 if FAIL else 0)
