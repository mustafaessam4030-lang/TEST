"""
Is the labelled data good enough to train on — not just big enough?

60 labelled rows is a CHECKPOINT: the point at which this assessment is worth
running, not permission to train. A model trained on 60 rows that all came
from one afternoon, one shipment, one field or one outcome would learn that
afternoon, not the Hub. So before anything is trained, the data has to show:

    volume        the checkpoint is reached (labelled rows)
    episodes      enough separately verified writes, not one write retried
    shipments     the writes belong to enough different shipments
    time          they span long enough, on enough different days, that one
                  bad day cannot dominate
    both outcomes enough verified successes AND verified failures — a
                  ranking cannot be learned from one side only
    competition   at least two strategies observed often enough to compare

Each criterion is reported with its number and its threshold. Anything it
cannot establish (e.g. no shipment references recorded) is a FAIL, not a
pass: missing evidence is not evidence of diversity.

    python -m ml.readiness

Read-only. It trains nothing, promotes nothing and changes no setting.
"""

import argparse
import os
import sys
from datetime import datetime

from . import episodes as episodes_module

READY = "READY FOR OFFLINE EVALUATION"
NOT_READY = "NOT READY"


def _int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def thresholds():
    return {
        "checkpoint_rows": _int("ML_READY_ROWS", 60),
        "episodes": _int("ML_READY_EPISODES", 40),
        "shipments": _int("ML_READY_SHIPMENTS", 20),
        "span_days": _int("ML_READY_SPAN_DAYS", 14),
        "distinct_days": _int("ML_READY_DAYS", 5),
        "positives": _int("ML_READY_POSITIVES", 10),
        "negatives": _int("ML_READY_NEGATIVES", 10),
        "rows_per_strategy": _int("ML_READY_PER_STRATEGY", 15),
    }


def _day(ts):
    text = str(ts or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def assess(rows, report=None, events=None, limits=None):
    """
    -> {"verdict", "ready", "criteria": [{name, value, threshold, passed,
    note}], "warnings": [...], "summary"}.

    `rows` are episodes.join() rows (verified label only). `events` (the raw
    telemetry) is used to recover the shipment reference of each episode.
    """
    limits = dict(thresholds(), **(limits or {}))
    report = report or {}
    criteria, warnings = [], []

    def criterion(name, value, threshold, passed, note=""):
        criteria.append({"name": name, "value": value, "threshold": threshold,
                         "passed": bool(passed), "note": note})

    criterion("labelled rows (checkpoint)", len(rows), limits["checkpoint_rows"],
              len(rows) >= limits["checkpoint_rows"],
              "rows from writes the Hub read back; unverified writes are excluded")

    episode_ids = {r.episode_id for r in rows if r.episode_id}
    criterion("verified write episodes", len(episode_ids), limits["episodes"],
              len(episode_ids) >= limits["episodes"])

    references = {r.episode_id: str(r.reference) for r in rows
                  if r.episode_id and getattr(r, "reference", None)}
    for event in events or ():
        if event.get("kind") == "episode" and event.get("episode_id") in episode_ids:
            if event.get("reference") and event["episode_id"] not in references:
                references[event["episode_id"]] = str(event["reference"])
    distinct_refs = len(set(references.values()))
    unknown = len(episode_ids) - len(references)
    criterion("distinct shipments", distinct_refs, limits["shipments"],
              distinct_refs >= limits["shipments"],
              "{0} episode(s) carry no shipment reference and are not counted"
              .format(unknown) if unknown else "")

    days = sorted({d for d in (_day(r.ts) for r in rows) if d})
    span = (days[-1] - days[0]).days + 1 if days else 0
    criterion("time span (days)", span, limits["span_days"],
              span >= limits["span_days"])
    criterion("distinct days with data", len(days), limits["distinct_days"],
              len(days) >= limits["distinct_days"])

    positives = sum(1 for r in rows if r.label)
    negatives = len(rows) - positives
    criterion("verified successes", positives, limits["positives"],
              positives >= limits["positives"])
    criterion("verified failures", negatives, limits["negatives"],
              negatives >= limits["negatives"],
              "a strategy that did not find the field, or a write that read "
              "back wrong")

    per_strategy = {}
    for r in rows:
        per_strategy[r.strategy] = per_strategy.get(r.strategy, 0) + 1
    compared = sorted(s for s, n in per_strategy.items()
                      if n >= limits["rows_per_strategy"])
    criterion("strategies observed often enough to compare", len(compared), 2,
              len(compared) >= 2,
              ", ".join("{0} {1}".format(s, per_strategy[s])
                        for s in sorted(per_strategy)))

    interactions = report.get("interactions") or 0
    dropped = report.get("dropped_unverified_episode") or 0
    if interactions and dropped / float(interactions) > 0.5:
        warnings.append(
            "{0} of {1} attempts ({2:.0%}) were excluded because their write was "
            "never read back. If that share stays high, the verified rows may "
            "not be representative — check VERIFY_AFTER_SAVE is on.".format(
                dropped, interactions, dropped / float(interactions)))
    if report.get("dropped_not_real"):
        warnings.append("{0} test/demo rows were present and excluded.".format(
            report["dropped_not_real"]))

    ready = all(c["passed"] for c in criteria)
    failed = [c for c in criteria if not c["passed"]]
    summary = (READY + ": every criterion passed. Next step is the offline "
               "evaluation; nothing is trained or promoted automatically."
               if ready else
               NOT_READY + ": " + "; ".join(
                   "{0} {1} (needs {2})".format(c["name"], c["value"], c["threshold"])
                   for c in failed))
    return {"verdict": READY if ready else NOT_READY, "ready": ready,
            "criteria": criteria, "warnings": warnings, "summary": summary,
            "checkpoint_reached": len(rows) >= limits["checkpoint_rows"]}


def assess_file(path=None):
    rows, report = episodes_module.join(path)
    events = episodes_module._read(path or episodes_module.config.TELEMETRY_PATH)
    return assess(rows, report, events)


def render(result):
    lines = ["Dataset readiness: {0}".format(result["verdict"]), ""]
    for c in result["criteria"]:
        lines.append("  {0}  {1:<46} {2!s:>6}  (needs {3})".format(
            "PASS" if c["passed"] else "FAIL", c["name"], c["value"], c["threshold"]))
        if c["note"]:
            lines.append("        {0}".format(c["note"]))
    for w in result["warnings"]:
        lines.append("  NOTE  " + w)
    lines += ["", result["summary"]]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--telemetry", default=None)
    args = parser.parse_args(argv)
    print(render(assess_file(args.telemetry)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
