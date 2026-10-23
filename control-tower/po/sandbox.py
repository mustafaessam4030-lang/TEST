"""
Sandbox PO email: a COPY of a saved job, in its own folder, sent only to an
allowlisted recipient — production is never read for writing and never sent.

    PO_EMAIL_MODE          production (default) | sandbox. Anything else:
                           every send is refused (fail closed).
    PO_SANDBOX_DIR         the sandbox job folder (default <install>/sandbox/po).
                           It may not be, contain or sit inside a production
                           PO folder — then nothing runs.
    PO_SANDBOX_RECIPIENTS  the only addresses a sandbox email may go to
                           (comma-separated). Empty: nothing is sent.
    PO_MAIL_SENDER         the mailbox Graph sends from; bound into the
                           authorization and checked in Sent Items.
    PO_PRODUCTION_EMAIL    "enabled" lifts the production block for the REAL
                           Graph. Until then a production PO email is refused.

The sandbox copy has its own job record, PDF, generated document, state,
audit trail, authorizations and duplicate-send ledger (all live under the
store's folder). The production job is only read.

PO_ALLOW_TEST_SEND=1 is honoured only for a mailer that cannot reach a real
mailbox (an in-process stand-in, or Graph on this machine's loopback). With
the real Microsoft Graph it changes nothing.
"""

import json
import os
import shutil
import urllib.parse
from pathlib import Path

from . import store as S

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "[SANDBOX] "
MODES = ("production", "sandbox")


def mode():
    raw = (os.environ.get("PO_EMAIL_MODE") or "production").strip().lower()
    return raw if raw in MODES else "invalid"


def allowlist():
    raw = os.environ.get("PO_SANDBOX_RECIPIENTS") or ""
    return sorted({a.strip().lower() for a in raw.replace(";", ",").split(",") if "@" in a})


def production_folders():
    """Every folder this install keeps production PO jobs or output in."""
    runtime = Path(os.environ.get("ATA_RUNTIME_DIR") or ROOT / "dashboard" / ".runtime")
    out = []
    for f in (os.environ.get("PO_DATA_DIR"), S.DEFAULT_DIR, runtime / "po",
              ROOT / "controlplane" / "data" / "po", os.environ.get("PO_OUTPUT_DIR")):
        if f:
            out.append(Path(f).resolve())
    return out


def folder():
    return Path(os.environ.get("PO_SANDBOX_DIR") or ROOT / "sandbox" / "po").resolve()


def _overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


def isolation_problems():
    """Why the sandbox folder is not safely apart from production ([] = it is)."""
    box = folder()
    return ["the sandbox folder {0} overlaps the production PO folder {1}".format(box, p)
            for p in production_folders() if _overlaps(box, p)]


def is_sandbox_folder(path):
    try:
        return Path(path).resolve() == folder()
    except Exception:
        return False


def store():
    """The sandbox store — refused (ValueError) unless the folder is isolated."""
    problems = isolation_problems()
    if problems:
        raise ValueError("; ".join(problems))
    box = folder()
    return S.Store(folder=box, output_dir=box / "output")


def is_test_mailer(mailer):
    """A mailer that cannot reach a real mailbox: an in-process stand-in, or Graph
    on this machine's loopback (the test suite's local server)."""
    if mailer is None:
        return False
    graph = getattr(mailer, "graph", None)
    if graph is None:
        from .mail import GraphMailer
        return not isinstance(mailer, GraphMailer)
    host = (urllib.parse.urlparse(str(graph)).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def test_bypass(mailer):
    """PO_ALLOW_TEST_SEND=1 — only ever for a test mailer, and never in sandbox mode."""
    return os.environ.get("PO_ALLOW_TEST_SEND") == "1" and is_test_mailer(mailer) and \
        mode() == "production"


def send_problems(store_, record, mailer=None):
    """The mode, isolation, recipient and sender rules — before any Graph request.
    mailer=None (a preview or a display) is judged as the real Graph."""
    reasons, m = [], mode()
    email = record.get("email") or {}
    real = mailer is None or not is_test_mailer(mailer)
    sbx_record = bool(record.get("sandbox"))
    sbx_store = is_sandbox_folder(store_.folder)
    if m == "invalid":
        reasons.append("PO_EMAIL_MODE={0!r} is neither 'production' nor 'sandbox' — nothing is "
                       "sent".format(os.environ.get("PO_EMAIL_MODE")))
    elif m == "sandbox":
        reasons += isolation_problems()
        if not (sbx_record and sbx_store):
            reasons.append("sandbox mode sends only a sandbox copy, from the sandbox folder")
        allowed = allowlist()
        rcpt = str(email.get("recipient") or "").strip().lower()
        if not allowed:
            reasons.append("no sandbox recipients are configured (PO_SANDBOX_RECIPIENTS)")
        elif rcpt not in allowed:
            reasons.append("{0} is not on the sandbox recipient allowlist".format(
                email.get("recipient")))
        if not str(email.get("subject") or "").startswith(PREFIX):
            reasons.append("a sandbox email's subject must start with " + PREFIX.strip())
    else:
        if sbx_record or sbx_store:
            reasons.append("a sandbox copy is never sent in production mode")
        if real and os.environ.get("PO_PRODUCTION_EMAIL") != "enabled":
            reasons.append("production PO email is disabled until the sandbox review is signed "
                           "off (PO_PRODUCTION_EMAIL)")
    if not test_bypass(mailer):
        expected = (os.environ.get("PO_MAIL_SENDER") or "").strip().lower()
        if not expected:
            reasons.append("no sender mailbox is configured (PO_MAIL_SENDER)")
        else:
            if str(email.get("sender") or "").strip().lower() != expected:
                reasons.append("the email was prepared for sender {0}, not {1}".format(
                    email.get("sender"), expected))
            actual = getattr(mailer, "sender", None) if mailer is not None else expected
            if str(actual or "").strip().lower() != expected:
                reasons.append("the mailer sends as {0}, not {1}".format(actual, expected))
    return reasons


def copy_job(record, source_folder):
    """A sandbox copy of a SAVED production job: its record, kept PDF and
    generated document, in the sandbox folder. The production job is only
    read. -> (copy, reasons)."""
    reasons = []
    if mode() != "sandbox":
        reasons.append("PO_EMAIL_MODE is not 'sandbox'")
    reasons += isolation_problems()
    if is_sandbox_folder(source_folder):
        reasons.append("that job is already a sandbox copy")
    if record.get("state") != S.SAVED:
        reasons.append("the job is {0} — only a SAVED job (validated, template saved and read "
                       "back) is copied".format(S.LABELS.get(record.get("state"),
                                                             record.get("state"))))
    sha = str((record.get("document") or {}).get("sha256") or "")
    pdf = Path(source_folder) / "documents" / (sha + ".pdf")
    out = record.get("output") or {}
    made = Path(out.get("path") or "")
    if not sha or not pdf.is_file():
        reasons.append("the job's kept PDF is missing")
    if not out.get("filename") or not made.is_file():
        reasons.append("the job's generated document is missing")
    if reasons:
        return None, reasons
    box = store()
    if box.get(record["po_id"]):
        return None, ["a sandbox copy of {0} already exists — use it, or delete the sandbox "
                      "folder to start again".format(record["po_id"])]
    copy = json.loads(json.dumps(record))
    shutil.copy2(pdf, Path(box.folder) / "documents" / (sha + ".pdf"))
    Path(box.output_dir).mkdir(parents=True, exist_ok=True)
    dest = Path(box.output_dir) / out["filename"]
    shutil.copy2(made, dest)
    copy["output"]["path"] = str(dest)
    copy["sandbox"] = {"source_po_id": record["po_id"], "source_folder": str(source_folder),
                       "copied_at": S.now_iso()}
    box.save(copy)
    copy = box.get(copy["po_id"])
    box.event(copy, "SANDBOX_COPIED", "email", "OK", source_folder=str(source_folder),
              document_sha256=sha, output_sha256=out.get("sha256"))
    return copy, []
