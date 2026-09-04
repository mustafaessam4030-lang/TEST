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

RECENCY

Observations are weighted by age with a configurable half-life. A selector that
worked reliably until the Hub was re-skinned last week should not keep
outvoting this week's evidence forever, and an unweighted count lets it do
exactly that. The weight is 0.5 ** (age_days / HALF_LIFE_DAYS), so a cell's
effective sample size shrinks as its evidence goes stale — which also makes the
Wilson bound widen on its own, and the confidence gate then declines. Staleness
turning into caution rather than into confident wrong answers is the property
worth having.

SUPPORT

Wilson is a ranking score, not a probability, and this file never calls it one.
A separate support gate — MIN_SUPPORT across the cell, MIN_SUPPORT_PER_ARM on
the arm being recommended — decides whether the cell may have an opinion at
all. Score and support are different questions and conflating them is how a
model ends up confidently ranking on four observations.

Standard library only — the arithmetic is a handful of divisions.
"""

import json
import math
import time

# 4: counts became weighted floats, the feature space lost `visible`, and the
# label became verified-persisted-success. A version 3 file describes a
# different world and is refused rather than reinterpreted.
MODEL_VERSION = 4
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
    counts[key][strategy] = [credit, trials]      both weighted floats
    recent[key][strategy] = [credit, trials]      the recent window only
    streaks[key][strategy] = consecutive failures at the end of the data
    timings[key]          = sorted list of observed durations in ms

    `credit` is the sum of fractional successes (reward.credit) times their
    recency weights, and `trials` the sum of the weights. Both are floats, so
    "45 trials" means 45 units of evidence, not 45 rows — which is the point of
    weighting them.

    `key` comes from features.key(ctx, level), so one model holds every
    backoff level at once.
    """

    def __init__(self, counts=None, timings=None, meta=None,
                 recent=None, streaks=None):
        self.counts = counts or {}
        self.timings = timings or {}
        self.meta = meta or {}
        # The same table over the recent window only. Held alongside rather
        # than replacing the full history so drift is a comparison between two
        # measurements, not an assertion.
        self.recent = recent or {}
        # streaks[key][strategy] = consecutive failures at the end of the data.
        self.streaks = streaks or {}

    # ── building ─────────────────────────────────────────────────────
    def observe(self, keys, strategy, credit, weight=1.0, duration_ms=None,
                recent=False):
        """
        Record one outcome against every backoff key at once.

        `credit` is fractional success in [0, 1] — see reward.credit(). A
        verified win banks close to 1, a verified win that took nine seconds
        and two retries banks around 0.6, a failure banks 0. `weight` is the
        recency weight. Counts are therefore floats, and a "trial" is a unit of
        evidence rather than a row.

        Recording at all levels during training is what makes the coarse cells
        usable immediately: the global cell sees every observation the specific
        cells see.
        """
        credit = max(0.0, min(1.0, float(credit)))
        weight = max(0.0, float(weight))
        if weight <= 0:
            return
        for key in keys:
            cell = self.counts.setdefault(key, {})
            entry = cell.setdefault(strategy, [0.0, 0.0])
            entry[1] += weight
            entry[0] += credit * weight
            if recent:
                rcell = self.recent.setdefault(key, {})
                rentry = rcell.setdefault(strategy, [0.0, 0.0])
                rentry[1] += weight
                rentry[0] += credit * weight
            if duration_ms is not None and credit > 0:
                self.timings.setdefault(key, []).append(float(duration_ms))

    def note_streak(self, keys, strategy, failed):
        """
        Track consecutive failures at the end of the data, per cell.

        A strategy that has failed QUARANTINE_FAILURES times in a row most
        recently is not merely low-scoring — something has changed and it is
        currently broken. Quarantine takes it out of the running until it is
        seen to work again, which the streak reset below does automatically.
        """
        for key in keys:
            cell = self.streaks.setdefault(key, {})
            cell[strategy] = (cell.get(strategy, 0) + 1) if failed else 0

    def finalise(self):
        for key in self.timings:
            self.timings[key].sort()
        self.meta.setdefault("built_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        self.meta["version"] = MODEL_VERSION
        self.meta["cells"] = len(self.counts)
        self.meta["observations"] = round(sum(
            entry[1] for cell in self.counts.values()
            for entry in cell.values()), 3)
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

    def assess(self, keys, strategies, min_observations=8,
               min_support=None, min_support_per_arm=None,
               quarantine_after=None):
        """
        The full picture for one decision: scores, support, quarantine.

        Returns a dict rather than a bare score because the caller has to be
        able to say WHY it declined, and "the score was 0.71" is not a reason
        when the cell holds four observations. `has_support` is the gate that
        actually matters; the score only ranks what is already allowed.
        """
        strategies = list(strategies)
        scores, level, trials = self.score(
            keys, strategies, min_observations=min_observations)

        blocked = set()
        if quarantine_after:
            for key in keys:
                for strategy in strategies:
                    if self.streaks.get(key, {}).get(strategy, 0) >= quarantine_after:
                        blocked.add(strategy)
                if self.streaks.get(key):
                    break

        allowed = [s for s in strategies if s not in blocked] or strategies
        top = max(allowed, key=lambda s: (scores.get(s, 0.0),
                                          -strategies.index(s)))

        key = keys[level] if 0 <= level < len(keys) else "*"
        cell = self.cell(key)
        arm_trials = cell.get(top, [0.0, 0.0])[1]

        min_support = self.__class__._default(min_support, 0)
        min_support_per_arm = self.__class__._default(min_support_per_arm, 0)
        reasons = []
        if trials < min_support:
            reasons.append("cell holds {0:.1f} observations, {1} required"
                           .format(trials, min_support))
        if arm_trials < min_support_per_arm:
            reasons.append("{0} has {1:.1f} observations, {2} required"
                           .format(top, arm_trials, min_support_per_arm))

        # The runner-up matters: two arms within noise of each other is not a
        # decision worth overriding a hand-tuned order for.
        ranked = sorted(allowed, key=lambda s: -scores.get(s, 0.0))
        margin = (scores.get(ranked[0], 0.0) - scores.get(ranked[1], 0.0)
                  if len(ranked) > 1 else scores.get(ranked[0], 0.0))

        return {
            "scores": scores,
            "level": level,
            "level_key": key,
            "trials": trials,
            "top": top,
            "top_trials": arm_trials,
            "margin": margin,
            "quarantined": sorted(blocked),
            "has_support": not reasons,
            "support_reasons": reasons,
        }

    @staticmethod
    def _default(value, fallback):
        return fallback if value is None else value

    def drift(self, keys, strategy, threshold=0.25):
        """
        Has this cell's behaviour changed lately?

        Compares the recent-window rate against the full-history rate for one
        arm. Returns (drifted, detail). Needs evidence on both sides — a cell
        with two recent observations cannot demonstrate drift and says so
        rather than raising a false alarm every time a run is unlucky.
        """
        for key in keys:
            whole = self.cell(key).get(strategy)
            near = self.recent.get(key, {}).get(strategy)
            if not whole or not near:
                continue
            if whole[1] < 10 or near[1] < 5:
                continue
            overall = whole[0] / whole[1]
            lately = near[0] / near[1]
            gap = lately - overall
            if abs(gap) >= threshold:
                return True, ("{0} at {1}: {2:.0%} overall vs {3:.0%} in the "
                              "recent window ({4:+.0%})".format(
                                  strategy, key, overall, lately, gap))
            return False, "{0:.0%} overall vs {1:.0%} lately".format(overall, lately)
        return False, "not enough recent evidence to judge drift"

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
            "recent": self.recent,
            "streaks": self.streaks,
            "timings": self.timings,
        }, indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, text, feature_version=None):
        raw = json.loads(text)
        if int(raw.get("version", 0)) != MODEL_VERSION:
            raise ValueError(
                "model version {0} is not {1}; retrain rather than guess"
                .format(raw.get("version"), MODEL_VERSION))
        meta = raw.get("meta") or {}
        # A model built over a different feature space keys its cells
        # differently. Loading it would not error — it would just look up rows
        # that are not there and quietly return "no evidence" forever, which is
        # the worst kind of failure because everything appears to work.
        if feature_version is not None:
            built_for = meta.get("feature_version")
            if built_for is not None and int(built_for) != int(feature_version):
                raise ValueError(
                    "model was built over feature space v{0}, this build uses "
                    "v{1}; retrain".format(built_for, feature_version))
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
                        or isinstance(entry[0], bool) or isinstance(entry[1], bool)
                        or entry[1] < 0 or entry[0] < 0
                        or entry[0] > entry[1] + 1e-9):
                    raise ValueError(
                        "counts[{0!r}][{1!r}] is not [credit, trials]"
                        .format(key, strategy))
        return cls(counts, timings, meta,
                   recent=raw.get("recent") or {},
                   streaks=raw.get("streaks") or {})

    def summary(self):
        return {
            "cells": len(self.counts),
            "observations": round(sum(entry[1] for cell in self.counts.values()
                                      for entry in cell.values()), 3),
            "timing_cells": len(self.timings),
            "quarantined": sum(1 for cell in self.streaks.values()
                               for n in cell.values() if n >= 5),
            "meta": self.meta,
        }


def recency_weight(age_days, half_life_days):
    """
    0.5 ** (age / half_life), floored so an old observation fades rather than
    vanishing. History that drops to exactly zero would let one fresh run
    reset a cell entirely, which is its own kind of overreaction.
    """
    if half_life_days is None or half_life_days <= 0:
        return 1.0
    try:
        age = max(0.0, float(age_days))
    except (TypeError, ValueError):
        return 1.0
    return max(0.02, 0.5 ** (age / float(half_life_days)))
