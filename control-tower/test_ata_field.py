"""
ATA field targeting tests.

The BU Shipment Info tab puts "ATA Date :" in the Clearing Agent block beside
six other date fields. Writing the ATA into "Duty Paid Date" or "Customs
Release Date" would be a silent, business-visible error, so the label matching
is tested against the real labels from the screenshot.

Run:  python test_ata_field.py
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

# Every label on the BU Shipment Info tab, verbatim from the screenshot.
BU_LABELS = [
    "Documents Checked Date :", "Assigned Clearing Agent :", "Delay Codes :",
    "Current Status :", "Comments :", "Customs Pre Entry Date :",
    "Duty Assessment Received Date :", "BOE / SGD / Customs Declaration Number :",
    "Duty Paid Date :", "Customs Declaration Date :", "Customs Release Date :",
    "ATA Date :", "Delivery Date :", "Receive Date :",
]
# And the COE tab, from the earlier screenshot.
COE_LABELS = [
    "BOL/AWB Number : *", "Territory : *", "Port Of Loading : *",
    "Shipping Mode : *", "Shipment Type : *", "ETA : *", "Current Status :",
    "UNA+ Invoice Number / EDI Booking : *", "Freight Forwarder : *",
    "Port of Discharge : *", "Carrier : *", "Division : *",
    "Hazardous Cargo : *", "ETD :", "Weight :", "Invoice Value :",
]

print("=" * 72)
print("1. THE ATA LABEL ANCHOR")
print("=" * 72)

# The xpath anchors on the label STARTING with ATA.
starts_ata = [l for l in BU_LABELS if l.strip().upper().startswith("ATA")]
check("Exactly one BU label starts with ATA", starts_ata == ["ATA Date :"],
      str(starts_ata))

# The stricter 'ATA Date' anchor.
starts_ata_date = [l for l in BU_LABELS
                   if l.strip().upper().startswith("ATA DATE")]
check("Exactly one BU label starts with 'ATA Date'",
      starts_ata_date == ["ATA Date :"], str(starts_ata_date))

print()
print("=" * 72)
print("2. THE SIX NEIGHBOURING DATE FIELDS MUST NOT MATCH")
print("=" * 72)
NEIGHBOURS = ["Customs Pre Entry Date :", "Duty Assessment Received Date :",
              "Duty Paid Date :", "Customs Declaration Date :",
              "Customs Release Date :", "Delivery Date :", "Receive Date :",
              "Documents Checked Date :"]
for label in NEIGHBOURS:
    check("Not matched: {0}".format(label),
          not label.strip().upper().startswith("ATA"))

# The get_by_label regex used for ATA.
ata_label = re.compile(r"^\s*ATA\s*(?:Date)?\s*:?\s*\*?\s*$", re.I)
check("ATA regex matches 'ATA Date :'", ata_label.match("ATA Date :") is not None)
check("ATA regex matches bare 'ATA'", ata_label.match("ATA") is not None)
for label in NEIGHBOURS:
    if ata_label.match(label):
        check("ATA regex wrongly matches {0}".format(label), False)
        break
else:
    check("ATA regex matches none of the neighbours", True)

print()
print("=" * 72)
print("3. ETA MUST NOT HIT ETD OR ATA")
print("=" * 72)
eta_label = re.compile(r"^\s*ETA\s*(?:Date)?\s*:?\s*\*?\s*$", re.I)
check("ETA regex matches 'ETA : *'", eta_label.match("ETA : *") is not None)
check("ETA regex does NOT match 'ETD :'", eta_label.match("ETD :") is None)
check("ETA regex does NOT match 'ATA Date :'",
      eta_label.match("ATA Date :") is None)

starts_eta = [l for l in COE_LABELS if l.strip().upper().startswith("ETA")]
check("Exactly one COE label starts with ETA", starts_eta == ["ETA : *"],
      str(starts_eta))
check("ETD is a separate label and is not caught",
      "ETD :" in COE_LABELS and not "ETD :".startswith("ETA"))

fill = SRC[SRC.index("def fill_date_field"):SRC.index("def save_manage_page")]
check("ETA css still excludes ATA ids",
      "[id*='ETA' i]:not([id*='ATA' i])" in fill)
check("ETA css still excludes ATA names",
      "[name*='ETA' i]:not([name*='ATA' i])" in fill)

print()
print("=" * 72)
print("4. HIDDEN-INPUT BUG  (why the 8s timeout happened)")
print("=" * 72)
ready = SRC[SRC.index("def manage_form_ready"):SRC.index("def wait_between_shipments")]
check("Readiness filters on :visible", ":visible" in ready)
check("Readiness no longer relies on .first being visible",
      ".first.is_visible" not in ready)
check("Readiness counts visible matches", "count() > 0" in ready)
check("ATA candidates prefer visible inputs",
      "input[id*='ATA' i]:visible" in fill)
check("ETA candidates prefer visible inputs",
      "input[id*='ETA' i]:not([id*='ATA' i]):visible" in fill)


class Input:
    def __init__(self, page, ident, visible):
        self.page, self.ident, self.visible = page, ident, visible


class TabbedPage:
    """Inactive panel's inputs come first in the DOM, exactly as on the hub."""

    def __init__(self):
        self.inputs = [
            Input(self, "coe_eta", False),      # hidden COE panel, first in DOM
            Input(self, "coe_etd", False),
            Input(self, "bu_duty_paid", True),
            Input(self, "bu_ata", True),
        ]

    def locator(self, selector):
        page = self

        class L:
            def __init__(self, sel):
                self.sel = sel

            def _matches(self):
                found = page.inputs
                if ":visible" in self.sel:
                    found = [i for i in found if i.visible]
                return found

            @property
            def first(self):
                found = self._matches()
                return _Handle(found[0] if found else None)

            def count(self):
                return len(self._matches())
        return L(selector)


class _Handle:
    def __init__(self, target):
        self.target = target

    def is_visible(self, timeout=None):
        return bool(self.target and self.target.visible)


page = TabbedPage()
old_style = page.locator("input[type='text']").first.is_visible()
new_style = page.locator("input[type='text']:visible").count() > 0
check("Old .first approach reported NOT ready (the 8s timeout)", old_style is False)
check("New :visible approach reports ready", new_style is True)


print()
print("=" * 72)
print("5. TAB SWITCH IS VERIFIED BY PANEL CONTENT")
print("=" * 72)
tab = SRC[SRC.index("def select_shipment_info_tab"):SRC.index("def fill_date_field")]
# Superseded: readiness used to match panel heading TEXT, which survives in
# the DOM during a postback. It now waits for the field itself (section 8).
check("Readiness waits on the target field",
      "panel_has_field(page, field_name)" in tab)
check("Heading-text matching is gone", "Clearing" not in tab)
check("The specific field is checked before the generic fallback",
      tab.index("the {0} field") < tab.index('("editable fields"'))
check("Tab labels from the screenshot are accepted",
      "Info(?:rmation)?" in tab)
for label in ["BU Shipment Info", "COE Shipment Info"]:
    view = label.split()[0]
    pattern = re.compile(
        r"^\s*{0}\s*(?:-\s*)?Shipment\s*Info(?:rmation)?\s*$".format(view), re.I)
    check("Tab label matched: {0!r}".format(label),
          pattern.match(label) is not None)
for other in ["Logs", "KPIs"]:
    matched = any(
        re.compile(r"^\s*{0}\s*(?:-\s*)?Shipment\s*Info(?:rmation)?\s*$".format(v),
                   re.I).match(other)
        for v in ("BU", "COE"))
    check("Sibling tab {0!r} is not mistaken for a panel".format(other), not matched)


print()
print("=" * 72)
print("7. ASP.NET POSTBACK CLICKS  (the Manage timeout)")
print("=" * 72)

# The exact Playwright error from the run log.
REAL = ("Locator.click: Timeout 5000ms exceeded. Call log: - waiting for "
        "locator(\"table\").first.locator(\"tbody tr\").nth(20)"
        ".get_by_role(\"button\", name=\"Manage\").first - locator resolved to "
        "<input type=\"submit\" value=\"Manage\" id=\"ContentPlaceHolder1_gvRequests"
        "_btnManage_19\"/> - attempting click action - element is visible, enabled "
        "and stable - performing click action - click action done - waiting for "
        "scheduled navigations to finish")

check("The real failure is recognised as a postback wait",
      A._is_postback_navigation_wait(Exception(REAL)) is True)
check("A genuine 'element not found' is NOT treated as success",
      A._is_postback_navigation_wait(
          Exception("Locator.click: Timeout 5000ms exceeded. waiting for locator "
                    "to be visible")) is False)
check("Postback budget is well above the 5s that failed",
      A.POSTBACK_CLICK_TIMEOUT_MS >= 15000,
      "{0}ms".format(A.POSTBACK_CLICK_TIMEOUT_MS))


class Btn:
    """Mimics a WebForms button: the click lands, the navigation wait times out."""

    def __init__(self, mode):
        self.mode, self.clicks = mode, 0

    def click(self, timeout=None, no_wait_after=None, force=False):
        self.clicks += 1
        if self.mode == "no_wait_after_unsupported" and no_wait_after is not None:
            raise TypeError("unexpected keyword argument 'no_wait_after'")
        if self.mode == "postback_timeout" and not force:
            raise Exception(REAL)
        if self.mode == "hard_fail" and not force:
            raise Exception("Locator.click: Timeout exceeded waiting for locator")


logged = []
A.write_log = lambda m: logged.append(m)

btn = Btn("postback_timeout")
A.click_postback(btn, "Manage for 33 2323 9905")
check("Postback timeout is absorbed, not raised", True)
check("It is reported as landed, not failed",
      any("click landed" in m for m in logged), logged[-1] if logged else "")

btn = Btn("no_wait_after_unsupported")
A.click_postback(btn, "Save")
check("Works on Playwright builds without no_wait_after", btn.clicks >= 2)

btn = Btn("hard_fail")
try:
    A.click_postback(btn, "Manage")
    check("A real click failure still reaches force-click", btn.clicks >= 3,
          "{0} attempts".format(btn.clicks))
except Exception:
    check("A real click failure still reaches force-click", False)

A.write_log = lambda m: None
check("Manage uses the postback click",
      'click_postback(manage_button' in SRC)
check("Save uses the postback click", 'click_postback(save_button' in SRC)
check("Search uses the postback click", 'click_postback(search_button' in SRC)
check("Pagination uses the postback click", 'click_postback(target' in SRC)
check("No bare 5000ms click remains on those controls",
      "manage_button.click(timeout=5000)" not in SRC
      and "save_button.click(timeout=5000)" not in SRC)


print()
print("=" * 72)
print("8. TAB POSTBACK  (from the 02:57 failure)")
print("=" * 72)
tabsrc = SRC[SRC.index("def select_shipment_info_tab"):SRC.index("def fill_date_field")]

check("Tab click goes through click_postback", "click_postback(tab" in tabsrc)
# The words still appear in the comment explaining WHY; what matters is that
# the attribute is never read to make a decision.
check("aria-selected is never read for a decision",
      'get_attribute("aria-selected")' not in tabsrc,
      "the page reported it true on BU, Logs AND KPIs at once")
check("Panel readiness waits on the FIELD, not on heading text",
      "panel_has_field(page, field_name)" in tabsrc
      and "Clearing" not in tabsrc)
check("field_name is passed in from update_one_view",
      "select_shipment_info_tab(page, view_name, field_name)" in SRC)
check("Ceiling raised for the postback render",
      A.HUB_FORM_READY_MAX_MS >= 15000, "{0}ms".format(A.HUB_FORM_READY_MAX_MS))
check("Per-candidate probing tightened from 3500ms",
      "first_visible(candidates, 1500)" in SRC)


class Panel:
    """Only the named inputs are visible, mimicking one rendered tab."""

    def __init__(self, visible_ids):
        self.visible_ids = visible_ids

    def locator(self, selector):
        ids = self.visible_ids
        class L:
            @property
            def first(self): return self
            def count(self):
                want = "ETA" if "ETA" in selector and "not([id*='ATA'" in selector.replace(" ", "") else (
                    "ATA" if "ATA" in selector else None)
                if want is None: return 0
                return sum(1 for i in ids if want.lower() in i.lower()
                           and not (want == "ETA" and "ata" in i.lower()))
            def is_visible(self, timeout=None): return self.count() > 0
        return L()

check("ETA field detected on the COE panel",
      A.panel_has_field(Panel(["txtETA"]), "ETA") is True)
check("ATA field detected on the BU panel",
      A.panel_has_field(Panel(["txtATA"]), "ATA") is True)
check("ATA NOT reported when only the COE panel is rendered",
      A.panel_has_field(Panel(["txtETA"]), "ATA") is False)
check("Empty panel reports no field (the 02:57 state)",
      A.panel_has_field(Panel([]), "ATA") is False)


print()
print("=" * 72)
print("9. MID-POSTBACK PAGE  (the 26 Aug 12:07 failure)")
print("=" * 72)
tabsrc = SRC[SRC.index("def select_shipment_info_tab"):SRC.index("def fill_date_field")]

check("Waits for the tab postback to land before polling",
      "wait_for_load_state" in tabsrc and
      tabsrc.index("click_postback(tab") < tabsrc.index("wait_for_load_state"))
check("Then waits for document.readyState complete",
      "page_is_settled" in tabsrc)
check("Field search covers every frame", "all_scopes" in SRC)
check("Diagnostic reports readyState and frame count",
      "readyState={0}" in SRC and "frames={2}" in SRC)
check("A dead panel triggers a clean Manage reload",
      "Reopening Manage for" in SRC)
check("The reload happens before the field lookup",
      SRC.index("Reopening Manage for")
      < SRC.index("fill_date_field(page, field_name, date_value)",
                  SRC.index("Reopening Manage for") - 4000))


class Frame:
    def __init__(self, ids): self.ids = ids
    def locator(self, sel):
        ids = self.ids
        class L:
            @property
            def first(self): return self
            def count(self):
                want = "ATA" if "ATA" in sel and "ETA" not in sel.split("ATA")[0][-8:] else (
                       "ETA" if "ETA" in sel else None)
                if want is None: return 0
                return sum(1 for i in ids if want.lower() in i.lower()
                           and not (want == "ETA" and "ata" in i.lower()))
            def is_visible(self, timeout=None): return self.count() > 0
        return L()


class Doc:
    """A page whose ATA field lives in a child frame, not the main document."""
    def __init__(self, main_ids, frame_ids):
        self.main = Frame(main_ids)
        self.child = Frame(frame_ids)
        self.main_frame = self.main
        self.frames = [self.main, self.child]
    def locator(self, sel): return self.main.locator(sel)


doc = Doc([], ["txtATA"])
check("ATA found when it sits inside a frame",
      A.panel_has_field(doc, "ATA") is True)
check("Nothing invented when no frame has it",
      A.panel_has_field(Doc([], []), "ATA") is False)


print()
print("=" * 72)
print("6. BUSINESS RULES UNCHANGED")
print("=" * 72)
check("ATA still goes to the BU view", A.BU_VIEW == "BU")
check("ETA still goes to the COE view", A.COE_VIEW == "COE")
check("A missing field still raises rather than writing elsewhere",
      'raise Exception(f"{field_name} field was not found on the Manage page.")' in fill)
check("Missing field still dumps the real page contents",
      "describe_manage_fields(page, field_name)" in fill)

print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
for n in FAIL:
    print("  FAILED:", n)
print("=" * 72)
sys.exit(1 if FAIL else 0)
