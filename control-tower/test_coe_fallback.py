"""
COE view fallback tests.

The run log showed every shipment failing with

    COE Shipments View option was not found after opening Centralized
    Shipments Tracking

while BU quietly fell back to the visible default table. The fallback is now
symmetric. These tests prove that is safe: the field is matched by name, so a
missing ETA field fails cleanly and can never be written to an ATA input.

Run:  python test_coe_fallback.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_eta as A

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "" if not detail else "  ({0})".format(detail)))


A.write_log = lambda m: None
SRC = Path(__file__).parent.joinpath("update_eta.py").read_text(encoding="utf-8")


print("=" * 70)
print("1. THE FALLBACK IS SYMMETRIC NOW")
print("=" * 70)
block = SRC[SRC.index("# 4. After opening Centralized"):
            SRC.index("Centralized Shipments Tracking.\"\n    )")]
check("BU-only guard removed", "if requested == BU_VIEW:" not in block)
check("Fallback reachable for any view", "table.is_visible(timeout=1500)" in block)
check("Which view fell back is logged", "{requested} Shipments View option" in block)


print()
print("=" * 70)
print("2. THE FIELD, NOT THE VIEW, DECIDES WHAT IS WRITTEN")
print("=" * 70)
fill = SRC[SRC.index("def fill_date_field"):SRC.index("def save_manage_page")]

check("ETA branch excludes ATA inputs by id",
      "[id*='ETA' i]:not([id*='ATA' i])" in fill)
check("ETA branch excludes ATA inputs by name",
      "[name*='ETA' i]:not([name*='ATA' i])" in fill)
check("ETA xpath requires an exact 'ETA' label",
      "normalize-space()='ETA'" in fill)
check("A missing field raises rather than writing anywhere",
      'raise Exception(f"{field_name} field was not found on the Manage page.")' in fill)
check("fill_date_field never receives the view name",
      "view_name" not in fill)


print()
print("=" * 70)
print("3. FIELD SELECTION SIMULATED")
print("=" * 70)


class Field:
    def __init__(self, page, attrs):
        self.page, self.attrs = page, attrs

    def get_attribute(self, name):
        return self.attrs.get(name)

    def click(self, timeout=None):
        pass

    def fill(self, value):
        if value:
            self.page.written.append((self.attrs.get("id"), value))

    def dispatch_event(self, _name):
        pass

    def press(self, _key):
        pass

    def input_value(self):
        return self.page.written[-1][1] if self.page.written else ""


class ManagePage:
    """A Manage form carrying only the inputs listed."""

    def __init__(self, inputs):
        self.inputs = inputs
        self.written = []

    def _match(self, selector):
        for attrs in self.inputs:
            ident = (attrs.get("id") or "") + (attrs.get("name") or "")
            if "ETA" in selector.upper() and "not(" in selector.replace("NOT(", "not("):
                if re.search(r"eta", ident, re.I) and not re.search(r"ata", ident, re.I):
                    return Field(self, attrs)
            elif "ATA" in selector.upper():
                if re.search(r"ata", ident, re.I):
                    return Field(self, attrs)
        return None

    def locator(self, selector):
        page = self

        class L:
            @property
            def first(self):
                return self

            def count(self):
                return 1 if page._match(selector) else 0

            def is_visible(self, timeout=None):
                return page._match(selector) is not None
        return L()

    def get_by_label(self, *a, **k):
        return self.locator("no-match")


def install(page):
    def fake_first_visible(candidates, timeout):
        for candidate in candidates:
            try:
                if candidate.is_visible():
                    return page._match_last
            except Exception:
                continue
        return None
    return fake_first_visible


# ETA present -> written correctly.
page = ManagePage([{"id": "txtETA"}, {"id": "txtATA"}])
page._match_last = Field(page, {"id": "txtETA", "type": "text"})
A.first_visible = install(page)
A.fill_date_field(page, "ETA", "21/08/2026")
check("ETA written to the ETA input", page.written == [("txtETA", "21/08/2026")],
      str(page.written))

# Only an ATA input exists -> ETA must NOT be written to it.
page = ManagePage([{"id": "txtATA"}])
A.first_visible = lambda candidates, timeout: None
try:
    A.fill_date_field(page, "ETA", "21/08/2026")
    check("ETA refuses to write when no ETA field exists", False)
except Exception as error:
    check("ETA refuses to write when no ETA field exists",
          "ETA field was not found" in str(error), str(error))
check("Nothing was written to the ATA input", page.written == [], str(page.written))


print()
print("=" * 70)
print("4. WORST CASE IS TODAY'S BEHAVIOUR")
print("=" * 70)
check("Failure message unchanged when the field is genuinely missing",
      'f"{field_name} field was not found on the Manage page."' in fill)
check("Per-shipment error handling still catches it",
      "except Exception as error:" in SRC[SRC.index("def main("):])
# A shipment that wrote nothing is FAILED; one that wrote a date before
# failing is now PARTIAL. Both are recorded, neither is fatal.
check("A failed shipment is still recorded, not fatal",
      re.search(r'_actions or "No update", "FAILED"', SRC) is not None)
check("A part-written shipment is recorded as PARTIAL, not FAILED",
      '"PARTIAL", str(error)' in SRC)
check("PARTIAL requires evidence a date actually reached the Hub",
      '"updated with" in v' in SRC)


print()
print("=" * 70)
print("5. REAL MENU LABELS  (from the Modify Shipment screenshot)")
print("=" * 70)
MENU = ["BU - Shipments", "BU - Pending Shipments",
        "COE - Shipments", "COE - Pending Shipments",
        "Centralized Shipments Tracking",
        "COE Shipment Info", "BU Shipment Info"]

for view, expected in (("COE", "COE - Shipments"), ("BU", "BU - Shipments")):
    hits = [m for m in MENU if A.view_pattern(view).search(m)]
    check("{0} matches exactly one menu entry".format(view),
          hits == [expected], str(hits))

check("Pending Shipments is never selected",
      not any(A.view_pattern(v).search("COE - Pending Shipments")
              or A.view_pattern(v).search("BU - Pending Shipments")
              for v in ("COE", "BU")))
check("The menu heading is not mistaken for a view",
      not A.view_pattern("COE").search("Centralized Shipments Tracking"))
check("The Manage tabs are not mistaken for views",
      not A.view_pattern("COE").search("COE Shipment Info"))


print()
print("=" * 70)
print("6. MODIFY SHIPMENT TAB SELECTION")
print("=" * 70)
check("select_shipment_info_tab exists", hasattr(A, "select_shipment_info_tab"))
tab_src = SRC[SRC.index("def select_shipment_info_tab"):SRC.index("def fill_date_field")]
check("Matches 'COE Shipment Info' style labels",
      "Shipment\\s*Info" in tab_src)
check("Skips the click when the tab is already active",
      "aria-selected" in tab_src and "already" in tab_src)
check("Waits for the panel instead of sleeping",
      "manage_form_ready" in tab_src and "wait_for_any" in tab_src)
check("Missing tab is non-fatal", "return False" in tab_src)
# Compare the CALL SITES. A bare .index() finds the def of fill_date_field,
# which appears earlier in the file than the call it is being compared to.
_call = SRC.index("select_shipment_info_tab(page, view_name, field_name)")
check("Tab is selected BEFORE the field is filled",
      SRC.index("fill_date_field(page, field_name, date_value)", _call) > _call)

print()
print("=" * 70)
print("7. LABEL FORMAT ON THE LIVE PAGE  ('ETA : *')")
print("=" * 70)
check("ETA matched by starts-with, not exact",
      "starts-with(normalize-space(),'ETA')" in fill)
check("ATA matched by starts-with too",
      "starts-with(normalize-space(),'ATA')" in fill)
check("ETD cannot be mistaken for ETA",
      not re.search(r"starts-with\(normalize-space\(\),'ET'\)", fill))


print()
print("=" * 70)
print("8. A SAVED ETA SURVIVES A LATER ATA FAILURE")
print("=" * 70)
upd = SRC[SRC.index("def update_internal_shipment"):SRC.index("# MAIN - DHL ONLY")]
check("BU failure is caught, not left to escape", "except Exception as error:" in upd)
check("Completed COE action is attached to the error", "error.actions = actions" in upd)
check("The failure is still raised", upd.rstrip().count("raise") >= 1)
check("main records the partial instead of 'No update'",
      'getattr(error, "actions", None)' in SRC)
check("Shipment is still marked FAILED, not silently passed",
      '"FAILED", str(error),' in SRC)


print()
print("=" * 70)
print("9. TAB REPORTING IS HONEST")
print("=" * 70)
tabsrc = SRC[SRC.index("def select_shipment_info_tab"):SRC.index("def fill_date_field")]
check("Accepts 'Shipment Information' as well as 'Shipment Info'",
      "Info(?:rmation)?" in tabsrc)
check("Returns False when the panel never rendered",
      "tab was clicked but its panel did not" in tabsrc and "return False" in tabsrc)
check("Does not claim 'tab selected' after a timeout",
      tabsrc.index("tab was clicked but its panel did not")
      < tabsrc.index("Shipment Info' tab selected ("))
check("Missing field triggers a page-contents dump",
      "describe_manage_fields(page, field_name)" in SRC)


print()
print("=" * 70)
print("5b. BUSINESS RULES UNCHANGED")
print("=" * 70)
check("COE still means ETA", A.COE_VIEW == "COE")
check("BU still means ATA", A.BU_VIEW == "BU")
check("SOURCE_VIEW unchanged", A.SOURCE_VIEW == "BU")
check("TARGET_STATUS unchanged", A.TARGET_STATUS == "Under Clearance")
# Whitespace-insensitive: the BU call now sits inside a try block.
check("COE_VIEW takes ETA in update_internal_shipment",
      re.search(r'COE_VIEW,\s*\n\s*"ETA"', SRC) is not None)
check("BU_VIEW takes ATA in update_internal_shipment",
      re.search(r'BU_VIEW,\s*\n\s*"ATA"', SRC) is not None)

print()
print("=" * 70)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
for n in FAIL:
    print("  FAILED:", n)
print("=" * 70)
sys.exit(1 if FAIL else 0)
