"""
ATLAS's learning layer: verified learning, recovery knowledge, evidence,
vision, maturity — and the boundaries around them.

Every store is pointed at a temporary folder; nothing reaches the real one.
Images are real: rendered by Chromium from HTML with known text, read by the
real Tesseract when it is installed (those checks are skipped otherwise).

    python test_intelligence.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(tempfile.mkdtemp(prefix="ct_intel_test_"))
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "store")
sys.path.insert(0, str(HERE))

from intelligence import events, learning, plans, evidence, vision, maturity, store  # noqa: E402
from dashboard.bridge import ControlTowerState                                     # noqa: E402
from dashboard import assistant, atlas_learning                                     # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "" if condition or not detail else "  ({0})".format(detail)))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP  {0}  ({1})".format(name, why))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def month_epoch(months_ago, day=10):
    t = time.localtime()
    year, mon = t.tm_year, t.tm_mon - months_ago
    while mon <= 0:
        mon += 12
        year -= 1
    return time.mktime((year, mon, day, 12, 0, 0, 0, 0, -1))


def ladder(first_ok, second_ok, third=None):
    out = [{"attempt": 1, "strategy": "existing page", "page_verified": first_ok,
            "skipped": False, "error": None if first_ok else "net::ERR_HTTP2_PROTOCOL_ERROR"}]
    if not first_ok:
        out.append({"attempt": 2, "strategy": "fresh context", "page_verified": second_ok,
                    "skipped": False, "error": None})
        if third is not None:
            out.append({"attempt": 3, "strategy": "clean edge, HTTP/2 disabled",
                        "page_verified": third == "ok", "skipped": third == "skip",
                        "error": None})
    return out


# ═════════════════════════════════════════════════════════════════════
rule("1. ONLY A VERIFIED OUTCOME IS A POSITIVE LEARNING SIGNAL")
check("VERIFIED SUCCESS needs SUCCESS and every read-back True",
      events.verified_success("SUCCESS", {"COE ETA": True, "BU ATA": True})
      and not events.verified_success("SUCCESS", {"COE ETA": True, "BU ATA": None})
      and not events.verified_success("SUCCESS", {})
      and not events.verified_success("FAILED", {"COE ETA": True}))
E = month_epoch(0, 3)
rows = []


def ship(ref, verified, strategies, result=None, run="r1", epoch=E, provider="AFKL"):
    rows.append({"kind": "shipment", "run_id": run, "reference": ref, "provider": provider,
                 "carrier": "Air France KLM Cargo", "result": result or
                 ("SUCCESS" if verified else "FAILED"), "verified": verified,
                 "strategy_issue": "AFKL navigation", "strategies": strategies,
                 "epoch": epoch, "at": store.stamp(epoch)})


ship("A1", True, ladder(False, True))                         # fresh context: success
ship("A2", False, ladder(False, True), result="SUCCESS")      # reached page, not verified
ship("A3", False, ladder(False, False, "ok"))                 # fresh fails; HTTP/2 reached, shipment failed
ship("A4", True, ladder(False, False, "ok"), run="r2")        # HTTP/2: success
ship("A5", True, ladder(False, False, "skip"), run="r2")      # HTTP/2 skipped
ship("A6", True, ladder(True, None))                          # no issue at all
rows.append({"kind": "recovery", "run_id": "r2", "reference": "H1", "provider": "HUB",
             "error_class": "TIMEOUT", "status": "RECOVERED",
             "attempts": [{"action": "wait_for_page_ready", "result": "SUCCESS", "verified": True}],
             "shipment_verified": False, "epoch": E})
rows.append({"kind": "recovery", "run_id": "r2", "reference": "H2", "provider": "HUB",
             "error_class": "TIMEOUT", "status": "RECOVERED",
             "attempts": [{"action": "wait_for_page_ready", "result": "FAILED", "verified": None},
                          {"action": "reload_page", "result": "SUCCESS", "verified": True}],
             "shipment_verified": True, "epoch": E})
rows.append({"kind": "feedback", "verdict": "recovery_worked", "reference": "A2", "epoch": E})
snap = learning.build(rows)
nav = learning.find_issue(snap, provider="AFKL", name="navigation")[0]
fresh = nav["strategies"]["fresh context"]
http2 = nav["strategies"]["clean edge, HTTP/2 disabled"]
check("A strategy that reached the page AND ended in a verified write is a success",
      fresh["successes"] == 1 and http2["successes"] == 1, str(fresh))
check("Reaching the right page without a verified write is UNVERIFIED, not a success",
      fresh["unverified"] == 1 and http2["unverified"] == 1, str(http2))
check("A strategy that ran and failed is a failure", fresh["failures"] == 3, str(fresh))
check("A skipped strategy is not an attempt", http2["skipped"] == 1 and http2["attempts"] == 2)
check("A shipment whose first way in worked is not a navigation issue",
      nav["occurrences"] == 5 and "A6" not in nav["affected"])
check("An operator saying 'recovery worked' changes no outcome count",
      fresh["successes"] == 1 and snap["feedback"] == {"recovery_worked": 1})
hub = learning.find_issue(snap, provider="HUB", name="TIMEOUT")[0]
wfp, reload_ = hub["strategies"]["wait_for_page_ready"], hub["strategies"]["reload_page"]
check("A recovery whose own check passed but whose shipment was not verified is unverified",
      wfp["unverified"] == 1 and wfp["successes"] == 0)
check("...and the attempt that ended in a verified write is the success",
      reload_["successes"] == 1 and wfp["failures"] == 1)
check("Provenance: runs, first/last seen, affected shipments, evidence runs",
      nav["runs"] == ["r1", "r2"] and nav["first_seen"] and nav["last_seen"]
      and fresh["evidence_runs"] == ["r1"] and http2["evidence_runs"] == ["r2"])

rule("2. RANKING AND CONFIDENCE")
check("Wilson lower bound: 18/20 outranks 3/3", learning.wilson(18, 20) > learning.wilson(3, 3))
check("No attempts, no bound", learning.wilson(0, 0) == 0.0)
check("Confidence follows decided attempts: <5, <12, <25, 25+",
      [learning.confidence(n) for n in (4, 5, 12, 25)] ==
      ["Insufficient data", "Low", "Medium", "High"])
big = []
for i in range(30):
    big.append({"kind": "shipment", "run_id": "r%d" % (i % 4), "reference": "B%d" % i,
                "provider": "AFKL", "carrier": "Air France KLM Cargo",
                "result": "SUCCESS", "verified": True, "strategy_issue": "AFKL navigation",
                "strategies": ladder(False, i % 5 == 0, "ok" if i % 5 else None),
                "epoch": E + i, "at": store.stamp(E + i)})
bsnap = learning.build(big)
bnav = learning.find_issue(bsnap, provider="AFKL", name="navigation")[0]
check("The strategy with the better verified record ranks first",
      bnav["best"] == "clean edge, HTTP/2 disabled"
      and bnav["strategies"]["clean edge, HTTP/2 disabled"]["confidence"] == "Medium", str(bnav["ranking"]))
check("...and the order the automation actually ran is known separately",
      bnav["current_first"] == "fresh context")
check("A strategy with too few decided attempts is not ranked",
      learning.build(rows[:1])["issues"][0]["best"] is None)

rule("3. PROPOSALS — PROPOSE, NEVER DEPLOY")
props = bsnap["proposals"]
check("A proposal when the verified record disagrees with the running order",
      len(props) == 1 and props[0]["change"].startswith("Try clean edge, HTTP/2 disabled before fresh context")
      and props[0]["status"] == "PROPOSED", str(props))
check("...with its evidence: counts and runs", props[0]["evidence"]["best"]["decided"] == 24
      and props[0]["evidence"]["best"]["runs"])
check("No proposal from thin evidence", learning.build(rows)["proposals"] == [])
check("A person's decision is recorded, as audit", learning.decide(props[0]["id"], "APPROVED", "ops-lead")
      and learning.proposals(bsnap)[0]["status"] == "APPROVED")
check("...and only APPROVED / REJECTED are decisions", not learning.decide(props[0]["id"], "DEPLOYED", "x"))
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")
check("The automation never reads proposals or learning: it only records to the stores",
      "from intelligence import events as _intel_events, evidence as _intel_evidence" in SRC
      and "learning" not in SRC.split("from intelligence import")[1].split("\n")[0]
      and "proposals.json" not in SRC)

rule("4. RECOVERY PLANS")
plan = plans.build("AFKL", "AFKL navigation", bsnap)
names = [s["action"] for s in plan["steps"]]
check("The AFKL plan is the ladder, ranked by the verified record, then verify, then stop safely",
      names == ["clean edge, HTTP/2 disabled", "fresh context", "bundled chromium", "verify",
                "stop safely"], str(names))
check("Each step carries its verified history", "verified" in plan["steps"][0]["history"]
      and plan["steps"][2]["history"] == "no verified history yet")
check("Nothing is reported as executed when nothing ran",
      all(s["status"] == plans.NOT_EXECUTED for s in plan["steps"]))
check("Root causes are hypotheses, labelled so",
      plan["hypotheses"] and all(h["kind"] == "hypothesis" for h in plan["hypotheses"]))
check("The recommendation names its record", plan["recommendation"]["action"] ==
      "clean edge, HTTP/2 disabled" and "/24 verified" in plan["recommendation"]["why"])
live = {"status": "RUNNING", "attempts": [{"action": "wait_for_page_ready", "result": "FAILED",
                                           "verified": False, "time": "12:00:01"},
                                          {"action": "reload_page", "result": "SUCCESS",
                                           "verified": True, "time": "12:00:07"}]}
hp = plans.build("HUB", "TIMEOUT", snap, live=live)
st = {s["action"]: s["status"] for s in hp["steps"]}
check("A live recovery's real attempts set the step states",
      st.get("wait_for_page_ready") == "FAILED" and st.get("reload_page") == "SUCCEEDED (verified)"
      and st.get("retry_interaction_once") == plans.NOT_EXECUTED, str(st))
check("A human verification has no recovery plan — a person does it",
      plans.build("GRIMALDI", "HUMAN_VERIFICATION", snap)["steps"] == []
      and "bypass" in plans.build("GRIMALDI", "HUMAN_VERIFICATION", snap)["not_recoverable"])
check("Similar historical failures are counted", plan["similar"]["count"] == 30)

rule("5. THE BRIDGE RECORDS OUTCOMES — ONLY WHEN ATTACHED")
b = ControlTowerState()
b.run_started(run_id="run-x")
b.shipment_started({"bol_awb": "N1", "carrier": "DHL Express", "provider": "DHL"})
b.view_updated("BU", "ATA", "03/10/2026", verified=True)
b.shipment_finished("N1", "SUCCESS", "", {})
check("Nothing is recorded without attach()", events.all_events() == [])
b.attach_intelligence(events)
b.shipment_started({"bol_awb": "074-11111111", "carrier": "KLM Cargo", "provider": "AFKL"})
b.strategy_attempts("AFKL", "AFKL navigation", [
    {"attempt": 1, "strategy": "existing page", "awb_verified": False, "error": "ERR_HTTP2"},
    {"attempt": 2, "strategy": "fresh context", "awb_verified": True}])
b.provider_result({"provider": "AFKL", "tracking_status": "Arrived", "eta": "01/10/2026", "ata": "02/10/2026"})
b.view_updated("COE", "ETA", "01/10/2026", verified=True)
b.view_updated("BU", "ATA", "02/10/2026", verified=True)
b.shipment_finished("074-11111111", "SUCCESS", "", {})
b.shipment_started({"bol_awb": "074-22222222", "carrier": "KLM Cargo", "provider": "AFKL"})
b.recovery_plan("TIMEOUT", "slow", ["wait_for_page_ready"], {}, False)
b.recovery_attempt(1, 1, "wait_for_page_ready", .5, "SUCCESS", True)
b.recovery_done(True, "ok", verified=True)
b.view_updated("BU", "ATA", "02/10/2026", verified=None)
b.shipment_finished("074-22222222", "SUCCESS", "", {})
b.shipment_started({"bol_awb": "S9", "carrier": "Grimaldi", "provider": "GRIMALDI"})
b.shipment_finished("S9", "HUMAN_QUEUED", "parked", outcome="HUMAN ACTION QUEUED")
task = {"action_id": "t1", "run_id": "run-x", "reference": "S9", "carrier": "Grimaldi Lines",
        "status": "TIMEOUT", "created_epoch": time.time() - 100,
        "closed_at": store.stamp(), "reason": "human_verification_required"}
b.human_queue_changed([task])
b.human_queue_changed([task])
recorded = events.all_events()
kinds = [r["kind"] for r in recorded]
s1 = [r for r in recorded if r.get("reference") == "074-11111111"][0]
s2 = [r for r in recorded if r.get("kind") == "shipment" and r.get("reference") == "074-22222222"][0]
rec = [r for r in recorded if r["kind"] == "recovery"][0]
check("A verified success is recorded as verified, with its strategies",
      s1["verified"] is True and s1["strategies"][1]["page_verified"] is True
      and s1["run_id"] == "run-x")
check("A success whose read-back did not run is recorded as NOT verified",
      s2["verified"] is False and s2["result"] == "SUCCESS")
check("The recovery is joined to that unverified outcome",
      rec["shipment_verified"] is False and rec["attempts"][0]["verified"] is True)
check("A parked shipment is not an outcome", not any(r.get("reference") == "S9" and
                                                     r["kind"] == "shipment" for r in recorded))
check("A closed human task is recorded once, with its wait",
      kinds.count("human_task") == 1 and 90 <= [r for r in recorded
                                                if r["kind"] == "human_task"][0]["waited_s"] <= 130)
check("Every event carries its run", all(r.get("run_id") == "run-x" for r in recorded
                                         if r["kind"] != "question"))
check("The same learning reads it: the unverified recovery is not a success",
      learning.find_issue(learning.build(), provider="AFKL", name="TIMEOUT")[0]
      ["strategies"]["wait_for_page_ready"]["successes"] == 0)

rule("6. EVIDENCE — REAL IMAGES, WITH PROVENANCE")
try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None
SHOTS = {}
PAGES = {
    "carrier": "<h2>Tracking result</h2><p>Air France KLM Cargo</p><p>Air waybill 074-47798870</p>"
               "<p>Status: Arrived 03/10/2026</p>",
    "dashboard": "<h2>Grimaldi Lines</h2><p>Shipment S330348776</p><p>Status: Human timeout</p>"
                 "<p>Nothing was written</p>",
    "captcha": "<h2>Grimaldi Lines</h2><p>Enter the security code shown below</p>"
               "<p style='font-size:40px'>7Q4K</p>",
    "blur": "<p style='font-size:7px;color:#bbb;filter:blur(1.2px)'>S330348776 Human timeout</p>",
}
if sync_playwright is not None:
    try:
        with sync_playwright() as p:
            exe = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
            opts = {"headless": True}
            if exe:
                opts["executable_path"] = str(exe[-1])
            browser = p.chromium.launch(**opts)
            page = browser.new_page(viewport={"width": 900, "height": 320}, device_scale_factor=2)
            for key, html in PAGES.items():
                page.set_content("<body style='font:22px Arial;padding:24px'>" + html + "</body>")
                SHOTS[key] = WORK / (key + ".png")
                page.screenshot(path=str(SHOTS[key]))
            browser.close()
    except Exception as error:
        SHOTS = {}
        print("  (could not render test images: {0})".format(str(error)[:80]))
if not SHOTS:
    for name in ("capture", "upload", "ocr"):
        skip(name, "no browser to render test images")
else:
    text_file = WORK / "carrier.txt"
    text_file.write_text("Tracking result\nAir France KLM Cargo\nAir waybill 074-47798870\n"
                         "Status: Arrived 03/10/2026\n", encoding="utf-8")
    cap = evidence.register_capture(SHOTS["carrier"], run_id="run-x", reference="074-47798870",
                                    carrier="Air France KLM Cargo", provider="AFKL",
                                    event="afkl_navigation_error", text_path=text_file)
    check("A real capture is indexed with run, shipment, carrier, event, size and hash",
          cap and cap["run_id"] == "run-x" and cap["sha256"] and cap["width"] == 1800
          and cap["source"] == "browser_capture")
    check("It is found again by shipment, by carrier and as a failure",
          evidence.search(reference="074-47798870")[0]["id"] == cap["id"]
          and evidence.search(provider="AFKL", failures_only=True)[0]["id"] == cap["id"])
    entry, path = evidence.file_for(cap["id"])
    check("Its file is served only while it matches the hash it was indexed with",
          path is not None and path.read_bytes()[:4] == b"\x89PNG")
    copy = WORK / "tamper.png"
    copy.write_bytes(SHOTS["carrier"].read_bytes())
    tam = evidence.register_capture(copy, run_id="run-x", reference="T", event="error")
    copy.write_bytes(SHOTS["dashboard"].read_bytes())
    check("...a file changed after capture is no longer evidence", evidence.file_for(tam["id"])[1] is None)
    check("Provenance says where and when", "during run run-x" in evidence.describe(cap)
          and "074-47798870" in evidence.describe(cap))
    reading = vision.read_image(SHOTS["carrier"], evidence.page_text(cap))
    check("A capture is read from the page text captured with it — exact",
          reading["engine"] == "page text captured with the screenshot"
          and {"field": "reference", "value": "074-47798870"}.items() <=
          next(f for f in reading["facts"] if f["field"] == "reference").items()
          and all(f["confidence"] == "High" for f in reading["facts"]))
    ok, msg = evidence.register_upload(b"not an image at all", "x.txt")
    check("A non-image upload is refused", not ok and "PNG" in msg)
    ok, msg = evidence.register_upload(b"\x89PNG\r\n\x1a\n" + b"0" * (evidence.MAX_UPLOAD + 1))
    check("An upload over 6 MB is refused", not ok and "6 MB" in msg)
    if vision.tesseract() is None:
        for name in ("ocr facts", "ocr verification screen", "ocr unclear"):
            skip(name, "Tesseract is not installed")
    else:
        dash_read = vision.read_image(SHOTS["dashboard"])
        values = {(f["field"], f["value"]) for f in dash_read["facts"]}
        check("OCR reads carrier, reference and status off a real image",
              ("carrier", "Grimaldi Lines") in values and ("reference", "S330348776") in values
              and ("status", "Human timeout") in values, str(values))
        check("...as visual facts with their evidence line and confidence",
              all(f["source"] == "OCR" and f["evidence"] for f in dash_read["facts"]))
        check("An inference is drawn only from a visible fact, and says which",
              any("before a Hub write" in i["text"] and "Human timeout" in i["because"]
                  for i in dash_read["inferences"]), str(dash_read["inferences"]))
        cap_read = vision.read_image(SHOTS["captcha"])
        check("A verification screen is not read at all", cap_read["verification_screen"]
              and cap_read["facts"] == [] and "7Q4K" not in json.dumps(cap_read))
        ok, msg = evidence.register_upload(SHOTS["captcha"].read_bytes(), "captcha.png",
                                           read=vision.read_image)
        check("...an upload of one is refused and not kept", not ok and "security verification" in msg
              and not any("captcha" in str(e.get("name")) for e in evidence.entries()))
        ok, up = evidence.register_upload(SHOTS["dashboard"].read_bytes(), "dash.png",
                                          read=vision.read_image)
        check("A screenshot upload is kept, as a user upload, in the uploads folder",
              ok and up["source"] == "user_upload"
              and Path(up["path"]).parent == evidence.uploads_dir().resolve())
        blur = vision.read_image(SHOTS["blur"])
        check("An image too faint to read gives no invented facts",
              not any(f["value"] == "S330348776" and f["confidence"] == "High"
                      for f in blur["facts"]), str(blur))
    facts, unclear = vision.parse([("Shipment S330348776", 41.0)], "OCR")
    check("A low-confidence read is UNCLEAR, not a fact", facts == [] and unclear
          and unclear[0]["confidence"] == "Low")
    facts, unclear = vision.parse([("$330348776", 96.0)], "OCR")
    check("A number glued to a confusable character ('$330348776') is ambiguous, not a reference",
          not any(f["field"] == "reference" for f in facts)
          and unclear and unclear[0]["kind"] == "ambiguous" and unclear[0]["digits"] == "330348776")
    facts, unclear = vision.parse([("Shipment S33O348776", 93.0)], "OCR")
    check("A misread reference is not repaired into a real one",
          not any(f["field"] == "reference" for f in facts + unclear))
    a = {"facts": [{"field": "carrier", "value": "Grimaldi Lines"}, {"field": "status", "value": "Failed"}]}
    c = {"facts": [{"field": "carrier", "value": "Grimaldi Lines"}, {"field": "status", "value": "Human timeout"}]}
    diff = vision.compare(a, c)
    check("Comparison separates what is the same from what differs",
          diff["same"] == [("carrier", ["Grimaldi Lines"])] and diff["different"][0][0] == "status")

rule("7. update_eta NEVER CAPTURES A VERIFICATION SCREEN")
import update_eta as A                                        # noqa: E402
A.write_log = lambda *a, **k: None


class FakePage(object):
    def __init__(self, text):
        self.text = text
        self.shots = []

    def locator(self, selector):
        page = self

        class L(object):
            def count(self_):
                return 0

            def inner_text(self_, timeout=None):
                return page.text
        return L()

    def screenshot(self, path=None, full_page=False):
        self.shots.append(path)
        Path(path).write_bytes(SHOTS["carrier"].read_bytes() if SHOTS else b"\x89PNG\r\n\x1a\n")


A.SCREENSHOT_FOLDER = WORK / "shots"
A.SCREENSHOT_FOLDER.mkdir(exist_ok=True)
A.LOG_FOLDER = WORK / "logs"
A.LOG_FOLDER.mkdir(exist_ok=True)
gnet = FakePage("Container Tracking  Shipment #  Security Code  Enter code  Search")
A.take_screenshot(gnet, "S330348776", "error")
check("A page showing a security code is never photographed", gnet.shots == [])
A.INTEL["evidence"] = evidence
result = FakePage("Tracking result  Shipment # S330348776  Actual Arrival 03/10/2026")
A.HUMAN_STATE["shipment"] = {"bol_awb": "S330348776", "carrier": "Grimaldi", "provider": "GRIMALDI",
                             "lookup_ref": "S330348776"}
A.take_screenshot(result, "S330348776", "after_human_result")
idx = evidence.search(reference="S330348776", source="browser_capture")
check("A result page is captured and indexed with run, carrier and its page text",
      result.shots and idx and idx[0]["run_id"] == A.RUN_ID and idx[0]["carrier"] == "Grimaldi"
      and idx[0]["text_path"] and "Actual Arrival" in evidence.page_text(idx[0]))
A.INTEL["evidence"] = None
check("The CAPTCHA wait no longer photographs the challenge",
      "take_screenshot(" not in SRC.split("def await_human_verification")[1].split("\ndef ")[0])

rule("8. MATURITY — EARNED, NEVER GRANTED BY TIME")
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "maturity")
events.invalidate()
check("A fresh install is not rated", maturity.status()["level"] == 0)
check("An empty month passing promotes nothing",
      maturity.evaluate(time.strftime("%Y-%m", time.localtime(month_epoch(2))), record=True)["promoted"]
      is False and maturity.level() == 0)
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "maturity2")
events.invalidate()
rich = []
for i in range(60):
    for months_ago in (3, 2, 1):
        e = month_epoch(months_ago, 5) + i * 60
        rich.append({"kind": "shipment", "run_id": "m%d-%d" % (months_ago, i % 4), "reference": "R%d" % i,
                     "provider": "AFKL", "carrier": "Air France KLM Cargo", "result": "SUCCESS",
                     "verified": True, "strategy_issue": "AFKL navigation",
                     "strategies": ladder(False, i % 6 != 0, "ok" if i % 6 == 0 else None),
                     "epoch": e, "at": store.stamp(e)})
        rich.append({"kind": "question", "intent": "attention", "answered": True, "epoch": e})
        rich.append({"kind": "feedback", "verdict": "helpful", "epoch": e})
        for k, cls in enumerate(("FAILED", "PARTIAL", "HUMAN_TIMEOUT")):
            if i < 3:
                rich.append({"kind": "shipment", "run_id": "m%d-%d" % (months_ago, i), "reference": "F%d%d" % (i, k),
                             "provider": "MSC", "carrier": "MSC", "result": cls, "outcome_class": "NO RESULT %d" % k,
                             "verified": False, "epoch": e, "at": store.stamp(e)})
for r in rich:
    events.record(r.pop("kind"), **r)
first = time.strftime("%Y-%m", time.localtime(month_epoch(3)))
check("This data would satisfy several levels at once",
      all(c["met"] for c in maturity.check(1, maturity.metrics(first)))
      and all(c["met"] for c in maturity.check(2, maturity.metrics(first))))
done = maturity.ensure_evaluated()
levels = [(d["month"], d["level_before"], d["level_after"]) for d in done]
check("One level per evaluation, at most — even when more is met",
      [d["level_after"] - d["level_before"] for d in done] == [1, 1, 1], str(levels))
check("Each ended month is evaluated once, and the current month never",
      len(done) == 3 and maturity.ensure_evaluated() == []
      and time.strftime("%Y-%m") not in [d["month"] for d in done])
check("The record keeps metrics and every criterion with value and threshold",
      all(d["metrics"]["verified_signals"] and d["criteria"] and
          all("value" in c and "minimum" in c for c in d["criteria"]) for d in done))
st = maturity.status()
check("Level 3 is earned here, and level 4 is not: no approved proposal, no confirmed accuracy",
      st["level"] == 3 and not all(c["met"] for c in st["next_criteria"])
      and any(c["metric"] == "approved_proposals" and not c["met"] for c in st["next_criteria"]),
      str([(c["metric"], c["met"]) for c in st["next_criteria"]]))
check("A rate without its minimum sample fails, it does not pass",
      any(c["metric"] == "classification_accuracy" and "not enough data" in c["why"]
          for c in st["next_criteria"]))
before = json.dumps(maturity.state())
maturity.evaluate(time.strftime("%Y-%m"), record=True)
check("Evaluating the current month records nothing", json.dumps(maturity.state()) == before)
rv = maturity.review(time.strftime("%Y-%m", time.localtime(month_epoch(1))))
text = maturity.render_review(rv)
check("The monthly review reports progress, the top behaviour, the weakness and the next goal",
      text.startswith("ATLAS MONTHLY REVIEW") and "Top learned behaviour" in text
      and "Biggest weakness" in text and "Next month goal" in text and "Requirements:" in text, text[:300])

rule("9. ATLAS ANSWERS FROM THE STORES — AND LABELS WHAT IS WHAT")
STATE = {"run": {"run_id": "m-now", "status": "running"}, "shipments": [], "counters": {},
         "current": {}, "timeline": [], "exceptions": []}


def ask(q, context=None):
    return assistant.answer(q, STATE, context or {})


r = ask("What have we learned?")
check("'What have we learned?' — counted facts and verified patterns",
      r["intent"] == "learned" and "**Fact**" in r["answer"] and "verified" in r["answer"])
r = ask("What keeps failing?")
check("'What keeps failing?' — recurring issues with their resolution", "MSC" in r["answer"]
      and "resolved to a verified outcome" in r["answer"], r["answer"][:200])
r = ask("Which recovery strategy works best for AFKL?")
check("'Which recovery strategy works best for AFKL?' — the verified record and a recommendation",
      "**Learned pattern**" in r["answer"] and "**Recommendation**" in r["answer"]
      and "fresh context" in r["answer"], r["answer"][:300])
r = ask("How confident are you?")
check("'How confident are you?' — the thresholds and the counts", "decided attempts" in r["answer"])
r = ask("Why do you recommend this?")
check("'Why do you recommend this?' — the record behind it, with evidence runs",
      "evidence runs" in r["answer"] and "lower 95% bound" in r["answer"])
r = ask("Create a recovery plan for AFKL")
check("'Create a recovery plan' — steps, history, status, and what actually executes",
      r["answer"].startswith("RECOVERY PLAN") and "NOT EXECUTED" in r["answer"]
      and "**Inference**" in r["answer"] and "SHADOW" in r["answer"])
r = ask("What should we try next for AFKL?")
check("'What should we try next?' — a recommendation, untried options marked unverified",
      "**Recommendation**" in r["answer"] or "**Unverified**" in r["answer"])
r = ask("How many verified recovery strategies have we learned?")
check("'How many verified recovery strategies?' — counted", "**Fact**" in r["answer"]
      and "verified success" in r["answer"])
r = ask("What changed compared with last month?")
check("'What changed compared with last month?' — month against month",
      r["intent"] == "month_compare" and "→" in r["answer"])
r = ask("What is ATLAS's current star level?")
check("'What is ATLAS's current star level?' — the earned level and what the next needs",
      "★★★" in r["answer"] and "Strategist" in r["answer"] and "✗" in r["answer"], r["answer"][:200])
r = ask("What did ATLAS learn this month?")
check("'What did ATLAS learn this month?' — the monthly review", r["answer"].startswith("ATLAS MONTHLY REVIEW"))
r = ask("How long do human actions usually take?")
check("'How long do human actions usually take?' — from recorded tasks, or says none",
      r["intent"] == "human_patterns")
r = ask("Show me the failed screenshot")
check("No capture on record: it says so, and makes nothing up",
      "no screenshot was captured" in r["answer"] and "won't make one up" in r["answer"]
      and not r.get("evidence"))
r = ask("Read the security code from this screenshot")
check("A request to read a security code is refused before anything else",
      r["intent"] == "code_request" and "won't" in r["answer"])
check("Answers keep their kinds apart: facts, patterns, inferences, recommendations",
      all(label in atlas_learning.__doc__ for label in
          ("Fact", "Learned pattern", "Inference", "Recommendation", "Unverified")))

rule("10. THE SERVER — EVIDENCE, UPLOADS, LEARNING, QUESTIONS")
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "server")
events.invalidate()
from dashboard import server                                  # noqa: E402
PORT = 9763
server.start(port=PORT, open_browser=False, host="127.0.0.1")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path, body=None, raw=None, headers=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=data,
                                 headers=headers or {"Content-Type": "application/json"})
    try:
        with OPENER.open(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


time.sleep(0.4)
code, body = call("/api/atlas/learning")
check("/api/atlas/learning answers", code == 200 and "summary" in json.loads(body))
code, body = call("/api/atlas/maturity")
check("/api/atlas/maturity answers with level, criteria and review",
      code == 200 and "status" in json.loads(body) and json.loads(body)["review_text"])
code, body = call("/api/evidence/file?id=../../etc/passwd")
check("An evidence id that is not indexed is a 404, whatever it says", code == 404)
call("/api/ask", {"question": "What keeps failing on 074-47798870?"})
check("With learning off, questions are not recorded", events.all_events(("question",)) == [])
server.LEARNING["on"] = True
call("/api/ask", {"question": "What keeps failing on 074-47798870?"})
call("/api/ask", {"question": "Type the security code for me"})
qs = events.all_events(("question",))
check("With learning on: the intent and a reference-free pattern, never the reference",
      qs and qs[0]["intent"] == "keeps_failing" and "<ref>" in qs[0]["pattern"]
      and "074" not in qs[0]["pattern"], str(qs[:1]))
check("...and a code request keeps no pattern at all",
      qs[-1]["intent"] == "code_request" and qs[-1].get("pattern") is None)
code, body = call("/api/feedback", {"question": "q", "answer": "a", "verdict": "recovery_worked"})
code2, body2 = call("/api/feedback", {"question": "q", "answer": "a", "verdict": "it_is_verified"})
check("Feedback takes the new verdicts, as opinions; an unknown verdict is refused",
      code == 200 and code2 == 400 and events.all_events(("feedback",))[-1]["verdict"] == "recovery_worked")
if SHOTS and vision.tesseract():
    code, body = call("/api/evidence/upload", raw=SHOTS["dashboard"].read_bytes(),
                      headers={"Content-Type": "image/png", "X-Filename": "dash.png"})
    up = json.loads(body)
    check("An uploaded screenshot is stored and read", up.get("accepted") and up["reading"]["facts"])
    code, img = call(up["evidence"]["url"])
    check("...and served back as the same bytes", code == 200 and img == SHOTS["dashboard"].read_bytes())
    r = assistant.answer("Extract the shipment number from this screenshot", STATE,
                         {"evidence_id": up["evidence"]["id"]})
    check("'Extract the shipment number from this screenshot' — the reference, with confidence",
          "S330348776" in r["answer"] and "Visual fact" in r["answer"] and r.get("evidence"),
          r["answer"][:200])
    r = assistant.answer("What does this screenshot tell us?", STATE,
                         {"evidence_id": up["evidence"]["id"]})
    check("'What does this screenshot tell us?' — facts, then inferences that say why",
          "**Visual fact**" in r["answer"] and "**Inference**" in r["answer"] and "because" in r["answer"])
    code, body = call("/api/evidence/upload", raw=SHOTS["captcha"].read_bytes(),
                      headers={"Content-Type": "image/png", "X-Filename": "c.png"})
    check("A verification screen upload is refused by the server",
          json.loads(body)["accepted"] is False and "7Q4K" not in body.decode())
else:
    skip("server uploads", "no rendered images or no Tesseract")

rule("11. NOTHING SECRET IS KEPT")
store.append("probe.jsonl", {"password": "hunter2", "note": "token=abcdef1234 ok",
                             "security_code": "7Q4K", "nested": {"api_key": "x", "fine": "y"}})
kept = (store.folder() / "probe.jsonl").read_text()
check("Secret-named fields are dropped, not masked; tokens in text are redacted",
      "hunter2" not in kept and "7Q4K" not in kept and "abcdef1234" not in kept
      and '"fine": "y"' in kept and "api_key" not in kept)
check("A question pattern drops references and numbers",
      events.question_pattern("Why did 074-47798870 fail at 12:30?") == "why did <ref> fail at <n>:<n>?")
check("The learning layer never touches a browser",
      all("playwright" not in (HERE / "intelligence" / f).read_text().lower().replace(
          "no playwright", "") for f in ("events.py", "learning.py", "plans.py", "maturity.py",
                                         "evidence.py", "vision.py", "store.py")))

rule("12. THE HUMAN SIDE: REVIEW, DECIDE, EVALUATE")
import io                                                     # noqa: E402
import contextlib                                             # noqa: E402
from intelligence import review                               # noqa: E402
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "review")
events.invalidate()
learning.invalidate()
for r in big:
    events.record("shipment", **{k: v for k, v in r.items() if k != "kind"})
learning.invalidate()
pid = learning.snapshot()["proposals"][0]["id"]


def run_cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = review.main(list(argv))
    return code, out.getvalue()


code, out = run_cli("proposals")
check("The review lists each proposal with its verified evidence", code == 0 and pid in out
      and "verified" in out and "tested deployment" in out)
code, out = run_cli("approve", pid, "--by", "Ops lead")
check("Approving names who decided and changes nothing in the automation",
      code == 0 and "Nothing in the automation changed" in out
      and learning.snapshot()["proposals"][0]["status"] == "APPROVED"
      and learning.snapshot()["proposals"][0]["decided_by"] == "Ops lead")
code, out = run_cli("approve", "nope000000", "--by", "x")
check("An unknown proposal cannot be approved", code == 1)
code, out = run_cli("status")
check("Status shows the level and the next level's criteria as a preview",
      code == 0 and "preview, not an evaluation" in out)
code, out = run_cli("learned")
check("'learned' prints issues with verified, unverified and skipped kept apart",
      "verified" in out and "unverified" in out and "skipped" in out)
check("The automation evaluates ended months at start, as well as the dashboard",
      "_intel_maturity.ensure_evaluated()" in SRC)
uv = SRC.split("def update_one_view")[1].split("\ndef ")[0]
check("A verified Hub write is captured as evidence — viewport only, real runs only",
      'if episode_verified is True and INTEL.get("evidence") is not None:' in uv
      and "full_page=False" in uv)

print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(len(PASS), len(FAIL),
                                        ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
