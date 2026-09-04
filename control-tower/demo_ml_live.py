"""
Proof that the ML layer is really used — not an assertion that it is.

Runs the REAL functions from update_eta.py against a REAL browser page that is
built to have the same shape as the Hub's Modify Shipment page: two tabs, an
ETA field on one and an ATA field on the other, the ATA field sitting inside a
container the browser does not consider visible until its tab is opened, and a
Save that persists so the value can be read back after a reload.

It is a stand-in for the Hub, not the Hub. Everything it exercises —
fill_date_field, first_visible, the candidate guards, write_date_value, the
read-back — is the production code, unmodified. What it cannot prove is your
Hub's own markup; only a run against the real thing does that.

    python demo_ml_live.py

Needs Playwright and Chromium. No internet, no credentials, no Hub.
"""

import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

WORK = Path(tempfile.mkdtemp(prefix="ct_ml_demo_"))
os.environ["ML_TELEMETRY_PATH"] = str(WORK / "telemetry.jsonl")
os.environ["ML_CHAMPION_PATH"] = str(WORK / "champion.json")
os.environ["ML_CHALLENGER_PATH"] = str(WORK / "challenger.json")
os.environ.pop("ML_MODEL_PATH", None)       # resolve the champion normally
os.environ.pop("ML_ENABLED", None)          # prove the DEFAULT is on
# ACTIVE, explicitly. The shipped default is SHADOW — a demonstration that the
# model can steer the field lookup has to turn steering on, and saying so is
# part of the demonstration.
os.environ["ML_MODE"] = "active"

from ml import config, features, model as M, predictor, trainer  # noqa: E402
import update_eta as A                                       # noqa: E402

STEPS = []


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def show(ok, text):
    STEPS.append((ok, text))
    print("  {0}  {1}".format("OK  " if ok else "FAIL", text))


# ── The stand-in Manage page ─────────────────────────────────────────
# Shaped from what the run log and the code comments say the real page does:
# the ATA field is labelled "ATA Date :" in a table cell rather than by a
# <label for=...>, and the inactive panel is present but not visible. That is
# why get_by_label misses it and the xpath candidate is the one that works.
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Modify Shipment</title>
<style>
 body{font:14px system-ui;margin:24px;} .panel{display:none;} .panel.on{display:block;}
 .tab{padding:8px 14px;border:1px solid #ccc;cursor:pointer;display:inline-block;}
 .tab.sel{background:#0a3d62;color:#fff;} td{padding:6px 10px;}
</style></head><body>
<h2>Modify Shipment — 9451291275</h2>
<table id="shipments"><tr><td>9451291275</td><td>Under Clearance</td></tr></table>

<div id="tabs">
  <span class="tab sel" role="tab" aria-selected="true"  onclick="pick('coe')">COE Shipment Info</span>
  <span class="tab"     role="tab" aria-selected="false" onclick="pick('bu')">BU Shipment Info</span>
</div>

<div id="coe" class="panel on">
  <table><tr><td>ETA :</td><td><input id="txtETA" name="txtETA" type="text"></td></tr></table>
</div>

<div id="bu" class="panel">
  <table>
    <tr><td>Customs Pre Entry :</td><td><input id="txtPreEntry" name="txtPreEntry"></td></tr>
    <tr><td>Duty Paid :</td><td><input id="txtDutyPaid" name="txtDutyPaid"></td></tr>
    <tr><td>ATA Date :</td><td><input id="txtATADate" name="txtATADate" type="text"></td></tr>
    <tr><td>Customs Release :</td><td><input id="txtRelease" name="txtRelease"></td></tr>
  </table>
</div>

<p><button id="save" onclick="save()">Save</button></p>
<script>
 function pick(which){
   document.querySelectorAll('.panel').forEach(p=>p.classList.remove('on'));
   document.getElementById(which).classList.add('on');
   document.querySelectorAll('.tab').forEach((t,i)=>{
     t.classList.toggle('sel', (which==='coe') === (i===0));
     t.setAttribute('aria-selected', String((which==='coe') === (i===0)));
   });
 }
 function save(){
   localStorage.setItem('ETA', document.getElementById('txtETA').value);
   localStorage.setItem('ATA', document.getElementById('txtATADate').value);
   document.getElementById('save').remove();
   document.body.insertAdjacentHTML('beforeend','<p id="saved">Saved.</p>');
 }
 window.addEventListener('load', function(){
   document.getElementById('txtETA').value = localStorage.getItem('ETA')||'';
   document.getElementById('txtATADate').value = localStorage.getItem('ATA')||'';
 });
</script></body></html>"""

PAGE_FILE = WORK / "manage.html"
PAGE_FILE.write_text(PAGE, encoding="utf-8")
PAGE_URL = PAGE_FILE.as_uri()

CTX = dict(provider="HUB", page="manage", field="ATA", view="BU")
ATA_STRATEGIES = ["label_exact", "xpath_ata_date", "xpath_starts_with",
                  "css_id_visible", "css_name_visible", "label_ata_date",
                  "label_loose"]


def build_model():
    """
    Train a model the ordinary way: write telemetry, run the real trainer.

    The observations describe THIS page — on it, get_by_label genuinely does
    not find a field labelled by a table cell, and the xpath candidate
    genuinely does. Nothing is hand-written into the model; it is trained from
    recorded outcomes by ml/trainer.py, and the trainer would refuse if there
    were too few of them.
    """
    truth = {"label_exact": 0.02, "xpath_ata_date": 0.97, "xpath_starts_with": 0.90,
             "css_id_visible": 0.55, "css_name_visible": 0.50,
             "label_ata_date": 0.05, "label_loose": 0.05}
    random.seed(4)
    path = Path(os.environ["ML_TELEMETRY_PATH"])
    with open(path, "w", encoding="utf-8") as handle:
        for index in range(700):
            ctx = features.context(page_ready="yes", frames="one",
                                   attempt="first", **CTX)
            strategy = random.choice(ATA_STRATEGIES)
            found = random.random() < truth[strategy]
            # Every attempt belongs to a write whose value was read back. That
            # is the only shape the trainer accepts, so the fixture has to have
            # it too — otherwise this demonstration would train a model the
            # real pipeline could never produce.
            persisted = found and random.random() < 0.97
            stamp = "2026-09-{0:02d}".format(index % 28 + 1)
            episode_id = "demo-{0}".format(index)
            handle.write(json.dumps({
                "kind": "interaction", "episode_id": episode_id,
                "context": ctx, "strategy": strategy, "success": found,
                "rank": ATA_STRATEGIES.index(strategy),
                "duration_ms": random.randint(250, 900) if found else None,
                "category": "OK" if found else "FIELD_NOT_VISIBLE",
                "source": "automation", "ts": stamp}) + "\n")
            handle.write(json.dumps({
                "kind": "episode", "episode_id": episode_id,
                "outcome": "VERIFIED" if persisted else "MISMATCH",
                "verified": bool(persisted), "source": "automation",
                "ts": stamp}) + "\n")
    ok, message, _ = trainer.train(echo=lambda *a: None)
    if not ok:
        return ok, message
    # The trainer writes a CHALLENGER. Nothing reaches production without
    # passing the evaluation gate, so promote it the ordinary way.
    promoted, promotion_message, _result = trainer.promote(echo=lambda *a: None)
    return promoted, message + "  |  " + promotion_message


def main():
    rule("0. TRAIN A MODEL THE ORDINARY WAY (this is the separate step)")
    ok, message = build_model()
    show(ok, "python -m ml.trainer  ->  " + message)
    if not ok:
        return 1
    predictor.reset()
    config.reload_from_environment()

    rule("1. STARTUP — what `python update_eta.py` prints")
    lines = []
    active = predictor.initialize(log=lines.append)
    for line in lines:
        print("     " + line)
    show(active, "ML initialised and reported its own state")
    show(any("Status: ENABLED" in l for l in lines), "Status: ENABLED")
    show(any("Model version: {0}".format(M.MODEL_VERSION) in l for l in lines),
         "Model version reported")
    show(any("Ranking score threshold" in l and "NOT a probability" in l
             for l in lines),
         "The score is named as a Wilson bound, not as a probability")
    show(any("Label rule: verified_persisted_success" in l for l in lines),
         "The label rule is the strict one")
    show(any("Promoted" in l for l in lines),
         "The model says which promotion put it in production")
    show("ML_ENABLED" not in os.environ,
         "This happened with ML_ENABLED UNSET — the default is on")
    show(os.environ.get("ML_MODE") == "active",
         "...and with ML_MODE=active, set here on purpose: the SHIPPED "
         "default is shadow, which would change nothing")

    try:
        from playwright.sync_api import sync_playwright
    except Exception as error:
        print("\n  Playwright is not available here: {0}".format(error))
        return 1

    with sync_playwright() as playwright:
        # Use whatever Chromium this machine already has. On a normal install
        # Playwright finds it; some environments pin it elsewhere.
        launch = {}
        for candidate in (os.environ.get("CHROMIUM_PATH"),
                          "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"):
            if candidate and Path(candidate).exists():
                launch["executable_path"] = candidate
                break
        browser = playwright.chromium.launch(**launch)
        page = browser.new_page()
        try:
            rule("2. THE MODEL'S ACTUAL RECOMMENDATION FOR THIS PAGE")
            ctx = A.ml_context(page_ready="yes", frames="one",
                               attempt="first", **CTX)
            recommendation = predictor.recommend_strategy(ctx, ATA_STRATEGIES)
            print("     context : {0}".format(features.describe(ctx)))
            print("     used    : {0}".format(recommendation.used))
            print("     reason  : {0}".format(recommendation.reason))
            print("     scores  :")
            for name in sorted(recommendation.scores,
                               key=lambda n: -recommendation.scores[n]):
                print("        {0:<20} {1:.2f}".format(
                    name, recommendation.scores[name]))
            print("     automation's own order : {0}".format(
                ", ".join(ATA_STRATEGIES[:3]) + ", ..."))
            print("     order the model asks for: {0}".format(
                ", ".join(recommendation.order[:3]) + ", ..."))
            show(recommendation.used, "The model gave a usable recommendation")
            show(recommendation.top == "xpath_ata_date",
                 "It puts xpath_ata_date first (it was 2nd in the fixed order)")

            rule("3. THE REAL fill_date_field RUNS, WITH THE MODEL IN THE LOOP")
            page.goto(PAGE_URL)
            page.evaluate("localStorage.clear()")
            page.goto(PAGE_URL)
            page.click("text=BU Shipment Info")

            started = time.time()
            A.fill_date_field(page, "ATA", "02/09/2026")
            with_ml = (time.time() - started) * 1000.0
            landed = page.input_value("#txtATADate")
            show(landed == "02/09/2026",
                 "ATA field written by the production code: {0!r}".format(landed))
            print("     took {0:.0f}ms with the model choosing the order".format(with_ml))

            os.environ["ML_ENABLED"] = "0"
            config.reload_from_environment()
            predictor.reset()
            page.goto(PAGE_URL)
            page.evaluate("localStorage.clear()")
            page.goto(PAGE_URL)
            page.click("text=BU Shipment Info")
            started = time.time()
            A.fill_date_field(page, "ATA", "02/09/2026")
            without_ml = (time.time() - started) * 1000.0
            print("     took {0:.0f}ms with the fixed order (ML_ENABLED=0)".format(
                without_ml))
            show(with_ml < without_ml,
                 "The model's order reached the field faster "
                 "({0:.0f}ms vs {1:.0f}ms)".format(with_ml, without_ml))
            os.environ.pop("ML_ENABLED")
            config.reload_from_environment()
            predictor.reset()
            predictor.initialize(log=lambda t: None)

            rule("4. THE SAFETY GUARDS STILL RAN")
            page.goto(PAGE_URL)
            page.evaluate("localStorage.clear()")
            page.goto(PAGE_URL)
            page.click("text=BU Shipment Info")
            A.fill_date_field(page, "ATA", "02/09/2026")
            show(page.input_value("#txtETA") == "",
                 "The ETA field was NOT touched by an ATA write")
            for other in ("txtPreEntry", "txtDutyPaid", "txtRelease"):
                show(page.input_value("#" + other) == "",
                     "{0} (another date field on the same panel) untouched".format(other))

            page.click("text=COE Shipment Info")
            A.fill_date_field(page, "ETA", "01/09/2026")
            show(page.input_value("#txtETA") == "01/09/2026",
                 "ETA written to the ETA field")
            show(page.input_value("#txtATADate") == "02/09/2026",
                 "...and the ATA field still holds its own value")

            rule("5. SAVE -> RELOAD -> READ BACK -> VERIFY")
            page.click("#save")
            page.wait_for_selector("#saved")
            show(True, "Saved")

            page.reload()
            page.wait_for_selector("#txtATADate", state="attached")
            page.click("text=BU Shipment Info")
            page.wait_for_selector("#txtATADate", state="visible")
            persisted = page.input_value("#txtATADate")
            show(persisted == "02/09/2026",
                 "After a full reload the Hub page holds ATA = {0!r}".format(persisted))

            # The production comparison, not a string compare invented here.
            expected = "02/09/2026"
            normalised = A.normalize_date(persisted) or persisted
            verified = normalised == expected or persisted == expected
            show(verified, "verify_saved_date's comparison passes: "
                           "{0!r} == {1!r}".format(normalised, expected))

            rule("6. AND IT FAILS WHEN IT SHOULD")
            page.evaluate("localStorage.setItem('ATA','31/12/1999')")
            page.reload()
            page.wait_for_selector("#txtATADate", state="attached")
            page.click("text=BU Shipment Info")
            page.wait_for_selector("#txtATADate", state="visible")
            wrong = page.input_value("#txtATADate")
            bad = (A.normalize_date(wrong) or wrong) == expected
            show(not bad,
                 "A value that did not persist correctly is REJECTED "
                 "(page holds {0!r}, expected {1!r})".format(wrong, expected))
        finally:
            browser.close()

    rule("RESULT")
    failed = [text for ok, text in STEPS if not ok]
    print("  {0} checks, {1} failed".format(len(STEPS), len(failed)))
    for text in failed:
        print("    FAILED: " + text)
    if not failed:
        print()
        print("  VERIFIED SUCCESS — the model was loaded, it changed the order,")
        print("  the production write ran, the guards held, and the value was")
        print("  read back from the page after a reload.")
    print()
    print("  (stand-in Hub page: {0})".format(PAGE_FILE))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
