"""
Sandbox PO email: strict isolation, sender/recipient safety, read-only preview.

    python test_po_sandbox.py

Mocked Graph only: a local HTTP stand-in on 127.0.0.1 (fixtures/po_engine.py
GraphStub). No real mailbox is contacted and no email leaves this machine.
TEST DATA: sanitised fixtures.
"""

import hashlib
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "fixtures")]
WORK = Path(tempfile.mkdtemp(prefix="ct_sandbox_"))
PROD, BOX = WORK / "prod", WORK / "sandbox"
SENDER, ME = "controltower@mantracgroup.com", "MEMabrouk@mantracgroup.com"
for k in ("PO_ALLOW_TEST_SEND", "PO_PRODUCTION_EMAIL", "PO_SIGNING_ORDER"):
    os.environ.pop(k, None)
os.environ.update(PO_DATA_DIR=str(PROD), PO_OUTPUT_DIR=str(PROD / "output"),
                  PO_SANDBOX_DIR=str(BOX), PO_EMAIL_MODE="sandbox",
                  PO_SANDBOX_RECIPIENTS=ME.lower())

import po_engine as F                                       # noqa: E402
from icums_boe import icums_boe_pdf                         # noqa: E402
from po import authorize as A, doctypes, mail as M, pipeline as P, sandbox as SB  # noqa
from po import store as S                                   # noqa: E402
from po.__main__ import main as cli                         # noqa: E402

GRAPH = F.GraphStub()
GRAPH_ENV = dict(GRAPH.env(), PO_MAIL_SENDER=SENDER)
os.environ.update(GRAPH_ENV)
PASS, FAIL = [], []
CONFIG = {"recipient": None, "sender": None, "auto_send": False,
          "defaults": {"supplier": None, "branch": None, "charge_to": None, "priority": None},
          "confirm_wait_s": 0, "email_disabled": True}


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "  ({0})".format(str(detail)[:400]) if detail and not ok else ""))


def rule(title):
    print("\n" + "=" * 74 + "\n" + title + "\n" + "=" * 74)


def snapshot(folder):
    """Every file under a folder with its content hash (missing folder: {})."""
    folder = Path(folder)
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file() and "locks" not in p.parts} \
        if folder.exists() else {}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli(list(argv))
    return code, out.getvalue() + err.getvalue()


def production_job(data=None, verified=True):
    os.environ["PO_EMAIL_MODE"] = "production"
    st = S.Store(folder=PROD, output_dir=PROD / "output")
    rec = st.create(doctypes.DEFAULT, "J552493", {})
    rec = P.process(st, rec, F.Source(data or icums_boe_pdf(), ref="J552493"), CONFIG,
                    sleep=lambda s: None)
    if verified and rec["state"] == S.SAVED:
        rec["provenance"] = dict(rec.get("provenance") or {}, source="REAL",
                                 verification="VERIFIED")
        st.save(rec)
        rec = st.get(rec["po_id"])
    os.environ["PO_EMAIL_MODE"] = "sandbox"
    return st, rec


def graph_calls():
    return GRAPH.state["counter"], len(GRAPH.state["sent"])


rule("1. A SANDBOX COPY — PRODUCTION IS ONLY READ")
prod, job = production_job()
check("A production job reaches SAVED (validated, template saved and read back)",
      job["state"] == S.SAVED, job["state"])
before_prod = snapshot(PROD)
os.environ["PO_EMAIL_MODE"] = "production"
copy, why = SB.copy_job(job, PROD)
check("In production mode no sandbox copy is made", copy is None and why, why)
os.environ["PO_EMAIL_MODE"] = "sandbox"
code, out = run("sandbox-copy", job["po_id"])
check("'sandbox-copy' copies the job into the sandbox folder", code == 0 and str(BOX) in out, out)
sbx = SB.store()
copy = sbx.get(job["po_id"])
check("...with its own record, PDF and generated PO, marked as a sandbox copy",
      copy and copy["sandbox"]["source_po_id"] == job["po_id"] and
      (BOX / "documents" / (job["document"]["sha256"] + ".pdf")).is_file() and
      Path(copy["output"]["path"]).parent == BOX / "output" and
      Path(copy["output"]["path"]).read_bytes() == Path(job["output"]["path"]).read_bytes())
check("...its own audit trail (the copy is recorded there, not in production)",
      any(e["event"] == "SANDBOX_COPIED" for e in sbx.events(job["po_id"])) and
      not any(e["event"] == "SANDBOX_COPIED" for e in prod.events(job["po_id"])))
check("Production is byte-for-byte unchanged by the copy", snapshot(PROD) == before_prod)
code, out = run("sandbox-copy", job["po_id"])
check("A second copy of the same job is refused", code == 1 and "already exists" in out, out)
_p, failed = production_job(icums_boe_pdf(total="653,700.54"))
_c, why = SB.copy_job(failed, PROD)
check("A job whose validation failed is never copied", _c is None and
      any("only a SAVED job" in w for w in why), why)

rule("2. THE PREVIEW IS READ-ONLY — job, audit, authorization, ledger untouched")
before_box, before_prod = snapshot(BOX), snapshot(PROD)
calls = graph_calls()
code, out = run("email-preview", job["po_id"], "--to", ME)
check("'email-preview' shows sender, recipient, subject, body, attachment and validation",
      code == 0 and "FROM        " + SENDER in out and "TO          " + ME in out and
      "SUBJECT     [SANDBOX] Duty Payment Request — 40726534505 — J552493" in out and
      copy["output"]["filename"] in out and copy["output"]["sha256"] in out and
      "VALIDATION  VALID" in out and "read back as this job's: yes" in out and
      "Please find attached" in out, out)
check("...says the sender's mailbox type is UNVERIFIED (a distribution list cannot send)",
      "UNVERIFIED" in out and "distribution list" in out)
check("...and the only thing missing is a named person's authorization",
      "no one has authorized this send" in out and out.count("\n  - ") == 1, out)
check("Nothing was written: the sandbox and production folders are byte-for-byte the same, "
      "and Graph was not called", snapshot(BOX) == before_box and
      snapshot(PROD) == before_prod and graph_calls() == calls)
pv = P.email_preview(sbx, sbx.get(job["po_id"]), "someone@example.com")
check("A preview for a recipient off the allowlist says so",
      any("not on the sandbox recipient allowlist" in r for r in pv["blocking"]), pv["blocking"])

rule("3. RECIPIENT, SENDER AND GRAPH SAFEGUARDS — refused before any Graph request")
calls = graph_calls()
code, out = run("authorize-send", job["po_id"], "--to", "real.person@mantracgroup.com",
                "--by", "Tester", "--confirm", "SEND")
check("A recipient off the allowlist: STOPPED before any Graph request",
      code == 1 and "not on the sandbox recipient allowlist" in out and graph_calls() == calls,
      out)
saved = {k: os.environ.pop(k) for k in ("GRAPH_CLIENT_SECRET",)}
code, out = run("authorize-send", job["po_id"], "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
os.environ.update(saved)
check("Graph not configured: STOPPED, naming what is missing, no Graph request",
      code == 1 and "GRAPH_CLIENT_SECRET" in out and graph_calls() == calls, out)
code, out = run("authorize-send", job["po_id"], "--to", ME, "--by", "Tester", "--confirm", "yes")
check("Without typing SEND: STOPPED", code == 1 and "--confirm SEND" in out)
os.environ["PO_SANDBOX_RECIPIENTS"] = ""
code, out = run("authorize-send", job["po_id"], "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
os.environ["PO_SANDBOX_RECIPIENTS"] = ME.lower()
check("An empty allowlist sends to no one", code == 1 and graph_calls() == calls, out)
os.environ["PO_EMAIL_MODE"] = "sandbx"
code, out = run("authorize-send", job["po_id"], "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
check("An unknown PO_EMAIL_MODE fails closed", code == 1 and graph_calls() == calls, out)
os.environ["PO_EMAIL_MODE"] = "production"
code, out = run("authorize-send", job["po_id"], "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
check("Production mode: the command refuses to send at all (production email disabled)",
      code == 1 and "only in sandbox mode" in out and graph_calls() == calls, out)
os.environ["PO_EMAIL_MODE"] = "sandbox"
os.environ["PO_SANDBOX_DIR"] = str(PROD)
code, out = run("authorize-send", job["po_id"], "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
code2, out2 = run("sandbox-copy", job["po_id"])
check("A sandbox folder pointed at the production folder: everything refused",
      code == 1 and code2 == 1 and "overlaps the production PO folder" in out + out2 and
      graph_calls() == calls, out + out2)
os.environ["PO_SANDBOX_DIR"] = str(PROD / "nested")
check("...also a folder inside production", bool(SB.isolation_problems()))
os.environ["PO_SANDBOX_DIR"] = str(BOX)
code, out = run("authorize-send", "40726599999", "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
check("Sandbox mode looks for the job ONLY in the sandbox folder (a production-only job is "
      "not found)", code == 2 and str(PROD) not in out.split("searched")[-1], out)
prepared, _w = P.prepare_email(SB.store(), SB.store().get(job["po_id"]), ME)
os.environ["PO_MAIL_SENDER"] = "someone.else@mantracgroup.com"
code, out = run("authorize-send", job["po_id"], "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
os.environ["PO_MAIL_SENDER"] = SENDER
check("A sender that changed after the email was prepared: refused, no Graph request",
      code == 1 and "sender" in out and graph_calls() == calls, out)

rule("4. ONE SANDBOX EMAIL — to the local Graph stand-in, verified, not called 'delivered'")
sbx = SB.store()
fresh_copy = sbx.get(job["po_id"])
if fresh_copy["state"] != S.SAVED:
    # The sender test prepared it (for the right sender): start again from a clean copy.
    import shutil
    shutil.rmtree(BOX)
    run("sandbox-copy", job["po_id"])
    sbx = SB.store()
before_prod = snapshot(PROD)
code, out = run("authorize-send", job["po_id"], "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
sent = GRAPH.state["sent"]
check("Sent once through (mocked) Graph, found in Sent Items and matching",
      code == 0 and len(sent) == 1 and "IN SENT ITEMS and matching" in out, out)
m = sent[0] if sent else {}
check("...from the configured sender, to the allowlisted recipient, with the [SANDBOX] subject "
      "and the job's own generated PO attached",
      m.get("mailbox") == SENDER and str(m.get("to")).lower().find(ME.lower()) >= 0 and
      str(m.get("subject", "")).startswith("[SANDBOX] "), m and {k: m.get(k) for k in (
          "mailbox", "to", "subject")})
check("...reported as 'not proof of delivery', never as delivered",
      "not proof of delivery" in out and "delivered" not in out.replace("not proof of delivery",
                                                                       ""), out)
rec = sbx.get(job["po_id"])
auth = A.read(sbx, job["po_id"])
check("The authorization bound sender, recipient, subject and attachment fingerprint, and is "
      "used up", auth["sender"] == SENDER and auth["recipient"] == ME and
      auth["subject"].startswith("[SANDBOX] ") and
      auth["attachment_sha256"] == rec["output"]["sha256"] and auth["used_at"])
check("The send is on the SANDBOX audit trail and ledger; production is byte-for-byte unchanged",
      any(e["event"] == "SEND_AUTHORIZED" for e in sbx.events(job["po_id"])) and
      (BOX / "ledger.json").is_file() and snapshot(PROD) == before_prod and
      prod.get(job["po_id"])["state"] == S.SAVED)
calls = graph_calls()
code, out = run("authorize-send", job["po_id"], "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
check("A duplicate send is refused, no second email",
      code == 1 and len(GRAPH.state["sent"]) == 1, out)

rule("5. TAMPERING, VALIDATION, AND THE TEST SWITCH")
_p2, job2 = production_job()
SB.copy_job(job2, PROD)
c2 = sbx.get(job2["po_id"])
Path(c2["output"]["path"]).write_bytes(b"tampered")
calls = graph_calls()
code, out = run("authorize-send", job2["po_id"], "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
check("A tampered attachment: STOPPED before any Graph request, the email not prepared",
      code == 1 and graph_calls() == calls and "not prepared" in out, out)
_p3, job3 = production_job(verified=False)
SB.copy_job(job3, PROD)
code, out = run("authorize-send", job3["po_id"], "--to", ME, "--by", "Tester", "--confirm",
                "SEND")
check("A document not observed in the real eHub: the sandbox keeps that check too",
      code == 1 and "real eHub" in out and graph_calls() == calls, out)
real = M.GraphMailer(graph="https://graph.microsoft.com/v1.0", sender=SENDER)
os.environ["PO_ALLOW_TEST_SEND"] = "1"
os.environ["PO_EMAIL_MODE"] = "production"
_p4, job4 = production_job()
os.environ["PO_EMAIL_MODE"] = "production"
j4, _w = P.prepare_email(_p4, job4, ME)
reasons = P.blocked_reasons(_p4, j4, mailer=real)
check("PO_ALLOW_TEST_SEND=1 with the REAL Graph changes nothing: production stays disabled, "
      "authorization still required", any("production PO email is disabled" in r
                                          for r in reasons) and
      any("authorized" in r for r in reasons), reasons)
check("...it is honoured only for a mailer that cannot reach a real mailbox",
      SB.is_test_mailer(M.GraphMailer(graph=GRAPH.url + "/v1.0")) and not
      SB.is_test_mailer(real) and not SB.is_test_mailer(None))
os.environ["PO_EMAIL_MODE"] = "sandbox"
check("...and never in sandbox mode", not SB.test_bypass(M.GraphMailer(graph=GRAPH.url)))
os.environ.pop("PO_ALLOW_TEST_SEND")
check("In production mode, a sandbox copy is never sendable",
      any("sandbox copy is never sent" in r for r in (
          os.environ.__setitem__("PO_EMAIL_MODE", "production") or
          SB.send_problems(sbx, sbx.get(job["po_id"]), None))))
os.environ["PO_EMAIL_MODE"] = "sandbox"

GRAPH.stop()
print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
