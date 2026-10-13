"""Phase 1: carrier pacing, first-block stop, deferral, queue persistence,
ETA/ATA separation and write/read-back at the Hub boundary."""
import inspect
import json
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import carrier_pacing as C

PASS, FAIL = [], []
DEFAULT_HELD = C.HELD
C.HELD = frozenset()        # generic tests use CMA_CGM as their ocean example


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, " - " + str(detail) if detail else ""))


class Clock(object):
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 1))
        self.t += s


tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "carrier_deferred.json")
SHIP = {"bol_awb": "CMAU1234567", "carrier": "CMA CGM", "tracking_reference": "CMAU1234567"}


def gate(clock, run="run-1", q=None):
    q = q or C.DeferredQueue(path, clock=clock)
    return C.CarrierGate(q, run, is_ocean=lambda p: p == "CMA_CGM", clock=clock,
                         sleep=clock.sleep), q


print("Rate limiting")
ck = Clock()
g, q = gate(ck)
with g.lookup("CMA_CGM"):
    ck.t += 10                                 # the lookup took 10 s
with g.lookup("CMA_CGM"):
    pass
check("Same ocean carrier: waits the rest of 45 s after the previous lookup ended",
      ck.slept == [45.0], ck.slept)
with g.lookup("DHL"):
    pass
check("Another carrier is not held up by CMA CGM's spacing", ck.slept == [45.0], ck.slept)
ck.t += 100
with g.lookup("CMA_CGM"):
    pass
check("No wait once the gap has already passed", ck.slept == [45.0], ck.slept)

print("One lookup at a time")
g2, _ = C.CarrierGate(C.DeferredQueue(os.path.join(tmp, "x.json")), "r",
                      clock=time.monotonic, sleep=time.sleep), None
inside, peak = [0], [0]


def work():
    with g2.lookup("P{0}".format(threading.get_ident() % 7)):
        inside[0] += 1
        peak[0] = max(peak[0], inside[0])
        time.sleep(0.05)
        inside[0] -= 1


threads = [threading.Thread(target=work) for _ in range(5)]
[t.start() for t in threads]
[t.join() for t in threads]
check("Never two lookups at once", peak[0] == 1, peak[0])

print("Per-run cap")
ck = Clock()
g, q = gate(ck)
C.MAX_PER_RUN["ocean"], saved = 2, C.MAX_PER_RUN["ocean"]
for _ in range(2):
    with g.lookup("CMA_CGM"):
        pass
r = g.refusal("CMA_CGM")
check("After the cap, the carrier is refused for this run", r and r[0] == C.REASON_CAP, r)
C.MAX_PER_RUN["ocean"] = saved

print("Carrier on hold")
C.HELD = DEFAULT_HELD
check("CMA CGM is on hold by default", "CMA_CGM" in C.HELD)
ck = Clock()
g, q = gate(ck)
r = g.refusal("CMA_CGM")
check("A held carrier is never looked up: deferred for a person", r and r[0] == C.REASON_HELD, r)
check("Other carriers are not affected by the hold", g.refusal("MSC") is None)
held = C.HELD
C.HELD = frozenset()
check("Releasing the hold (CARRIER_HOLD=\"\") lets it be asked again", g.refusal("CMA_CGM") is None)

print("First block stops the carrier")
os.remove(path) if os.path.exists(path) else None
ck = Clock()
g, q = gate(ck)
check("Before a block the carrier is asked", g.refusal("CMA_CGM") is None)
g.blocked("CMA_CGM", "Access is temporarily restricted")
r = g.refusal("CMA_CGM")
check("After the first block it is not asked again in this run",
      r and r[0] == C.REASON_BLOCKED, r)
check("Other carriers carry on", g.refusal("MSC") is None)
ck.t += 3600
g_next, _ = gate(ck, run="run-2")
r = g_next.refusal("CMA_CGM")
check("A later run inside the cooldown leaves it alone too", r and r[0] == C.REASON_COOLDOWN, r)
ck.t += C.COOLDOWN_SECONDS
g_later, _ = gate(ck, run="run-3")
check("After the cooldown a later run asks again", g_later.refusal("CMA_CGM") is None)

print("Deferral and queue persistence")
os.remove(path)
ck = Clock()
q = C.DeferredQueue(path, clock=ck)
q.defer(SHIP, "CMA_CGM", "CARRIER_ACCESS_RESTRICTED", "restriction page", "run-1")
q2 = C.DeferredQueue(path, clock=ck)             # a new run reads the file
rows = q2.pending()
need = ("bol_awb", "carrier", "provider", "reason", "detail", "deferred_at", "run_id")
check("Deferred shipment survives into the next run", len(rows) == 1)
check("It carries identity, carrier, reason, time and run ID",
      rows and all(rows[0].get(k) for k in need), {k: rows[0].get(k) for k in need})
q2.defer(SHIP, "CMA_CGM", C.REASON_COOLDOWN, "still cooling down", "run-2")
check("Deferring again counts the attempt, keeps one record",
      len(q2.pending()) == 1 and q2.pending()[0]["attempts"] == 2)
check("Resolved when a later run reaches the carrier",
      q2.resolve("CMAU1234567", "CMA_CGM", "SUCCESS", "run-3") and not q2.pending())
check("Resolution and history kept on disk",
      json.load(open(path))["shipments"]["CMA_CGM|CMAU1234567"]["resolved_run_id"] == "run-3")
try:
    q2.defer({"carrier": "X"}, "X", "R", "", "run")
    check("A record without Hub identity is refused", False)
except ValueError:
    check("A record without Hub identity is refused", True)
open(path, "w").write("{broken")
q3 = C.DeferredQueue(path)
check("A damaged file is set aside, not silently overwritten",
      os.path.exists(path + ".damaged") and q3.pending() == [])

print("ETA / ATA separation at the Hub boundary")
import update_eta as A
logs = []
A.write_log = logs.append
r, n = A.separate_eta_ata({"eta": "12/07/2026", "ata": "12/07/2026"})
check("ETA equal to ATA: only the ATA is written", (r["eta"], r["ata"]) == (None, "12/07/2026"), n)
r, n = A.separate_eta_ata({"ata": "15/07/2026", "ata_source": "Estimated time of arrival"})
check("An ATA taken from an estimate is dropped", r["ata"] is None, n)
r, n = A.separate_eta_ata({"eta": "20/07/2026", "ata": "12/07/2026",
                           "ata_source": "Discharged from vessel"})
check("Distinct ETA and real ATA both kept", (r["eta"], r["ata"]) == ("20/07/2026", "12/07/2026"))

print("Write / read-back at the Hub boundary")
calls = []
fail_ata = [True]


def fake_view(page, shipment, view, field, value, return_to_table=True, require_verified=False):
    calls.append((field, value, require_verified))
    if field == "ATA" and fail_ata[0]:
        raise A.WriteUnverified("read-back shows a different value", {"category": "WRITE_UNVERIFIED"})
    return "{0} updated with {1}".format(field, value)


real_view = A.update_one_view
A.update_one_view = fake_view
try:
    A.update_internal_shipment(None, SHIP, {"eta": "20/07/2026", "ata": "12/07/2026",
                                            "read_back_required": True})
    check("A failed read-back is never reported as success", False)
except A.WriteUnverified as error:
    check("A failed read-back is never reported as success", True)
    check("The ETA already written is carried out with the failure (PARTIAL, truthful)",
          "updated with" in (getattr(error, "actions", {}) or {}).get("coe", ""))
check("Read-back is required for every field written",
      [c[2] for c in calls] == [True, True], calls)
calls.clear()
fail_ata[0] = False
A.update_internal_shipment(None, SHIP, {"eta": "12/07/2026", "ata": "12/07/2026",
                                        "read_back_required": True})
check("ETA = ATA reaches the Hub as the ATA only", [c[0] for c in calls] == ["ATA"], calls)
A.update_one_view = real_view

print("Wiring in the run loop")
src = inspect.getsource(A.main)
check("Every lookup goes through the gate",
      "with _gate.lookup(_provider):\n                        dhl_result = get_provider_result" in src)
check("A refused carrier is deferred before any lookup",
      src.index("_gate.refusal(_provider)") < src.index("with _gate.lookup(_provider)"))
check("A restriction page stops the carrier and defers the shipment",
      "_gate.blocked(_provider" in src and '"CARRIER_ACCESS_RESTRICTED"' in src)
check("CAPTCHA stays human-in-the-loop (queue flow untouched)",
      'getattr(error, "reason", "") == "queued"' in src)
check("Deferred is a skip with nothing written, not a failure",
      issubclass(A.CarrierDeferred, A.SkipShipment))

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
