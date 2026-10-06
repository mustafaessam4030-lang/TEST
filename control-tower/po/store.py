"""
PO records, their events, and the send ledger — on disk, one folder.

    <PO_DATA_DIR>/
        jobs/<po_id>.json      one record per processing job (latest state)
        events.jsonl           every PO event, append-only
        ledger.json            what was sent: po_key + document hash +
                               template version + recipient -> result
        documents/<sha>.pdf    the retrieved PDF, by content hash (evidence)
        output/                generated documents (PO_OUTPUT_DIR overrides)

THE STATE MACHINE lives here so nothing can move a record except through
it: transition() refuses any step the table does not allow — in particular
none reaches TEMPLATE_GENERATED or anything after it without VALIDATED.

THE TRANSACTION LAYER (enforced here, not by the pipeline's control flow):

  * MILESTONES — the canonical stages of a PO transaction (CANONICAL), each
    recorded with its evidence by milestone(), which refuses one whose
    prerequisites (PREREQUISITES) are not already recorded in this attempt.
    transition() refuses a state whose milestones (STATE_REQUIRES) and
    evidence checks (_evidence_problems) are missing: FIELDS_EXTRACTED without
    PDF_INTEGRITY_VERIFIED, TEMPLATE_GENERATED without
    BUSINESS_RULES_VALIDATED, EMAIL_PREPARED without TEMPLATE_VERIFIED and
    OUTPUT_PERSISTED, EMAIL_SENDING without IDEMPOTENCY_CONFIRMED,
    EMAIL_CONFIRMED without reconciliation evidence — whatever code asks.
  * DURABLE WRITES — a record is written to a unique temporary file, flushed
    to disk and atomically renamed; every write carries a version and is
    refused (ConcurrentUpdate) if another process wrote the record since it
    was read: no lost update between two workers.
  * LOCKS — operating-system file locks (fcntl / msvcrt): released by the OS
    when the holder dies, so there is no stale lock to break and no window
    where two processes both believe they hold one.
  * AUDIT — events.jsonl is append-only and hash-chained (each event carries
    the previous event's hash); verify_audit() proves nothing was removed,
    reordered or altered. Every state change is an event with the old and
    new state, the actor, the attempt and the correlation id.

Never stored: credentials, tokens, the email body beyond its subject and
recipient. Event metadata passes through the same redaction as ATLAS's
intelligence store.
"""

import contextlib
import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DIR = HERE.parent / "ml" / "data" / "po"

# ── STATES ───────────────────────────────────────────────────────────────
QUEUED = "QUEUED"
# The state names of the PO brief, exactly. DISCOVERED is the code's name
# for PO_DISCOVERED; a record stored before the rename reads as the new one.
DISCOVERED = PO_DISCOVERED = "PO_DISCOVERED"
LEGACY_STATES = {"DISCOVERED": "PO_DISCOVERED"}
PDF_FOUND = "PDF_FOUND"
PDF_READ = "PDF_READ"
FIELDS_EXTRACTED = "FIELDS_EXTRACTED"
VALIDATING = "VALIDATING"
VALIDATED = "VALIDATED"
TEMPLATE_GENERATED = "TEMPLATE_GENERATED"
SAVED = "SAVED"
EMAIL_PREPARED = "EMAIL_PREPARED"
EMAIL_SENDING = "EMAIL_SENDING"
EMAIL_SENT = "EMAIL_SENT"
EMAIL_CONFIRMED = "EMAIL_CONFIRMED"
PDF_NOT_FOUND = "PDF_NOT_FOUND"
PDF_UNREADABLE = "PDF_UNREADABLE"
EXTRACTION_FAILED = "EXTRACTION_FAILED"
VALIDATION_FAILED = "VALIDATION_FAILED"
TEMPLATE_FAILED = "TEMPLATE_FAILED"
EMAIL_FAILED = "EMAIL_FAILED"
# Not failures of the pipeline: the eHub record is not one to process
# (SKIPPED — skip_reason says why), or a person must decide (NEEDS_REVIEW).
SKIPPED = "SKIPPED"
NEEDS_REVIEW = "NEEDS_REVIEW"
# The precise failure states (no generic FAILED). PDF_NOT_FOUND is the
# DOCUMENT_NOT_FOUND of the failure taxonomy: Manage → Documents has no Bill
# Entry; the state keeps its stored name.
DOCUMENT_NOT_FOUND = PDF_NOT_FOUND
DISCOVERY_FAILED = "DISCOVERY_FAILED"            # the Shipments list could not be read
AUTH_REQUIRED = "AUTH_REQUIRED"                  # eHub is not signed in
MANAGE_NAVIGATION_FAILED = "MANAGE_NAVIGATION_FAILED"   # Manage did not open THIS record
DOCUMENT_AMBIGUOUS = "DOCUMENT_AMBIGUOUS"        # several Bill Entry documents, none chosen
PDF_DOWNLOAD_FAILED = "PDF_DOWNLOAD_FAILED"      # the Bill Entry was found, its download failed
SAVE_FAILED = "SAVE_FAILED"                      # generated, but not saved and read back
EMAIL_UNKNOWN = "EMAIL_UNKNOWN"                  # the send may have happened: reconcile first
# The worker stopped mid-job. Recoverable: `python -m po recover` resumes
# it from what was already proven (record["interrupted"] says where).
WORKER_DISCONNECTED = "WORKER_DISCONNECTED"
# The Manage page that opened shows a different shipment (or a declaration
# that is not the selected Bill Entry's): nothing is read from it.
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
# A person reviewed the job and closed it: nothing is generated or sent.
REVIEW_REJECTED = "REVIEW_REJECTED"
# A message for this job IS in the mailbox, but its recipient, subject or
# attachment is not what this job prepared: an incident — a person checks.
# Never resent automatically.
EMAIL_RECONCILIATION_FAILED = "EMAIL_RECONCILIATION_FAILED"
# Why a record was not processed (SKIPPED.skip_reason).
SKIP_REASONS = ("SKIPPED_NOT_UNDER_CLEARANCE", "SKIPPED_ALREADY_PROCESSED",
                "SKIPPED_MISSING_REQUIRED_REFERENCE", "SKIPPED_DUPLICATE",
                "SKIPPED_STATUS_CHANGED")

FAILED_STATES = (PDF_NOT_FOUND, PDF_UNREADABLE, EXTRACTION_FAILED, VALIDATION_FAILED,
                 TEMPLATE_FAILED, EMAIL_FAILED, DISCOVERY_FAILED, AUTH_REQUIRED,
                 MANAGE_NAVIGATION_FAILED, DOCUMENT_AMBIGUOUS, PDF_DOWNLOAD_FAILED, SAVE_FAILED,
                 IDENTITY_MISMATCH, EMAIL_RECONCILIATION_FAILED)
ACTIVE_STATES = (QUEUED, DISCOVERED, PDF_FOUND, PDF_READ, FIELDS_EXTRACTED, VALIDATING,
                 EMAIL_SENDING)
# States a job can be resumed from after its worker stopped (recover()).
RESUMABLE = (QUEUED, DISCOVERED, PDF_FOUND, PDF_READ, FIELDS_EXTRACTED, VALIDATING, VALIDATED,
             TEMPLATE_GENERATED, SAVED)

TRANSITIONS = {
    QUEUED: (DISCOVERED, PDF_NOT_FOUND, AUTH_REQUIRED, DISCOVERY_FAILED, WORKER_DISCONNECTED),
    DISCOVERED: (PDF_FOUND, PDF_NOT_FOUND, PDF_UNREADABLE, SKIPPED, NEEDS_REVIEW, DISCOVERY_FAILED,
                 AUTH_REQUIRED,
                 MANAGE_NAVIGATION_FAILED, DOCUMENT_AMBIGUOUS, PDF_DOWNLOAD_FAILED,
                 IDENTITY_MISMATCH, WORKER_DISCONNECTED),
    PDF_FOUND: (PDF_READ, PDF_UNREADABLE, SKIPPED, WORKER_DISCONNECTED),
    PDF_READ: (FIELDS_EXTRACTED, EXTRACTION_FAILED, WORKER_DISCONNECTED),
    # EXTRACTION_FAILED from here: a printed value is malformed (normalisation).
    FIELDS_EXTRACTED: (VALIDATING, EXTRACTION_FAILED, WORKER_DISCONNECTED),
    # NEEDS_REVIEW from validation: only a value whose source is not proven
    # (G4) is missing — a person supplies it; everything else is a failure.
    VALIDATING: (VALIDATED, VALIDATION_FAILED, NEEDS_REVIEW, IDENTITY_MISMATCH,
                 WORKER_DISCONNECTED),
    VALIDATED: (TEMPLATE_GENERATED, TEMPLATE_FAILED, WORKER_DISCONNECTED),
    # Generated (filled and read back in memory) is not saved: SAVED only once
    # the file is in the output folder and read back from disk.
    TEMPLATE_GENERATED: (SAVED, TEMPLATE_FAILED, SAVE_FAILED, WORKER_DISCONNECTED),
    SAVED: (EMAIL_PREPARED, WORKER_DISCONNECTED),
    EMAIL_PREPARED: (EMAIL_SENDING,),
    EMAIL_SENDING: (EMAIL_SENT, EMAIL_FAILED, EMAIL_CONFIRMED, EMAIL_UNKNOWN),
    # An explicitly authorized resend (who and why on the ledger) starts a
    # new submission of the same verified document.
    EMAIL_SENT: (EMAIL_CONFIRMED, EMAIL_SENDING, EMAIL_RECONCILIATION_FAILED),
    # A failed send whose cause was transient may be sent again — through
    # the ledger, on the same prepared message.
    EMAIL_FAILED: (EMAIL_SENDING,),
    EMAIL_CONFIRMED: (EMAIL_SENDING,),
    # Reconciled against the mailbox: sent (found in Sent Items), still a
    # draft (safe to send), or gone without trace (failed — a person decides).
    EMAIL_UNKNOWN: (EMAIL_SENT, EMAIL_CONFIRMED, EMAIL_SENDING, EMAIL_FAILED,
                    EMAIL_RECONCILIATION_FAILED),
    # Review resolved: a printed value chosen (validation again, in full), the
    # document fetched again from eHub, or the job closed by the reviewer.
    NEEDS_REVIEW: (VALIDATING, DISCOVERED, REVIEW_REJECTED),
    DOCUMENT_AMBIGUOUS: (DISCOVERED, REVIEW_REJECTED),
    # Resumed from what was already proven — never past a stage that was
    # not finished, and never past VALIDATED without validating again:
    # discovery again, the kept PDF read again, or validation again (which
    # re-builds and re-saves the output).
    WORKER_DISCONNECTED: (DISCOVERED, PDF_FOUND, VALIDATING),
}

# The words the queue shows for each state.
LABELS = {
    QUEUED: "QUEUED", DISCOVERED: "FINDING PDF", PDF_FOUND: "PDF FOUND", PDF_READ: "READING",
    FIELDS_EXTRACTED: "EXTRACTING", VALIDATING: "VALIDATING", VALIDATED: "VALIDATED",
    TEMPLATE_GENERATED: "TEMPLATE GENERATED", SAVED: "SAVED", EMAIL_PREPARED: "EMAIL READY",
    EMAIL_SENDING: "SENDING", EMAIL_SENT: "EMAIL SENT", EMAIL_CONFIRMED: "VERIFIED",
    PDF_NOT_FOUND: "PDF NOT FOUND", PDF_UNREADABLE: "PDF UNREADABLE",
    EXTRACTION_FAILED: "EXTRACTION FAILED", VALIDATION_FAILED: "VALIDATION FAILED",
    TEMPLATE_FAILED: "TEMPLATE FAILED", EMAIL_FAILED: "EMAIL FAILED",
    SKIPPED: "SKIPPED — NOT UNDER CLEARANCE", NEEDS_REVIEW: "NEEDS REVIEW",
    DISCOVERY_FAILED: "DISCOVERY FAILED", AUTH_REQUIRED: "SIGN-IN REQUIRED",
    MANAGE_NAVIGATION_FAILED: "MANAGE FAILED", DOCUMENT_AMBIGUOUS: "DOCUMENT AMBIGUOUS",
    PDF_DOWNLOAD_FAILED: "DOWNLOAD FAILED", SAVE_FAILED: "SAVE FAILED",
    EMAIL_UNKNOWN: "EMAIL UNKNOWN", WORKER_DISCONNECTED: "INTERRUPTED",
    IDENTITY_MISMATCH: "IDENTITY MISMATCH", REVIEW_REJECTED: "REJECTED AT REVIEW",
    EMAIL_RECONCILIATION_FAILED: "EMAIL DOES NOT MATCH",
}
LABELS[EMAIL_SENDING] = "EMAIL SUBMITTED"      # handed to Graph, no answer yet
LABELS[EMAIL_SENT] = "EMAIL ACCEPTED"          # Graph answered 202
LABELS[PDF_NOT_FOUND] = "DOCUMENT NOT FOUND"
# What a person does next, for every stop.
NEXT_ACTION = {
    PDF_NOT_FOUND: "Open the record in eHub: no Bill of Entry is attached yet. The next "
                   "automatic run looks again.",
    DISCOVERY_FAILED: "Check that eHub's Shipments list opens on the worker; the evidence "
                      "screenshot shows the page as it was.",
    AUTH_REQUIRED: "Sign the worker's browser in to eHub (credentials file), then run "
                   "`python -m po recover`.",
    MANAGE_NAVIGATION_FAILED: "Open the record by hand in eHub and compare with the kept "
                              "screenshot; nothing was read from a page that could not be "
                              "tied to this record.",
    DOCUMENT_AMBIGUOUS: "Open Manage → Documents and decide which Bill of Entry applies; "
                        "nothing was guessed.",
    PDF_DOWNLOAD_FAILED: "Retry later (transient) or download the Bill of Entry by hand to "
                         "check it opens.",
    PDF_UNREADABLE: "Open the kept PDF: it is not a readable, complete PDF. Ask for a clean "
                    "copy in eHub.",
    EXTRACTION_FAILED: "Read the kept PDF: it does not read as a Bill of Entry.",
    VALIDATION_FAILED: "Compare the mismatched values in the drawer with eHub and the PDF; "
                       "nothing was generated or sent.",
    NEEDS_REVIEW: "Read the review reason in the drawer. The supplier invoice No. (G4) comes "
                  "only from the Bill of Entry: choose one of its printed values, fetch the "
                  "document again after it is corrected in eHub, or reject the job.",
    IDENTITY_MISMATCH: "The Manage page that opened is not this shipment's. Open the record by "
                       "hand in eHub; nothing was read from the page.",
    REVIEW_REJECTED: "Nothing to do: a person closed the job at review (see the reason).",
    EMAIL_RECONCILIATION_FAILED: "Open the mailbox's Sent Items: a message for this job was "
                                 "sent but does not match what was prepared (see the "
                                 "mismatches). Do not resend; tell the recipient if needed.",
    TEMPLATE_FAILED: "The approved template or a value did not read back; nothing was sent.",
    SAVE_FAILED: "Check the output folder is reachable and writable, then `python -m po "
                 "recover`.",
    EMAIL_FAILED: "Read the Graph error; a transient failure can be sent again from the drawer.",
    EMAIL_UNKNOWN: "Do not resend. Run `python -m po recover` — it checks the mailbox first.",
    WORKER_DISCONNECTED: "Run `python -m po recover` on the worker: the job resumes from "
                         "what was already proven.",
    SKIPPED: "Nothing to do: the record is not one to process (see the reason).",
}

EVENTS = ("PO_DISCOVERED", "EHUB_RECORD_FOUND", "CLEARANCE_CHECKED", "RECORD_SKIPPED",
          "MANAGE_OPENED", "DOCUMENTS_SECTION_FOUND", "BILL_ENTRY_FOUND", "IDENTIFIER_EXTRACTED",
          "BILL_ENTRY_DOWNLOADED", "DOCUMENT_REVIEW_REQUIRED", "PDF_FOUND", "PDF_NOT_FOUND", "PDF_READ", "PDF_UNREADABLE",
          "FIELDS_EXTRACTED", "EXTRACTION_FAILED", "VALIDATION_STARTED", "VALIDATION_PASSED",
          "VALIDATION_FAILED", "TEMPLATE_GENERATION_STARTED", "TEMPLATE_GENERATED",
          "TEMPLATE_FAILED", "OUTPUT_SAVED", "EMAIL_PREPARED", "EMAIL_BLOCKED", "EMAIL_SEND_STARTED",
          "EMAIL_SENT", "EMAIL_CONFIRMED", "EMAIL_FAILED", "PO_COMPLETED", "RETRY",
          "AUTH_REQUIRED", "IDENTITY_CHECKED", "DUPLICATE_DETECTED", "NEEDS_REVIEW",
          "EMAIL_UNKNOWN", "EMAIL_RECONCILED", "WORKER_DISCONNECTED", "RESUMED",
          "STAGE_TIMED", "STATE_CHANGED", "MILESTONE", "REVIEW_RESOLVED", "IDENTITY_MISMATCH",
          "EMAIL_VERIFIED")

# ── THE CANONICAL TRANSACTION: milestones and what each requires ─────────
CANONICAL = ("DISCOVERED", "ELIGIBLE_VERIFIED", "MANAGE_OPENED", "SHIPMENT_IDENTITY_VERIFIED",
             "DOCUMENT_DISCOVERED", "DOCUMENT_SELECTED", "PDF_RETRIEVED",
             "PDF_INTEGRITY_VERIFIED", "FIELDS_EXTRACTED", "FIELDS_NORMALIZED", "CROSS_VALIDATED",
             "BUSINESS_RULES_VALIDATED", "TEMPLATE_GENERATED", "TEMPLATE_VERIFIED",
             "OUTPUT_PERSISTED", "EMAIL_PREPARED", "IDEMPOTENCY_CONFIRMED", "EMAIL_SUBMITTED",
             "EMAIL_RECONCILING", "EMAIL_CONFIRMED", "COMPLETED")
PREREQUISITES = {
    "ELIGIBLE_VERIFIED": ("DISCOVERED",),
    "MANAGE_OPENED": ("ELIGIBLE_VERIFIED",),
    # The Manage page is tied to the shipment from the page itself, or —
    # when the page does not show it — from the document's own BL/AWB and
    # declaration (decided at cross-validation): never assumed.
    "SHIPMENT_IDENTITY_VERIFIED": ("MANAGE_OPENED",),
    "DOCUMENT_DISCOVERED": ("MANAGE_OPENED",),
    "DOCUMENT_SELECTED": ("DOCUMENT_DISCOVERED",),
    "PDF_RETRIEVED": ("DOCUMENT_SELECTED",),
    "PDF_INTEGRITY_VERIFIED": ("PDF_RETRIEVED",),
    "FIELDS_EXTRACTED": ("PDF_INTEGRITY_VERIFIED",),
    "FIELDS_NORMALIZED": ("FIELDS_EXTRACTED",),
    "CROSS_VALIDATED": ("FIELDS_NORMALIZED", "SHIPMENT_IDENTITY_VERIFIED"),
    "BUSINESS_RULES_VALIDATED": ("CROSS_VALIDATED",),
    "TEMPLATE_GENERATED": ("BUSINESS_RULES_VALIDATED",),
    "TEMPLATE_VERIFIED": ("TEMPLATE_GENERATED",),
    "OUTPUT_PERSISTED": ("TEMPLATE_VERIFIED",),
    "EMAIL_PREPARED": ("TEMPLATE_VERIFIED", "OUTPUT_PERSISTED"),
    "IDEMPOTENCY_CONFIRMED": ("EMAIL_PREPARED",),
    "EMAIL_SUBMITTED": ("IDEMPOTENCY_CONFIRMED",),
    "EMAIL_RECONCILING": ("EMAIL_SUBMITTED",),
    "EMAIL_CONFIRMED": ("EMAIL_RECONCILING",),
    "COMPLETED": ("EMAIL_CONFIRMED",),
}
# A state is only reachable with these milestones recorded in this attempt.
STATE_REQUIRES = {
    PDF_FOUND: ("PDF_RETRIEVED",),
    PDF_READ: ("PDF_INTEGRITY_VERIFIED",),
    FIELDS_EXTRACTED: ("FIELDS_EXTRACTED",),
    VALIDATED: ("BUSINESS_RULES_VALIDATED",),
    TEMPLATE_GENERATED: ("TEMPLATE_GENERATED", "TEMPLATE_VERIFIED"),
    SAVED: ("OUTPUT_PERSISTED",),
    EMAIL_PREPARED: ("EMAIL_PREPARED",),
    EMAIL_SENDING: ("IDEMPOTENCY_CONFIRMED",),
    EMAIL_SENT: ("EMAIL_SUBMITTED", "EMAIL_RECONCILING"),
    EMAIL_CONFIRMED: ("EMAIL_CONFIRMED", "COMPLETED"),
}
# Re-doing a stage forgets what came after it (a resumed validation must
# prove everything after it again).
RESET_FROM = {"rediscover": "ELIGIBLE_VERIFIED", "revalidate": "CROSS_VALIDATED",
              "resend": "IDEMPOTENCY_CONFIRMED"}
TERMINAL = (EMAIL_CONFIRMED, SKIPPED, REVIEW_REJECTED, IDENTITY_MISMATCH)


class InvariantViolation(Exception):
    """A state or milestone was asked for without the evidence it requires."""


class ConcurrentUpdate(Exception):
    """Another process wrote this job since it was read: the write is refused."""


FSYNC = os.environ.get("PO_FSYNC", "1") != "0"


def _fsync_dir(path):
    if os.name == "nt" or not FSYNC:
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_atomic(path, text):
    """Unique temp file → flush → fsync → atomic rename; the directory synced."""
    path = Path(path)
    tmp = path.with_name("{0}.{1}.{2}.tmp".format(path.name, os.getpid(), uuid.uuid4().hex[:8]))
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        if FSYNC:
            os.fsync(handle.fileno())
    os.replace(str(tmp), str(path))
    _fsync_dir(path.parent)


def _lock_file(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle):
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _canonical(entry):
    return json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

FORBIDDEN = ("password", "secret", "token", "authorization", "cookie", "captcha",
             "security_code", "credential")


def _current(record):
    """A record stored under an older state name, read under the current one."""
    if isinstance(record, dict) and record.get("state") in LEGACY_STATES:
        record["state"] = LEGACY_STATES[record["state"]]
    return record


class IllegalTransition(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clean(value, depth=0):
    if depth > 5:
        return None
    if isinstance(value, dict):
        return {str(k): _clean(v, depth + 1) for k, v in value.items()
                if not any(word in str(k).lower() for word in FORBIDDEN)}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in list(value)[:200]]
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:400]


def new_po_id():
    return "po-{0}-{1}".format(datetime.now().strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])


def po_key(doctype_id, reference):
    """The document's identity across jobs: the type and the Hub reference."""
    from .extract import normal_reference
    return "{0}:{1}".format(doctype_id, normal_reference(reference))


class Store(object):
    def __init__(self, folder=None, output_dir=None):
        self.folder = Path(folder or os.environ.get("PO_DATA_DIR") or DEFAULT_DIR)
        self.output_dir = Path(output_dir or os.environ.get("PO_OUTPUT_DIR")
                               or (self.folder / "output"))
        for sub in ("jobs", "documents"):
            (self.folder / sub).mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sink = []

    # -- listeners (ATLAS, the bridge, the control plane) ---------------------
    def subscribe(self, fn):
        self._sink.append(fn)

    # -- records ---------------------------------------------------------------
    def _path(self, po_id):
        safe = "".join(c for c in str(po_id) if c.isalnum() or c in "-_")
        return self.folder / "jobs" / (safe + ".json")

    def get(self, po_id):
        try:
            return _current(json.loads(self._path(po_id).read_text(encoding="utf-8")))
        except Exception:
            return None

    def save(self, record):
        """
        Durable, versioned write. Refused (ConcurrentUpdate) when the job on
        disk is not the version this copy was read at — another process
        wrote it in between; the caller must read it again.
        """
        with self._lock, self.xlock("job-" + self._path(record["po_id"]).stem, timeout=30.0):
            path = self._path(record["po_id"])
            mine = int(record.get("version") or 0)
            if path.exists():
                try:
                    disk = int(json.loads(path.read_text(encoding="utf-8")).get("version") or 0)
                except (OSError, ValueError):
                    disk = mine
                if disk != mine:
                    raise ConcurrentUpdate("job {0} was written by another process (version {1}, "
                                           "this copy {2})".format(record["po_id"], disk, mine))
            record["version"] = mine + 1
            record["updated"] = now_iso()
            record["updated_epoch"] = round(time.time(), 3)
            # Who is working the job, renewed on every write: a job whose
            # holder is gone is recoverable (lease_stale).
            record["lease"] = {"pid": os.getpid(), "host": socket.gethostname(),
                               "at": record["updated_epoch"]}
            try:
                write_atomic(path, json.dumps(_clean(record), indent=1, ensure_ascii=False))
            except Exception:
                record["version"] = mine
                raise
        return record

    def all(self, limit=200):
        rows = []
        for path in (self.folder / "jobs").glob("*.json"):
            try:
                rows.append(_current(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        rows.sort(key=lambda r: -float(r.get("created_epoch") or 0))
        return rows[:limit]

    def create(self, doctype_id, reference, request, run_id=None, started_by=None):
        po_id = new_po_id()
        record = {
            "po_id": po_id, "run_id": run_id or po_id, "doctype": doctype_id,
            "po_key": po_key(doctype_id, reference) if reference else None,
            "reference": reference or "", "discovery": None, "identifier": None, "number": None,
            "request": request, "state": QUEUED, "label": LABELS[QUEUED],
            "started_by": started_by, "created": now_iso(), "created_epoch": time.time(),
            "progress": "Queued", "document": None, "hub": None, "fields": None,
            "validation": None, "output": None, "email": None, "failure": None,
            "history": [{"at": now_iso(), "state": QUEUED}], "attempts": {},
            "version": 0, "attempt": 1, "previous_state": None, "started_at": None,
            "completed_at": None, "milestones": [],
            "worker": {"host": socket.gethostname(), "pid": os.getpid(),
                       "worker_id": os.environ.get("ATA_WORKER_ID")},
        }
        return self.save(record)

    # -- milestones: the canonical transaction, enforced ----------------------
    @staticmethod
    def milestones(record):
        """The milestones recorded in the job's current attempt, by name."""
        return {m["name"]: m for m in record.get("milestones") or []}

    def milestone(self, record, name, save=True, **evidence):
        """Record a canonical milestone with its evidence — only once its
        prerequisites are recorded (InvariantViolation otherwise)."""
        if name not in CANONICAL:
            raise ValueError("unknown milestone {0}".format(name))
        have = self.milestones(record)
        missing = [p for p in PREREQUISITES.get(name, ()) if p not in have]
        if missing:
            raise InvariantViolation("{0} cannot be recorded before {1}".format(
                name, ", ".join(missing)))
        entry = {"name": name, "at": now_iso(), "attempt": record.get("attempt") or 1,
                 "evidence": _clean(evidence)}
        record["milestones"] = [m for m in record.get("milestones") or [] if m["name"] != name] \
            + [entry]
        self.event(record, "MILESTONE", name.lower(), "OK", milestone=name,
                   detail=entry["evidence"])
        return self.save(record) if save else record

    @staticmethod
    def reset_milestones(record, kind):
        """Forget every milestone from RESET_FROM[kind] on (kept in history)."""
        start = CANONICAL.index(RESET_FROM[kind])
        later = set(CANONICAL[start:])
        gone = [m for m in record.get("milestones") or [] if m["name"] in later]
        if gone:
            record.setdefault("superseded_milestones", []).extend(gone[-40:])
        record["milestones"] = [m for m in record.get("milestones") or []
                                if m["name"] not in later]
        return record

    def _evidence_problems(self, record, to):
        """What the job lacks to be in state `to` — checked on every transition."""
        have = self.milestones(record)
        problems = ["milestone {0} not recorded".format(m) for m in STATE_REQUIRES.get(to, ())
                    if m not in have]
        validation = record.get("validation") or {}
        if to in (VALIDATED, TEMPLATE_GENERATED, SAVED, EMAIL_PREPARED, EMAIL_SENDING):
            if validation.get("decision") != "VALID" or not validation.get("passed"):
                problems.append("validation decision is {0}, not VALID".format(
                    validation.get("decision")))
            if (record.get("identity") or {}).get("decision") != "MATCH":
                problems.append("shipment identity is {0}, not MATCH".format(
                    (record.get("identity") or {}).get("decision")))
            g4 = (record.get("request_fields") or {}).get("invoice_no") or {}
            printed = record.get("invoice_candidate") or {}
            allowed = set([printed.get("value")] + list(printed.get("candidates") or []))
            origins = ("bill_of_entry", "bill_of_entry:chosen_at_review")
            if g4.get("origin") == "bill_of_entry:user_reference" and not (allowed - {None}) \
                    and not printed.get("malformed"):
                # No Invoice No. printed: the digits of the Bill of Entry's own
                # User Reference — exactly those, nothing else.
                ref = ((record.get("fields") or {}).get("user_reference") or {})
                if ref.get("status") == "FOUND":
                    allowed = {re.sub(r"[^0-9]", "", str(ref.get("value") or ""))}
                    origins = ("bill_of_entry:user_reference",)
            if g4.get("origin") not in origins or g4.get("value") not in allowed - {None, ""}:
                problems.append("G4 is not an explicit Invoice No. or the User Reference "
                                "printed on the Bill of Entry")
        if to in (FIELDS_EXTRACTED, VALIDATING) and \
                not ((record.get("document") or {}).get("integrity") or {}).get("verified"):
            problems.append("the PDF's integrity is not verified")
        if to in (SAVED, EMAIL_PREPARED, EMAIL_SENDING) and \
                not (record.get("output") or {}).get("verified"):
            problems.append("the output is not verified on disk")
        if to == EMAIL_SENT and not ((record.get("email") or {}).get("accepted_evidence")):
            problems.append("no evidence Microsoft 365 accepted the send")
        if to == EMAIL_CONFIRMED and \
                not ((record.get("email") or {}).get("reconciliation") or {}).get("verified"):
            problems.append("the sent message was not reconciled and verified in the mailbox")
        return problems

    # -- the state machine -----------------------------------------------------
    def transition(self, record, to, progress=None, failure=None, actor=None):
        current = record["state"]
        if to not in TRANSITIONS.get(current, ()):
            raise IllegalTransition("{0} → {1} is not allowed".format(current, to))
        problems = self._evidence_problems(record, to)
        if problems:
            raise InvariantViolation("{0} → {1} refused: {2}".format(
                current, to, "; ".join(problems)))
        record["previous_state"] = current
        record["state"] = to
        record["label"] = LABELS[to]
        if progress:
            record["progress"] = progress
        if failure is not None:
            record["failure"] = failure
        stamp = now_iso()
        if current == QUEUED and not record.get("started_at"):
            record["started_at"] = stamp
        if to in TERMINAL or to in FAILED_STATES:
            record["completed_at"] = stamp
        else:
            record["completed_at"] = None
        record["worker"] = {"host": socket.gethostname(), "pid": os.getpid(),
                            "worker_id": os.environ.get("ATA_WORKER_ID") or
                            (record.get("worker") or {}).get("worker_id")}
        record["history"] = (record.get("history") or [])[-40:] + [
            {"at": stamp, "state": to, "from": current, "attempt": record.get("attempt") or 1}]
        record = self.save(record)
        self.event(record, "STATE_CHANGED", "state", "OK",
                   old_state=current, new_state=to, actor=actor or record.get("actor") or
                   "automation", reason=(progress or "")[:200],
                   failure_code=(failure or {}).get("code") if failure else None,
                   attempt=record.get("attempt") or 1)
        return record

    # -- events ----------------------------------------------------------------
    def _head(self):
        """(seq, hash) of the last chained event; rebuilt from the file if needed."""
        try:
            head = json.loads((self.folder / "events.head").read_text(encoding="utf-8"))
            return int(head["seq"]), head["hash"]
        except Exception:
            pass
        seq, last = 0, "0" * 64
        try:
            with open(self.folder / "events.jsonl", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("hash"):
                        seq, last = int(row.get("seq") or seq + 1), row["hash"]
        except OSError:
            pass
        return seq, last

    def event(self, record, name, stage, status, source="po", evidence=None, **metadata):
        if name not in EVENTS:
            raise ValueError("unknown PO event {0}".format(name))
        at = metadata.pop("at", None)
        actor = metadata.pop("actor", None) or metadata.get("by") or record.get("actor")
        entry = {"event_id": uuid.uuid4().hex[:16], "event": name,
                 "run_id": record.get("run_id"), "po_id": record["po_id"],
                 "correlation_id": record.get("correlation_id") or record.get("po_id"),
                 "attempt": record.get("attempt") or 1, "actor": actor or "automation",
                 "timestamp": at or now_iso(), "stage": stage, "status": status, "source": source,
                 "evidence_reference": evidence, "metadata": _clean(metadata)}
        with self._lock, self.xlock("events", timeout=30.0):
            seq, prev = self._head()
            entry["seq"], entry["prev"] = seq + 1, prev
            entry["hash"] = hashlib.sha256((prev + _canonical(entry)).encode("utf-8")).hexdigest()
            with open(self.folder / "events.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                if FSYNC:
                    os.fsync(handle.fileno())
            write_atomic(self.folder / "events.head",
                         json.dumps({"seq": entry["seq"], "hash": entry["hash"]}))
        for fn in list(self._sink):
            try:
                fn(entry, record)
            except Exception:
                pass
        return entry

    def import_event(self, event):
        """A worker's event, appended to THIS store's chain (its own id and
        timestamp kept; the chain fields are this store's)."""
        entry = {k: v for k, v in _clean(event).items() if k not in ("seq", "prev", "hash")}
        entry["imported"] = True
        with self._lock, self.xlock("events", timeout=30.0):
            seq, prev = self._head()
            entry["seq"], entry["prev"] = seq + 1, prev
            entry["hash"] = hashlib.sha256((prev + _canonical(entry)).encode("utf-8")).hexdigest()
            with open(self.folder / "events.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                if FSYNC:
                    os.fsync(handle.fileno())
            write_atomic(self.folder / "events.head",
                         json.dumps({"seq": entry["seq"], "hash": entry["hash"]}))
        return entry

    def verify_audit(self):
        """
        Recompute the hash chain of events.jsonl. {"ok", "events", "chained",
        "unchained_before_chain", "problem"} — ok is False when an event was
        altered, removed, inserted or reordered after it was written.
        """
        out = {"ok": True, "events": 0, "chained": 0, "unchained_before_chain": 0,
               "problem": None}
        prev, seq = "0" * 64, 0
        try:
            lines = (self.folder / "events.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            out["events"] += 1
            try:
                row = json.loads(line)
            except ValueError:
                return dict(out, ok=False, problem="line {0} is not JSON".format(number))
            if not row.get("hash"):
                if out["chained"]:
                    return dict(out, ok=False, problem="line {0} has no hash after the chain "
                                                       "began".format(number))
                out["unchained_before_chain"] += 1
                continue
            claimed = row.pop("hash")
            if row.get("prev") != prev or int(row.get("seq") or 0) != seq + 1:
                return dict(out, ok=False, problem="line {0} does not follow the event before it "
                                                   "(removed, inserted or reordered)".format(number))
            if hashlib.sha256((prev + _canonical(row)).encode("utf-8")).hexdigest() != claimed:
                return dict(out, ok=False, problem="line {0} was altered after it was written"
                            .format(number))
            prev, seq = claimed, seq + 1
            out["chained"] += 1
        head = self._head()
        if out["chained"] and head != (seq, prev):
            return dict(out, ok=False, problem="the chain head does not match the last event "
                                               "(events were removed from the end)")
        return out

    def events(self, po_id=None, limit=500):
        try:
            lines = (self.folder / "events.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if po_id is None or row.get("po_id") == po_id:
                out.append(row)
        return out[-limit:]

    # -- evidence --------------------------------------------------------------
    def keep_document(self, data):
        """The retrieved PDF, kept by its content hash — written atomically."""
        sha = hashlib.sha256(data).hexdigest()
        path = self.folder / "documents" / (sha + ".pdf")
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            tmp = path.with_name("{0}.{1}.tmp".format(path.name, uuid.uuid4().hex[:8]))
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                if FSYNC:
                    os.fsync(handle.fileno())
            os.replace(str(tmp), str(path))
        return sha, path

    # -- one writer at a time, across processes --------------------------------
    @contextlib.contextmanager
    def xlock(self, name, timeout=20.0, stale_s=None):
        """
        An exclusive lock across processes (and threads): an operating-system
        lock on <folder>/locks/<name>.lock. The OS releases it when its holder
        exits or dies, so a crashed worker never leaves a lock to break.
        TimeoutError when another holder keeps it past `timeout`.
        (`stale_s` is accepted for older callers and ignored.)
        """
        folder = self.folder / "locks"
        folder.mkdir(parents=True, exist_ok=True)
        handle = open(folder / (name + ".lock"), "a+b")
        deadline = time.time() + timeout
        try:
            while True:
                try:
                    _lock_file(handle)
                    break
                except OSError:
                    if time.time() > deadline:
                        raise TimeoutError("the PO {0} lock is held by another process".format(
                            name))
                    time.sleep(0.02)
            try:
                handle.seek(0)
                handle.truncate()
                handle.write("{0} {1} {2}".format(socket.gethostname(), os.getpid(),
                                                  time.time()).encode())
                handle.flush()
            except OSError:
                pass
            try:
                yield
            finally:
                _unlock_file(handle)
        finally:
            handle.close()

    # -- idempotency: one document, one job ------------------------------------
    @staticmethod
    def idempotency_key(doctype_id, reference, identifier, document_hash):
        """The same shipment, the same Bill Entry, the same document bytes."""
        from .extract import normal_reference
        return "|".join([doctype_id, normal_reference(reference), normal_reference(identifier),
                         document_hash or ""])

    def _claims(self):
        try:
            return json.loads((self.folder / "claims.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    def claim(self, key, po_id):
        """
        (True, None) when this job may process the document; (False, holder)
        when another job already holds it — in progress with a live lease, or
        already through to a saved output. A holder that failed, or whose
        worker is gone, releases it.
        """
        with self.xlock("claims"):
            claims = self._claims()
            holder = claims.get(key)
            if holder and holder.get("po_id") != po_id:
                other = self.get(holder["po_id"])
                alive = other is not None and (
                    other["state"] in (SAVED, EMAIL_PREPARED, EMAIL_SENDING, EMAIL_SENT,
                                       EMAIL_CONFIRMED, EMAIL_UNKNOWN, EMAIL_FAILED, NEEDS_REVIEW)
                    or (other["state"] in ACTIVE_STATES + (VALIDATED, TEMPLATE_GENERATED)
                        and not self.lease_stale(other)))
                if alive:
                    return False, dict(holder, state=other["state"])
            claims[key] = {"po_id": po_id, "at": now_iso()}
            write_atomic(self.folder / "claims.json", json.dumps(claims, indent=1))
            return True, None

    @staticmethod
    def lease_stale(record, max_age_s=900):
        """True when the process that held this job is gone (or silent too long)."""
        lease = record.get("lease") or {}
        if not lease:
            return True
        if lease.get("host") == socket.gethostname() and lease.get("pid") == os.getpid():
            return False
        if lease.get("host") == socket.gethostname() and lease.get("pid"):
            try:
                os.kill(int(lease["pid"]), 0)
            except ProcessLookupError:
                return True
            except (PermissionError, OSError, ValueError):
                pass
        return time.time() - float(lease.get("at") or 0) > max_age_s

    # -- the send ledger (idempotency) -----------------------------------------
    def _ledger(self):
        try:
            return json.loads((self.folder / "ledger.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def ledger_key(po_key_, document_hash, template_version, recipient):
        return "|".join([po_key_, document_hash or "", template_version or "",
                         str(recipient or "").lower()])

    def sent_before(self, key):
        return self._ledger().get(key)

    def sent_for_document(self, po_key_):
        return [dict(v, key=k) for k, v in self._ledger().items() if k.startswith(po_key_ + "|")]

    def ledger_write(self, key, entry):
        with self._lock, self.xlock("ledger"):
            data = self._ledger()
            history = (data.get(key) or {}).get("history") or []
            entry = dict(entry, history=(history + [{k: entry.get(k) for k in
                                                     ("status", "at", "po_id", "by")}])[-20:])
            data[key] = _clean(entry)
            write_atomic(self.folder / "ledger.json", json.dumps(data, indent=1))
        return entry

    def reserve(self, key, po_id, by, allow_own=True, authorize=False, extra=None):
        """
        Claim, atomically across processes, the right to send this exact
        document to this recipient: (True, previous) or (False, holder).
        Refused when it is already SENDING/SENT/CONFIRMED/UNKNOWN for another
        job — or for this one, unless `allow_own` and its last attempt
        FAILED — and not `authorize`d as a resend.
        """
        with self._lock:
            with self.xlock("ledger"):
                data = self._ledger()
                current = data.get(key)
                if current and not authorize:
                    own = current.get("po_id") == po_id
                    if current.get("status") in ("SENT", "CONFIRMED", "UNKNOWN") or \
                            (current.get("status") == "SENDING" and not own) or \
                            (own and not allow_own and current.get("status") != "FAILED"):
                        return False, current
                entry = dict({"status": "SENDING", "at": now_iso(), "po_id": po_id, "by": by},
                             **(extra or {}))
                history = (current or {}).get("history") or []
                entry["history"] = (history + [{k: entry.get(k) for k in
                                                ("status", "at", "po_id", "by")}])[-20:]
                data[key] = _clean(entry)
                write_atomic(self.folder / "ledger.json", json.dumps(data, indent=1))
                return True, current
