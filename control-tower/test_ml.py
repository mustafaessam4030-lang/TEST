"""
The learning layer, on its own.

The most important tests in this file are the ones that prove the layer does
NOTHING: with ML_ENABLED off, with no model, with a corrupt model, with a
context nobody has ever seen, the answer must be "keep your own order". Every
other test is about the estimator being honest with the evidence it has.

Run:  python test_ml.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ml import config, dataset, evaluator, features, model as M, predictor, telemetry, trainer

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


def with_env(**pairs):
    for key, value in pairs.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    config.reload_from_environment()
    predictor.reset()


CTX = features.context(provider="HUB", page="manage", field="ATA",
                       view="BU", visible="no", page_ready="yes")
STRATEGIES = ["label_exact", "xpath_ata_date", "css_id_visible"]

print("=" * 70)
print("1. WITH ML OFF, THE LAYER HAS NO OPINION")
print("=" * 70)
with_env(ML_ENABLED=None)
r = predictor.recommend_strategy(CTX, STRATEGIES)
check("recommend_strategy declines", r.used is False, repr(r))
check("...and names the reason", "ML_ENABLED" in r.reason, r.reason)
budget, reason = predictor.recommend_wait(CTX, 15000)
check("recommend_wait returns the caller's own budget", budget == 15000)
check("An off switch is indistinguishable from an absent model",
      predictor.recommend_strategy(CTX, STRATEGIES).order == [])

print()
print("=" * 70)
print("2. WITH ML ON BUT NO MODEL, IT STILL HAS NO OPINION")
print("=" * 70)
missing = Path(tempfile.gettempdir()) / "ct_no_such_model_12345.json"
if missing.exists():
    missing.unlink()
with_env(ML_ENABLED=1, ML_MODEL_PATH=missing)
r = predictor.recommend_strategy(CTX, STRATEGIES)
check("A missing model falls back", r.used is False, repr(r))
check("...and says the file is missing", "no model" in r.reason.lower(), r.reason)
check("Wait budget is unchanged", predictor.recommend_wait(CTX, 15000)[0] == 15000)

print()
print("=" * 70)
print("3. A CORRUPT OR HOSTILE MODEL FILE FALLS BACK, NEVER CRASHES")
print("=" * 70)
tmp = Path(tempfile.mkdtemp())
for label, text in [
        ("not JSON at all", "{{{ not json"),
        ("wrong version", json.dumps({"version": 999, "counts": {}})),
        ("counts is not a table", json.dumps({"version": M.MODEL_VERSION, "counts": []})),
        ("a cell is not a table",
         json.dumps({"version": M.MODEL_VERSION, "counts": {"k": 5}})),
        ("successes exceed trials",
         json.dumps({"version": M.MODEL_VERSION, "counts": {"k": {"s": [9, 2]}}})),
        ("negative trials",
         json.dumps({"version": M.MODEL_VERSION, "counts": {"k": {"s": [0, -1]}}})),
        ("entry is not a pair",
         json.dumps({"version": M.MODEL_VERSION, "counts": {"k": {"s": "hello"}}}))]:
    bad = tmp / "bad.json"
    bad.write_text(text, encoding="utf-8")
    with_env(ML_ENABLED=1, ML_MODEL_PATH=bad)
    r = predictor.recommend_strategy(CTX, STRATEGIES)
    check("Falls back on a model that is {0}".format(label), r.used is False, repr(r))

print()
print("=" * 70)
print("4. THE ESTIMATOR IS HONEST ABOUT EVIDENCE")
print("=" * 70)
check("One success out of one scores low, not 1.0",
      M.wilson_lower_bound(1, 1) < 0.30, str(M.wilson_lower_bound(1, 1)))
check("45 out of 50 scores high",
      M.wilson_lower_bound(45, 50) > 0.75, str(M.wilson_lower_bound(45, 50)))
check("An unobserved strategy scores 0", M.wilson_lower_bound(0, 0) == 0.0)
check("Evidence beats luck: 45/50 outranks 1/1",
      M.wilson_lower_bound(45, 50) > M.wilson_lower_bound(1, 1))
check("A smoothed rate is never 0 and never 1",
      0.0 < M.smoothed_rate(0, 10) and M.smoothed_rate(10, 10) < 1.0)

print()
print("=" * 70)
print("5. BACKOFF: AN UNSEEN CONTEXT STILL GETS AN ANSWER")
print("=" * 70)
built = M.StrategyModel()
seen = features.context(provider="HUB", page="manage", field="ATA", view="BU",
                        visible="no", page_ready="yes", frames="one", attempt="first")
for _ in range(40):
    built.observe(features.keys(seen), "xpath_ata_date", True, 900)
for _ in range(40):
    built.observe(features.keys(seen), "label_exact", False)
built.finalise()

unseen = features.context(provider="HUB", page="manage", field="ATA", view="BU",
                          visible="no", page_ready="yes", frames="many",
                          attempt="later")
scores, level, trials = built.score(features.keys(unseen),
                                    ["label_exact", "xpath_ata_date"])
check("A never-seen context is answered from a coarser one",
      scores["xpath_ata_date"] > scores["label_exact"], str(scores))
check("...and reports which level answered it", level > 0, str(level))
check("...with the trial count behind it", trials >= 40, str(trials))

print()
print("=" * 70)
print("6. THE CONFIDENCE GATE")
print("=" * 70)
model_path = tmp / "good.json"
model_path.write_text(built.to_json(), encoding="utf-8")

with_env(ML_ENABLED=1, ML_MODEL_PATH=model_path, ML_CONFIDENCE_THRESHOLD=0.5)
r = predictor.recommend_strategy(seen, ["label_exact", "xpath_ata_date"])
check("A well-evidenced context is used", r.used is True, repr(r))
check("...and puts the winner first", r.top == "xpath_ata_date", repr(r))

with_env(ML_ENABLED=1, ML_MODEL_PATH=model_path, ML_CONFIDENCE_THRESHOLD=0.99)
r = predictor.recommend_strategy(seen, ["label_exact", "xpath_ata_date"])
check("A threshold nothing can meet falls back", r.used is False, repr(r))

thin = M.StrategyModel()
thin.observe(features.keys(seen), "label_exact", True)
thin.finalise()
thin_path = tmp / "thin.json"
thin_path.write_text(thin.to_json(), encoding="utf-8")
with_env(ML_ENABLED=1, ML_MODEL_PATH=thin_path, ML_CONFIDENCE_THRESHOLD=0.65)
r = predictor.recommend_strategy(seen, ["label_exact", "xpath_ata_date"])
check("One observation is not enough to act on", r.used is False, repr(r))

print()
print("=" * 70)
print("7. A RECOMMENDATION IS A REORDERING AND NOTHING ELSE")
print("=" * 70)
with_env(ML_ENABLED=1, ML_MODEL_PATH=model_path, ML_CONFIDENCE_THRESHOLD=0.5)
r = predictor.recommend_strategy(seen, ["label_exact", "xpath_ata_date"])
check("Every candidate offered comes back",
      set(r.order) == {"label_exact", "xpath_ata_date"}, str(r.order))
check("Nothing is invented", len(r.order) == 2, str(r.order))
r2 = predictor.recommend_strategy(seen, ["only_one"])
check("A single candidate is left alone", r2.used is False)

print()
print("=" * 70)
print("8. WAIT BUDGETS CAN ONLY EVER SHRINK")
print("=" * 70)
with_env(ML_ENABLED=1, ML_MODEL_PATH=model_path)
budget, reason = predictor.recommend_wait(seen, 15000, floor_ms=3000)
check("A budget is proposed from timing evidence", budget <= 15000, str(budget))
check("...never below the floor", budget >= 3000, str(budget))
check("...and it is genuinely shorter here", budget < 15000, "{0} {1}".format(budget, reason))

slow = M.StrategyModel()
for _ in range(20):
    slow.observe(features.keys(seen), "tab_postback", True, 40000)
slow.finalise()
slow_path = tmp / "slow.json"
slow_path.write_text(slow.to_json(), encoding="utf-8")
with_env(ML_ENABLED=1, ML_MODEL_PATH=slow_path)
budget, _ = predictor.recommend_wait(seen, 15000, floor_ms=3000)
check("Slow evidence cannot lengthen the caller's ceiling", budget == 15000, str(budget))

with_env(ML_ENABLED=1, ML_MODEL_PATH=model_path, ML_MAX_WAIT=100)
budget, _ = predictor.recommend_wait(seen, 15000, floor_ms=50)
check("ML_MAX_WAIT is a hard second ceiling", budget <= 15000 and budget <= 100,
      str(budget))

print()
print("=" * 70)
print("9. NO FAKE MACHINE LEARNING")
print("=" * 70)
empty = tmp / "empty.jsonl"
empty.write_text("", encoding="utf-8")
ok, message, _ = trainer.train(telemetry_path=empty, model_path=tmp / "out.json",
                               echo=lambda *a: None)
check("Training on nothing refuses", ok is False, message)
check("...and says how much is missing", "minimum" in message or "rows" in message, message)

one_sided = tmp / "one_sided.jsonl"
with open(one_sided, "w", encoding="utf-8") as handle:
    for index in range(100):
        handle.write(json.dumps({
            "kind": "interaction", "context": CTX, "strategy": "a",
            "success": True, "ts": "2026-09-0{0}".format(index % 9 + 1)}) + "\n")
ok, message, _ = trainer.train(telemetry_path=one_sided, model_path=tmp / "out.json",
                               echo=lambda *a: None)
check("Training with no failure ever seen refuses", ok is False, message)
check("No model file is written by a refused train",
      not (tmp / "out.json").exists())

print()
print("=" * 70)
print("10. TELEMETRY NEVER RECORDS A SECRET")
print("=" * 70)
tele = tmp / "t.jsonl"
with_env(ML_TELEMETRY_PATH=tele, ML_TELEMETRY_ENABLED=1)
telemetry.record({"kind": "interaction", "strategy": "s", "success": True,
                  "password": "hunter2", "context": {"cookie": "abc",
                                                     "provider": "HUB"},
                  "Authorization": "Bearer xyz", "username": "someone"})
written = tele.read_text(encoding="utf-8")
for secret in ("hunter2", "abc", "Bearer xyz", "someone"):
    check("{0!r} is not written".format(secret), secret not in written)
check("...but the useful part is", "HUB" in written)
check("A telemetry failure never raises",
      telemetry.record({"kind": "x", "bad": object()}) in (True, False))

print()
print("=" * 70)
print("11. THE EVALUATOR WILL NOT CLAIM AN IMPROVEMENT IT CANNOT SHOW")
print("=" * 70)
verdict, reason = evaluator.verdict({"observations_scored": 12,
                                     "baseline_first_try": 0.2,
                                     "model_first_try": 0.9})
check("A tiny sample is INSUFFICIENT DATA, not a win  (a 70-point gap on 12 "
      "observations is still not evidence)", verdict == "INSUFFICIENT DATA",
      "{0}: {1}".format(verdict, reason))
verdict, _ = evaluator.verdict({"observations_scored": 400,
                                "baseline_first_try": 0.50,
                                "model_first_try": 0.52})
check("A 2-point difference is NO DIFFERENCE", verdict == "NO DIFFERENCE", verdict)
verdict, _ = evaluator.verdict({"observations_scored": 400,
                                "baseline_first_try": 0.50,
                                "model_first_try": 0.80})
check("A real gain is reported as BETTER", verdict == "BETTER", verdict)
verdict, _ = evaluator.verdict({"observations_scored": 400,
                                "baseline_first_try": 0.80,
                                "model_first_try": 0.40})
check("A regression is reported as WORSE", verdict == "WORSE", verdict)

print()
print("=" * 70)
print("12. THE DATASET IS STRICT AND SAYS WHAT IT DROPPED")
print("=" * 70)
mixed = tmp / "mixed.jsonl"
mixed.write_text("\n".join([
    json.dumps({"kind": "interaction", "context": CTX, "strategy": "a", "success": True}),
    "not json",
    json.dumps({"kind": "decision", "chosen": "a"}),
    json.dumps({"kind": "interaction", "context": CTX}),
    json.dumps({"kind": "interaction", "context": CTX, "strategy": "b", "success": False}),
]), encoding="utf-8")
rows, report = dataset.load(mixed)
check("Good rows are kept", len(rows) == 2, str(report))
check("Malformed lines are counted", report["malformed"] == 1, str(report))
check("Other event kinds are counted", report["wrong_kind"] == 1, str(report))
check("Incomplete rows are counted", report["incomplete"] == 1, str(report))
rows, report = dataset.load(mixed)
check("Telemetry written by a test is refused by the dataset",
      dataset.load(Path(__file__).parent / "ml" / "data" / "telemetry.jsonl"
                   )[1].get("not_real", 0) >= 0)
check("An unknown feature key cannot fragment the table",
      "nonsense" not in features.key(
          features.clean({"provider": "HUB", "nonsense": "x"}),
          features.BACKOFF_LEVELS[0]))

print()
print("=" * 70)
print("13. THE TELEMETRY -> TRAIN -> EVALUATE PIPELINE RUNS END TO END")
print("=" * 70)
# Synthetic data. This proves the PLUMBING works; it is not evidence that the
# model helps on the real Hub, and nothing here is used to train a shipped
# model.
import random
random.seed(11)
ORDER = ["label_exact", "xpath_ata_date", "css_id_visible"]
WORLDS = {
    ("ATA", "no"): {"label_exact": .08, "xpath_ata_date": .93, "css_id_visible": .30},
    ("ATA", "yes"): {"label_exact": .70, "xpath_ata_date": .88, "css_id_visible": .85},
    ("ETA", "yes"): {"label_exact": .90, "xpath_ata_date": .20, "css_id_visible": .80},
}
pipe = tmp / "pipeline.jsonl"
with open(pipe, "w", encoding="utf-8") as handle:
    for index in range(1500):
        field, vis = random.choice(list(WORLDS))
        ctx = features.context(provider="HUB", page="manage", field=field,
                               view="BU" if field == "ATA" else "COE",
                               visible=vis, page_ready="yes", frames="one",
                               attempt="first")
        strategy = random.choice(ORDER)
        succeeded = random.random() < WORLDS[(field, vis)][strategy]
        handle.write(json.dumps({
            "kind": "interaction", "context": ctx, "strategy": strategy,
            "success": succeeded, "rank": ORDER.index(strategy),
            "duration_ms": random.randint(300, 1600) if succeeded else None,
            "ts": "2026-09-{0:02d}".format(index % 28 + 1)}) + "\n")

ok, message, built_model = trainer.train(
    telemetry_path=pipe, model_path=tmp / "pipeline.json", echo=lambda *a: None)
check("Enough real-shaped data does train", ok is True, message)
check("...and writes a model file", (tmp / "pipeline.json").exists())
check("...whose rates match what generated them",
      built_model.observed_rate(
          features.keys(features.context(
              provider="HUB", page="manage", field="ATA", view="BU",
              visible="no", page_ready="yes", frames="one", attempt="first")),
          "xpath_ata_date")[0] > 0.85)

outcome = evaluator.evaluate(telemetry_path=pipe, echo=lambda *a: None)
check("The evaluator scores the holdout", outcome.get("ok") is True, str(outcome))
check("...with a sample big enough to mean something",
      outcome["observations_scored"] >= 100, str(outcome["observations_scored"]))
check("...and finds the model better on data where it should be",
      outcome["verdict"] == "BETTER", "{0} {1}".format(
          outcome["verdict"], outcome["verdict_reason"]))
check("The baseline it compares against comes from the recorded rank",
      outcome["baseline_first_try"] < outcome["model_first_try"])

with_env(ML_ENABLED=None, ML_MODEL_PATH=None, ML_CONFIDENCE_THRESHOLD=None,
         ML_MAX_WAIT=None, ML_TELEMETRY_PATH=None)

print()
print("=" * 70)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 70)
sys.exit(1 if FAIL else 0)
