"""
Hub readiness tests.

The one that matters is STALE TABLE: the old fixed sleep could return while the
previous page's rows were still on screen. These assert the new code cannot.

Run:  python test_hub_waits.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_eta as A

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "" if not detail else "  ({0})".format(detail)))


class FakeTable:
    def __init__(self, page):
        self.page = page

    def inner_text(self, timeout=None):
        return self.page.table_text

    def locator(self, _sel):
        page = self.page

        class Rows:
            def count(self):
                return page.row_count
        return Rows()

    def is_visible(self, timeout=None):
        return self.page.table_visible

    def wait_for(self, state=None, timeout=None):
        return True


class FakePage:
    """
    Simulates the hub. `swap_after_ms` is how long the server takes to replace
    the table; until then the OLD content is still on screen.
    """

    def __init__(self, swap_after_ms=0, new_text="PAGE-2 rows", rows_after=25):
        self.table_text = "PAGE-1 rows"
        self.row_count = 20
        self.table_visible = True
        self.swap_after_ms = swap_after_ms
        self.new_text = new_text
        self.rows_after = rows_after
        self.clock = 0
        self.clicked_at = None
        self.polls = 0
        self.form_visible_after_ms = None

    def click(self):
        self.clicked_at = self.clock

    def wait_for_timeout(self, ms):
        self.clock += ms
        time.sleep(ms / 6000.0)          # compressed
        if (self.clicked_at is not None
                and self.clock - self.clicked_at >= self.swap_after_ms):
            self.table_text = self.new_text
            self.row_count = self.rows_after

    def locator(self, selector):
        page = self

        showing = selector

        class L:
            @property
            def first(self):
                return self

            def _present(self):
                if page.form_visible_after_ms is None:
                    return False
                return page.clock >= page.form_visible_after_ms

            def is_visible(self, timeout=None):
                return self._present()

            def count(self):
                # manage_form_ready() now filters with :visible and counts,
                # because .first could be a hidden input on a tabbed page.
                if ":visible" in showing and not self._present():
                    return 0
                return 1 if self._present() else 0
        return L()


def install(page):
    """Point find_shipments_table at the fake, count the polls."""
    def fake_find(p):
        p.polls += 1
        return FakeTable(p)
    A.find_shipments_table = fake_find


A.write_log = lambda m: None

print("=" * 68)
print("1. TABLE SIGNATURE")
print("=" * 68)
page = FakePage(); install(page)
sig1 = A.table_signature(page)
check("Signature is produced", sig1 is not None, sig1)
check("Same table gives the same signature", A.table_signature(page) == sig1)
page.table_text = "PAGE-2 rows"
check("Different content gives a different signature",
      A.table_signature(page) != sig1)


print()
print("=" * 68)
print("2. FAST HUB — must not pay the old fixed 1200ms")
print("=" * 68)
page = FakePage(swap_after_ms=240); install(page)
before = A.table_signature(page)
page.click()
ok = A.wait_for_table_change(page, before, reason="test")
check("Change detected", ok is True)
check("Exited in ~240ms, not 1200ms", page.clock <= 480,
      "{0}ms of simulated wait".format(page.clock))
check("Polling stayed cheap", page.polls < 12, "{0} DOM reads".format(page.polls))


print()
print("=" * 68)
print("3. STALE TABLE — the race the old sleep could lose")
print("=" * 68)
# Hub takes 3s. The old code slept 1300ms then checked visibility, which was
# already true against PAGE-1, so it would have read the WRONG page.
page = FakePage(swap_after_ms=3000); install(page)
before = A.table_signature(page)
page.click()

page.wait_for_timeout(1300)              # what the old code did
stale = A.table_signature(page)
check("Old fixed sleep WOULD have read stale rows", stale == before,
      "still PAGE-1 after 1300ms")
check("...and the table was 'visible' the whole time, so wait_for(visible) "
      "proved nothing", page.table_visible is True)

page2 = FakePage(swap_after_ms=3000); install(page2)
before2 = A.table_signature(page2)
page2.click()
ok = A.wait_for_table_change(page2, before2, reason="test")
check("New code waits for the real change", ok is True)
check("New code returns only once content changed",
      A.table_signature(page2) != before2)
check("New code waited the full ~3s it needed", page2.clock >= 2900,
      "{0}ms".format(page2.clock))


print()
print("=" * 68)
print("4. HUB NEVER RESPONDS — must fall back, not hang")
print("=" * 68)
page = FakePage(swap_after_ms=10 ** 9); install(page)
before = A.table_signature(page)
page.click()
ok = A.wait_for_table_change(page, before, max_ms=2000, reason="test")
check("Returns False rather than hanging", ok is False)
check("Respected the ceiling", 2000 <= page.clock <= 2400, "{0}ms".format(page.clock))
check("Ceiling is far above the old sleep it replaced",
      A.HUB_TABLE_REFRESH_MAX_MS >= 12000,
      "{0}ms".format(A.HUB_TABLE_REFRESH_MAX_MS))


print()
print("=" * 68)
print("5. MANAGE FORM + SAVE")
print("=" * 68)
page = FakePage(); install(page)
page.form_visible_after_ms = 300
got = A.wait_for_any(page, [("form", lambda: A.manage_form_ready(page))],
                     A.HUB_FORM_READY_MAX_MS, reason="test")
check("Manage form detected when it appears", got == "form")
check("Exited near 300ms, not the old 1200ms", page.clock <= 600,
      "{0}ms".format(page.clock))

page = FakePage(); install(page)
page.form_visible_after_ms = None
got = A.wait_for_any(page, [("form", lambda: A.manage_form_ready(page))],
                     1000, reason="test")
check("Missing form times out cleanly", got is None)

calls = {"n": 0}
def save_gone():
    calls["n"] += 1
    return calls["n"] > 3
page = FakePage(); install(page)
got = A.wait_for_any(page, [("save control gone", save_gone)], 3000, reason="test")
check("Save completion detected by condition", got == "save control gone")


print()
print("=" * 68)
print("6. NOTHING ELSE MOVED")
print("=" * 68)
check("TARGET_STATUS unchanged", A.TARGET_STATUS == "Under Clearance")
check("MAX_RECORDS_PER_RUN unchanged", A.MAX_RECORDS_PER_RUN == 200)
check("MAX_TABLE_PAGES unchanged", A.MAX_TABLE_PAGES == 10)
check("Between-shipment courtesy pause preserved",
      A.BETWEEN_SHIPMENTS_MAX_SECONDS >= 1)
check("DHL processing ceiling unchanged", A.DHL_PROCESSING_TIMEOUT_SECONDS == 90)
for name in ["restore_filtered_page", "click_manage_in_view", "save_manage_page",
             "select_shipments_view", "go_to_table_page", "fill_date_field",
             "select_under_clearance_filter", "update_one_view"]:
    check("{0}() still present".format(name), hasattr(A, name))

print()
print("=" * 68)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
for n in FAIL:
    print("  FAILED:", n)
print("=" * 68)
sys.exit(1 if FAIL else 0)
