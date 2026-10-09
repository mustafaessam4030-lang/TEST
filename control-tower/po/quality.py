"""
How reliable PO Automation actually is — computed from the jobs on record,
nothing else.

Every rate is (numerator, denominator, percent) over the jobs that reached
the stage before it; a rate with no denominator is None, never a number made
up to fill the table. Jobs whose document was observed in the real eHub
session (provenance REAL / VERIFIED) are counted apart from every other job
(TEST, SIMULATED, UNKNOWN), so a stand-in run can never make the real
figures look better.
"""

from . import store as S


def _reached(record, state):
    return any(h.get("state") == state for h in record.get("history") or []) or \
        record.get("state") == state


def _rate(num, den):
    return {"n": num, "of": den, "pct": round(100.0 * num / den, 1) if den else None}


def _events_by_job(store):
    by = {}
    for e in store.events(limit=100000):
        by.setdefault(e.get("po_id"), []).append(e)
    return by


def _block(records, events):
    attempted = [r for r in records if _reached(r, S.DISCOVERED)]
    not_eligible = [r for r in attempted if r.get("skip_reason") in
                    ("SKIPPED_NOT_UNDER_CLEARANCE", "SKIPPED_STATUS_CHANGED")]
    eligible = [r for r in attempted if r not in not_eligible]
    found = [r for r in eligible if _reached(r, S.PDF_FOUND)]
    managed = [r for r in eligible if ((r.get("discovery") or {}).get("manage"))]
    read = [r for r in found if _reached(r, S.PDF_READ)]
    extracted = [r for r in read if _reached(r, S.FIELDS_EXTRACTED)]
    validating = [r for r in extracted if _reached(r, S.VALIDATING)]
    validated = [r for r in validating if _reached(r, S.VALIDATED)]
    saved = [r for r in validated if _reached(r, S.SAVED)]
    submitted = [r for r in saved if _reached(r, S.EMAIL_SENDING)]
    confirmed = [r for r in submitted if r["state"] == S.EMAIL_CONFIRMED]

    def clean_run(r):
        names = {e.get("event") for e in events.get(r["po_id"], [])}
        return not names & {"RETRY", "RESUMED", "NEEDS_REVIEW", "DOCUMENT_REVIEW_REQUIRED"}
    first_pass = [r for r in eligible if r["state"] in (S.EMAIL_CONFIRMED, S.EMAIL_PREPARED,
                                                        S.EMAIL_SENT) and clean_run(r)]
    human = [r for r in eligible if r["state"] in (S.NEEDS_REVIEW, S.DOCUMENT_AMBIGUOUS,
                                                   S.AUTH_REQUIRED, S.EMAIL_UNKNOWN) or
             any((e.get("event") == "RESUMED" and e.get("stage") == "review") or
                 e.get("event") == "REVIEW_RESOLVED" for e in events.get(r["po_id"], []))]
    duplicates_blocked = sum(1 for r in attempted if r.get("skip_reason") == "SKIPPED_DUPLICATE") \
        + sum(1 for evs in events.values() for e in evs
              if e.get("event") == "EMAIL_BLOCKED" and (e.get("metadata") or {}).get("duplicate"))
    resumed = [r for r in attempted if any(e.get("event") == "RESUMED" and e.get("stage") ==
                                           "recovery" for e in events.get(r["po_id"], []))]
    recovered = [r for r in resumed if r["state"] not in S.FAILED_STATES +
                 (S.WORKER_DISCONNECTED,)]
    totals = [sum((r.get("timings") or {}).values()) for r in saved if r.get("timings")]
    stages = {}
    for r in attempted:
        for k, v in (r.get("timings") or {}).items():
            stages.setdefault(k, []).append(v)
    by_code = {}
    for r in attempted:
        code = (r.get("failure") or {}).get("code") if r["state"] in S.FAILED_STATES + (
            S.NEEDS_REVIEW, S.EMAIL_UNKNOWN, S.WORKER_DISCONNECTED) else None
        if code:
            by_code[code] = by_code.get(code, 0) + 1
    pdf_failed = [r for r in eligible if r["state"] in (S.PDF_UNREADABLE, S.PDF_DOWNLOAD_FAILED)]
    pdf_tried = [r for r in eligible if _reached(r, S.PDF_FOUND) or r in pdf_failed]
    email_failed = [r for r in submitted if r["state"] in (S.EMAIL_FAILED,
                                                           S.EMAIL_RECONCILIATION_FAILED)]
    email_unknown = [r for r in submitted if r["state"] == S.EMAIL_UNKNOWN or
                     any(e.get("event") == "EMAIL_UNKNOWN" for e in events.get(r["po_id"], []))]
    retried = [r for r in attempted if any(e.get("event") == "RETRY"
                                           for e in events.get(r["po_id"], []))]
    by_state = {}
    for r in records:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    return {
        "jobs": len(attempted), "not_eligible": len(not_eligible), "eligible": len(eligible),
        "queue_depth": by_state.get(S.QUEUED, 0),
        "active": {k: v for k, v in sorted(by_state.items()) if k in S.ACTIVE_STATES},
        "by_state": dict(sorted(by_state.items())),
        "needs_review": by_state.get(S.NEEDS_REVIEW, 0) + by_state.get(S.DOCUMENT_AMBIGUOUS, 0),
        "pdf_failure": _rate(len(pdf_failed), len(pdf_tried)),
        "extraction_failure": _rate(len(read) - len(extracted) +
                                    len([r for r in extracted if r["state"] ==
                                         S.EXTRACTION_FAILED]), len(read)),
        "validation_failure": _rate(len([r for r in validating if r["state"] in (
            S.VALIDATION_FAILED, S.IDENTITY_MISMATCH)]), len(validating)),
        "email_failure": _rate(len(email_failed), len(submitted)),
        "email_unknown": _rate(len(email_unknown), len(submitted)),
        "retry": _rate(len(retried), len(attempted)),
        # Discovery worked when eHub was read and Manage reached the record —
        # whatever the record then held (a missing Bill Entry is not a
        # discovery failure).
        "discovery_success": _rate(len([r for r in eligible if r["state"] not in (
            S.DISCOVERY_FAILED, S.AUTH_REQUIRED, S.MANAGE_NAVIGATION_FAILED, S.QUEUED,
            S.DISCOVERED) and not (r["state"] == S.WORKER_DISCONNECTED and (
                r.get("interrupted") or {}).get("state") in (S.QUEUED, S.DISCOVERED))]),
            len(eligible)),
        "document_discovery_success": _rate(len([r for r in managed if _reached(r, S.PDF_FOUND)]),
                                            len(managed)),
        "extraction_success": _rate(len(extracted), len(read)),
        "validation_pass": _rate(len(validated), len(validating)),
        "template_success": _rate(len(saved), len(validated)),
        "email_success": _rate(len(confirmed), len(submitted)),
        "first_pass_success": _rate(len(first_pass), len(eligible)),
        "human_intervention": _rate(len(human), len(eligible)),
        "duplicates_prevented": duplicates_blocked,
        "recovery_success": _rate(len(recovered), len(resumed)),
        "avg_processing_ms": round(sum(totals) / len(totals)) if totals else None,
        "avg_stage_ms": {k: round(sum(v) / len(v)) for k, v in sorted(stages.items())},
        "failures_by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
    }


def metrics(store, limit=5000):
    records = store.all(limit)
    events = _events_by_job(store)
    real = [r for r in records if (r.get("provenance") or {}).get("source") == "REAL" and
            (r.get("provenance") or {}).get("verification") == "VERIFIED"]
    other = [r for r in records if r not in real]
    return {"real": _block(real, events), "not_real": _block(other, events),
            "note": "Computed from recorded jobs only. 'real' = documents observed in the real "
                    "eHub session; 'not_real' = test, stand-in or unverified jobs. A rate with "
                    "nothing to divide by is null."}
