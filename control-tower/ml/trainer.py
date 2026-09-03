"""
Telemetry -> model file.

Refuses to build a model from data that cannot support one. There is no
synthetic data, no bootstrapping from assumptions, and no "seed" model: until
the automation has run enough times to have seen strategies both work and
fail, the honest output is a refusal and a count of what is still missing.

    python -m ml.trainer            build from the default telemetry file
    python -m ml.trainer --show     print the table that was built
    python -m ml.trainer --dry-run  report what would happen, write nothing
"""

import argparse
import sys
import time
from pathlib import Path

from . import config, dataset, model as model_module


def build(rows):
    """Fold rows into a model. Every row is recorded at every backoff level."""
    built = model_module.StrategyModel()
    for row in rows:
        built.observe(row.keys(), row.strategy, row.success, row.duration_ms)
    built.meta["rows"] = len(rows)
    built.meta["built_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return built.finalise()


def train(telemetry_path=None, model_path=None, minimum_rows=60,
          dry_run=False, echo=print):
    """
    Returns (ok, message, model_or_None).

    `ok` False with a message saying exactly what is missing is a normal,
    expected outcome early on.
    """
    rows, report = dataset.load(telemetry_path)
    echo("Read {0}: {1} lines, {2} usable, {3} malformed, {4} incomplete."
         .format(report["path"], report["lines"], report["kept"],
                 report["malformed"], report["incomplete"]))

    ok, reason = dataset.enough_to_train(rows, minimum_rows=minimum_rows)
    if not ok:
        return False, "Not training: {0}".format(reason), None

    summary = dataset.summarise(rows)
    echo("Strategies seen:")
    for name, (successes, trials) in sorted(summary["strategies"].items()):
        echo("   {0:<34} {1:>4}/{2:<4}  {3:5.1f}%".format(
            name, successes, trials, 100.0 * successes / max(1, trials)))

    built = build(rows)
    if dry_run:
        return True, "Dry run: would write {0} cells from {1} rows.".format(
            built.summary()["cells"], len(rows)), built

    path = Path(model_path or config.ML_MODEL_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write beside and rename, so an interrupted train cannot leave a
    # half-written model where a run will try to load it.
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(built.to_json(), encoding="utf-8")
    temporary.replace(path)
    return True, "Wrote {0} ({1} cells, {2} observations).".format(
        path, built.summary()["cells"], built.summary()["observations"]), built


def show(built, limit=40, echo=print):
    """Print the table. The model is meant to be readable by a person."""
    rankings = []
    for key, cell in built.counts.items():
        trials = sum(entry[1] for entry in cell.values())
        rankings.append((trials, key, cell))
    rankings.sort(reverse=True)
    echo("")
    echo("{0:<58} {1:<28} {2:>9} {3:>8}".format(
        "CONTEXT", "STRATEGY", "RATE", "WILSON"))
    echo("-" * 106)
    for trials, key, cell in rankings[:limit]:
        first = True
        for strategy, (successes, attempts) in sorted(
                cell.items(), key=lambda kv: -model_module.wilson_lower_bound(*kv[1])):
            echo("{0:<58} {1:<28} {2:>4}/{3:<4} {4:>8.2f}".format(
                (key if first else "")[:58], strategy[:28],
                successes, attempts,
                model_module.wilson_lower_bound(successes, attempts)))
            first = False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Train the Control Tower strategy model.")
    parser.add_argument("--telemetry", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--min-rows", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args(argv)

    ok, message, built = train(args.telemetry, args.model,
                               minimum_rows=args.min_rows, dry_run=args.dry_run)
    print(message)
    if ok and built and args.show:
        show(built)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
