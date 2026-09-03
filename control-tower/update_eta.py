import csv
import random
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import atexit
import json
import os
import threading
import tempfile

from playwright.sync_api import sync_playwright

# ------------------------------------------------------------
# CONTROL TOWER DASHBOARD (optional, never blocks the run)
# ------------------------------------------------------------
try:
    import sys as _sys

    _ROOT = Path(__file__).resolve().parent
    _sys.path.insert(0, str(_ROOT))

    try:
        # Normal layout: dashboard/ sits next to this script.
        from dashboard.bridge import bridge as tower
        from dashboard import server as tower_server
        from dashboard.control import control as tower_control
    except ImportError:
        # Flattened layout: bridge.py and server.py sit next to this script
        # (happens when a zip is extracted without its folders).
        _sys.path.insert(0, str(_ROOT / "dashboard"))
        from bridge import bridge as tower
        import server as tower_server
        from control import control as tower_control

    TOWER_AVAILABLE = True
except Exception as _tower_import_error:  # dashboard missing or broken
    TOWER_AVAILABLE = False
    # Only complain if a dashboard folder is actually present. Running
    # update_eta.py on its own is a supported setup, not a fault, so it should
    # not open with a warning about something you never installed.
    if (_ROOT / "dashboard").exists():
        print("=" * 62, flush=True)
        print("CONTROL TOWER DASHBOARD DID NOT LOAD", flush=True)
        print(
            "Reason: {0}: {1}".format(
                type(_tower_import_error).__name__, _tower_import_error
            ),
            flush=True,
        )
        print("Expected: {0}".format(_ROOT / "dashboard"), flush=True)
        print("Run check_dashboard.py from the automation root for a diagnosis.",
              flush=True)
        print("The automation itself will continue normally.", flush=True)
        print("=" * 62, flush=True)

    class _NoTower:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    tower = _NoTower()
    tower_server = _NoTower()
    tower_control = _NoTower()

# Set to False to run the automation without the dashboard.
DASHBOARD_ENABLED = True
DASHBOARD_PORT = 8787
DASHBOARD_OPEN_BROWSER = True

# ------------------------------------------------------------
# SHARING THE DASHBOARD
# ------------------------------------------------------------
# "127.0.0.1" -> only this machine can open the dashboard (default, safest).
# "0.0.0.0"   -> colleagues on the same network can open it too. The console
#                prints the exact links to send them when the run starts.
DASHBOARD_HOST = "0.0.0.0"

# Required in the URL as ?key=... once you leave loopback. The dashboard is
# read-only — it cannot start, stop or change the automation — but it does show
# live shipment references, carriers and dates, so put a key on it before
# opening the port. Leave as None to disable the check.
DASHBOARD_ACCESS_KEY = "mantrac2026"

# ------------------------------------------------------------
# DASHBOARD CONTROL
# ------------------------------------------------------------
# False keeps the dashboard strictly read-only — viewers can watch but cannot
# affect the run. Set True to let it pause, resume, stop and re-run individual
# shipments.
#
# Requests are never applied mid-shipment: the automation checks between
# shipments, so a click can never interrupt a write to the Hub. Anyone with the
# dashboard link gets these controls, so only enable it if that is what you
# want for the people you share with.
DASHBOARD_ALLOW_CONTROL = False


# ============================================================
# CONFIGURATION - DHL ONLY
# ============================================================

INTERNAL_URL = (
    "https://logisticshub.mantracgroup.com/"
    "WorkFlow/ShipmentTracking/ShipmentList.aspx"
)
DHL_BASE_URL = "https://www.dhl.com/eg-en/home/tracking.html"
QATAR_BASE_URL = "https://www.qrcargo.com/s/track-your-shipment"
QATAR_AWB_PREFIX = "157"

BASE_FOLDER = Path(r"C:\Automation")
CREDENTIALS_FILE = BASE_FOLDER / "credentials.txt"
RESULTS_FILE = BASE_FOLDER / "tracking_results.csv"
LOG_FOLDER = BASE_FOLDER / "logs"
SCREENSHOT_FOLDER = BASE_FOLDER / "screenshots"

TARGET_STATUS = "Under Clearance"
SOURCE_VIEW = "BU"
COE_VIEW = "COE"
BU_VIEW = "BU"
MAX_TABLE_PAGES = 10
MAX_RECORDS_PER_RUN = 200
# Playwright delays EVERY action by this much.
#
# Restored to the original 150ms. I had lowered this to 60 for speed, but your
# automation was working at 150 and this hub renders a dropdown menu on click —
# exactly the kind of interaction where shaving action delay changes behaviour.
# Proven-working beats faster. Lower it deliberately later if you want, once a
# full run is green.
SLOW_MO_MS = 150
BETWEEN_SHIPMENTS_MIN_SECONDS = 2
BETWEEN_SHIPMENTS_MAX_SECONDS = 4

DHL_IMMEDIATE_CHECK_SECONDS = 10

# How long DHL is allowed to stay in a non-terminal state (loading / carrier
# processing) before we give up on it. DHL genuinely takes ~30s on a bad day,
# so this is deliberately generous — it is a ceiling, not a target. The state
# machine exits the instant a terminal state is reached.
DHL_READY_MAX_SECONDS = 75

# If the page shows no progress at all for this long — same state, no new
# content, no processing banner — treat it as temporarily stuck rather than
# waiting out the full ceiling.
DHL_STUCK_AFTER_SECONDS = 25
# Was a blind wait_for_timeout(34s). It is now the MAXIMUM time we keep polling
# DHL for a dated Event Log row; the moment a date appears we move on, so a fast
# response costs ~1s instead of a guaranteed 34s.
DHL_RETRY_WAIT_SECONDS = 34
DHL_PROCESSING_TIMEOUT_SECONDS = 90
DHL_FINAL_RESULT_WAIT_SECONDS = 40

QATAR_RESULT_WAIT_SECONDS = 45
QATAR_RETRY_WAIT_SECONDS = 10
QATAR_MAX_ATTEMPTS = 2

# Cookie banners: how long we are willing to spend looking for one. Checking
# costs nothing when the banner is absent (see accept_cookie_banner).
COOKIE_BUDGET_SECONDS = 3

# Consent is an opening-moments concern. After this many seconds into the
# readiness wait we stop retrying it, so a persistent cookie notice in a footer
# can never eat the shipment-data budget.
DHL_COOKIE_WINDOW_SECONDS = 12
DHL_COOKIE_MAX_ATTEMPTS = 3

# Condition-based settle after a carrier page load, instead of a blind sleep.
PAGE_SETTLE_MAX_SECONDS = 6

# Playwright timeouts, named so their intent is readable at the call site and
# tunable in one place instead of 40-odd literals.
CLICK_TIMEOUT_MS = 5000        # a control we expect to be present
# The hub is ASP.NET WebForms: Manage, Save, Search and paging all fire
# __doPostBack. Playwright's click() waits for that navigation to finish inside
# its own timeout, so a slow postback raises even though the click landed.
# These clicks get a longer budget AND are treated as successful once the click
# itself is dispatched; the real confirmation is the condition wait that follows.
POSTBACK_CLICK_TIMEOUT_MS = 20000
NAVIGATION_TIMEOUT_MS = 60000  # a full page load over a slow link
READ_TIMEOUT_MS = 4000         # reading text we expect to exist
PROBE_TIMEOUT_MS = 800         # "is this here?" checks that may legitimately fail

# Ceilings for the internal Logistics Hub. These are MAXIMUMS, not targets:
# every wait below them exits the moment its condition is met. They are set
# well above the fixed sleeps they replace so a slow hub is handled better
# than before, not worse.
HUB_TABLE_REFRESH_MAX_MS = 12000   # replaces fixed 1200/1300ms table sleeps
HUB_FORM_READY_MAX_MS = 15000      # Manage form / tab panel after a postback
HUB_SAVE_MAX_MS = 8000             # replaces fixed 1500ms after Save
HUB_POLL_MS = 120                  # cheap: one DOM read per poll

# False means update and save the real internal ETA.
DRY_RUN = False

# Reload the Manage page after a save and confirm the value is actually there.
#
# The architecture map turned this up as the one real gap: save_manage_page()
# proves the POSTBACK completed (the Save control goes away, or the table comes
# back), which is not the same as proving the value persisted. Off by default
# because switching it on adds a reload to every successful write and so
# changes the shape of a run that already works. Turn it on with
# VERIFY_AFTER_SAVE=1, and for the real-hub E2E procedure.
VERIFY_AFTER_SAVE = (os.environ.get("VERIFY_AFTER_SAVE", "").strip().lower()
                     in ("1", "true", "yes", "on"))

# On an unexpected fatal error, keep Edge and CMD open so the exact problem
# remains visible instead of closing immediately.
PAUSE_ON_FATAL_ERROR = True

# Local deterministic intelligence only; no LLM or external AI service.

# ============================================================
# OUTPUT LOCATION
# ============================================================
#
# BASE_FOLDER is where outputs SHOULD go and is always tried first, so a
# correctly set up machine behaves exactly as before. If it is not writable we
# fall back rather than crash — but we say so loudly on the console and in the
# log itself. Logging is never disabled and nothing is swallowed silently.

def _probe_writable(folder):
    """
    Prove a folder is usable by actually creating it and writing a file.

    Returns None on success, or a human-readable reason it cannot be used.
    Checking os.access() is not enough on Windows, where ACLs and OneDrive
    can reject a write that access() reports as fine.
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except PermissionError as error:
        return "cannot create the folder (permission denied: {0})".format(error)
    except OSError as error:
        return "cannot create the folder ({0})".format(error)

    probe = folder / ".write_probe_{0}.tmp".format(os.getpid())
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("probe")
        probe.unlink()
        return None
    except PermissionError as error:
        return "folder exists but is not writable (permission denied: {0})".format(error)
    except OSError as error:
        return "folder exists but is not writable ({0})".format(error)


def _candidate_output_folders():
    """BASE_FOLDER first; everything after it is a fallback, in preference order."""
    candidates = [(BASE_FOLDER, "the configured BASE_FOLDER")]

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            (Path(local_app_data) / "MantracControlTower", "your local app data folder")
        )
    try:
        candidates.append((Path.home() / "MantracControlTower", "your home folder"))
    except Exception:
        pass
    candidates.append(
        (Path(tempfile.gettempdir()) / "MantracControlTower", "the system temp folder")
    )
    return candidates


def resolve_output_folder():
    """
    Return the first writable output folder, plus the list of rejections.

    Raises RuntimeError if every candidate fails — logging is never quietly
    turned off.
    """
    rejected = []
    for folder, description in _candidate_output_folders():
        reason = _probe_writable(folder)
        if reason is None:
            return folder, description, rejected
        rejected.append((folder, description, reason))

    lines = ["Could not find anywhere to write the run log."]
    for folder, description, reason in rejected:
        lines.append("  {0} ({1}): {2}".format(folder, description, reason))
    lines.append("")
    lines.append("Fix one of these, then run again:")
    lines.append("  - create C:\\Automation and give your account write access, or")
    lines.append("  - run this script from an account that can write there, or")
    lines.append("  - point BASE_FOLDER at a folder you own.")
    raise RuntimeError("\n".join(lines))


OUTPUT_FOLDER, OUTPUT_FOLDER_DESCRIPTION, OUTPUT_FOLDER_REJECTED = resolve_output_folder()

# Outputs follow whichever folder proved writable. CREDENTIALS_FILE is
# deliberately NOT moved: it is an input the operator places at a known path,
# and reading it needs no write permission.
if OUTPUT_FOLDER != BASE_FOLDER:
    RESULTS_FILE = OUTPUT_FOLDER / "tracking_results.csv"
    LOG_FOLDER = OUTPUT_FOLDER / "logs"
    SCREENSHOT_FOLDER = OUTPUT_FOLDER / "screenshots"

LOG_FOLDER.mkdir(parents=True, exist_ok=True)
SCREENSHOT_FOLDER.mkdir(parents=True, exist_ok=True)

# Microseconds AND the process id, so two runs started in the same second — or
# at the same instant on different processes — can never share a file.
LOG_FILE = LOG_FOLDER / "run_{0:%Y%m%d_%H%M%S_%f}_pid{1}.log".format(
    datetime.now(), os.getpid()
)


class SkipShipment(Exception):
    """Expected shipment skip; processing continues with the next shipment."""


# ============================================================
# GENERAL HELPERS
# ============================================================

# Anything matching these never reaches the log file. The dashboard bridge
# redacts separately; this protects the file on disk even when the dashboard is
# absent.
_SECRET_PATTERNS = [
    (re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1 [redacted]"),
    (re.compile(r"\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)"
                r"\s*[:=]\s*.+?(?=(?:[,;]|\s\s|$))", re.I), r"\1: [redacted]"),
]


_suppressed = {}


def note_suppressed(where, error, detail=""):
    """
    Record an exception that is deliberately not fatal.

    Best-effort probes legitimately fail — trying eight selectors means seven
    misses. Logging every one would bury the run log, so the first occurrence
    per site is logged and the rest are counted. Nothing is lost: the totals
    are reported at the end of the run.
    """
    key = str(where)
    seen = _suppressed.get(key, 0)
    _suppressed[key] = seen + 1
    if seen == 0:
        write_log(
            "Note: {0} did not succeed ({1}: {2}){3}. Continuing; further "
            "occurrences will be counted, not logged.".format(
                where, type(error).__name__, str(error)[:160],
                " [{0}]".format(detail) if detail else "",
            )
        )


def report_suppressed():
    """End-of-run summary so nothing suppressed is invisible."""
    if not _suppressed:
        return
    write_log("Suppressed, non-fatal conditions this run:")
    for where, count in sorted(_suppressed.items(), key=lambda kv: -kv[1]):
        write_log("  {0} x{1}".format(where, count))


def redact_secrets(message):
    for pattern, replacement in _SECRET_PATTERNS:
        message = pattern.sub(replacement, message)
    return message


class _RunLog:
    """
    Holds the run log open for the life of the process.

    Opening and closing on every line invited sharing violations on Windows
    (antivirus, OneDrive, Explorer preview). One handle, flushed per line, so a
    crash still leaves a complete log.

    If the handle ever fails — the classic case being an old file locked by
    another process — it rolls over to a fresh uniquely named file and says so.
    It never silently stops logging.
    """

    def __init__(self, path):
        self.path = path
        self.handle = None
        self.degraded = False
        self._open()

    def _open(self):
        self.handle = open(self.path, "a", encoding="utf-8", buffering=1)

    def _rollover(self, error):
        previous = self.path
        self.path = self.path.with_name(
            "{0}_rollover{1}{2}".format(
                self.path.stem, datetime.now().strftime("%H%M%S%f"), self.path.suffix
            )
        )
        try:
            if self.handle:
                try:
                    self.handle.close()
                except Exception:
                    pass
            self._open()
            notice = (
                "[{0:%Y-%m-%d %H:%M:%S}] LOGGING: {1} became unwritable ({2}). "
                "Continuing in {3}.".format(datetime.now(), previous, error, self.path)
            )
            print(notice, flush=True)
            self.handle.write(notice + "\n")
            return True
        except OSError as rollover_error:
            self.degraded = True
            print(
                "[{0:%Y-%m-%d %H:%M:%S}] LOGGING FAILED: cannot write to disk "
                "({1}). The run continues and every line is still printed to "
                "this console, but the log file is incomplete.".format(
                    datetime.now(), rollover_error
                ),
                flush=True,
            )
            return False

    def write(self, line):
        if self.degraded:
            return
        try:
            self.handle.write(line + "\n")
        except (OSError, ValueError) as error:
            if self._rollover(error):
                try:
                    self.handle.write(line + "\n")
                except OSError:
                    self.degraded = True

    def close(self):
        try:
            if self.handle:
                self.handle.close()
        except Exception:
            pass


_run_log = _RunLog(LOG_FILE)
atexit.register(_run_log.close)


def shipment_log(reference, message, carrier=None, level="INFO"):
    """
    One line, one shipment, always the same shape:

        [INFO] Shipment 157-49568713 | Carrier=DHL | Tracking data detected

    Everything the operator needs to grep a single shipment out of a busy run.
    """
    parts = ["Shipment {0}".format(reference or "unknown")]
    if carrier:
        parts.append("Carrier={0}".format(carrier))
    parts.append(str(message))
    write_log("[{0}] {1}".format(level, " | ".join(parts)))


def write_log(message):
    message = redact_secrets(str(message))
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    _run_log.write(line)
    tower.log(message)


def safe_filename(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def take_screenshot(page, bol_awb, suffix):
    path = SCREENSHOT_FOLDER / f"{safe_filename(bol_awb)}_{suffix}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        write_log(f"Screenshot saved: {path}")
    except Exception as error:
        write_log(f"Screenshot failed: {error}")
    return path


def save_page_text(page, bol_awb, suffix):
    path = LOG_FOLDER / f"{safe_filename(bol_awb)}_{suffix}.txt"
    try:
        text = page.locator("body").inner_text(timeout=5000)
        path.write_text(text, encoding="utf-8")
        write_log(f"Page text saved: {path}")
    except Exception as error:
        write_log(f"Page text save failed: {error}")
    return path


def _is_postback_navigation_wait(error):
    """
    True when Playwright timed out waiting for a postback, not on the click.

    The call log ends with "click action done / waiting for scheduled
    navigations to finish" — the button WAS pressed. Treating that as a failure
    aborted shipments whose Manage page was already opening.
    """
    text = str(error).lower()
    return (
        "scheduled navigation" in text
        or "click action done" in text
        or ("timeout" in text and "navigation" in text)
    )


def click_postback(locator, description, timeout_ms=None):
    """
    Click a control that triggers an ASP.NET postback.

    Returns normally once the click has been dispatched. It deliberately does
    NOT try to confirm the outcome — every caller already waits on a real
    condition (the Manage form appearing, the results table changing), which is
    stronger evidence than Playwright's navigation heuristic.
    """
    budget = POSTBACK_CLICK_TIMEOUT_MS if timeout_ms is None else timeout_ms

    try:
        locator.click(timeout=budget, no_wait_after=True)
        return
    except TypeError:
        # Playwright build without no_wait_after on click; fall through.
        pass
    except Exception as error:
        if _is_postback_navigation_wait(error):
            write_log(f"{description}: click landed, postback still in flight.")
            return
        note_suppressed(f"{description} (first attempt)", error)

    try:
        locator.click(timeout=budget)
        return
    except Exception as error:
        if _is_postback_navigation_wait(error):
            write_log(f"{description}: click landed, postback still in flight.")
            return
        note_suppressed(f"{description} (retry)", error)

    # Last resort: force past any overlay, then let the caller's condition wait
    # decide whether it actually worked.
    locator.click(timeout=budget, force=True)


def first_visible(candidates, timeout_ms=2500):
    for candidate in candidates:
        try:
            locator = candidate.first
            locator.wait_for(state="visible", timeout=timeout_ms)
            return locator
        except Exception:
            continue
    return None


# ============================================================
# COOKIE CONSENT
# ============================================================

# DHL and Qatar both run OneTrust. Id selectors are checked first because they
# are exact and instant; the generic role/text probes are the fallback for when
# the carrier restyles the banner.
# Consent widgets differ per carrier. AFKL myCargo, for example, shows a small
# panel closed with an X rather than an "Accept all" button, so the close
# controls are included alongside the accept ones.
COOKIE_SELECTORS = [
    # Close buttons first: several carrier panels (AFKL myCargo among them)
    # only offer an X, with no accept button at all.
    "button[aria-label*='close' i][class*='cookie' i]",
    "[class*='cookie' i] button[aria-label*='close' i]",
    "[class*='consent' i] button[aria-label*='close' i]",
    "[id*='cookie' i] button[aria-label*='Close' i]",
    "[class*='cookie' i] button[class*='close' i]",
    "[class*='consent' i] button[class*='close' i]",
    "[class*='cookie' i] [role='button'][aria-label*='close' i]",
    "[class*='cookie' i] svg[class*='close' i]",
    "[class*='cookie-notice' i] button",
    "[class*='cookiebar' i] button",
    "#onetrust-accept-btn-handler",
    "#accept-recommended-btn-handler",
    "button#truste-consent-button",
    "button[aria-label*='Accept All' i]",
    "button[aria-label*='Accept all cookies' i]",
    "button[title*='Accept All' i]",
    ".onetrust-close-btn-handler",
    "#cookie-accept",
    "button[data-testid*='accept' i]",
]

COOKIE_TEXTS = [
    r"Only necessary",
    r"Necessary only",
    r"Accept necessary",
    r"Accept All Cookies",
    r"Accept all cookies",
    r"Accept All",
    r"Allow all",
    r"I Accept",
    r"Agree",
]


# One comma-joined CSS query instead of nine separate ones. Every locator call
# is an IPC round-trip to the browser, so this is 1 round-trip per frame per
# poll rather than 9 — the difference between a few milliseconds and a couple
# of seconds of pure chatter on pages that have no banner.
COOKIE_SELECTOR_QUERY = ", ".join(COOKIE_SELECTORS)


def _click_cookie_button(scope):
    """Try one frame. Returns the selector that worked, or None."""
    try:
        combined = scope.locator(COOKIE_SELECTOR_QUERY).first
        if combined.count() > 0 and combined.is_visible():
            combined.click(timeout=2000)
            return "css-consent-button"
    except Exception:
        pass

    # A bare X inside a cookie panel, matched by symbol rather than by label.
    for symbol in ("\u00d7", "\u2715", "\u2716", "X"):
        try:
            locator = scope.locator(
                "[class*='cookie' i] button, [class*='consent' i] button"
            ).filter(has_text=re.compile(r"^\s*{0}\s*$".format(re.escape(symbol)))).first
            if locator.count() and locator.is_visible():
                locator.click(timeout=2000)
                return "cookie close [{0}]".format(symbol)
        except Exception:
            continue

    for pattern in COOKIE_TEXTS:
        try:
            locator = scope.get_by_role(
                "button", name=re.compile(pattern, re.I)
            ).first
            if locator.count() == 0:
                continue
            if not locator.is_visible():
                continue
            locator.click(timeout=2000)
            return "role=button[{0}]".format(pattern)
        except Exception:
            continue

    return None


def accept_cookie_banner(page, site_label, budget_seconds=None):
    """
    Dismiss a cookie-consent banner if one is showing.

    Costs almost nothing when no banner exists: every probe is a count() check,
    never a blocking wait. Banners injected late (OneTrust usually lands a
    second or two after DOMContentLoaded) are caught by re-probing until the
    budget expires. Never raises, never blocks the run.
    """
    budget = COOKIE_BUDGET_SECONDS if budget_seconds is None else budget_seconds
    deadline = time.time() + budget
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        scopes = [page]
        try:
            # Consent widgets are often inside an iframe.
            scopes.extend(page.frames)
        except Exception:
            pass

        for scope in scopes:
            try:
                hit = _click_cookie_button(scope)
            except Exception as error:
                note_suppressed("cookie banner click", error)
                hit = None
            if hit:
                write_log(
                    "{0}: cookie consent banner detected and accepted "
                    "(via {1}, {2:.1f}s after load).".format(
                        site_label, hit, budget - (deadline - time.time())
                    )
                )
                try:
                    tower.step("Accepted {0} cookie banner".format(site_label))
                except Exception:
                    pass
                # Let the overlay tear down before anything else is clicked.
                page.wait_for_timeout(300)
                return True

        # Nothing yet. Short re-probe in case the banner is still being injected.
        page.wait_for_timeout(400)

    return False


# ============================================================
# CONDITION-BASED WAITING
# ============================================================

def wait_until_settled(page, ready_check, max_seconds=None, poll_ms=250):
    """
    Return as soon as ready_check(page) is true, or when the budget runs out.

    This replaces blind sleeps after navigation. A page that renders in 400ms
    costs 400ms instead of a fixed 2.2s.
    """
    budget = PAGE_SETTLE_MAX_SECONDS if max_seconds is None else max_seconds
    deadline = time.time() + budget

    while time.time() < deadline:
        try:
            if ready_check(page):
                return True
        except Exception as error:
            note_suppressed("readiness check", error)
        page.wait_for_timeout(poll_ms)

    return False


def page_has_content(page):
    """Cheap readiness signal: the body has rendered something substantial."""
    try:
        return len(page.locator("body").inner_text(timeout=1200).strip()) > 200
    except Exception:
        return False


# ============================================================
# FAILURE CLASSIFICATION
# ============================================================

SUCCESS = "SUCCESS"
NO_RESULT = "NO RESULT"
TIMEOUT = "TIMEOUT"
TEMPORARY_WEBSITE_ISSUE = "TEMPORARY WEBSITE ISSUE"
AUTHENTICATION_ISSUE = "AUTHENTICATION ISSUE"
UNEXPECTED_PAGE_STATE = "UNEXPECTED PAGE STATE"
# A transport failure reaching the carrier is its own thing. It used to fall
# through to NO RESULT, which reads as "the carrier has no such air waybill" —
# a statement about the shipment made on the strength of a broken connection.
AFKL_NAVIGATION_ERROR = "AFKL NAVIGATION ERROR"
FAILED = "FAILED"

# Only these are worth a second attempt. Everything else is permanent for this
# run and retrying just wastes a browser round-trip.
RETRYABLE = {TIMEOUT, TEMPORARY_WEBSITE_ISSUE, UNEXPECTED_PAGE_STATE}


def classify_failure(error):
    """Map an exception onto one of the named operational outcomes."""
    text = "{0} {1}".format(type(error).__name__, error).casefold()

    # Checked FIRST: the message carries its own verdict, and it must not be
    # re-read as "no result" just because it also mentions a carrier.
    if "afkl navigation error" in text or "afkl_navigation_error" in text:
        return AFKL_NAVIGATION_ERROR
    if "no estimated" in text or "returned no" in text or "did not provide" in text:
        return NO_RESULT
    if "login" in text or "credential" in text or "sign in" in text or "unauthor" in text:
        return AUTHENTICATION_ISSUE
    if "timeout" in text or "timed out" in text or "did not finish within" in text:
        return TIMEOUT
    if any(word in text for word in
           ("net::", "err_", "connection", "socket", "dns", "502", "503", "504",
            "temporarily", "processing did not")):
        return TEMPORARY_WEBSITE_ISSUE
    if "was not found" in text or "no such element" in text or "not visible" in text:
        return UNEXPECTED_PAGE_STATE
    return FAILED


def log_operation_failure(carrier, reference, operation, error,
                          attempt, max_attempts, outcome, final):
    """One structured line per failure. Nothing is swallowed."""
    write_log(
        "FAILURE | carrier={0} | reference={1} | operation={2} | outcome={3} | "
        "attempt={4}/{5} | final={6} | reason={7}".format(
            carrier or "unknown",
            reference or "unknown",
            operation,
            outcome,
            attempt,
            max_attempts,
            "yes" if final else "no (will retry)",
            str(error).replace("\n", " ")[:400],
        )
    )


def run_with_retry(operation_name, carrier, reference, call,
                   max_attempts=2, base_delay_seconds=2):
    """
    Run `call`, retrying only when the failure class says a retry could help.

    Backoff is exponential (2s, 4s, ...) so a carrier having a bad moment is
    given room, while a permanent failure such as NO RESULT exits immediately.
    """
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            result = call()
            if attempt > 1:
                write_log(
                    "{0} succeeded for {1} on attempt {2}.".format(
                        operation_name, reference, attempt
                    )
                )
            return result
        except Exception as error:
            last_error = error
            outcome = classify_failure(error)
            retryable = outcome in RETRYABLE and attempt < max_attempts
            log_operation_failure(
                carrier, reference, operation_name, error,
                attempt, max_attempts, outcome, final=not retryable,
            )
            if not retryable:
                raise
            delay = base_delay_seconds * (2 ** (attempt - 1))
            write_log(
                "{0} is retryable ({1}). Backing off {2}s before attempt {3}.".format(
                    operation_name, outcome, delay, attempt + 1
                )
            )
            time.sleep(delay)

    raise last_error


# ============================================================
# THE OPTIONAL LEARNING LAYER
# ============================================================
#
# Imported defensively. If the ml package is absent, broken, or half-installed
# the automation runs exactly as it did before it existed — that is the whole
# contract, and it is enforced here rather than trusted to the package.
#
# With ML_ENABLED off (the default) the predictor answers "no opinion" to
# everything, so an enabled install with no model behaves identically to one
# with no package at all.

try:
    from ml import config as ml_config
    from ml import features as ml_features
    from ml import predictor as ml_predictor
    from ml import telemetry as ml_telemetry
    ML_AVAILABLE = True
except Exception as _ml_import_error:      # pragma: no cover - environment
    ML_AVAILABLE = False
    ml_config = ml_features = ml_predictor = ml_telemetry = None
    _ML_IMPORT_ERROR = str(_ml_import_error)


# Set once at startup by main(). True only when the switch is on AND a valid
# model actually loaded — the switch alone changes nothing.
ML_ACTIVE = False


def ml_context(**kwargs):
    """A feature context, or None when the layer is not available."""
    if not ML_AVAILABLE:
        return None
    try:
        return ml_features.context(**kwargs)
    except Exception:
        return None


def ml_record(context, strategy, success, duration_ms=None,
              category="OK", detail="", reference=None, rank=None):
    """
    Record one interaction. Never raises, never changes control flow.

    Telemetry is collected even when ML_ENABLED is off — you cannot train a
    model without data, and collecting it does not alter a single decision.
    """
    if not ML_AVAILABLE:
        return
    try:
        ml_telemetry.interaction(
            context or {}, strategy, success, duration_ms=duration_ms,
            category=category, detail=detail, reference=reference,
            rank=rank, redactor=redact_secrets)
    except Exception:
        pass


def ml_category(error):
    """The fine-grained telemetry category for an exception."""
    if not ML_AVAILABLE:
        return "OK"
    try:
        return ml_telemetry.classify_exception(error)
    except Exception:
        return "OK"


def ml_order(named_candidates, context):
    """
    Reorder a list of (name, locator) pairs using the model.

    Returns the pairs in the order to try them, and the name of the strategy
    the model put first (or None when it had no opinion). The SET of
    candidates is never changed — only their order — so every selector-level
    ETA/ATA guard the caller built still applies exactly as written.
    """
    if not ML_AVAILABLE or context is None or len(named_candidates) < 2:
        return named_candidates, None
    try:
        names = [name for name, _ in named_candidates]
        recommendation = ml_predictor.recommend_strategy(
            context, names, log=write_log)
        if not recommendation.used:
            return named_candidates, None
        by_name = dict(named_candidates)
        reordered = [(name, by_name[name]) for name in recommendation.order
                     if name in by_name]
        if len(reordered) != len(named_candidates):
            return named_candidates, None
        return reordered, recommendation.top
    except Exception as error:
        note_suppressed("asking the model for a candidate order", error)
        return named_candidates, None


def ml_wait_budget(context, default_ms, floor_ms=500):
    """
    A wait budget for this context. Never longer than `default_ms`.

    The call site's own constant remains the ceiling; the model can only ever
    propose something shorter, and only from observed successful waits.
    """
    if not ML_AVAILABLE or context is None:
        return default_ms
    try:
        budget, _reason = ml_predictor.recommend_wait(
            context, default_ms, floor_ms=floor_ms, log=write_log)
        return budget
    except Exception:
        return default_ms


# ============================================================
# HUB READINESS
# ============================================================

def table_signature(page):
    """
    Cheap fingerprint of the results table: row count plus the head of its
    text. One DOM read, no per-cell locator traffic.

    Returns None when no table is present, which is itself a usable state
    (it means the old table has gone and the new one has not arrived).
    """
    try:
        table = find_shipments_table(page)
        if table is None:
            return None
        text = table.inner_text(timeout=1500)
        rows = table.locator("tr").count()
        return "{0}:{1}".format(rows, text[:400])
    except Exception:
        return None


def wait_for_table_change(page, before, max_ms=None, reason=""):
    """
    Wait until the results table is different from `before`, then until it is
    visible.

    Why not just wait_for(visible): the previous table is still on screen at
    the moment of the click, so visibility is already true and proves nothing.
    Comparing content is what actually establishes that the hub responded.

    Falls back to the plain visibility wait if the signature never changes —
    behaviour is then identical to before this patch, never worse.
    """
    ceiling = HUB_TABLE_REFRESH_MAX_MS if max_ms is None else max_ms
    waited = 0

    while waited < ceiling:
        current = table_signature(page)
        if current is not None and current != before:
            if waited > 1500:
                write_log(
                    "Hub table refreshed after {0}ms{1}.".format(
                        waited, " ({0})".format(reason) if reason else ""
                    )
                )
            return True
        page.wait_for_timeout(HUB_POLL_MS)
        waited += HUB_POLL_MS

    write_log(
        "Hub table did not visibly change within {0}ms{1}; "
        "falling back to a visibility check.".format(
            ceiling, " ({0})".format(reason) if reason else ""
        )
    )
    return False


def wait_for_any(page, checks, max_ms, poll_ms=None, reason=""):
    """
    Return the name of the first satisfied condition, or None on timeout.

    `checks` is a list of (name, callable). Used where the right readiness
    signal is one of several possibilities.
    """
    poll = HUB_POLL_MS if poll_ms is None else poll_ms
    waited = 0
    while waited < max_ms:
        for name, check in checks:
            try:
                if check():
                    return name
            except Exception:
                continue
        page.wait_for_timeout(poll)
        waited += poll
    if reason:
        write_log("Timed out after {0}ms waiting for {1}.".format(max_ms, reason))
    return None


def manage_form_ready(page):
    """
    The Manage page is usable once a VISIBLE editable field exists.

    This used to take `.first` and ask whether it was visible. On the tabbed
    Modify Shipment page the inactive panel's inputs come first in the DOM, so
    `.first` was a hidden input and this returned False forever — which is why
    waiting for the BU panel timed out after 8s even though the panel was full
    of date fields. `:visible` filters at the selector, so `.first` is the
    first VISIBLE match.
    """
    try:
        return page.locator(
            "input[type='date']:visible, input[id*='ETA' i]:visible, "
            "input[id*='ATA' i]:visible, input[name*='ETA' i]:visible, "
            "input[name*='ATA' i]:visible, input[type='text']:visible"
        ).count() > 0
    except Exception:
        return False


def honour_control_requests():
    """
    Apply any dashboard request. Called only BETWEEN shipments, never during
    one, so a pause or stop can never split a Hub write.

    Returns "stop" when the run should end, otherwise None.
    """
    if not DASHBOARD_ALLOW_CONTROL:
        return None

    try:
        if tower_control.should_stop():
            write_log("Stop requested from the dashboard. Finishing this run.")
            return "stop"

        announced = False
        while tower_control.is_paused():
            if not announced:
                write_log("Paused from the dashboard. Waiting for Resume...")
                tower.step("Paused from the dashboard")
                announced = True
            time.sleep(1.0)
            if tower_control.should_stop():
                write_log("Stop requested while paused. Finishing this run.")
                return "stop"
        if announced:
            write_log("Resumed from the dashboard.")
    except Exception as error:
        note_suppressed("handling a dashboard control request", error)

    return None


def wait_between_shipments():
    seconds = random.randint(
        BETWEEN_SHIPMENTS_MIN_SECONDS,
        BETWEEN_SHIPMENTS_MAX_SECONDS,
    )
    tower.step(f"Cooling down {seconds}s before the next shipment")
    write_log(f"Waiting {seconds} seconds before the next shipment...")
    time.sleep(seconds)


# ============================================================
# CREDENTIALS AND RESULTS
# ============================================================

def load_credentials():
    if not CREDENTIALS_FILE.exists():
        raise Exception(f"Missing credentials file: {CREDENTIALS_FILE}")

    values = {}
    with open(CREDENTIALS_FILE, "r", encoding="utf-8-sig") as credentials_file:
        for raw_line in credentials_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip().upper()] = value.strip()

    username = values.get("USERNAME")
    password = values.get("PASSWORD")

    if not username or not password:
        raise Exception("credentials.txt must contain USERNAME= and PASSWORD= lines.")

    return username, password


def save_result(shipment, provider_result, action, result, details=""):
    file_exists = RESULTS_FILE.exists()
    fields = [
        "Run_Time",
        "Table_Page",
        "BOL_AWB",
        "Carrier",
        "Provider",
        "Existing_ETA",
        "Provider_Status",
        "Provider_ETA",
        "Provider_ATA",
        "COE_ETA_Action",
        "BU_ATA_Action",
        "Internal_Action",
        "Result",
        "Details",
    ]

    with open(RESULTS_FILE, "a", newline="", encoding="utf-8-sig") as result_file:
        writer = csv.DictWriter(result_file, fieldnames=fields)
        if not file_exists:
            writer.writeheader()

        writer.writerow(
            {
                "Run_Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Table_Page": shipment.get("table_page", ""),
                "BOL_AWB": shipment.get("bol_awb", ""),
                "Carrier": shipment.get("carrier", ""),
                "Provider": provider_result.get("provider", ""),
                "Existing_ETA": shipment.get("current_eta", ""),
                "Provider_Status": provider_result.get("tracking_status", ""),
                "Provider_ETA": provider_result.get("eta", ""),
                "Provider_ATA": provider_result.get("ata", ""),
                "COE_ETA_Action": action.get("coe", "") if isinstance(action, dict) else "",
                "BU_ATA_Action": action.get("bu", "") if isinstance(action, dict) else "",
                "Internal_Action": str(action),
                "Result": result,
                "Details": details,
            }
        )


# ============================================================
# DATE EXTRACTION
# ============================================================

def normalize_date(raw_date):
    value = re.sub(r"\s+", " ", raw_date).strip().replace(",", "")
    value = re.sub(
        r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+",
        "",
        value,
        flags=re.I,
    )

    formats = [
        "%B %d %Y",
        "%b %d %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%d-%B-%Y",
        "%d-%b-%Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
    ]

    for date_format in formats:
        try:
            return datetime.strptime(value, date_format).strftime("%d/%m/%Y")
        except ValueError:
            continue

    return None


MONTH_NUMBERS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def year_for_dayless_date(day, month, reference=None):
    """
    Choose the year for a date printed without one.

    AFKL myCargo writes "04 SEP 20:15" and nothing else, so the year has to be
    inferred. Taking the current year is wrong across a year boundary: a page
    read on 2 January showing "28 DEC" belongs to the year just gone. Pick
    whichever candidate year lands nearest today.
    """
    reference = reference or datetime.now()
    best = None
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidate = datetime(year, month, day)
        except ValueError:
            continue                      # 29 Feb in a non-leap year
        if best is None or abs((candidate - reference).days) < abs(
                (best - reference).days):
            best = candidate
    return best


def extract_all_dates(text, allow_yearless=False):
    """
    Every date in `text`, as (position, dd/mm/yyyy, raw).

    `allow_yearless` is opt-in and off by default. DHL and Qatar both print
    full dates, and letting a bare "04 SEP" match there would turn flight
    numbers and reference codes into dates. Only the AFKL reader asks for it,
    because that page never prints a year at all — which is the reason
    057-05765454 was skipped with "no dates on the page" while its result was
    sitting on screen.
    """
    months = (
        "January|February|March|April|May|June|July|August|September|October|"
        "November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    )

    patterns = [
        rf"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*,?\s*"
        rf"(?:{months})\s+\d{{1,2}},?\s+\d{{4}}",
        rf"\d{{1,2}}\s+(?:{months})\s+\d{{4}}",
        r"\d{1,2}[/-]\d{1,2}[/-]\d{4}",
        r"\d{4}-\d{1,2}-\d{1,2}",
    ]

    results = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            parsed = normalize_date(match.group(0))
            if parsed:
                results.append((match.start(), parsed, match.group(0)))

    if allow_yearless:
        # Spans already claimed by a full date. A year-less pattern must not
        # re-read part of one, and — the bug this guards — the two year-less
        # patterns must not read each other: in "04 SEP 20:15" the day-first
        # pattern takes "04 SEP" and the month-first pattern would then take
        # "SEP 20", turning 4 September into 20 September. Day-first runs
        # first because that is the format these pages print.
        taken = [(start, start + len(raw)) for start, _, raw in results]
        yearless = [
            rf"\b(\d{{1,2}})\s+({months})\b(?!\s*,?\s*\d{{4}})",
            rf"\b({months})\s+(\d{{1,2}})\b(?!\s*,?\s*\d{{4}})",
        ]
        for index, pattern in enumerate(yearless):
            for match in re.finditer(pattern, text, flags=re.I):
                if any(match.start() < end and start < match.end()
                       for start, end in taken):
                    continue
                first, second = match.group(1), match.group(2)
                day_text, month_text = (first, second) if index == 0 else (second, first)
                month = MONTH_NUMBERS.get(month_text[:3].lower())
                try:
                    day = int(day_text)
                except ValueError:
                    continue
                if not month or not 1 <= day <= 31:
                    continue
                resolved = year_for_dayless_date(day, month)
                if resolved:
                    results.append((match.start(),
                                    resolved.strftime("%d/%m/%Y"),
                                    match.group(0)))
                    taken.append((match.start(), match.end()))

    results.sort(key=lambda item: item[0])
    return results


def extract_date_near_labels(text, labels, radius=900):
    dates = extract_all_dates(text)
    matches = []

    for label in labels:
        for label_match in re.finditer(label, text, flags=re.I):
            for date_position, parsed, raw in dates:
                distance = abs(date_position - label_match.start())
                if distance <= radius:
                    matches.append((distance, parsed, raw, label))

    if not matches:
        return None

    matches.sort(key=lambda item: item[0])
    distance, parsed, raw, label = matches[0]
    write_log(f"DHL date near '{label}': {raw} -> {parsed} (distance {distance})")
    return parsed


def extract_date_after_label(text, labels, radius=260):
    """
    Read the date that FOLLOWS a label, not merely the nearest one.

    extract_date_near_labels() uses absolute distance, which is right for DHL's
    event log where the date leads the row. In a "label: value" layout it is
    wrong: on an AFKL result the ETA's date sits 15 characters before
    "Actual Time of Arrival" while the real ATA sits 28 characters after, so
    absolute distance returns the estimate as the actual — the same date
    reported twice, which is worse than reporting nothing.

    Falls back to the nearest-date rule when nothing follows a label, so a
    layout that puts the value first still works.
    """
    dates = extract_all_dates(text)
    forward = []

    for label in labels:
        for label_match in re.finditer(label, text, flags=re.I):
            end = label_match.end()
            for date_position, parsed, raw in dates:
                if date_position < end:
                    continue
                distance = date_position - end
                if distance <= radius:
                    forward.append((distance, parsed, raw, label))

    if forward:
        forward.sort(key=lambda item: item[0])
        distance, parsed, raw, label = forward[0]
        write_log(
            f"Date after '{label}': {raw} -> {parsed} (distance {distance})")
        return parsed

    return extract_date_near_labels(text, labels, radius)


def latest_date(date_values):
    valid_dates = []

    for date_value in date_values:
        if not date_value:
            continue
        try:
            parsed = datetime.strptime(date_value, "%d/%m/%Y")
            valid_dates.append((parsed, date_value))
        except ValueError:
            continue

    if not valid_dates:
        return None

    valid_dates.sort(key=lambda item: item[0], reverse=True)
    return valid_dates[0][1]


# ============================================================
# INTERNAL LOGIN
# ============================================================

def login_internal(page, username, password):
    write_log("Opening internal website...")
    page.goto(INTERNAL_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)

    if not page.url.startswith("chrome-error"):
        write_log("Internal authentication succeeded.")
        return

    username_field = first_visible(
        [
            page.get_by_label(re.compile(r"email|username|user name", re.I)),
            page.locator("input[type='email']"),
            page.locator("input[name*='user' i]"),
            page.locator("input[id*='user' i]"),
        ]
    )

    password_field = first_visible(
        [
            page.get_by_label(re.compile(r"password", re.I)),
            page.locator("input[type='password']"),
            page.locator("input[name*='pass' i]"),
            page.locator("input[id*='pass' i]"),
        ]
    )

    if username_field is None or password_field is None:
        raise Exception("Automatic internal login fields were not found.")

    username_field.fill(username)
    password_field.fill(password)

    login_button = first_visible(
        [
            page.get_by_role("button", name=re.compile(r"sign in|login|log in", re.I)),
            page.locator("button[type='submit']"),
            page.locator("input[type='submit']"),
        ]
    )

    if login_button is not None:
        login_button.click(timeout=5000)
    else:
        password_field.press("Enter")

    page.wait_for_timeout(2000)
    page.goto(INTERNAL_URL, wait_until="domcontentloaded", timeout=60000)


# ============================================================
# INTERNAL NAVIGATION, FILTER, TABLE, PAGINATION
# ============================================================

def find_shipments_table(page):
    tables = page.locator("table")
    for index in range(tables.count()):
        table = tables.nth(index)
        try:
            text = table.inner_text(timeout=1500).casefold()
            if "bol/awb" in text and "carrier" in text:
                return table
        except Exception:
            continue
    raise Exception("Centralized Shipments Tracking table was not found.")


def click_centralized_shipments_tracking(page):
    """
    Open the Centralized Shipments Tracking feature first.

    The COE and BU view options are displayed only after clicking the yellow
    'Centralized Shipments Tracking' item in the top navigation bar.
    """
    navigation_pattern = re.compile(
        r"Centralized\s+Shipments?\s+Tracking",
        re.I,
    )

    navigation_control = first_visible(
        [
            page.get_by_role("link", name=navigation_pattern),
            page.get_by_role("button", name=navigation_pattern),
            page.get_by_text(navigation_pattern, exact=False),
            page.locator("a").filter(has_text=navigation_pattern),
            page.locator("button").filter(has_text=navigation_pattern),
        ],
        5000,
    )

    if navigation_control is None:
        # The browser may already be inside the feature after a direct URL
        # navigation. In that case, accept the page only if the shipment table
        # or one of the COE/BU options is already visible.
        feature_visible = False
        try:
            feature_visible = find_shipments_table(page).is_visible(timeout=1500)
        except Exception:
            pass

        if not feature_visible:
            for option_pattern in [
                re.compile(r"COE.*Shipment", re.I),
                re.compile(r"BU.*Shipment", re.I),
            ]:
                try:
                    if page.get_by_text(option_pattern, exact=False).first.is_visible(timeout=800):
                        feature_visible = True
                        break
                except Exception:
                    continue

        if feature_visible:
            write_log("Centralized Shipments Tracking feature is already open.")
            return

        raise Exception("Centralized Shipments Tracking navigation option was not found.")

    try:
        navigation_control.click(timeout=5000)
    except Exception:
        navigation_control.click(timeout=5000, force=True)

    # REGRESSION FIX. This wait previously accepted "the results table is
    # visible" as proof the menu had opened. That table is already on screen
    # from the previous page, so the condition was true immediately, and
    # select_shipments_view then searched a dropdown that had not rendered —
    # which is why COE was reported missing and every shipment failed.
    #
    # Clicking this nav opens a DROPDOWN. The only honest evidence it opened
    # is a menu entry being visible, so that is what we wait for.
    # The trailing guard keeps the Manage page's "COE Shipment Info" tab from
    # being mistaken for a nav menu entry.
    menu_entry = re.compile(
        r"(?:BU|COE)\s*[-–—]?\s*(?:Pending\s+)?Shipments?\b(?!\s*Info)", re.I
    )

    opened = wait_for_any(
        page,
        [
            ("menu entry", lambda: page.get_by_text(menu_entry).first.is_visible(
                timeout=300)),
            ("view control", lambda: page.locator(
                "select[id*='View' i], select[name*='View' i]"
            ).first.count() > 0),
        ],
        HUB_TABLE_REFRESH_MAX_MS,
        reason="the Centralized Shipments Tracking menu to open",
    )

    if opened is None:
        # Nothing proved the menu opened. Fall back to the original grace
        # period rather than racing ahead into a closed dropdown.
        page.wait_for_timeout(1500)
        write_log(
            "Centralized Shipments Tracking clicked, but no menu entry became "
            "visible; used the original 1500ms grace period."
        )
    else:
        write_log("Centralized Shipments Tracking opened ({0} visible).".format(opened))


def view_pattern(view_name):
    """
    Match the nav-menu entry for a shipments view.

    The live menu reads:
        BU - Shipments        BU - Pending Shipments
        COE - Shipments       COE - Pending Shipments

    "Pending Shipments" is a DIFFERENT list and must never be selected in
    place of the plain one, so the pattern excludes it explicitly. The old
    pattern ended in a bare `|COE` alternative that would happily match
    "COE - Pending Shipments" — and match the menu's own heading too.
    """
    prefix = COE_VIEW if view_name.upper() == COE_VIEW else BU_VIEW
    return re.compile(
        r"^\s*{0}\s*[-–—]?\s*Shipments?(?:\s+View)?\s*$".format(prefix),
        re.I,
    )


def view_pattern_loose(view_name):
    """Fallback for layouts that wrap or decorate the label."""
    prefix = COE_VIEW if view_name.upper() == COE_VIEW else BU_VIEW
    return re.compile(
        r"{0}\s*[-–—]?\s*Shipments?\b(?!\s*(?:Pending|Info))".format(prefix),
        re.I,
    )


def select_shipments_view(page, view_name):
    """
    Select COE or BU after opening Centralized Shipments Tracking.

    Supports:
      - normal links/buttons/tabs
      - a native select list
      - hidden menu items revealed by the top navigation
      - BU being the already-open/default table
    """
    requested = view_name.upper().strip()
    if requested not in {COE_VIEW, BU_VIEW}:
        raise Exception(f"Unsupported internal shipment view: {view_name}")

    exact_patterns = (
        [
            re.compile(r"^\s*COE\s*-\s*Shipment\s*$", re.I),
            re.compile(r"^\s*COE\s+Shipments?\s+View\s*$", re.I),
            re.compile(r"^\s*COE\s+Shipment\s*$", re.I),
        ]
        if requested == COE_VIEW
        else [
            re.compile(r"^\s*BU\s*-\s*Shipment\s*$", re.I),
            re.compile(r"^\s*BU\s+Shipments?\s+View\s*$", re.I),
            re.compile(r"^\s*BU\s+Shipment\s*$", re.I),
        ]
    )

    # 1. Native select/dropdown containing COE/BU options.
    selects = page.locator("select:visible")
    for select_index in range(selects.count()):
        select_control = selects.nth(select_index)
        try:
            options = select_control.locator("option")
            for option_index in range(options.count()):
                option = options.nth(option_index)
                option_text = re.sub(r"\s+", " ", option.inner_text()).strip()
                if any(pattern.fullmatch(option_text) for pattern in exact_patterns):
                    value = option.get_attribute("value")
                    before_signature = table_signature(page)
                    if value is not None:
                        select_control.select_option(value=value)
                    else:
                        select_control.select_option(label=option_text)
                    wait_for_table_change(
                        page, before_signature, reason="view dropdown"
                    )
                    find_shipments_table(page).wait_for(state="visible", timeout=15000)
                    write_log(f"{requested} Shipments View selected from dropdown.")
                    return
        except Exception:
            continue

    # 2. User-facing links/buttons/tabs/menu items.
    candidates = []
    for pattern in exact_patterns:
        candidates.extend(
            [
                page.get_by_role("tab", name=pattern),
                page.get_by_role("link", name=pattern),
                page.get_by_role("button", name=pattern),
                page.get_by_role("menuitem", name=pattern),
                page.get_by_text(pattern, exact=False),
                page.locator("a").filter(has_text=pattern),
                page.locator("button").filter(has_text=pattern),
                page.locator("li").filter(has_text=pattern),
            ]
        )

    control = first_visible(candidates, 1800)
    if control is not None:
        try:
            selected = (
                (control.get_attribute("aria-selected") or "").lower() == "true"
                or "active" in (control.get_attribute("class") or "").lower()
            )
        except Exception:
            selected = False

        if not selected:
            before_signature = table_signature(page)
            try:
                control.click(timeout=5000)
            except Exception:
                control.click(timeout=5000, force=True)
            wait_for_table_change(page, before_signature, reason="view control")

        find_shipments_table(page).wait_for(state="visible", timeout=15000)
        write_log(f"{requested} Shipments View selected.")
        return

    # 3. DOM fallback for custom navigation components without standard roles.
    expected_texts = (
        ["coe - shipment", "coe shipment", "coe shipments view"]
        if requested == COE_VIEW
        else ["bu - shipment", "bu shipment", "bu shipments view"]
    )
    signature_before_click = table_signature(page)
    clicked = page.evaluate(
        """expectedTexts => {
            const normalized = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            const elements = Array.from(document.querySelectorAll(
                'a,button,li,[role="tab"],[role="button"],[role="menuitem"],div,span'
            ));
            const element = elements.find(item => {
                const text = normalized(item.innerText || item.textContent);
                const style = getComputedStyle(item);
                const visible = style.display !== 'none' && style.visibility !== 'hidden' && item.offsetParent !== null;
                return visible && expectedTexts.includes(text);
            });
            if (!element) return false;
            element.click();
            return true;
        }""",
        expected_texts,
    )
    if clicked:
        wait_for_table_change(page, signature_before_click, reason="custom navigation")
        find_shipments_table(page).wait_for(state="visible", timeout=15000)
        write_log(f"{requested} Shipments View selected through custom navigation.")
        return

    # 4. After opening Centralized Shipments Tracking, the hub may present a
    # single shipments table with no separate view selector at all.
    #
    # This fallback used to apply to BU only, so COE raised and the whole
    # update aborted before writing anything — the run log showed every
    # shipment failing with "COE Shipments View option was not found" even
    # though both carriers had returned good dates.
    #
    # Applying it to COE as well is safe because the VIEW does not decide
    # which field is written: fill_date_field() selects the field by name, and
    # its ETA branch explicitly excludes ATA inputs
    # (input[id*='ETA']:not([id*='ATA'])). If the visible table's Manage page
    # has no ETA field, fill_date_field raises exactly as it does today. So
    # the worst case here is the current behaviour, and the best case is a
    # working update. Nothing can be written to the wrong field.
    try:
        table = find_shipments_table(page)
        if table.is_visible(timeout=1500):
            write_log(
                f"{requested} Shipments View option was not separately "
                "displayed; using the visible default shipment table. The "
                f"{'ETA' if requested == COE_VIEW else 'ATA'} field is still "
                "matched by name on the Manage page, so a missing field will "
                "fail cleanly rather than write to the wrong one."
            )
            return
    except Exception as error:
        note_suppressed("falling back to the default shipments table", error)

    take_screenshot(page, f"{requested}_view", "option_not_found")
    try:
        visible_text = page.locator("body").inner_text(timeout=5000)
        diagnostic_path = LOG_FOLDER / f"{requested}_view_visible_text.txt"
        diagnostic_path.write_text(visible_text, encoding="utf-8")
        write_log(f"Visible page text saved: {diagnostic_path}")
    except Exception as error:
        note_suppressed("saving the view diagnostic text", error)

    # Put the answer in the RUN LOG, not only in a file. When this fails we
    # need to know what the page IS offering, so the label can be matched
    # instead of guessed. Every dropdown option, tab, link and button that
    # could plausibly be a shipments view is listed here.
    try:
        write_log(
            f"--- {requested} view not found. Options actually on the page: ---"
        )
        seen = []

        for option in page.locator("select option").all()[:60]:
            try:
                text = (option.inner_text(timeout=300) or "").strip()
            except Exception:
                continue
            if text and text not in seen:
                seen.append(text)
                write_log(f"    dropdown option : {text!r}")

        for role in ("tab", "link", "button"):
            for control in page.get_by_role(role).all()[:60]:
                try:
                    text = (control.inner_text(timeout=300) or "").strip()
                except Exception:
                    continue
                text = " ".join(text.split())
                if not text or len(text) > 60 or text in seen:
                    continue
                # Only things that look like a view/shipment control.
                if re.search(r"view|shipment|coe|bu\b|track|clearance", text, re.I):
                    seen.append(text)
                    write_log(f"    {role:<15}: {text!r}")

        if not seen:
            write_log("    (nothing matched — the page may not have finished rendering)")
        write_log(f"--- end of {requested} view option list ---")
    except Exception as error:
        note_suppressed("listing available view options", error)

    raise Exception(
        f"{requested} Shipments View option was not found after opening "
        "Centralized Shipments Tracking."
    )


def open_shipments_view(page, view_name):
    # Open the site/home context first, then click the yellow top-navigation
    # feature shown in the screenshot, and only then choose COE or BU.
    if page.url.startswith("chrome-error") or "logisticshub.mantracgroup.com" not in page.url:
        page.goto(INTERNAL_URL, wait_until="domcontentloaded", timeout=60000)
        # click_centralized_shipments_tracking() runs next; wait for something
        # clickable to exist rather than assuming 1000ms was enough.
        if wait_for_any(
            page,
            [("page body", lambda: page_has_content(page))],
            HUB_TABLE_REFRESH_MAX_MS,
            reason="the hub home page to render",
        ) is None:
            page.wait_for_timeout(1000)

    click_centralized_shipments_tracking(page)
    select_shipments_view(page, view_name)


def select_under_clearance_filter(page):
    write_log("Selecting Status = Under Clearance...")
    selected = False

    for candidate in [
        page.locator("select[id*='Status' i]"),
        page.locator("select[name*='Status' i]"),
        page.get_by_label("Status", exact=False),
    ]:
        try:
            control = candidate.first
            control.wait_for(state="visible", timeout=2000)
            if control.evaluate("element => element.tagName.toLowerCase()") == "select":
                try:
                    control.select_option(label=TARGET_STATUS)
                    selected = True
                    break
                except Exception:
                    options = control.locator("option")
                    for index in range(options.count()):
                        option = options.nth(index)
                        if option.inner_text().strip().casefold() == TARGET_STATUS.casefold():
                            control.select_option(value=option.get_attribute("value"))
                            selected = True
                            break
                    if selected:
                        break
        except Exception:
            continue

    if not selected:
        try:
            status_label = page.get_by_text(
                re.compile(r"^\s*Status\s*:?\s*$", re.I)
            ).first
            status_label.wait_for(state="visible", timeout=2500)
            dropdown = status_label.locator(
                "xpath=following::*[self::div or self::span or self::button][1]"
            )
            dropdown.click(timeout=3500)
            page.get_by_text(TARGET_STATUS, exact=True).last.click(timeout=3500)
            selected = True
        except Exception:
            pass

    if not selected:
        raise Exception("Status filter could not be set to Under Clearance.")

    search_button = first_visible(
        [
            page.get_by_role("button", name="Search", exact=True),
            page.locator("button:has-text('Search')"),
            page.locator("input[type='submit'][value*='Search' i]"),
        ],
        2500,
    )
    if search_button is None:
        raise Exception("Search button was not found.")

    before_signature = table_signature(page)
    click_postback(search_button, "Under Clearance search")
    wait_for_table_change(page, before_signature, reason="Under Clearance search")
    find_shipments_table(page).wait_for(state="visible", timeout=12000)
    write_log("Under Clearance search results loaded.")


def go_to_table_page(page, page_number):
    if page_number <= 1:
        return

    write_log(f"Opening results page {page_number}...")
    target = first_visible(
        [
            page.get_by_role("link", name=str(page_number), exact=True),
            page.get_by_role("button", name=str(page_number), exact=True),
            page.locator("a").filter(has_text=re.compile(rf"^\s*{page_number}\s*$")),
            page.locator("button").filter(has_text=re.compile(rf"^\s*{page_number}\s*$")),
        ],
        2500,
    )
    if target is None:
        raise SkipShipment(f"Results page {page_number} is not available.")

    before_signature = table_signature(page)
    click_postback(target, f"results page {page_number}")
    wait_for_table_change(
        page, before_signature, reason="results page {0}".format(page_number)
    )
    find_shipments_table(page).wait_for(state="visible", timeout=12000)


def restore_filtered_page(page, view_name, page_number):
    page.bring_to_front()
    page.goto(INTERNAL_URL, wait_until="domcontentloaded", timeout=60000)
    open_shipments_view(page, view_name)
    select_under_clearance_filter(page)
    go_to_table_page(page, page_number)


# What we last navigated to, and the URL it was true at. Used only as a hint —
# every field is re-verified against the DOM before anything is skipped.
_hub_state = {"view": None, "page": None, "url": None}

# Counters so the run log can show whether the reuse is actually working.
_hub_stats = {"navigations": 0, "reused": 0}


# Three distinct answers, because conflating two of them allowed a false skip:
#   an int  -> pagination says this page
#   "none"  -> there is no pagination control, so a single page is implied
#   "error" -> we could not read it, which is NOT evidence of anything
PAGINATION_ABSENT = "none"
PAGINATION_UNREADABLE = "error"


def _active_table_page(page):
    """Read the highlighted pagination number, or say why we could not."""
    try:
        marked = page.locator(
            "[aria-current='page'], li.active a, li.active span, "
            "a.active, .pagination .active, .page-item.active .page-link"
        ).first
        if marked.count() == 0:
            return PAGINATION_ABSENT
        text = marked.inner_text(timeout=800).strip()
        return int(text) if text.isdigit() else PAGINATION_UNREADABLE
    except Exception:
        return PAGINATION_UNREADABLE


def _active_status_filter(page):
    """Read the Status dropdown's selected label, or None if unreadable."""
    try:
        control = page.locator(
            "select[id*='Status' i], select[name*='Status' i]"
        ).first
        if control.count() == 0:
            return None
        label = control.evaluate(
            "element => element.options[element.selectedIndex] "
            "? element.options[element.selectedIndex].text : null"
        )
        return label.strip() if label else None
    except Exception:
        return None


def hub_state_matches(page, view_name, page_number):
    """
    True only when the browser is provably already showing the right view,
    the Under Clearance filter and the right results page.

    Every unknown answers False. Skipping a navigation we should have made
    would put a date on the wrong shipment, so the bias is entirely towards
    navigating.
    """
    if _hub_state["view"] != view_name or _hub_state["page"] != page_number:
        return False

    try:
        current_url = page.url
    except Exception:
        return False

    # A navigation away from the hub invalidates everything we believe.
    if "logisticshub.mantracgroup.com" not in (current_url or ""):
        return False
    if _hub_state["url"] != current_url:
        return False

    # The table must actually be on screen right now.
    try:
        table = find_shipments_table(page)
        if table is None or not table.is_visible(timeout=800):
            return False
    except Exception:
        return False

    # The filter must still read Under Clearance. Unreadable is not proof.
    status = _active_status_filter(page)
    if status is None or status.casefold() != TARGET_STATUS.casefold():
        return False

    # Pagination must agree. A missing control legitimately implies page 1;
    # an unreadable control proves nothing and must never allow a skip.
    active_page = _active_table_page(page)
    if active_page == PAGINATION_UNREADABLE:
        return False
    if active_page == PAGINATION_ABSENT:
        if page_number != 1:
            return False
    elif active_page != page_number:
        return False

    return True


def ensure_filtered_page(page, view_name, page_number):
    """
    Guarantee the filtered table for `view_name` page `page_number` is showing,
    doing the least work that achieves it.

    Drop-in replacement for restore_filtered_page: identical postcondition,
    identical exceptions. It simply skips the rebuild when the page is already
    verified correct.
    """
    if hub_state_matches(page, view_name, page_number):
        _hub_stats["reused"] += 1
        write_log(
            f"{view_name} view page {page_number} is already loaded and "
            "verified; skipping re-navigation."
        )
        return

    restore_filtered_page(page, view_name, page_number)
    _hub_stats["navigations"] += 1
    _hub_state["view"] = view_name
    _hub_state["page"] = page_number
    try:
        _hub_state["url"] = page.url
    except Exception:
        _hub_state["url"] = None


def invalidate_hub_state():
    """Called whenever we knowingly navigate away from the filtered table."""
    _hub_state["view"] = None
    _hub_state["page"] = None
    _hub_state["url"] = None


def normalize_header(value):
    return re.sub(r"\s+", " ", value).strip().casefold()


def build_header_map(table):
    headers = table.locator("thead th")
    if headers.count() == 0:
        headers = table.locator("tr").first.locator("th")

    raw_map = {
        normalize_header(headers.nth(index).inner_text()): index
        for index in range(headers.count())
    }

    aliases = {
        "bol_awb": ["bol/awb number", "bol / awb number", "bol/awb"],
        "carrier": ["carrier name", "carrier"],
        "eta": ["eta"],
        "status": ["status"],
    }

    resolved = {}
    for logical_name, names in aliases.items():
        for name in names:
            if name in raw_map:
                resolved[logical_name] = raw_map[name]
                break
        if logical_name not in resolved:
            raise Exception(f"Required table column was not found: {logical_name}")
    return resolved


# ============================================================
# AIRLINE REGISTRY
# ============================================================
#
# The first three digits of an AWB are the IATA carrier prefix. 057 IS Air
# France — always, regardless of how the Hub happens to spell the carrier name.
# Matching on the prefix is therefore far more reliable than matching on text
# like "Qatar Airways Cargo" vs "QATAR" vs "QR".
#
# `provider` is the tracking integration that handles it. Several airlines
# share one portal: Air France and KLM are both tracked on AFKL myCargo, so a
# single integration covers 057 and 074.
#
# provider = None means "we know exactly who this is, but it is not automated
# yet" — which is a far more useful message than "unsupported carrier", and it
# lets the run log count what each missing airline is costing.

AIRLINES = {
    "020": {"name": "Lufthansa Cargo",          "code": "LH", "provider": None},
    "057": {"name": "Air France",               "code": "AF", "provider": "AFKL"},
    "065": {"name": "Saudia Cargo",             "code": "SV", "provider": None},
    "071": {"name": "Ethiopian Airlines",       "code": "ET", "provider": None},
    "074": {"name": "KLM Royal Dutch Airlines", "code": "KL", "provider": "AFKL"},
    "077": {"name": "EgyptAir",                 "code": "MS", "provider": None},
    "083": {"name": "South African Airways",    "code": "SA", "provider": None},
    "125": {"name": "British Airways",          "code": "BA", "provider": None},
    "157": {"name": "Qatar Airways",            "code": "QR", "provider": "QATAR"},
    "176": {"name": "Emirates SkyCargo",        "code": "EK", "provider": None},
    "235": {"name": "Turkish Airlines",         "code": "TK", "provider": None},
    "459": {"name": "RwandAir",                 "code": "WB", "provider": None},
    "485": {"name": "Astral Aviation",          "code": "8V", "provider": "ASTRAL"},
    "574": {"name": "Allied Air Limited",       "code": "4W", "provider": None},
    # 615 is an AIR WAYBILL, not a parcel number. dhl.com consumer tracking
    # does not recognise it — the run log shows 615-62310566 returning
    # READY_NO_RESULT in 15.6s while a plain 10-digit DHL Express number
    # (9451291275) worked on the same page. It needs DHL's air-cargo portal,
    # so it is marked unautomated until that URL is available rather than
    # being sent somewhere that will always answer "no result".
    "615": {"name": "DHL Aviation",             "code": "QY", "provider": None},
    "932": {"name": "Virgin Atlantic Cargo",    "code": "VS", "provider": None},
}

# Counted per run so the log can report what each unautomated airline costs.
_unsupported_seen = {}


def airline_from_awb(bol_awb):
    """
    Identify the airline from the AWB prefix.

    Returns (prefix, entry) or (None, None). Tolerates "157-49568713",
    "157 4956 8713" and "1574956871" alike.
    """
    digits = re.sub(r"\D", "", str(bol_awb or ""))
    if len(digits) < 4:
        return None, None
    prefix = digits[:3]
    return (prefix, AIRLINES[prefix]) if prefix in AIRLINES else (prefix, None)


def carrier_provider(carrier_name, bol_awb=None):
    """
    Which tracking integration handles this shipment.

    The AWB prefix is authoritative when we have it; the carrier name is only
    a fallback for records where the number is missing or malformed.
    """
    if bol_awb:
        _prefix, entry = airline_from_awb(bol_awb)
        if entry:
            return entry["provider"]

    normalized = re.sub(r"\s+", " ", carrier_name or "").strip().upper()
    if "DHL" in normalized:
        return "DHL"
    if "QATAR AIRWAYS" in normalized or normalized == "QATAR" or "QATAR CARGO" in normalized:
        return "QATAR"
    if "AIR FRANCE" in normalized or normalized.startswith("KLM") or "AFKL" in normalized:
        return "AFKL"
    return None


def describe_unsupported(bol_awb, carrier_name):
    """A reason a person can act on, and a tally for prioritising the next build."""
    prefix, entry = airline_from_awb(bol_awb)
    if entry:
        label = "{0} ({1})".format(entry["name"], prefix)
        reason = "{0} is not automated yet.".format(label)
    elif prefix:
        label = "Unknown prefix {0}".format(prefix)
        reason = ("AWB prefix {0} does not match any airline in the registry "
                  "({1}).".format(prefix, carrier_name or "no carrier name"))
    else:
        label = carrier_name or "unknown carrier"
        reason = "No usable AWB prefix on this record ({0}).".format(label)

    _unsupported_seen[label] = _unsupported_seen.get(label, 0) + 1
    return reason


def report_unsupported():
    """End-of-run tally: which airlines are worth automating next, from real data."""
    if not _unsupported_seen:
        return
    total = sum(_unsupported_seen.values())
    write_log(
        "Shipments skipped because their airline is not automated yet "
        "({0} in total):".format(total)
    )
    for label, count in sorted(_unsupported_seen.items(), key=lambda kv: -kv[1]):
        write_log("    {0} x{1}".format(label, count))
    write_log(
        "    ^ automate these in that order; the top of this list is where the "
        "next integration pays back most."
    )


def collect_supported_shipments(page, table_page):
    """Source list comes from BU Shipments View / Under Clearance."""
    table = find_shipments_table(page)
    columns = build_header_map(table)
    rows = table.locator("tbody tr")
    shipments = []

    for index in range(rows.count()):
        cells = rows.nth(index).locator("td")
        if cells.count() <= max(columns.values()):
            continue

        status = cells.nth(columns["status"]).inner_text().strip()
        carrier = cells.nth(columns["carrier"]).inner_text().strip()
        provider = carrier_provider(carrier)

        if status.casefold() != TARGET_STATUS.casefold() or provider is None:
            continue

        bol_awb = cells.nth(columns["bol_awb"]).inner_text().strip()
        current_eta = cells.nth(columns["eta"]).inner_text().strip()

        if bol_awb:
            shipments.append(
                {
                    "bol_awb": bol_awb,
                    "carrier": carrier,
                    "provider": provider,
                    "current_eta": current_eta,
                    "table_page": table_page,
                }
            )

    write_log(
        f"Supported Under Clearance rows found on BU page {table_page}: "
        f"{len(shipments)}"
    )
    tower.page_scanned(table_page, len(shipments))
    return shipments


def find_row_by_bol(page, bol_awb):
    rows = find_shipments_table(page).locator("tbody tr")
    for index in range(rows.count()):
        row = rows.nth(index)
        if bol_awb in row.inner_text():
            return row
    return None


def click_manage_in_view(page, view_name, bol_awb, preferred_page):
    """Find BOL/AWB in the requested view, scanning pages when necessary."""
    page_order = [preferred_page] + [
        page_number
        for page_number in range(1, MAX_TABLE_PAGES + 1)
        if page_number != preferred_page
    ]

    for page_number in page_order:
        try:
            ensure_filtered_page(page, view_name, page_number)
        except SkipShipment:
            continue

        row = find_row_by_bol(page, bol_awb)
        if row is None:
            continue

        manage_button = first_visible(
            [
                row.get_by_role("button", name="Manage", exact=False),
                row.get_by_role("link", name="Manage", exact=False),
                row.locator("button:has-text('Manage')"),
                row.locator("a:has-text('Manage')"),
                row.locator("input[value*='Manage' i]"),
            ],
            2500,
        )
        if manage_button is None:
            raise Exception(f"Manage control was not found for {bol_awb} in {view_name} view.")

        click_postback(manage_button, f"Manage for {bol_awb}")
        # We are leaving the results table for the Manage form.
        invalidate_hub_state()
        # Wait for the Manage form to actually be editable rather than assuming
        # it took 1200ms. fill_date_field runs next and needs a real field.
        if wait_for_any(
            page,
            [("form", lambda: manage_form_ready(page))],
            HUB_FORM_READY_MAX_MS,
            reason="the Manage form for {0}".format(bol_awb),
        ) is None:
            write_log(
                f"Manage form for {bol_awb} was not confirmed ready; "
                "continuing so the existing field lookup can report properly."
            )
        write_log(f"Manage opened for {bol_awb} in {view_name} Shipments View.")
        return page_number

    raise SkipShipment(f"{bol_awb} was not found in {view_name} Shipments View pages 1-{MAX_TABLE_PAGES}.")


# ============================================================
# DHL DIRECT URL, TIMELINE, AND RETRY
# ============================================================

def build_dhl_tracking_url(tracking_number):
    encoded_tracking = quote(tracking_number.strip(), safe="")
    return f"{DHL_BASE_URL}?locale=true&submit=1&tracking-id={encoded_tracking}"


def dhl_processing_active(page):
    try:
        message = page.get_by_text(
            re.compile(r"Your request is being processed", re.I),
            exact=False,
        ).first
        return message.is_visible(timeout=800)
    except Exception:
        return False


def wait_until_processing_finishes(page, tracking_number):
    start_time = time.time()
    last_log = -10

    while time.time() - start_time < DHL_PROCESSING_TIMEOUT_SECONDS:
        if not dhl_processing_active(page):
            elapsed = int(time.time() - start_time)
            write_log(f"DHL processing screen is gone after {elapsed} seconds.")
            page.wait_for_timeout(1200)
            return True

        elapsed = int(time.time() - start_time)
        if elapsed - last_log >= 10:
            write_log(
                f"DHL processing screen active for {tracking_number}; "
                f"elapsed {elapsed} seconds..."
            )
            last_log = elapsed

        page.wait_for_timeout(1000)

    return False


def click_event_log(page):
    """Open DHL Event Log once and wait for the event rows to render."""
    event_log_tab = first_visible(
        [
            page.get_by_role("tab", name=re.compile(r"^\s*Event\s+Log\s*$", re.I)),
            page.get_by_role("button", name=re.compile(r"^\s*Event\s+Log\s*$", re.I)),
            page.get_by_role("link", name=re.compile(r"^\s*Event\s+Log\s*$", re.I)),
            page.get_by_text(re.compile(r"^\s*Event\s+Log\s*$", re.I)),
        ],
        3500,
    )

    if event_log_tab is None:
        return False

    try:
        selected = (
            (event_log_tab.get_attribute("aria-selected") or "").lower() == "true"
            or "active" in (event_log_tab.get_attribute("class") or "").lower()
        )
    except Exception:
        selected = False

    if not selected:
        try:
            event_log_tab.click(timeout=5000)
        except Exception:
            event_log_tab.click(timeout=5000, force=True)

    # The screenshot shows Event Log columns: Time, Status Update, Location.
    try:
        page.get_by_text(
            re.compile(r"^\s*Status\s+Update\s*$", re.I),
            exact=False,
        ).first.wait_for(state="visible", timeout=10000)
    except Exception:
        page.wait_for_timeout(1800)

    write_log("DHL Event Log opened and rendered.")
    return True


def parse_event_row_text(row_text, event_name):
    """Extract a date only from the same Event Log row as the requested event."""
    if not re.search(rf"^|\s{re.escape(event_name)}\s|{re.escape(event_name)}$", row_text, re.I):
        if event_name.casefold() not in row_text.casefold():
            return None

    dates = extract_all_dates(row_text)
    if not dates:
        return None

    # One event row normally contains one date. If DHL adds more, choose the
    # latest date within that exact row rather than another page-level date.
    return latest_date([parsed_date for _, parsed_date, _ in dates])


def extract_event_log_rows_from_scope(scope):
    """
    Read DHL Event Log rows and return dates strictly associated with:
      Estimated Delivery -> ETA
      Arrived Final Destination -> ATA
    """
    eta_dates = []
    ata_dates = []

    # Primary method: actual HTML table rows, matching the displayed grid.
    rows = scope.locator("table tbody tr")
    for index in range(rows.count()):
        try:
            row = rows.nth(index)
            if not row.is_visible(timeout=400):
                continue
            row_text = row.inner_text(timeout=1500).strip()
            if not row_text:
                continue

            eta = parse_event_row_text(row_text, "Estimated Delivery")
            if eta:
                eta_dates.append(eta)
                write_log(f"DHL Event Log Estimated Delivery row date: {eta}")

            ata = parse_event_row_text(row_text, "Arrived Final Destination")
            if ata:
                ata_dates.append(ata)
                write_log(f"DHL Event Log Arrived Final Destination row date: {ata}")
        except Exception:
            continue

    # Fallback for div-based responsive rows. Find each exact event label and
    # inspect only its closest bounded row/card container.
    for event_name, target_dates in [
        ("Estimated Delivery", eta_dates),
        ("Arrived Final Destination", ata_dates),
    ]:
        labels = scope.get_by_text(
            re.compile(rf"^\s*{re.escape(event_name)}\s*$", re.I),
            exact=False,
        )

        for index in range(labels.count()):
            try:
                label = labels.nth(index)
                if not label.is_visible(timeout=400):
                    continue

                row_texts = []
                for xpath in [
                    "xpath=ancestor::tr[1]",
                    "xpath=ancestor::*[@role='row'][1]",
                    "xpath=../..",
                    "xpath=../../..",
                ]:
                    try:
                        container = label.locator(xpath).first
                        text = container.inner_text(timeout=1200).strip()
                        if 0 < len(text) <= 1500:
                            row_texts.append(text)
                    except Exception:
                        continue

                try:
                    bounded_text = label.evaluate(
                        """element => {
                            let node = element;
                            for (let i = 0; i < 5 && node; i++, node = node.parentElement) {
                                const text = (node.innerText || '').trim();
                                if (text.length >= 10 && text.length <= 1500) return text;
                            }
                            return '';
                        }"""
                    )
                    if bounded_text:
                        row_texts.append(bounded_text)
                except Exception:
                    pass

                for row_text in row_texts:
                    event_date = parse_event_row_text(row_text, event_name)
                    if event_date:
                        target_dates.append(event_date)
                        write_log(f"DHL Event Log {event_name} card date: {event_date}")
                        break
            except Exception:
                continue

    return {"eta_dates": eta_dates, "ata_dates": ata_dates}


def extract_event_log_result(page):
    """
    Event Log is authoritative:
      Estimated Delivery means the date is estimated (ETA).
      Arrived Final Destination means the event is completed (ATA).
    """
    # The processing banner alone must not block extraction: DHL can leave it
    # in the DOM while the Event Log is fully rendered. Only refuse when the
    # banner is showing AND there is genuinely no shipment data to read.
    if dhl_processing_active(page):
        page_text = _page_text(page)
        if not dhl_data_markers(page_text)["found"]:
            return None
        write_log(
            "DHL still shows the processing banner, but shipment data is "
            "present; reading it rather than waiting."
        )

    if not click_event_log(page):
        return None

    eta_dates = []
    ata_dates = []

    main_result = extract_event_log_rows_from_scope(page)
    eta_dates.extend(main_result["eta_dates"])
    ata_dates.extend(main_result["ata_dates"])

    # DHL can render the tracking widget inside an iframe.
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            frame_result = extract_event_log_rows_from_scope(frame)
            eta_dates.extend(frame_result["eta_dates"])
            ata_dates.extend(frame_result["ata_dates"])
        except Exception:
            continue

    # Bounded text fallback: pair each exact event with the closest preceding
    # date. Never choose a date from an unrelated event row.
    if not eta_dates or not ata_dates:
        scopes = [page] + [frame for frame in page.frames if frame != page.main_frame]
        for scope in scopes:
            try:
                event_text = scope.locator("body").inner_text(timeout=4000)
                all_dates = extract_all_dates(event_text)

                for event_name, target_dates in [
                    ("Estimated Delivery", eta_dates),
                    ("Arrived Final Destination", ata_dates),
                ]:
                    if target_dates:
                        continue
                    for event_match in re.finditer(
                        re.escape(event_name),
                        event_text,
                        flags=re.I,
                    ):
                        candidates = []
                        for date_position, parsed_date, raw_date in all_dates:
                            if date_position <= event_match.start():
                                distance = event_match.start() - date_position
                                if distance <= 500:
                                    candidates.append((distance, parsed_date, raw_date))
                        if candidates:
                            candidates.sort(key=lambda item: item[0])
                            target_dates.append(candidates[0][1])
                            write_log(
                                f"DHL Event Log text fallback {event_name}: "
                                f"{candidates[0][2]} -> {candidates[0][1]}"
                            )
            except Exception:
                continue

    latest_eta = latest_date(eta_dates)
    latest_ata = latest_date(ata_dates)

    if latest_eta:
        write_log(f"Latest DHL Event Log Estimated Delivery ETA: {latest_eta}")
    if latest_ata:
        write_log(f"Latest DHL Event Log Arrived Final Destination ATA: {latest_ata}")

    if not latest_eta and not latest_ata:
        return None

    if latest_ata:
        shipment_state = "Arrived Final Destination completed"
    else:
        shipment_state = "Estimated Delivery only"

    return {
        "provider": "DHL",
        "tracking_status": shipment_state,
        "event_log_eta": latest_eta,
        "event_log_ata": latest_ata,
        "eta": latest_eta,
        "ata": latest_ata,
    }


def event_log_ready(page):
    if dhl_processing_active(page):
        return False

    for pattern in [
        re.compile(r"^\s*Event\s+Log\s*$", re.I),
        re.compile(r"^\s*Estimated\s+Delivery\s*$", re.I),
        re.compile(r"^\s*Arrived\s+Final\s+Destination\s*$", re.I),
    ]:
        try:
            if page.get_by_text(pattern, exact=False).first.is_visible(timeout=400):
                return True
        except Exception:
            continue

    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            if frame.get_by_text(
                re.compile(r"^\s*Event\s+Log\s*$", re.I),
                exact=False,
            ).first.is_visible(timeout=300):
                return True
        except Exception:
            continue

    return False


# ============================================================
# DHL PAGE STATE MACHINE
# ============================================================
#
# The DHL tracking page passes through several distinct states and the old code
# could only tell "Event Log visible" from "not visible". Everything else — an
# unknown AWB, an Akamai interstitial, an error page — looked identical to
# "still loading", so the automation waited out its whole budget for pages that
# had already finished.
#
# Each state below is read from ONE body.inner_text() snapshot per poll rather
# than a handful of locator probes. That is a single round-trip per second
# instead of six, which matters because this loop can run for a minute.

DHL_LOADING = "LOADING"                  # shell not painted yet
DHL_PROCESSING = "PROCESSING"            # Akamai / DHL interstitial, keep waiting
DHL_COOKIE = "COOKIE"                    # consent banner covering the page
DHL_READY_RESULT = "READY_RESULT"        # Event Log rendered — terminal
DHL_READY_NO_RESULT = "READY_NO_RESULT"  # DHL says it has nothing — terminal
DHL_ERROR = "ERROR"                      # error / blocked page — terminal
DHL_STUCK = "STUCK"                      # no progress for too long — terminal

DHL_TERMINAL = {DHL_READY_RESULT, DHL_READY_NO_RESULT, DHL_ERROR, DHL_STUCK}

# DHL's own copy for "this tracking number gave us nothing". Matching any of
# these ends the wait immediately instead of polling for another 100 seconds.
DHL_NO_RESULT_PATTERNS = [
    r"no results? (were )?found",
    r"could not be found",
    r"we (could|couldn't|could not) find",
    r"not found in our system",
    r"no (tracking )?information (is )?available",
    r"check (the|your) (tracking|waybill|shipment) number",
    r"invalid (tracking|waybill|shipment) number",
    r"no shipment details",
]

DHL_ERROR_PATTERNS = [
    r"access denied",
    r"service (is )?(temporarily )?unavailable",
    r"something went wrong",
    r"error 5\d\d",
    r"http (500|502|503|504)",
    r"too many requests",
    r"rate limit",
]

# Event Log is the authoritative success marker (see extract_event_log_result).
DHL_RESULT_PATTERNS = [
    r"event\s+log",
    r"estimated\s+delivery",
    r"arrived\s+final\s+destination",
]

# --- What "the shipment data is on screen" actually looks like -------------
#
# Grouped by the area of the page they come from, so a failure to match is
# diagnosable from the log rather than being one opaque boolean.

# The shipment details block near the top of a DHL result.
DHL_DETAIL_PATTERNS = [
    r"ship(?:ment)?\s+date",
    r"\bpieces?\b",
    r"total\s+weight",
    r"origin\s+service\s+area",
    r"destination\s+service\s+area",
    r"shipper\s+reference",
    r"waybill\s+number",
    r"\bproduct\b\s*:",
]

# The timeline / event log section itself.
DHL_TIMELINE_PATTERNS = [
    r"event\s+log",
    r"shipment\s+(?:history|timeline|progress)",
    r"tracking\s+history",
    r"status\s+update",
    r"\bpiece\s+details\b",
]

# Delivery-date labels. Matched WITH a nearby date before being trusted.
DHL_DELIVERY_DATE_LABELS = [
    "Estimated Delivery",
    "Expected Delivery",
    "Scheduled Delivery",
    "Delivered On",
    "Delivery Date",
]

# Event names that tell us where the shipment actually is. Ordered most to
# least advanced; the first match wins.
DHL_STATUS_EVENTS = [
    ("DELIVERED", [r"\bdelivered\b", r"signed\s+for\s+by"]),
    ("EXCEPTION", [r"\bexception\b", r"held\b", r"customs\s+status\s+updated",
                   r"delivery\s+attempt", r"\bdelay(?:ed)?\b", r"returned\s+to"]),
    ("ARRIVED", [r"arrived\s+final\s+destination", r"with\s+delivery\s+courier",
                 r"out\s+for\s+delivery"]),
    ("IN_TRANSIT", [r"\bin\s+transit\b", r"departed\s+facility",
                    r"arrived\s+at\s+(?:sort|delivery)", r"processed\s+at",
                    r"clearance\s+(?:event|processing)", r"transferred\s+through"]),
    ("PENDING", [r"shipment\s+information\s+received", r"electronic\s+shipment",
                 r"awaiting\s+collection", r"\bpicked\s+up\b"]),
]


def page_shows_tracking_number(text, tracking_number):
    """
    Is the shipment on screen the one we asked about?

    Guards the data-first rule against a stale page: after a re-search the
    PREVIOUS shipment's Event Log can still be rendered, and reading it would
    write another shipment's dates onto this record.

    Returns True (proceed), or False only when the page clearly shows a
    DIFFERENT waybill and not ours. A page that echoes no waybill at all is
    not evidence of staleness, so it returns True.
    """
    if not tracking_number:
        return True

    wanted = re.sub(r"\D", "", str(tracking_number))
    if not wanted:
        return True

    digits_on_page = re.sub(r"\D", "", text)
    if wanted in digits_on_page:
        return True

    # Ours is absent. Is some other waybill present?
    others = re.findall(r"\b\d[\d\s-]{8,}\d\b", text)
    for candidate in others:
        if re.sub(r"\D", "", candidate) != wanted:
            return False

    return True


def dhl_data_markers(text):
    """
    Report which kinds of shipment data are visible.

    Returns a dict. `found` is True when the page carries anything the
    automation can legitimately act on.
    """
    dated_events = extract_all_dates(text)

    delivery_date = None
    for label in DHL_DELIVERY_DATE_LABELS:
        found_date = extract_date_near_labels(text, [label])
        if found_date:
            delivery_date = (label, found_date)
            break

    markers = {
        "details": _matches(text, DHL_DETAIL_PATTERNS),
        "timeline": _matches(text, DHL_TIMELINE_PATTERNS),
        "dated_event": len(dated_events) > 0,
        "delivery_date": delivery_date is not None,
        "date_count": len(dated_events),
        "delivery_date_value": delivery_date,
    }
    markers["found"] = any(
        markers[key] for key in ("details", "timeline", "dated_event", "delivery_date")
    )
    return markers


def dhl_shipment_status(text):
    """
    Best-effort shipment status from the event names actually on the page.

    Returns one of DELIVERED / EXCEPTION / ARRIVED / IN_TRANSIT / PENDING, or
    None when no recognised event is present. Never guesses from a date alone.
    """
    for status, patterns in DHL_STATUS_EVENTS:
        if _matches(text, patterns):
            return status
    return None


def dhl_latest_event(text):
    """
    The most recent dated line, as (date, line).

    Reads whole lines so the returned text is what a person would read on the
    page. Returns (None, None) when nothing dated is present.
    """
    best = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # A forecast is not an event. "Estimated Delivery: 26 August 2026" is
        # the ETA, and reporting it as the latest event would overstate where
        # the shipment actually is.
        if re.search(r"(estimated|expected|scheduled)\s+delivery", stripped, re.I):
            continue
        dates = extract_all_dates(stripped)
        if not dates:
            continue
        for _position, parsed, _raw in dates:
            try:
                when = datetime.strptime(parsed, "%d/%m/%Y")
            except ValueError:
                continue
            if best is None or when > best[0]:
                best = (when, parsed, stripped[:200])
    if best is None:
        return None, None
    return best[1], best[2]

DHL_PROCESSING_PATTERNS = [
    r"your request is being processed",
    r"please wait while",
    r"verifying your browser",
    r"checking your browser",
]

DHL_COOKIE_PATTERNS = [
    r"accept all cookies",
    r"we use cookies",
    r"cookie (settings|preferences)",
]


def _page_text(page):
    """One text snapshot of the main frame plus any tracking iframes."""
    chunks = []
    try:
        chunks.append(page.locator("body").inner_text(timeout=2000))
    except Exception:
        pass
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            chunks.append(frame.locator("body").inner_text(timeout=1000))
        except Exception as error:
            note_suppressed("reading an iframe's text", error)
            continue
    return "\n".join(chunks)


def _matches(text, patterns):
    for pattern in patterns:
        if re.search(pattern, text, re.I):
            return True
    return False


def detect_dhl_state(page, text=None, tracking_number=None):
    """
    Classify the DHL page, deciding on SHIPMENT DATA before anything else.

    Previous versions tested the "your request is being processed" phrase
    first, so a page displaying the full Event Log was still reported as
    PROCESSING if that phrase survived anywhere in the DOM. Data now wins:
    if the shipment information is on screen, the page is ready, whatever
    else it also happens to say.
    """
    if text is None:
        text = _page_text(page)

    stripped = text.strip()

    # 1. DATA FIRST — but it must be THIS shipment's data. The stale-page risk
    #    is real after a re-search, so data only wins when the waybill matches.
    markers = dhl_data_markers(stripped)
    if markers["found"]:
        if page_shows_tracking_number(stripped, tracking_number):
            return DHL_READY_RESULT, text
        # Someone else's shipment is still on screen: treat as not ready yet.
        if _matches(stripped, DHL_PROCESSING_PATTERNS):
            return DHL_PROCESSING, text
        return DHL_LOADING, text

    # 2. A hard error page carries no data and needs no further waiting.
    if _matches(stripped, DHL_ERROR_PATTERNS):
        return DHL_ERROR, text

    # 3. DHL explicitly saying it has nothing, once the page has painted.
    if len(stripped) > 200 and _matches(stripped, DHL_NO_RESULT_PATTERNS):
        return DHL_READY_NO_RESULT, text

    # 4. Only now is the processing banner informative: no data is present,
    #    so the fact that DHL says it is working is the useful signal.
    if _matches(stripped, DHL_PROCESSING_PATTERNS):
        return DHL_PROCESSING, text

    # 5. Consent overlay with nothing behind it yet.
    if _matches(stripped, DHL_COOKIE_PATTERNS):
        return DHL_COOKIE, text

    # 6. Painted or not, no markers of any kind: still filling in.
    return DHL_LOADING, text


def wait_for_dhl_page(page, tracking_number, max_seconds=None,
                      stuck_after=None, label="DHL"):
    """
    Poll the state machine and return the moment DHL reaches a terminal state.

    Returns (state, elapsed_seconds).

    - READY_RESULT      the Event Log is on screen; caller can extract
    - READY_NO_RESULT   DHL answered and has nothing; stop immediately
    - ERROR             error/blocked page; stop immediately
    - STUCK             no progress at all for `stuck_after`
    - LOADING/PROCESSING returned only when `max_seconds` runs out

    Time spent in PROCESSING does not count towards the stuck detector: DHL is
    visibly working, so we let it work.
    """
    ceiling = DHL_READY_MAX_SECONDS if max_seconds is None else max_seconds
    stuck_limit = DHL_STUCK_AFTER_SECONDS if stuck_after is None else stuck_after

    started = time.time()
    deadline = started + ceiling
    last_state = None
    last_progress = started
    last_length = 0
    last_report = 0.0
    cookie_attempts = 0

    while time.time() < deadline:
        state, text = detect_dhl_state(page, tracking_number=tracking_number)
        elapsed = time.time() - started
        length = len(text.strip())

        if state != last_state:
            write_log(
                "{0} page state: {1} (after {2:.1f}s) for {3}".format(
                    label, state, elapsed, tracking_number
                )
            )
            try:
                tower.step("{0}: {1}".format(label, state.replace("_", " ").lower()))
            except Exception:
                pass
            last_state = state
            last_progress = time.time()

        # Growing content counts as progress even within the same state.
        if length > last_length + 50:
            last_length = length
            last_progress = time.time()

        if state == DHL_READY_RESULT:
            markers = dhl_data_markers(text)
            status = dhl_shipment_status(text)
            latest_date, latest_line = dhl_latest_event(text)
            write_log(
                "{0} ready after {1:.1f}s for {2} — details={3} timeline={4} "
                "dated_events={5} delivery_date={6} status={7}".format(
                    label, elapsed, tracking_number,
                    markers["details"], markers["timeline"], markers["date_count"],
                    markers["delivery_date_value"][1]
                    if markers["delivery_date_value"] else "none",
                    status or "unknown",
                )
            )
            if latest_date:
                write_log(
                    "{0} latest dated event for {1}: {2} | {3}".format(
                        label, tracking_number, latest_date, latest_line
                    )
                )
            return state, elapsed

        if state in (DHL_READY_NO_RESULT, DHL_ERROR):
            write_log(
                "{0} reached terminal state {1} after {2:.1f}s — not waiting "
                "any longer for {3}.".format(label, state, elapsed, tracking_number)
            )
            return state, elapsed

        # Consent is handled early and cheaply. It gets a few short attempts
        # inside the opening seconds of the readiness window and never more;
        # a banner must not be able to consume the whole budget.
        if (state == DHL_COOKIE
                and cookie_attempts < DHL_COOKIE_MAX_ATTEMPTS
                and elapsed < DHL_COOKIE_WINDOW_SECONDS):
            cookie_attempts += 1
            if accept_cookie_banner(page, label, budget_seconds=1.5):
                last_progress = time.time()
            page.wait_for_timeout(250)
            continue

        # PROCESSING means DHL is genuinely working; give it room.
        if state != DHL_PROCESSING and (time.time() - last_progress) > stuck_limit:
            write_log(
                "{0} showed no progress for {1}s (state {2}); treating as "
                "temporarily stuck for {3}.".format(
                    label, stuck_limit, state, tracking_number
                )
            )
            return DHL_STUCK, time.time() - started

        if elapsed - last_report >= 10:
            write_log(
                "{0} still {1} after {2:.0f}s for {3} (ceiling {4}s)...".format(
                    label, state, elapsed, tracking_number, ceiling
                )
            )
            last_report = elapsed

        page.wait_for_timeout(750)

    elapsed = time.time() - started
    write_log(
        "{0} did not become ready within {1}s for {2}; last state was {3}.".format(
            label, ceiling, tracking_number, last_state
        )
    )
    return last_state or DHL_LOADING, elapsed


def wait_for_dated_dhl_result(page, timeout_seconds):
    """
    Wait until DHL is genuinely ready, then read ETA/ATA from the Event Log.

    Previously this polled `event_log_ready` on a fixed 1s cadence and had no
    way to tell "still loading" from "DHL has nothing for this AWB", so an
    unknown tracking number cost the full budget. It now returns as soon as the
    page reaches any terminal state.
    """
    started = time.time()
    deadline = started + timeout_seconds

    while time.time() < deadline:
        remaining = deadline - time.time()
        state, _elapsed = wait_for_dhl_page(
            page,
            "tracking",
            max_seconds=remaining,
            label="DHL",
        )

        if state == DHL_READY_RESULT:
            # Markers are on screen; give the rows a beat to finish painting,
            # then extract.
            page.wait_for_timeout(600)
            result = extract_event_log_result(page)
            if result:
                return result

            # Extraction is expensive: click_event_log alone probes four
            # locators and waits for the rows, ~28s per call. Re-running it on
            # a page that has not changed just burns the budget (the run log
            # showed four identical 28s attempts on one shipment). Wait for the
            # page to actually GAIN a date before paying for it again.
            dates_before = len(extract_all_dates(_page_text(page)))
            write_log(
                "No dated ETA/ATA row yet ({0} date(s) on the page). Watching "
                "for new dates before re-reading the Event Log.".format(dates_before)
            )
            gained = False
            while time.time() < deadline:
                page.wait_for_timeout(1500)
                if len(extract_all_dates(_page_text(page))) > dates_before:
                    gained = True
                    break
            if not gained:
                return None
            write_log("New dated content appeared; re-reading the Event Log.")
            continue

        if state in (DHL_READY_NO_RESULT, DHL_ERROR, DHL_STUCK):
            return None

        # LOADING / PROCESSING with the budget exhausted.
        return None

    return None


def click_dhl_track_again(page, tracking_number):
    tracking_input = first_visible(
        [
            page.get_by_placeholder("Enter your tracking number(s)", exact=False),
            page.locator("input[name*='tracking' i]"),
            page.locator("input[id*='tracking' i]"),
            page.locator("input[type='text']:visible"),
        ],
        3000,
    )

    track_button = first_visible(
        [
            page.get_by_role("button", name="Track", exact=True),
            page.locator("button:has-text('Track')"),
            page.locator("input[type='submit'][value*='Track' i]"),
        ],
        2500,
    )

    if track_button is not None:
        track_button.click(timeout=5000, force=True)
        return

    if tracking_input is not None:
        tracking_input.press("Enter")
        return

    page.goto(
        build_dhl_tracking_url(tracking_number),
        wait_until="domcontentloaded",
        timeout=60000,
    )


def get_dhl_result(page, tracking_number):
    direct_url = build_dhl_tracking_url(tracking_number)

    write_log(f"Opening direct DHL tracking URL for {tracking_number}")
    write_log(f"DHL URL: {direct_url}")

    page.bring_to_front()
    page.goto(direct_url, wait_until="domcontentloaded", timeout=60000)

    # Cookie consent first: the banner can cover the widget and block clicks.
    accept_cookie_banner(page, "DHL")

    # Then wait on DHL's ACTUAL readiness rather than a fixed 2.2s. If DHL is
    # having a slow day this sits patiently; if it answered in 800ms we move on
    # in 800ms. Terminal states (no result / error) break out immediately.
    state, elapsed = wait_for_dhl_page(page, tracking_number)
    write_log(
        f"DHL settled in state {state} after {elapsed:.1f}s for {tracking_number}."
    )

    if state == DHL_READY_NO_RESULT:
        save_page_text(page, tracking_number, "dhl_no_result_page")
        raise SkipShipment(
            "DHL reported no tracking information for this number."
        )

    if state == DHL_ERROR:
        take_screenshot(page, tracking_number, "dhl_error_page")
        save_page_text(page, tracking_number, "dhl_error_page")
        raise SkipShipment("DHL returned an error or blocked page.")

    # If the timeline result is already available, use it immediately.
    result = wait_for_dated_dhl_result(page, DHL_IMMEDIATE_CHECK_SECONDS)

    if result:
        write_log("DHL Event Log dates were available immediately.")
        save_page_text(page, tracking_number, "dhl_event_log_result")
        return result

    write_log(
        "No dated DHL Event Log result yet. Polling for up to "
        f"{DHL_RETRY_WAIT_SECONDS}s (exits as soon as a date appears)..."
    )

    # THE 30-SECOND STALL. This used to be a blind wait_for_timeout(34s) that
    # ran even when DHL had already rendered the answer one second later.
    # wait_for_dated_dhl_result polls once a second and returns immediately on
    # success, so the same worst case is preserved but the common case is fast.
    result = wait_for_dated_dhl_result(page, DHL_RETRY_WAIT_SECONDS)

    if result:
        write_log("DHL Event Log dates appeared while polling; no re-search needed.")
        save_page_text(page, tracking_number, "dhl_event_log_result")
        return result

    # Requirement: only re-run the search when there is strong evidence the
    # first attempt never loaded. If any shipment data is on screen, a second
    # search would discard a good page and cost another full round trip.
    current_text = _page_text(page)
    current_markers = dhl_data_markers(current_text)
    if current_markers["found"]:
        write_log(
            "Not re-searching {0}: shipment data is present on the page "
            "(details={1} timeline={2} dated_events={3}). Re-reading instead."
            .format(tracking_number, current_markers["details"],
                    current_markers["timeline"], current_markers["date_count"])
        )
        result = extract_event_log_result(page)
        if result:
            save_page_text(page, tracking_number, "dhl_event_log_result")
            return result
        write_log(
            "Shipment data was present but no dated ETA/ATA row could be read; "
            "searching again as a last resort."
        )

    write_log("Polling window elapsed. Clicking DHL Track/Search again...")
    click_dhl_track_again(page, tracking_number)
    accept_cookie_banner(page, "DHL", budget_seconds=1.5)

    state, elapsed = wait_for_dhl_page(page, tracking_number)
    write_log(
        f"DHL state after second search: {state} ({elapsed:.1f}s) for {tracking_number}."
    )

    if state == DHL_READY_NO_RESULT:
        save_page_text(page, tracking_number, "dhl_no_result_page")
        raise SkipShipment("DHL reported no tracking information for this number.")

    if state == DHL_PROCESSING:
        # Still on the interstitial when the ceiling hit — fall back to the
        # original 90s processing watcher rather than failing early.
        write_log("DHL is still processing; using the extended processing watcher.")
        if not wait_until_processing_finishes(page, tracking_number):
            take_screenshot(page, tracking_number, "dhl_processing_timeout")
            raise SkipShipment("DHL processing did not finish within 90 seconds.")

    result = wait_for_dated_dhl_result(page, DHL_FINAL_RESULT_WAIT_SECONDS)

    if not result:
        write_log("No Event Log date after second search. Reloading direct DHL URL once...")

        page.goto(direct_url, wait_until="domcontentloaded", timeout=60000)
        accept_cookie_banner(page, "DHL", budget_seconds=1.5)

        state, elapsed = wait_for_dhl_page(page, tracking_number)
        write_log(
            f"DHL state after reload: {state} ({elapsed:.1f}s) for {tracking_number}."
        )

        if state == DHL_READY_NO_RESULT:
            save_page_text(page, tracking_number, "dhl_no_result_page")
            raise SkipShipment("DHL reported no tracking information for this number.")

        if state == DHL_PROCESSING:
            if not wait_until_processing_finishes(page, tracking_number):
                take_screenshot(page, tracking_number, "dhl_final_processing_timeout")
                raise SkipShipment("Final DHL processing did not finish within 90 seconds.")

        result = wait_for_dated_dhl_result(page, DHL_FINAL_RESULT_WAIT_SECONDS)

    if not result:
        take_screenshot(page, tracking_number, "dhl_no_event_log_date")
        save_page_text(page, tracking_number, "dhl_no_event_log_date")
        raise SkipShipment("DHL returned no Estimated Delivery or Arrived Final Destination Event Log date after retry.")

    save_page_text(page, tracking_number, "dhl_event_log_result")
    return result


# ============================================================
# QATAR AIRWAYS CARGO TRACKING
# ============================================================

def parse_qatar_awb(raw_awb):
    """
    Normalize examples such as:
      157-50025474
      157 - 48824005
      15750025474

    Returns prefix 157 and the eight-digit main number.
    """
    digits = re.sub(r"\D", "", raw_awb or "")

    if len(digits) == 8:
        return QATAR_AWB_PREFIX, digits

    if len(digits) == 11 and digits.startswith(QATAR_AWB_PREFIX):
        return digits[:3], digits[3:]

    raise SkipShipment(
        f"Qatar Airways AWB must contain prefix 157 and an 8-digit main number: {raw_awb}"
    )


def qatar_result_date(page, status_name):
    """Extract a date from the same Qatar status banner/card."""
    pattern = re.compile(rf"^\s*{re.escape(status_name)}(?:\s*\([^)]*\))?\s+", re.I)
    labels = page.get_by_text(pattern, exact=False)
    dates = []

    for index in range(labels.count()):
        try:
            label = labels.nth(index)
            if not label.is_visible(timeout=500):
                continue

            text_candidates = []
            try:
                text_candidates.append(label.inner_text(timeout=1200))
            except Exception:
                pass

            for xpath in ["xpath=..", "xpath=../..", "xpath=../../.."]:
                try:
                    value = label.locator(xpath).first.inner_text(timeout=1200).strip()
                    if value:
                        text_candidates.append(value)
                except Exception:
                    continue

            for text in text_candidates:
                # Qatar examples: 23-Jul-2026 06:45 and 09-Apr-2026 16:40.
                for match in re.finditer(r"\b\d{1,2}-[A-Za-z]{3}-\d{4}\b", text):
                    try:
                        parsed = datetime.strptime(match.group(0), "%d-%b-%Y").strftime("%d/%m/%Y")
                        dates.append(parsed)
                    except ValueError:
                        continue
        except Exception:
            continue

    return latest_date(dates)


def qatar_result_ready(page):
    try:
        body_text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return False

    return bool(
        re.search(r"Estimated\s+Arrival\s*\([^)]*\)\s+\d{1,2}-[A-Za-z]{3}-\d{4}", body_text, re.I)
        or re.search(r"\bArrived\s*\([^)]*\)\s+\d{1,2}-[A-Za-z]{3}-\d{4}", body_text, re.I)
    )


def qatar_scopes(page):
    """Return the main Qatar page plus all child frames used by the tracking widget."""
    scopes = [page]
    for frame in page.frames:
        if frame != page.main_frame:
            scopes.append(frame)
    return scopes


def select_qatar_mawb_mode(page):
    """Ensure MAWB is selected, including when the Qatar form is inside an iframe."""
    for scope in qatar_scopes(page):
        mawb_radio = first_visible(
            [
                scope.get_by_role("radio", name=re.compile(r"^\s*MAWB\s*$", re.I)),
                scope.get_by_label(re.compile(r"^\s*MAWB\s*$", re.I)),
                scope.locator("input[type='radio'][value*='MAWB' i]"),
            ],
            1200,
        )

        if mawb_radio is not None:
            try:
                if not mawb_radio.is_checked():
                    mawb_radio.check(timeout=4000, force=True)
            except Exception:
                try:
                    mawb_radio.click(timeout=4000, force=True)
                except Exception:
                    pass
            write_log("Qatar MAWB mode confirmed.")
            return scope

        try:
            mawb_text = scope.get_by_text(
                re.compile(r"^\s*MAWB\s*$", re.I),
                exact=False,
            ).first
            if mawb_text.is_visible(timeout=700):
                write_log("Qatar MAWB mode is already selected.")
                return scope
        except Exception:
            continue

    raise SkipShipment("Qatar Airways MAWB option was not found.")


def verify_qatar_prefix(page, expected_prefix):
    """Verify the fixed Prefix 157 control in the main page or tracking iframe."""
    for scope in qatar_scopes(page):
        # Text displayed as one block, for example 'Prefix157'.
        try:
            prefix_text = scope.get_by_text(
                re.compile(rf"Prefix\s*{re.escape(expected_prefix)}", re.I),
                exact=False,
            ).first
            if prefix_text.is_visible(timeout=700):
                write_log(f"Qatar prefix confirmed: {expected_prefix}")
                return scope
        except Exception:
            pass

        # Editable/read-only prefix input fallback.
        for candidate in [
            scope.locator("input[name*='prefix' i]"),
            scope.locator("input[id*='prefix' i]"),
            scope.locator("input[maxlength='3']"),
        ]:
            try:
                control = candidate.first
                control.wait_for(state="visible", timeout=700)
                current = re.sub(r"\D", "", control.input_value() or "")
                if current != expected_prefix and control.is_editable():
                    control.fill(expected_prefix)
                    current = re.sub(r"\D", "", control.input_value() or "")
                if current == expected_prefix:
                    write_log(f"Qatar prefix confirmed: {expected_prefix}")
                    return scope
            except Exception:
                continue

    raise SkipShipment(f"Qatar Airways prefix {expected_prefix} was not visible on the MAWB form.")


def find_qatar_main_awb_field(page):
    """
    Find Qatar's tokenized AWB Number(s) input.

    Qatar uses a Salesforce/LWC token input. The visible placeholder may be on
    a combobox wrapper while the real editable input is nested inside it.
    """
    for scope in qatar_scopes(page):
        direct_candidates = [
            scope.get_by_placeholder("AWB Number(s)", exact=True),
            scope.get_by_placeholder(re.compile(r"AWB\s*Number", re.I)),
            scope.get_by_role("combobox", name=re.compile(r"AWB\s*Number", re.I)),
            scope.locator("input[placeholder='AWB Number(s)']"),
            scope.locator("input[placeholder*='AWB Number' i]"),
            scope.locator("input[role='combobox']"),
            scope.locator("input.slds-combobox__input"),
            scope.locator("lightning-base-combobox input"),
            scope.locator("lightning-input input[type='text']"),
        ]
        field = first_visible(direct_candidates, 1500)
        if field is not None:
            input_type = (field.get_attribute("type") or "text").lower()
            if input_type not in {"radio", "checkbox", "button", "submit", "hidden"}:
                return field, scope

        # Find the visible AWB Number(s) text/wrapper and then search inside its
        # nearest component/container for the real editable input.
        awb_text_candidates = [
            scope.get_by_text(re.compile(r"^\s*AWB\s+Number\(s\)\s*$", re.I)),
            scope.get_by_text(re.compile(r"AWB\s+Number", re.I), exact=False),
            scope.locator("[placeholder*='AWB Number' i]"),
        ]
        for text_candidate in awb_text_candidates:
            try:
                text_node = text_candidate.first
                text_node.wait_for(state="visible", timeout=1000)
                for xpath in [
                    "xpath=ancestor::*[contains(@class,'combobox')][1]",
                    "xpath=ancestor::*[contains(@class,'input')][1]",
                    "xpath=ancestor::*[self::div or self::lightning-base-combobox][1]",
                    "xpath=../..",
                    "xpath=../../..",
                ]:
                    try:
                        container = text_node.locator(xpath).first
                        nested = first_visible(
                            [
                                container.locator("input[role='combobox']"),
                                container.locator("input[type='text']"),
                                container.locator("input:not([type])"),
                                container.locator("[contenteditable='true']"),
                            ],
                            800,
                        )
                        if nested is not None:
                            return nested, scope
                    except Exception:
                        continue
            except Exception:
                continue

        # Final strict fallback: only wide, editable text/combobox controls.
        editable = scope.locator(
            "input:visible:not([type='hidden']):not([type='radio']):"
            "not([type='checkbox']):not([type='button']):not([type='submit']):"
            "not([type='reset']):not([disabled]), textarea:visible:not([disabled]), "
            "[contenteditable='true']:visible"
        )
        ranked = []
        for index in range(editable.count()):
            try:
                candidate = editable.nth(index)
                attrs = " ".join(filter(None, [
                    candidate.get_attribute("placeholder"),
                    candidate.get_attribute("aria-label"),
                    candidate.get_attribute("name"),
                    candidate.get_attribute("id"),
                    candidate.get_attribute("class"),
                    candidate.get_attribute("role"),
                ])).casefold()
                value = ""
                try:
                    value = candidate.input_value().strip()
                except Exception:
                    pass
                if "prefix" in attrs or re.sub(r"\D", "", value) == QATAR_AWB_PREFIX:
                    continue
                box = candidate.bounding_box()
                width = box["width"] if box else 0
                score = 0
                if "awb" in attrs:
                    score += 100
                if "combobox" in attrs:
                    score += 50
                if "number" in attrs:
                    score += 30
                if width >= 350:
                    score += 40
                elif width >= 200:
                    score += 20
                if width >= 180:
                    ranked.append((score, width, candidate))
            except Exception:
                continue
        if ranked:
            ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
            score, width, candidate = ranked[0]
            write_log(f"Qatar tokenized AWB input found (score={score}, width={width:.0f}).")
            return candidate, scope

    return None, None


def find_qatar_track_button(page, preferred_scope=None):
    scopes = []
    if preferred_scope is not None:
        scopes.append(preferred_scope)
    scopes.extend(scope for scope in qatar_scopes(page) if scope not in scopes)

    for scope in scopes:
        button = first_visible(
            [
                scope.get_by_role(
                    "button",
                    name=re.compile(r"^\s*Track\s+Shipment\(s\)\s*$", re.I),
                ),
                scope.get_by_role(
                    "button",
                    name=re.compile(r"^\s*Track\s+Shipment", re.I),
                ),
                scope.locator("button:has-text('Track Shipment(s)')"),
                scope.locator("button:has-text('Track Shipment')"),
                scope.locator("input[type='submit'][value*='Track Shipment' i]"),
            ],
            1500,
        )
        if button is not None:
            return button
    return None


def submit_qatar_awb(page, raw_awb):
    prefix, main_number = parse_qatar_awb(raw_awb)

    select_qatar_mawb_mode(page)
    verify_qatar_prefix(page, prefix)

    main_field, field_scope = find_qatar_main_awb_field(page)
    if main_field is None:
        take_screenshot(page, raw_awb, "qatar_awb_field_not_found")
        save_page_text(page, raw_awb, "qatar_awb_field_not_found")
        raise SkipShipment(
            "Qatar Airways AWB Number(s) field was not found in the page or its frames."
        )

    # Paste/fill only the 8-digit main number after the fixed prefix 157.
    # Example: 157 - 59759011 -> enter only 59759011 in AWB Number(s).
    # focus() avoids Qatar notification overlays intercepting pointer clicks.
    main_field.scroll_into_view_if_needed(timeout=4000)
    main_field.focus(timeout=4000)
    main_field.fill("")
    main_field.fill(main_number)
    main_field.dispatch_event("input")
    main_field.dispatch_event("change")

    entered_value = re.sub(r"\D", "", main_field.input_value() or "")
    if entered_value != main_number:
        raise SkipShipment(
            f"Qatar main AWB was not typed correctly. Expected {main_number}, found {entered_value}."
        )

    write_log(
        f"Qatar prefix is {prefix}; only the main AWB number was pasted into AWB Number(s): {main_number}"
    )

    main_field.press("Tab")
    track_button = find_qatar_track_button(page, field_scope)

    if track_button is not None:
        track_button.click(timeout=5000, force=True)
        write_log("Qatar Track Shipment(s) clicked.")
    else:
        main_field.press("Enter")
        write_log("Qatar tracking submitted with Enter.")


def extract_qatar_result(page):
    eta = qatar_result_date(page, "Estimated Arrival")
    ata = qatar_result_date(page, "Arrived")

    # Text fallback for the visible status banners.
    if not eta or not ata:
        try:
            body_text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            body_text = ""

        if not eta:
            match = re.search(
                r"Estimated\s+Arrival\s*\([^)]*\)\s+(\d{1,2}-[A-Za-z]{3}-\d{4})",
                body_text,
                re.I,
            )
            if match:
                eta = datetime.strptime(match.group(1), "%d-%b-%Y").strftime("%d/%m/%Y")

        if not ata:
            match = re.search(
                r"\bArrived\s*\([^)]*\)\s+(\d{1,2}-[A-Za-z]{3}-\d{4})",
                body_text,
                re.I,
            )
            if match:
                ata = datetime.strptime(match.group(1), "%d-%b-%Y").strftime("%d/%m/%Y")

    if not eta and not ata:
        return None

    status = "Arrived" if ata else "Estimated Arrival"
    return {
        "provider": "Qatar Airways",
        "tracking_status": status,
        "eta": eta,
        "ata": ata,
    }


def get_qatar_result(page, tracking_number):
    write_log(f"Opening Qatar Airways Cargo tracking for {tracking_number}")
    page.bring_to_front()
    page.goto(QATAR_BASE_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)

    accept_cookie_banner(page, "Qatar Airways")

    for attempt in range(1, QATAR_MAX_ATTEMPTS + 1):
        submit_qatar_awb(page, tracking_number)

        end_time = time.time() + QATAR_RESULT_WAIT_SECONDS
        while time.time() < end_time:
            result = extract_qatar_result(page)
            if result:
                write_log(
                    f"Qatar result: status={result['tracking_status']} | "
                    f"ETA={result.get('eta')} | ATA={result.get('ata')}"
                )
                save_page_text(page, tracking_number, "qatar_result")
                return result
            page.wait_for_timeout(1000)

        if attempt < QATAR_MAX_ATTEMPTS:
            write_log(
                f"No Qatar result on attempt {attempt}. Waiting "
                f"{QATAR_RETRY_WAIT_SECONDS} seconds before retry..."
            )
            # Short, deliberate pause so the carrier is not hammered, then a
            # condition-based settle instead of another blind 2s.
            page.wait_for_timeout(QATAR_RETRY_WAIT_SECONDS * 1000)
            page.goto(QATAR_BASE_URL, wait_until="domcontentloaded", timeout=60000)
            wait_until_settled(page, page_has_content, PAGE_SETTLE_MAX_SECONDS)
            accept_cookie_banner(page, "Qatar Airways", budget_seconds=1.5)

    take_screenshot(page, tracking_number, "qatar_no_result")
    save_page_text(page, tracking_number, "qatar_no_result")
    raise SkipShipment("Qatar Airways returned no Estimated Arrival or Arrived date.")


# ============================================================
# AIR FRANCE / KLM / MARTINAIR  (AFKL myCargo) - prefixes 057 and 074
# ============================================================
#
# One portal serves both airlines: the search box itself says
# "057-... or 074-...", so a single integration covers two carriers.
# Tracking is public — the myCargo login is for booking, not for this.
#
# The entry page and controls below are taken from the live site. The RESULT
# layout has not been seen yet, so extraction is deliberately data-first: it
# looks for dated content near arrival labels rather than for a fixed
# selector, and dumps what it did find when it comes up empty. That is the
# lesson from DHL, where guessed selectors cost days.

# Two entry points, both confirmed to carry an air-waybill box:
#   singlesearch — the dedicated tracking page, tried first
#   homepage     — the Track & trace tab, used if the first does not render
#                  its input (a redirect, a region gate, or a layout change)
# Trying both costs nothing on the happy path and saves the run when AFKL
# reshuffles its site, which cargo portals do without notice.
# The direct shipment page (see build_afkl_detail_url) is the primary route.
# singlesearch stays as the one fallback if that page cannot be confirmed.
# The homepage is deliberately NOT here any more: it never carried the
# shipment, and reaching for it after a transport error is what turned one
# failed navigation into a lost shipment.
AFKL_URLS = [
    "https://www.afklcargo.com/mycargo/shipment/singlesearch",
]
AFKL_BASE_URL = AFKL_URLS[0]
AFKL_RESULT_WAIT_SECONDS = 40
AFKL_MAX_ATTEMPTS = 2

# The shipment page can be addressed directly, which is the route a person
# uses and the one that works. The search form remains only as a last resort.
AFKL_DETAIL_ATTEMPTS = 3
AFKL_DETAIL_READY_MS = 30000

# Chromium raises these against some carrier servers on a rapid second
# navigation. They mean "the transport had a bad moment", not "the page is
# wrong", so the same url is retried rather than a different one being tried.
TRANSIENT_NAV_ERRORS = (
    "ERR_HTTP2_PROTOCOL_ERROR", "ERR_CONNECTION_RESET", "ERR_NETWORK_CHANGED",
    "ERR_EMPTY_RESPONSE", "ERR_CONNECTION_CLOSED", "ERR_TIMED_OUT",
    "ERR_CONNECTION_TIMED_OUT", "ERR_SOCKET_NOT_CONNECTED",
)

# ============================================================
# SIMPLE AWB PORTALS
# ============================================================
#
# Most airline cargo sites are the same shape: open a page, dismiss a cookie
# panel, type the air waybill, press Track, read dates off the result. AFKL and
# Astral are both exactly that, so they share one implementation and differ
# only by configuration. Adding the next carrier is an entry in PORTALS, not
# another copy of this function.
#
# The labels below are informed by each site's own wording. Where a result page
# has not been seen, extraction stays data-first — it looks for dated content
# near arrival labels rather than a fixed selector, and dumps what it did find
# when it comes up empty, so the labels can be corrected from evidence.

GENERIC_ETA_LABELS = [
    r"Estimated\s+(?:Time\s+of\s+)?Arrival", r"\bETA\b",
    r"Estimated\s+Delivery", r"Expected\s+(?:Arrival|Delivery)",
    r"Scheduled\s+Arrival", r"Planned\s+Arrival", r"Due\s+Date",
]
GENERIC_ATA_LABELS = [
    r"Actual\s+(?:Time\s+of\s+)?Arrival", r"\bATA\b",
    r"\bArrived\b", r"Delivered", r"Received\s+from\s+Flight",
    r"RCF\b", r"DLV\b",
]
GENERIC_NO_RESULT = [
    r"no\s+(?:result|shipment|record|data)s?\s+(?:found|available)",
    r"could\s+not\s+be\s+found", r"not\s+found",
    r"invalid\s+(?:air\s*waybill|awb)",
    r"check\s+the\s+(?:air\s*waybill|awb)", r"no\s+information",
    r"please\s+enter\s+a\s+valid",
]

PORTALS = {
    "AFKL": {
        "label": "AFKL myCargo",
        "urls": AFKL_URLS,
        # The box says "057-... or 074-...", so one portal serves both airlines.
        "placeholder": r"05\s*7|07\s*4|AWB",
        "button": r"^\s*(Check\s+status|Track)\s*$",
        "dashed": True,
        "wait": 40,
        "attempts": 2,
        # This page prints no years and no "ETA" label, so the generic reader
        # cannot see it at all; extract_portal_result sends AFKL to
        # _read_afkl_page instead. Kept as a flag rather than a callable so
        # PORTALS stays plain data.
        "own_reader": True,
        # The shipment page can be opened directly by air waybill, which is
        # what a person does and what actually works. See open_afkl_detail.
        "detail_url": True,
    },
    "ASTRAL": {
        "label": "Astral Aviation",
        "urls": ["https://astral-aviation.com/track-cargo/"],
        # Its box reads "Enter 11 Digit AWB Number eg XXX-XXXXXXXX".
        "placeholder": r"AWB\s*Number|11\s*Digit",
        "button": r"^\s*Track\s*$",
        "dashed": True,
        "wait": 40,
        "attempts": 2,
    },
}


AFKL_DETAIL_URL = "https://www.afklcargo.com/mycargo/shipment/detail/{0}"


def build_afkl_detail_url(tracking_number):
    """
    The direct shipment page for an air waybill.

        05705765454   -> .../detail/057-05765454
        057-05765454  -> .../detail/057-05765454
        057 0576 5454 -> .../detail/057-05765454

    Returns None when the reference cannot be a valid 11-digit AWB, so the
    caller falls back to the search form rather than requesting a URL built
    from a number that was never an air waybill. The AWB itself is never
    altered — only its punctuation is normalised.
    """
    digits = re.sub(r"\D", "", str(tracking_number or ""))
    if len(digits) != 11:
        return None
    return AFKL_DETAIL_URL.format("{0}-{1}".format(digits[:3], digits[3:11]))


def page_is_afkl_detail(page, tracking_number):
    """
    Is this really the requested shipment's page?

    Two things have to hold: the air waybill has to appear on the page, and
    the page has to carry the furniture of a result rather than an error or a
    still-booting shell. Navigating successfully is not the same as arriving
    at the right shipment, and a wrong-shipment page must never reach the
    extraction step.
    """
    try:
        text = _page_text(page)
    except Exception:
        return False
    if len(text.strip()) < 120:
        return False

    digits = re.sub(r"\D", "", str(tracking_number or ""))
    stripped = re.sub(r"\D", "", text)
    if digits and digits not in stripped:
        return False

    return bool(re.search(
        r"Progress\s+details|Flight\s+schedule|Estimated\s+Pick\s*up\s+time|"
        r"Checked-in|EN\s+ROUTE|DELIVERED", text, re.I))


# ── The navigation ladder ────────────────────────────────────────────
#
# Four strategies, one attempt each, in order, stopping the moment one loads
# the requested shipment. Bounded and deterministic: no strategy is tried
# twice and there is no fifth.
#
#   1  the browser we already have, direct detail URL
#   2  a FRESH context on that browser, same URL      (state/connection reuse)
#   3  a Chromium launched with --disable-http2       (HTTP/2 framing)
#   4  branded Microsoft Edge, channel="msedge"       (browser build)
#
# Each step targets a different hypothesis, so whichever one succeeds tells us
# what was actually wrong. Applying all of them at once would fix the symptom
# and teach us nothing.

AFKL_NAV_TIMEOUT_MS = 45000


class AfklNavigationError(Exception):
    """
    Could not reach the AFKL page. Says nothing about the air waybill.

    Carries the per-attempt diagnostics so the run log can show which
    strategies were tried and how each one failed.
    """

    def __init__(self, tracking_number, attempts):
        self.tracking_number = tracking_number
        self.attempts = attempts
        Exception.__init__(self, (
            "AFKL NAVIGATION ERROR for {0}: none of the {1} navigation "
            "strategies could load the shipment page. {2}"
        ).format(tracking_number, len(attempts),
                 " | ".join("#{0} {1}: {2}".format(
                     a["attempt"], a["strategy"], a["error"] or a["outcome"])
                     for a in attempts)))


def _afkl_attempt(page, url, tracking_number, label, strategy, number,
                  http2_disabled=False, channel="chromium"):
    """
    One navigation attempt. Returns a diagnostic record; never raises.

    Everything the brief asked to see is recorded: which strategy, whether
    HTTP/2 was disabled, the URL, the exception, the response status, the
    final URL, whether the document reached DOMContentLoaded, and how long it
    took.
    """
    record = {
        "attempt": number, "strategy": strategy, "channel": channel,
        "http2_disabled": http2_disabled, "url": url,
        "error": None, "status": None, "final_url": None,
        "dom_content_loaded": False, "loaded": False,
        "awb_verified": False, "elapsed_ms": None, "outcome": "not run",
    }
    started = time.time()
    try:
        response = page.goto(url, wait_until="domcontentloaded",
                             timeout=AFKL_NAV_TIMEOUT_MS)
        record["dom_content_loaded"] = True
        if response is not None:
            record["status"] = response.status
        record["final_url"] = page.url
        try:
            page.wait_for_load_state("load", timeout=8000)
            record["loaded"] = True
        except Exception:
            pass

        wait_until_settled(page, page_has_content, PAGE_SETTLE_MAX_SECONDS)
        accept_cookie_banner(page, label)

        # The shipment renders after the app fetches it, so identity is
        # confirmed rather than assumed from a 200.
        settled = wait_for_any(
            page,
            [("the shipment detail page",
              lambda: page_is_afkl_detail(page, tracking_number))],
            AFKL_DETAIL_READY_MS, poll_ms=500,
            reason="the {0} shipment page for {1}".format(label, tracking_number),
        )
        record["awb_verified"] = bool(settled)
        record["final_url"] = page.url
        record["outcome"] = "loaded and verified" if settled else (
            "page loaded but {0} could not be confirmed on it".format(tracking_number))
    except Exception as error:
        record["error"] = str(error).split("\n")[0][:200]
        record["outcome"] = "navigation exception"
    record["elapsed_ms"] = int((time.time() - started) * 1000)
    return record


def _log_afkl_attempt(record):
    write_log(
        "AFKL nav | attempt={attempt} | strategy={strategy} | channel={channel} | "
        "http2_disabled={http2_disabled} | status={status} | dcl={dom_content_loaded} | "
        "load={loaded} | awb_verified={awb_verified} | {elapsed_ms}ms | "
        "final_url={final_url} | outcome={outcome}{err}".format(
            err=(" | error=" + record["error"]) if record["error"] else "",
            **record))


def open_afkl_detail(page, config, tracking_number):
    """
    Go straight to the shipment page, through a bounded fallback ladder.

    Returns the page that holds the confirmed shipment — usually the one
    passed in, but a later strategy may hand back a page on a different
    browser, which the caller then reads instead.

    Raises AfklNavigationError when every strategy fails. That is deliberately
    NOT "no shipment found": the difference between "the carrier says this AWB
    does not exist" and "we could not reach the carrier" is the difference
    between a real answer and a broken pipe, and 057-05765454 was reported as
    the former on the strength of the latter.
    """
    url = build_afkl_detail_url(tracking_number)
    if url is None:
        write_log(
            "{0}: {1} is not an 11-digit air waybill, so no detail URL can be "
            "built.".format(config["label"], tracking_number))
        return None

    label = config["label"]
    attempts = []
    write_log("{0}: opening the shipment page directly — {1}".format(label, url))

    # ── 1 · the browser we already have ─────────────────────────────
    record = _afkl_attempt(page, url, tracking_number, label,
                           "existing page", 1)
    _log_afkl_attempt(record)
    attempts.append(record)
    if record["awb_verified"]:
        write_log("{0}: shipment page confirmed on attempt 1.".format(label))
        return page

    # ── 2 · a fresh context on the same browser ─────────────────────
    # Connection reuse and cached state are the cheapest explanations for a
    # protocol error on one page and not another, so this is tried before
    # anything heavier.
    extra_pages = []
    browser = None
    try:
        browser = page.context.browser
    except Exception as error:
        note_suppressed("reaching the browser for a fresh AFKL context", error)

    if browser is not None:
        try:
            context = browser.new_context()
            fresh = context.new_page()
            extra_pages.append((context, fresh))
            record = _afkl_attempt(fresh, url, tracking_number, label,
                                   "fresh context", 2)
            _log_afkl_attempt(record)
            attempts.append(record)
            if record["awb_verified"]:
                write_log("{0}: shipment page confirmed on attempt 2 "
                          "(a fresh context was enough).".format(label))
                return fresh
        except Exception as error:
            attempts.append({"attempt": 2, "strategy": "fresh context",
                             "channel": "chromium", "http2_disabled": False,
                             "url": url, "error": str(error)[:200],
                             "status": None, "final_url": None,
                             "dom_content_loaded": False, "loaded": False,
                             "awb_verified": False, "elapsed_ms": None,
                             "outcome": "could not create a context"})
            _log_afkl_attempt(attempts[-1])

    # Attempts 3 and 4 need their own browser. They are only worth the launch
    # cost when the failure so far looks like transport rather than content.
    transport = any(a["error"] and any(t in a["error"] for t in TRANSIENT_NAV_ERRORS)
                    for a in attempts)

    # ── 3 · Chromium with HTTP/2 disabled ───────────────────────────
    if transport:
        record, kept = _afkl_side_browser(
            page, url, tracking_number, label, 3,
            "chromium --disable-http2", channel=None,
            args=["--disable-http2"], http2_disabled=True)
        attempts.append(record)
        if record["awb_verified"] and kept is not None:
            write_log("{0}: shipment page confirmed on attempt 3 — HTTP/2 was "
                      "the problem.".format(label))
            return kept
    else:
        write_log("{0}: skipping the HTTP/2 strategy — the failures so far are "
                  "not transport errors.".format(label))

    # ── 4 · branded Microsoft Edge ──────────────────────────────────
    record, kept = _afkl_side_browser(
        page, url, tracking_number, label, 4,
        "microsoft edge (msedge channel)", channel="msedge",
        args=[], http2_disabled=False)
    attempts.append(record)
    if record["awb_verified"] and kept is not None:
        write_log("{0}: shipment page confirmed on attempt 4 — the bundled "
                  "Chromium was the problem, branded Edge works.".format(label))
        return kept

    save_page_text(page, tracking_number, "afkl_navigation_error")
    take_screenshot(page, tracking_number, "afkl_navigation_error")
    raise AfklNavigationError(tracking_number, attempts)


def _afkl_side_browser(page, url, tracking_number, label, number, strategy,
                       channel=None, args=None, http2_disabled=False):
    """
    Run one attempt in a browser of its own. Returns (record, page_or_None).

    A clean temporary profile every time — Playwright's default. The operator's
    own Edge profile is never touched.
    """
    record = {"attempt": number, "strategy": strategy,
              "channel": channel or "chromium", "http2_disabled": http2_disabled,
              "url": url, "error": None, "status": None, "final_url": None,
              "dom_content_loaded": False, "loaded": False,
              "awb_verified": False, "elapsed_ms": None, "outcome": "not run"}
    started = time.time()
    try:
        playwright = getattr(page.context.browser, "_playwright", None)
    except Exception:
        playwright = None
    if playwright is None:
        record["outcome"] = "no playwright handle available for a side browser"
        record["elapsed_ms"] = int((time.time() - started) * 1000)
        _log_afkl_attempt(record)
        return record, None

    launch = {"headless": True}
    if channel:
        launch["channel"] = channel
    if args:
        launch["args"] = list(args)

    browser = None
    try:
        browser = playwright.chromium.launch(**launch)
        side = browser.new_page()
        inner = _afkl_attempt(side, url, tracking_number, label,
                              strategy, number, http2_disabled=http2_disabled,
                              channel=channel or "chromium")
        _log_afkl_attempt(inner)
        if inner["awb_verified"]:
            # The caller reads this page, so the browser must stay open. It is
            # closed by the caller's own cleanup at the end of the shipment.
            AFKL_SIDE_BROWSERS.append(browser)
            return inner, side
        browser.close()
        return inner, None
    except Exception as error:
        record["error"] = str(error).split("\n")[0][:200]
        record["outcome"] = "could not launch"
        record["elapsed_ms"] = int((time.time() - started) * 1000)
        _log_afkl_attempt(record)
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        return record, None


# Side browsers opened by strategies 3 and 4, closed when the run ends.
AFKL_SIDE_BROWSERS = []


def close_afkl_side_browsers():
    while AFKL_SIDE_BROWSERS:
        browser = AFKL_SIDE_BROWSERS.pop()
        try:
            browser.close()
        except Exception as error:
            note_suppressed("closing an AFKL side browser", error)


def portal_awb(tracking_number, dashed=True):
    """XXX-XXXXXXXX when the portal wants it dashed, digits only otherwise."""
    digits = re.sub(r"\D", "", str(tracking_number or ""))
    if len(digits) < 11:
        return str(tracking_number or "").strip()
    return "{0}-{1}".format(digits[:3], digits[3:11]) if dashed else digits[:11]


def find_portal_input(page, placeholder):
    return first_visible(
        [
            page.get_by_placeholder(re.compile(placeholder, re.I)),
            page.get_by_label(re.compile(r"Air\s*waybill|AWB", re.I)),
            page.locator("input[name*='awb' i], input[id*='awb' i]"),
            page.locator("input[type='text']:visible"),
        ],
        PROBE_TIMEOUT_MS * 3,
    )


def open_portal(page, config, tracking_number):
    """Open whichever entry point actually shows the air waybill box."""
    problems = []
    for url in config["urls"]:
        # Chromium raises ERR_HTTP2_PROTOCOL_ERROR against some carrier servers
        # on a rapid second navigation — it hit both AFKL URLs on the retry and
        # lost a shipment whose number had already been accepted. Backing off
        # and trying again clears it; the error is transport, not the site.
        navigated = False
        for nav_attempt in range(1, 4):
            try:
                page.goto(url, wait_until="domcontentloaded",
                          timeout=NAVIGATION_TIMEOUT_MS)
                navigated = True
                break
            except Exception as error:
                message = str(error)
                transient = any(token in message
                                for token in TRANSIENT_NAV_ERRORS)
                if transient and nav_attempt < 3:
                    write_log(
                        "{0}: {1} on {2} (attempt {3}). Backing off {4}s."
                        .format(config["label"], message.split(" at ")[0][:60],
                                url, nav_attempt, nav_attempt * 3))
                    page.wait_for_timeout(nav_attempt * 3000)
                    continue
                problems.append("{0}: {1}".format(url, message[:90]))
                break
        if not navigated:
            continue

        # Give the app a moment to hydrate before looking for anything: an
        # Angular form that has not booted yet will ignore whatever we type.
        wait_until_settled(page, page_has_content, PAGE_SETTLE_MAX_SECONDS)
        if not accept_cookie_banner(page, config["label"]):
            # Not fatal — the panel sits at the bottom and rarely blocks the
            # form — but worth recording, because it was still on screen in the
            # failure screenshot and may cover controls on a small window.
            write_log(f"{config['label']}: no cookie panel was dismissed.")

        field = find_portal_input(page, config["placeholder"])
        if field is not None:
            if url != config["urls"][0]:
                write_log(f"{config['label']}: used the fallback entry point {url}")
            return field
        problems.append("{0}: no air waybill box on the page".format(url))

    save_page_text(page, tracking_number, config["label"].lower() + "_no_input")
    raise SkipShipment(
        "No {0} air waybill box was found. Tried: {1}".format(
            config["label"], " | ".join(problems)))


def type_into(field, value, description=""):
    """
    Put a value into a framework-managed input and CONFIRM it stuck.

    myCargo is an Angular app. fill() writes straight to the DOM node, and if
    change detection does not see it the form stays invalid — which is exactly
    what the failure screenshot showed: an empty box and a greyed-out Track
    button. Typing key by key fires the real input events the framework is
    listening for.

    Returns the value actually in the field, so the caller can tell the
    difference between "typed" and "accepted".
    """
    field.click(timeout=CLICK_TIMEOUT_MS)
    try:
        field.fill("")
    except Exception as error:
        note_suppressed("clearing {0}".format(description or "the input"), error)

    try:
        field.press_sequentially(value, delay=55)
    except Exception:
        # Older Playwright builds name it type().
        try:
            field.type(value, delay=55)
        except Exception as error:
            note_suppressed("typing into {0}".format(description or "the input"), error)
            field.fill(value)

    try:
        landed = (field.input_value() or "").strip()
    except Exception:
        landed = ""

    if re.sub(r"\D", "", landed) != re.sub(r"\D", "", value):
        # Last resort: set it and raise the events the framework expects.
        try:
            field.evaluate(
                "(el, v) => { const setter = Object.getOwnPropertyDescriptor("
                "window.HTMLInputElement.prototype, 'value').set;"
                " setter.call(el, v);"
                " el.dispatchEvent(new Event('input', {bubbles:true}));"
                " el.dispatchEvent(new Event('change', {bubbles:true}));"
                " el.dispatchEvent(new Event('blur', {bubbles:true})); }",
                value,
            )
            landed = (field.input_value() or "").strip()
        except Exception as error:
            note_suppressed("forcing the value into {0}".format(
                description or "the input"), error)

    return landed


def wait_until_enabled(page, locator, max_ms, description):
    """
    Wait for a control to become usable.

    A disabled Track button is the form telling us it has not accepted the
    input yet. Clicking it regardless is what made the previous run look as
    though nothing happened at all.
    """
    waited = 0
    while waited < max_ms:
        try:
            if locator.is_enabled():
                return True
        except Exception:
            pass
        page.wait_for_timeout(200)
        waited += 200
    write_log(f"{description} did not become enabled within {max_ms}ms.")
    return False


def submit_portal_awb(page, field, config, tracking_number):
    formatted = portal_awb(tracking_number, config.get("dashed", True))
    landed = type_into(field, formatted, f"the {config['label']} air waybill box")

    if re.sub(r"\D", "", landed) != re.sub(r"\D", "", formatted):
        save_page_text(page, tracking_number, config["label"].lower() + "_not_typed")
        raise SkipShipment(
            "The {0} air waybill box would not accept the number "
            "(typed '{1}', field holds '{2}').".format(
                config["label"], formatted, landed))

    write_log(f"{config['label']} air waybill accepted: {landed}")

    button = first_visible(
        [page.get_by_role("button", name=re.compile(config["button"], re.I)),
         page.locator("button:has-text('Track'), input[type='submit']")],
        PROBE_TIMEOUT_MS * 3,
    )

    if button is None:
        field.press("Enter")
        return

    # The button is disabled until the form validates. Give it a moment.
    if not wait_until_enabled(page, button, 6000, f"The {config['label']} Track button"):
        write_log(
            f"{config['label']}: Track is still disabled after the number was "
            "entered; submitting with Enter instead."
        )
        field.press("Enter")
        return

    click_postback(button, f"{config['label']} search")


def afkl_destination(text):
    """
    The final station on the air waybill.

    Read in order of reliability: the pick-up line names it outright, the
    header prints "BRU -> JRO", and the last flight leg ends there.
    """
    found = re.search(r"Estimated\s+Pick\s*up\s+time\s+([A-Z]{3})\s*:", text, re.I)
    if found:
        return found.group(1).upper()

    legs = re.findall(r"\b([A-Z]{3})\s*-\s*([A-Z]{3})\b", text)
    if legs:
        return legs[-1][1].upper()

    # The header prints "BRU -> JRO". Both codes must be standalone words or
    # the pattern reads "NCE" out of "AIR FRANCE" and filters every arrival row
    # against a station that does not exist.
    header = re.search(
        r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])\s*[^\w\s]{0,3}\s*"
        r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])", text)
    return header.group(2).upper() if header else None


def _read_afkl_page(page, provider):
    """
    Read an AFKL myCargo result.

    The generic label search cannot read this page. It prints no years, and it
    labels nothing "ETA" or "Estimated Arrival" — arrival sits in a Progress
    details table as a station code, the word ARRIVAL, and a date that is
    prefixed "Estimated:" while it is still a forecast and bare once it has
    actually happened. That prefix is the whole distinction between ETA and
    ATA here, so it is what this reads.
    """
    text = _page_text(page)
    if len(text.strip()) < 120:
        return None

    if _matches(text, GENERIC_NO_RESULT):
        return {"provider": provider, "tracking_status": "No result",
                "eta": None, "ata": None, "no_result": True}

    # Only read this page structurally when it IS the myCargo layout. Without
    # the gate the row scan treats the sentence "Estimated Time of Arrival" as
    # a progress row and, because that phrasing carries no "Estimated:" prefix,
    # files the estimate as an actual arrival — a wrong ATA, which is worse
    # than no ATA. Any other AFKL layout goes to the label reader instead.
    if not re.search(r"Progress\s+details|Flight\s+schedule|"
                     r"Estimated\s+Pick\s*up\s+time", text, re.I):
        return _read_generic_portal_page(page, provider)

    destination = afkl_destination(text)
    eta = ata = None

    # Walk the Progress details rows, carrying the station heading down the
    # rows that belong to it — the station is printed once per block, not on
    # every row.
    station = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^([A-Z]{3})\b", stripped)
        if heading:
            station = heading.group(1)
        # "of Arrival" inside a sentence is not a progress row.
        if not re.search(r"\bARRIVAL\b", stripped, re.I) or re.search(
                r"\bof\s+Arrival\b", stripped, re.I):
            continue
        if destination and station != destination:
            continue
        dates = extract_all_dates(stripped, allow_yearless=True)
        if not dates:
            continue
        value = dates[-1][1]
        if re.search(r"Estimated\s*:", stripped, re.I):
            eta = eta or value
        else:
            ata = value          # an actual arrival supersedes any estimate

    # Fallbacks, in descending order of directness.
    if not eta:
        pickup = re.search(
            r"Estimated\s+Pick\s*up\s+time\s+[A-Z]{3}\s*:\s*(.{0,24})", text, re.I)
        if pickup:
            dates = extract_all_dates(pickup.group(1), allow_yearless=True)
            if dates:
                eta = dates[0][1]
    if not eta:
        ready = re.search(
            r"ready\s+for\s+pick\s*up\s+after\s*:?\s*(.{0,24})", text, re.I)
        if ready:
            dates = extract_all_dates(ready.group(1), allow_yearless=True)
            if dates:
                eta = dates[0][1]
    if not eta and destination:
        # Last leg of the flight schedule: "CDG - JRO  AF0877  04 SEP 10:15 - 04 SEP 20:15"
        leg = re.search(
            r"[A-Z]{3}\s*-\s*" + destination + r"\b(.{0,90})", text, re.I)
        if leg:
            dates = extract_all_dates(leg.group(1), allow_yearless=True)
            if dates:
                eta = dates[-1][1]

    if not eta and not ata:
        # Nothing structural here. This may be a different AFKL layout — one
        # that does label its dates — so try the label reader before giving up.
        return _read_generic_portal_page(page, provider)

    # "On Time: Your shipment has been delivered before LAT" is about the
    # shipper meeting the latest acceptance time, not about delivery, and the
    # progress bar prints a greyed-out "Delivered" milestone on every shipment.
    # Both said DELIVERED for a shipment whose header read EN ROUTE, so they
    # are removed before the status is read and the live states are checked
    # ahead of the terminal one.
    status_text = re.sub(r"delivered\s+before\s+LAT", " ", text, flags=re.I)
    status_text = re.sub(r"Checked-in\b.*?\bDelivered\b", " ", status_text,
                         flags=re.I | re.S)
    status = "Estimated arrival"
    for pattern in (r"\bEN\s+ROUTE\b", r"\bIN\s+TRANSIT\b",
                    r"\bCHECKED[-\s]?IN\b", r"\bDELIVERED\b", r"\bARRIVED\b"):
        found = re.search(pattern, status_text, re.I)
        if found:
            status = " ".join(found.group(0).split()).title()
            break
    if ata and status == "Estimated arrival":
        status = "Arrived"

    write_log(
        "AFKL myCargo: destination={0} ETA={1} ATA={2} status={3}".format(
            destination or "unknown", eta, ata, status))
    return {"provider": provider, "tracking_status": status,
            "eta": eta, "ata": ata}


def extract_portal_result(page, provider):
    """
    Read arrival dates from whatever the result page rendered.

    Returns None until something dated and relevant is on screen, so the caller
    keeps polling rather than acting on a half-drawn page.
    """
    if provider == "AFKL":
        return _read_afkl_page(page, provider)

    return _read_generic_portal_page(page, provider)


def _read_generic_portal_page(page, provider):
    """The label-driven reader: works wherever the value follows its label."""
    text = _page_text(page)
    if len(text.strip()) < 120:
        return None

    if _matches(text, GENERIC_NO_RESULT):
        return {"provider": provider, "tracking_status": "No result",
                "eta": None, "ata": None, "no_result": True}

    # Forward-biased: on these layouts the value follows its label.
    eta = extract_date_after_label(text, GENERIC_ETA_LABELS)
    ata = extract_date_after_label(text, GENERIC_ATA_LABELS)
    if not eta and not ata:
        return None

    # An ATA identical to the ETA usually means the estimate was read twice.
    if eta and ata and eta == ata and not re.search(
            r"Actual\s+(?:Time\s+of\s+)?Arrival|\bATA\b|Delivered|\bRCF\b",
            text, re.I):
        ata = None

    status = "Arrived" if ata else "Estimated arrival"
    for pattern in (r"Delivered", r"\bArrived\b", r"In\s+transit", r"Departed",
                    r"Booked", r"Manifested", r"Received\s+from\s+shipper"):
        found = re.search(pattern, text, re.I)
        if found:
            status = found.group(0).strip().title()
            break

    return {"provider": provider, "tracking_status": status, "eta": eta, "ata": ata}


def get_portal_result(page, provider, tracking_number):
    config = PORTALS[provider]
    airline = (airline_from_awb(tracking_number)[1] or {}).get("name", config["label"])
    slug = provider.lower()
    write_log(f"Opening {config['label']} tracking for {tracking_number} ({airline})")
    page.bring_to_front()

    for attempt in range(1, config.get("attempts", 2) + 1):
        # AFKL: go straight to the shipment page. Everything after this point
        # — the wait loop, the extraction, the ETA/ATA rules — is unchanged and
        # simply runs against the right page instead of a search form.
        direct = False
        if config.get("detail_url"):
            # A navigation failure is raised, not swallowed. The search form is
            # only reached when the AWB could not produce a detail URL at all —
            # never as a way of papering over a transport error.
            landed = open_afkl_detail(page, config, tracking_number)
            if landed is not None:
                page = landed          # a later strategy may hand back its own page
                direct = True

        if not direct:
            if config.get("detail_url"):
                write_log(
                    "{0}: no detail URL could be built for {1}; using the "
                    "search form.".format(config["label"], tracking_number))
            field = open_portal(page, config, tracking_number)
            submit_portal_awb(page, field, config, tracking_number)

        end_time = time.time() + config.get("wait", 40)
        while time.time() < end_time:
            result = extract_portal_result(page, provider)
            if result and result.get("no_result"):
                save_page_text(page, tracking_number, slug + "_no_result")
                raise SkipShipment(
                    f"{airline} reported no information for this air waybill.")
            if result:
                write_log(
                    f"{config['label']} result: status={result['tracking_status']} | "
                    f"ETA={result.get('eta')} | ATA={result.get('ata')}"
                )
                save_page_text(page, tracking_number, slug + "_result")
                return result
            page.wait_for_timeout(1000)

        # Record what the page showed on THIS attempt. Previously the dump only
        # ran after the final attempt, so when the retry died on a navigation
        # error the evidence from the successful attempt was lost entirely —
        # which is exactly what happened to 057-05765454.
        save_page_text(page, tracking_number,
                       "{0}_attempt{1}".format(slug, attempt))
        describe_page_dates(page, config["label"], tracking_number)

        if attempt < config.get("attempts", 2):
            write_log(f"No {config['label']} result on attempt {attempt}. Retrying...")
            page.wait_for_timeout(3000)

    # Record exactly what the page did show, so labels are corrected from
    # evidence rather than guessed at a second time.
    take_screenshot(page, tracking_number, slug + "_no_result")
    save_page_text(page, tracking_number, slug + "_no_result")
    describe_page_dates(page, config["label"], tracking_number)
    raise SkipShipment(f"{airline} returned no arrival date that could be read.")


def extract_afkl_result(page, provider="AFKL"):
    """Public name for the AFKL reader; the logic lives in _read_afkl_page."""
    return _read_afkl_page(page, provider)


def get_afkl_result(page, tracking_number):
    return get_portal_result(page, "AFKL", tracking_number)


def describe_page_dates(page, label, tracking_number):
    """
    Log every date on the page and the words around it.

    When a carrier's labels differ from what was expected, this turns the next
    run log into the answer instead of another round of guessing.
    """
    try:
        text = _page_text(page)
        dates = extract_all_dates(text)
        write_log(f"--- {label}: dates visible for {tracking_number} ---")
        if not dates:
            write_log("    (no dates on the page at all)")
        for position, parsed, raw in dates[:25]:
            around = " ".join(text[max(0, position - 70):position + 40].split())
            write_log(f"    {parsed}  <-  ...{around}...")
        write_log(f"--- end of {label} date list ---")
    except Exception as error:
        note_suppressed(f"listing {label} dates", error)


def get_provider_result(provider_pages, shipment):
    provider = shipment.get("provider") or carrier_provider(
        shipment.get("carrier", ""), shipment.get("bol_awb"))

    if provider == "DHL":
        return get_dhl_result(provider_pages["DHL"], shipment["bol_awb"])

    if provider == "QATAR":
        return get_qatar_result(provider_pages["QATAR"], shipment["bol_awb"])

    if provider in PORTALS:
        return get_portal_result(provider_pages[provider], provider,
                                 shipment["bol_awb"])

    raise SkipShipment(
        describe_unsupported(shipment.get("bol_awb"), shipment.get("carrier")))


# ============================================================
# INTERNAL MANAGE PAGE - COE ETA AND BU ATA
# ============================================================

def describe_manage_fields(page, wanted):
    """
    List what the Manage page is actually offering when a field is missing.

    Guessing at label text from a screenshot is how the ETA xpath ended up
    requiring an exact 'ETA' when the page says 'ETA : *'. This puts the real
    names in the run log so the selector can be matched rather than guessed.
    """
    try:
        write_log(f"--- {wanted} not found. Fields visible on the Manage page: ---")

        shown = 0
        visible_count = 0
        for index, scope in enumerate(all_scopes(page)):
            label = "main document" if index == 0 else "frame {0}".format(index)
            try:
                found = scope.locator("input:not([type='hidden'])").all()[:60]
            except Exception:
                continue
            for element in found:
                # Every input is listed, visible or not, WITH its visibility.
                # The old version skipped invisible ones, so the log said "no
                # visible inputs anywhere" both when the panel was genuinely
                # empty and when it had rendered inside a collapsed container —
                # two different faults with one message, and the field lookup
                # was gated on the same :visible test, so they always failed
                # together and neither could be told apart from the log.
                try:
                    visible = element.is_visible()
                    attributes = {
                        key: element.get_attribute(key)
                        for key in ("id", "name", "type", "placeholder", "aria-label")
                    }
                except Exception:
                    continue
                described = {k: v for k, v in attributes.items() if v}
                if described:
                    shown += 1
                    if visible:
                        visible_count += 1
                    write_log("    {0} input : {1} visible={2}".format(
                        label, described, visible))

        if shown == 0:
            try:
                ready = page.evaluate("() => document.readyState")
                body = len((page.locator("body").inner_text(timeout=1500) or "").strip())
                total = page.locator("input").count()
            except Exception:
                ready, body, total = "unknown", 0, -1
            write_log(
                "    (no inputs at all — readyState={0}, body text={1} chars, "
                "frames={2}, inputs incl. hidden={3}) — the panel did not "
                "render".format(ready, body, len(all_scopes(page)), total)
            )
        elif visible_count == 0:
            write_log(
                "    ({0} inputs are present but NONE are visible — the panel "
                "rendered inside a collapsed or hidden container, which is a "
                "different fault from an empty page)".format(shown))

        for tab in page.get_by_role("tab").all()[:20]:
            try:
                text = " ".join((tab.inner_text(timeout=300) or "").split())
                state = tab.get_attribute("aria-selected")
            except Exception:
                continue
            if text:
                write_log(f"    tab   : {text!r} (aria-selected={state})")

        write_log(f"--- end of Manage page field list ---")
    except Exception as error:
        note_suppressed("listing Manage page fields", error)


def all_scopes(page):
    """The main document plus every frame, so a panel in an iframe is found."""
    scopes = [page]
    try:
        for frame in page.frames:
            if frame != page.main_frame:
                scopes.append(frame)
    except Exception:
        pass
    return scopes


def panel_has_field(page, field_name):
    """
    Is the target date field actually on screen, in ANY frame?

    aria-selected is not consulted: the run log showed BU, Logs AND KPIs all
    reporting aria-selected=true at the same moment, so it proves nothing. A
    visible, editable input is the only honest evidence.
    """
    other = "ATA" if field_name == "ETA" else "ETA"
    for scope in all_scopes(page):
        try:
            css = scope.locator(
                "input[id*='{0}' i]:not([id*='{1}' i]):visible, "
                "input[name*='{0}' i]:not([name*='{1}' i]):visible".format(
                    field_name, other)
            )
            if css.count() > 0:
                return True
        except Exception:
            pass
        try:
            label = scope.locator(
                "xpath=//*[self::label or self::span or self::div or self::td]"
                "[starts-with(normalize-space(),'{0}')]"
                "/following::input[not(@type='hidden')][1]".format(field_name)
            ).first
            if label.count() > 0 and label.is_visible(timeout=400):
                return True
        except Exception:
            continue
    return False


def page_is_settled(page):
    """True once the document has finished loading a postback."""
    try:
        return page.evaluate("() => document.readyState") == "complete"
    except Exception:
        return False


def select_shipment_info_tab(page, view_name, field_name=None):
    """
    Pick the COE or BU tab on the Modify Shipment page.

    The Manage/Modify page carries both sets of fields behind tabs labelled
    "COE Shipment Info" and "BU Shipment Info". ETA lives on the COE tab and
    ATA on the BU tab, so the tab must be selected before the field is filled
    — nothing in the automation was doing that.

    This is also why the COE list view is not actually required to write an
    ETA: the tab is on the shipment's own Manage page, reachable from either
    list.

    Returns True when a tab was selected or was already active. Returns False
    when no tab was found, in which case the caller carries on and
    fill_date_field decides — a page without tabs simply shows the fields.
    """
    # The tab is truncated to "BU Shipm..." in the UI, so the full label may be
    # "BU Shipment Info" or "BU Shipment Information". Accept both.
    label = re.compile(
        r"^\s*{0}\s*(?:-\s*)?Shipment\s*Info(?:rmation)?\s*$".format(view_name),
        re.I,
    )

    # If the field we need is already on screen, the right panel is showing.
    # Clicking anyway would fire a needless postback.
    if field_name and panel_has_field(page, field_name):
        write_log(
            f"The {view_name} panel is already showing its {field_name} field; "
            "no tab click needed."
        )
        return True

    tab = first_visible(
        [
            page.get_by_role("tab", name=label),
            page.get_by_role("link", name=label),
            page.get_by_role("button", name=label),
            page.get_by_text(label),
        ],
        PROBE_TIMEOUT_MS * 2,
    )

    if tab is None:
        write_log(
            f"No '{view_name} Shipment Info' tab was visible on the Manage "
            "page; the fields may already be on screen."
        )
        return False

    # aria-selected is NOT consulted: this page reports it true on several tabs
    # at once, so trusting it could skip the click and leave the wrong panel up.
    # Selecting a tab is an ASP.NET postback like every other control here.
    click_postback(tab, f"'{view_name} Shipment Info' tab")

    # THE BUG THIS FIXES. Selecting the tab fires an ASP.NET postback. The old
    # code went straight to polling for the field, so it spent its whole budget
    # looking at a document that was being torn down and rebuilt — which is why
    # the diagnostic reported "no visible inputs" while the tab showed as
    # selected. Wait for the navigation to land first.
    try:
        page.wait_for_load_state("domcontentloaded", timeout=HUB_FORM_READY_MAX_MS)
    except Exception as error:
        note_suppressed("waiting for the tab postback to load", error)
    wait_for_any(
        page, [("document ready", lambda: page_is_settled(page))],
        HUB_FORM_READY_MAX_MS, reason="the tab postback to finish",
    )

    # The tab swaps the panel in; wait for a date input rather than a sleep.
    # Wait for the FIELD, not for text. Panel headings survive in the DOM while
    # a postback is in flight, so matching them let the code proceed against a
    # page whose inputs had already been torn down — exactly what the
    # "(no visible inputs)" diagnostic showed.
    checks = []
    if field_name:
        checks.append(("the {0} field".format(field_name),
                       lambda: panel_has_field(page, field_name)))
    checks.append(("editable fields", lambda: manage_form_ready(page)))

    # The budget may be shortened by evidence, never lengthened:
    # HUB_FORM_READY_MAX_MS stays the ceiling and the floor is 3s, so the
    # deterministic worst case is unchanged.
    panel_context = ml_context(provider="HUB", page="tab_panel",
                               field=field_name, view=view_name)
    panel_budget = ml_wait_budget(panel_context, HUB_FORM_READY_MAX_MS,
                                  floor_ms=3000)
    panel_started = time.time()
    settled = wait_for_any(
        page, checks, int(panel_budget),
        reason=f"the {view_name} Shipment Info panel",
    )
    ml_record(panel_context, "tab_postback", settled is not None,
              (time.time() - panel_started) * 1000.0,
              "OK" if settled else "PAGE_NOT_READY")
    if settled is None:
        write_log(
            f"'{view_name} Shipment Info' tab was clicked but its panel did not "
            f"render an editable field within {HUB_FORM_READY_MAX_MS}ms. The "
            "field lookup will report what is actually on the page."
        )
        return False
    write_log(f"'{view_name} Shipment Info' tab selected ({settled} present).")
    return True


def fill_date_field(page, field_name, date_value):
    # Each candidate is NAMED. The names are what telemetry records and what
    # the model reorders; the selectors, their order and every ETA/ATA guard
    # inside them are exactly as they were.
    if field_name == "ETA":
        named = [
            ("label_exact",
             page.get_by_label(re.compile(r"^\s*ETA\s*(?:Date)?\s*:?\s*\*?\s*$", re.I))),
            ("css_id_visible",
             page.locator("input[id*='ETA' i]:not([id*='ATA' i]):visible")),
            ("css_name_visible",
             page.locator("input[name*='ETA' i]:not([name*='ATA' i]):visible")),
            ("label_loose",
             page.get_by_label("ETA", exact=False)),
            ("css_id_any",
             page.locator("input:not([type='hidden'])[id*='ETA' i]:not([id*='ATA' i])")),
            ("css_name_any",
             page.locator("input:not([type='hidden'])[name*='ETA' i]:not([name*='ATA' i])")),
            ("xpath_exact_label",
             page.locator(
                 "xpath=//*[self::label or self::span or self::div or self::td]"
                 "[normalize-space()='ETA']/following::input[not(@type='hidden')][1]"
             )),
            # The live page labels the field "ETA : *", not "ETA", so the
            # exact-match xpath above never fired. starts-with keeps ETD and
            # ATA out while tolerating the colon and the required-marker.
            ("xpath_starts_with",
             page.locator(
                 "xpath=//*[self::label or self::span or self::div or self::td]"
                 "[starts-with(normalize-space(),'ETA')]"
                 "/following::input[not(@type='hidden')][1]"
             )),
        ]
    else:
        # The BU Shipment Info tab labels this field "ATA Date :", sitting in
        # the Clearing Agent block among six other date fields — Customs Pre
        # Entry, Duty Assessment Received, Customs Declaration, Duty Paid,
        # Customs Release, Delivery and Receive Date. Every candidate below
        # anchors on the label STARTING with ATA so none of those can be hit.
        #
        # :visible matters as much as the selector: the inactive COE panel's
        # inputs come first in the DOM, so an unfiltered match lands on a
        # hidden field.
        named = [
            ("label_exact",
             page.get_by_label(re.compile(r"^\s*ATA\s*(?:Date)?\s*:?\s*\*?\s*$", re.I))),
            ("xpath_ata_date",
             page.locator(
                 "xpath=//*[self::label or self::span or self::div or self::td]"
                 "[starts-with(normalize-space(),'ATA Date')]"
                 "/following::input[not(@type='hidden')][1]"
             )),
            ("xpath_starts_with",
             page.locator(
                 "xpath=//*[self::label or self::span or self::div or self::td]"
                 "[starts-with(normalize-space(),'ATA')]"
                 "/following::input[not(@type='hidden')][1]"
             )),
            ("css_id_visible", page.locator("input[id*='ATA' i]:visible")),
            ("css_name_visible", page.locator("input[name*='ATA' i]:visible")),
            ("label_ata_date", page.get_by_label("ATA Date", exact=False)),
            ("label_loose", page.get_by_label("ATA", exact=False)),
        ]

    context = ml_context(
        provider="HUB", page="manage", field=field_name,
        view=("COE" if field_name == "ETA" else "BU"),
        page_ready=page_is_settled(page), frames=len(all_scopes(page)))

    # The model may only REORDER these; it cannot add, drop or rewrite one.
    named, predicted = ml_order(named, context)
    candidates = [locator for _name, locator in named]

    # The panel is confirmed ready before this runs, so a generous per-candidate
    # timeout only adds latency. Seven candidates at 3500ms cost 25s of probing
    # on the failing run.
    started = time.time()
    field = first_visible(candidates, 1500)
    elapsed_ms = (time.time() - started) * 1000.0

    if field is not None:
        # Which named candidate actually won. Recording the losers too is what
        # gives the model something to learn from: a strategy that is never
        # tried is not the same as one that is tried and fails.
        winner = None
        for index, (name, locator) in enumerate(named):
            if locator is field or getattr(locator, "_impl_obj", locator) is field:
                winner = name
                break
        if winner is None:
            winner = predicted or named[0][0]
        for rank, (name, _locator) in enumerate(named):
            if name == winner:
                ml_record(context, name, True, elapsed_ms, "OK", rank=rank)
                break
            ml_record(context, name, False, None, "FIELD_NOT_VISIBLE", rank=rank)
    else:
        for rank, (name, _locator) in enumerate(named):
            ml_record(context, name, False, elapsed_ms, "FIELD_NOT_VISIBLE",
                      rank=rank)

    if field is None:
        # Before declaring it missing, look for it without the :visible gate.
        # A panel that renders inside a collapsed container has real inputs
        # that Playwright reports as not visible; the run that failed on
        # 9451291275 gave up here on a field it had never actually looked for.
        field = find_field_ignoring_visibility(page, field_name)
        if field is not None:
            write_log(
                "The {0} field is present but not reported visible; writing to "
                "it directly.".format(field_name)
            )
            ml_record(context, "ignore_visibility", True, None, "SCROLL_REQUIRED")
        else:
            ml_record(context, "ignore_visibility", False, None, "FIELD_NOT_FOUND")

    if field is None:
        describe_manage_fields(page, field_name)
        raise Exception(f"{field_name} field was not found on the Manage page.")

    input_type = (field.get_attribute("type") or "text").lower()
    value = (
        datetime.strptime(date_value, "%d/%m/%Y").strftime("%Y-%m-%d")
        if input_type == "date"
        else date_value
    )

    landed = write_date_value(field, value, field_name)
    write_log(f"Internal {field_name} overwritten with: {landed}")


def find_field_ignoring_visibility(page, field_name):
    """
    Last resort: the input exists in the DOM but Playwright will not call it
    visible.

    Every ATA candidate in fill_date_field is :visible-gated, and so was the
    diagnostic — so when the BU panel rendered inside a collapsed container the
    run reported "no visible inputs anywhere" and gave up on a field that was
    sitting right there. This matches on id/name only, scrolls the element into
    view, and hands it back so the caller can decide how to write it.

    The `other` guard is kept: ETA must never match ATA, or the wrong date goes
    into the wrong field, which is worse than not writing at all.
    """
    other = "ATA" if field_name == "ETA" else "ETA"
    for scope in all_scopes(page):
        try:
            found = scope.locator(
                "input[id*='{0}' i]:not([id*='{1}' i]), "
                "input[name*='{0}' i]:not([name*='{1}' i])".format(field_name, other)
            ).all()
        except Exception:
            continue
        for element in found:
            try:
                if (element.get_attribute("type") or "").lower() == "hidden":
                    continue
                element.scroll_into_view_if_needed(timeout=1500)
                return element
            except Exception:
                continue
    return None


def write_date_value(field, value, field_name):
    """
    Put a date into the field and confirm it stuck.

    Falls back to a scripted write with real input/change events when the
    normal path cannot interact with the element — an ASP.NET date box inside a
    panel the browser considers off-screen accepts this and rejects a click.
    """
    context = ml_context(provider="HUB", page="manage", field=field_name)
    started = time.time()
    method = "click_fill"
    try:
        field.click(timeout=2500)
        field.fill("")
        field.fill(value)
        field.dispatch_event("input")
        field.dispatch_event("change")
        field.press("Tab")
        ml_record(context, "click_fill", True,
                  (time.time() - started) * 1000.0, "OK")
    except Exception as error:
        note_suppressed("typing the {0} normally".format(field_name), error)
        ml_record(context, "click_fill", False, None, ml_category(error),
                  detail=str(error)[:200])
        method = "scripted_events"
        field.evaluate(
            "(el, v) => { const setter = Object.getOwnPropertyDescriptor("
            "window.HTMLInputElement.prototype, 'value').set;"
            " setter.call(el, v);"
            " el.dispatchEvent(new Event('input', {bubbles:true}));"
            " el.dispatchEvent(new Event('change', {bubbles:true}));"
            " el.dispatchEvent(new Event('blur', {bubbles:true})); }",
            value,
        )

    try:
        landed = (field.input_value() or "").strip()
    except Exception:
        landed = ""
    if method == "scripted_events":
        ml_record(context, "scripted_events", bool(landed),
                  (time.time() - started) * 1000.0,
                  "OK" if landed else "INPUT_REJECTED")
    if not landed:
        raise Exception(
            "{0} field was found but would not accept {1}.".format(field_name, value))
    return landed


def save_manage_page(page):
    save_button = first_visible(
        [
            page.get_by_role(
                "button",
                name=re.compile(r"^\s*(save|update|submit|confirm)\s*$", re.I),
            ),
            page.locator("button:has-text('Save')"),
            page.locator("button:has-text('Update')"),
            page.locator("input[type='submit'][value*='Save' i]"),
            page.locator("input[type='submit'][value*='Update' i]"),
        ],
        3000,
    )
    if save_button is None:
        raise Exception("Save/Update button was not found on the Manage page.")

    # A save is done when the page moves on: the Save control goes away, or
    # the results table comes back. Either is a positive signal; the fixed
    # 1500ms was neither, and was equally capable of being too short.
    click_postback(save_button, "Save on the Manage page")
    invalidate_hub_state()
    settled = wait_for_any(
        page,
        [
            ("save control gone", lambda: not save_button.is_visible(timeout=300)),
            ("results table returned",
             lambda: find_shipments_table(page).is_visible(timeout=300)),
        ],
        HUB_SAVE_MAX_MS,
        reason="the Manage page to finish saving",
    )
    if settled is None:
        # Unproven, so keep the original grace period rather than racing on.
        page.wait_for_timeout(1500)
        write_log(
            "Save completion was not positively confirmed; used the original "
            "1500ms grace period before continuing."
        )
    else:
        write_log(f"Manage page saved successfully ({settled}).")


def verify_saved_date(page, shipment, view_name, field_name, expected):
    """
    Reopen the shipment and confirm `expected` is what the Hub now holds.

    Returns (ok, detail). A mismatch RAISES in the caller rather than being
    logged and forgotten, because a write that silently did not stick is the
    one failure this automation must never report as success.

    Deterministic throughout. The model has no say in whether a value is
    correct, only — elsewhere — in what order to look for the field.
    """
    bol_awb = shipment["bol_awb"]
    context = ml_context(provider="HUB", page="manage", field=field_name,
                         view=view_name)
    started = time.time()
    try:
        click_manage_in_view(page, view_name, bol_awb, shipment["table_page"])
        select_shipment_info_tab(page, view_name, field_name)

        field = first_visible(
            [page.locator("input[id*='{0}' i]:not([id*='{1}' i]):visible".format(
                field_name, "ATA" if field_name == "ETA" else "ETA")),
             page.locator("input[name*='{0}' i]:not([name*='{1}' i]):visible".format(
                 field_name, "ATA" if field_name == "ETA" else "ETA"))],
            2000)
        if field is None:
            field = find_field_ignoring_visibility(page, field_name)
        if field is None:
            ml_record(context, "verify_reload", False, None, "VERIFICATION_FAILURE")
            return False, "the {0} field could not be found on reload".format(field_name)

        actual = (field.input_value() or "").strip()
        # The page may hand it back in either format; compare on the date, not
        # on the string.
        normalised = normalize_date(actual) or actual
        ok = normalised == expected or actual == expected
        ml_record(context, "verify_reload", ok,
                  (time.time() - started) * 1000.0,
                  "OK" if ok else "VERIFICATION_FAILURE")
        if ok:
            write_log("Verified: {0} {1} is {2} in the Hub after reload."
                      .format(view_name, field_name, actual))
            return True, actual
        return False, "the Hub holds {0!r}, not {1!r}".format(actual, expected)
    except Exception as error:
        ml_record(context, "verify_reload", False, None, ml_category(error))
        return False, "verification could not be completed: {0}".format(error)


def update_one_view(page, shipment, view_name, field_name, date_value,
                    return_to_table=True):
    """
    `return_to_table` defaults to True, which is the original behaviour: end by
    navigating back to the filtered results table.

    It is passed False only when the caller can see that the very next action
    will navigate to a different view anyway, making this trip dead work. The
    shipment update itself is identical either way.
    """
    if not date_value:
        return "No provider date available; no update"

    bol_awb = shipment["bol_awb"]
    page_number = click_manage_in_view(
        page,
        view_name,
        bol_awb,
        shipment["table_page"],
    )

    tower.step(
        f"Writing {field_name} {date_value} to the {view_name} view",
        system="hub",
    )
    write_log(
        f"Updating {view_name} Shipments View {field_name} for {bol_awb} "
        f"with {date_value}."
    )
    # ETA is on the COE tab, ATA on the BU tab. Select it before filling.
    if not select_shipment_info_tab(page, view_name, field_name):
        # The panel did not render an editable field. An aborted or overlapping
        # postback leaves the Manage page in exactly that state, and it does not
        # recover on its own — but a clean reload of the page does. Try once.
        write_log(
            f"The {view_name} panel did not render. Reopening Manage for "
            f"{bol_awb} and trying once more."
        )
        try:
            click_manage_in_view(page, view_name, bol_awb, page_number)
            if select_shipment_info_tab(page, view_name, field_name):
                write_log(f"The {view_name} panel rendered after reopening Manage.")
            else:
                write_log(
                    f"The {view_name} panel still has no {field_name} field after "
                    "reopening. Reporting what is on the page."
                )
        except Exception as error:
            note_suppressed("reopening Manage to recover the panel", error)

    fill_date_field(page, field_name, date_value)

    if DRY_RUN:
        take_screenshot(page, bol_awb, f"dry_run_{view_name.lower()}_{field_name.lower()}")
        if return_to_table:
            ensure_filtered_page(page, view_name, page_number)
        return f"DRY RUN - {field_name} {date_value} filled, not saved"

    save_manage_page(page)

    if VERIFY_AFTER_SAVE:
        verified, detail = verify_saved_date(
            page, shipment, view_name, field_name, date_value)
        if not verified:
            # Refuse to report a success that cannot be proven.
            raise Exception(
                "{0} {1} was saved but not verified for {2}: {3}".format(
                    view_name, field_name, bol_awb, detail))

    tower.view_updated(view_name, field_name, date_value)
    action = f"{view_name} {field_name} updated with {date_value} and saved"
    write_log(f"{action} for {bol_awb}.")
    if return_to_table:
        ensure_filtered_page(page, view_name, page_number)
    else:
        write_log(
            f"Skipping the return trip to the {view_name} table: the next step "
            "navigates to a different view and would discard it."
        )
    return action


def update_internal_shipment(internal_page, shipment, dhl_result):
    """
    Update both internal views independently:
      COE -> ETA from provider estimated event
      BU  -> ATA from provider completed arrival event
    """
    actions = {"coe": "", "bu": ""}

    # If a BU update follows, it re-navigates to the BU view immediately, so
    # returning to the COE table first is pure waste. Only skip when we can
    # see that the BU update will definitely run.
    bu_update_follows = bool(dhl_result.get("ata"))

    if dhl_result.get("eta"):
        actions["coe"] = update_one_view(
            internal_page,
            shipment,
            COE_VIEW,
            "ETA",
            dhl_result["eta"],
            return_to_table=not bu_update_follows,
        )
    else:
        actions["coe"] = "No provider ETA; COE ETA not updated"
        write_log(actions["coe"])

    if dhl_result.get("ata"):
        try:
            actions["bu"] = update_one_view(
                internal_page,
                shipment,
                BU_VIEW,
                "ATA",
                dhl_result["ata"],
            )
        except Exception as error:
            # The COE ETA above may already be SAVED IN THE HUB. Letting this
            # exception escape discarded `actions`, so the results file recorded
            # "No update" for a shipment that had genuinely been updated.
            # Carry the completed work out with the failure.
            actions["bu"] = f"BU ATA update failed: {error}"
            write_log(
                "BU ATA update failed for {0}, but the COE result above still "
                "stands: {1}".format(shipment.get("bol_awb"), actions["coe"] or "none")
            )
            error.actions = actions
            raise
    else:
        actions["bu"] = "No provider ATA; BU ATA not updated"
        write_log(actions["bu"])

    if not dhl_result.get("eta") and not dhl_result.get("ata"):
        raise SkipShipment("The carrier did not provide ETA or ATA.")

    return actions


# ============================================================
# MAIN - DHL ONLY, PAGES 1 TO 10
# ============================================================

def main():
    if DASHBOARD_ENABLED and TOWER_AVAILABLE:
        if os.environ.get("CT_STATE_FILE"):
            pass          # the supervisor already owns the port
        else:
            tower_server.start(
                port=DASHBOARD_PORT,
                open_browser=DASHBOARD_OPEN_BROWSER,
                host=DASHBOARD_HOST,
                access_key=DASHBOARD_ACCESS_KEY,
            )
    # Under the supervisor the dashboard is served by the parent process, so
    # this run publishes its state to a file instead of hosting a server.
    _state_file = os.environ.get("CT_STATE_FILE")
    if _state_file:
        def _publish_state():
            while True:
                try:
                    Path(_state_file).write_text(
                        json.dumps(tower.snapshot(trim=True)), encoding="utf-8")
                except Exception:
                    pass
                time.sleep(0.6)

        threading.Thread(target=_publish_state, daemon=True).start()
        write_log("Publishing live state to the Control Tower supervisor.")

    tower_control.configure(
        DASHBOARD_ALLOW_CONTROL or bool(os.environ.get("CT_CONTROL_FILE")))

    # Tell the dashboard which carriers this build can actually track, so the
    # Systems panel matches the automation instead of a hand-kept list.
    for _prefix, _entry in sorted(AIRLINES.items()):
        if not _entry["provider"]:
            continue
        tower.register_system(
            _entry["provider"],
            PORTALS.get(_entry["provider"], {}).get("label")
            or {"DHL": "DHL Tracking", "QATAR": "Qatar Airways Cargo"}.get(
                _entry["provider"], _entry["name"]),
            "Tracking {0}".format(", ".join(sorted(
                e["name"] for e in AIRLINES.values()
                if e["provider"] == _entry["provider"]))),
        )
    tower_control.clear()
    tower.run_started(
        dry_run=DRY_RUN,
        target_status=TARGET_STATUS,
        max_records=MAX_RECORDS_PER_RUN,
        max_pages=MAX_TABLE_PAGES,
        results_file=str(RESULTS_FILE),
        log_file=str(LOG_FILE),
    )
    if OUTPUT_FOLDER != BASE_FOLDER:
        write_log(
            "NOTE: {0} could not be used for output, so this run is writing to "
            "{1} ({2}).".format(BASE_FOLDER, OUTPUT_FOLDER, OUTPUT_FOLDER_DESCRIPTION)
        )
        for folder, description, reason in OUTPUT_FOLDER_REJECTED:
            write_log("  rejected {0}: {1}".format(folder, reason))
    write_log(f"Run log: {LOG_FILE}")
    write_log("Automation started in DHL and Qatar Airways COE ETA / BU ATA mode.")
    username, password = load_credentials()
    write_log("Local smart extraction is active; no LLM or external AI service is used.")
    # The learning layer announces its own state. It loads the model ONCE, here
    # — training is never triggered by a run, and never will be: a model that
    # retrained itself on the way past would be a different model every time
    # and impossible to hold responsible for anything.
    global ML_ACTIVE
    if ML_AVAILABLE:
        ML_ACTIVE = ml_predictor.initialize(log=write_log)
        write_log("[ML] Telemetry: {0}".format(ml_config.TELEMETRY_PATH))
    else:
        ML_ACTIVE = False
        write_log("[ML] Package not present")
        write_log("[ML] Status: FALLBACK")
        write_log("[ML] Using deterministic automation")
    if VERIFY_AFTER_SAVE:
        write_log("VERIFY_AFTER_SAVE is on: every write is re-read from the Hub.")

    successful = 0
    failed = 0
    skipped = 0
    partial = 0
    processed_bols = set()
    stop_requested = False

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="msedge",
            headless=False,
            slow_mo=SLOW_MO_MS,
        )

        context = browser.new_context(
            http_credentials={"username": username, "password": password}
        )

        internal_page = context.new_page()
        dhl_page = context.new_page()
        qatar_page = context.new_page()
        provider_pages = {"DHL": dhl_page, "QATAR": qatar_page}
        # One tab per simple portal, opened once and reused for the whole run.
        for _portal in PORTALS:
            provider_pages[_portal] = context.new_page()

        fatal_error = None

        try:
            tower.step("Signing in to the Logistics Hub", system="hub")
            login_internal(internal_page, username, password)
            tower.system_ok("hub", "Signed in")

            for table_page in range(1, MAX_TABLE_PAGES + 1):
                if successful + failed + skipped >= MAX_RECORDS_PER_RUN:
                    write_log(f"Maximum record limit reached: {MAX_RECORDS_PER_RUN}")
                    break

                try:
                    ensure_filtered_page(internal_page, SOURCE_VIEW, table_page)
                except SkipShipment as error:
                    write_log(f"Pagination ended at page {table_page}: {error}")
                    tower.pagination_ended(table_page, str(error))
                    break

                if stop_requested:
                    break

                shipments = collect_supported_shipments(internal_page, table_page)

                # A re-run requested from the dashboard jumps the queue for
                # this page, so the operator sees the answer without waiting
                # for the whole list.
                for _requested in iter(tower_control.take_reprocess, None):
                    match = next(
                        (s for s in shipments if s["bol_awb"] == _requested), None
                    )
                    if match is None:
                        write_log(
                            f"Re-run requested for {_requested}, but it is not on "
                            f"hub page {table_page}. It will be picked up when its "
                            "page is reached."
                        )
                        continue
                    write_log(f"Re-run requested from the dashboard: {_requested}")
                    processed_bols.discard(_requested)
                    shipments = [match] + [s for s in shipments
                                           if s["bol_awb"] != _requested]

                for shipment in shipments:
                    bol_awb = shipment["bol_awb"]

                    if bol_awb in processed_bols:
                        continue

                    if honour_control_requests() == "stop":
                        stop_requested = True
                        break

                    processed_bols.add(bol_awb)
                    dhl_result = {}

                    try:
                        tower.shipment_started(shipment)
                        shipment_log(
                            bol_awb,
                            "Opening tracking page (hub page {0}, existing ETA {1})".format(
                                table_page, shipment["current_eta"] or "none"),
                            carrier=shipment["carrier"],
                        )

                        tower.step(
                            f"Tracking {bol_awb} on the carrier site",
                            system=shipment.get("provider"),
                        )
                        dhl_result = get_provider_result(provider_pages, shipment)
                        tower.system_ok(
                            shipment.get("provider"),
                            f"Result returned for {bol_awb}",
                        )
                        tower.provider_result(dhl_result)

                        write_log(
                            f"Provider result for {bol_awb}: "
                            f"{dhl_result.get('provider')} | "
                            f"Status={dhl_result.get('tracking_status')} | "
                            f"ETA={dhl_result.get('eta')} | "
                            f"ATA={dhl_result.get('ata')}"
                        )

                        action = update_internal_shipment(
                            internal_page,
                            shipment,
                            dhl_result,
                        )

                        save_result(shipment, dhl_result, action, "SUCCESS")
                        successful += 1
                        tower.shipment_finished(bol_awb, "SUCCESS", "", action)
                        tower.counters(successful, failed, skipped, partial)

                    except AfklNavigationError as error:
                        # NOT "no shipment found". The carrier was never
                        # reached, so nothing has been learned about the AWB.
                        failed += 1
                        write_log("AFKL NAVIGATION ERROR for {0}: {1}".format(
                            bol_awb, error))
                        log_operation_failure(
                            shipment.get("carrier"), bol_awb, "carrier navigation",
                            error, 1, 1, AFKL_NAVIGATION_ERROR, final=True,
                        )
                        save_result(shipment, dhl_result, "No update",
                                    "FAILED", str(error))
                        tower.shipment_finished(
                            bol_awb, "FAILED", str(error),
                            outcome=AFKL_NAVIGATION_ERROR)
                        tower.counters(successful, failed, skipped, partial)
                        try:
                            ensure_filtered_page(internal_page, SOURCE_VIEW, table_page)
                        except Exception as restore_error:
                            write_log(f"Internal page restore warning: {restore_error}")

                    except SkipShipment as error:
                        skipped += 1
                        write_log(f"SKIPPED {bol_awb}: {error}")
                        _outcome = classify_failure(error)
                        log_operation_failure(
                            shipment.get("carrier"), bol_awb, "carrier tracking",
                            error, 1, 1, _outcome, final=True,
                        )
                        save_result(shipment, dhl_result, "Skipped", "SKIPPED", str(error))
                        tower.shipment_finished(
                            bol_awb, "SKIPPED", str(error), outcome=_outcome
                        )
                        tower.counters(successful, failed, skipped, partial)

                        try:
                            ensure_filtered_page(internal_page, SOURCE_VIEW, table_page)
                        except Exception as restore_error:
                            write_log(f"Internal page restore warning: {restore_error}")

                    except Exception as error:
                        failed += 1
                        take_screenshot(internal_page, bol_awb, "error")
                        write_log(f"ERROR for {bol_awb}: {error}")
                        _outcome = classify_failure(error)
                        log_operation_failure(
                            shipment.get("carrier"), bol_awb, "shipment update",
                            error, 1, 1, _outcome, final=True,
                        )
                        # If a date already reached the Hub before this failed,
                        # the shipment is PARTIAL, not FAILED. Marking the whole
                        # thing a failure hid work that was genuinely done and
                        # made the run look worse than it was.
                        _actions = getattr(error, "actions", None)
                        _wrote_something = bool(
                            _actions and any(
                                isinstance(v, str) and "updated with" in v
                                for v in _actions.values()
                            )
                        )
                        if _wrote_something:
                            partial += 1
                            save_result(shipment, dhl_result, _actions,
                                        "PARTIAL", str(error))
                            tower.shipment_finished(
                                bol_awb, "PARTIAL", str(error), outcome=_outcome
                            )
                            shipment_log(
                                bol_awb,
                                "Partly updated - one field written, one failed",
                                carrier=shipment.get("carrier"), level="WARNING",
                            )
                        else:
                            save_result(shipment, dhl_result,
                                        _actions or "No update", "FAILED", str(error))
                            tower.shipment_finished(
                                bol_awb, "FAILED", str(error), outcome=_outcome
                            )
                        tower.counters(successful, failed, skipped, partial)

                        try:
                            ensure_filtered_page(internal_page, SOURCE_VIEW, table_page)
                        except Exception as restore_error:
                            write_log(f"Internal page restore warning: {restore_error}")

                    wait_between_shipments()

            write_log(
                f"DHL/Qatar automation finished. Successful: {successful}, "
                f"Failed: {failed}, Skipped: {skipped}, DRY_RUN={DRY_RUN}"
            )
            total_hub_requests = _hub_stats["navigations"] + _hub_stats["reused"]
            if total_hub_requests:
                write_log(
                    "Hub navigation: {0} rebuilds, {1} reused without "
                    "re-navigating ({2:.0f}% avoided).".format(
                        _hub_stats["navigations"],
                        _hub_stats["reused"],
                        100.0 * _hub_stats["reused"] / total_hub_requests,
                    )
                )
            report_unsupported()
            report_suppressed()
            if stop_requested:
                write_log(
                    "Run ended early at the dashboard's request. Everything "
                    "already written to the Hub stands."
                )
            tower_control.clear()
            write_log(f"Results saved to: {RESULTS_FILE}")
            tower.counters(successful, failed, skipped, partial)
            tower.run_finished()

        except Exception as error:
            fatal_error = error
            tower.run_fatal(error)
            full_traceback = traceback.format_exc()
            write_log(f"FATAL ERROR: {type(error).__name__}: {error}")
            write_log("Full traceback follows:")
            for traceback_line in full_traceback.rstrip().splitlines():
                write_log(traceback_line)

            try:
                take_screenshot(internal_page, "fatal", "internal_page")
            except Exception:
                pass

            try:
                take_screenshot(dhl_page, "fatal", "dhl_page")
            except Exception:
                pass

            try:
                take_screenshot(qatar_page, "fatal", "qatar_page")
            except Exception:
                pass

            if PAUSE_ON_FATAL_ERROR:
                print("\nThe automation stopped because of a fatal error.", flush=True)
                if DASHBOARD_ENABLED and TOWER_AVAILABLE:
                    print(
                        f"Control Tower is still live at http://127.0.0.1:{DASHBOARD_PORT}/",
                        flush=True,
                    )
                print(f"Log file: {LOG_FILE}", flush=True)
                print("Microsoft Edge will remain open for inspection.", flush=True)
                input("Press ENTER only after reviewing/copying the error...")

        finally:
            # Strategies 3 and 4 may have left a browser of their own open so
            # the caller could read the page they landed on. They close here,
            # with everything else.
            close_afkl_side_browsers()
            browser.close()
            write_log("Microsoft Edge closed automatically.")

        if fatal_error is not None:
            raise fatal_error


if __name__ == "__main__":
    try:
        main()
        if DASHBOARD_ENABLED and TOWER_AVAILABLE:
            print(
                f"\nRun complete. Control Tower stays live at "
                f"http://127.0.0.1:{DASHBOARD_PORT}/",
                flush=True,
            )
            input("Press ENTER to close the dashboard and this window...")
    except Exception as error:
        print(f"\nAUTOMATION FAILED: {type(error).__name__}: {error}", flush=True)
        print(f"Full details were saved in: {LOG_FILE}", flush=True)
        input("Press ENTER to close this CMD run...")
