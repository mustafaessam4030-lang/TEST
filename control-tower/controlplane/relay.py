"""
Human Action: who holds a task, and the live browser view between the
worker's Edge tab and that one person's browser.

Claims live in the database, so every request — from any tab, any device,
any app instance sharing the database — sees the same holder. A claim is a
lease: it lasts ATA_SESSION_LEASE_S past the holder's last sign of life
(every frame request renews it) and nobody else can take it until then.

The view itself — screen frames out, mouse and keyboard in — is held in this
process's memory and nowhere else. It is never logged, never written to disk
or the database, never shown to ATLAS, and is dropped when the claim ends.
Keystrokes pass through as the person typed them, to the worker that owns
the tab; this module does not look at them beyond checking their shape.
"""

import threading
import time

from .db import now

INPUT_TYPES = ("mousePressed", "mouseReleased", "mouseMoved", "mouseWheel",
               "keyDown", "keyUp", "char")
MAX_EVENTS = 400            # queued per action; older input is dropped
MAX_FRAME = 1500 * 1024     # one JPEG frame
SPECIAL_KEYS = ("Backspace", "Tab", "Enter", "Escape", "Delete", "ArrowLeft",
                "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp",
                "PageDown", "Shift", "Control", "Alt", "Meta", " ")


class ClaimError(Exception):
    def __init__(self, message, status=409, holder=None):
        Exception.__init__(self, message)
        self.status, self.holder = status, holder


def clean_events(events):
    """Only well-formed pointer and key events, bounded. Raises ValueError."""
    if not isinstance(events, list) or len(events) > 120:
        raise ValueError("events must be a list of at most 120")
    out = []
    for e in events:
        if not isinstance(e, dict) or e.get("type") not in INPUT_TYPES:
            raise ValueError("unknown event")
        item = {"type": e["type"]}
        if e["type"].startswith("mouse"):
            for k in ("x", "y"):
                v = float(e.get(k, 0))
                if not 0 <= v <= 10000:
                    raise ValueError("pointer outside the page")
                item[k] = round(v, 1)
            item["button"] = e.get("button") if e.get("button") in (
                "left", "right", "middle", "none") else "left"
            item["clickCount"] = max(0, min(3, int(e.get("clickCount") or 0)))
            if e["type"] == "mouseWheel":
                item["deltaX"] = max(-3000, min(3000, float(e.get("deltaX") or 0)))
                item["deltaY"] = max(-3000, min(3000, float(e.get("deltaY") or 0)))
        else:
            key = str(e.get("key") or "")
            text = str(e.get("text") or "")
            if len(key) > 24 or len(text) > 4:
                raise ValueError("key too long")
            item["key"], item["text"] = key, text
            item["code"] = str(e.get("code") or "")[:24]
            item["modifiers"] = max(0, min(15, int(e.get("modifiers") or 0)))
        out.append(item)
    return out


class Relay(object):
    def __init__(self, db, audit, settings):
        self.db, self.audit, self.settings = db, audit, settings
        self._cond = threading.Condition()
        self._frames = {}       # action_id -> {"seq", "data", "meta", "at"}
        self._input = {}        # action_id -> list of events
        self._status = {}       # action_id -> what the worker says about the view
        self._viewer = {}       # action_id -> last time the holder's tab asked

    # -- claims --------------------------------------------------------------

    def holder(self, action_id):
        row = self.db.one("SELECT * FROM claims WHERE action_id = ?", (action_id,))
        if row and not row["released_at"] and row["lease_until"] > now():
            return row
        return None

    def claim(self, user, run_id, action_id):
        """The task for this person, or ClaimError naming who has it."""
        try:
            return self._claim(user, run_id, action_id)
        except ClaimError:
            raise
        except Exception:
            # Two first claims raced to insert the same row; one lost.
            holder = self.holder(action_id)
            if holder and holder["user_id"] != user["user_id"]:
                raise ClaimError("{0} is handling this Human Action. It becomes free "
                                 "when they finish or leave it.".format(
                                     holder["user_email"]), 409, holder["user_email"])
            if holder:
                return holder
            raise

    def _claim(self, user, run_id, action_id):
        stamp = now()
        lease = stamp + self.settings.session_lease_s
        with self.db.tx() as c:
            row = self.db.one("SELECT * FROM claims WHERE action_id = ?" +
                              self.db.lock_clause, (action_id,), c)
            if row and not row["released_at"] and row["lease_until"] > stamp and \
                    row["user_id"] != user["user_id"]:
                raise ClaimError("{0} is handling this Human Action. It becomes free "
                                 "when they finish or leave it.".format(
                                     row["user_email"]), 409, row["user_email"])
            if row is None:
                self.db.execute("INSERT INTO claims (action_id, run_id, user_id, "
                                "user_email, claimed_at, lease_until) VALUES "
                                "(?, ?, ?, ?, ?, ?)",
                                (action_id, run_id, user["user_id"], user["work_email"],
                                 stamp, lease), c)
            else:
                self.db.execute("UPDATE claims SET run_id = ?, user_id = ?, user_email = ?, "
                                "claimed_at = ?, lease_until = ?, released_at = NULL "
                                "WHERE action_id = ?",
                                (run_id, user["user_id"], user["work_email"],
                                 stamp if row["user_id"] != user["user_id"] or
                                 row["released_at"] else row["claimed_at"], lease,
                                 action_id), c)
        return self.holder(action_id)

    def renew(self, user, action_id):
        changed = self.db.execute(
            "UPDATE claims SET lease_until = ? WHERE action_id = ? AND user_id = ? "
            "AND released_at IS NULL AND lease_until > ?",
            (now() + self.settings.session_lease_s, action_id, user["user_id"], now()))
        if changed:
            self._viewer[action_id] = time.time()
        return bool(changed)

    def release(self, user, action_id):
        changed = self.db.execute(
            "UPDATE claims SET released_at = ? WHERE action_id = ? AND user_id = ? "
            "AND released_at IS NULL", (now(), action_id, user["user_id"]))
        self.drop(action_id)
        return bool(changed)

    def is_holder(self, user, action_id):
        row = self.holder(action_id)
        return bool(row and row["user_id"] == user["user_id"])

    # -- the view (memory only) ----------------------------------------------

    def put_frame(self, action_id, data, meta):
        if not data or len(data) > MAX_FRAME or data[:2] != b"\xff\xd8":
            return False          # JPEG only
        with self._cond:
            seq = (self._frames.get(action_id) or {}).get("seq", 0) + 1
            self._frames[action_id] = {"seq": seq, "data": data, "at": time.time(),
                                       "meta": {k: meta.get(k) for k in (
                                           "width", "height", "scale", "offset_top")
                                           if isinstance(meta.get(k), (int, float))}}
            self._cond.notify_all()
        return True

    def frame(self, action_id, after=0, wait_s=10):
        deadline = time.time() + max(0, min(wait_s, 20))
        with self._cond:
            while True:
                frame = self._frames.get(action_id)
                if frame and frame["seq"] > after:
                    return frame
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._cond.wait(remaining)

    def push_input(self, action_id, events):
        with self._cond:
            queue = self._input.setdefault(action_id, [])
            queue.extend(events)
            del queue[:-MAX_EVENTS]
            self._cond.notify_all()

    def take_input(self, action_id, wait_s=10):
        deadline = time.time() + max(0, min(wait_s, 20))
        with self._cond:
            while True:
                queue = self._input.get(action_id)
                if queue:
                    self._input[action_id] = []
                    return queue
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self._cond.wait(remaining)

    def set_status(self, action_id, status):
        allowed = {k: status.get(k) for k in ("attached", "streaming", "reason",
                                              "carrier", "reference", "page_state")}
        with self._cond:
            self._status[action_id] = dict(allowed, at=time.time())
            self._cond.notify_all()

    def status(self, action_id):
        return self._status.get(action_id)

    def viewer_active(self, action_id):
        """Is the holder's tab still asking for frames? For the worker."""
        seen = self._viewer.get(action_id)
        return bool(seen and time.time() - seen < self.settings.session_lease_s)

    def drop(self, action_id):
        with self._cond:
            self._frames.pop(action_id, None)
            self._input.pop(action_id, None)
            self._status.pop(action_id, None)
            self._viewer.pop(action_id, None)
            self._cond.notify_all()
