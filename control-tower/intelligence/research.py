"""
ATLAS's web research — optional, free, self-hosted. No paid API.

ATLAS answers run facts from the run (dashboard/assistant.py, authoritative).
When a question needs outside information — a carrier advisory, a port
notice, what an error message means — this module can look it up, IF the
Control Tower has been given a search service of its own:

    SEARCH  a self-hosted SearXNG instance (open source, JSON API):
            ATLAS_SEARCH_URL=http://127.0.0.1:8888
    FETCH   the top results, only on an allow-list of official domains
            (carriers' public notices, port authorities, vendor
            documentation), honouring robots.txt, 8 s, 512 KB; never a carrier
            TRACKING page (those carry human verification)

Nothing is installed or required: with ATLAS_SEARCH_URL unset, ATLAS answers
from the run and says plainly that outside sources were not checked.

What leaves the building: the search queries (built here, deterministically,
from the carrier, the reference, the error text) — never credentials, never a
URL's query string. Results are labelled WEB wherever they are shown; they
are never written to the learning store and never decide a run fact.

    ATLAS_SEARCH_URL            the SearXNG base URL (unset = no research)
    ATLAS_SEARCH                0 = off even when a URL is set
    ATLAS_SEARCH_ALLOW_REMOTE   1 = allow a non-local SearXNG host (default:
                                localhost / private network only)
    ATLAS_SEARCH_TIMEOUT_S      default 10
    ATLAS_SEARCH_CACHE_S        default 1800 (30 min; "latest" refreshes)
    ATLAS_FETCH_ALLOW           extra comma-separated domains to fetch from
"""

import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from urllib.parse import urlparse

USER_AGENT = "ATA-Control-Tower-ATLAS/1.0 (+self-hosted research; respects robots.txt)"
MAX_FETCH_BYTES = 512 * 1024
FETCH_TIMEOUT_S = 8.0
MAX_QUERIES = 3
MAX_FETCHES = 2

# Who publishes what: shown beside each source so the operator can weigh it.
OFFICIAL_CARRIERS = (
    "msc.com", "cma-cgm.com", "maersk.com", "hapag-lloyd.com", "one-line.com",
    "evergreen-line.com", "coscoshipping.com", "oocl.com", "yangming.com", "hmm21.com", "zim.com",
    "pilship.com", "grimaldi.napoli.it", "grimaldi-lines.com", "afklcargo.com",
    "airfranceklm.com", "dhl.com", "qrcargo.com", "skycargo.com", "turkishcargo.com",
    "kline.com")
OFFICIAL_PORTS = ("ghanaports.gov.gh", "gpha.gov.gh", "apmterminals.com", "dpworld.com")
OFFICIAL_DOCS = ("playwright.dev", "learn.microsoft.com", "developer.mozilla.org",
                 "docs.python.org", "chromium.org", "support.microsoft.com")
PORT_HINTS = ("port", "harbour", "harbor", "terminal")
INDUSTRY_HINTS = ("lloydslist", "joc.com", "splash247", "seatrade", "porttechnology", "drewry",
                  "sea-intelligence", "theloadstar", "aircargonews")
# Never fetched, even on an allowed domain: tracking pages sit behind human
# verification, and sign-in pages are not information.
NEVER_FETCH = re.compile(r"(track|tracing|login|signin|sign-in|account|captcha)", re.I)
SECRET_KEYS = re.compile(r"pass|secret|token|credential|authorization|cookie|captcha|"
                         r"security.?code|otp|api.?key", re.I)

_lock = threading.Lock()
_cache = {}            # key -> (time, result)
_progress = {}         # id -> {"stages": [...], "done": bool, "at": float}
_robots = {}           # host -> (time, RobotFileParser | None)


# ── configuration ────────────────────────────────────────────────────────

def _off(name):
    return (os.environ.get(name) or "").strip().lower() in ("0", "false", "no", "off")


def _num(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


def local_host(url):
    """True for localhost and private-network addresses: self-hosted services."""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local") or \
            host.endswith(".lan") or host.endswith(".internal"):
        return True
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(host))
    except (OSError, ValueError):
        return False
    return addr.is_private or addr.is_loopback


def search_url():
    return (os.environ.get("ATLAS_SEARCH_URL") or "").strip().rstrip("/")


def enabled():
    url = search_url()
    if not url or _off("ATLAS_SEARCH"):
        return False
    return local_host(url) or os.environ.get("ATLAS_SEARCH_ALLOW_REMOTE") == "1"


def why_off():
    if _off("ATLAS_SEARCH"):
        return "web research is switched off on this Control Tower (ATLAS_SEARCH=0)"
    if search_url() and not enabled():
        return ("the configured search service is not on this machine or its network "
                "(ATLAS_SEARCH_ALLOW_REMOTE is not set)")
    return ("web research isn't set up on this Control Tower (optional: a self-hosted SearXNG "
            "at ATLAS_SEARCH_URL — see START_HERE.md)")


# ── progress: what is actually happening, for the chat ──────────────────

def progress_start(pid):
    if not pid:
        return
    with _lock:
        now = time.time()
        for key in [k for k, v in _progress.items() if now - v["at"] > 600]:
            _progress.pop(key, None)
        _progress[pid] = {"stages": [], "done": False, "at": now}


def stage(pid, text, detail=None):
    """One real step, as it starts."""
    if not pid:
        return
    with _lock:
        entry = _progress.setdefault(pid, {"stages": [], "done": False, "at": time.time()})
        entry["stages"].append({"text": text, "detail": detail, "at": time.time()})
        entry["stages"] = entry["stages"][-12:]
        entry["at"] = time.time()


def progress_done(pid):
    if not pid:
        return
    with _lock:
        if pid in _progress:
            _progress[pid]["done"] = True


def progress(pid):
    with _lock:
        entry = _progress.get(pid)
        if not entry:
            return {"stage": None, "detail": None, "done": False, "steps": 0}
        last = entry["stages"][-1] if entry["stages"] else {}
        return {"stage": last.get("text"), "detail": last.get("detail"), "done": entry["done"],
                "steps": len(entry["stages"])}


def clean_progress_id(raw):
    raw = str(raw or "")
    return raw if re.fullmatch(r"[A-Za-z0-9_-]{8,64}", raw) else ""


# ── what leaves the building ─────────────────────────────────────────────

def redact(value, depth=0):
    """A brief as it may be handed on: no secrets by name, URLs without queries."""
    if depth > 5:
        return None
    if isinstance(value, dict):
        return {k: redact(v, depth + 1) for k, v in value.items()
                if not SECRET_KEYS.search(str(k)) and v not in (None, "", [], {})}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in list(value)[:20]]
    if isinstance(value, str):
        return re.sub(r"(https?://[^\s?#\"']+)[?#][^\s\"']*", r"\1", value)[:600]
    return value


def _domain_in(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def publisher(url):
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if _domain_in(host, OFFICIAL_CARRIERS):
        return host, "Carrier (official)"
    if _domain_in(host, OFFICIAL_PORTS) or any(h in host for h in PORT_HINTS):
        return host, "Port / terminal"
    if _domain_in(host, OFFICIAL_DOCS):
        return host, "Official documentation"
    if host.endswith(".gov") or ".gov." in host:
        return host, "Government"
    if any(h in host for h in INDUSTRY_HINTS):
        return host, "Industry source"
    return host, "Other source"


def fetch_allowed(url):
    """Only official domains, never a tracking or sign-in page."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    extra = tuple(d.strip().lower() for d in (os.environ.get("ATLAS_FETCH_ALLOW") or "").split(",")
                  if d.strip())
    allowed = _domain_in(host, OFFICIAL_CARRIERS + OFFICIAL_PORTS + OFFICIAL_DOCS + extra) or \
        host.endswith(".gov") or ".gov." in host
    return allowed and parsed.scheme in ("http", "https") and \
        not NEVER_FETCH.search(parsed.path + "?" + parsed.query)


# ── search and fetch ─────────────────────────────────────────────────────

class ResearchError(Exception):
    pass


def _get(url, timeout, limit=MAX_FETCH_BYTES):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json, text/html"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(limit + 1)[:limit], response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        raise ResearchError("HTTP {0}".format(error.code))
    except (urllib.error.URLError, OSError) as error:
        raise ResearchError("not reachable ({0})".format(str(getattr(error, "reason", error))[:120]))


def search(query, timeout=None):
    """SearXNG JSON results: [{url, title, snippet}]. Raises ResearchError."""
    timeout = timeout or _num("ATLAS_SEARCH_TIMEOUT_S", 10)
    url = "{0}/search?{1}".format(search_url(), urllib.parse.urlencode(
        {"q": query, "format": "json", "safesearch": 1}))
    raw, _ctype = _get(url, timeout, 2 * 1024 * 1024)
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise ResearchError("the search service did not answer in JSON (is format=json enabled?)")
    out = []
    for item in (data.get("results") or [])[:8]:
        if item.get("url", "").startswith(("http://", "https://")):
            out.append({"url": item["url"], "title": (item.get("title") or "")[:160],
                        "snippet": " ".join((item.get("content") or "").split())[:400]})
    return out


def _robots_ok(url):
    parsed = urlparse(url)
    root = "{0}://{1}".format(parsed.scheme, parsed.netloc)
    with _lock:
        hit = _robots.get(root)
    if hit and time.time() - hit[0] < 3600:
        parser = hit[1]
    else:
        parser = urllib.robotparser.RobotFileParser()
        try:
            raw, _c = _get(root + "/robots.txt", FETCH_TIMEOUT_S, 256 * 1024)
            parser.parse(raw.decode("utf-8", "replace").splitlines())
        except ResearchError:
            parser = None                  # no robots.txt reachable: be conservative
        with _lock:
            _robots[root] = (time.time(), parser)
    return parser is not None and parser.can_fetch(USER_AGENT, url)


def page_text(url):
    """The readable text of an allowed official page, or raises ResearchError."""
    if not fetch_allowed(url):
        raise ResearchError("not an allowed official page")
    if not _robots_ok(url):
        raise ResearchError("robots.txt does not allow it (or could not be read)")
    raw, ctype = _get(url, FETCH_TIMEOUT_S)
    if "html" not in ctype and "text" not in ctype:
        raise ResearchError("not a text page ({0})".format(ctype or "unknown type"))
    html = raw.decode("utf-8", "replace")
    html = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    return " ".join(text.split())[:6000]


def queries_for(mode, question, brief):
    """Targeted searches, built deterministically — never one vague search."""
    ship = (brief or {}).get("shipment") or {}
    fail = (brief or {}).get("failure") or {}
    carrier = ship.get("carrier") or ""
    out = []
    if mode == "error" and fail.get("error_message"):
        message = re.sub(r"https?://\S+", "", fail["error_message"])
        out.append("{0} {1}".format(carrier, " ".join(message.split()[:14])).strip())
        if carrier:
            out.append("{0} customer advisory".format(carrier))
    elif mode in ("shipment", "mixed") and carrier:
        if ship.get("reference"):
            out.append("{0} {1}".format(carrier, ship["reference"]))
        out.append("{0} customer advisory schedule update".format(carrier))
        if re.search(r"\b(port|terminal|congestion|berth)\b", question or "", re.I):
            out.append("{0} port congestion notice".format(carrier))
    if not out or mode in ("general", "search"):
        out.append(" ".join(str(question or "").split())[:200])
    seen, final = set(), []
    for q in out:
        if q and q.lower() not in seen:
            seen.add(q.lower())
            final.append(q)
    return final[:MAX_QUERIES]


def _friendly(query):
    q = (query or "").casefold()
    if re.search(r"\b(port|terminal|congestion|berth)\b", q):
        return "Checking the port…"
    if re.search(r"\b(vessel|voyage|imo|sailing)\b", q):
        return "Checking the vessel…"
    if re.search(r"\b(advisory|notice|announcement|schedule update|news)\b", q):
        return "Checking the latest public updates…"
    if re.search(r"\b(error|exception|timeout|403|401|err_|failed|restricted|denied)\b", q):
        return "Looking up this error…"
    return "Searching the web…"


def investigate(question, mode, brief, subject=None, fresh=False, pid=None):
    """
    {"ok": True, "results": [{url, title, snippet, publisher, kind, text?}],
     "searches": [...], "fetched": [...], "errors": [...], "cached": bool}
    or {"ok": False, "reason"} — never raises.
    """
    if not enabled():
        return {"ok": False, "reason": why_off()}
    clean = redact(brief or {})
    queries = queries_for(mode, question, clean)
    key = hashlib.sha256(json.dumps([mode, subject, queries], sort_keys=True)
                         .encode("utf-8")).hexdigest()
    cache_s = _num("ATLAS_SEARCH_CACHE_S", 1800)
    with _lock:
        hit = _cache.get(key)
    if hit and not fresh and time.time() - hit[0] < cache_s:
        stage(pid, "Using what I found a few minutes ago…")
        return dict(hit[1], cached=True)
    results, errors, fetched, seen = [], [], [], set()
    for q in queries:
        stage(pid, _friendly(q), q)
        try:
            found = search(q)
        except ResearchError as error:
            errors.append("search '{0}': {1}".format(q[:60], error))
            continue
        for item in found:
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            host, kind = publisher(item["url"])
            results.append(dict(item, publisher=host, kind=kind, query=q))
    order = {"Carrier (official)": 0, "Port / terminal": 1, "Official documentation": 2,
             "Government": 3, "Industry source": 4, "Other source": 5}
    results.sort(key=lambda r: order.get(r["kind"], 9))
    for item in [r for r in results if fetch_allowed(r["url"])][:MAX_FETCHES]:
        stage(pid, "Reading {0}…".format(item["publisher"]), item["url"])
        try:
            item["text"] = page_text(item["url"])
            fetched.append(item["url"])
        except ResearchError as error:
            errors.append("{0}: {1}".format(item["publisher"], error))
    if not results and errors:
        return {"ok": False, "reason": "the search service could not be used: " + errors[0],
                "searches": queries, "errors": errors}
    out = {"ok": True, "results": results[:8], "searches": queries, "fetched": fetched,
           "errors": errors, "cached": False, "at": time.time()}
    with _lock:
        _cache[key] = (time.time(), out)
    return out


def clear_cache():
    with _lock:
        _cache.clear()
        _robots.clear()
