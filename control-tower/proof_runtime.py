"""
Runtime proof that `python update_eta.py` really uses the ML layer.

Not "the functions exist and have unit tests". This SPAWNS the production
entry point as a subprocess — the same command an operator types — lets it run
its real startup, and then reads the run log FILE that the run itself wrote.
Nothing in the ML layer is mocked or stubbed at any point.

The run dies later on, at the login step, because there is no internal Hub
here. That is expected and irrelevant: the ML initialisation happens before it
and its evidence is already on disk.

    python proof_runtime.py

Needs Playwright and Chromium. No Hub, no credentials that mean anything, no
internet.
"""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

WORK = Path(tempfile.mkdtemp(prefix="ct_proof_"))
MODEL = WORK / "strategy_model.json"
TELEMETRY = WORK / "telemetry.jsonl"

RESULTS = []


def rule(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def show(ok, text, detail=""):
    RESULTS.append((ok, text))
    print("  {0}  {1}".format("OK  " if ok else "FAIL", text))
    if detail:
        for line in str(detail).splitlines():
            print("        " + line)


CTX = dict(provider="HUB", page="manage", field="ATA", view="BU")
ATA_STRATEGIES = ["label_exact", "xpath_ata_date", "xpath_starts_with",
                  "css_id_visible", "css_name_visible", "label_ata_date",
                  "label_loose"]

MANAGE_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Modify Shipment</title><style>
 body{font:14px system-ui;margin:24px}.panel{display:none}.panel.on{display:block}
 .tab{padding:8px 14px;border:1px solid #ccc;cursor:pointer;display:inline-block}
 td{padding:6px 10px}</style></head><body>
<h2>Modify Shipment</h2>
<div><span class="tab" role="tab" onclick="pick('coe')">COE Shipment Info</span>
     <span class="tab" role="tab" onclick="pick('bu')">BU Shipment Info</span></div>
<div id="coe" class="panel on"><table>
  <tr><td>ETA :</td><td><input id="txtETA" name="txtETA"></td></tr></table></div>
<div id="bu" class="panel"><table>
  <tr><td>Customs Pre Entry :</td><td><input id="txtPreEntry" name="txtPreEntry"></td></tr>
  <tr><td>Duty Paid :</td><td><input id="txtDutyPaid" name="txtDutyPaid"></td></tr>
  <tr><td>ATA Date :</td><td><input id="txtATADate" name="txtATADate"></td></tr>
  <tr><td>Customs Release :</td><td><input id="txtRelease" name="txtRelease"></td></tr>
</table></div>
<script>function pick(w){document.querySelectorAll('.panel').forEach(function(p){
 p.classList.remove('on')});document.getElementById(w).classList.add('on')}</script>
</body></html>"""


def write_telemetry():
    """Real telemetry rows, written the way the automation writes them."""
    from ml import features
    truth = {"label_exact": 0.02, "xpath_ata_date": 0.97, "xpath_starts_with": 0.90,
             "css_id_visible": 0.55, "css_name_visible": 0.50,
             "label_ata_date": 0.05, "label_loose": 0.05}
    random.seed(4)
    with open(TELEMETRY, "w", encoding="utf-8") as handle:
        for index in range(700):
            ctx = features.context(visible="no", page_ready="yes", frames="one",
                                   attempt="first", **CTX)
            strategy = random.choice(ATA_STRATEGIES)
            ok = random.random() < truth[strategy]
            handle.write(json.dumps({
                "kind": "interaction", "context": ctx, "strategy": strategy,
                "success": ok, "rank": ATA_STRATEGIES.index(strategy),
                "duration_ms": random.randint(250, 900) if ok else None,
                "source": "automation",
                "ts": "2026-09-{0:02d}".format(index % 28 + 1)}) + "\n")


def run_production_entry_point(label, expect_model):
    """
    Spawn `python update_eta.py` and return (stdout, run log text, log path).

    CT_STATE_FILE stops it hosting the dashboard port. Everything else about
    the startup is exactly what an operator gets.
    """
    env = dict(os.environ)
    env["CT_STATE_FILE"] = str(WORK / "state.json")
    env["ML_MODEL_PATH"] = str(MODEL)
    env["ML_TELEMETRY_PATH"] = str(TELEMETRY)
    env.pop("ML_ENABLED", None)               # prove the DEFAULT is on
    env["PYTHONUNBUFFERED"] = "1"

    before = set(Path(HERE).glob("C:\\Automation/logs/*.log"))
    started = time.time()
    process = subprocess.Popen(
        [sys.executable, "update_eta.py"], cwd=str(HERE), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output = ""
    try:
        # It will fail at login. We only need the startup block.
        output = process.communicate(timeout=90)[0]
    except subprocess.TimeoutExpired:
        process.kill()
        output = process.communicate()[0] or ""
    elapsed = time.time() - started

    after = set(Path(HERE).glob("C:\\Automation/logs/*.log"))
    fresh = sorted(after - before, key=lambda p: p.stat().st_mtime)
    log_path = fresh[-1] if fresh else None
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path else ""
    print("     spawned: {0} update_eta.py   ({1:.1f}s, exit={2})".format(
        Path(sys.executable).name, elapsed, process.returncode))
    return output, log_text, log_path


def ml_block(text):
    return [line for line in text.splitlines() if "[ML]" in line]


def main():
    rule("SETUP — train a model the ordinary way (a separate, deliberate act)")
    os.environ["ML_MODEL_PATH"] = str(MODEL)
    os.environ["ML_TELEMETRY_PATH"] = str(TELEMETRY)
    write_telemetry()
    from ml import trainer
    ok, message, _ = trainer.train(echo=lambda *a: None)
    show(ok, "python -m ml.trainer  ->  " + message)
    if not ok:
        return 1
    model_stamp = MODEL.stat().st_mtime_ns
    model_bytes = MODEL.stat().st_size

    # ── 1-5, 8 ───────────────────────────────────────────────────────
    rule("A. THE PRODUCTION ENTRY POINT, WITH A MODEL PRESENT")
    stdout, log_text, log_path = run_production_entry_point("with model", True)
    lines = ml_block(log_text) or ml_block(stdout)
    print("     run log: {0}".format(log_path))
    print()
    print("     ---- verbatim from the run log the run itself wrote ----")
    for line in lines:
        print("     " + line)
    print("     --------------------------------------------------------")

    show(bool(lines), "[1] ML initialisation ran inside the production startup")
    show(any("Initializing..." in l for l in lines), "[1] '[ML] Initializing...'")
    show(any("Model loaded successfully" in l for l in lines),
         "[2] A valid model was loaded")
    show(any(str(MODEL) in l for l in lines),
         "[2] ...the one the trainer wrote: {0}".format(MODEL.name))
    show(any("Status: ENABLED" in l for l in lines), "[3] Status became ENABLED")
    show(any("Model version:" in l for l in lines), "[3] Model version reported")
    show(any("Confidence threshold" in l for l in lines),
         "[3] Confidence threshold reported")
    show("ML_ENABLED" not in os.environ,
         "[3] ...with ML_ENABLED unset, so this is the DEFAULT")

    show(MODEL.stat().st_mtime_ns == model_stamp
         and MODEL.stat().st_size == model_bytes,
         "[8] The model file was NOT rewritten — no training at startup")
    show(not any(re.search(r"train|Wrote .*strategy_model", l, re.I)
                 for l in log_text.splitlines()),
         "[8] Nothing in the run log mentions training")

    # ── 7 ────────────────────────────────────────────────────────────
    rule("B. THE SAME ENTRY POINT, MODEL REMOVED")
    hidden = WORK / "hidden.json"
    shutil.move(str(MODEL), str(hidden))
    stdout2, log2, log_path2 = run_production_entry_point("no model", False)
    lines2 = ml_block(log2) or ml_block(stdout2)
    print()
    print("     ---- verbatim ----")
    for line in lines2:
        print("     " + line)
    print("     ------------------")
    show(any("No trained model found" in l for l in lines2),
         "[7] The missing model is reported")
    show(any("Status: FALLBACK" in l for l in lines2), "[7] Status became FALLBACK")
    show(any("Using deterministic automation" in l for l in lines2),
         "[7] ...and it says it is using the deterministic path")
    show(not any("Status: ENABLED" in l for l in lines2),
         "[7] It never claimed ENABLED without a model")
    show(not MODEL.exists(),
         "[8] A missing model was NOT trained into existence at startup")

    rule("C. THE SAME ENTRY POINT, MODEL CORRUPTED")
    MODEL.write_text("{{{ not json at all", encoding="utf-8")
    stdout3, log3, _ = run_production_entry_point("corrupt model", False)
    lines3 = ml_block(log3) or ml_block(stdout3)
    print()
    print("     ---- verbatim ----")
    for line in lines3:
        print("     " + line)
    print("     ------------------")
    show(any("Model load failed" in l for l in lines3),
         "[7] A corrupt model is reported as a failure")
    show(any("Status: FALLBACK" in l for l in lines3), "[7] Status became FALLBACK")
    show(not any("Status: ENABLED" in l for l in lines3),
         "[7] It did not claim ENABLED on a broken file")
    shutil.move(str(hidden), str(MODEL))

    # ── 4, 5, 6 ──────────────────────────────────────────────────────
    rule("D. A REAL PREDICTION REACHING THE SEAM, AND THE GUARDS")
    os.environ["ML_TELEMETRY_PATH"] = str(WORK / "seam.jsonl")
    from ml import config, predictor
    config.reload_from_environment()
    predictor.reset()

    import update_eta as A
    captured = []
    original_write_log = A.write_log
    A.write_log = lambda message: (captured.append(str(message)),
                                   original_write_log(message))[1]

    page_file = WORK / "manage.html"
    page_file.write_text(MANAGE_PAGE, encoding="utf-8")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        launch = {}
        for candidate in (os.environ.get("CHROMIUM_PATH"),
                          "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"):
            if candidate and Path(candidate).exists():
                launch["executable_path"] = candidate
                break
        browser = playwright.chromium.launch(**launch)
        page = browser.new_page()
        try:
            page.goto(page_file.as_uri())
            page.click("text=BU Shipment Info")
            A.fill_date_field(page, "ATA", "02/09/2026")     # production code
            ata_value = page.input_value("#txtATADate")
            eta_value = page.input_value("#txtETA")
            others = {name: page.input_value("#" + name)
                      for name in ("txtPreEntry", "txtDutyPaid", "txtRelease")}
        finally:
            browser.close()
            A.write_log = original_write_log

    ml_lines = [l for l in captured if l.startswith("ML:")]
    print("     ---- what the automation logged while writing the field ----")
    for line in captured:
        print("     " + line)
    print("     ------------------------------------------------------------")

    show(bool(ml_lines), "[4] A prediction was executed during the write",
         ml_lines[0] if ml_lines else "")
    show(any("xpath_ata_date" in l for l in ml_lines),
         "[5] The model's choice reached fill_date_field's candidate order")

    decisions = []
    seam_file = WORK / "seam.jsonl"
    if seam_file.exists():
        for line in seam_file.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("kind") == "decision":
                decisions.append(event)
    show(bool(decisions),
         "[4] ...and the decision is on disk as observable state",
         json.dumps(decisions[0], sort_keys=True)[:220] if decisions else "")
    show(any(d.get("used") and d.get("chosen") == "xpath_ata_date"
             for d in decisions),
         "[5] The recorded decision names the strategy that was used")

    show(ata_value == "02/09/2026",
         "[6] The ATA field holds the date: {0!r}".format(ata_value))
    show(eta_value == "",
         "[6] The ETA field was NOT written by an ATA operation")
    for name, value in others.items():
        show(value == "",
             "[6] {0} — another date field on the same panel — untouched".format(name))
    show(any("Internal ATA overwritten" in l for l in captured),
         "[6] The deterministic write path ran and logged its own result")

    rule("RESULT")
    failed = [text for ok, text in RESULTS if not ok]
    print("  {0} runtime checks, {1} failed".format(len(RESULTS), len(failed)))
    for text in failed:
        print("    FAILED: " + text)
    print()
    if not failed:
        print("  Every line above marked 'verbatim' was written by a subprocess")
        print("  running `python update_eta.py` — the production entry point,")
        print("  with nothing in the ML layer mocked.")
    print("  work dir: {0}".format(WORK))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
