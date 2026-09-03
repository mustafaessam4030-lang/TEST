"""
The estimator.

WHAT IT IS

A Laplace-smoothed Bernoulli success-rate table over discrete contexts, with
backoff to coarser contexts, ranked by the lower bound of a Wilson score
interval. Alongside it, a per-context latency quantile table for timing.

WHY THIS AND NOT SOMETHING BIGGER

The question being asked is "which of these seven known locators works on this
kind of page?" The candidate set is fixed and small, the features are
categorical, and the answer is a per-cell success rate. That is a lookup
problem, and a lookup table with proper smoothing and a confidence bound
answers it exactly, while a gradient-boosted tree or a network would have to
rediscover the table from far more data than this run produces.

Three properties matter more here than model capacity:

  * It is honest with little data. A cell with 2 observations reports low
    confidence, so the confidence gate rejects it and the automation keeps its
    own order. A model that returns a sharp 0.97 from two samples is the
    dangerous option.
  * It backs off. A context never seen before still gets an answer from the
    coarser context that contains it, degrading to the global rate.
  * It is inspectable. `python -m ml.trainer --show` prints the whole table.
    An operator can see why a strategy was chosen and overrule it.

Ranking on the Wilson LOWER bound rather than the raw rate is what makes it
safe: 1/1 successes scores 0.21, while 45/50 scores 0.80. A strategy has to
earn its position with evidence, not luck.

Standard library only — the arithmetic is a handful of divisions.
"""

import json
import math
import time

MODEL_VERSION = 3
DEFAULT_PRIOR_ALPHA = 1.0      # Laplace: one imagined success
DEFAULT_PRIOR_BETA = 1.0       # ...and one imagined failure


def wilson_lower_bound(successes, trials, z=1.96):
    """
    Lower bound of the Wilson score interval for a proportion.

    This is the ranking score. With no trials it is 0.0, so an unobserved
    strategy can never outrank an observed one on optimism alone.
    """
    if trials <= 0:
        return 0.0
    successes = max(0.0, min(float(successes), float(trials)))
    phat = successes / trials
    denominator = 1.0 + (z * z) / trials
    centre = phat + (z * z) / (2.0 * trials)
    margin = z * math.sqrt(
        (phat * (1.0 - phat) + (z * z) / (4.0 * trials)) / trials)
    return max(0.0, (centre - margin) / denominator)


def smoothed_rate(successes, trials,
                  alpha=DEFAULT_PRIOR_ALPHA, beta=DEFAULT_PRIOR_BETA):
    """Posterior mean of a Beta-Bernoulli. Never 0 and never 1."""
    return (successes + alpha) / (trials + alpha + beta)


def quantile(sorted_values, q):
    """Linear-interpolated quantile of an already sorted list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = q * (len(sorted_values) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return float(sorted_values[low])
    weight = position - low
    return float(sorted_values[low]) * (1 - weight) + float(sorted_values[high]) * weight


class StrategyModel:
    """
    counts[key][strategy] = [successes, trials]
    timings[key]          = sorted list of observed durations in ms

    `key` comes from features.key(ctx, level), so one model holds every
    backoff level at once.
    """

    def __init__(self, counts=None, timings=None, meta=None):
        self.counts = counts or {}
        self.timings = timings or {}
        self.meta = meta or {}

    # ── building ─────────────────────────────────────────────────────
    def observe(self, keys, strategy, success, duration_ms=None):
        """
        Record one outcome against every backoff key at once.

        Recording at all levels during training is what makes the coarse cells
        usable immediately: the global cell sees every observation the specific
        cells see.
        """
        for key in keys:
            cell = self.counts.setdefault(key, {})
            entry = cell.setdefault(strategy, [0, 0])
            entry[1] += 1
            if success:
                entry[0] += 1
            if duration_ms is not None and success:
                self.timings.setdefault(key, []).append(float(duration_ms))

    def finalise(self):
        for key in self.timings:
            self.timings[key].sort()
        self.meta.setdefault("built_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.meta["version"] = MODEL_VERSION
        self.meta["cells"] = len(self.counts)
        self.meta["observations"] = sum(
            entry[1] for cell in self.counts.values() for entry in cell.values())
        return self

    # ── reading ──────────────────────────────────────────────────────
    def cell(self, key):
        return self.counts.get(key, {})

    def score(self, keys, strategies, min_observations=8):
        """
        Score `strategies` for a context, walking the backoff chain.

        Returns (scores, level_index, trials_at_level). `scores` maps strategy
        to its Wilson lower bound. The first level with at least
        `min_observations` trials across the strategies asked about wins; if no
        level has that much evidence, the last (global) level is used and the
        caller's confidence gate will almost certainly reject it.
        """
        for index, key in enumerate(keys):
            cell = self.cell(key)
            if not cell:
                continue
            trials = sum(cell.get(s, [0, 0])[1] for s in strategies)
            if trials >= min_observations or index == len(keys) - 1:
                scores = {}
                for strategy in strategies:
                    successes, attempts = cell.get(strategy, [0, 0])
                    scores[strategy] = wilson_lower_bound(successes, attempts)
                return scores, index, trials
        return {s: 0.0 for s in strategies}, len(keys) - 1, 0

    def observed_rate(self, keys, strategy):
        """Smoothed success rate for one strategy, at the first level that has it."""
        for key in keys:
            cell = self.cell(key)
            if strategy in cell:
                successes, trials = cell[strategy]
                return smoothed_rate(successes, trials), trials
        return None, 0

    def timing(self, keys, q=0.90):
        """
        A wait budget from observed successful durations.

        The 90th percentile of what has actually worked, at the first level
        with enough samples. Returns None when there is nothing to go on, and
        the caller then keeps its own constant.
        """
        for key in keys:
            samples = self.timings.get(key) or []
            if len(samples) >= 5:
                return quantile(sorted(samples), q), len(samples)
        return None, 0

    # ── persistence ──────────────────────────────────────────────────
    def to_json(self):
        return json.dumps({
            "version": MODEL_VERSION,
            "meta": self.meta,
            "counts": self.counts,
            "timings": self.timings,
        }, indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, text):
        raw = json.loads(text)
        if int(raw.get("version", 0)) != MODEL_VERSION:
            raise ValueError(
                "model version {0} is not {1}; retrain rather than guess"
                .format(raw.get("version"), MODEL_VERSION))
        counts = raw.get("counts") or {}
        timings = raw.get("timings") or {}
        if not isinstance(counts, dict) or not isinstance(timings, dict):
            raise ValueError("model file is malformed")
        # Shape check: a corrupted file must fail here, not at prediction time
        # in the middle of a run.
        for key, cell in counts.items():
            if not isinstance(cell, dict):
                raise ValueError("counts[{0!r}] is not a table".format(key))
            for strategy, entry in cell.items():
                if (not isinstance(entry, (list, tuple)) or len(entry) != 2
                        or not all(isinstance(n, (int, float)) for n in entry)
                        or entry[1] < 0 or entry[0] < 0 or entry[0] > entry[1]):
                    raise ValueError(
                        "counts[{0!r}][{1!r}] is not [successes, trials]"
                        .format(key, strategy))
        return cls(counts, timings, raw.get("meta") or {})

    def summary(self):
        return {
            "cells": len(self.counts),
            "observations": sum(entry[1] for cell in self.counts.values()
                                for entry in cell.values()),
            "timing_cells": len(self.timings),
            "meta": self.meta,
        }
