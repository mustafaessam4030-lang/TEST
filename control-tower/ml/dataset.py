"""
Telemetry on disk turned into rows a trainer can use.

Deliberately strict. A malformed line is dropped and counted rather than
guessed at, and the counts are reported, because a dataset that quietly
discards a third of its input produces a model nobody can explain.
"""

import json
from pathlib import Path

from . import config, features


class Row:
    __slots__ = ("context", "strategy", "success", "duration_ms",
                 "category", "reference", "ts", "rank")

    def __init__(self, context, strategy, success, duration_ms,
                 category, reference, ts, rank=None):
        self.context = context
        self.strategy = strategy
        self.success = success
        self.duration_ms = duration_ms
        self.category = category
        self.reference = reference
        self.ts = ts
        self.rank = rank

    def keys(self):
        return features.keys(self.context)

    def __repr__(self):
        return "Row({0} {1} success={2})".format(
            features.describe(self.context), self.strategy, self.success)


def load(path=None, kind="interaction"):
    """
    Read telemetry into rows.

    Returns (rows, report). `report` says how many lines were read, kept and
    rejected, and why — so "not enough data" is always a statement with a
    number behind it.
    """
    path = Path(path or config.TELEMETRY_PATH)
    report = {"path": str(path), "lines": 0, "kept": 0,
              "wrong_kind": 0, "malformed": 0, "incomplete": 0}
    rows = []
    if not path.exists():
        report["missing"] = True
        return rows, report

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            report["lines"] += 1
            try:
                raw = json.loads(line)
            except (ValueError, TypeError):
                report["malformed"] += 1
                continue
            if not isinstance(raw, dict):
                report["malformed"] += 1
                continue
            if raw.get("kind") != kind:
                report["wrong_kind"] += 1
                continue
            strategy = raw.get("strategy")
            if not strategy or "success" not in raw:
                report["incomplete"] += 1
                continue
            rows.append(Row(
                context=features.clean(raw.get("context") or {}),
                strategy=str(strategy),
                success=bool(raw.get("success")),
                duration_ms=raw.get("duration_ms"),
                category=raw.get("category") or "OK",
                reference=raw.get("reference"),
                ts=raw.get("ts"),
                rank=raw.get("rank"),
            ))
            report["kept"] += 1
    return rows, report


def split(rows, holdout=0.25):
    """
    Chronological split: earlier rows train, later rows evaluate.

    Not random. The question being asked is "would a model built from what we
    knew then have helped afterwards", and a random split answers a different,
    easier question by letting the model see the future.
    """
    if not rows:
        return [], []
    ordered = sorted(rows, key=lambda r: (r.ts or "", r.strategy))
    cut = int(len(ordered) * (1.0 - holdout))
    cut = max(1, min(cut, len(ordered) - 1)) if len(ordered) > 1 else len(ordered)
    return ordered[:cut], ordered[cut:]


def summarise(rows):
    """Counts by strategy, by category and by context — the sanity check."""
    by_strategy, by_category, by_context = {}, {}, {}
    for row in rows:
        s = by_strategy.setdefault(row.strategy, [0, 0])
        s[1] += 1
        s[0] += 1 if row.success else 0
        by_category[row.category] = by_category.get(row.category, 0) + 1
        key = features.describe(row.context)
        c = by_context.setdefault(key, [0, 0])
        c[1] += 1
        c[0] += 1 if row.success else 0
    return {
        "rows": len(rows),
        "strategies": by_strategy,
        "categories": by_category,
        "contexts": by_context,
    }


def enough_to_train(rows, minimum_rows=60, minimum_strategies=2):
    """
    Is there enough real data to bother?

    Returns (ok, reason). The trainer refuses below this rather than emitting a
    model built from a handful of rows, because such a model would pass every
    smoke test and then make confident, wrong recommendations in production.
    """
    if len(rows) < minimum_rows:
        return False, ("only {0} usable rows; {1} is the minimum. Run the "
                       "automation with telemetry on for longer."
                       .format(len(rows), minimum_rows))
    strategies = {row.strategy for row in rows}
    if len(strategies) < minimum_strategies:
        return False, ("only {0} distinct strategy seen ({1}); a model needs "
                       "at least {2} to have anything to choose between."
                       .format(len(strategies), ", ".join(sorted(strategies)),
                               minimum_strategies))
    if not any(row.success for row in rows):
        return False, "no successful observation in the data"
    if all(row.success for row in rows):
        return False, ("every observation succeeded; there is nothing to "
                       "learn from until a strategy has been seen to fail")
    return True, "ok"
