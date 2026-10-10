"""
How ATLAS talks in the chat: a normal answer to a normal question.

The rules in assistant.py build a full, sectioned answer for every question
(a shipment card, a diagnosis, every counter). That remains the source of
truth, and it is still what a person gets when they ask for it ("details",
"full report", "explain", "diagnosis"...). Everything else is answered here,
in a sentence or two, from the same run records:

    "Where is 1570046231?"      1570046231 (QATAR AIRWAYS) arrived on 20/08/2026 ...
    "How many failed?"          1 shipment failed: 8842001173.
    "How many potatoes?"        I've harvested 4 potatoes so far 🥔

Presentation only. Nothing here reads anything the rules did not, changes
the run, or invents a value: a missing value is said to be missing, and when
the records cannot support a short answer the full one is returned unchanged.
"""

import re

# Asking for more than the short answer.
DETAIL = re.compile(
    r"\b(details?|detailed|in detail|full (?:report|details?|picture|story|answer)|everything|"
    r"explain|elaborate|tell me more|more info|diagnos\w*|root cause|investigat\w*|"
    r"step by step|trace|show (?:me )?(?:the )?card|all (?:the )?info)\b", re.I)

SHIPMENT_INTENTS = ("shipment", "eta")
# The everyday shipment question; anything more specific (origin, route,
# vessel, history...) keeps the full answer, which says what is known.
WHERE = re.compile(
    r"(?i:^\s*(?:where(?:'s| is)?|what(?:'s| is) the status|status(?: of)?|how(?:'s| is)|"
    r"any (?:news|update)|update on|what about|track|check)\b)|"
    # or just a reference on its own: digits and capitals, no words
    r"^\s*(?=[A-Z0-9 -]*\d)[A-Z0-9][A-Z0-9 -]{5,}\??\s*$")
FAILURE_INTENTS = ("failure_why", "failure_what", "latest_failure", "why")
RUN_INTENTS = ("summary", "run")

WORK_SENTENCE = {
    "RETRY_THIS_RUN": "It's queued for one retry later in this run.",
    "NEXT_RUN": "The next run will look it up again.",
    "NEEDS_PERSON": "It needs a person: it's in the Human Action queue.",
    "NEEDS_DECISION": "It needs a decision from the owner before it can be retried.",
}


def notices_of(data):
    try:
        from dashboard.assistant import notices
        return notices(data)
    except Exception:
        return []


def wants_detail(question):
    return bool(DETAIL.search(question or ""))


def _sentence(text):
    text = " ".join(str(text or "").split()).rstrip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _refs(records, limit=5):
    refs = [r.get("reference") for r in records if r.get("reference")]
    shown = ", ".join(refs[:limit])
    return shown + (" and {0} more".format(len(refs) - limit) if len(refs) > limit else "")


def _plural(n, word):
    return "{0} {1}{2}".format(n, word, "" if n == 1 else "s")


def _verified(record):
    values = list((record.get("verification") or {}).values())
    return bool(values) and all(v is True for v in values)


# -- shipments ------------------------------------------------------------------

def _carrier_line(rec):
    ref, carrier = rec.get("reference"), rec.get("carrier")
    who = "{0} ({1})".format(ref, carrier) if carrier else str(ref)
    eta, ata, status = rec.get("provider_eta"), rec.get("provider_ata"), rec.get("provider_status")
    if ata:
        return "{0} arrived on {1}{2}.".format(who, ata, ", carrier ETA {0}".format(eta) if eta else "")
    if eta:
        return "{0} is due on {1}{2}.".format(
            who, eta, " (the carrier shows “{0}”)".format(status) if status else "")
    if status:
        return "{0}: the carrier shows “{1}”.".format(who, status)
    return None


def _state_line(rec):
    state, error = rec.get("state"), rec.get("error")
    if state == "updated":
        return "I wrote it to the Hub{0}.".format(" and read it back" if _verified(rec) else "")
    if state == "partial":
        return "Only part of it reached the Hub: " + _sentence(error or "the second date failed")
    if state == "skipped":
        return "Nothing was written: " + _sentence(error or "it was skipped")
    if state == "failed":
        reason = _sentence(error or "no reason recorded")
        line = ("It failed: " + reason) if "written" in reason.lower() else \
            ("It failed, so nothing was written to the Hub: " + reason)
        work = WORK_SENTENCE.get((rec.get("work") or {}).get("mode"))
        return line + (" " + work if work else "")
    if state == "processing":
        return "I'm still working on it."
    if state == "waiting_for_human":
        return "It's waiting for a person in the Human Action queue."
    if state == "human_timeout":
        return "Nobody completed its human step in time, so nothing was written."
    return None


def shipment(rec):
    first = _carrier_line(rec)
    second = _state_line(rec)
    if not first:
        who = "{0} ({1})".format(rec.get("reference"), rec.get("carrier")) \
            if rec.get("carrier") else str(rec.get("reference"))
        if rec.get("state") == "skipped":
            return "{0} was skipped, so nothing was written to the Hub: {1}".format(
                who, _sentence(rec.get("error") or "no reason was recorded"))
        if rec.get("state") == "failed":
            return failure(rec)
        if not second:
            return None
        return "{0}: {1}".format(who, second)
    return first + (" " + second if second else "")


def eta(rec, question):
    ref = rec.get("reference")
    asks_ata = bool(re.search(r"\bata\b|arriv", question or "", re.I)) and \
        not re.search(r"\beta\b", question or "", re.I)
    value, other = (rec.get("provider_ata"), rec.get("provider_eta")) if asks_ata else \
        (rec.get("provider_eta"), rec.get("provider_ata"))
    word = "ATA" if asks_ata else "ETA"
    if value:
        extra = ""
        if other and not asks_ata:
            extra = " It arrived on {0}.".format(other)
        return "{0}'s carrier {1} is {2}.{3}".format(ref, word, value, extra)
    why = rec.get("error") if rec.get("state") in ("skipped", "failed") else None
    return "The carrier didn't give an {0} for {1}{2}".format(
        word, ref, ": " + _sentence(why) if why else ".")


def failure(rec):
    ref, carrier, state = rec.get("reference"), rec.get("carrier"), rec.get("state")
    who = "{0} ({1})".format(ref, carrier) if carrier else str(ref)
    error = _sentence(rec.get("error") or "no reason was recorded")
    if state == "failed":
        work = WORK_SENTENCE.get((rec.get("work") or {}).get("mode"))
        nothing = "" if "written" in error.lower() else " Nothing was written to the Hub."
        cls = rec.get("outcome")
        return "{0} failed{1}: {2}{3}{4}".format(
            who, " ({0})".format(cls) if cls and cls.lower() not in error.lower() else "",
            error, nothing, " " + work if work else "")
    if state == "skipped":
        return "{0} was skipped: {1}".format(who, error)
    if state == "partial":
        return "{0} was only partly updated: {1}".format(who, error)
    if state == "human_timeout":
        return "{0} waited for a person who didn't complete the step in time, so nothing " \
               "was written.".format(who)
    return None


# -- the run ----------------------------------------------------------------------

def counts(data, question):
    q = (question or "").lower()
    if re.search(r"total|in all|altogether|left|remaining|to go|still", q):
        return None                    # the full answer says whether the total is known
    failed, skipped, partial = data.failed, data.skipped, data.partial
    updated = data.updated
    processed = (data.counters or {}).get("processed")
    if processed is None:
        processed = len(updated) + len(failed) + len(skipped) + len(partial)
    if not processed:
        return "No shipments have been processed yet."
    if re.search(r"fail", q):
        return "{0} failed{1}".format(_plural(len(failed), "shipment"),
                                      ": " + _refs(failed) + "." if failed else ".") \
            if failed else "No shipments failed."
    if re.search(r"skip", q):
        return "{0} {1} skipped{2}".format(_plural(len(skipped), "shipment"),
                                           "was" if len(skipped) == 1 else "were",
                                           ": " + _refs(skipped) + "." if skipped else ".") \
            if skipped else "No shipments were skipped."
    if re.search(r"updat|written|writ|complet|success(?!\s*rate)|done", q):
        n = len(updated) + len(partial)
        return "{0} written to the Hub{1}.".format(
            _plural(n, "shipment") + (" was" if n == 1 else " were"),
            " ({0} only partly)".format(len(partial)) if partial else "")
    if re.search(r"rate|percent|%", q):
        rate = (data.counters or {}).get("success_rate")
        return "The success rate is {0}%.".format(rate) if rate is not None else \
            "There's no success rate yet: nothing has finished."
    bits = ["{0} written to the Hub".format(len(updated) + len(partial))]
    if skipped:
        bits.append("{0} skipped".format(len(skipped)))
    bits.append("{0} failed".format(len(failed)))
    return "{0} processed: {1}.".format(_plural(processed, "shipment"), ", ".join(bits))


def run_summary(data):
    run = data.run or {}
    status = run.get("status")
    if not data.shipments and status in (None, "idle"):
        return None
    written = len(data.updated) + len(data.partial)
    failed, skipped = data.failed, data.skipped
    name = " ({0})".format(run["run_id"]) if run.get("run_id") else ""
    verb = {"running": "is still going", "finished": "finished", "fatal": "stopped with an error",
            "stopped": "was stopped"}.get(status, "is {0}".format(status or "idle"))
    parts = ["{0} written to the Hub".format(written)]
    if skipped:
        parts.append("{0} skipped".format(len(skipped)))
    parts.append("{0} failed".format(len(failed)))
    text = "The {0} run{1} {2}: {3} processed{4}, {5}.".format(
        "current" if status == "running" else "last", name, verb, len(data.shipments),
        " so far" if status == "running" else "", ", ".join(parts))
    if failed:
        queued = [r for r in failed if (r.get("work") or {}).get("mode") == "RETRY_THIS_RUN"]
        text += " The failure{0} {1} {2}{3}.".format(
            "" if len(failed) == 1 else "s", "was" if len(failed) == 1 else "were", _refs(failed),
            ", queued for a retry" if queued and len(queued) == len(failed) else "")
    waiting = [r for r in data.shipments if r.get("state") == "waiting_for_human"]
    if waiting:
        text += " {0} waiting for you in the Human Action queue.".format(
            _plural(len(waiting), "shipment") + (" is" if len(waiting) == 1 else " are"))
    return text


def attention(data, notices):
    """What needs a person, without a header or three lines about one failure."""
    found = [n for n in notices if n.get("level") != "ok"]
    if not found:
        return None                        # the rules' calm sentence is already short
    if any(r.get("state") == "waiting_for_human" for r in data.shipments) or \
            (data.state.get("human_action") or {}).get("waiting") or any(
                str(t.get("status") or "") in ("WAITING_FOR_HUMAN", "OPERATOR_OPENED",
                                               "VERIFICATION_PENDING")
                for t in (data.state.get("human_queue") or [])):
        return None                        # the human-first answer stays as it is
    failed = data.failed

    def about_failures(n):
        action = n.get("action") or {}
        text = n.get("text") or ""
        return (action.get("type") == "filter" and action.get("state") == "failed") or \
            "work list" in text or text.startswith("No safe recovery")

    others = [_sentence(n.get("text")) for n in found if not (failed and about_failures(n))]
    parts = list(others)
    if failed:
        queued = [r for r in failed if (r.get("work") or {}).get("mode") == "RETRY_THIS_RUN"]
        who = ", ".join("{0} ({1})".format(r.get("reference"), r.get("carrier"))
                        if r.get("carrier") else str(r.get("reference")) for r in failed[:5])
        more = " and {0} more".format(len(failed) - 5) if len(failed) > 5 else ""
        parts.append("{0}{1} failed{2}.".format(
            who, more, ", and it's queued for a retry" if len(failed) == 1 and queued else
            "; {0} of them are queued for a retry".format(len(queued)) if queued else ""))
    return " ".join(parts) or None


# -- Potato Mode ------------------------------------------------------------------

def potato(state, question):
    p = (state or {}).get("potato") if isinstance(state, dict) else None
    if not isinstance(p, dict):
        return "No potatoes here yet 🥔"
    q = (question or "").lower()
    if re.search(r"\b(plant|harvest|grow)\b|ازرع|إزرع", q) and not re.search(
            r"how many|how much|\?$", q):
        return "I can't plant one on request 😄 Only real errors plant them, and only a " \
               "verified fix harvests them."
    if re.search(r"\bwhy\b|activat|trigger|turn(ed)? on|potato mode", q):
        ctx = p.get("context") or {}
        if p.get("active") and ctx.get("reference"):
            return "Shipment {0} failed {1} times in a row{2}, so I stopped repeating the same " \
                   "attempts and moved on 🥔".format(
                       ctx["reference"], ctx.get("errors"),
                       " ({0})".format(ctx["problem"]) if ctx.get("problem") else "")
        last = p.get("last_harvest")
        if last:
            return "Potato Mode isn't on right now. The last time was {0}, and it's since been " \
                   "fixed and verified 🥔".format(last.get("reference"))
        return "Potato Mode isn't on: no shipment has failed {0} times in a row.".format(
            p.get("threshold") or 3)
    harvests = int(p.get("harvests") or 0)
    if harvests:
        text = "I've harvested {0} so far 🥔".format(_plural(harvests, "potato").replace(
            "potatos", "potatoes"))
    else:
        text = "No potatoes harvested yet 🥔"
    ctx = p.get("context") or {}
    if p.get("active") and ctx.get("reference"):
        text += " One is growing for {0}.".format(ctx["reference"])
    return text


# -- the one entry point ----------------------------------------------------------

def concise(question, reply, state, data):
    """The reply, as a normal answer. Returns the reply unchanged when the full
    answer was asked for, or when the records cannot support a short one."""
    try:
        if not isinstance(reply, dict) or wants_detail(question) or reply.get("web_sources") \
                or (reply.get("llm") or {}).get("used"):
            return reply                   # asked for, researched, or already phrased
        intent = reply.get("intent")
        rec = data.find(reply.get("reference")) if reply.get("reference") else None
        text = None
        asks_date = bool(re.search(r"\b(eta|ata)\b", question or "", re.I))
        how_many = bool(re.search(r"\bhow many\b|\bnumber of\b|\bcount\b", question or "", re.I))
        if intent == "potato":
            text = potato(state, question)
        elif intent in ("failed", "skipped") and how_many:
            text = counts(data, question)
        elif intent == "attention":
            text = attention(data, notices_of(data))
        elif rec is not None and (intent == "eta" or (not intent and asks_date)):
            text = eta(rec, question)
        elif rec is not None and (intent in SHIPMENT_INTENTS or not intent) and \
                WHERE.search(question or "") and not re.search(
                    r"origin|destination|route|from where|coming from|going to|port|vessel|"
                    r"voyage|flight|container|weight|piece|history|timeline|when did|how long",
                    question or "", re.I):
            text = shipment(rec)
        elif intent in FAILURE_INTENTS and rec is not None:
            # ATLAS's failure analysis reads more than the record's last line
            # (a "skip" after a completed verification is an access problem):
            # its plain-words explanation leads; the recorded reason follows.
            first = (reply.get("answer") or "").split("\n\n")[0].strip()
            if reply.get("failure_id") and first and "**" not in first and len(first) <= 450:
                text = _sentence(first)
                error = str(rec.get("error") or "").strip().rstrip(".")
                if rec.get("state") in ("failed", "skipped", "partial") and error and \
                        error.lower() not in text.lower():
                    text += " The run recorded: " + _sentence(error)
            else:
                text = failure(rec)
            named = re.sub(r"\W", "", str(rec.get("reference") or "")) in re.sub(
                r"\W", "", question or "")
            same = [r for r in data.failed if r.get("carrier") == rec.get("carrier")]
            if text and not named and len(same) > 1:
                # A question about a carrier, not one shipment: say how many first.
                text = "{0} {1} shipments failed. The latest, {2}".format(
                    len(same), rec.get("carrier"), text[0].lower() + text[1:]
                    if not text[:1].isdigit() else text)
        elif intent == "counts":
            text = counts(data, question)
        elif intent in RUN_INTENTS:
            text = run_summary(data)
        if not text:
            return reply
        out = dict(reply)
        out["answer"] = text
        out["card"] = None                 # the dashboard has the full card
        out.pop("investigation", None)
        out.pop("details", None)
        if intent in ("counts", "failed", "skipped", "attention") + RUN_INTENTS:
            out["downloads"] = []
        out["brief"] = True
        more = None
        if rec is not None and intent in FAILURE_INTENTS:
            more = "Explain why {0} failed".format(rec.get("reference"))
        elif rec is not None and (intent in SHIPMENT_INTENTS or not intent):
            more = "Tell me everything about {0}".format(rec.get("reference"))
        elif intent in ("counts",) + RUN_INTENTS:
            more = "Full run report"
        if more:
            chips = [c for c in (out.get("suggestions") or []) if c != more]
            out["suggestions"] = [more] + chips[:3]
        return out
    except Exception:
        return reply
