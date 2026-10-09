"""
Which browser can actually reach the AFKL shipment page — on THIS machine?

This is a diagnostic. It changes nothing: it does not import a selector, does
not touch the parser, does not retry, and does not write to the Hub. It opens
the real URL five ways and reports exactly what each one did.

    python diagnose_afkl.py                    057-05765454
    python diagnose_afkl.py 074-99887766       any other air waybill
    python diagnose_afkl.py --headed           watch the browsers work

It must be run on the machine that SEES the problem. A verdict from anywhere
else is a verdict about that other machine's network.

The five tests, each isolating one variable:

  1  Microsoft Edge, visible window        is the site reachable at all here?
  2  Playwright Chromium                   does the failure reproduce?
  3  Playwright Chromium --disable-http2   is it HTTP/2 framing?
  4  Playwright Edge (channel=msedge)      is it the bundled Chromium build?
  5  Plain HTTPS, no browser               is it the network rather than any browser?

A report is written to afkl_diagnostic.txt so it can be sent on as-is.
"""

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import update_eta as A                                        # noqa: E402

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
HEADED = "--headed" in sys.argv
AWB = ARGS[0] if ARGS else "057-05765454"
URL = A.build_afkl_detail_url(AWB)
REPORT = HERE / "afkl_diagnostic.txt"

LINES = []
RESULTS = []


def say(text=""):
    print(text)
    LINES.append(text)


def rule(char="="):
    say(char * 78)


def blank_record(number, strategy, channel, http2_disabled):
    return {
        "test": number, "strategy": strategy, "channel": channel,
        "http2_disabled": http2_disabled, "url": URL,
        "navigation": "not run", "exception": None, "status": None,
        "final_url": None, "title": None, "dom_content_loaded": False,
        "loaded": False, "afkl_page_rendered": False, "awb_in_dom": False,
        "body_chars": 0, "elapsed_ms": None, "result": "FAILED",
    }


def report(record):
    say()
    rule("-")
    say("TEST {0} — {1}".format(record["test"], record["strategy"]))
    rule("-")
    for key in ("channel", "http2_disabled", "url", "navigation", "exception",
                "status", "final_url", "title", "dom_content_loaded", "loaded",
                "afkl_page_rendered", "awb_in_dom", "body_chars", "elapsed_ms"):
        value = record[key]
        say("   {0:<22} {1}".format(key, "—" if value is None else value))
    say("   {0:<22} {1}".format("RESULT", record["result"]))
    RESULTS.append(record)


def browser_test(number, strategy, launcher, channel="chromium",
                 http2_disabled=False):
    """One browser attempt. Never raises; every outcome becomes a record."""
    record = blank_record(number, strategy, channel, http2_disabled)
    started = time.time()
    browser = None
    try:
        browser, page = launcher()
        try:
            response = page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            record["navigation"] = "succeeded"
            record["dom_content_loaded"] = True
            if response is not None:
                record["status"] = response.status
            try:
                page.wait_for_load_state("load", timeout=12000)
                record["loaded"] = True
            except Exception:
                pass

            page.wait_for_timeout(7000)      # let the app fetch the shipment
            try:
                record["title"] = page.title()
            except Exception:
                pass
            record["final_url"] = page.url
            text = ""
            try:
                text = page.locator("body").inner_text(timeout=6000) or ""
            except Exception:
                pass
            record["body_chars"] = len(text.strip())
            lowered = (page.url or "").lower()
            record["afkl_page_rendered"] = (
                ("afklcargo" in lowered or "mycargo" in lowered)
                and record["body_chars"] > 200)
            # The AWB has to be in the DOM, dashed or not.
            digits = "".join(c for c in AWB if c.isdigit())
            stripped = "".join(c for c in text if c.isdigit())
            record["awb_in_dom"] = bool(digits) and digits in stripped

            if record["awb_in_dom"] and record["afkl_page_rendered"]:
                record["result"] = "SUCCESS — page loaded and AWB confirmed"
            elif record["afkl_page_rendered"]:
                record["result"] = ("LOADED, AFKL RENDERED, BUT AWB NOT IN DOM "
                                    "— inspect before calling it 'not found'")
            elif record["dom_content_loaded"]:
                record["result"] = "LOADED BUT THE AFKL PAGE DID NOT RENDER"
        finally:
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
    except Exception as error:
        message = str(error).split("\n")[0][:240]
        record["exception"] = message
        # A browser that is not installed has told us NOTHING about AFKL.
        # Reporting that as a navigation error would put a false data point
        # into the verdict, which is the whole thing this diagnostic exists
        # to avoid.
        if ("is not found at" in message or "Executable doesn't exist" in message
                or "not installed" in message):
            record["navigation"] = "not attempted"
            record["result"] = "BROWSER NOT INSTALLED — this test proves nothing"
        else:
            record["navigation"] = "failed"
            record["result"] = "NAVIGATION ERROR"
    record["elapsed_ms"] = int((time.time() - started) * 1000)
    report(record)
    return record


def raw_https_test(number):
    """
    No browser at all.

    If this fails the same way the browsers do, the problem is the network
    path rather than anything about a browser, and no launch flag will fix it.
    """
    record = blank_record(number, "plain HTTPS request (no browser)", "urllib", False)
    started = time.time()
    try:
        request = urllib.request.Request(URL, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0 Safari/537.36"})
        with urllib.request.urlopen(request, timeout=45,
                                    context=ssl.create_default_context()) as response:
            body = response.read(200000).decode("utf-8", "replace")
            record["status"] = response.status
            record["final_url"] = response.geturl()
            record["navigation"] = "succeeded"
            record["body_chars"] = len(body)
            record["dom_content_loaded"] = True
            marker = body.lower()
            record["afkl_page_rendered"] = "afkl" in marker or "mycargo" in marker
            digits = "".join(c for c in AWB if c.isdigit())
            record["awb_in_dom"] = digits in "".join(c for c in body if c.isdigit())
            record["result"] = ("SERVER RESPONDED" if response.status < 400
                                else "HTTP {0}".format(response.status))
            start = marker.find("<title>")
            if start >= 0:
                record["title"] = body[start + 7:body.find("</title>", start)][:120]
    except urllib.error.HTTPError as error:
        record["status"] = error.code
        record["navigation"] = "server answered with an error"
        record["exception"] = "HTTP {0} {1}".format(error.code, error.reason)
        record["result"] = "HTTP {0}".format(error.code)
    except Exception as error:
        record["navigation"] = "failed"
        record["exception"] = "{0}: {1}".format(type(error).__name__, error)[:240]
        record["result"] = "CONNECTION ERROR"
    record["elapsed_ms"] = int((time.time() - started) * 1000)
    report(record)
    return record


def main():
    rule()
    say("AFKL NAVIGATION DIAGNOSTIC")
    rule()
    say("   machine          {0}".format(
        __import__("platform").platform()))
    say("   python           {0}".format(sys.version.split()[0]))
    say("   air waybill      {0}".format(AWB))
    say("   url              {0}".format(URL))
    say("   run at           {0}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    if URL is None:
        say("\n   That is not an 11-digit air waybill; nothing to test.")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except Exception as error:
        say("\n   Playwright is unavailable: {0}".format(error))
        say("   Install it with:  pip install -r requirements.txt")
        return 1

    with sync_playwright() as playwright:
        def edge_visible():
            # As close to "your normal Edge" as automation gets: the real Edge
            # build, in a visible window — but a CLEAN temporary profile, never
            # your own. Your signed-in Edge is not touched.
            browser = playwright.chromium.launch(channel="msedge", headless=False)
            return browser, browser.new_page()

        def chromium_plain():
            browser = playwright.chromium.launch(headless=not HEADED)
            return browser, browser.new_page()

        def chromium_no_http2():
            browser = playwright.chromium.launch(
                headless=not HEADED, args=["--disable-http2"])
            return browser, browser.new_page()

        def edge_headless():
            browser = playwright.chromium.launch(
                channel="msedge", headless=not HEADED)
            return browser, browser.new_page()

        browser_test(1, "Microsoft Edge, visible window", edge_visible,
                     channel="msedge")
        browser_test(2, "Playwright Chromium", chromium_plain)
        browser_test(3, "Playwright Chromium with --disable-http2",
                     chromium_no_http2, http2_disabled=True)
        browser_test(4, "Playwright Edge (channel=msedge)", edge_headless,
                     channel="msedge")

    raw_https_test(5)

    # ── verdict ──────────────────────────────────────────────────────
    say()
    rule()
    say("SUMMARY")
    rule()
    for record in RESULTS:
        say("TEST {0}: {1}".format(record["test"], record["strategy"]))
        say("   result = {0}".format(record["result"]))
        if record["exception"]:
            say("   error  = {0}".format(record["exception"]))

    winners = [r for r in RESULTS if r["result"].startswith("SUCCESS")]
    skipped = [r for r in RESULTS if r["navigation"] == "not attempted"]
    say()
    say("WINNING STRATEGY:")
    if winners:
        for record in winners:
            say("   TEST {0} — {1}".format(record["test"], record["strategy"]))
    else:
        say("   none — no browser loaded the page and confirmed the AWB")

    say()
    rule()
    say("WHICH CASE APPLIES")
    rule()
    by_test = {r["test"]: r for r in RESULTS}
    won = {n for n in by_test if by_test[n]["result"].startswith("SUCCESS")}
    unusable = {r["test"] for r in skipped}
    if unusable:
        say("   Tests {0} could not run — the browser is not installed on this"
            .format(", ".join(str(n) for n in sorted(unusable))))
        say("   machine, so they say nothing either way.")
        if 2 in unusable or 3 in unusable:
            say("   Install the Playwright browser first:")
            say("       python -m playwright install chromium")
        if 1 in unusable or 4 in unusable:
            say("   Microsoft Edge was not found. Tests 1 and 4 need it.")
        say()
    errors = " ".join((r["exception"] or "") for r in RESULTS)
    server_answered = by_test.get(5, {}).get("status") is not None

    if won == {1, 2, 3, 4} or (won and {2, 3, 4} <= won):
        say("   E) everything works — the problem is not navigation.")
        say("      Re-run the automation and look at what happens AFTER the")
        say("      page loads.")
    elif 1 in won and 2 not in won and 3 not in won and 4 not in won:
        say("   A) normal Edge works, every Playwright variant fails.")
        say("      Automation is being treated differently from a human")
        say("      browser — look at the launch profile, not the protocol.")
    elif 3 in won and 2 not in won:
        say("   B) --disable-http2 fixes it. The cause is HTTP/2 framing")
        say("      between this machine's Chromium and AFKL. Make strategy 3")
        say("      the production route for AFKL.")
    elif 4 in won and 2 not in won and 3 not in won:
        say("   C) branded Edge works where the bundled Chromium does not.")
        say("      The cause is the browser build. Pin channel='msedge' for")
        say("      the AFKL path.")
    elif not won and server_answered:
        say("   D) every browser failed, but a plain HTTPS request reached the")
        say("      server. The problem is in the browser layer, not the")
        say("      network — see the exceptions above.")
    elif not won and unusable >= {1, 2, 3, 4}:
        say("   INCONCLUSIVE — no browser on this machine could even start,")
        say("   so nothing here is evidence about AFKL. Install the browsers")
        say("   and run it again.")
    elif not won:
        say("   D) every Playwright variant failed AND a plain HTTPS request")
        say("      could not reach the server either. That points at the")
        say("      network path — proxy, TLS inspection, or a firewall between")
        say("      this machine and afklcargo.com — rather than any browser.")
        if "ERR_HTTP2_PROTOCOL_ERROR" in errors:
            say("      ERR_HTTP2_PROTOCOL_ERROR appearing even with HTTP/2")
            say("      disabled would mean something upstream is terminating")
            say("      the connection, not the browser negotiating it.")
    else:
        say("   Mixed result — read the per-test rows above.")

    say()
    say("   NOTE: a shipment is only 'not found' when a test shows")
    say("   afkl_page_rendered=True and awb_in_dom=False. Nothing above with a")
    say("   navigation error says anything at all about the air waybill.")

    try:
        REPORT.write_text("\n".join(LINES) + "\n", encoding="utf-8")
        print("\nReport written to {0}".format(REPORT))
    except OSError as error:
        print("\nCould not write the report: {0}".format(error))

    return 0 if winners else 1


if __name__ == "__main__":
    sys.exit(main())
