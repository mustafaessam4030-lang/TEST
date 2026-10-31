"""
Check one ocean carrier against its real website, on this machine.

    python diagnose_ocean.py MAERSK 231045678
    python diagnose_ocean.py MSC MEDUAB123456
    python diagnose_ocean.py            (lists the carriers)

Opens the carrier the way the automation does — straight to the shipment
where the carrier allows it, otherwise through its search box — and prints:
every visible input on the page, which box was used, what the result page
says, and the ETA and ATA the reader took from it, with the label each came
from. Nothing is written anywhere. Edge opens visibly so you can watch, and
human verification is left for you to complete.

Run it to check a carrier's page without writing anything. Normal runs
write ocean results (OCEAN_WRITE=0 stops that) and count a write as a
success only when the Hub reads the date back.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_eta as A                                        # noqa: E402


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("Carriers:")
        for key, carrier in A.OCEAN_PORTALS.items():
            print("  {0:10} {1:30} {2}".format(
                key, carrier["label"],
                carrier.get("deep_link") or (carrier["urls"] or ["(no address yet)"])[0]))
        return 0
    key = sys.argv[1].strip().upper()
    reference = sys.argv[2].strip()
    if key not in A.OCEAN_PORTALS:
        print("Unknown carrier {0}. Run without arguments for the list.".format(key))
        return 2
    config = A.PORTALS[key]
    if not config.get("urls"):
        print("{0} has no tracking address yet.".format(config["label"]))
        return 2

    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=False)
        try:
            page = browser.new_context().new_page()
            print("=" * 74)
            print("{0} — {1}".format(config["label"], reference))
            print("=" * 74)
            result, problem = None, None
            try:
                result = A.get_portal_result(page, key, reference, None)
            except Exception as error:
                problem = error
            print()
            print("-" * 74)
            print("EVERY VISIBLE INPUT ON THE LAST PAGE")
            print("-" * 74)
            for row in page.evaluate("""() => Array.from(
                    document.querySelectorAll('input, textarea'))
                .filter(e => { const b = e.getBoundingClientRect();
                               return b.width || b.height; })
                .map(e => ({p: e.placeholder || '', n: e.name || '',
                            i: e.id || '', t: e.type || ''}))"""):
                print("  placeholder={p!r} name={n!r} id={i!r} type={t!r}".format(**row))
            print()
            print("-" * 74)
            print("WHAT THE PAGE SAYS (first 1200 characters)")
            print("-" * 74)
            print(" ".join((A._page_text(page) or "").split())[:1200])
            print()
            print("-" * 74)
            print("WHAT THE READER TOOK FROM IT")
            print("-" * 74)
            if problem is not None:
                print("  no result: {0}".format(str(problem)[:300]))
            else:
                for kind in ("eta", "ata"):
                    print("  {0}: {1}  (from {2!r})".format(
                        kind.upper(), result.get(kind),
                        result.get(kind + "_source")))
                print("  status: {0}".format(result.get("tracking_status")))
            print()
            print("  Compare those dates with what the page shows. Nothing was "
                  "written to the Hub.")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
