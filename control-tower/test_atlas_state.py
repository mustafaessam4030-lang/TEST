"""
ATLAS Adaptive State Engine + Potato Garden (intelligence/atlas_state.py).

    python test_atlas_state.py

Covers: transitions and thresholds, verification (never success because a call
returned), duplicate events, the dwell against flicker, persistence and the
origin guard, configurable messages, safety (the run behaves identically with
or without the engine; Human Action outranks it; a broken engine cannot break
a run), authentication and the absence of any write route, the chat answer,
and the dashboard rendering. TEST DATA only: temporary store folders, nothing
real is read or written.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_state_intel_")
os.environ.pop("ATLAS_DATA_ORIGIN", None)

from intelligence import atlas_state as A          # noqa: E402
from intelligence import store                     # noqa: E402
from dashboard.bridge import ControlTowerState     # noqa: E402
from dashboard import assistant                    # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "" if condition else "  ({0})".format(detail)))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP  {0}  ({1})".format(name, why))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


class Clock(object):
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def engine(**config):
    clock = Clock()
    return A.StateEngine(config, clock=clock), clock


# ═════════════════════════════════════════════════════════════════════════
rule("1. TRANSITIONS — deterministic, from real events, every one recorded")
e, c = engine()
check("A new engine is IDLE", e.state == "IDLE")
t = e.observe("run_started", run_id="R1")
check("A run starting makes it FOCUSED", e.state == "FOCUSED" and t["to"] == "FOCUSED")
check("Every transition records from, to, time, trigger and reason",
      all(t.get(k) for k in ("from", "to", "at", "trigger", "reason")) and "epoch" in t, str(t))
e.observe("shipment_started", reference="X1")
e.observe("attempt_failed", key="a1", reference="X1", action="reload_page")
check("One failure is not frustration", e.state == "FOCUSED")
e.observe("attempt_failed", key="a2", reference="X1", action="reopen_manage")
check("Two failures in a row: FRUSTRATED", e.state == "FRUSTRATED", e.state)
e.observe("attempt_failed", key="a3", reference="X1", action="alternate_selector")
check("Three: RECOVERING", e.state == "RECOVERING", e.state)
check("...the reason says the run stops retrying it and moves on (what the automation does)",
      "moves on" in e.reason, e.reason)
check("...and the potato message is shown", "🥔" in (e.snapshot()["message"] or ""))
c.t += 100
e.observe("run_finished", status="finished")
check("The run finishing returns it to IDLE (after the dwell)", e.state == "IDLE", e.state)
check("Transitions are kept in order, newest first in the snapshot",
      [x["to"] for x in e.snapshot()["transitions"]][:4] ==
      ["IDLE", "RECOVERING", "FRUSTRATED", "FOCUSED"],
      str([x["to"] for x in e.snapshot()["transitions"]]))

# ═════════════════════════════════════════════════════════════════════════
rule("2. THRESHOLDS — configurable, validated")
e, c = engine(frustrated_after=3, recovering_after=5)
e.observe("run_started", run_id="R2")
for i in range(1, 6):
    e.observe("attempt_failed", key="b%d" % i, reference="X2")
    if i == 2:
        check("With frustrated_after=3, two failures stay FOCUSED", e.state == "FOCUSED")
    if i == 3:
        check("...three are FRUSTRATED", e.state == "FRUSTRATED")
    if i == 4:
        check("...four are still FRUSTRATED", e.state == "FRUSTRATED")
check("...and recovering_after=5 makes the fifth RECOVERING", e.state == "RECOVERING")
cfg = A.merged_config({"frustrated_after": -1, "recovering_after": "lots",
                       "min_dwell_s": 99999, "messages": {"potato": ["", 3]}})
check("Out-of-range or wrong-type settings are ignored, defaults kept",
      cfg["frustrated_after"] == 2 and cfg["recovering_after"] == 3 and
      cfg["min_dwell_s"] == 15 and cfg["messages"]["potato"] == A.DEFAULT_MESSAGES["potato"])
cfg = A.merged_config({"frustrated_after": 4, "recovering_after": 2})
check("recovering_after can never be below frustrated_after",
      cfg["recovering_after"] == 4, str(cfg["recovering_after"]))
e, _ = engine()
e.observe("run_started", run_id="R3")
e.observe("recovery_exhausted", key="x", reference="X3", error_class="TIMEOUT")
check("A recovery running out of options goes straight to RECOVERING",
      e.state == "RECOVERING" and e.trigger == "recovery_exhausted")
e.observe("shipment_finished", key="s", reference="X4", result="SKIPPED")
e.observe("shipment_finished", key="s2", reference="X5", result="PARTIAL")
check("SKIPPED and PARTIAL are not counted as failures", e.streak == 3, str(e.streak))

# ═════════════════════════════════════════════════════════════════════════
rule("3. VERIFICATION — success only when verified; learning only when recorded")
e, c = engine()
e.observe("run_started", run_id="R4")
e.observe("attempt_failed", key="c1", reference="Y1")
e.observe("attempt_failed", key="c2", reference="Y1")
e.observe("recovery_completed", key="c3", reference="Y1", verified=None)
check("A recovery that returned without error but was not verified counts for nothing",
      e.streak == 2 and e.state == "FRUSTRATED", "{0} {1}".format(e.streak, e.state))
e.observe("recovery_completed", key="c4", reference="Y1", verified=False)
check("...nor one contradicted by verification", e.streak == 2)
e.observe("shipment_finished", key="c5", reference="Y1", result="SUCCESS", verified=False)
check("A SUCCESS without a read-back is not a verified success", e.streak == 2)
c.t += 100
e.observe("recovery_completed", key="c6", reference="Y1", verified=True, action="reload_page")
check("A verified recovery resets the streak and calms down", e.streak == 0 and
      e.state == "FOCUSED", "{0} {1}".format(e.streak, e.state))
e.observe("learning_recorded", key="c7", reference="Y1", kind="recovery", verified=False,
          status="RECOVERED")
check("LEARNING is refused for an unverified recovery record", e.state == "FOCUSED")
e.observe("learning_recorded", key="c8", reference="Y1", kind="recovery", verified=True,
          status="EXHAUSTED")
check("...and for an exhausted one, even on a shipment later verified", e.state == "FOCUSED")
e.observe("learning_recorded", key="c9", reference="Y1", kind="shipment", verified=True,
          status="SUCCESS")
check("...and for a plain shipment record", e.state == "FOCUSED")
check("No verified recovery is claimed yet", e.snapshot()["latest_verified_recovery"] is None)
e.observe("learning_recorded", key="c10", reference="Y1", kind="recovery", verified=True,
          status="RECOVERED", action="reload_page", error_class="UNEXPECTED PAGE STATE")
lv = e.snapshot()["latest_verified_recovery"]
check("A verified recovery actually written to the store: LEARNING", e.state == "LEARNING")
check("...and it becomes the latest verified recovery, with its evidence",
      lv and lv["reference"] == "Y1" and lv["solution"] == "reload_page" and lv["verified"] is True
      and lv["evidence"], str(lv))

# ═════════════════════════════════════════════════════════════════════════
rule("4. DUPLICATES AND FLICKER")
e, c = engine()
e.observe("run_started", run_id="R5")
for _ in range(5):
    e.observe("attempt_failed", key="same", reference="Z1")
check("The same event five times counts once", e.streak == 1, str(e.streak))
e.observe("recovery_exhausted", key="z2", reference="Z1", error_class="TIMEOUT")
e.observe("shipment_finished", key="z3", reference="Z1", result="FAILED", error_class="FAILED")
e.observe("recovery_exhausted", key="z4", reference="Z1", error_class="OTHER")
check("One incident plants one potato, whatever reports it", e.garden["planted"] == 1,
      str(e.garden["planted"]))
n = len(e.transitions)
e.observe("attempt_failed", key="z5", reference="Z1")
check("Staying in the same state adds no transition", len(e.transitions) == n)
e, c = engine(min_dwell_s=30)
e.observe("run_started", run_id="R6")
e.observe("attempt_failed", key="d1", reference="Q1")
e.observe("attempt_failed", key="d2", reference="Q1")
check("Escalation shows at once", e.state == "FRUSTRATED")
c.t += 5
e.observe("recovery_completed", key="d3", reference="Q1", verified=True)
check("Calming down inside the dwell waits (no flicker)", e.state == "FRUSTRATED" and
      e.snapshot()["pending"]["to"] == "FOCUSED", e.state)
c.t += 30
check("...and applies once the dwell has passed", e.snapshot()["state"] == "FOCUSED")
e.observe("attempt_failed", key="d4", reference="Q2")
e.observe("attempt_failed", key="d5", reference="Q2")
check("...while escalating again is never delayed", e.state == "FRUSTRATED")

# ═════════════════════════════════════════════════════════════════════════
rule("5. THE GARDEN — stable ids, stages, harvest only on verification")
e, c = engine()
e.observe("run_started", run_id="G1")
e.observe("recovery_exhausted", key="g1", reference="P1", error_class="TIMEOUT")
plant = e.snapshot()["garden"]["current"]
check("A recovery incident plants a seed with an id and a creation time",
      plant and plant["id"] == A.plant_id("G1|P1") and plant["stage_name"] == "seed" and
      plant["planted_at"], str(plant))
e.observe("shipment_finished", key="g2", reference="P2", result="SUCCESS", verified=True)
check("A verified shipment waters it: one stage up", e.snapshot()["garden"]["current"]
      ["stage_name"] == "sprout")
e.observe("shipment_finished", key="g3", reference="P3", result="SUCCESS", verified=False)
check("An unverified one does not", e.snapshot()["garden"]["current"]["stage_name"] == "sprout")
e.observe("shipment_finished", key="g4", reference="P1", result="SUCCESS", verified=None)
check("The incident's shipment without a read-back is no harvest", e.garden["harvests"] == 0)
e.observe("shipment_finished", key="g5", reference="P1", result="SUCCESS", verified=True)
g = e.snapshot()["garden"]
check("Its shipment verified: harvested, one potato", g["harvests"] == 1 and
      g["plants"][0]["harvested_at"] and g["current"] is None, str(g))
check("The garden is labelled as gamification", "not an operational metric" in g["note"])

# ═════════════════════════════════════════════════════════════════════════
rule("6. PERSISTENCE — the store folder, behind the origin guard")
saved = {}
e1 = A.StateEngine(load=lambda: None, save=lambda d: saved.update(data=json.loads(json.dumps(d))))
e1.observe("run_started", run_id="S1")
e1.observe("recovery_exhausted", key="s1", reference="K1", error_class="TIMEOUT")
e2 = A.StateEngine(load=lambda: saved.get("data"))
check("A new engine (a new run, a restart) keeps the garden",
      e2.garden["planted"] == 1 and e2.garden["plants"][0]["reference"] == "K1")
check("...and the transition history", len(e2.transitions) == len(e1.transitions))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_state_store_")
b = ControlTowerState()
b.persist_atlas_state()
b.run_started(run_id="S2")
b.shipment_started({"bol_awb": "K2", "carrier": "QATAR", "provider": "QATAR"})
b.recovery_plan("TIMEOUT", "t", ["reload_page"], {}, True)
b.recovery_attempt(1, 1, "reload_page", None, "FAILED", False)
b.recovery_done(False, "exhausted")
path = Path(os.environ["ATLAS_INTEL_DIR"]) / A.STATE_FILE
on_disk = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
check("Through the bridge, the garden is written to the store folder",
      on_disk.get("garden", {}).get("planted") == 1, str(on_disk)[:120])
b2 = ControlTowerState()
b2.persist_atlas_state()
check("...and read back by the next bridge", b2.snapshot()["atlas_state"]["garden"]["planted"] == 1)
prod = tempfile.mkdtemp(prefix="ct_state_prod_")
Path(prod, "ORIGIN").write_text("production\n", encoding="utf-8")
os.environ["ATLAS_INTEL_DIR"] = prod
b3 = ControlTowerState()
b3.persist_atlas_state()
b3.run_started(run_id="S3")
b3.recovery_plan("TIMEOUT", "t", ["reload_page"], {}, True)
b3.recovery_done(False, "exhausted")
check("A test process never writes its garden into a production store",
      not (Path(prod) / A.STATE_FILE).exists())
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_state_intel2_")

# ═════════════════════════════════════════════════════════════════════════
rule("7. MESSAGES — configurable, never the same joke twice in a row")
e, c = engine()
shown = []
for i in range(6):
    e.observe("run_started", key="m%d" % i, run_id="M%d" % i)
    e.observe("recovery_exhausted", key="mx%d" % i, reference="R%d" % i)
    shown.append(e.snapshot()["message"])
check("Six recoveries: no message repeats back to back",
      all(shown[i] != shown[i + 1] for i in range(len(shown) - 1)), str(shown))
check("The signature line is among them", any("محتاج أزرع بطاطس" in (m or "") for m in shown))
e, _ = engine(messages={"potato": ["Custom line for {reference} 🥔"]})
e.observe("run_started", run_id="M9")
e.observe("recovery_exhausted", key="mm", reference="AWB9")
check("Messages come from configuration, with the reference filled in",
      e.snapshot()["message"] == "Custom line for AWB9 🥔", str(e.snapshot()["message"]))

# ═════════════════════════════════════════════════════════════════════════
rule("8. SAFETY — the run behaves the same; Human Action first; nothing breaks it")


def drive(b):
    b.run_started(run_id="SAFE")
    for ref, outcome in (("A1", "FAILED"), ("A2", "FAILED"), ("A3", "SUCCESS")):
        b.shipment_started({"bol_awb": ref, "carrier": "DHL", "provider": "DHL"})
        if outcome == "FAILED":
            b.recovery_plan("TIMEOUT", "t", ["reload_page", "alt"], {}, True)
            b.recovery_attempt(1, 2, "reload_page", None, "FAILED", False)
            b.recovery_attempt(2, 2, "alt", None, "FAILED", False)
            b.recovery_done(False, "exhausted")
        b.shipment_finished(ref, outcome, "timeout" if outcome == "FAILED" else "")
    return b.snapshot()


def operational(snap):
    drop = ("atlas_state", "version", "generated_at", "cold_version")
    clean = {k: v for k, v in snap.items() if k not in drop}
    text = json.dumps(clean, sort_keys=True, default=str)
    return text


with_engine = ControlTowerState()
without = ControlTowerState()
without.atlas_state = None
s1, s2 = drive(with_engine), drive(without)
check("The run's state is the same with or without the engine (counters, records, work list, "
      "retry queue)", json.loads(operational(s1))["counters"] == json.loads(operational(s2))
      ["counters"] and s1["retry_queue"] == s2["retry_queue"] and
      [r["state"] for r in s1["shipments"]] == [r["state"] for r in s2["shipments"]])
check("...and the engine really was in Potato Mode during it",
      any(t["to"] == "RECOVERING" for t in s1["atlas_state"]["transitions"]))
check("Without an engine the snapshot simply has no state", s2["atlas_state"] is None)


class Broken(object):
    def observe(self, *a, **k):
        raise RuntimeError("boom")

    def set_human(self, *a):
        raise RuntimeError("boom")

    def snapshot(self):
        raise RuntimeError("boom")


broken = ControlTowerState()
broken.atlas_state = Broken()
s3 = drive(broken)
check("An engine that raises cannot break a run: every shipment still closes",
      [r["state"] for r in s3["shipments"]] == [r["state"] for r in s2["shipments"]] and
      s3["atlas_state"] is None)
h = ControlTowerState()
drive(h)
h.run_started(run_id="H1")
h.shipment_started({"bol_awb": "H9", "carrier": "AFKL", "provider": "AFKL"})
h.recovery_plan("TIMEOUT", "t", ["reload_page"], {}, True)
h.recovery_done(False, "exhausted")
h.human_action_opened({"action_id": "act1", "reference": "H9", "carrier": "AFKL",
                       "step": "verify", "reason": "captcha"})
snap = h.snapshot()
st = snap["atlas_state"]
check("A waiting Human Action outranks Potato Mode: priority set, joke withheld",
      st["priority"] == "human_action" and "🥔" not in (st["message"] or "") and
      "Human Action" in st["message"], str(st["message"]))
check("...and the Human Action itself is untouched and waiting",
      snap["human_action"] and snap["human_action"].get("waiting") is True)
src = (HERE / "intelligence" / "atlas_state.py").read_text(encoding="utf-8")
check("The engine imports nothing that can act: no browser, network, mail or automation code",
      not any(w in src for w in ("playwright", "urllib", "requests", "smtplib", "update_eta",
                                 "subprocess", "import os")))

# ═════════════════════════════════════════════════════════════════════════
rule("9. LEARNING THROUGH THE BRIDGE — only what the store actually wrote")


class FakeEvents(object):
    def __init__(self, ok):
        self.ok, self.rows = ok, []

    def record(self, kind, **fields):
        self.rows.append(dict(fields, kind=kind))
        return self.ok


def learn(ok):
    b = ControlTowerState()
    b.intel = FakeEvents(ok)
    b.run_started(run_id="L1")
    b.shipment_started({"bol_awb": "L9", "carrier": "QATAR", "provider": "QATAR"})
    b.recovery_plan("TIMEOUT", "t", ["reload_page"], {}, True)
    b.recovery_attempt(1, 1, "reload_page", None, "SUCCESS", True)
    b.recovery_done(True, "found", verified=True)
    b.view_updated("COE", "ETA", "24/08/2026", verified=True)
    b.shipment_finished("L9", "SUCCESS", "")
    return b, b.snapshot()["atlas_state"]


b, st = learn(True)
check("Verified recovery, shipment read back, record written: LEARNING",
      st["state"] == "LEARNING" and st["latest_verified_recovery"]["solution"] == "reload_page",
      st["state"])
b, st = learn(False)
check("The same, but the store refused the write: no LEARNING, no claim",
      st["state"] != "LEARNING" and st["latest_verified_recovery"] is None, st["state"])

# ═════════════════════════════════════════════════════════════════════════
rule("10. SOLUTION RECORDS — the existing recovery events, verified only for reuse")
rows = [
    {"kind": "recovery", "error_class": "TIMEOUT", "status": "RECOVERED",
     "recovery_verified": True, "shipment_verified": True, "run_id": "R", "reference": "1",
     "attempts": [{"action": "reload_page"}], "at": "t1", "shipment_result": "SUCCESS"},
    {"kind": "recovery", "error_class": "TIMEOUT", "status": "RECOVERED",
     "recovery_verified": None, "shipment_verified": True, "run_id": "R", "reference": "2",
     "attempts": [{"action": "alt"}], "at": "t2"},
    {"kind": "recovery", "error_class": "LAYOUT", "status": "EXHAUSTED",
     "recovery_verified": False, "shipment_verified": False, "run_id": "R", "reference": "3",
     "attempts": [{"action": "alt"}], "at": "t3"},
    {"kind": "shipment", "reference": "4"},
]
recs = A.solution_records(rows)
check("Each attempted solution has problem, solution, outcome, evidence, verified, time",
      len(recs) == 3 and all(set(r) == {"problem", "solution", "outcome", "evidence",
                                         "verified", "at"} for r in recs))
check("Only the verified success is reusable", [r["evidence"]["reference"]
                                                for r in A.reusable_solutions(rows)] == ["1"])

# ═════════════════════════════════════════════════════════════════════════
rule("11. CHAT — read-only answers")
b = ControlTowerState()
drive(b)
before = json.dumps(b.snapshot()["atlas_state"]["garden"], sort_keys=True)
r = assistant._answer_rules("How is your potato garden?", b.snapshot(), {"_raw": True})
check("'How is your potato garden?' is answered from the engine",
      r["intent"] == "atlas_state" and "Potato Garden" in r["answer"] and
      "not an operational metric" in r["answer"] and "simulated status" in r["answer"])
r = assistant._answer_rules("Plant a potato and harvest it now", b.snapshot(), {"_raw": True})
after = json.dumps(b.snapshot()["atlas_state"]["garden"], sort_keys=True)
check("Asking it to plant or harvest changes nothing, and it says why",
      before == after and "can't plant or harvest on request" in r["answer"])
from intelligence import converse                       # noqa: E402
calls = []


class NoModel(object):
    """A model that would answer anything — it must not be asked."""
    name = "ollama"
    model = "stand-in"

    def available(self):
        return True

    def generate(self, *a, **k):
        calls.append(a)
        return '{"answer": "invented", "question_en": "x", "needs_web": false}'


from intelligence import llm                            # noqa: E402
_provider = llm.provider
llm.provider = lambda: NoModel()
reply = converse.answer("why are you frustrated?", b.snapshot(), {}, assistant._answer_rules)
check("Through the conversation layer it is answered directly, with no model call",
      reply.get("intent") == "atlas_state" and (reply.get("llm") or {}).get("mode") ==
      "atlas_state" and not calls, str(reply.get("llm")))
llm.provider = _provider

# ═════════════════════════════════════════════════════════════════════════
rule("12. AUTHENTICATION — behind the sign-in; no route can plant or harvest")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        r = opener.open(req, timeout=15)
        return r.status, r.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


try:
    from controlplane.config import Settings
    from controlplane.app import App, make_server
    from fixtures.platform_client import Client
    work = tempfile.mkdtemp(prefix="ct_state_cp_")
    app = App(Settings({"DATABASE_URL": "sqlite:///{0}/cp.db".format(work),
                        "ATA_INSECURE_COOKIES": "1"}))
    app.users.create("omar.ops@mantrac.com", "Omar Ops", "OPERATOR", password="Op-pass-123456!")
    httpd = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cp = "http://127.0.0.1:{0}".format(httpd.server_address[1])
    s, _ = call(cp + "/api/state")
    check("Remote platform: the state (with ATLAS's state in it) needs a sign-in", s == 401, str(s))
    op = Client(cp)
    s, _d, _h = op.login("omar.ops@mantrac.com", "Op-pass-123456!")
    codes = [op.call("POST", path, {"reference": "X"})[0]
             for path in ("/api/atlas/garden", "/api/atlas/state", "/api/atlas/plant",
                          "/api/atlas/harvest")]
    check("...and a signed-in operator finds no route to plant, harvest or set a state",
          s == 200 and all(code in (403, 404, 405) for code in codes), str(codes))
except Exception as error:
    skip("Remote platform authentication", "{0}: {1}".format(type(error).__name__, error))

# ═════════════════════════════════════════════════════════════════════════
rule("13. DASHBOARD — the state card, the garden and the chat header render")
from dashboard import server as tower_server             # noqa: E402
from dashboard.bridge import bridge as B                 # noqa: E402
B.run_started(run_id="UI-TEST")
for ref, outcome in (("UI1", "SUCCESS"), ("UI2", "FAILED")):
    B.shipment_started({"bol_awb": ref, "carrier": "QATAR AIRWAYS", "provider": "QATAR"})
    if outcome == "FAILED":
        B.recovery_plan("UNEXPECTED PAGE STATE", "t", ["reload_page", "alt"], {}, True)
        B.recovery_attempt(1, 2, "reload_page", None, "FAILED", False)
        B.recovery_attempt(2, 2, "alt", None, "FAILED", False)
        B.recovery_done(False, "exhausted")
    else:
        B.view_updated("COE", "ETA", "24/08/2026", verified=True)
    B.shipment_finished(ref, outcome, "" if outcome == "SUCCESS" else "no button")
PORT = free_port()
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1")
BASE = "http://127.0.0.1:%d/" % PORT
for _ in range(50):
    try:
        opener.open(BASE + "api/state", timeout=1)
        break
    except Exception:
        time.sleep(0.1)
s, raw = call(BASE + "api/state")
st = (json.loads(raw or b"{}") or {}).get("atlas_state") or {}
check("The dashboard's /api/state carries ATLAS's state", s == 200 and st.get("state") ==
      "RECOVERING", "{0} {1}".format(s, st.get("state")))
codes = [call(BASE + p, "POST", b"{}", {"Content-Type": "application/json"})[0]
         for p in ("api/atlas/garden", "api/atlas/plant", "api/atlas/state")]
check("The local dashboard has no route to plant, harvest or set a state either",
      all(code in (403, 404, 405) for code in codes), str(codes))
try:
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    options = [{"headless": True, "executable_path": str(p)} for p in sorted(
        Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))] + [
        {"headless": True}, {"headless": True, "channel": "msedge"}]
    browser = None
    for option in options:
        try:
            browser = pw.chromium.launch(**option)
            break
        except Exception:
            continue
    if browser is None:
        raise RuntimeError("no browser")
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.add_init_script("sessionStorage.setItem('ct-intro','1')")
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(BASE)
    page.wait_for_function("() => typeof S !== 'undefined' && S && S.atlas_state", timeout=15000)
    page.evaluate("() => { const b = document.querySelector('[data-nav=\"atlas\"]'); if (b) b.click(); }")
    page.wait_for_selector("#asCard:not([hidden])", timeout=10000)
    check("The ATLAS page shows the state card with the state and why",
          page.inner_text("#asPillT").lower() == "recovering" and
          "why" in page.inner_text("#asBody").lower(),
          page.inner_text("#asPillT"))
    check("...the potato message", "🥔" in page.inner_text("#asBody"))
    check("...and the garden, labelled as gamification, with the planted potato",
          "not an operational metric" in page.inner_text("#pgBody") and
          page.locator("#pgBody .pg-p").count() == 1)
    check("The chat header carries the compact state",
          page.evaluate("document.getElementById('chMood').textContent") == "Recovering" and
          not page.evaluate("document.getElementById('chMood').hidden"))
    m = browser.new_page(viewport={"width": 390, "height": 844})
    m.add_init_script("sessionStorage.setItem('ct-intro','1')")
    m.goto(BASE)
    m.wait_for_function("() => typeof S !== 'undefined' && S && S.atlas_state", timeout=15000)
    m.evaluate("() => { const b = document.querySelector('[data-nav=\"atlas\"]'); if (b) b.click(); }")
    m.wait_for_selector("#asCard:not([hidden])", timeout=10000)
    box = m.locator("#asCard").bounding_box()
    check("On a phone the card fits the screen", box and box["x"] >= 0 and
          box["x"] + box["width"] <= 391, str(box))
    check("No script errors on the page", not errors, str(errors[:2]))
    browser.close()
    pw.stop()
except Exception as error:
    skip("Dashboard rendering", "{0}: {1}".format(type(error).__name__, str(error)[:120]))

print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(len(PASS), len(FAIL),
                                         ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
