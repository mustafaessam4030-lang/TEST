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


def _now():
    return datetime.now()


def _stamp(value=None):
    return (value or _now()).strftime("%H:%M:%S")


def _iso(value):
    return value.isoformat() if value else None


def _guard(method):
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            return None
    return wrapper


class ControlTowerState:
    """Single source of truth. All mutations bump `version`."""

    def __init__(self):
        self._lock = threading.RLock()
        self.version = 0

        self.run_status = "idle"          # idle | running | finished | fatal
        self.started_at = None
        self.finished_at = None
        self.last_heartbeat = None
        self.dry_run = None
        self.target_status = None
        self.max_records = None
        self.max_pages = None
        self.results_file = None
        self.log_file = None

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

    @_guard
    def provider_result(self, result):
        with self._lock:
            record = self._current_record()
            if record is None or not isinstance(result, dict):
                return
            record["provider_status"] = result.get("tracking_status")
            record["provider_eta"] = result.get("eta")
            record["provider_ata"] = result.get("ata")
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

    @_guard
    def view_updated(self, view_name, field_name, value):
        with self._lock:
            record = self._current_record()
            if record is not None:
                if field_name.upper() == "ETA":
                    record["coe_action"] = "{0} {1} → {2}".format(view_name, field_name, value)
                else:
                    record["bu_action"] = "{0} {1} → {2}".format(view_name, field_name, value)
                record["updated"] = _stamp()
            self.systems["hub"]["ops"] += 1
            self.systems["hub"]["last_success"] = _stamp()
            self._mark("ok", "{0} view {1} saved as {2}".format(view_name, field_name, value))
            self._touch()

    @_guard
    def shipment_finished(self, reference, outcome, details="", actions=None,
                          outcome_class=None, **kwargs):
        with self._lock:
            record = self._index.get(reference)
            if record is None:
                return
            outcome = (outcome or "").upper()
            record["state"] = {
                "SUCCESS": "updated",
                "SKIPPED": "skipped",
                "FAILED": "failed",
                "PARTIAL": "partial",
            }.get(outcome, "unknown")
            record["error"] = details or None
            # Named operational class from classify_failure(), e.g. NO RESULT.
            record["outcome"] = outcome_class or kwargs.get("outcome")
            record["updated"] = _stamp()
            record["duration_ms"] = int(
                (_now().timestamp() - record["started_epoch"]) * 1000
            )
            if isinstance(actions, dict):
                record["coe_action"] = actions.get("coe") or record["coe_action"]
                record["bu_action"] = actions.get("bu") or record["bu_action"]

            if outcome == "SUCCESS":
                record["step"] = "Complete"
                self._mark("ok", "{0} updated in Logistics Hub".format(reference))
            elif outcome == "SKIPPED":
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
            elif outcome == "PARTIAL":
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

    @_guard
    def counters(self, successful, failed, skipped, partial=None):
        with self._lock:
            self.successful = successful
            self.failed = failed
            self.skipped = skipped
            if partial is not None:
                self.partial = partial
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

    def snapshot(self, trim=False):
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

            return {
                "version": self.version,
                "generated_at": _stamp(now),
                "run": {
                    "status": self.run_status,
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
                "shipments": ([
                    (dict(record, steps=[]) if record["reference"] != self.current_shipment
                     else record)
                    for record in self.shipments
                ] if trim else list(self.shipments)),
                "logs": (list(self.logs)[:self.WIRE_LOGS] if trim
                         else list(self.logs)),
                "log_total": len(self.logs),
                "exceptions": list(self.exceptions),
                "timeline": (list(self.timeline)[:self.WIRE_TIMELINE] if trim
                             else list(self.timeline)),
            }


bridge = ControlTowerState()
