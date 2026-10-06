"""
ATLAS as a colleague: shipment intelligence, error investigation, general
questions, and research when the run is not enough.

assistant.answer() builds ATLAS's grounded reply from the run first. This
module then looks at the question and that reply and decides:

  general      a definition ("what is transshipment?") — from the glossary
  shipment     where is it / why is it late / the vessel / the port /
               everything about it — the run's picture, then research
  error        why did it fail — the run's evidence, the causes ranked
               against it (intelligence/knowledge), then research
  mixed        the run and the outside world in one question
  search       the operator asked to look something up
  fallback     the run has nothing on it — research, or say so plainly

Research (intelligence/research) runs only when it is configured and only
when the question needs it. Run facts stay authoritative: the research is
given them and told so, and the run's own grounded reply is kept with the
answer (as its details) whatever the research says.
"""

import re

from intelligence import knowledge as K
from intelligence import research as R

SEARCH_ASK = re.compile(r"\b(search|look (it |this |that )?up|google|on the (web|internet)|online|"
                        r"find out|research|check (online|the web|the news)|any news|"
                        r"latest (news|update|information|info))\b", re.I)
FRESH_ASK = re.compile(r"\b(latest|right now|currently|today|now|refresh|again|up to date|"
                       r"most recent)\b", re.I)
SHIPMENT_ASK = re.compile(
    r"\b(where is|where's|whereabouts|vessel|ship\b|voyage|imo|port|terminal|berth|congestion|"
    r"delay|delayed|late|behind schedule|latest event|last event|tracking event|route|"
    r"transship|everything|all you (know|can)|tell me (about|more)|what('?s| is) (going on|"
    r"happening)|anything unusual|investigate|find more|more information|check the|"
    r"what happened to)\b", re.I)
ERROR_ASK = re.compile(r"\b(why|cause|caused|root cause|reason|what went wrong|fix|investigate|"
                       r"how (do|can) (i|we) (fix|solve|resolve)|what does (this|that|the) "
                       r"error mean)\b", re.I)
EXTERNAL_HINT = re.compile(r"\b(today|this week|having issues|outage|down|news|announce|"
                           r"advisory|strike|weather|congestion|public)\b", re.I)


def _brief_record(record):
    if not record:
        return None
    keep = ("reference", "carrier", "provider", "mode", "state", "outcome", "internal_eta",
            "provider_eta", "provider_ata", "provider_status", "provider_eta_source",
            "provider_ata_source", "flight", "verification", "coe_action", "bu_action", "message",
            "started_at", "updated", "table_page")
    out = {k: record.get(k) for k in keep if record.get(k) not in (None, "", [], {})}
    access = record.get("carrier_access") or {}
    if access:
        out["carrier_access"] = {k: access.get(k) for k in ("state", "detail", "facts")}
    return out


def _brief_failure(f):
    if not f:
        return None
    return {"category": f.get("classification"), "label": f.get("classification_label"),
            "stage": f.get("stage_label"), "error_message": (f.get("error_message") or "")[:500],
            "observed": f.get("observed_state"), "last_successful_step":
            f.get("last_successful_event"), "recovery_attempts": len(f.get("recovery_attempts")
                                                                    or []),
            "root_cause_status": f.get("root_cause_status")}


def brief(data, record=None, failure=None, run_reply=None, ranking=None):
    run = data.state.get("run") or {}
    out = {"run_id": run.get("run_id"), "run_status": run.get("status"),
           "counters": data.counters, "shipment": _brief_record(record),
           "failure": _brief_failure(failure)}
    if ranking:
        out["ranked_causes_from_run_evidence"] = [
            {"status": c["status"], "cause": c["cause"], "why": c["why"]}
            for c in ranking["causes"][:5]]
    if run_reply:
        out["what_atlas_answered_from_the_run"] = re.sub(r"\*\*", "", run_reply)[:1800]
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def classify(question, reply, data, record, failure):
    q = question or ""
    if K.glossary(q) and not record and not SEARCH_ASK.search(q):
        return "general"
    if failure is not None and ERROR_ASK.search(q) and (record is None or
                                                        record.get("state") != "updated"):
        return "mixed" if EXTERNAL_HINT.search(q) else "error"
    if record is not None and SHIPMENT_ASK.search(q):
        return "shipment"
    if SEARCH_ASK.search(q):
        return "search"
    if reply.get("understood") is False:
        return "fallback"
    return None


def _subject(data, question, reply, context):
    record = data.find(reply.get("reference")) if reply.get("reference") else None
    if record is None:
        for candidate in data.references_in(question or ""):
            record = data.find(candidate)
            if record is not None:
                break
    if record is None and context.get("reference") and re.search(
            r"\b(this|that|it|its|the)\b", question or "", re.I):
        record = data.find(context["reference"])
    return record


def _carrier_line(record):
    bits = []
    if record.get("provider_eta"):
        bits.append("the carrier's ETA was {0}".format(record["provider_eta"]))
    if record.get("provider_ata"):
        bits.append("its ATA was {0}".format(record["provider_ata"]))
    if record.get("provider_status"):
        bits.append("its latest tracking status was “{0}”".format(
            record["provider_status"]))
    return bits


def shipment_picture(record):
    """What the run knows about one shipment, said the way a person says it."""
    ref, carrier = record.get("reference"), record.get("carrier") or "the carrier"
    state = record.get("state")
    parts = ["I checked what we have in the run first. {0} is with {1}.".format(ref, carrier)]
    said = _carrier_line(record)
    if said:
        parts.append("When the run read it, " + ", ".join(said) + ".")
    if record.get("internal_eta"):
        parts.append("The Hub had {0} before the run.".format(record["internal_eta"]))
    verified = record.get("verification") or {}
    if state == "updated":
        ok = [k for k, v in verified.items() if v is True]
        parts.append("The run wrote it to the Hub{0}.".format(
            " and read it back to check ({0})".format(", ".join(ok)) if ok else ""))
    elif state in ("failed", "partial", "skipped", "human_timeout"):
        parts.append("It did not complete in this run ({0}).".format(
            (record.get("outcome") or state).replace("_", " ").lower()))
    elif state in ("processing", "waiting_for_human"):
        parts.append("The run is working on it right now.")
    return " ".join(parts)


def failure_lead(f):
    """The opening a colleague gives: what happened, in plain words."""
    carrier = f.get("carrier") or "the carrier"
    category = f.get("classification")
    observed = f.get("observed_state") or {}
    if category == "CARRIER_ACCESS_RESTRICTED":
        after = "got past the human verification, but " if "after the human verification" in \
            (f.get("headline") or "") else ""
        return ("The run {0}{1} still returned its access-restricted page for {2}. So it failed "
                "before extraction — we never treated that page as shipment data, and nothing "
                "was written to the Hub.".format(after, carrier, f.get("shipment_id")))
    if category == "CARRIER_ACCESS_NOT_CONFIRMED":
        return ("After the human verification, {0}'s shipment page never appeared for {1}, so "
                "nothing was extracted or written.".format(carrier, f.get("shipment_id")))
    if category in ("SECURITY_VERIFICATION_REQUIRED", "HUMAN_ACTION_REQUIRED"):
        return "{0} needs a person for {1} — the run waits for that step rather than " \
               "attempting it.".format(carrier, f.get("shipment_id"))
    read = ", ".join(observed.get("read") or [])
    return "{0} on {1} stopped at {2}: {3}.{4} {5}".format(
        f.get("shipment_id"), carrier, (f.get("stage_label") or "a step").lower(),
        f.get("classification_label"), " The run had read {0}.".format(read) if read else "",
        f.get("impact") or "").strip()


def _not_in_run():
    return ("What the run doesn't record — the vessel, its position, the ports on the route, "
            "port conditions or carrier notices — I'd have to look up")


def enrich(question, state, context, reply, data, run_failures, pick_failure):
    """The reply, made a colleague's answer. Never raises; returns the reply."""
    try:
        return _enrich(question, context, reply, data, run_failures, pick_failure)
    except Exception:
        return reply


def _failure_for(data, record, question, context, run_failures, pick):
    failures = run_failures(data)
    if not failures:
        return None
    return pick(failures, question, record, context)


def _enrich(question, context, reply, data, run_failures, pick_failure):
    if reply.get("conduct") or reply.get("intent") in ("code_request", "greeting", "thanks") \
            or (context.get("domain") == "po"):
        return reply
    record = _subject(data, question, reply, context)
    failure = _failure_for(data, record, question, context, run_failures, pick_failure)
    if failure is not None and record is not None and \
            failure.get("shipment_id") != record.get("reference"):
        failure = None
    if failure is None and record is None and ERROR_ASK.search(question or ""):
        fs = run_failures(data)
        failure = fs[0] if len(fs) == 1 else None
    mode = classify(question, reply, data, record, failure)
    if mode is None:
        return reply
    pid = R.clean_progress_id(context.get("progress_id"))
    run_text = reply.get("answer") or ""

    if mode == "general":
        title, text = K.glossary(question)
        if R.enabled() and FRESH_ASK.search(question):
            return _researched(reply, question, "general", brief(data), pid, None, title)
        out = dict(reply, answer=text, intent="general_knowledge", understood=True,
                   knowledge="general", grounded=True)
        out.pop("fallback", None)
        return out

    ranking = None
    if failure is not None and mode in ("error", "mixed"):
        R.stage(pid, "Reading the failure evidence…")
        ranking = K.investigate_failure(failure, data.shipments)
    elif record is not None:
        R.stage(pid, "Checking the shipment…")

    if R.enabled():
        subject = (record or {}).get("reference") or (failure or {}).get("failure_id")
        research_mode = {"search": "mixed" if record else "general", "fallback": "general"}.get(
            mode, mode)
        return _researched(reply, question, research_mode,
                           brief(data, record, failure, run_text, ranking), pid, subject,
                           None, details=run_text if mode != "fallback" else None,
                           fresh=bool(FRESH_ASK.search(question)))

    # Research is not configured: the run's own picture, honestly bounded.
    off = R.why_off()
    if mode in ("error", "mixed") and failure is not None:
        text = failure_lead(failure) + "\n\n" + run_text + \
            "\n\n**What I think is going on**\n" + K.investigation_text(ranking) + \
            "\n\nI haven't checked outside sources for this: {0}.".format(off)
        return dict(reply, answer=text, investigation=ranking, research={"ok": False,
                                                                         "reason": off})
    if mode == "shipment" and record is not None:
        text = shipment_picture(record) + "\n\n" + run_text + "\n\n" + _not_in_run() + \
            ", and {0}.".format(off)
        return dict(reply, answer=text, research={"ok": False, "reason": off})
    if mode == "search":
        return dict(reply, answer=run_text + ("\n\n" if run_text else "") +
                    "I can't look that up from here: {0}.".format(off),
                    research={"ok": False, "reason": off})
    return reply


def _researched(reply, question, mode, run_brief, pid, subject, title, details=None,
                fresh=False):
    result = R.investigate(question, mode, run_brief, subject=subject, fresh=fresh, pid=pid)
    meta = {"ok": result.get("ok"), "searches": result.get("searches") or [],
            "fetched": result.get("fetched") or [], "cached": bool(result.get("cached")),
            "errors": result.get("errors") or [],
            "reason": result.get("reason")}
    if not result.get("ok"):
        # Research failed: the run's answer stands, and the failure is said.
        note = "I tried to look this up but couldn't: {0}.".format(result.get("reason"))
        base = reply.get("answer") or ""
        if reply.get("understood") is False:
            base = ""
        return dict(reply, answer=(base + "\n\n" if base else "") + note, research=meta)
    out = dict(reply, answer=result["answer"], web_sources=result["sources"], research=meta,
               intent=reply.get("intent") or "research", understood=True, grounded=False)
    out.pop("fallback", None)
    if details and details.strip() and details.strip() != result["answer"].strip():
        out["details"] = details
    out["sources"] = list(reply.get("sources") or []) + ["web research"]
    return out
