"""
The PO email to controltower@mantracgroup.com: preview, one authorized send,
Sent Items check, no duplicates; signatures shown, never assumed.

    python test_po_email.py

TEST DATA only: sanitised fixtures and a stand-in mailbox (no Graph call).
"""

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "fixtures")]
WORK = Path(tempfile.mkdtemp(prefix="ct_email_"))
os.environ["PO_DATA_DIR"] = str(WORK / "default")
os.environ.pop("PO_ALLOW_TEST_SEND", None)        # the real gate, with the authorization
os.environ.pop("PO_SIGNING_ORDER", None)
os.environ["PO_MAIL_SENDER"] = "ata-sender@example.test"

import po_engine as F                                       # noqa: E402
from icums_boe import icums_boe_pdf                         # noqa: E402
from po import authorize as A, doctypes, pipeline as P, service, signatures as SG  # noqa
from po import store as S                                   # noqa: E402

PASS, FAIL = [], []
TO = "controltower@mantracgroup.com"
CONFIG = {"recipient": None, "sender": None, "auto_send": False,
          "defaults": {"supplier": None, "branch": None, "charge_to": None, "priority": None},
          "confirm_wait_s": 0, "email_disabled": True}


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "  ({0})".format(str(detail)[:400]) if detail and not ok else ""))


class Mailbox(object):
    """Stand-in Graph mailbox: records every call, keeps what was 'sent'."""

    def __init__(self, deliver=True):
        self.calls, self.sent, self.drafts, self.deliver = [], [], {}, deliver

    def create(self, to, subject, text, name, data, content_type=None):
        self.calls.append("create")
        mid = "m{0}".format(len(self.drafts) + 1)
        self.drafts[mid] = {"to": [to], "subject": subject, "text": text,
                            "attachments": [{"name": name, "size": len(data)}],
                            "imid": "<{0}@test>".format(mid)}
        return {"message_id": mid, "internet_message_id": self.drafts[mid]["imid"],
                "request_id": "r"}

    def send(self, mid):
        self.calls.append("send")
        self.sent.append(mid)
        return {"accepted": True, "http_status": 202, "request_id": "r",
                "client_request_id": "c"}

    def confirm(self, imid, wait_s=0, every_s=0):
        self.calls.append("confirm")
        for mid in self.sent:
            if self.drafts[mid]["imid"] == imid and self.deliver:
                return {"id": mid, "internetMessageId": imid, "sentDateTime": "2026-10-10T10:00:00Z"}
        return None

    def inspect(self, mid):
        d = self.drafts[mid]
        return {"to": d["to"], "subject": d["subject"], "from": "ata-sender@example.test",
                "attachments": d["attachments"]}

    def find_by_attachment(self, folder, subject, name):
        return None


_n = {"i": 0}


def saved_job(data=None, verified=True):
    _n["i"] += 1
    st = S.Store(folder=WORK / "job{0}".format(_n["i"]), output_dir=WORK / "o{0}".format(_n["i"]))
    rec = st.create(doctypes.DEFAULT, "J552493", {})
    rec = P.process(st, rec, F.Source(data or icums_boe_pdf(), ref="J552493"), CONFIG,
                    sleep=lambda s: None)
    if verified and rec["state"] == S.SAVED:
        # As if observed in the real eHub session (the stand-in source is TEST).
        rec["provenance"] = dict(rec.get("provenance") or {}, source="REAL",
                                 verification="VERIFIED")
        st.save(rec)
        rec = st.get(rec["po_id"])
    return st, rec


print("=" * 74 + "\n1. THE PREVIEW: EXACTLY WHAT WOULD BE SENT — NOTHING IS SENT\n" + "=" * 74)
st, rec = saved_job()
check("The pilot job stops at SAVED with no email", rec["state"] == S.SAVED and
      (rec.get("email") or {}).get("status") == "BLOCKED")
rec, why = P.prepare_email(st, rec, TO)
check("A validated, saved, read-back job is prepared for controltower@mantracgroup.com",
      not why and rec["state"] == S.EMAIL_PREPARED and rec["email"]["recipient"] == TO, why)
pv = P.email_preview(st, rec)
check("The preview names sender, recipient, subject, attachment (name, size, SHA-256)",
      pv["from"] == "ata-sender@example.test" and pv["to"] == TO and
      pv["subject"] == "Duty Payment Request — 40726534505 — J552493" and
      pv["attachment"] == rec["output"]["filename"] and pv["attachment_bytes"] > 0 and
      pv["attachment_sha256"] == rec["output"]["sha256"], pv)
check("...the exact body that the send uses", pv["body"] == P.email_text(rec) and
      rec["po_id"] in pv["body"])
check("...the validated values and their validation evidence",
      pv["values"]["C13 declaration"] == "40726534505 / 00" and
      pv["values"]["G6 duty (GHS)"] == 653670.54 and pv["validation"]["decision"] == "VALID"
      and pv["validation"]["passed"] and not pv["validation"]["failed"] and
      pv["template_read_back"], pv["values"])
check("...and it is not sendable without a named person's authorization",
      any("no one has authorized" in r for r in pv["blocking"]), pv["blocking"])

print("=" * 74 + "\n2. NOTHING UNVERIFIED IS PREPARED\n" + "=" * 74)
st2, bad = saved_job(icums_boe_pdf(total="653,700.54"))
bad, why = P.prepare_email(st2, bad, TO)
check("Validation failed (tax lines ≠ Total): the email is not prepared, with the reason",
      why and bad["state"] != S.EMAIL_PREPARED and any("SAVED" in r or "validation" in r
                                                       for r in why), why)
st3, amb = saved_job(icums_boe_pdf(continuation_number="40726534505 / 01"))
amb, why = P.prepare_email(st3, amb, TO)
check("An ambiguous declaration (/ 00, / 01): not prepared", why and
      amb["state"] != S.EMAIL_PREPARED, why)
st4, tam = saved_job()
Path(tam["output"]["path"]).write_bytes(b"changed")
tam, why = P.prepare_email(st4, tam, TO)
check("A template changed after it was saved (read-back fails): not prepared", bool(why), why)

print("=" * 74 + "\n3. ONE AUTHORIZED SEND, VERIFIED IN SENT ITEMS, NO DUPLICATE\n" + "=" * 74)
box = Mailbox()
r, out = P.send(st, rec, box, by="dashboard", confirm_wait_s=0, sleep=lambda s: None)
check("The dashboard / API / auto-send cannot send without the authorization: BLOCKED, no "
      "mailbox call", out == "BLOCKED" and not box.calls, (out, box.calls))
ok, why = A.grant(st, st.get(rec["po_id"]), "someone.else@example.test", "Mustafa")
check("An authorization for another recipient is refused", not ok and why, why)
ok, why = A.grant(st, st.get(rec["po_id"]), TO, "")
check("An authorization must name the person", not ok)
ok, why = A.grant(st, st.get(rec["po_id"]), TO, "Mustafa")
check("The named person authorizes this one email (recorded on the audit trail)",
      ok and any(e["event"] == "SEND_AUTHORIZED" and e["metadata"].get("by") == "Mustafa"
                 for e in st.events(rec["po_id"])), why)
r, out = P.send(st, st.get(rec["po_id"]), box, by="Mustafa", confirm_wait_s=0,
                sleep=lambda s: None)
check("Sent once, found in Sent Items and read back: CONFIRMED",
      out == "CONFIRMED" and box.calls.count("send") == 1 and r["state"] == S.EMAIL_CONFIRMED,
      (out, r["state"], box.calls))
d = box.drafts[box.sent[0]]
check("...with exactly the previewed recipient, subject, body and attachment",
      d["to"] == [TO] and d["subject"] == pv["subject"] and d["text"] == pv["body"] and
      d["attachments"][0]["name"] == pv["attachment"])
ev = [e["event"] for e in st.events(rec["po_id"])]
check("...and the audit trail records the real result", "EMAIL_SEND_STARTED" in ev and
      any(e in ev for e in ("EMAIL_CONFIRMED", "EMAIL_RECONCILED", "COMPLETED")), ev[-8:])
r2, out2 = P.send(st, st.get(rec["po_id"]), box, by="Mustafa", confirm_wait_s=0,
                  sleep=lambda s: None)
check("A second send is refused: the authorization is used up, no second email",
      out2 == "BLOCKED" and box.calls.count("send") == 1, (out2, box.calls))
check("...and its reason says so", any("already used" in x or "not ready" in x
                                       for x in P.blocked_reasons(st, st.get(rec["po_id"]))))
st5, other = saved_job()
other, _w = P.prepare_email(st5, other, TO)
check("The next PO email stays disabled until separately authorized",
      any("no one has authorized" in x for x in P.blocked_reasons(st5, other)))
st6, lost = saved_job()
lost, _w = P.prepare_email(st6, lost, TO)
A.grant(st6, lost, TO, "Mustafa")
quiet = Mailbox(deliver=False)
r6, out6 = P.send(st6, st6.get(lost["po_id"]), quiet, by="Mustafa", confirm_wait_s=0,
                  sleep=lambda s: None)
check("Graph accepted it but it is not in Sent Items: not claimed as delivered ({0})".format(
      out6), out6 != "CONFIRMED" and r6["state"] != S.EMAIL_CONFIRMED, (out6, r6["state"]))
st7, edited = saved_job()
edited, _w = P.prepare_email(st7, edited, TO)
A.grant(st7, edited, TO, "Mustafa")
e2 = st7.get(edited["po_id"])
e2["email"]["subject"] = e2["email"]["subject"] + " (edited)"
st7.save(e2)
check("An email changed after it was authorized is refused",
      any("changed after it was authorized" in x for x in
          P.blocked_reasons(st7, st7.get(edited["po_id"]))))

print("=" * 74 + "\n4. SIGNATURES: SHOWN, NEVER ASSUMED\n" + "=" * 74)
sig = SG.status(st.get(rec["po_id"]))
check("The template's six signature roles, all PENDING — none fabricated",
      [x["role"] for x in sig["roles"]] == SG.ROLES and sig["signed"] == 0 and
      all(x["status"] == "PENDING" and not x["by"] for x in sig["roles"]), sig)
check("The official signing order is reported as NOT configured",
      not sig["order_configured"] and "NOT configured" in SG.line(st.get(rec["po_id"])))
fake = dict(st.get(rec["po_id"]), signatures=[{"role": "Prepared By", "by": "X",
                                                "at": "now", "attachment_sha256": "other"}])
check("A signature for another version of the document does not count",
      SG.status(fake)["signed"] == 0)
os.environ["PO_SIGNING_ORDER"] = "Prepared By|Logistics Manager|1st Authorizer|2nd Authorizer"
check("A configured order is shown in that order",
      [x["role"] for x in SG.status(rec)["roles"]] == ["Prepared By", "Logistics Manager",
                                                       "1st Authorizer", "2nd Authorizer"]
      and SG.status(rec)["order_configured"])
os.environ.pop("PO_SIGNING_ORDER")
check("The PO row carries the signature line", service.card(st.get(rec["po_id"]))["signatures"]
      .startswith("✍ 0 of 6 signed"))

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
