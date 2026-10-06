"""
PO Automation — the production-hardening test matrix.

What is real here and what is not, exactly:

  REAL CODE     the PO store and state machine, the pipeline (process,
                from_document, validate_onward, send, reconcile, interrupt,
                resume, recover, supply), extraction, validation, the
                approved template (openpyxl, written to disk and read back),
                the idempotency claims and the send ledger with their
                cross-process locks, quality metrics, the readiness gate,
                the sweep's eligibility decisions
  REAL PDFs     generated here with PyMuPDF (synthetic Bills of Entry —
                NOT documents from eHub)
  MOCKED        the eHub source (a scripted object returning a PDF and a
                discovery trail) and the mailer (a scripted Graph stand-in in
                memory) — so crashes, concurrency and unknown email outcomes
                can be forced exactly. The browser navigation against
                eHub-shaped pages and the HTTP Graph flow are covered in
                test_po.py (TESTED WITH STAND-IN). Nothing here is evidence
                about the real eHub or the real Microsoft 365.

    python test_po_hardening.py
"""

import json
import multiprocessing
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORK = Path(tempfile.mkdtemp(prefix="ct_po_hard_"))
os.environ["PO_DATA_DIR"] = str(WORK / "default")
os.environ["PO_ALLOW_TEST_SEND"] = "1"            # the mocked mailer only
os.environ.setdefault("ATLAS_INTEL_DIR", str(WORK / "intel"))

import fitz                                        # noqa: E402

from po import doctypes, extract as X, pipeline as P, store as S, template as T  # noqa: E402
from po import quality as Q, readiness as RD                                    # noqa: E402
from po import ehub as EH                                                       # noqa: E402
from po import __main__ as CLI                                                  # noqa: E402
from po.mail import MailError                                                   # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(str(detail)[:400]) if detail and not condition
                                 else ""))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def boe_text(bl="176-88452310", number="40726534505 / 00", duty="653,492.35",
             import_duty="246,320.05", rate="11.20", vat=None, extra=""):
    vat = vat if vat is not None else (
        "Import VAT                        304,446.44\n"
        "Network Charge VAT                  1,066.03\n"
        "Import NHIL                        50,741.08\n"
        "GETFund Import                     50,741.08\n"
        "Network Charge VAT Fund Levy          177.67\n")
    return ("GHANA REVENUE AUTHORITY - CUSTOMS DIVISION\nICUMS BILL OF ENTRY / ASSESSMENT NOTICE\n"
            "Declaration No: {number}\nUser Reference: MTG-LOG-2026-0341\nBL/AWB No: {bl}\n"
            "Date of Assessment: 16/07/2026\nTotal Invoice Value (CIF) USD 169,740.11\n"
            "Exchange Rate {rate}\nImport Duty                       {imp}\n{vat}"
            "Total Duty and Levies GHS         {duty}\n{extra}").format(
                number=number, bl=bl, duty=duty, imp=import_duty, rate=rate, vat=vat,
                extra=extra)


def pdf_of(text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), text, fontsize=9)
    return doc.tobytes()


GOOD = pdf_of(boe_text())
CONFIG = {"recipient": "accounts.ghana@mantrac.com", "sender": "ata@mantrac.com",
          "auto_send": False, "defaults": {"supplier": "CAT", "branch": None, "charge_to": None,
                                           "priority": None}, "confirm_wait_s": 0}
NOSLEEP = lambda s: None                           # noqa: E731


class Source(object):
    """MOCKED eHub source: one Bill of Entry for one row, or a scripted failure."""

    def __init__(self, data=GOOD, ref="176-88452310", identifier="40726534505", error=None,
                 real=False, fail_times=0):
        self.data, self.ref, self.identifier = data, ref, identifier
        self.error, self.real, self.fail_times, self.calls = error, real, fail_times, 0

    def fetch(self, reference):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise P.SourceError("eHub answered 503 (transient)", "transient")
        if self.error:
            raise self.error
        prov = {"source": "REAL", "verification": "VERIFIED", "why": "test stand-in claims it"} \
            if self.real else {"source": "TEST", "verification": "UNVERIFIED",
                               "why": "mocked source"}
        trail = {"steps": [{"step": "ehub_record", "ok": True}, {"step": "manage", "ok": True},
                           {"step": "bill_entry", "ok": True}, {"step": "download", "ok": True}],
                 "provenance": prov, "clearance": {"found": "Under Clearance", "ok": True},
                 "ehub_record": {"bol_awb": self.ref, "status": "Under Clearance"},
                 "manage": {"url": "https://ehub.test/manage"},
                 "bill_entry": {"selected": "BillofEntry_{0}.pdf".format(self.identifier)},
                 "password": "must-never-be-stored"}
        return {"data": self.data, "filename": "BillofEntry_{0}.pdf".format(self.identifier),
                "identifier": self.identifier, "origin": "ehub", "url": "https://ehub.test/f",
                "hub": {"bol_awb": self.ref, "status": "Under Clearance",
                        "identifier": self.identifier, "identity_on_manage": True},
                "trail": trail}


def store(name):
    return S.Store(folder=WORK / name)


def job(st, ref="176-88452310", invoice="9116093", **src):
    record = st.create(doctypes.DEFAULT, ref, {"invoice_no": invoice} if invoice else {})
    return P.process(st, record, Source(ref=ref, **src), CONFIG, sleep=NOSLEEP)


def kill_lease(st, po_id):
    """The job's worker is gone: its lease names a process that no longer exists."""
    path = st._path(po_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["lease"] = {"pid": 999999, "host": socket.gethostname(), "at": time.time() - 3600}
    path.write_text(json.dumps(data), encoding="utf-8")


class Mailer(object):
    """MOCKED Graph: drafts, a send whose outcome can be scripted, Sent Items."""

    def __init__(self, send_plan=None):
        self.drafts, self.sent, self.sends, self.created = {}, {}, 0, 0
        self.send_plan = list(send_plan or [])

    def create(self, to, subject, text, name, data):
        self.created += 1
        mid = "m{0}".format(self.created)
        self.drafts[mid] = {"to": to, "subject": subject, "name": name, "bytes": len(data),
                            "imid": "<{0}@t>".format(mid)}
        return {"message_id": mid, "internet_message_id": self.drafts[mid]["imid"],
                "http_status": 201}

    def send(self, mid):
        step = self.send_plan.pop(0) if self.send_plan else "ok"
        if step == "unknown-not-sent":
            raise MailError("connection dropped", "unknown", None, "send")
        msg = self.drafts.pop(mid, None)
        if msg is None:
            raise MailError("ErrorItemNotFound", "permanent", 404, "send")
        self.sends += 1
        self.sent[msg["imid"]] = dict(msg, sentDateTime="2026-10-06T10:00:00Z")
        if step == "unknown-sent":
            raise MailError("connection dropped after the send", "unknown", None, "send")
        return {"accepted": True, "http_status": 202}

    def find_sent(self, imid):
        return self.sent.get(imid)

    def confirm(self, imid, wait_s=0):
        return self.find_sent(imid)

    def get_message(self, mid):
        return {"id": mid, "isDraft": True} if mid in self.drafts else None


# ═════════════════════════════════════════════════════════════════════════
rule("1. NUMBERS — normalisation, signs, precision; nothing coerced, nothing assumed zero")
# ═════════════════════════════════════════════════════════════════════════
cases = {"1,234.56": 1234.56, "1.234,56": 1234.56, "1 234.56": 1234.56, "653,492.35": 653492.35,
         "-12.50": -12.5, "(12.50)": -12.5, "11.2045": 11.2045, "0": 0.0, "12,50": 12.5,
         "1,2,3": None, "12.34.56": None, "abc": None, "": None, None: None}
bad = {k: (X.to_float(k), v) for k, v in cases.items() if X.to_float(k) != v}
check("to_float: every printed form read exactly; malformed numbers are None, never coerced",
      not bad, bad)
st = store("n1")
r = job(st, data=pdf_of(boe_text(rate="11.2045")))
check("An exchange rate printed 11.2045 reaches C21 as 11.2045 — not rounded to 11.20",
      r["state"] == S.EMAIL_PREPARED and r["output"]["cells"]["C21"] == 11.2045,
      (r["state"], (r.get("output") or {}).get("cells")))
st = store("n2")
r = job(st, data=pdf_of(boe_text(duty="-653,492.35")))
check("A negative total duty is read as negative and stops validation (> 0), nothing generated",
      r["state"] == S.VALIDATION_FAILED and r["fields"]["duty_amount_ghs"]["value"] < 0
      and not r.get("output"), (r["state"], r["fields"]["duty_amount_ghs"]))
st = store("n3")
dup_vat = ("Import VAT                        304,446.44\nImport VAT                        "
           "304,446.44\nNetwork Charge VAT                  1,066.03\n")
r = job(st, data=pdf_of(boe_text(vat=dup_vat)))
check("A VAT label printed twice is AMBIGUOUS — never summed twice — and validation stops",
      r["fields"]["vat_lines"]["status"] == "AMBIGUOUS" and r["state"] == S.VALIDATION_FAILED
      and "printed more than once: Import VAT" in r["fields"]["vat_lines"]["note"],
      (r["state"], r["fields"]["vat_lines"].get("note")))
st = store("n4")
r = job(st, data=pdf_of(boe_text(duty="", import_duty="")))
check("A missing total duty is MISSING (not zero): validation stops, G6 never written",
      r["fields"]["duty_amount_ghs"]["status"] == "MISSING" and r["state"] == S.VALIDATION_FAILED
      and not r.get("output"), r["state"])
st = store("n5")
r = job(st, data=pdf_of(boe_text(import_duty="246,000.00")))
check("The document's own arithmetic off by more than 1.00 GHS: validation stops",
      r["state"] == S.VALIDATION_FAILED and any(c["name"] == "arithmetic:duty" and
                                                c["status"] == "FAILED"
                                                for c in r["validation"]["checks"]), r["state"])
st = store("n6")
r = job(st)
pm = {m["field"]: m for m in r["provenance_map"]}
check("Field-level provenance: raw text → normalised value → template cell → rules",
      pm["duty_amount_ghs"]["raw"] == "653,492.35" and pm["duty_amount_ghs"]["normalized"] ==
      653492.35 and pm["duty_amount_ghs"]["cell"] == "G6" and "> 0" in pm["duty_amount_ghs"]["rules"]
      and pm["invoice_no"]["cell"] == "G4" and pm["invoice_no"]["source"] == "request"
      and pm["document_number"]["cell"] == "C13", pm["duty_amount_ghs"])
cells = r["output"]["cells"]
check("Calculation inputs reach exactly their cells; the template computes G19/C24/C26 itself",
      cells["G6"] == 653492.35 and cells["C19"] == 169740.11 and cells["C21"] == 11.2 and
      cells["G20"] == "=304446.44+1066.03+50741.08+50741.08+177.67" and "G19" not in cells,
      cells)

# ═════════════════════════════════════════════════════════════════════════
rule("2. PDF INTEGRITY — nothing extracted from a file that cannot be trusted")
# ═════════════════════════════════════════════════════════════════════════
for label, data in (("empty", b""), ("an HTML error page saved as .pdf",
                                     b"<!DOCTYPE html><html><body>500</body></html>"),
                    ("truncated (cut-off download)", GOOD[:len(GOOD) // 2])):
    st = store("i-" + label[:5])
    r = job(st, data=data)
    check("{0} → PDF_UNREADABLE, nothing extracted".format(label),
          r["state"] == S.PDF_UNREADABLE and not r.get("fields")
          and r["failure"]["next_action"], (r["state"], r.get("failure")))
st = store("i-ok")
r = job(st)
check("A sound PDF: integrity recorded (bytes, %PDF header, %%EOF, not repaired)",
      r["document"]["integrity"]["eof_marker"] and r["document"]["integrity"]["repaired"] is False)

# ═════════════════════════════════════════════════════════════════════════
rule("3. G4 — SUPPLIER INVOICE No.: the Bill of Entry's explicit Invoice No., never UNA+, never a guess")
# ═════════════════════════════════════════════════════════════════════════
st = store("g4")
r = job(st, invoice=None, data=pdf_of(boe_text(extra="Invoice No: 2600005261\n")))
check("Printed explicitly ('Invoice No: 2600005261'), none given: G4 from the Bill of Entry, "
      "origin and evidence recorded, EMAIL READY",
      r["state"] == S.EMAIL_PREPARED and r["output"]["cells"]["G4"] == 2600005261
      and r["request_fields"]["invoice_no"]["origin"] == "bill_of_entry"
      and "Invoice No: 2600005261" in r["request_fields"]["invoice_no"]["evidence"],
      (r["state"], r.get("failure"), r.get("request_fields", {}).get("invoice_no")))
st = store("g4-none")
r = job(st, invoice=None, data=pdf_of(boe_text(extra="UNA+ Invoice Number 70076\n"
                                                    "Total Invoice Value (CIF) 169,740.11\n")))
check("No explicit Invoice No. (only UNA+ and 'Invoice Value'): NEEDS_REVIEW (absent) — "
      "no UNA+ fallback, nothing generated",
      r["state"] == S.NEEDS_REVIEW and r["failure"]["code"] == "G4_SOURCE_UNPROVEN"
      and r["failure"]["g4_reason"] == "absent" and not r.get("output")
      and r["request_fields"]["invoice_no"]["value"] is None
      and "70076" not in json.dumps(r["request_fields"]), (r["state"], r.get("failure")))
r, problems = P.supply(st, r, {"invoice_no": "INV-77"}, by="ada", config=CONFIG)
check("Supplied at review: validated again in full, G4 = the supplied value, EMAIL READY",
      r["state"] == S.EMAIL_PREPARED and r["output"]["cells"]["G4"] == "INV-77" and not problems)
check("REGRESSION — G4's source → field → cell: invoice_no → G4, origin recorded",
      [m for m in doctypes.get()["mapping"] if m["cell"] == "G4"][0]["field"] == "invoice_no"
      and "supplied by ada at review" in r["request_fields"]["invoice_no"]["origin"])
st = store("g4-many")
r = job(st, invoice=None, data=pdf_of(boe_text(extra="Invoice No: A1001\n"
                                                    "Commercial Invoice Number: B2002\n")))
check("Two different printed invoice numbers: NEEDS_REVIEW (ambiguous), none chosen",
      r["state"] == S.NEEDS_REVIEW and r["failure"]["g4_reason"] == "ambiguous"
      and r["failure"]["candidates"] == ["A1001", "B2002"] and not r.get("output"),
      (r["state"], r.get("failure")))
st = store("g4-clash")
r = job(st, invoice="INV-1", data=pdf_of(boe_text(extra="Invoice No: 2600005261\n")))
check("The job's value disagrees with the printed one: NEEDS_REVIEW (conflict), nothing generated",
      r["state"] == S.NEEDS_REVIEW and r["failure"]["g4_reason"] == "conflict"
      and not r.get("output"), (r["state"], r.get("failure")))
st = store("g4-agree")
r = job(st, invoice="2600005261", data=pdf_of(boe_text(extra="Invoice No: 2600005261\n")))
check("The job's value agrees with the printed one: EMAIL READY, the agreement noted",
      r["state"] == S.EMAIL_PREPARED and "matches" in r["request_fields"]["invoice_no"]["note"],
      (r["state"], r.get("failure")))
check("REGRESSION — no UNA+ route to G4 exists in the pipeline",
      "una_invoice" not in (HERE / "po" / "pipeline.py").read_text(encoding="utf-8"))

# ═════════════════════════════════════════════════════════════════════════
rule("4. IDEMPOTENCY — one Bill of Entry, one job, one output, one email")
# ═════════════════════════════════════════════════════════════════════════
st = store("idem")
first = job(st)
second = job(st)
check("The same document again → SKIPPED_DUPLICATE, naming the first job, no new output",
      second["state"] == S.SKIPPED and second["skip_reason"] == "SKIPPED_DUPLICATE"
      and second["duplicate_of"] == first["po_id"] and not second.get("output"))
st = store("idem-fail")
f1 = job(st, data=pdf_of(boe_text(bl="176-11111111")))      # BL mismatch → fails validation
f2 = job(st, data=pdf_of(boe_text(bl="176-11111111")))
check("A holder that FAILED does not block a new attempt at the same document",
      f1["state"] == S.VALIDATION_FAILED and f2["state"] == S.VALIDATION_FAILED, f2["state"])
other = job(st, ref="176-22222222", identifier="40726534599",
            data=pdf_of(boe_text(bl="176-22222222", number="40726534599 / 00")))
check("A different Bill of Entry is not a duplicate", other["state"] == S.EMAIL_PREPARED)

st = store("conc10")
results = []


def worker():
    rec = st.create(doctypes.DEFAULT, "176-88452310", {"invoice_no": "1"})
    results.append(P.process(st, rec, Source(), CONFIG, sleep=NOSLEEP)["state"])


threads = [threading.Thread(target=worker) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()
outputs = list((st.output_dir).glob("*.xlsx"))
check("10 concurrent jobs for the SAME document: exactly 1 output, 9 SKIPPED_DUPLICATE",
      results.count(S.EMAIL_PREPARED) == 1 and results.count(S.SKIPPED) == 9 and len(outputs) == 1,
      (sorted(results), len(outputs)))
st = store("conc5")
results = []


def worker_n(n):
    ref, num = "176-3000000{0}".format(n), "4072653450{0}".format(n)
    rec = st.create(doctypes.DEFAULT, ref, {"invoice_no": str(n)})
    results.append(P.process(st, rec, Source(ref=ref, identifier=num, data=pdf_of(
        boe_text(bl=ref, number=num + " / 00"))), CONFIG, sleep=NOSLEEP)["state"])


threads = [threading.Thread(target=worker_n, args=(n,)) for n in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()
names = [p.name for p in st.output_dir.glob("*.xlsx")]
check("5 concurrent jobs for 5 documents: 5 outputs, no collision, none lost",
      results.count(S.EMAIL_PREPARED) == 5 and len(set(names)) == 5, (results, names))


def _claim_in_process(folder, key, po_id, queue):
    from po import store as S2
    ok, _h = S2.Store(folder=folder).claim(key, po_id)
    queue.put(ok)


def _reserve_in_process(folder, key, po_id, queue):
    from po import store as S2
    ok, _h = S2.Store(folder=folder).reserve(key, po_id, "proc")
    queue.put(ok)


folder = WORK / "procs"
S.Store(folder=folder)
queue = multiprocessing.Queue()
procs = [multiprocessing.Process(target=_claim_in_process,
                                 args=(str(folder), "DUTY|REF|ID|sha", "po-x-{0}".format(i), queue))
         for i in range(10)]
# A claim held by a job that does not exist counts as alive only through its
# record — so each process's claim is a fresh holder; the lock still makes the
# writes one at a time and the file stays valid JSON.
for p in procs:
    p.start()
for p in procs:
    p.join(30)
got = [queue.get(timeout=5) for _ in procs]
check("10 PROCESSES claiming at once: the claims file stays whole (one writer at a time)",
      len(got) == 10 and isinstance(json.loads((folder / "claims.json").read_text()), dict))
queue = multiprocessing.Queue()
procs = [multiprocessing.Process(target=_reserve_in_process,
                                 args=(str(folder), "po|sha|v1|to@x", "po-y-{0}".format(i), queue))
         for i in range(10)]
for p in procs:
    p.start()
for p in procs:
    p.join(30)
got = [queue.get(timeout=5) for _ in procs]
check("10 PROCESSES reserving the same send at once: exactly ONE may send",
      got.count(True) == 1, got)

# ═════════════════════════════════════════════════════════════════════════
rule("5. RECOVERY — a worker can stop at any point; nothing restarts from zero, "
     "nothing that may have succeeded is called failed")
# ═════════════════════════════════════════════════════════════════════════


class Crash(BaseException):
    """A crash: not an Exception, so nothing in the pipeline catches it."""


def crash_at(st, target, attr):
    original = getattr(target, attr)
    state = {"n": 0}

    def boom(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise Crash()
        return original(*a, **k)
    setattr(target, attr, boom)
    return lambda: setattr(target, attr, original)


for label, target, attr, stuck in (
        ("after the PDF was downloaded", X, "read_pdf", S.PDF_FOUND),
        ("during extraction", X, "extract", S.PDF_READ),
        ("after the template was generated (before saving)", T, "save", S.TEMPLATE_GENERATED),
        ("after saving (before the email was prepared)", P, "prepare", S.SAVED)):
    st = store("crash-" + attr)
    src = Source()
    rec = st.create(doctypes.DEFAULT, "176-88452310", {"invoice_no": "9116093"})
    undo = crash_at(st, target, attr)
    try:
        P.process(st, rec, src, CONFIG, sleep=NOSLEEP)
    except Crash:
        pass
    undo()
    left = st.get(rec["po_id"])
    kill_lease(st, rec["po_id"])
    calls = src.calls
    done = P.recover(st, CONFIG, source=src, log=lambda *a: None)
    final = st.get(rec["po_id"])
    check("Crash {0}: left at {1}, recovered to EMAIL READY without downloading again".format(
        label, stuck), left["state"] == stuck and final["state"] == S.EMAIL_PREPARED and
        src.calls == calls and any(e["event"] == "RESUMED" for e in st.events(rec["po_id"])),
        (left["state"], final["state"], src.calls - calls, done))
check("...a resume after SAVED re-validated and re-saved; the earlier file is kept and named",
      final.get("superseded_outputs") and final["output"]["path"] !=
      final["superseded_outputs"][0]["path"], final.get("superseded_outputs"))
st = store("crash-q")
rec = st.create(doctypes.DEFAULT, "176-88452310", {"invoice_no": "9116093"})
kill_lease(st, rec["po_id"])
src = Source()
P.recover(st, CONFIG, source=src, log=lambda *a: None)
check("A job that never started (QUEUED, worker gone) is discovered on recovery",
      st.get(rec["po_id"])["state"] == S.EMAIL_PREPARED and src.calls == 1)
st = store("crash-live")
rec = job(st)
before = st.get(rec["po_id"])["state"]
P.recover(st, CONFIG, source=Source(), log=lambda *a: None)
check("A job whose worker is alive is never touched by recovery",
      st.get(rec["po_id"])["state"] == before)

# ═════════════════════════════════════════════════════════════════════════
rule("6. EMAIL — PREPARED → SUBMITTED → ACCEPTED → CONFIRMED; UNKNOWN reconciled, never resent")
# ═════════════════════════════════════════════════════════════════════════
st = store("mail-ok")
r = job(st)
m = Mailer()
r, out = P.send(st, r, m, by="omar", sleep=NOSLEEP)
check("A normal send: CONFIRMED (found in Sent Items), one email",
      out == "CONFIRMED" and m.sends == 1 and r["state"] == S.EMAIL_CONFIRMED)
check("...each step on the record: SUBMITTED → ACCEPTED → CONFIRMED",
      [h["state"] for h in r["history"]][-3:] == [S.EMAIL_SENDING, S.EMAIL_SENT,
                                                  S.EMAIL_CONFIRMED])
st = store("mail-unknown-sent")
r = job(st)
m = Mailer(["unknown-sent"])
r, out = P.send(st, r, m, by="omar", sleep=NOSLEEP)
check("The connection drops AFTER Graph sent it: reconciled in Sent Items → CONFIRMED, sent ONCE",
      out == "CONFIRMED" and m.sends == 1, (out, m.sends))
st = store("mail-unknown-draft")
r = job(st)
m = Mailer(["unknown-not-sent", "ok"])
r, out = P.send(st, r, m, by="omar", sleep=NOSLEEP)
check("The connection drops BEFORE it was sent: the draft is provably still a draft → sent once",
      out == "CONFIRMED" and m.sends == 1, (out, m.sends))


class Vanishing(Mailer):
    """Graph accepted it, then nothing can be seen: not in Sent Items yet, draft gone."""

    def send(self, mid):
        self.drafts.pop(mid, None)
        self.sends += 1
        raise MailError("no answer", "unknown", None, "send")


st = store("mail-unknown")
r = job(st)
m = Vanishing()
r, out = P.send(st, r, m, by="omar", sleep=NOSLEEP)
check("Outcome cannot be established: EMAIL_UNKNOWN — not FAILED, not resent",
      out == "UNKNOWN" and r["state"] == S.EMAIL_UNKNOWN and m.sends == 1, (out, r["state"]))
r2, out2 = P.send(st, st.get(r["po_id"]), Mailer(), by="omar", sleep=NOSLEEP)
check("...and a Send PO pressed now is BLOCKED (reconcile first)", out2 == "BLOCKED")
key = st.ledger_key(r["po_key"], r["document"]["sha256"], "DUTY_REQUEST_V1",
                    "accounts.ghana@mantrac.com")
check("...the ledger says UNKNOWN, so no other job may send it either",
      st.sent_before(key)["status"] == "UNKNOWN")
m.sent[r["email"]["internet_message_id"]] = {"sentDateTime": "2026-10-06T10:01:00Z"}
r3, out3 = P.reconcile(st, st.get(r["po_id"]), m)
check("Reconciled later, found in Sent Items → CONFIRMED (no second send)",
      r3["state"] == S.EMAIL_CONFIRMED and m.sends == 1, r3["state"])
st = store("mail-crash")
r = job(st)
r = st.transition(r, S.EMAIL_SENDING, "Submitting")              # the worker died here
kill_lease(st, r["po_id"])
P.recover(st, CONFIG, source=None, mailer=None, log=lambda *a: None)
check("Worker died mid-send → EMAIL_UNKNOWN (never EMAIL_FAILED)",
      st.get(r["po_id"])["state"] == S.EMAIL_UNKNOWN)

# ═════════════════════════════════════════════════════════════════════════
rule("7. EMAIL SAFETY — the attachment must be THIS job's verified document")
# ═════════════════════════════════════════════════════════════════════════
st = store("safe")
a = job(st)
b = job(st, ref="176-22222222", identifier="40726534599",
        data=pdf_of(boe_text(bl="176-22222222", number="40726534599 / 00")))
swapped = dict(st.get(a["po_id"]), output=dict(b["output"]))
reasons = P.blocked_reasons(st, swapped)
check("Another job's output attached to this job: refused (declaration/trace differ)",
      any("another job" in x or "not this job's" in x for x in reasons), reasons)
Path(a["output"]["path"]).write_bytes(Path(a["output"]["path"]).read_bytes() + b"x")
check("The output changed after generation: refused",
      any("changed after it was generated" in x for x in P.blocked_reasons(st, st.get(a["po_id"]))))
st = store("safe2")
v = job(st, data=pdf_of(boe_text(bl="176-11111111")))
check("Validation failed: never sendable",
      "validation has not passed" in P.blocked_reasons(st, v))
big = job(store("safe3"))
Path(big["output"]["path"]).write_bytes(b"0" * (3 * 1024 * 1024 + 1))
big["output"]["sha256"] = __import__("hashlib").sha256(Path(big["output"]["path"])
                                                       .read_bytes()).hexdigest()
check("An attachment over Graph's 3 MB inline limit: refused before any call",
      any("3 MB" in x for x in P.blocked_reasons(store("safe3"), big)))

# ═════════════════════════════════════════════════════════════════════════
rule("8. RETRIES — bounded, exponential, recorded; never for a business failure")
# ═════════════════════════════════════════════════════════════════════════
st = store("retry")
waits = []
rec = st.create(doctypes.DEFAULT, "176-88452310", {"invoice_no": "1"})
src = Source(fail_times=2)
r = P.process(st, rec, src, CONFIG, sleep=waits.append)
check("Two transient eHub failures: retried with backoff 2 s then 4 s, then the job goes on",
      r["state"] == S.EMAIL_PREPARED and waits == [2.0, 4.0] and src.calls == 3, (waits, r["state"]))
check("...each retry is an event", len([e for e in st.events(r["po_id"]) if e["event"] == "RETRY"])
      == 2)
st = store("retry-ra")
err = MailError("throttled", "transient", 429, "create")
err.retry_after = 30
calls = {"n": 0}


def throttled():
    calls["n"] += 1
    if calls["n"] == 1:
        raise err
    return "ok"


waits = []
rec = st.create(doctypes.DEFAULT, "176-88452310", {})
check("Graph throttling: Retry-After (30 s) is honoured over the backoff",
      P._retry("email_send", throttled, st, rec, waits.append) == "ok" and waits == [30.0], waits)
st = store("retry-no")
src = Source(data=pdf_of(boe_text(bl="176-11111111")))
r = P.process(st, st.create(doctypes.DEFAULT, "176-88452310", {"invoice_no": "1"}), src, CONFIG,
              sleep=NOSLEEP)
check("A validation failure is not retried: one fetch", src.calls == 1)

# ═════════════════════════════════════════════════════════════════════════
rule("9. DISCOVERY STOPS — precise states, each with a reason and a next action")
# ═════════════════════════════════════════════════════════════════════════


def stopped(kind, stage):
    e = P.SourceError("scripted: {0} at {1}".format(kind, stage), kind)
    e.stage = stage
    if kind == "skipped":
        e.skip_reason = "SKIPPED_STATUS_CHANGED"
    return job(store("stop-{0}-{1}".format(kind, stage)), error=e)


for kind, stage, want in (("auth", "auth", S.AUTH_REQUIRED),
                          ("permanent", "ehub_record", S.DISCOVERY_FAILED),
                          ("navigation", "manage", S.MANAGE_NAVIGATION_FAILED),
                          ("not_found", "bill_entry", S.PDF_NOT_FOUND),
                          ("review", "bill_entry", S.DOCUMENT_AMBIGUOUS),
                          ("permanent", "download", S.PDF_DOWNLOAD_FAILED),
                          ("unreadable", "download", S.PDF_UNREADABLE),
                          ("skipped", "identity", S.SKIPPED)):
    r = stopped(kind, stage)
    check("{0} at {1} → {2}, with a reason and a next action".format(kind, stage, want),
          r["state"] == want and r["failure"].get("detail") and
          (r["failure"].get("next_action") or want == S.SKIPPED), (r["state"], r.get("failure")))
check("Status changed between the list and Manage → SKIPPED_STATUS_CHANGED",
      stopped("skipped", "identity")["skip_reason"] == "SKIPPED_STATUS_CHANGED")


class FakePage(object):
    def __init__(self, url, password=False, identity=None):
        self.url, self.password, self.identity = url, password, identity or {}

    def evaluate(self, script, *a):
        if "input[type=password]" in script:
            return self.password
        if "current" in script and "status" in script:
            return self.identity
        return None

    def screenshot(self, **k):
        raise AssertionError("must not screenshot a sign-in page")


check("A visible password field = sign-in page (AUTH_REQUIRED), never read as the list",
      EH.auth_state(FakePage("https://logisticshub.mantracgroup.com/Login.aspx", True))
      == "sign_in_required" and EH.auth_state(FakePage(
          "https://logisticshub.mantracgroup.com/WorkFlow/ShipmentTracking/ShipmentList.aspx"))
      == "signed_in")
ev = EH.capture(FakePage("https://ehub.test/Login.aspx?ReturnUrl=x&session=abc", True), "auth")
check("Evidence never photographs a sign-in page; the address is kept without its query",
      "not_captured" in ev and ev["url"] == "https://ehub.test/Login.aspx")
idt = EH.manage_identity(FakePage("https://ehub.test/m", identity={
    "text": "BU Shipment Info ... BOL 176-88452310 ...", "status": "Under Clearance",
    "declaration": "40726534505"}), {"bol_awb": "176-88452310"})
check("Manage identity read off the page: BOL/AWB present, status, declaration",
      idt["reference_on_page"] and idt["status"] == "Under Clearance"
      and idt["declaration"] == "40726534505", idt)

# ═════════════════════════════════════════════════════════════════════════
rule("10. ELIGIBILITY — every row decided, nothing silently dropped; one sweep at a time")
# ═════════════════════════════════════════════════════════════════════════
rows = [{"bol_awb": "A1", "status": "Under Clearance"}, {"bol_awb": "", "status": "Under Clearance"},
        {"bol_awb": "B2", "status": "Cleared"}, {"bol_awb": "A1", "status": "Under Clearance"},
        {"bol_awb": "C3", "status": "Under Clearance"}, {"bol_awb": "D4", "status": " Under  Clearance "},
        {"bol_awb": "E5", "status": "UNDER CLEARANCE"}]
d = CLI.eligibility(rows, handled={"C3"})
check("ELIGIBLE / SKIPPED_MISSING_REQUIRED_REFERENCE / _NOT_UNDER_CLEARANCE / _DUPLICATE / "
      "_ALREADY_PROCESSED — one decision per row (spacing forgiven, case not: the rule is exact)",
      [x["decision"] for x in d] == ["ELIGIBLE", "SKIPPED_MISSING_REQUIRED_REFERENCE",
                                     "SKIPPED_NOT_UNDER_CLEARANCE", "SKIPPED_DUPLICATE",
                                     "SKIPPED_ALREADY_PROCESSED", "ELIGIBLE",
                                     "SKIPPED_NOT_UNDER_CLEARANCE"],
      [x["decision"] for x in d])
st = store("sweep-lock")
with st.xlock("sweep", timeout=0.5, stale_s=3600):
    ran = CLI.sweep(None, st, log=lambda *a: None)
check("A second sweep on the same data folder does not start while one runs", ran == [])

# ═════════════════════════════════════════════════════════════════════════
rule("11. OBSERVABILITY, QUALITY, READINESS — from recorded jobs only")
# ═════════════════════════════════════════════════════════════════════════
st = store("obs")
r = job(st)
r, _o = P.send(st, r, Mailer(), by="omar", sleep=NOSLEEP)
x = CLI.explain(st, r["po_id"])
check("'What exactly happened to PO X?' — one report: row, document hash, fields, validation, "
      "template, output, email, timeline",
      x["document"]["sha256"] and x["extracted"] and x["validation"]["passed"]
      and x["output"]["verified"] and x["email"]["status"] == "CONFIRMED"
      and x["timeline"][0]["event"] == "PO_DISCOVERED" and x["correlation_id"] == r["po_id"])
check("...with a duration for every stage that ran",
      {"discovery", "read", "extraction", "validation", "template", "save", "email"}
      <= set(x["timings_ms"]), x["timings_ms"])
q = Q.metrics(st)
check("Quality metrics: a TEST job is counted apart; the REAL block stays empty (null rates)",
      q["not_real"]["jobs"] == 1 and q["real"]["jobs"] == 0
      and q["real"]["extraction_success"]["pct"] is None
      and q["not_real"]["email_success"]["pct"] == 100.0, q["not_real"])
rd = RD.evaluate(st)
check("Readiness with no real-eHub job: NOT READY, real gates UNVERIFIED (never PASS from a test)",
      rd["status"] == "NOT READY" and all(g["status"] != "PASS" for g in rd["pilot_gates"][1:]),
      [(g["gate"], g["status"]) for g in rd["pilot_gates"]])

# ═════════════════════════════════════════════════════════════════════════
rule("12. SECURITY — nothing secret-named is stored")
# ═════════════════════════════════════════════════════════════════════════
blob = json.dumps(st.get(r["po_id"])) + json.dumps(st.events(r["po_id"]))
check("A 'password' key in a discovery trail is never stored in the job or its events",
      "must-never-be-stored" not in blob)
check("No credential or token appears in the job, events, ledger or claims",
      not any(w in (blob + (st.folder / "ledger.json").read_text()).lower()
              for w in ("client_secret", "bearer ", "access_token")))

# ═════════════════════════════════════════════════════════════════════════
rule("13. PERFORMANCE — stage timings with the mocked source (NOT real eHub numbers)")
# ═════════════════════════════════════════════════════════════════════════
st = store("perf")
t0 = time.monotonic()
for n in range(10):
    ref, num = "176-5000000{0}".format(n), "4072653460{0}".format(n)
    rr = st.create(doctypes.DEFAULT, ref, {"invoice_no": str(n)})
    P.process(st, rr, Source(ref=ref, identifier=num, data=pdf_of(boe_text(
        bl=ref, number=num + " / 00"))), CONFIG, sleep=NOSLEEP)
total = time.monotonic() - t0
avg = Q.metrics(st)["not_real"]["avg_stage_ms"]
print("    10 jobs in {0:.2f}s; average ms per stage: {1}".format(total, avg))
check("10 jobs processed; read/extract/validate/template/save each measured", total < 60
      and {"read", "extraction", "validation", "template", "save"} <= set(avg), avg)

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
