"""
ATLAS conversations, per signed-in person, for Admins to review.

Kept only with personal sign-in, and only after the chat window has told
people their conversations are saved. Each question and ATLAS's answer is one
line of JSONL in dashboard/.runtime/ (never in Git or the release), kept for
ATA_CONVERSATION_DAYS days (default 90; 0 stops recording). Secrets typed into
a question are masked before anything is written, and a question about a
carrier verification code is not kept at all.
"""
import json
import os
import re
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_LOCK = threading.Lock()
MAX_QUESTION = 1000
MAX_ANSWER = 4000
_SECRETS = re.compile(
    r"(password|passwd|pwd|secret|token|api[_-]?key|bearer|otp|passcode)(?:\s*[:=]\s*|\s+is\s+)\S+", re.I)
_last_prune = [0.0]


def path():
    runtime = Path(os.environ.get("ATA_RUNTIME_DIR") or HERE / ".runtime")
    return Path(os.environ.get("ATA_CONVERSATIONS_FILE") or runtime / "atlas_conversations.jsonl")


def days():
    try:
        return max(0, int(os.environ.get("ATA_CONVERSATION_DAYS", "90")))
    except ValueError:
        return 90


def enabled():
    return days() > 0


def _mask(text, limit):
    return _SECRETS.sub(lambda m: m.group(1) + ": [hidden]", str(text or ""))[:limit]


def record(user, question, reply):
    """One question and its answer, for this person. Never raises."""
    if not enabled() or not user:
        return
    try:
        intent = str((reply or {}).get("intent") or "")
        kept = "[a question about a verification code - not kept]" \
            if intent == "code_request" else _mask(question, MAX_QUESTION)
        line = {"ts": int(time.time()), "user_id": user.get("user_id"),
                "email": user.get("work_email") or user.get("email"), "name": user.get("display_name"),
                "role": user.get("role"), "question": kept,
                "answer": _mask((reply or {}).get("answer"), MAX_ANSWER), "intent": intent or None}
        target = path()
        with _LOCK:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as out:
                out.write(json.dumps(line, ensure_ascii=False) + "\n")
            if time.time() - _last_prune[0] > 3600:
                _last_prune[0] = time.time()
                _prune(target)
    except Exception:
        pass


def _prune(target):
    cutoff = time.time() - days() * 86400
    keep = []
    with open(target, encoding="utf-8") as src:
        for raw in src:
            try:
                if json.loads(raw).get("ts", 0) >= cutoff:
                    keep.append(raw)
            except ValueError:
                continue
    tmp = target.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as out:
        out.writelines(keep)
    os.replace(tmp, target)


def list_(email=None, limit=200):
    """Newest first; optionally one person's. Older than the retention: left out."""
    target = path()
    if not target.exists():
        return []
    cutoff = time.time() - days() * 86400
    want = (email or "").strip().lower() or None
    rows = []
    with _LOCK, open(target, encoding="utf-8") as src:
        for raw in src:
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            if row.get("ts", 0) < cutoff:
                continue
            if want and str(row.get("email") or "").lower() != want:
                continue
            rows.append(row)
    rows.reverse()
    return rows[:max(1, min(int(limit), 1000))]
