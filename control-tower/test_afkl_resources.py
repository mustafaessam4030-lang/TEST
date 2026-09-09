"""
What the AFKL ladder costs the SERVER while it runs.

The bug this pins was reported from the field, not from a log: on the same
Windows server, Microsoft Edge could open AFKL myCargo with the automation
off, could not open it with the automation on, and could again the moment the
automation stopped. Nothing about AFKL's markup explains that. A machine-wide
resource does.

It was strategy 2 of the navigation ladder. It opened a fresh browser context
and a page on the carrier for every shipment whose first attempt failed, and
never closed either. A Playwright context is not freed by Python's garbage
collector — it lives on the browser side until close() is called — so each one
sat there for the rest of the run holding an open page, and behind that page a
live keep-alive TCP connection to the carrier's host. Sockets and ephemeral
ports are shared with every other program on the machine, including the
operator's own Edge.

These checks run against a local stub, so they need no internet and say
nothing about AFKL's site. What they measure is the automation's own footprint.

    python test_afkl_resources.py
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_eta as A                                        # noqa: E402

PASS, FAIL, SKIP = [], [], []
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format(
        "PASS" if condition else "FAIL", name,
        "  ({0})".format(detail) if detail and not condition else ""))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP  {0}  ({1})".format(name, why))


# ─────────────────────────────────────────────────────────────────────────
# 1 · THE SOURCE GUARANTEES
# ─────────────────────────────────────────────────────────────────────────
print("=" * 74)
print("1. NOTHING THE LADDER OPENS IS LEFT TO THE GARBAGE COLLECTOR")
print("=" * 74)

BODY_SRC = SRC.split("def open_afkl_detail")[1].split("\ndef _afkl_budget_left")[0]

check("Strategy 2's context is closed on the failing path",
      "_close_afkl_context(extra_context)" in BODY_SRC)
check("...and on every other exit from the ladder, via finally",
      "    finally:\n        # Whichever way this ends" in BODY_SRC
      and BODY_SRC.rindex("_close_afkl_context(extra_context)")
      > BODY_SRC.index("    finally:"))
check("The old leaking list is gone", "extra_pages" not in SRC)
check("A context handed back to the caller is tracked, not forgotten",
      "AFKL_HELD_CONTEXTS.append(extra_context)" in BODY_SRC
      and isinstance(A.AFKL_HELD_CONTEXTS, list))
check("Each lookup releases what the previous one handed back",
      "release_afkl_helpers(keep_page=page)" in BODY_SRC)
check("...while leaving the page the caller is reading alone",
      "keep_page" in SRC.split("def release_afkl_helpers")[1][:900]
      and "if context is keep_context:" in SRC)
check("Side browsers are released per lookup, not only at run end",
      "AFKL_SIDE_BROWSERS.remove(browser)"
      in SRC.split("def release_afkl_helpers")[1].split("\ndef ")[0])
check("Run-end cleanup keeps nothing",
      "def close_afkl_side_browsers" in SRC
      and "release_afkl_helpers()"
      in SRC.split("def close_afkl_side_browsers")[1].split("\ndef ")[0])
check("Closing never raises over the run",
      "def _close_afkl_context" in SRC
      and "except Exception as error:"
      in SRC.split("def _close_afkl_context")[1].split("\ndef ")[0])

# The mechanism was a shared resource, so the automation must not be reaching
# for machine-wide network settings either. It never did; this keeps it so.
check("The automation sets no environment, proxy or DNS override",
      "os.environ[" not in SRC and "--proxy-server" not in SRC
      and "--host-resolver-rules" not in SRC)
check("No request interception is installed on the carrier's traffic",
      "page.route(" not in SRC and "context.route(" not in SRC)


# ─────────────────────────────────────────────────────────────────────────
# THE STUB CARRIER
# ─────────────────────────────────────────────────────────────────────────
# HTTP/1.1 with keep-alive, because that is what the run uses: DISABLE_HTTP2
# defaults on, so Chromium holds real sockets open per context instead of
# multiplexing one. CAP models an edge that only tolerates so many concurrent
# connections from one source address and stalls the rest — which is what a
# carrier's front door, or an exhausted ephemeral port pool, looks like from
# the browser's side.
CAP = 4
TARPIT_SECONDS = 20
PORT = int(os.environ.get("AFKL_STUB_PORT", "9633"))
PAGE = (b"<!doctype html><title>myCargo</title>"
        b"<div>myCargo Track and trace</div>")
LIVE = {"open": 0, "peak": 0, "tarpitted": 0}
LOCK = threading.Lock()


class Carrier(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def setup(self):
        BaseHTTPRequestHandler.setup(self)
        with LOCK:
            LIVE["open"] += 1
            LIVE["peak"] = max(LIVE["peak"], LIVE["open"])

    def finish(self):
        with LOCK:
            LIVE["open"] -= 1
        try:
            BaseHTTPRequestHandler.finish(self)
        except Exception:
            pass

    def do_GET(self):
        with LOCK:
            crowded = LIVE["open"] > CAP
            if crowded:
                LIVE["tarpitted"] += 1
        if crowded:
            time.sleep(TARPIT_SECONDS)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
        except Exception:
            pass


def open_connections():
    with LOCK:
        return LIVE["open"]


def launch_options():
    """A browser that exists on this machine, whichever one that is."""
    for channel in ("msedge", "chrome", None):
        options = {"headless": True}
        if channel:
            options["channel"] = channel
        yield options
    # A machine with Playwright's own Chromium unpacked but no headless shell,
    # and one where the binary simply lives somewhere else.
    seen = set()
    named = os.environ.get("CHROMIUM_PATH")
    roots = [Path(named)] if named else []
    for pattern in ("chromium-*/chrome-linux/chrome",
                    "chromium-*/chrome-win/chrome.exe"):
        roots.extend(sorted(Path("/opt/pw-browsers").glob(pattern))
                     if Path("/opt/pw-browsers").is_dir() else [])
    for binary in roots:
        if binary in seen or not binary.exists():
            continue
        seen.add(binary)
        yield {"headless": True, "executable_path": str(binary)}


def open_browser(playwright):
    last = None
    for options in launch_options():
        try:
            return playwright.chromium.launch(**options), None
        except Exception as error:
            last = str(error).split("\n")[0][:120]
    return None, last


# ─────────────────────────────────────────────────────────────────────────
# 2 · THE MEASUREMENTS
# ─────────────────────────────────────────────────────────────────────────
LOOKUPS = int(os.environ.get("AFKL_RESOURCE_LOOKUPS", "6"))

server = ThreadingHTTPServer(("127.0.0.1", PORT), Carrier)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:{0}/mycargo/shipment/detail".format(PORT)

# The ladder is pointed at the stub and given short waits. Everything else
# about it — the four strategies, the budget, the identity check — is the
# production code, unmodified.
A.build_afkl_detail_url = lambda number: URL
A.write_log = lambda *args, **kwargs: None
A.AFKL_NAV_TIMEOUT_MS = 2500
A.AFKL_DETAIL_READY_MS = 1000
A.PAGE_SETTLE_MAX_SECONDS = 1
A.AFKL_NAV_BUDGET_MS = 6000
A.save_page_text = lambda *args, **kwargs: None
A.take_screenshot = lambda *args, **kwargs: None

try:
    from playwright.sync_api import sync_playwright
except Exception as error:                                    # pragma: no cover
    sync_playwright = None
    IMPORT_ERROR = str(error)[:120]

print()
print("=" * 74)
print("2. THE FOOTPRINT, MEASURED — {0} LOOKUPS THAT ALL FAIL".format(LOOKUPS))
print("=" * 74)

behaviour = {}
if sync_playwright is None:
    for name in ("no context accumulation", "no page accumulation",
                 "no connection accumulation", "the operator's browser",
                 "a leak is still detectable", "a kept page is released later"):
        skip(name, "playwright is not importable: {0}".format(IMPORT_ERROR))
else:
    with sync_playwright() as playwright:
        A._PLAYWRIGHT = playwright
        browser, why = open_browser(playwright)
        if browser is None:
            for name in ("no context accumulation", "no page accumulation",
                         "no connection accumulation", "the operator's browser",
                         "a leak is still detectable",
                         "a kept page is released later"):
                skip(name, "no browser could be launched: {0}".format(why))
        else:
            context = browser.new_context()
            page = context.new_page()
            print("  {0:>7}  {1:>8} {2:>6} {3:>12}".format(
                "lookup", "contexts", "pages", "connections"))
            baseline = (len(browser.contexts),
                        sum(len(c.pages) for c in browser.contexts))
            for number in range(1, LOOKUPS + 1):
                try:
                    A.open_afkl_detail(page, A.PORTALS["AFKL"],
                                       "05705765{0:03d}".format(number))
                except Exception:
                    pass
                time.sleep(0.4)
                print("  {0:>7}  {1:>8} {2:>6} {3:>12}".format(
                    number, len(browser.contexts),
                    sum(len(c.pages) for c in browser.contexts),
                    open_connections()))
            after = (len(browser.contexts),
                     sum(len(c.pages) for c in browser.contexts))
            print()
            check("No context accumulation across {0} failing lookups"
                  .format(LOOKUPS),
                  after[0] <= baseline[0],
                  "{0} -> {1}".format(baseline[0], after[0]))
            check("No page accumulation either",
                  after[1] <= baseline[1],
                  "{0} -> {1}".format(baseline[1], after[1]))
            # One connection is the caller's own page. The claim is that the
            # count does not climb with the number of shipments.
            check("Connections to the carrier stay flat, at most the one the "
                  "caller's page needs",
                  open_connections() <= 2, "{0} open".format(open_connections()))
            check("...and the busiest moment never crowded the carrier",
                  LIVE["peak"] <= CAP + 1,
                  "peak {0}, cap {1}".format(LIVE["peak"], CAP))

            # ─────────────────────────────────────────────────────────
            # 3 · THE OPERATOR'S OWN BROWSER
            # ─────────────────────────────────────────────────────────
            print()
            print("=" * 74)
            print("3. EDGE STILL REACHES THE CARRIER WHILE THE RUN CONTINUES")
            print("=" * 74)
            operator, why = open_browser(playwright)
            if operator is None:
                skip("the operator's browser", why)
            else:
                try:
                    tab = operator.new_page()
                    reached, elapsed, error = False, None, None
                    started = time.time()
                    try:
                        tab.goto(URL, wait_until="commit", timeout=8000)
                        reached = "myCargo" in tab.content()
                    except Exception as problem:
                        error = str(problem).split("\n")[0][:100]
                    elapsed = int((time.time() - started) * 1000)
                    check("A separate browser opens the carrier while the "
                          "automation is running", reached,
                          error or "not the carrier page")
                    print("      it took {0}ms, with {1} connections open"
                          .format(elapsed, open_connections()))
                    check("...promptly, not after the edge stopped stalling it",
                          elapsed < TARPIT_SECONDS * 1000,
                          "{0}ms".format(elapsed))
                    check("Nothing was ever stalled for crowding",
                          LIVE["tarpitted"] == 0,
                          "{0} requests stalled".format(LIVE["tarpitted"]))

                    # ─────────────────────────────────────────────────
                    # 4 · CAN THIS TEST STILL SEE THE BUG?
                    # ─────────────────────────────────────────────────
                    # A regression test that passes because it cannot detect
                    # the fault is worth nothing. So the old behaviour is
                    # reproduced deliberately — a context per lookup, never
                    # closed — and the same operator request must fail.
                    print()
                    print("=" * 74)
                    print("4. THE SAME TEST STILL CATCHES THE OLD BEHAVIOUR")
                    print("=" * 74)
                    leaked = []
                    for _ in range(CAP + 3):
                        stray = browser.new_context()
                        strand = stray.new_page()
                        try:
                            strand.goto(URL, wait_until="commit", timeout=5000)
                        except Exception:
                            pass
                        leaked.append(stray)
                    time.sleep(0.6)
                    crowded = open_connections()
                    print("      {0} unclosed contexts hold {1} connections "
                          "to the carrier".format(len(leaked), crowded))
                    check("Unclosed contexts really do hold connections open",
                          crowded > CAP,
                          "{0} open, cap {1}".format(crowded, CAP))
                    blocked, slow = False, None
                    started = time.time()
                    try:
                        tab.goto(URL + "?again", wait_until="commit",
                                 timeout=6000)
                    except Exception:
                        blocked = True
                    slow = int((time.time() - started) * 1000)
                    check("...and the operator's browser then cannot get "
                          "through", blocked,
                          "it loaded anyway in {0}ms".format(slow))
                    # How quickly does the resource come back? This is the
                    # field observation in miniature: Edge worked again the
                    # moment the automation stopped.
                    for stray in leaked:
                        try:
                            stray.close()
                        except Exception:
                            pass
                    freed_at = time.time()
                    deadline = freed_at + TARPIT_SECONDS + 10
                    while open_connections() > 2 and time.time() < deadline:
                        time.sleep(0.25)
                    drained = int((time.time() - freed_at) * 1000)
                    # Most of this is the stub finishing its own stall; the
                    # browser's sockets go the moment close() returns.
                    print("      closing them freed the connections in {0}ms, "
                          "{1}s of which is the stub's own stall ({2} left)"
                          .format(drained, TARPIT_SECONDS, open_connections()))
                    check("Closing the contexts frees the resource, which is "
                          "why stopping the automation fixed it",
                          open_connections() <= 2,
                          "{0} still open after {1}ms".format(
                              open_connections(), drained))
                    recovered, again = False, None
                    started = time.time()
                    try:
                        tab.goto(URL + "?recovered", wait_until="commit",
                                 timeout=8000)
                        recovered = "myCargo" in tab.content()
                    except Exception as problem:
                        again = str(problem).split("\n")[0][:100]
                    check("...and the operator's browser gets through again "
                          "straight away", recovered, again or "still blocked")
                    print("      it took {0}ms the second time".format(
                        int((time.time() - started) * 1000)))
                finally:
                    try:
                        operator.close()
                    except Exception:
                        pass

            # ─────────────────────────────────────────────────────────
            # 5 · THE PAGE THE CALLER IS READING IS NEVER PULLED AWAY
            # ─────────────────────────────────────────────────────────
            print()
            print("=" * 74)
            print("5. A KEPT PAGE SURVIVES, AND IS RELEASED ONE LOOKUP LATER")
            print("=" * 74)
            kept_context = browser.new_context()
            kept = kept_context.new_page()
            A.AFKL_HELD_CONTEXTS.append(kept_context)
            A.release_afkl_helpers(keep_page=kept)
            check("The page the caller is reading stays open",
                  not kept.is_closed() and kept_context in A.AFKL_HELD_CONTEXTS)
            A.release_afkl_helpers(keep_page=page)
            time.sleep(0.3)
            check("...and the next lookup closes it",
                  kept.is_closed() and kept_context not in A.AFKL_HELD_CONTEXTS)
            check("Releasing twice is harmless", A.release_afkl_helpers() is None)
            browser.close()

server.shutdown()

print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(
    len(PASS), len(FAIL),
    ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
