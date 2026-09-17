"""
Structured feedback on the assistant's answers.

Append-only JSONL, beside the telemetry the automation writes. It is training
and evaluation MATERIAL, not training: nothing here ever touches a production
model. Turning it into a model is a separate, deliberate act, and it goes
through the same evaluation gate as everything else — a thumbs-down is a
signal, not an instruction.

Kept apart from ml/data/telemetry.jsonl on purpose. That file records what the
automation observed on a page; this one records what a person thought of an
answer. Mixing them would let opinions become observations.
"""

import json
import os
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE.parent / "ml" / "data" / "assistant_feedback.jsonl"
MAX_BYTES = 8 * 1024 * 1024

# The same deny-list shape the telemetry writer uses. Feedback is free text
# typed by a person, so it gets the same treatment.
_SECRETS = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|bearer)\s*[:=]\s*\S+", re.I)


def path():
    return Path(os.environ.get("ASSISTANT_FEEDBACK_PATH") or DEFAULT_PATH)


def _clean(text, limit=2000):
    if text is None:
        return ""
    return _SECRETS.sub(r"\1=[redacted]", str(text))[:limit]


def record(question, answer, verdict, correction="", sources=None,
           confidence=None, intent=None, reference=None):
    """
    Store one piece of feedback. Returns (ok, message).

    `verdict` is "helpful" or "not_helpful"; anything else is rejected rather
    than stored as a shrug, because a field that can hold anything cannot be
    counted later.
    """
    verdict = str(verdict or "").strip().lower()
    if verdict not in ("helpful", "not_helpful"):
        return False, "verdict must be 'helpful' or 'not_helpful'"
    if not str(question or "").strip():
        return False, "there is no question to attach this to"

    row = {
        "kind": "assistant_feedback",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "question": _clean(question, 1000),
        "answer": _clean(answer, 4000),
        "data_sources_used": list(sources or [])[:20],
        "answer_confidence": confidence,
        "intent": _clean(intent, 60),
        "reference": _clean(reference, 64),
        "user_feedback": verdict,
        "correction": _clean(correction, 2000),
    }
    try:
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if target.exists() and target.stat().st_size > MAX_BYTES:
                target.replace(target.with_suffix(".jsonl.1"))
        except OSError:
            pass
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return True, "Thank you — recorded."
    except Exception as error:
        return False, "could not record feedback: {0}".format(error)


def stats():
    """Counts for the dashboard. Never raises."""
    target = path()
    summary = {"path": str(target), "total": 0, "helpful": 0,
               "not_helpful": 0, "corrections": 0}
    if not target.exists():
        return summary
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("kind") != "assistant_feedback":
                    continue
                summary["total"] += 1
                if row.get("user_feedback") == "helpful":
                    summary["helpful"] += 1
                elif row.get("user_feedback") == "not_helpful":
                    summary["not_helpful"] += 1
                if (row.get("correction") or "").strip():
                    summary["corrections"] += 1
    except OSError:
        pass
    return summary
