"""
Reproduce the ATLAS evidence report on THIS machine.

Read-only. It does not train, does not write a model, does not touch the
telemetry file, and does not change a setting. Everything it prints is read
from the code and the files as they actually are here — so if a number differs
from the report you were sent, this machine is the truth and the report is
stale.

    python verify_atlas.py

Exits 0 if every structural guarantee holds, 1 if any does not.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))
os.chdir(str(HERE))

# Point every writable path at a throwaway directory BEFORE importing the
# package, so nothing this script does can reach the real telemetry or model.
SANDBOX = Path(tempfile.mkdtemp(prefix="atlas_verify_"))
REAL_TELEMETRY = None
os.environ["ML_TELEMETRY_PATH"] = str(SANDBOX / "telemetry.jsonl")

PASS, FAIL = [], []


def rule(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def ok(condition, text, detail=""):
    (PASS if condition else FAIL).append(text)
    print("  {0}  {1}".format("OK  " if condition else "FAIL", text))
    # Detail is evidence for a FAILURE. Printing it on every pass buries the
    # one line that matters in forty that do not.
    if detail and not condition:
        for line in str(detail).splitlines():
            print("        " + line)


def fact(label, value):
    print("  {0:34s} {1}".format(label, value))


import update_eta as A                                              # noqa: E402
from ml import (calibration, config, episodes, features, identity,   # noqa: E402
                model as M, predictor, reward, telemetry)
import bridge as B                                                   # noqa: E402

SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")


# ── 0. what is actually on this machine ──────────────────────────────
rule("0. WHAT IS ON THIS MACHINE")
# Read the real paths from a fresh config read, not the sandboxed one.
real_telemetry = Path(str(config.HERE / "data" / "telemetry.jsonl"))
champion = Path(config.CHAMPION_PATH)
challenger = Path(config.CHALLENGER_PATH)
fact("engine", "{0} — {1}".format(identity.NAME, identity.FULL_NAME))
fact("model identifier", identity.identifier(M.MODEL_VERSION,
                                             features.FEATURE_VERSION))
fact("mode", config.ML_MODE)
fact("ML_ENABLED", config.ML_ENABLED)
fact("VERIFY_AFTER_SAVE", A.VERIFY_AFTER_SAVE)
fact("production telemetry", "{0}  exists={1}".format(
    real_telemetry, real_telemetry.exists()))
fact("champion", "{0}  exists={1}".format(champion.name, champion.exists()))
fact("challenger", "{0}  exists={1}".format(challenger.name, challenger.exists()))

real_rows = 0
if real_telemetry.exists():
    try:
        rows, report = episodes.join(path=real_telemetry)
        real_rows = report["kept"]
        print()
        fact("events read", report["events"])
        fact("episodes", report["episodes"])
        fact("...with a read-back verdict", report["episodes_with_verdict"])
        fact("interactions", report["interactions"])
        fact("LABELLED ROWS (trainable)", report["kept"])
        fact("  positive / negative", "{0} / {1}".format(
            report["positive"], report["negative"]))
        for name in ("dropped_not_real", "dropped_no_episode_id",
                     "dropped_unknown_episode", "dropped_unverified_episode"):
            if report.get(name):
                fact("  " + name.replace("_", " "), report[name])
    except Exception as error:
        fact("join failed", error)
else:
    fact("LABELLED ROWS (trainable)", 0)

print()
if real_rows < 60:
    print("  >>> ML READY FOR LEARNING - NOT YET PROVEN SUPERIOR")
    print("  >>> {0} labelled rows; 60 is the minimum to train at all.".format(real_rows))
    if not A.VERIFY_AFTER_SAVE:
        print("  >>> VERIFY_AFTER_SAVE is OFF, so every episode is excluded.")
        print("  >>> Set VERIFY_AFTER_SAVE=1 before a live run to collect data.")
else:
    print("  >>> {0} labelled rows exist. Run `python -m ml.evaluator` for a".format(real_rows))
    print("  >>> baseline comparison. Only a BETTER verdict justifies ML_MODE=active.")


# ── 1. the reward, computed here ─────────────────────────────────────
rule("1. THE REWARD, COMPUTED ON THIS MACHINE")
for name in ("W_SUCCESS", "W_LATENCY", "W_RETRY", "W_FAILURE",
             "W_VERIFY_FAIL", "LATENCY_REF_MS"):
    fact(name, getattr(config, name))
fact("RETRY_REF", reward.RETRY_REF)
fact("bounds", tuple(round(b, 3) for b in reward.bounds()))
print()
CASES = [
    ("verified, 300ms, 0 retries",
     dict(found=True, verified=True, duration_ms=300, retries=0, category="OK")),
    ("verified, 8000ms, 2 retries",
     dict(found=True, verified=True, duration_ms=8000, retries=2, category="OK")),
    ("not found (FIELD_NOT_FOUND)",
     dict(found=False, verified=False, duration_ms=1500, retries=0,
          category="FIELD_NOT_FOUND")),
    ("not found (NETWORK_ERROR)",
     dict(found=False, verified=False, duration_ms=1500, retries=0,
          category="NETWORK_ERROR")),
    ("found, did NOT persist",
     dict(found=True, verified=False, duration_ms=1500, retries=0,
          category="VERIFICATION_FAILURE", verification_failed=True)),
]
computed = {}
for name, kwargs in CASES:
    value = reward.compute(**kwargs)
    computed[name] = value
    print("  {0:32s} reward {1:+.3f}   credit {2:.3f}".format(
        name, value, reward.credit(value)))
ok(computed["verified, 300ms, 0 retries"] > computed["verified, 8000ms, 2 retries"],
   "A fast clean win outranks a slow retried one")
ok(computed["found, did NOT persist"] < computed["not found (FIELD_NOT_FOUND)"],
   "A write that did not persist is the worst outcome")
ok(computed["not found (NETWORK_ERROR)"] > computed["not found (FIELD_NOT_FOUND)"],
   "A network failure is not charged to the strategy")


# ── 2. features ──────────────────────────────────────────────────────
rule("2. FEATURES")
fact("FEATURE_VERSION", features.FEATURE_VERSION)
fact("keys", ", ".join(features.FEATURE_KEYS))
fact("backoff levels", len(features.BACKOFF_LEVELS))
ok("visible" not in features.FEATURE_KEYS,
   "`visible` is gone - it was the ANSWER to the lookup, not an input")
try:
    features.context(visible="yes")
    ok(False, "features.context(visible=..) is refused")
except TypeError:
    ok(True, "features.context(visible=..) is refused")
ok(all(name in features.FEATURE_KEYS
       for level in features.BACKOFF_LEVELS for name in level),
   "No backoff level names a feature that no longer exists")


# ── 3. support, calibration, gates ───────────────────────────────────
rule("3. SUPPORT, CALIBRATION AND GATES")
for name in ("MIN_SUPPORT", "MIN_SUPPORT_PER_ARM", "ML_MIN_OBSERVATIONS",
             "ML_CONFIDENCE_THRESHOLD", "CALIBRATION_MIN_ROWS",
             "QUARANTINE_FAILURES", "HALF_LIFE_DAYS", "DRIFT_WINDOW_DAYS",
             "DRIFT_THRESHOLD", "REQUIRE_VERIFIED_LABEL",
             "ML_EXPLORATION_ENABLED", "ML_MAX_WAIT"):
    fact(name, getattr(config, name))
blank = calibration.measure(M.StrategyModel(), [])
fact("calibration with 0 rows", repr(blank["calibrated"]))
ok(blank["calibrated"] is None,
   "Calibration is UNKNOWN with no data - not True and not False")
ok(config.active_model_path() == config.CHAMPION_PATH
   or os.environ.get("ML_MODEL_PATH"),
   "The predictor loads the CHAMPION, not whatever was last trained")


# ── 4. the attribution rules ─────────────────────────────────────────
rule("4. THE ATTRIBUTION RULES")


class Recorder:
    def __enter__(self):
        self.lines = []
        self._original = A.write_log
        A.write_log = lambda message: self.lines.append(str(message))
        return self

    def __exit__(self, *_exc):
        A.write_log = self._original
        return False

    def said(self, label):
        return any(identity.NAME + " → " + label in l for l in self.lines)


def episode(influenced, chosen, outcome, verified):
    with Recorder() as recorder:
        A.ml_episode_begin("VERIFY", "COE", "ETA", "01/01/2026")
        current = A.ml_episode_current()
        if current is not None:
            current.atlas_influenced = influenced
            current.atlas_chosen = chosen
        A.ml_episode_end(outcome, verified, "")
    return recorder


good = episode(True, "css_id_visible", A.ML_EPISODE_VERIFIED, True)
ok(good.said(A.ATLAS_ACTION_COMPLETED),
   "Verified AND influenced -> Action completed")
not_ours = episode(False, None, A.ML_EPISODE_VERIFIED, True)
ok(not not_ours.said(A.ATLAS_ACTION_COMPLETED),
   "Verified but NOT influenced -> NO completion claim")
ok(not_ours.said(A.ATLAS_DETERMINISTIC_FALLBACK),
   "...it says Deterministic fallback instead")
unverified = episode(True, "css_id_visible", A.ML_EPISODE_UNVERIFIED, None)
ok(not unverified.said(A.ATLAS_ACTION_COMPLETED),
   "Influenced but NOT verified -> NO completion claim")
ok(unverified.said(A.ATLAS_ACTION_UNVERIFIED), "...it says Action unverified")
mismatch = episode(True, "css_id_visible", A.ML_EPISODE_MISMATCH, False)
ok(not mismatch.said(A.ATLAS_ACTION_COMPLETED),
   "A write that did not persist claims nothing")

ok(SRC.count("ATLAS_ACTION_COMPLETED,") == 1,
   "Exactly ONE place can say 'Action completed'",
   "found {0}".format(SRC.count("ATLAS_ACTION_COMPLETED,")))
guard = SRC.split("atlas_log(ATLAS_ACTION_COMPLETED")[0].rstrip()
ok(guard.endswith("if verified is True and episode.atlas_influenced:"),
   "...and it sits behind `verified is True and episode.atlas_influenced`",
   guard[-90:])
ok(SRC.count("episode.atlas_influenced = True") == 1,
   "Exactly ONE place sets the influence flag")
order_body = SRC.split("def ml_order")[1].split("\ndef ")[0]
ok("atlas_influenced = True" in order_body
   and order_body.index("if not recommendation.used:")
   < order_body.index("episode.atlas_influenced = True"),
   "...it is ml_order, AFTER the recommendation is confirmed used")


# ── 5. shadow and fallback never count as a selection ────────────────
rule("5. SHADOW AND FALLBACK NEVER COUNT AS A SELECTION")
context = features.context(provider="HUB", page="manage", field="ATA",
                           view="BU", page_ready="yes", frames="one",
                           attempt="first")
built = M.StrategyModel()
for _ in range(50):
    built.observe(features.keys(context), "xpath_ata_date", 1.0, duration_ms=700)
    built.observe(features.keys(context), "label_exact", 0.0)
built.meta["feature_version"] = features.FEATURE_VERSION
built.finalise()
model_file = SANDBOX / "verify_model.json"
model_file.write_text(built.to_json(), encoding="utf-8")
CANDIDATES = [("label_exact", 1), ("xpath_ata_date", 2)]


def with_mode(mode):
    os.environ["ML_MODE"] = mode
    os.environ["ML_ENABLED"] = "1"
    os.environ["ML_MODEL_PATH"] = str(model_file)
    os.environ["ML_CONFIDENCE_THRESHOLD"] = "0.5"
    config.reload_from_environment()
    predictor.reset()


with_mode("shadow")
with Recorder() as shadow_log:
    A.ml_episode_begin("VERIFY-S", "BU", "ATA", "01/01/2026")
    ordered, top = A.ml_order(CANDIDATES, context)
    shadow_influenced = A.atlas_influenced()
    A.ml_episode_end(A.ML_EPISODE_VERIFIED, True, "")
ok(ordered == CANDIDATES, "SHADOW: the automation's own order is returned")
ok(shadow_influenced is False, "SHADOW: nothing is attributed to ATLAS")
ok(not shadow_log.said(A.ATLAS_STRATEGY_SELECTED),
   "SHADOW: it NEVER says Strategy selected")
ok(not shadow_log.said(A.ATLAS_ACTION_COMPLETED),
   "SHADOW: a verified write is credited to the deterministic order")
budget, reason = predictor.recommend_wait(context, 15000, floor_ms=3000)
ok(budget == 15000, "SHADOW: wait budgets are left alone too", str(budget))

with_mode("active")
with Recorder() as active_log:
    A.ml_episode_begin("VERIFY-A", "BU", "ATA", "01/01/2026")
    ordered, top = A.ml_order(CANDIDATES, context)
    active_influenced = A.atlas_influenced()
    A.ml_episode_end(A.ML_EPISODE_VERIFIED, True, "")
ok([n for n, _ in ordered] == ["xpath_ata_date", "label_exact"],
   "ACTIVE: the order really changes", str([n for n, _ in ordered]))
ok(active_influenced is True, "ACTIVE: the influence is recorded")
ok(active_log.said(A.ATLAS_STRATEGY_SELECTED), "ACTIVE: it says Strategy selected")
ok(active_log.said(A.ATLAS_ACTION_COMPLETED), "ACTIVE: and only now, Action completed")

# A fallback event forced by hand cannot manufacture a selection.
with Recorder() as fallback_log:
    A.ml_episode_begin("VERIFY-F", "BU", "ATA", "01/01/2026")
    A.atlas_log(A.ATLAS_FALLBACK_ACTIVATED, "forced by verify_atlas.py")
    fallback_influenced = A.atlas_influenced()
    A.ml_episode_end(A.ML_EPISODE_VERIFIED, True, "")
ok(fallback_influenced is False,
   "A Fallback activated event does NOT set the influence flag")
ok(not fallback_log.said(A.ATLAS_ACTION_COMPLETED),
   "...and cannot produce an Action completed")
ok(A.ATLAS_DETERMINISTIC_FALLBACK not in B.ATLAS_INFLUENCE_LABELS,
   "Deterministic fallback never lights the dashboard ATLAS chip")


# ── 6. the five override guarantees ──────────────────────────────────
rule("6. WHAT ATLAS CANNOT OVERRIDE")
SEAMS = ("ml_order(", "ml_wait_budget(", "recommend_strategy", "recommend_wait",
         "ml_predictor.")


def body(name):
    return SRC.split("def {0}(".format(name))[1].split("\ndef ")[0]


def seams_in(name):
    try:
        text = body(name)
    except IndexError:
        return ["(function not found)"]
    return [s for s in SEAMS if s in text]


GROUPS = [
    ("shipment identity", ("click_manage_in_view", "find_shipments_table",
                           "page_is_afkl_detail", "build_afkl_detail_url")),
    ("ETA/ATA routing", ("update_internal_shipment",)),
    ("date validation", ("normalize_date", "extract_all_dates",
                         "year_for_dayless_date")),
    ("save verification", ("save_manage_page",)),
]
for group, functions in GROUPS:
    for name in functions:
        found = seams_in(name)
        ok(not found, "{0}: {1}() consults the engine nowhere".format(group, name),
           str(found))
verify_body = body("verify_saved_date")
ok(not any(s in verify_body for s in SEAMS),
   "read-back verification: verify_saved_date() consults the engine nowhere")
ok("ok = normalised == expected or actual == expected" in verify_body,
   "...the pass/fail comparison is deterministic string/date equality")
ok("recommend_verify" not in SRC and "ml_skip" not in SRC,
   "...and there is no way to ask the engine to skip it")
ok("recommend_value" not in SRC and "ml_value" not in SRC,
   "The engine is never asked WHAT to write")
ok("ml_field" not in SRC and "recommend_field" not in SRC,
   "The engine is never asked WHICH FIELD this is")

real_order_sites = SRC.count("named, predicted = ml_order(")
real_wait_sites = SRC.count("= ml_wait_budget(")
fact("ml_order call sites", real_order_sites)
fact("ml_wait_budget call sites", real_wait_sites)
ok(real_order_sites == 1 and real_wait_sites == 1,
   "The engine's entire surface is two functions, one call site each")

with_mode("active")
slow = M.StrategyModel()
for _ in range(20):
    slow.observe(features.keys(context), "tab_postback", 1.0, duration_ms=40000)
slow.meta["feature_version"] = features.FEATURE_VERSION
slow.finalise()
slow_file = SANDBOX / "slow.json"
slow_file.write_text(slow.to_json(), encoding="utf-8")
os.environ["ML_MODEL_PATH"] = str(slow_file)
config.reload_from_environment()
predictor.reset()
budget, reason = predictor.recommend_wait(context, 15000, floor_ms=3000)
ok(budget == 15000,
   "Evidence of 40s waits cannot LENGTHEN the caller's 15s ceiling",
   "{0} - {1}".format(budget, reason))


# ── 7. no synthetic data in the pipeline ─────────────────────────────
rule("7. NO SYNTHETIC DATA IN THE TRAINING PIPELINE")
tagged = SANDBOX / "tagged.jsonl"
tagged.write_text("\n".join([
    json.dumps({"kind": "episode", "episode_id": "t1", "outcome": "VERIFIED",
                "verified": True, "source": "test"}),
    json.dumps({"kind": "interaction", "episode_id": "t1", "strategy": "a",
                "success": True, "context": {"field": "ETA"}, "source": "test"}),
]), encoding="utf-8")
_rows, tagged_report = episodes.join(path=tagged)
ok(tagged_report["kept"] == 0 and tagged_report["dropped_not_real"] == 1,
   "Rows tagged as test telemetry are refused as training data",
   str(tagged_report))
ok(telemetry.test_source() is False,
   "This script is not a test process, so it writes to the real path...")
ok(str(telemetry.target_path()) == os.environ["ML_TELEMETRY_PATH"],
   "...which this script redirected to a sandbox, so nothing real was touched",
   str(telemetry.target_path()))
ok(not real_telemetry.exists() or real_telemetry.stat().st_size > 0,
   "The real telemetry file was not created by running this")


rule("RESULT")
print("  {0} structural checks, {1} failed".format(len(PASS) + len(FAIL), len(FAIL)))
for text in FAIL:
    print("    FAILED: " + text)
print()
if FAIL:
    print("  Something in the ATLAS foundation does not hold on this machine.")
    print("  Send this whole output back and do not enable ML_MODE=active.")
else:
    print("  Every structural guarantee holds on this machine.")
    print()
    if real_rows < 60:
        print("  ML READY FOR LEARNING - NOT YET PROVEN SUPERIOR")
        print("  {0} labelled observations exist. The mechanisms are correct;".format(real_rows))
        print("  they have not been given anything to learn from yet.")
    else:
        print("  {0} labelled observations exist. Run:".format(real_rows))
        print("      python -m ml.evaluator")
        print("  and read the VERDICT line. Only BETTER justifies ML_MODE=active.")
print()
print("  Nothing was written outside {0}".format(SANDBOX))
print("  Deterministic automation remains the source of truth.")
sys.exit(1 if FAIL else 0)
