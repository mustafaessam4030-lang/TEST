"""
Shadow scorecard: did the model's choices beat the automation's own order on
real runs, judged only by what the Hub read back?

In shadow mode the model picks a strategy for every write and the automation
ignores it, using its own order. Each decision row now carries the write's
episode_id, so the pick can be set against that write's verified outcome:

    winner     the strategy that found the field, in an episode whose write
               was read back out of the Hub and CONFIRMED
    baseline   the first strategy in the automation's own order (always tried
               first, so its outcome is always known)
    model      the strategy the model would have put first

CONSERVATIVE BY CONSTRUCTION. A pick scores a hit only if it IS the verified
winner. A model pick that was never tried (because the automation's order
found the field first) is counted as a miss, not guessed at — so the
scorecard can understate the model, never flatter it. Episodes with no
read-back verdict are excluded and counted; a write read back WRONG has no
winner, so neither side scores.

    python -m ml.shadow

Read-only. It trains nothing, promotes nothing, approves nothing.
"""

import argparse
import sys

from . import config, episodes

BETTER, WORSE, NO_DIFFERENCE, INSUFFICIENT = (
    "BETTER", "WORSE", "NO DIFFERENCE", "INSUFFICIENT DATA")


def score(events=None, path=None, min_decisions=100, min_disagreements=20,
          margin=0.05):
    events = episodes._read(path or config.TELEMETRY_PATH) if events is None else events
    eps = episodes.collect(events=events)
    tried = {}
    for raw in events:
        if raw.get("kind") != "interaction" or raw.get("source", "automation") != "automation":
            continue
        role = raw.get("role") or ("verification" if raw.get("strategy") == "verify_reload"
                                   else "strategy")
        if role != "strategy" or not raw.get("episode_id"):
            continue
        tried.setdefault(raw["episode_id"], []).append(raw)

    out = {"decisions": 0, "no_episode_id": 0, "no_verdict": 0, "scored": 0,
           "baseline_hits": 0, "model_hits": 0, "disagreements": 0,
           "model_wins": 0, "baseline_wins": 0, "both_missed": 0,
           "model_untried": 0}
    for raw in events:
        if raw.get("kind") != "decision" or raw.get("source", "automation") != "automation":
            continue
        if not raw.get("shadow") or raw.get("used") or not raw.get("chosen"):
            continue
        out["decisions"] += 1
        episode_id = raw.get("episode_id")
        if not episode_id:
            out["no_episode_id"] += 1
            continue
        episode = eps.get(episode_id)
        if episode is None or not episode.has_verdict:
            out["no_verdict"] += 1
            continue
        candidates = list(raw.get("candidates") or [])
        if not candidates:
            out["no_verdict"] += 1
            continue
        attempts = tried.get(episode_id, [])
        winner = None
        if episode.confirmed:
            found = [a["strategy"] for a in attempts if a.get("success")]
            winner = found[-1] if found else None
        baseline, model = candidates[0], raw["chosen"]
        b_hit, m_hit = baseline == winner, model == winner and winner is not None
        out["scored"] += 1
        out["baseline_hits"] += b_hit
        out["model_hits"] += m_hit
        if model != baseline:
            out["disagreements"] += 1
            if m_hit and not b_hit:
                out["model_wins"] += 1
            elif b_hit and not m_hit:
                out["baseline_wins"] += 1
            else:
                out["both_missed"] += 1
            if model not in {a.get("strategy") for a in attempts}:
                out["model_untried"] += 1

    n = out["scored"]
    out["baseline_rate"] = out["baseline_hits"] / n if n else None
    out["model_rate"] = out["model_hits"] / n if n else None
    if n < min_decisions or out["disagreements"] < min_disagreements:
        out["verdict"] = INSUFFICIENT
        out["reason"] = ("{0} scored shadow decisions ({1} needed) and {2} where the "
                         "model disagreed with the automation ({3} needed)".format(
                             n, min_decisions, out["disagreements"], min_disagreements))
    else:
        gain = out["model_rate"] - out["baseline_rate"]
        if gain > margin and out["model_wins"] > out["baseline_wins"]:
            out["verdict"] = BETTER
        elif gain < -margin:
            out["verdict"] = WORSE
        else:
            out["verdict"] = NO_DIFFERENCE
        out["reason"] = ("first-try verified success: automation {0:.0%}, model {1:.0%} "
                         "over {2} decisions; where they disagreed ({3}): model right "
                         "{4}, automation right {5}".format(
                             out["baseline_rate"], out["model_rate"], n,
                             out["disagreements"], out["model_wins"],
                             out["baseline_wins"]))
    return out


def render(result):
    return "\n".join([
        "Shadow scorecard: {0}".format(result["verdict"]),
        "  shadow decisions recorded        {0}".format(result["decisions"]),
        "  scored against a Hub read-back    {0}".format(result["scored"]),
        "  excluded: no episode link         {0}  (recorded before this was tracked)"
        .format(result["no_episode_id"]),
        "  excluded: write never read back   {0}".format(result["no_verdict"]),
        "  model choice never tried (=miss)  {0}".format(result["model_untried"]),
        "",
        result["reason"],
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--telemetry", default=None)
    args = parser.parse_args(argv)
    print(render(score(path=args.telemetry)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
