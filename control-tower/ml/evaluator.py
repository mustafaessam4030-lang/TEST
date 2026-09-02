"""
Does the model actually beat the automation's own order?

Trains on the earlier part of the telemetry and replays the later part, asking
for each held-out observation: at that moment, would the model have put a
working strategy first sooner than the hand-tuned order does?

The metric is EXPECTED FIRST-TRY SUCCESS, weighted by held-out observations:
for each situation in the holdout, how often would the strategy placed first
have worked? The baseline order is reconstructed from the telemetry itself —
every interaction records the rank it had in the automation's own list — so
this compares against what the code was really doing, not against a guess.

    python -m ml.evaluator
    python -m ml.evaluator --holdout 0.3

Nothing here is allowed to conclude "better" from a difference that the row
count cannot support: the report prints the sample size next to every number,
and `verdict()` refuses to recommend enabling below a floor.
"""

import argparse
import sys

from . import config, dataset, features, model as model_module, trainer


def _baseline_order(candidates, ranks):
    """
    The automation's OWN order, reconstructed from the telemetry.

    Each interaction records the rank the strategy had in the caller's list, so
    the baseline never has to be guessed or hard-coded here — it is whatever
    the code was actually doing when the data was collected.
    """
    return sorted(candidates,
                  key=lambda s: (ranks.get(s, 99), s))


def evaluate(telemetry_path=None, holdout=0.25, min_observations=None,
             confidence=None, echo=print):
    """
    Train on the earlier rows, score the later ones.

    The metric is EXPECTED FIRST-TRY SUCCESS: for each held-out observation,
    the probability that the strategy placed first would have worked in that
    context, estimated from the held-out data itself. Weighting by held-out
    observations rather than by distinct context means the sample size is the
    number of things that actually happened, not the number of situations they
    happened in — an earlier version scored one context as n=1 and could not
    tell a real effect from noise.
    """
    rows, report = dataset.load(telemetry_path)
    echo("Read {0}: {1} usable rows.".format(report["path"], report["kept"]))

    ok, reason = dataset.enough_to_train(rows)
    if not ok:
        return {"ok": False, "reason": reason}

    train_rows, test_rows = dataset.split(rows, holdout=holdout)
    if not test_rows:
        return {"ok": False, "reason": "holdout is empty"}

    built = trainer.build(train_rows)
    min_observations = int(config.ML_MIN_OBSERVATIONS
                           if min_observations is None else min_observations)
    confidence = (config.ML_CONFIDENCE_THRESHOLD
                  if confidence is None else confidence)

    # Held-out truth: per context, per strategy, how often it actually worked,
    # and where it sat in the automation's own order.
    truth = {}
    for row in test_rows:
        key = features.key(row.context, features.BACKOFF_LEVELS[0])
        bucket = truth.setdefault(
            key, {"context": row.context, "counts": {}, "ranks": {}, "rows": 0})
        bucket["rows"] += 1
        entry = bucket["counts"].setdefault(row.strategy, [0, 0])
        entry[1] += 1
        if row.success:
            entry[0] += 1
        if row.rank is not None:
            bucket["ranks"].setdefault(row.strategy, row.rank)

    baseline_weighted = model_weighted = weight_total = 0.0
    contexts_scored = contexts_unscored = model_declined = 0

    for bucket in truth.values():
        candidates = sorted(bucket["counts"])
        if len(candidates) < 2:
            contexts_unscored += 1
            continue

        rate = {s: (bucket["counts"][s][0] / bucket["counts"][s][1])
                for s in candidates}
        baseline_order = _baseline_order(candidates, bucket["ranks"])

        scores, _level, trials = built.score(
            features.keys(bucket["context"]), candidates,
            min_observations=min_observations)
        best = max(scores.values()) if scores else 0.0
        if best < confidence:
            model_declined += 1
            model_order = baseline_order          # falls back, by design
        else:
            model_order = sorted(
                candidates, key=lambda s: (-scores.get(s, 0.0),
                                           baseline_order.index(s)))

        weight = float(bucket["rows"])
        baseline_weighted += rate[baseline_order[0]] * weight
        model_weighted += rate[model_order[0]] * weight
        weight_total += weight
        contexts_scored += 1

    if weight_total <= 0:
        return {"ok": False, "reason": "nothing in the holdout could be scored"}

    result = {
        "ok": True,
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "observations_scored": int(weight_total),
        "contexts_scored": contexts_scored,
        "contexts_unscored": contexts_unscored,
        "model_declined": model_declined,
        "baseline_first_try": baseline_weighted / weight_total,
        "model_first_try": model_weighted / weight_total,
    }
    result["verdict"], result["verdict_reason"] = verdict(result)
    return result


def verdict(result, minimum_observations=100, minimum_gain=0.05,
            minimum_contexts=None):
    """
    Should ML be enabled?

    Requires a real sample and a real margin. "No difference" and "not enough
    evidence" are both perfectly good answers and are reported as such.
    """
    # `minimum_contexts` is accepted for callers that still pass it.
    if minimum_contexts is not None:
        minimum_observations = minimum_contexts
    scored = result.get("observations_scored")
    if scored is None:
        scored = result.get("contexts_scored") or 0
    if scored < minimum_observations:
        return "INSUFFICIENT DATA", (
            "only {0} scoreable observations in the holdout; {1} is the minimum "
            "before this comparison means anything".format(
                scored, minimum_observations))
    base = result.get("baseline_first_try")
    pred = result.get("model_first_try")
    if base is None or pred is None:
        return "INSUFFICIENT DATA", "nothing scoreable in the holdout"
    gain = pred - base
    if gain > minimum_gain:
        return "BETTER", (
            "expected first-try success {0:.0%} -> {1:.0%} over {2} held-out "
            "observations".format(base, pred, scored))
    if gain < -minimum_gain:
        return "WORSE", (
            "first-try hit rate {0:.0%} -> {1:.0%}; do NOT enable"
            .format(base, pred))
    return "NO DIFFERENCE", (
        "first-try hit rate {0:.0%} -> {1:.0%}, inside the {2:.0%} margin"
        .format(base, pred, minimum_gain))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare the model against the automation's own order.")
    parser.add_argument("--telemetry", default=None)
    parser.add_argument("--holdout", type=float, default=0.25)
    args = parser.parse_args(argv)

    result = evaluate(args.telemetry, holdout=args.holdout)
    if not result.get("ok"):
        print("Cannot evaluate: {0}".format(result.get("reason")))
        return 1

    print("")
    print("  train rows            {0}".format(result["train_rows"]))
    print("  holdout rows          {0}".format(result["test_rows"]))
    print("  observations scored   {0}".format(result["observations_scored"]))
    print("  contexts scored       {0}".format(result["contexts_scored"]))
    print("  contexts unscored     {0}".format(result["contexts_unscored"]))
    print("  model declined        {0}  (fell back to the original order)"
          .format(result["model_declined"]))
    print("")
    print("  expected first-try    baseline {0}   model {1}".format(
        "n/a" if result["baseline_first_try"] is None
        else "{0:.0%}".format(result["baseline_first_try"]),
        "n/a" if result["model_first_try"] is None
        else "{0:.0%}".format(result["model_first_try"])))
    print("")
    print("  VERDICT: {0} — {1}".format(result["verdict"], result["verdict_reason"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
