"""
The PO pipeline: HUB → PDF → EXTRACTION → VALIDATION → TEMPLATE → OUTPUT →
EMAIL → VERIFICATION → AUDIT → ATLAS.

    process(store, record, source, config)   up to EMAIL_PREPARED
    send(store, record, mailer, config, by)  EMAIL_SENDING → SENT → CONFIRMED

Every stage moves the record through store.transition(), which refuses any
step the state machine does not allow; every stage writes its event with
its evidence. A failure stops the job in its failure state with a
structured `failure` the ATLAS failure-intelligence pipeline reads.

RETRIES ARE STAGE-AWARE (RETRY_POLICY): retrieving the PDF and talking to
Graph are retried when the error is transient; validation, missing fields
and template problems never are — retrying cannot change a mismatch.

Nothing here claims a success it has not observed:
  VALIDATED           every blocking check passed
  TEMPLATE_GENERATED  the saved file was re-opened and every cell read back
  EMAIL_SENT          Graph answered 202 to the send
  EMAIL_CONFIRMED     the message was found in the mailbox's Sent Items
"""

import os
import time
from pathlib import Path

from . import doctypes, extract as X, store as S, template as T, validate as V
from .mail import MailError

RETRY_POLICY = {
    # stage: (max attempts, backoff seconds, retry when)
    "pdf_retrieval": (3, 2.0, "transient"),
    "email_send": (3, 3.0, "transient"),
    "extraction": (1, 0, None),
    "validation": (1, 0, None),
    "template": (1, 0, None),
}


class SourceError(Exception):
    """kind: 'not_found' | 'transient' | 'ambiguous' | 'permanent'."""

    def __init__(self, message, kind="permanent", candidates=None):
        Exception.__init__(self, message)
        self.kind = kind
        self.candidates = candidates or []


def failure(category, stage, message, **extra):
    """The structured failure ATLAS's failure intelligence reads (domain 'po')."""
    out = {"domain": "po", "category": category, "stage": stage, "detail": message}
    out.update(extra)
    return out


def config_from_env():
    return {
        "recipient": os.environ.get("PO_MAIL_RECIPIENT") or None,
        "sender": os.environ.get("PO_MAIL_SENDER") or None,
        "auto_send": os.environ.get("PO_AUTO_SEND", "0").strip().lower() in ("1", "true", "yes"),
        "defaults": {k: os.environ.get("PO_DEFAULT_" + k.upper()) or None
                     for k in ("supplier", "branch", "charge_to", "priority")},
        "confirm_wait_s": float(os.environ.get("PO_CONFIRM_WAIT_S") or 45),
    }


def _request_fields(doctype, request, config):
    """Operator- and configuration-supplied fields, typed like PDF fields."""
    out = {}
    for spec in doctype["fields"]:
        if spec["source"] == doctypes.PDF:
            continue
        given = (request or {}).get(spec["name"])
        value, origin = None, None
        if given not in (None, ""):
            value, origin = str(given).strip(), "request"
        elif spec["source"] == doctypes.CONFIG and (config.get("defaults") or {}).get(spec["name"]):
            value, origin = config["defaults"][spec["name"]], "configuration"
        out[spec["name"]] = {"name": spec["name"], "label": spec["label"], "kind": spec["kind"],
                             "status": X.FOUND if value else X.MISSING, "value": value,
                             "evidence": origin, "candidates": [], "origin": origin}
    return out


def _retry(stage, fn, store, record, sleep=time.sleep):
    attempts, backoff, when = RETRY_POLICY[stage]
    last = None
    for n in range(1, attempts + 1):
        try:
            return fn()
        except (SourceError, MailError) as error:
            last = error
            record.setdefault("attempts", {})[stage] = n
            if getattr(error, "kind", None) != when or n == attempts:
                raise
            store.event(record, "RETRY", stage, "RETRYING", attempt=n, of=attempts,
                        reason=str(error)[:200])
            sleep(backoff * n)
    raise last                                         # pragma: no cover


def process(store, record, source, config=None, sleep=time.sleep):
    """One job up to EMAIL_PREPARED (or its failure state). Returns the record."""
    config = config or config_from_env()
    doctype = doctypes.get(record["doctype"])
    reference = record["reference"]

    # ── eHUB: the record, its clearance status, Manage, Documents, Bill Entry
    record = store.transition(record, S.DISCOVERED, "Finding the record in eHub…")
    store.event(record, "PO_DISCOVERED", "discovery", "OK", reference=reference or None,
                mode="reference" if reference else "next Under Clearance record",
                doctype=doctype["id"])
    try:
        found = _retry("pdf_retrieval", lambda: source.fetch(reference), store, record, sleep)
    except SourceError as error:
        trail = getattr(error, "trail", None)
        record["discovery"] = trail
        _trail_events(store, record, trail)
        row = getattr(error, "row", None) or (trail or {}).get("ehub_record")
        if row and not record.get("reference"):
            _adopt_reference(record, row)
        if error.kind == "skipped":
            record = store.transition(record, S.SKIPPED, "Skipped — not Under Clearance", failure(
                "NOT_UNDER_CLEARANCE", "ehub_record", str(error),
                status=(row or {}).get("status")))
            store.event(record, "RECORD_SKIPPED", "ehub_record", "SKIPPED",
                        reason=str(error)[:300], ehub_status=(row or {}).get("status"))
            return record
        if error.kind == "review":
            record = store.transition(record, S.NEEDS_REVIEW, "Needs review", failure(
                "DOCUMENT_REVIEW_REQUIRED", "bill_entry", str(error),
                candidates=error.candidates[:10]))
            store.event(record, "DOCUMENT_REVIEW_REQUIRED", "bill_entry", "BLOCKED",
                        reason=str(error)[:300], candidates=error.candidates[:10])
            return record
        category = {"not_found": "DOCUMENT_NOT_FOUND", "no_bill_entry": "DOCUMENT_NOT_FOUND",
                    "ambiguous": "DOCUMENT_AMBIGUOUS",
                    "transient": "NETWORK_FAILURE"}.get(error.kind, "NAVIGATION_FAILURE")
        record = store.transition(record, S.PDF_NOT_FOUND, "Document not found",
                                  failure(category, "pdf_retrieval", str(error),
                                          candidates=error.candidates[:10]))
        store.event(record, "PDF_NOT_FOUND", "pdf_retrieval", "FAILED", reason=str(error)[:300],
                    kind=error.kind, candidates=error.candidates[:10])
        return record
    data = found["data"]
    sha, path = store.keep_document(data)
    record["hub"] = found.get("hub") or {}
    record["discovery"] = found.get("trail")
    if not record.get("reference"):
        _adopt_reference(record, record["hub"])
    # The identifier eHub's own document name carries is this job's number
    # from here on; the PDF's declaration number is checked against it.
    record["identifier"] = found.get("identifier")
    record["number"] = found.get("identifier") or record.get("number")
    _trail_events(store, record, found.get("trail"))
    record["document"] = {"filename": found.get("filename"), "source": found.get("origin"),
                          "url": found.get("url"), "sha256": sha, "bytes": len(data),
                          "evidence": str(path),
                          "retrieved_at": S.now_iso(),
                          "method": ((found.get("trail") or {}).get("download") or {}).get(
                              "method"),
                          "served_filename": ((found.get("trail") or {}).get("download") or {})
                          .get("served_filename")}
    record = store.transition(record, S.PDF_FOUND, "Reading the PDF…")
    store.event(record, "PDF_FOUND", "pdf_retrieval", "OK", evidence=sha,
                filename=found.get("filename"), bytes=len(data), origin=found.get("origin"),
                identifier=found.get("identifier"))

    # ── READ ──────────────────────────────────────────────────────────────
    try:
        read = X.read_pdf(data)
    except X.Unreadable as error:
        record = store.transition(record, S.PDF_UNREADABLE, "The PDF could not be read",
                                  failure("PDF_UNREADABLE", "pdf_read", str(error)))
        store.event(record, "PDF_UNREADABLE", "pdf_read", "FAILED", evidence=sha,
                    reason=str(error)[:300])
        return record
    record["document"].update(pages=read["pages"], read_methods=read["methods"],
                              chars=read["chars"])
    record = store.transition(record, S.PDF_READ, "Extracting fields…")
    store.event(record, "PDF_READ", "pdf_read", "OK", evidence=sha, pages=read["pages"],
                methods=read["methods"])

    # ── EXTRACT ───────────────────────────────────────────────────────────
    fields = X.extract(read["text"], doctype)
    if not X.looks_like(fields):
        record["fields"] = fields
        record = store.transition(record, S.EXTRACTION_FAILED, "Not a {0}".format(
            doctype["document"]), failure(
                "DATA_EXTRACTION_FAILURE", "extraction",
                "The PDF does not read as a {0}: fewer than two of its key fields were "
                "found.".format(doctype["document"])))
        store.event(record, "EXTRACTION_FAILED", "extraction", "FAILED", evidence=sha,
                    found=[n for n, f in fields.items() if f["status"] == X.FOUND])
        return record
    request_fields = _request_fields(doctype, record.get("request"), config)
    record["fields"] = fields
    record["request_fields"] = request_fields
    record["number"] = record.get("identifier") or \
        fields.get(doctype["number_field"], {}).get("value")
    record = store.transition(record, S.FIELDS_EXTRACTED, "Validating against the Hub…")
    store.event(record, "FIELDS_EXTRACTED", "extraction", "OK", evidence=sha,
                found=[n for n, f in fields.items() if f["status"] == X.FOUND],
                missing=[n for n, f in fields.items() if f["status"] == X.MISSING],
                ambiguous=[n for n, f in fields.items() if f["status"] == X.AMBIGUOUS])

    # ── VALIDATE (the gate) ───────────────────────────────────────────────
    record = store.transition(record, S.VALIDATING)
    store.event(record, "VALIDATION_STARTED", "validation", "RUNNING")
    result = V.validate(doctype, fields, record["hub"], request_fields)
    record["validation"] = result
    if not result["passed"]:
        record["email"] = {"status": "BLOCKED", "reasons": result["reasons"],
                           "recipient": config.get("recipient")}
        mismatch = next((c for c in result["checks"] if c["status"] == "MISMATCH"), None)
        record = store.transition(record, S.VALIDATION_FAILED, "Validation failed", failure(
            "VALIDATION_FAILURE", "validation", "; ".join(result["reasons"]),
            mismatch={"label": mismatch["label"], "pdf": mismatch["pdf"], "hub": mismatch["hub"]}
            if mismatch else None))
        store.event(record, "VALIDATION_FAILED", "validation", "FAILED",
                    reasons=result["reasons"])
        store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=result["reasons"])
        return record
    record = store.transition(record, S.VALIDATED, "Generating the document…")
    store.event(record, "VALIDATION_PASSED", "validation", "OK",
                checks=[{"label": c["label"], "status": c["status"]} for c in result["checks"]])

    # ── TEMPLATE → OUTPUT ─────────────────────────────────────────────────
    store.event(record, "TEMPLATE_GENERATION_STARTED", "template", "RUNNING",
                template=doctype["template"]["version"])
    values = {n: f["value"] for n, f in fields.items() if f["status"] == X.FOUND}
    values.update({n: f["value"] for n, f in request_fields.items() if f["status"] == X.FOUND})
    values["request_reference"] = (record.get("request") or {}).get("reference_note") or None
    try:
        out = T.fill(doctype, values, store.output_dir, record["number"], reference,
                     job=record["po_id"])
    except Exception as error:
        record = store.transition(record, S.TEMPLATE_FAILED, "Template generation failed",
                                  failure("TEMPLATE_FAILURE", "template", str(error)[:400]))
        store.event(record, "TEMPLATE_FAILED", "template", "FAILED", reason=str(error)[:300])
        return record
    out["generated_by"] = record.get("started_by")
    out["generated_at"] = S.now_iso()
    record["output"] = out
    record = store.transition(record, S.TEMPLATE_GENERATED, "Preparing the email…")
    store.event(record, "TEMPLATE_GENERATED", "template", "OK", evidence=out["sha256"],
                filename=out["filename"], template=out["template_version"],
                bytes=out["bytes"])

    # ── EMAIL: prepared, never sent from here unless configured to ────────
    return prepare(store, record, config)


def prepare(store, record, config):
    doctype = doctypes.get(record["doctype"])
    recipient = config.get("recipient")
    subject = doctype["email_subject"].format(number=record.get("number"),
                                              reference=record["reference"])
    email = {"recipient": recipient, "sender": config.get("sender"), "subject": subject,
             "attachment": record["output"]["filename"], "status": "READY",
             "template_version": record["output"]["template_version"]}
    if not recipient:
        email.update(status="BLOCKED", reasons=["No recipient is configured (PO_MAIL_RECIPIENT)."])
        record["email"] = email
        store.save(record)
        store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=email["reasons"])
        return record
    record["email"] = email
    record = store.transition(record, S.EMAIL_PREPARED, "Ready to send")
    store.event(record, "EMAIL_PREPARED", "email", "OK", recipient=recipient, subject=subject,
                attachment=email["attachment"])
    return record


TRAIL_EVENTS = {"ehub_record": "EHUB_RECORD_FOUND", "clearance_status": "CLEARANCE_CHECKED",
                "manage": "MANAGE_OPENED", "documents_section": "DOCUMENTS_SECTION_FOUND",
                "bill_entry": "BILL_ENTRY_FOUND", "identifier": "IDENTIFIER_EXTRACTED",
                "download": "BILL_ENTRY_DOWNLOADED"}


def _trail_events(store, record, trail):
    """One event per discovery step, as the trail recorded it."""
    for step in (trail or {}).get("steps") or []:
        name = TRAIL_EVENTS.get(step.get("step"))
        if not name:
            continue
        detail = {k: v for k, v in step.items() if k not in ("step", "ok", "at")}
        store.event(record, name, "discovery", "OK" if step.get("ok") else "FAILED",
                    at=step.get("at"), **detail)


def _adopt_reference(record, row):
    """A job started as 'the next Under Clearance record' takes that record's BOL/AWB."""
    record["reference"] = row.get("bol_awb") or record.get("reference")
    record["po_key"] = S.po_key(record["doctype"], record["reference"])


def blocked_reasons(store, record):
    """Why this record may not be sent now — the gate before any Graph call."""
    reasons = []
    if not (record.get("validation") or {}).get("passed"):
        reasons.append("validation has not passed")
    out = record.get("output") or {}
    if not out.get("verified"):
        reasons.append("no verified output document")
    elif not Path(out.get("path") or "").is_file():
        reasons.append("the output file {0} no longer exists".format(out.get("filename")))
    else:
        import hashlib
        if hashlib.sha256(Path(out["path"]).read_bytes()).hexdigest() != out.get("sha256"):
            reasons.append("the output file changed after it was generated")
    if not (record.get("email") or {}).get("recipient"):
        reasons.append("no recipient is configured")
    if record["state"] not in (S.EMAIL_PREPARED, S.EMAIL_FAILED):
        reasons.append("the job is {0}, not ready to send".format(S.LABELS.get(record["state"])))
    if record["state"] == S.EMAIL_FAILED and (record.get("failure") or {}).get("kind") == \
            "permanent":
        reasons.append("the last send failed permanently: {0}".format(
            record["failure"].get("detail")))
    return reasons


def send(store, record, mailer, by=None, authorize_resend=False, reason=None,
         confirm_wait_s=None, sleep=time.sleep):
    """
    EMAIL_PREPARED → EMAIL_SENDING → EMAIL_SENT → EMAIL_CONFIRMED.
    Returns (record, outcome) — outcome is SENT, CONFIRMED, BLOCKED or FAILED.
    """
    reasons = blocked_reasons(store, record)
    if reasons:
        store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=reasons)
        return record, "BLOCKED"
    email = record["email"]
    key = store.ledger_key(record["po_key"], record["document"]["sha256"],
                           record["output"]["template_version"], email["recipient"])
    previous = store.sent_before(key)
    resending_own = previous and previous.get("po_id") == record["po_id"] and \
        previous.get("status") in ("FAILED", "SENDING")
    if previous and previous.get("status") in ("SENT", "CONFIRMED", "SENDING") and \
            not resending_own and not authorize_resend:
        why = ("This document was already sent to {0} on {1} (job {2}). It is not sent again "
               "unless a resend is explicitly authorized.".format(
                   email["recipient"], previous.get("at"), previous.get("po_id")))
        email.update(status="BLOCKED", reasons=[why], duplicate_of=previous.get("po_id"))
        store.save(record)
        store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=[why], duplicate=True,
                    previous_po=previous.get("po_id"))
        return record, "BLOCKED"
    store.ledger_write(key, {"status": "SENDING", "at": S.now_iso(), "po_id": record["po_id"],
                             "by": by, "recipient": email["recipient"],
                             "authorized_resend": bool(authorize_resend and previous),
                             "resend_reason": reason if authorize_resend else None})
    record = store.transition(record, S.EMAIL_SENDING, "Sending…")
    store.event(record, "EMAIL_SEND_STARTED", "email", "RUNNING", recipient=email["recipient"],
                authorized_resend=bool(authorize_resend and previous), by=by)

    def fail(error):
        kind = getattr(error, "kind", "permanent")
        r = store.transition(record, S.EMAIL_FAILED, "Email failed", failure(
            "EMAIL_FAILURE", "email", str(error)[:300], kind=kind, step=getattr(error, "step", None),
            http_status=getattr(error, "status", None)))
        r["email"].update(status="FAILED", error=str(error)[:300])
        store.save(r)
        store.ledger_write(key, {"status": "FAILED", "at": S.now_iso(), "po_id": r["po_id"],
                                 "by": by, "recipient": email["recipient"]})
        store.event(r, "EMAIL_FAILED", "email", "FAILED", kind=kind,
                    step=getattr(error, "step", None), http_status=getattr(error, "status", None),
                    reason=str(error)[:300])
        return r, "FAILED"

    # 1. The draft — reused if an earlier attempt of this job created one.
    try:
        if not email.get("message_id"):
            data = Path(record["output"]["path"]).read_bytes()
            text = ("Please find attached the {0} for {1} ({2}).\n\nGenerated by ATA PO "
                    "Automation from the document attached to the Hub shipment, validated "
                    "against the Hub, template {3}.\n").format(
                        doctypes.get(record["doctype"])["label"], record.get("number"),
                        record["reference"], record["output"]["template_version"])
            created = _retry("email_send", lambda: mailer.create(
                email["recipient"], email["subject"], text, record["output"]["filename"], data),
                store, record, sleep)
            email.update(message_id=created["message_id"],
                         internet_message_id=created["internet_message_id"])
            store.save(record)
    except MailError as error:
        return fail(error)

    # 2. The send. An unknown outcome is checked in Sent Items before any retry;
    #    resending the same draft cannot duplicate — it no longer exists once sent.
    try:
        try:
            accepted = mailer.send(email["message_id"])
        except MailError as error:
            if error.kind not in ("unknown", "transient"):
                raise
            found = mailer.find_sent(email.get("internet_message_id"))
            if found:
                accepted = {"accepted": True, "http_status": None, "found_after_error": True}
            else:
                store.event(record, "RETRY", "email_send", "RETRYING", attempt=1,
                            reason=str(error)[:200])
                sleep(RETRY_POLICY["email_send"][1])
                accepted = mailer.send(email["message_id"])
    except MailError as error:
        return fail(error)
    email.update(status="SENT", sent_at=S.now_iso(), graph_status=accepted.get("http_status"))
    record = store.transition(record, S.EMAIL_SENT, "Sent — confirming delivery…")
    store.ledger_write(key, {"status": "SENT", "at": S.now_iso(), "po_id": record["po_id"],
                             "by": by, "recipient": email["recipient"],
                             "internet_message_id": email.get("internet_message_id")})
    store.event(record, "EMAIL_SENT", "email", "OK", recipient=email["recipient"],
                subject=email["subject"], attachment=email["attachment"],
                graph_status=accepted.get("http_status"), by=by)

    # 3. Confirmation in Sent Items.
    try:
        found = mailer.confirm(email.get("internet_message_id"),
                               wait_s=confirm_wait_s if confirm_wait_s is not None else 45)
    except MailError as error:
        found = None
        email["confirm_error"] = str(error)[:200]
    if not found:
        email["status"] = "SENT"
        email["confirmation"] = "Accepted by Microsoft 365; not yet found in Sent Items."
        store.save(record)
        return record, "SENT"
    email.update(status="CONFIRMED", confirmed_at=found.get("sentDateTime") or S.now_iso(),
                 confirmation="Found in the mailbox's Sent Items.")
    record = store.transition(record, S.EMAIL_CONFIRMED, "Completed")
    store.ledger_write(key, {"status": "CONFIRMED", "at": S.now_iso(), "po_id": record["po_id"],
                             "by": by, "recipient": email["recipient"],
                             "internet_message_id": email.get("internet_message_id")})
    store.event(record, "EMAIL_CONFIRMED", "email", "OK",
                sent_at=found.get("sentDateTime"), recipient=email["recipient"])
    store.event(record, "PO_COMPLETED", "complete", "OK", recipient=email["recipient"],
                template=record["output"]["template_version"])
    return record, "CONFIRMED"


def abandon(store, record, reason):
    """A job whose worker died mid-way ends in the failure state its stage allows."""
    to = {S.QUEUED: S.PDF_NOT_FOUND, S.DISCOVERED: S.PDF_NOT_FOUND,
          S.PDF_FOUND: S.PDF_UNREADABLE, S.PDF_READ: S.EXTRACTION_FAILED,
          S.FIELDS_EXTRACTED: None, S.VALIDATING: S.VALIDATION_FAILED,
          S.VALIDATED: S.TEMPLATE_FAILED, S.EMAIL_SENDING: S.EMAIL_FAILED}.get(record["state"])
    if record["state"] == S.FIELDS_EXTRACTED:
        record = store.transition(record, S.VALIDATING)
        to = S.VALIDATION_FAILED
    if to is None:
        return record
    record = store.transition(record, to, "Stopped", failure(
        "UNKNOWN_FAILURE", record["state"].lower(), reason, kind="unknown"))
    return record
