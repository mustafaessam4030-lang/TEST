"""
The PO service the dashboard and the control plane call. It never blocks a
request on the Hub, a PDF or Graph:

    start()  records the job (QUEUED) and hands it to a launcher, which runs
             it in the background — a `python -m po process` child process
             locally, a worker command remotely. Jobs run one at a time.
    send()   checks the gate, then sends in a background thread; the page
             watches the record move SENDING → SENT → VERIFIED.

Audit: every start, send, block and resend authorization goes through the
`audit` callable — the control plane's audit log when there is one, the PO
store's own audit.jsonl on a single machine. Never a credential or token.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import doctypes, pipeline as P, store as S

HERE = Path(__file__).resolve().parent

KPI_OF = {
    S.QUEUED: "pending",
    S.DISCOVERED: "processing", S.PDF_FOUND: "processing", S.PDF_READ: "processing",
    S.FIELDS_EXTRACTED: "processing", S.VALIDATING: "processing", S.EMAIL_SENDING: "processing",
    S.VALIDATED: "processing",
    S.VALIDATION_FAILED: "validation_required",
    S.TEMPLATE_GENERATED: "generated", S.EMAIL_PREPARED: "generated",
    S.EMAIL_SENT: "sent", S.EMAIL_CONFIRMED: "sent",
    S.NEEDS_REVIEW: "validation_required",
    S.PDF_NOT_FOUND: "failed", S.PDF_UNREADABLE: "failed", S.EXTRACTION_FAILED: "failed",
    S.TEMPLATE_FAILED: "failed", S.EMAIL_FAILED: "failed",
}
KPIS = ("pending", "processing", "validation_required", "generated", "sent", "failed")
PAGE_STATUS = {"pending": "Processing", "processing": "Processing",
               "validation_required": "Validation Required", "generated": "Generated",
               "sent": "Sent", "failed": "Failed"}


def _local_audit(store):
    def audit(action, result="SUCCESS", actor=None, target=None, metadata=None):
        entry = {"at": S.now_iso(), "action": action, "result": result,
                 "actor": (actor or {}).get("work_email") if isinstance(actor, dict) else actor,
                 "target": target, "metadata": S._clean(metadata or {})}
        with open(store.folder / "audit.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    return audit


class SubprocessLauncher(object):
    """One job at a time, each in its own `python -m po process` process."""

    def __init__(self, store, env=None):
        self.store = store
        self.env = env
        self.service = None
        self._queue = []
        self._lock = threading.Lock()
        self._busy = False

    def __call__(self, record):
        with self._lock:
            self._queue.append(record["po_id"])
            if self._busy:
                return
            self._busy = True
        threading.Thread(target=self._drain, daemon=True, name="po-jobs").start()

    def _drain(self):
        while True:
            with self._lock:
                if not self._queue:
                    self._busy = False
                    return
                po_id = self._queue.pop(0)
            env = dict(os.environ, **(self.env or {}))
            env["PO_DATA_DIR"] = str(self.store.folder)
            env["PO_OUTPUT_DIR"] = str(self.store.output_dir)
            try:
                subprocess.run([sys.executable, "-m", "po", "process", "--po-id", po_id],
                               cwd=str(HERE.parent), env=env, timeout=900,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            record = self.store.get(po_id)
            if record and record["state"] in S.ACTIVE_STATES:
                record = P.abandon(self.store, record, "the processing job ended before finishing")
            if self.service is not None:
                self.service.processed(self.store.get(po_id))


class InlineLauncher(object):
    """Runs the pipeline in a thread with a given source — tests and tools."""

    def __init__(self, store, source_factory, config=None):
        self.store, self.source_factory, self.config = store, source_factory, config
        self.service = None

    def __call__(self, record):
        def run():
            try:
                P.process(self.store, self.store.get(record["po_id"]),
                          self.source_factory(record), self.config, sleep=lambda s: None)
            except Exception as error:
                P.abandon(self.store, self.store.get(record["po_id"]), str(error)[:200])
            if self.service is not None:
                self.service.processed(self.store.get(record["po_id"]))
        thread = threading.Thread(target=run, daemon=True, name="po-inline")
        thread.start()
        self.last = thread


class WorkerLauncher(object):
    """Remote: the job goes to an automation worker as a `po_process` command.
    `dispatch(record)` enqueues it and returns the worker id, or raises."""

    stale_queued_s = 180            # no worker picked it up
    stale_active_s = 900            # a worker took it and went silent

    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.service = None

    def __call__(self, record):
        store = self.service.store
        try:
            worker_id = self.dispatch(record)
        except Exception as error:
            current = store.get(record["po_id"])
            current = store.transition(current, S.DISCOVERED, "No worker")
            current = store.transition(current, S.PDF_NOT_FOUND, "No automation worker", P.failure(
                "WORKER_UNAVAILABLE", "pdf_retrieval",
                "No automation worker is online to open the Hub: {0}".format(str(error)[:160]),
                kind="transient"))
            store.event(current, "PDF_NOT_FOUND", "pdf_retrieval", "FAILED",
                        reason="no automation worker is online")
            self.service.processed(current)
            return
        current = store.get(record["po_id"])
        current["worker_id"] = worker_id
        current["progress"] = "Sent to the automation worker…"
        store.save(current)


def card(record):
    """A queue row: what the table shows, nothing more."""
    email = record.get("email") or {}
    validation = record.get("validation") or {}
    output = record.get("output") or {}
    return {
        "po_id": record["po_id"], "reference": record["reference"],
        "number": record.get("number"), "doctype": record["doctype"],
        "identifier": record.get("identifier"),
        "source": (record.get("provenance") or {}).get("source"),
        "verification": (record.get("provenance") or {}).get("verification"),
        "bill_entry": ((record.get("discovery") or {}).get("bill_entry") or {}).get("selected"),
        "clearance": ((record.get("discovery") or {}).get("clearance") or {}).get("found"),
        "supplier": ((record.get("request_fields") or {}).get("supplier") or {}).get("value")
        or (record.get("request") or {}).get("supplier"),
        "document": (record.get("document") or {}).get("filename"),
        "state": record["state"], "label": record.get("label"),
        "progress": record.get("progress"),
        "kpi": KPI_OF.get(record["state"]),
        "validation": ("PASSED" if validation.get("passed") else
                       "FAILED" if validation else None),
        "template": output.get("template_version"),
        "email": email.get("status"),
        "created": record.get("created"), "updated": record.get("updated"),
        "started_by": record.get("started_by"),
        "failed": record["state"] in S.FAILED_STATES,
        "reason": (record.get("failure") or {}).get("detail") or
                  "; ".join(email.get("reasons") or []) or None,
    }


class PoService(object):
    def __init__(self, store=None, launcher=None, mailer_factory=None, audit=None, config=None):
        self.store = store or S.Store()
        self.launcher = launcher or SubprocessLauncher(self.store)
        self.mailer_factory = mailer_factory
        self.audit = audit or _local_audit(self.store)
        self._config = config
        self._threads = []
        if hasattr(self.launcher, "service"):
            self.launcher.service = self

    def processed(self, record):
        """A job reached the end of processing (EMAIL READY or a failure): audit it."""
        if record is None or record.get("_audited_processed"):
            return
        validation = record.get("validation") or {}
        self.audit("PO_PROCESSED", result=record["state"], actor=record.get("started_by"),
                   target=record["po_id"],
                   metadata={"reference": record["reference"], "number": record.get("number"),
                             "document": (record.get("document") or {}).get("filename"),
                             "document_sha256": (record.get("document") or {}).get("sha256"),
                             "validation": "PASSED" if validation.get("passed") else
                             ("FAILED" if validation else "NOT_RUN"),
                             "reasons": validation.get("reasons"),
                             "template": (record.get("output") or {}).get("template_version"),
                             "output": (record.get("output") or {}).get("filename"),
                             "recipient": (record.get("email") or {}).get("recipient"),
                             "run_id": record.get("run_id"),
                             "failure": (record.get("failure") or {}).get("category")})
        record["_audited_processed"] = True
        self.store.save(record)
        self.learn(record)

    def learn(self, record):
        """
        One PO outcome for ATLAS's learning store. VERIFIED only for a send
        found in the mailbox's Sent Items; an accepted send, a generated
        document or a passed validation earns nothing on its own.
        """
        if record is None:
            return False
        try:
            from intelligence import events as intel_events
        except Exception:
            return False
        state = record["state"]
        if state in S.ACTIVE_STATES or state in (S.VALIDATED, S.TEMPLATE_GENERATED, S.SKIPPED):
            return False                # a skip is not an outcome to learn from
        key = "_learned_" + state
        if record.get(key):
            return False
        ok = intel_events.record(
            "po", reference=record["reference"], run_id=record["po_id"],
            po_key=record.get("po_key"), doctype=record.get("doctype"), state=state,
            verified=state == S.EMAIL_CONFIRMED,
            category=None if state in (S.EMAIL_PREPARED, S.EMAIL_SENT, S.EMAIL_CONFIRMED)
            else (record.get("failure") or {}).get("category") or "UNKNOWN_FAILURE",
            template=(record.get("output") or {}).get("template_version"))
        if ok:
            record[key] = True
            self.store.save(record)
        return ok

    def expire_stale(self):
        """Jobs a worker never took, or took and went silent on, end visibly."""
        queued_s = getattr(self.launcher, "stale_queued_s", None)
        active_s = getattr(self.launcher, "stale_active_s", None)
        if not queued_s and not active_s:
            return []
        ended = []
        now = time.time()
        for record in self.store.all(200):
            if record["state"] not in S.ACTIVE_STATES or record["state"] == S.EMAIL_SENDING:
                continue
            age = now - float(record.get("updated_epoch") or record.get("created_epoch") or now)
            limit = queued_s if record["state"] == S.QUEUED else active_s
            if limit and age > limit:
                done = P.abandon(self.store, record,
                                 "no worker picked the job up" if record["state"] == S.QUEUED
                                 else "the worker stopped reporting on the job")
                self.processed(done)
                ended.append(done["po_id"])
        return ended

    def import_from_worker(self, worker_id, po_id, payload):
        """
        A worker's progress on a job it was given. The worker may move a job
        up to EMAIL READY; sending is this server's job alone. Files are taken
        only when their SHA-256 matches what the record says.
        """
        import base64
        import hashlib
        current = self.store.get(po_id)
        if current is None or current.get("worker_id") != worker_id:
            return False, "not this worker's job"
        incoming = payload.get("record") or {}
        state = incoming.get("state")
        if state not in S.LABELS or state in (S.EMAIL_SENDING, S.EMAIL_SENT, S.EMAIL_CONFIRMED):
            return False, "a worker cannot report {0}".format(state)
        files = payload.get("files") or {}
        output = incoming.get("output")
        if output:
            data = files.get("output")
            if data is not None:
                raw = base64.b64decode(data)
                if hashlib.sha256(raw).hexdigest() != output.get("sha256"):
                    return False, "the output does not match its recorded SHA-256"
                target = self.store.output_dir / Path(output["filename"]).name
                self.store.output_dir.mkdir(parents=True, exist_ok=True)
                if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != \
                        output["sha256"]:
                    return False, "an output named {0} already exists".format(target.name)
                target.write_bytes(raw)
                output = dict(output, path=str(target))
            elif not (current.get("output") or {}).get("path"):
                output = dict(output, verified=False)
            else:
                output = dict(output, path=current["output"]["path"])
        document = incoming.get("document")
        if document and files.get("document") is not None:
            raw = base64.b64decode(files["document"])
            if hashlib.sha256(raw).hexdigest() != document.get("sha256"):
                return False, "the document does not match its recorded SHA-256"
            _, path = self.store.keep_document(raw)
            document = dict(document, evidence=str(path))
        for key in ("state", "label", "progress", "hub", "fields", "request_fields", "number",
                    "validation", "email", "failure", "history", "attempts"):
            if key in incoming:
                current[key] = incoming[key]
        if document:
            current["document"] = document
        if output:
            current["output"] = output
        self.store.save(current)
        known = {e.get("event_id") for e in self.store.events(po_id)}
        for event in payload.get("events") or []:
            if event.get("event_id") in known or event.get("event") not in S.EVENTS:
                continue
            with open(self.store.folder / "events.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(S._clean(event), ensure_ascii=False) + "\n")
            for fn in list(self.store._sink):
                try:
                    fn(event, current)
                except Exception:
                    pass
        if state not in S.ACTIVE_STATES:
            self.processed(self.store.get(po_id))
            cfg = self.config()
            if state == S.EMAIL_PREPARED and cfg.get("auto_send"):
                self.send("auto-send", po_id)
        return True, "ok"

    def config(self):
        return self._config if self._config is not None else P.config_from_env()

    # -- reading ---------------------------------------------------------------
    def summary(self, limit=100):
        records = self.store.all(limit)
        counts = {k: 0 for k in KPIS}
        for r in records:
            kpi = KPI_OF.get(r["state"])
            if kpi:
                counts[kpi] += 1
        # The page's status: each document's LATEST job counts (a mismatch
        # later sent correctly is not still "validation required"), the most
        # urgent state wins.
        latest = {}
        for r in records:
            latest.setdefault(r.get("po_key"), r)
        kinds = {KPI_OF.get(r["state"]) for r in latest.values()}
        status = "Ready"
        for kpi in ("processing", "pending", "validation_required", "failed", "generated", "sent"):
            if kpi in kinds:
                status = PAGE_STATUS[kpi]
                break
        cfg = self.config()
        from .mail import configured
        graph_ok, graph_missing = configured()
        doctype = doctypes.get()
        try:
            from .template import manifest
            template = manifest(doctype)["version"]
        except Exception:
            template = None
        return {
            "status": status, "kpis": counts, "jobs": [card(r) for r in records],
            "config": {"recipient": cfg.get("recipient"), "sender": cfg.get("sender"),
                       "auto_send": cfg.get("auto_send"), "doctype": doctype["id"],
                       "doctype_label": doctype["label"], "document": doctype["document"],
                       "number_label": doctype["number_label"],
                       "template": template, "output_dir": str(self.store.output_dir),
                       "graph_configured": graph_ok, "graph_missing": graph_missing,
                       "defaults": {k: v for k, v in (cfg.get("defaults") or {}).items() if v}},
        }

    def detail(self, po_id):
        record = self.store.get(po_id)
        if record is None:
            return None
        out = dict(record)
        out["events"] = self.store.events(po_id)
        out["card"] = card(record)
        from .evidence import stages
        out["stages"] = stages(record)
        out["blocked"] = P.blocked_reasons(self.store, record) \
            if record["state"] in (S.EMAIL_PREPARED, S.EMAIL_FAILED) else None
        doc = record.get("document") or {}
        doc.pop("evidence", None)
        out["document"] = doc or None
        if out.get("output"):
            out["output"] = {k: v for k, v in out["output"].items() if k != "path"}
            out["output"]["folder"] = str(self.store.output_dir)
        return out

    # -- acting ----------------------------------------------------------------
    # A record whose latest job ended here is not picked again by "next Under
    # Clearance record": it is done, waiting for someone, or needs a person.
    # A transient failure (eHub or the worker unreachable) may be picked again.
    RETRYABLE = ("NETWORK_FAILURE", "NAVIGATION_FAILURE", "WORKER_UNAVAILABLE", "UNKNOWN_FAILURE")

    def handled_references(self):
        latest = {}
        for r in self.store.all(500):
            if r.get("reference"):
                latest.setdefault(r["reference"], r)
        return sorted(ref for ref, r in latest.items()
                      if not (r["state"] == S.PDF_NOT_FOUND and
                              (r.get("failure") or {}).get("category") in self.RETRYABLE))

    def start(self, actor, reference, request=None, doctype=None, run_id=None):
        """
        One job: for the eHub record `reference` (its BOL/AWB), or — with no
        reference — for the next record eHub lists as Under Clearance that
        has not been handled yet.
        """
        reference = str(reference or "").strip()
        clean = {k: str(v).strip()[:120] for k, v in (request or {}).items()
                 if k in ("invoice_no", "supplier", "branch", "charge_to", "priority",
                          "reference_note") and v not in (None, "")}
        if not reference:
            clean["skip_references"] = self.handled_references()
        who = actor.get("work_email") if isinstance(actor, dict) else actor
        record = self.store.create(doctypes.get(doctype)["id"], reference[:60], clean,
                                   run_id=run_id, started_by=who)
        self.audit("PO_PROCESS_STARTED", actor=actor, target=record["po_id"],
                   metadata={"reference": reference or None,
                             "mode": "reference" if reference else "next Under Clearance record",
                             "doctype": record["doctype"],
                             "invoice_no": clean.get("invoice_no")})
        self.launcher(record)
        return record

    def send(self, actor, po_id, authorize_resend=False, reason=None, wait=False):
        record = self.store.get(po_id)
        if record is None:
            raise KeyError(po_id)
        who = actor.get("work_email") if isinstance(actor, dict) else actor
        reasons = P.blocked_reasons(self.store, record)
        if reasons:
            self.store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=reasons, by=who)
            self.audit("PO_EMAIL_BLOCKED", result="BLOCKED", actor=actor, target=po_id,
                       metadata={"reasons": reasons})
            return record, "BLOCKED", reasons
        if authorize_resend and not (reason or "").strip():
            return record, "BLOCKED", ["a resend needs a reason, recorded in the audit log"]
        from .mail import GraphMailer, configured
        factory = self.mailer_factory or GraphMailer
        if factory is GraphMailer:
            ok, missing = configured()
            if not ok:
                # Not a failure of this job: email is not set up yet. The job
                # stays ready, and is sent once an admin configures Graph.
                reasons = ["Microsoft 365 email is not configured ({0} not set). Nothing was "
                           "sent; the job stays ready.".format(", ".join(missing))]
                self.store.event(record, "EMAIL_BLOCKED", "email", "BLOCKED", reasons=reasons,
                                 by=who)
                self.audit("PO_EMAIL_BLOCKED", result="NOT_CONFIGURED", actor=actor,
                           target=po_id, metadata={"missing": missing})
                return record, "BLOCKED", reasons
        cfg = self.config()

        def run():
            current = self.store.get(po_id)
            done, outcome = P.send(self.store, current, factory(), by=who,
                                   authorize_resend=authorize_resend, reason=reason,
                                   confirm_wait_s=cfg.get("confirm_wait_s", 45))
            email = done.get("email") or {}
            self.audit("PO_EMAIL_" + outcome, result="SUCCESS" if outcome in ("SENT", "CONFIRMED")
                       else outcome, actor=actor, target=po_id,
                       metadata={"reference": done["reference"], "number": done.get("number"),
                                 "recipient": email.get("recipient"),
                                 "subject": email.get("subject"),
                                 "attachment": email.get("attachment"),
                                 "template": (done.get("output") or {}).get("template_version"),
                                 "validation": "PASSED" if (done.get("validation") or {}).get(
                                     "passed") else "FAILED",
                                 "graph_status": email.get("graph_status"),
                                 "internet_message_id": email.get("internet_message_id"),
                                 "run_id": done.get("run_id"),
                                 "authorized_resend": bool(authorize_resend),
                                 "resend_reason": reason if authorize_resend else None,
                                 "reasons": email.get("reasons")})
            if outcome in ("CONFIRMED", "FAILED"):
                self.learn(self.store.get(po_id))
            self.last_outcome = outcome

        if authorize_resend:
            self.audit("PO_RESEND_AUTHORIZED", actor=actor, target=po_id,
                       metadata={"reason": reason})
        thread = threading.Thread(target=run, daemon=True, name="po-send")
        thread.start()
        self._threads.append(thread)
        if wait:
            thread.join(120)
        return self.store.get(po_id), "STARTED", []

    def reconfirm(self, po_id):
        """A send Graph accepted but Sent Items had not shown yet: look again."""
        record = self.store.get(po_id)
        if record is None or record["state"] != S.EMAIL_SENT:
            return record
        mailer = (self.mailer_factory or __import__("po.mail", fromlist=["GraphMailer"])
                  .GraphMailer)()
        found = mailer.find_sent((record.get("email") or {}).get("internet_message_id"))
        if found:
            record["email"].update(status="CONFIRMED", confirmed_at=found.get("sentDateTime"),
                                   confirmation="Found in the mailbox's Sent Items.")
            record = self.store.transition(record, S.EMAIL_CONFIRMED, "Completed")
            self.store.event(record, "EMAIL_CONFIRMED", "email", "OK",
                             sent_at=found.get("sentDateTime"))
            self.store.event(record, "PO_COMPLETED", "complete", "OK")
            self.audit("PO_EMAIL_CONFIRMED", target=po_id,
                       metadata={"reference": record["reference"],
                                 "recipient": record["email"].get("recipient"),
                                 "run_id": record.get("run_id")})
            self.learn(record)
        return record

    def wait_idle(self, timeout=60):
        """Tests: wait for background sends to finish."""
        deadline = time.time() + timeout
        for thread in list(self._threads):
            thread.join(max(0, deadline - time.time()))


# The service this process serves — set by the dashboard or the control
# plane when it starts, read by ATLAS. Without one, a read-only service over
# the default store, so ATLAS can still answer from records on disk.
_CURRENT = {"service": None}


def register(service):
    _CURRENT["service"] = service
    return service


def current():
    if _CURRENT["service"] is None:
        service = PoService(launcher=lambda record: None)
        service.read_only = True
        _CURRENT["service"] = service
    return _CURRENT["service"]


def local():
    """The single-machine dashboard's service: jobs as child processes."""
    service = _CURRENT["service"]
    if service is None or getattr(service, "read_only", False):
        store = S.Store()
        service = register(PoService(store=store, launcher=SubprocessLauncher(store)))
    return service
