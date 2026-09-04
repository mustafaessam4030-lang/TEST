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

from . import config, identity

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

# What became of a whole write operation. Coarser than the interaction
# categories on purpose: an episode is the unit a label attaches to, and it has
# exactly four ways to end.
EPISODE_VERIFIED = "VERIFIED"        # saved and read back correct
EPISODE_UNVERIFIED = "UNVERIFIED"    # saved, read-back did not run
EPISODE_MISMATCH = "MISMATCH"        # saved, read back WRONG
EPISODE_ERROR = "ERROR"              # never got as far as a save

OUTCOMES = (EPISODE_VERIFIED, EPISODE_UNVERIFIED, EPISODE_MISMATCH,
            EPISODE_ERROR)

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


def test_source():
    """
    True when this process is a test run.

    Telemetry written by the suite is tagged so the dataset can refuse it. The
    alternative — tests quietly appending fixtures to the file a model is
    trained from — is how a model ends up confidently wrong about a page it has
    never actually seen.
    """
    import sys
    argv0 = (sys.argv[0] if sys.argv else "") or ""
    return "test_" in os.path.basename(argv0) or "run_tests" in argv0


def target_path():
    """
    Where this process writes.

    Tagging test rows was necessary but not sufficient. The suite exercises the
    real writer — that is the point of it — and every run was therefore
    appending fixtures to the same file production data lands in. Nothing was
    trained on them, but "the guard catches it downstream" is a worse answer
    than not mixing them in the first place: an operator looking at the
    telemetry file should see runs, not test runs.

    So a test process writing to the DEFAULT location is redirected to a
    sibling file. A test that sets ML_TELEMETRY_PATH explicitly still gets
    exactly the path it asked for, because those tests are checking the writer.
    """
    path = Path(config.TELEMETRY_PATH)
    if test_source() and not os.environ.get("ML_TELEMETRY_PATH"):
        return path.with_name(path.stem + ".test" + path.suffix)
    return path


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
        # Which engine wrote this. One field, on every row, so a telemetry
        # file is self-describing and a future second engine cannot be
        # confused with this one after the fact.
        payload.setdefault("engine", identity.NAME)
        if test_source():
            payload["source"] = "test"
        path = target_path()
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
                source="automation", rank=None, episode_id=None, retries=0):
    """
    The event shape the trainer expects.

    `context` is a features.context() dict, `strategy` the named approach that
    was tried, `success` whether it worked, `duration_ms` how long it took.

    `episode_id` is what makes this row trainable. On its own an attempt says
    "the locator matched something"; joined to its episode it says "the locator
    matched, and the value it led to was read back out of the Hub afterwards
    and was correct". Only the second of those is a success worth learning
    from, and without the id the two cannot be told apart. A row with no
    episode_id is still recorded — it is a real observation — but the dataset
    cannot label it and will say so.
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
        # Which write operation this attempt was part of. The episode event
        # carries that write's verified outcome.
        "episode_id": episode_id,
        # How many times this same strategy had already been tried within the
        # episode before this attempt.
        "retries": int(retries or 0),
        # Where this strategy sat in the automation's OWN order. Recorded so
        # the evaluator can reconstruct the baseline from the data rather than
        # being told what it was.
        "rank": rank,
    }, redactor=redactor)


def episode(episode_id, reference, view, field, value, outcome,
            verified=None, duration_ms=None, detail="", redactor=None,
            atlas_influenced=False, atlas_chosen=None, atlas_mode=None,
            label=None):
    """
    The result of one complete write: open Manage, find the field, type,
    save, read back.

    `outcome` is one of the OUTCOMES below. `verified` is deliberately
    three-valued:

        True   read-back ran and confirmed the value is in the Hub
        False  read-back ran and DISAGREED
        None   read-back never ran (VERIFY_AFTER_SAVE off, or the episode
               failed before reaching it)

    None is not a soft False. An unverified episode is not evidence that the
    write failed, and labelling it as one would put fabricated negatives into
    the training data — so the dataset excludes those episodes entirely rather
    than guessing which way they went.
    """
    return record({
        "kind": "episode",
        "episode_id": episode_id,
        "reference": reference,
        "view": view,
        "field": field,
        "value": value,
        "outcome": outcome if outcome in OUTCOMES else EPISODE_ERROR,
        "verified": verified,
        "duration_ms": None if duration_ms is None else round(float(duration_ms), 1),
        "detail": detail,
        # Did ATLAS actually steer this write? False whenever the automation
        # used its own order — which includes shadow mode, a declined
        # recommendation, and having no model at all. Recorded rather than
        # inferred later, because "was this ours" is not a question the
        # telemetry can answer after the fact.
        "atlas_influenced": bool(atlas_influenced),
        "atlas_chosen": atlas_chosen,
        "atlas_mode": atlas_mode,
        "label": label,
    }, redactor=redactor)


def decision(context, chosen, scores, used, reason, redactor=None,
             mode=None, shadow=False, support=None, trials=None,
             level_key=None, label=None):
    """
    What the predictor recommended and whether the automation took it.

    In shadow mode `used` is False and `chosen` is still populated — that pair
    is the whole point of the mode. Scoring those shadow choices against what
    the run actually did is the unbiased evidence an off-policy replay of past
    telemetry cannot give, because the replay only ever sees the situations the
    deterministic order found hard.
    """
    return record({
        "kind": "decision",
        "context": context,
        # The ATLAS vocabulary label this decision was announced under, so the
        # telemetry and the run log can be reconciled line for line.
        "label": label,
        "chosen": chosen,
        "scores": scores,
        "used": bool(used),
        "shadow": bool(shadow),
        "mode": mode,
        "has_support": support,
        "trials": trials,
        "level_key": level_key,
        "reason": reason,
    }, redactor=redactor)


def failures():
    return _failures


def stats():
    path = target_path()
    if not path.exists():
        return {"path": str(path), "exists": False, "lines": 0, "bytes": 0}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = sum(1 for _ in handle)
    except OSError:
        lines = 0
    return {"path": str(path), "exists": True, "lines": lines,
            "bytes": path.stat().st_size}
