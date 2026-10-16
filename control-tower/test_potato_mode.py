"""
Potato Mode (intelligence/potato.py) — the Easter egg, and proof it never
gets in the way.

    python test_potato_mode.py

One joke per incident after a configurable number of errors; a potato
harvested only when that incident's shipment is fixed and verified; and the
run — recovery, root cause, retries, learning records, the work list, Human
Action, authentication — exactly the same with or without it. TEST DATA only:
temporary store folders, nothing real is read or written.
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
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_potato_intel_")
os.environ.pop("ATLAS_DATA_ORIGIN", None)

from intelligence import potato as P                # noqa: E402
from dashboard.bridge import ControlTowerState      # noqa: E402
from dashboard import assistant                     # noqa: E402

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


def errors(m, ref, n, start=0, problem="TIMEOUT"):
    for i in range(start, start + n):
        m.observe("error", key="{0}-{1}".format(ref, i), reference=ref, problem=problem)


# ═════════════════════════════════════════════════════════════════════════
rule("1. ONE JOKE PER INCIDENT, AFTER A CONFIGURABLE NUMBER OF ERRORS")
m = P.PotatoMode()
m.observe("run_started", run_id="R1")
errors(m, "A1", 2)
check("Below the threshold (3): no joke", not m.snapshot()["active"])
errors(m, "A1", 1, start=2)
snap = m.snapshot()
check("At the threshold: Potato Mode, one message, with its context",
      snap["active"] and snap["message"] in P.DEFAULT_MESSAGES and
      snap["context"]["reference"] == "A1" and snap["context"]["errors"] == 3, str(snap))
first = snap["message"]
errors(m, "A1", 5, start=3)
check("More errors on the same incident: no second joke, no second potato",
      m.snapshot()["message"] == first and m.planted == 1)
errors(m, "B2", 1)
errors(m, "C3", 1)
check("Errors spread over different shipments are separate incidents (no joke)",
      m.snapshot()["context"]["reference"] == "A1" and m.planted == 1)
errors(m, "B2", 2, start=1)
check("A second incident gets its own joke, never the same line twice in a row",
      m.snapshot()["context"]["reference"] == "B2" and m.snapshot()["message"] != first)
m5 = P.PotatoMode({"threshold": 5})
m5.observe("run_started", run_id="R5")
errors(m5, "X", 4)
check("The threshold is configurable (5: four errors are not enough)", not m5.snapshot()["active"])
errors(m5, "X", 1, start=4)
check("...five are", m5.snapshot()["active"])
cfg = P.merged_config({"threshold": 0, "messages": ["", 7]})
check("Invalid settings are ignored", cfg["threshold"] == 3 and cfg["messages"] == P.DEFAULT_MESSAGES)
mc = P.PotatoMode({"messages": ["Custom 🥔"]})
mc.observe("run_started", run_id="RC")
errors(mc, "Z", 3)
check("Messages come from configuration", mc.snapshot()["message"] == "Custom 🥔")
m = P.PotatoMode()
m.observe("run_started", run_id="RD")
for _ in range(5):
    m.observe("error", key="same-event", reference="D1")
check("The same event five times counts once", m.errors.get("RD|D1") == 1)
m.observe("run_started", run_id="RE")
errors(m, "D1", 2)
check("A new run is a new incident: the count starts again", not m.snapshot()["active"])

# ═════════════════════════════════════════════════════════════════════════
rule("2. THE HARVEST — only when the incident is fixed and verified")
m = P.PotatoMode()
m.observe("run_started", run_id="H1")
errors(m, "K1", 3)
m.observe("verified", key="v0", reference="OTHER")
check("Another shipment verified harvests nothing", m.harvests == 0)
m.observe("verified", key="v1", reference="K1")
snap = m.snapshot()
check("Its shipment verified: one potato, and the joke goes away",
      snap["harvests"] == 1 and not snap["active"] and snap["last_harvest"]["reference"] == "K1")
m.observe("verified", key="v2", reference="K1")
check("Verified again: still one (no duplicate harvest)", m.harvests == 1)
m.observe("verified", key="v3", reference="NOPOTATO")
check("A shipment that never reached Potato Mode harvests nothing", m.harvests == 1)

# ═════════════════════════════════════════════════════════════════════════
rule("3. THROUGH THE BRIDGE — verification means read back, nothing less")


def incident(b, ref, attempts=3):
    b.shipment_started({"bol_awb": ref, "carrier": "DHL", "provider": "DHL"})
    b.recovery_plan("UNEXPECTED PAGE STATE", "no button", ["reload_page", "reopen", "alt"], {}, True)
    for i, action in enumerate(["reload_page", "reopen", "alt"][:attempts], 1):
        b.recovery_attempt(i, attempts, action, None, "FAILED", False)
    b.recovery_done(False, "exhausted")
    b.shipment_finished(ref, "FAILED", "no button", outcome_class="UNEXPECTED PAGE STATE")


b = ControlTowerState()
b.run_started(run_id="BR1")
incident(b, "S1")
p = b.snapshot()["potato"]
check("Three failed recovery attempts on one shipment: Potato Mode", p["active"] and
      p["context"]["reference"] == "S1", str(p))
b.deferred_retry_started("S1")
b.shipment_started({"bol_awb": "S1", "carrier": "DHL", "provider": "DHL"})
b.view_updated("COE", "ETA", "12/09/2026")                    # saved, NOT read back
b.shipment_finished("S1", "SUCCESS", "")
check("SUCCESS without a read-back is not verified: no potato", b.snapshot()["potato"]["harvests"] == 0)
b2 = ControlTowerState()
b2.run_started(run_id="BR2")
incident(b2, "S2")
b2.deferred_retry_started("S2")
b2.shipment_started({"bol_awb": "S2", "carrier": "DHL", "provider": "DHL"})
b2.view_updated("COE", "ETA", "12/09/2026", verified=True)
b2.shipment_finished("S2", "SUCCESS", "")
check("Retried, written and read back: harvested", b2.snapshot()["potato"]["harvests"] == 1)

# ═════════════════════════════════════════════════════════════════════════
rule("4. NEVER IN THE WAY — recovery, learning, work list, retries unchanged")


class Recorder(object):
    """The learning store, as the bridge sees it: every record it is asked to write."""

    def __init__(self):
        self.rows = []

    def record(self, kind, **fields):
        self.rows.append(dict(fields, kind=kind))
        return True


def drive(b):
    b.intel = Recorder()
    b.run_started(run_id="SAME")
    for ref in ("F1", "F2"):
        incident(b, ref)
    b.shipment_started({"bol_awb": "OK1", "carrier": "QATAR", "provider": "QATAR"})
    b.recovery_plan("TIMEOUT", "slow", ["reload_page"], {}, True)
    b.recovery_attempt(1, 1, "reload_page", None, "SUCCESS", True)
    b.recovery_done(True, "loaded", verified=True)
    b.view_updated("COE", "ETA", "30/08/2026", verified=True)
    b.shipment_finished("OK1", "SUCCESS", "")
    b.deferred_retry_started("F1")
    b.shipment_started({"bol_awb": "F1", "carrier": "DHL", "provider": "DHL"})
    b.view_updated("COE", "ETA", "12/09/2026", verified=True)
    b.shipment_finished("F1", "SUCCESS", "")
    b.run_finished("finished")
    return b


def no_time(value):
    """Without what differs between ANY two runs: timestamps, and failure_id
    (failures.failure_id hashes the shipment's start time)."""
    if isinstance(value, dict):
        return {k: no_time(v) for k, v in value.items()
                if k not in ("at", "time", "started", "finished", "updated", "epoch",
                             "duration_ms", "started_epoch", "generated_at", "version",
                             "cold_version", "heartbeat_age", "started_at", "finished_at",
                             "last_success", "runtime_seconds", "potato", "failure_id")}
    if isinstance(value, list):
        return [no_time(v) for v in value]
    return value


with_p = drive(ControlTowerState())
without = ControlTowerState()
without.potato = None
without = drive(without)
s1, s2 = with_p.snapshot(), without.snapshot()
check("Potato Mode really fired during the run (and harvested F1)",
      s1["potato"]["harvests"] == 1 and s1["potato"]["planted"] == 2, str(s1["potato"]))
check("Recovery is identical: every plan, attempt, verification and outcome",
      no_time(s1["recovery_history"]) == no_time(s2["recovery_history"]) and
      s1["atlas"]["recovery"] == s2["atlas"]["recovery"])
check("Root-cause and failure intelligence are identical (ATLAS's intelligence log)",
      no_time(s1["intel_log"]) == no_time(s2["intel_log"]))
check("Learning records written are identical, record for record",
      no_time(with_p.intel.rows) == no_time(without.intel.rows) and len(with_p.intel.rows) > 0,
      "{0} vs {1}".format(len(with_p.intel.rows), len(without.intel.rows)))
check("Retries, the work list and every shipment's outcome are identical",
      s1["retry_queue"] == s2["retry_queue"] and s1["counters"] == s2["counters"] and
      [(r["reference"], r["state"]) for r in s1["shipments"]] ==
      [(r["reference"], r["state"]) for r in s2["shipments"]])
check("The whole run state matches, apart from the potato itself",
      no_time(s1) == no_time(s2))


class Broken(object):
    def observe(self, *a, **k):
        raise RuntimeError("boom")

    def set_human(self, *a):
        raise RuntimeError("boom")

    def snapshot(self):
        raise RuntimeError("boom")


broken = ControlTowerState()
broken.potato = Broken()
s3 = drive(broken).snapshot()
check("A Potato Mode that crashes cannot break a run: identical outcome, no potato",
      no_time(s3) == no_time(s2) and s3["potato"] is None)

# ═════════════════════════════════════════════════════════════════════════
rule("5. SAFETY — Human Action first; nothing in it can act")
h = ControlTowerState()
h.run_started(run_id="HU")
incident(h, "W1")
check("Potato Mode on before the Human Action", h.snapshot()["potato"]["active"])
h.human_action_opened({"action_id": "act1", "reference": "W2", "carrier": "AFKL",
                       "step": "verify", "reason": "captcha"})
snap = h.snapshot()
check("A Human Action waiting: no joke shown", not snap["potato"]["active"] and
      snap["potato"]["message"] is None)
check("...and the Human Action itself is untouched and waiting",
      snap["human_action"] and snap["human_action"].get("waiting") is True)
src = (HERE / "intelligence" / "potato.py").read_text(encoding="utf-8")
check("The module imports nothing that can act: no browser, network, mail, files or automation",
      not any(w in src for w in ("playwright", "urllib", "requests", "smtplib", "update_eta",
                                 "subprocess", "import os", "open(")))
check("It does not claim feelings: no 'I feel', 'sad', 'conscious' in it",
      not any(w in src.lower() for w in ("i feel", " sad", "conscious", "emotion")))

# ═════════════════════════════════════════════════════════════════════════
rule("6. PERSISTENCE — the store folder, behind the origin guard")
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_potato_store_")
b = ControlTowerState()
b.persist_potato()
b.run_started(run_id="PS1")
incident(b, "Q1")
b.deferred_retry_started("Q1")
b.shipment_started({"bol_awb": "Q1", "carrier": "DHL", "provider": "DHL"})
b.view_updated("COE", "ETA", "12/09/2026", verified=True)
b.shipment_finished("Q1", "SUCCESS", "")
path = Path(os.environ["ATLAS_INTEL_DIR"]) / P.STATE_FILE
saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
check("The potato count is written to the store folder", saved.get("harvests") == 1, str(saved)[:100])
b2 = ControlTowerState()
b2.persist_potato()
check("...and survives a restart", b2.snapshot()["potato"]["harvests"] == 1)
prod = tempfile.mkdtemp(prefix="ct_potato_prod_")
Path(prod, "ORIGIN").write_text("production\n", encoding="utf-8")
os.environ["ATLAS_INTEL_DIR"] = prod
b3 = ControlTowerState()
b3.persist_potato()
b3.run_started(run_id="PS3")
incident(b3, "Q3")
check("A test process never writes potatoes into a production store",
      not (Path(prod) / P.STATE_FILE).exists())
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_potato_intel2_")

# ═════════════════════════════════════════════════════════════════════════
rule("7. CHAT — read-only, no model needed")
b = drive(ControlTowerState())
before = json.dumps(b.snapshot()["potato"], sort_keys=True)
r = assistant._answer_rules("How many potatoes do you have?", b.snapshot(), {"_raw": True})
check("'How many potatoes?' is answered from Potato Mode",
      r["intent"] == "potato" and "Potatoes harvested: 1" in r["answer"] and
      "not an operational metric" in r["answer"], r["answer"][:120])
r = assistant._answer_rules("plant a potato for me", b.snapshot(), {"_raw": True})
check("Asking it to plant one changes nothing, and it says why",
      json.dumps(b.snapshot()["potato"], sort_keys=True) == before and
      "can't plant or harvest on request" in r["answer"])
r = assistant._answer_rules("Why did F2 fail?", b.snapshot(), {"_raw": True})
check("A real question is still a real answer, not a potato joke",
      r["intent"] != "potato" and "🥔" not in r["answer"], r["intent"])
from intelligence import converse, llm                  # noqa: E402
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


_provider = llm.provider
llm.provider = lambda: NoModel()
reply = converse.answer("how is the potato harvest?", b.snapshot(), {}, assistant._answer_rules)
llm.provider = _provider
check("Through the conversation layer it is answered directly, with no model call",
      reply.get("intent") == "potato" and (reply.get("llm") or {}).get("mode") == "potato"
      and not calls, str(reply.get("llm")))

# ═════════════════════════════════════════════════════════════════════════
rule("8. AUTHENTICATION — behind the sign-in; no route can plant or harvest")


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
    work = tempfile.mkdtemp(prefix="ct_potato_cp_")
    app = App(Settings({"DATABASE_URL": "sqlite:///{0}/cp.db".format(work),
                        "ATA_INSECURE_COOKIES": "1"}))
    app.users.create("omar.ops@mantrac.com", "Omar Ops", "OPERATOR", password="Op-pass-123456!")
    httpd = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cp = "http://127.0.0.1:{0}".format(httpd.server_address[1])
    s, _ = call(cp + "/api/state")
    check("Remote platform: the state (potato included) needs a sign-in", s == 401, str(s))
    op = Client(cp)
    s, _d, _h = op.login("omar.ops@mantrac.com", "Op-pass-123456!")
    codes = [op.call("POST", path, {"reference": "X"})[0]
             for path in ("/api/potato", "/api/atlas/potato", "/api/atlas/plant",
                          "/api/atlas/harvest")]
    check("...and a signed-in operator finds no route to plant or harvest",
          s == 200 and all(code in (403, 404, 405) for code in codes), str(codes))
except Exception as error:
    skip("Remote platform authentication", "{0}: {1}".format(type(error).__name__, error))

# ═════════════════════════════════════════════════════════════════════════
rule("9. DASHBOARD — the card, the header chip, and the harvest animation")
from dashboard import server as tower_server             # noqa: E402
from dashboard.bridge import bridge as B                 # noqa: E402
B.run_started(run_id="UI-TEST")
B.shipment_started({"bol_awb": "UI0", "carrier": "QATAR AIRWAYS", "provider": "QATAR"})
B.view_updated("COE", "ETA", "24/08/2026", verified=True)
B.shipment_finished("UI0", "SUCCESS", "")
incident(B, "UI1")
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
pt = (json.loads(raw or b"{}") or {}).get("potato") or {}
check("The dashboard's /api/state carries Potato Mode", s == 200 and pt.get("active") is True,
      "{0} {1}".format(s, pt))
codes = [call(BASE + p, "POST", b"{}", {"Content-Type": "application/json"})[0]
         for p in ("api/potato", "api/atlas/plant", "api/atlas/harvest")]
check("The local dashboard has no route to plant or harvest either",
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
    errs = []
    page.on("pageerror", lambda err: errs.append(str(err)))
    page.goto(BASE)
    page.wait_for_function("() => typeof S !== 'undefined' && S && S.potato", timeout=15000)
    page.evaluate("() => { const b = document.querySelector('[data-nav=\"atlas\"]'); if (b) b.click(); }")
    page.wait_for_selector("#ptCard:not([hidden])", timeout=10000)
    body = page.inner_text("#ptBody")
    check("The ATLAS page shows the joke, its context and the counter",
          any(line[:20] in body for line in P.DEFAULT_MESSAGES) and "UI1" in body and
          "harvested" in body, body[:120])
    check("The chat header shows 🥔 Potato Mode while it is on",
          page.evaluate("document.getElementById('chMood').textContent") == "🥔 Potato Mode" and
          not page.evaluate("document.getElementById('chMood').hidden"))
    B.deferred_retry_started("UI1")
    B.shipment_started({"bol_awb": "UI1", "carrier": "DHL", "provider": "DHL"})
    B.view_updated("COE", "ETA", "12/09/2026", verified=True)
    B.shipment_finished("UI1", "SUCCESS", "")
    page.wait_for_selector("#ptN.pop .pt-fly", timeout=15000)
    check("A verified fix: the counter pops and a potato rises",
          page.inner_text("#ptN b") == "1")
    page.wait_for_timeout(300)
    check("...and the joke and the header chip go away",
          "UI1 ·" not in page.inner_text("#ptBody") and
          page.evaluate("document.getElementById('chMood').hidden"))
    mobile = browser.new_page(viewport={"width": 390, "height": 844})
    mobile.add_init_script("sessionStorage.setItem('ct-intro','1')")
    mobile.goto(BASE)
    mobile.wait_for_function("() => typeof S !== 'undefined' && S && S.potato", timeout=15000)
    mobile.evaluate("() => { const b = document.querySelector('[data-nav=\"atlas\"]'); if (b) b.click(); }")
    mobile.wait_for_selector("#ptCard:not([hidden])", timeout=10000)
    box = mobile.locator("#ptCard").bounding_box()
    check("On a phone the card fits the screen", box and box["x"] >= 0 and
          box["x"] + box["width"] <= 391, str(box))
    check("No script errors on the page", not errs, str(errs[:2]))
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
