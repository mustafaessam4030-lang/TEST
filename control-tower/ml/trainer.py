"""
Telemetry -> model file.

Refuses to build a model from data that cannot support one. There is no
synthetic data, no bootstrapping from assumptions, and no "seed" model: until
the automation has run enough times to have seen strategies both work and
fail — on writes that were READ BACK and confirmed — the honest output is a
refusal and a count of what is still missing.

WHAT IT WRITES, AND WHERE

    challenger.json   every train writes here. Always.
    champion.json     only `--promote` writes here, and only after the
                      evaluator says the challenger beats the automation's own
                      order on held-out data by a real margin.

The predictor loads the CHAMPION. That separation is the whole point: training
is cheap and can happen after every run, but nothing a training run produces
reaches production until it has been scored against what the deterministic
order actually did. A model that has never been evaluated has no claim on
anything.

    python -m ml.trainer              build a challenger
    python -m ml.trainer --show       print the table that was built
    python -m ml.trainer --dry-run    report what would happen, write nothing
    python -m ml.trainer --promote    evaluate, and promote only if it wins
    python -m ml.trainer --status     what exists on disk right now
"""

import argparse
import datetime
import shutil
import sys
import time
from pathlib import Path

from . import config, episodes, features, model as model_module, reward

MINIMUM_ROWS = 60


def _age_days(ts, now=None):
    """Days between `ts` and now. Unparseable timestamps are treated as fresh."""
    if not ts:
        return 0.0
    now = now or datetime.datetime.now()
    for shape in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            when = datetime.datetime.strptime(str(ts)[:19], shape)
            return max(0.0, (now - when).total_seconds() / 86400.0)
        except ValueError:
            continue
    return 0.0


def build(rows, now=None, half_life_days=None, recent_window_days=None):
    """
    Fold labelled rows into a model.

    Three things happen per row that did not before:

      * it contributes CREDIT rather than a success bit, so a slow win with
        retries counts for less than a fast clean one;
      * it is weighted by age, so last month's evidence fades;
      * if it falls in the recent window it is also recorded there, which is
        what lets drift be measured as a comparison rather than asserted.

    Rows are folded in chronological order so the failure streaks used for
    quarantine are genuinely the streaks at the END of the data.
    """
    half_life_days = (config.HALF_LIFE_DAYS if half_life_days is None
                      else half_life_days)
    recent_window_days = (config.DRIFT_WINDOW_DAYS if recent_window_days is None
                          else recent_window_days)
    now = now or datetime.datetime.now()

    built = model_module.StrategyModel()
    for row in sorted(rows, key=lambda r: (r.ts or "", r.strategy)):
        age = _age_days(row.ts, now)
        weight = model_module.recency_weight(age, half_life_days)
        keys = row.keys()
        built.observe(keys, row.strategy, row.credit, weight=weight,
                      duration_ms=row.duration_ms,
                      recent=(age <= recent_window_days))
        built.note_streak(keys, row.strategy, failed=not row.label)

    built.meta["rows"] = len(rows)
    built.meta["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    # Provenance. Anyone looking at a model file has to be able to see what
    # world it was built for without reading the code that built it.
    built.meta["feature_version"] = features.FEATURE_VERSION
    built.meta["label_rule"] = ("verified_persisted_success"
                                if config.REQUIRE_VERIFIED_LABEL
                                else "field_found_only")
    built.meta["half_life_days"] = half_life_days
    built.meta["reward_weights"] = {
        "success": config.W_SUCCESS, "latency": config.W_LATENCY,
        "retry": config.W_RETRY, "failure": config.W_FAILURE,
        "verify_fail": config.W_VERIFY_FAIL,
        "latency_ref_ms": config.LATENCY_REF_MS,
    }
    built.meta["reward_bounds"] = list(reward.bounds())
    return built.finalise()


def enough_to_train(rows, minimum_rows=MINIMUM_ROWS, minimum_strategies=2):
    """
    Is there enough labelled data to bother?

    Returns (ok, reason). The trainer refuses below this rather than emitting a
    model built from a handful of rows, because such a model would pass every
    smoke test and then make confident, wrong recommendations in production.
    """
    if len(rows) < minimum_rows:
        return False, ("only {0} labelled rows; {1} is the minimum"
                       .format(len(rows), minimum_rows))
    strategies = {row.strategy for row in rows}
    if len(strategies) < minimum_strategies:
        return False, ("only {0} distinct strategy seen ({1}); a model needs "
                       "at least {2} to have anything to choose between."
                       .format(len(strategies), ", ".join(sorted(strategies)),
                               minimum_strategies))
    if not any(row.label for row in rows):
        return False, ("no VERIFIED success in the data. Every row is a "
                       "failure or an unconfirmed write, and a model trained "
                       "on that would only learn to say no.")
    if all(row.label for row in rows):
        return False, ("every observation succeeded; there is nothing to "
                       "learn from until a strategy has been seen to fail")
    return True, "ok"


def train(telemetry_path=None, model_path=None, minimum_rows=MINIMUM_ROWS,
          dry_run=False, echo=print):
    """
    Returns (ok, message, model_or_None).

    `ok` False with a message saying exactly what is missing is a normal,
    expected outcome early on — and it will be the outcome until the automation
    has run against the real Hub with read-back verification on.
    """
    rows, report = episodes.join(telemetry_path)
    echo("Read {0}: {1} events, {2} episodes ({3} with a verdict), "
         "{4} interactions, {5} labelled."
         .format(report["path"], report["events"], report["episodes"],
                 report["episodes_with_verdict"], report["interactions"],
                 report["kept"]))
    echo("Label rule: {0}.  Positives {1}, negatives {2}."
         .format(report["label_rule"], report["positive"], report["negative"]))
    for name in ("dropped_not_real", "dropped_no_episode_id",
                 "dropped_unknown_episode", "dropped_unverified_episode"):
        if report.get(name):
            echo("   {0}: {1}".format(name.replace("_", " "), report[name]))

    ok, reason = enough_to_train(rows, minimum_rows=minimum_rows)
    if not ok:
        return False, "Not training: {0}\n{1}".format(
            reason, episodes.explain_shortfall(report)), None

    summary = episodes.summarise(rows)
    echo("Strategies seen:")
    for name, entry in sorted(summary["strategies"].items()):
        echo("   {0:<34} {1:>4}/{2:<4}  {3:5.1f}%  mean reward {4:+.3f}".format(
            name, entry["positive"], entry["rows"],
            100.0 * entry["rate"], entry["mean_reward"]))

    built = build(rows)
    if dry_run:
        return True, "Dry run: would write {0} cells from {1} rows.".format(
            built.summary()["cells"], len(rows)), built

    path = Path(model_path or config.CHALLENGER_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write beside and rename, so an interrupted train cannot leave a
    # half-written model where a run will try to load it.
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(built.to_json(), encoding="utf-8")
    temporary.replace(path)
    return True, "Wrote challenger {0} ({1} cells, {2} observations).".format(
        path, built.summary()["cells"], built.summary()["observations"]), built


def promote(telemetry_path=None, holdout=0.25, echo=print, force=False):
    """
    Promote the challenger to champion — but only if it earns it.

    THE GATE. Every one of these must pass:

      1. there is enough labelled data to train on at all
      2. the challenger beats the automation's own order on held-out rows by
         more than the evaluator's minimum margin
      3. the held-out comparison rests on enough observations to mean anything

    Calibration is measured and reported but is NOT a gate: a model can be
    poorly calibrated and still rank better, and ranking is what it is used
    for. What poor calibration forbids is calling its score a probability,
    which the predictor then does not do.

    `force` exists for the tests and says so in the champion's metadata, so a
    forced promotion can never be mistaken for an earned one.
    """
    from . import evaluator          # imported here: evaluator imports us

    rows, report = episodes.join(telemetry_path)
    ok, reason = enough_to_train(rows)
    if not ok and not force:
        return False, "Not promoting: {0}".format(reason), None

    result = evaluator.evaluate(telemetry_path, holdout=holdout, echo=echo)
    if not result.get("ok") and not force:
        return False, "Not promoting: {0}".format(result.get("reason")), result

    verdict = result.get("verdict")
    if verdict != "BETTER" and not force:
        return False, ("Not promoting: the evaluator says {0} — {1}. The "
                       "deterministic order stays in charge."
                       .format(verdict, result.get("verdict_reason"))), result

    challenger = Path(config.CHALLENGER_PATH)
    if not challenger.exists():
        return False, "Not promoting: no challenger at {0}".format(challenger), result

    champion = Path(config.CHAMPION_PATH)
    champion.parent.mkdir(parents=True, exist_ok=True)
    if champion.exists():
        # Keep the model being replaced. A promotion that turns out badly has
        # to be undoable without a retrain.
        shutil.copy2(str(champion),
                     str(champion.with_suffix(
                         ".{0}.json".format(time.strftime("%Y%m%d%H%M%S")))))
    text = challenger.read_text(encoding="utf-8")
    built = model_module.StrategyModel.from_json(
        text, feature_version=features.FEATURE_VERSION)
    built.meta["promoted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    built.meta["promotion_verdict"] = verdict
    built.meta["promotion_reason"] = result.get("verdict_reason")
    built.meta["promotion_forced"] = bool(force and verdict != "BETTER")
    temporary = champion.with_suffix(".json.tmp")
    temporary.write_text(built.to_json(), encoding="utf-8")
    temporary.replace(champion)
    return True, "Promoted challenger to champion at {0} ({1}).".format(
        champion, verdict), result


def status(echo=print):
    """What is actually on disk, and what the predictor would load."""
    lines = []
    for name, path in (("champion", Path(config.CHAMPION_PATH)),
                       ("challenger", Path(config.CHALLENGER_PATH))):
        if not path.exists():
            lines.append("{0:<11} absent  ({1})".format(name, path))
            continue
        try:
            built = model_module.StrategyModel.from_json(
                path.read_text(encoding="utf-8"),
                feature_version=features.FEATURE_VERSION)
            meta = built.meta
            lines.append(
                "{0:<11} {1} cells, {2} observations, built {3}, "
                "features v{4}, label {5}".format(
                    name, built.summary()["cells"],
                    built.summary()["observations"],
                    meta.get("built_at", "?"), meta.get("feature_version", "?"),
                    meta.get("label_rule", "?")))
            if meta.get("promoted_at"):
                lines.append("{0:<11} promoted {1} — {2}".format(
                    "", meta["promoted_at"], meta.get("promotion_reason", "")))
        except Exception as error:
            lines.append("{0:<11} UNREADABLE: {1}".format(name, error))
    for line in lines:
        echo("  " + line)
    return lines


def show(built, limit=40, echo=print):
    """Print the table. The model is meant to be readable by a person."""
    rankings = []
    for key, cell in built.counts.items():
        trials = sum(entry[1] for entry in cell.values())
        rankings.append((trials, key, cell))
    rankings.sort(key=lambda item: (-item[0], item[1]))
    echo("")
    echo("{0:<52} {1:<26} {2:>13} {3:>8}".format(
        "CONTEXT", "STRATEGY", "CREDIT/TRIALS", "WILSON"))
    echo("-" * 104)
    for trials, key, cell in rankings[:limit]:
        first = True
        for strategy, entry in sorted(
                cell.items(), key=lambda kv: -model_module.wilson_lower_bound(*kv[1])):
            echo("{0:<52} {1:<26} {2:>6.1f}/{3:<6.1f} {4:>8.2f}".format(
                (key if first else "")[:52], strategy[:26],
                entry[0], entry[1],
                model_module.wilson_lower_bound(*entry)))
            first = False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Train the Control Tower strategy model.")
    parser.add_argument("--telemetry", default=None)
    parser.add_argument("--model", default=None,
                        help="where to write the challenger (default: challenger.json)")
    parser.add_argument("--min-rows", type=int, default=MINIMUM_ROWS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--promote", action="store_true",
                        help="evaluate, and promote to champion only if it wins")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)

    if args.status:
        status()
        return 0

    ok, message, built = train(args.telemetry, args.model,
                               minimum_rows=args.min_rows, dry_run=args.dry_run)
    print(message)
    if ok and built and args.show:
        show(built)

    if ok and args.promote and not args.dry_run:
        print("")
        promoted, promotion_message, _result = promote(args.telemetry)
        print(promotion_message)
        return 0 if promoted else 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
