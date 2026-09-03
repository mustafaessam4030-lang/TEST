"""
Answers that come from the telemetry file rather than the run snapshot.

The existing assistant reasons over `bridge.snapshot()` — everything the
CURRENT run has seen. That is the right source for "where is 057-05765454",
but it cannot answer "why is AFKL slower", because that question is about
history the snapshot does not hold.

This module reads the telemetry the automation writes and answers those
questions with counted observations. It states the sample size next to every
figure, and when there is nothing to count it says so rather than reaching for
a plausible-sounding number. The rule is the one the operator asked for: DATA
FIRST, LANGUAGE SECOND. If the data is not there, there is no answer.

Read-only. Nothing in here writes, trains or changes a setting.
"""

import json
import time
from pathlib import Path

try:
    from ml import config as ml_config
    AVAILABLE = True
except Exception:                       # pragma: no cover
    AVAILABLE = False
    ml_config = None

MAX_SCAN_BYTES = 8 * 1024 * 1024
CACHE_SECONDS = 20
_cache = {"at": 0.0, "rows": None}

CARRIER_LABELS = {
    "DHL": "DHL", "AFKL": "Air France / KLM", "QATAR": "Qatar Airways Cargo",
    "ASTRAL": "Astral Aviation", "HUB": "the Hub",
}


def _label(key):
    return CARRIER_LABELS.get(str(key).upper(), str(key))


def load(path=None, force=False):
    """
    Telemetry interactions, cached briefly.

    Only rows the automation wrote are counted — anything tagged as a test is
    skipped, the same rule the trainer applies, so a test run can never end up
    in an answer given to an operator.
    """
    now = time.time()
    if not force and _cache["rows"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["rows"]

    rows = []
    target = Path(path or (ml_config.TELEMETRY_PATH if AVAILABLE else ""))
    if target and str(target) and target.exists():
        try:
            size = target.stat().st_size
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                if size > MAX_SCAN_BYTES:
                    handle.seek(size - MAX_SCAN_BYTES)
                    handle.readline()
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("kind") != "interaction":
                        continue
                    if event.get("source", "automation") != "automation":
                        continue
                    rows.append(event)
        except OSError:
            pass

    _cache["at"] = now
    _cache["rows"] = rows
    return rows


def _by(rows, field):
    out = {}
    for event in rows:
        key = (event.get("context") or {}).get(field) or "unknown"
        bucket = out.setdefault(key, {"attempts": 0, "successes": 0, "ms": []})
        bucket["attempts"] += 1
        if event.get("success"):
            bucket["successes"] += 1
            duration = event.get("duration_ms")
            if isinstance(duration, (int, float)):
                bucket["ms"].append(float(duration))
    for bucket in out.values():
        samples = sorted(bucket["ms"])
        bucket["median_ms"] = samples[len(samples) // 2] if samples else None
        bucket["mean_ms"] = (sum(samples) / len(samples)) if samples else None
        bucket["timed"] = len(samples)
        bucket["rate"] = (bucket["successes"] / bucket["attempts"]
                          if bucket["attempts"] else None)
    return out


def _seconds(ms):
    return "{0:.1f}s".format(ms / 1000.0) if ms >= 1000 else "{0:.0f}ms".format(ms)


def have_data(rows=None):
    rows = load() if rows is None else rows
    return len(rows) > 0


def no_data_answer(topic="that"):
    """The honest answer when the telemetry cannot support a claim."""
    if not AVAILABLE:
        return ("The learning package is not installed here, so I have no "
                "execution telemetry to answer {0} from.".format(topic))
    path = Path(ml_config.TELEMETRY_PATH)
    if not path.exists():
        return ("There is no execution telemetry on this machine yet "
                "({0} does not exist), so I cannot answer {1} from measured "
                "data. It starts filling on the first run.".format(path.name, topic))
    return ("The telemetry file exists but holds no automation observations "
            "yet, so I have nothing measured to answer {0} from.".format(topic))


# ── the answers ──────────────────────────────────────────────────────

def carrier_timing(rows=None):
    """Median successful interaction time per carrier, with sample sizes."""
    rows = load() if rows is None else rows
    if not rows:
        return None
    groups = {k: v for k, v in _by(rows, "provider").items()
              if v["timed"] >= 3 and k != "unknown"}
    if not groups:
        return ("I have {0} recorded interactions, but not enough of them "
                "carry a duration yet to compare carriers. A comparison needs "
                "at least three timed successes per carrier."
                .format(len(rows)))

    ordered = sorted(groups.items(), key=lambda kv: -(kv[1]["median_ms"] or 0))
    lines = ["Median time per successful interaction, from recorded telemetry:"]
    for key, bucket in ordered:
        lines.append("- **{0}** — {1} (median of {2} timed successes, "
                     "{3:.0f}% success over {4} attempts)".format(
                         _label(key), _seconds(bucket["median_ms"]),
                         bucket["timed"], 100 * (bucket["rate"] or 0),
                         bucket["attempts"]))
    if len(ordered) > 1:
        slow, fast = ordered[0], ordered[-1]
        lines.append("")
        lines.append("{0} is the slowest and {1} the fastest, a gap of {2}."
                     .format(_label(slow[0]), _label(fast[0]),
                             _seconds(abs((slow[1]["median_ms"] or 0)
                                          - (fast[1]["median_ms"] or 0)))))
    return "\n".join(lines)


def strategy_performance(rows=None, field=None):
    """Which locator strategies actually work, counted."""
    rows = load() if rows is None else rows
    if not rows:
        return None
    if field:
        rows = [r for r in rows
                if str((r.get("context") or {}).get("field", "")).upper()
                == field.upper()]
        if not rows:
            return ("I have telemetry, but none of it is for the {0} field yet."
                    .format(field.upper()))

    groups = _by(rows, "field") if not field else None
    buckets = {}
    for event in rows:
        name = event.get("strategy") or "unknown"
        bucket = buckets.setdefault(name, {"attempts": 0, "successes": 0})
        bucket["attempts"] += 1
        if event.get("success"):
            bucket["successes"] += 1
    ranked = sorted(buckets.items(),
                    key=lambda kv: -(kv[1]["successes"] / max(1, kv[1]["attempts"])))
    lines = ["Strategy success rates{0}, from {1} recorded attempts:".format(
        " for " + field.upper() if field else "", len(rows))]
    for name, bucket in ranked[:10]:
        lines.append("- `{0}` — {1:.0f}% ({2}/{3})".format(
            name, 100 * bucket["successes"] / max(1, bucket["attempts"]),
            bucket["successes"], bucket["attempts"]))
    if groups:
        lines.append("")
        lines.append("Fields seen: " + ", ".join(sorted(groups)))
    return "\n".join(lines)


def failure_categories(rows=None):
    """What actually goes wrong, counted by category."""
    rows = load() if rows is None else rows
    if not rows:
        return None
    counts = {}
    for event in rows:
        if event.get("success"):
            continue
        category = event.get("category") or "unknown"
        counts[category] = counts.get(category, 0) + 1
    if not counts:
        return ("Every one of the {0} recorded interactions succeeded, so "
                "there are no failure categories to report.".format(len(rows)))
    total = sum(counts.values())
    lines = ["Failure categories across {0} unsuccessful attempts "
             "(of {1} total):".format(total, len(rows))]
    for category, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append("- **{0}** — {1} ({2:.0f}%)".format(
            category, count, 100 * count / total))
    return "\n".join(lines)


def verification_summary(rows=None):
    """How often a written value was proven to have persisted."""
    rows = load() if rows is None else rows
    checks = [r for r in rows if r.get("strategy") == "verify_reload"]
    if not checks:
        return ("No read-back verification has been recorded. That check only "
                "runs with VERIFY_AFTER_SAVE=1, and nothing in the telemetry "
                "shows it having run yet.")
    passed = sum(1 for r in checks if r.get("success"))
    return ("Read-back verification ran {0} time{1}: {2} confirmed the value "
            "had persisted, {3} did not.".format(
                len(checks), "" if len(checks) == 1 else "s", passed,
                len(checks) - passed))


def ml_recommendations(path=None):
    """How often the model was consulted, and how often it was trusted."""
    target = Path(path or (ml_config.TELEMETRY_PATH if AVAILABLE else ""))
    used = declined = 0
    top = {}
    if target and str(target) and target.exists():
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("kind") != "decision":
                        continue
                    if event.get("used"):
                        used += 1
                        name = event.get("chosen") or "unknown"
                        top[name] = top.get(name, 0) + 1
                    else:
                        declined += 1
        except OSError:
            pass
    if not used and not declined:
        return ("The model has not been consulted yet — no decision has been "
                "recorded. That is expected until a model exists and a run "
                "reaches a field lookup.")
    lines = ["The model was consulted {0} time{1}: it was followed {2} time{3} "
             "and declined {4} (below the confidence threshold, or with too "
             "little evidence).".format(
                 used + declined, "" if used + declined == 1 else "s",
                 used, "" if used == 1 else "s", declined)]
    if top:
        lines.append("")
        lines.append("Strategies it chose:")
        for name, count in sorted(top.items(), key=lambda kv: -kv[1])[:6]:
            lines.append("- `{0}` — {1}".format(name, count))
    return "\n".join(lines)


def sources_used(kind):
    """What a given answer was built from, for the feedback record."""
    return {
        "carrier_timing": ["ml/data/telemetry.jsonl:interaction"],
        "strategy_performance": ["ml/data/telemetry.jsonl:interaction"],
        "failure_categories": ["ml/data/telemetry.jsonl:interaction"],
        "verification": ["ml/data/telemetry.jsonl:interaction:verify_reload"],
        "ml_recommendations": ["ml/data/telemetry.jsonl:decision"],
    }.get(kind, [])
