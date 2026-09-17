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
MODEL = WORK / "champion.json"
CHALLENGER = WORK / "challenger.json"
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
    """
    A telemetry fixture, in exactly the shape the automation writes.

    SYNTHETIC. It exists so this proof has a model to load; it is written to a
    temporary directory and never to the production telemetry file, and it is
    NOT evidence that the model helps on the real Hub. What it proves is the
    RUNTIME PATH: that `python update_eta.py` loads a model, reports it
    honestly, and lets a real prediction reach the real field lookup.

    Each row belongs to an episode with a verified outcome, because that is the
    only shape the trainer will accept — an interaction with no read-back
    verdict behind it is dropped, and a fixture that ignored that would train a
    model the production pipeline could never produce.
    """
    from ml import features
    truth = {"label_exact": 0.02, "xpath_ata_date": 0.97, "xpath_starts_with": 0.90,
             "css_id_visible": 0.55, "css_name_visible": 0.50,
             "label_ata_date": 0.05, "label_loose": 0.05}
    random.seed(4)
    with open(TELEMETRY, "w", encoding="utf-8") as handle:
        for index in range(700):
            ctx = features.context(page_ready="yes", frames="one",
                                   attempt="first", **CTX)
            strategy = random.choice(ATA_STRATEGIES)
            found = random.random() < truth[strategy]
            persisted = found and random.random() < 0.97
            stamp = "2026-09-{0:02d}".format(index % 28 + 1)
            episode_id = "proof-{0}".format(index)
            handle.write(json.dumps({
                "kind": "interaction", "episode_id": episode_id,
                "context": ctx, "strategy": strategy,
                "success": found, "rank": ATA_STRATEGIES.index(strategy),
                "duration_ms": random.randint(250, 900) if found else None,
                "category": "OK" if found else "FIELD_NOT_VISIBLE",
                "source": "automation", "ts": stamp}) + "\n")
            handle.write(json.dumps({
                "kind": "episode", "episode_id": episode_id,
                "outcome": "VERIFIED" if persisted else "MISMATCH",
                "verified": bool(persisted), "source": "automation",
                "ts": stamp}) + "\n")


def placeholder_credentials():
    """
    A throwaway credentials file, so the run reaches its ML startup.

    `main()` loads credentials before it initialises the learning layer, so
    without this the subprocess dies three lines too early and this proof reads
    an empty log. The values are obvious nonsense and the login they are for
    does not exist here — the run still fails at the Hub, which is expected and
    is well past the point being proved.

    Returns a cleanup callable. If an operator already has a real credentials
    file, it is left completely alone and nothing is removed.
    """
    import update_eta as A
    path = Path(A.CREDENTIALS_FILE)
    if path.exists():
        return lambda: None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("USERNAME=proof-not-a-real-account\n"
                    "PASSWORD=proof-not-a-real-password\n", encoding="utf-8")

    def remove():
        try:
            path.unlink()
        except OSError:
            pass
    return remove


def run_production_entry_point(label, expect_model, mode=None):
    """
    Spawn `python update_eta.py` and return (stdout, run log text, log path).

    CT_STATE_FILE stops it hosting the dashboard port. Everything else about
    the startup is exactly what an operator gets.
    """
    env = dict(os.environ)
    env["CT_STATE_FILE"] = str(WORK / "state.json")
    env["ML_CHAMPION_PATH"] = str(MODEL)
    env["ML_TELEMETRY_PATH"] = str(TELEMETRY)
    env.pop("ML_MODEL_PATH", None)            # let it resolve the CHAMPION
    env.pop("ML_ENABLED", None)               # prove the DEFAULT is on
    if mode is None:
        env.pop("ML_MODE", None)              # prove the DEFAULT mode
    else:
        env["ML_MODE"] = mode
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
    # Both shapes the engine writes: the bracketed startup block and the
    # "ATLAS → ..." event lines a run emits as it works.
    return [line for line in text.splitlines()
            if "[ATLAS]" in line or "ATLAS \u2192" in line]


def main():
    cleanup_credentials = placeholder_credentials()
    try:
        return _main()
    finally:
        cleanup_credentials()


def _main():
    rule("SETUP — train a model the ordinary way (a separate, deliberate act)")
    os.environ["ML_CHAMPION_PATH"] = str(MODEL)
    os.environ["ML_CHALLENGER_PATH"] = str(CHALLENGER)
    os.environ["ML_TELEMETRY_PATH"] = str(TELEMETRY)
    os.environ.pop("ML_MODEL_PATH", None)
    write_telemetry()
    from ml import config as ml_config, trainer
    ml_config.reload_from_environment()
    ok, message, _ = trainer.train(echo=lambda *a: None)
    show(ok, "python -m ml.trainer  ->  " + message)
    if not ok:
        return 1
    show(not MODEL.exists(),
         "[0] Training wrote a CHALLENGER and did NOT touch the champion — "
         "nothing reaches production without passing the gate")

    promoted, promotion_message, _result = trainer.promote(echo=lambda *a: None)
    show(promoted, "python -m ml.trainer --promote  ->  " + promotion_message)
    if not promoted:
        return 1
    show(MODEL.exists(), "[0] ...and only then did the champion appear")
    model_stamp = MODEL.stat().st_mtime_ns
    model_bytes = MODEL.stat().st_size

    # ── 1-5, 8 ───────────────────────────────────────────────────────
    rule("A. THE PRODUCTION ENTRY POINT, WITH A MODEL PRESENT")
    stdout, log_text, log_path = run_production_entry_point(
        "with model", True, mode="active")
    lines = ml_block(log_text) or ml_block(stdout)
    print("     run log: {0}".format(log_path))
    print()
    print("     ---- verbatim from the run log the run itself wrote ----")
    for line in lines:
        print("     " + line)
    print("     --------------------------------------------------------")

    show(bool(lines), "[1] ML initialisation ran inside the production startup")
    show(any("Initializing..." in l for l in lines), "[1] '[ATLAS] Initializing...'")
    show(any("[ATLAS] Adaptive Logistics Strategy Engine" in l for l in lines),
         "[1] The engine introduces itself by name")
    show(not any("[ML]" in l for l in lines),
         "[1] Nothing still calls itself '[ML]'")
    show(any("Model loaded successfully" in l for l in lines),
         "[2] A valid model was loaded")
    show(any(str(MODEL) in l for l in lines),
         "[2] ...the one the trainer wrote: {0}".format(MODEL.name))
    show(any("Status: ENABLED" in l for l in lines), "[3] Status became ENABLED")
    show(any("Model version:" in l for l in lines), "[3] Model version reported")
    show(any("feature space" in l for l in lines),
         "[3] The feature space the model was built for is reported")
    show(any("Label rule" in l for l in lines),
         "[3] The label rule in force is reported")
    show(any("Ranking score threshold" in l and "NOT a probability" in l
             for l in lines),
         "[3] The score is called a Wilson bound, NOT a probability")
    show(any("Support required" in l for l in lines),
         "[3] The support requirement is reported")
    show(any("Promoted" in l for l in lines),
         "[3] The promotion that earned it production is named")
    show(any("ATLAS/" in l for l in lines),
         "[3] The model identifier is reported")
    show("ML_ENABLED" not in os.environ,
         "[3] ...with ML_ENABLED unset, so this is the DEFAULT")

    show(MODEL.stat().st_mtime_ns == model_stamp
         and MODEL.stat().st_size == model_bytes,
         "[8] The model file was NOT rewritten — no training at startup")
    show(not any(re.search(r"train|Wrote .*champion", l, re.I)
                 for l in log_text.splitlines()),
         "[8] Nothing in the run log mentions training")

    # ── the default mode ─────────────────────────────────────────────
    rule("A2. THE SAME ENTRY POINT, DEFAULT MODE (ML_MODE UNSET)")
    stdout_s, log_s, _ = run_production_entry_point("default mode", True)
    lines_s = ml_block(log_s) or ml_block(stdout_s)
    print()
    print("     ---- verbatim ----")
    for line in lines_s:
        print("     " + line)
    print("     ------------------")
    show(any("Mode: SHADOW" in l for l in lines_s),
         "[9] With ML_MODE unset the run defaults to SHADOW")
    show(any("Status: SHADOW" in l for l in lines_s),
         "[9] ...and reports SHADOW, not ENABLED")
    show(any("DISCARDED" in l for l in lines_s),
         "[9] ...and says plainly that its recommendations change nothing")
    show(any("ATLAS \u2192 Deterministic fallback" in l for l in lines_s),
         "[9] ...in the ATLAS vocabulary, as a Deterministic fallback")
    show(not any("ATLAS \u2192 Strategy selected" in l for l in lines_s),
         "[9] A shadow run NEVER claims a strategy selection")
    show(not any("Status: ENABLED" in l for l in lines_s),
         "[9] A fresh install never claims to be steering the automation")

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
    show(any("ATLAS \u2192 Deterministic fallback" in l for l in lines2),
         "[7] ...and records a Deterministic fallback rather than staying quiet")
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
    os.environ["ML_MODE"] = "active"
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

    ml_lines = [l for l in captured if l.startswith("ATLAS ")]
    print("     ---- what the automation logged while writing the field ----")
    for line in captured:
        print("     " + line)
    print("     ------------------------------------------------------------")

    show(bool(ml_lines), "[4] A prediction was executed during the write",
         ml_lines[0] if ml_lines else "")
    show(any("xpath_ata_date" in l for l in ml_lines),
         "[5] The model's choice reached fill_date_field's candidate order")
    show(any("Strategy selected" in l for l in ml_lines),
         "[5] ...and it was announced as an ATLAS strategy selection")
    show(not any("Action completed" in l for l in captured),
         "[6] No completion was claimed — this write was never read back")

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
