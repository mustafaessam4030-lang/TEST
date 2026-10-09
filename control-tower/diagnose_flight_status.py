"""
What the myCargo page ACTUALLY offers, and whether the flight card answers.

The landing page carries two forms. This opens the real page on this machine
and prints what is really there — every visible input with its placeholder,
name and id, which box each of the automation's matchers picks, and whether
the two forms can be told apart. Then, if you give it a flight, it drives the
flight status card and prints what came back.

    python diagnose_flight_status.py                    just look at the page
    python diagnose_flight_status.py AF0877 04/09/2026  look, then ask

It types nothing into the air waybill form and submits nothing unless you
give it a flight number and a date. Edge opens visibly so you can watch.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_eta as A                                        # noqa: E402


def describe_inputs(page):
    print("-" * 74)
    print("EVERY VISIBLE INPUT ON THE PAGE")
    print("-" * 74)
    rows = page.evaluate("""() => {
        const out = [];
        for (const el of document.querySelectorAll('input, textarea')) {
            const box = el.getBoundingClientRect();
            if (box.width === 0 && box.height === 0) continue;
            out.push({
                tag: el.tagName.toLowerCase(),
                type: el.getAttribute('type') || '',
                placeholder: el.getAttribute('placeholder') || '',
                name: el.getAttribute('name') || '',
                id: el.id || '',
                formcontrolname: el.getAttribute('formcontrolname') || '',
                aria: el.getAttribute('aria-label') || '',
                form: el.form ? (el.form.id || el.form.name || 'unnamed') : '',
            });
        }
        return out;
    }""")
    if not rows:
        print("  none — the page has not rendered its forms")
    for index, row in enumerate(rows, 1):
        print("  {0}. placeholder={1!r}".format(index, row["placeholder"]))
        print("     id={0!r} name={1!r} formcontrolname={2!r} aria={3!r} "
              "form={4!r}".format(row["id"], row["name"],
                                  row["formcontrolname"], row["aria"],
                                  row["form"]))
    print()

    print("-" * 74)
    print("EVERY VISIBLE BUTTON")
    print("-" * 74)
    for text in page.evaluate("""() => {
        const out = [];
        for (const el of document.querySelectorAll(
                "button, input[type='submit']")) {
            const box = el.getBoundingClientRect();
            if (box.width === 0 && box.height === 0) continue;
            out.push(((el.innerText || el.value || '').trim() || '(no text)')
                     + '   [' + el.tagName.toLowerCase()
                     + (el.disabled ? ', disabled' : '') + ']');
        }
        return out;
    }"""):
        print("  " + text)
    print()


def describe_matchers(page):
    print("-" * 74)
    print("WHAT THE AUTOMATION'S MATCHERS PICK")
    print("-" * 74)

    def name_of(locator):
        if locator is None:
            return "NOTHING FOUND"
        try:
            return "placeholder={0!r} id={1!r}".format(
                locator.get_attribute("placeholder"),
                locator.get_attribute("id"))
        except Exception as error:
            return "found, but could not be described: {0}".format(
                str(error)[:60])

    awb = A.find_portal_input(page, A.PORTALS["AFKL"]["placeholder"])
    print("  air waybill box            : {0}".format(name_of(awb)))
    if awb is not None:
        print("  ...treated as a flight box : {0}".format(
            A.is_flight_status_field(awb)))
    catch = A.find_portal_input(page, "nothing-will-match-this-pattern")
    print("  the catch-all alone picks  : {0}".format(name_of(catch)))

    fields = A.find_flight_status_fields(page)
    if fields is None:
        print("  flight status card         : NOT FOUND")
    else:
        print("  flight number box          : {0}".format(
            name_of(fields["number"])))
        print("  flight date box            : {0}".format(
            name_of(fields["date"])))
        try:
            print("  the card's button          : {0!r}".format(
                fields["button"].inner_text().strip()))
        except Exception:
            print("  the card's button          : found")
    print()
    return fields


def main():
    flight = sys.argv[1].strip().upper() if len(sys.argv) > 1 else None
    when = sys.argv[2].strip() if len(sys.argv) > 2 else None
    url = A.PORTALS["AFKL"]["urls"][0]

    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        A._PLAYWRIGHT = playwright
        browser = playwright.chromium.launch(channel="msedge", headless=False)
        try:
            context = browser.new_context()
            page = context.new_page()
            print()
            print("=" * 74)
            print("OPENING {0}".format(url))
            print("=" * 74)
            page.goto(url, wait_until="commit",
                      timeout=A.NAVIGATION_TIMEOUT_MS)
            A.wait_until_settled(page, A.page_has_content,
                                 A.PAGE_SETTLE_MAX_SECONDS)
            A.accept_cookie_banner(page, "AFKL myCargo")
            A.wait_for_any(
                page,
                [("either form",
                  lambda: A.find_flight_status_fields(page) is not None
                  or A.find_portal_input(page, A.PORTALS["AFKL"]["placeholder"],
                                         strict=True, timeout_ms=250)
                  is not None)],
                A.PORTAL_FORM_READY_MS, reason="either myCargo form")
            print()
            describe_inputs(page)
            describe_matchers(page)

            if A.captcha_on_page(page):
                print("  A human verification challenge is on the page. "
                      "Nothing was submitted.")
                return 0

            if not flight or not when:
                print("  Pass a flight and a date to actually ask, e.g.")
                print("      python diagnose_flight_status.py AF0877 "
                      "04/09/2026")
                return 0

            print("=" * 74)
            print("ASKING THE CARD ABOUT {0} ON {1}".format(flight, when))
            print("=" * 74)
            found = A.check_afkl_flight_status(
                page, {"flight": flight, "date": when,
                       "origin": None, "destination": None})
            print()
            if found is None:
                print("  Nothing came back. The log above says where it "
                      "stopped, and the page text was saved for reading.")
            else:
                print("  flight            : {0}".format(found["flight"]))
                print("  scheduled arrival : {0}".format(
                    found["scheduled_arrival"]))
                print("  actual arrival    : {0}".format(
                    found["actual_arrival"]))
                print()
                print("  As a shipment date this would be filed as an "
                      "ESTIMATE, never as an actual arrival: a flight that "
                      "landed does not prove the shipment was on it.")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
