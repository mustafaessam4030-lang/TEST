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
            }
            self.recovery_stats["diagnosed"] += 1
            self._mark("warn", "ATLAS diagnosed {0}".format(error_class))
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
                recovered=bool(recovered), reason=reason, verified=verified)
            self._mark("ok" if recovered else "warn",
                       "ATLAS recovery {0}".format(
                           "succeeded" if recovered else "exhausted"))
            self._touch()

    @_guard
    def recovery_cleared(self):
        """The shipment moved on. The panel stops showing a stale error."""
        with self._lock:
            self.recovery = None
            self._touch()

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
            for key, value in config.items():
                if hasattr(self, key):
                    setattr(self, key, value)
            self.systems["browser"]["state"] = "connected"
            self.systems["browser"]["activity"] = "Edge session open"
            self.systems["browser"]["last_success"] = _stamp()
            self._mark("start", "Automation run started")
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
            self._mark("warn", "Human verification required{0}{1}".format(
                " on " + str(label) if label else "",
                " for " + reference if reference else ""))
            self._touch()

    @_guard
    def human_verification_cleared(self, reference=None, waited_seconds=None):
        with self._lock:
            self.human_verification = {
                "waiting": False, "reference": reference,
                "where": (self.human_verification or {}).get("where", ""),
                "since": (self.human_verification or {}).get("since"),
                "cleared_after_s": waited_seconds,
            }
            self.systems["browser"]["state"] = "connected"
            self.systems["browser"]["activity"] = None
            self._mark("ok", "Human verification cleared{0}{1}".format(
                " after " + str(waited_seconds) + "s" if waited_seconds is not None else "",
                " for " + reference if reference else ""))
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
            entry = {"time": _stamp(), "event": str(event)[:40],
                     "run_id": action.get("run_id") or self.run_id,
                     "action_id": action.get("action_id"),
                     "reference": action.get("reference"),
                     "detail": str(detail)[:300]}
            self.human_events.appendleft(entry)
            if self.human_action is not None:
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
            record = {
                "reference": reference,
                "carrier": shipment.get("carrier"),
                "provider": shipment.get("provider"),
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
                          outcome_class=None, outcome=None, **kwargs):
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
            }.get(result, "unknown")
            record["error"] = details or None
            # Named operational class from classify_failure(), e.g. NO RESULT.
            record["outcome"] = outcome_class or outcome
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
                # Live recovery status, or None. Small and bounded: one
                # error, its plan, and the attempts made against it.
                "recovery": self.recovery,
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
