"""
Potato Mode — a small Easter egg in ATLAS's personality. 🥔

PLAYFUL, NOT A FEELING. When the same incident (one shipment in one run) keeps
failing, ATLAS shows ONE short joke about planting potatoes. When that
incident is later resolved and verified (the shipment written to the Hub and
read back), a potato is harvested: a fun counter on the dashboard. That is
all. Nothing here claims ATLAS feels anything.

OBSERVE ONLY. It is fed events the dashboard bridge already produces. It
never calls back into the automation: recovery, root-cause analysis,
retries, learning and every safety check run exactly as they would without
it. While a Human Action is waiting, the joke is not shown.

Configuration (optional): potato_config.json in the ATLAS store folder,
    {"threshold": 3, "messages": ["...", "..."]}
Out-of-range values are ignored.
"""

import threading
import time

STATE_FILE = "potato.json"
CONFIG_FILE = "potato_config.json"
NOTE = "Just for fun: not an operational metric."

DEFAULT_MESSAGES = [
    "يا معلم، إحنا كده دخلنا موسم زراعة البطاطس 🥔 خليني أراجع سبب الغلط بدل ما نفضل "
    "نلف في نفس الدايرة.",
    "الـ pipeline وقعت تاني؟ تمام، البطاطس على حسابي… بس الأول هنفهم المشكلة 😂🥔",
    "عدد الأخطاء بدأ يقلقني يا معلم. هراجع المحاولات السابقة وأختار حل مختلف بدل تكرار "
    "نفس الغلطة.",
]
DEFAULTS = {"threshold": 3, "messages": DEFAULT_MESSAGES}


def merged_config(override=None):
    cfg = {"threshold": DEFAULTS["threshold"], "messages": list(DEFAULT_MESSAGES)}
    override = override if isinstance(override, dict) else {}
    value = override.get("threshold")
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 50:
        cfg["threshold"] = value
    lines = override.get("messages")
    if isinstance(lines, list):
        lines = [str(x)[:300] for x in lines if isinstance(x, str) and x.strip()]
        if lines:
            cfg["messages"] = lines
    return cfg


class PotatoMode(object):
    """Counts errors per incident; one joke at the threshold; a harvest on verification."""

    def __init__(self, config=None, load=None, save=None, clock=None):
        self.cfg = merged_config(config)
        self._save = save
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self.run_id = None
        self.errors = {}            # incident -> error count (this process)
        self.active = None          # the incident Potato Mode is showing for
        self.message = None
        self.context = None
        self.human_waiting = False
        self.harvests = 0
        self.planted = 0
        self.last_harvest = None
        self.incidents = []         # potato incidents (bounded), for duplicate checks
        self.rotation = -1
        self._seen = set()
        try:
            data = load() if load else None
        except Exception:
            data = None
        if isinstance(data, dict):
            self.harvests = int(data.get("harvests") or 0)
            self.planted = int(data.get("planted") or 0)
            self.last_harvest = data.get("last_harvest")
            self.rotation = int(data.get("rotation") or -1)
            self.incidents = [i for i in (data.get("incidents") or []) if isinstance(i, dict)][-50:]

    def _persist(self):
        if not self._save:
            return
        try:
            self._save({"harvests": self.harvests, "planted": self.planted,
                        "last_harvest": self.last_harvest, "rotation": self.rotation,
                        "incidents": self.incidents[-50:], "note": NOTE})
        except Exception:
            pass

    def _stamp(self):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._clock()))

    def _incident(self, reference):
        return "{0}|{1}".format(self.run_id, reference)

    def _known(self, incident):
        for item in self.incidents:
            if item.get("incident") == incident:
                return item
        return None

    def observe(self, event, key=None, **f):
        """One bridge event. Returns True when Potato Mode or a harvest happened. Never raises."""
        try:
            with self._lock:
                if key is not None:
                    if key in self._seen:
                        return False           # the same event twice counts once
                    self._seen.add(key)
                    if len(self._seen) > 2000:
                        self._seen.clear()
                return self._observe(event, f)
        except Exception:
            return False

    def _observe(self, event, f):
        ref = f.get("reference")
        if event == "run_started":
            self.run_id = f.get("run_id")
            self.errors, self.active, self.message, self.context = {}, None, None, None
            return False
        if event == "error" and ref:
            incident = self._incident(ref)
            count = self.errors.get(incident, 0) + 1
            self.errors[incident] = count
            if count < self.cfg["threshold"] or self._known(incident):
                return False                   # below threshold, or already joked
            self.rotation = (self.rotation + 1) % len(self.cfg["messages"])
            self.active, self.message = incident, self.cfg["messages"][self.rotation]
            self.context = {"reference": ref, "errors": count,
                            "problem": f.get("problem"), "at": self._stamp()}
            self.incidents.append({"incident": incident, "reference": ref,
                                   "problem": f.get("problem"), "planted_at": self._stamp(),
                                   "harvested_at": None})
            self.incidents = self.incidents[-50:]
            self.planted += 1
            self._persist()
            return True
        if event == "verified" and ref:
            item = self._known(self._incident(ref))
            if not item or item.get("harvested_at"):
                return False                   # no potato for it, or already harvested
            item["harvested_at"] = self._stamp()
            self.harvests += 1
            self.last_harvest = {"reference": ref, "problem": item.get("problem"),
                                 "at": item["harvested_at"]}
            if self.active == item["incident"]:
                self.active, self.message, self.context = None, None, None
            self._persist()
            return True
        return False

    def set_human(self, waiting):
        """A Human Action is waiting: no jokes until it is done."""
        self.human_waiting = bool(waiting)

    def snapshot(self):
        with self._lock:
            show = bool(self.active and not self.human_waiting)
            return {"active": show,
                    "message": self.message if show else None,
                    "context": dict(self.context) if show and self.context else None,
                    "harvests": self.harvests, "planted": self.planted,
                    "last_harvest": dict(self.last_harvest) if self.last_harvest else None,
                    "threshold": self.cfg["threshold"], "note": NOTE}
