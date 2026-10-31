"""
The model council: several models, from different families and providers (the tower's Ollama models
and Snowflake Cortex), working on the same step together.

    members(need, profile, k)        up to k healthy models for the step, distinct families first
    hypotheses(facts, known, ...)    each member suggests causes in parallel; similar suggestions merge,
                                     each says which models proposed it; all stay "open - not tested"
    review(facts, draft, drafter...) reviewers from families other than the drafter's; every concern kept
                                     with who raised it; the verdict is the majority, a split said plainly
    word(system, prompt, inputs...)  members draft in parallel; each draft is fact-checked (factguard);
                                     the best-ranked draft that passes is used, the rejected ones recorded

How many members: the mission's profile (drill 0, normal 1, deep 2, emergency 3), or ATLAS_COUNCIL_SIZE.
Every call goes through models.call (ledger, budget, slots: one local model at a time on the tower).
A model never supplies a number, a date or a status: those come from the records, and what a model
says is checked against them. More models agreeing raises attention, never a grade: grades come from
the evidence (specialists.verify).
"""
import json
import os
import re
import threading

from . import db, models

SIZE = {"drill": 0, "normal": 1, "deep": 2, "emergency": 3}


def size(profile):
    raw = (os.environ.get("ATLAS_COUNCIL_SIZE") or "").strip()
    if raw.isdigit():
        return max(0, min(int(raw), 5))
    return SIZE.get(profile, 1)


def members(need, profile, k, tier="T1", used=0, budget=80000, avoid_family=None):
    """Up to k models for `need`: the router's best, then the best of each other family, then any other."""
    chosen, families, exclude = [], set(), set()
    for strict in (True, False):
        while len(chosen) < k:
            m, _ = models.route(need, tier, profile, used, budget, avoid_family, exclude=exclude)
            if m is None:
                break
            exclude.add(m["id"])
            if strict and models.family(m) in families:
                continue
            chosen.append(m)
            families.add(models.family(m))
        exclude = {m["id"] for m in chosen}
    return chosen


# Text in the records or on a web page that talks to an AI ("ignore previous instructions", "SYSTEM:",
# "approve all actions", an email address, "set FLAG=1") is data, never an instruction: it is cut out of
# what a model is shown. The records themselves are unchanged and still shown to people as they are.
INSTRUCTION = re.compile(r"(ignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules|prompts?)"
                         r"|\b(?:system|assistant|developer)\s*:|\b(?:ai|llm)\s+(?:agents?|models?|assistants?)\s+reading\s+this"
                         r"|\bapprove\s+(?:all|every)\b|\bset\s+[A-Z][A-Z0-9_]{3,}\s*=|\brm\s+-rf\b|\bcredentials?\b"
                         r"|[\w.+-]+@[\w-]+\.[\w.]+)", re.I)
REMOVED = "[instruction-like text removed]"
# A "cause" that is an order, not a cause: starts with a command, or is a shell command
COMMAND = re.compile(r"^\s*(?:please\s+)?(?:run|execute|delete|send|email|escalate|approve|ignore|disable|enable|set|call|wire|pay)\b|\brm\s+-rf\b", re.I)


def _guard(mission_id, text):
    """A blocked attempt, on the mission's timeline (kind "guard") so people can see what was tried."""
    if mission_id:
        try:
            db.event(mission_id, "guard", text)
        except Exception:
            pass


def defang(lines):
    """-> (lines with instruction-like text cut from its first sign to the end of that line, how many were cut)."""
    out, n = [], 0
    for line in lines:
        m = INSTRUCTION.search(str(line))
        if m:
            n += 1
            line = str(line)[:m.start()].rstrip(" ;:,-") + " " + REMOVED
        out.append(line)
    return out, n


def _parallel(group, fn):
    """Run fn(member) for each member at once (the providers' slots keep the tower's model to one call)."""
    out = [None] * len(group)

    def run(i, m):
        try:
            out[i] = {"model": m["id"], "family": models.family(m), "ok": True, "value": fn(m)}
        except Exception as error:
            out[i] = {"model": m["id"], "family": models.family(m), "ok": False, "error": type(error).__name__}
    threads = [threading.Thread(target=run, args=(i, m), daemon=True) for i, m in enumerate(group)]
    [t.start() for t in threads]
    [t.join(90) for t in threads]
    return [o for o in out if o]


def _json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def _words(text):
    return {w for w in re.findall(r"[a-z]{3,}", str(text).lower()) if w not in ("the", "and", "for", "with", "not", "was")}


# Models word the same cause differently ("missing paperwork", "documentation discrepancy"): a cause is
# grouped by its theme first, by shared words otherwise. Themes are logistics vocabulary, not a model's.
THEMES = [
    ("documents", r"document|paperwork|invoice|packing list|certificate|\bdocs?\b|bill of lading|airway bill"),
    ("agent capacity", r"capacity|backlog|overload|workload|understaff|resourc|bandwidth"),
    ("duty and tax", r"\bduty|duties|\btax|tariff|valuation|assessment|payment"),
    ("communication", r"communicat|coordinat|hand-?off|follow-?up|respons"),
    ("systems", r"system|portal|\bit\b|software|integration|outage|sync"),
    ("inspection", r"inspect|examination|physical check|hold\b|held\b"),
    ("congestion", r"congest|port|terminal|airport|warehouse|space"),
    ("carrier", r"carrier|airline|shipping line|vessel|flight|transshipment"),
    ("regulatory", r"regulat|permit|licen[cs]e|compliance|restricted|approval"),
]


def theme(text):
    t = str(text).lower()
    return next((name for name, rx in THEMES if re.search(rx, t)), None)


def _same(a, b):
    ta, tb = theme(a), theme(b)
    if ta and tb:
        return ta == tb
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / float(len(wa | wb)) >= 0.5


def hypotheses(facts, known, mission_id, task_id, profile, budget):
    """-> {"causes": [{"label", "proposed_by": [model...]}], "members": [...], "failed": [...]}"""
    k = size(profile)
    group = members("reason", profile, k, budget=budget, used=models.tokens_used(mission_id))
    if not group:
        return {"causes": [], "members": [], "failed": []}
    system = ("You help a logistics control tower investigate. List other plausible causes worth testing, "
              "as JSON: {\"causes\": [\"short cause\", ...]} (at most 3). Do not repeat these: " + "; ".join(known))
    facts, cut = defang(facts)
    known, _ = defang(known)
    if cut:
        _guard(mission_id, "{0} line(s) of instruction-like text cut from the records before any model saw them".format(cut))
    prompt = "FACTS:\n- " + "\n- ".join(facts)
    res = _parallel(group, lambda m: _json(models.call("reason", system, prompt, mission_id=mission_id, task_id=task_id,
                                                       purpose="hypotheses (council)", profile=profile, budget=budget,
                                                       max_tokens=200, json_mode=True, only=m, max_calls=len(group) + 1)["text"]))
    merged = []
    for r in res:
        if not r["ok"]:
            continue
        extra = sorted(k_ for k_ in r["value"] if k_ != "causes")
        if extra:
            _guard(mission_id, "{0} tried to add {1}: ignored, a model cannot act or change settings".format(
                r["model"], ", ".join("'{0}'".format(x) for x in extra)))
        for c in (r["value"].get("causes") or [])[:3]:
            c = " ".join(str(c).split())[:120]
            if INSTRUCTION.search(c) or COMMAND.search(c):
                _guard(mission_id, "{0} suggested a 'cause' that is an instruction (\"{1}\"): dropped".format(r["model"], c[:70]))
                continue
            if not c or any(_words(c) and _words(c) == _words(k_) for k_ in known):
                continue
            hit = next((x for x in merged if _same(x["label"], c)), None)
            if hit:
                if r["model"] not in hit["proposed_by"]:
                    hit["proposed_by"].append(r["model"])
                    hit["wordings"].append({"model": r["model"], "text": c})
            else:
                merged.append({"label": c, "theme": theme(c), "proposed_by": [r["model"]], "wordings": [{"model": r["model"], "text": c}]})
    merged.sort(key=lambda x: -len(x["proposed_by"]))
    return {"causes": merged[:5], "members": [r["model"] for r in res if r["ok"]], "failed": [r["model"] for r in res if not r["ok"]],
            "removed": cut}


def review(facts, draft, drafter_family, mission_id, task_id, profile, budget):
    """-> {"agree": True | False | None, "split": bool, "reviewers": [{model, agree, concerns}], "failed": [...]}"""
    k = max(1, size(profile)) if size(profile) else 0
    group = members("review", profile, k, budget=budget, used=models.tokens_used(mission_id), avoid_family=drafter_family)
    if not group:
        return {"agree": None, "split": False, "reviewers": [], "failed": []}
    system = ("You are an independent reviewer. Check whether the DRAFT follows from the FACTS. "
              "Reply JSON only: {\"agree\": true|false, \"concerns\": [\"...\"]}. Do not add facts.")
    facts, cut = defang(facts)
    if cut:
        _guard(mission_id, "{0} line(s) of instruction-like text cut before the reviewers saw the facts".format(cut))
    prompt = "FACTS:\n- " + "\n- ".join(facts) + "\n\nDRAFT:\n" + draft
    res = _parallel(group, lambda m: _json(models.call("review", system, prompt, mission_id=mission_id, task_id=task_id,
                                                       purpose="independent review (council)", profile=profile, budget=budget,
                                                       max_tokens=250, json_mode=True, only=m, max_calls=len(group) + 1)["text"]))
    for r in res:
        if r["ok"]:
            extra = sorted(k_ for k_ in r["value"] if k_ not in ("agree", "concerns"))
            if extra:
                _guard(mission_id, "{0} (reviewer) tried {1}: ignored, a reviewer only agrees or raises concerns".format(
                    r["model"], ", ".join("'{0}'".format(x) for x in extra)))
    reviewers = [{"model": r["model"], "family": r["family"], "agree": bool(r["value"].get("agree")),
                  "concerns": [str(c)[:200] for c in (r["value"].get("concerns") or [])][:4]} for r in res if r["ok"]]
    if not reviewers:
        return {"agree": None, "split": False, "reviewers": [], "failed": [r["model"] for r in res]}
    yes = sum(1 for r in reviewers if r["agree"])
    return {"agree": yes * 2 > len(reviewers), "split": 0 < yes < len(reviewers), "reviewers": reviewers,
            "failed": [r["model"] for r in res if not r["ok"]], "removed": cut}


MEASURE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(%|percent|days?|hours?|weeks?|shipments?|awbs?|bols?|escalations?)\b", re.I)


def _measures(text, inputs):
    """Stricter than the chat's fact check for mission reports: a small number with a unit ("99 days",
    "12 shipments", "40%") must be in the evidence too. -> the violations."""
    source = " ".join(str(x) for x in inputs if x)
    have = {n.replace(",", "") for n in re.findall(r"\d+(?:[.,]\d+)?", source)}
    have |= {n.split(".")[0] for n in have}
    return ["number {0} {1}".format(n, u) for n, u in MEASURE.findall(text or "")
            if n.replace(",", "") not in have and n.replace(",", "").split(".")[0] not in have]


# A mission report states what the records show; it never says something was done. Action words must
# be in the evidence (e.g. a recorded escalation), otherwise the draft is refused.
ACTED = re.compile(r"\b(escalated|e-?mailed|messaged|notified|contacted|re-?ran|reprocessed|executed|deleted|paid|booked|"
                   r"cancell?ed|submitted|instructed|wrote to|called)\b", re.I)
MAX_CHARS = 1200


def checks(text, inputs):
    """The council's own checks on a draft, after the chat's fact check: length, numbers with units, action claims."""
    bad = []
    if len(text or "") > MAX_CHARS:
        bad.append("{0:,} characters (a summary is at most {1:,})".format(len(text), MAX_CHARS))
    bad += _measures(text, inputs)
    source = " ".join(str(x) for x in inputs if x).lower()
    bad += ["claims it '{0}'".format(w) for w in sorted({m.group(0).lower() for m in ACTED.finditer(text or "")}) if w not in source]
    if INSTRUCTION.search(text or ""):
        bad.append("instruction-like text")
    return bad


def word(system, prompt, inputs, mission_id, task_id, profile, budget, max_tokens=220):
    """-> {"text" | None, "model", "rejected": [{model, why}], "members": [...]}: the best draft that passes the fact check."""
    from .. import factguard
    k = size(profile)
    group = members("word", profile, k, budget=budget, used=models.tokens_used(mission_id))
    if not group:
        return {"text": None, "model": None, "rejected": [], "members": []}
    lines, cut = defang(prompt.split("\n"))
    prompt = "\n".join(lines)
    res = _parallel(group, lambda m: models.call("word", system, prompt, mission_id=mission_id, task_id=task_id,
                                                 purpose="report wording (council)", profile=profile, budget=budget,
                                                 max_tokens=max_tokens, only=m, max_calls=len(group) + 1)["text"])
    rejected = []
    for r in res:
        if r["ok"]:                                 # "Here is the executive summary:" is not part of it
            r["value"] = re.sub(r"^\s*(?:here is|here's|below is)[^\n:]{0,60}:\s*", "", r["value"] or "", flags=re.I)
    for r in res:                                   # in the router's order: the best-ranked passing draft wins
        if not r["ok"]:
            rejected.append({"model": r["model"], "why": "no answer ({0})".format(r["error"])})
            continue
        ok, bad = factguard.check(r["value"], inputs)
        bad = list(bad) + checks(r["value"], inputs)
        ok = ok and not bad
        if ok:
            return {"text": r["value"].strip(), "model": r["model"], "rejected": rejected, "members": [x["model"] for x in res]}
        rejected.append({"model": r["model"], "why": "refused by the fact check ({0})".format(", ".join(str(b)[:80] for b in bad[:3]))})
        _guard(mission_id, "{0}'s report draft refused: {1}".format(r["model"], ", ".join(str(b)[:60] for b in bad[:3])))
    return {"text": None, "model": None, "rejected": rejected, "members": [x["model"] for x in res]}
