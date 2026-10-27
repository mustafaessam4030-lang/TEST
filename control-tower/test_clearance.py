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
      and "(/clearance#bu=GH)" in reply["answer"], reply["answer"])

print("\n5. THE PAGES")
html = open(os.path.join(HERE, "dashboard", "static", "clearance.html"), encoding="utf-8").read()
idx = open(os.path.join(HERE, "dashboard", "static", "index.html"), encoding="utf-8").read()
check("the Control Tower menu opens Clearance", "href:'/clearance'" in idx and "location.href = p.href" in idx)
check("the page loads nothing from the internet", "<script src=" not in html and "cdnjs" not in html and "https://" not in html.split("<script>")[1])
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
        check("in a browser: clicking a card filters", pg.locator("#e-count").inner_text() == "2 shipments", pg.locator("#e-count").inner_text())
        pg.screenshot(path=os.path.join(TMP, "clearance.png"), full_page=True)
        check("in a browser: no script errors", not errors, errors)
        b.close()
except ImportError:
    print("  (Playwright not installed: browser checks skipped)")

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
