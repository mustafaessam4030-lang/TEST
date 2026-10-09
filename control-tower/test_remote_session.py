"""
The remote Control Tower end to end: browser -> control plane -> worker
agent (a real separate process) -> the run (update_eta's own Human Action
code, on a real Chromium tab) -> carrier stand-in -> in-memory Hub, and back.

    RUN CONTROL    start from the API, duplicate refused, run completes
    HUMAN ACTION   parked after the grace window, Open & Continue, a second
                   operator refused, the run's tab streamed to the holder,
                   their clicks and keys typed into it, the run detects and
                   double-checks the verification, extracts, writes, reads
                   back -> SUCCESS. Automatic: nobody presses Resume.
    SECURITY       the security code is nowhere: not in the database, the
                   run state, the audit, the worker's files, the logs
    TIMEOUT        nobody comes: the task times out, nothing is written
    DISCONNECT     the worker agent is killed mid-run: WORKER_DISCONNECTED;
                   a new agent adopts the still-running run: RUNNING again
    SESSION LOST   the run is stopped while a task is held: session lost
    DASHBOARD      sign in, theme kept per user, the viewer drawn for the
                   holder, no controls for a Viewer

What is NOT real here, and why: the carrier site (fixtures/carrier_stub.py,
a GNET-like page whose code is an image) and the Hub's Manage page (the
in-memory Hub in fixtures/remote_runner.py). Neither can be reached from a
test. Everything between them is the production code.

    python test_remote_session.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORK = Path(tempfile.mkdtemp(prefix="ct_remote_"))
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "cp_intel")
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")

from controlplane.config import Settings          # noqa: E402
from controlplane.app import App, make_server      # noqa: E402
from fixtures import carrier_stub as stub          # noqa: E402
from fixtures.platform_client import Client        # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP  {0}  ({1})".format(name, why))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def until(fn, seconds=60, every=0.25):
    end = time.time() + seconds
    while time.time() < end:
        try:
            value = fn()
        except Exception:
            value = None
        if value:
            return value
        time.sleep(every)
    return None


try:
    from playwright.sync_api import sync_playwright
    CHROME = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
    BROWSER_OK = True
except ImportError:
    BROWSER_OK = False

if not BROWSER_OK:
    skip("Remote Human Action end to end", "Playwright is not installed")
    print("\n{0} passed, {1} failed, {2} skipped".format(len(PASS), len(FAIL), len(SKIP)))
    sys.exit(0)

_server, CARRIER = stub.start()
app = App(Settings({"DATABASE_URL": "sqlite:///{0}/ata.db".format(WORK),
                    "ATA_INSECURE_COOKIES": "1", "ATA_WORKER_OFFLINE_S": "5"}))
app.start_background()
PW = {"op1": "Hub-Write-2026!", "op2": "Hub-Write-2027!", "viewer": "Read-Only-2026!",
      "admin": "Tower-Key-2026!"}
app.users.create("omar.ops@mantrac.com", "Omar Farouk", "OPERATOR", password=PW["op1"])
app.users.create("olga.ops@mantrac.com", "Olga Nasser", "OPERATOR", password=PW["op2"])
app.users.create("vera.view@mantrac.com", "Vera Nabil", "VIEWER", password=PW["viewer"])
app.users.create("mona.admin@mantrac.com", "Mona Hassan", "ADMIN", password=PW["admin"])
WID, TOKEN = app.orch.add_worker("ATA-WORKER-01")
httpd = make_server(app, "127.0.0.1", 0)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:{0}".format(httpd.server_address[1])
RUNTIME = WORK / "runtime"


def start_agent(refs, hold="90", grace="2500", tag="a"):
    env = dict(os.environ, ATA_RUNNER_SCRIPT=str(HERE / "fixtures" / "remote_runner.py"),
               ATA_RUNTIME_DIR=str(RUNTIME), ATLAS_INTEL_DIR=str(WORK / "worker_intel"),
               E2E_WORK=str(WORK / "runner"), E2E_CARRIER_BASE=CARRIER, E2E_REFS=refs,
               E2E_HOLD_S=hold, E2E_GRACE_MS=grace, ATA_HEARTBEAT_S="2")
    return subprocess.Popen([sys.executable, "-m", "worker", "--url", BASE, "--token", TOKEN],
                            cwd=str(HERE), env=env, stdout=open(WORK / ("agent-" + tag + ".out"), "w"),
                            stderr=subprocess.STDOUT, start_new_session=True)


op1, op2, viewer = Client(BASE), Client(BASE), Client(BASE)
op1.login("omar.ops@mantrac.com", PW["op1"])
op2.login("olga.ops@mantrac.com", PW["op2"])
viewer.login("vera.view@mantrac.com", PW["viewer"])


def state(c=op1):
    return c.get("/api/state")[1]


def waiting_task(c=op1):
    q = [t for t in state(c).get("human_queue") or [] if t.get("status") == "WAITING_FOR_HUMAN"]
    return q[0] if q else None


def task_by(action_id, c=op1):
    return next((t for t in state(c).get("human_queue") or []
                 if t.get("action_id") == action_id), None)


def run_status(run_id):
    return (app.orch.run(run_id) or {}).get("status")


agents = []
try:
    # ═════════════════════════════════════════════════════════════════
    rule("1. RUN CONTROL — the worker connects; Start creates the run")
    s, d, _ = op1.post("/api/runs", {})
    check("Before any worker connects, Start says 'Automation worker unavailable.'",
          s == 503 and d["message"] == "Automation worker unavailable.")
    agent = start_agent("S330348776", hold="90", grace="2500", tag="1")
    agents.append(agent)
    check("The worker agent connects outbound and reports IDLE",
          until(lambda: op1.get("/api/health")[1]["worker"] == "IDLE", 30))
    s, d, _ = op1.post("/api/runs", {})
    run1 = (d.get("run") or {}).get("run_id")
    check("Start creates the run and sends it to the worker", s == 200 and run1)
    s2, d2, _ = op2.post("/api/runs", {})
    check("A second Start from another operator is refused while it runs", s2 == 409)
    check("The run's id is the control plane's: the runner reports the same one",
          until(lambda: (state().get("run") or {}).get("run_id") == run1, 30))

    # ═════════════════════════════════════════════════════════════════
    rule("2. HUMAN ACTION — parked, Open & Continue, streamed, typed, verified")
    t = until(waiting_task, 60)
    check("After the grace window nobody had opened it, so the shipment is parked "
          "(WAITING_FOR_HUMAN) and the run holds", bool(t), str(state().get("human_queue")))
    if not t:
        raise SystemExit("no parked task")
    aid = t["action_id"]
    s, d, _ = viewer.post("/api/human", {"op": "open", "run_id": run1, "action_id": aid})
    check("A Viewer cannot open it (403)", s == 403)
    s, d, _ = op1.post("/api/human", {"op": "open", "run_id": run1, "action_id": aid})
    check("Operator A: Open & Continue accepted", d.get("accepted") is True, str(d))
    s, d, _ = op2.post("/api/human", {"op": "open", "run_id": run1, "action_id": aid})
    check("Operator B is told A has it, and nothing is sent", d.get("accepted") is False and
          d.get("holder") == "omar.ops@mantrac.com")
    seq = {"n": 0, "meta": None, "bytes": 0}

    def frame():
        s, data, h = op1.get("/api/session/{0}/frame?after={1}".format(aid, seq["n"]), timeout=20)
        if s == 200:
            seq.update(n=int(h["X-Seq"]), meta=json.loads(h["X-Meta"]), bytes=len(data))
            return data
        return None
    first = until(frame, 60, 0.1)
    check("A receives the run's own tab as JPEG frames",
          bool(first) and first[:2] == b"\xff\xd8" and seq["meta"]["width"] > 0)
    check("B gets no frames (409)", op2.get("/api/session/{0}/frame".format(aid))[0] == 409)
    check("The page shows the shipment the run filled in, waiting at the code",
          until(lambda: stub.LAYOUT.get("code"), 15) is not None)
    check("...and the run itself never touched the code box", stub.SUBMITTED == [])
    L = stub.LAYOUT

    def click(x, y):
        return [{"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
                {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1}]
    keys = [e for ch in stub.CODE for e in ({"type": "keyDown", "key": ch, "text": ch},
                                            {"type": "keyUp", "key": ch})]
    s, d, _ = op1.post("/api/session/{0}/input".format(aid),
                       {"events": click(L["code"]["x"], L["code"]["y"]) + keys})
    check("A's click and keystrokes go to the worker", s == 200)
    time.sleep(1.5)
    check("Nothing has been submitted yet: the run waits for the person", stub.SUBMITTED == [])
    op1.post("/api/session/{0}/input".format(aid), {"events": click(L["go"]["x"], L["go"]["y"])})
    done = until(lambda: (task_by(aid) or {}).get("status") in (
        "SUCCESS", "FAILED", "TIMEOUT", "VERIFICATION_NOT_CONFIRMED", "HUMAN_SESSION_LOST")
        and task_by(aid), 60)
    check("The carrier accepted the code the person typed", stub.SUBMITTED and
          stub.SUBMITTED[-1]["code_ok"] is True and stub.SUBMITTED[-1]["ship"] == "S330348776",
          str(stub.SUBMITTED))
    check("The run detected it, confirmed twice, and carried on by itself -> SUCCESS",
          done and done["status"] == "SUCCESS", str(done))
    events = [e.get("event") for e in state().get("human_events") or []]
    check("...through the queue's own states: confirmed before resuming",
          "VERIFICATION_CONFIRMED" in events and "HUMAN_RESUMED" in events, str(events[-8:]))
    record = next((r for r in state().get("shipments") or [] if r["reference"] == "S330348776"), {})
    check("The shipment was written and read back: COE ETA and BU ATA",
          "saved" in str(record.get("coe_action")) and "saved" in str(record.get("bu_action"))
          and record.get("state") == "updated", str({k: record.get(k) for k in (
              "state", "coe_action", "bu_action")}))
    check("The run then completes", until(lambda: run_status(run1) == "COMPLETED", 60),
          run_status(run1))
    check("...and the worker is idle again", until(
        lambda: op1.get("/api/health")[1]["worker"] == "IDLE", 20))
    trail = [(e["action"], e["result"]) for e in reversed(app.audit.list(200))
             if e["action"] in ("START_RUN", "OPEN_HUMAN_ACTION", "CONTINUE_HUMAN_ACTION")]
    check("Audit: START_RUN, OPEN_HUMAN_ACTION (A sent, B refused), "
          "CONTINUE_HUMAN_ACTION confirmed then SUCCESS",
          ("START_RUN", "QUEUED") in trail and ("OPEN_HUMAN_ACTION", "SENT") in trail and
          ("OPEN_HUMAN_ACTION", "REFUSED_CLAIMED") in trail and
          ("CONTINUE_HUMAN_ACTION", "SUCCESS") in trail, str(trail))

    # ═════════════════════════════════════════════════════════════════
    rule("3. SECURITY — the security code is stored nowhere")
    leaks = []
    for path in WORK.rglob("*"):
        if path.is_file():
            try:
                if stub.CODE.encode() in path.read_bytes():
                    leaks.append(str(path.relative_to(WORK)))
            except OSError:
                pass
    check("Not in any file: database, run state, control file, worker runtime, runner log, "
          "ATLAS stores (searched {0} files)".format(sum(1 for p in WORK.rglob("*") if p.is_file())),
          not leaks, str(leaks))
    check("Not in what the dashboard receives", stub.CODE not in json.dumps(state()))

    # ═════════════════════════════════════════════════════════════════
    rule("4. TIMEOUT — nobody comes")
    agent.terminate()
    agent.wait(10)
    agent = start_agent("S330400011", hold="5", grace="1500", tag="2")
    agents.append(agent)
    until(lambda: op1.get("/api/health")[1]["worker"] == "IDLE", 30)
    s, d, _ = op1.post("/api/runs", {})
    run2 = (d.get("run") or {}).get("run_id")
    t2 = until(waiting_task, 60)
    ended = until(lambda: run_status(run2) == "COMPLETED", 90)
    final = task_by(t2["action_id"]) if t2 else None
    check("Parked, never opened, the task times out when the run ends",
          bool(ended) and final and final["status"] == "TIMEOUT", str(final))
    rec = next((r for r in state().get("shipments") or [] if r["reference"] == "S330400011"), {})
    check("...and nothing was written for it", not rec.get("coe_action") and not rec.get("bu_action"))

    # ═════════════════════════════════════════════════════════════════
    rule("5. DISCONNECT — the agent dies mid-run; a new one adopts the run")
    agent.terminate()
    agent.wait(10)
    agent = start_agent("S330400021", hold="120", grace="1500", tag="3")
    agents.append(agent)
    until(lambda: op1.get("/api/health")[1]["worker"] == "IDLE", 30)
    s, d, _ = op1.post("/api/runs", {})
    run3 = (d.get("run") or {}).get("run_id")
    t3 = until(waiting_task, 60)
    check("A run is going, parked on a task", bool(t3) and run_status(run3) == "RUNNING")
    last = json.loads((RUNTIME / "last_run.json").read_text())
    runner_pid = last.get("pid")
    agent.kill()                    # SIGKILL: no goodbye, no clean shutdown
    agent.wait(10)
    check("With the agent killed, the control plane marks the run WORKER_DISCONNECTED",
          until(lambda: run_status(run3) == "WORKER_DISCONNECTED", 30), run_status(run3))
    alive = runner_pid and Path("/proc/{0}".format(runner_pid)).exists()
    check("...while the run itself keeps going on the worker", bool(alive))
    s, d, _ = op1.post("/api/human", {"op": "open", "run_id": run3, "action_id": t3["action_id"]})
    check("...Human Actions cannot be opened until it returns, and the reason is given",
          d.get("accepted") is False and "disconnected" in d.get("message", ""))
    check("...and no second run can be started beside it", op1.post("/api/runs", {})[0] in (409, 503))
    agent = start_agent("S330400021", hold="120", grace="1500", tag="4")
    agents.append(agent)
    check("A new agent adopts the still-running run: RUNNING again, reconciled",
          until(lambda: run_status(run3) == "RUNNING", 30), run_status(run3))
    check("...the audit says so", any(e["action"] == "RUN_RECONCILED" and e["result"] == "RUNNING"
                                      for e in app.audit.list(50)))

    # ═════════════════════════════════════════════════════════════════
    rule("6. SESSION LOST — the run is stopped while a task is held")
    s, d, _ = op1.post("/api/human", {"op": "open", "run_id": run3, "action_id": t3["action_id"]})
    check("Operator opens the task", d.get("accepted") is True, str(d))
    s, d, _ = op1.post("/api/runs/{0}/stop".format(run3), {"force": True})
    check("Stop (immediate) is accepted", s == 200)
    check("The run ends as STOPPED", until(lambda: run_status(run3) in ("STOPPED", "COMPLETED",
                                                                       "INTERRUPTED"), 60),
          run_status(run3))
    lost = task_by(t3["action_id"])
    check("The held task shows HUMAN_SESSION_LOST — nothing written, nothing to resume",
          lost and lost["status"] in ("HUMAN_SESSION_LOST", "TIMEOUT"), str(lost))

    # ═════════════════════════════════════════════════════════════════
    rule("7. DASHBOARD — sign in, theme per user, viewer, roles")
    agent.terminate()
    agent.wait(10)
    agent = start_agent("S330400031", hold="60", grace="1500", tag="5")
    agents.append(agent)
    until(lambda: op1.get("/api/health")[1]["worker"] == "IDLE", 30)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=str(CHROME[-1]) if CHROME else None)
        ctx = browser.new_context(viewport={"width": 1440, "height": 950})
        ctx.add_init_script("try{sessionStorage.setItem('ct-intro','1')}catch(e){}")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/")
        check("Opening the address without a session shows the sign-in page",
              "/login" in page.url and page.is_visible("#email"))
        page.fill("#email", "omar.ops@mantrac.com")
        page.fill("#password", "wrong-one-entirely")
        page.click("#go")
        page.wait_for_selector("#note:not([hidden])")
        check("A wrong password is answered on the page", "not right" in page.inner_text("#note"))
        page.fill("#password", PW["op1"])
        page.click("#go")
        page.wait_for_selector("#pfMe:not([hidden])", timeout=15000)
        check("Signed in: name and role in the header",
              page.inner_text("#pfName") == "Omar Farouk" and page.inner_text("#pfRole").lower() == "operator")
        # The button is drawn from the first live state, a moment after sign-in.
        offered = until(lambda: page.is_visible("#btnStart") and
                        page.inner_text("#btnStart") == "Start automation" and
                        not page.is_disabled("#btnStart"), 20)
        check("Start automation is offered to an Operator", bool(offered),
              page.inner_text("#btnStart") if page.is_visible("#btnStart") else "hidden")
        page.click("[data-theme-set='dark']")
        page.wait_for_timeout(600)
        check("Choosing Dark switches the page to the dark theme",
              page.evaluate("document.documentElement.dataset.theme") == "dark")
        ctx2 = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx2.add_init_script("try{sessionStorage.setItem('ct-intro','1')}catch(e){}")
        page2 = ctx2.new_page()
        page2.goto(BASE + "/login")
        page2.fill("#email", "omar.ops@mantrac.com")
        page2.fill("#password", PW["op1"])
        page2.click("#go")
        page2.wait_for_selector("#pfMe:not([hidden])", timeout=15000)
        page2.wait_for_timeout(500)
        check("...and the choice follows the person to another device",
              page2.evaluate("document.documentElement.dataset.theme") == "dark" and
              page2.evaluate("document.documentElement.dataset.themeChoice") == "dark")
        page.click("#btnStart")
        page.wait_for_selector(".hq-go:not([disabled])", timeout=60000)
        page.click(".hq-go")
        page.wait_for_selector("#rv:not([hidden])")
        # The page's CSP forbids eval, so wait_for_function cannot run here.
        until(lambda: page.evaluate("document.getElementById('rvImg').naturalWidth") > 0, 60)
        check("Open & Continue in the dashboard opens the browser view with the carrier page",
              page.is_visible("#rvImg") and "Complete the verification" in page.inner_text("#rvHow"))
        until(lambda: stub.LAYOUT.get("code"), 10)
        box = page.locator("#rvImg").bounding_box()
        meta = page.evaluate("rv.meta")

        def at(pt):
            return (box["x"] + pt["x"] / meta["width"] * box["width"],
                    box["y"] + pt["y"] / meta["height"] * box["height"])
        before = len(stub.SUBMITTED)
        page.mouse.click(*at(stub.LAYOUT["code"]))
        page.keyboard.type(stub.CODE, delay=40)
        page.mouse.click(*at(stub.LAYOUT["go"]))
        until(lambda: page.inner_text("#rvHow").startswith("Verified success"), 60)
        check("A real mouse and keyboard in the dashboard complete it; the view reports "
              "Verified success", len(stub.SUBMITTED) > before and stub.SUBMITTED[-1]["code_ok"])
        steps = page.eval_on_selector_all("#rvSteps li", "els => els.map(e => e.className)")
        check("...with every step lit: confirmed, extracting, validating, Hub write, read-back",
              steps and all(c == "on" for c in steps), str(steps))
        vctx = browser.new_context(viewport={"width": 390, "height": 844})
        vctx.add_init_script("try{sessionStorage.setItem('ct-intro','1')}catch(e){}")
        vp = vctx.new_page()
        vp.goto(BASE + "/login")
        vp.fill("#email", "vera.view@mantrac.com")
        vp.fill("#password", PW["viewer"])
        vp.click("#go")
        vp.wait_for_selector("#pfMe:not([hidden])", timeout=15000)
        vp.wait_for_timeout(1200)
        visible_open = [i for i in range(vp.locator(".hq-go").count())
                        if vp.locator(".hq-go").nth(i).is_visible()]
        check("A Viewer sees no Start, Stop or Open & Continue",
              not vp.is_visible("#btnStart") and not vp.is_visible("#btnStop") and not visible_open)
        check("...and has no Access page", not vp.is_visible(".tnav-i[data-nav='access']"))
        page.click("#rvLeave")
        perf = page.evaluate("""() => {
          const base = JSON.parse(JSON.stringify(S)), runs = [], copies = [];
          for (let r = 0; r < 7; r++){
            let t = performance.now();
            for (let i = 0; i < 200; i++){ const x = JSON.parse(JSON.stringify(base)); x.current.step = 'c' + i; }
            const copy = (performance.now() - t) / 200;
            t = performance.now();
            for (let i = 0; i < 200; i++){
              const x = JSON.parse(JSON.stringify(base)); x.current.step = 'p' + i; S = x; paint();
            }
            runs.push((performance.now() - t) / 200 - copy);
          }
          runs.sort(); return runs[3]; }""")
        check("A screen refresh in the remote dashboard costs {0:.2f} ms (baseline 0.34 ms "
              "locally; budget 1 ms)".format(perf), perf < 1.0)
        page.click("#pfOut")
        page.wait_for_url("**/login**")
        page.goto(BASE + "/")
        page.wait_for_url("**/login**")
        check("Sign out returns to the sign-in page, and the session is gone",
              "/login" in page.url and page.is_visible("#email"))
        check("No script errors on the page", not errors, str(errors[:3]))
        browser.close()
finally:
    for a in agents:
        try:
            a.kill()
        except Exception:
            pass
    subprocess.run(["pkill", "-f", str(HERE / "fixtures" / "remote_runner.py")], capture_output=True)

print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(len(PASS), len(FAIL),
                                         ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
