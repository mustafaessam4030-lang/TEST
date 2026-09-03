"""
Automation tests. No browser, no network — a fake Playwright page that serves
real DHL page copy so the state machine can be driven through every branch.

Run:  python test_automation.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import update_eta as A

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "" if not detail else "  ({0})".format(detail)))


# ══════════════════════════════════════════════════════════════
# FAKE PLAYWRIGHT
# ══════════════════════════════════════════════════════════════

class FakeLocator:
    def __init__(self, hits=0, visible=True, text=""):
        self._hits, self._visible, self._text = hits, visible, text
        self.clicked = False

    def __getattr__(self, _name):
        return lambda *a, **k: self

    @property
    def first(self):
        return self

    def count(self):
        return self._hits

    def is_visible(self, timeout=None):
        return self._hits > 0 and self._visible

    def click(self, timeout=None, force=False):
        if self._hits == 0:
            raise Exception("no element")
        self.clicked = True

    def inner_text(self, timeout=None):
        return self._text


class FakePage:
    """
    Serves a scripted sequence of body texts, one per poll. The last entry
    repeats forever. `cookie_selector` is the one selector that "exists".
    """

    def __init__(self, texts, cookie_selector=None):
        self.texts = texts if isinstance(texts, list) else [texts]
        self.cookie_selector = cookie_selector
        self.reads = 0
        self.slept_ms = 0
        self.cookie_clicks = 0
        self.selector_probes = 0
        self.main_frame = self
        self.frames = [self]

    def _current(self):
        index = min(self.reads, len(self.texts) - 1)
        return self.texts[index]

    def locator(self, selector):
        if selector == "body":
            text = self._current()
            self.reads += 1
            return FakeLocator(hits=1, text=text)
        self.selector_probes += 1
        # Real CSS accepts a comma-joined list; the fake must too.
        candidates = [part.strip() for part in selector.split(",")]
        if self.cookie_selector and self.cookie_selector in candidates:
            page = self

            class CookieLocator(FakeLocator):
                def click(inner, timeout=None, force=False):
                    page.cookie_clicks += 1
                    page.cookie_selector = None      # banner goes away
                    page.texts = [t.replace("Accept All Cookies", "")
                                  for t in page.texts]
            return CookieLocator(hits=1)
        return FakeLocator(hits=0)

    def get_by_role(self, role, name=None, exact=False):
        return FakeLocator(hits=0)

    def get_by_text(self, pattern, exact=False):
        return FakeLocator(hits=0)

    def wait_for_timeout(self, ms):
        self.slept_ms += ms
        time.sleep(ms / 4000.0)          # compressed so tests stay quick


# Real DHL page copy.
LOADING = "Loading"
PROCESSING = ("DHL Express Tracking\nYour request is being processed. "
              "Please wait while we retrieve your shipment details. " + "x" * 300)
RESULT = ("DHL Express Tracking 5271993480\nShipment Details\nEvent Log\n"
          "Time  Status Update  Location\n"
          "26/08/2026  Estimated Delivery  CAIRO - EGYPT\n"
          "20/08/2026  Arrived Final Destination  CAIRO - EGYPT\n" + "x" * 200)
NO_RESULT = ("DHL Express Tracking\nNo results found for the number you entered. "
             "Please check your tracking number and try again." + "x" * 250)
ERROR = ("DHL\nService is temporarily unavailable. "
         "Please try again later. Error 503" + "x" * 250)
COOKIE = ("DHL Express Tracking\nWe use cookies to improve your experience. "
          "Accept All Cookies\nCookie Settings" + "x" * 250)

A.write_log = lambda message: None       # silence the run log during tests


print("=" * 68)
print("1. DHL STATE DETECTION")
print("=" * 68)
for label, text, expected in [
    ("Event Log rendered", RESULT, A.DHL_READY_RESULT),
    ("Akamai processing screen", PROCESSING, A.DHL_PROCESSING),
    ("Unknown tracking number", NO_RESULT, A.DHL_READY_NO_RESULT),
    ("503 error page", ERROR, A.DHL_ERROR),
    ("Cookie banner only", COOKIE, A.DHL_COOKIE),
    ("Blank shell", LOADING, A.DHL_LOADING),
]:
    state, _ = A.detect_dhl_state(FakePage(text))
    check(label + " -> " + expected, state == expected, "got " + state)

# Data now outranks the banner: a page showing THIS shipment's Event Log is
# ready even if the interstitial text is still in the DOM. That was the bug
# behind "DHL shows data while the automation says PROCESSING".
state, _ = A.detect_dhl_state(FakePage(PROCESSING + RESULT),
                              tracking_number="5271993480")
check("Data outranks the processing banner", state == A.DHL_READY_RESULT,
      "got " + state)

# The old concern was still valid, so it is now handled by identity rather
# than precedence: a DIFFERENT shipment's page must not be accepted.
stale = PROCESSING + RESULT.replace("5271993480", "9999999999")
state, _ = A.detect_dhl_state(FakePage(stale), tracking_number="5271993480")
check("A stale page for another waybill is NOT accepted",
      state != A.DHL_READY_RESULT, "got " + state)


print()
print("=" * 68)
print("2. 'WAIT EXACTLY AS LONG AS DHL NEEDS'")
print("=" * 68)

# Fast DHL: ready on the second poll.
page = FakePage([LOADING, RESULT])
start = time.time()
state, elapsed = A.wait_for_dhl_page(page, "5271993480", max_seconds=75)
check("Fast DHL returns READY_RESULT", state == A.DHL_READY_RESULT, "got " + state)
check("Fast DHL exits in ~1s, not 75s", elapsed < 3, "{0:.1f}s".format(elapsed))

# Genuinely slow DHL: 30s of processing, then the result.
slow = [PROCESSING] * 40 + [RESULT]
page = FakePage(slow)
state, elapsed = A.wait_for_dhl_page(page, "5271993480", max_seconds=75)
check("Slow DHL still returns READY_RESULT", state == A.DHL_READY_RESULT, "got " + state)
check("Slow DHL was allowed to finish (waited through processing)",
      page.reads > 30, "{0} polls".format(page.reads))

# Unknown AWB: must not burn the budget.
page = FakePage([LOADING, NO_RESULT])
state, elapsed = A.wait_for_dhl_page(page, "0000000000", max_seconds=75)
check("Unknown AWB returns READY_NO_RESULT", state == A.DHL_READY_NO_RESULT, "got " + state)
check("Unknown AWB exits immediately, not after 75s", elapsed < 3,
      "{0:.1f}s".format(elapsed))

# Error page: terminal.
page = FakePage([LOADING, ERROR])
state, elapsed = A.wait_for_dhl_page(page, "5271993480", max_seconds=75)
check("Error page returns ERROR", state == A.DHL_ERROR, "got " + state)
check("Error page exits immediately", elapsed < 3, "{0:.1f}s".format(elapsed))

# Stuck: painted, no markers, no progress.
STALLED = "DHL Express Tracking" + "x" * 300
page = FakePage([STALLED])
state, elapsed = A.wait_for_dhl_page(page, "5271993480", max_seconds=75, stuck_after=3)
check("No progress detected as STUCK", state == A.DHL_STUCK, "got " + state)
check("STUCK trips at the stuck limit, not the ceiling", elapsed < 10,
      "{0:.1f}s".format(elapsed))

# Ceiling honoured when DHL processes forever.
page = FakePage([PROCESSING])
state, elapsed = A.wait_for_dhl_page(page, "5271993480", max_seconds=4, stuck_after=2)
check("Endless processing hits the ceiling", state == A.DHL_PROCESSING, "got " + state)
check("Processing is never called stuck", elapsed >= 3.5, "{0:.1f}s".format(elapsed))


print()
print("=" * 68)
print("3. COOKIE CONSENT")
print("=" * 68)

page = FakePage([COOKIE], cookie_selector="#onetrust-accept-btn-handler")
handled = A.accept_cookie_banner(page, "DHL")
check("OneTrust banner detected and clicked", handled and page.cookie_clicks == 1,
      "clicks={0}".format(page.cookie_clicks))

page = FakePage([RESULT])
start = time.time()
handled = A.accept_cookie_banner(page, "DHL", budget_seconds=3)
cost = time.time() - start
check("Absent banner returns False", handled is False)
check("Absent banner does not block the run", cost < 4.5, "{0:.2f}s".format(cost))
check("Absent banner used instant count() probes",
      page.selector_probes > 0, "{0} probes".format(page.selector_probes))

# Banner appearing late must still be caught.
page = FakePage([RESULT], cookie_selector=None)


class LateBanner(FakePage):
    def __init__(self):
        FakePage.__init__(self, [COOKIE])
        self.calls = 0

    def locator(self, selector):
        if selector != "body":
            self.calls += 1
            if self.calls > 3 and "#onetrust-accept-btn-handler" in selector:
                self.cookie_selector = "#onetrust-accept-btn-handler"
        return FakePage.locator(self, selector)


late = LateBanner()
handled = A.accept_cookie_banner(late, "DHL", budget_seconds=3)
check("Late-injected banner is still caught", handled is True)

# The state machine dismisses a banner and carries on.
page = FakePage([COOKIE, COOKIE, RESULT], cookie_selector="#onetrust-accept-btn-handler")
state, elapsed = A.wait_for_dhl_page(page, "5271993480", max_seconds=20)
check("Cookie state resolves then reaches the result",
      state == A.DHL_READY_RESULT, "got " + state)


print()
print("=" * 68)
print("4. FAILURE CLASSIFICATION")
print("=" * 68)
for label, error, expected in [
    ("no ETA/ATA", A.SkipShipment("DHL returned no Estimated Delivery date"), A.NO_RESULT),
    ("processing timeout", Exception("DHL processing did not finish within 90 seconds"), A.TIMEOUT),
    ("network drop", Exception("net::ERR_CONNECTION_RESET"), A.TEMPORARY_WEBSITE_ISSUE),
    ("503", Exception("Server returned 503"), A.TEMPORARY_WEBSITE_ISSUE),
    ("bad credentials", Exception("Missing credentials file"), A.AUTHENTICATION_ISSUE),
    ("missing control", Exception("Save/Update button was not found"), A.UNEXPECTED_PAGE_STATE),
    ("unknown", Exception("something else entirely"), A.FAILED),
]:
    got = A.classify_failure(error)
    check("{0} -> {1}".format(label, expected), got == expected, "got " + got)

check("Only sensible classes are retryable",
      A.RETRYABLE == {A.TIMEOUT, A.TEMPORARY_WEBSITE_ISSUE, A.UNEXPECTED_PAGE_STATE})
check("NO RESULT is never retried", A.NO_RESULT not in A.RETRYABLE)
check("AUTH issues are never retried", A.AUTHENTICATION_ISSUE not in A.RETRYABLE)


print()
print("=" * 68)
print("5. RETRY WITH BACKOFF")
print("=" * 68)

attempts = {"n": 0}


def flaky():
    attempts["n"] += 1
    if attempts["n"] < 2:
        raise Exception("net::ERR_TIMED_OUT")
    return "ok"


start = time.time()
result = A.run_with_retry("carrier tracking", "DHL", "5271993480", flaky,
                          max_attempts=3, base_delay_seconds=1)
check("Retryable failure recovers", result == "ok" and attempts["n"] == 2,
      "{0} attempts".format(attempts["n"]))
check("Backoff actually waited", time.time() - start >= 1)

permanent = {"n": 0}


def hopeless():
    permanent["n"] += 1
    raise A.SkipShipment("DHL returned no Estimated Delivery date")


try:
    A.run_with_retry("carrier tracking", "DHL", "1", hopeless,
                     max_attempts=3, base_delay_seconds=1)
    check("Permanent failure raises", False)
except Exception:
    check("Permanent failure raises", True)
check("Permanent failure tried exactly once", permanent["n"] == 1,
      "{0} attempts".format(permanent["n"]))


print()
print("=" * 68)
print("5b. AIRLINE REGISTRY  (AWB prefix routing)")
print("=" * 68)
for awb, expected_provider, expected_name in [
    ("157-49568713", "QATAR", "Qatar Airways"),
    ("057 1234 5678", "AFKL", "Air France"),
    ("074-99887766", "AFKL", "KLM Royal Dutch Airlines"),
    ("020-11223344", None, "Lufthansa Cargo"),
    ("077-12345678", None, "EgyptAir"),
]:
    prefix, entry = A.airline_from_awb(awb)
    check("{0} -> {1}".format(awb, expected_name),
          entry is not None and entry["name"] == expected_name,
          str(entry))
    check("   provider = {0}".format(expected_provider),
          A.carrier_provider(expected_name, awb) == expected_provider)

check("Spacing and dashes are tolerated",
      A.airline_from_awb("157 4956 8713")[0] == A.airline_from_awb("157-49568713")[0])
check("An unknown prefix is not guessed at",
      A.airline_from_awb("999-11112222")[1] is None)
check("A too-short reference yields nothing",
      A.airline_from_awb("12")[0] is None)
check("The prefix outranks a wrong carrier name",
      A.carrier_provider("Totally Wrong Airline", "157-49568713") == "QATAR")
check("Carrier name still works when no AWB is given",
      A.carrier_provider("DHL EXPRESS") == "DHL")
check("Air France and KLM share one integration",
      A.AIRLINES["057"]["provider"] == A.AIRLINES["074"]["provider"] == "AFKL")
check("All 16 airlines are registered", len(A.AIRLINES) == 16, str(len(A.AIRLINES)))

A._unsupported_seen.clear()
reason = A.describe_unsupported("020-11223344", "Lufthansa")
check("Skip reason names the airline and prefix",
      "Lufthansa Cargo (020)" in reason, reason)
A.describe_unsupported("020-55667788", "Lufthansa")
check("Unsupported airlines are tallied for prioritising",
      A._unsupported_seen.get("Lufthansa Cargo (020)") == 2,
      str(A._unsupported_seen))


print()
print("=" * 68)
print("5c. AFKL  (Air France / KLM, prefixes 057 and 074)")
print("=" * 68)

check("AWB is formatted the way AFKL expects",
      A.portal_awb("05712345678") == "057-12345678",
      A.portal_awb("05712345678"))
check("Spacing and dashes are normalised",
      A.portal_awb("074 9988 7766") == "074-99887766")
check("A short reference is passed through untouched",
      A.portal_awb("ABC123") == "ABC123")

AFKL_RESULT = """AIR FRANCE KLM MARTINAIR Cargo
Air waybill 057-12345678
Status  In transit
Origin CDG  Destination CAI
Estimated Time of Arrival   28 August 2026
Flight AF3620   Pieces 4   Weight 512 kg
"""
AFKL_ARRIVED = AFKL_RESULT.replace(
    "Estimated Time of Arrival   28 August 2026",
    "Estimated Time of Arrival   28 August 2026\n"
    "Actual Time of Arrival      27 August 2026\nRCF Received from Flight")
AFKL_NONE = ("AIR FRANCE KLM MARTINAIR Cargo\nNo results found for this air "
             "waybill. Please check the AWB number and try again." + "x" * 200)
AFKL_SHELL = "AIR FRANCE KLM MARTINAIR Cargo"


class AfklPage:
    def __init__(self, text): self.text = text; self.main_frame = self; self.frames = [self]
    def locator(self, sel):
        page = self
        class L:
            @property
            def first(self): return self
            def inner_text(self, timeout=None): return page.text
            def count(self): return 1
            def is_visible(self, timeout=None): return True
        return L()
    def wait_for_timeout(self, ms): pass


# extract_afkl_result was folded into the shared portal extractor.
A.extract_afkl_result = lambda page: A.extract_portal_result(page, "AFKL")

r = A.extract_afkl_result(AfklPage(AFKL_RESULT))
check("ETA read from an in-transit result", r and r["eta"] == "28/08/2026", str(r))
check("No ATA invented when the shipment has not arrived", r and r["ata"] is None)
check("Status reflects the page", r and "Transit" in r["tracking_status"], str(r))

r = A.extract_afkl_result(AfklPage(AFKL_ARRIVED))
check("ATA read once the shipment has arrived", r and r["ata"] == "27/08/2026", str(r))
check("ETA still read alongside the ATA", r and r["eta"] == "28/08/2026")

r = A.extract_afkl_result(AfklPage(AFKL_NONE))
check("An unknown AWB is recognised, not waited out",
      r and r.get("no_result") is True, str(r))

check("A half-drawn page returns nothing rather than a guess",
      A.extract_afkl_result(AfklPage(AFKL_SHELL)) is None)
check("Dates are returned in the automation's dd/mm/yyyy",
      A.normalize_date("28 August 2026") == "28/08/2026")

check("AFKL is routed from the prefix, not the carrier name",
      A.carrier_provider("whatever", "057-12345678") == "AFKL"
      and A.carrier_provider("whatever", "074-99887766") == "AFKL")
# Superseded: the per-carrier branch became one generic route through PORTALS.
check("get_provider_result routes every configured portal",
      "if provider in PORTALS:" in Path("update_eta.py").read_text(encoding="utf-8"))
SRC_A = Path("update_eta.py").read_text(encoding="utf-8")
check("A browser tab is created for every portal",
      "provider_pages[_portal] = context.new_page()" in SRC_A)


print()
print("=" * 68)
print("5d. ASTRAL AVIATION  (prefix 485)")
print("=" * 68)
check("Astral is registered against its prefix",
      A.AIRLINES["485"]["provider"] == "ASTRAL")
check("Astral routes from the AWB prefix",
      A.carrier_provider("anything", "485-12345678") == "ASTRAL")
check("Astral has a portal configuration", "ASTRAL" in A.PORTALS)
check("Its tracking page is the cargo one",
      "astral-aviation.com/track-cargo" in A.PORTALS["ASTRAL"]["urls"][0])

# Its box reads "Enter 11 Digit AWB Number eg XXX-XXXXXXXX".
check("AWB formatted as XXX-XXXXXXXX",
      A.portal_awb("48512345678") == "485-12345678", A.portal_awb("48512345678"))

ASTRAL_RESULT = """Astral Aviation  Track Cargo
AWB 485-12345678
Status  In transit
Origin NBO   Destination CAI
Estimated Time of Arrival  30 August 2026
Pieces 12   Weight 890 kg
"""
ASTRAL_ARRIVED = ASTRAL_RESULT.replace(
    "Status  In transit", "Status  Delivered").replace(
    "Estimated Time of Arrival  30 August 2026",
    "Estimated Time of Arrival  30 August 2026\nActual Time of Arrival  29 August 2026")
ASTRAL_NONE = ("Astral Aviation\nNo records found for this AWB number." + "x" * 200)

r = A.extract_portal_result(AfklPage(ASTRAL_RESULT), "ASTRAL")
check("Astral ETA is read", r and r["eta"] == "30/08/2026", str(r))
check("Astral invents no ATA in transit", r and r["ata"] is None)
r = A.extract_portal_result(AfklPage(ASTRAL_ARRIVED), "ASTRAL")
check("Astral ATA is read on arrival", r and r["ata"] == "29/08/2026", str(r))
check("Astral ETA survives alongside", r and r["eta"] == "30/08/2026")
r = A.extract_portal_result(AfklPage(ASTRAL_NONE), "ASTRAL")
check("Astral unknown AWB is recognised", r and r.get("no_result") is True)
check("Astral half-drawn page yields nothing",
      A.extract_portal_result(AfklPage("Astral Aviation"), "ASTRAL") is None)

check("Both portals share one implementation",
      SRC_A.count("def get_portal_result") == 1)
check("Adding a carrier is a config entry, not new code",
      len(A.PORTALS) == 2 and "urls" in A.PORTALS["ASTRAL"])


print()
print("=" * 68)
print("5f. THE AFKL EMPTY-FIELD FAILURE  (057-05765454)")
print("=" * 68)


class FrameworkField:
    """
    An Angular-style input: fill() writes nothing the framework accepts,
    only real key events do. This is what the failure screenshot showed —
    an empty box and a disabled Track button.
    """

    def __init__(self, accepts_fill=False, accepts_typing=True):
        self.value = ""
        self.accepts_fill = accepts_fill
        self.accepts_typing = accepts_typing
        self.typed = False

    def click(self, timeout=None): pass
    def fill(self, v):
        if self.accepts_fill or v == "":
            self.value = v
    def press_sequentially(self, v, delay=None):
        self.typed = True
        if self.accepts_typing:
            self.value = v
    def press(self, key): pass
    def input_value(self): return self.value
    def evaluate(self, script, v=None):
        self.value = v          # the JS setter path always works
        return None


A.write_log = lambda m: None

field = FrameworkField(accepts_fill=False, accepts_typing=True)
landed = A.type_into(field, "057-05765454", "test")
check("Typing lands the value where fill() silently failed",
      landed == "057-05765454", repr(landed))
check("It typed rather than only filling", field.typed is True)

field = FrameworkField(accepts_fill=False, accepts_typing=False)
landed = A.type_into(field, "057-05765454", "test")
check("The JS setter recovers a field that ignores both",
      landed == "057-05765454", repr(landed))

field = FrameworkField(accepts_fill=True)
check("A normal input still works", A.type_into(field, "485-11112222") == "485-11112222")


class Btn:
    def __init__(self, enabled_after=0):
        self.calls = 0; self.enabled_after = enabled_after; self.clicked = False
    def is_enabled(self):
        self.calls += 1
        return self.calls > self.enabled_after
    def click(self, timeout=None, no_wait_after=None, force=False):
        self.clicked = True


class ClockPage:
    def wait_for_timeout(self, ms): pass


check("A disabled control is waited for, not clicked blindly",
      A.wait_until_enabled(ClockPage(), Btn(enabled_after=3), 3000, "Track") is True)
check("A permanently disabled control times out honestly",
      A.wait_until_enabled(ClockPage(), Btn(enabled_after=10 ** 6), 600, "Track") is False)

SRC_F = Path("update_eta.py").read_text(encoding="utf-8")
check("Submission verifies the value actually landed",
      "would not accept the number" in SRC_F)
check("Enter is used when Track will not enable",
      "submitting with Enter instead" in SRC_F)
check("A bare X can dismiss a cookie panel",
      "cookie close [" in SRC_F)
check("The page is settled before anything is typed",
      SRC_F.index("wait_until_settled(page, page_has_content, PAGE_SETTLE_MAX_SECONDS)\n        if not accept_cookie_banner")
      < SRC_F.index("field = find_portal_input(page, config[\"placeholder\"])"))


print()
print("=" * 68)
print("5g. FROM THE 2 SEP RUN LOG")
print("=" * 68)
SRC_G = Path("update_eta.py").read_text(encoding="utf-8")

# 615-62310566 came back READY_NO_RESULT in 15.6s from dhl.com parcel tracking,
# while plain 9451291275 worked on the same page. 615 is an air waybill.
check("615 is no longer sent to parcel tracking",
      A.AIRLINES["615"]["provider"] is None)
check("615 gives an actionable reason",
      "DHL Aviation (615)" in A.describe_unsupported("615-62310566", "DHL European"))
check("Plain DHL Express numbers still route to DHL",
      A.carrier_provider("DHL Express", "9451291275") == "DHL")
check("A 615 shipment is skipped, not failed against the wrong site",
      A.carrier_provider("DHL European", "615-62310566") is None)

# Both AFKL URLs died with ERR_HTTP2_PROTOCOL_ERROR on the retry, losing a
# shipment whose number had already been accepted.
check("Transport errors are retried, not fatal",
      "ERR_HTTP2_PROTOCOL_ERROR" in SRC_G and "Backing off" in SRC_G)
check("Retries cover the other common transport failures",
      all(t in SRC_G for t in ("ERR_CONNECTION_RESET", "ERR_EMPTY_RESPONSE")))
check("A genuine failure still gives up after the retries",
      'problems.append("{0}: {1}".format(url, message[:90]))' in SRC_G)

# The evidence from attempt 1 was discarded when attempt 2 failed to navigate.
check("Every attempt records what the page showed",
      '"{0}_attempt{1}".format(slug, attempt)' in SRC_G)
check("Dates are dumped per attempt, not only at the end",
      SRC_G.count("describe_page_dates(page, config[\"label\"], tracking_number)") == 2)


print()
print("=" * 68)
print("5e. THE DASHBOARD REFLECTS THE REGISTRY")
print("=" * 68)
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from dashboard.bridge import ControlTowerState

state = ControlTowerState()
for _prefix, _entry in sorted(A.AIRLINES.items()):
    if not _entry["provider"]:
        continue
    state.register_system(
        _entry["provider"],
        A.PORTALS.get(_entry["provider"], {}).get("label")
        or {"DHL": "DHL Tracking", "QATAR": "Qatar Airways Cargo"}.get(
            _entry["provider"], _entry["name"]),
        "Tracking " + ", ".join(sorted(
            e["name"] for e in A.AIRLINES.values()
            if e["provider"] == _entry["provider"])))

names = [s["name"] for s in state.snapshot()["systems"]]
for expected in ["DHL Tracking", "Qatar Airways Cargo", "AFKL myCargo",
                 "Astral Aviation"]:
    check("Systems panel shows {0}".format(expected), expected in names, str(names))
check("The Hub is listed first", names[0] == "Mantrac Logistics Hub")
check("The browser is listed last", "Playwright" in names[-1])
check("Every automated provider appears exactly once",
      len([n for n in names if n == "AFKL myCargo"]) == 1)

afkl = next(s for s in state.snapshot()["systems"] if s["key"] == "AFKL")
check("AFKL declares both airlines it covers",
      "Air France" in afkl["role"] and "KLM" in afkl["role"], afkl["role"])
check("An unautomated carrier is NOT shown as a system",
      not any("Lufthansa" in n for n in names), str(names))


print()
print("=" * 68)
print("6. BUSINESS LOGIC PRESERVED")
print("=" * 68)
check("TARGET_STATUS unchanged", A.TARGET_STATUS == "Under Clearance", A.TARGET_STATUS)
check("MAX_RECORDS_PER_RUN unchanged", A.MAX_RECORDS_PER_RUN == 200)
check("MAX_TABLE_PAGES unchanged", A.MAX_TABLE_PAGES == 10)
check("DHL processing ceiling unchanged", A.DHL_PROCESSING_TIMEOUT_SECONDS == 90)
for name in ["collect_supported_shipments", "update_internal_shipment", "save_result",
             "load_credentials", "get_dhl_result", "get_qatar_result",
             "extract_event_log_result", "wait_until_processing_finishes",
             "event_log_ready", "parse_qatar_awb"]:
    check("{0}() still present".format(name), hasattr(A, name))

print()
print("=" * 68)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
if FAIL:
    for name in FAIL:
        print("  FAILED:", name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
