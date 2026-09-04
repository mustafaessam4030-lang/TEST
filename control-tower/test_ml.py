"""
The learning layer, on its own.

The most important tests in this file are the ones that prove the layer does
NOTHING: with ML_MODE off, with no model, with a corrupt model, in shadow
mode, with a context nobody has ever seen, the answer must be "keep your own
order". Every other test is about the layer being honest with the evidence it
has — which after the decision-layer rebuild means five specific things:

    * a strategy attempt is only a success if the write it belonged to was
      READ BACK from the Hub and confirmed
    * an episode nobody verified is EXCLUDED, not counted as a failure
    * features must be knowable before the decision they inform
    * support and score are different gates and both must pass
    * a Wilson lower bound is never called a probability

Run:  python test_ml.py
"""

import json
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ml import (calibration, config, dataset, episodes, evaluator, features,
                model as M, predictor, reward, telemetry, trainer)

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
                       view="BU", page_ready="yes", frames="one",
                       attempt="first")
STRATEGIES = ["label_exact", "xpath_ata_date", "css_id_visible"]
tmp = Path(tempfile.mkdtemp())

print("=" * 70)
print("1. WITH ML OFF, THE LAYER HAS NO OPINION")
print("=" * 70)
with_env(ML_ENABLED=0, ML_MODE="off")
r = predictor.recommend_strategy(CTX, STRATEGIES)
check("recommend_strategy declines", r.used is False, repr(r))
check("...and names the reason", "ML_ENABLED" in r.reason, r.reason)
budget, reason = predictor.recommend_wait(CTX, 15000)
check("recommend_wait returns the caller's own budget", budget == 15000)
check("An off switch is indistinguishable from an absent model",
      predictor.recommend_strategy(CTX, STRATEGIES).order == [])
check("ML_MODE=off forces ML_ENABLED off whatever the flag says",
      config.ML_ENABLED is False)

print()
print("=" * 70)
print("2. WITH ML ON BUT NO MODEL, IT STILL HAS NO OPINION")
print("=" * 70)
missing = tmp / "no_such_model.json"
with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=missing)
r = predictor.recommend_strategy(CTX, STRATEGIES)
check("A missing model falls back", r.used is False, repr(r))
check("...and says the file is missing", "no model" in r.reason.lower(), r.reason)
check("Wait budget is unchanged", predictor.recommend_wait(CTX, 15000)[0] == 15000)

print()
print("=" * 70)
print("3. A CORRUPT OR HOSTILE MODEL FILE FALLS BACK, NEVER CRASHES")
print("=" * 70)
for label, text in [
        ("not JSON at all", "{{{ not json"),
        ("wrong version", json.dumps({"version": 999, "counts": {}})),
        ("counts is not a table", json.dumps({"version": M.MODEL_VERSION, "counts": []})),
        ("a cell is not a table",
         json.dumps({"version": M.MODEL_VERSION, "counts": {"k": 5}})),
        ("credit exceeds trials",
         json.dumps({"version": M.MODEL_VERSION, "counts": {"k": {"s": [9, 2]}}})),
        ("negative trials",
         json.dumps({"version": M.MODEL_VERSION, "counts": {"k": {"s": [0, -1]}}})),
        ("entry is not a pair",
         json.dumps({"version": M.MODEL_VERSION, "counts": {"k": {"s": "hello"}}})),
        ("built over a different feature space",
         json.dumps({"version": M.MODEL_VERSION, "counts": {"k": {"s": [1, 2]}},
                     "meta": {"feature_version": 1}}))]:
    bad = tmp / "bad.json"
    bad.write_text(text, encoding="utf-8")
    with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=bad)
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
print("5. FEATURES MUST BE KNOWABLE BEFORE THE DECISION THEY INFORM")
print("=" * 70)
check("`visible` is gone — it was the ANSWER to the lookup, not an input",
      "visible" not in features.FEATURE_KEYS, str(features.FEATURE_KEYS))


def _try_visible():
    try:
        features.context(visible="yes")
        return "accepted"
    except TypeError:
        return "refused"


check("features.context(visible=...) raises rather than accepting it",
      _try_visible() == "refused", _try_visible())
check("...and an old row carrying it still loads, minus that key",
      features.clean({"visible": "no", "field": "ATA"})["field"] == "ATA")
check("`attempt` survives because it IS knowable beforehand",
      "attempt" in features.FEATURE_KEYS)
check("...and distinguishes the first look from a later one",
      features.context(attempt=1)["attempt"] == "first"
      and features.context(attempt=3)["attempt"] == "later")
check("The feature version is stamped so a stale model is refused",
      isinstance(features.FEATURE_VERSION, int) and features.FEATURE_VERSION >= 2)
check("No backoff level mentions a feature that no longer exists",
      all(name in features.FEATURE_KEYS
          for level in features.BACKOFF_LEVELS for name in level))

print()
print("=" * 70)
print("6. THE LABEL IS VERIFIED PERSISTED SUCCESS, AND NOTHING WEAKER")
print("=" * 70)
EV = [
    # A confirmed write. The locator that missed is a negative, the one that
    # won is a positive.
    {"kind": "episode", "episode_id": "e1", "outcome": "VERIFIED",
     "verified": True, "ts": "2026-09-01T10:00:00"},
    {"kind": "interaction", "episode_id": "e1", "strategy": "label_exact",
     "success": False, "category": "FIELD_NOT_VISIBLE", "context": CTX,
     "rank": 0, "ts": "2026-09-01T10:00:00"},
    {"kind": "interaction", "episode_id": "e1", "strategy": "css_id_visible",
     "success": True, "duration_ms": 700, "category": "OK", "context": CTX,
     "rank": 1, "ts": "2026-09-01T10:00:01"},
    # Saved, never read back. Contributes NOTHING.
    {"kind": "episode", "episode_id": "e2", "outcome": "UNVERIFIED",
     "verified": None, "ts": "2026-09-01T11:00:00"},
    {"kind": "interaction", "episode_id": "e2", "strategy": "css_id_visible",
     "success": True, "duration_ms": 700, "category": "OK", "context": CTX,
     "ts": "2026-09-01T11:00:00"},
    # Found the field; the value did NOT survive the save. A negative.
    {"kind": "episode", "episode_id": "e3", "outcome": "MISMATCH",
     "verified": False, "ts": "2026-09-01T12:00:00"},
    {"kind": "interaction", "episode_id": "e3", "strategy": "label_loose",
     "success": True, "duration_ms": 900, "category": "OK", "context": CTX,
     "ts": "2026-09-01T12:00:00"},
    # No episode at all.
    {"kind": "interaction", "strategy": "orphan", "success": True, "context": CTX},
]
rows, report = episodes.join(events=EV)
by_strategy = {row.strategy: row for row in rows}

check("A locator that missed on a verified write is a NEGATIVE",
      by_strategy["label_exact"].label is False)
check("A locator that won on a verified write is a POSITIVE",
      by_strategy["css_id_visible"].label is True)
check("A locator that 'found the field' but whose value did NOT persist "
      "is a NEGATIVE — this is the whole point of the rebuild",
      by_strategy["label_loose"].label is False)
check("...and is penalised harder than simply not finding the field",
      by_strategy["label_loose"].reward < by_strategy["label_exact"].reward,
      "{0} vs {1}".format(by_strategy["label_loose"].reward,
                          by_strategy["label_exact"].reward))
check("An UNVERIFIED episode contributes NO rows at all — not a negative",
      report["dropped_unverified_episode"] == 1 and len(rows) == 3, str(report))
check("...because counting it as a failure would be manufacturing data",
      report["negative"] == 2, str(report))
check("An interaction with no episode is dropped, and counted",
      report["dropped_no_episode_id"] == 1, str(report))
check("The report accounts for every interaction it read",
      report["interactions"] == (report["kept"] + report["dropped_not_real"]
                                 + report["dropped_no_episode_id"]
                                 + report["dropped_unknown_episode"]
                                 + report["dropped_unverified_episode"]),
      str(report))
check("The rule in force is named in the report",
      report["label_rule"] == "verified_persisted_success", str(report))

relaxed, relaxed_report = episodes.join(events=EV, require_verified=False)
check("The relaxed rule is available but is not the default",
      config.REQUIRE_VERIFIED_LABEL is True
      and relaxed_report["label_rule"] == "field_found_only")
check("...and under it the unverified episode DOES contribute",
      len(relaxed) > len(rows), "{0} vs {1}".format(len(relaxed), len(rows)))

print()
print("=" * 70)
print("7. THE REWARD SEPARATES A CLEAN WIN FROM AN EXPENSIVE ONE")
print("=" * 70)
clean = reward.compute(True, True, duration_ms=300, retries=0)
slow = reward.compute(True, True, duration_ms=9000, retries=2)
check("A verified win scores near the top", clean > 0.95, str(clean))
check("A slow, retried win scores lower", slow < clean, "{0} vs {1}".format(slow, clean))
check("...but is still positive — it did work", slow > 0, str(slow))
check("Not finding the field is negative",
      reward.compute(False, False, 1500, 0, "FIELD_NOT_FOUND") < 0)
check("A write that did not persist is the worst outcome",
      reward.compute(True, False, 1500, 0, "VERIFICATION_FAILURE", True)
      < reward.compute(False, False, 1500, 0, "FIELD_NOT_FOUND"))
check("A NETWORK error is barely charged to the strategy — it was not its "
      "fault, and blaming it would teach the model the weather",
      abs(reward.fault("NETWORK_ERROR")) < 0.01)
check("...while FIELD_NOT_FOUND is charged in full",
      reward.fault("FIELD_NOT_FOUND") == 1.0)
worst, best = reward.bounds()
check("The reward is bounded on both sides", worst < 0 < best and worst > -3.0,
      str((worst, best)))
check("Credit is clamped into [0, 1] so Wilson stays valid",
      reward.credit(best) <= 1.0 and reward.credit(worst) == 0.0)
check("A missing duration costs nothing rather than costing the maximum",
      reward.latency_cost(None) == 0.0)
check("Every reward term is inspectable",
      set(reward.explain(True, True, 100).keys()) ==
      {"success", "latency", "retry", "failure", "verify_fail", "total"})

print()
print("=" * 70)
print("8. RECENCY: OLD EVIDENCE FADES, IT DOES NOT VANISH")
print("=" * 70)
check("Today's observation carries full weight",
      M.recency_weight(0, 30) == 1.0)
check("One half-life halves it", abs(M.recency_weight(30, 30) - 0.5) < 1e-9)
check("Two half-lives quarter it", abs(M.recency_weight(60, 30) - 0.25) < 1e-9)
check("Very old evidence fades to a floor, never to zero",
      0 < M.recency_weight(3650, 30) <= 0.02)
check("A half-life of zero disables weighting rather than dividing by it",
      M.recency_weight(100, 0) == 1.0)

faded = M.StrategyModel()
faded.observe(["k"], "a", 1.0, weight=1.0)
faded.observe(["k"], "a", 1.0, weight=0.1)
check("Weights accumulate as fractional evidence, not as row counts",
      abs(faded.counts["k"]["a"][1] - 1.1) < 1e-9, str(faded.counts))
check("Credit is weighted the same way",
      abs(faded.counts["k"]["a"][0] - 1.1) < 1e-9, str(faded.counts))

print()
print("=" * 70)
print("9. SUPPORT AND SCORE ARE DIFFERENT GATES, AND BOTH MUST PASS")
print("=" * 70)
seen = features.context(provider="HUB", page="manage", field="ATA", view="BU",
                        page_ready="yes", frames="one", attempt="first")
built = M.StrategyModel()
for _ in range(40):
    built.observe(features.keys(seen), "xpath_ata_date", 1.0, duration_ms=900)
    built.observe(features.keys(seen), "label_exact", 0.0)
built.finalise()

well_evidenced = built.assess(features.keys(seen),
                              ["label_exact", "xpath_ata_date"],
                              min_support=30, min_support_per_arm=8)
check("A well-evidenced cell has support", well_evidenced["has_support"] is True)
check("...and ranks the working strategy first",
      well_evidenced["top"] == "xpath_ata_date", str(well_evidenced))

thin = M.StrategyModel()
for _ in range(4):
    thin.observe(features.keys(seen), "xpath_ata_date", 1.0)
thin.finalise()
thin_verdict = thin.assess(features.keys(seen),
                           ["label_exact", "xpath_ata_date"],
                           min_support=30, min_support_per_arm=8)
check("Four observations is NOT support, however high the score",
      thin_verdict["has_support"] is False, str(thin_verdict))
check("...and the reason names the shortfall in numbers",
      any("required" in reason for reason in thin_verdict["support_reasons"]),
      str(thin_verdict["support_reasons"]))

quarantined = M.StrategyModel()
for _ in range(40):
    quarantined.observe(features.keys(seen), "xpath_ata_date", 1.0)
    quarantined.observe(features.keys(seen), "label_exact", 1.0)
for _ in range(6):
    quarantined.note_streak(features.keys(seen), "label_exact", failed=True)
quarantined.finalise()
q = quarantined.assess(features.keys(seen), ["label_exact", "xpath_ata_date"],
                       min_support=30, min_support_per_arm=8,
                       quarantine_after=5)
check("A strategy failing repeatedly and recently is quarantined",
      "label_exact" in q["quarantined"], str(q))
check("...and is not the one recommended", q["top"] != "label_exact", str(q))
quarantined.note_streak(features.keys(seen), "label_exact", failed=False)
q2 = quarantined.assess(features.keys(seen), ["label_exact", "xpath_ata_date"],
                        min_support=30, min_support_per_arm=8,
                        quarantine_after=5)
check("One success releases it — quarantine is not a life sentence",
      "label_exact" not in q2["quarantined"], str(q2))

print()
print("=" * 70)
print("10. BACKOFF: AN UNSEEN CONTEXT STILL GETS AN ANSWER")
print("=" * 70)
unseen = features.context(provider="HUB", page="manage", field="ATA", view="BU",
                          page_ready="yes", frames="many", attempt="later")
scores, level, trials = built.score(features.keys(unseen),
                                    ["label_exact", "xpath_ata_date"])
check("A never-seen context is answered from a coarser one",
      scores["xpath_ata_date"] > scores["label_exact"], str(scores))
check("...and reports which level answered it", level > 0, str(level))
check("...with the trial count behind it", trials >= 40, str(trials))

print()
print("=" * 70)
print("11. DRIFT: WHEN RECENT BEHAVIOUR DISAGREES WITH HISTORY, STAND DOWN")
print("=" * 70)
drifting = M.StrategyModel()
for _ in range(40):
    drifting.observe(["k"], "a", 1.0)
for _ in range(20):
    drifting.observe(["k"], "a", 0.0, recent=True)
drifted, detail = drifting.drift(["k"], "a", threshold=0.25)
check("A cell whose recent rate collapsed is flagged", drifted is True, detail)
check("...and the detail states both rates", "%" in detail, detail)
steady = M.StrategyModel()
for _ in range(40):
    steady.observe(["k"], "a", 1.0)
for _ in range(20):
    steady.observe(["k"], "a", 1.0, recent=True)
check("A steady cell is not flagged", steady.drift(["k"], "a")[0] is False)
check("Too little recent evidence is 'cannot judge', not 'drifted'",
      M.StrategyModel().drift(["k"], "a")[0] is False)

print()
print("=" * 70)
print("12. SHADOW IS THE DEFAULT, AND SHADOW CHANGES NOTHING")
print("=" * 70)
model_path = tmp / "good.json"
built.meta["feature_version"] = features.FEATURE_VERSION
model_path.write_text(built.to_json(), encoding="utf-8")

with_env(ML_MODE=None, ML_ENABLED=None, ML_MODEL_PATH=None,
         ML_CONFIDENCE_THRESHOLD=None)
check("The default mode is shadow, not active", config.ML_MODE == "shadow")

with_env(ML_ENABLED=1, ML_MODE="shadow", ML_MODEL_PATH=model_path,
         ML_CONFIDENCE_THRESHOLD=0.5)
r = predictor.recommend_strategy(seen, ["label_exact", "xpath_ata_date"])
check("In shadow mode the model is consulted but NOT used", r.used is False, repr(r))
check("...and returns no order for the caller to apply", r.order == [], str(r.order))
check("...while still recording what it would have chosen",
      r.would_have_used is True and r.shadow_order[0] == "xpath_ata_date", repr(r))
check("...and saying so in the reason", "shadow" in r.reason.lower(), r.reason)

with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=model_path,
         ML_CONFIDENCE_THRESHOLD=0.5)
r = predictor.recommend_strategy(seen, ["label_exact", "xpath_ata_date"])
check("Only ML_MODE=active actually reorders anything", r.used is True, repr(r))
check("...and puts the winner first", r.top == "xpath_ata_date", repr(r))

print()
print("=" * 70)
print("13. A RECOMMENDATION IS A REORDERING AND NOTHING ELSE")
print("=" * 70)
check("Every candidate offered comes back",
      set(r.order) == {"label_exact", "xpath_ata_date"}, str(r.order))
check("Nothing is invented", len(r.order) == 2, str(r.order))
check("A single candidate is left alone",
      predictor.recommend_strategy(seen, ["only_one"]).used is False)
with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=model_path,
         ML_CONFIDENCE_THRESHOLD=0.99)
check("A threshold nothing can meet falls back",
      predictor.recommend_strategy(seen, ["label_exact", "xpath_ata_date"]).used
      is False)

thin_path = tmp / "thin.json"
thin.meta["feature_version"] = features.FEATURE_VERSION
thin_path.write_text(thin.to_json(), encoding="utf-8")
with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=thin_path,
         ML_CONFIDENCE_THRESHOLD=0.65)
r = predictor.recommend_strategy(seen, ["label_exact", "xpath_ata_date"])
check("Thin evidence is refused on SUPPORT, before the score is consulted",
      r.used is False and "not enough evidence" in r.reason, r.reason)

print()
print("=" * 70)
print("14. WAIT BUDGETS CAN ONLY EVER SHRINK")
print("=" * 70)
with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=model_path)
budget, reason = predictor.recommend_wait(seen, 15000, floor_ms=3000)
check("A budget is proposed from timing evidence", budget <= 15000, str(budget))
check("...never below the floor", budget >= 3000, str(budget))
check("...and it is genuinely shorter here", budget < 15000, "{0} {1}".format(budget, reason))

slow_model = M.StrategyModel()
for _ in range(20):
    slow_model.observe(features.keys(seen), "tab_postback", 1.0, duration_ms=40000)
slow_model.finalise()
slow_model.meta["feature_version"] = features.FEATURE_VERSION
slow_path = tmp / "slow.json"
slow_path.write_text(slow_model.to_json(), encoding="utf-8")
with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=slow_path)
budget, _ = predictor.recommend_wait(seen, 15000, floor_ms=3000)
check("Slow evidence cannot lengthen the caller's ceiling", budget == 15000, str(budget))

with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=model_path, ML_MAX_WAIT=100)
budget, _ = predictor.recommend_wait(seen, 15000, floor_ms=50)
check("ML_MAX_WAIT is a hard second ceiling", budget <= 100, str(budget))
with_env(ML_MAX_WAIT=None)

# A shortened wait is a smaller intervention than a reorder, but it is still
# the model steering the run — and shadow means NOTHING.
with_env(ML_ENABLED=1, ML_MODE="shadow", ML_MODEL_PATH=model_path)
shadow_budget, shadow_reason = predictor.recommend_wait(seen, 15000, floor_ms=3000)
check("In shadow mode the caller's own budget is returned unchanged",
      shadow_budget == 15000, str(shadow_budget))
check("...while the shorter budget it would have proposed is still recorded",
      "shadow:" in shadow_reason and "would have proposed" in shadow_reason,
      shadow_reason)

print()
print("=" * 70)
print("15. NO FAKE MACHINE LEARNING")
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
            "kind": "episode", "episode_id": "s{0}".format(index),
            "outcome": "VERIFIED", "verified": True,
            "ts": "2026-09-0{0}".format(index % 9 + 1)}) + "\n")
        handle.write(json.dumps({
            "kind": "interaction", "episode_id": "s{0}".format(index),
            "context": CTX, "strategy": "a", "success": True,
            "ts": "2026-09-0{0}".format(index % 9 + 1)}) + "\n")
ok, message, _ = trainer.train(telemetry_path=one_sided, model_path=tmp / "out.json",
                               echo=lambda *a: None)
check("Training with no failure ever seen refuses", ok is False, message)
check("No model file is written by a refused train",
      not (tmp / "out.json").exists())

unverified_only = tmp / "unverified.jsonl"
with open(unverified_only, "w", encoding="utf-8") as handle:
    for index in range(200):
        handle.write(json.dumps({
            "kind": "episode", "episode_id": "u{0}".format(index),
            "outcome": "UNVERIFIED", "verified": None,
            "ts": "2026-09-01"}) + "\n")
        handle.write(json.dumps({
            "kind": "interaction", "episode_id": "u{0}".format(index),
            "context": CTX, "strategy": "a" if index % 2 else "b",
            "success": index % 3 != 0, "ts": "2026-09-01"}) + "\n")
ok, message, _ = trainer.train(telemetry_path=unverified_only,
                               model_path=tmp / "out2.json", echo=lambda *a: None)
check("200 unverified episodes still will not train a model", ok is False, message)
check("...and the refusal explains how to fix it",
      "VERIFY_AFTER_SAVE" in message, message)

print()
print("=" * 70)
print("16. CALIBRATION IS MEASURED, NOT ASSERTED")
print("=" * 70)
blank = calibration.measure(M.StrategyModel(), [], min_rows=200)
check("With no rows, calibration is UNKNOWN — not True and not False",
      blank["calibrated"] is None, str(blank))
check("...and says how many rows it would need",
      "200" in blank["reason"], blank["reason"])
check("The default minimum is high enough that a handful cannot pass",
      config.CALIBRATION_MIN_ROWS >= 100, str(config.CALIBRATION_MIN_ROWS))
check("The word 'confidence' never dresses a Wilson bound as a probability",
      "NOT a probability" in Path("ml/predictor.py").read_text(encoding="utf-8")
      or "not a probability" in Path("ml/model.py").read_text(encoding="utf-8"))

print()
print("=" * 70)
print("17. THE EVALUATOR WILL NOT CLAIM AN IMPROVEMENT IT CANNOT SHOW")
print("=" * 70)
verdict, verdict_reason = evaluator.verdict({"observations_scored": 12,
                                             "baseline_first_try": 0.2,
                                             "model_first_try": 0.9})
check("A tiny sample is INSUFFICIENT DATA, not a win  (a 70-point gap on 12 "
      "observations is still not evidence)", verdict == "INSUFFICIENT DATA",
      "{0}: {1}".format(verdict, verdict_reason))
check("A 2-point difference is NO DIFFERENCE",
      evaluator.verdict({"observations_scored": 400, "baseline_first_try": 0.50,
                         "model_first_try": 0.52})[0] == "NO DIFFERENCE")
check("A real gain is reported as BETTER",
      evaluator.verdict({"observations_scored": 400, "baseline_first_try": 0.50,
                         "model_first_try": 0.80})[0] == "BETTER")
check("A regression is reported as WORSE",
      evaluator.verdict({"observations_scored": 400, "baseline_first_try": 0.80,
                         "model_first_try": 0.40})[0] == "WORSE")

print()
print("=" * 70)
print("18. THE DATASET IS STRICT AND SAYS WHAT IT DROPPED")
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
check("An unknown feature key cannot fragment the table",
      "nonsense" not in features.key(
          features.clean({"provider": "HUB", "nonsense": "x"}),
          features.BACKOFF_LEVELS[0]))

test_tagged = tmp / "tagged.jsonl"
test_tagged.write_text("\n".join([
    json.dumps({"kind": "episode", "episode_id": "t1", "outcome": "VERIFIED",
                "verified": True, "source": "test"}),
    json.dumps({"kind": "interaction", "episode_id": "t1", "context": CTX,
                "strategy": "a", "success": True, "source": "test"}),
]), encoding="utf-8")
_tagged_rows, tagged_report = episodes.join(path=test_tagged)
check("Telemetry written by the test suite is refused as training data",
      tagged_report["kept"] == 0 and tagged_report["dropped_not_real"] == 1,
      str(tagged_report))

print()
print("=" * 70)
print("19. THE PIPELINE RUNS END TO END, AND THE GATE HOLDS")
print("=" * 70)
# SYNTHETIC MECHANISM FIXTURE. This proves the plumbing — episode join, reward,
# recency, training, holdout evaluation, promotion gate — actually runs. It is
# written to a temporary file, never to the production telemetry, and it is NOT
# evidence that the model helps on the real Hub. Nothing here trains a shipped
# model. The only thing that could establish superiority is real production
# telemetry, which does not exist yet.
random.seed(11)
ORDER = ["label_exact", "xpath_ata_date", "css_id_visible"]
WORLDS = {
    ("ATA", "one"): {"label_exact": .08, "xpath_ata_date": .93, "css_id_visible": .30},
    ("ATA", "many"): {"label_exact": .70, "xpath_ata_date": .88, "css_id_visible": .85},
    ("ETA", "one"): {"label_exact": .90, "xpath_ata_date": .20, "css_id_visible": .80},
}
pipe = tmp / "pipeline.jsonl"
with open(pipe, "w", encoding="utf-8") as handle:
    for index in range(1500):
        field, frames = random.choice(list(WORLDS))
        ctx = features.context(provider="HUB", page="manage", field=field,
                               view="BU" if field == "ATA" else "COE",
                               page_ready="yes", frames=frames, attempt="first")
        strategy = random.choice(ORDER)
        found = random.random() < WORLDS[(field, frames)][strategy]
        # A found field persists nearly always; the rest are real mismatches.
        persisted = found and random.random() < 0.97
        stamp = "2026-09-{0:02d}".format(index % 28 + 1)
        episode_id = "p{0}".format(index)
        handle.write(json.dumps({
            "kind": "interaction", "episode_id": episode_id, "context": ctx,
            "strategy": strategy, "success": found, "rank": ORDER.index(strategy),
            "duration_ms": random.randint(300, 1600) if found else None,
            "category": "OK" if found else "FIELD_NOT_VISIBLE",
            "ts": stamp}) + "\n")
        handle.write(json.dumps({
            "kind": "episode", "episode_id": episode_id,
            "outcome": "VERIFIED" if persisted else "MISMATCH",
            "verified": bool(persisted), "ts": stamp}) + "\n")

challenger = tmp / "challenger.json"
ok, message, built_model = trainer.train(
    telemetry_path=pipe, model_path=challenger, echo=lambda *a: None)
check("Enough labelled data does train", ok is True, message)
check("...and writes a challenger file", challenger.exists())
check("...stamped with the feature space it was built for",
      built_model.meta.get("feature_version") == features.FEATURE_VERSION)
check("...and with the label rule in force",
      built_model.meta.get("label_rule") == "verified_persisted_success")
check("...and the reward weights it was scored under",
      "success" in (built_model.meta.get("reward_weights") or {}))
check("...whose rates match the world that generated them",
      built_model.observed_rate(
          features.keys(features.context(
              provider="HUB", page="manage", field="ATA", view="BU",
              page_ready="yes", frames="one", attempt="first")),
          "xpath_ata_date")[0] > 0.80)

outcome = evaluator.evaluate(telemetry_path=pipe, echo=lambda *a: None)
check("The evaluator scores the holdout", outcome.get("ok") is True, str(outcome))
check("...with a sample big enough to mean something",
      outcome["observations_scored"] >= 100, str(outcome.get("observations_scored")))
check("...and finds the model better on data where it should be",
      outcome["verdict"] == "BETTER", "{0} {1}".format(
          outcome["verdict"], outcome.get("verdict_reason")))
check("The baseline it compares against comes from the recorded rank",
      outcome["baseline_first_try"] < outcome["model_first_try"])
check("...and it reports a calibration measurement alongside",
      "calibration" in outcome and "calibrated" in outcome["calibration"])

print()
print("=" * 70)
print("20. CHAMPION AND CHALLENGER: TRAINING DOES NOT REACH PRODUCTION")
print("=" * 70)
champion = tmp / "champion.json"
with_env(ML_CHAMPION_PATH=champion, ML_CHALLENGER_PATH=challenger,
         ML_MODEL_PATH=None, ML_MODE="active", ML_ENABLED=1)
check("The predictor loads the CHAMPION, not whatever was last trained",
      str(config.active_model_path()) == str(champion),
      str(config.active_model_path()))
check("A trained challenger alone does not become the champion",
      not champion.exists())
r = predictor.recommend_strategy(seen, ["label_exact", "xpath_ata_date"])
check("...so with no promotion, the automation keeps its own order",
      r.used is False, repr(r))

promoted, promotion_message, _res = trainer.promote(
    telemetry_path=pipe, echo=lambda *a: None)
check("A challenger that wins the holdout IS promoted", promoted is True,
      promotion_message)
check("...and the champion file now exists", champion.exists())

thin_pipe = tmp / "thin_pipe.jsonl"
with open(thin_pipe, "w", encoding="utf-8") as handle:
    for index in range(40):
        stamp = "2026-09-01"
        handle.write(json.dumps({
            "kind": "interaction", "episode_id": "q{0}".format(index),
            "context": CTX, "strategy": "a" if index % 2 else "b",
            "success": index % 3 != 0, "rank": index % 2, "ts": stamp}) + "\n")
        handle.write(json.dumps({
            "kind": "episode", "episode_id": "q{0}".format(index),
            "outcome": "VERIFIED", "verified": True, "ts": stamp}) + "\n")
promoted, promotion_message, _res = trainer.promote(
    telemetry_path=thin_pipe, echo=lambda *a: None)
check("A challenger that cannot show a win is NOT promoted", promoted is False,
      promotion_message)
check("...and the refusal names the verdict",
      "Not promoting" in promotion_message, promotion_message)

# A fresh process is what a real run is; drop the cache from before the
# promotion so status() reads the champion that now exists.
predictor.reset()
status = predictor.status()
check("Status reports whether the model was actually proven",
      status["proven"] is True and status["promotion_verdict"] == "BETTER",
      str({k: status[k] for k in ("proven", "promotion_verdict")}))
unproven = M.StrategyModel(counts={"k": {"s": [1.0, 2.0]}},
                           meta={"feature_version": features.FEATURE_VERSION})
champion.write_text(unproven.to_json(), encoding="utf-8")
predictor.reset()
check("A model that never passed a gate is NOT reported as proven",
      predictor.status()["proven"] is False)

print()
print("=" * 70)
print("21. TELEMETRY NEVER RECORDS A SECRET")
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
check("An episode event records the three-valued verdict",
      telemetry.episode("e", "ref", "COE", "ETA", "01/01/2026",
                        telemetry.EPISODE_UNVERIFIED, verified=None) is True)
check("...and 'never checked' is stored as null, not as false",
      '"verified": null' in tele.read_text(encoding="utf-8"))

print()
print("=" * 70)
print("22. THE STARTUP LOG SAYS WHAT IS TRUE AND NO MORE")
print("=" * 70)
with_env(ML_ENABLED=None, ML_MODE=None, ML_MODEL_PATH=missing,
         ML_CHAMPION_PATH=missing, ML_CONFIDENCE_THRESHOLD=None)
check("ML_ENABLED defaults to true", config.ML_ENABLED is True)
check("...but active() is False without a model", predictor.active() is False)
check("...so a recommendation is still declined",
      predictor.recommend_strategy(CTX, STRATEGIES).used is False)
lines = []
check("initialize() reports FALLBACK, not ENABLED",
      predictor.initialize(log=lines.append) is False
      and any("Status: FALLBACK" in l for l in lines), " / ".join(lines))

lines = []
built.meta.pop("promotion_verdict", None)
model_path.write_text(built.to_json(), encoding="utf-8")
with_env(ML_ENABLED=1, ML_MODE="shadow", ML_MODEL_PATH=model_path,
         ML_CONFIDENCE_THRESHOLD=0.5)
loaded = predictor.initialize(log=lines.append)
joined = " / ".join(lines)
check("initialize() reports the mode it is in", loaded is True
      and any("Mode: SHADOW" in l for l in lines), joined)
check("An unpromoted model is announced as NOT YET PROVEN SUPERIOR",
      any("NOT YET PROVEN SUPERIOR" in l for l in lines), joined)
check("...and shadow says plainly that it changes nothing",
      any("DISCARDED" in l for l in lines), joined)
check("The log states the feature space and the label rule",
      any("feature space" in l for l in lines)
      and any("Label rule" in l for l in lines), joined)
check("The score is described as a Wilson bound, NOT as a probability",
      any("NOT a probability" in l for l in lines), joined)
check("The support requirement is stated",
      any("Support required" in l for l in lines), joined)

bad = tmp / "corrupt.json"
bad.write_text("{{{", encoding="utf-8")
lines = []
with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=bad)
check("A corrupt model reports the failure then FALLBACK",
      predictor.initialize(log=lines.append) is False
      and any("Model load failed" in l for l in lines)
      and any("Status: FALLBACK" in l for l in lines), " / ".join(lines))

with_env(ML_ENABLED=None, ML_MODE=None, ML_MODEL_PATH=None,
         ML_CHAMPION_PATH=None, ML_CHALLENGER_PATH=None,
         ML_CONFIDENCE_THRESHOLD=None, ML_MAX_WAIT=None, ML_TELEMETRY_PATH=None)

print()
print("=" * 70)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 70)
sys.exit(1 if FAIL else 0)
