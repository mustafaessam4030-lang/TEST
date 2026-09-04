"""
Joining every strategy attempt to the write it caused.

THE PROBLEM THIS SOLVES

Telemetry records two very different things in the same file. An *interaction*
is one attempt at one small mechanical act — this locator was tried, it matched
or it did not, it took this long. An *episode* is a whole write: open Manage,
select the tab, find the field, type the date, save, reopen, read it back.

The label the operator asked for lives on the episode, not the interaction. A
locator that matched an input is not a success if the date it led to was not in
the Hub afterwards. Without the join, the model learns "did an element appear",
which is a question nobody was asking.

WHAT COUNTS AS WHAT

    positive      the attempt found the field AND its episode was read back
                  and confirmed
    negative      the attempt was tried and did not find the field, or found
                  it and the read-back disagreed
    excluded      the episode has no read-back outcome at all

That last line is the one that matters most, and it is why this module exists
rather than a two-line filter. An unverified episode is not a failed episode.
Counting it as a negative would put invented failures into the training data,
and — worse — it would do so ASYMMETRICALLY: unverified episodes can only ever
contribute negatives, never positives, so every strategy's rate would be pulled
down by an amount that depends on how often verification happened to be on.
That is manufactured evidence. The whole episode is dropped instead, and the
report says how many were dropped and why.

With ML_REQUIRE_VERIFIED_LABEL=0 the strict rule relaxes to "found the field",
which is the OLD label. It is available because a site without read-back
verification would otherwise produce no training data at all, but it is not the
default and anything trained under it is marked as such in the model metadata
so a weaker label can never be mistaken for the real one later.
"""

import json
from pathlib import Path

from . import config, features, reward, telemetry


class Episode:
    """One write operation and how it ended."""

    __slots__ = ("episode_id", "reference", "view", "field", "value",
                 "outcome", "verified", "duration_ms", "ts")

    def __init__(self, episode_id, reference=None, view=None, field=None,
                 value=None, outcome=telemetry.EPISODE_ERROR, verified=None,
                 duration_ms=None, ts=None):
        self.episode_id = episode_id
        self.reference = reference
        self.view = view
        self.field = field
        self.value = value
        self.outcome = outcome
        self.verified = verified
        self.duration_ms = duration_ms
        self.ts = ts

    @property
    def has_verdict(self):
        """Did read-back actually run? None means it did not."""
        return self.verified is not None

    @property
    def confirmed(self):
        return self.verified is True

    @property
    def contradicted(self):
        return self.verified is False

    def __repr__(self):
        return "Episode({0} {1}/{2} {3})".format(
            self.episode_id, self.view, self.field, self.outcome)


class Labelled:
    """One interaction with its episode's verdict attached."""

    __slots__ = ("context", "strategy", "found", "duration_ms", "category",
                 "reference", "ts", "rank", "retries", "episode_id",
                 "label", "reward", "credit")

    def __init__(self, context, strategy, found, duration_ms, category,
                 reference, ts, rank, retries, episode_id,
                 label, reward_value, credit_value):
        self.context = context
        self.strategy = strategy
        self.found = found
        self.duration_ms = duration_ms
        self.category = category
        self.reference = reference
        self.ts = ts
        self.rank = rank
        self.retries = retries
        self.episode_id = episode_id
        self.label = label                # True = verified persisted success
        self.reward = reward_value
        self.credit = credit_value

    def keys(self):
        return features.keys(self.context)

    def __repr__(self):
        return "Labelled({0} {1} label={2} reward={3:.3f})".format(
            features.describe(self.context), self.strategy,
            self.label, self.reward)


def _read(path):
    """Every well-formed event in the telemetry file, in order."""
    events = []
    path = Path(path)
    if not path.exists():
        return events
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(raw, dict):
                events.append(raw)
    return events


def collect(path=None, events=None):
    """
    episode_id -> Episode, from the `episode` events in the telemetry.

    Later events for the same id win. An episode can legitimately be written
    twice — the automation records an ERROR outcome if it is torn down early
    and the real outcome when it completes — and the last word is the true one.
    """
    events = _read(path or config.TELEMETRY_PATH) if events is None else events
    found = {}
    for raw in events:
        if raw.get("kind") != "episode":
            continue
        if raw.get("source", "automation") != "automation":
            continue
        episode_id = raw.get("episode_id")
        if not episode_id:
            continue
        found[episode_id] = Episode(
            episode_id=episode_id,
            reference=raw.get("reference"),
            view=raw.get("view"),
            field=raw.get("field"),
            value=raw.get("value"),
            outcome=raw.get("outcome") or telemetry.EPISODE_ERROR,
            verified=raw.get("verified"),
            duration_ms=raw.get("duration_ms"),
            ts=raw.get("ts"),
        )
    return found


def join(path=None, events=None, require_verified=None):
    """
    Labelled rows, plus a report that accounts for every line.

    Returns (rows, report). Nothing is silently dropped: every interaction ends
    up in exactly one of the report's counters, so "we have 40 usable rows" is
    always accompanied by where the other rows went.
    """
    if require_verified is None:
        require_verified = config.REQUIRE_VERIFIED_LABEL

    events = _read(path or config.TELEMETRY_PATH) if events is None else events
    episodes = collect(events=events)

    report = {
        "path": str(path or config.TELEMETRY_PATH),
        "events": len(events),
        "episodes": len(episodes),
        "episodes_with_verdict": sum(1 for e in episodes.values() if e.has_verdict),
        "interactions": 0,
        "kept": 0,
        "positive": 0,
        "negative": 0,
        "dropped_not_real": 0,
        "dropped_no_episode_id": 0,
        "dropped_unknown_episode": 0,
        "dropped_unverified_episode": 0,
        "label_rule": "verified_persisted_success" if require_verified
                      else "field_found_only",
        "feature_version": features.FEATURE_VERSION,
    }

    rows = []
    for raw in events:
        if raw.get("kind") != "interaction":
            continue
        report["interactions"] += 1

        # A test run's telemetry may never become training data. The suite
        # writes to the same file by design — it exercises the real writer —
        # and a model built partly from fixtures would be confident about
        # pages that do not exist.
        if raw.get("source", "automation") != "automation":
            report["dropped_not_real"] += 1
            continue

        episode_id = raw.get("episode_id")
        episode = episodes.get(episode_id) if episode_id else None

        if require_verified:
            if not episode_id:
                report["dropped_no_episode_id"] += 1
                continue
            if episode is None:
                # The run was killed between the attempt and the episode
                # record. Real data, unusable: there is no verdict to attach.
                report["dropped_unknown_episode"] += 1
                continue
            if not episode.has_verdict:
                report["dropped_unverified_episode"] += 1
                continue
            verified = episode.confirmed
            verification_failed = episode.contradicted
        else:
            # The relaxed rule. The attempt's own outcome is the label, which
            # is exactly what the layer did before episodes existed.
            verified = bool(raw.get("success"))
            verification_failed = False

        found = bool(raw.get("success"))
        category = raw.get("category") or telemetry.NONE
        duration_ms = raw.get("duration_ms")
        retries = raw.get("retries") or 0

        value = reward.compute(
            found=found, verified=verified, duration_ms=duration_ms,
            retries=retries, category=category,
            verification_failed=verification_failed and found)

        label = bool(found and verified)
        rows.append(Labelled(
            context=features.clean(raw.get("context") or {}),
            strategy=str(raw.get("strategy") or "unknown"),
            found=found,
            duration_ms=duration_ms,
            category=category,
            reference=raw.get("reference"),
            ts=raw.get("ts"),
            rank=raw.get("rank"),
            retries=retries,
            episode_id=episode_id,
            label=label,
            reward_value=value,
            credit_value=reward.credit(value),
        ))
        report["kept"] += 1
        report["positive" if label else "negative"] += 1

    return rows, report


def explain_shortfall(report):
    """
    Why there is not enough data, in the operator's terms.

    Called when training refuses. The point is that "not enough data" should
    never be the end of the sentence — it should say which of the four things
    is missing so the next run can fix it.
    """
    if not report.get("interactions"):
        return ("No interactions have been recorded yet. The telemetry file "
                "fills on the first real run of the automation.")
    if report.get("dropped_not_real") == report.get("interactions"):
        return ("Every recorded interaction came from the test suite. Test "
                "telemetry is never trained on; run the automation itself.")
    if report.get("dropped_no_episode_id"):
        return ("{0} of {1} interactions carry no episode id, so they cannot "
                "be joined to a write outcome. Those were recorded by a build "
                "older than the episode join."
                .format(report["dropped_no_episode_id"], report["interactions"]))
    if report.get("dropped_unverified_episode"):
        return ("{0} interactions belong to episodes whose value was never "
                "read back, so there is no verified outcome to label them "
                "with. Run with VERIFY_AFTER_SAVE=1 — that check is what turns "
                "a run into training data."
                .format(report["dropped_unverified_episode"]))
    return ("{0} usable rows so far.".format(report.get("kept", 0)))


def summarise(rows):
    """Counts by strategy with mean reward — the sanity check before training."""
    by_strategy = {}
    for row in rows:
        entry = by_strategy.setdefault(
            row.strategy, {"rows": 0, "positive": 0, "credit": 0.0,
                           "reward": 0.0})
        entry["rows"] += 1
        entry["positive"] += 1 if row.label else 0
        entry["credit"] += row.credit
        entry["reward"] += row.reward
    for entry in by_strategy.values():
        entry["mean_reward"] = entry["reward"] / max(1, entry["rows"])
        entry["rate"] = entry["positive"] / max(1, entry["rows"])
    return {"rows": len(rows), "strategies": by_strategy}
