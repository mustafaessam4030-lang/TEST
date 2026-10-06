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
  VALIDATED           the validation gate's decision is VALID
  TEMPLATE_GENERATED  the filled template was re-opened and every cell read back
  SAVED               the file is in the output folder, re-opened from disk and
                      read back again, with its SHA-256
  EMAIL_SENT          Graph answered 202 to the send
  EMAIL_CONFIRMED     the message was found in the mailbox's Sent Items and its
                      recipient, subject and attachment are this job's

Each canonical milestone (store.CANONICAL) is recorded with its evidence at
the point it is proven; the store refuses any state whose milestones are
missing, so the order below is enforced by the state layer, not only here:

  DISCOVERED → ELIGIBLE_VERIFIED → MANAGE_OPENED → (SHIPMENT_IDENTITY_VERIFIED)
  → DOCUMENT_DISCOVERED → DOCUMENT_SELECTED → PDF_RETRIEVED
  → PDF_INTEGRITY_VERIFIED → FIELDS_EXTRACTED → FIELDS_NORMALIZED
  → CROSS_VALIDATED → BUSINESS_RULES_VALIDATED → TEMPLATE_GENERATED
  → TEMPLATE_VERIFIED → OUTPUT_PERSISTED → EMAIL_PREPARED
  → IDEMPOTENCY_CONFIRMED → EMAIL_SUBMITTED → EMAIL_RECONCILING
  → EMAIL_CONFIRMED → COMPLETED
"""

import os
import re
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
        # Pilot: the job stops once the output is saved and read back (SAVED);
        # no email is prepared, so none can be sent.
        "email_disabled": os.environ.get("PO_NO_EMAIL", "0").strip().lower() in
        ("1", "true", "yes"),
    }


def _request_fields(doctype, request, config):
    """Configuration fields (supplier, branch…), typed like PDF fields — the
    operator may override them per job. Never G4: see _g4_source."""
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


def _g4_norm(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper()).lstrip("0")


def _g4_source(doctype, request, printed):
    """
    The supplier invoice No. (G4) and, when it is not proven, why.
    -> (field, issue | None)

    G4 comes ONLY from the Bill of Entry: the value printed under an explicit
    "Invoice No." / "Invoice Number" label (extract.printed_invoice_no). Never
    eHub's UNA+ column, shipment metadata, a search value, an operator's typed
    value, a file name or a model.

      one well-formed printed value      → G4 (origin bill_of_entry)
      the job's own value disagrees      → issue "conflict" (review)
      several different printed values   → issue "ambiguous" (review)
      the label printed, value malformed → issue "malformed" (review)
      no explicit Invoice No. printed    → issue "absent" (review)
      a reviewer chose one of the PRINTED values → G4 (origin
        bill_of_entry:chosen_at_review); a value that is not printed on the
        document can never be chosen
    """
    spec = doctypes.field(doctype, "invoice_no")
    base = {"name": "invoice_no", "label": spec["label"], "kind": "text", "candidates": [],
            "status": X.MISSING, "value": None, "origin": None, "evidence": None,
            "page": printed.get("page"), "method": None}
    expected = str(request.get("invoice_no") or "").strip() or None
    value, many = printed.get("value"), list(printed.get("candidates") or [])
    bad = printed.get("malformed") or []
    printed_values = ([value] if value else []) + many
    chosen = request.get("g4_choice")
    if chosen and chosen in printed_values:
        return dict(base, status=X.FOUND, value=chosen, raw=chosen,
                    origin="bill_of_entry:chosen_at_review",
                    evidence="printed on the Bill of Entry; chosen at review by {0}".format(
                        request.get("g4_choice_by") or "a reviewer"),
                    note="one of the values the Bill of Entry prints, chosen by a person"), None
    if bad:
        return dict(base, candidates=[{"value": None, "raw": b["raw"], "line": b["line"]}
                                      for b in bad],
                    note="the Invoice No. label is printed but its value is malformed"), {
            "reason": "malformed", "candidates": many,
            "detail": "The Bill of Entry prints an Invoice No. label whose value is not a clean "
                      "invoice number ({0}); it is not read, trimmed or guessed.".format(
                          ", ".join("'{0}'".format(b["raw"]) for b in bad[:3]))}
    if value:
        field = dict(base, status=X.FOUND, value=value, raw=value, origin="bill_of_entry",
                     evidence="printed on the Bill of Entry: {0}".format(printed.get("evidence")),
                     note="explicit 'Invoice No.' on the Bill of Entry")
        if expected and _g4_norm(expected) != _g4_norm(value):
            field["note"] = "the job gives {0}; the Bill of Entry prints {1}".format(expected, value)
            return field, {"reason": "conflict", "candidates": [value],
                           "detail": "Supplier invoice No. (G4) disagrees: the job gives {0}, "
                                     "the Bill of Entry prints Invoice No. {1}.".format(
                                         expected, value)}
        if expected:
            field["note"] = "explicit 'Invoice No.' on the Bill of Entry; matches the job's value"
        return field, None
    if many:
        return dict(base, candidates=[{"value": v} for v in many],
                    note="the Bill of Entry prints several invoice numbers — none chosen"), {
            "reason": "ambiguous", "candidates": many,
            "detail": "The Bill of Entry prints several different invoice numbers ({0}); G4 is "
                      "not chosen from them automatically.".format(" / ".join(many))}
    return dict(base, note="the Bill of Entry prints no explicit 'Invoice No.'"), {
        "reason": "absent", "candidates": [],
        "detail": "The Bill of Entry prints no explicit 'Invoice No.'. G4 is not filled from "
                  "anything else (eHub's UNA+ column, the job, a guess) — never."}


def _same_declaration(a, b):
    whole = X.normal_reference(a)
    base = X.normal_reference(str(a).split("/")[0])
    return X.normal_reference(b) in (whole, base) or X.normal_reference(a) == \
        X.normal_reference(str(b).split("/")[0])


def identity_decision(hub, fields=None):
    """
    Whose shipment this is, decided from evidence — MATCH, MISMATCH or
    INSUFFICIENT_EVIDENCE — with the evidence and the reason.

    MISMATCH  any contradiction: the Manage page shows another BOL/AWB; the
              Manage page's declaration is not the selected Bill Entry's; the
              PDF's BL/AWB is not the row's; the PDF's declaration is not the
              eHub document's identifier.
    MATCH     no contradiction, the PDF's BL/AWB is the row's, AND either the
              Manage page itself shows the row's BOL/AWB or the PDF's
              declaration is the eHub Bill Entry identifier.
              (Before the PDF is read — fields None — the Manage page showing
              the row's BOL/AWB is MATCH for the page.)
    otherwise INSUFFICIENT_EVIDENCE — never treated as MATCH.
    """
    hub = hub or {}
    row = hub.get("bol_awb")
    doc_id = hub.get("identifier")
    on_page = hub.get("identity_on_manage")
    evidence, contradictions = [], []
    if hub.get("identity_conflict"):
        contradictions.append("the Manage page shows {0}, not {1}".format(
            hub["identity_conflict"], row))
    if on_page is True:
        evidence.append("the Manage page shows {0}".format(row))
    page_decl = hub.get("manage_declaration")
    if page_decl and doc_id:
        if _same_declaration(page_decl, doc_id):
            evidence.append("the Manage page's declaration {0} is the Bill Entry's".format(
                page_decl))
        else:
            contradictions.append("the Manage page's declaration {0} is not the Bill Entry's "
                                  "{1}".format(page_decl, doc_id))
    doc_proof = None
    if fields is not None:
        bl = (fields.get("bl_awb") or {})
        decl = (fields.get("document_number") or {})
        bl_v = bl.get("value") if bl.get("status") == X.FOUND else None
        decl_v = decl.get("value") if decl.get("status") == X.FOUND else None
        if bl_v and row:
            if X.normal_reference(bl_v) == X.normal_reference(row):
                evidence.append("the PDF's BL/AWB {0} is the row's".format(bl_v))
            else:
                contradictions.append("the PDF's BL/AWB {0} is not the row's {1}".format(bl_v, row))
        if decl_v and doc_id:
            if _same_declaration(decl_v, doc_id):
                evidence.append("the PDF's declaration {0} is eHub's Bill Entry {1}".format(
                    decl_v, doc_id))
            else:
                contradictions.append("the PDF's declaration {0} is not eHub's Bill Entry "
                                      "{1}".format(decl_v, doc_id))
        bl_ok = bool(bl_v and row and X.normal_reference(bl_v) == X.normal_reference(row))
        decl_ok = bool(decl_v and doc_id and _same_declaration(decl_v, doc_id))
        doc_proof = bl_ok and (on_page is True or decl_ok)
    if contradictions:
        return {"decision": "MISMATCH", "evidence": evidence, "contradictions": contradictions,
                "why": "; ".join(contradictions), "stage": "document" if fields else "manage"}
    if fields is None:
        if on_page is True:
            return {"decision": "MATCH", "evidence": evidence, "contradictions": [],
                    "why": "the Manage page shows the row's BOL/AWB", "stage": "manage"}
        return {"decision": "INSUFFICIENT_EVIDENCE", "evidence": evidence, "contradictions": [],
                "why": "the Manage page does not show the row's BOL/AWB; the document must "
                       "prove it", "stage": "manage"}
    if doc_proof:
        return {"decision": "MATCH", "evidence": evidence, "contradictions": [],
                "why": "; ".join(evidence), "stage": "document"}
    return {"decision": "INSUFFICIENT_EVIDENCE", "evidence": evidence, "contradictions": [],
            "why": "not enough to tie the document to the shipment: the PDF's BL/AWB must be "
                   "the row's, and the Manage page must show it or the PDF's declaration must "
                   "be eHub's Bill Entry identifier", "stage": "document"}


def review_brief(record, code, detail):
    """What a person reviewing the job reads — never 'something went wrong'."""
    printed = record.get("invoice_candidate") or {}
    actions = {
        "G4_SOURCE_UNPROVEN": ["choose one of the values the Bill of Entry prints"
                               if (printed.get("candidates") or printed.get("value")) else None,
                               "fetch the document again once it is corrected in eHub",
                               "reject the job"],
        "IDENTITY_UNPROVEN": ["fetch the document again", "reject the job"],
        "LOW_CONFIDENCE_EXTRACTION": ["confirm the OCR-read values against the kept PDF",
                                      "fetch a text PDF again", "reject the job"],
    }.get(code, ["reject the job"])
    return {
        "what_happened": "Validation stopped at NEEDS REVIEW ({0}).".format(code),
        "why": detail,
        "evidence": {"document": (record.get("document") or {}).get("evidence"),
                     "document_sha256": (record.get("document") or {}).get("sha256"),
                     "printed_invoice": printed or None,
                     "identity": record.get("identity")},
        "missing": detail,
        "operator_action": [a for a in actions if a],
        "after_resolution": "Validation runs again in full from the stored document; nothing is "
                            "generated or sent until it is VALID. A rejected job ends; nothing "
                            "is sent.",
    }


def _retry(stage, fn, store, record, sleep=time.sleep):
    """Bounded retries with exponential backoff, each one recorded; only for
    the failure kind the stage's policy names (transient)."""
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
            wait = max(backoff * (2 ** (n - 1)), float(getattr(error, "retry_after", 0) or 0))
            store.event(record, "RETRY", stage, "RETRYING", attempt=n, of=attempts,
                        wait_s=round(wait, 1), reason=str(error)[:200])
            sleep(wait)
    raise last                                         # pragma: no cover


def _timed(record, stage, started):
    """How long a stage took, kept on the job (milliseconds, summed over retries/resumes)."""
    ms = int((time.monotonic() - started) * 1000)
    timings = record.setdefault("timings", {})
    timings[stage] = timings.get(stage, 0) + ms
    return ms


def _stop(store, record, state, code, stage, message, event, event_status="FAILED",
          category=None, **extra):
    """One precise stop: state, code, reason, evidence pointer, next action."""
    record = store.transition(record, state, S.LABELS.get(state, state).capitalize(),
                              failure(category or code, stage, message, code=code,
                                      next_action=S.NEXT_ACTION.get(state), **extra))
    store.event(record, event, stage, event_status, reason=str(message)[:300], code=code,
                **{k: v for k, v in extra.items() if k in ("candidates", "kind", "duplicate_of",
                                                            "skip_reason", "ehub_status")})
    return record


# A SourceError's stage decides the precise state; a source that names no
# stage (a stand-in, an older source) keeps the original mapping.
STAGE_STATE = {"auth": S.AUTH_REQUIRED, "ehub_record": S.DISCOVERY_FAILED,
               "manage": S.MANAGE_NAVIGATION_FAILED, "identity": S.MANAGE_NAVIGATION_FAILED,
               "documents_section": S.PDF_NOT_FOUND, "bill_entry": S.PDF_NOT_FOUND,
               "download": S.PDF_DOWNLOAD_FAILED}


def _source_failure(store, record, error):
    trail = getattr(error, "trail", None)
    record["discovery"] = trail
    record["provenance"] = (trail or {}).get("provenance") or \
        {"source": "UNKNOWN", "verification": "UNVERIFIED", "why": "no discovery trail"}
    _trail_events(store, record, trail)
    row = getattr(error, "row", None) or (trail or {}).get("ehub_record")
    if row and not record.get("reference"):
        _adopt_reference(record, row)
    kind, stage = error.kind, getattr(error, "stage", None)
    if kind == "skipped":
        reason = getattr(error, "skip_reason", None) or "SKIPPED_NOT_UNDER_CLEARANCE"
        record["skip_reason"] = reason
        return _stop(store, record, S.SKIPPED, reason, stage or "ehub_record", str(error),
                     "RECORD_SKIPPED", "SKIPPED", category="NOT_UNDER_CLEARANCE",
                     skip_reason=reason,
                     ehub_status=(row or {}).get("status"), status=(row or {}).get("status"))
    if kind == "review":
        return _stop(store, record, S.DOCUMENT_AMBIGUOUS, "DOCUMENT_AMBIGUOUS", "bill_entry",
                     str(error), "DOCUMENT_REVIEW_REQUIRED", "BLOCKED",
                     candidates=error.candidates[:10])
    if kind == "auth":
        return _stop(store, record, S.AUTH_REQUIRED, "AUTH_REQUIRED", "auth", str(error),
                     "AUTH_REQUIRED", "BLOCKED")
    if kind == "identity_mismatch":
        record["identity"] = getattr(error, "identity", None) or {
            "decision": "MISMATCH", "why": str(error), "stage": "manage"}
        return _stop(store, record, S.IDENTITY_MISMATCH, "IDENTITY_MISMATCH", "identity",
                     str(error), "IDENTITY_MISMATCH", "FAILED")
    if kind == "unreadable":
        record = store.transition(record, S.PDF_UNREADABLE, "The PDF could not be trusted",
                                  failure("PDF_UNREADABLE", "download", str(error),
                                          code="PDF_UNREADABLE",
                                          next_action=S.NEXT_ACTION[S.PDF_UNREADABLE]))
        store.event(record, "PDF_UNREADABLE", "download", "FAILED", reason=str(error)[:300])
        return record
    if stage in STAGE_STATE and kind not in ("not_found", "no_bill_entry"):
        state = STAGE_STATE[stage]
        code = {S.AUTH_REQUIRED: "AUTH_REQUIRED", S.DISCOVERY_FAILED: "DISCOVERY_FAILED",
                S.MANAGE_NAVIGATION_FAILED: "MANAGE_NAVIGATION_FAILED",
                S.PDF_NOT_FOUND: "DOCUMENT_NOT_FOUND",
                S.PDF_DOWNLOAD_FAILED: "PDF_DOWNLOAD_FAILED"}[state]
        return _stop(store, record, state, code, stage, str(error), "PDF_NOT_FOUND",
                     kind=kind, candidates=error.candidates[:10])
    category = {"not_found": "DOCUMENT_NOT_FOUND", "no_bill_entry": "DOCUMENT_NOT_FOUND",
                "ambiguous": "DOCUMENT_AMBIGUOUS",
                "transient": "NETWORK_FAILURE"}.get(kind, "NAVIGATION_FAILURE")
    record = store.transition(record, S.PDF_NOT_FOUND, "Document not found",
                              failure(category, "pdf_retrieval", str(error),
                                      candidates=error.candidates[:10], code="DOCUMENT_NOT_FOUND"
                                      if category == "DOCUMENT_NOT_FOUND" else category,
                                      next_action=S.NEXT_ACTION[S.PDF_NOT_FOUND]))
    store.event(record, "PDF_NOT_FOUND", "pdf_retrieval", "FAILED", reason=str(error)[:300],
                kind=kind, candidates=error.candidates[:10])
    return record


def process(store, record, source, config=None, sleep=time.sleep):
    """One job up to EMAIL_PREPARED (or its precise stop). Returns the record."""
    config = config or config_from_env()
    doctype = doctypes.get(record["doctype"])
    reference = record["reference"]
    record.setdefault("correlation_id", record["po_id"])
    if os.environ.get("ATA_WORKER_ID"):
        record["worker_id"] = os.environ["ATA_WORKER_ID"]

    # ── eHUB: the record, its clearance status, Manage, Documents, Bill Entry
    if record.get("milestones"):
        S.Store.reset_milestones(record, "rediscover")
    record["identity"] = None
    record = store.transition(record, S.DISCOVERED, "Finding the record in eHub…")
    store.milestone(record, "DISCOVERED", mode="reference" if reference else
                    "next Under Clearance record", reference=reference or None)
    store.event(record, "PO_DISCOVERED", "discovery", "OK", reference=reference or None,
                mode="reference" if reference else "next Under Clearance record",
                doctype=doctype["id"])
    t0 = time.monotonic()
    try:
        found = _retry("pdf_retrieval", lambda: source.fetch(reference), store, record, sleep)
    except SourceError as error:
        _timed(record, "discovery", t0)
        return _source_failure(store, record, error)
    _timed(record, "discovery", t0)
    data = found["data"]
    sha, path = store.keep_document(data)
    record["hub"] = found.get("hub") or {}
    record["discovery"] = found.get("trail")
    # Where this document came from, decided by what the browser observed:
    # REAL / VERIFIED only from the real eHub session.
    record["provenance"] = (found.get("trail") or {}).get("provenance") or \
        {"source": "UNKNOWN", "verification": "UNVERIFIED", "why": "no discovery trail"}
    if not record.get("reference"):
        _adopt_reference(record, record["hub"])
    # The identifier eHub's own document name carries is this job's number
    # from here on; the PDF's declaration number is checked against it.
    record["identifier"] = found.get("identifier")
    record["number"] = found.get("identifier") or record.get("number")
    _trail_events(store, record, found.get("trail"))
    missing = _discovery_milestones(store, record, found, sha, data)
    if missing:
        if missing[0] == "IDENTITY_MISMATCH":
            return _stop(store, record, S.IDENTITY_MISMATCH, "IDENTITY_MISMATCH", "identity",
                         missing[1], "IDENTITY_MISMATCH", "FAILED")
        return _stop(store, record, S.DISCOVERY_FAILED, "SOURCE_EVIDENCE_MISSING", "discovery",
                     "The source did not prove every discovery step, so nothing is read: "
                     "{0}.".format(missing[1]), "PDF_NOT_FOUND")
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

    # ── IDEMPOTENCY: one shipment + Bill Entry + document → one job ──────
    key = store.idempotency_key(record["doctype"], record["reference"], record.get("identifier"),
                                sha)
    record["idempotency_key"] = key
    ok, holder = store.claim(key, record["po_id"])
    if not ok:
        record["skip_reason"] = "SKIPPED_DUPLICATE"
        record["duplicate_of"] = holder.get("po_id")
        store.event(record, "DUPLICATE_DETECTED", "idempotency", "BLOCKED",
                    duplicate_of=holder.get("po_id"), holder_state=holder.get("state"))
        return _stop(store, record, S.SKIPPED, "SKIPPED_DUPLICATE", "idempotency",
                     "This Bill of Entry ({0}, same document) is already handled by job {1} "
                     "({2}); nothing was generated again.".format(
                         record.get("identifier") or record["reference"], holder.get("po_id"),
                         S.LABELS.get(holder.get("state"), holder.get("state"))),
                     "RECORD_SKIPPED", "SKIPPED", skip_reason="SKIPPED_DUPLICATE",
                     duplicate_of=holder.get("po_id"))
    return from_document(store, record, data, config)


def _discovery_milestones(store, record, found, sha, data):
    """
    The discovery milestones, each from the source's own evidence (its trail):
    ELIGIBLE_VERIFIED, MANAGE_OPENED, SHIPMENT_IDENTITY_VERIFIED (when the
    Manage page itself proves it), DOCUMENT_DISCOVERED, DOCUMENT_SELECTED,
    PDF_RETRIEVED. -> None, or (kind, what is missing) — a step the source did
    not evidence is never assumed.
    """
    from .ehub import REQUIRED_STATUS, status_ok
    trail = found.get("trail") or {}
    hub = found.get("hub") or {}
    steps = {st.get("step"): st for st in trail.get("steps") or []}
    clearance = trail.get("clearance") or {}
    if not (clearance.get("ok") and status_ok(clearance.get("found"))):
        return "MISSING", "no observed '{0}' status for the record (found: {1!r})".format(
            REQUIRED_STATUS, clearance.get("found"))
    store.milestone(record, "ELIGIBLE_VERIFIED", save=False, observed_status=clearance["found"],
                    required=REQUIRED_STATUS, source="eHub shipment list",
                    view=(trail.get("ehub_record") or {}).get("view"),
                    shipment=(trail.get("ehub_record") or {}).get("bol_awb") or hub.get("bol_awb"),
                    at=(steps.get("clearance_status") or steps.get("ehub_record") or {}).get("at"),
                    page_status=(trail.get("identity") or {}).get("status"))
    manage = trail.get("manage")
    if not manage:
        return "MISSING", "no Manage page was evidenced"
    store.milestone(record, "MANAGE_OPENED", save=False, url=manage.get("url"),
                    at=(steps.get("manage") or {}).get("at"))
    page = identity_decision(hub)
    record["identity"] = page
    if page["decision"] == "MISMATCH":
        store.save(record)
        return "IDENTITY_MISMATCH", "The Manage page that opened is not {0}'s: {1}".format(
            hub.get("bol_awb"), page["why"])
    if page["decision"] == "MATCH":
        store.milestone(record, "SHIPMENT_IDENTITY_VERIFIED", save=False, decision="MATCH",
                        stage="manage", evidence=page["evidence"])
    bill = trail.get("bill_entry") or {}
    selected = bill.get("selected") or found.get("filename")
    candidates = bill.get("candidates") or ([{"name": selected}] if bill.get("selected") else [])
    if not candidates or not selected:
        return "MISSING", "no Bill Entry document was evidenced in Manage → Documents"
    store.milestone(record, "DOCUMENT_DISCOVERED", save=False,
                    candidates=[c.get("name") for c in candidates][:10])
    store.milestone(record, "DOCUMENT_SELECTED", save=False, selected=selected,
                    identifier=found.get("identifier"), rule=bill.get("rule"))
    download = trail.get("download") or {}
    store.milestone(record, "PDF_RETRIEVED", save=False, sha256=sha, bytes=len(data),
                    method=download.get("method"), content_type=download.get("content_type"),
                    served_filename=download.get("served_filename"))
    return None


def from_document(store, record, data, config=None):
    """PDF_FOUND → read → extract → validate → template → save → prepare."""
    config = config or config_from_env()
    doctype = doctypes.get(record["doctype"])
    sha = record["document"]["sha256"]
    if "PDF_RETRIEVED" not in S.Store.milestones(record):
        # Resuming from the kept PDF: it is the same bytes, proven by its hash.
        import hashlib
        if hashlib.sha256(data).hexdigest() != sha:
            raise S.InvariantViolation("the kept PDF does not match its recorded SHA-256")
        store.milestone(record, "PDF_RETRIEVED", save=False, sha256=sha, bytes=len(data),
                        method="kept evidence")

    # ── READ (with the integrity checks) ─────────────────────────────────
    t0 = time.monotonic()
    try:
        read = X.read_pdf(data)
    except X.Unreadable as error:
        _timed(record, "read", t0)
        record = store.transition(record, S.PDF_UNREADABLE, "The PDF could not be read",
                                  failure("PDF_UNREADABLE", "pdf_read", str(error),
                                          code="PDF_UNREADABLE",
                                          next_action=S.NEXT_ACTION[S.PDF_UNREADABLE]))
        store.event(record, "PDF_UNREADABLE", "pdf_read", "FAILED", evidence=sha,
                    reason=str(error)[:300])
        return record
    _timed(record, "read", t0)
    record["document"].update(pages=read["pages"], read_methods=read["methods"],
                              chars=read["chars"], integrity=read.get("integrity"))
    store.milestone(record, "PDF_INTEGRITY_VERIFIED", save=False, **read.get("integrity"))
    record = store.transition(record, S.PDF_READ, "Extracting fields…")
    store.event(record, "PDF_READ", "pdf_read", "OK", evidence=sha, pages=read["pages"],
                methods=read["methods"])

    # ── EXTRACT ───────────────────────────────────────────────────────────
    t0 = time.monotonic()
    fields = X.extract(read["text"], doctype, read.get("spans"))
    _timed(record, "extraction", t0)
    if not X.looks_like(fields):
        record["fields"] = fields
        record = store.transition(record, S.EXTRACTION_FAILED, "Not a {0}".format(
            doctype["document"]), failure(
                "DATA_EXTRACTION_FAILURE", "extraction",
                "The PDF does not read as a {0}: fewer than two of its key fields were "
                "found.".format(doctype["document"]), code="EXTRACTION_FAILED",
                next_action=S.NEXT_ACTION[S.EXTRACTION_FAILED]))
        store.event(record, "EXTRACTION_FAILED", "extraction", "FAILED", evidence=sha,
                    found=[n for n, f in fields.items() if f["status"] == X.FOUND])
        return record
    # The supplier invoice No. (G4): ONLY an explicit "Invoice No." /
    # "Invoice Number" printed on the Bill of Entry. eHub's "UNA+ Invoice
    # Number" column is never used — not even as a fallback.
    record["invoice_candidate"] = X.printed_invoice_no(read["text"], read.get("spans")) or None
    record["fields"] = fields
    record["number"] = record.get("identifier") or \
        fields.get(doctype["number_field"], {}).get("value")
    store.milestone(record, "FIELDS_EXTRACTED", save=False,
                    fields={n: {k: f.get(k) for k in ("status", "raw", "value", "page", "method",
                                                      "confidence")}
                            for n, f in fields.items() if n != "vat_lines"},
                    vat_lines=(fields.get("vat_lines") or {}).get("status"))
    record = store.transition(record, S.FIELDS_EXTRACTED, "Validating against the Hub…")
    store.event(record, "FIELDS_EXTRACTED", "extraction", "OK", evidence=sha,
                found=[n for n, f in fields.items() if f["status"] == X.FOUND],
                missing=[n for n, f in fields.items() if f["status"] == X.MISSING],
                ambiguous=[n for n, f in fields.items() if f["status"] == X.AMBIGUOUS],
                malformed=[n for n, f in fields.items() if f["status"] == X.MALFORMED])
    # ── NORMALISE: a printed value that is not well-formed stops here ─────
    bad = {n: f for n, f in fields.items() if f["status"] == X.MALFORMED}
    if bad:
        detail = "; ".join("{0}: {1}".format(f["label"], f.get("note")) for f in bad.values())
        record = store.transition(record, S.EXTRACTION_FAILED, "A value is malformed", failure(
            "NORMALIZATION_FAILED", "normalization",
            "The Bill of Entry prints values that are not well-formed numbers — nothing is "
            "coerced: " + detail, code="NORMALIZATION_FAILED", fields=sorted(bad),
            next_action="Read the kept PDF: the value is printed but malformed. Have the "
                        "Bill of Entry corrected in eHub; nothing is guessed."))
        store.event(record, "EXTRACTION_FAILED", "normalization", "FAILED", evidence=sha,
                    reason=detail[:300], code="NORMALIZATION_FAILED")
        return record
    store.milestone(record, "FIELDS_NORMALIZED", save=False,
                    normalized={n: f.get("value") for n, f in fields.items()
                                if f["status"] == X.FOUND and n != "vat_lines"},
                    grammar="ICUMS en-GH: comma thousands, dot decimal")
    record = store.transition(record, S.VALIDATING)
    return validate_onward(store, record, config)


def validate_onward(store, record, config=None):
    """VALIDATING → VALIDATED → TEMPLATE_GENERATED → SAVED → EMAIL_PREPARED, from the
    job's stored fields — the same path for a first run, a resume, or a review."""
    config = config or config_from_env()
    doctype = doctypes.get(record["doctype"])
    fields = record["fields"]
    request = dict(record.get("request") or {})
    # A validation proves everything after it again.
    S.Store.reset_milestones(record, "revalidate")
    request_fields = _request_fields(doctype, request, config)
    g4, g4_issue = _g4_source(doctype, request, record.get("invoice_candidate") or {})
    request_fields["invoice_no"] = g4
    record["request_fields"] = request_fields
    record["provenance_map"] = provenance_map(doctype, fields, request_fields)
    identity = identity_decision(record.get("hub"), fields)
    record["identity"] = identity

    # ── VALIDATE (the one gate) ───────────────────────────────────────────
    store.event(record, "VALIDATION_STARTED", "validation", "RUNNING")
    t0 = time.monotonic()
    result = V.validate(doctype, fields, record["hub"], request_fields, identity=identity,
                        g4_issue=g4_issue, ocr_confirmed=bool(request.get("ocr_confirmed_by")))
    _timed(record, "validation", t0)
    record["validation"] = result
    record["calculations"] = result["calculations"]
    if identity["decision"] == "MATCH" and \
            "SHIPMENT_IDENTITY_VERIFIED" not in S.Store.milestones(record):
        store.milestone(record, "SHIPMENT_IDENTITY_VERIFIED", save=False, decision="MATCH",
                        stage=identity.get("stage"), evidence=identity.get("evidence"))
    cross = [c for c in result["comparisons"] if c["result"] != "MATCH" and
             c["field"] != "BOL/AWB shown on the Manage page"]
    if identity["decision"] == "MATCH" and not [c for c in cross
                                                if c["result"] == "MISMATCH"] and \
            not any(n.startswith("hub:") for n in result["failed_checks"]):
        store.milestone(record, "CROSS_VALIDATED", save=False, comparisons=result["comparisons"])
    if result["decision"] == V.NEEDS_REVIEW:
        record["email"] = {"status": "BLOCKED", "reasons": result["reasons"],
                           "recipient": config.get("recipient")}
        detail = (g4_issue or {}).get("detail") if result["code"] == "G4_SOURCE_UNPROVEN" else \
            "; ".join(result["reasons"])
        record["review"] = review_brief(record, result["code"], detail)
        record = store.transition(record, S.NEEDS_REVIEW, "Needs review: {0}".format(
            result["code"].replace("_", " ").lower()),
            failure(result["code"], "validation", detail + " Every other check passed.",
                    code=result["code"], g4_reason=(g4_issue or {}).get("reason"),
                    candidates=(g4_issue or {}).get("candidates"),
                    remediation=result["remediation"],
                    next_action=S.NEXT_ACTION[S.NEEDS_REVIEW]))
        store.event(record, "NEEDS_REVIEW", "validation", "BLOCKED", reason=detail[:300],
                    code=result["code"], g4_reason=(g4_issue or {}).get("reason"))
        store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=result["reasons"])
        return record
    if result["decision"] == V.INVALID:
        record["email"] = {"status": "BLOCKED", "reasons": result["reasons"],
                           "recipient": config.get("recipient")}
        mismatch = next((c for c in result["checks"] if c["status"] == "MISMATCH"), None)
        state = S.IDENTITY_MISMATCH if result["code"] == "IDENTITY_MISMATCH" else \
            S.VALIDATION_FAILED
        record = store.transition(record, state, "Validation failed" if state ==
                                  S.VALIDATION_FAILED else "Identity mismatch", failure(
            "VALIDATION_FAILURE", "validation", "; ".join(result["reasons"]),
            code=result["code"], remediation=result["remediation"],
            failed_checks=result["failed_checks"],
            next_action=S.NEXT_ACTION[state],
            mismatch={"label": mismatch["label"], "pdf": mismatch["pdf"], "hub": mismatch["hub"]}
            if mismatch else None))
        store.event(record, "VALIDATION_FAILED", "validation", "FAILED",
                    reasons=result["reasons"], code=result["code"])
        store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=result["reasons"])
        return record
    store.milestone(record, "BUSINESS_RULES_VALIDATED", save=False,
                    decision=result["decision"], calculations=result["calculations"],
                    g4_origin=g4.get("origin"))
    record = store.transition(record, S.VALIDATED, "Generating the document…")
    store.event(record, "VALIDATION_PASSED", "validation", "OK",
                checks=[{"label": c["label"], "status": c["status"]} for c in result["checks"]])

    # ── TEMPLATE → OUTPUT ─────────────────────────────────────────────────
    store.event(record, "TEMPLATE_GENERATION_STARTED", "template", "RUNNING",
                template=doctype["template"]["version"])
    values = {n: f["value"] for n, f in fields.items() if f["status"] == X.FOUND}
    values.update({n: f["value"] for n, f in request_fields.items() if f["status"] == X.FOUND})
    values["request_reference"] = (record.get("request") or {}).get("reference_note") or None
    t0 = time.monotonic()
    try:
        built = T.build(doctype, values, trace={"job": record["po_id"],
                                               "document": record["document"]["sha256"],
                                               "reference": record["reference"]})
    except Exception as error:
        _timed(record, "template", t0)
        record = store.transition(record, S.TEMPLATE_FAILED, "Template generation failed",
                                  failure("TEMPLATE_FAILURE", "template", str(error)[:400],
                                          code="TEMPLATE_FAILED",
                                          next_action=S.NEXT_ACTION[S.TEMPLATE_FAILED]))
        store.event(record, "TEMPLATE_FAILED", "template", "FAILED", reason=str(error)[:300])
        return record
    _timed(record, "template", t0)
    record["template"] = {"version": built["template_version"],
                          "template_sha256": built["template_sha256"], "cells": built["cells"],
                          "generated_at": S.now_iso(), "read_back": "in memory, every cell",
                          "sha256": built["sha256"]}
    store.milestone(record, "TEMPLATE_GENERATED", save=False, template=built["template_version"],
                    sha256=built["sha256"], cells=len(built["cells"]))
    # The read-back build() already did: every written cell equals its
    # validated value and every template formula is intact (else it raised).
    store.milestone(record, "TEMPLATE_VERIFIED", save=False,
                    checked=sorted(built["cells"]) + sorted(doctype["template"]["formulas"]),
                    method="re-opened from the generated bytes")
    record = store.transition(record, S.TEMPLATE_GENERATED, "Saving the document…")
    store.event(record, "TEMPLATE_GENERATED", "template", "OK", template=built["template_version"],
                cells=len(built["cells"]))

    # ── SAVE: to the configured output folder, read back from disk ─────────
    t0 = time.monotonic()
    try:
        out = T.save(doctype, built, store.output_dir, record["number"], record["reference"],
                     job=record["po_id"])
    except Exception as error:
        _timed(record, "save", t0)
        record = store.transition(record, S.SAVE_FAILED, "The document could not be saved",
                                  failure("SAVE_FAILED", "output",
                                          "saving to {0} failed: {1}".format(
                                              store.output_dir, str(error)[:300]),
                                          code="SAVE_FAILED",
                                          next_action=S.NEXT_ACTION[S.SAVE_FAILED]))
        store.event(record, "TEMPLATE_FAILED", "output", "FAILED", reason=str(error)[:300])
        return record
    _timed(record, "save", t0)
    out["generated_by"] = record.get("started_by")
    out["generated_at"] = record["template"]["generated_at"]
    record["output"] = out
    store.milestone(record, "OUTPUT_PERSISTED", save=False, path=out["path"], bytes=out["bytes"],
                    sha256=out["sha256"], job=record["po_id"], method="atomic publish, re-opened "
                    "from disk and read back")
    record = store.transition(record, S.SAVED, "Preparing the email…")
    store.event(record, "OUTPUT_SAVED", "output", "OK", evidence=out["sha256"],
                filename=out["filename"], folder=out["folder"], template=out["template_version"],
                bytes=out["bytes"])

    # ── EMAIL: prepared, never sent from here unless configured to ────────
    return prepare(store, record, config)


def provenance_map(doctype, fields, request_fields):
    """Field → raw text → normalised value → template cell → validation rule. No
    silent transformation: every value that reaches a cell is traceable here."""
    cells = {m["field"]: m["cell"] for m in doctype["mapping"]}
    rules = {}
    for spec in doctype["fields"]:
        rules[spec["name"]] = ["required" if spec["required"] else "optional"]
    for check in doctype["hub_checks"]:
        rules.setdefault(check["pdf"], []).append("must equal eHub {0}".format(check["hub"]))
    for name in ("cif_usd", "exchange_rate", "duty_amount_ghs"):
        rules.setdefault(name, []).append("> 0")
    rules.setdefault("duty_amount_ghs", []).append(
        "total duty − VAT block = import duty line (±{0:.2f})".format(
            doctype.get("duty_tolerance", 1.0)))
    out = []
    for name, f in list(fields.items()) + list(request_fields.items()):
        out.append({"field": name, "label": f.get("label"), "source": f.get("origin") or "pdf",
                    "raw": f.get("raw") if f.get("kind") != "lines" else None,
                    "evidence": f.get("evidence"), "normalized": f.get("value"),
                    "status": f.get("status"), "cell": cells.get(name),
                    "rules": rules.get(name, []), "note": f.get("note")})
    return out


def prepare(store, record, config):
    if config.get("email_disabled"):
        # Pilot / no-email run: stop at SAVED. Nothing is prepared, so the
        # Send button and auto-send have nothing to act on.
        record["email"] = {"status": "BLOCKED", "recipient": None,
                           "reasons": ["Email is disabled for this run (pilot): the job stops "
                                       "once the output is saved, for a field-by-field review."]}
        store.save(record)
        store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=record["email"]["reasons"],
                    pilot=True)
        return record
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
    store.milestone(record, "EMAIL_PREPARED", save=False, recipient=recipient, subject=subject,
                    attachment=email["attachment"], attachment_sha256=record["output"]["sha256"])
    record = store.transition(record, S.EMAIL_PREPARED, "Ready to send")
    store.event(record, "EMAIL_PREPARED", "email", "OK", recipient=recipient, subject=subject,
                attachment=email["attachment"])
    return record


TRAIL_EVENTS = {"ehub_record": "EHUB_RECORD_FOUND", "clearance_status": "CLEARANCE_CHECKED",
                "manage": "MANAGE_OPENED", "identity": "IDENTITY_CHECKED",
                "documents_section": "DOCUMENTS_SECTION_FOUND",
                "bill_entry": "BILL_ENTRY_FOUND", "identifier": "IDENTIFIER_EXTRACTED",
                "download": "BILL_ENTRY_DOWNLOADED", "auth": "AUTH_REQUIRED"}


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


# Graph takes a file attachment inline up to 3 MB; larger needs an upload
# session, which this sender does not do — refused before anything is sent.
MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024


def blocked_reasons(store, record, authorize_resend=False):
    """Why this record may not be sent now — the gate before any Graph call."""
    reasons = []
    prov = record.get("provenance") or {}
    if prov.get("verification") != "VERIFIED" and \
            os.environ.get("PO_ALLOW_TEST_SEND") != "1":
        # A document that was not observed in the real eHub session is never
        # emailed for real. PO_ALLOW_TEST_SEND=1 exists for the test suite's
        # stand-in mailbox only.
        reasons.append("the document's source is {0} / {1} — only a document observed in the "
                       "real eHub session is sent".format(prov.get("source", "UNKNOWN"),
                                                          prov.get("verification", "UNVERIFIED")))
    if not (record.get("validation") or {}).get("passed") or \
            (record.get("validation") or {}).get("decision") != "VALID":
        reasons.append("validation has not passed (decision {0})".format(
            (record.get("validation") or {}).get("decision")))
    out = record.get("output") or {}
    if not out.get("verified"):
        reasons.append("no verified output document")
    elif not Path(out.get("path") or "").is_file():
        reasons.append("the output file {0} no longer exists".format(out.get("filename")))
    else:
        import hashlib
        data = Path(out["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != out.get("sha256"):
            reasons.append("the output file changed after it was generated")
        elif len(data) > MAX_ATTACHMENT_BYTES:
            reasons.append("the output is {0:,} bytes — over the 3 MB Graph attachment limit; "
                           "not sent".format(len(data)))
        else:
            # The attachment must be THIS job's document: re-opened from disk,
            # its declaration, invoice No. and duty are this job's own values.
            reasons += T.verify_output(record)
    email = record.get("email") or {}
    if not email.get("recipient"):
        reasons.append("no recipient is configured")
    if email.get("attachment") and out.get("filename") and email["attachment"] != out["filename"]:
        reasons.append("the prepared attachment name is not this job's output")
    if email.get("subject") and (str(record.get("number") or "") not in email["subject"] or
                                 str(record.get("reference") or "") not in email["subject"]):
        reasons.append("the prepared subject does not name this job's declaration and shipment")
    sendable = (S.EMAIL_PREPARED, S.EMAIL_FAILED) + \
        ((S.EMAIL_SENT, S.EMAIL_CONFIRMED) if authorize_resend else ())
    if record["state"] not in sendable:
        reasons.append("the job is {0}, not ready to send".format(S.LABELS.get(record["state"])))
    if record["state"] == S.EMAIL_FAILED and (record.get("failure") or {}).get("kind") == \
            "permanent":
        reasons.append("the last send failed permanently: {0}".format(
            record["failure"].get("detail")))
    return reasons


def send(store, record, mailer, by=None, authorize_resend=False, reason=None,
         confirm_wait_s=None, sleep=time.sleep):
    """
    The email as a distributed transaction:

      EMAIL_PREPARED → [ledger reserved: IDEMPOTENCY_CONFIRMED] → EMAIL_SENDING
      → draft created → [EMAIL_SUBMITTED, recorded BEFORE the send call]
      → send → EMAIL_SENT (Graph 202: EMAIL_RECONCILING)
      → reconciled in Sent Items, recipient / subject / attachment verified
      → EMAIL_CONFIRMED (+ COMPLETED)

    An outcome that is not known (timeout, gateway error, lost response) is
    EMAIL_UNKNOWN and is reconciled — never resent blindly. A throttled or
    unavailable Graph (429 / 503) is retried within the policy, honouring
    Retry-After, and only on the SAME draft once it is proven still a draft.
    Returns (record, outcome): SENT, CONFIRMED, BLOCKED, FAILED, UNKNOWN or
    MISMATCH.
    """
    reasons = blocked_reasons(store, record, authorize_resend)
    if reasons:
        store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=reasons)
        return record, "BLOCKED"
    email = record["email"]
    if authorize_resend and record["state"] in (S.EMAIL_SENT, S.EMAIL_CONFIRMED):
        # A new submission of the same document: a new draft; the earlier
        # send is kept on the job.
        email.setdefault("previous_sends", []).append(
            {k: email.get(k) for k in ("internet_message_id", "sent_at", "confirmed_at",
                                       "status", "reconciliation")})
        for k in ("message_id", "internet_message_id", "sent_at", "confirmed_at",
                  "confirmation", "graph_status", "accepted_evidence", "reconciliation"):
            email.pop(k, None)
        S.Store.reset_milestones(record, "resend")
        record["attempt"] = int(record.get("attempt") or 1) + 1
    key = store.ledger_key(record["po_key"], record["document"]["sha256"],
                           record["output"]["template_version"], email["recipient"])
    # Atomic across processes: only one sender for this document + recipient.
    ok, previous = store.reserve(key, record["po_id"], by, authorize=bool(authorize_resend),
                                 extra={"recipient": email["recipient"],
                                        "resend_reason": reason if authorize_resend else None})
    if not ok:
        why = ("This document was already {0} to {1} on {2} (job {3}). It is not sent again "
               "unless a resend is explicitly authorized.".format(
                   "sent" if previous.get("status") in ("SENT", "CONFIRMED") else
                   "being sent" if previous.get("status") == "SENDING" else
                   "submitted with an unconfirmed outcome", email["recipient"],
                   previous.get("at"), previous.get("po_id")))
        email.update(status="BLOCKED", reasons=[why], duplicate_of=previous.get("po_id"))
        store.save(record)
        store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=[why], duplicate=True,
                    previous_po=previous.get("po_id"))
        return record, "BLOCKED"
    if authorize_resend and previous:
        store.ledger_write(key, {"status": "SENDING", "at": S.now_iso(), "po_id": record["po_id"],
                                 "by": by, "recipient": email["recipient"],
                                 "authorized_resend": True, "resend_reason": reason})
    store.milestone(record, "IDEMPOTENCY_CONFIRMED", save=False, ledger_key=key,
                    previous=(previous or {}).get("status"),
                    authorized_resend=bool(authorize_resend and previous), by=by)
    record = store.transition(record, S.EMAIL_SENDING, "Submitting to Microsoft 365…", actor=by)
    store.event(record, "EMAIL_SEND_STARTED", "email", "RUNNING", recipient=email["recipient"],
                authorized_resend=bool(authorize_resend and previous), by=by)
    t0 = time.monotonic()

    def fail(error):
        kind = getattr(error, "kind", "permanent")
        _timed(record, "email", t0)
        r = store.transition(record, S.EMAIL_FAILED, "Email failed", failure(
            "EMAIL_FAILURE", "email", str(error)[:300], kind=kind, step=getattr(error, "step", None),
            http_status=getattr(error, "status", None),
            code="EMAIL_SUBMISSION_FAILED" if getattr(error, "step", None) == "send" else
            "EMAIL_PREPARATION_FAILED" if getattr(error, "step", None) == "create" else
            "EMAIL_FAILED",
            request_id=getattr(error, "request_id", None),
            next_action=S.NEXT_ACTION[S.EMAIL_FAILED]))
        r["email"].update(status="FAILED", error=str(error)[:300])
        store.save(r)
        store.ledger_write(key, {"status": "FAILED", "at": S.now_iso(), "po_id": r["po_id"],
                                 "by": by, "recipient": email["recipient"]})
        store.event(r, "EMAIL_FAILED", "email", "FAILED", kind=kind,
                    step=getattr(error, "step", None), http_status=getattr(error, "status", None),
                    reason=str(error)[:300])
        return r, "FAILED"

    def unknown(why):
        _timed(record, "email", t0)
        r = store.transition(record, S.EMAIL_UNKNOWN, "Email outcome unknown", failure(
            "EMAIL_UNKNOWN", "email", why, kind="unknown", code="EMAIL_UNKNOWN",
            next_action=S.NEXT_ACTION[S.EMAIL_UNKNOWN]))
        r["email"].update(status="UNKNOWN", error=why[:300])
        store.save(r)
        store.ledger_write(key, {"status": "UNKNOWN", "at": S.now_iso(), "po_id": r["po_id"],
                                 "by": by, "recipient": email["recipient"],
                                 "internet_message_id": email.get("internet_message_id")})
        store.event(r, "EMAIL_UNKNOWN", "email", "UNKNOWN", reason=why[:300])
        return r, "UNKNOWN"

    # 1. The draft — reused if an earlier attempt of this job created one.
    try:
        if not email.get("message_id"):
            data = Path(record["output"]["path"]).read_bytes()
            text = ("Please find attached the {0} for {1} ({2}).\n\nGenerated by ATA PO "
                    "Automation from the document attached to the Hub shipment, validated "
                    "against the Hub, template {3}. Job {4}.\n").format(
                        doctypes.get(record["doctype"])["label"], record.get("number"),
                        record["reference"], record["output"]["template_version"],
                        record["po_id"])
            created = _retry("email_send", lambda: mailer.create(
                email["recipient"], email["subject"], text, record["output"]["filename"], data),
                store, record, sleep)
            email.update(message_id=created["message_id"],
                         internet_message_id=created["internet_message_id"],
                         draft_request_id=created.get("request_id"),
                         attachment_bytes=len(data))
            store.save(record)
    except MailError as error:
        return fail(error)

    # 2. The send. Recorded as SUBMITTED before the call: a crash from here on
    #    is reconciled, never resent blindly.
    store.milestone(record, "EMAIL_SUBMITTED", message_id=email.get("message_id"),
                    internet_message_id=email.get("internet_message_id"))
    try:
        accepted = mailer.send(email["message_id"])
    except MailError as error:
        if error.kind not in ("unknown", "transient"):
            return fail(error)
        store.event(record, "EMAIL_UNKNOWN", "email", "CHECKING", reason=str(error)[:200],
                    http_status=getattr(error, "status", None))
        state = _reconcile_state(mailer, email, wait_s=min(20.0, confirm_wait_s or 20.0))
        if state == "sent":
            accepted = {"accepted": True, "http_status": None, "found_after_error": True}
        elif state == "draft":
            try:
                wait = max(RETRY_POLICY["email_send"][1],
                           float(getattr(error, "retry_after", 0) or 0))
                store.event(record, "RETRY", "email_send", "RETRYING", attempt=1, wait_s=wait,
                            reason="the draft is still a draft: it was not sent")
                sleep(wait)
                accepted = mailer.send(email["message_id"])
            except MailError as again:
                if again.kind == "permanent":
                    return fail(again)
                return unknown("the send was submitted twice without a confirmed outcome: "
                               "{0}".format(str(again)[:200]))
        else:
            return unknown("Microsoft 365 gave no answer to the send ({0}), the message is not "
                           "in Sent Items yet and the draft is no longer a draft — it may have "
                           "been sent".format(str(error)[:160]))
    email.update(status="SENT", sent_at=S.now_iso(), graph_status=accepted.get("http_status"),
                 accepted_evidence={"http_status": accepted.get("http_status"),
                                    "found_after_error": bool(accepted.get("found_after_error")),
                                    "request_id": accepted.get("request_id"),
                                    "at": S.now_iso()})
    store.milestone(record, "EMAIL_RECONCILING", save=False,
                    accepted=email["accepted_evidence"])
    record = store.transition(record, S.EMAIL_SENT, "Accepted — confirming delivery…", actor=by)
    store.ledger_write(key, {"status": "SENT", "at": S.now_iso(), "po_id": record["po_id"],
                             "by": by, "recipient": email["recipient"],
                             "internet_message_id": email.get("internet_message_id")})
    store.event(record, "EMAIL_SENT", "email", "OK", recipient=email["recipient"],
                subject=email["subject"], attachment=email["attachment"],
                graph_status=accepted.get("http_status"), by=by)

    # 3. Reconciliation: the message in Sent Items, and it is THIS job's.
    _timed(record, "email", t0)
    store.save(record)
    record, outcome = confirm(store, record, mailer,
                              wait_s=confirm_wait_s if confirm_wait_s is not None else 45, by=by)
    return record, outcome


def _verify_message(record, found, mailer):
    """
    The message found in Sent Items is the one this job prepared: its
    recipient, subject, attachment name and size. -> (verified, details):
    True (it is), False (it is not — mismatches listed), or None (it could not
    be read back: NOT verified, and not a mismatch either — never assumed).
    """
    email = record.get("email") or {}
    inspect = getattr(mailer, "inspect", None)
    details = {"message_id": found.get("id"), "internet_message_id":
               found.get("internetMessageId") or email.get("internet_message_id"),
               "sent_at": found.get("sentDateTime")}
    if inspect is None:
        return None, dict(details, mismatches=[],
                          unverified="this mailer cannot read the sent message back")
    try:
        message = inspect(found.get("id"))
    except MailError as error:
        return None, dict(details, mismatches=[], unverified="the sent message could not be "
                                                             "read back: {0}".format(
                                                                 str(error)[:120]))
    if not message:
        return None, dict(details, mismatches=[], unverified="the sent message could not be "
                                                             "read back")
    to = sorted(str(r).lower() for r in message.get("to") or [])
    names = [(a.get("name"), a.get("size")) for a in message.get("attachments") or []]
    details.update(recipients=to, subject=message.get("subject"),
                   attachments=[{"name": n, "size": z} for n, z in names],
                   sender=message.get("from"))
    mismatches = []
    if to != [str(email.get("recipient") or "").lower()]:
        mismatches.append("sent to {0}, prepared for {1}".format(to, email.get("recipient")))
    if message.get("subject") != email.get("subject"):
        mismatches.append("subject {0!r}, prepared {1!r}".format(message.get("subject"),
                                                               email.get("subject")))
    if [n for n, _z in names] != [email.get("attachment")]:
        mismatches.append("attachments {0}, prepared {1}".format([n for n, _z in names],
                                                                 email.get("attachment")))
    elif email.get("attachment_bytes") and names[0][1] is not None and \
            abs(int(names[0][1]) - int(email["attachment_bytes"])) > 4096:
        # Graph reports the attachment item's size, slightly above the file's.
        mismatches.append("attachment size {0}, prepared {1}".format(names[0][1],
                                                                    email["attachment_bytes"]))
    return not mismatches, dict(details, mismatches=mismatches)


def confirm(store, record, mailer, wait_s=45, by=None):
    """
    EMAIL_SENT (or a reconciled EMAIL_UNKNOWN) → EMAIL_CONFIRMED only when the
    message is found in Sent Items AND it is this job's; a message that is
    there but different → EMAIL_RECONCILIATION_FAILED; not found yet → stays
    EMAIL_SENT (outcome SENT).
    """
    email = record["email"]
    key = store.ledger_key(record["po_key"], record["document"]["sha256"],
                           record["output"]["template_version"], email.get("recipient"))
    try:
        found = _find_sent(mailer, email, wait_s)
    except MailError as error:
        found = None
        email["confirm_error"] = str(error)[:200]
    if not found:
        email["status"] = "SENT"
        email["confirmation"] = "Accepted by Microsoft 365; not yet found in Sent Items."
        store.save(record)
        return record, "SENT"
    verified, details = _verify_message(record, found, mailer)
    email["reconciliation"] = dict(details, found=True, verified=bool(verified), at=S.now_iso())
    store.event(record, "EMAIL_VERIFIED", "email", {True: "OK", False: "MISMATCH"}.get(
        verified, "UNVERIFIED"), **{k: v for k, v in details.items() if k != "sender"})
    if verified is None:
        # Found, but not read back: stays EMAIL SENT (accepted, not confirmed).
        email["status"] = "SENT"
        email["confirmation"] = "Found in Sent Items, but its recipient, subject and " \
                                "attachment could not be read back: not confirmed."
        store.save(record)
        return record, "SENT"
    if verified is False:
        record = store.transition(record, S.EMAIL_RECONCILIATION_FAILED,
                                  "Sent message does not match", failure(
                                      "EMAIL_RECONCILIATION_FAILED", "email",
                                      "A message for this job is in Sent Items but does not "
                                      "match what was prepared: " +
                                      "; ".join(details["mismatches"]),
                                      code="EMAIL_RECONCILIATION_FAILED",
                                      next_action=S.NEXT_ACTION[S.EMAIL_RECONCILIATION_FAILED]),
                                  actor=by)
        store.ledger_write(key, {"status": "SENT", "at": S.now_iso(), "po_id": record["po_id"],
                                 "by": by, "recipient": email.get("recipient"),
                                 "reconciliation": "MISMATCH"})
        return record, "MISMATCH"
    return _confirmed(store, record, key, found, by)


def _find_sent(mailer, email, wait_s):
    """The message in Sent Items, by internetMessageId — or, when that id was
    never recorded (a crash right after the draft), by this job's unique
    attachment name and subject."""
    if email.get("internet_message_id"):
        return mailer.confirm(email["internet_message_id"], wait_s=wait_s)
    finder = getattr(mailer, "find_by_attachment", None)
    if finder and email.get("attachment"):
        return finder("sentitems", email.get("subject"), email["attachment"])
    return None


def _confirmed(store, record, key, found, by=None):
    email = record["email"]
    email.update(status="CONFIRMED", confirmed_at=found.get("sentDateTime") or S.now_iso(),
                 confirmation="Found in the mailbox's Sent Items; recipient, subject and "
                              "attachment verified.")
    milestones = S.Store.milestones(record)
    if "EMAIL_SUBMITTED" not in milestones:
        store.milestone(record, "EMAIL_SUBMITTED", save=False,
                        established_by="reconciliation: the message is in Sent Items")
    if "EMAIL_RECONCILING" not in S.Store.milestones(record):
        store.milestone(record, "EMAIL_RECONCILING", save=False,
                        established_by="reconciliation")
    store.milestone(record, "EMAIL_CONFIRMED", save=False,
                    reconciliation=email.get("reconciliation"))
    store.milestone(record, "COMPLETED", save=False, recipient=email["recipient"],
                    template=record["output"]["template_version"],
                    output_sha256=record["output"]["sha256"])
    record = store.transition(record, S.EMAIL_CONFIRMED, "Completed", actor=by)
    store.ledger_write(key, {"status": "CONFIRMED", "at": S.now_iso(), "po_id": record["po_id"],
                             "by": by, "recipient": email["recipient"],
                             "internet_message_id": email.get("internet_message_id")})
    store.event(record, "EMAIL_CONFIRMED", "email", "OK",
                sent_at=found.get("sentDateTime"), recipient=email["recipient"])
    store.event(record, "PO_COMPLETED", "complete", "OK", recipient=email["recipient"],
                template=record["output"]["template_version"])
    return record, "CONFIRMED"


def _reconcile_state(mailer, email, wait_s=20.0):
    """'sent' (in Sent Items), 'draft' (still an unsent draft), or 'unknown'."""
    try:
        found = _find_sent(mailer, email, wait_s)
    except MailError:
        found = None
    if found:
        return "sent"
    getter = getattr(mailer, "get_message", None)
    if not email.get("message_id"):
        finder = getattr(mailer, "find_by_attachment", None)
        if finder and email.get("attachment"):
            try:
                draft = finder("drafts", email.get("subject"), email["attachment"])
            except MailError:
                return "unknown"
            if draft and draft.get("isDraft", True):
                email["message_id"] = draft.get("id")
                email["internet_message_id"] = draft.get("internetMessageId")
                return "draft"
        return "unknown"
    if getter:
        try:
            message = getter(email["message_id"])
        except MailError:
            return "unknown"
        if message and message.get("isDraft") is True:
            return "draft"
    return "unknown"


def reconcile(store, record, mailer, wait_s=20.0, by="recover"):
    """
    EMAIL_UNKNOWN → what the mailbox says: CONFIRMED (in Sent Items and
    verified as this job's), back to sendable (the draft is still a draft —
    EMAIL_FAILED, transient), or still UNKNOWN (gone without trace: a person
    checks; never resent automatically).
    """
    if record["state"] != S.EMAIL_UNKNOWN:
        return record, "NOT_APPLICABLE"
    email = record.get("email") or {}
    key = store.ledger_key(record["po_key"], record["document"]["sha256"],
                           record["output"]["template_version"], email.get("recipient"))
    if "EMAIL_SUBMITTED" not in S.Store.milestones(record):
        # EMAIL_SUBMITTED is written to disk BEFORE Graph's send is called:
        # without it, the send was provably never called — nothing can have
        # gone out. A draft that was created is reused, never duplicated.
        if not email.get("message_id"):
            finder = getattr(mailer, "find_by_attachment", None)
            if finder and email.get("attachment"):
                try:
                    draft = finder("drafts", email.get("subject"), email["attachment"])
                except MailError:
                    draft = None
                if draft:
                    email["message_id"] = draft.get("id")
                    email["internet_message_id"] = draft.get("internetMessageId")
        state = "not_submitted"
    else:
        state = _reconcile_state(mailer, email, wait_s=wait_s)
    store.event(record, "EMAIL_RECONCILED", "email", state.upper(), by=by)
    if state == "sent":
        found = _find_sent(mailer, email, 0) or {}
        verified, details = _verify_message(record, found, mailer)
        email["reconciliation"] = dict(details, found=True, verified=bool(verified),
                                       at=S.now_iso())
        if verified is None:
            # Sent (it is in Sent Items) but not read back: EMAIL SENT, and
            # the next confirmation reads it back again.
            if "EMAIL_SUBMITTED" not in S.Store.milestones(record):
                store.milestone(record, "EMAIL_SUBMITTED", save=False,
                                established_by="reconciliation: the message is in Sent Items")
            email["accepted_evidence"] = {"found_after_error": True, "at": S.now_iso()}
            store.milestone(record, "EMAIL_RECONCILING", save=False,
                            established_by="reconciliation")
            record = store.transition(record, S.EMAIL_SENT, "In Sent Items — not yet verified",
                                      actor=by)
            return record, "SENT"
        if verified is False:
            record = store.transition(record, S.EMAIL_RECONCILIATION_FAILED,
                                      "Sent message does not match", failure(
                                          "EMAIL_RECONCILIATION_FAILED", "email",
                                          "A message for this job is in Sent Items but does "
                                          "not match what was prepared: " +
                                          "; ".join(details["mismatches"]),
                                          code="EMAIL_RECONCILIATION_FAILED",
                                          next_action=S.NEXT_ACTION[
                                              S.EMAIL_RECONCILIATION_FAILED]), actor=by)
            return record, "MISMATCH"
        return _confirmed(store, record, key, found, by)
    if state in ("draft", "not_submitted"):
        record = store.transition(record, S.EMAIL_FAILED, "Not sent — safe to send", failure(
            "EMAIL_FAILURE", "email", "Reconciled: the message is still an unsent draft in the "
            "mailbox, so it was never sent. It can be sent." if state == "draft" else
            "Reconciled: the job stopped before the send was ever submitted to Microsoft 365 "
            "(no EMAIL_SUBMITTED on record), so nothing went out. It can be sent.",
            kind="transient",
            code="EMAIL_FAILED", next_action="Send it from the drawer (the same draft is used)."),
            actor=by)
        record["email"]["status"] = "FAILED"
        store.save(record)
        store.ledger_write(key, {"status": "FAILED", "at": S.now_iso(), "po_id": record["po_id"],
                                 "by": by, "recipient": email.get("recipient")})
        return record, "SENDABLE"
    return record, "UNKNOWN"


# ── RECOVERY: a worker can stop at any point ─────────────────────────────

def interrupt(store, record, reason):
    """
    The worker stopped (or the job broke) mid-way. Nothing is declared failed
    that may have succeeded: a send in flight becomes EMAIL_UNKNOWN (reconcile
    before anything else); any other unfinished stage becomes
    WORKER_DISCONNECTED, resumable from what was already proven.
    """
    state = record["state"]
    if state == S.EMAIL_SENDING:
        record = store.transition(record, S.EMAIL_UNKNOWN, "Email outcome unknown", failure(
            "EMAIL_UNKNOWN", "email", "the worker stopped while the email was being submitted: "
            "{0}".format(reason), kind="unknown", code="EMAIL_UNKNOWN",
            next_action=S.NEXT_ACTION[S.EMAIL_UNKNOWN]))
        store.event(record, "EMAIL_UNKNOWN", "email", "UNKNOWN", reason=str(reason)[:300])
        return record
    if state not in S.RESUMABLE:
        return record
    record["interrupted"] = {"state": state, "at": S.now_iso(), "reason": str(reason)[:300]}
    record = store.transition(record, S.WORKER_DISCONNECTED, "Interrupted", failure(
        "WORKER_DISCONNECTED", state.lower(), "the job stopped at {0}: {1}".format(
            S.LABELS.get(state, state), reason), kind="unknown", code="WORKER_DISCONNECTED",
        next_action=S.NEXT_ACTION[S.WORKER_DISCONNECTED]))
    store.event(record, "WORKER_DISCONNECTED", state.lower(), "INTERRUPTED",
                reason=str(reason)[:300])
    return record


# The earlier name: callers that end a job whose worker went away.
abandon = interrupt


def resume(store, record, config=None, source=None, sleep=time.sleep):
    """
    Continue an interrupted job from what it had already proven — never from
    zero when the work is kept, never past a stage that did not finish.
    """
    config = config or config_from_env()
    if record["state"] != S.WORKER_DISCONNECTED:
        return record
    was = (record.get("interrupted") or {}).get("state")
    record["attempt"] = int(record.get("attempt") or 1) + 1
    store.event(record, "RESUMED", "recovery", "RUNNING", from_state=was)
    record["failure"] = None
    if was in (S.QUEUED, S.DISCOVERED) or not record.get("document"):
        if source is None:
            return record
        # Discovery again (WORKER_DISCONNECTED → DISCOVERED): nothing was proven yet.
        return process(store, record, source, config, sleep)
    if was in (S.PDF_FOUND, S.PDF_READ, S.FIELDS_EXTRACTED) or not record.get("fields"):
        path = Path((record.get("document") or {}).get("evidence") or "")
        if not path.is_file():
            if source is None:
                return record
            return process(store, record, source, config, sleep)
        # The kept PDF is read again from the start: integrity, extraction,
        # normalisation — nothing proven earlier is reused past retrieval.
        S.Store.reset_milestones(record, "rediscover")
        _restore_discovery(store, record)
        record = store.transition(record, S.PDF_FOUND, "Reading the kept PDF…")
        return from_document(store, record, path.read_bytes(), config)
    # VALIDATING / VALIDATED / TEMPLATE_GENERATED / SAVED: validation again,
    # in full, then build and save anew — no state past VALIDATED is ever
    # reached without validating. An earlier saved file is kept and named.
    if (record.get("output") or {}).get("path"):
        record.setdefault("superseded_outputs", []).append(
            {k: record["output"].get(k) for k in ("path", "filename", "sha256", "saved_at")})
        record["output"] = None
    record = store.transition(record, S.VALIDATING, "Validating again…")
    return validate_onward(store, record, config)


def _restore_discovery(store, record):
    """Re-record the discovery milestones from the job's own kept trail (the
    evidence is the trail recorded when the document was retrieved)."""
    found = {"trail": record.get("discovery") or {}, "hub": record.get("hub") or {},
             "filename": (record.get("document") or {}).get("filename"),
             "identifier": record.get("identifier")}
    path = Path((record.get("document") or {}).get("evidence") or "")
    data = path.read_bytes()
    missing = _discovery_milestones(store, record, found, record["document"]["sha256"], data)
    if missing:
        raise S.InvariantViolation("the kept discovery evidence is incomplete: {0}".format(
            missing[1]))


def resolve_review(store, record, action, by, value=None, reason=None, config=None,
                   source=None, sleep=time.sleep):
    """
    A person resolves a job in review — and only in ways that keep every rule:

      choose           G4 := one of the values the Bill of Entry PRINTS (a value
                       that is not printed is refused); validation runs again
      confirm_values   the OCR-read values were compared with the PDF by a
                       person; validation runs again
      refetch          fetch the document from eHub again (after it was
                       corrected there); everything is proven again
      reject           close the job; nothing is generated or sent
    -> (record, problems)
    """
    if record["state"] not in (S.NEEDS_REVIEW, S.DOCUMENT_AMBIGUOUS):
        return record, ["the job is {0}, not waiting for review".format(
            S.LABELS.get(record["state"], record["state"]))]
    code = (record.get("failure") or {}).get("code")
    if not by:
        return record, ["a reviewer must be named"]
    if action == "reject":
        if not str(reason or "").strip():
            return record, ["a reason is required to reject a job"]
        store.event(record, "REVIEW_RESOLVED", "review", "REJECTED", by=by,
                    reason=str(reason)[:300], code=code)
        record = store.transition(record, S.REVIEW_REJECTED, "Rejected at review", failure(
            "REVIEW_REJECTED", "review", "Rejected by {0}: {1}".format(by, str(reason)[:300]),
            code="REVIEW_REJECTED", next_action=S.NEXT_ACTION[S.REVIEW_REJECTED]), actor=by)
        return record, []
    if action == "refetch":
        if source is None:
            return record, ["no eHub source is available to fetch the document again"]
        record["attempt"] = int(record.get("attempt") or 1) + 1
        store.event(record, "REVIEW_RESOLVED", "review", "REFETCH", by=by, code=code)
        return process(store, record, source, config, sleep), []
    if record["state"] != S.NEEDS_REVIEW or not record.get("fields"):
        return record, ["this review can only be resolved by fetching again or rejecting"]
    request = dict(record.get("request") or {})
    if action == "choose":
        printed = record.get("invoice_candidate") or {}
        allowed = ([printed["value"]] if printed.get("value") else []) + \
            list(printed.get("candidates") or [])
        choice = str(value or "").strip().upper()
        if code != "G4_SOURCE_UNPROVEN":
            return record, ["nothing in this review is chosen from printed values"]
        if choice not in allowed:
            return record, ["{0!r} is not printed on the Bill of Entry; G4 can only be one of "
                            "the values it prints ({1}) — never a typed value".format(
                                value, ", ".join(allowed) or "none")]
        request.update(g4_choice=choice, g4_choice_by=by)
    elif action == "confirm_values":
        if code != "LOW_CONFIDENCE_EXTRACTION":
            return record, ["there are no OCR-read values to confirm"]
        request.update(ocr_confirmed_by=by)
    else:
        return record, ["unknown review action {0!r}".format(action)]
    record["request"] = request
    record["failure"] = None
    record["attempt"] = int(record.get("attempt") or 1) + 1
    store.event(record, "REVIEW_RESOLVED", "review", action.upper(), by=by, value=choice
                if action == "choose" else None, code=code)
    record = store.transition(record, S.VALIDATING, "Validating after review…", actor=by)
    return validate_onward(store, record, config), []


def supply(store, record, values, by=None, config=None):
    """
    The earlier review entry point. G4 is never a typed value: a supplied
    invoice No. is accepted only as the choice of a value the Bill of Entry
    prints (resolve_review "choose"); anything else is refused.
    """
    value = (values or {}).get("invoice_no")
    if not str(value or "").strip():
        return record, ["no value was given"]
    return resolve_review(store, record, "choose", by or "a reviewer", value=value,
                          config=config)


def recover(store, config=None, source=None, mailer=None, log=print):
    """
    `python -m po recover` and the start of every sweep: every job whose
    worker is gone is interrupted, resumed, or (for an unknown send)
    reconciled with the mailbox. Returns [(po_id, before, after)].
    """
    done = []
    for record in store.all(500):
        before = record["state"]
        if before in S.RESUMABLE + (S.EMAIL_SENDING,) and store.lease_stale(record):
            record = interrupt(store, record, "its worker is no longer running")
        if record["state"] == S.WORKER_DISCONNECTED:
            try:
                record = resume(store, record, config, source)
            except Exception as error:
                log("[PO recover] {0}: could not resume: {1}".format(record["po_id"], error))
        if record["state"] == S.EMAIL_UNKNOWN and mailer is not None:
            try:
                record, _o = reconcile(store, record, mailer)
            except Exception as error:
                log("[PO recover] {0}: could not reconcile: {1}".format(record["po_id"], error))
        if record["state"] == S.EMAIL_SENT and mailer is not None:
            try:
                record, _o = confirm(store, record, mailer, wait_s=0, by="recover")
            except Exception as error:
                log("[PO recover] {0}: could not confirm: {1}".format(record["po_id"], error))
        if record["state"] != before:
            done.append((record["po_id"], before, record["state"]))
            log("[PO recover] {0}: {1} → {2}".format(record["po_id"], before, record["state"]))
    return done
