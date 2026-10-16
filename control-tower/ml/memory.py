"""
Failure memory: what actually worked, last time this went wrong.

Reads the recovery rows the automation wrote and answers one question —

    "For this failure signature, which recovery has a VERIFIED history?"

THE LABEL RULE, and it is the whole module:

    verification is True   -> a positive example. The action ran against a
                             real page and the caller's own check confirmed
                             the workflow state was restored.
    verification is False  -> a negative example. It ran and did not work.
    verification is None   -> NOTHING. Not a soft negative. "Nobody checked"
                             is not evidence either way.

and, before any of that:

    execution != ACTUALLY_TRIED -> excluded entirely. A shadow recommendation
                                   is an opinion about a page, not a thing
                                   that happened to one, and letting
                                   WOULD_TRY rows into the history would let
                                   ATLAS grade its own homework.
    source != "automation"      -> excluded. Test telemetry never trains.
    chosen is None              -> excluded. A fallback or a human checkpoint
                                   recorded no action, so there is no action
                                   to credit or blame.

Nothing here writes telemetry, and nothing invents a row.
"""

import json
import threading
import time
from pathlib import Path

from . import config, identity, model as model_module, recovery

# Reused rather than reinvented: the same Wilson lower bound that ranks
# strategies, and the same recency half-life.
wilson_lower_bound = model_module.wilson_lower_bound
recency_weight = model_module.recency_weight

# A signature seen this many times, with at least this many verified
# outcomes, before the memory is allowed to have an opinion. These are the
# EXISTING support numbers; nothing here lowers them.
MIN_VERIFIED_EXAMPLES = int(config.MIN_SUPPORT_PER_ARM)   # per action
MIN_VERIFIED_TOTAL = int(config.MIN_SUPPORT)              # per signature

NO_HISTORY = "No verified recovery history for this failure signature."

# Recovery happens when something has already gone wrong, so it must not then
# make things slower. The parsed rows are cached against the telemetry file's
# (size, mtime), so a run with several failures re-reads the file once per
# change rather than once per failure — and a run with no new telemetry does
# not re-read it at all.
_CACHE_LOCK = threading.Lock()
_CACHE = {"key": None, "rows": None}


def invalidate():
    """Drop the cache. For tests, and for anything that rewrites telemetry."""
    with _CACHE_LOCK:
        _CACHE["key"] = None
        _CACHE["rows"] = None


def _age_days(stamp):
    if not stamp:
        return 0.0
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            then = time.mktime(time.strptime(str(stamp)[:19], fmt))
            return max(0.0, (time.time() - then) / 86400.0)
        except (ValueError, OverflowError):
            continue
    return 0.0


def _rows(path=None, events=None):
    """Every recovery row, unfiltered. Cached on the file's identity.

    Never raises.
    """
    if events is not None:
        return [e for e in events if e.get("kind") == "recovery"]
    target = Path(path or config.TELEMETRY_PATH)
    try:
        stat = target.stat()
        key = (str(target), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return []
    with _CACHE_LOCK:
        if _CACHE["key"] == key and _CACHE["rows"] is not None:
            return _CACHE["rows"]
    out = []
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("kind") == "recovery":
                    out.append(event)
    except OSError:
        return out
    with _CACHE_LOCK:
        _CACHE["key"] = key
        _CACHE["rows"] = out
    return out


def labelled(path=None, events=None):
    """
    The rows that may become learning examples, plus a report accounting for
    every row that could not.

    Returns (examples, report). Every excluded row lands in exactly one
    counter, so "we have 3 examples" always comes with where the rest went.
    """
    report = {
        "rows": 0, "kept": 0, "positive": 0, "negative": 0,
        "dropped_not_real": 0, "dropped_not_executed": 0,
        "dropped_unverified": 0, "dropped_no_action": 0,
        "label_rule": "verified_recovery_outcome",
    }
    examples = []
    for row in _rows(path, events):
        report["rows"] += 1
        if row.get("source", "automation") != "automation":
            report["dropped_not_real"] += 1
            continue
        if not row.get("chosen"):
            # A fallback, a human checkpoint or a "no safe action" row. Real,
            # and not about any particular action.
            report["dropped_no_action"] += 1
            continue
        if row.get("execution") != identity.STATE_RECOVERY_ACTUALLY_TRIED:
            # WOULD_TRY. An opinion, never an outcome.
            report["dropped_not_executed"] += 1
            continue
        verified = row.get("verification")
        if verified is None:
            report["dropped_unverified"] += 1
            continue
        report["kept"] += 1
        report["positive" if verified is True else "negative"] += 1
        examples.append({
            "signature": row.get("error_signature"),
            "error_class": row.get("error_class"),
            "context": row.get("context") or {},
            "action": row.get("chosen"),
            "verified": bool(verified),
            "latency_ms": row.get("latency_ms"),
            "ts": row.get("ts"),
            # Recorded, never re-derived: whether ATLAS's ranking was followed
            # when this happened. History gathered under the deterministic
            # order is still valid history — it just was not ATLAS's doing.
            "atlas_chose": bool(row.get("used")),
        })
    return examples, report


def _cell(example, level):
    """Backoff keys, most specific first."""
    context = example.get("context") or {}
    if level == 0:
        return example.get("signature") or example.get("error_class")
    if level == 1:
        return "{0}|{1}|{2}".format(
            example.get("error_class"), context.get("page"),
            context.get("field"))
    return example.get("error_class")


LEVELS = ("exact signature", "error class + page + field", "error class")


def recall(signature, error_class=None, context=None, path=None,
           events=None, half_life_days=None):
    """
    What history says about this failure, at the most specific level that has
    any verified evidence.

    Returns a report. When nothing qualifies it says so in words rather than
    returning an empty structure that a caller might read as "nothing worked".
    """
    half_life = float(half_life_days or config.HALF_LIFE_DAYS)
    examples, report = labelled(path, events)
    probe = {"signature": signature, "error_class": error_class,
             "context": context or {}}

    out = {
        "signature": signature,
        "error_class": error_class,
        "level": None,
        "level_name": None,
        "examples": 0,
        "verified_examples": 0,
        "actions": [],
        "winner": None,
        "sufficient": False,
        "reason": NO_HISTORY,
        "corpus": report,
    }

    for level in (0, 1, 2):
        key = _cell(probe, level)
        if not key:
            continue
        matching = [e for e in examples if _cell(e, level) == key]
        if not matching:
            continue

        by_action = {}
        for example in matching:
            bucket = by_action.setdefault(example["action"], {
                "action": example["action"], "trials": 0.0, "successes": 0.0,
                "raw_trials": 0, "raw_successes": 0, "latencies": [],
                "last_seen": None, "age_days": None,
            })
            age = _age_days(example.get("ts"))
            weight = recency_weight(age, half_life)
            bucket["trials"] += weight
            bucket["raw_trials"] += 1
            if example["verified"]:
                bucket["successes"] += weight
                bucket["raw_successes"] += 1
                if example.get("latency_ms") is not None:
                    bucket["latencies"].append(float(example["latency_ms"]))
            if bucket["age_days"] is None or age < bucket["age_days"]:
                bucket["age_days"] = age
                bucket["last_seen"] = example.get("ts")

        rows = []
        for bucket in by_action.values():
            latencies = sorted(bucket.pop("latencies"))
            bucket["median_latency_ms"] = (
                round(model_module.quantile(latencies, 0.5), 1)
                if latencies else None)
            bucket["success_rate"] = (
                round(bucket["raw_successes"] / bucket["raw_trials"], 3)
                if bucket["raw_trials"] else 0.0)
            # The ranking score. A Wilson LOWER bound, so four wins out of
            # four does not outrank twenty out of twenty-two.
            bucket["score"] = round(wilson_lower_bound(
                bucket["successes"], bucket["trials"]), 4)
            action = recovery.ACTIONS_BY_NAME.get(bucket["action"])
            bucket["risk"] = action.risk if action else None
            bucket["enough"] = bucket["raw_successes"] >= MIN_VERIFIED_EXAMPLES
            rows.append(bucket)

        # VERIFIED OUTCOME FIRST, then reliability, then latency, then
        # recency, then safety. Anything with no verified success at all
        # sorts below everything that has one, whatever its score.
        rows.sort(key=lambda r: (
            r["raw_successes"] == 0,
            -r["score"],
            r["median_latency_ms"] if r["median_latency_ms"] is not None else 1e9,
            r["age_days"] if r["age_days"] is not None else 1e9,
            r["risk"] if r["risk"] is not None else 1.0,
        ))

        verified_total = sum(r["raw_successes"] for r in rows)
        out.update(level=level, level_name=LEVELS[level],
                   examples=len(matching), verified_examples=verified_total,
                   actions=rows)
        best = rows[0] if rows else None
        if best is None or best["raw_successes"] == 0:
            out["reason"] = (
                "{0} recovery attempt(s) recorded at the {1} level, but none "
                "of them verified, so there is no successful history to "
                "recommend from.".format(len(matching), LEVELS[level]))
            return out
        out["winner"] = best["action"]
        if (verified_total >= MIN_VERIFIED_TOTAL
                and best["raw_successes"] >= MIN_VERIFIED_EXAMPLES):
            out["sufficient"] = True
            out["reason"] = (
                "{0} verified outcome(s) at the {1} level; {2} won {3}/{4} "
                "with a ranking score of {5:.2f}.".format(
                    verified_total, LEVELS[level], best["action"],
                    best["raw_successes"], best["raw_trials"], best["score"]))
        else:
            out["reason"] = (
                "{0} verified outcome(s) at the {1} level — {2} leads with "
                "{3}/{4} — but {5} in total and {6} for one action are "
                "required before this is acted on.".format(
                    verified_total, LEVELS[level], best["action"],
                    best["raw_successes"], best["raw_trials"],
                    MIN_VERIFIED_TOTAL, MIN_VERIFIED_EXAMPLES))
        return out

    return out


def scores(signature, error_class=None, context=None, path=None, events=None):
    """
    {action: ranking score} from verified history, for the ranker.

    Empty when the history is not sufficient — so a thin history cannot move
    the order at all, rather than moving it a little.
    """
    memory = recall(signature, error_class, context, path, events)
    if not memory["sufficient"]:
        return {}
    return {row["action"]: row["score"] for row in memory["actions"]
            if row["raw_successes"]}


def evaluate(signature, error_class=None, context=None, path=None,
             events=None, echo=None):
    """
    "For this failure signature, what would ATLAS recommend, and on what?"

    Returns the report and, when `echo` is given, prints it. Everything shown
    is counted from the rows; nothing is asserted that the corpus does not
    support.
    """
    memory = recall(signature, error_class, context, path, events)
    if echo:
        echo("FAILURE SIGNATURE: {0}".format(signature))
        if error_class:
            echo("  error class            : {0}".format(error_class))
        corpus = memory["corpus"]
        echo("  recovery rows on file  : {0}".format(corpus["rows"]))
        echo("    usable examples      : {0} "
             "({1} verified success, {2} verified failure)".format(
                 corpus["kept"], corpus["positive"], corpus["negative"]))
        echo("    excluded, WOULD_TRY  : {0}".format(
            corpus["dropped_not_executed"]))
        echo("    excluded, unverified : {0}".format(
            corpus["dropped_unverified"]))
        echo("    excluded, no action  : {0}".format(
            corpus["dropped_no_action"]))
        echo("    excluded, test rows  : {0}".format(
            corpus["dropped_not_real"]))
        echo("  matched at level       : {0}".format(
            memory["level_name"] or "nothing matched"))
        echo("  verified examples here : {0}".format(
            memory["verified_examples"]))
        if memory["actions"]:
            echo("  {0:<26} {1:>7} {2:>8} {3:>9} {4:>7} {5:>6}".format(
                "action", "score", "verified", "median ms", "days", "risk"))
            for row in memory["actions"]:
                echo("  {0:<26} {1:>7.2f} {2:>8} {3:>9} {4:>7.1f} {5:>6}".format(
                    row["action"], row["score"],
                    "{0}/{1}".format(row["raw_successes"], row["raw_trials"]),
                    "-" if row["median_latency_ms"] is None
                    else int(row["median_latency_ms"]),
                    row["age_days"] or 0.0,
                    "-" if row["risk"] is None else row["risk"]))
        echo("  would recommend        : {0}".format(
            memory["winner"] or "nothing"))
        echo("  sufficient evidence    : {0}".format(memory["sufficient"]))
        echo("  {0}".format(memory["reason"]))
    return memory


def main(argv=None):
    """
    python -m ml.memory                     every signature that has history
    python -m ml.memory <signature>         the evaluator, for one signature
    """
    import sys as _sys
    argv = list(_sys.argv[1:] if argv is None else argv)
    if argv:
        evaluate(argv[0], argv[1] if len(argv) > 1 else None, echo=print)
        return 0

    examples, report = labelled()
    print("RECOVERY HISTORY — {0}".format(config.TELEMETRY_PATH))
    print("  recovery rows        : {0}".format(report["rows"]))
    print("  usable examples      : {0} ({1} verified success, "
          "{2} verified failure)".format(report["kept"], report["positive"],
                                         report["negative"]))
    print("  excluded, WOULD_TRY  : {0}".format(report["dropped_not_executed"]))
    print("  excluded, unverified : {0}".format(report["dropped_unverified"]))
    print("  excluded, no action  : {0}".format(report["dropped_no_action"]))
    print("  excluded, test rows  : {0}".format(report["dropped_not_real"]))
    if not examples:
        print("")
        print("  {0}".format(NO_HISTORY))
        print("  Recovery history is written when a recovery action is")
        print("  ACTUALLY_TRIED against a real page and the caller's own")
        print("  verification then confirms it. Nothing else counts.")
        return 0
    seen = {}
    for example in examples:
        key = example["signature"] or example["error_class"]
        seen.setdefault(key, 0)
        seen[key] += 1
    print("")
    for key, count in sorted(seen.items(), key=lambda kv: -kv[1]):
        print("")
        evaluate(key, echo=lambda s: print("  " + s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
