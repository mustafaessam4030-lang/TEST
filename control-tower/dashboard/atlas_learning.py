"""
ATLAS's answers about what it has learned, the evidence it holds, and how it
is evaluated. Read-only: it reads the intelligence stores and never writes.

Every answer keeps five kinds of statement apart, and labels them:

    Fact               counted from recorded events or read off real evidence
    Learned pattern    what verified outcomes add up to, with its sample
    Inference          a conclusion drawn from facts, saying which ones
    Recommendation     what to try, and on what record
    Unverified         seen, but not confirmed by the verification pipeline

If the stores hold nothing on a question, the answer says so.
"""

import re
import sys
import time
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    from intelligence import events, learning, plans, evidence, vision, maturity
    AVAILABLE = True
except Exception:                                   # pragma: no cover
    AVAILABLE = False

PROVIDERS = [("AFKL", r"\bafkl\b|air\s*france|\bklm\b"), ("QATAR", r"qatar"),
             ("DHL", r"\bdhl\b"), ("GRIMALDI", r"grimaldi"), ("MSC", r"\bmsc\b"),
             ("MAERSK", r"maersk"), ("CMA_CGM", r"cma\s*cgm|\bcma\b"), ("COSCO", r"cosco"),
             ("ONE", r"\bone\b(?=.*(?:line|carrier|shipment|ocean))"),
             ("HAPAG", r"hapag"), ("HUB", r"\bhub\b")]

INTENTS = [
    ("image_compare", ["compare this screenshot", "compare this image", "compare the screenshot",
                       "compare it with the previous", "compare with the previous failure"]),
    ("image_read", ["this screenshot", "this image", "the screenshot i sent", "the image i sent",
                    "this photo", "the photo", "this picture", "the picture", "attached photo",
                    "the image", "in the photo", "in the picture",
                    "the screenshot i uploaded", "the image i uploaded", "from the screenshot",
                    "attached screenshot", "attached image", "in the screenshot"]),
    ("evidence", ["show me the screenshot", "show the screenshot", "send me the screenshot",
                  "failed screenshot", "screenshot from", "screenshot of", "carrier page looks like",
                  "show me the evidence", "show the evidence", "show me a screenshot",
                  "what did the page look like", "screenshots"]),
    ("why_recovery_failed", ["why didn't the previous recovery", "why didnt the previous recovery",
                             "why did the recovery fail", "why didn't the recovery",
                             "why didnt the recovery", "previous recovery work"]),
    ("learn_failure", ["learn from this failure", "learned from this failure",
                       "learn from that failure", "learn from the failure"]),
    ("monthly_review", ["monthly review", "learn this month", "learned this month",
                        "this month's review", "atlas learn this month", "review of the month"]),
    ("star", ["star level", "stars", "maturity", "atlas level", "what level is atlas",
              "current level", "how mature"]),
    ("how_many_strategies", ["how many verified", "verified recovery strategies",
                             "verified strategies", "how many strategies"]),
    ("what_fixes", ["usually fixes", "what fixes", "works best", "best strategy", "best recovery",
                    "which recovery strategy", "recovery strategy works", "which strategy works"]),
    ("try_next", ["try next", "what should we try", "what to try"]),
    ("plan", ["recovery plan", "create a plan", "make a plan", "plan for", "plan to recover"]),
    ("why_recommend", ["why do you recommend", "why recommend", "why that strategy",
                       "why this strategy", "reason for the recommendation"]),
    ("confidence", ["how confident", "how sure", "confidence"]),
    ("month_compare", ["compared with last month", "compared to last month", "vs last month",
                       "than last month", "since last month", "how has", "improved",
                       "changed compared"]),
    ("keeps_failing", ["keeps failing", "keep failing", "recurring failure", "recurring issue",
                       "fails most", "what keeps", "most common failure", "repeat failures"]),
    ("human_patterns", ["human actions usually", "usually take", "human action stats",
                        "how often does", "average human wait", "human action patterns",
                        "how long do human actions", "verification required times"]),
    ("learned", ["what have we learned", "what did we learn", "what has atlas learned",
                 "what have you learned", "what did you learn", "what has been learned",
                 "what do you know about", "learning"]),
]


def detect(question):
    if not AVAILABLE:
        return None
    lowered = " {0} ".format(" ".join(str(question or "").casefold().split()))
    for name, phrases in INTENTS:
        if any(p in lowered for p in phrases):
            return name
    return None


def provider_in(question):
    for key, pattern in PROVIDERS:
        if re.search(pattern, question or "", re.I):
            return key
    return None


def _pct(x):
    return "{0:.0%}".format(x) if isinstance(x, (int, float)) else "no data"


def _strategy_line(s):
    line = "{0}: {1}/{2} verified successes ({3}), confidence {4}".format(
        s["name"], s["successes"], s["decided"], _pct(s["rate"]), s["confidence"])
    if s["unverified"]:
        line += "; {0} more appeared to work but were NOT verified".format(s["unverified"])
    if s["skipped"]:
        line += "; skipped {0}×".format(s["skipped"])
    return line


def _issue_block(issue, top=4):
    lines = ["**Fact** — {0} {1}: {2} occurrence(s) across {3} run(s), first {4}, last {5}.".format(
        issue["carrier"] or issue["provider"], issue["issue"], issue["occurrences"],
        len(issue["runs"]), issue["first_seen"], issue["last_seen"])]
    strategies = sorted(issue["strategies"].values(),
                        key=lambda s: (s["wilson"], s["successes"]), reverse=True)
    decided = [s for s in strategies if s["decided"]]
    if decided:
        lines.append("**Learned pattern** — recovery strategies, verified outcomes only:")
        lines += ["• " + _strategy_line(s) for s in decided[:top]]
    unverified = [s for s in strategies if not s["decided"] and s["unverified"]]
    if unverified:
        lines.append("**Unverified** — " + "; ".join(
            "{0}: {1} attempt(s) with no verified outcome".format(s["name"], s["unverified"])
            for s in unverified[:3]))
    if issue.get("best"):
        b = issue["strategies"][issue["best"]]
        lines.append("Best observed: **{0}** — evidence runs: {1}.".format(
            issue["best"], ", ".join(b["evidence_runs"][-4:]) or "none listed"))
    elif strategies:
        lines.append("No strategy has enough verified outcomes to rank yet "
                     "(needs {0} decided attempts with at least one verified success).".format(
                         learning.MIN_RANKED))
    return lines


def _pick_issue(snap, question, context_record=None):
    provider = provider_in(question)
    name = None
    if context_record is not None:
        provider = provider or context_record.get("provider")
        name = context_record.get("outcome")
    found = learning.find_issue(snap, provider=provider, name=name) if (provider or name) else []
    if not found and provider:
        found = learning.find_issue(snap, provider=provider)
    if not found and not provider:
        found = [i for i in snap["issues"] if i["strategies"]] or snap["issues"]
    return found[0] if found else None, provider


def _none(snap):
    if not snap["events"]:
        return ("**Fact** — nothing measured yet: no real run has been recorded, so I have "
                "no learning to answer from and I won't guess. Outcomes, recoveries, human "
                "actions and evidence start accumulating with the next run.")
    return None


def answer(intent, question, data, context, evidence_id=None):
    """(text, extras) or None. extras may carry evidence, reading, plan, reference."""
    if not AVAILABLE:
        return None
    snap = learning.snapshot()
    record = data.find(context.get("reference")) if context.get("reference") else None
    handler = {
        "learned": _learned, "keeps_failing": _keeps_failing, "what_fixes": _what_fixes,
        "try_next": _try_next, "plan": _plan, "why_recommend": _why_recommend,
        "confidence": _confidence, "month_compare": _month_compare,
        "how_many_strategies": _how_many, "star": _star, "monthly_review": _review,
        "human_patterns": _human, "why_recovery_failed": _why_recovery_failed,
        "learn_failure": _learn_failure, "evidence": _evidence,
        "image_read": _image_read, "image_compare": _image_compare,
    }.get(intent)
    if handler is None:
        return None
    return handler(snap, question, data, context, record, evidence_id)


def _learned(snap, question, data, context, record, evidence_id):
    empty = _none(snap)
    if empty:
        return empty, {}
    s = snap["summary"]
    provider = provider_in(question)
    if provider:
        issues = learning.find_issue(snap, provider=provider)
        if not issues:
            return "**Fact** — no recorded issue for {0} yet.".format(provider), {}
        lines = []
        for issue in issues[:3]:
            lines += _issue_block(issue) + [""]
        return "\n".join(lines).strip(), {}
    lines = ["**Fact** — from {0} recorded event(s) over {1} run(s):".format(snap["events"], s["runs"]),
             "• {0} issue(s) seen, {1} learned (3+ occurrences in 2+ runs)".format(
                 s["issues_seen"], s["issues_learned"]),
             "• {0} verified learning signal(s): {1} verified recovery success(es)".format(
                 s["verified_signals"], s["verified_successes"]),
             "• {0} strateg(y/ies) with a verified record".format(s["verified_strategies"]),
             "• {0} recurring operator question(s)".format(s["recurring_questions"])]
    if s["unverified"]:
        lines.append("**Unverified** — {0} attempt(s) appeared to work but did not end in a "
                     "verified Hub write; they are not counted as successes.".format(s["unverified"]))
    ranked = [i for i in snap["issues"] if i.get("best")]
    if ranked:
        lines.append("**Learned pattern** — " + "; ".join(
            "{0} {1}: {2} works best ({3}/{4} verified)".format(
                i["carrier"] or i["provider"], i["issue"], i["best"],
                i["strategies"][i["best"]]["successes"], i["strategies"][i["best"]]["decided"])
            for i in ranked[:3]) + ".")
    return "\n".join(lines), {}


def _keeps_failing(snap, question, data, context, record, evidence_id):
    empty = _none(snap)
    if empty:
        return empty, {}
    # What keeps failing is what keeps ending unresolved, not what reliably
    # recovers: unresolved first, then frequency.
    issues = sorted([i for i in snap["issues"] if i["occurrences"] >= 2],
                    key=lambda i: (-(i["occurrences"] - min(i["occurrences"], i["resolved_verified"])),
                                   -i["occurrences"]))
    provider = provider_in(question)
    if provider:
        issues = [i for i in issues if provider.casefold() in
                  " ".join(str(x) for x in (i["provider"], i["carrier"])).casefold()]
    if not issues:
        return "**Fact** — nothing has failed more than once in the recorded runs.", {}
    lines = ["**Fact** — recurring issues, most frequent first:"]
    for i in issues[:6]:
        unresolved = i["occurrences"] - min(i["occurrences"], i["resolved_verified"])
        lines.append("• {0} {1} — {2}× in {3} run(s); {4} resolved to a verified outcome, "
                     "{5} not".format(i["carrier"] or i["provider"], i["issue"], i["occurrences"],
                                      len(i["runs"]), i["resolved_verified"], unresolved))
    return "\n".join(lines), {}


def _what_fixes(snap, question, data, context, record, evidence_id):
    empty = _none(snap)
    if empty:
        return empty, {}
    issue, provider = _pick_issue(snap, question, record)
    if issue is None:
        return ("**Fact** — no recorded issue{0} has recovery attempts yet, so I have nothing "
                "verified to recommend.".format(" for " + provider if provider else "")), {}
    lines = _issue_block(issue)
    if issue.get("best"):
        b = issue["strategies"][issue["best"]]
        lines.append("**Recommendation** — {0}, on {1}/{2} verified outcomes (lower 95% bound "
                     "{3}).".format(issue["best"], b["successes"], b["decided"], _pct(b["wilson"])))
    return "\n".join(lines), {}


def _try_next(snap, question, data, context, record, evidence_id):
    issue, provider = _pick_issue(snap, question, record)
    if issue is None:
        return _none(snap) or "**Fact** — no recorded issue to plan from.", {}
    plan = plans.build(issue["provider"], issue["issue"], snap)
    lines = ["**Fact** — {0} {1} has occurred {2}×.".format(
        issue["carrier"] or issue["provider"], issue["issue"], issue["occurrences"])]
    untried = [s for s in plan["steps"] if s["action"] not in ("verify", "stop safely")
               and s["history"] == "no verified history yet"]
    if plan.get("recommendation"):
        r = plan["recommendation"]
        lines.append("**Recommendation** — {0} ({1}).".format(r["action"], r["why"]))
    if untried:
        lines.append("**Unverified** — never tried with a verified outcome: " +
                     ", ".join(s["action"] for s in untried[:4]) +
                     ". Worth a controlled test before relying on it.")
    if not plan.get("recommendation") and not untried:
        lines.append("Nothing in the safe vocabulary has a verified record or is untried; "
                     "the next step is a person looking at the evidence.")
    lines.append("The automation itself runs its deterministic order (ATLAS is in SHADOW); "
                 "changing that order goes through a tested, approved deployment.")
    return "\n".join(lines), {"plan": plan}


def _plan(snap, question, data, context, record, evidence_id):
    provider = provider_in(question)
    name = None
    live = data.state.get("recovery")
    if record is not None:
        provider = provider or record.get("provider")
        name = record.get("outcome")
    if not provider and live:
        provider, name = live.get("provider"), live.get("error_class")
    if not name:
        issue, provider = _pick_issue(snap, question, record)
        if issue is None:
            return ("**Fact** — I need a failure to plan for: name a carrier or ask about a "
                    "failed shipment first."), {}
        provider, name = issue["provider"], issue["issue"]
    if name and "NAVIGATION ERROR" in str(name).upper() and str(provider).upper() == "AFKL":
        name = "AFKL navigation"
    plan = plans.build(provider, name, snap,
                       live=live if live and live.get("provider") == provider else None)
    if plan.get("not_recoverable"):
        return "**Fact** — {0} is not recoverable automatically: {1}.".format(
            name, plan["not_recoverable"]), {"plan": plan}
    title = name if provider and str(provider).casefold() in str(name).casefold() \
        else "{0} {1}".format(provider or "", name).strip()
    lines = ["RECOVERY PLAN — {0}".format(title), ""]
    sim = plan["similar"]
    lines.append("**Fact** — {0} similar occurrence(s) recorded{1}.".format(
        sim["count"], ", {0} resolved to a verified outcome".format(sim["resolved_verified"])
        if sim["count"] else ""))
    if plan["hypotheses"]:
        lines.append("**Inference** — likely causes (hypotheses, not findings):")
        lines += ["• {0}: {1}".format(h["name"], h["cause"]) for h in plan["hypotheses"][:4]]
    lines.append("Plan:")
    for step in plan["steps"]:
        lines.append("{0}. {1} — {2}. History: {3}. Status: {4}.".format(
            step["n"], step["action"], step["detail"] or "", step["history"] or "—", step["status"]))
    if plan.get("recommendation"):
        lines.append("**Recommendation** — start with {0}.".format(plan["recommendation"]["action"]))
    lines.append("Executes: {0}. No step is reported as run unless it ran.".format(plan["executes"]))
    return "\n".join(lines), {"plan": plan}


def _why_recommend(snap, question, data, context, record, evidence_id):
    issue, provider = _pick_issue(snap, question, record)
    if issue is None or not issue.get("best"):
        return ("**Fact** — I have no ranked recommendation{0}: no strategy has enough verified "
                "outcomes yet.".format(" for " + provider if provider else "")), {}
    b = issue["strategies"][issue["best"]]
    others = [s for s in issue["strategies"].values() if s["name"] != b["name"] and s["decided"]]
    lines = ["**Learned pattern** — {0} succeeded in {1} of {2} decided attempts on {3} {4}, "
             "each confirmed by a verified Hub write (lower 95% bound {5}).".format(
                 b["name"], b["successes"], b["decided"], issue["carrier"] or issue["provider"],
                 issue["issue"], _pct(b["wilson"]))]
    if others:
        lines.append("Compared with: " + "; ".join(_strategy_line(s) for s in others[:3]) + ".")
    lines.append("**Fact** — evidence runs: {0}.".format(", ".join(b["evidence_runs"][-5:])))
    lines.append("Ranked by the lower bound, not the raw rate, so a short lucky streak cannot "
                 "outrank a long steady record.")
    return "\n".join(lines), {}


def _confidence(snap, question, data, context, record, evidence_id):
    issue, provider = _pick_issue(snap, question, record)
    if issue is None or not issue["strategies"]:
        return "**Fact** — there is no recorded recovery to be confident about yet.", {}
    lines = ["Confidence rests on decided attempts only (verified success or failure):",
             "under 5 Insufficient data · 5–11 Low · 12–24 Medium · 25+ High."]
    for s in sorted(issue["strategies"].values(), key=lambda x: -x["decided"])[:4]:
        lines.append("• " + _strategy_line(s))
    return "\n".join(lines), {}


def _month_compare(snap, question, data, context, record, evidence_id):
    now = time.strftime("%Y-%m")
    year, mon = int(now[:4]), int(now[5:7])
    prev = "{0:04d}-{1:02d}".format(year - (mon == 1), 12 if mon == 1 else mon - 1)
    c = learning.compare(snap, prev, now)
    if not c["shipments"][0] and not c["shipments"][1]:
        return "**Fact** — there are no recorded shipments in {0} or {1} to compare.".format(prev, now), {}
    provider = provider_in(question)
    lines = ["**Fact** — {0} → {1}:".format(prev, now),
             "• shipments {0} → {1}".format(*c["shipments"]),
             "• verified success rate {0} → {1}".format(_pct(c["verified_rate"][0]),
                                                        _pct(c["verified_rate"][1])),
             "• verified recovery rate {0} → {1}".format(_pct(c["recovery_rate"][0]),
                                                         _pct(c["recovery_rate"][1])),
             "• failures {0} → {1}".format(*c["failures"])]
    issues = [i for i in c["issues"] if not provider or provider.casefold() in
              str(i["issue"]).casefold()]
    if issues:
        lines.append("By issue: " + "; ".join("{0} {1}: {2} → {3}".format(
            i["carrier"] or "", i["name"], i["before"], i["after"]) for i in issues[:5]))
    if not c["shipments"][0]:
        lines.append("There is no previous month on record, so this is a baseline, not a trend.")
    return "\n".join(lines), {}


def _how_many(snap, question, data, context, record, evidence_id):
    s = snap["summary"]
    lines = ["**Fact** — {0} recovery strateg(y/ies) have at least one verified success; "
             "{1} verified learning signal(s) in total ({2} successes).".format(
                 s["verified_strategies"], s["verified_signals"], s["verified_successes"])]
    ranked = [i for i in snap["issues"] if i.get("best")]
    if ranked:
        lines.append("Ranked best strategies: " + "; ".join(
            "{0} {1} → {2} ({3})".format(i["carrier"] or i["provider"], i["issue"], i["best"],
                                         i["strategies"][i["best"]]["confidence"])
            for i in ranked[:5]) + ".")
    if s["unverified"]:
        lines.append("**Unverified** — {0} attempt(s) not counted.".format(s["unverified"]))
    return "\n".join(lines), {}


def _star(snap, question, data, context, record, evidence_id):
    st = maturity.status()
    lines = ["ATLAS {0} — {1}.".format(st["stars"], st["title"])]
    if st["last_evaluated"]:
        lines.append("Last evaluated: {0}. Stars are earned only when every criterion of the "
                     "next level is met at a monthly evaluation.".format(st["last_evaluated"]))
    else:
        lines.append("No month has been evaluated yet — the first evaluation runs once a month "
                     "with recorded runs has ended.")
    if st["next_level"]:
        lines.append("To reach {0} {1} (measured now, {2}):".format(
            maturity.stars(st["next_level"]), st["next_title"], st["preview_month"]))
        for c in st["next_criteria"]:
            lines.append("{0} {1}: {2} (needs {3}){4}".format(
                "✓" if c["met"] else "✗", c["label"], maturity._fmt(c["value"]),
                maturity._fmt(c["minimum"]), " — " + c["why"] if c["why"] else ""))
    return "\n".join(lines), {"maturity": st}


def _review(snap, question, data, context, record, evidence_id):
    return maturity.render_review(maturity.review()), {}


def _human(snap, question, data, context, record, evidence_id):
    if not snap["human"]:
        return "**Fact** — no Human Action has been recorded yet.", {}
    provider = provider_in(question)
    rows = [h for h in snap["human"] if not provider or provider.casefold() in
            str(h["carrier"]).casefold().replace(" ", "")] or snap["human"]
    lines = ["**Fact** — Human Actions recorded:"]
    for h in rows[:5]:
        wait = "{0:.0f}s".format(h["mean_wait_s"]) if h["mean_wait_s"] is not None else "unknown"
        if h["mean_wait_s"] and h["mean_wait_s"] >= 60:
            wait = "{0}m {1:02d}s".format(int(h["mean_wait_s"] // 60), int(h["mean_wait_s"] % 60))
        lines.append("• {0}: {1} task(s) in {2} run(s); average wait {3}; completed and "
                     "verified {4}/{1}; timed out {5}; sessions lost {6}".format(
                         h["carrier"], h["tasks"], len(h["runs"]), wait, h["completed_verified"],
                         h["timed_out"], h["sessions_lost"]))
    lines.append("Completion counts only tasks whose shipment then ended with a verified Hub "
                 "write. ATLAS never learns to perform the verification itself.")
    return "\n".join(lines), {}


def _why_recovery_failed(snap, question, data, context, record, evidence_id):
    episodes = list(data.state.get("recovery_history") or [])
    if record is not None:
        episodes = [e for e in episodes if e.get("reference") == record.get("reference")]
    failed = [e for e in episodes if e.get("status") == "EXHAUSTED"]
    rows = [r for r in events.all_events(("recovery",)) if r.get("status") == "EXHAUSTED"]
    if record is not None:
        rows = [r for r in rows if r.get("reference") == record.get("reference")]
    if failed:
        e = failed[0]
        lines = ["**Fact** — the recovery for {0} ({1}) tried:".format(e.get("reference"),
                                                                     e.get("error_class"))]
        for a in e.get("attempts") or []:
            lines.append("• {0}: {1}{2}".format(a.get("action"), a.get("result"),
                                                ", verification failed" if a.get("verified") is False
                                                else ""))
        lines.append("None produced a verified result, so the run stopped safely: {0}.".format(
            e.get("reason") or "no reason recorded"))
    elif rows:
        r = rows[-1]
        lines = ["**Fact** — the last exhausted recovery on record ({0}, run {1}, {2}) tried: {3}."
                 .format(r.get("reference"), r.get("run_id"), r.get("error_class"),
                         ", ".join("{0} → {1}".format(a.get("action"), a.get("result"))
                                   for a in r.get("attempts") or []) or "nothing")]
    else:
        return "**Fact** — no failed recovery is on record{0}.".format(
            " for " + record.get("reference") if record else ""), {}
    issue, _ = _pick_issue(snap, question, record)
    if issue and issue.get("best"):
        lines.append("**Recommendation** — {0} has the best verified record for this issue "
                     "({1}).".format(issue["best"], _strategy_line(issue["strategies"][issue["best"]])))
    return "\n".join(lines), {}


def _learn_failure(snap, question, data, context, record, evidence_id):
    target = record or (data.failed[0] if data.failed else None)
    if target is None:
        return "**Fact** — no failed shipment to learn from in this run.", {}
    found = learning.find_issue(snap, provider=target.get("provider"), name=target.get("outcome")) \
        if target.get("outcome") else []
    lines = ["**Fact** — {0} on {1} ended {2}{3}.".format(
        target.get("reference"), target.get("carrier"), target.get("state"),
        " ({0})".format(target.get("outcome")) if target.get("outcome") else "")]
    if found:
        lines += _issue_block(found[0])
    else:
        lines.append("It is the first of its kind on record; one occurrence is not yet a pattern.")
    return "\n".join(lines), {"reference": target.get("reference")}


# -- evidence -------------------------------------------------------------

def _evidence(snap, question, data, context, record, evidence_id):
    provider = provider_in(question)
    failures = bool(re.search(r"fail|error|exception|broke", question, re.I))
    ref = record.get("reference") if record is not None else None
    for candidate in data.references_in(question):
        found = data.find(candidate)
        ref = found.get("reference") if found else candidate
    run = None
    if re.search(r"\bthis run\b|\bcurrent run\b", question, re.I):
        run = data.run.get("run_id")
    hits = evidence.search(reference=ref, run_id=run, provider=provider, failures_only=failures,
                           source="browser_capture", limit=3)
    if not hits and ref:
        hits = evidence.search(provider=provider, failures_only=failures,
                               source="browser_capture", limit=3)
        hits = [h for h in hits if h.get("reference") == ref]
    if not hits:
        return ("**Fact** — no screenshot was captured{0}. I only show real captures from the "
                "automation's browser or images you uploaded; I won't make one up.".format(
                    " for " + (ref or provider) if (ref or provider) else " for that")), {}
    first = hits[0]
    lines = ["Here is the real capture: {0}".format(evidence.describe(first))]
    if len(hits) > 1:
        lines.append("Also on record: " + "; ".join(evidence.describe(h) for h in hits[1:3]))
    return "\n".join(lines), {"evidence": [evidence.public(h) for h in hits[:3]],
                              "reference": first.get("reference")}


def _reading_text(entry, reading):
    lines = ["Source: {0}".format(evidence.describe(entry)),
             "Read with: {0}.".format(reading.get("engine") or "nothing — no reader available")]
    if reading.get("verification_screen") or reading.get("message") and not reading.get("facts"):
        lines.append(reading.get("message") or "")
        return lines
    if reading["facts"]:
        lines.append("**Visual fact** — on the image:")
        for f in reading["facts"][:12]:
            lines.append("• {0}: {1} (confidence {2}; seen in “{3}”)".format(
                f["field"], f["value"], f["confidence"], f["evidence"][:90]))
    if reading["unclear"]:
        lines.append("I can't reliably read: " + "; ".join(
            "{0} “{1}”".format(f["field"], f["value"]) for f in reading["unclear"][:5]) + ".")
    if reading["inferences"]:
        lines.append("**Inference** — drawn only from what is visible:")
        lines += ["• {0} (because {1})".format(i["text"], i["because"]) for i in reading["inferences"]]
    return lines


def read_entry(entry):
    path = evidence.file_for(entry["id"])[1]
    if path is None:
        return None
    return vision.read_image(path, evidence.page_text(entry))


def _context_image(context, evidence_id):
    eid = evidence_id or context.get("evidence_id")
    if eid:
        return evidence.find(eid)
    uploads = evidence.search(source="user_upload", limit=1)
    return uploads[0] if uploads else None


def _image_read(snap, question, data, context, record, evidence_id):
    entry = _context_image(context, evidence_id)
    if entry is None:
        return ("**Fact** — I don't have an image from you in this conversation. Attach one "
                "with the image button and ask again."), {}
    reading = read_entry(entry)
    if reading is None:
        return "**Fact** — that image is no longer available, or it changed since it was stored.", {}
    lines = _reading_text(entry, reading)
    wants = re.search(r"shipment number|reference|awb|bol|bill of lading|tracking number",
                      question, re.I)
    if wants and not reading.get("verification_screen"):
        refs = [f for f in reading["facts"] if f["field"] == "reference"]
        if refs:
            lines.insert(0, "Shipment reference(s) on the image: " + ", ".join(
                "{0} ({1})".format(f["value"], f["confidence"]) for f in refs) + ".")
        else:
            lines.insert(0, "I can't reliably read a shipment reference from this image.")
        known = [f["value"] for f in refs if data.find(f["value"])]
        if known:
            lines.append("**Fact** — {0} is in this run.".format(", ".join(known)))
    for f in reading.get("unclear") or []:
        if f.get("kind") == "ambiguous" and f.get("digits"):
            match = next((r for r in data.shipments
                          if re.sub(r"\D", "", r.get("reference") or "") == f["digits"]), None)
            if match is not None:
                lines.append("**Inference** — “{0}” may be {1}, a shipment in this run with the "
                             "same digits; the first character is not clear on the image.".format(
                                 f["value"], match.get("reference")))
    return "\n".join(lines), {"evidence": [evidence.public(entry)], "reading": reading,
                              "evidence_id": entry["id"]}


def _image_compare(snap, question, data, context, record, evidence_id):
    entry = _context_image(context, evidence_id)
    if entry is None:
        return "**Fact** — I don't have an image from you to compare. Attach one first.", {}
    previous = [e for e in evidence.search(source="browser_capture", failures_only=True, limit=10)
                if e["id"] != entry["id"]]
    if not previous:
        return ("**Fact** — there is no captured failure screenshot on record to compare "
                "with."), {"evidence": [evidence.public(entry)]}
    other = previous[0]
    a, b = read_entry(entry), read_entry(other)
    if a is None or b is None:
        return "**Fact** — one of the images could not be read.", {}
    if a.get("verification_screen") or b.get("verification_screen"):
        return (a.get("message") if a.get("verification_screen") else b.get("message")), {}
    diff = vision.compare(a, b)
    lines = ["Comparing your image with {0}".format(evidence.describe(other)),
             "**Visual fact** — the same on both: " + ("; ".join(
                 "{0} {1}".format(f, ", ".join(v)) for f, v in diff["same"]) or "nothing readable"),
             "**Visual fact** — different: " + ("; ".join(
                 "{0}: {1} vs {2}".format(f, ", ".join(x) or "—", ", ".join(y) or "—")
                 for f, x, y in diff["different"][:6]) or "nothing readable")]
    return "\n".join(lines), {"evidence": [evidence.public(entry), evidence.public(other)]}
