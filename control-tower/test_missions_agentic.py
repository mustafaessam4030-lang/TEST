"""
ATLAS agentic missions (ATLAS_AGENTIC_AI_ARCHITECTURE_PLAN.md, stages S1-S9): one model
ledger, the tool catalogue, the planner (validated graphs, fan-out, plan editing,
deadlines, call caps, replanning), actions through the three existing engines with
per-action approval and read-back verification, Level 4 policies, memory and lessons,
web investigation, replay / handoff / notes / analytics, the API, To Do and Mission
Control. Every external system is a stand-in: a fake run control channel, a fake
escalation store, fake search, fake models. Nothing is billed, nothing live is touched.

    python test_missions_agentic.py
"""
import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="agentic_")
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "DASHBOARD_ACCOUNTS": "0", "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"),
                   "PO_DATA_DIR": os.path.join(TMP, "po"), "PO_AUTO": "0", "ATLAS_AI": "0",
                   "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key"), "ATLAS_HEALTH_MIN_SAMPLE": "5"})
os.environ.pop("ATLAS_MISSION_ACTIONS", None)
os.environ.pop("ATLAS_SEARCH_URL", None)
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:500]))


from dashboard import clearance as C  # noqa: E402


def row(bu, step, awb, overdue=None, agent="AGENT X", eta="2026-09-01", ata=None):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, awb=awb, overdue=overdue, agent=agent, eta=eta, ata=ata, mode="Air", status="InProgress",
             due="2026-10-01", ffw="Ceva")
    return r


ROWS = [row("EG", "Customs Declaration", "157-11111111", 45.0, ata="2026-09-06"),
        row("EG", "Customs Declaration", "157-22222222", 45.0, ata=None)] + \
       [row("EG", "Customs Declaration", "EG-%04d" % i, 20.0) for i in range(14)] + \
       [row("GH", "Assign Clearing Agent", "GH-0001", 9.0, agent=None)] + \
       [row("TZ", "Duty Payment" if i % 3 else "Customs Release", "TZ-%04d" % i, 35.0 + i, agent="AG%d" % (i % 2)) for i in range(40)]
DATA = {"asof": time.strftime("%Y-%m-%d"), "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS], "dq": {}}
C.load = lambda: DATA
ESC = []
C.escalations = lambda: list(ESC)

from intelligence.missions import actions, classify, db, engine, memory, models, plan, registry, specialists, tools  # noqa: E402
from intelligence import events as E  # noqa: E402

CALLS = []


class Fake(object):
    def __init__(self, name, fail=False):
        self.model, self.fail = name, fail

    def health(self):
        return {"ok": True, "detail": "ready"}

    def generate(self, system, prompt, timeout=None, json_mode=False, max_tokens=0, images=None):
        CALLS.append(self.model)
        if self.fail:
            raise RuntimeError("down")
        if "plan investigations" in system:
            return PLANNER["answer"]
        if "independent reviewer" in system:
            return '{"agree": true, "concerns": []}'
        if "other plausible causes" in system:
            return '{"causes": []}'
        return "The shipment is at its step and past its due date."


PLANNER = {"answer": '{"template": "made_up", "skip": []}'}
FAILING = set()
models.PROVIDERS["cortex"] = lambda mid: Fake(mid, fail=mid in FAILING)
models.PROVIDERS["ollama"] = Fake("local", fail=True)
# the ATA run's records: the carrier's ATA differs from eHub's for 157-11111111 (tracking out of sync)
STATE = {"s": {"run": {"run_id": "R1", "status": "idle"}, "shipments": [
    {"reference": "157-11111111", "carrier": "AFKL", "provider_ata": "2026-09-01", "provider_eta": "2026-08-30", "outcome": "SUCCESS",
     "verification": "VERIFIED"},
    {"reference": "157-22222222", "carrier": "AFKL", "provider_eta": "2026-09-01", "outcome": "SUCCESS", "verification": "VERIFIED"}]}}
engine.configure(state=lambda: STATE["s"], export=lambda: DATA)
RECS = C.records(DATA)
CTL = []
tools.configure(control=lambda action, ref: (CTL.append((action, ref)) or True, "{0} is queued.".format(ref)),
                escalate=lambda r, reason, by: _fake_escalate(r, reason, by), load=lambda: DATA)


def _fake_escalate(r, reason, by):
    if not r:
        return False, "not in the export", None
    if any(e["awb"] == r["awb"] and e["status"] == "open" for e in ESC):
        return False, "already open", None
    e = {"id": "esc%04d" % len(ESC), "awb": r["awb"], "bu": r["bu"], "status": "open", "reason": reason, "by": by}
    ESC.append(e)
    return True, "Escalated", e


MS = HERE / "intelligence" / "missions"
print("\n1. ONLY THREE ACTION ENGINES, ONLY IN THE CATALOGUE")
reasoning = "".join((MS / f).read_text(encoding="utf-8") for f in ("engine.py", "specialists.py", "classify.py", "plan.py", "memory.py", "registry.py"))
check("Specialists, the engine, the planner and memory never call an engine or the network themselves",
      not re.search(r"\.escalate\(|control\.request|urlopen|requests\.|webbrowser|playwright|selenium", reasoning))
check("The catalogue has exactly three action tools: reprocess, escalate, queue_human",
      sorted(k for k, v in tools.TOOLS.items() if v["kind"] == "act") == ["escalate", "queue_human", "reprocess"])
check("Each action tool needs its own permission (runs.start, clearance.escalate, missions.run)",
      tools.TOOLS["reprocess"]["permission"] == "runs.start" and tools.TOOLS["escalate"]["permission"] == "clearance.escalate"
      and tools.TOOLS["queue_human"]["permission"] == "missions.run")
check("No tool opens a browser, signs in or sends mail", not re.search(r"playwright|selenium|smtplib|send_mail|outlook", (MS / "tools.py").read_text()))
check("Actions are off unless ATLAS_MISSION_ACTIONS=1", not actions.enabled())

print("\n2. S1: ONE LEDGER")
from intelligence import llm  # noqa: E402
before = len(models.ledger(None, 10000))
chain = llm.ChainProvider(Fake("claude-sonnet-4-5"), Fake("local"))
chain.first.model = "claude-sonnet-4-5"
chain.generate("system", "a chat question")
rows = models.ledger(None, 10000)[before:]
check("A chat call through Cortex is one ledger row (purpose chat, no mission, prompt as a hash only)",
      len(rows) == 1 and rows[0]["purpose"] == "chat" and rows[0]["mission_id"] is None and rows[0]["prompt_sha256"]
      and "a chat question" not in json.dumps(rows[0]), rows)
models.QUIET.on = True
models.record_chat("local", "ollama", True, time.time(), "s", "p", "t")
models.QUIET.on = False
os.environ["ATLAS_ROUTER"] = "legacy"
models.record_chat("local", "ollama", True, time.time(), "s", "p", "t")
os.environ.pop("ATLAS_ROUTER")
check("A mission's own call is not counted twice; ATLAS_ROUTER=legacy stops chat rows", len(models.ledger(None, 10000)) == before + 1)

print("\n3. S2: THE TOOL CATALOGUE")
check("allowed() follows the person's permissions", tools.allowed("export_rows", lambda p: p == "clearance.view")
      and not tools.allowed("reprocess", lambda p: p == "clearance.view") and not tools.allowed("nope", lambda p: True))
tools.STATS.update(hits=0, misses=0)
a1 = tools.read("export_rows", ["EG"])
a2 = tools.read("export_rows", ["EG"])
check("The same read within 15 minutes is answered once and shared", a1 == a2 and tools.STATS["hits"] == 1 and tools.STATS["misses"] == 1)
try:
    tools.read("reprocess", "X")
    refused = False
except KeyError:
    refused = True
check("An action tool cannot be called as a read", refused)

print("\n4. S3: THE PLANNER")
for q, want in (("Compare the carriers: which carrier performs best? analyze", "carrier_compare"),
                ("Investigate all delayed shipments in TZ, why are they late", "multi_delay"),
                ("Why does the ETA differ between the carrier and eHub for 157-11111111? investigate", "eta_discrepancy"),
                ("The automation failed for 157-11111111, why? investigate", "ata_failure"),
                ("Exception report for 157-11111111: investigate", "exception_report"),
                ("How do we track an unfamiliar carrier? research how to integrate a new carrier", "carrier_research")):
    a = classify.assess(q, DATA, RECS)
    check("'{0}...' -> {1}".format(q[:44], want), a["template"] == want and a["level"] != "NORMAL", (a["template"], a["level"]))
check("Every template builds a valid graph", all(not plan.validate(plan.build(k, ["A", "B"])) for k in registry.TEMPLATES))
bad = plan.build("shipment_delay") + [{"id": "x", "specialist": "hacker", "depends_on": ["ghost"], "params": {}}]
probs = plan.validate(bad)
check("Unknown agents and missing dependencies are rejected", any("unknown specialist" in p for p in probs) and any("ghost" in p for p in probs), probs)
cyc = [{"id": "a", "specialist": "shipment", "depends_on": ["b"]}, {"id": "b", "specialist": "shipment", "depends_on": ["a"]},
       {"id": "report", "specialist": "report", "depends_on": ["a"]}]
check("A cycle is rejected", "the plan has a cycle" in plan.validate(cyc))
check("A plan too big is rejected", any("limit" in p for p in plan.validate(
    [{"id": "t%d" % i, "specialist": "shipment", "depends_on": []} for i in range(70)] + [{"id": "report", "specialist": "report", "depends_on": []}])))
ed = plan.build("shipment_delay", remove=["review", "verify", "report", "facts"])
ids = [t["id"] for t in ed]
check("A person may remove optional steps; facts, verify and report stay; dependants are rewired",
      "review" not in ids and {"facts", "verify", "report"} <= set(ids)
      and set(next(t for t in ed if t["id"] == "report")["depends_on"]) == {"verify", "actions"} and not plan.validate(ed), ed)
fan = plan.build("multi_delay", ["A%d" % i for i in range(40)])
items = [t for t in fan if t["specialist"] == "item"]
check("Fan-out: one task per item, capped at 25, and the merge waits for all of them",
      len(items) == 25 and set(next(t for t in fan if t["id"] == "merge")["depends_on"]) == {t["id"] for t in items})

os.environ["ATLAS_MISSION_PLANNER"] = "1"
a = classify.assess("Why is 157-11111111 stuck at customs? investigate the delay", DATA, RECS)
d1 = plan.draft(a, "deep")
PLANNER["answer"] = '{"template": "missing_ata", "skip": ["verify"]}'
d2 = plan.draft(a, "deep")
PLANNER["answer"] = '{"template": "missing_ata", "skip": ["review"], "why": "the dates disagree"}'
d3 = plan.draft(a, "deep")
check("A planning model naming a template outside the catalogue is rejected; the template plan stands",
      d1["template"] == a["template"] and "not in the catalogue" in d1["note"], d1)
check("...as is one that skips a required step", d2["template"] == a["template"] and "required" in d2["note"], d2)
check("A valid model plan is used, and says who planned it", d3["template"] == "missing_ata" and d3["skip"] == ["review"] and "planned by" in d3["note"], d3)
check("No planning model in a drill", plan.draft(a, "drill")["model"] is None)
os.environ.pop("ATLAS_MISSION_PLANNER")

print("\n5. RUNNING: FAN-OUT, DEADLINES, CALL CAPS, REPLANNING")
engine.start()
fm = engine.propose("Investigate all delayed shipments in TZ, why are they late", DATA, RECS, None, by="op", origin="manual")
check("The fan-out proposal names its items (longest past due first, at most 25)",
      fm["template"] == "multi_delay" and len(fm["plan"]["items"]) == 25 and fm["plan"]["items"][0] == "TZ-0039", fm["plan"].get("items"))
ok, msg = engine.approve(fm["id"], "op", "drill", remove=["review"])
st = engine.wait(fm["id"], 120)
g = engine.get(fm["id"])
merge_ev = [e for e in g["evidence"] if e["class"] == "CALCULATED"]
check("It runs 25 item tasks, merges them, and recomputes every merged count",
      st in ("done", "insufficient") and sum(1 for t in g["tasks"] if t["specialist"] == "item") == 25
      and merge_ev and all(e["verification"] == "recomputed" for e in merge_ev), (st, [e["verification"] for e in merge_ev]))
check("The removed step did not run; the report shows what the fan-out examined",
      not any(t["id"] == "review" for t in g["tasks"]) and g["report"]["fanout"]["examined"] == 25, g["report"].get("fanout"))
check("The approval records the edit and the deadline", any("removed: review" in e["text"] and "deadline" in e["text"] for e in g["events"]))

# deadline: a mission whose deadline has passed reports from what is done
dm = engine.propose("Why is EG-0003 stuck at customs? investigate the delay", DATA, RECS, None, by="op", origin="manual")
engine.MAX_ACTIVE, keep_active = 0, engine.MAX_ACTIVE           # hold it in the queue while its deadline passes
engine.approve(dm["id"], "op", "drill")
db.update("missions", {"id": dm["id"]}, status="running", deadline_at=time.time() - 1)
engine.MAX_ACTIVE = keep_active
st = engine.wait(dm["id"], 60)
gd = engine.get(dm["id"])
check("Past its deadline, the mission stops starting work and writes its report from what is done, said plainly",
      st in ("done", "insufficient", "failed") and any(t["status"] == "cancelled" for t in gd["tasks"])
      and (not gd.get("report") or any("deadline" in l for l in gd["report"]["limits"])) and any(e["kind"] == "deadline" for e in gd["events"]),
      (st, [t["status"] for t in gd["tasks"]], gd.get("report", {}) and gd["report"].get("limits")))

# watchdog: a task stuck running past its timeout fails and the late result is discarded
wm = engine.propose("Why is EG-0004 stuck at customs? investigate the delay", DATA, RECS, None, by="op", origin="manual")
engine.MAX_ACTIVE = 0
engine.approve(wm["id"], "op", "drill")
with db._lock:                                      # the workers wait: no task is picked between these writes
    db.update("missions", {"id": wm["id"]}, status="running")
    db.update("tasks", {"mission_id": wm["id"], "id": "facts"}, status="running", started_at=time.time() - 999, attempts=1)
    engine._watchdog(time.time())
check("A task running past its timeout fails, and the mission carries on without it",
      db.one("SELECT status, error FROM tasks WHERE mission_id=? AND id='facts'", wm["id"])["status"] == "failed")
engine._run(engine.db.one("SELECT * FROM missions WHERE id=?", wm["id"]), dict(db.one("SELECT * FROM tasks WHERE mission_id=? AND id='facts'", wm["id"])))
check("...and its late result is discarded, not written over the failure",
      db.one("SELECT status FROM tasks WHERE mission_id=? AND id='facts'", wm["id"])["status"] == "failed"
      and any("discarded" in e["text"] for e in engine.get(wm["id"])["events"]))
engine.MAX_ACTIVE = keep_active
engine.control(wm["id"], "cancel", "op")

# call cap: one call per task, the primary fails -> no second call
os.environ["ATLAS_MISSION_CALLS_PER_TASK"] = "1"
FAILING.update({"claude-sonnet-4-5", "claude-sonnet-4-6"})
n0 = len(CALLS)
try:
    models.call("reason", "s", "p", mission_id="capm", task_id="t1", profile="deep")
    raised = False
except models.ModelUnavailable:
    raised = True
first = len(CALLS) - n0
try:
    models.call("reason", "s", "p", mission_id="capm", task_id="t1", profile="deep")
except models.ModelUnavailable as error:
    capped = "used its" in str(error)
check("Per-task call cap: one failed call, no fallback beyond the cap, and the next call is refused",
      raised and first == 1 and capped, (raised, first))
os.environ.pop("ATLAS_MISSION_CALLS_PER_TASK")
FAILING.clear()

print("\n6. S4: ACTIONS, APPROVAL, READ-BACK")
xm = engine.propose("Why is 157-11111111 stuck at customs? investigate the delay", DATA, RECS, None, by="op", origin="manual")
engine.approve(xm["id"], "op", "deep")
engine.wait(xm["id"], 90)
gx = engine.get(xm["id"])
check("The replanner added the date reconciliation when tracking was supported",
      any(t["id"] == "eta" for t in gx["tasks"]) and any(e["kind"] == "replan" for e in gx["events"]), [t["id"] for t in gx["tasks"]])
check("When the carrier and eHub disagree the finding is Weak, and only a re-run (read back by the run) is proposed",
      gx["grade"] == "Weak" and gx["actions"] and all(a["kind"] == "reprocess" for a in gx["actions"]), (gx["grade"], gx["actions"]))
am = engine.propose("Why is 157-22222222 stuck at customs? investigate the delay", DATA, RECS, None, by="op", origin="manual")
engine.approve(am["id"], "op", "deep")
engine.wait(am["id"], 90)
ga = engine.get(am["id"])
kinds = sorted(a["kind"] for a in ga["actions"])
check("A checked finding becomes action cards: a re-run and an escalation, each waiting for approval",
      ga["grade"] in ("Strong", "Moderate") and "reprocess" in kinds and "escalate" in kinds
      and all(a["status"] == "proposed" for a in ga["actions"]) and ga["verification_status"] == "awaiting_approval", (ga["grade"], ga["actions"]))
rp = next(a for a in ga["actions"] if a["kind"] == "reprocess")
es = next(a for a in ga["actions"] if a["kind"] == "escalate")
check("Each card carries its permission, an idempotency key and how success will be checked",
      rp["permission"] == "runs.start" and es["permission"] == "clearance.escalate" and rp["idempotency_key"] and "read back" in rp["criterion"])
ok, msg, _ = actions.decide(rp["id"], True, "op", lambda p: True)
check("With actions switched off, approving executes nothing and says so", not ok and "switched off" in msg and not CTL, msg)
os.environ["ATLAS_MISSION_ACTIONS"] = "1"
ok, msg, _ = actions.decide(rp["id"], True, "viewer", lambda p: p == "missions.run")
check("Approving needs the action's own permission (runs.start), not just missions.run", not ok and "runs.start" in msg and not CTL, msg)
ok, msg, after = actions.decide(rp["id"], True, "op", lambda p: True)
check("Approved: the run's control channel receives exactly one re-run; status waits for the read-back",
      ok and CTL == [("reprocess", "157-22222222")] and after["status"] == "awaiting_outcome", (ok, msg, CTL, after and after["status"]))
check("Approving the same action again is refused (never executed twice)", not actions.decide(rp["id"], True, "op", lambda p: True)[0] and len(CTL) == 1)
check("No read-back yet: still waiting, never reported as done", actions.verify_pending() == 0
      and db.one("SELECT status FROM actions WHERE id=?", rp["id"])["status"] == "awaiting_outcome")
time.sleep(0.05)
E.record("shipment", reference="157-22222222", result="SUCCESS", verified=True, verification={"ata": True, "eta": True})
E.invalidate()
actions.verify_pending()
check("The run's verified success for that reference, after the request, confirms it",
      db.one("SELECT status FROM actions WHERE id=?", rp["id"])["status"] == "verified")
ok, msg, after = actions.decide(es["id"], True, "op", lambda p: p == "clearance.escalate")
check("The escalation goes through the Clearance page's function and is confirmed in its store",
      ok and len(ESC) == 1 and after["status"] == "verified" and after["engine_ref"] == ESC[0]["id"], (ok, msg, after and after["status"]))
check("Every executed action confirmed: the mission is done and verified", engine.get(am["id"])["verification_status"] == "done_verified")
check("The cards are not made twice for the same finding", actions.from_report(engine.get(am["id"]), ga["report"]["actions"]) == [])

# not verified: the run's result for the reference was not a verified success
bm = engine.propose("Why is EG-0005 stuck at customs? investigate the delay", DATA, RECS, None, by="op", origin="manual")
aid = actions._add(bm, "reprocess", "Re-run EG-0005", "test", {"reference": "EG-0005"})
actions.decide(aid, True, "op", lambda p: True)
time.sleep(0.05)
E.record("shipment", reference="EG-0005", result="SUCCESS", verified=False, verification={"ata": False})
E.invalidate()
actions.verify_pending()
check("A success whose Hub write was not read back is NOT verified, and the mission says so",
      db.one("SELECT status FROM actions WHERE id=?", aid)["status"] == "not_verified" and
      db.one("SELECT verification_status FROM missions WHERE id=?", bm["id"])["verification_status"] == "done_unverified")
# restart while executing
cid = actions._add(bm, "reprocess", "Re-run EG-0006", "test", {"reference": "EG-0006"})
db.update("actions", {"id": cid}, status="executing", executed_at=time.time(), decided_by="op", decided_at=time.time())
n_ctl = len(CTL)
actions.recover()
check("After a restart during execution, the action is read back, never re-run",
      db.one("SELECT status FROM actions WHERE id=?", cid)["status"] == "awaiting_outcome" and len(CTL) == n_ctl)
# engine refuses
tools.configure(control=lambda a, r: (False, "Control is switched off."))
fid = actions._add(bm, "reprocess", "Re-run EG-0007", "test", {"reference": "EG-0007"})
ok, msg, after = actions.decide(fid, True, "op", lambda p: True)
check("When the engine refuses, the action is failed with the engine's own words", not ok and after["status"] == "failed" and "switched off" in msg)
tools.configure(control=lambda action, ref: (CTL.append((action, ref)) or True, "{0} is queued.".format(ref)))
rid = actions._add(bm, "escalate", "Escalate EG-0008", "test", {"awb": "EG-0008", "reason": "x"})
actions.decide(rid, False, "op", lambda p: True)
check("Rejected: nothing is done", db.one("SELECT status FROM actions WHERE id=?", rid)["status"] == "rejected" and not any(e["awb"] == "EG-0008" for e in ESC))

from dashboard import todo  # noqa: E402
pend = actions._add(bm, "escalate", "Escalate EG-0010", "High exposure", {"awb": "EG-0010", "reason": "x"})
items = todo.build(lambda p: p in ("missions.view", "clearance.escalate"))["items"]
check("A pending approval is in To Do for whoever can approve it", any(i["id"] == "mact:" + pend and i["state"] == "pending_approval" for i in items))
check("...and not for someone who cannot", not any(i["id"] == "mact:" + pend for i in todo.build(lambda p: p == "missions.view")["items"]))

print("\n7. S8: LEVEL 4 POLICIES (NONE BY DEFAULT)")
check("No policy file: no Level 4", actions.policies() == ([], []))
pol = os.path.join(TMP, "pol.json")
os.environ["ATLAS_MISSION_POLICIES"] = pol
today = time.strftime("%Y-%m-%d")
from datetime import date, timedelta  # noqa: E402
Path(pol).write_text(json.dumps([
    {"id": "old", "kind": "queue_human", "approved_by": "admin", "approved_on": "2026-01-01", "expires": "2026-01-20", "max_per_mission": 5},
    {"id": "long", "kind": "queue_human", "approved_by": "admin", "approved_on": today, "expires": (date.today() + timedelta(days=60)).isoformat()},
    {"id": "agent-tasks", "kind": "queue_human", "approved_by": "admin", "approved_on": today, "min_grade": "Moderate",
     "expires": (date.today() + timedelta(days=7)).isoformat(), "max_per_mission": 3, "hypotheses": ["agent"]}]))
good, badp = actions.policies()
check("Expired or longer-than-30-day policies are ignored, with the reason",
      [p["id"] for p in good] == ["agent-tasks"] and {p["id"]: p["reason"] for p in badp} == {"old": "expired", "long": "longer than 30 days"}, (good, badp))
pm = engine.propose("Why is GH-0001 waiting for a clearing agent? investigate the delay", DATA, RECS, None, by="op", origin="manual")
engine.approve(pm["id"], "op", "drill")
engine.wait(pm["id"], 90)
gp = engine.get(pm["id"])
qh = [a for a in gp["actions"] if a["kind"] == "queue_human"]
check("A matching policy approves only what it names, recorded as the policy and audited in the timeline",
      qh and all(a["decided_by"].startswith("policy:agent-tasks") and a["status"] == "verified" for a in qh)
      and any(e["kind"] == "policy" for e in gp["events"]), (gp["grade"], gp["actions"]))
check("A To Do task from a mission is listed until a person marks it done", any(i["id"] == "mact:" + qh[0]["id"] for i in todo.build(lambda p: True)["items"]))
actions.complete(qh[0]["id"], "op")
check("...then it is done", not any(i["id"] == "mact:" + qh[0]["id"] for i in todo.build(lambda p: True)["items"]))
os.environ.pop("ATLAS_MISSION_POLICIES")

print("\n8. S6: MEMORY AND LESSONS")
mem = memory.listing(am["id"])
check("A finished mission leaves its outcome and lessons as drafts, never as knowledge",
      any(x["kind"] == "outcome" for x in mem) and any(x["kind"] == "lesson" for x in mem) and all(x["status"] == "draft" for x in mem), mem)
again = engine.propose("Why is 157-22222222 still stuck at customs? investigate the delay again", DATA, RECS, None, by="op", origin="manual")
check("A new mission about the same shipment shows the earlier one", any(s["mission_id"] == am["id"] for s in again["plan"]["similar"]), again["plan"].get("similar"))
lesson = next(x for x in mem if x["kind"] == "lesson")
ok, msg = memory.promote(lesson["id"], "op@mantrac")
row_l = db.one("SELECT status, proposal_id FROM memory WHERE id=?", lesson["id"])
check("A person sends a lesson to knowledge review as an observation; it is not approved knowledge",
      ok and row_l["status"] == "proposed_to_kb" and row_l["proposal_id"], (ok, msg))
check("An outcome cannot be promoted, and a lesson only once", not memory.promote(next(x for x in mem if x["kind"] == "outcome")["id"], "op")[0]
      and not memory.promote(lesson["id"], "op")[0])

print("\n9. S7 / CARRIERS: WEB INVESTIGATION AND CARRIER COMPARISON")
wq = "How do we track an unfamiliar carrier? research how to integrate a new carrier"
w1 = engine.propose(wq, DATA, RECS, None, by="op", origin="manual")
engine.approve(w1["id"], "op", "drill")
engine.wait(w1["id"], 60)
g1 = engine.get(w1["id"])
check("Without web search set up, it says so and looks nothing up", g1["grade"] == "Insufficient" and "not set up" in json.dumps(g1["report"]), g1["report"]["summary"])
tools.TOOLS["web_search"]["fn"] = lambda q: [{"url": "https://cargo.example.com/track/12345678", "title": "Track", "snippet": "enter your AWB"},
                                             {"url": "https://cargo.example.com/help/milestones", "title": "Milestones", "snippet": "RCS DEP ARR DLV explained"}]
tools._cache.clear()
db.update("missions", {"id": w1["id"]}, status="done")
w2 = engine.replay(w1["id"], "op")[0]
engine.approve(w2["id"], "op", "drill")
engine.wait(w2["id"], 60)
g2 = engine.get(w2["id"])
ext = [e for e in g2["evidence"] if e["class"] == "EXTERNAL_UNVERIFIED"]
check("Public sources are external and unverified, never stronger than Weak; tracking pages are skipped",
      len(ext) == 1 and "milestones" in ext[0]["source"] and g2["grade"] == "Weak", (g2["grade"], [e["source"] for e in ext]))
now = time.time()
for i in range(6):
    E.record("shipment", reference="A%d" % i, carrier="Air France KLM", provider="afkl", result="SUCCESS", verified=i < 5, epoch=now - 3600)
for i in range(2):
    E.record("shipment", reference="Q%d" % i, carrier="Qatar", provider="qatar", result="FAILED", verified=False, epoch=now - 3600)
E.invalidate()
tools._cache.clear()
cm = engine.propose("Compare the carriers: which carrier performs best? analyze", DATA, RECS, None, by="op", origin="manual")
engine.approve(cm["id"], "op", "drill")
engine.wait(cm["id"], 60)
gc = engine.get(cm["id"])
txt = json.dumps(gc["report"])
check("Carrier comparison recounts every rate from the run's records; a carrier below the minimum is 'insufficient data'",
      gc["grade"] == "Moderate" and "insufficient data" in txt and all(e["verification"] == "recomputed" for e in gc["evidence"]), (gc["grade"], gc["report"]["summary"]))

print("\n10. S9: REPLAY, COMPARISON, HANDOFF, NOTES, ANALYTICS")
check("A replay is proposed (not started) and points at the original", w2["parent_id"] == w1["id"] and w2["origin"] == "replay")
cmpx = engine.compare(w1["id"], w2["id"])
check("Comparison shows what changed", cmpx["changed"]["grade"] and cmpx["a"]["grade"] == "Insufficient" and cmpx["b"]["grade"] == "Weak", cmpx)
check("Handoff and notes are recorded on the mission", engine.assign(am["id"], "lead@mantrac", "op")[0] and engine.note(am["id"], "op", "Called the agent")[0]
      and engine.get(am["id"])["assigned_to"] == "lead@mantrac" and engine.get(am["id"])["notes"][0]["text"] == "Called the agent")
an = engine.analytics()
check("Analytics come from the store: every executed action was approved by a person or a policy",
      an["missions"] >= 6 and an["actions"]["executed"] >= 4 and an["actions"]["approval_compliance"] == 1.0, an["actions"])

print("\n11. THE API, LIVE STREAM AND MISSION CONTROL")
from dashboard import server as SV  # noqa: E402
with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    PORT = so.getsockname()[1]
SV.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=None)
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


def api(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


reg = api("/api/missions/registry")
check("The registry lists the tool catalogue and whether actions are on", len(reg["tools"]) == len(tools.TOOLS) and "actions_enabled" in reg)
one = api("/api/missions/" + am["id"])
check("A mission carries its actions, lessons, notes, conflicts and model use by agent",
      one["actions"] and "can_decide" in one["actions"][0] and one["memory"] and one["notes"] and "conflicts" in one and "usage_by_agent" in one)
pa = api("/api/missions/propose", {"question": "Why is EG-0012 stuck at customs? investigate the delay"})["mission"]
ap = api("/api/missions/{0}/approve".format(pa["id"]), {"remove": ["review", "facts"], "deadline_min": 5, "profile": "drill"})
tids = [t["id"] for t in ap["mission"]["tasks"]]
check("Approving through the API applies the edit: optional step removed, required kept, deadline set",
      ap["ok"] and "review" not in tids and "facts" in tids and ap["mission"]["deadline_at"], tids)
with urllib.request.urlopen(BASE + "/api/missions/{0}/stream".format(pa["id"]), timeout=10) as r:
    first = r.readline().decode() + r.readline().decode()
check("The live stream sends the mission's version", first.startswith("event: mission") and pa["id"] in first, first)
an2 = api("/api/missions/analytics")
check("GET /api/missions/analytics", an2["missions"] >= 6)
pq = next(a for a in api("/api/missions/" + bm["id"])["actions"] if a["id"] == pend)
r = api("/api/missions/{0}/actions/{1}/approve".format(bm["id"], pend), {})
check("Approving through the API uses the platform's own escalation engine (and its rules)", "mission" in r and r["mission"], r.get("message"))
try:
    api("/api/missions/{0}/actions/{1}/explode".format(bm["id"], pend), {})
    code = 200
except urllib.error.HTTPError as error:
    code = error.code
check("Unknown mission routes are refused (404)", code == 404)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright:
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    with sync_playwright() as pw:
        br = pw.chromium.launch(executable_path=exe) if os.path.exists(exe) else pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1440, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','light')")
        prop = api("/api/missions/propose", {"question": "Why is EG-0013 stuck at customs? investigate the delay"})["mission"]
        pg.goto(BASE + "/hub#missions?id=" + prop["id"] + "&review=1"); pg.wait_for_timeout(2500)
        check("The plan editor: a checkbox per optional step, profile and deadline",
              pg.locator(".ms-plan input[type=checkbox]").count() >= 4 and pg.locator("#msProf").count() == 1 and pg.locator("#msDl").count() == 1)
        pg.locator('.ms-plan input[data-step="review"]').uncheck()
        check("Unticking a step strikes it out", "off" in (pg.locator('.ms-plan li[data-step="review"]').get_attribute("class") or ""))
        pg.screenshot(path=os.path.join(TMP, "agentic_plan.png"), full_page=True)
        pg.goto(BASE + "/hub#missions?id=" + bm["id"]); pg.wait_for_timeout(2500)
        body = pg.locator("#modBody").inner_text()
        check("Approvals and outcomes: each card says its status, permission and how it is checked",
              "Approvals and outcomes" in body and "Not confirmed" in body and "Done when" in body, body[:600])
        pg.goto(BASE + "/hub#missions?id=" + am["id"]); pg.wait_for_timeout(2500)
        body = pg.locator("#modBody").inner_text()
        check("Lessons, notes, handoff and replay are on the mission page",
              "Lessons" in body and "Called the agent" in body and "Hand over" in body and "Replay" in body, body[-600:])
        pg.screenshot(path=os.path.join(TMP, "agentic_mission.png"), full_page=True)
        pg.goto(BASE + "/hub#missions"); pg.wait_for_timeout(2000)
        check("The list shows mission analytics", "Confirmed by read-back" in pg.locator("#modBody").inner_text())
        check("No page error", not errs, errs)
        br.close()
print("Screenshots:", TMP)
print("\n{0} passed, {1} failed".format(len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
