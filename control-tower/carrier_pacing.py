"""
Carrier pacing and deferral: ask each carrier politely, and stop asking a
carrier the moment it blocks us.

    one at a time    a lookup holds the gate's lock; a second lookup waits
    spacing          at least MIN_GAP seconds between the end of one lookup
                     and the start of the next ON THE SAME CARRIER
    cap              at most MAX_PER_RUN lookups per carrier per run
    first block      a carrier that shows its restriction page is not asked
                     again in this run, nor in later runs until COOLDOWN has
                     passed. Its remaining shipments are DEFERRED.
    deferred queue   every deferred or blocked shipment is kept on disk with
                     its identity, carrier, reason, time and run ID, until a
                     later run reaches the carrier and resolves it.

Nothing here touches a page, a CAPTCHA or the Hub. A block is never worked
around: the only response to one is to stop asking and wait.

    python carrier_pacing.py [file]     list the deferred queue
"""

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


# No wait between lookups by default (the operator's decision, 14 Oct: the
# waits slowed every run). Set CARRIER_MIN_GAP_OCEAN / _OTHER to add one.
MIN_GAP = {"ocean": _env_float("CARRIER_MIN_GAP_OCEAN", 0),
           "other": _env_float("CARRIER_MIN_GAP_OTHER", 0)}
MAX_PER_RUN = {"ocean": int(_env_float("CARRIER_MAX_PER_RUN_OCEAN", 30)),
               "other": int(_env_float("CARRIER_MAX_PER_RUN_OTHER", 150))}
COOLDOWN_SECONDS = _env_float("CARRIER_BLOCK_COOLDOWN_HOURS", 12) * 3600

# Beside the run logs (update_eta.LOG_FOLDER).
DEFAULT_PATH = Path(r"C:\Automation") / "logs" / "carrier_deferred.json"

# Carriers ON HOLD: kept in the automation (configuration, code, history) but
# never looked up. Their shipments go straight to the deferred queue for a
# person. CMA CGM is held until a permitted channel (API or alert emails) is
# in place: it blocked access even after a completed human verification.
#
# The production hold is fixed here in the code, so it survives every restart
# and no missing, empty or mistyped setting can lift it:
#   CARRIER_HOLD=X,Y          ADDS carriers to the hold (never removes one)
#   CARRIER_HOLD_RELEASE=X    lifts a production hold, only by naming the
#                             carrier exactly; the run log says so each run
PRODUCTION_HOLD = frozenset({"CMA_CGM"})


def _names(value):
    return frozenset(p.strip().upper() for p in str(value or "").split(",") if p.strip())


def held_carriers(environ=None):
    """The hold in effect, read fresh (a restart reads the same code)."""
    env = os.environ if environ is None else environ
    released = _names(env.get("CARRIER_HOLD_RELEASE")) & PRODUCTION_HOLD
    return (PRODUCTION_HOLD - released) | _names(env.get("CARRIER_HOLD"))


def hold_report(environ=None):
    env = os.environ if environ is None else environ
    released = sorted(_names(env.get("CARRIER_HOLD_RELEASE")) & PRODUCTION_HOLD)
    line = "Carrier hold in effect: {0}.".format(", ".join(sorted(held_carriers(env))) or "none")
    if released:
        line += " RELEASED by CARRIER_HOLD_RELEASE: {0}.".format(", ".join(released))
    return line

DEFERRED, RESOLVED = "DEFERRED", "RESOLVED"
REASON_HELD = "CARRIER_ON_HOLD"
REASON_BLOCKED = "CARRIER_BLOCKED_THIS_RUN"
REASON_COOLDOWN = "CARRIER_COOLING_DOWN"
REASON_CAP = "CARRIER_RUN_CAP_REACHED"


def _fmt(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


class DeferredQueue(object):
    """
    The on-disk book of deferred shipments and blocked carriers. Every change
    is written at once (temp file, then an atomic replace), so a crash never
    leaves half a file and nothing deferred is lost between runs.
    """

    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("shipments", {})
                data.setdefault("carriers", {})
                return data
        except FileNotFoundError:
            pass
        except Exception:
            # A damaged file is kept aside, never silently overwritten.
            try:
                os.replace(str(self.path), str(self.path) + ".damaged")
            except Exception:
                pass
        return {"shipments": {}, "carriers": {}}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(self.path))

    @staticmethod
    def _key(bol_awb, provider):
        return "{0}|{1}".format(str(provider or "").upper(), str(bol_awb or "").strip())

    def defer(self, shipment, provider, reason, detail, run_id):
        """Record (or update) a deferred shipment. -> the record."""
        bol = str(shipment.get("bol_awb") or "").strip()
        if not bol:
            raise ValueError("a deferred shipment needs its Hub identity (bol_awb)")
        with self._lock:
            now = self.clock()
            key = self._key(bol, provider)
            rec = self._data["shipments"].get(key) or {
                "bol_awb": bol, "provider": provider, "attempts": 0,
                "first_deferred_at": _fmt(now), "history": []}
            rec.update(
                tracking_reference=shipment.get("tracking_reference") or bol,
                carrier=shipment.get("carrier"), status=DEFERRED, reason=reason,
                detail=str(detail or "")[:300], run_id=run_id, deferred_at=_fmt(now),
                deferred_epoch=now, attempts=int(rec.get("attempts") or 0) + 1)
            rec["history"] = (rec.get("history") or [])[-19:] + [
                {"time": _fmt(now), "run_id": run_id, "status": DEFERRED, "reason": reason}]
            self._data["shipments"][key] = rec
            self._save()
            return dict(rec)

    def resolve(self, bol_awb, provider, outcome, run_id):
        """A later run reached the carrier for this shipment. -> True if it was queued."""
        with self._lock:
            rec = self._data["shipments"].get(self._key(bol_awb, provider))
            if not rec or rec.get("status") != DEFERRED:
                return False
            now = self.clock()
            rec.update(status=RESOLVED, resolved_at=_fmt(now), resolved_run_id=run_id,
                       resolution=outcome)
            rec["history"] = (rec.get("history") or [])[-19:] + [
                {"time": _fmt(now), "run_id": run_id, "status": RESOLVED, "reason": outcome}]
            self._save()
            return True

    def pending(self):
        with self._lock:
            return [dict(r) for r in self._data["shipments"].values()
                    if r.get("status") == DEFERRED]

    def record_block(self, provider, reason, run_id):
        with self._lock:
            now = self.clock()
            self._data["carriers"][str(provider).upper()] = {
                "blocked_at": _fmt(now), "blocked_epoch": now, "reason": str(reason)[:300],
                "run_id": run_id}
            self._save()

    def cooling_down(self, provider):
        """The carrier's last block, if it is still within the cooldown."""
        with self._lock:
            block = self._data["carriers"].get(str(provider).upper())
            if block and self.clock() - float(block.get("blocked_epoch") or 0) < COOLDOWN_SECONDS:
                return dict(block)
            return None


class CarrierGate(object):
    """Per-run pacing for every carrier. One instance per run."""

    def __init__(self, queue, run_id, is_ocean=lambda provider: False,
                 clock=time.monotonic, sleep=time.sleep, log=lambda message: None,
                 held=None):
        self.queue, self.run_id = queue, run_id
        # held=None: the production hold. Tests pass their own set.
        self.held = held_carriers() if held is None else frozenset(held)
        log(hold_report() if held is None else
            "Carrier hold (TEST configuration): {0}.".format(", ".join(sorted(self.held)) or "none"))
        self.is_ocean, self.clock, self.sleep, self.log = is_ocean, clock, sleep, log
        self._busy = threading.Lock()          # one lookup at a time
        self._last_end = {}
        self._count = {}
        self._blocked = {}

    def _kind(self, provider):
        return "ocean" if self.is_ocean(provider) else "other"

    def refusal(self, provider):
        """Why this carrier must not be asked now: (reason, detail) or None."""
        p = str(provider or "").upper()
        if p in self.held:
            return REASON_HELD, "{0} is on hold: not looked up by the automation until a " \
                                "permitted channel is in place; a person checks it.".format(p)
        if p in self._blocked:
            return REASON_BLOCKED, "{0} blocked access earlier in this run ({1}); not asked " \
                                   "again until a later run.".format(p, self._blocked[p])
        cooling = self.queue.cooling_down(p)
        if cooling:
            return REASON_COOLDOWN, "{0} blocked access at {1}; it is left alone for {2:g} h " \
                                    "after a block.".format(p, cooling["blocked_at"],
                                                            COOLDOWN_SECONDS / 3600)
        cap = MAX_PER_RUN[self._kind(p)]
        if self._count.get(p, 0) >= cap:
            return REASON_CAP, "{0} has had its {1} lookups for this run.".format(p, cap)
        return None

    def lookup(self, provider):
        """Context manager around ONE carrier lookup: waits its turn and spacing."""
        gate = self

        class _Turn(object):
            def __enter__(self_):
                gate._busy.acquire()
                p = str(provider or "").upper()
                gap = MIN_GAP[gate._kind(p)]
                last = gate._last_end.get(p)
                if last is not None:
                    wait = gap - (gate.clock() - last)
                    if wait > 0:
                        gate.log("Pacing {0}: waiting {1:.0f}s before the next lookup."
                                 .format(p, wait))
                        gate.sleep(wait)
                gate._count[p] = gate._count.get(p, 0) + 1
                return self_

            def __exit__(self_, *exc):
                gate._last_end[str(provider or "").upper()] = gate.clock()
                gate._busy.release()
                return False

        return _Turn()

    def blocked(self, provider, detail):
        """The carrier showed its restriction page: stop asking it."""
        p = str(provider or "").upper()
        self._blocked[p] = str(detail or "restriction page")[:160]
        self.queue.record_block(p, detail, self.run_id)
        self.log("CARRIER BLOCK: {0} is not asked again in this run; its remaining "
                 "shipments are deferred.".format(p))


class RunLimits(object):
    """
    The run's two hard limits, counted at the source:
      shipments  every shipment STARTED (counted before its lookup, whatever
                 it ends as: success, failure, skip, review, parked, partial)
      writes     every Hub field SAVED (counted the moment the save is
                 clicked, before the read-back, so a write that later fails
                 verification or a partial shipment still counts)
    A limit of 0 or less means no shipment / no write at all.
    """

    def __init__(self, max_shipments, max_writes):
        self.max_shipments, self.max_writes = int(max_shipments), int(max_writes)
        self.started, self.writes = 0, 0
        self.started_refs, self.written = [], []
        self._lock = threading.Lock()

    def exhausted(self):
        """Why no further shipment may start, or None."""
        if self.started >= self.max_shipments:
            return "the run's shipment limit ({0}) is reached".format(self.max_shipments)
        if self.writes >= self.max_writes:
            return "the run's Hub write limit ({0}) is reached".format(self.max_writes)
        return None

    def begin_shipment(self, reference):
        """Count a shipment as started. -> None, or why it may not start."""
        with self._lock:
            why = self.exhausted()
            if why:
                return why
            self.started += 1
            self.started_refs.append(reference)
            return None

    def write_refusal(self):
        """Why the next Hub field may not be written, or None."""
        if self.writes >= self.max_writes:
            return "the run's Hub write limit ({0}) is reached".format(self.max_writes)
        return None

    def record_write(self, reference, view, field, value):
        with self._lock:
            self.writes += 1
            self.written.append((reference, view, field, value))


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    queue = DeferredQueue(path)
    rows = queue.pending()
    print("{0} deferred shipment(s) in {1}".format(len(rows), path))
    for r in sorted(rows, key=lambda r: r.get("deferred_epoch") or 0):
        print("  {deferred_at}  {provider:<10} {bol_awb:<22} run {run_id}  {reason}: {detail}"
              .format(**{k: r.get(k) or "" for k in ("deferred_at", "provider", "bol_awb",
                                                       "run_id", "reason", "detail")}))
    for p, b in sorted(queue._data["carriers"].items()):
        print("  carrier {0} last blocked {1} (run {2})".format(p, b["blocked_at"], b["run_id"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
