"""
Failure intelligence — what ATLAS understands about a shipment that did not
complete, built from the run's own records and nothing else.

    OBSERVE      the shipment record, its steps, what was read, what was
                 written, the run's own outcome class, the recovery episodes
                 the run's executor recorded, and any failure the code that
                 stopped it DECLARED (stage, category, the rule that decided)
    CLASSIFY     a category, only where the evidence supports it; otherwise
                 UNKNOWN_FAILURE
    DIAGNOSE     last successful event, first failing event, stage
    ROOT CAUSE   VERIFIED only when the deciding code declared it; INFERRED
                 when read from the run's outcome class or message; UNKNOWN
    PLAN         from verified history, approved proposals, the run's own
                 built-in safe actions — or "no verified strategy"
    VERIFY       what the run's verification said, never assumed
    LEARN        what was recorded, and that only verified outcomes earn credit

Every statement carries its knowledge type:

    FACT            read directly from this run's records
    LEARNED         a pattern from the verified learning store (past runs)
    INFERENCE       derived by a stated rule; may be wrong
    RECOMMENDATION  what to do; a person decides
    UNVERIFIED      what the evidence does not establish

The same function feeds the dashboard's failure card, "ATLAS noticed", the
chat and the run log, so they cannot disagree. Nothing here executes,
writes to the Hub or changes the automation.
"""

import hashlib

CATEGORIES = (
    "NAVIGATION_FAILURE", "PAGE_NOT_READY", "AUTHENTICATION_FAILURE", "TIMEOUT",
    "NETWORK_FAILURE", "DATA_EXTRACTION_FAILURE", "VALIDATION_FAILURE",
    "HUB_WRITE_FAILURE", "HUB_READBACK_FAILURE", "CARRIER_POLICY_BLOCK",
    "HUMAN_ACTION_REQUIRED", "SECURITY_VERIFICATION_REQUIRED", "CARRIER_ACCESS_RESTRICTED",
    "CARRIER_ACCESS_NOT_CONFIRMED", "UNKNOWN_FAILURE")
ACCESS_CATEGORIES = ("CARRIER_ACCESS_RESTRICTED", "CARRIER_ACCESS_NOT_CONFIRMED")

LABELS = {
    "NAVIGATION_FAILURE": "the carrier page could not be reached",
    "PAGE_NOT_READY": "the page was not in the expected state",
    "AUTHENTICATION_FAILURE": "sign-in was refused",
    "TIMEOUT": "a step did not finish in time",
    "NETWORK_FAILURE": "the carrier website did not respond normally",
    "DATA_EXTRACTION_FAILURE": "no usable date could be read",
    "VALIDATION_FAILURE": "a value read did not pass validation",
    "HUB_WRITE_FAILURE": "the Hub write did not complete",
    "HUB_READBACK_FAILURE": "the Hub did not read back the value written",
    "CARRIER_POLICY_BLOCK": "the write was blocked by a rule of this automation",
    "HUMAN_ACTION_REQUIRED": "a person's step was not completed",
    "SECURITY_VERIFICATION_REQUIRED": "the carrier asked for human verification",
    "CARRIER_ACCESS_RESTRICTED": "the carrier restricted access and showed its restriction page",
    "CARRIER_ACCESS_NOT_CONFIRMED": "after the human verification the carrier never showed the "
                                    "shipment page",
    "UNKNOWN_FAILURE": "the cause is not identified",
}

# The run's own outcome classes (update_eta.classify_failure and the human
# loop) and the category each points at. Reading the class is an INFERENCE:
# the run classified the message, it did not declare the cause.
FROM_OUTCOME = {
    "AFKL NAVIGATION ERROR": "NAVIGATION_FAILURE",
    "TIMEOUT": "TIMEOUT",
    "TEMPORARY WEBSITE ISSUE": "NETWORK_FAILURE",
    "AUTHENTICATION ISSUE": "AUTHENTICATION_FAILURE",
    "UNEXPECTED PAGE STATE": "PAGE_NOT_READY",
    "NO RESULT": "DATA_EXTRACTION_FAILURE",
    "HUMAN VERIFICATION REQUIRED": "SECURITY_VERIFICATION_REQUIRED",
    "HUMAN TIMEOUT": "HUMAN_ACTION_REQUIRED",
    "HUMAN SESSION LOST": "HUMAN_ACTION_REQUIRED",
    "WRITE BLOCKED BY POLICY": "CARRIER_POLICY_BLOCK",
    "CARRIER ACCESS RESTRICTED": "CARRIER_ACCESS_RESTRICTED",
    "CARRIER ACCESS NOT CONFIRMED": "CARRIER_ACCESS_NOT_CONFIRMED",
}

# Message rules, applied only when nothing better exists. Each is a stated,
# deterministic reading of the run's own message — still an INFERENCE.
MESSAGE_RULES = (
    (("did not read back", "does not hold", "read back", "not verified"), "HUB_READBACK_FAILURE"),
    (("save failed", "could not save", "save did not"), "HUB_WRITE_FAILURE"),
    (("is not a date", "in the future", "invalid date", "rejected"), "VALIDATION_FAILURE"),
    (("timed out", "timeout"), "TIMEOUT"),
    (("net::", "err_", "connection", "502", "503", "504"), "NETWORK_FAILURE"),
    (("navigation", "did not open", "could not open"), "NAVIGATION_FAILURE"),
    (("no estimated", "returned no", "did not provide", "no eta"), "DATA_EXTRACTION_FAILURE"),
)

# The run's recovery executor (ml/recovery.py) speaks its own error classes.
TO_RECOVERY_CLASS = {
    "PAGE_NOT_READY": "PAGE_NOT_READY", "TIMEOUT": "TIMEOUT",
    "NAVIGATION_FAILURE": "NAVIGATION_FAILURE", "NETWORK_FAILURE": "NETWORK_TRANSIENT",
    "HUB_WRITE_FAILURE": "SAVE_FAILURE", "HUB_READBACK_FAILURE": "VERIFICATION_FAILURE",
    "VALIDATION_FAILURE": "VALIDATION_FAILURE", "AUTHENTICATION_FAILURE": "AUTHENTICATION",
    "SECURITY_VERIFICATION_REQUIRED": "HUMAN_VERIFICATION",
    # Both are the recovery policy's CARRIER_ACCESS_RESTRICTED (no automatic
    # action); RESTRICTED last, so the reverse map names it.
    "CARRIER_ACCESS_NOT_CONFIRMED": "CARRIER_ACCESS_RESTRICTED",
    "CARRIER_ACCESS_RESTRICTED": "CARRIER_ACCESS_RESTRICTED",
}

# ...and back: the executor's own diagnosis of the failing step, when it
# ran. Still an INFERENCE — a stated classification rule, not a declaration.
FROM_RECOVERY_CLASS = dict(
    {v: k for k, v in TO_RECOVERY_CLASS.items()},
    UNEXPECTED_PAGE_STATE="PAGE_NOT_READY", FRAME_NOT_READY="PAGE_NOT_READY",
    PARTIAL_LOAD="PAGE_NOT_READY", ELEMENT_NOT_FOUND="PAGE_NOT_READY",
    ELEMENT_NOT_VISIBLE="PAGE_NOT_READY", STALE_ELEMENT="PAGE_NOT_READY",
    ELEMENT_DISABLED="PAGE_NOT_READY", BLOCKING_DIALOG="PAGE_NOT_READY",
    SESSION_EXPIRED="AUTHENTICATION_FAILURE", SERVER_ERROR="NETWORK_FAILURE",
    INPUT_REJECTED="VALIDATION_FAILURE")
BASIS_WORDS = {"outcome": "the run's outcome class", "recovery": "the recovery executor's diagnosis",
               "message": "the run's message",
               "access": "the carrier-access state the run recorded"}

STAGES = {
    "navigation": "opening the carrier's tracking page",
    "carrier_lookup": "looking the shipment up on the carrier site",
    "human_verification": "the carrier's human verification",
    "carrier_access": "getting to the shipment page on the carrier site",
    "data_extraction": "reading the dates from the carrier page",
    "validation": "validating the dates read",
    "hub_write": "writing to the Hub",
    "hub_readback": "reading the Hub back after the write",
    "unknown": "an unidentified step",
}
STAGE_OF = {
    "NAVIGATION_FAILURE": "navigation", "PAGE_NOT_READY": "carrier_lookup",
    "AUTHENTICATION_FAILURE": "carrier_lookup", "TIMEOUT": "carrier_lookup",
    "NETWORK_FAILURE": "carrier_lookup", "DATA_EXTRACTION_FAILURE": "data_extraction",
    "VALIDATION_FAILURE": "validation", "HUB_WRITE_FAILURE": "hub_write",
    "HUB_READBACK_FAILURE": "hub_readback", "CARRIER_POLICY_BLOCK": "hub_write",
    "HUMAN_ACTION_REQUIRED": "human_verification",
    "SECURITY_VERIFICATION_REQUIRED": "human_verification",
    "CARRIER_ACCESS_RESTRICTED": "carrier_access",
    "CARRIER_ACCESS_NOT_CONFIRMED": "carrier_access",
}
FAILED_STATES = ("failed", "partial", "human_timeout")
# A skip is a failure when the run classified it as one. "No result" — the
# carrier publishes no date yet — is an expected outcome, not a failure.
EXPECTED_SKIPS = (None, "", "NO RESULT", "SUCCESS")


def _first_upper(text):
    return text[:1].upper() + text[1:]


def _fact(text, source):
    return {"type": "FACT", "text": text, "source": source}


def _item(kind, text, source):
    return {"type": kind, "text": text, "source": source}


def _access(record):
    return ((record.get("carrier_access") or {}).get("state") or "").upper()


def is_failure(record):
    state = record.get("state")
    if state != "updated" and _access(record) in ("RESTRICTED", "NOT_CONFIRMED") and \
            (state != "processing" or _access(record) == "RESTRICTED"):
        # The carrier refused, or never served the page after a completed
        # verification: a failure even when it ended as a plain skip.
        return True
    if state == "skipped" and record.get("human_verification") == "COMPLETED" and \
            _access(record) != "CONFIRMED":
        return True
    if state in FAILED_STATES:
        return True
    if state == "skipped":
        return bool(record.get("failure")) or record.get("outcome") not in EXPECTED_SKIPS
    return False


def classify(record, diagnosed=None):
    """(category, basis): declared by the run, read from its outcome class,
    the recovery executor's diagnosis (`diagnosed`, its error class) or the
    message, or unknown. Never forced."""
    declared = (record.get("failure") or {}).get("category")
    if declared in CATEGORIES:
        return declared, "declared"
    if _access(record) == "RESTRICTED":
        return "CARRIER_ACCESS_RESTRICTED", "access"
    if record.get("human_verification") == "COMPLETED" and _access(record) != "CONFIRMED" and \
            record.get("state") in ("skipped", "failed"):
        return "CARRIER_ACCESS_NOT_CONFIRMED", "access"
    if record.get("state") == "partial":
        return "HUB_WRITE_FAILURE", "outcome"
    mapped = FROM_OUTCOME.get(str(record.get("outcome") or "").upper())
    if mapped:
        return mapped, "outcome"
    mapped = FROM_RECOVERY_CLASS.get(str(diagnosed or "").upper())
    if mapped:
        return mapped, "recovery"
    text = str(record.get("error") or "").casefold()
    for needles, category in MESSAGE_RULES:
        if any(n in text for n in needles):
            return category, "message"
    return "UNKNOWN_FAILURE", "none"


def _stage(record, category):
    declared = (record.get("failure") or {}).get("stage")
    if declared in STAGES:
        return declared
    if category in STAGE_OF:
        return STAGE_OF[category]
    if record.get("coe_action") or record.get("bu_action"):
        return "hub_write"
    if record.get("provider_eta") or record.get("provider_ata") or record.get("provider_status"):
        return "validation"
    return "unknown"


def _read(record):
    parts = []
    for kind, label in (("eta", "ETA"), ("ata", "ATA")):
        value = record.get("provider_" + kind)
        if value:
            source = record.get("provider_{0}_source".format(kind))
            parts.append("{0} {1}{2}".format(label, value, " (from '{0}')".format(source)
                                            if source else ""))
    return parts


def _last_success(record, stage):
    declared = (record.get("failure") or {}).get("last_success")
    read = _read(record)
    if read:
        return "{0} read from {1}".format(" and ".join(read),
                                          record.get("carrier") or "the carrier")
    if declared:
        return declared
    steps = [s.get("text") for s in (record.get("steps") or []) if s.get("text")]
    before = [s for s in steps if s not in ("Skipped", "Failed", "Partly updated",
                                            "Human timeout")]
    return before[-1] if before else None


def _wrote(record):
    return [a for a in (record.get("coe_action"), record.get("bu_action"))
            if isinstance(a, str) and a]


def failure_id(run_id, record):
    seed = "{0}|{1}|{2}".format(run_id, record.get("reference"), record.get("started_at"))
    return "f_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def from_record(record, run_id=None, recoveries=None, learning=None, events=None,
                evidence=None):
    """The failure intelligence record for one shipment record, or None."""
    if not isinstance(record, dict) or not is_failure(record):
        return None
    ref = record.get("reference")
    episodes = [e for e in (recoveries or []) if e.get("reference") == ref]
    diagnosed = next((e.get("error_class") for e in reversed(episodes) if e.get("error_class")), None)
    category, basis = classify(record, diagnosed)
    stage = _stage(record, category)
    declared = record.get("failure") or {}
    cause = declared.get("cause") or {}
    ref, carrier = record.get("reference"), record.get("carrier") or record.get("provider")
    message = record.get("error") or ""
    read = _read(record)
    wrote = _wrote(record)
    steps = [{"at": s.get("time"), "text": s.get("text")} for s in (record.get("steps") or [])
             if s.get("text")][-12:]
    last_ok = _last_success(record, stage)
    attempts = [{"action": a.get("action"), "result": a.get("result"),
                 "verified": a.get("verified"), "error_class": e.get("error_class")}
                for e in episodes for a in (e.get("attempts") or [])]
    recovered = [e for e in episodes if e.get("status") == "RECOVERED"]

    # ── ROOT CAUSE ──────────────────────────────────────────────────────
    if basis == "declared" and (cause or declared.get("detail")):
        root_status = "VERIFIED"
        root = declared.get("detail") or LABELS[category]
    elif basis == "access":
        root_status = "VERIFIED"
        root = LABELS[category]
    elif category != "UNKNOWN_FAILURE":
        root_status = "INFERRED"
        root = LABELS[category]
    else:
        root_status = "UNKNOWN"
        root = None

    # ── KNOWLEDGE, TYPED ────────────────────────────────────────────────
    facts = [_fact("{0} ({1}) did not complete in run {2}; the run recorded it as {3}{4}.".format(
        ref, carrier or "carrier unknown", run_id or "—", record.get("state"),
        ", outcome {0}".format(record.get("outcome")) if record.get("outcome") else ""),
        "shipment record")]
    if read:
        facts.append(_fact("Data extraction succeeded: {0} was read from {1}.".format(
            " and ".join(read), carrier or "the carrier"), "shipment record"))
    elif declared.get("observed") and category not in ACCESS_CATEGORIES:
        facts.append(_fact("Read before it stopped: {0}.".format(", ".join(
            "{0} {1}".format(k, v) for k, v in declared["observed"].items())), "declared failure"))
    if wrote:
        facts.append(_fact("Hub actions recorded: {0}.".format("; ".join(wrote)), "shipment record"))
    elif record.get("state") != "partial":
        facts.append(_fact("Nothing was written to the Hub for this shipment.", "shipment record"))
    if message:
        facts.append(_fact('The run\'s own message: "{0}"'.format(message[:400]), "run message"))
    if basis == "declared":
        facts.append(_fact("Stopped at: {0}. {1}".format(
            STAGES.get(stage, stage), declared.get("detail") or ""), "declared by the run's code"))
        if cause.get("name") and category not in ACCESS_CATEGORIES:
            facts.append(_fact("Decided by {0}: {1} is {2}.".format(
                cause.get("decided_by") or "the run", cause["name"], cause.get("value") or "set"),
                "declared by the run's code"))
    if last_ok:
        facts.append(_fact("Last successful step: {0}.".format(last_ok), "steps"))
    if attempts:
        facts.append(_fact("The run's recovery executor tried: {0}.".format(", ".join(
            "{0} → {1}{2}".format(a["action"], str(a["result"]).lower(),
                                   " (verified)" if a["verified"] is True else
                                   " (verification failed)" if a["verified"] is False else
                                   " (not verified)") for a in attempts)), "recovery history"))

    inferences, unverified = [], []
    if basis in BASIS_WORDS:
        inferences.append(_item("INFERENCE", "This looks like {0}: {1} (read from {2}{3}).".format(
            category, LABELS[category], BASIS_WORDS[basis],
            ", which named it {0}".format(diagnosed) if basis == "recovery" else ""),
            "classification rule"))
    if category in ACCESS_CATEGORIES:
        check = record.get("access_check") or {}
        observed = declared.get("observed") or {}
        verified = check.get("verification_completed") is True or \
            observed.get("after_human_verification") == "yes" or \
            record.get("human_verification") == "COMPLETED"
        who = carrier or "the carrier"
        if verified and category == "CARRIER_ACCESS_RESTRICTED":
            lead = ("Human verification was completed, but {0} continued to restrict access. The "
                    "carrier page never became usable for extraction, so no shipment data was "
                    "extracted and no Hub write was attempted.".format(who))
        elif verified:
            lead = ("Human verification was completed, but {0} never showed the shipment page, "
                    "so carrier access was never confirmed: no shipment data was extracted and "
                    "no Hub write was attempted.".format(who))
        else:
            lead = ("{0} restricted access as soon as its tracking page was opened. The page "
                    "never became usable for extraction, so no shipment data was extracted and "
                    "no Hub write was attempted.".format(who))
        facts.insert(0, _fact(lead, "carrier-access record"))
        if check:
            facts.insert(1, _fact("Recorded by the run: " + "; ".join(
                "{0} = {1}".format(k, str(v).lower() if isinstance(v, bool) else v)
                for k, v in check.items()) + ".", "carrier-access record"))
    if category == "CARRIER_ACCESS_RESTRICTED":
        observed = declared.get("observed") or {}
        if observed.get("url"):
            facts.append(_fact("The restriction page: {0} — \"{1}\".".format(
                observed["url"], observed.get("title") or "no title"), "declared failure"))
        if observed.get("after_human_verification") == "yes":
            facts.append(_fact("The human verification was completed before this; completing it "
                               "did not give access to the shipment page.", "declared failure"))
        if observed.get("page_text") or observed.get("screenshot"):
            facts.append(_fact("Evidence kept: {0}.".format(", ".join(
                v for v in (observed.get("page_text"), observed.get("screenshot")) if v)),
                "declared failure"))
        unverified.append(_item("UNVERIFIED",
            "WHY the carrier restricted this worker is not established by the run. {0}. "
            "Whether the restriction follows the worker's IP/network, the browser session, the "
            "automation environment, an account, or another carrier-side condition needs the "
            "worker diagnostic: python -m worker.verify carrier --carrier {1} --reference {2}."
            .format(_first_upper(cause.get("stated_condition") or "the page names no cause")
                    .replace("possible causes: none named", "no possible cause in the wording "
                             "recognised"),
                    record.get("provider") or "CARRIER", ref), "evidence gap"))
    elif category == "CARRIER_ACCESS_NOT_CONFIRMED":
        unverified.append(_item("UNVERIFIED",
            "Why the shipment page never appeared is not established: no restriction wording was "
            "recognised, and the page text was kept. The worker diagnostic separates session, "
            "browser, IP and carrier causes: python -m worker.verify carrier --carrier {0} "
            "--reference {1}.".format(record.get("provider") or "CARRIER", ref), "evidence gap"))
    elif root_status == "VERIFIED" and cause.get("stated_condition"):
        unverified.append(_item("UNVERIFIED",
            "The configuration's own stated condition is: {0}. Whether that policy should "
            "now change is not something this run can establish.".format(cause["stated_condition"]),
            "declared by the run's code"))
    elif root_status == "VERIFIED":
        unverified.append(_item("UNVERIFIED", "Why that rule is set is not recorded in this run.",
                                "evidence gap"))
    elif root_status == "INFERRED":
        unverified.append(_item("UNVERIFIED",
            "The root cause is not confirmed: the run classified the message, it did not "
            "declare the cause.", "evidence gap"))
    else:
        unverified.append(_item("UNVERIFIED",
            "The run does not contain enough evidence to identify the cause.", "evidence gap"))

    item = {
        "failure_id": failure_id(run_id, record), "run_id": run_id,
        "shipment_id": ref, "carrier": carrier, "provider": record.get("provider"),
        "operation": declared.get("operation") or STAGES.get(stage, stage),
        "stage": stage, "stage_label": STAGES.get(stage, stage),
        "timestamp": record.get("updated") or record.get("started_at"),
        "error_type": category, "error_message": message[:600],
        "observed_state": {"state": record.get("state"), "outcome": record.get("outcome"),
                           "read": read, "written": wrote,
                           "hub_eta_before": record.get("internal_eta")},
        "evidence_refs": [],
        "previous_events": steps,
        "last_successful_event": last_ok,
        "first_failing_event": message[:240] or record.get("step"),
        "recovery_attempts": attempts,
        "recovery_result": ("RECOVERED" if recovered else "EXHAUSTED" if episodes else None),
        "verification_result": _verification(record),
        "classification": category, "classification_basis": basis,
        "diagnosed_class": diagnosed,
        "classification_label": LABELS[category],
        "root_cause_status": root_status, "root_cause": root,
        "facts": facts, "inferences": inferences, "unverified": unverified,
        "recommendations": [], "learned": [],
        "declared_cause": cause or None,
        "headline": headline(record, category, read),
        "impact": impact(record, wrote, read),
    }
    item["recovery_plan"] = plan(item, learning)
    item["recommendations"] = item["recovery_plan"]["recommendations"]
    if learning is not None:
        item["learned"] = known(item, learning)
    if evidence is not None:
        item["evidence_refs"] = evidence_refs(item, evidence)
    item["learning_status"] = learning_status(item, events)
    mode, why = work_mode(item)
    item["work"] = {"mode": mode, "why": why,
                    "retried": bool(record.get("deferred_retry")),
                    "earlier_attempt": record.get("deferred_retry") or None}
    return item


def _verification(record):
    v = record.get("verification") or {}
    if not v:
        return "No Hub read-back ran for this shipment."
    return "; ".join("{0}: {1}".format(k, "read back and matched" if ok is True else
                                       "did NOT match" if ok is False else "not read back")
                     for k, ok in v.items())


def headline(record, category, read):
    ref, carrier = record.get("reference"), record.get("carrier") or record.get("provider")
    if category == "CARRIER_ACCESS_RESTRICTED":
        verified = (record.get("access_check") or {}).get("verification_completed") is True or \
            record.get("human_verification") == "COMPLETED"
        return "{0} · {1}: carrier access restricted{2} — the carrier showed its restriction " \
               "page; nothing was extracted or written.".format(
                   ref, carrier, " after the human verification was completed" if verified else "")
    if category == "CARRIER_ACCESS_NOT_CONFIRMED":
        return "{0} · {1}: carrier access not confirmed after the human verification — the " \
               "shipment page never appeared; nothing was extracted or written.".format(ref, carrier)
    if category == "CARRIER_POLICY_BLOCK":
        return "{0} · {1}: {2} — Hub write not performed (blocked by configuration).".format(
            ref, carrier, " and ".join(read) + " read" if read else "result read")
    return "{0} · {1}: {2}.".format(ref, carrier, LABELS[category])


def impact(record, wrote, read):
    if wrote and record.get("state") == "partial":
        return "Only part of the update reached the Hub: {0}.".format("; ".join(wrote))
    if read and not wrote:
        return ("The carrier's dates were read but the Hub still holds its previous value"
                "{0}.".format(" (ETA {0})".format(record["internal_eta"])
                              if record.get("internal_eta") else ""))
    return "Nothing was written to the Hub for this shipment; it is looked up again next run."


# ── RECOVERY PLAN ────────────────────────────────────────────────────

def _builtin(category):
    """The run's own safe recovery actions for this category, if any."""
    klass = TO_RECOVERY_CLASS.get(category)
    if not klass:
        return klass, [], None
    try:
        from ml import recovery as rec
    except Exception:
        return klass, [], None
    reason_not = rec.why_not(klass)
    if reason_not:
        return klass, [], reason_not
    actions = rec.candidates(klass, in_write=category == "HUB_WRITE_FAILURE")
    return klass, [{"strategy": a.name, "detail": a.detail, "safety": a.risk,
                    "verifies": a.verifies} for a in actions], None


def _history(item, learning):
    """Verified strategies for this provider and failure, from past runs."""
    if not learning:
        return [], []
    try:
        from intelligence import learning as L
    except Exception:
        return [], []
    names = {item["classification"], str(item["observed_state"].get("outcome") or ""),
             TO_RECOVERY_CLASS.get(item["classification"]) or "", item.get("diagnosed_class") or ""}
    verified, approved = [], []
    for issue in learning.get("issues") or []:
        if str(issue.get("provider") or "").upper() != str(item.get("provider") or "").upper():
            continue
        if str(issue.get("issue") or "").upper() not in {n.upper() for n in names if n}:
            continue
        best = issue.get("best")
        if best:
            s = issue["strategies"][best]
            if s.get("successes") and s.get("confidence") not in ("Insufficient data",):
                verified.append({"strategy": best, "successes": s["successes"],
                                 "decided": s.get("decided"), "confidence": s.get("confidence"),
                                 "runs": list(s.get("evidence_runs") or [])[-5:],
                                 "issue": issue.get("key")})
    for p in learning.get("proposals") or []:
        if p.get("status") == "APPROVED" and any(
                str(p.get("issue") or "").upper().endswith(":" + n.upper()) for n in names if n):
            approved.append({"strategy": p.get("change"), "decided_by": p.get("decided_by"),
                             "issue": p.get("issue")})
    return verified, approved


def access_steps(item):
    """The recovery plan for a carrier that will not serve the page — in the
    order it must happen, each saying who does it. Nothing here is automatic."""
    ref, provider = item.get("shipment_id"), item.get("provider") or "CARRIER"
    url = next((f["text"].rstrip(".") for f in item["facts"]
                if f["text"].startswith("The restriction page")), "the run's record")
    return [
        {"strategy": "Diagnose from this run's evidence", "source": "ATLAS, read-only",
         "reason": item["headline"], "evidence": url[:200],
         "expected": "what the carrier showed, when, and what the run did not do",
         "safety": "read-only", "executes": "done — this answer"},
        {"strategy": "Identify a session, browser, IP or carrier restriction",
         "source": "the worker diagnostic",
         "reason": "the run cannot tell these apart; the worker can, by comparing a normal Edge "
                   "with the automation's browser and recording VPN, proxy, IP and network",
         "evidence": "python -m worker.verify carrier --carrier {0} --reference {1} "
                     "(verify_carrier.bat)".format(provider, ref),
         "expected": "what the restriction follows: ESTABLISHED, CONSISTENT or NOT_ESTABLISHED",
         "safety": "nothing written to eHub; opens the carrier page once per browser",
         "executes": "by a person, on the Windows worker"},
        {"strategy": "Apply only the change the finding supports", "source": "a person / IT",
         "reason": "network or IP: change the worker's route to the carrier (no VPN, proxy or "
                   "hotspot path); the automation's browser: the automation's owner reviews it; "
                   "not reproduced: re-run the shipment",
         "evidence": "the diagnostic's determination",
         "safety": "a configuration change by a person; nothing bypasses the carrier's controls",
         "executes": "only by a person"},
        {"strategy": "Re-run, and verify access before extraction", "source": "the run",
         "reason": "the run extracts and writes only after it reads the shipment page itself",
         "evidence": "CARRIER_ACCESS_CONFIRMED in the run's events",
         "expected": "the normal lookup; extraction, Hub write and read-back follow only then",
         "safety": "the normal lookup, once, chosen by a person (Re-run or Human Action)",
         "executes": "after a person re-runs it"},
    ]


def plan(item, learning=None):
    """
    The recovery plan, from what is actually known. ATLAS proposes; only the
    run's own executor acts, at the failing step, and verifies each action.
    """
    category = item["classification"]
    klass, builtin, not_recoverable = _builtin(category)
    verified, approved = _history(item, learning)
    steps, recs = [], []
    status = "NO_VERIFIED_STRATEGY"
    if category in ("SECURITY_VERIFICATION_REQUIRED", "HUMAN_ACTION_REQUIRED"):
        status = "HUMAN_REQUIRED"
        steps.append({"strategy": "Open & Continue", "source": "human action",
                      "reason": "The carrier needs a person; the automation never completes "
                                "human verification itself.",
                      "evidence": item["error_message"][:160],
                      "expected": "The run reads the result once the page confirms the step, "
                                  "then writes and reads back.",
                      "safety": "HUMAN_ONLY", "executes": "only when a person does it"})
    for v in verified:
        status = "VERIFIED_STRATEGY"
        steps.append({"strategy": v["strategy"], "source": "verified history",
                      "reason": "It has the best verified record for this failure on this carrier.",
                      "evidence": "{0} verified successes of {1} decided attempts, confidence {2} "
                                  "({3})".format(v["successes"], v["decided"], v["confidence"],
                                                 v["issue"]),
                      "expected": "The page reaches the expected state; the run then verifies.",
                      "safety": "existing automation strategy",
                      "executes": "by the run, when this failure recurs; never by ATLAS"})
    for a in approved:
        if status == "NO_VERIFIED_STRATEGY":
            status = "APPROVED_STRATEGY"
        steps.append({"strategy": a["strategy"], "source": "approved proposal",
                      "reason": "Approved by {0}.".format(a["decided_by"] or "an admin"),
                      "evidence": a["issue"], "expected": "As approved; verified by the run.",
                      "safety": "approved, deployed only through a tested change",
                      "executes": "after a tested deployment, never by itself"})
    for b in builtin:
        if status == "NO_VERIFIED_STRATEGY":
            status = "BUILT_IN_RULES"
        steps.append({"strategy": b["strategy"], "source": "built-in safe recovery rule",
                      "reason": b["detail"], "evidence": "recovery class {0}".format(klass),
                      "expected": "verified by '{0}'".format(b["verifies"] or "the caller's check"),
                      "safety": b["safety"],
                      "executes": "automatically by the run at the failing page step, "
                                  "within its budget"})
    if not_recoverable and status not in ("HUMAN_REQUIRED",):
        status = "NOT_RECOVERABLE"
    if category in ACCESS_CATEGORIES:
        status = "RECOVERY_REQUIRED"
        not_recoverable = not_recoverable or (
            "a carrier's access restriction is not retried or worked around; it is diagnosed "
            "on the worker and fixed in its environment")
        steps = access_steps(item)
    if status == "RECOVERY_REQUIRED":
        statement = ("Parked as an exception — recovery required. No automatic recovery is "
                     "permitted: {0}. Diagnose first; extraction continues only after the run "
                     "sees the shipment page (CARRIER_ACCESS_CONFIRMED).".format(not_recoverable))
    elif status in ("NO_VERIFIED_STRATEGY", "NOT_RECOVERABLE"):
        statement = ("No verified recovery strategy exists for this failure class ({0})."
                     .format(category))
        if not_recoverable:
            statement += " The recovery policy says it must not be retried: {0}.".format(
                not_recoverable)
    elif status == "HUMAN_REQUIRED":
        statement = "This needs a person: Open & Continue, then the run continues by itself."
    elif status == "VERIFIED_STRATEGY":
        statement = "A verified strategy exists for this failure on {0}: {1}.".format(
            item.get("carrier"), ", ".join(v["strategy"] for v in verified))
    elif status == "APPROVED_STRATEGY":
        statement = "An approved strategy exists; it applies after a tested deployment."
    else:
        statement = ("No verified history yet; the run's built-in safe actions for this class "
                     "are: {0}.".format(", ".join(b["strategy"] for b in builtin)))

    # Recommendations are labelled as such and never executed.
    if category in ACCESS_CATEGORIES:
        recs.append(_item("RECOMMENDATION",
            "Run the carrier access diagnostic on the Windows worker (python -m worker.verify "
            "carrier --carrier {0} --reference {1}): it records VPN, proxy, public IP, network "
            "type and Edge, and compares a manual Edge with the automation's browser. Decide the "
            "infrastructure change from that evidence. Do not try to get past the restriction."
            .format(item.get("provider") or "CARRIER", item.get("shipment_id")),
            "recommendation"))
    elif category == "CARRIER_POLICY_BLOCK":
        cause = item.get("declared_cause") or {}
        recs.append(_item("RECOMMENDATION",
            "Review the {0} setting with the automation's owner before changing production "
            "behaviour. This run shows exactly what was read, so the value can be checked "
            "against the carrier page first.".format(cause.get("name") or "write-policy"),
            "recommendation"))
    elif status == "HUMAN_REQUIRED":
        recs.append(_item("RECOMMENDATION", "Open & Continue the shipment when you are available; "
                          "nothing is written until the run verifies the page.", "recommendation"))
    elif status in ("NO_VERIFIED_STRATEGY", "NOT_RECOVERABLE"):
        recs.append(_item("RECOMMENDATION",
            "Check the shipment on the carrier site and the run's message above; it is looked up "
            "again next run. If it recurs, the learning store will show whether it is a pattern.",
            "recommendation"))
    else:
        recs.append(_item("RECOMMENDATION", "No action is needed from you now; the run applies "
                          "these strategies itself and records whether they verified.",
                          "recommendation"))
    return {"status": status, "statement": statement, "steps": steps,
            "recovery_class": klass, "recommendations": recs,
            "executes": "ATLAS never acts on a page; the run's executor acts and verifies."}


# ── WORK: WHAT HAPPENS TO THIS FAILURE, WITHOUT STOPPING THE RUN ─────
#
# Every failure goes on ATLAS's work list (intelligence/backlog.py). How it
# is worked depends on what the evidence supports — never on a guess:
#
#   RETRY_THIS_RUN  a transient carrier-side failure with nothing written:
#                   the run retries it ONCE, after the other shipments, by
#                   the same pipeline (look up, write, read back). The run
#                   never waits for it.
#   NEXT_RUN        nothing was written and the shipment is still in the
#                   Hub's list: the next run looks it up again by itself.
#   NEEDS_PERSON    a person's step (human verification) — Open & Continue.
#   NEEDS_DECISION  a rule, a validation, a Hub write or read-back problem,
#                   or an unknown cause: an owner decides; retrying would
#                   not change it, or could write twice.
RETRY_IN_RUN = ("NAVIGATION_FAILURE", "TIMEOUT", "NETWORK_FAILURE", "PAGE_NOT_READY")
WORK_MODES = ("RETRY_THIS_RUN", "NEXT_RUN", "NEEDS_PERSON", "NEEDS_DECISION")


def work_mode(item):
    """(mode, why) for one failure record. Deterministic; ATLAS does not choose."""
    category = item["classification"]
    state = item["observed_state"].get("state")
    written = bool(item["observed_state"].get("written"))
    if category in ("SECURITY_VERIFICATION_REQUIRED", "HUMAN_ACTION_REQUIRED"):
        return "NEEDS_PERSON", "the carrier needs a person; the automation never does that step"
    if category in ACCESS_CATEGORIES:
        return "NEEDS_DECISION", ("parked as an exception, recovery required: the carrier did not "
                                  "serve the page; retrying would not change it and is not done — "
                                  "the worker's environment is diagnosed first")
    if category in RETRY_IN_RUN and state == "failed" and not written:
        return "RETRY_THIS_RUN", ("{0} is transient and nothing was written, so the run retries "
                                  "it once after the other shipments".format(category))
    if category in ("CARRIER_POLICY_BLOCK", "VALIDATION_FAILURE", "HUB_WRITE_FAILURE",
                    "HUB_READBACK_FAILURE", "UNKNOWN_FAILURE") or written:
        return "NEEDS_DECISION", ("a retry would not change a {0}{1}".format(
            category, " and part was already written" if written else ""))
    return "NEXT_RUN", "nothing was written, so the next run looks it up again by itself"


# ── LEARNING, KNOWN PATTERNS, EVIDENCE ───────────────────────────────

def known(item, learning):
    """Has this happened before? From the verified learning store (type LEARNED)."""
    out = []
    names = {item["classification"], str(item["observed_state"].get("outcome") or ""),
             item.get("diagnosed_class") or ""}
    for issue in learning.get("issues") or []:
        if str(issue.get("provider") or "").upper() != str(item.get("provider") or "").upper():
            continue
        if str(issue.get("issue") or "").upper() not in {n.upper() for n in names if n}:
            continue
        runs = [r for r in issue.get("runs") or [] if r != item.get("run_id")]
        if not runs:
            continue
        out.append(_item("LEARNED",
            "Seen before on {0}: {1} occurrence(s) across {2} other run(s), first {3}, last {4}; "
            "{5} later resolved with a verified write.".format(
                item.get("carrier"), issue.get("occurrences"), len(runs),
                issue.get("first_seen") or "—", issue.get("last_seen") or "—",
                issue.get("resolved_verified", 0)), "learning store"))
    return out


def learning_status(item, events=None):
    """What the learning store holds for this failure, and what it earns."""
    if events is None:
        return "Not checked: the learning store is not available here."
    rows = [e for e in events if e.get("run_id") == item.get("run_id") and
            e.get("reference") == item.get("shipment_id")]
    shipment = [e for e in rows if e.get("kind") == "shipment"]
    recovery = [e for e in rows if e.get("kind") == "recovery"]
    if not shipment:
        return "Not recorded for learning (this run is not attached to a learning store, " \
               "or the record has not arrived yet)."
    text = "Recorded as {0} — no positive learning credit".format(
        shipment[-1].get("result") or "not completed")
    if recovery:
        text += "; {0} recovery episode(s) recorded, credited only if the shipment verifies".format(
            len(recovery))
    return text + "."


def evidence_refs(item, evidence):
    """Real captures for this shipment in this run, by id."""
    try:
        hits = evidence.search(reference=item.get("shipment_id"), run_id=item.get("run_id"),
                               limit=5)
    except Exception:
        return []
    return [{"id": h.get("id"), "event": h.get("event"), "at": h.get("at")} for h in hits]


def build(state, learning=None, events=None, evidence=None):
    """Every failure in this run, newest first — the one source for every view."""
    state = state or {}
    run_id = (state.get("run") or {}).get("run_id")
    # The live episode is also archived into the history once it ends: count
    # each episode once.
    recoveries, seen = [], set()
    for e in list(state.get("recovery_history") or []) + [state.get("recovery") or {}]:
        key = (e.get("reference"), e.get("started"), e.get("error_class"))
        if e and key not in seen:
            seen.add(key)
            recoveries.append(e)
    out = []
    for record in state.get("shipments") or []:
        item = from_record(record, run_id=run_id, recoveries=recoveries, learning=learning,
                           events=events, evidence=evidence)
        if item:
            out.append(item)
    return out


def context():
    """The learning snapshot, events and evidence index, when the stores exist."""
    try:
        from intelligence import learning as L, events as E, evidence as V
        return {"learning": L.snapshot(), "events": E.all_events(), "evidence": V}
    except Exception:
        return {"learning": None, "events": None, "evidence": None}


# ── PO AUTOMATION: the same failure intelligence, another domain ─────────
#
# A PO job that did not complete is a failure record in the same shape as a
# shipment's, so the chat, the work list and ATLAS NOTICED read it the same
# way. Its category is DECLARED by the pipeline stage that stopped it (po/
# pipeline.failure); its facts come from the job record and its events.

PO_LABELS = {
    "NOT_UNDER_CLEARANCE": "the eHub record is not Under Clearance, so it was not processed",
    "DOCUMENT_REVIEW_REQUIRED": "a person must choose the Bill Entry document",
    "DOCUMENT_NOT_FOUND": "no Bill Entry document was found on the eHub record",
    "DOCUMENT_AMBIGUOUS": "more than one document could be the one",
    "NAVIGATION_FAILURE": "the Hub shipment page could not be opened",
    "NETWORK_FAILURE": "the Hub did not respond normally",
    "WORKER_UNAVAILABLE": "no automation worker was online to open the Hub",
    "PDF_UNREADABLE": "the PDF could not be read",
    "DATA_EXTRACTION_FAILURE": "the PDF does not read as the expected document",
    "VALIDATION_FAILURE": "the document did not pass validation",
    "TEMPLATE_FAILURE": "the template could not be filled and verified",
    "EMAIL_FAILURE": "the email was not sent",
    "UNKNOWN_FAILURE": "the job stopped without a recorded cause",
    "DISCOVERY_FAILED": "eHub's Shipments list could not be read",
    "AUTH_REQUIRED": "eHub was not signed in",
    "MANAGE_NAVIGATION_FAILED": "Manage did not open this record",
    "PDF_DOWNLOAD_FAILED": "the Bill Entry was found but could not be downloaded",
    "SAVE_FAILED": "the generated document could not be saved and read back",
    "EMAIL_UNKNOWN": "the email may or may not have gone out — the mailbox must be checked",
    "WORKER_DISCONNECTED": "the worker stopped mid-job (resumable)",
    "G4_SOURCE_UNPROVEN": "the supplier invoice No. (G4) is needed from a person",
    "SKIPPED_DUPLICATE": "the same Bill of Entry is already handled by another job",
}
PO_STAGES = {"ehub_record": "checking the eHub record's clearance status",
             "auth": "signing in to eHub", "manage": "opening Manage on the row",
             "identity": "checking whose record Manage opened",
             "download": "downloading the Bill Entry", "output": "saving the document",
             "idempotency": "checking the document is not already handled",
             "bill_entry": "choosing the Bill Entry document",
             "pdf_retrieval": "finding the document in the Hub", "pdf_read": "reading the PDF",
             "extraction": "extracting the fields", "validation": "validating against the Hub",
             "template": "filling the template", "email": "sending the email"}
# What a person can do, by category — a recommendation, never an action.
PO_ADVICE = {
    "NOT_UNDER_CLEARANCE": "Nothing to do now: the record is processed once eHub lists it as "
                           "Under Clearance.",
    "DOCUMENT_REVIEW_REQUIRED": "Open the record in eHub, decide which Bill Entry document is the "
                                "right one (remove or rename the others), then process it again.",
    "DOCUMENT_NOT_FOUND": "Attach the Bill Entry document to the record in eHub (its name must "
                          "start with 'Bill Entry'), then process it again.",
    "DOCUMENT_AMBIGUOUS": "Name the document when you process it again (Document name), so one "
                          "is chosen by a person, not by ATA.",
    "NAVIGATION_FAILURE": "Process it again; if it repeats, check the shipment opens in the Hub.",
    "NETWORK_FAILURE": "Process it again once the Hub responds normally.",
    "WORKER_UNAVAILABLE": "Start the automation worker, then process it again.",
    "PDF_UNREADABLE": "Replace the PDF in the Hub with a readable copy (not password-protected, "
                      "not an empty scan).",
    "DATA_EXTRACTION_FAILURE": "Check the document attached to the shipment is the declaration "
                               "(BOE).",
    "TEMPLATE_FAILURE": "Ask the automation's owner to check the approved template; nothing "
                        "was sent.",
}


def _po_validation_advice(record):
    advice = []
    for c in (record.get("validation") or {}).get("checks") or []:
        if not c.get("blocking"):
            continue
        if c["status"] == "MISMATCH":
            advice.append("Check which document is attached to {0} in the Hub: the PDF says {1}, "
                          "the Hub says {2}. Correct the attachment or the Hub record, then "
                          "process it again.".format(record["reference"], c["pdf"], c["hub"]))
        elif c["status"] == "MISSING" and c.get("source") == "request":
            advice.append("Provide the {0} when you process it again.".format(c["label"].lower()))
        elif c["status"] == "MISSING" and c.get("source") == "config":
            advice.append("Set the {0} in the PO configuration or provide it with the "
                          "request.".format(c["label"].lower()))
        elif c["status"] in ("MISSING", "AMBIGUOUS"):
            advice.append("Check the {0} on the PDF itself: {1}.".format(
                c["label"], c.get("detail") or c["status"].lower()))
        elif c["status"] == "FAILED":
            advice.append("Reconcile the figures on the declaration before release: {0}.".format(
                c.get("detail")))
    return advice


def discovery_lines(record):
    """The eHub steps the job's trail recorded, one sentence each — facts only."""
    trail = record.get("discovery") or {}
    out = []
    row = trail.get("ehub_record")
    if row:
        out.append("eHub record {0} ({1}) found on view {2}, page {3}.".format(
            row.get("bol_awb"), row.get("carrier") or "carrier not shown", row.get("view"),
            row.get("table_page")))
    clearance = trail.get("clearance")
    if clearance:
        out.append("Status in eHub: '{0}' — {1}.".format(
            clearance.get("found"), "exactly Under Clearance" if clearance.get("ok") else
            "not exactly Under Clearance, so Manage was not opened"))
    if trail.get("manage"):
        out.append("Manage opened ({0}).".format(trail["manage"].get("url") or "details page"))
    docs = trail.get("documents")
    if docs:
        out.append("Documents section {0}: {1}.".format(
            "found" if docs.get("found") else "NOT found",
            ", ".join(docs.get("entries") or []) or "no document listed"))
    bill = trail.get("bill_entry")
    if bill:
        if bill.get("selected"):
            out.append("Bill Entry document: {0} → identifier {1} ({2}).".format(
                bill["selected"], bill.get("identifier"), bill.get("rule")))
        elif bill.get("candidates"):
            out.append("Bill Entry documents: {0} — {1}.".format(
                ", ".join(c["name"] for c in bill["candidates"]), bill.get("rule")))
        else:
            out.append("No document whose name starts with 'Bill Entry'.")
    dl = trail.get("download")
    if dl:
        out.append("Downloaded from eHub ({0}, {1} bytes{2}).".format(
            dl.get("method"), dl.get("bytes"),
            ", served as " + dl["served_filename"] if dl.get("served_filename") else ""))
    return out


def from_po(record, events=None, learning=None, history=None):
    """The failure record for a PO job that did not complete, or None."""
    if not isinstance(record, dict):
        return None
    state = record.get("state")
    email = record.get("email") or {}
    failed = state in ("PDF_NOT_FOUND", "PDF_UNREADABLE", "EXTRACTION_FAILED",
                       "VALIDATION_FAILED", "TEMPLATE_FAILED", "EMAIL_FAILED", "NEEDS_REVIEW",
                       "SKIPPED", "DISCOVERY_FAILED", "AUTH_REQUIRED",
                       "MANAGE_NAVIGATION_FAILED", "DOCUMENT_AMBIGUOUS", "PDF_DOWNLOAD_FAILED",
                       "SAVE_FAILED", "EMAIL_UNKNOWN", "WORKER_DISCONNECTED")
    blocked = email.get("status") == "BLOCKED" and not failed
    if not failed and not blocked:
        return None
    declared = record.get("failure") or {}
    category = declared.get("category") or ("EMAIL_FAILURE" if blocked else "UNKNOWN_FAILURE")
    if blocked:
        category = "VALIDATION_FAILURE" if not (record.get("validation") or {}).get("passed") \
            else "EMAIL_FAILURE"
    label = PO_LABELS.get(category, category.replace("_", " ").lower())
    stage = declared.get("stage") or ("email" if blocked else "unknown")
    ref, number = record.get("reference"), record.get("number")
    events = [e for e in (events or []) if e.get("po_id") == record.get("po_id")]
    ok_events = [e for e in events if e.get("status") == "OK"]
    last_ok = ok_events[-1]["event"] if ok_events else None
    doc = record.get("document") or {}
    facts = [_fact("PO job {0} for {1} ({2}) stopped at {3}: {4}.".format(
        record.get("po_id"), ref, record.get("doctype"), PO_STAGES.get(stage, stage), state if
        failed else "email blocked"), "PO job record")]
    facts += [_fact(t, "eHub discovery trail") for t in discovery_lines(record)]
    if doc.get("filename"):
        facts.append(_fact("The document {0} was retrieved from the {1} ({2} bytes, SHA-256 "
                           "{3}…).".format(doc["filename"], doc.get("source") or "Hub",
                                           doc.get("bytes"), str(doc.get("sha256"))[:12]),
                           "PO job record"))
    fields = record.get("fields") or {}
    found = [f for f in fields.values() if f.get("status") == "FOUND"]
    if fields:
        facts.append(_fact("Extracted from the PDF: {0} field(s) found{1}.".format(
            len(found), "; missing: " + ", ".join(f["label"] for f in fields.values()
                                                  if f.get("status") == "MISSING")
            if any(f.get("status") == "MISSING" for f in fields.values()) else ""),
            "PO job record"))
    for c in (record.get("validation") or {}).get("checks") or []:
        if c.get("blocking"):
            facts.append(_fact("Validation — {0}: {1}{2}.".format(
                c["label"], c["status"],
                " (PDF: {0}, Hub: {1})".format(c["pdf"], c["hub"]) if c["status"] == "MISMATCH"
                else " — " + c["detail"] if c.get("detail") else ""), "validation"))
    if not (record.get("output") or {}).get("verified"):
        facts.append(_fact("No document was generated.", "PO job record"))
    if email.get("status") in ("BLOCKED", "FAILED", None) and state not in ("EMAIL_SENT",
                                                                          "EMAIL_CONFIRMED"):
        facts.append(_fact("No email was sent.", "PO job record"))
    if declared.get("detail"):
        facts.append(_fact('The pipeline\'s own reason: "{0}"'.format(declared["detail"][:400]),
                           "declared by the pipeline"))
    attempts = record.get("attempts") or {}
    if attempts:
        facts.append(_fact("Retries the job made: {0}.".format(", ".join(
            "{0} × {1}".format(n, s) for s, n in attempts.items())), "PO job record"))
    unverified = []
    if (category == "EMAIL_FAILURE" and declared.get("kind") == "unknown") or \
            state == "EMAIL_UNKNOWN":
        unverified.append(_item("UNVERIFIED", "Whether Microsoft 365 delivered it is not "
                                "established; the send outcome is unknown.", "evidence gap"))
    if category == "UNKNOWN_FAILURE":
        unverified.append(_item("UNVERIFIED", "The job stopped without recording why.",
                                "evidence gap"))
    advice = _po_validation_advice(record) if category == "VALIDATION_FAILURE" else \
        [PO_ADVICE[category]] if category in PO_ADVICE else []
    if category == "EMAIL_FAILURE":
        if declared.get("kind") in ("transient", "unknown"):
            advice.append("Send again from the PO page: the same prepared message is used, and "
                          "the send ledger stops a duplicate.")
        elif email.get("duplicate_of"):
            advice.append("It was already sent (job {0}). Send again only with an authorized "
                          "resend.".format(email["duplicate_of"]))
        else:
            advice.append("Check the Microsoft 365 mail settings (Mail.Send permission, the "
                          "sender mailbox) with the platform owner, then send again.")
    if category in ("NOT_UNDER_CLEARANCE", "DOCUMENT_REVIEW_REQUIRED", "DOCUMENT_NOT_FOUND") \
            and not advice:
        advice = [PO_ADVICE[category]]
    # The pipeline's own next action for its precise stop (po.store.NEXT_ACTION).
    if declared.get("next_action") and declared["next_action"] not in advice:
        advice.append(declared["next_action"])
    recs = [_item("RECOMMENDATION", a, "PO advice") for a in advice]
    retryable = category in ("NETWORK_FAILURE", "NAVIGATION_FAILURE", "WORKER_UNAVAILABLE",
                             "PDF_DOWNLOAD_FAILED", "WORKER_DISCONNECTED") or \
        (category == "EMAIL_FAILURE" and declared.get("kind") in ("transient", "unknown"))
    learned = []
    for issue in (learning or {}).get("issues") or []:
        if issue.get("provider") == "PO" and issue.get("issue") == category:
            others = [r for r in issue.get("runs") or [] if r != record.get("po_id")]
            if others:
                learned.append(_item("LEARNED", "Seen before in PO Automation: {0} time(s) "
                                     "across {1} other job(s); {2} later resolved with a "
                                     "confirmed send.".format(issue.get("occurrences"),
                                                              len(others),
                                                              issue.get("resolved_verified", 0)),
                                     "learning store"))
    item = {
        "domain": "po", "po_id": record.get("po_id"),
        "failure_id": "f_" + hashlib.sha1("{0}|{1}".format(record.get("po_id"), state).encode(
            "utf-8")).hexdigest()[:12],
        "run_id": record.get("run_id"), "shipment_id": ref, "number": number,
        "carrier": "PO Automation", "provider": "PO",
        "operation": PO_STAGES.get(stage, stage), "stage": stage,
        "stage_label": PO_STAGES.get(stage, stage),
        "timestamp": record.get("updated"), "error_type": category,
        "error_message": (declared.get("detail") or "; ".join(email.get("reasons") or []))[:600],
        "observed_state": {"state": state, "outcome": state, "read": [], "written": [],
                           "email": email.get("status")},
        "evidence_refs": [{"id": doc.get("sha256"), "event": "PDF_FOUND", "at": None}]
        if doc.get("sha256") else [],
        "previous_events": [{"at": e.get("timestamp"), "text": e.get("event")} for e in events][-12:],
        "last_successful_event": last_ok,
        "first_failing_event": next((e["event"] for e in events if e.get("status") in
                                     ("FAILED", "BLOCKED")), state),
        "recovery_attempts": [{"action": "retry " + s, "result": "FAILED", "verified": None,
                               "error_class": category} for s, n in attempts.items()
                              for _ in range(max(0, int(n) - 1))],
        "recovery_result": None, "verification_result": email.get("confirmation") or
        ("Validation: " + ("passed" if (record.get("validation") or {}).get("passed") else
                           "failed" if record.get("validation") else "not run")),
        "classification": category, "classification_basis": "declared",
        "classification_label": label,
        "root_cause_status": "VERIFIED" if declared.get("category") or blocked else "UNKNOWN",
        "root_cause": declared.get("detail") or label,
        "facts": facts, "inferences": [], "unverified": unverified,
        "recommendations": recs, "learned": learned, "declared_cause": None,
        "headline": "PO {0}{1}: {2}{3}.".format(
            number + " · " if number else "", ref, label,
            " — not sent" if state not in ("EMAIL_SENT", "EMAIL_CONFIRMED") else ""),
        "impact": "No document was sent for {0}; nothing reached the recipient.".format(ref),
        "recovery_plan": {
            "status": "BUILT_IN_RULES" if retryable else "NO_VERIFIED_STRATEGY",
            "statement": ("The job retries this stage itself when the cause is transient "
                          "(the PO retry policy); it can be run again safely."
                          if retryable else
                          "No verified recovery strategy exists for this failure. A retry "
                          "cannot change it; a person decides."),
            "steps": [{"strategy": "run the job again", "source": "PO retry policy",
                       "reason": "the cause is transient", "evidence": category,
                       "expected": "the stage completes and the job continues",
                       "safety": "the same gate and ledger apply",
                       "executes": "when a person processes or sends it again"}]
            if retryable else [],
            "recommendations": recs, "recovery_class": None,
            "executes": "ATLAS never acts; a person runs the job again."},
        "learning_status": "Recorded as {0} — no positive learning credit; only a confirmed send "
                           "earns it.".format(state),
        "work": {"mode": "NEXT_RUN" if retryable else "NEEDS_DECISION",
                 "why": "a transient cause — run it again" if retryable else
                 "a retry would not change a {0}".format(category), "retried": False},
    }
    return item
