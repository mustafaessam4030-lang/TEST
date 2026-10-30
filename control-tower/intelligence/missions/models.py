"""
Models for missions: what is there, which one a task gets, and a record of every call.

REGISTRY   every model a mission may use, with what it is good for, where it
           runs, the data it may see, its cost and its fallbacks. The Cortex
           list is the account's allowlist as read on 30 Oct 2026 (Claude
           Opus is in the account but NOT allowlisted, so it is not here);
           ATLAS_MISSION_MODELS (JSON) can replace it. A model is only
           "available" when its provider says so now (health()).
route()    hard filters first (capability, data tier, health, budget, the
           resource profile), then a score; the local model is preferred
           when it is good enough. Explains its choice in words.
call()     the one way a mission calls a model: through the router, counted
           in the ledger (model_invocations.jsonl in the intelligence store:
           provider, model, task, time, latency, tokens as usage_in /
           usage_out (the store refuses any field named "token"), cost
           estimate, status, a hash of the output, verification status). The prompt is
           not stored, only its hash. Never a picture (local vision only).

Numbers, dates and statuses never come from here: a model only plans,
words, classifies or reviews, and what it says is checked (factguard).
"""
import hashlib
import json
import os
import threading
import time

from .. import store as _store

# What each model is for: 0 = no, 1 = usable, 2 = good, 3 = best we have.
DEFAULT = [
    {"id": "deterministic", "provider": "code", "where": "tower", "caps": {"numbers": 3, "checks": 3},
     "tiers": ["T0", "T1", "T2"], "cost": 0.0, "note": "ATLAS's own code: the authority for numbers, statuses and checks"},
    {"id": "local", "provider": "ollama", "where": "tower", "caps": {"classify": 2, "extract": 2, "explain": 1, "word": 1, "plan": 0, "reason": 1, "review": 0},
     "tiers": ["T0", "T1", "T2"], "cost": 0.0, "note": "The tower's own model (Ollama); private, free, one answer at a time"},
    {"id": "claude-sonnet-4-5", "provider": "cortex", "where": "snowflake (cross-region: AWS US)", "caps": {"plan": 3, "reason": 3, "explain": 3, "word": 3, "review": 2, "classify": 2, "extract": 2},
     "tiers": ["T0", "T1"], "cost": 1.0, "fallbacks": ["claude-sonnet-4-6", "local"]},
    {"id": "claude-sonnet-4-6", "provider": "cortex", "where": "snowflake (cross-region: AWS US)", "caps": {"plan": 3, "reason": 3, "explain": 3, "word": 3, "review": 2, "classify": 2, "extract": 2},
     "tiers": ["T0", "T1"], "cost": 1.0, "fallbacks": ["claude-sonnet-4-5", "local"]},
    {"id": "llama3.3-70b", "provider": "cortex", "where": "snowflake", "caps": {"review": 3, "reason": 2, "explain": 2, "word": 2, "classify": 2, "extract": 2, "plan": 1},
     "tiers": ["T0", "T1"], "cost": 0.4, "family": "llama", "fallbacks": ["llama3.1-70b", "local"],
     "note": "A different model family from Claude: the independent reviewer"},
    {"id": "llama3.1-8b", "provider": "cortex", "where": "snowflake", "caps": {"classify": 2, "extract": 2, "word": 1, "explain": 1},
     "tiers": ["T0", "T1"], "cost": 0.1, "family": "llama", "fallbacks": ["local"]},
]

_lock = threading.Lock()
QUIET = threading.local()  # set while a mission calls a provider: chat's own ledger row is not written twice
PROVIDERS = {}            # tests put their stand-ins here: {"cortex": fn(model) -> provider, "ollama": provider}
LOCAL_BLOCKED = lambda: False      # the engine sets it: no local model while the ATA run is busy


def registry():
    raw = os.environ.get("ATLAS_MISSION_MODELS")
    if raw:
        try:
            rows = json.loads(raw)
            if isinstance(rows, list) and rows:
                return rows
        except ValueError:
            pass
    return DEFAULT


def family(m):
    return m.get("family") or ("claude" if m["id"].startswith("claude") else m["id"].split("-")[0])


def _provider(m):
    if m["provider"] == "code":
        return None
    if m["provider"] == "ollama":
        if "ollama" in PROVIDERS:
            return PROVIDERS["ollama"]
        from .. import llm
        p = llm.provider()
        return getattr(p, "then", p) if isinstance(p, llm.ChainProvider) else p
    if m["provider"] == "cortex":
        if "cortex" in PROVIDERS:
            return PROVIDERS["cortex"](m["id"])
        from .. import cortex as _cortex

        class _At(_cortex.CortexProvider):                     # one Cortex model, named
            @property
            def model(self):
                return m["id"]
        return _At()
    return None


def health(m):
    """{"ok", "detail"} now: the provider's own health, never assumed."""
    if m["provider"] == "code":
        return {"ok": True, "detail": "always"}
    try:
        p = _provider(m)
        if p is None:
            return {"ok": False, "detail": "no provider"}
        h = p.health() or {}
        return {"ok": bool(h.get("ok")), "detail": h.get("detail") or ("ready" if h.get("ok") else "not available")}
    except Exception as error:
        return {"ok": False, "detail": type(error).__name__}


def snapshot():
    """The registry with live health and measured latency, for Mission Control."""
    lat = latency()
    out = []
    for m in registry():
        row = dict(m)
        row["health"] = health(m)
        row["p50_ms"] = lat.get(m["id"])
        out.append(row)
    return out


# ── the router ──────────────────────────────────────────────────────────

PROFILES = {
    # how many calls may run at once, per kind; billed models allowed or not
    "normal":    {"local": 1, "cortex": 2, "sites": 1, "billed": True, "label": "Normal"},
    "deep":      {"local": 1, "cortex": 4, "sites": 2, "billed": True, "label": "Deep"},
    "emergency": {"local": 1, "cortex": 6, "sites": 3, "billed": True, "label": "Emergency"},
    "drill":     {"local": 1, "cortex": 0, "sites": 0, "billed": False, "label": "Drill (no billed models, no writes)"},
}


def route(need, tier="T1", profile="deep", used_tokens=0, budget=80000, avoid_family=None, prefer_local=True, exclude=()):
    """-> (model row | None, why). `need` is a capability (plan, reason, word, review, classify, extract, explain)."""
    prof = PROFILES.get(profile) or PROFILES["deep"]
    rejected = []
    cands = []
    for m in registry():
        if m["provider"] == "code" or m["id"] in exclude:
            continue
        score = (m.get("caps") or {}).get(need, 0)
        if score <= 0:
            continue
        if tier not in (m.get("tiers") or []):
            rejected.append("{0}: not for {1} data".format(m["id"], tier))
            continue
        billed = (m.get("cost") or 0) > 0
        if billed and (not prof["billed"] or prof.get("cortex", 0) <= 0):
            rejected.append("{0}: the {1} profile uses no billed model".format(m["id"], prof["label"]))
            continue
        if billed and used_tokens >= budget:
            rejected.append("{0}: the mission's token budget is used".format(m["id"]))
            continue
        if m["provider"] == "ollama" and LOCAL_BLOCKED():
            rejected.append("{0}: the ATA run is using the machine".format(m["id"]))
            continue
        if avoid_family and family(m) == avoid_family:
            rejected.append("{0}: same family as the draft it reviews".format(m["id"]))
            continue
        h = health(m)
        if not h["ok"]:
            rejected.append("{0}: {1}".format(m["id"], h["detail"]))
            continue
        local_bonus = 0.6 if (prefer_local and not billed and score >= 2) else 0
        cands.append((score + local_bonus - 0.2 * (m.get("cost") or 0), m))
    if not cands:
        return None, "no model fits ({0})".format("; ".join(rejected[:4]) or "none registered")
    cands.sort(key=lambda x: -x[0])
    best = cands[0][1]
    return best, "{0} for {1}{2}".format(best["id"], need, " (best available)" if len(cands) > 1 else "")


# ── the ledger ──────────────────────────────────────────────────────────

LEDGER = "model_invocations.jsonl"
_lat = {}


def latency():
    with _lock:
        return {k: int(sorted(v)[len(v) // 2]) for k, v in _lat.items() if v}


def _record(row):
    try:
        _store.append(LEDGER, row)
    except Exception:
        pass


def ledger(mission_id=None, limit=500):
    try:
        rows = _store.read(LEDGER) or []
    except Exception:
        rows = []
    if mission_id:
        rows = [r for r in rows if r.get("mission_id") == mission_id]
    return rows[-limit:]


def tokens_used(mission_id):
    return sum((r.get("usage_in") or 0) + (r.get("usage_out") or 0) for r in ledger(mission_id, 5000))


class ModelUnavailable(Exception):
    pass


def calls_made(mission_id, task_id):
    return sum(1 for r in ledger(mission_id, 5000) if r.get("task_id") == task_id)


def call_cap():
    """Model calls one mission task may make: the first choice and one fallback, unless configured."""
    try:
        return max(1, int(os.environ.get("ATLAS_MISSION_CALLS_PER_TASK") or 2))
    except ValueError:
        return 2


def call(need, system, prompt, mission_id=None, task_id=None, purpose=None, tier="T1", profile="deep",
         budget=80000, avoid_family=None, max_tokens=600, timeout=40, json_mode=False, max_calls=None):
    """-> {"text", "model", "family", "why"}; raises ModelUnavailable when no model fits or every fit failed.
    A mission task makes at most max_calls calls (call_cap()): beyond that it continues without a model."""
    used = tokens_used(mission_id) if mission_id else 0
    tried = set()
    why_all = []
    left = 3
    if mission_id and task_id:
        left = (max_calls or call_cap()) - calls_made(mission_id, task_id)
        if left <= 0:
            raise ModelUnavailable("this task has used its {0} model call(s)".format(max_calls or call_cap()))
    for _ in range(min(3, left)):
        m, why = route(need, tier, profile, used, budget, avoid_family, exclude=tried)
        if m is None:
            why_all.append(why)
            break
        tried.add(m["id"])
        p = _provider(m)
        started = time.time()
        row = {"invocation_id": hashlib.sha1("{0}{1}{2}".format(time.time(), task_id, m["id"]).encode()).hexdigest()[:16],
               "mission_id": mission_id, "task_id": task_id, "purpose": purpose or need, "provider": m["provider"],
               "model_id": m["id"], "where": m.get("where"), "data_tier": tier, "started_at": _store.stamp(started),
               "prompt_sha256": hashlib.sha256((system + "\n" + prompt).encode("utf-8")).hexdigest()}
        try:
            QUIET.on = True
            try:
                text = p.generate(system, prompt, timeout=timeout, json_mode=json_mode, max_tokens=max_tokens)
            finally:
                QUIET.on = False
            ms = (time.time() - started) * 1000
            inp, out = (len(system) + len(prompt)) // 4, len(text or "") // 4      # estimated when the provider gives none
            row.update(status="ok", latency_ms=int(ms), usage_in=inp, usage_out=out,
                       cost_estimate=round((inp + out) / 1e6 * (m.get("cost") or 0), 6), usage_estimated=True,
                       output_sha256=hashlib.sha256((text or "").encode("utf-8")).hexdigest(), verification="unverified")
            _record(row)
            with _lock:
                _lat.setdefault(m["id"], []).append(ms)
                _lat[m["id"]] = _lat[m["id"]][-50:]
            return {"text": text or "", "model": m["id"], "family": family(m), "why": why}
        except Exception as error:
            row.update(status="error", latency_ms=int((time.time() - started) * 1000), error_class=type(error).__name__,
                       usage_in=0, usage_out=0, cost_estimate=0)
            _record(row)
            why_all.append("{0}: {1}".format(m["id"], type(error).__name__))      # the next best is tried, never the same twice
    raise ModelUnavailable("; ".join(why_all) or "no model available")


def record_chat(model, provider, ok, started, system, prompt, text=None, error=None):
    """One ledger for every model call (plan S1): ATLAS's chat calls are recorded here too, with the
    same fields as a mission's (purpose "chat", no mission). Chat's routing is unchanged; the prompt is
    kept only as a hash. ATLAS_ROUTER=legacy stops these rows. Never raises."""
    if getattr(QUIET, "on", False) or str(os.environ.get("ATLAS_ROUTER") or "").strip().lower() == "legacy":
        return
    try:
        inp, out = (len(system or "") + len(prompt or "")) // 4, len(text or "") // 4
        row = {"invocation_id": hashlib.sha1("{0}{1}".format(time.time(), model).encode()).hexdigest()[:16],
               "mission_id": None, "task_id": None, "purpose": "chat", "provider": provider, "model_id": str(model or "")[:80],
               "started_at": _store.stamp(started), "latency_ms": int((time.time() - started) * 1000),
               "prompt_sha256": hashlib.sha256(((system or "") + "\n" + (prompt or "")).encode("utf-8")).hexdigest()}
        if ok:
            m = next((x for x in registry() if x["id"] == model), {})
            row.update(status="ok", usage_in=inp, usage_out=out, usage_estimated=True,
                       cost_estimate=round((inp + out) / 1e6 * (m.get("cost") or 0), 6),
                       output_sha256=hashlib.sha256((text or "").encode("utf-8")).hexdigest())
        else:
            row.update(status="error", error_class=str(error or "error")[:60], usage_in=0, usage_out=0, cost_estimate=0)
        _record(row)
    except Exception:
        pass
