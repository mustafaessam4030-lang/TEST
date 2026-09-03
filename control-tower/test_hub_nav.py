"""
Hub navigation tests.

The dangerous failure is a FALSE SKIP: reusing the page when it is not actually
showing the right view / filter / page would write a date onto the wrong
shipment. Section 2 tries to provoke that from every angle.

Run:  python test_hub_nav.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_eta as A

PASS, FAIL = [], []
HUB = "https://logisticshub.mantracgroup.com/shipments"


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "" if not detail else "  ({0})".format(detail)))


class Loc:
    def __init__(self, count=1, visible=True, text="", value=None, raises=False):
        self._c, self._v, self._t, self._val, self._r = count, visible, text, value, raises

    @property
    def first(self):
        return self

    def count(self):
        if self._r:
            raise RuntimeError("browser gone")
        return self._c

    def is_visible(self, timeout=None):
        if self._r:
            raise RuntimeError("browser gone")
        return self._v

    def inner_text(self, timeout=None):
        if self._r:
            raise RuntimeError("browser gone")
        return self._t

    def evaluate(self, _script):
        if self._r:
            raise RuntimeError("browser gone")
        return self._val

    def locator(self, _sel):
        return Loc(count=20)

    def wait_for(self, **kw):
        return True


class Hub:
    """A hub page whose every observable can be broken independently."""

    def __init__(self, url=HUB, table=True, status="Under Clearance",
                 active_page="1", pagination=True, broken=None):
        self.url = url
        self.table_visible = table
        self.status = status
        self.active_page = active_page
        self.pagination = pagination
        self.broken = broken or set()
        self.restores = []

    def locator(self, selector):
        if "aria-current" in selector or "pagination" in selector:
            if "pagination" in self.broken:
                return Loc(raises=True)
            if not self.pagination:
                return Loc(count=0)
            return Loc(count=1, text=self.active_page)
        if "Status" in selector:
            if "status" in self.broken:
                return Loc(raises=True)
            if self.status is None:
                return Loc(count=0)
            return Loc(count=1, value=self.status)
        return Loc(count=1)

    def bring_to_front(self):
        pass

    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        pass


def install(hub):
    A.find_shipments_table = lambda p: (
        Loc(raises=True) if "table" in hub.broken else Loc(visible=hub.table_visible)
    )
    calls = []

    def fake_restore(page, view_name, page_number):
        calls.append((view_name, page_number))
    A.restore_filtered_page = fake_restore
    return calls


A.write_log = lambda m: None
ORIGINAL_RESTORE = A.restore_filtered_page


def prime(hub, view="BU", page_number=1):
    """Put the module into the 'we just navigated here' state."""
    A._hub_state["view"] = view
    A._hub_state["page"] = page_number
    A._hub_state["url"] = hub.url


print("=" * 70)
print("1. REUSE WORKS WHEN THE PAGE IS GENUINELY CORRECT")
print("=" * 70)
hub = Hub(); calls = install(hub); prime(hub)
A._hub_stats["navigations"] = A._hub_stats["reused"] = 0
check("Verified match reports True", A.hub_state_matches(hub, "BU", 1) is True)
A.ensure_filtered_page(hub, "BU", 1)
check("No navigation performed", calls == [], "{0} restores".format(len(calls)))
check("Reuse counted", A._hub_stats["reused"] == 1)

hub = Hub(active_page="3"); calls = install(hub); prime(hub, "COE", 3)
check("Reuse works on page 3 too", A.hub_state_matches(hub, "COE", 3) is True)


print()
print("=" * 70)
print("2. FALSE SKIP MUST BE IMPOSSIBLE  (the dangerous case)")
print("=" * 70)

cases = [
    ("wrong view requested",      Hub(),                                   "COE", 1),
    ("wrong page requested",      Hub(active_page="1"),                    "BU",  2),
    ("table not visible",         Hub(table=False),                        "BU",  1),
    ("filter is not Under Clearance", Hub(status="All"),                   "BU",  1),
    ("filter unreadable",         Hub(status=None),                        "BU",  1),
    ("navigated off the hub",     Hub(url="https://dhl.com/track"),        "BU",  1),
    ("url changed since we set it", Hub(url=HUB + "?p=9"),                 "BU",  1),
    ("pagination says another page", Hub(active_page="7"),                 "BU",  1),
    ("no pagination but page 2 wanted", Hub(pagination=False),             "BU",  2),
    ("table read throws",         Hub(broken={"table"}),                   "BU",  1),
    ("status read throws",        Hub(broken={"status"}),                  "BU",  1),
    ("pagination read throws",    Hub(broken={"pagination"}),              "BU",  1),
]

for label, hub, view, page_number in cases:
    calls = install(hub)
    A._hub_state["view"] = "BU"
    A._hub_state["page"] = 1
    A._hub_state["url"] = HUB          # deliberately stale/optimistic
    matched = A.hub_state_matches(hub, view, page_number)
    A.ensure_filtered_page(hub, view, page_number)
    check("Refuses to skip: " + label,
          matched is False and len(calls) == 1,
          "matched={0}, restores={1}".format(matched, len(calls)))

hub = Hub(); calls = install(hub)
A.invalidate_hub_state()
check("Cold start never skips", A.hub_state_matches(hub, "BU", 1) is False)

hub = Hub(); calls = install(hub); prime(hub)
A.invalidate_hub_state()
check("invalidate_hub_state() forces a rebuild",
      A.hub_state_matches(hub, "BU", 1) is False)


print()
print("=" * 70)
print("3. NAVIGATION COUNT PER SHIPMENT")
print("=" * 70)

navs = []


def counting_restore(page, view_name, page_number):
    navs.append((view_name, page_number))
    page.url = HUB
    page.active_page = str(page_number)


def simulate(eta, ata, reuse):
    """
    Walk the real call sequence for one shipment.
      click_manage_in_view -> ensure(view)
      ...manage/save invalidate...
      trailing ensure(view) unless suppressed
    """
    del navs[:]
    hub = Hub()
    A.find_shipments_table = lambda p: Loc(visible=True)
    A.restore_filtered_page = counting_restore
    A.invalidate_hub_state()
    if not reuse:
        A.hub_state_matches = lambda *a, **k: False

    # The BEFORE arm always makes the trailing trip; only the AFTER arm can
    # suppress it. Conflating the two made this measure nothing.
    bu_follows = bool(ata)
    suppress_coe_trip = reuse and bu_follows
    for view, value, trailing in (
        ("COE", eta, not suppress_coe_trip),
        ("BU", ata, True),
    ):
        if not value:
            continue
        A.ensure_filtered_page(hub, view, 1)   # click_manage_in_view
        A.invalidate_hub_state()               # opened Manage
        A.invalidate_hub_state()               # saved
        if trailing:
            A.ensure_filtered_page(hub, view, 1)
    return len(navs)


import importlib
real_matches = A.hub_state_matches

before_both = simulate("24/08/2026", "20/08/2026", reuse=False)
A.hub_state_matches = real_matches
after_both = simulate("24/08/2026", "20/08/2026", reuse=True)
A.hub_state_matches = real_matches

check("ETA+ATA: navigations reduced", after_both < before_both,
      "{0} -> {1} per shipment".format(before_both, after_both))
check("ETA+ATA: exactly one dead trip removed", before_both - after_both == 1,
      "saved {0}".format(before_both - after_both))

before_eta = simulate("24/08/2026", None, reuse=False)
A.hub_state_matches = real_matches
after_eta = simulate("24/08/2026", None, reuse=True)
A.hub_state_matches = real_matches
check("ETA only: trailing trip KEPT (known-state invariant)",
      after_eta == before_eta == 2, "{0} vs {1}".format(before_eta, after_eta))


print()
print("=" * 70)
print("4. BUSINESS RULES UNTOUCHED")
print("=" * 70)
A.restore_filtered_page = ORIGINAL_RESTORE
import inspect
src = inspect.getsource(A.update_internal_shipment)
check("Still raises SkipShipment when neither date exists",
      'raise SkipShipment("The carrier did not provide ETA or ATA.")' in src)
check("COE still takes ETA", re.search(r'COE_VIEW,\s*\n\s*"ETA"', src) is not None)
check("BU still takes ATA", re.search(r'BU_VIEW,\s*\n\s*"ATA"', src) is not None)
check("BU trailing trip never suppressed",
      "return_to_table" not in src.split('BU_VIEW')[1][:200])
check("return_to_table defaults to True",
      inspect.signature(A.update_one_view).parameters["return_to_table"].default is True)
check("SOURCE_VIEW unchanged", A.SOURCE_VIEW == "BU")
check("TARGET_STATUS unchanged", A.TARGET_STATUS == "Under Clearance")
check("restore_filtered_page still exists as the fallback",
      callable(A.restore_filtered_page))

print()
print("=" * 70)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
for n in FAIL:
    print("  FAILED:", n)
print("=" * 70)
sys.exit(1 if FAIL else 0)
