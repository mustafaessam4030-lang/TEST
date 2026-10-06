"""
ATLAS's research — the public web, read for an operator's question.

ATLAS answers run facts from the run. When a question needs more — where a
vessel is, whether a port is congested, what a carrier announced, what an
error means and what fixes it — this module asks Claude, with the Messages
API's own web search and web fetch tools, to look it up and write the answer
the way a colleague would, from what it actually found.

WHAT IT SENDS. The operator's question, and a short brief of what the run
holds about the subject (reference, carrier, dates, the recorded error),
redacted: URLs lose their query strings, nothing named like a secret is kept,
and no credential, security code or CAPTCHA value is ever in the run state
to begin with. Nothing else.

WHAT COMES BACK, AND WHAT IS KEPT. The answer text, the searches that ran,
the pages that were read, and the sources the answer cites. A source is shown
only when the API's own search or fetch returned that URL in this request; a
URL in the answer that did not come back from a search or fetch is removed.
Nothing here is ever written to the learning store as fact.

WHAT THE OPERATOR SEES WHILE IT RUNS. The request is streamed, so each search
query and each page read is known the moment it starts. Those, and nothing
invented, are the progress lines the chat shows (progress()).

OFF unless configured. ATLAS_RESEARCH_API_KEY (or ANTHROPIC_API_KEY) turns it
on; ATLAS_RESEARCH=0 turns it off regardless. Without it ATLAS says plainly
that live research is not switched on, and answers from the run.

    ATLAS_RESEARCH_API_KEY / ANTHROPIC_API_KEY   the API key
    ATLAS_RESEARCH                               0 = off
    ATLAS_RESEARCH_BASE_URL                      default https://api.anthropic.com
    ATLAS_RESEARCH_MODEL                         default claude-opus-5-5
    ATLAS_RESEARCH_MAX_SEARCHES                  default 6 per question
    ATLAS_RESEARCH_CACHE_S                       default 1800 (30 min)
    ATLAS_RESEARCH_TIMEOUT_S                     default 120
"""

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

API_VERSION = "2023-06-01"
DEFAULT_BASE = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-opus-5-5"
SEARCH_TOOL = "web_search_20250305"
FETCH_TOOL = "web_fetch_20250910"
MAX_CONTINUATIONS = 3

# Who publishes what: shown beside each source so the operator can weigh it.
OFFICIAL_CARRIERS = (
    "msc.com", "cma-cgm.com", "maersk.com", "hapag-lloyd.com", "one-line.com",
    "evergreen-line.com", "coscoshipping.com", "lines.coscoshipping.com", "oocl.com",
    "yangming.com", "hmm21.com", "zim.com", "pilship.com", "grimaldi.napoli.it",
    "grimaldi-lines.com", "afklcargo.com", "airfranceklm.com", "dhl.com", "qrcargo.com",
    "emirates.com", "skycargo.com", "turkishcargo.com", "kline.com", "ecuworldwide.com")
PORT_HINTS = ("port", "ports", "harbour", "harbor", "terminal", "gpha.gov", "mpa.gov",
              "portauthority", "apmterminals.com", "dpworld.com", "msc-terminal")
DOC_HINTS = ("playwright.dev", "learn.microsoft.com", "docs.microsoft.com", "developer.mozilla.org",
             "docs.python.org", "chromium.org", "docs.snowflake.com", "platform.claude.com",
             "docs.github.com", "w3.org", "support.google.com")
INDUSTRY_HINTS = ("lloydslist", "joc.com", "splash247", "seatrade", "porttechnology", "drewry",
                  "sea-intelligence", "freightos", "xeneta", "theloadstar", "aircargonews",
                  "marinetraffic.com", "vesselfinder.com", "myshiptracking.com")

SECRET_KEYS = re.compile(r"pass|secret|token|credential|authorization|cookie|captcha|"
                         r"security.?code|otp|api.?key", re.I)

SYSTEM = """You are ATLAS, the operations intelligence inside Mantrac's ATA Control Tower — \
an experienced logistics and automation colleague. You answer the operator's question about \
shipments, carriers, vessels, ports, customs clearance, or an automation error.

How you work:
- Start from the CONTROL TOWER RUN brief you are given: it is what our own run verified. Those \
values are authoritative for run facts. Never silently replace them with something from the web.
- Then research what the question needs, with targeted searches (the reference with the \
carrier; the vessel and voyage; the port and its operational notices; the carrier's official \
notices; for an error, the exact error text with the library/vendor). Prefer, in order: the \
carrier's official sources, port or terminal authorities, official documentation (vendor, \
browser, library, Microsoft), reliable industry sources, then others only when useful and \
clearly identified. Do not run one vague search when a precise one is possible. Do not search \
when the question is plain general knowledge you can answer reliably.
- If one source does not have the answer, try a reasonable alternative before giving up.
- When the run and an outside source disagree, say both, say which is newer or more \
authoritative if that can be established, and do not pick one silently.
- Never invent anything: no vessel positions, ETAs, ATAs, port events, shipment events, \
tracking numbers, sources, or carrier statements. If you could not verify something, say \
"I couldn't verify …" and say what you did verify. Never claim you checked a page you did not \
check. Never suggest a researched explanation is proven unless the evidence proves it.
- For an error: separate the observed error from its cause. Rank possible causes as CONFIRMED, \
LIKELY, POSSIBLE or UNKNOWN, say why each fits the evidence, the safest diagnostic step, the \
fix, and how to verify the fix. Do not recommend randomly changing configuration, raising \
timeouts blindly, or retrying a blocked carrier repeatedly.
- Security is absolute: never suggest or attempt to bypass CAPTCHA, anti-bot systems, \
carrier access restrictions or authentication; never suggest rotating IPs or residential \
proxies to evade blocking; never look for security codes; never expose credentials. If access \
was restricted, explain it and research official/public information instead.
- Do not try to read carrier tracking pages that require a human verification step.

How you write:
- Like a calm, direct, experienced colleague sitting next to the operator — natural sentences, \
first person ("I checked the run first…", "I couldn't verify…"). Not a support bot, not a \
database dump, no "Based on the available information".
- Short opening line that answers the question. Then what we know from the run, what the \
research found, what is uncertain, what it probably means, and what you recommend — in that \
spirit, not as a rigid template. Bullets only when they help. Plain markdown: **bold** for a \
short heading, "- " bullets, "1. " steps. No tables. No raw URLs in the text (sources are \
listed separately from your citations). Keep it focused; no padding."""

MODE_NOTES = {
    "shipment": "The operator is asking about a shipment. Give the most useful verified picture: "
                "carrier, mode, route/ports, vessel and voyage, latest event, ETA/ATA, delays or "
                "exceptions — only what you can verify or what the run holds.",
    "error": "The operator is asking why something failed. Investigate the recorded error: what "
             "it means, likely causes ranked against the run's evidence, the safest next step, "
             "and how to verify a fix. The run's own evidence decides what is CONFIRMED.",
    "mixed": "The question mixes our run with outside information. Answer FROM OUR RUN first, "
             "then FROM EXTERNAL RESEARCH, then connect the two carefully — never claim the "
             "outside information caused the run's result unless the evidence supports it.",
    "general": "A general logistics or operations question. Answer from knowledge; search only "
               "if it depends on current information.",
}

_lock = threading.Lock()
_cache = {}            # key -> (time, result)
_progress = {}         # id -> {"stages": [...], "done": bool, "at": float}


# ── configuration ────────────────────────────────────────────────────────

def api_key():
    return (os.environ.get("ATLAS_RESEARCH_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
            or "").strip()


def enabled():
    if (os.environ.get("ATLAS_RESEARCH") or "").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(api_key())


def why_off():
    if (os.environ.get("ATLAS_RESEARCH") or "").strip().lower() in ("0", "false", "no", "off"):
        return "web research is switched off on this Control Tower (ATLAS_RESEARCH=0)"
    return ("web research isn't switched on for this Control Tower yet (it needs an API key: "
            "ATLAS_RESEARCH_API_KEY)")


def _num(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


def config():
    return {"base": (os.environ.get("ATLAS_RESEARCH_BASE_URL") or DEFAULT_BASE).rstrip("/"),
            "model": os.environ.get("ATLAS_RESEARCH_MODEL") or DEFAULT_MODEL,
            "max_searches": int(_num("ATLAS_RESEARCH_MAX_SEARCHES", 6)),
            "cache_s": _num("ATLAS_RESEARCH_CACHE_S", 1800),
            "timeout_s": _num("ATLAS_RESEARCH_TIMEOUT_S", 120)}


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
    """The run brief as it may be sent: no secrets by name, URLs without queries."""
    if depth > 5:
        return None
    if isinstance(value, dict):
        return {k: redact(v, depth + 1) for k, v in value.items()
                if not SECRET_KEYS.search(str(k)) and v not in (None, "", [], {})}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in list(value)[:20]]
    if isinstance(value, str):
        text = re.sub(r"(https?://[^\s?#\"']+)[?#][^\s\"']*", r"\1", value)
        return text[:600]
    return value


def publisher(url):
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if any(host == d or host.endswith("." + d) for d in OFFICIAL_CARRIERS):
        return host, "Carrier (official)"
    if any(h in host for h in PORT_HINTS):
        return host, "Port / terminal"
    if any(host == d or host.endswith("." + d) for d in DOC_HINTS):
        return host, "Official documentation"
    if any(h in host for h in INDUSTRY_HINTS):
        return host, "Industry source"
    if host.endswith(".gov") or ".gov." in host:
        return host, "Government"
    return host, "Other source"


# ── the call ─────────────────────────────────────────────────────────────

class ResearchError(Exception):
    pass


def _tools(cfg, fresh):
    return [{"type": SEARCH_TOOL, "name": "web_search", "max_uses": max(1, cfg["max_searches"])},
            {"type": FETCH_TOOL, "name": "web_fetch", "max_uses": 4,
             "citations": {"enabled": True}, "max_content_tokens": 20000}]


def _friendly(query):
    """The operator-facing words for one real search, from its own query."""
    q = (query or "").casefold()
    if re.search(r"\b(port|terminal|harbou?r|congestion|berth)\b", q):
        return "Checking the port…"
    if re.search(r"\b(vessel|voyage|imo|ship position|sailing)\b", q):
        return "Checking the vessel…"
    if re.search(r"\b(error|exception|timeout|403|401|err_|failed|restricted|denied)\b", q):
        return "Looking up this error…"
    if re.search(r"\b(advisory|notice|announcement|customer advisory|news)\b", q):
        return "Checking the latest public updates…"
    return "Looking at the carrier information…" if re.search(
        r"\b(track|tracking|b/?l|bill of lading|awb|container|booking)\b", q) \
        else "Searching the web…"


def _post_stream(cfg, body, pid, seen):
    """One streamed request. Returns (content blocks, stop_reason, usage)."""
    data = json.dumps(dict(body, stream=True)).encode("utf-8")
    request = urllib.request.Request(cfg["base"] + "/v1/messages", data=data, method="POST",
                                     headers={"x-api-key": api_key(),
                                              "anthropic-version": API_VERSION,
                                              "content-type": "application/json",
                                              "accept": "text/event-stream"})
    try:
        response = urllib.request.urlopen(request, timeout=cfg["timeout_s"])
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8", "replace"))
            message = ((detail.get("error") or {}).get("message") or "")[:240]
        except Exception:
            message = ""
        raise ResearchError("the research service answered HTTP {0}{1}".format(
            error.code, ": " + message if message else ""))
    except (urllib.error.URLError, OSError) as error:
        raise ResearchError("the research service could not be reached ({0})".format(
            str(getattr(error, "reason", error))[:160]))
    blocks, stop, usage, event = {}, None, {}, None
    wrote = False
    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[5:].strip())
            except ValueError:
                continue
            kind = payload.get("type") or event
            if kind == "error":
                raise ResearchError("the research service reported: {0}".format(
                    ((payload.get("error") or {}).get("message") or "an error")[:200]))
            if kind == "content_block_start":
                index = payload.get("index", len(blocks))
                block = dict(payload.get("content_block") or {})
                if block.get("type") == "server_tool_use":
                    block["_json"] = ""
                if block.get("type") == "text":
                    block.setdefault("text", "")
                    block["citations"] = list(block.get("citations") or [])
                blocks[index] = block
                if block.get("type") in ("web_search_tool_result", "web_fetch_tool_result"):
                    _note_results(block, seen)
            elif kind == "content_block_delta":
                block = blocks.get(payload.get("index"))
                delta = payload.get("delta") or {}
                if block is None:
                    continue
                if delta.get("type") == "input_json_delta":
                    block["_json"] = block.get("_json", "") + (delta.get("partial_json") or "")
                elif delta.get("type") == "text_delta":
                    block["text"] = block.get("text", "") + (delta.get("text") or "")
                    if not wrote and (delta.get("text") or "").strip() and seen["searches"]:
                        wrote = True
                        stage(pid, "Putting it together…")
                elif delta.get("type") == "citations_delta":
                    block.setdefault("citations", []).append(delta.get("citation") or {})
            elif kind == "content_block_stop":
                block = blocks.get(payload.get("index"))
                if block is not None and block.get("type") == "server_tool_use":
                    try:
                        block["input"] = json.loads(block.pop("_json") or "{}")
                    except ValueError:
                        block["input"] = {}
                    if block.get("name") == "web_search":
                        query = str(block["input"].get("query") or "")[:200]
                        seen["searches"].append(query)
                        stage(pid, _friendly(query), query)
                    elif block.get("name") == "web_fetch":
                        url = str(block["input"].get("url") or "")[:300]
                        seen["fetches"].append(url)
                        stage(pid, "Reading {0}…".format(publisher(url)[0] or "a page"), url)
            elif kind == "message_delta":
                stop = (payload.get("delta") or {}).get("stop_reason") or stop
                usage.update(payload.get("usage") or {})
            elif kind == "message_start":
                usage.update(((payload.get("message") or {}).get("usage")) or {})
    content = []
    for index in sorted(blocks):
        block = blocks[index]
        block.pop("_json", None)
        content.append(block)
    return content, stop, usage


def _note_results(block, seen):
    content = block.get("content")
    if block.get("type") == "web_search_tool_result":
        if isinstance(content, dict):
            seen["errors"].append(content.get("error_code") or "search error")
            return
        for item in content or []:
            if item.get("url"):
                seen["urls"][item["url"]] = {"title": item.get("title"),
                                             "page_age": item.get("page_age")}
    else:
        if isinstance(content, dict) and content.get("type") == "web_fetch_tool_result_error":
            seen["errors"].append(content.get("error_code") or "fetch error")
            return
        if isinstance(content, dict) and content.get("url"):
            doc = content.get("content") or {}
            seen["urls"][content["url"]] = {"title": doc.get("title"), "fetched": True,
                                            "retrieved_at": content.get("retrieved_at")}


URL_IN_TEXT = re.compile(r"https?://[^\s)\]>\"']+")


def _clean_text(text, seen_urls):
    """No URL in the answer that did not come back from a search or a fetch."""
    def keep(match):
        url = match.group(0).rstrip(".,;:")
        return match.group(0) if url in seen_urls else "(link removed: not from a checked source)"
    return URL_IN_TEXT.sub(keep, text or "").strip()


def investigate(question, mode, brief, subject=None, fresh=False, pid=None, history=None):
    """
    Research one question. Returns
      {"ok", "answer", "sources": [{url, title, publisher, kind, cited}], "searches",
       "fetched", "errors", "cached", "model"}
    or {"ok": False, "reason"} — never raises.
    """
    if not enabled():
        return {"ok": False, "reason": why_off()}
    cfg = config()
    mode = mode if mode in MODE_NOTES else "general"
    clean_brief = redact(brief or {})
    key = hashlib.sha256(json.dumps([mode, subject or " ".join(str(question).casefold().split()),
                                     clean_brief], sort_keys=True, default=str)
                         .encode("utf-8")).hexdigest()
    with _lock:
        hit = _cache.get(key)
    if hit and not fresh and time.time() - hit[0] < cfg["cache_s"]:
        stage(pid, "Using what I found a few minutes ago…")
        return dict(hit[1], cached=True, cached_at=hit[0])
    prompt = ("{0}\n\nCONTROL TOWER RUN (verified by our automation — authoritative for run "
              "facts; may be empty):\n{1}\n\nOPERATOR'S QUESTION:\n{2}{3}").format(
                  MODE_NOTES[mode], json.dumps(clean_brief, indent=1, default=str)[:6000],
                  str(question)[:1200],
                  "\n\nThe operator asked for the latest information: do not rely on anything "
                  "older than necessary." if fresh else "")
    messages = [{"role": "user", "content": prompt}]
    seen = {"urls": {}, "searches": [], "fetches": [], "errors": []}
    body = {"model": cfg["model"], "max_tokens": 2500, "system": SYSTEM,
            "tools": _tools(cfg, fresh), "messages": messages}
    try:
        for _turn in range(MAX_CONTINUATIONS + 1):
            content, stop, _usage = _post_stream(cfg, body, pid, seen)
            if stop != "pause_turn":
                break
            # A long search turn paused: send it back unchanged to continue.
            body = dict(body, messages=messages + [{"role": "assistant", "content": content}])
    except ResearchError as error:
        return {"ok": False, "reason": str(error), "searches": seen["searches"]}
    except Exception as error:                               # never into the chat as a crash
        return {"ok": False, "reason": "research stopped unexpectedly: {0}".format(
            str(error)[:160]), "searches": seen["searches"]}

    texts, cited = [], {}
    for block in content:
        if block.get("type") != "text":
            continue
        texts.append(block.get("text") or "")
        for c in block.get("citations") or []:
            url = c.get("url")
            if url and url in seen["urls"]:
                cited.setdefault(url, c.get("title"))
    answer = _clean_text("".join(texts), seen["urls"])
    if not answer:
        return {"ok": False, "reason": "the research came back without an answer",
                "searches": seen["searches"]}
    sources = []
    for url, title in cited.items():
        host, kind = publisher(url)
        meta = seen["urls"].get(url) or {}
        sources.append({"url": url, "title": (title or meta.get("title") or host)[:140],
                        "publisher": host, "kind": kind, "cited": True,
                        "page_age": meta.get("page_age")})
    order = {"Carrier (official)": 0, "Port / terminal": 1, "Official documentation": 2,
             "Government": 3, "Industry source": 4, "Other source": 5}
    sources.sort(key=lambda s: order.get(s["kind"], 9))
    result = {"ok": True, "answer": answer, "sources": sources[:8],
              "searches": seen["searches"], "fetched": seen["fetches"],
              "errors": seen["errors"], "cached": False, "model": cfg["model"],
              "at": time.time()}
    with _lock:
        _cache[key] = (time.time(), result)
        for k in [k for k, v in _cache.items() if time.time() - v[0] > cfg["cache_s"] * 4]:
            _cache.pop(k, None)
    return result


def clear_cache():
    with _lock:
        _cache.clear()
