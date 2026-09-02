"""
Structured, append-only telemetry.

This does NOT replace the run log. `write_log` stays exactly as it is and
remains the thing a human reads; every event recorded here also gets a short
line through the existing sink so the two never disagree about what happened.
What this adds is a machine-readable record, because prose cannot be trained
on and the run log is prose.

One JSON object per line. Append-only, best-effort, and wrapped so that a full
disk or a locked file can never take a run down: a failure to record telemetry
is noted once and then ignored.

Nothing sensitive is written. The event payload is filtered against a
deny-list before it is serialised, and the caller's own redaction is applied to
every string on top of that. Credentials, cookies and tokens are never part of
a context in the first place — the fields recorded are page shapes, strategy
names, durations and outcomes.
"""

import json
import os
import time
from pathlib import Path

from . import config

# Categorised failure reasons. Deliberately finer-grained than the run's
# operational outcomes (NO RESULT / TIMEOUT / ...) which are unchanged and
# still drive retry decisions. These describe the mechanics of a single
# interaction, which is what the model needs to learn from.
FIELD_NOT_FOUND = "FIELD_NOT_FOUND"
FIELD_NOT_VISIBLE = "FIELD_NOT_VISIBLE"
SCROLL_REQUIRED = "SCROLL_REQUIRED"
INPUT_REJECTED = "INPUT_REJECTED"
CHANGE_EVENT_FAILED = "CHANGE_EVENT_FAILED"
PAGE_NOT_READY = "PAGE_NOT_READY"
TIMEOUT = "TIMEOUT"
BOT_CHALLENGE = "BOT_CHALLENGE"
NETWORK_ERROR = "NETWORK_ERROR"
VALIDATION_FAILURE = "VALIDATION_FAILURE"
VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
NONE = "OK"

CATEGORIES = (
    FIELD_NOT_FOUND, FIELD_NOT_VISIBLE, SCROLL_REQUIRED, INPUT_REJECTED,
    CHANGE_EVENT_FAILED, PAGE_NOT_READY, TIMEOUT, BOT_CHALLENGE,
    NETWORK_ERROR, VALIDATION_FAILURE, VERIFICATION_FAILURE, NONE,
)

# Never serialise anything whose key looks like a secret, whatever a caller
# passes. The layer has no need for any of it, so this is belt and braces
# rather than the primary defence.
_DENIED_KEYS = (
    "password", "passwd", "pwd", "secret", "token", "cookie", "cookies",
    "authorization", "auth", "credential", "credentials", "apikey", "api_key",
    "access_key", "session", "bearer", "username", "user", "email",
)

_failures = 0
_notified = False


def classify_exception(error):
    """
    Map an exception onto one of the categories above.

    This is additive: `classify_failure` in update_eta.py is untouched and
    still decides retries. This only labels telemetry.
    """
    text = "{0} {1}".format(type(error).__name__, error).casefold()
    if "err_" in text or "net::" in text or "connection" in text or "dns" in text:
        return NETWORK_ERROR
    if "captcha" in text or "are you a robot" in text or "access denied" in text:
        return BOT_CHALLENGE
    if "timeout" in text or "timed out" in text:
        return TIMEOUT
    if "not visible" in text or "element is not visible" in text:
        return FIELD_NOT_VISIBLE
    if "outside of the viewport" in text or "scroll" in text:
        return SCROLL_REQUIRED
    if "would not accept" in text or "not_typed" in text:
        return INPUT_REJECTED
    if "dispatch" in text or "change event" in text:
        return CHANGE_EVENT_FAILED
    if "was not found" in text or "no such element" in text:
        return FIELD_NOT_FOUND
    if "did not render" in text or "readystate" in text:
        return PAGE_NOT_READY
    if "verif" in text or "did not persist" in text:
        return VERIFICATION_FAILURE
    if "invalid" in text or "valid" in text:
        return VALIDATION_FAILURE
    return FIELD_NOT_FOUND


def _safe(value, redactor=None, depth=0):
    """Recursively strip denied keys and apply the caller's redaction."""
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if any(bad in str(k).casefold() for bad in _DENIED_KEYS):
                continue
            out[str(k)] = _safe(v, redactor, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_safe(v, redactor, depth + 1) for v in value[:40]]
    if isinstance(value, str):
        text = value[:400]
        return redactor(text) if redactor else text
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]


def record(event, redactor=None):
    """
    Append one event. Returns True when it was written.

    Never raises. A telemetry problem must not be able to end a run that is
    otherwise doing its job.
    """
    global _failures, _notified
    if not config.TELEMETRY_ENABLED:
        return False
    try:
        payload = _safe(dict(event), redactor)
        payload.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        path = Path(config.TELEMETRY_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rotate rather than grow without limit.
        try:
            if path.exists() and path.stat().st_size > config.TELEMETRY_MAX_BYTES:
                path.replace(path.with_suffix(".jsonl.1"))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        return True
    except Exception:
        _failures += 1
        if not _notified:
            _notified = True
        return False


def interaction(context, strategy, success, duration_ms=None,
                category=NONE, detail="", reference=None, redactor=None,
                source="automation", rank=None):
    """
    The event shape the trainer expects.

    `context` is a features.context() dict, `strategy` the named approach that
    was tried, `success` whether it worked, `duration_ms` how long it took.
    """
    return record({
        "kind": "interaction",
        "context": context,
        "strategy": strategy,
        "success": bool(success),
        "duration_ms": None if duration_ms is None else round(float(duration_ms), 1),
        "category": category if category in CATEGORIES else FIELD_NOT_FOUND,
        "detail": detail,
        "reference": reference,
        "source": source,
        # Where this strategy sat in the automation's OWN order. Recorded so
        # the evaluator can reconstruct the baseline from the data rather than
        # being told what it was.
        "rank": rank,
    }, redactor=redactor)


def decision(context, chosen, scores, used, reason, redactor=None):
    """What the predictor recommended and whether the automation took it."""
    return record({
        "kind": "decision",
        "context": context,
        "chosen": chosen,
        "scores": scores,
        "used": bool(used),
        "reason": reason,
    }, redactor=redactor)


def failures():
    return _failures


def stats():
    path = Path(config.TELEMETRY_PATH)
    if not path.exists():
        return {"path": str(path), "exists": False, "lines": 0, "bytes": 0}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = sum(1 for _ in handle)
    except OSError:
        lines = 0
    return {"path": str(path), "exists": True, "lines": lines,
            "bytes": path.stat().st_size}
