"""
Which navigation strategy actually reaches AFKL?

Runs the four strategies against the REAL shipment page, one attempt each, and
reports what each one did. It is a diagnostic, not a fix: the point is to find
out WHICH hypothesis is true, because applying every workaround at once would
cure the symptom and teach us nothing.

    python diagnose_afkl.py                      057-05765454
    python diagnose_afkl.py 074-99887766         another air waybill

The strategies, each testing one idea:

    1  the ordinary Chromium Playwright uses      is it reproducible at all?
    2  a fresh context on that same browser       connection reuse / state?
    3  Chromium launched with --disable-http2     HTTP/2 framing?
    4  branded Microsoft Edge (channel=msedge)    the browser build itself?

Nothing here touches the automation. It opens pages and reports.
"""

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import update_eta as A                                        # noqa: E402

AWB = sys.argv[1] if len(sys.argv) > 1 else "057-05765454"
URL = A.build_afkl_detail_url(AWB)

RESULTS = []


def line(char="="):
    print(char * 78)


def run(number, strategy, launcher, http2_disabled=False, channel="chromium"):
    """One attempt. Records everything the report needs; never raises."""
    record = {
        "attempt": number, "strategy": strategy, "channel": channel,
        "http2_disabled": http2_disabled, "url": URL, "error": None,
        "status": None, "final_url": None, "dom_content_loaded": False,
        "loaded": False, "is_afkl": False, "awb_on_page": False,
        "body_chars": 0, "elapsed_ms": None, "result": "FAILED",
    }
    print()
    line("-")
    print("ATTEMPT {0} — {1}".format(number, strategy))
    line("-")
    started = time.time()
    browser = context = None
    try:
        browser, page = launcher()
        try:
            response = page.goto(URL, wait_until="domcontentloaded", timeout=45000)
            record["dom_content_loaded"] = True
            if response is not None:
                record["status"] = response.status
            record["final_url"] = page.url
            try:
                page.wait_for_load_state("load", timeout=10000)
                record["loaded"] = True
            except Exception:
                pass

            page.wait_for_timeout(6000)          # let the app fetch the shipment
            text = ""
            try:
                text = page.locator("body").inner_text(timeout=5000) or ""
            except Exception:
                pass
            record["body_chars"] = len(text.strip())
            record["is_afkl"] = ("afklcargo" in (page.url or "").lower()
                                 or "mycargo" in (page.url or "").lower())
            record["awb_on_page"] = A.page_is_afkl_detail(page, AWB)
            record["final_url"] = page.url

            if record["awb_on_page"]:
                record["result"] = "SUCCESS"
            elif record["dom_content_loaded"]:
                record["result"] = "LOADED BUT AWB NOT CONFIRMED"
        finally:
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
    except Exception as error:
        record["error"] = str(error).split("\n")[0][:220]
        record["result"] = "NAVIGATION ERROR"
    record["elapsed_ms"] = int((time.time() - started) * 1000)

    for key in ("channel", "http2_disabled", "status", "dom_content_loaded",
                "loaded", "is_afkl", "awb_on_page", "body_chars",
                "final_url", "elapsed_ms"):
        print("   {0:<20} {1}".format(key, record[key]))
    if record["error"]:
        print("   {0:<20} {1}".format("navigation error", record["error"]))
    print("   {0:<20} {1}".format("RESULT", record["result"]))
    RESULTS.append(record)
    return record


def main():
    line()
    print("AFKL NAVIGATION DIAGNOSTIC")
    line()
    print("   air waybill      {0}".format(AWB))
    print("   normalised       {0}".format(
        A.portal_awb(AWB, dashed=True)))
    print("   url              {0}".format(URL))
    if URL is None:
        print("\n   That is not an 11-digit air waybill; nothing to test.")
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except Exception as error:
        print("\n   Playwright is unavailable here: {0}".format(error))
        return 1

    with sync_playwright() as playwright:
        chromium_path = None
        for candidate in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",):
            if Path(candidate).exists():
                chromium_path = candidate
                break

        def plain():
            kwargs = {"headless": True}
            if chromium_path:
                kwargs["executable_path"] = chromium_path
            browser = playwright.chromium.launch(**kwargs)
            return browser, browser.new_page()

        def fresh_context():
            kwargs = {"headless": True}
            if chromium_path:
                kwargs["executable_path"] = chromium_path
            browser = playwright.chromium.launch(**kwargs)
            browser.new_context().new_page()      # a first context, then discard
            context = browser.new_context()
            return browser, context.new_page()

        def no_http2():
            kwargs = {"headless": True, "args": ["--disable-http2"]}
            if chromium_path:
                kwargs["executable_path"] = chromium_path
            browser = playwright.chromium.launch(**kwargs)
            return browser, browser.new_page()

        def edge():
            # A clean temporary Playwright profile — never the operator's own.
            browser = playwright.chromium.launch(headless=True, channel="msedge")
            return browser, browser.new_page()

        run(1, "existing Playwright Chromium", plain)
        run(2, "fresh browser context, same URL", fresh_context)
        run(3, "Chromium with --disable-http2", no_http2, http2_disabled=True)
        run(4, "Microsoft Edge (channel=msedge)", edge, channel="msedge")

    print()
    line()
    print("SUMMARY")
    line()
    for record in RESULTS:
        print("ATTEMPT {0}:".format(record["attempt"]))
        print("   strategy = {0}".format(record["strategy"]))
        print("   result   = {0}{1}".format(
            record["result"],
            "  (" + record["error"] + ")" if record["error"] else ""))
    winner = next((r for r in RESULTS if r["result"] == "SUCCESS"), None)
    print()
    print("WINNING STRATEGY:")
    if winner:
        print("   #{0} — {1}".format(winner["attempt"], winner["strategy"]))
    else:
        print("   none — every strategy failed")

    print()
    line()
    print("ROOT CAUSE")
    line()
    errors = " ".join((r["error"] or "") for r in RESULTS)
    if winner and winner["attempt"] == 3:
        print("   A) bundled Chromium HTTP/2 behaviour — disabling HTTP/2 fixed it.")
    elif winner and winner["attempt"] == 4:
        print("   B) browser/channel difference — branded Edge reaches what the")
        print("      bundled Chromium cannot.")
    elif winner and winner["attempt"] == 2:
        print("   D) connection or context state — a fresh context was enough,")
        print("      which points at connection reuse rather than the protocol.")
    elif winner and winner["attempt"] == 1:
        print("   The failure did not reproduce here. It is intermittent, or")
        print("   specific to the machine that saw it.")
    elif "ERR_CONNECTION_RESET" in errors or "ERR_CONNECTION_REFUSED" in errors:
        print("   C) network/server behaviour — every strategy was reset at the")
        print("      transport layer, including ones that change nothing about")
        print("      the browser. That is the network between here and AFKL,")
        print("      not the browser: run this on the machine that runs the")
        print("      automation to get a verdict that applies to it.")
    elif "ERR_HTTP2_PROTOCOL_ERROR" in errors:
        print("   A) HTTP/2 — the error survived every strategy, so it is the")
        print("      protocol negotiation itself rather than one browser build.")
    else:
        print("   D) something else — see the per-attempt errors above.")

    return 0 if winner else 1


if __name__ == "__main__":
    sys.exit(main())
