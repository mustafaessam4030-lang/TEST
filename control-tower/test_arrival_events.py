"""
ETA / ATA: shared steps, each carrier adapter on its own, and the 44
shipments of the 12 October 2026 bug - all from sanitized saved pages, with
no live carrier request and no Hub.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")
import arrival_events as E                                   # noqa: E402

FIX = HERE / "fixtures"
OCT12 = date(2026, 10, 12)
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name,
                                  "" if ok else "  ({0})".format(detail)))


def section(title):
    print("\n" + title)


# ---------------------------------------------------------------- dates
section("1. Date validation")
check("Month-name date", E.parse_date("October 12, 2026") == "12/10/2026")
check("DHL heading with weekday and time",
      E.parse_date("Friday, October 23, 2026 at 8:28 PM (UTC)") == "23/10/2026")
check("DHL 'By End of Day'", E.parse_date("October 14, 2026 - By End of Day") == "14/10/2026")
check("dd/mm/yyyy", E.parse_date("05/10/2026") == "05/10/2026")
check("ISO", E.parse_date("2026-10-05") == "05/10/2026")
check("Qatar dd-Mon-yyyy", E.parse_date("26-Sep-2026") == "26/09/2026")
for bad in ("5/10/26", "10/5", "next week", "2026", "13/13/2026", ""):
    check("Ambiguous or invalid refused: {0!r}".format(bad), E.parse_date(bad) is None)

# ------------------------------------------------------- classification
section("2. Classification (DHL wording from the saved pages)")
cases = [
    ("Estimated Carrier Arrival (Destination)", "DGF", None, None, E.EST_ARRIVAL),
    ("Carrier: Arrived", "DGF", None, "Kotoka International Airport", E.ACT_ARRIVAL),
    ("Carrier: Arrived (1st Transit Point)", "DGF", None, "Amsterdam Airport", E.TRANSIT),
    ("Arrived Final Destination", "DGF", None, "Kotoka International Airport", E.ACT_ARRIVAL),
    ("Arrived Final Destination", "DGF", None, "Accra", E.POST_ARRIVAL),
    ("Estimated Delivery", "DGF", None, None, E.EST_DELIVERY),
    ("Shipment Delivered", "DGF", None, "Tarkwa", E.ACT_DELIVERY),
    ("Import Customs Cleared", "DGF", None, "Accra", E.OTHER),
    ("Freight Recovered at", "DGF", None, "Accra", E.POST_ARRIVAL),
    ("Arrived at DHL Sort Facility LAGOS - NIGERIA", "EXPRESS", "LAGOS - NIGERIA", None,
     E.ACT_ARRIVAL),
    ("Arrived at DHL Sort Facility DUBAI - UNITED ARAB EMIRATES", "EXPRESS",
     "LAGOS - NIGERIA", None, E.TRANSIT),
    ("Delivered", "EXPRESS", "LAGOS - NIGERIA", None, E.ACT_DELIVERY),
    ("Something DHL has never printed", "DGF", None, None, E.OTHER),
]
for desc, product, dest, place, want in cases:
    got = E.classify_dhl(desc, product, dest, place)[0]
    check("{0} '{1}'{2} -> {3}".format(product, desc, " @ " + place if place else "", want),
          got == want, got)
check("Delivery label is never an arrival", E.classify_label("Delivered", "ata") == E.ACT_DELIVERY)
check("Estimate label in the ATA field is an estimate",
      E.classify_label("Estimated time of arrival", "ata") == E.EST_ARRIVAL)
check("'Expected Delivery' is a delivery estimate, not an ETA",
      E.classify_label("Expected Delivery", "eta") == E.EST_DELIVERY)

# ------------------------------------------------------------- decide
section("3. Decision rules")


def ev(kind, d, desc="x", code=None):
    return E.event(kind, d, desc, "test", "REF", code=code)


d = E.decide([ev(E.EST_ARRIVAL, "15/10/2026")], today=OCT12)
check("Estimate only -> ETA, no ATA", (d["eta"], d["ata"]) == ("15/10/2026", None))
d = E.decide([ev(E.EST_ARRIVAL, "05/10/2026")], today=OCT12)
check("An EXPIRED estimate is still only an ETA, never an ATA", d["ata"] is None, d)
d = E.decide([ev(E.ACT_DELIVERY, "10/10/2026", "Delivered")], today=OCT12)
check("Delivery without arrival -> review, nothing inferred", d["review"] and d["ata"] is None)
d = E.decide([ev(E.ACT_ARRIVAL, "07/10/2026"), ev(E.EST_ARRIVAL, "07/10/2026"),
              ev(E.ACT_DELIVERY, "09/10/2026")], today=OCT12)
check("Arrived -> ATA only; the estimate is not written", (d["eta"], d["ata"]) ==
      (None, "07/10/2026"), d)
d = E.decide([ev(E.ACT_ARRIVAL, "07/10/2026"), ev(E.ACT_ARRIVAL, "09/10/2026")], today=OCT12)
check("Conflicting actual arrivals -> review", d["review"] and not d["ata"], d)
d = E.decide([ev(E.ACT_ARRIVAL, "07/10/2026", code="EXP:ARRIVED_DESTINATION_COUNTRY"),
              ev(E.ACT_ARRIVAL, "09/10/2026", code="EXP:ARRIVED_DESTINATION_COUNTRY")],
             today=OCT12)
check("Express sort-then-delivery facility: the first arrival", d["ata"] == "07/10/2026", d)
d = E.decide([ev(E.ACT_ARRIVAL, "20/10/2026")], today=OCT12)
check("Actual arrival in the future -> review", d["review"] and not d["ata"])
d = E.decide([ev(E.ACT_ARRIVAL, "10/10/2026"), ev(E.POST_ARRIVAL, "08/10/2026", "Freight Recovered at")],
             today=OCT12)
check("Arrival after a post-arrival step -> review", d["review"] and not d["ata"])
d = E.decide([ev(E.EST_ARRIVAL, "10/10/2026"), ev(E.EST_ARRIVAL, "12/10/2026")], today=OCT12)
check("Two different estimates -> review", d["review"] and not d["eta"])
d = E.decide([E.event(E.ACT_ARRIVAL, None, "Arrived", "t", "REF")], today=OCT12)
check("Undated arrival -> review", d["review"])

# ---------------------------------------------------------- the guard
section("4. Shared guard before every Hub write (all carriers)")
S = {"bol_awb": "K000001", "tracking_reference": "K000001"}
g = E.check_before_write(S, {"eta": "12/10/2026", "ata": "12/10/2026",
                             "ata_source": "Carrier: Arrived"}, today=OCT12)
check("ETA = ATA from one read: review (an estimate may have been read twice)",
      g[2] and g[1] is None, g)
g = E.check_before_write(S, {"ata": "12/10/2026"}, today=OCT12)
check("ATA with no named source: review", g[2] and g[1] is None, g)
g = E.check_before_write(S, {"ata": "12/10/2026", "ata_source": "Estimated Arrival"}, today=OCT12)
check("ATA from an estimate label -> review", g[2] and g[1] is None, g)
g = E.check_before_write(S, {"ata": "10/10/2026", "ata_source": "Delivered"}, today=OCT12)
check("ATA from a delivery label -> review", g[2] and g[1] is None, g)
g = E.check_before_write(S, {"eta": "20/10/2026", "eta_source": "Estimated Delivery"}, today=OCT12)
check("ETA from a delivery estimate is not written", g[0] is None and not g[2], g)
g = E.check_before_write(S, {"ata": "10/10/2026", "reference": "K999999"}, today=OCT12)
check("Another shipment's answer -> review", g[2] and "not K000001" in g[2][0], g)
g = E.check_before_write(S, {"ata": "2026-10-10"}, today=OCT12)
check("Not dd/mm/yyyy -> review", g[2], g)
g = E.check_before_write(S, {"ata": "30/10/2026"}, today=OCT12)
check("ATA in the future -> review", g[2], g)
g = E.check_before_write(S, {"eta": "20/10/2026", "ata": "10/10/2026",
                             "ata_source": "Discharged from vessel"}, today=OCT12)
check("Arrived: the real ATA passes, the ETA is not written",
      g[:2] == (None, "10/10/2026") and not g[2], g)
check("Hub ATA empty: no problem", E.ata_overwrite_problem("", "10/10/2026") is None)
check("Hub ATA same: no problem", E.ata_overwrite_problem("10/10/2026", "10/10/2026") is None)
check("Hub ATA older: not replaced, review",
      E.ata_overwrite_problem("05/10/2026", "10/10/2026") is not None)
check("Hub ATA newer: not replaced, review",
      E.ata_overwrite_problem("15/10/2026", "10/10/2026") is not None)
check("Hub ATA unreadable: review", E.ata_overwrite_problem("3rd Oct", "10/10/2026") is not None)
check("Hub ATA with no digits (empty / input mask) counts as empty",
      E.ata_overwrite_problem("__/__/____", "10/10/2026") is None)
check("Hub ATA '1/10/2026' is the same date as 01/10/2026",
      E.ata_overwrite_problem("1/10/2026", "01/10/2026") is None)
check("Hub ATA with a time part is the same date",
      E.ata_overwrite_problem("12/10/2026 00:00", "12/10/2026") is None)

# ------------------------------------------- the 44 shipments of 12 Oct
section("5. Regression: the 44 shipments of the 12 October run (sanitized)")
manifest = json.loads((FIX / "dhl_oct12" / "manifest.json").read_text(encoding="utf-8"))
check("44 affected shipments in the fixtures", len(manifest) == 44, len(manifest))
false_ata = wrong = 0
for pseudo, m in sorted(manifest.items()):
    text = (FIX / "dhl_oct12" / (pseudo + ".txt")).read_text(encoding="utf-8")
    events, product, _dest = E.dhl_events(text, pseudo)
    d = E.decide(events, today=OCT12)
    ok = (d["eta"], d["ata"], bool(d["review"])) == (m["expected_eta"], m["expected_ata"],
                                                     m["expected_review"])
    acts = {e["date"] for e in events if e["type"] == E.ACT_ARRIVAL}
    sound = (d["ata"] is None or d["ata"] in acts) and not (d["eta"] and d["eta"] == d["ata"])
    if not acts and m["old_ata"] not in (None, "None"):
        false_ata += 1
        sound = sound and d["ata"] is None
    if acts and m["old_ata"] not in acts:
        wrong += 1
    check("{0} ({1}, {2}): old ETA/ATA {3}/{4} -> now {5}/{6}{7}".format(
        pseudo, m["hub_carrier"][:16], product, m["old_eta"], m["old_ata"], d["eta"], d["ata"],
        " review" if d["review"] else ""), ok and sound, d)
check("The old run wrote an ATA on 12 shipments with no arrival event on the page (10 not "
      "arrived, 2 delivered without one); now none", false_ata == 12, false_ata)
check("The old run wrote the wrong arrival date on the others; now the landing date",
      wrong == 30, wrong)

# ----------------------------------------------- adapters, one by one
section("6. Carrier adapters, each on its own saved page (no live request)")
import update_eta as A                                       # noqa: E402
A.write_log = lambda *a, **k: None


class Page(object):
    """A saved page: body text only, and nothing found by get_by_text."""

    def __init__(self, text):
        self.text = text

    def locator(self, _sel):
        page = self

        class L(object):
            def inner_text(self, timeout=None):
                return page.text
        return L()

    def get_by_text(self, *a, **k):
        class N(object):
            def count(self):
                return 0
        return N()


eth = (FIX / "ethiopian_transit_delivered.txt").read_text(encoding="utf-8")
A._page_text = lambda page: page.text
A.PORTALS.setdefault("ETHIOPIAN", {"label": "Ethiopian Airlines"})
r = A._read_generic_portal_page(Page(eth), "ETHIOPIAN")
check("Generic reader (Ethiopian): transit-leg 'DELIVERED' is NOT an ATA -> review",
      r and r.get("ata") is None and r.get("review"), r)
r = A._read_generic_portal_page(Page("Shipment 1\nActual Time of Arrival: 05/10/2026\n" + "x" * 200),
                                "ETHIOPIAN")
check("Generic reader: an explicit actual-arrival label gives the ATA, with its source",
      r and r["ata"] == "05/10/2026" and r.get("ata_source"), r)
r = A._read_generic_portal_page(Page("Estimated Time of Arrival: 10/10/2026 14:00\n"
                                     "Actual Time of Arrival: -\n" + "x" * 200), "ETHIOPIAN")
check("REVIEWER BLOCKER: an estimate printed before an empty actual field is not the ATA",
      r and r["eta"] == "10/10/2026" and not r["ata"], r)
g = E.check_before_write(S, r or {}, today=OCT12)
check("...and only the ETA can reach the Hub", g[:2] == ("10/10/2026", None) and not g[2], g)
r = A._read_generic_portal_page(Page("Menu: Arrived | Delivered | In transit\n"
                                     "Estimated Time of Arrival: 15/10/2026\n" + "x" * 200),
                                "ETHIOPIAN")
check("Arrival words without a date (legends, filters) do not block an ETA",
      r and r["eta"] == "15/10/2026" and not r.get("review"), r)
r = A._read_generic_portal_page(Page("Estimated Delivery 20/10/2026\n" + "x" * 200), "ETHIOPIAN")
check("Generic reader: a delivery estimate is not an ETA", r is None or not r.get("eta"), r)

q = A.extract_qatar_result(Page((FIX / "qatar_arrived_destination.txt").read_text(encoding="utf-8")))
check("Qatar: 'Arrived ( DAR)' on a BRU-DAR route -> ATA with source",
      q and q["ata"] == "26/09/2026" and q.get("ata_source") == "Arrived (DAR)", q)
qt = (FIX / "qatar_in_transit.txt").read_text(encoding="utf-8")
q = A.extract_qatar_result(Page(qt))
check("Qatar in transit: estimate only", q and q["eta"] and not q["ata"], q)
q = A.extract_qatar_result(Page(qt.replace("Estimated Arrival ( JRO )", "Arrived ( DOH)")))
check("Qatar: arrival at a TRANSIT station -> review, no ATA", q and q.get("review") and not q["ata"], q)

ocean = A.validate_arrival_result({"provider": "MSC", "eta": "20/10/2026", "ata": "10/10/2026",
                                   "eta_source": "ETA", "ata_source": "Discharged"}, "MSC", "M1")
g = E.check_before_write({"bol_awb": "M1"}, ocean, today=OCT12)
check("Ocean reader result (labelled 'Discharged') passes the shared guard",
      g[1] == "10/10/2026" and not g[2], g)
aviation = {"provider": "DHL_AVIATION", "ata": "10/10/2026", "ata_source": "RCF at ACC: all 3 pieces"}
g = E.check_before_write({"bol_awb": "615-1"}, aviation, today=OCT12)
check("DHL Aviation result (RCF at destination, all pieces) passes", g[1] == "10/10/2026", g)

dgf = (FIX / "dhl_oct12" / "OCT12-01.txt").read_text(encoding="utf-8")
events, product, dest = E.dhl_events(dgf, "OCT12-01")
check("DHL page reader keeps code, description, date, time, timezone, place, source, reference, "
      "estimated", all(set(("code", "description", "date", "time", "tz", "location", "source",
                            "reference", "estimated")) <= set(e) for e in events) and
      any(e["tz"] for e in events) and product == "DGF", events[:1])

# --------------------------------------------- Hub write boundary
section("7. Hub write boundary: guard, no ATA overwrite, read-back kept")
writes = []
A.update_one_view = lambda page, s, view, field, value, **k: writes.append((field, value)) or \
    "{0} updated with {1} and saved".format(field, value)
try:
    A.update_internal_shipment(None, S, {"ata": "12/10/2026", "ata_source": "Estimated Arrival"})
    check("Estimate-sourced ATA never reaches the Hub", False)
except A.NeedsReview as error:
    check("Estimate-sourced ATA never reaches the Hub", writes == [], (writes, error))
try:
    A.update_internal_shipment(None, S, {"review": ["unclear"], "ata": None, "eta": None})
    check("An adapter's review stops the write", False)
except A.NeedsReview:
    check("An adapter's review stops the write", writes == [])
try:
    A.update_internal_shipment(None, S, {"eta": "12/10/2026", "ata": "12/10/2026",
                                         "ata_source": "Carrier: Arrived"})
    check("ETA = ATA: review, nothing written", False, writes)
except A.NeedsReview:
    check("ETA = ATA: review, nothing written", writes == [], writes)
A.update_internal_shipment(None, S, {"eta": "20/10/2026", "ata": "12/10/2026",
                                     "ata_source": "Carrier: Arrived"})
check("Arrived: only the ATA is written; the ETA is not (no write before the ATA checks)",
      writes == [("ATA", "12/10/2026")], writes)

# update_one_view itself: an existing different ATA stops it before the fill.
import importlib                                             # noqa: E402
B = importlib.reload(A)
B.write_log = lambda *a, **k: None
filled = []
B.click_manage_in_view = lambda *a, **k: 1
B.select_shipment_info_tab = lambda *a, **k: True
B.fill_date_field = lambda page, field, value: filled.append((field, value))
B.take_screenshot = lambda *a, **k: None
B.read_current_date_field = lambda page, field: "05/10/2026"
try:
    B.update_one_view(None, dict(S, table_page=1), "BU", "ATA", "10/10/2026", require_verified=True)
    check("Existing different Hub ATA: not overwritten, review", False, filled)
except B.NeedsReview as error:
    check("Existing different Hub ATA: not overwritten, review", filled == [], error)
B.read_current_date_field = lambda page, field: False
try:
    B.update_one_view(None, dict(S, table_page=1), "BU", "ATA", "10/10/2026", require_verified=True)
    check("Unreadable current Hub ATA: fail closed (a Hub failure, retried as one)", False, filled)
except B.NeedsReview:
    check("Unreadable current Hub ATA: fail closed (a Hub failure, retried as one)", False)
except RuntimeError:
    check("Unreadable current Hub ATA: fail closed (a Hub failure, retried as one)", filled == [])
check("Save -> read-back -> verify sequence still in update_one_view",
      "verify_saved_date(" in __import__("inspect").getsource(B.update_one_view))
check("CMA CGM production hold untouched", "CMA_CGM" in B.carrier_pacing.held_carriers())

# ------------------------------------------- read-only Hub field check
section("8. worker.verify ata-read: read only, field representations")
from worker import verify as W                              # noqa: E402
C = importlib.reload(A)
C.write_log = lambda *a, **k: None
C.click_manage_in_view = lambda *a, **k: 1
C.select_shipment_info_tab = lambda *a, **k: True
for raw, want_empty, want_norm in (("", True, None), ("__/__/____", True, None),
                                   ("12/10/2026", False, "12/10/2026"),
                                   ("1/10/2026", False, "01/10/2026"),
                                   # read-back's normalize_date has no time part: reported as
                                   # None, so a Hub showing times would fail read-back (safe)
                                   ("12/10/2026 00:00", False, None)):
    C.read_current_date_field = lambda page, field, raw=raw: raw
    obs = {"shipment": {"reference": "K000001", "table_page": 1}, "stages": []}
    W._read_fields(None, obs)
    ata = obs["hub_fields"][0]
    rule = ata.get("overwrite_rule") or {}
    check("ATA field {0!r}: read, normalised {1}, empty={2}, same date accepted".format(
        raw, want_norm, want_empty),
        ata["read"] and ata.get("normalized") == want_norm
        and rule.get("empty_field_treated_as_empty") == want_empty
        and (want_empty or rule.get("same_date_is_accepted"))
        and (want_empty or want_norm is None or rule.get("a_different_date_would_go_to_review")),
        ata)
C.read_current_date_field = lambda page, field: C.save_manage_page(page) or "x"
obs = {"shipment": {"reference": "K000001", "table_page": 1}, "stages": []}
W._read_fields(None, obs)
check("ata-read refuses any save attempt and reports it",
      obs["writes_attempted"] == ["save_manage_page", "save_manage_page"], obs["writes_attempted"])
check("...and restores the real fill/save afterwards",
      C.fill_date_field.__name__ == "fill_date_field" and C.save_manage_page.__name__ == "save_manage_page")
C.read_current_date_field = lambda page, field: False
obs = {"shipment": {"reference": "K000001", "table_page": 1}, "stages": []}
W._read_fields(None, obs)
check("Unreadable ATA field: the check is BLOCKED, not passed", obs.get("result") == "BLOCKED", obs)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
