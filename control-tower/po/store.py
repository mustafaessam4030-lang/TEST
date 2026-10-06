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

import hashlib
import json
import os
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
# (SKIPPED), or a person must choose the document (NEEDS_REVIEW).
SKIPPED = "SKIPPED"
NEEDS_REVIEW = "NEEDS_REVIEW"

FAILED_STATES = (PDF_NOT_FOUND, PDF_UNREADABLE, EXTRACTION_FAILED, VALIDATION_FAILED,
                 TEMPLATE_FAILED, EMAIL_FAILED)
ACTIVE_STATES = (QUEUED, DISCOVERED, PDF_FOUND, PDF_READ, FIELDS_EXTRACTED, VALIDATING,
                 EMAIL_SENDING)

TRANSITIONS = {
    QUEUED: (DISCOVERED, PDF_NOT_FOUND),
    DISCOVERED: (PDF_FOUND, PDF_NOT_FOUND, SKIPPED, NEEDS_REVIEW),
    PDF_FOUND: (PDF_READ, PDF_UNREADABLE),
    PDF_READ: (FIELDS_EXTRACTED, EXTRACTION_FAILED),
    FIELDS_EXTRACTED: (VALIDATING,),
    VALIDATING: (VALIDATED, VALIDATION_FAILED),
    VALIDATED: (TEMPLATE_GENERATED, TEMPLATE_FAILED),
    # Generated (filled and read back in memory) is not saved: SAVED only once
    # the file is in the output folder and read back from disk.
    TEMPLATE_GENERATED: (SAVED, TEMPLATE_FAILED),
    SAVED: (EMAIL_PREPARED,),
    EMAIL_PREPARED: (EMAIL_SENDING,),
    EMAIL_SENDING: (EMAIL_SENT, EMAIL_FAILED, EMAIL_CONFIRMED),
    EMAIL_SENT: (EMAIL_CONFIRMED,),
    # A failed send whose cause was transient may be sent again — through
    # the ledger, on the same prepared message.
    EMAIL_FAILED: (EMAIL_SENDING,),
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
}

EVENTS = ("PO_DISCOVERED", "EHUB_RECORD_FOUND", "CLEARANCE_CHECKED", "RECORD_SKIPPED",
          "MANAGE_OPENED", "DOCUMENTS_SECTION_FOUND", "BILL_ENTRY_FOUND", "IDENTIFIER_EXTRACTED",
          "BILL_ENTRY_DOWNLOADED", "DOCUMENT_REVIEW_REQUIRED", "PDF_FOUND", "PDF_NOT_FOUND", "PDF_READ", "PDF_UNREADABLE",
          "FIELDS_EXTRACTED", "EXTRACTION_FAILED", "VALIDATION_STARTED", "VALIDATION_PASSED",
          "VALIDATION_FAILED", "TEMPLATE_GENERATION_STARTED", "TEMPLATE_GENERATED",
          "TEMPLATE_FAILED", "OUTPUT_SAVED", "EMAIL_PREPARED", "EMAIL_BLOCKED", "EMAIL_SEND_STARTED",
          "EMAIL_SENT", "EMAIL_CONFIRMED", "EMAIL_FAILED", "PO_COMPLETED", "RETRY")

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
        with self._lock:
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

    def reserve(self, key, po_id, by):
        """
        Claim the right to send this exact document to this recipient. False
        when it is already SENT/CONFIRMED or another send holds it.
        """
        with self._lock:
            current = self._ledger().get(key)
            if current and current.get("status") in ("SENDING", "SENT", "CONFIRMED"):
                return False, current
            self.ledger_write(key, {"status": "SENDING", "at": now_iso(), "po_id": po_id,
                                    "by": by})
            return True, None
