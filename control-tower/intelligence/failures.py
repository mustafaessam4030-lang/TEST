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
    "HUMAN_ACTION_REQUIRED", "SECURITY_VERIFICATION_REQUIRED", "UNKNOWN_FAILURE")

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
               "message": "the run's message"}

STAGES = {
    "navigation": "opening the carrier's tracking page",
    "carrier_lookup": "looking the shipment up on the carrier site",
    "human_verification": "the carrier's human verification",
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
}
FAILED_STATES = ("failed", "partial", "human_timeout")
# A skip is a failure when the run classified it as one. "No result" — the
# carrier publishes no date yet — is an expected outcome, not a failure.
EXPECTED_SKIPS = (None, "", "NO RESULT", "SUCCESS")


def _fact(text, source):
    return {"type": "FACT", "text": text, "source": source}


def _item(kind, text, source):
    return {"type": kind, "text": text, "source": source}


def is_failure(record):
    state = record.get("state")
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
    elif declared.get("observed"):
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
        if cause.get("name"):
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
    if root_status == "VERIFIED" and cause.get("stated_condition"):
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
    if status in ("NO_VERIFIED_STRATEGY", "NOT_RECOVERABLE"):
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
    if category == "CARRIER_POLICY_BLOCK":
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
