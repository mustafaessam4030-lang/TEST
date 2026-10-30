"""
ATLAS Missions (intelligence/missions, ATLAS_MISSIONS_PLAN.md): Deep Mission and
Emergency Mode. Ask before escalating; nothing runs without an approval; read-only;
every claim with its source and every number recomputed; models routed, counted and
checked; restart-safe; the ATA run keeps the machine. Stand-in models only: nothing billed.

    python test_missions.py
"""
import io
import json
import os
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="missions_")
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "DASHBOARD_ACCOUNTS": "0", "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"),
                   "PO_DATA_DIR": os.path.join(TMP, "po"), "PO_AUTO": "0", "ATLAS_AI": "0",
                   "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key")})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

PASSED, FAILED, SKIPPED = [], [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:400]))


from dashboard import clearance as C  # noqa: E402


def row(bu, step, awb, overdue=None, agent="AGENT X", eta="2026-09-01", ata=None, status="InProgress"):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, awb=awb, overdue=overdue, agent=agent, eta=eta, ata=ata, mode="Air", status=status,
             due="2026-10-01", ffw="Ceva")
    return r


ROWS = [row("EG", "Customs Declaration", "157-11111111", 45.0, ata="2026-09-06")] + \
       [row("EG", "Customs Declaration", "EG-%04d" % i, 20.0) for i in range(14)] + \
       [row("GH", "Assign Clearing Agent", "GH-0001", 9.0, agent=None), row("KE", "Completed", "KE-0001"),
        row("NG", "Submit to BU", "NG-0001", 3.0)] + \
       [row("TZ", "Duty Payment", "TZ-%04d" % i, 35.0) for i in range(160)]
DATA = {"asof": time.strftime("%Y-%m-%d"), "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS], "dq": {}}
LIVE_DATA = {"d": DATA}
C.load = lambda: LIVE_DATA["d"]
C.escalations = lambda: []

from intelligence.missions import classify, db, engine, models, registry, specialists  # noqa: E402

CALLS = []


class Fake(object):
    def __init__(self, name, fail=False, invent=False):
        self.model, self.fail, self.invent = name, fail, invent

    def health(self):
        return {"ok": True, "detail": "ready"}

    def generate(self, system, prompt, timeout=None, json_mode=False, max_tokens=0, images=None):
        CALLS.append(self.model)
        if self.fail:
            raise RuntimeError("down")
        if "independent reviewer" in system:
            return '{"agree": true, "concerns": []}'
        if "other plausible causes" in system:
            return '{"causes": ["Port congestion at the airport"]}'
        if self.invent:
            return "157-11111111 is 99 days late and was released on 2026-12-31."
        return "157-11111111 is at Customs Declaration and past its step's due date."


STATE = {"s": {}}
FAILING, INVENT = set(), {"on": False}
models.PROVIDERS["cortex"] = lambda mid: Fake(mid, fail=mid in FAILING, invent=INVENT["on"])
models.PROVIDERS["ollama"] = Fake("local")
engine.configure(state=lambda: STATE["s"], export=lambda: LIVE_DATA["d"])
RECS = C.records(DATA)

SRC = "".join((HERE / "intelligence" / "missions" / f).read_text(encoding="utf-8")
              for f in ("engine.py", "specialists.py", "classify.py", "registry.py", "db.py"))

print("\n1. READ-ONLY BY CONSTRUCTION")
check("Missions never write: no escalation, no run control, no HTTP of their own",
      not __import__("re").search(r"\.escalate\(", SRC) and "control.request" not in SRC and "urlopen" not in SRC and "requests." not in SRC)
check("Claude Opus is not in the model registry (present in Snowflake, not on the allowlist)",
      not any("opus" in m["id"] for m in models.registry()))
check("The ledger never uses a field the store would drop (\"token\")",
      "input_tokens" not in (HERE / "intelligence" / "missions" / "models.py").read_text())
from controlplane import rbac  # noqa: E402
check("Permissions: everyone sees missions; Operators approve; only Admins approve a critical incident",
      "missions.view" in rbac.ROLE_PERMISSIONS["VIEWER"] and "missions.run" not in rbac.ROLE_PERMISSIONS["VIEWER"]
      and "missions.run" in rbac.ROLE_PERMISSIONS["OPERATOR"] and "missions.emergency" not in rbac.ROLE_PERMISSIONS["OPERATOR"]
      and "missions.emergency" in rbac.ROLE_PERMISSIONS["ADMIN"])
from dashboard import hub, accounts  # noqa: E402
check("A Missions item in the hub sidebar (Insight), nothing else renamed",
      any(m["id"] == "missions" and m["group"] == "Insight" for m in hub.MODULES) and [m["id"] for m in hub.MODULES][:6] ==
      ["overview", "tracking", "ata", "runs", "po", "clearance"])
check("Routes are mapped: GET and POST /api/missions need missions.view (finer checks in the handler)",
      accounts.permission_for("GET", "/api/missions") == "missions.view" and accounts.permission_for("POST", "/api/missions/abc/approve") == "missions.view")

print("\n2. IS THIS A MISSION?")
a = classify.assess("What is the status of 157-11111111?", DATA, RECS)
check("A plain lookup stays NORMAL (ATLAS answers as today)", a["level"] == "NORMAL", a)
a = classify.assess("Why is 157-11111111 stuck? investigate", DATA, RECS)
check("'Why' about a shipment 45 days past due: a Deep Mission, with reasons in words",
      a["level"] == "DEEP_MISSION" and any("45 days" in r for r in a["reasons"]), a)
a2 = classify.assess("Why is everything so complicated across all carriers and every source", DATA, RECS)
check("Complexity alone (no shipment, no BU in the records) is never a mission", a2["level"] == "NORMAL", a2)
a3 = classify.assess("Investigate why NG-0001 is late, compare every source", DATA, RECS)
check("Complex but low impact is not an emergency", a3["level"] != "CRITICAL_INCIDENT" and a3["severity"] in ("NORMAL", "HIGH"), a3)
a4 = classify.assess("Urgent: investigate the TZ backlog, why is it all past due", DATA, RECS)
check("A BU with 160 open past-due shipments, marked urgent: CRITICAL incident from the records",
      a4["level"] == "CRITICAL_INCIDENT" and a4["severity"] in ("CRITICAL", "SEVERE") and a4["template"] == "backlog", a4)
check("A BU-limited person cannot pull another BU into scope", classify.assess("Why is 157-11111111 stuck? investigate", DATA, RECS, {"GH"})["level"] == "NORMAL")

print("\n3. ASK BEFORE ESCALATING")
m = engine.propose("Why is 157-11111111 stuck at customs? investigate", DATA, RECS, None, by="op@mantrac")
check("A proposal is created and nothing runs", m and m["status"] == "proposed" and not db.many("SELECT * FROM tasks WHERE mission_id=?", m["id"]))
check("The proposal shows the plan, specialists, models, sources, estimate and limits",
      len(m["plan"]["plan"]) == 10 and m["plan"]["specialists"] and m["plan"]["websites"] and m["plan"]["limits"] and m["plan"]["estimate"]["budget"])
m_again = engine.propose("Why is 157-11111111 stuck at customs? investigate", DATA, RECS, None, by="op@mantrac")
check("The same task asked twice is one proposal, not two", m_again["id"] == m["id"])
d = engine.propose("Why is GH-0001 waiting so long? investigate the agent pattern", DATA, RECS, None, by="op")
ok, msg = engine.decline(d["id"], "op")
check("Continue normally: declined, and not proposed again for that task",
      ok and engine.propose("Why is GH-0001 waiting so long? investigate the agent pattern", DATA, RECS, None, by="op") is None)

print("\n4. THE MISSION RUNS")
CALLS.clear()
ok, msg = engine.approve(m["id"], "op@mantrac")
st = engine.wait(m["id"], 60)
g = engine.get(m["id"])
check("Approved, it runs to a report", ok and st == "done" and g["report"], (st, g.get("phase")))
check("The approval is recorded: who, when, the plan's hash", g["approved_by"] == "op@mantrac" and g["approved_at"] and g["plan_hash"])
tasks = {t["id"]: t for t in g["tasks"]}
order_ok = all(tasks[d2]["ended_at"] <= tasks[t["id"]]["started_at"] + 1e-6 for t in g["tasks"] for d2 in (t.get("depends_on") or []))
check("Every task started after the tasks it depends on", order_ok)
CLASSES = ("VERIFIED_LIVE", "HISTORICAL", "GENERAL_KNOWLEDGE", "PLATFORM_RULE", "HYPOTHESIS", "EXTERNAL_UNVERIFIED")
check("Every finding has a source and a class", g["evidence"] and all(e["source"] and e["class"] in CLASSES for e in g["evidence"]))
check("Numbers were recomputed from the source and matched", "recomputed" in {e["verification"] for e in g["evidence"]}
      and "mismatch" not in {e["verification"] for e in g["evidence"]} and g["report"]["verification"].split(" of ")[0] != "0")
hyp = {h["id"]: h for h in g["hypotheses"]}
check("Hypotheses are decided by evidence: customs delay supported, BU wait rejected, model suggestion only 'open'",
      hyp["customs"]["status"] == "supported" and hyp["bu"]["status"] == "rejected" and any(h["status"] == "open" and h["origin"].startswith("model") for h in g["hypotheses"]))
check("Confidence is a grade with reasons, never a percentage", g["grade"] in ("Strong", "Moderate", "Weak", "Insufficient")
      and g["report"]["grade_reasons"] and "%" not in g["grade"])
check("The independent review used a different model family from the one that drafted",
      tasks["review"]["model"] and models.family({"id": tasks["review"]["model"]}) != models.family({"id": tasks["hyp"]["model"]}), (tasks["review"]["model"], tasks["hyp"]["model"]))
check("Next steps are proposals, classed, none executed", g["report"]["actions"] and all(a["status"] == "not executed" and a["class"] in registry.ACTION_CLASSES for a in g["report"]["actions"]))
led = models.ledger(m["id"])
check("Every model call is in the ledger: model, task, latency, usage, cost estimate, status, output hash",
      led and all({"model_id", "task_id", "latency_ms", "usage_in", "usage_out", "cost_estimate", "status", "prompt_sha256"} <= set(r) for r in led)
      and all("prompt" not in r for r in led))

print("\n5. CHECKED, NOT TRUSTED")
INVENT["on"] = True
m2 = engine.propose("Why is EG-0003 stuck at customs? investigate the delay", DATA, RECS, None, by="op")
engine.approve(m2["id"], "op"); engine.wait(m2["id"], 60)
g2 = engine.get(m2["id"])
INVENT["on"] = False
check("A model's wording that adds a number or date is rejected; ATLAS's own wording is used",
      g2["report"]["narrative_by"] == "ATLAS" and g2["report"].get("narrative_rejected"), g2["report"].get("narrative_by"))
# the source changes between reading and checking: the verifier sees it
changed = [dict(r) for r in ROWS]
for r in changed:
    if r["awb"] == "EG-0005":
        r["overdue"] = 99.0
m3 = engine.propose("Why is EG-0005 stuck at customs? investigate the delay", DATA, RECS, None, by="op")
orig = specialists.FUNCS["verify"]


def verify_after_change(ctx):
    LIVE_DATA["d"] = {"asof": DATA["asof"], "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in changed], "dq": {}}
    try:
        return orig(ctx)
    finally:
        LIVE_DATA["d"] = DATA


specialists.FUNCS["verify"] = verify_after_change
engine.approve(m3["id"], "op"); engine.wait(m3["id"], 60)
specialists.FUNCS["verify"] = orig
g3 = engine.get(m3["id"])
check("A claim that no longer matches the source is caught, and confidence drops to Weak",
      "mismatch" in {e["verification"] for e in g3["evidence"]} and g3["grade"] == "Weak", g3["grade"])

print("\n6. MODELS: ROUTED, BUDGETED, FALLING BACK")
FAILING.add("claude-sonnet-4-5")
r = models.call("word", "s", "p", mission_id="x1", task_id="t", budget=80000)
check("A model that fails gives way to the next best, never the same one twice", r["model"] != "claude-sonnet-4-5")
FAILING.clear()
m_, why = models.route("word", budget=10, used_tokens=50)
check("Over the mission's budget, no billed model: the local one or none", m_ is None or (m_.get("cost") or 0) == 0, why)
m_, why = models.route("plan", profile="drill")
check("A drill uses no billed model", m_ is None or (m_.get("cost") or 0) == 0, why)
m_, why = models.route("word", tier="T2")
check("Personal or financial data (T2) never goes to Cortex", m_ is None or m_["provider"] != "cortex", why)
STATE["s"] = {"run": {"status": "running"}}
m_, why = models.route("classify", profile="drill")
check("While the ATA run is busy, missions do not use the local model", m_ is None or m_["provider"] != "ollama", why)
STATE["s"] = {}

print("\n7. WORKLOAD: RESTARTS, PAUSE, CANCEL, LIMITS")
m4 = engine.propose("Why is EG-0007 stuck at customs? investigate the delay", DATA, RECS, None, by="op")
engine.control(m4["id"], "cancel", "op")
check("A proposal can be cancelled; nothing ran", engine.get(m4["id"])["status"] == "cancelled" and not engine.get(m4["id"])["tasks"])
m5 = engine.propose("Why is EG-0008 stuck at customs? investigate the delay", DATA, RECS, None, by="op")
# simulate a crash mid-run: tasks marked running with no worker behind them
for i, (tid, spec, obj, deps) in enumerate(registry.TEMPLATES[m5["template"]]["tasks"]):
    db.insert("tasks", mission_id=m5["id"], id=tid, objective=obj, specialist=spec, needs=["records"], depends_on=deps, priority=10 - i,
              status="done" if tid == "facts" else "running" if tid == "pattern" else "queued", idempotent=1, attempts=0,
              output={"out": {"rows": []}} if tid == "facts" else None)
db.update("missions", {"id": m5["id"]}, status="running", approved_by="op", approved_at=time.time())
n = engine.recover()
check("After a restart, unfinished tasks are queued again and the mission resumes", n >= 1 and engine.get(m5["id"])["status"] == "queued")
with engine._cond:
    engine._cond.notify_all()
check("...and it finishes, keeping the work done before", engine.wait(m5["id"], 60) in ("done", "insufficient"))
m6 = engine.propose("Why is EG-0009 stuck at customs? investigate the delay", DATA, RECS, None, by="op")
old = engine.MAX_QUEUED
engine.MAX_QUEUED = 0
ok6, msg6 = engine.approve(m6["id"], "op")
engine.MAX_QUEUED = old
check("Backpressure: when the queue is full, a new mission is refused, said plainly", not ok6 and "waiting" in msg6, msg6)

print("\n8. EMERGENCY MODE")
sug = engine.suggest_incidents(DATA, RECS)
check("A BU over the critical threshold is suggested as an incident, never started by itself",
      sug and all(s["status"] == "proposed" and s["origin"] == "auto" and s["level"] == "CRITICAL_INCIDENT" for s in sug))
check("...once: asking again does not duplicate it", len(engine.suggest_incidents(DATA, RECS)) == len(sug)
      and db.one("SELECT COUNT(*) AS n FROM missions WHERE origin='auto'")["n"] == len(sug))
ok7, _ = engine.approve(sug[0]["id"], "admin")
engine.wait(sug[0]["id"], 90)
g7 = engine.get(sug[0]["id"])
check("An approved incident produces the incident plan: recommendation, verification criteria, rollback",
      g7["report"] and g7["report"].get("incident") and g7["report"]["incident"]["verification_criteria"] and g7["report"]["incident"]["rollback"])

print("\n9. THE SERVER AND MISSION CONTROL")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
from dashboard import server as S  # noqa: E402
with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    PORT = so.getsockname()[1]
S.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=None)
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


def api(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


ans = api("/api/ask", {"question": "Why is EG-0011 stuck at customs? investigate the delay", "context": {}})
check("ATLAS still answers, and offers the mission with a link to review it (nothing started)",
      ans.get("answer") and "/hub#missions?id=" in ans["answer"] and ans.get("mission") and
      engine.get(ans["mission"]["id"])["status"] == "proposed", (ans.get("answer") or "")[-300:])
ans2 = api("/api/ask", {"question": "What is the status of EG-0011?", "context": {}})
check("A plain question gets no mission offer", "/hub#missions" not in (ans2.get("answer") or ""))
lst = api("/api/missions")
check("GET /api/missions lists missions and what this person may do", lst["missions"] and "can" in lst)
p = api("/api/missions/propose", {"question": "Why is EG-0012 stuck at customs? investigate the delay"})
check("POST propose returns a proposal", p.get("mission") and p["mission"]["status"] == "proposed")
ap = api("/api/missions/{0}/approve".format(p["mission"]["id"]), {})
check("POST approve starts it", ap.get("ok"), ap)
reg = api("/api/missions/registry")
check("The registry shows models with live health, specialists, websites (live lookups stay with the run)",
      reg["models"] and all("health" in m for m in reg["models"]) and reg["specialists"] and all(w["live"] is False for w in reg["websites"]))

if sync_playwright is None:
    SKIPPED.append("browser")
else:
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    with sync_playwright() as pw:
        br = pw.chromium.launch(executable_path=exe) if os.path.exists(exe) else pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda msg: msg.type == "error" and errs.append(msg.text))
        pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','light')")
        prop = api("/api/missions/propose", {"question": "Why is EG-0013 stuck at customs? investigate the delay"})["mission"]
        pg.goto(BASE + "/hub#missions?id=" + prop["id"]); pg.wait_for_timeout(2500)
        check("Missions is in the sidebar", pg.locator('#nav a[data-mod="missions"]').count() == 1)
        txt = pg.locator("#modBody").inner_text()
        check("The proposal card: why, the plan, and Enter Deep Mission / Review the plan / Continue normally",
              "Enter Deep Mission" in txt and "Review the plan" in txt and "Continue normally" in txt and "ATLAS suggests" in txt, txt[:400])
        pg.get_by_role("button", name="Review the plan").click(); pg.wait_for_timeout(500)
        check("Review the plan opens the step-by-step plan", pg.evaluate("document.getElementById('msPlan').open"))
        pg.get_by_role("button", name="Enter Deep Mission").click()
        for _ in range(60):
            pg.wait_for_timeout(500)
            if "Report" in pg.locator("#modBody").inner_text() and "Confidence" in pg.locator("#modBody").inner_text():
                break
        body = pg.locator("#modBody").inner_text()
        check("It runs live: task graph, evidence, hypotheses, then the report with its confidence",
              "Confidence" in body and "Evidence" in body and "Hypotheses" in body and pg.locator(".ms-node").count() >= 10, body[:300])
        check("Next steps are marked not executed", "none executed" in body.lower() or "not executed" in body)
        pg.goto(BASE + "/hub#missions"); pg.wait_for_timeout(2000)
        lt = pg.locator("#modBody").inner_text().lower()
        check("The list shows finished and suggested missions", "finished" in lt and "suggested by atlas" in lt, lt[:300])
        check("No page error in Mission Control", not errs, errs)
        pg.screenshot(path=os.path.join(TMP, "missions.png"))
        br.close()

print("\n{0} passed, {1} failed{2}".format(len(PASSED), len(FAILED), ", {0} skipped".format(len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
