"""
Where one ATLAS question spends its time.

    trace.begin()                 at the start of a request (the server)
    with trace.span("route"):     around a stage, anywhere below it
    trace.model(stats)            a model call's own timings (Ollama reports
                                  load, prompt and generation durations)
    trace.useful("rules")         the moment a useful answer existed
    trace.end() -> summary        {"total_ms", "first_useful_ms", "stages", "model"}

Per thread (one request per server thread), so concurrent questions do not
mix. A span outside a request costs one attribute lookup and records nothing.
The last 500 summaries are kept in memory for /api/atlas/perf; nothing is
written to disk and no question text is kept — stage names and numbers only.
"""
import threading
import time
from collections import deque

_local = threading.local()
RECENT = deque(maxlen=500)
_recent_lock = threading.Lock()


class _Trace(object):
    __slots__ = ("t0", "stages", "order", "model", "useful", "useful_by", "stack")

    def __init__(self):
        self.t0 = time.perf_counter()
        self.stages, self.order, self.model = {}, [], []
        self.useful, self.useful_by, self.stack = None, None, []


def begin():
    _local.t = _Trace()


def active():
    return getattr(_local, "t", None) is not None


class span(object):
    """Time a stage. Re-entered stages add up ("kb.search" called twice)."""
    __slots__ = ("name", "s")

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        t = getattr(_local, "t", None)
        self.s = time.perf_counter() if t is not None else None
        if t is not None:
            t.stack.append(self.name)
        return self

    def __exit__(self, *exc):
        t = getattr(_local, "t", None)
        if t is not None and self.s is not None:
            if t.stack and t.stack[-1] == self.name:
                t.stack.pop()
            ms = (time.perf_counter() - self.s) * 1000
            if self.name not in t.stages:
                t.order.append(self.name)
            t.stages[self.name] = t.stages.get(self.name, 0.0) + ms
        return False


def model(stats):
    """One model call: {"purpose", "model", "wall_ms", "load_ms", "prompt_ms",
    "gen_ms", "prompt_tokens", "out_tokens"} — whatever the provider reported."""
    t = getattr(_local, "t", None)
    if t is not None and isinstance(stats, dict):
        row = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in stats.items()}
        row.setdefault("purpose", t.stack[-1] if t.stack else None)
        t.model.append(row)


def useful(by):
    """The first moment an answer the operator could use existed (the rules'
    answer, a knowledge answer, a model's answer). Only the first counts."""
    t = getattr(_local, "t", None)
    if t is not None and t.useful is None:
        t.useful = (time.perf_counter() - t.t0) * 1000
        t.useful_by = by


def end(intent=None):
    t = getattr(_local, "t", None)
    _local.t = None
    if t is None:
        return None
    total = (time.perf_counter() - t.t0) * 1000
    out = {"total_ms": round(total, 1),
           "first_useful_ms": round(t.useful, 1) if t.useful is not None else None,
           "first_useful_by": t.useful_by,
           "stages": {k: round(t.stages[k], 1) for k in t.order},
           "model": t.model, "intent": intent}
    with _recent_lock:
        RECENT.append(dict(out, at=time.time()))
    return out


def server_timing(summary):
    """The summary as a Server-Timing header (browser dev tools show it)."""
    if not summary:
        return ""
    parts = ["total;dur={0}".format(summary["total_ms"])]
    if summary.get("first_useful_ms") is not None:
        parts.append("useful;dur={0}".format(summary["first_useful_ms"]))
    for k, v in list(summary["stages"].items())[:20]:
        parts.append("{0};dur={1}".format("".join(c if c.isalnum() else "_" for c in k), v))
    return ", ".join(parts)


def _pct(values, p):
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(p * len(v)))], 1)


def stats(window_s=None):
    """p50 / p90 per stage and overall, from the recent requests."""
    now = time.time()
    with _recent_lock:
        rows = [r for r in RECENT if window_s is None or now - r["at"] <= window_s]
    stages = {}
    for r in rows:
        for k, v in r["stages"].items():
            stages.setdefault(k, []).append(v)
    by_intent = {}
    for r in rows:
        by_intent.setdefault(r.get("intent") or "?", []).append(r["total_ms"])
    calls = [m for r in rows for m in r["model"]]
    return {"requests": len(rows),
            "total_ms": {"p50": _pct([r["total_ms"] for r in rows], 0.5), "p90": _pct([r["total_ms"] for r in rows], 0.9)},
            "first_useful_ms": {"p50": _pct([r["first_useful_ms"] for r in rows if r["first_useful_ms"] is not None], 0.5),
                                "p90": _pct([r["first_useful_ms"] for r in rows if r["first_useful_ms"] is not None], 0.9)},
            "stages": {k: {"n": len(v), "p50": _pct(v, 0.5), "p90": _pct(v, 0.9)} for k, v in stages.items()},
            "by_intent": {k: {"n": len(v), "p50": _pct(v, 0.5)} for k, v in by_intent.items()},
            "model_calls": {"n": len(calls),
                            "per_request": round(len(calls) / float(len(rows)), 2) if rows else 0,
                            "wall_ms_p50": _pct([c.get("wall_ms") for c in calls if c.get("wall_ms")], 0.5),
                            "prompt_ms_p50": _pct([c.get("prompt_ms") for c in calls if c.get("prompt_ms")], 0.5)}}
