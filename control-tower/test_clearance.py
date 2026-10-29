"""
The Clearance page (/clearance): the eHub shipment export read on the server
(dashboard/clearance.py), behind the same sign-in and access levels as the
dashboard. Viewers see it, Operators and Admins load a new export (audited),
a file in the inbox folder is picked up, nothing is repaired, and a bad file
never replaces what is shown. Then the page itself, in a browser.

The workbooks here are made up in the test: no company data.

    python test_clearance.py
"""
import datetime as dt
import http.cookiejar
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="clearance_")
os.environ.update({
    "DASHBOARD_ACCOUNTS_DB": os.path.join(TMP, "accounts.db"), "DASHBOARD_ACCOUNTS": "auto",
    "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"), "PO_DATA_DIR": os.path.join(TMP, "po"),
    "PO_AUTO": "0", "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key"),
    "ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

import openpyxl                                               # noqa: E402
from dashboard import accounts, clearance                     # noqa: E402
from dashboard import server as tower_server                  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(str(detail)[:300])))


HEAD = ["BU", "Division", "Stage (Next Step)", "Mode", "Forwarder", "AWB/BOL", "UNA+ Invoice",
        "Clearing Agent", "Status", "ETA", "ATA", "Submitted To BU", "CustomsReleaseDate", "Completed",
        "Current Step", "Last Date", "Expected Completion Date", "Performance", "Expected Cycle Days",
        "Actual Days", "On Time"] + ["{0} - Achieved".format(k) for k in range(1, 10)] + ["Mode"]
D = dt.datetime


def row(bu, mode, awb, step, eta, due, perf, ontime, actual=None, status="Under Clearance",
        agent="TEST AGENT", ach="YYN------"):
    done = step == "Completed"
    return ([bu, "Spare Parts", step, mode, "TEST FFW", awb, 12345, agent, "Completed" if done else status,
             eta, None, D(2026, 3, 1), None, D(2026, 3, 20) if done else None, step, D(2026, 10, 20),
             due, perf, 8, actual, "yes" if ontime else "no"]
            + [{"Y": "Yes", "N": "No"}.get(c) for c in ach] + ["dup-mode-column"])


ROWS = [
    row("GH", "Air", "TEST-0001", "Completed", D(2026, 3, 5), None, 217, True, actual=7, ach="YYYYYYYYY"),
    row("GH", "Ocean", "TEST-0002", "Customs Release", D(2026, 6, 1), D(2026, 9, 1), 55, False),
    row("EG", "Air", "TEST-0003", "Assign Clearing Agent", D(2026, 5, 1), D(1899, 12, 30), 46000, False),
    row("EG", "Courier", "TEST-0004", "Completed", D(2926, 6, 2), None, 3, False, actual=46190),
    row("TZ", "Truck", "TEST-0005", "Duty Payment", None, D(2026, 10, 30), -4, True,
        agent="AGL - AFRICA GLOBAL"),
    row("TZ", "Air", "TEST-0006", "Correction Required", D(2026, 8, 1), D(2026, 10, 1), 25, False,
        status="RequiredCorrection", agent="AGL-AFRICA GLOBAL"),
]


def workbook(path, rows, sheet="Export"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    wb.create_sheet("Summary").append(["not read"])
    wb.save(path)
    return path


print("1. READING AN EXPORT (nothing repaired)")
data = clearance.read_workbook(workbook(os.path.join(TMP, "a.xlsx"), ROWS))
F = {k: i for i, k in enumerate(data["fields"])}
R = {r[F["awb"]]: r for r in data["rows"]}
check("every shipment row is read", data["count"] == 6, data["count"])
check("the data date is the latest 'Last Date'", data["asof"] == "2026-10-20", data["asof"])
check("an ETA year 2926 is not shown as a date; counted", R["TEST-0004"][F["eta"]] is None and data["dq"]["eta_bad_year"] == 1)
check("a missing ETA is counted", data["dq"]["eta_missing"] == 1)
check("a step due date of 1899 is not counted as 46,000 days late", R["TEST-0003"][F["overdue"]] is None and data["dq"]["due_bad"] == 1)
check("a cycle time from a missing date (46190) is dropped and counted", R["TEST-0004"][F["cycle"]] is None and data["dq"]["cycle_bad"] == 1)
check("a real past-due open shipment keeps its days", R["TEST-0002"][F["overdue"]] == 55 and R["TEST-0002"][F["due"]] == "2026-09-01")
check("a completed shipment has no 'past due'", R["TEST-0001"][F["overdue"]] is None and R["TEST-0001"][F["cycle"]] == 7)
check("On Time is the export's own flag", R["TEST-0005"][F["ontime"]] == 1 and R["TEST-0006"][F["ontime"]] == 0)
check("stage results become Y / N / -", R["TEST-0002"][F["ach"]] == "YYN------")
check("an agent spelled two ways is reported, not merged", data["dq"]["agent_name_variants"] == 1)
check("the duplicated 'Mode' column does not overwrite the first", R["TEST-0003"][F["mode"]] == "Air")
check("UNA+ Invoice is not carried into the page", "UNA+ Invoice" not in json.dumps(data["fields"]) and 12345 not in R["TEST-0002"])
try:
    clearance.prepare([["BU", "Mode"], ["GH", "Air"]])
    check("a sheet without the needed columns is refused", False)
except ValueError as error:
    check("a sheet without the needed columns is refused", "no column" in str(error))

print("\n2. THE SERVER: sign-in and access levels")
acc = accounts.store()
PW = "Clearance-Test-2026!"
acc.users.create("admin@mantrac.example", "Ama Admin", "ADMIN", password=PW)
acc.users.create("oper@mantrac.example", "Kofi Operator", "OPERATOR", password=PW)
acc.users.create("view@mantrac.example", "Esi Viewer", "VIEWER", password=PW)
s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1", access_key="clearance-key-123456")
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def client():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect()), jar


def call(opener, method, path, body=None, csrf=None, headers=None, raw=False):
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Origin", BASE)
    if data is not None and not raw:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if csrf:
        req.add_header("X-CSRF-Token", csrf)
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def js(b):
    try:
        return json.loads(b or b"{}")
    except ValueError:
        return {}


def sign_in(email):
    o, jar = client()
    st, b, _h = call(o, "POST", "/api/auth/login", {"email": email, "password": PW})
    return o, js(b).get("csrf"), jar


def wait_ready(o):
    for _ in range(60):
        st = js(call(o, "GET", "/api/clearance/status")[1])
        if st.get("state") in ("ready", "error"):
            return st
        time.sleep(0.25)
    return st


anon, _ = client()
st, _b, h = call(anon, "GET", "/clearance")
check("signed out: /clearance goes to the sign-in page", st == 302 and (h.get("Location") or "").startswith("/login"), (st, h.get("Location")))
check("signed out: the data API answers 401", call(anon, "GET", "/api/clearance")[0] == 401)

V, V_csrf, _ = sign_in("view@mantrac.example")
st, page, _h = call(V, "GET", "/clearance")
check("a Viewer opens the Clearance page", st == 200 and b"Shipment Control Tower" in page, st)
d = js(call(V, "GET", "/api/clearance")[1])
check("before any export: empty, and a Viewer is not told the server folder", d.get("empty") is True and d.get("inbox") is None)
XLSX = open(workbook(os.path.join(TMP, "b.xlsx"), ROWS), "rb").read()
st, b, _h = call(V, "POST", "/api/clearance/upload", XLSX, V_csrf,
                 {"Content-Type": "application/octet-stream", "X-Filename": "export.xlsx"}, raw=True)
check("a Viewer cannot load an export (403)", st == 403, (st, b[:120]))

O, O_csrf, O_jar = sign_in("oper@mantrac.example")
d = js(call(O, "GET", "/api/clearance")[1])
check("an Operator is told where to drop exports on the server", "inbox" in (d.get("inbox") or ""))
st, b, _h = call(O, "POST", "/api/clearance/upload", XLSX, None,
                 {"Content-Type": "application/octet-stream", "X-Filename": "export.xlsx"}, raw=True)
check("an upload without the page's CSRF token is refused", st == 403, st)
st, b, _h = call(O, "POST", "/api/clearance/upload", b"not an excel file", O_csrf,
                 {"Content-Type": "application/octet-stream", "X-Filename": "export.xlsx"}, raw=True)
check("a file that is not an .xlsx is refused", st == 200 and js(b).get("accepted") is False, b[:160])
st, b, _h = call(O, "POST", "/api/clearance/upload", XLSX, O_csrf,
                 {"Content-Type": "application/octet-stream", "X-Filename": "Shipment Update 26-10.xlsx"}, raw=True)
check("an Operator loads an export", st == 200 and js(b).get("accepted") is True, b[:200])
stt = wait_ready(O)
check("it is read on the server", stt.get("state") == "ready" and stt["current"]["count"] == 6, stt)
check("who loaded it and when is shown", stt["current"]["by"] == "oper@mantrac.example" and stt["current"]["source"] == "upload")
d = js(call(V, "GET", "/api/clearance")[1])
check("everyone then sees the same data", d.get("count") == 6 and len(d.get("rows", [])) == 6)
check("the uploaded file is not kept on the server", not os.path.exists(os.path.join(TMP, "rt", "clearance", "upload.xlsx")))

bad = open(workbook(os.path.join(TMP, "c.xlsx"), ROWS, sheet="Sheet1"), "rb").read()
call(O, "POST", "/api/clearance/upload", bad, O_csrf,
     {"Content-Type": "application/octet-stream", "X-Filename": "wrong.xlsx"}, raw=True)
stt = wait_ready(O)
check("a workbook without the Export sheet is refused and says why", stt.get("state") == "error" and "Export" in stt.get("message", ""), stt)
check("…and what was shown is not replaced", js(call(V, "GET", "/api/clearance")[1]).get("count") == 6)

A, A_csrf, _ = sign_in("admin@mantrac.example")
audit = js(call(A, "GET", "/api/admin/audit?limit=50")[1])
entries = audit.get("entries") or audit.get("events") or audit.get("items") or []
check("each load is in the audit log", any(e.get("action") == "UPLOAD_CLEARANCE_EXPORT" for e in entries), str(audit)[:300])

print("\n3. THE INBOX FOLDER")
workbook(os.path.join(os.environ["ATA_CLEARANCE_INBOX"], "weekly.xlsx"), ROWS[:2])
call(V, "GET", "/api/clearance/status")
stt = wait_ready(V)
check("a newer file in the inbox is read without anyone uploading", stt.get("state") == "ready" and stt["current"]["count"] == 2
      and stt["current"]["source"] == "inbox", stt)
call(V, "GET", "/api/clearance/status")
check("the same inbox file is not read twice", js(call(V, "GET", "/api/clearance/status")[1]).get("state") == "ready")
_st, _mb, _ = call(V, "GET", "/api/clearance/map")
check("a Viewer gets the Overview map's figures (clearance.view)", _st == 200 and "bus" in js(_mb) and "places" in js(_mb), _st)
_st, _pb, _ = call(V, "GET", "/api/clearance/places")
check("a Viewer gets the map's places (clearance.view)", _st == 200 and js(_pb)["places"]["EG"]["country"] == "Egypt", _st)

print("\n3b. AN UPLOAD IS NOT REPLACED BY AN OLDER INBOX FILE")
st, b, _h = call(O, "POST", "/api/clearance/upload", XLSX, O_csrf,
                 {"Content-Type": "application/octet-stream", "X-Filename": "Shipment%20Update%20%E2%80%93%2027-10.xlsx"}, raw=True)
stt = wait_ready(O)
check("an upload after the inbox file is shown", stt["current"]["count"] == 6, stt)
for _ in range(3):
    call(V, "GET", "/api/clearance")
    time.sleep(0.2)
stt = wait_ready(V)
check("…and stays: the older inbox file is not read again", stt["current"]["count"] == 6 and stt["current"]["source"] == "upload", stt)
check("a non-Latin file name arrives intact (URL-encoded header)", stt["current"]["file"] == "Shipment Update – 27-10.xlsx", stt["current"]["file"])
check("the status never shows internal inbox stamps", "inbox_file" not in json.dumps(stt))
d = js(call(V, "GET", "/api/clearance")[1])
check("a Viewer is not told the server folder", "inbox" not in d or d.get("inbox") is None)
odd = ["<img src=x onerror=alert(1)>"] + row("GH", "Air", "X", "Customs Release", D(2026, 6, 1), D(2026, 9, 1), 5, True)[1:]
odd[HEAD.index("On Time")] = "Y"
nb = clearance.prepare([HEAD, odd])
check("an unclear On Time ('Y') is counted, not guessed", nb["dq"]["ontime_unclear"] == 1 and nb["rows"][0][F["ontime"]] == 0)
check("a BU value is kept as text (the page escapes it)", nb["rows"][0][F["bu"]] == "<img src=x onerror=alert(1)>")
check("ETA years are judged against today, not a fixed window", "2025" not in open(os.path.join(HERE, "dashboard", "clearance.py")).read().split("def prepare")[1].split("def read_workbook")[0])
from dashboard import atlas_skills as _sk                    # noqa: E402
check("a shipment-specific customs question is not taken over by the export summary",
      _sk.detect("how does customs clearance work?") is None)
check("'shipments under clearance' stays with the run (the Hub's status filter)",
      _sk.detect("Show me shipments under clearance") is None)

print("\n4. FOR ATLAS")
summ = clearance.summary()
check("a summary of the loaded export (counts only from the data)", summ and summ["total"] == 6 and summ["past_due"] == 2
      and summ["longest"][0]["awb"] == "TEST-0002", summ)

from dashboard import atlas_skills                             # noqa: E402
check("ATLAS: a clearance question goes to the clearance skill",
      atlas_skills.detect("how many shipments are past due in customs clearance?") == "clearance"
      and atlas_skills.detect("what is the status of the BOE for PO 123?") != "clearance")
reply = atlas_skills.answer("clearance", "clearance past due for GH", None)
check("ATLAS answers from the loaded export only, names the BU and links the page",
      "**GH**" in reply["answer"] and "TEST-0002" in reply["answer"] and "55 days" in reply["answer"]
      and "(/clearance#bu=GH&flag=pastdue)" in reply["answer"], reply["answer"])

print("\n4b. ACTIONS: escalate, contacts, call / mail log")
def post(o, csrf, path, body):
    st, b, _h = call(o, "POST", path, body, csrf)
    return st, js(b)
st, r = post(V, V_csrf, "/api/clearance/escalate", {"awb": "TEST-0002", "bu": "GH", "reason": "x"})
check("a Viewer cannot escalate (403)", st == 403, st)
st, r = post(O, O_csrf, "/api/clearance/escalate", {"awb": "TEST-0002", "bu": "GH", "reason": ""})
check("an escalation needs a reason", st == 200 and r.get("ok") is False)
st, r = post(O, O_csrf, "/api/clearance/escalate", {"awb": "NOT-IN-EXPORT", "reason": "late"})
check("only a shipment of the loaded export can be escalated", r.get("ok") is False)
st, r = post(O, O_csrf, "/api/clearance/escalate", {"awb": "test 0002", "bu": "GH", "reason": "Agent not answering"})
esc1 = r.get("escalation") or {}
check("an Operator escalates; the facts come from the export, not the page", r.get("ok") is True and esc1.get("awb") == "TEST-0002"
      and esc1.get("step") == "Customs Release" and esc1.get("overdue") == 55 and esc1.get("by") == "oper@mantrac.example", r)
st, r = post(O, O_csrf, "/api/clearance/escalate", {"awb": "TEST-0002", "bu": "GH", "reason": "again"})
check("a second open escalation for the same shipment is refused", r.get("ok") is False)
st, r = post(O, O_csrf, "/api/clearance/contacts", {"agents": {"TEST AGENT": {"email": "a@b.com"}}})
check("contacts are Admin-only (403 for an Operator)", st == 403, st)
st, r = post(A, A_csrf, "/api/clearance/contacts", {"agents": {"TEST AGENT": {"person": "Ali", "email": "not-an-email"}}})
check("a bad email address is refused", r.get("ok") is False and "email" in r.get("message", ""))
st, r = post(A, A_csrf, "/api/clearance/contacts", {"agents": {"TEST AGENT": {"person": "Ali", "email": "ali@agent.example; ops@agent.example", "phone": "+20 2 1234 5678"}},
                                                     "bus": {"GH": {"person": "GH desk", "email": "gh@mantrac.example"}}})
check("an Admin saves contacts", r.get("ok") is True and r["contacts"]["agents"]["TEST AGENT"]["phone"] == "+20 2 1234 5678")
check("everyone can read the contacts (for Call and Mail)", js(call(V, "GET", "/api/clearance/contacts")[1])["bus"]["GH"]["person"] == "GH desk")
st, r = post(V, V_csrf, "/api/clearance/contact-log", {"awb": "TEST-0002", "bu": "GH", "kind": "mail", "to": "ali@agent.example"})
check("a Mail draft is recorded (the platform sends nothing)", r.get("ok") is True)
act = js(call(V, "GET", "/api/clearance/activity?awb=TEST-0002")[1])
check("the shipment's activity: its escalation and the mail", len(act["escalations"]) == 1 and act["contacts"][0]["kind"] == "mail")
reply = atlas_skills.answer("clearance", "any open escalations?", None)["answer"]
check("ATLAS lists open escalations", "TEST-0002" in reply and "Agent not answering" in reply, reply)
reply = atlas_skills.answer("clearance", "what is the status of TEST-0002?", None)["answer"]
check("ATLAS answers one shipment from the export: step, due, days, agent, escalation, mail",
      "Customs Release" in reply and "2026-09-01" in reply and "55" in reply and "TEST AGENT" in reply
      and "Escalation (open, GH)" in reply and "Mail drafted" in reply, reply)
check("…and only for the shipments it names", atlas_skills.detect("what is the status of TEST-0002?") == "clearance")
st, r = post(V, V_csrf, "/api/clearance/resolve", {"id": esc1.get("id"), "note": "x"})
check("a Viewer cannot resolve (403)", st == 403)
st, r = post(O, O_csrf, "/api/clearance/resolve", {"id": esc1.get("id"), "note": "Agent replied, release tomorrow"})
check("an Operator resolves it with a note", r.get("ok") is True and r["escalation"]["status"] == "resolved")
st, r = post(O, O_csrf, "/api/clearance/escalate", {"awb": "TEST-0002", "bu": "GH", "reason": "Still not released"})
check("after resolving, it can be escalated again", r.get("ok") is True)
audit = js(call(A, "GET", "/api/admin/audit?limit=100")[1])
entries = audit.get("entries") or audit.get("events") or audit.get("items") or []
acts = {e.get("action") for e in entries}
check("escalate, resolve, contacts and mail are all in the audit log",
      {"ESCALATE_SHIPMENT", "RESOLVE_ESCALATION", "CLEARANCE_CONTACTS_CHANGED", "CLEARANCE_MAIL"} <= acts, acts)
check("a question asked on the Clearance page goes to the clearance data",
      atlas_skills.detect("GH air shipments in June", None, {"page": "clearance"}) == "clearance")
from dashboard import assistant as _a0                         # noqa: E402
_run = _a0.RunData({"shipments": [{"reference": "157-49568713", "state": "updated", "mode": "air"}]})
check("elsewhere, while a run has shipments, 'air shipments' stays the run's own filter",
      atlas_skills.detect("GH air shipments in June", _run, {}) is None)
check("…and with no run, a question naming a BU is answered from the export",
      atlas_skills.detect("GH air shipments in June", _a0.RunData({}), {}) == "clearance")

print("\n4b2. ATLAS WRITES THE EMAIL FOR THE SITUATION")
def rowd(**kw):
    base = dict(awb="T-1", bu="GH", mode="Ocean", div="Spare Parts", ffw="TEST FFW", agent="TEST AGENT",
                status="Under Clearance", step="Customs Release", eta="2026-06-01", ata=None, due="2026-09-01",
                overdue=55, completed=None)
    base.update(kw); return base
d = clearance.draft_mail(rowd(), "Esi")
check("customs step: to the clearing agent, Cc the BU (from Contacts)", d["to"] == "ali@agent.example; ops@agent.example" and d["cc"] == "gh@mantrac.example", d)
check("55 days late: urgent, with the due date and days", d["subject"].startswith("URGENT:") and "1 Sep 2026" in d["body"] and "55 days late" in d["body"], d["subject"])
check("the request fits the step", "obtain the customs release" in d["body"] and d["body"].startswith("Dear Ali,"))
d = clearance.draft_mail(rowd(overdue=3, due="2026-10-23"))
check("3 days late: a follow-up, not urgent", "follow-up" in d["situation"] and not d["subject"].startswith("URGENT") and "3 days late" in d["body"], d["situation"])
d = clearance.draft_mail(rowd(overdue=-2, due="2026-10-28"))
check("not yet due: a reminder", "reminder" in d["situation"] and "due on 28 Oct 2026" in d["body"], d["situation"])
d = clearance.draft_mail(rowd(overdue=None, due=None))
check("no usable due date: says so, does not claim it is late", "no usable due date" in d["situation"] and "late." not in d["body"].split("eHub has no due date")[0])
d = clearance.draft_mail(rowd(step="Assign Clearing Agent", agent=""))
check("assign agent: to the BU", d["to"] == "gh@mantrac.example" and "assign a clearing agent" in d["body"], d)
d = clearance.draft_mail(rowd(step="Submit to BU"))
check("submit to BU: to the COE, missing COE address named, nothing invented", d["to"] == "" and any("COE" in m for m in d["missing"]), d)
d = clearance.draft_mail(rowd(agent="UNKNOWN AGENT"))
check("an agent with no address on file: To stays empty and it says so", d["to"] == "" and "UNKNOWN AGENT" in d["missing"][0], d)
d = clearance.draft_mail(rowd(step="Completed", completed="2026-10-01", overdue=None))
check("completed: a thank-you, nothing chased", "completed" in d["situation"] and "Thank you" in d["body"] and "URGENT" not in d["subject"])
reply = atlas_skills.answer("clearance", "write an email for TEST-0002", None)["answer"]
check("ATLAS writes it in the chat, with the situation and an Outlook link through the page",
      "situation: Customs Release" in reply and "To: ali@agent.example" in reply and "/clearance#q=TEST-0002&mail=GH" in reply
      and "escalated" in reply, reply[:400])
st, b2, _h = call(V, "GET", "/api/clearance/draft?awb=TEST-0002&bu=GH")
check("the page's Mail uses the same draft", js(b2)["draft"]["subject"].startswith("URGENT: TEST-0002"), b2[:200])

print("\n4c. ATLAS KEEPS THE OTHER QUESTIONS (an export is loaded)")
for q, ctx in [("did this job run against the real eHub?", {"domain": "po"}),
               ("where do the eHub records come from", {"domain": "po"}),
               ("which shipments are under clearance in eHub", {}),
               ("how does the eHub login work?", {}),
               ("why did the run fail to log in to eHub", {}),
               ("was shipment 1234567890 escalated to human action?", {}),
               ("what needs escalation in this run?", {}),
               ("how does customs clearance work?", {})]:
    check("not the export: " + q, atlas_skills.detect(q, None, ctx) != "clearance", atlas_skills.detect(q, None, ctx))
vals = clearance.query(None)["values"]
f = atlas_skills._cl_filters("how many shipments are not completed", dict(vals, years=[]))
check("'not completed' means open, not completed", f.get("flag") == "open", f)
f = atlas_skills._cl_filters("not yet cleared shipments", dict(vals, years=[]))
check("'not yet cleared' means open", f.get("flag") == "open", f)
for q in ["which shipments may be late in CAT", "sea shipments in the market", "a separate decision for the junior team"]:
    f = atlas_skills._cl_filters(q, dict(vals, years=["2026"]))
    check("no month read from ordinary words: " + q, "month" not in f, f)
f = atlas_skills._cl_filters("GH shipments in June 2026", dict(vals, years=["2026"]))
check("a real month is read", f.get("month") == "2026-06", f)
f = atlas_skills._cl_filters("eta in may", dict(vals, years=["2026"]))
check("'may' as a month only with ETA/month or a year", f.get("month") == "2026-05", f)
st, r = post(V, V_csrf, "/api/clearance/contact-log", {"awb": "TEST-0002", "bu": "GH", "kind": "call", "to": "[click](/clearance) +99 not on file"})
log = [c for c in clearance.contact_log("TEST-0002") if c["kind"] == "call"]
check("a recipient not on file is not recorded as typed", log and log[-1]["to"] == "", log)
st, b, _h = call(O, "POST", "/api/clearance/escalate", None, O_csrf, {"Content-Type": "application/json"}, raw=True)
st, b, _h = call(O, "POST", "/api/clearance/contacts", b'["not", "an", "object"]', A_csrf, {"Content-Type": "application/json"}, raw=True)
check("a body that is not an object is a clean 400 / 403, never a crash", st in (400, 403), st)
st, b, _h = call(A, "POST", "/api/clearance/contacts", b'{"agents": {"X": "not-an-object"}}', A_csrf, {"Content-Type": "application/json"}, raw=True)
check("a contact that is not an object is refused", st == 200 and js(b).get("ok") is False, b[:120])

print("\n5. THE PAGES")
html = open(os.path.join(HERE, "dashboard", "static", "clearance.html"), encoding="utf-8").read()
idx = open(os.path.join(HERE, "dashboard", "static", "index.html"), encoding="utf-8").read()
check("the Control Tower menu opens Clearance", "href:'/clearance'" in idx and "location.href = p.href" in idx)
import re as _re                                              # noqa: E402
check("the page loads nothing from the internet (its scripts and styles are this server's own)",
      not _re.search(r'(src|href)="(https?:)?//', html) and "cdnjs" not in html
      and set(_re.findall(r'<script src="([^"]+)"', html)) <= {"/static/search.js", "/static/todo.js"}
      and "https://" not in html.split("<script>")[1])
check("uploads carry the session's CSRF token", '"X-CSRF-Token": PF.csrf' in html and "/api/clearance/upload" in html)
check("the page links back to the Control Tower", 'href="/"' in html)

try:
    from playwright.sync_api import sync_playwright
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    cookie = [c for c in O_jar][0]
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe) if os.path.exists(exe) else p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1500, "height": 950})
        ctx.add_cookies([{"name": cookie.name, "value": cookie.value, "url": BASE}])
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(BASE + "/clearance")
        pg.wait_for_function("document.getElementById('e-count').textContent.includes('6 shipments')", timeout=15000)
        check("in a browser: the page shows the loaded export", "6 shipments" in pg.locator("#e-count").inner_text())
        check("in a browser: an Operator sees 'Load new export'", pg.locator("#loadBtn").is_visible())
        pg.locator('.kpi[data-set*="pastdue"]').click()
        pg.wait_for_selector("#dd.show", timeout=5000)
        check("in a browser: clicking a card lists exactly those shipments in a pop-up",
              pg.locator("#dd-rows tr").count() == 2 and "2" in pg.locator("#dd-sum").inner_text()
              and pg.locator("#e-count").inner_text() == "6 shipments", pg.locator("#dd-info").inner_text())
        pg.click("#dd-filter")
        check("in a browser: 'Filter the page to this' filters the page", pg.locator("#e-count").inner_text() == "2 shipments"
              and not pg.locator("#dd").is_visible(), pg.locator("#e-count").inner_text())
        pg.locator('[data-clear="flag"]').click()
        pg.locator('.kpi[data-set*="pastdue"]').click(modifiers=["Shift"])
        check("in a browser: Shift+click filters directly", pg.locator("#e-count").inner_text() == "2 shipments"
              and not pg.locator("#dd").is_visible())
        pg.locator('[data-clear="flag"]').click()
        row = pg.locator("#xbody tr", has_text="TEST-0002")
        check("in a browser: the table has Escalate (shown as Escalated), Call and Mail",
              row.locator('[data-act="esc"]').inner_text() == "Escalated" and row.locator('[data-act="call"]').count() == 1
              and row.locator('[data-act="mail"]').count() == 1)
        row.locator('[data-act="mail"]').click()
        pg.wait_for_selector("#mTo", timeout=10000)
        check("in a browser: Mail drafts to the agent, Cc the BU, with the export's facts",
              pg.locator("#mTo").input_value() == "ali@agent.example; ops@agent.example" and pg.locator("#mCc").input_value() == "gh@mantrac.example"
              and "Customs Release" in pg.locator("#mBody").input_value() and "55 days past due" in pg.locator("#mSub").input_value())
        check("in a browser: the draft says which situation ATLAS wrote it for", "Customs Release" in pg.locator("#m-body .note").first.inner_text())
        pg.keyboard.press("Escape")
        row.locator('[data-act="call"]').click()
        check("in a browser: Call offers the numbers on file only", pg.locator('#m-body a[href^="tel:"]').count() == 1
              and pg.locator('#m-body a[href^="tel:"]').get_attribute("href") == "tel:+20212345678")
        pg.keyboard.press("Escape")
        other = pg.locator("#xbody tr", has_text="TEST-0006")
        other.locator('[data-act="esc"]').click()
        pg.fill("#eWhy", "Correction pending for 3 weeks")
        pg.locator("#m-act button", has_text="Escalate").click()
        pg.wait_for_function("[...document.querySelectorAll('#xbody tr')].some(t => t.textContent.includes('TEST-0006') && t.textContent.includes('Escalated'))", timeout=10000)
        check("in a browser: escalating marks the row and puts it at the top of the alerts",
              "TEST-0006" in pg.locator("#alerts .al.es").first.inner_text())
        pg.click("#askFab")
        pg.fill("#askIn", "which clearing agent has the most past due?")
        pg.locator("#askForm button").click()
        pg.wait_for_function("(() => { const b = document.querySelectorAll('#askLog .bot'); return b.length >= 2 && !b[b.length - 1].textContent.startsWith('…'); })()", timeout=15000)
        ans = pg.locator("#askLog .bot").last
        check("in a browser: ATLAS answers on the page", "TEST AGENT" in ans.inner_text(), ans.inner_text())
        ans.locator("a").first.click()
        pg.wait_for_timeout(400)
        check("in a browser: ATLAS's link filters the page", "Open and past due" in pg.locator("#pills").inner_text(), pg.locator("#pills").inner_text())
        pg.goto(BASE + "/clearance#q=TEST-0002&mail=GH")
        pg.wait_for_selector("#mTo", timeout=15000)
        check("in a browser: ATLAS's 'open in Outlook' link opens its draft on the page", pg.locator("#mSub").input_value().startswith("URGENT: TEST-0002"))
        pg.keyboard.press("Escape")
        pg.screenshot(path=os.path.join(TMP, "clearance.png"), full_page=True)
        check("in a browser: no script errors", not errors, errors)
        b.close()
except ImportError:
    print("  (Playwright not installed: browser checks skipped)")

print("\nATLAS sees the dashboards")
from dashboard import assistant as _as                       # noqa: E402
_vals = clearance.query(None)["values"]
_bu = clearance.query(None)["past_due_by_bu"][0][0]          # a BU with past-due shipments
_other = [b for b in _vals["bu"] if b != _bu][0]
_view = {"bu": _bu, "flag": "pastdue"}
_r = _as.answer("summarise this view", {}, {"page": "clearance", "view": _view})
_ans = _r.get("answer") or ""
_want = clearance.query({"bu": _bu, "flag": "pastdue"})["total"]
check("'summarise this view' counts the filters on screen, and says so",
      _r.get("intent") == "skill_clearance" and _ans.startswith("In the view you have open")
      and "**{0:,}** shipment".format(_want) in _ans and "filters on your screen" in _ans, _ans[:300])
_r = _as.answer("how many shipments overall?", {}, {"page": "clearance", "view": _view})
check("'overall' ignores the view",
      "**{0:,}** shipment".format(clearance.query(None)["total"]) in _r["answer"]
      and "view you have open" not in _r["answer"], _r["answer"][:200])
_r = _as.answer("how many shipments in {0}?".format(_other), {}, {"page": "clearance", "view": _view})
check("a question naming its own BU gets exactly that BU",
      "**{0:,}** shipment".format(clearance.query({"bu": _other})["total"]) in _r["answer"]
      and "view you have open" not in _r["answer"], _r["answer"][:200])
_r = _as.answer("summarise this view", {}, {"page": "clearance",
                "view": {"bu": "<script>", "flag": "anything", "month": "2026-13", "group": "x"}})
check("a view naming nothing in the export is ignored, never echoed",
      "<script>" not in _r["answer"] and "view you have open" not in _r["answer"], _r["answer"][:200])
_r = _as.answer("summarise this view", {}, {"view": _view})
check("the view counts only when asked from the Clearance page", "view you have open" not in (_r.get("answer") or ""))
for _q in ("how does clearance work?", "what does the clearance page show?"):
    _r = _as.answer(_q, {}, {"page": "clearance"})
    check("'{0}' explains the page".format(_q), _r.get("intent") == "handbook" and
          "Clearance KPI 360" in _r["answer"] and "Escalate" in _r["answer"], _r.get("intent"))
_head = clearance.query(None)
for _q in ("what's on the dashboard?", "status", "what am I looking at", "how is everything today?"):
    _r = _as.answer(_q, {}, {})
    check("'{0}' on the main page: the automation and the Clearance headline".format(_q),
          _r.get("intent") == "dashboard" and "standing by" in _r["answer"]
          and "{0:,} shipments".format(_head["total"]) in _r["answer"], (_r.get("intent"), _r.get("answer", "")[:200]))
check("an empty run's summary shows the dashboard, not 'nothing to summarize'",
      "On the Control Tower now" in _as.answer("give me a summary", {}, {})["answer"])
for _q, _not in (("what's happening with the failed shipments", "dashboard"),
                 ("give me an overview of the run", "dashboard"),
                 ("how is everything with DHL", "dashboard"),
                 ("what's happening right now", "dashboard"),
                 ("what's happening with 157-49568713", "dashboard")):
    check("'{0}' keeps its own meaning, not the dashboard overview".format(_q),
          _as.detect_intent(_q) != _not, _as.detect_intent(_q))
_r = _as.answer("summarise this view", {}, {"page": "clearance", "view": dict(_view, more="1")})
check("a filter ATLAS cannot count by (search, stage…) is admitted, not hidden",
      "can't count by" in _r["answer"], _r["answer"][-300:])
_r = _as.answer("what's the PO status here", {}, {"page": "clearance", "view": _view})
check("a PO question on the Clearance page is not answered from the export",
      _r.get("intent") != "skill_clearance", _r.get("intent"))
_r = _as.answer("summarise everything on my screen", {}, {"page": "clearance", "view": _view})
check("'everything on my screen' still means the view", "view you have open" in _r["answer"], _r["answer"][:160])
_q0 = clearance.query(None)
for _q in ("which BU is the worst?", "top 5 late shipments", "what is the on-time rate?", "which step is the bottleneck?"):
    _r = _as.answer(_q, {}, {})
    check("'{0}' on the main page is answered from the export, not the model".format(_q),
          _r.get("intent") == "skill_clearance", _r.get("intent"))
_top_bu, _n = _q0["past_due_by_bu"][0]
check("'which BU is the worst?' answers first with the BU and its count",
      _as.answer("which BU is the worst?", {}, {})["answer"].startswith(
          "**{0}** is the BU with the most past-due shipments: **{1:,}**".format(_top_bu, _n)))
check("'what is the on-time rate?' answers first with the rate",
      _as.answer("what is the on-time rate?", {}, {})["answer"].startswith(
          "The on-time rate is **{0}%**".format(round(100.0 * _q0["on_time"] / _q0["total"]))))
_run0 = _a0.RunData({})
for _q in ("search the web for customs news in Nigeria", "any news from Egypt", "what is the weather in Kenya",
           "what is the capital of Ghana?", "what does clearing mean?", "who are the bus drivers", "is Emirates on time?"):
    check("'{0}' is not answered with clearance figures".format(_q), atlas_skills.detect(_q, _run0, {}) != "clearance",
          atlas_skills.detect(_q, _run0, {}))
check("a role without clearance.view never gets clearance figures from ATLAS",
      atlas_skills.detect("which BU is the worst?", _run0, {"clearance": "0"}) is None
      and atlas_skills.detect("summarise this view", _run0, {"clearance": "0", "page": "clearance"}) is None)
_srv = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "server.py"), encoding="utf-8").read()
check("the server takes the view only from the Clearance page, short strings, known keys",
      'if raw_context.get("page") == "clearance" and isinstance(raw_context.get("view"), dict)' in _srv
      and 'str(v)[:80]' in _srv)

print("\nWhere shipments clear: the map, the headline, forwarder and agent filters")
from dashboard import atlas_skills as _sk                     # noqa: E402
_pl = clearance.places()
check("a BU is placed only where its code is the country's ISO code",
      _pl["EG"] == {"iso": "818", "country": "Egypt"} and "JR" not in _pl and "LB" not in _pl)
check("ATLAS reads a country name with the same table ('in Egypt' -> EG)",
      _sk._cl_filters("late shipments in Egypt", {"bu": ["EG", "GH"], "mode": [], "div": [], "step": [],
                                                    "agent": [], "ffw": [], "years": []}).get("bu") == "EG")
_cl0 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "static", "clearance.html"), encoding="utf-8").read()
check("Clearance keeps the headline from the rows on screen, with no map of its own",
      "function headline(R)" in _cl0 and "function mapCard()" not in _cl0 and 'id="mapcard"' not in _cl0)
_ix0 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "static", "index.html"), encoding="utf-8").read()
check("the Overview no longer has the clearance map card (removed at the operator's request)",
      "data-clearmap" not in _ix0 and "clearmap.js" not in _ix0
      and not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "static", "clearmap.js")))
_ms = clearance.map_summary()
_q1 = clearance.query(None)
check("the map's figures are the export's own counts (open, past due, per BU)",
      _ms["open"] == _q1["open"] and _ms["late"] == _q1["past_due"] and _ms["total"] == _q1["total"]
      and sum(b["late"] for b in _ms["bus"]) == _q1["past_due"]
      and {b["bu"]: b["late"] for b in _ms["bus"] if b["late"]} == dict(_q1["past_due_by_bu"]), _ms)
check("forwarder and clearing-agent filters, kept in the link and sent to ATLAS",
      'id="f-ffw"' in _cl0 and 'id="f-agent"' in _cl0 and '"ffw","agent","status"' in _cl0
      and 'ALLK.forEach((k) => { if (state[k] && state[k] !== "All") v[k] = state[k]; });' in _cl0)
_ag = clearance.query(None)["values"]["agent"][0]
check("ATLAS keeps an agent from the screen only if the export has it",
      _sk.clean_view({"agent": _ag, "ffw": "NOT A FORWARDER"}, clearance.query(None)["values"]) == {"agent": _ag})

print("\nThe platform menu on the Clearance page")
_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "static")
_cl = open(os.path.join(_dir, "clearance.html"), encoding="utf-8").read()
check("the page sends ATLAS its filters, and marks the ones it cannot count by",
      'context: {page: "clearance", view: atlasView()}' in _cl and 'v.more = "1"' in _cl
      and "state.ontime === 0" in _cl and "state.ontime === false" not in _cl)
_ix = open(os.path.join(_dir, "index.html"), encoding="utf-8").read()
check("Clearance shows the platform's pages, itself marked current",
      all('href="/#{0}"'.format(x) in _cl for x in ("overview", "shipments", "po", "atlas", "human", "access"))
      and '<a href="/clearance" aria-current="page">' in _cl)
check("Access appears only for those who manage users", 'id="pbAccess" hidden' in _cl
      and 'PF.perms.includes("users.manage")' in _cl)
check("Sign out on Clearance sends the CSRF token", '"/api/auth/logout"' in _cl and '"X-CSRF-Token": PF.csrf' in _cl)
check("The Control Tower opens the page named after #, menu pages only",
      "function openFromHash" in _ix and "!x.href && !x.film" in _ix and "can-admin" in _ix)

_know = atlas_skills.answer("clearance", "do you know egypt?", None)["answer"]
check("'Do you know Egypt?' gets a short yes with the details offered, not every figure",
      atlas_skills.detect("do you know egypt?", None, {}) == "clearance" and _know.startswith("Yes")
      and "Egypt (EG)" in _know and "Past due by step" not in _know and "Longest waits" not in _know)
check("'Tell me about Ghana' anywhere is the export's GH figures",
      atlas_skills.detect("tell me about Ghana", None, {}) == "clearance")
_missing = atlas_skills.answer("clearance", "do you know the status of 8842001173", None)["answer"]
check("A shipment number the export does not hold is said to be missing, not answered with the whole export",
      "can't find 8842001173" in _missing and "Past due by step" not in _missing)

_h = open(os.path.join(HERE, "dashboard", "static", "clearance.html"), encoding="utf-8").read()
check("A click on a chart opens the list of exactly those shipments (the page's filters plus the clicked one)",
      "function drill(o)" in _h and "function rowsWith(o)" in _h and "Object.assign(state, o)" in _h
      and "if (e.shiftKey || active) apply(o); else drill(o);" in _h)
check("The list: counts, search, sortable columns, CSV of the list, a row opens its details, filter the page to it",
      all(x in _h for x in ('id="dd-sum"', 'id="dd-q"', "data-ddsort", 'id="dd-csv"', 'id="dd-filter"', "data-row=")))
check("Every part of a mode card opens its list: on-time label, total, completed bar, past-due centre",
      'class="ok ${good ? "good" : "bad"}" data-set="${setAttr({mode: m, ontime: 0})}"' in _h
      and 'class="tot" data-set="${setAttr({mode: m})}"' in _h and 'data-set="${setAttr({mode: m, flag: "done"})}"' in _h
      and 'class="cset" data-set="${setAttr({mode: m, flag: "pastdue"})}"' in _h and "on-time below 95%" in _h)
check("Closed, the list is hidden (the '/' search is not blocked by it)",
      'aria-labelledby="dd-title" hidden>' in _h and '$("dd").hidden = true' in _h)
check("The list sits under the shipment drawer and closes with Escape",
      ".dd{position:fixed;inset:0;z-index:69" in _h and '$("dd").classList.contains("show")) { ddClose()' in _h)
check("Every page declares an icon (no /favicon.ico 404 in the console)",
      'rel="icon"' in _h and "def _send_no_icon" in open(os.path.join(HERE, "dashboard", "server.py"), encoding="utf-8").read())

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
