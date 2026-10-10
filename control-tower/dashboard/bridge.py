"""
Control Tower bridge.

The automation writes its real state here. The dashboard server reads it.
Nothing in this module may ever raise into the automation: every public method
is wrapped so a dashboard problem can never stop a shipment run.
"""

import re
import threading
from collections import deque
from datetime import datetime

MAX_LOG_LINES = 800
MAX_SHIPMENTS = 500
MAX_EXCEPTIONS = 200
MAX_TIMELINE = 300
MAX_ATLAS_EVENTS = 200
MAX_HUMAN_EVENTS = 60
MAX_INTEL_LOG = 200
INTEL_EVENTS = ("failure_detected", "failure_classified", "diagnosis_created",
                "recovery_plan_created", "recovery_started", "recovery_completed",
                "recovery_failed", "verification_started", "verification_passed",
                "verification_failed", "learning_recorded",
                # ATLAS's work list: every failure is put on it, worked
                # without stopping the run, and resolved only by verification.
                "work_item_recorded", "deferred_retry_started", "work_item_resolved")
# Human Action queue states that are finished (human_queue.TERMINAL).
_HQ_TERMINAL = frozenset(("SUCCESS", "TIMEOUT", "HUMAN_SESSION_LOST",
                          "VERIFICATION_NOT_CONFIRMED", "FAILED"))
FAILURE_KEYS = ("category", "stage", "operation", "last_success", "detail")

# Transport mode, for display: how the shipment MOVES, which is not the same
# as who tracks it. DHL tracks the K-references, but an airline flies them —
# the Hub lists K223259 under Brussels Airlines and K179801 under KLM. So the
# carrier the Hub names is read first; the tracking provider decides only when
# the carrier name says nothing about the mode. Never guessed from a
# reference. One function, here, so the dashboard and ATLAS read the same
# answer. Nothing recognised is "unknown", and the dashboard then shows a
# neutral illustration rather than a vehicle.
TRANSPORT_MODES = {
    "AFKL": "air", "QATAR": "air", "ASTRAL": "air",
    # DHL Express moves international shipments through its air network;
    # every DHL row the Hub clears is an import. DHL's road freight is named
    # as such in the carrier column, and ROAD_NAMES catches it first.
    "DHL": "air",
    "CMA_CGM": "ocean", "MSC": "ocean", "GRIMALDI": "ocean", "COSCO": "ocean",
    "MAERSK": "ocean", "ONE": "ocean", "HAPAG": "ocean",
}
# What a carrier NAME says about the mode. Rail and road are checked before
# air, so "DHL Freight" is road even though DHL's default is air.
AIR_NAMES = re.compile(r"\bair(?:lines?|ways)?\b|\baviation\b|\bsky\s*cargo\b|\bKLM\b"
                       r"|\bLufthansa\b|\bEgyptAir\b|\bSaudia\b|\bEmirates\b|\bRwandAir\b"
                       r"|\bAstral\b", re.I)
RAIL_NAMES = re.compile(r"\brail(?:way)?s?\b|\bRZD\b|\bDB\s+Cargo\b|\bEgyptian\s+National\s+Railways\b", re.I)
ROAD_NAMES = re.compile(r"\broad\b|\btruck(?:ing)?s?\b|\bhaulage\b|\bDHL\s+Freight\b|\bland\s+freight\b", re.I)


def transport_mode(provider, carrier=None):
    """air | ocean | road | rail | unknown — how the shipment moves."""
    name = str(carrier or "")
    known = TRANSPORT_MODES.get(str(provider or "").upper())
    if name:
        if RAIL_NAMES.search(name):
            return "rail"
        if ROAD_NAMES.search(name):
            return "road"
        if AIR_NAMES.search(name):
            return "air"
    if known:
        return known
    return "unknown"


# Shipment states. WAITING_FOR_HUMAN is not an outcome: the shipment is
# still open, its lookup paused inside the run until a person acts. It
# leaves only for PROCESSING (resumed) or HUMAN_TIMEOUT / FAILED.
SHIPMENT_STATES = ("processing", "waiting_for_human", "updated", "skipped",
                   "failed", "partial", "human_timeout")

# ATLAS — Adaptive Logistics Strategy Engine. Mirrored from ml/identity.py so
# the dashboard renders correctly with the ml package absent; the test suite
# checks the two agree.
ATLAS_NAME = "ATLAS"
ATLAS_FULL_NAME = "Adaptive Logistics Strategy Engine"

# Labels that assert ATLAS influenced an outcome. An event carrying one of
# these marks its shipment as ATLAS-influenced; everything else does not.
# `Deterministic fallback` is deliberately absent — it is the engine saying it
# stood down, which is the opposite of a claim.
ATLAS_INFLUENCE_LABELS = frozenset((
    "Strategy selected", "Strategy failed", "Fallback activated",
    "Verification passed", "Action completed",
))


def _now():
    return datetime.now()


def _stamp(value=None):
    return (value or _now()).strftime("%H:%M:%S")


def _iso(value):
    return value.isoformat() if value else None


# Every error _guard swallows, by method, with the last message. The guard
# stays — a dashboard fault must never stop the automation — but it no longer
# swallows in silence. shipment_finished raised a TypeError on every skipped,
# failed and partly updated shipment for weeks, and nothing anywhere said so.
GUARDED_ERRORS = {}


def _guard(method):
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception as error:
            entry = GUARDED_ERRORS.setdefault(
                method.__name__, {"count": 0, "last": None})
            entry["count"] += 1
            entry["last"] = "{0}: {1}".format(type(error).__name__,
                                              str(error)[:200])
            return None
    wrapper.__name__ = method.__name__
    wrapper.__wrapped__ = method
    return wrapper


def _clean_failure(failure):
    """A declared failure, reduced to its known fields and bounded strings."""
    if not isinstance(failure, dict):
        return None
    out = {k: str(failure[k])[:240] for k in FAILURE_KEYS if failure.get(k)}
    cause = failure.get("cause")
    if isinstance(cause, dict):
        out["cause"] = {k: str(cause[k])[:240] for k in (
            "kind", "name", "value", "decided_by", "stated_condition") if cause.get(k)}
    observed = failure.get("observed")
    if isinstance(observed, dict):
        out["observed"] = {str(k)[:30]: str(v)[:240] for k, v in list(observed.items())[:8]}
    return out or None


def _verified_outcome(result, verification):
    """The existing pipeline's verdict: SUCCESS and every Hub write read back."""
    try:
        from intelligence import events as _ev
        return _ev.verified_success(result, verification)
    except Exception:
        values = list((verification or {}).values())
        return str(result or "").upper() == "SUCCESS" and bool(values) and \
            all(v is True for v in values)


def _state_engine(persist):
    """
    ATLAS's state engine, with the store's persistence and configuration
    when `persist` (the automation attached the store), else in memory.
    """
    try:
        from intelligence import atlas_state as _as
        if not persist:
            return _as.StateEngine()
        from intelligence import store as _st
        return _as.StateEngine(
            _st.load_json(_as.CONFIG_FILE, {}),
            load=lambda: _st.load_json(_as.STATE_FILE, None),
            save=lambda data: _st.admitted() and _st.save_json(_as.STATE_FILE, data))
    except Exception:
        return None


class ControlTowerState:
    """Single source of truth. All mutations bump `version`."""

    def __init__(self):
        self._lock = threading.RLock()
        self.version = 0
        # MEASURED: the stream re-sent the WHOLE state on every change — 81
        # pushes a minute during a run, 194KB each, 16.1 MB/min for the
        # browser to parse. `shipments` alone is 68% of that and grows without
        # bound (342KB at 400 shipments), and most of those pushes are a log
        # line or a step, which do not touch a shipment at all.
        #
        # This version moves ONLY when the shipment records or the exception
        # list actually change, so the expensive part of the payload is sent
        # when it means something instead of ~80 times a minute.
        self.cold_version = 0
        # The recovery ATLAS is working on right now, or None. Replaced
        # wholesale per error rather than accumulated: this is a live status
        # panel, not a history, and the history is the activity feed.
        self.recovery = None
        # Run-level tally of ATLAS recoveries, for the success rate on the
        # ATLAS page. Counted from the same three calls that drive the panel.
        self.recovery_stats = {"diagnosed": 0, "attempts": 0,
                               "recovered": 0, "exhausted": 0}
        # Finished recovery episodes, newest first, each with the shipment it
        # was for. The live panel above forgets an episode when the shipment
        # moves on; ATLAS's chat still has to be able to say what happened.
        self.recovery_history = deque(maxlen=30)

        self.run_status = "idle"          # idle | running | finished | fatal
        self.run_id = None
        self.started_at = None
        self.finished_at = None
        self.last_heartbeat = None
        self.dry_run = None
        self.target_status = None
        self.max_records = None
        self.max_pages = None
        self.results_file = None
        self.log_file = None

        # ATLAS activity for this run. A feed the panel can read, plus the
        # two counts that answer "how much of this run did the engine
        # actually steer".
        # None until a challenge is seen. The dashboard shows nothing at all
        # rather than a reassuring "no captcha" that was never checked.
        self.human_verification = None
        # The human-in-the-loop action the run has open (or last had open).
        # Non-sensitive context only: run, action, reference, carrier, step,
        # reason, tab, URL without its query string, timestamps.
        self.human_action = None
        self.human_events = deque(maxlen=MAX_HUMAN_EVENTS)
        # The run's Human Action queue: every shipment parked for a person,
        # with its state and history, as human_queue.py keeps it.
        self.human_queue = []
        # ATLAS's event store (intelligence.events), when the automation has
        # attached it. None in tests and demos: nothing is recorded then.
        self.intel = None
        self._intel_human_done = set()
        self._intel_recovery_done = set()
        # ATLAS's operational intelligence, as it happens: failure detected,
        # classified, diagnosed, a recovery planned, started, verified or
        # not, and what was recorded for learning. Ids, classes and short
        # reasons only — never a value a person typed, never a credential.
        self.intel_log = deque(maxlen=MAX_INTEL_LOG)
        # Shipments to retry once, after the others, in this run (failures
        # whose work mode is RETRY_THIS_RUN), and those already retried.
        self.retry_queue = []
        self._retried = set()
        # Where the same lines go in the run log (the automation sets it).
        self.log_hook = None
        # ATLAS's simulated state and the Potato Garden (intelligence/
        # atlas_state.py). Observe-only: fed the events below, it never
        # changes what the run does. In memory until the store is attached.
        self.atlas_state = _state_engine(persist=False)

        self.atlas_events = deque(maxlen=MAX_ATLAS_EVENTS)
        self.atlas_influenced_actions = 0
        self.atlas_fallbacks = 0

        self.current_step = None
        self.current_system = None
        self.current_page = None
        self.current_shipment = None

        self.discovered = 0               # rows queued so far by pagination
        self.pages_scanned = 0
        self.pagination_complete = False
        self.successful = 0
        self.failed = 0
        self.skipped = 0
        self.partial = 0
        self.needs_human = 0

        self.systems = {
            "hub": {
                "key": "hub",
                "name": "Mantrac Logistics Hub",
                "role": "Internal shipment register",
                "state": "idle",
                "activity": None,
                "last_success": None,
                "last_error": None,
                "ops": 0,
                "last_ms": None,
            },
            # Carriers are no longer hardcoded here. The automation registers
            # each one it can actually track at run start (see register_system),
            # so adding a carrier makes it appear on this panel with no change
            # to the dashboard. DHL and Qatar are seeded because they are the
            # two long-standing integrations.
            "DHL": {
                "key": "DHL",
                "name": "DHL Tracking",
                "role": "Carrier event log",
                "state": "idle",
                "activity": None,
                "last_success": None,
                "last_error": None,
                "ops": 0,
                "last_ms": None,
            },
            "QATAR": {
                "key": "QATAR",
                "name": "Qatar Airways Cargo",
                "role": "Carrier AWB tracking",
                "state": "idle",
                "activity": None,
                "last_success": None,
                "last_error": None,
                "ops": 0,
                "last_ms": None,
            },
            "browser": {
                "key": "browser",
                "name": "Microsoft Edge (Playwright)",
                "role": "Automation runtime",
                "state": "idle",
                "activity": None,
                "last_success": None,
                "last_error": None,
                "ops": 0,
                "last_ms": None,
            },
        }

        self.shipments = deque(maxlen=MAX_SHIPMENTS)
        self.logs = deque(maxlen=MAX_LOG_LINES)
        self.exceptions = deque(maxlen=MAX_EXCEPTIONS)
        self.timeline = deque(maxlen=MAX_TIMELINE)

        self._index = {}
        self._step_started = None
        self._system_started = None
        self._log_seq = 0

    # -- internals ---------------------------------------------------------

    def _touch(self):
        self.version += 1
        self.last_heartbeat = _now()

    @_guard
    def recovery_plan(self, error_class, message, order, scores, used,
                      evidence=None, hypotheses=None, why_first=None,
                      verifies=None, checkpoint=None, history=None):
        """ATLAS has diagnosed an error and has a plan. Nothing tried yet."""
        with self._lock:
            self.recovery = {
                "error_class": error_class,
                "message": message,
                # Observed facts and the causes they point at. Both come from
                # the runtime; neither is rendered if it is empty, because an
                # invented diagnosis is worse than none.
                "evidence": list(evidence or []),
                "hypotheses": list(hypotheses or []),
                "why_first": why_first,
                # One sentence from failure memory. "No verified recovery
                # history for this failure signature." is the honest and
                # commonest answer during the collection phase.
                "history": history,
                "verifies": verifies,
                "checkpoint": checkpoint,
                # `used` is the only thing that says whether ATLAS's ranking
                # is being followed. In shadow it is False and the plan is
                # what ATLAS WOULD have done.
                "atlas_selected": bool(used),
                "plan": [{"action": a,
                          "confidence": (scores or {}).get(a)}
                         for a in (order or [])],
                "attempts": [],
                "status": "PLANNED",
                "recovered": None,
                "verified": None,
                "reason": None,
                "started": _stamp(),
                "reference": self.current_shipment,
                "provider": self.current_system,
            }
            self.recovery_stats["diagnosed"] += 1
            self._mark("warn", "ATLAS diagnosed {0}".format(error_class))
            self._intel_event("recovery_plan_created", self.current_shipment,
                              error_class=error_class,
                              plan=",".join(order or []) or "none",
                              checkpoint=checkpoint)
            self._touch()

    @_guard
    def recovery_attempt(self, index, total, action, confidence, result,
                         verified):
        with self._lock:
            if self.recovery is None:
                return
            attempts = self.recovery["attempts"]
            for existing in attempts:
                if existing["index"] == index:
                    existing.update(result=result, verified=verified)
                    break
            else:
                self.recovery_stats["attempts"] += 1
                attempts.append({"index": index, "total": total,
                                 "action": action, "confidence": confidence,
                                 "result": result, "verified": verified,
                                 "time": _stamp()})
            ref = self.recovery.get("reference")
            if result == "RUNNING":
                self._intel_event("recovery_started", ref, action=action, attempt=index)
                self._intel_event("verification_started", ref, action=action,
                                  check=self.recovery.get("verifies"))
            elif verified is True:
                self._intel_event("verification_passed", ref, action=action)
            elif verified is False or result == "FAILED":
                self._intel_event("verification_failed", ref, action=action,
                                  result=result, verified=verified)
                self._observe("attempt_failed", key="af|{0}|{1}|{2}|{3}".format(
                    self.run_id, ref, self.recovery.get("started"), index),
                    reference=ref, action=action,
                    error_class=self.recovery.get("error_class"))
            self.recovery["status"] = "RUNNING"
            self._touch()

    @_guard
    def recovery_done(self, recovered, reason, verified=None):
        with self._lock:
            if self.recovery is None:
                return
            # The verification belongs to the attempt that ended it, and it is
            # three-valued like every other verification here: True confirmed,
            # False contradicted, None never checked.
            if self.recovery.get("status") not in ("RECOVERED", "EXHAUSTED"):
                self.recovery_stats["recovered" if recovered else "exhausted"] += 1
            self.recovery.update(
                status="RECOVERED" if recovered else "EXHAUSTED",
                recovered=bool(recovered), reason=reason, verified=verified,
                finished=_stamp())
            self._intel_event("recovery_completed" if recovered else "recovery_failed",
                              self.recovery.get("reference"),
                              error_class=self.recovery.get("error_class"),
                              verified=verified, reason=reason)
            last = (self.recovery.get("attempts") or [{}])[-1]
            self._observe("recovery_completed" if recovered else "recovery_exhausted",
                          key="rd|{0}|{1}|{2}".format(self.run_id, self.recovery.get("reference"),
                                                       self.recovery.get("started")),
                          reference=self.recovery.get("reference"),
                          error_class=self.recovery.get("error_class"),
                          action=last.get("action"), verified=verified)
            self._archive_recovery()
            self._mark("ok" if recovered else "warn",
                       "ATLAS recovery {0}".format(
                           "succeeded" if recovered else "exhausted"))
            self._touch()

    @_guard
    def recovery_cleared(self):
        """The shipment moved on. The panel stops showing a stale error."""
        with self._lock:
            self._archive_recovery()
            self.recovery = None
            self._touch()

    def _failure_intelligence(self, record):
        """
        A shipment that did not complete: detect, classify, diagnose and plan,
        from this record and this run's recovery history. Caller holds the lock.
        """
        try:
            from intelligence import failures as _failures
        except Exception:
            return
        try:
            episodes = [e for e in self.recovery_history
                        if e.get("reference") == record.get("reference")]
            item = _failures.from_record(record, run_id=self.run_id, recoveries=episodes)
        except Exception:
            return
        if item is None:
            return
        ref, fid = record.get("reference"), item["failure_id"]
        self._intel_event("failure_detected", ref, failure_id=fid, carrier=record.get("carrier"),
                          stage=item["stage"], state=record.get("state"))
        self._intel_event("failure_classified", ref, failure_id=fid,
                          classification=item["classification"],
                          basis=item["classification_basis"])
        self._intel_event("diagnosis_created", ref, failure_id=fid,
                          root_cause_status=item["root_cause_status"])
        self._intel_event("recovery_plan_created", ref, failure_id=fid,
                          plan=item["recovery_plan"]["status"])
        # ── WORK: the run does not stop. A transient failure with nothing
        #    written is queued for one retry after the other shipments; every
        #    failure goes on ATLAS's work list with its plan.
        work = item["work"]
        record["work"] = {"mode": work["mode"], "why": work["why"]}
        if work["mode"] == "RETRY_THIS_RUN" and ref not in self._retried \
                and ref not in self.retry_queue:
            self.retry_queue.append(ref)
            record["work"]["queued"] = True
        if self.intel is not None:
            try:
                from intelligence import backlog as _backlog
                stored = _backlog.add(item)
            except Exception:
                stored = None
            if stored:
                self._intel_event("work_item_recorded", ref, failure_id=fid, key=stored["key"],
                                  mode=work["mode"], status=stored["status"])

    def _emit_outcome(self, record, result):
        """
        The shipment's final outcome, and every recovery episode that ran on
        it, joined to that outcome. Called with the lock held.

        `verified` is the existing pipeline's verdict and nothing else: the
        shipment counted successful AND every Hub write read back and matched.
        """
        if self.intel is None:
            return
        try:
            from intelligence import events as _ev
            verified_fn = _ev.verified_success
        except Exception:
            def verified_fn(res, ver):
                values = list((ver or {}).values())
                return res == "SUCCESS" and bool(values) and all(v is True for v in values)
        verification = dict(record.get("verification") or {})
        verified = verified_fn(result, verification)
        reference = record.get("reference")
        written = any(isinstance(a, str) and ("updated with" in a or "→" in a)
                      for a in (record.get("coe_action"), record.get("bu_action")))
        self._emit("shipment", reference=reference, carrier=record.get("carrier"),
                   provider=record.get("provider"), mode=record.get("transport_mode"),
                   result=result, outcome_class=record.get("outcome"),
                   verified=verified, verification=verification,
                   extracted=bool(record.get("provider_status") or record.get("provider_eta")
                                  or record.get("provider_ata")),
                   written=written, duration_ms=record.get("duration_ms"),
                   error=(record.get("error") or None),
                   human_step=bool(record.get("human_step")),
                   # What the code that stopped it declared (e.g.
                   # CARRIER_PAGE_LAYOUT_CANDIDATE), for carrier health.
                   failure_category=(record.get("failure") or {}).get("category"),
                   strategy_issue=record.get("strategy_issue"),
                   strategies=record.get("strategies") or [])
        try:
            from intelligence import backlog as _backlog
            for item in _backlog.outcome(reference, self.run_id, result, verified):
                if item["status"] == "RESOLVED_VERIFIED":
                    self._intel_event("work_item_resolved", reference, key=item["key"],
                                      by=item["resolved"]["by"])
        except Exception:
            pass
        episodes = [e for e in self.recovery_history if e.get("reference") == reference]
        if self.recovery and self.recovery.get("reference") == reference \
                and not self.recovery.get("_archived"):
            episodes.append(self.recovery)
        for e in episodes:
            key = "{0}|{1}|{2}".format(reference, e.get("started"), e.get("error_class"))
            if key in self._intel_recovery_done:
                continue
            self._intel_recovery_done.add(key)
            self._emit("recovery", reference=reference, carrier=record.get("carrier"),
                       provider=e.get("provider") or record.get("provider"),
                       error_class=e.get("error_class"), status=e.get("status"),
                       atlas_selected=bool(e.get("atlas_selected")),
                       recovery_verified=e.get("verified"),
                       attempts=[{"action": a.get("action"), "result": a.get("result"),
                                  "verified": a.get("verified"), "index": a.get("index")}
                                 for a in (e.get("attempts") or [])],
                       shipment_result=result, shipment_verified=verified)

    def _archive_recovery(self):
        """Keep a finished (or abandoned) episode once. Caller holds the lock."""
        episode = self.recovery
        if not episode or episode.get("_archived"):
            return
        episode["_archived"] = True
        self.recovery_history.appendleft(
            {k: (list(v) if isinstance(v, list) else v)
             for k, v in episode.items() if k != "_archived"})

    def _touch_cold(self):
        """
        Call from anything that changes a shipment record or an exception.

        Missing a call here would leave the shipments table showing stale rows
        until something else happened to bump it, so test_ui.py drives every
        public method that can touch a record and asserts that a changed
        payload always moved this number.
        """
        self.cold_version += 1

    def _mark(self, icon, text):
        self.timeline.appendleft({
            "time": _stamp(),
            "icon": icon,
            "text": text,
        })

    def _system(self, key):
        return self.systems.get(key)

    # -- run lifecycle -----------------------------------------------------

    @_guard
    def run_started(self, **config):
        with self._lock:
            self.run_status = "running"
            self.started_at = _now()
            self.finished_at = None
            self.human_action = None
            self.human_events.clear()
            self.human_queue = []
            self.recovery_history.clear()
            self._intel_human_done = set()
            self._intel_recovery_done = set()
            self.intel_log.clear()
            self.retry_queue = []
            self._retried = set()
            for key, value in config.items():
                if hasattr(self, key):
                    setattr(self, key, value)
            self.systems["browser"]["state"] = "connected"
            self.systems["browser"]["activity"] = "Edge session open"
            self.systems["browser"]["last_success"] = _stamp()
            self._mark("start", "Automation run started")
            self._observe("run_started", key="rs|{0}|{1}".format(self.run_id, _stamp()),
                          run_id=self.run_id)
            self._touch()

    @_guard
    def run_finished(self, status="finished"):
        with self._lock:
            self.run_status = status
            self.finished_at = _now()
            self.current_step = None
            self.current_system = None
            self.current_shipment = None
            self.pagination_complete = True
            for system in self.systems.values():
                if system["state"] in ("connected", "processing"):
                    system["state"] = "idle"
                    system["activity"] = None
            self._mark(
                "stop",
                "Run finished — {0} updated, {1} skipped, {2} failed".format(
                    self.successful, self.skipped, self.failed
                ),
            )
            self._observe("run_finished", key="rf|{0}|{1}".format(self.run_id, status),
                          status=status)
            self._touch()

    @_guard
    def run_fatal(self, error):
        with self._lock:
            self.run_status = "fatal"
            self.finished_at = _now()
            self.exceptions.appendleft({
                "time": _stamp(),
                "severity": "fatal",
                "reference": None,
                "system": self.current_system,
                "step": self.current_step,
                "message": str(error),
            })
            self._mark("error", "Fatal error — run stopped")
            self._touch()
        self._touch_cold()

    @_guard
    def heartbeat(self):
        with self._lock:
            self._touch()

    # -- pipeline ----------------------------------------------------------

    @_guard
    def page_scanned(self, table_page, rows):
        with self._lock:
            self.current_page = table_page
            self.pages_scanned = max(self.pages_scanned, table_page)
            self.discovered += rows
            self._mark(
                "scan",
                "Page {0} scanned — {1} Under Clearance rows queued".format(table_page, rows),
            )
            self._touch()

    @_guard
    def pagination_ended(self, table_page, reason=""):
        with self._lock:
            self.pagination_complete = True
            self._mark("scan", "Pagination ended at page {0}".format(table_page))
            self._touch()

    @_guard
    def step(self, text, system=None):
        with self._lock:
            self.current_step = text
            self._step_started = _now()
            if system:
                self.current_system = system
                target = self._system(system)
                if target:
                    target["state"] = "processing"
                    target["activity"] = text
                    self._system_started = _now()
            shipment = self._current_record()
            if shipment is not None:
                shipment["step"] = text
                shipment["steps"].append({"time": _stamp(), "text": text})
            self._touch()

    @_guard
    def atlas(self, label, detail="", reference=None):
        """
        Record one ATLAS event.

        The automation calls this from the engine's emission points and
        nowhere else. `influenced` flips only for a label that actually
        asserts ATLAS did something — a `Deterministic fallback` is ATLAS
        saying it stood down, and marking a shipment as ATLAS-influenced
        because the engine announced it was NOT involved would invert the one
        distinction the panel exists to show.
        """
        with self._lock:
            label = str(label or "").strip()
            event = {
                "time": _stamp(),
                "label": label,
                "detail": str(detail or "")[:400],
                "reference": reference,
                "influenced": label in ATLAS_INFLUENCE_LABELS,
            }
            self.atlas_events.appendleft(event)
            if label in ATLAS_INFLUENCE_LABELS:
                self.atlas_influenced_actions += 1
            elif label == "Deterministic fallback":
                self.atlas_fallbacks += 1

            record = (self._index.get(reference) if reference
                      else self._current_record())
            if record is not None:
                state = record.setdefault(
                    "atlas", {"influenced": False, "chosen": None, "events": []})
                state["events"].append(event)
                del state["events"][:-12]
                if event["influenced"]:
                    state["influenced"] = True
                if label == "Strategy selected" and detail:
                    state["chosen"] = str(detail).split(" ")[0].strip(":,")
            self._touch()
        self._touch_cold()

    @_guard
    def human_verification_required(self, reference=None, label=""):
        """
        The run is paused waiting for a person to clear a challenge.

        Recorded as its own state, not as an error. The shipment was never
        looked up, so calling it a failure would be wrong — and the operator
        needs to know a browser is waiting for them, which an exception buried
        in a log does not achieve.
        """
        with self._lock:
            self.human_verification = {
                "waiting": True, "reference": reference, "where": str(label)[:120],
                "since": _stamp(), "cleared_after_s": None,
            }
            self.systems["browser"]["state"] = "waiting"
            self.systems["browser"]["activity"] = (
                "Human verification required" + (" for " + reference if reference else ""))
            record = self._index.get(reference) or self._current_record()
            if record is not None:
                record["human_verification"] = "REQUIRED"
            self._mark("warn", "Human verification required{0}{1}".format(
                " on " + str(label) if label else "",
                " for " + reference if reference else ""))
            self._touch()

    @_guard
    def human_verification_cleared(self, reference=None, waited_seconds=None):
        """
        HUMAN_VERIFICATION_COMPLETED: the challenge is gone. That is NOT
        carrier access — carrier_access stays NOT_CONFIRMED until the run
        reads the shipment page itself (carrier_access() below).
        """
        with self._lock:
            self.human_verification = {
                "waiting": False, "reference": reference,
                "where": (self.human_verification or {}).get("where", ""),
                "since": (self.human_verification or {}).get("since"),
                "cleared_after_s": waited_seconds,
                "state": "HUMAN_VERIFICATION_COMPLETED",
                "carrier_access": "NOT_CONFIRMED",
            }
            record = self._index.get(reference) or self._current_record()
            if record is not None:
                record["human_verification"] = "COMPLETED"
                record["carrier_access"] = {"state": "NOT_CONFIRMED", "url": "",
                                            "detail": "verification completed; the shipment "
                                                      "page has not been read yet",
                                            "at": _stamp()}
            self.systems["browser"]["state"] = "connected"
            self.systems["browser"]["activity"] = None
            self._mark("warn", "Human verification completed{0}{1}; carrier access not yet "
                       "confirmed".format(
                           " after " + str(waited_seconds) + "s" if waited_seconds is not None
                           else "", " for " + reference if reference else ""))
            self._touch()

    def carrier_access(self, reference, state, url=None, detail="", facts=None):
        """
        CONFIRMED only when the shipment's page was read; RESTRICTED when the
        carrier showed its restriction page. Kept on the shipment's record
        and, for the shipment a person just verified, on that state too.
        """
        state = state if state in ("CONFIRMED", "NOT_CONFIRMED") else "RESTRICTED"
        with self._lock:
            entry = {"state": state, "url": str(url or "")[:300], "detail": str(detail)[:200],
                     "at": _stamp()}
            record = self._index.get(reference) or self._current_record()
            if record is not None:
                record["carrier_access"] = entry
                if isinstance(facts, dict):
                    # The operator's checklist for this attempt, as the run
                    # recorded it: verification, access, extraction, write.
                    record["access_check"] = {str(k)[:30]: (v if isinstance(v, bool)
                                                            else str(v)[:80])
                                              for k, v in list(facts.items())[:8]}
            hv = self.human_verification
            if hv and hv.get("reference") == reference:
                hv["carrier_access"] = state
                hv["state"] = "CARRIER_ACCESS_" + state
            if state != "CONFIRMED":
                self._mark("error", "Carrier access {0} for {1}".format(
                    "restricted" if state == "RESTRICTED" else "not confirmed", reference))
            self._touch()

    # -- human in the loop -------------------------------------------------

    _HUMAN_FIELDS = ("run_id", "action_id", "reference", "carrier", "provider",
                     "step", "reason", "page_id", "url", "opened_at",
                     "deadline", "timeout_s", "instructions")

    @_guard
    def human_action_opened(self, action):
        """
        The run is now WAITING_FOR_HUMAN on one shipment, holding its tab.

        The shipment record moves to waiting_for_human — not skipped, not
        failed, not done — and the action is published for the dashboard to
        offer Open Browser Session and Resume against.
        """
        with self._lock:
            published = {key: action.get(key) for key in self._HUMAN_FIELDS}
            published.update(waiting=True, state="waiting_for_human",
                             claimed_by=None, session_opened_at=None,
                             last_response=None, resumed_via=None,
                             closed_at=None)
            self.human_action = published
            record = self._index.get(published.get("reference"))
            if record is not None:
                record["human_step"] = True
                record["state"] = "waiting_for_human"
                record["step"] = "Waiting for a person on {0}".format(
                    published.get("carrier") or "the carrier page")
                record["updated"] = _stamp()
            self.systems["browser"]["state"] = "waiting"
            self.systems["browser"]["activity"] = "Waiting for a person on {0}{1}".format(
                published.get("carrier") or "the carrier page",
                " for " + str(published["reference"]) if published.get("reference") else "")
            self.current_step = "Waiting for a person on {0}".format(
                published.get("carrier") or "the carrier page")
            self._mark("warn", "HUMAN ACTION REQUIRED — {0} {1}".format(
                published.get("carrier") or "", published.get("reference") or ""))
            self._touch()
        self._touch_cold()

    @_guard
    def human_action_event(self, event, detail="", **fields):
        """One entry in the human-intervention log, plus any field updates."""
        with self._lock:
            action = self.human_action or {}
            # The event names its own action when it has one: a queue task's
            # events must not be filed under whatever wait is live.
            action_id = fields.get("action_id") or action.get("action_id")
            entry = {"time": _stamp(), "event": str(event)[:40],
                     "run_id": action.get("run_id") or self.run_id,
                     "action_id": action_id,
                     "reference": fields.get("reference")
                     or (action.get("reference")
                         if action_id == action.get("action_id") else None),
                     "detail": str(detail)[:300]}
            self.human_events.appendleft(entry)
            if self.human_action is not None and \
                    action_id == self.human_action.get("action_id"):
                for key in ("claimed_by", "session_opened_at", "last_response",
                            "url", "page_id"):
                    if key in fields:
                        self.human_action[key] = fields[key]
            self._touch()

    @_guard
    def human_action_closed(self, outcome, detail=""):
        """
        The wait is over. `outcome` is resumed, timeout, session_lost or
        unattended. Resumed puts the shipment back to processing — it is
        NOT a success: the result still has to be read, validated and
        written and read back.
        """
        with self._lock:
            if self.human_action is None:
                return
            self.human_action.update(
                waiting=False, state=str(outcome), closed_at=_stamp(),
                last_response=detail or self.human_action.get("last_response"))
            queued = any(t.get("action_id") == self.human_action.get("action_id")
                         for t in self.human_queue)
            if not queued and outcome in ("resumed", "timeout", "session_lost"):
                # The single in-place wait. "resumed" is not an outcome: the
                # shipment's own verified result decides, and learning joins it.
                waited = None
                try:
                    waited = round((_now() - datetime.strptime(
                        str(self.human_action.get("opened_at")),
                        "%Y-%m-%d %H:%M:%S")).total_seconds(), 1)
                except Exception:
                    pass
                self._emit("human_task", action_id=self.human_action.get("action_id"),
                           reference=self.human_action.get("reference"),
                           carrier=self.human_action.get("carrier"),
                           provider=self.human_action.get("provider"),
                           reason=self.human_action.get("reason"),
                           step=self.human_action.get("step"),
                           status={"resumed": "RESUMED", "timeout": "TIMEOUT",
                                   "session_lost": "HUMAN_SESSION_LOST"}[outcome],
                           waited_s=waited, queued=False)
            record = self._index.get(self.human_action.get("reference"))
            if record is not None and record.get("state") == "waiting_for_human":
                if outcome == "resumed":
                    record["state"] = "processing"
                    record["step"] = "Resumed after human action — reading the result"
                else:
                    record["step"] = {"timeout": "Human timeout",
                                      "session_lost": "Browser session lost",
                                      "unattended": "Needs a person"}.get(
                                          outcome, "Human action ended")
                record["updated"] = _stamp()
            self.systems["browser"]["state"] = "connected"
            self.systems["browser"]["activity"] = None
            self._mark("ok" if outcome == "resumed" else "warn",
                       "Human action {0} — {1}".format(
                           outcome, self.human_action.get("reference") or ""))
            self._touch()
        self._touch_cold()

    def attach_intelligence(self, events):
        """Record outcomes to ATLAS's event store from now on (or stop: None)."""
        self.intel = events
        if events is not None:
            self.persist_atlas_state()

    def persist_atlas_state(self):
        """Keep ATLAS's state and garden in the store folder from now on."""
        engine = _state_engine(persist=True)
        if engine is not None:
            self.atlas_state = engine

    def _atlas_state_view(self):
        """The engine's view, with Human Action priority from this state. Lock held."""
        engine = self.atlas_state
        if engine is None:
            return None
        try:
            waiting = bool(self.human_action and self.human_action.get("waiting")) or any(
                str(t.get("status") or "") not in _HQ_TERMINAL for t in self.human_queue)
            engine.set_human(waiting)
            return engine.snapshot()
        except Exception:
            return None

    def _observe(self, event, key=None, **fields):
        """One event for ATLAS's state engine. Display only; never raises."""
        engine = self.atlas_state
        if engine is None:
            return
        try:
            engine.observe(event, key=key, **fields)
        except Exception:
            pass

    def _emit(self, kind, **fields):
        """One event to ATLAS's store, if attached. Never raises."""
        intel = self.intel
        if intel is None:
            return
        try:
            fields.setdefault("run_id", self.run_id)
            written = intel.record(kind, **fields)
            if written and kind == "recovery":
                actions = [a.get("action") for a in (fields.get("attempts") or [])
                           if a.get("verified") is True]
                self._observe("learning_recorded",
                              key="lr|{0}|{1}|{2}".format(self.run_id, fields.get("reference"),
                                                          fields.get("error_class")),
                              reference=fields.get("reference"), kind="recovery",
                              status=fields.get("status"), error_class=fields.get("error_class"),
                              action=actions[-1] if actions else None,
                              verified=(fields.get("recovery_verified") is True
                                        and fields.get("shipment_verified") is True))
            if written and kind in ("shipment", "recovery"):
                self._intel_event(
                    "learning_recorded", fields.get("reference"), kind=kind,
                    verified=bool(fields.get("verified") if kind == "shipment"
                                  else fields.get("shipment_verified")),
                    status=fields.get("result") if kind == "shipment" else fields.get("status"))
        except Exception:
            pass

    def _intel_event(self, event, reference=None, **fields):
        """One line of ATLAS's operational intelligence log. Caller may hold the lock."""
        if event not in INTEL_EVENTS:
            return
        entry = {"at": _stamp(), "event": event, "reference": reference,
                 "run_id": self.run_id}
        for key, value in fields.items():
            if value is None or key in entry:
                continue
            entry[key] = value if isinstance(value, (bool, int, float)) else str(value)[:160]
        self.intel_log.appendleft(entry)
        hook = self.log_hook
        if hook is not None:
            try:
                hook("[ATLAS] {0} | {1}".format(event, " | ".join(
                    "{0}={1}".format(k, v) for k, v in entry.items()
                    if k not in ("at", "event") and v is not None)))
            except Exception:
                pass

    def deferred_retries(self):
        """References to retry once, after the other shipments. Never raises."""
        try:
            with self._lock:
                return [r for r in self.retry_queue if r not in self._retried]
        except Exception:
            return []

    @_guard
    def deferred_retry_started(self, reference):
        """The run is retrying this shipment now — its one deferred retry."""
        with self._lock:
            if reference in self.retry_queue:
                self.retry_queue.remove(reference)
            self._retried.add(reference)
            self._intel_event("deferred_retry_started", reference)
            if self.intel is not None:
                try:
                    from intelligence import backlog as _backlog
                    _backlog.retrying(reference, self.run_id)
                except Exception:
                    pass
            self._touch()

    def set_log_hook(self, hook):
        """The automation's write_log, so ATLAS's intelligence lines reach the run log."""
        self.log_hook = hook

    @_guard
    def strategy_attempts(self, provider, issue, attempts):
        """
        The navigation strategies tried for the current shipment, as the
        automation recorded them: which ran, which were skipped, and whether
        each reached the right shipment page. Kept on the record and joined
        to the shipment's verified outcome when it closes.
        """
        with self._lock:
            record = self._current_record()
            if record is None:
                return
            record["strategy_issue"] = str(issue)[:60]
            record["strategies"] = [{
                "attempt": a.get("attempt"),
                "strategy": str(a.get("strategy") or "")[:60],
                "page_verified": a.get("awb_verified") is True,
                "skipped": "skipped" in str(a.get("outcome") or ""),
                "error": (str(a.get("error") or a.get("outcome") or "")[:160] or None),
            } for a in (attempts or [])[:8]]
            self._touch()
        self._touch_cold()

    _QUEUE_FIELDS = ("action_id", "run_id", "reference", "carrier", "provider",
                     "step", "reason", "created_at", "created_epoch",
                     "timeout_at", "timeout_epoch", "status", "label",
                     "claimed_by", "attempts", "last_detail", "updated_at",
                     "closed_at", "history")

    @_guard
    def human_queue_changed(self, tasks):
        """
        The run's Human Action queue changed. Published as the run keeps it
        — no codes, answers or credentials exist in a task to publish. A
        parked shipment's row says where its task stands.
        """
        with self._lock:
            clean = []
            for task in (tasks or [])[-100:]:
                if not isinstance(task, dict):
                    continue
                item = {key: task.get(key) for key in self._QUEUE_FIELDS}
                item["history"] = list(item.get("history") or [])[-16:]
                clean.append(item)
            self.human_queue = clean
            for task in clean:
                if task.get("status") in ("SUCCESS", "TIMEOUT", "HUMAN_SESSION_LOST",
                                          "VERIFICATION_NOT_CONFIRMED", "FAILED") \
                        and task.get("action_id") not in self._intel_human_done:
                    self._intel_human_done.add(task.get("action_id"))
                    waited = None
                    try:
                        closed = datetime.strptime(str(task.get("closed_at")),
                                                   "%Y-%m-%d %H:%M:%S").timestamp()
                        waited = round(max(0.0, closed - float(task.get("created_epoch"))), 1)
                    except Exception:
                        pass
                    self._emit("human_task", action_id=task.get("action_id"),
                               run_id=task.get("run_id") or self.run_id,
                               reference=task.get("reference"), carrier=task.get("carrier"),
                               provider=task.get("provider"), reason=task.get("reason"),
                               step=task.get("step"), status=task.get("status"),
                               attempts=task.get("attempts"), waited_s=waited,
                               queued=True)
            for task in clean:
                record = self._index.get(task.get("reference"))
                if record is None or record.get("state") != "waiting_for_human":
                    continue
                if record["reference"] == self.current_shipment and \
                        self.human_action and self.human_action.get("waiting"):
                    continue        # the live wait already says it
                step = "Human Action queue — {0}".format(
                    (task.get("label") or "").lower() or "waiting")
                if record.get("step") != step:
                    record["step"] = step
                    record["updated"] = _stamp()
            self._touch()
        self._touch_cold()

    @_guard
    def register_system(self, key, name, role="Carrier AWB tracking"):
        """
        Add a carrier to the Systems panel.

        Called by the automation for every provider it can track, so the panel
        always reflects what is actually automated rather than a list that has
        to be kept in step by hand.
        """
        with self._lock:
            if key in self.systems:
                self.systems[key]["name"] = name
                self.systems[key]["role"] = role
            else:
                self.systems[key] = {
                    "key": key, "name": name, "role": role, "state": "idle",
                    "activity": None, "last_success": None, "last_error": None,
                    "ops": 0, "last_ms": None,
                }
            self._touch()

    @_guard
    def system_ok(self, key, activity=None):
        with self._lock:
            target = self._system(key)
            if not target:
                return
            target["state"] = "connected"
            target["activity"] = activity
            target["last_success"] = _stamp()
            target["ops"] += 1
            if self._system_started:
                target["last_ms"] = int((_now() - self._system_started).total_seconds() * 1000)
            self._touch()

    @_guard
    def system_error(self, key, message):
        with self._lock:
            target = self._system(key)
            if not target:
                return
            target["state"] = "error"
            target["last_error"] = "{0} — {1}".format(_stamp(), str(message)[:240])
            self._touch()

    @_guard
    def system_warn(self, key, message):
        with self._lock:
            target = self._system(key)
            if not target:
                return
            target["state"] = "warning"
            target["activity"] = str(message)[:180]
            self._touch()

    # -- shipments ---------------------------------------------------------

    def _current_record(self):
        reference = self.current_shipment
        return self._index.get(reference) if reference else None

    @_guard
    def shipment_started(self, shipment):
        with self._lock:
            reference = shipment.get("bol_awb")
            self._observe("shipment_started", key="ss|{0}|{1}|{2}".format(
                self.run_id, reference, reference in self._retried), reference=reference)
            record = {
                "reference": reference,
                "carrier": shipment.get("carrier"),
                "provider": shipment.get("provider"),
                "transport_mode": transport_mode(shipment.get("provider"),
                                                 shipment.get("carrier")),
                "internal_eta": shipment.get("current_eta") or None,
                "table_page": shipment.get("table_page"),
                "hub_status": self.target_status,
                "state": "processing",
                "step": "Opening carrier tracking",
                "provider_status": None,
                "provider_eta": None,
                "provider_ata": None,
                "coe_action": None,
                "bu_action": None,
                # Did ATLAS steer any write on this shipment, and what did it
                # say while doing so. Set only by atlas() below, which is only
                # called by the engine's own emission points.
                "atlas": {"influenced": False, "chosen": None, "events": []},
                "error": None,
                "outcome": None,
                "started_at": _iso(_now()),
                "started_epoch": _now().timestamp(),
                "duration_ms": None,
                "updated": _stamp(),
                "steps": [],
            }
            previous = self._index.get(reference)
            if previous is not None and reference in self._retried \
                    and previous.get("state") in ("failed", "skipped", "partial"):
                # The run's one deferred retry of a failed shipment: one row,
                # which remembers how the first attempt ended.
                record["deferred_retry"] = {
                    "state": previous.get("state"), "outcome": previous.get("outcome"),
                    "error": (previous.get("error") or "")[:300],
                    "at": previous.get("updated")}
                try:
                    self.shipments.remove(previous)
                except ValueError:
                    pass
            elif previous is not None and previous.get("state") in (
                    "waiting_for_human", "human_timeout"):
                # Looked up again from the Human Action queue: the same
                # shipment, so one row, not two.
                try:
                    self.shipments.remove(previous)
                except ValueError:
                    pass
            self._index[reference] = record
            self.shipments.appendleft(record)
            # The deque drops old records but _index kept them forever. Prune
            # so the two stay the same size.
            if len(self._index) > MAX_SHIPMENTS:
                live = {r["reference"] for r in self.shipments}
                self._index = {k: v for k, v in self._index.items() if k in live}
            self.current_shipment = reference
            self.current_step = "Opening carrier tracking"
            self.current_system = shipment.get("provider")
            self._mark(
                "shipment",
                "{0} {1} picked up from page {2}".format(
                    shipment.get("provider") or "Carrier", reference, shipment.get("table_page")
                ),
            )
            self._touch()
        self._touch_cold()

    @_guard
    def provider_result(self, result):
        with self._lock:
            record = self._current_record()
            if record is None or not isinstance(result, dict):
                return
            record["provider_status"] = result.get("tracking_status")
            record["provider_eta"] = result.get("eta")
            record["provider_ata"] = result.get("ata")
            # The label each date was read from, when the reader kept one —
            # "Actual Arrival", "ETA" — shown as the source event.
            record["provider_eta_source"] = result.get("eta_source")
            record["provider_ata_source"] = result.get("ata_source")
            record["updated"] = _stamp()
            self._mark(
                "ok",
                "{0} responded for {1} — ETA {2} / ATA {3}".format(
                    result.get("provider") or "Carrier",
                    record["reference"],
                    result.get("eta") or "—",
                    result.get("ata") or "—",
                ),
            )
            self._touch()
        self._touch_cold()

    @_guard
    def view_updated(self, view_name, field_name, value, verified=None):
        """
        A date was saved to a Hub view. `verified` is the read-back verdict
        as update_one_view recorded it — True read back and matched, None not
        read back — kept on the record so the dashboard can say which, and
        never upgraded to a verification nobody performed.
        """
        with self._lock:
            record = self._current_record()
            if record is not None:
                record.setdefault("verification", {})[
                    "{0} {1}".format(view_name, field_name)] = verified
                if field_name.upper() == "ETA":
                    record["coe_action"] = "{0} {1} → {2}".format(view_name, field_name, value)
                else:
                    record["bu_action"] = "{0} {1} → {2}".format(view_name, field_name, value)
                record["updated"] = _stamp()
            self.systems["hub"]["ops"] += 1
            self.systems["hub"]["last_success"] = _stamp()
            self._mark("ok", "{0} view {1} saved as {2}".format(view_name, field_name, value))
            self._touch()
        self._touch_cold()

    @_guard
    def shipment_finished(self, reference, result, details="", actions=None,
                          outcome_class=None, outcome=None, failure=None, **kwargs):
        """
        Close a shipment's record.

        `result` is SUCCESS, SKIPPED, FAILED or PARTIAL. `outcome` — or its
        older spelling `outcome_class` — is the named operational class,
        e.g. NO RESULT.

        Both used to be called `outcome`: the second positional parameter and
        the keyword every failure path passed. Python refuses a call that
        gives one name two values, _guard swallowed the TypeError, and so
        every skipped, failed or partly updated shipment stayed "Processing"
        on the dashboard for the rest of the run. Only SUCCESS, which passes
        no keyword, ever closed.
        """
        with self._lock:
            record = self._index.get(reference)
            if record is None:
                return
            result = (result or "").upper()
            record["state"] = {
                "SUCCESS": "updated",
                "SKIPPED": "skipped",
                "FAILED": "failed",
                "PARTIAL": "partial",
                "HUMAN_TIMEOUT": "human_timeout",
                # Parked for a person: still waiting, not an outcome.
                "HUMAN_QUEUED": "waiting_for_human",
            }.get(result, "unknown")
            record["error"] = details or None
            # Named operational class from classify_failure(), e.g. NO RESULT.
            record["outcome"] = outcome_class or outcome
            # What the code that stopped this shipment declared about it:
            # stage, category, the rule that decided, what was already read.
            record["failure"] = _clean_failure(failure)
            record["updated"] = _stamp()
            record["duration_ms"] = int(
                (_now().timestamp() - record["started_epoch"]) * 1000
            )
            if isinstance(actions, dict):
                record["coe_action"] = actions.get("coe") or record["coe_action"]
                record["bu_action"] = actions.get("bu") or record["bu_action"]

            if result == "SUCCESS":
                record["step"] = "Complete"
                self._mark("ok", "{0} updated in Logistics Hub".format(reference))
            elif result == "SKIPPED":
                record["step"] = "Skipped"
                provider = record.get("provider")
                if provider and self.systems.get(provider, {}).get("state") == "processing":
                    self.systems[provider]["state"] = "connected"
                    self.systems[provider]["activity"] = "No ETA or ATA published"
                self._mark("warn", "{0} skipped — {1}".format(reference, details))
                self.exceptions.appendleft({
                    "time": _stamp(),
                    "severity": "warning",
                    "reference": reference,
                    "system": record.get("provider"),
                    "step": record.get("step"),
                    "outcome": record.get("outcome"),
                    "message": details or "Skipped",
                })
            elif result == "HUMAN_QUEUED":
                record["step"] = "Human Action queue — waiting for you"
                self._mark("warn", "{0} paused safely and added to the Human "
                           "Action queue".format(reference))
            elif result == "HUMAN_TIMEOUT":
                # Nobody completed the human step in time. Nothing was looked
                # up and nothing was written: not a success, not a statement
                # about the shipment.
                record["step"] = "Human timeout"
                self._mark("warn", "{0} — human action not completed: {1}".format(
                    reference, details))
                self.exceptions.appendleft({
                    "time": _stamp(),
                    "severity": "warning",
                    "reference": reference,
                    "system": record.get("provider"),
                    "step": record.get("step"),
                    "outcome": record.get("outcome"),
                    "message": details or "Human action not completed",
                })
            elif result == "PARTIAL":
                # A date WAS written to the Hub; a second field failed. Calling
                # the whole shipment a failure understated the work done.
                record["step"] = "Partly updated"
                self._mark(
                    "warn",
                    "{0} partly updated — {1}".format(reference, details),
                )
                self.exceptions.appendleft({
                    "time": _stamp(),
                    "severity": "warning",
                    "reference": reference,
                    "system": record.get("provider"),
                    "step": record.get("step"),
                    "outcome": record.get("outcome"),
                    "message": details or "Partly updated",
                })
            else:
                record["step"] = "Failed"
                self._mark("error", "{0} failed — {1}".format(reference, details))
                self.exceptions.appendleft({
                    "time": _stamp(),
                    "severity": "error",
                    "reference": reference,
                    "system": record.get("provider"),
                    "step": record.get("step"),
                    "outcome": record.get("outcome"),
                    "message": details or "Unknown error",
                })
                if record.get("provider"):
                    self.system_error(record["provider"], details or "Shipment failed")

            if result != "HUMAN_QUEUED":
                self._failure_intelligence(record)
                self._emit_outcome(record, result)
            self._observe("shipment_finished", key="sf|{0}|{1}|{2}|{3}".format(
                self.run_id, reference, result, reference in self._retried),
                reference=reference, result=result,
                verified=_verified_outcome(result, record.get("verification")),
                error_class=(record.get("failure") or {}).get("category")
                or record.get("outcome"))

            self.current_shipment = None
            self.current_step = "Waiting before next shipment"
            self.current_system = None
            self._touch()
        self._touch_cold()

    @_guard
    def counters(self, successful, failed, skipped, partial=None,
                 needs_human=None):
        with self._lock:
            self.successful = successful
            self.failed = failed
            self.skipped = skipped
            if partial is not None:
                self.partial = partial
            if needs_human is not None:
                self.needs_human = needs_human
            self._touch()

    # -- logs --------------------------------------------------------------

    LEVEL_RULES = (
        ("error", re.compile(r"\b(fatal|error|failed|failure|traceback)\b", re.I)),
        ("warning", re.compile(r"\b(warn|warning|skipped|timeout|slow|retry|not found)\b", re.I)),
        ("success", re.compile(r"\b(saved|updated|success|finished|logged in|completed)\b", re.I)),
    )

    # Nothing in the automation logs a secret today, but write_log() is teed
    # straight to the browser, so anything that ever slipped into an exception
    # message would be visible in the UI. Redact on the way in.
    SECRET_PATTERNS = [
        # Order matters: the bearer/basic rule runs first so the generic
        # key:value rule cannot stop at the scheme and leave the token behind.
        (re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.I), r"\1 [redacted]"),
        (re.compile(r"\b(password|passwd|pwd|secret|token|api[_-]?key|authorization)"
                    r"\s*[:=]\s*.+?(?=(?:[,;]|\s\s|$))", re.I), r"\1: [redacted]"),
    ]

    @classmethod
    def _redact(cls, message):
        for pattern, replacement in cls.SECRET_PATTERNS:
            message = pattern.sub(replacement, message)
        return message

    @_guard
    def log(self, message, source=None):
        message = self._redact(message)
        with self._lock:
            level = "info"
            for name, pattern in self.LEVEL_RULES:
                if pattern.search(message):
                    level = name
                    break
            self._log_seq += 1
            self.logs.appendleft({
                "id": self._log_seq,
                "time": _stamp(),
                "level": level,
                "source": source or self.current_system or "runner",
                "message": message,
            })
            self._touch()

    # -- snapshot ----------------------------------------------------------

    # The browser paints at most 200 log lines and 60 timeline rows, so pushing
    # the full 800/300 buffers on every state change was ~329KB per push at
    # several pushes a second. The server sends the trimmed view; the assistant
    # still gets the full one.
    WIRE_LOGS = 250
    WIRE_TIMELINE = 80

    def snapshot(self, trim=False, since_cold=None):
        """
        The whole state, or everything except the expensive unchanged part.

        `since_cold` is the cold_version a caller already holds. When it is
        current, `shipments` and `exceptions` are left out and named in
        `unchanged` instead, and the receiver keeps what it already has. The
        rest of the payload — the run, the current step, the counters, the log
        tail — is always present, so a stream frame is never ambiguous about
        what it is asserting.
        """
        with self._lock:
            now = _now()
            runtime = None
            if self.started_at:
                end = self.finished_at or now
                runtime = int((end - self.started_at).total_seconds())

            processed = self.successful + self.failed + self.skipped + self.partial
            total_known = None
            if self.pagination_complete:
                total_known = min(self.discovered, self.max_records or self.discovered)

            success_rate = None
            if processed:
                # A partial counts as a write: a date did reach the Hub.
                success_rate = round(
                    (self.successful + self.partial) / processed * 100, 1
                )

            current = self._index.get(self.current_shipment) if self.current_shipment else None

            payload = {
                "version": self.version,
                "cold_version": self.cold_version,
                "generated_at": _stamp(now),
                "run": {
                    "status": self.run_status,
                    "run_id": self.run_id,
                    "started_at": _iso(self.started_at),
                    "finished_at": _iso(self.finished_at),
                    "runtime_seconds": runtime,
                    "heartbeat_age": (
                        round((now - self.last_heartbeat).total_seconds(), 1)
                        if self.last_heartbeat else None
                    ),
                    "dry_run": self.dry_run,
                    "target_status": self.target_status,
                    "max_records": self.max_records,
                    "max_pages": self.max_pages,
                    "log_file": self.log_file,
                    "results_file": self.results_file,
                },
                "current": {
                    "step": self.current_step,
                    "system": self.current_system,
                    "page": self.current_page,
                    "shipment": current,
                },
                "human_verification": self.human_verification,
                # WAITING_FOR_HUMAN: the action the run holds open, and the
                # intervention log. Elapsed time is computed by the page from
                # opened_at so it ticks without a push.
                "human_action": (dict(self.human_action)
                                 if self.human_action else None),
                "human_events": list(self.human_events)[:20],
                "human_queue": [dict(t) for t in self.human_queue],
                # Live recovery status, or None. Small and bounded: one
                # error, its plan, and the attempts made against it.
                "recovery": ({k: v for k, v in self.recovery.items()
                              if k != "_archived"} if self.recovery else None),
                "recovery_history": list(self.recovery_history)[:15],
                # ATLAS's operational intelligence log, newest first.
                "intel_log": list(self.intel_log)[:100],
                "retry_queue": list(self.retry_queue),
                # ATLAS's simulated state and the Potato Garden: display only.
                "atlas_state": self._atlas_state_view(),
                "atlas": {
                    "name": ATLAS_NAME,
                    "full_name": ATLAS_FULL_NAME,
                    "influenced_actions": self.atlas_influenced_actions,
                    "fallbacks": self.atlas_fallbacks,
                    "recovery": dict(self.recovery_stats),
                    "events": list(self.atlas_events)[:40],
                },
                "progress": {
                    "discovered": self.discovered,
                    "processed": processed,
                    "total": total_known,
                    "determinate": total_known is not None,
                    "pages_scanned": self.pages_scanned,
                    "remaining": (total_known - processed) if total_known is not None else None,
                },
                "counters": {
                    "successful": self.successful,
                    "failed": self.failed,
                    "skipped": self.skipped,
                    "partial": self.partial,
                    "needs_human": self.needs_human,
                    "processed": processed,
                    "success_rate": success_rate,
                },
                # Hub first, browser last, carriers in between — the order a
                # person reads them in.
                "systems": (
                    [s for s in self.systems.values() if s["key"] == "hub"]
                    + [s for s in self.systems.values()
                       if s["key"] not in ("hub", "browser")]
                    + [s for s in self.systems.values() if s["key"] == "browser"]
                ),
                # The record still being worked on is sent WITHOUT its
                # live step, because that is the only part of this array that
                # changes several times a second and nothing reads it from
                # here: paintLive() takes the in-progress shipment from
                # `current.shipment` above, and the shipments table has no
                # step column. Leaving it in made the whole array — 68% of
                # the payload — churn on every step, which is what stopped
                # this section from being cold.
                #
                # A FINISHED record keeps its step, because by then
                # `current.shipment` is null and the Live page falls back to
                # this array to show how the last shipment ended. The
                # assistant is unaffected: it reads the untrimmed snapshot.
                "shipments": ([
                    (dict(record, steps=[], step=None)
                     if record["reference"] == self.current_shipment
                     else dict(record, steps=[]))
                    for record in self.shipments
                ] if trim else list(self.shipments)),
                "logs": (list(self.logs)[:self.WIRE_LOGS] if trim
                         else list(self.logs)),
                "log_total": len(self.logs),
                "exceptions": list(self.exceptions),
                "timeline": (list(self.timeline)[:self.WIRE_TIMELINE] if trim
                             else list(self.timeline)),
            }
            if since_cold is not None and since_cold == self.cold_version:
                # Nothing about the shipments or the exceptions has changed
                # since the receiver last saw them, so say so instead of
                # re-sending them. This is the 68% of the payload that grows
                # with the length of the run.
                payload["unchanged"] = ["shipments", "exceptions"]
                del payload["shipments"]
                del payload["exceptions"]
            return payload


bridge = ControlTowerState()
