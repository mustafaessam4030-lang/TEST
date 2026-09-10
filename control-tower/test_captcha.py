"""
Human verification: detect, pause, resume — never bypass.

The rule this file exists to enforce is that there is no automated solve, and
that a challenge is never confused with a shipment problem. A challenge page
carries no air waybill data, so reading it would produce "the carrier has no
information for this shipment" — a statement about the shipment made on the
strength of a page that never looked it up.

Also covers the two AFKL corrections that go with it: the canonical Single
Search entry point, and the airline prefix being part of the AWB's identity.

    python test_captcha.py                 offline
    python test_captcha.py --browser       also renders real challenge markup
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))

import update_eta as A                                          # noqa: E402
import bridge as B                                              # noqa: E402

PASS, FAIL = [], []
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


class Page:
    """Enough of a page for the detector."""

    def __init__(self, text="", selectors=()):
        self.text = text
        self.selectors = set(selectors)
        self.main_frame = self
        self.frames = [self]
        self.waits = 0

    def locator(self, selector):
        present = selector in self.selectors
        page = self

        class L:
            @property
            def first(self):
                return self

            def count(self):
                return 1 if present else 0

            def inner_text(self, timeout=None):
                return page.text

            def is_visible(self, timeout=None):
                return present
        return L()

    def wait_for_timeout(self, ms):
        self.waits += 1
        # After two polls, the "person" clears it.
        if self.waits >= 2:
            self.selectors = set()
            self.text = CARGO


CARGO = ("Track and Trace\nProgress details\nJRO ARRIVAL 4 pcs "
         "Estimated: 04 SEP 20:15\nFlight schedule\n" + "x" * 300)

TURNSTILE = ("Verify you are human by completing the action below.\n"
             "www.afklcargo.com needs to review the security of your connection "
             "before proceeding.")
RECAPTCHA = "I'm not a robot\nreCAPTCHA\nPrivacy - Terms"
CF_INTERSTITIAL = "Checking if the site connection is secure\nafklcargo.com"

print("=" * 72)
print("1. DETECTION — STRUCTURE FIRST")
print("=" * 72)
for selector in ("iframe[src*='challenges.cloudflare.com']",
                 "iframe[title*='reCAPTCHA']", "div.cf-turnstile",
                 "div.g-recaptcha", "#challenge-form",
                 "input[name='cf-turnstile-response']"):
    check("{0} is detected".format(selector),
          A.captcha_on_page(Page(CARGO, {selector})))
check("A widget iframe is detected even when the page reads like cargo",
      A.captcha_on_page(Page(CARGO, {"div.cf-turnstile"})),
      "structure must win over text")

print()
print("=" * 72)
print("2. DETECTION — TEXT, DELIBERATELY NARROW")
print("=" * 72)
check("Cloudflare Turnstile wording", A.captcha_on_page(Page(TURNSTILE)))
check("reCAPTCHA wording", A.captcha_on_page(Page(RECAPTCHA)))
check("The Cloudflare interstitial", A.captcha_on_page(Page(CF_INTERSTITIAL)))
check("An ordinary cargo page is NOT a challenge", not A.captcha_on_page(Page(CARGO)))
check("A page that merely says 'verify' is not a challenge",
      not A.captcha_on_page(Page(
          "Please verify the air waybill number and try again. " + "x" * 200)))
check("Nor one about verified shipments",
      not A.captcha_on_page(Page(
          "Shipment verified. Delivery confirmed by signature. " + "x" * 200)))
check("A LONG page carrying the phrase in prose is not a challenge — a real "
      "challenge page is short",
      not A.captcha_on_page(Page(
          "Our security policy: we may ask you to confirm you are human. "
          + "x" * 5000)))
check("A detector failure is False, never an exception",
      A.captcha_on_page(object()) is False)

print()
print("=" * 72)
print("3. THERE IS NO AUTOMATED SOLVE")
print("=" * 72)
check("Nothing clicks a challenge checkbox",
      not re.search(r"(recaptcha|turnstile|hcaptcha|challenge)[^\n]*\.click\(",
                    SRC, re.I))
check("No challenge token is ever submitted",
      "cf-turnstile-response" not in SRC.split("CAPTCHA_SELECTORS")[1].split(")")[1]
      if "CAPTCHA_SELECTORS" in SRC else True)
check("No solver service is contacted",
      not any(name in SRC.lower() for name in
              ("2captcha", "anticaptcha", "capsolver", "deathbycaptcha",
               "capmonster", "solve_captcha", "bypass_captcha")))
check("The intent is stated where the code lives",
      "There is NO automated solve here" in SRC)
check("...and the waiting function only waits",
      all(token not in A.await_human_verification.__doc__.lower()
          for token in ("solve", "bypass", "click")))

print()
print("=" * 72)
print("4. THE PAUSE IS SAFE AND BOUNDED")
print("=" * 72)
check("The wait is bounded", 0 < A.CAPTCHA_WAIT_MS <= 900000, str(A.CAPTCHA_WAIT_MS))
check("...and capped at 15 minutes however it is configured",
      A._captcha_wait_ms() <= 900000)
import os
os.environ["CAPTCHA_WAIT_MS"] = "99999999"
check("An absurd override is clamped, not honoured", A._captcha_wait_ms() == 900000,
      str(A._captcha_wait_ms()))
os.environ["CAPTCHA_WAIT_MS"] = "0"
check("Zero is allowed, for an unattended run", A._captcha_wait_ms() == 0)
os.environ["CAPTCHA_WAIT_MS"] = "not a number"
check("Garbage falls back to the default", A._captcha_wait_ms() == 180000)
os.environ.pop("CAPTCHA_WAIT_MS", None)

body = SRC.split("def await_human_verification")[1].split("\ndef ")[0]
check("The wait does NOT navigate, reload or resubmit — the page the person "
      "left behind is the page the run continues with",
      not any(token in body for token in ("page.goto(", "page.reload(",
                                          "submit_portal_awb", "page.go_back(")))
check("It polls on a fixed interval rather than spinning",
      "CAPTCHA_POLL_MS" in body)
check("It re-checks the page rather than assuming the person succeeded",
      "captcha_on_page(page)" in body)
check("It tells the operator what to do, in the run log",
      "HUMAN VERIFICATION REQUIRED" in body and "browser window" in body)
check("It captures evidence for the operator",
      "take_screenshot" in body and "save_page_text" in body)

print()
print("=" * 72)
print("5. A CHALLENGE IS NOT A SHIPMENT FAILURE")
print("=" * 72)
check("It has its own outcome", A.CAPTCHA_REQUIRED == "HUMAN VERIFICATION REQUIRED")
check("...which is NOT retryable — a retry cannot clear a challenge",
      A.CAPTCHA_REQUIRED not in A.RETRYABLE)
check("...and is not NO RESULT, which would blame the shipment",
      A.CAPTCHA_REQUIRED != A.NO_RESULT)
error = A.CaptchaRequired("057-05765454", "AFKL myCargo")
check("classify_failure maps it correctly",
      A.classify_failure(error) == A.CAPTCHA_REQUIRED, A.classify_failure(error))
check("The message names the shipment and where it happened",
      "057-05765454" in str(error) and "AFKL myCargo" in str(error))
check("...and says nothing was written",
      "nothing was written" in str(error))
check("CaptchaRequired is NOT a SkipShipment — a skip is a claim about the "
      "shipment, and this is not one",
      not issubclass(A.CaptchaRequired, A.SkipShipment))
check("The shipment loop counts it separately from skipped and failed",
      "needs_human += 1" in SRC)
check("...and the run summary reports it",
      "Human verification required: {needs_human}" in SRC)
check("Nothing is written to the Hub on that path",
      'save_result(shipment, dhl_result, "No update",\n'
      '                                    "HUMAN VERIFICATION REQUIRED"' in SRC)

print()
print("=" * 72)
print("6. DETECT -> PAUSE -> RESUME, END TO END")
print("=" * 72)
page = Page(TURNSTILE, {"div.cf-turnstile"})
check("A challenge is present to begin with", A.captcha_on_page(page))
cleared = A.await_human_verification(page, "057-05765454", "AFKL myCargo")
check("The wait returns True once the person clears it", cleared is True)
check("...and the page is now the real one", not A.captcha_on_page(page))
check("...so extraction can proceed on it",
      A._read_afkl_page(page, "AFKL") is not None)

stuck = Page(TURNSTILE, {"div.cf-turnstile"})
stuck.wait_for_timeout = lambda ms: None            # nobody ever clears it
import os as _os
_os.environ["CAPTCHA_WAIT_MS"] = "0"
A.CAPTCHA_WAIT_MS = A._captcha_wait_ms()
check("An uncleared challenge returns False rather than looping forever",
      A.await_human_verification(stuck, "057-05765454", "AFKL myCargo") is False)
_os.environ.pop("CAPTCHA_WAIT_MS", None)
A.CAPTCHA_WAIT_MS = A._captcha_wait_ms()

check("The entry point checks for a challenge BEFORE looking for the form",
      SRC.index("if captcha_on_page(page):") <
      SRC.index("if not accept_cookie_banner(page, config[\"label\"]):"))
check("...and raises rather than reporting a missing tracking field",
      "raise CaptchaRequired(tracking_number, config[\"label\"])" in SRC)
# The PORTAL loop specifically — SRC has three `while time.time() < end_time`
# loops and the first belongs to Qatar, so the earlier version of this check
# was reading the wrong one.
_portal_loop = SRC.split("def get_portal_result")[1].split("\ndef ")[0]
_loop_body = _portal_loop.split("while time.time() < end_time:")[1]
check("The portal result loop checks for a challenge before extracting",
      _loop_body.index("captcha_on_page(page)")
      < _loop_body.index("extract_portal_result"))
check("Qatar checks for one too — it navigates directly, not via open_portal",
      "await_human_verification(page, tracking_number,\n"
      "                                        \"Qatar Airways Cargo\")" in SRC)
_qatar = SRC.split("def get_qatar_result")[1].split("\ndef ")[0]
check("...before it looks for the Qatar form",
      _qatar.index("captcha_on_page(page)") < _qatar.index("submit_qatar_awb"))

print()
print("=" * 72)
print("7. THE DASHBOARD IS TOLD, AND HONESTLY")
print("=" * 72)
state = B.ControlTowerState()
check("Nothing is claimed before a challenge is seen",
      state.snapshot()["human_verification"] is None)
state.human_verification_required("057-05765454", "AFKL myCargo")
hv = state.snapshot()["human_verification"]
check("A challenge is reported as waiting", hv["waiting"] is True, str(hv))
check("...naming the shipment and the page", hv["reference"] == "057-05765454"
      and hv["where"] == "AFKL myCargo", str(hv))
check("...and the browser system shows it is waiting, not failed",
      [s for s in state.snapshot()["systems"]
       if s["key"] == "browser"][0]["state"] == "waiting")
state.human_verification_cleared("057-05765454", 42)
hv = state.snapshot()["human_verification"]
check("Clearing it is recorded with how long it took",
      hv["waiting"] is False and hv["cleared_after_s"] == 42, str(hv))
check("The bridge cannot raise into the automation",
      state.human_verification_required(object(), object()) is None)

print()
print("=" * 72)
print("8. ATLAS RECORDS IT AS NEITHER SUCCESS NOR FAILURE OF A STRATEGY")
print("=" * 72)
captcha_block = SRC.split("def await_human_verification")[1].split("\ndef ")[0]
check("The challenge is recorded in telemetry",
      'ml_record(' in captcha_block and '"captcha_required"' in captcha_block)
check("...categorised as a bot challenge",
      '"BOT_CHALLENGE"' in captcha_block)
from ml import reward
check("...which is charged to NO strategy — a challenge is not a locator's "
      "fault", reward.fault("BOT_CHALLENGE") == 0.0)
check("It is not an ATLAS selection", "ATLAS_STRATEGY_SELECTED" not in captcha_block)
check("...nor an ATLAS action completed", "ATLAS_ACTION_COMPLETED" not in captcha_block)
check("A challenge cannot set the influence flag",
      "atlas_influenced = True" not in captcha_block)

print()
print("=" * 72)
print("9. AFKL: THE CANONICAL SINGLE SEARCH URL")
print("=" * 72)
check("Single Search is the entry point",
      A.PORTALS["AFKL"]["urls"] == [
          "https://www.afklcargo.com/mycargo/shipment/singlesearch"],
      str(A.PORTALS["AFKL"]["urls"]))
check("There is no homepage navigation",
      not any(u.rstrip("/").endswith("afklcargo.com")
              for u in A.PORTALS["AFKL"]["urls"]))
check("The direct shipment page is built under the same myCargo path",
      A.AFKL_DETAIL_URL.startswith(
          "https://www.afklcargo.com/mycargo/shipment/detail/"),
      A.AFKL_DETAIL_URL)
check("Astral is untouched",
      A.PORTALS["ASTRAL"]["urls"] == ["https://astral-aviation.com/track-cargo/"])

print()
print("=" * 72)
print("10. AFKL: THE PREFIX IS PART OF THE IDENTITY")
print("=" * 72)
for raw, expect in [("05705765454", "057-05765454"),
                    ("057-05765454", "057-05765454"),
                    ("07499887766", "074-99887766"),
                    ("074-99887766", "074-99887766"),
                    ("074/1234/5678", "074-12345678"),
                    ("16012345678", "160-12345678")]:
    url = A.build_afkl_detail_url(raw)
    check("{0!r} keeps its prefix -> {1}".format(raw, expect),
          url == A.AFKL_DETAIL_URL.format(expect), str(url))
check("057 is not hardcoded into the URL builder",
      '"057"' not in SRC.split("def build_afkl_detail_url")[1].split("\ndef ")[0])
check("KLM 074 routes to AFKL, like Air France 057",
      A.carrier_provider("x", "074-99887766") == "AFKL"
      and A.carrier_provider("x", "057-05765454") == "AFKL")
check("Both airlines are named separately in the registry",
      A.AIRLINES["057"]["name"] == "Air France"
      and "KLM" in A.AIRLINES["074"]["name"])
check("A 074 page does not satisfy a 057 request",
      not A.awb_on_page(Page("Shipment 074-99887766 " + CARGO), "057-05765454"))
check("...and vice versa",
      not A.awb_on_page(Page("Shipment 057-05765454 " + CARGO), "074-99887766"))
check("The right AWB matches however it is punctuated",
      A.awb_on_page(Page("AWB 057 0576 5454 " + CARGO), "057-05765454")
      and A.awb_on_page(Page("AWB 05705765454 " + CARGO), "057-05765454"))

print()
print("=" * 72)
print("11. NO FALSE SUCCESS: IDENTITY IS CHECKED ON BOTH AFKL PATHS")
print("=" * 72)
check("The direct-URL path verifies identity",
      "awb_on_page(page, tracking_number)" in
      SRC.split("def page_is_afkl_detail")[1].split("\ndef ")[0])
check("The search-form path verifies it too",
      "identity_required and not awb_on_page(page, tracking_number)" in SRC)
check("...and AFKL asks for that check",
      A.PORTALS["AFKL"].get("verify_identity") is True)
check("A wrong-AWB page is refused rather than read",
      "does not carry {1}. Not " in SRC)
check("...and the evidence is kept", '"_wrong_awb"' in SRC)
check("The other carriers are unaffected",
      not A.PORTALS["ASTRAL"].get("verify_identity")
      and not A.PORTALS.get("QATAR", {}).get("verify_identity"))
check("A page for the wrong shipment is refused by the detail check",
      not A.page_is_afkl_detail(Page(CARGO.replace("JRO", "JRO 074-99887766")),
                                "057-05765454"))

if "--browser" in sys.argv:
    print()
    print("=" * 72)
    print("12. REAL BROWSER — actual challenge markup")
    print("=" * 72)
    try:
        import tempfile
        from playwright.sync_api import sync_playwright
        tmp = Path(tempfile.mkdtemp())
        PAGES = {
            "turnstile": """<!doctype html><meta charset=utf-8>
              <title>Just a moment...</title><body>
              <h1>Verify you are human by completing the action below.</h1>
              <p>www.afklcargo.com needs to review the security of your
              connection before proceeding.</p>
              <div class="cf-turnstile" data-sitekey="x"></div>
              <input type="hidden" name="cf-turnstile-response"></body>""",
            "recaptcha": """<!doctype html><meta charset=utf-8><body>
              <div class="g-recaptcha"></div>
              <iframe title="reCAPTCHA" src="about:blank"></iframe>
              <span>I'm not a robot</span></body>""",
            "interstitial": """<!doctype html><meta charset=utf-8><body>
              <div id="cf-challenge-running"></div>
              <h2>Checking if the site connection is secure</h2></body>""",
            "cargo": """<!doctype html><meta charset=utf-8><body>
              <h1>Track and Trace</h1><p>057-05765454 EN ROUTE</p>
              <h2>Progress details</h2>
              <p>JRO ARRIVAL 4 pcs Estimated: 04 SEP 20:15</p>
              <h2>Flight schedule</h2><p>CDG - JRO AF0877 04 SEP 10:15</p>
              <p>""" + "shipment detail " * 40 + "</p></body>",
        }
        for name, html in PAGES.items():
            (tmp / (name + ".html")).write_text(html, encoding="utf-8")
        launch = {}
        import os as _o
        for c in (_o.environ.get("CHROMIUM_PATH"),
                  "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"):
            if c and Path(c).exists():
                launch["executable_path"] = c
                break
        with sync_playwright() as pw:
            browser = pw.chromium.launch(**launch)
            page = browser.new_page()
            try:
                for name in ("turnstile", "recaptcha", "interstitial"):
                    page.goto((tmp / (name + ".html")).as_uri())
                    check("a real {0} page is detected in the browser".format(name),
                          A.captcha_on_page(page))
                page.goto((tmp / "cargo.html").as_uri())
                check("a real cargo page is NOT detected as a challenge",
                      not A.captcha_on_page(page))
                check("...and the AWB identity check passes on it",
                      A.awb_on_page(page, "057-05765454"))
                check("...while a different AWB is refused",
                      not A.awb_on_page(page, "074-99887766"))
            finally:
                browser.close()
    except Exception as error:
        check("browser check ran", False, str(error)[:200])
else:
    print()
    print("  (skipping the browser check — re-run with --browser)")

print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 72)
sys.exit(1 if FAIL else 0)
