"""
ATLAS Adaptive State Engine, and the Potato Garden.

SIMULATED. The states here are an operational / personality display — a way
for ATLAS to show, at a glance, how the run is going for it. They are not
feelings, and nothing here claims they are.

OBSERVE ONLY. The engine is fed the events the dashboard bridge already
produces (a recovery attempt failed, a recovery was verified, a shipment was
read back, a learning record was written). It never calls back into the
automation, never retries, pauses, skips or writes anything operational, and
never changes the order or priority of work. Whatever state it shows, the run
does exactly what it would have done without it. A Human Action waiting for a
person always outranks it: the joke is withheld and the status says so.

    IDLE         no active run
    FOCUSED      working normally
    FRUSTRATED   repeated failures (configurable count)
    RECOVERING   the failure threshold was reached, or a recovery ran out of
                 options: the run has stopped retrying that shipment, it is on
                 the work list, and the run carries on (what the automation
                 already does)
    LEARNING     a VERIFIED recovery was actually written to the learning
                 store — never merely because a call returned without error

THE POTATO GARDEN is gamification, not an operational metric. A potato is
planted when ATLAS enters RECOVERING for a real incident (once per incident),
grows a stage each time a shipment is verified (written and read back), and is
harvested when that incident's shipment ends verified.

Every transition records when, from what, to what, the triggering event and
why. Escalations (towards RECOVERING) show at once; calming down waits a
minimum dwell, so the status does not flicker. Duplicate events are ignored.

Configuration (optional): atlas_state_config.json in the ATLAS store folder,
merged over DEFAULTS; out-of-range values are ignored.
"""

import hashlib
import threading
import time

STATES = ("IDLE", "FOCUSED", "FRUSTRATED", "RECOVERING", "LEARNING")
LABELS = {"IDLE": "Idle", "FOCUSED": "Focused", "FRUSTRATED": "Frustrated",
          "RECOVERING": "Recovering", "LEARNING": "Learning"}
# Higher = more urgent. Moving up shows at once; moving down waits the dwell.
SEVERITY = {"IDLE": 0, "FOCUSED": 1, "LEARNING": 1, "FRUSTRATED": 2, "RECOVERING": 3}
STAGES = ("seed", "sprout", "leafy", "flowering")
STATE_FILE = "atlas_state.json"
CONFIG_FILE = "atlas_state_config.json"
SIMULATED_NOTE = "A simulated status for display, not a feeling."
GARDEN_NOTE = "Gamification only, not an operational metric."

DEFAULT_MESSAGES = {
    "IDLE": ["No run in progress."],
    "FOCUSED": ["Working through the run.", "On it.", "Following the shipments one by one."],
    "FRUSTRATED": [
        "This one keeps failing. Still trying the recovery options I have.",
        "Another failure on the same kind of problem. Staying on it.",
    ],
    "LEARNING": ["That recovery worked and was verified. Recorded for next time."],
    # Shown once when RECOVERING starts, rotated so it never repeats twice in
    # a row. Every line must stay true: the run has stopped retrying that
    # shipment, it is on the work list, and the run continues.
    "potato": [
        "يا معلم، الموقع ده رخم 😂 جربت الحلول المتاحة ومفيش نتيجة. هوقف تكرار "
        "المحاولات، وأسجّل المشكلة، وأحدد الخطوة الآمنة التالية. محتاج أزرع بطاطس شوية 🥔",
        "That site is being difficult. I tried the recovery options I have, so I've stopped "
        "retrying this shipment, put it on the work list, and moved to the next one. "
        "Planting a potato while I'm at it 🥔",
        "Out of good options for this one. No more retries on it: it's recorded on the work "
        "list and the run carries on. One more potato for the garden 🥔",
        "الموقع مش راضي النهارده. وقفت المحاولات على الشحنة دي، سجلتها في قائمة الشغل، "
        "وكملت على اللي بعدها. زرعت بطاطساية 🥔",
    ],
    "human": ["A Human Action is waiting, and it comes first."],
}

DEFAULTS = {
    "frustrated_after": 2,     # consecutive failures before FRUSTRATED
    "recovering_after": 3,     # consecutive failures before RECOVERING
    "min_dwell_s": 15,         # minimum time in a state before calming down
    "max_transitions": 30,     # kept in history
    "max_plants": 40,          # kept in the garden
    "dedupe_keys": 600,        # recent event keys remembered
    "messages": DEFAULT_MESSAGES,
}


def merged_config(override=None):
    """DEFAULTS with valid overrides applied; anything out of range is ignored."""
    cfg = dict(DEFAULTS)
    cfg["messages"] = {k: list(v) for k, v in DEFAULT_MESSAGES.items()}
    override = override if isinstance(override, dict) else {}

    def number(key, low, high):
        value = override.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if low <= value <= high:
            cfg[key] = int(value)

    number("frustrated_after", 1, 50)
    number("recovering_after", 1, 100)
    number("min_dwell_s", 0, 600)
    number("max_transitions", 5, 200)
    number("max_plants", 5, 500)
    number("dedupe_keys", 50, 5000)
    if cfg["recovering_after"] < cfg["frustrated_after"]:
        cfg["recovering_after"] = cfg["frustrated_after"]
    messages = override.get("messages")
    if isinstance(messages, dict):
        for key, lines in messages.items():
            if key in cfg["messages"] and isinstance(lines, list):
                lines = [str(x)[:400] for x in lines if isinstance(x, str) and x.strip()]
                if lines:
                    cfg["messages"][key] = lines
    return cfg


def plant_id(incident):
    """Stable for the same incident: planting it twice finds the same plant."""
    return "P-" + hashlib.sha1(incident.encode("utf-8")).hexdigest()[:10]


class StateEngine(object):
    """
    Deterministic, configurable, observe-only. `load` and `save` are optional
    persistence callables (the bridge passes the ATLAS store's when the
    automation attaches it); without them the garden lives in memory.
    """

    def __init__(self, config=None, load=None, save=None, clock=None):
        self.cfg = merged_config(config)
        self._save = save
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self.state = "IDLE"
        self.since = self._clock()
        self.reason = "No run in progress."
        self.trigger = None
        self.reference = None
        self.streak = 0
        self.run_id = None
        self.incident = None          # the incident RECOVERING is about
        self.pending = None           # a calmer state waiting out the dwell
        self.human_waiting = False
        self.message = None
        self.transitions = []
        self.garden = {"plants": [], "harvests": 0, "planted": 0}
        self.latest_verified = None
        self.rotation = {}
        self._seen = []
        self._seen_set = set()
        data = None
        if load:
            try:
                data = load()
            except Exception:
                data = None
        if isinstance(data, dict):
            self._restore(data)

    # -- persistence --------------------------------------------------------

    def _restore(self, data):
        garden = data.get("garden") or {}
        plants = [p for p in (garden.get("plants") or []) if isinstance(p, dict) and p.get("id")]
        self.garden = {"plants": plants[-self.cfg["max_plants"]:],
                       "harvests": int(garden.get("harvests") or 0),
                       "planted": int(garden.get("planted") or len(plants))}
        self.transitions = [t for t in (data.get("transitions") or [])
                            if isinstance(t, dict)][-self.cfg["max_transitions"]:]
        if isinstance(data.get("latest_verified"), dict):
            self.latest_verified = data["latest_verified"]
        if isinstance(data.get("rotation"), dict):
            self.rotation = {k: int(v) for k, v in data["rotation"].items()
                             if isinstance(v, int)}

    def _persist(self):
        if not self._save:
            return
        try:
            self._save({"garden": self.garden, "transitions": self.transitions,
                        "latest_verified": self.latest_verified, "rotation": self.rotation,
                        "note": GARDEN_NOTE})
        except Exception:
            pass

    # -- helpers ------------------------------------------------------------

    def _stamp(self, epoch):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))

    def _duplicate(self, key):
        if key in self._seen_set:
            return True
        self._seen.append(key)
        self._seen_set.add(key)
        if len(self._seen) > self.cfg["dedupe_keys"]:
            old = self._seen.pop(0)
            self._seen_set.discard(old)
        return False

    def _pick(self, kind, **values):
        """The next line for `kind`, rotating, never the same twice in a row."""
        lines = self.cfg["messages"].get(kind) or [""]
        index = self.rotation.get(kind, -1) + 1
        if index >= len(lines):
            index = 0
        self.rotation[kind] = index
        try:
            return lines[index].format(**values)
        except Exception:
            return lines[index]

    def _go(self, target, reason, trigger, reference, now, force=False):
        """
        Move to `target`. Up the severity scale (or into LEARNING) at once;
        down only after the dwell — until then it waits in `pending`.
        """
        if target not in STATES:
            return None
        if target == self.state:
            self.pending = None
            self.reason = reason
            return None
        calmer = (target != "LEARNING" and SEVERITY[target] < SEVERITY[self.state]) or (
            target in ("FOCUSED", "IDLE") and self.state == "LEARNING")
        if calmer and not force and now - self.since < self.cfg["min_dwell_s"]:
            self.pending = {"to": target, "reason": reason, "trigger": trigger,
                            "reference": reference}
            return None
        entry = {"from": self.state, "to": target, "at": self._stamp(now),
                 "epoch": round(now, 1), "reason": reason, "trigger": trigger,
                 "reference": reference}
        self.state, self.since, self.reason = target, now, reason
        self.trigger, self.reference, self.pending = trigger, reference, None
        if target == "RECOVERING":
            self.message = None if self.human_waiting else self._pick(
                "potato", reference=reference or "")
        elif target in ("FRUSTRATED", "LEARNING", "FOCUSED", "IDLE"):
            self.message = self._pick(target, reference=reference or "")
        self.transitions.append(entry)
        self.transitions = self.transitions[-self.cfg["max_transitions"]:]
        self._persist()
        return entry

    def _evaluate(self, trigger, reference, now, why):
        if self.streak >= self.cfg["recovering_after"]:
            return self._go("RECOVERING", "{0} failures in a row ({1}). The run stops retrying "
                            "this one, records it, and moves on.".format(self.streak, why),
                            trigger, reference, now)
        if self.streak >= self.cfg["frustrated_after"]:
            return self._go("FRUSTRATED", "{0} failures in a row ({1}).".format(self.streak, why),
                            trigger, reference, now)
        return None

    # -- the garden ---------------------------------------------------------

    def _plant(self, incident, reference, error, now):
        pid = plant_id(incident)
        for plant in self.garden["plants"]:
            if plant["id"] == pid:
                return None                    # already planted for this incident
        plant = {"id": pid, "incident": incident, "reference": reference,
                 "problem": error, "run_id": self.run_id,
                 "planted_at": self._stamp(now), "planted_epoch": round(now, 1),
                 "stage": 0, "stage_name": STAGES[0], "waterings": 0,
                 "harvested_at": None}
        self.garden["plants"].append(plant)
        self.garden["plants"] = self.garden["plants"][-self.cfg["max_plants"]:]
        self.garden["planted"] += 1
        return plant

    def _water(self):
        """A verified shipment: the newest growing plant moves up one stage."""
        for plant in reversed(self.garden["plants"]):
            if plant.get("harvested_at"):
                continue
            plant["waterings"] = plant.get("waterings", 0) + 1
            if plant["stage"] < len(STAGES) - 1:
                plant["stage"] += 1
                plant["stage_name"] = STAGES[plant["stage"]]
            return plant
        return None

    def _harvest(self, reference, now):
        """The incident's shipment ended verified: its plant is harvested."""
        harvested = None
        for plant in self.garden["plants"]:
            if plant.get("harvested_at") or plant.get("reference") != reference \
                    or plant.get("run_id") != self.run_id:
                continue
            plant["harvested_at"] = self._stamp(now)
            self.garden["harvests"] += 1
            harvested = plant
        return harvested

    # -- events -------------------------------------------------------------

    def observe(self, event, key=None, **f):
        """
        One event from the bridge. Returns the transition it caused, or None.
        Never raises: a display feature must not be able to break a run.
        """
        try:
            with self._lock:
                return self._observe(event, key, f)
        except Exception:
            return None

    def _observe(self, event, key, f):
        now = self._clock()
        if key is None:
            key = "|".join([event] + ["{0}={1}".format(k, f[k]) for k in sorted(f)])
        if self._duplicate(key):
            return None
        ref = f.get("reference")
        if event == "run_started":
            self.run_id = f.get("run_id")
            self.streak, self.incident = 0, None
            return self._go("FOCUSED", "Run {0} started.".format(self.run_id or ""),
                            event, None, now, force=True)
        if event == "run_finished":
            return self._go("IDLE", "The run finished ({0}).".format(f.get("status") or "done"),
                            event, None, now)
        if event == "shipment_started":
            if self.streak >= self.cfg["frustrated_after"]:
                return None        # still a failure streak: stay where we are
            return self._go("FOCUSED", "Working on {0}.".format(ref or "the next shipment"),
                            event, ref, now)
        if event == "attempt_failed":
            self.streak += 1
            change = self._evaluate(event, ref, now, "recovery attempt {0} on {1} did not "
                                    "verify".format(f.get("action") or "", ref or "a shipment"))
            if change and change["to"] == "RECOVERING":
                self._enter_recovery(ref, f.get("error_class"), now)
            return change
        if event == "recovery_exhausted":
            self.streak = max(self.streak + 1, self.cfg["recovering_after"])
            change = self._go("RECOVERING", "Recovery for {0} ran out of options ({1}). The run "
                              "stops retrying it, records it on the work list, and moves "
                              "on.".format(ref or "a shipment", f.get("error_class") or "error"),
                              event, ref, now)
            self._enter_recovery(ref, f.get("error_class"), now)
            return change
        if event == "recovery_completed":
            if f.get("verified") is not True:
                # Returned without error is not success. Nothing is counted.
                self.reason = ("A recovery on {0} reported done but was not verified; not "
                               "counted.".format(ref or "a shipment"))
                return None
            self.streak = 0
            return self._go("FOCUSED", "Recovery on {0} verified ({1}).".format(
                ref or "a shipment", f.get("action") or "recovery"), event, ref, now)
        if event == "shipment_finished":
            result = str(f.get("result") or "").upper()
            if result == "SUCCESS" and f.get("verified") is True:
                self.streak = 0
                self._water()
                plant = self._harvest(ref, now)
                self._persist()
                why = "{0} written and read back.".format(ref or "A shipment")
                if plant:
                    why += " Its potato was harvested 🥔"
                return self._go("FOCUSED", why, event, ref, now)
            if result in ("FAILED", "HUMAN_TIMEOUT"):
                self.streak += 1
                change = self._evaluate(event, ref, now, "{0} ended {1}".format(
                    ref or "a shipment", result.lower()))
                if change and change["to"] == "RECOVERING":
                    self._enter_recovery(ref, f.get("error_class") or result, now)
                return change
            return None            # SKIPPED, PARTIAL, queued, unverified: not counted
        if event == "learning_recorded":
            # Only a verified recovery that was actually written to the store.
            if f.get("kind") != "recovery" or f.get("verified") is not True \
                    or str(f.get("status") or "").upper() != "RECOVERED":
                return None
            self.latest_verified = {"reference": ref, "problem": f.get("error_class"),
                                    "solution": f.get("action"), "run_id": self.run_id,
                                    "at": self._stamp(now), "verified": True,
                                    "evidence": "learning store: recovery record, shipment "
                                                "read back"}
            self._persist()
            return self._go("LEARNING", "Recorded a verified recovery for {0}.".format(
                ref or "a shipment"), event, ref, now)
        if event == "human_waiting":
            self.human_waiting = True
            if self.state == "RECOVERING":
                self.message = None
            return None
        if event == "human_done":
            self.human_waiting = False
            return None
        return None

    def _enter_recovery(self, reference, error, now):
        # One incident per shipment per run, whatever stage reported it.
        incident = "{0}|{1}".format(self.run_id, reference)
        self.incident = incident
        if self._plant(incident, reference, error, now):
            self._persist()

    def set_human(self, waiting):
        """A Human Action is (or is no longer) waiting. It outranks every state."""
        try:
            with self._lock:
                self.human_waiting = bool(waiting)
                if self.human_waiting and self.state == "RECOVERING":
                    self.message = None
        except Exception:
            pass

    def tick(self):
        """Apply a calmer state that has waited out its dwell. Never raises."""
        try:
            with self._lock:
                if self.pending and self._clock() - self.since >= self.cfg["min_dwell_s"]:
                    p = self.pending
                    return self._go(p["to"], p["reason"], p["trigger"], p["reference"],
                                    self._clock())
        except Exception:
            pass
        return None

    # -- what the dashboard and the chat read -----------------------------------

    def snapshot(self):
        self.tick()
        with self._lock:
            plants = self.garden["plants"]
            growing = [p for p in plants if not p.get("harvested_at")]
            message = self.message
            priority = None
            if self.human_waiting:
                priority = "human_action"
                message = self.cfg["messages"]["human"][0]
            return {
                "state": self.state, "label": LABELS[self.state],
                "since": self._stamp(self.since), "reason": self.reason,
                "trigger": self.trigger, "reference": self.reference,
                "message": message, "priority": priority,
                "streak": self.streak,
                "thresholds": {"frustrated_after": self.cfg["frustrated_after"],
                               "recovering_after": self.cfg["recovering_after"],
                               "min_dwell_s": self.cfg["min_dwell_s"]},
                "pending": dict(self.pending) if self.pending else None,
                "transitions": [dict(t) for t in reversed(self.transitions[-10:])],
                "latest_verified_recovery": dict(self.latest_verified)
                if self.latest_verified else None,
                "garden": {
                    "note": GARDEN_NOTE,
                    "harvests": self.garden["harvests"],
                    "planted": self.garden["planted"],
                    "growing": len(growing),
                    "current": dict(growing[-1]) if growing else None,
                    "plants": [dict(p) for p in plants[-8:]],
                },
                "note": SIMULATED_NOTE,
            }


# -- solution records (section 5): the existing recovery events, projected -----

def solution_records(rows):
    """
    Attempted solutions, from the `recovery` events the bridge already writes
    to events.jsonl: problem, attempted solution, outcome, evidence, whether
    it was verified, and when. Nothing new is stored.
    """
    out = []
    for row in rows or []:
        if row.get("kind") != "recovery":
            continue
        attempts = row.get("attempts") or []
        verified = (str(row.get("status") or "").upper() == "RECOVERED"
                    and row.get("recovery_verified") is True
                    and row.get("shipment_verified") is True)
        out.append({
            "problem": row.get("error_class"),
            "solution": ", ".join(str(a.get("action")) for a in attempts if a.get("action"))
            or None,
            "outcome": row.get("status"),
            "evidence": {"run_id": row.get("run_id"), "reference": row.get("reference"),
                         "shipment_result": row.get("shipment_result")},
            "verified": verified,
            "at": row.get("at"),
        })
    return out


def reusable_solutions(rows):
    """Only verified successes. Reuse still goes through the existing gates."""
    return [r for r in solution_records(rows) if r["verified"]]
