"""
The Tool Catalogue: everything a mission may touch, in one list.

    TOOLS[name] = {kind: read | act, permission, level, idempotent, title, fn}
    read(name, *args, **kw)      -> the tool's result (reads are de-duplicated for TTL seconds)
    allowed(name, can)           -> may a person with these permissions use it
    configure(control=..., escalate=..., load=...)   the server (or a test) hands in the engines

Read tools wrap functions ATLAS already has (the eHub export, the hub's stage
lists, escalations, carrier health, the knowledge base, the run's events,
the automation's own validation and failure classification, web research).
Action tools are exactly three and each is an existing engine:

    reprocess(ref)        -> the ATA run's control channel: control.request("reprocess", ref)
    escalate(awb, reason) -> the Clearance page's escalation: clearance.escalate(row, reason, by)
    queue_human(title)    -> a task for a person in To Do (the mission's own action row)

No tool opens a browser, signs in, sends an email or writes the Hub itself;
the run does that, with its own read-back. A model never adds a tool: plans
naming anything not listed here are rejected (plan.validate).
"""
import hashlib
import json
import threading
import time

TTL = 900                                     # a read shared across missions for 15 minutes

_cfg = {"control": None, "escalate": None, "load": None}
_cache = {}
_lock = threading.Lock()
STATS = {"hits": 0, "misses": 0}


def configure(control=None, escalate=None, load=None):
    """control: fn(action, reference) -> (accepted, message). escalate: fn(row, reason, by) -> (ok, msg, entry).
    load: fn() -> the eHub export. Tests pass stand-ins; the server passes its own engines."""
    if control is not None:
        _cfg["control"] = control
    if escalate is not None:
        _cfg["escalate"] = escalate
    if load is not None:
        _cfg["load"] = load


def reset_for_tests():
    _cfg.update(control=None, escalate=None, load=None)
    with _lock:
        _cache.clear()
    STATS.update(hits=0, misses=0)


# ── read tools (each wraps an existing function) ─────────────────────────
def _C():
    from dashboard import clearance as C
    return C


def export():
    if _cfg["load"] is not None:
        return _cfg["load"]()
    return _C().load()


def export_rows(allowed=None):
    data = export() or {}
    rows = _C().records(data) if data else []
    return [r for r in rows if allowed is None or r.get("bu") in allowed]


def stage_list(stage, bu=None, allowed=None, limit=None):
    from dashboard import hub
    return hub.stage_list(stage, bu, data=export(), limit=limit, allowed=allowed)


def escalation_list():
    return _C().escalations()


def carrier_health():
    from intelligence import carrier_health as H
    return H.health()


def kb_search(text, limit=1):
    from intelligence import kb as KB
    return KB.kb().search(text, limit=limit, approved_only=True)


def run_events(reference=None, since=None):
    """The bridge's shipment outcomes (result + per-field Hub read-back), oldest first."""
    from intelligence import events as E
    rows = E.all_events(("shipment",))
    if reference:
        key = _norm(reference)
        rows = [r for r in rows if _norm(r.get("reference")) == key]
    if since:
        rows = [r for r in rows if (r.get("epoch") or 0) >= since]
    return rows


def validate_record(record):
    """The automation's own date rules for one run record (dashboard/atlas_skills.shipment_checks,
    the checks behind validate_one): [(PASS | FAIL | ..., text)]."""
    from dashboard import atlas_skills
    return atlas_skills.shipment_checks(record)


def failure_class(record):
    """VERIFIED / INFERRED / UNKNOWN root cause of a failed lookup (failures.from_record)."""
    from intelligence import failures
    return failures.from_record(record)


def web_search(query):
    from intelligence import research
    if not research.enabled():
        raise LookupError("web search is not set up (ATLAS_SEARCH_URL)")
    return research.search(query)


def _norm(ref):
    try:
        return _C().norm_awb(ref)
    except Exception:
        return "".join(ch for ch in str(ref or "").upper() if ch.isalnum())


# ── action adapters (the three existing engines) ─────────────────────────
def reprocess(reference, by=None):
    """Queue one reference for the ATA run. -> (accepted, message, engine_ref). The run re-tracks it,
    writes the Hub and reads it back; success is only what that read-back says (actions.verify)."""
    fn = _cfg["control"]
    if fn is None:
        return False, "The ATA run's control channel is not connected here.", None
    accepted, message = fn("reprocess", reference)
    return bool(accepted), message, ("control:reprocess:" + str(reference)) if accepted else None


def escalate(awb, reason, by, bu=None):
    """Open an escalation through the Clearance page's own function (one open escalation per shipment and BU).
    The row is the shipment in that BU: an AWB can appear in more than one BU."""
    C = _C()
    key = _norm(awb)
    rows = [r for r in export_rows() if _norm(r.get("awb")) == key and (bu is None or r.get("bu") == bu)]
    if bu is None and len({r.get("bu") for r in rows}) > 1:
        return False, "{0} is in more than one BU; the action must name the BU.".format(awb), None
    row = rows[0] if rows else None
    fn = _cfg["escalate"] or C.escalate
    ok, message, entry = fn(row, reason, by)
    return bool(ok), message, (entry or {}).get("id")


def queue_human(title, by=None):
    """A task for a person: it is listed in To Do until someone marks it done. Nothing else happens."""
    return True, "Added to To Do: {0}".format(title), "todo"


TOOLS = {
    "export_rows":   {"kind": "read", "permission": "clearance.view", "level": 1, "idempotent": True, "fn": export_rows,
                      "title": "eHub export rows (BU-scoped)"},
    "stage_list":    {"kind": "read", "permission": "clearance.view", "level": 1, "idempotent": True, "fn": stage_list,
                      "title": "The shipments behind a hub number (stage, BU)"},
    "escalations":   {"kind": "read", "permission": "clearance.view", "level": 1, "idempotent": True, "fn": escalation_list,
                      "title": "Escalations on the Clearance page"},
    "carrier_health": {"kind": "read", "permission": "dashboard.view", "level": 1, "idempotent": True, "fn": carrier_health,
                       "title": "Carrier health from run outcomes"},
    "kb_search":     {"kind": "read", "permission": "missions.view", "level": 1, "idempotent": True, "fn": kb_search,
                      "title": "ATLAS's reviewed knowledge"},
    "run_events":    {"kind": "read", "permission": "dashboard.view", "level": 1, "idempotent": True, "fn": run_events,
                      "title": "The run's shipment outcomes and Hub read-back"},
    "validate":      {"kind": "read", "permission": "dashboard.view", "level": 1, "idempotent": True, "fn": validate_record,
                      "title": "The automation's own date rules"},
    "failure_class": {"kind": "read", "permission": "dashboard.view", "level": 1, "idempotent": True, "fn": failure_class,
                      "title": "Failure classification (VERIFIED / INFERRED / UNKNOWN)"},
    "web_search":    {"kind": "read", "permission": "missions.view", "level": 1, "idempotent": True, "fn": web_search,
                      "title": "Public web search (self-hosted SearXNG), never tracking pages"},
    "reprocess":     {"kind": "act", "permission": "runs.start", "level": 3, "idempotent": False, "fn": reprocess,
                      "title": "Re-run one reference in the ATA run (Hub write + read-back by the run)"},
    "escalate":      {"kind": "act", "permission": "clearance.escalate", "level": 3, "idempotent": False, "fn": escalate,
                      "title": "Open an escalation on the Clearance page"},
    "queue_human":   {"kind": "act", "permission": "missions.run", "level": 2, "idempotent": True, "fn": queue_human,
                      "title": "Put a task for a person in To Do"},
}


def allowed(name, can):
    t = TOOLS.get(name)
    return bool(t) and bool(can(t["permission"]))


def read(name, *args, **kw):
    """Call a read tool. The same read (tool + arguments + export date) within TTL is answered once and
    shared, across missions too: the second caller gets the same result, counted in STATS."""
    t = TOOLS.get(name)
    if not t or t["kind"] != "read":
        raise KeyError("not a read tool: {0}".format(name))
    try:
        asof = (export() or {}).get("asof") if name in ("export_rows", "stage_list") else None
    except Exception:
        asof = None
    key = hashlib.sha1(json.dumps([name, args, kw, asof], sort_keys=True, default=str).encode("utf-8")).hexdigest()
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < TTL:
            STATS["hits"] += 1
            return hit[1]
    value = t["fn"](*args, **kw)
    with _lock:
        STATS["misses"] += 1
        _cache[key] = (now, value)
        if len(_cache) > 256:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:64]:
                _cache.pop(k, None)
    return value


def dedupe_key(name, *args, **kw):
    return hashlib.sha1(json.dumps([name, args, kw], sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def catalogue():
    return [{"name": k, "kind": v["kind"], "permission": v["permission"], "level": v["level"],
             "idempotent": v["idempotent"], "title": v["title"]} for k, v in TOOLS.items()]
