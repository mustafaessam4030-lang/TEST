"""
Is the number the model reports actually a probability?

WHY THIS EXISTS

The estimator ranks on a Wilson lower bound, and the first version of this
layer compared that bound against a threshold called ML_CONFIDENCE_THRESHOLD.
That is a category error and it is worth being blunt about it: a Wilson lower
bound is a deliberately pessimistic ranking score, not a calibrated
probability. "0.65" does not mean "works 65% of the time" — it means "we are
95% sure the true rate is at least 0.65". Treating one as the other makes the
gate stricter than intended early on and looser than intended later, in ways
nobody can reason about.

This module measures the thing directly. It bins the model's PREDICTED rate
(the smoothed posterior mean, which is meant to be a probability) against what
actually happened on held-out rows, and reports:

    reliability   per bin: predicted vs observed, and how many rows
    ECE           expected calibration error — the average gap, row-weighted
    brier         Brier score, the standard proper scoring rule

A model whose 0.8 bin actually succeeds 80% of the time is calibrated and its
confidence may be reported as a probability. One whose 0.8 bin succeeds 45% of
the time is not, and the honest thing to report is the rank order without a
probability attached.

Below CALIBRATION_MIN_ROWS there is no calibration measurement at all, and the
answer is "unknown" rather than a number computed from too few rows. An ECE
from 12 observations is noise with a decimal point.
"""

from . import config, features, model as model_module

DEFAULT_BINS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def _bin_of(value, edges):
    for index in range(len(edges) - 1):
        if value < edges[index + 1] or index == len(edges) - 2:
            return index
    return len(edges) - 2


def measure(built, rows, bins=DEFAULT_BINS, min_rows=None):
    """
    Reliability of `built`'s predictions on `rows` (which it did NOT see).

    `rows` are episodes.Labelled. Returns a report; `calibrated` is None when
    there were not enough rows to say, which is a different answer from False
    and is treated as one everywhere downstream.
    """
    min_rows = int(config.CALIBRATION_MIN_ROWS if min_rows is None else min_rows)
    buckets = [{"predicted": 0.0, "observed": 0.0, "rows": 0}
               for _ in range(len(bins) - 1)]
    scored = 0
    brier_total = 0.0

    for row in rows:
        keys = row.keys()
        predicted, trials = built.observed_rate(keys, row.strategy)
        if predicted is None or trials <= 0:
            continue
        outcome = 1.0 if row.label else 0.0
        index = _bin_of(predicted, bins)
        bucket = buckets[index]
        bucket["predicted"] += predicted
        bucket["observed"] += outcome
        bucket["rows"] += 1
        brier_total += (predicted - outcome) ** 2
        scored += 1

    report = {
        "rows_scored": scored,
        "rows_available": len(rows),
        "min_rows": min_rows,
        "bins": [],
        "ece": None,
        "brier": None,
        "calibrated": None,
        "reason": "",
    }

    for index, bucket in enumerate(buckets):
        if not bucket["rows"]:
            continue
        report["bins"].append({
            "range": [bins[index], bins[index + 1]],
            "rows": bucket["rows"],
            "predicted": bucket["predicted"] / bucket["rows"],
            "observed": bucket["observed"] / bucket["rows"],
        })

    if scored < min_rows:
        report["reason"] = (
            "only {0} scoreable held-out rows; {1} are needed before a "
            "calibration figure means anything".format(scored, min_rows))
        return report

    report["brier"] = brier_total / scored
    report["ece"] = sum(
        entry["rows"] * abs(entry["predicted"] - entry["observed"])
        for entry in report["bins"]) / scored
    # 0.10 is the working threshold: an average gap of ten points between
    # promised and delivered is the most that can be called "a probability"
    # without the word doing work it has not earned.
    report["calibrated"] = report["ece"] <= 0.10
    report["reason"] = ("mean gap between predicted and observed is {0:.1%} "
                        "over {1} rows".format(report["ece"], scored))
    return report


def describe(report):
    """The reliability table, for the run log and the evaluator output."""
    lines = []
    if report.get("calibrated") is None:
        lines.append("Calibration: UNKNOWN — {0}".format(report.get("reason")))
    else:
        lines.append("Calibration: {0} — ECE {1:.1%}, Brier {2:.3f} over {3} rows"
                     .format("OK" if report["calibrated"] else "POOR",
                             report["ece"], report["brier"],
                             report["rows_scored"]))
    for entry in report.get("bins", []):
        lines.append("  {0:.1f}-{1:.1f}  predicted {2:5.1%}  observed {3:5.1%}  "
                     "n={4}".format(entry["range"][0], entry["range"][1],
                                    entry["predicted"], entry["observed"],
                                    entry["rows"]))
    return "\n".join(lines)
