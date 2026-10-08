"""
The airlines added from the operator's carrier sheet (Carrier_Data_Request_002,
8 October 2026): every prefix on the sheet is known, and the ones with a
public tracking page are routed to it, set up the way that page was seen to
work when it was opened live.

    python test_airlines.py

Offline. The DHL Aviation reader is checked against the page the operator's
own example waybill (615-62308396) returned, saved in fixtures/.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))

import update_eta as A                                          # noqa: E402

PASS, FAIL = [], []
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


class Page:
    """Enough of a page for the readers: one body of text, no frames."""

    def __init__(self, text):
        self.text = text
        self.main_frame = self
        self.frames = [self]

    def locator(self, selector):
        page = self

        class L:
            first = property(lambda self: self)

            def count(self):
                return 0

            def inner_text(self, timeout=None):
                return page.text

            def is_visible(self, timeout=None):
                return False
        return L()


SHEET = ("098 057 125 071 077 176 074 020 157 065 083 235 932 001 014 055 061 086 "
         "105 217 201 205 239 257 360 524 555 649 693 705 771 817 870 891 988 999 "
         "236 695 997 278 005 160 297 781 784 423 761 991 114 607 736 023 072 250 "
         "058 096 635 131 589 180 229 760 758 045 080 266 724 182 232 537 599 673 "
         "910 214 403 081 070 281 285 512 117 603 618 502 542 016 037 406 670 738 "
         "615 574 459 485").split()
NEW = {"020": "LUFTHANSA", "065": "SAUDIA", "071": "ETHIOPIAN", "077": "EGYPTAIR",
       "083": "SAA", "176": "EMIRATES", "235": "TURKISH", "459": "RWANDAIR",
       "615": "DHL_AVIATION", "932": "VIRGIN"}

print("=" * 72)
print("1. EVERY AIRLINE ON THE SHEET IS KNOWN BY ITS PREFIX")
print("=" * 72)
check("All 94 prefixes on the sheet are in the registry",
      all(p in A.AIRLINES for p in SHEET), [p for p in SHEET if p not in A.AIRLINES])
check("Cathay Pacific (160) is named, not 'unknown prefix'",
      "Cathay Pacific" in A.describe_unsupported("160-12345675", ""))
check("British Airways (125) and Allied Air (574) stay unautomated — no public "
      "tracker could be reached for them",
      A.AIRLINES["125"]["provider"] is None and A.AIRLINES["574"]["provider"] is None)
check("Air France, KLM, Qatar and Astral are unchanged",
      [A.AIRLINES[p]["provider"] for p in ("057", "074", "157", "485")]
      == ["AFKL", "AFKL", "QATAR", "ASTRAL"])

print()
print("=" * 72)
print("2. THE TEN NEW AIRLINES ARE ROUTED TO THEIR OWN TRACKING PAGES")
print("=" * 72)
for prefix, key in sorted(NEW.items()):
    check("{0} → {1}".format(prefix, key),
          A.carrier_provider("whatever the Hub calls it", prefix + "-12345675") == key)
for key in NEW.values():
    config = A.PORTALS[key]
    check("{0}: a tracking address, read only when it carries this waybill, "
          "tab opened on first use".format(key),
          config["urls"] and config["urls"][0].startswith("https://")
          and config["verify_identity"] is True and config["lazy"] is True)
check("DHL K-references still go to DHL, whatever the prefix",
      A.carrier_provider("KLM", "K157123") == "DHL")
check("Idle tabs are not opened at start-up for the new airlines",
      'PORTALS[_portal].get("ocean") or PORTALS[_portal].get("lazy")' in SRC)

print()
print("=" * 72)
print("3. HOW EACH PAGE TAKES THE WAYBILL (as seen live)")
print("=" * 72)
check("Lufthansa, EgyptAir, Turkish and RwandAir take prefix and serial apart",
      all(A.PORTALS[k].get("split_awb") for k in ("LUFTHANSA", "EGYPTAIR", "TURKISH",
                                                  "RWANDAIR")))
check("Turkish adds the serial with Enter before Search is enabled",
      A.PORTALS["TURKISH"]["split_awb"].get("enter") is True)
check("South African opens 'Track Shipments' and submits with Tab, Enter",
      A.PORTALS["SAA"]["tab"] and A.PORTALS["SAA"]["submit_keys"] == ["Tab", "Enter"])
check("DHL Aviation, Virgin and Emirates take the waybill in the address",
      A.PORTALS["DHL_AVIATION"]["deep_link"].endswith("/track/{0}")
      and "values={0}" in A.PORTALS["VIRGIN"]["deep_link"]
      and "values={0}" in A.PORTALS["EMIRATES"]["deep_link"])
check("An air waybill in an address is written the carrier's way (615-62308396); "
      "a bill of lading as the Hub holds it",
      "else portal_awb(tracking_number, config.get(\"dashed\", True)))" in SRC
      and "str(tracking_number).strip() if config.get(\"verbatim\")" in SRC)
check("Virgin and Emirates want the eleven digits without a dash",
      A.portal_awb("932-12345675", A.PORTALS["VIRGIN"]["dashed"]) == "93212345675")
try:
    A.submit_split_awb(None, None, A.PORTALS["LUFTHANSA"], "020-1234567")
    refused = False
except A.SkipShipment:
    refused = True
check("A waybill that is not 11 digits is refused before anything is typed", refused)

print()
print("=" * 72)
print("4. EACH PAGE'S OWN 'NOTHING FOR THIS NUMBER'")
print("=" * 72)
ANSWERS = {
    "ETHIOPIAN": "AWB No : 12345675\nNo cargo tracking details found. Please make sure "
                 "the AWB number is correct.",
    "EGYPTAIR": "AWB DETAILS\nNo tracking information found for 077-12345675",
    "SAA": "Track your shipment\nFollowing AWBs are invalid: 08312345675.",
    "DHL_AVIATION": "The number you are trying to track is either incorrect or unknown.",
    "VIRGIN": "Search Results (0)\nNo matching records found.",
    "EMIRATES": "Search Results (0)\nNo matching records found.",
}
for key, text in ANSWERS.items():
    r = A.extract_portal_result(Page(text), key)
    check("{0}: '{1}…' is a no-result".format(key, text.split("\n")[-1][:38]),
          r and r.get("no_result") is True, str(r))
check("...a sentence is read only for the airline that says it",
      A.extract_portal_result(Page(ANSWERS["SAA"]), "QATAR") is None)

print()
print("=" * 72)
print("5. DHL AVIATION: ATA ONLY WHEN THE WHOLE SHIPMENT IS THERE")
print("=" * 72)
REAL = (HERE / "fixtures" / "dhl_aviation_61562308396.txt").read_text(encoding="utf-8")
r = A.extract_portal_result(Page(REAL), "DHL_AVIATION")
check("615-62308396: arrived, ATA 10/08/2026 — the day the last 8 of 54 pieces "
      "reached Lagos", r and r["ata"] == "10/08/2026" and r["tracking_status"] == "Arrived",
      str(r))
check("...no ETA is made up — the page gives none", r and r["eta"] is None)
check("...and the log can say why", r and r.get("ata_source") == "NFD at LOS: all 54 pieces")
PARTIAL = REAL.replace("NFD\tAwaiting Consignee Collection\t8 pcs\tLOS", "XXX\t8 pcs\tBRU")
r = A.extract_portal_result(Page(PARTIAL), "DHL_AVIATION")
check("Only 46 of 54 pieces at Lagos: no ATA, and it says so",
      r and r["ata"] is None and r["tracking_status"].startswith("Partly arrived (46 of 54"),
      str(r))
r = A.extract_portal_result(Page(REAL.replace("LOS\tHUB", "BRU\tHUB")), "DHL_AVIATION")
check("Nothing at the destination yet: in transit, no dates",
      r and r["ata"] is None and r["eta"] is None and r["tracking_status"] == "In transit",
      str(r))
check("A half-drawn page gives nothing",
      A.extract_portal_result(Page("Tracking DHL ACS Shipments"), "DHL_AVIATION") is None)
check("The page carries the waybill, so the identity check passes",
      A.awb_on_page(Page(REAL), "615-62308396"))

print()
print("=" * 72)
print("6. IDENTITY AND HUMAN CHECKS")
print("=" * 72)
check("Ethiopian prints only the serial, and the serial is what is checked",
      A.PORTALS["ETHIOPIAN"]["identity"] == "serial"
      and A.awb_serial_on_page(Page("AWB No : 12345675"), "071-12345675")
      and not A.awb_serial_on_page(Page("AWB No : 12345676"), "071-12345675"))
for text in ("Performing security verification\nThis website uses a security service "
             "to protect against malicious bots. This page is displayed while the "
             "website verifies you are not a bot.",
             "AWB Number\nSubmit\nCaptcha verification is required. Please use "
             "alternative contact methods.",
             "Before we continue...\nPress & Hold to confirm you are\na human (and not a bot)."):
    check("A human check, handed to a person: '{0}…'".format(text.split("\n")[0][:34]),
          A.captcha_on_page(Page(text)))
check("PerimeterX's press-and-hold box is recognised by its element too",
      "#px-captcha" in A.CAPTCHA_SELECTORS)
check("Nothing here clicks, holds or solves a challenge",
      not re.search(r"px-captcha[^\n]*\.(click|press|hover)\(", SRC))

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
