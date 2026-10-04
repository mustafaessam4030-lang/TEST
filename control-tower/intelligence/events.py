"""
The ATLAS event store — OBSERVE.

What happened, run after run, in one append-only file (events.jsonl). Every
record carries its provenance: the run it came from, the shipment, the
carrier, the time. Learning is computed FROM this file; nothing is learned
that is not in it.

    shipment     how a shipment ended, and whether the existing verification
                 pipeline confirmed it (VERIFIED only when every Hub write was
                 read back and matched)
    strategy     carried on a shipment: each navigation strategy tried, and
                 whether it reached the right shipment page
    recovery     one recovery episode: its attempts, joined to the shipment's
                 final, verified outcome
    human_task   one Human Action task, closed: carrier, reason, wait, outcome
    question     what an operator asked: the intent and a reference-free
                 pattern, never the raw conversation
    feedback     what an operator thought of an answer or a recovery — an
                 opinion, kept apart from outcomes and never a verification

Who writes: the dashboard bridge, once attach() has been called — the
automation does that in main(), and only there, so tests and demos never
reach the real file.
"""

import re
import threading

from . import store

FILE = "events.jsonl"
KINDS = ("shipment", "recovery", "human_task", "question", "feedback")
VERDICTS = ("helpful", "not_helpful", "correct", "incorrect", "recovery_worked",
            "recovery_failed", "suggestion_useful", "suggestion_not_useful")

_cache = {"sig": None, "rows": []}
_lock = threading.Lock()


def verified_success(result, verification):
    """
    VERIFIED SUCCESS, by the existing pipeline's own verdicts: the shipment
    was counted successful AND every Hub write it made was read back and
    matched. A success with no read-back, or any read-back that was not True,
    is not verified.
    """
    if str(result or "").upper() != "SUCCESS":
        return False
    values = list((verification or {}).values())
    return bool(values) and all(v is True for v in values)


def record(kind, **fields):
    """Append one event. Unknown kinds are refused. Never raises."""
    if kind not in KINDS:
        return False
    if kind == "feedback" and fields.get("verdict") not in VERDICTS:
        return False
    fields = dict(fields)
    fields["kind"] = kind
    fields.setdefault("at", store.stamp())
    fields.setdefault("epoch", round(store.now(), 1))
    try:
        return store.append(FILE, fields)
    except Exception:
        return False


def all_events(kinds=None):
    """Every event, oldest first. Cached until the file changes."""
    sig = store.signature(FILE)
    with _lock:
        if _cache["sig"] != sig:
            _cache["rows"] = [r for r in store.read(FILE) if r.get("kind") in KINDS]
            _cache["sig"] = sig
        rows = _cache["rows"]
    if kinds:
        return [r for r in rows if r.get("kind") in kinds]
    return list(rows)


def invalidate():
    with _lock:
        _cache["sig"] = None


_REF = re.compile(r"\b(?:[A-Z]{1,4}\d{5,}|\d{3}[-\s]?\d{4}[-\s]?\d{4}|\d{6,}|[A-Z]{4}\d{7})\b", re.I)


def question_pattern(text):
    """
    A question with its specifics removed, so repetition can be counted
    without keeping what was said: references become <REF>, other numbers
    <N>. Lower case, one line, bounded.
    """
    text = " ".join(str(text or "").split())
    text = _REF.sub("<REF>", text)
    text = re.sub(r"\d+", "<N>", text)
    return store.redact(text.casefold(), 140)
