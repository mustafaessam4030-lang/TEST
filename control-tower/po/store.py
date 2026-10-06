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

Never stored: credentials, tokens, the email body beyond its subject and
recipient. Event metadata passes through the same redaction as ATLAS's
intelligence store.
"""

import contextlib
import hashlib
import json
import os
import socket
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
# Why a record was not processed (SKIPPED.skip_reason).
SKIP_REASONS = ("SKIPPED_NOT_UNDER_CLEARANCE", "SKIPPED_ALREADY_PROCESSED",
                "SKIPPED_MISSING_REQUIRED_REFERENCE", "SKIPPED_DUPLICATE",
                "SKIPPED_STATUS_CHANGED")

FAILED_STATES = (PDF_NOT_FOUND, PDF_UNREADABLE, EXTRACTION_FAILED, VALIDATION_FAILED,
                 TEMPLATE_FAILED, EMAIL_FAILED, DISCOVERY_FAILED, AUTH_REQUIRED,
                 MANAGE_NAVIGATION_FAILED, DOCUMENT_AMBIGUOUS, PDF_DOWNLOAD_FAILED, SAVE_FAILED)
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
                 WORKER_DISCONNECTED),
    PDF_FOUND: (PDF_READ, PDF_UNREADABLE, SKIPPED, WORKER_DISCONNECTED),
    PDF_READ: (FIELDS_EXTRACTED, EXTRACTION_FAILED, WORKER_DISCONNECTED),
    FIELDS_EXTRACTED: (VALIDATING, WORKER_DISCONNECTED),
    # NEEDS_REVIEW from validation: only a value whose source is not proven
    # (G4) is missing — a person supplies it; everything else is a failure.
    VALIDATING: (VALIDATED, VALIDATION_FAILED, NEEDS_REVIEW, WORKER_DISCONNECTED),
    VALIDATED: (TEMPLATE_GENERATED, TEMPLATE_FAILED, WORKER_DISCONNECTED),
    # Generated (filled and read back in memory) is not saved: SAVED only once
    # the file is in the output folder and read back from disk.
    TEMPLATE_GENERATED: (SAVED, TEMPLATE_FAILED, SAVE_FAILED, WORKER_DISCONNECTED),
    SAVED: (EMAIL_PREPARED, WORKER_DISCONNECTED),
    EMAIL_PREPARED: (EMAIL_SENDING,),
    EMAIL_SENDING: (EMAIL_SENT, EMAIL_FAILED, EMAIL_CONFIRMED, EMAIL_UNKNOWN),
    # An explicitly authorized resend (who and why on the ledger) starts a
    # new submission of the same verified document.
    EMAIL_SENT: (EMAIL_CONFIRMED, EMAIL_SENDING),
    # A failed send whose cause was transient may be sent again — through
    # the ledger, on the same prepared message.
    EMAIL_FAILED: (EMAIL_SENDING,),
    EMAIL_CONFIRMED: (EMAIL_SENDING,),
    # Reconciled against the mailbox: sent (found in Sent Items), still a
    # draft (safe to send), or gone without trace (failed — a person decides).
    EMAIL_UNKNOWN: (EMAIL_SENT, EMAIL_CONFIRMED, EMAIL_SENDING, EMAIL_FAILED),
    # A person supplied the missing value: validation runs again in full.
    NEEDS_REVIEW: (VALIDATING,),
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
    NEEDS_REVIEW: "Check the Bill of Entry and supply the Supplier invoice No. (G4) in the "
                  "drawer; validation then runs again in full.",
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
          "STAGE_TIMED")

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
        with self._lock:
            record["updated"] = now_iso()
            record["updated_epoch"] = round(time.time(), 3)
            # Who is working the job, renewed on every write: a job whose
            # holder is gone is recoverable (lease_stale).
            record["lease"] = {"pid": os.getpid(), "host": socket.gethostname(),
                               "at": record["updated_epoch"]}
            path = self._path(record["po_id"])
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(_clean(record), indent=1, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(str(tmp), str(path))
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
        }
        return self.save(record)

    # -- the state machine -----------------------------------------------------
    def transition(self, record, to, progress=None, failure=None):
        current = record["state"]
        if to not in TRANSITIONS.get(current, ()):
            raise IllegalTransition("{0} → {1} is not allowed".format(current, to))
        record["state"] = to
        record["label"] = LABELS[to]
        if progress:
            record["progress"] = progress
        if failure is not None:
            record["failure"] = failure
        record["history"] = (record.get("history") or [])[-40:] + [{"at": now_iso(), "state": to}]
        return self.save(record)

    # -- events ----------------------------------------------------------------
    def event(self, record, name, stage, status, source="po", evidence=None, **metadata):
        if name not in EVENTS:
            raise ValueError("unknown PO event {0}".format(name))
        at = metadata.pop("at", None)
        entry = {"event_id": uuid.uuid4().hex[:16], "event": name,
                 "run_id": record.get("run_id"), "po_id": record["po_id"],
                 "timestamp": at or now_iso(), "stage": stage, "status": status, "source": source,
                 "evidence_reference": evidence, "metadata": _clean(metadata)}
        with self._lock:
            with open(self.folder / "events.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        for fn in list(self._sink):
            try:
                fn(entry, record)
            except Exception:
                pass
        return entry

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
        sha = hashlib.sha256(data).hexdigest()
        path = self.folder / "documents" / (sha + ".pdf")
        if not path.exists():
            path.write_bytes(data)
        return sha, path

    # -- one writer at a time, across processes --------------------------------
    @contextlib.contextmanager
    def xlock(self, name, timeout=20.0, stale_s=60.0):
        """
        A lock file, created exclusively: the ledger and the claims are
        written by one process at a time (the sweep, the dashboard's send,
        a recovery). A lock older than `stale_s` is a dead holder's.
        """
        path = self.folder / (name + ".lock")
        deadline = time.time() + timeout
        while True:
            try:
                fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.write(fd, "{0} {1}".format(os.getpid(), time.time()).encode())
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - path.stat().st_mtime > stale_s:
                        path.unlink()
                        continue
                except OSError:
                    continue
                if time.time() > deadline:
                    raise TimeoutError("the PO {0} lock is held by another process".format(name))
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                path.unlink()
            except OSError:
                pass

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
            path = self.folder / "claims.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(claims, indent=1), encoding="utf-8")
            os.replace(str(tmp), str(path))
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
            path = self.folder / "ledger.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(str(tmp), str(path))
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
                path = self.folder / "ledger.json"
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
                os.replace(str(tmp), str(path))
                return True, current
