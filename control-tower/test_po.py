"""
PO Automation — the document pipeline, end to end, with nothing mocked that
the pipeline itself does.

Real here: the PDF reading (PyMuPDF; Tesseract for a scanned page), the
extraction and validation, the approved template filled with openpyxl and
read back, the state machine, the event log and send ledger, the Hub
document retrieval in a real Chromium browser, the control plane's HTTP
routes, RBAC and audit, the worker's push path, ATLAS's answers.

Stand-ins, said plainly:
  * THE HUB — a local page that serves a shipment page with its attached
    documents, behind HTTP basic auth like the real Hub. The navigation to a
    shipment in the real Hub is the automation's existing, separately tested
    code; here `open_shipment` goes to the stand-in page.
  * MICROSOFT GRAPH — a local HTTP server that speaks the three Graph calls
    the mailer uses (token, create draft, send, find in Sent Items) and can
    fail each one on demand. A send counts as CONFIRMED only when this
    server has the message in Sent Items.

    1  discovery and retrieval (Hub, real browser)     11 Graph failure
    2  PDF parsing (text, OCR, unreadable)              12 duplicate-send prevention
    3  extraction (typed, missing, ambiguous)           13 retry behaviour
    4  validation success                               14 state machine
    5  validation mismatch — the gate                   15 RBAC (control plane)
    6  template generation and read-back               16 audit
    7  template versioning and tampering               17 ATLAS PO context
    8  output storage (deterministic, no overwrite)    18 PO failure intelligence + learning
    9  email preparation                                19 worker path (real subprocess)
    10 Graph send success → confirmed                   20 END TO END, and the page

Run:  python test_po.py
"""

import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))
WORK = Path(tempfile.mkdtemp(prefix="ct_po_test_"))
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "intel")
os.environ["ATLAS_DATA_ORIGIN"] = "test"
os.environ["PO_DATA_DIR"] = str(WORK / "po")
os.environ.pop("PO_OUTPUT_DIR", None)
# The stand-in mailbox below receives the "sends" of stand-in (TEST) jobs.
# Without this switch a document not observed in the real eHub is never sent.
os.environ["PO_ALLOW_TEST_SEND"] = "1"

import fitz                                                    # noqa: E402
from openpyxl import load_workbook                             # noqa: E402

from po import doctypes, extract as X, pipeline as P, store as S, template as T  # noqa: E402
from po import validate as V, service as SV, web as W, mail as M                  # noqa: E402
from po import ehub as EH                                                          # noqa: E402
from intelligence import failures as F, events as E, learning as L                 # noqa: E402

PASS, FAIL, SKIP = [], [], []
SECRET = "graph-secret-" + hashlib.sha1(os.urandom(8)).hexdigest()


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(str(detail)[:500]) if detail and not condition
                                 else ""))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


# ─────────────────────────────────────────────────────────────────────────
# DOCUMENTS
# ─────────────────────────────────────────────────────────────────────────
# A declaration in ICUMS wording. Its arithmetic is consistent: total duty
# 653,492.35 = VAT block 407,172.30 + import duty 246,320.05.
def boe_text(bl="176-88452310", number="40726534505 / 00", date="16/07/2026",
             duty="653,492.35", import_duty="246,320.05", extra="", invoice="9116093"):
    # The normal Bill of Entry prints the supplier invoice No. (G4's only
    # source); invoice=None is a document that does not.
    extra = ("Invoice No: {0}\n".format(invoice) if invoice else "") + extra
    return ("GHANA REVENUE AUTHORITY - CUSTOMS DIVISION\n"
            "ICUMS BILL OF ENTRY / ASSESSMENT NOTICE\n"
            "Declaration No: {number}\n"
            "User Reference: MTG-LOG-2026-0341\n"
            "BL/AWB No: {bl}\n"
            "Date of Assessment: {date}\n"
            "Total Invoice Value (CIF) USD 169,740.11\n"
            "Exchange Rate 11.20\n"
            "Import Duty                       {import_duty}\n"
            "Import VAT                        304,446.44\n"
            "Network Charge VAT                  1,066.03\n"
            "Import NHIL                        50,741.08\n"
            "GETFund Import                     50,741.08\n"
            "Network Charge VAT Fund Levy          177.67\n"
            "Total Duty and Levies GHS         {duty}\n{extra}").format(
                number=number, bl=bl, date=date, duty=duty, import_duty=import_duty,
                extra=extra)


def pdf_of(text, scanned=False, password=None):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), text, fontsize=10 if scanned else 9)
    if scanned:
        pix = page.get_pixmap(dpi=220)
        img = fitz.open()
        p2 = img.new_page(width=page.rect.width, height=page.rect.height)
        p2.insert_image(p2.rect, stream=pix.tobytes("png"))
        doc = img
    if password:
        out = io.BytesIO()
        doc.save(out, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
        return out.getvalue()
    return doc.tobytes()


GOOD = pdf_of(boe_text())
OTHER_SHIPMENT = pdf_of(boe_text(bl="176-99001122"))

# ─────────────────────────────────────────────────────────────────────────
# THE eHUB STAND-IN — a list with statuses and Manage links; a details page
# with a Documents tab; Bill Entry documents behind WebForms postbacks or
# links; all behind basic auth like eHub. NOT the real eHub: what it proves
# is the discovery and download code, against a page shaped like one.
# ─────────────────────────────────────────────────────────────────────────
HUB_USER, HUB_PASS = "hub.reader", "hub-" + hashlib.sha1(os.urandom(6)).hexdigest()
EHUB = []             # [(bol, carrier, status, docs, has_docs_section)] in list order
FILES = {}            # file key -> bytes
HITS = {}


def ehub_row(bol, status="Under Clearance", docs=(), carrier="DHL Express", section=True,
             layout="tabs"):
    EHUB.append({"bol": bol, "carrier": carrier, "status": status, "docs": list(docs),
                 "section": section, "layout": layout})


PRESSED = []          # any record-changing control the automation pressed (must stay empty)


def real_layout(r):
    """
    The Manage page as the real eHub shows it (screenshot, 2026-10-06):
    comments, then a "Documents" heading and the file names as links, then
    Choose Files / Upload, then Save / Correction Required / Complete.
    """
    docs = "".join("<div class='doc-row'><a href='/ehub/file/{1}?name={2}'>{0}</a></div>".format(
        name, key, urllib.parse.quote(name)) for name, key, mode in r["docs"])
    return ("<html><body><form id='aspnetForm' method='post' action='/ehub/press/{0}'>"
            "<label>Delay Codes :</label><select><option>Select Clearance Delay</option></select>"
            "<label>Inspection Comments :</label><textarea></textarea>"
            "<label>General Comments :</label><textarea>15/09 CARGO DELIVERED</textarea>"
            "<div class='docs'><h4>Documents</h4>{1}</div>"
            "<input type='file' name='files'><button type='submit' name='act' value='upload'>"
            "Upload</button>"
            "<button type='submit' name='act' value='save'>Save</button>"
            "<button type='submit' name='act' value='correction'>Correction Required</button>"
            "<button type='submit' name='act' value='complete'>Complete</button>"
            "</form></body></html>").format(r["bol"], docs)


def ehub_get(bol):
    return next((r for r in EHUB if r["bol"] == bol), None)


# The Shipments List and a record's page as the real eHub showed them on
# 6 Oct (screenshots: KKLUENR260174). SIMULATED: the structure, not eHub.
SEARCHED = []


def shipments_page(query):
    bol = (query.get("bol") or [""])[0].strip()
    SEARCHED.append(bol)
    # The list as the Hub's own "Under Clearance" filter shows it, or (only if
    # something were typed in the box) the search result for that number.
    wanted = (query.get("status") or [""])[0]
    hits = [r for r in EHUB if r.get("layout") == "screen" and
            ((bol and r["bol"] == bol) or (not bol and wanted and
                                           r["status"].startswith(wanted)))]
    rows = "".join(
        "<tr><td>{0}</td><td>{1}</td><td>GH</td><td>CAT FFW</td><td>Ocean</td><td>{2}</td>"
        "<td>Roro</td><td>14/09/2026</td><td><span class='badge'>{3}</span></td>"
        "<td><a class='btn' href='/ehub/bu/{0}'>Manage</a></td></tr>".format(
            r["bol"], r.get("una", ""), r["carrier"], r["status"]) for r in hits)
    table = ("<table class='grid'><thead><tr><th>BOL/AWB Number</th><th>UNA+ Invoice Number</th>"
             "<th>Territory</th><th>Freight Forwarder</th><th>Shipment Mode</th><th>Carrier Name"
             "</th><th>Shipment Type</th><th>ETA</th><th>Status</th><th></th></tr></thead>"
             "<tbody>{0}</tbody></table>".format(rows)) if (bol or wanted) else ""
    return ("<html><body><h3>Shipments <small>List</small></h3><h4>Searching Options</h4>"
            "<form method='get' action='/ehub/shipments'>"
            "<div class='col'><label>BOL/AWB Number</label><input type='text' name='bol'></div>"
            "<div class='col'><label>UNA+ Invoice Number</label><input type='text' name='una' "
            "placeholder='Enter UNA+ Invoice Number'></div>"
            "<div class='col'><label>Status :</label><select name='status'><option>All</option>"
            "<option>Under Clearance</option></select></div>"
            "<button type='submit'>Search</button></form>"
            "<h4>Searching Result</h4>{0}</body></html>").format(table)



def bu_page(r):
    rows = "".join(
        "<div class='doc-row'><div class='name'>{0}</div><div class='actions'>"
        "<button type='button' class='del' onclick=\"document.getElementById('del').submit()\">"
        "Delete</button><button type='button' class='dl' onclick=\"location.href='/ehub/file/{1}"
        "?name={2}'\">Download</button></div></div>".format(name, key, urllib.parse.quote(name))
        for name, key, mode in r["docs"])
    return ("<html><body><ul class='tabs'><li class='active'><a href='#'>BU Shipment Info</a></li>"
            "<li><a href='#'>COE Shipment Info</a></li><li><a href='#'>Logs</a></li></ul>"
            "<h5>BU Information</h5><label>Current Status :</label><div>{1}</div>"
            "<h5>Clearing Agent Information</h5><label>BOE / SGD / Customs Declaration Number :"
            "</label><input value='{2}'>"
            "<label>General Comments :</label><textarea>AWAITING DVLA REGISTRATION DOCUMENTS"
            "</textarea><div class='docs'><h5>Documents</h5>{3}</div>"
            "<form id='del' method='post' action='/ehub/press/{0}'><input type='hidden' name='act' "
            "value='delete'></form>"
            "<input type='file'><button type='button' onclick=\"document.getElementById('del')"
            ".submit()\">Upload</button>"
            "<button type='button'>Save</button><button type='button'>Correction Required</button>"
            "<button type='button'>Complete</button></body></html>").format(
                r["bol"], r["status"], r.get("boe", ""), rows)


class Hub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            BaseHTTPRequestHandler.handle(self)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass          # a client closing an idle keep-alive connection

    def log_message(self, *a):
        pass

    def _reply(self, status, body, kind="text/html; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        good = "Basic " + base64.b64encode("{0}:{1}".format(HUB_USER, HUB_PASS).encode()).decode()
        if (self.headers.get("Authorization") or "") != good:
            self._reply(401, "sign in", extra={"WWW-Authenticate": 'Basic realm="ehub"'})
            return False
        return True

    def _file(self, key, name):
        if key.startswith("flaky") and HITS.get("file:" + key, 0) <= 2:
            self._reply(503, "busy")
            return
        data = FILES.get(key)
        if data is None:
            self._reply(404, "missing")
            return
        kind = "application/pdf" if data.startswith(b"%PDF") else "text/html"
        self._reply(200, data, kind, {"Content-Disposition":
                                      'attachment; filename="{0}"'.format(name)})

    def do_GET(self):
        if not self._authed():
            return
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        HITS[path] = HITS.get(path, 0) + 1
        if path == "/ehub/shipments":
            self._reply(200, shipments_page(urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query)))
            return
        if path.startswith("/ehub/bu/"):
            r = ehub_get(path.rsplit("/", 1)[-1])
            self._reply(200, bu_page(r) if r else "no record")
            return
        if path == "/ehub/list":
            rows = "".join(
                "<tr><td>{0}</td><td>{1}</td><td>20/10/2026</td><td>{2}</td>"
                "<td><a href='/ehub/manage/{0}'>Manage</a></td></tr>".format(
                    r["bol"], r["carrier"], r["status"]) for r in EHUB)
            self._reply(200, "<html><body><table id='grid'><thead><tr><th>BOL/AWB Number</th>"
                             "<th>Carrier Name</th><th>ETA</th><th>Status</th><th></th></tr>"
                             "</thead><tbody>{0}</tbody></table></body></html>".format(rows))
            return
        if path.startswith("/ehub/manage/"):
            r = ehub_get(path.rsplit("/", 1)[-1])
            if r is None:
                self._reply(404, "no record")
                return
            if r.get("layout") == "real":
                self._reply(200, real_layout(r))
                return
            docs = ""
            for i, (name, key, mode) in enumerate(r["docs"]):
                if mode == "postback":
                    link = ("<a id='ctl00_docs_lnk{0}' href=\"javascript:__doPostBack("
                            "'ctl00$docs$lnk{0}','')\">View</a>").format(i)
                else:
                    link = "<a href='/ehub/file/{0}?name={1}'>View</a>".format(
                        key, urllib.parse.quote(name))
                docs += "<tr><td>{0}</td><td>12/10/2026</td><td>{1}</td></tr>".format(name, link)
            section = ("<div id='docs' style='display:none'><h3>Documents</h3><table>"
                       "<tr><th>File</th><th>Uploaded</th><th></th></tr>{0}</table></div>"
                       .format(docs)) if r["section"] else ""
            tab = ("<li><a href='#' onclick=\"document.getElementById('docs').style.display="
                   "'block';return false;\">Documents</a></li>") if r["section"] else ""
            self._reply(200, (
                "<html><body><form id='aspnetForm' method='post'>"
                "<input type='hidden' name='__EVENTTARGET' id='__EVENTTARGET'>"
                "<h2>Shipment {0}</h2><ul class='tabs'><li><a href='#'>Shipment Info</a></li>{1}"
                "</ul><div id='info'>Status: {2}</div>{3}</form>"
                "<script>function __doPostBack(t,a){{document.getElementById('__EVENTTARGET')"
                ".value=t;document.getElementById('aspnetForm').submit();}}</script>"
                "</body></html>").format(r["bol"], tab, r["status"], section))
            return
        if path.startswith("/ehub/file/"):
            key = path.rsplit("/", 1)[-1]
            HITS["file:" + key] = HITS.get("file:" + key, 0) + 1
            name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("name", [key])[0]
            self._file(key, name)
            return
        self._reply(404, "no")

    def do_POST(self):
        if not self._authed():
            return
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        n = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode(errors="ignore"))
        if path.startswith("/ehub/press/"):
            PRESSED.append((path, form.get("act")))
            self._reply(200, "<html><body>record changed</body></html>")
            return
        r = ehub_get(path.rsplit("/", 1)[-1]) if path.startswith("/ehub/manage/") else None
        target = (form.get("__EVENTTARGET") or [""])[0]
        m = re.match(r"ctl00\$docs\$lnk(\d+)$", target)
        if r is None or not m:
            self._reply(404, "no")
            return
        name, key, mode = r["docs"][int(m.group(1))]
        HITS["file:" + key] = HITS.get("file:" + key, 0) + 1
        self._file(key, name)


hub_srv = ThreadingHTTPServer(("127.0.0.1", 0), Hub)
hub_srv.daemon_threads = True
threading.Thread(target=hub_srv.serve_forever, daemon=True).start()
HUB = "http://127.0.0.1:{0}".format(hub_srv.server_address[1])


def stub_rows(page):
    """The stand-in's list, read from the page — as ehub_rows reads eHub's."""
    page.goto(HUB + "/ehub/list")
    for cells in page.evaluate("""() => Array.from(document.querySelectorAll('#grid tbody tr'))
        .map(tr => Array.from(tr.querySelectorAll('td')).map(td => td.innerText.trim()))"""):
        yield {"bol_awb": cells[0], "carrier": cells[1], "status": cells[3], "table_page": 1,
               "view": "BU"}


def find_stub(page, reference, skip):
    from po.ehub import choose
    return choose(stub_rows(page), reference, skip)


def manage_stub(page, row):
    page.goto(HUB + "/ehub/list")
    page.locator("#grid tbody tr").filter(has_text=row["bol_awb"]).get_by_text("Manage").click()
    page.wait_for_load_state("load")
    return {"url": page.url, "title": page.title()}


# ─────────────────────────────────────────────────────────────────────────
# THE GRAPH STAND-IN
# ─────────────────────────────────────────────────────────────────────────
GRAPH = {"messages": {}, "sent": [], "plan": {"create": [], "send": []}, "tokens": set(),
         "counter": 0, "sends": 0, "hide_sent": False, "bodies": []}


class Graph(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            BaseHTTPRequestHandler.handle(self)
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass          # the "drop" plan closes the connection on purpose

    def log_message(self, *a):
        pass

    def _json(self, status, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        auth = self.headers.get("Authorization") or ""
        return auth.startswith("Bearer ") and auth[7:] in GRAPH["tokens"]

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        raw = self._body()
        if path.endswith("/oauth2/v2.0/token"):
            form = urllib.parse.parse_qs(raw.decode())
            if form.get("client_secret", [""])[0] != SECRET or \
                    form.get("grant_type", [""])[0] != "client_credentials":
                self._json(401, {"error": "invalid_client"})
                return
            token = "tok-" + hashlib.sha1(os.urandom(8)).hexdigest()
            GRAPH["tokens"].add(token)
            self._json(200, {"access_token": token, "expires_in": 3600})
            return
        if not self._authed():
            self._json(401, {"error": {"message": "InvalidAuthenticationToken"}})
            return
        m = re.match(r"^/v1\.0/users/([^/]+)/messages$", path)
        if m:
            plan = GRAPH["plan"]["create"]
            if plan:
                self._json(plan.pop(0), {"error": {"message": "planned failure"}})
                return
            body = json.loads(raw)
            GRAPH["counter"] += 1
            mid = "msg-{0}".format(GRAPH["counter"])
            imid = "<{0}.{1}@ata.test>".format(GRAPH["counter"], int(time.time()))
            att = body["attachments"][0]
            GRAPH["messages"][mid] = {"id": mid, "internetMessageId": imid,
                                      "subject": body["subject"], "mailbox": m.group(1),
                                      "to": body["toRecipients"][0]["emailAddress"]["address"],
                                      "attachment": att["name"],
                                      "attachment_bytes": base64.b64decode(att["contentBytes"])}
            GRAPH["bodies"].append(body)
            self._json(201, {"id": mid, "internetMessageId": imid})
            return
        m = re.match(r"^/v1\.0/users/([^/]+)/messages/([^/]+)/send$", path)
        if m:
            mid = urllib.parse.unquote(m.group(2))
            plan = GRAPH["plan"]["send"]
            step = plan.pop(0) if plan else None
            if isinstance(step, int):
                self._json(step, {"error": {"message": "planned failure"}})
                return
            msg = GRAPH["messages"].pop(mid, None)
            if msg is None:
                self._json(404, {"error": {"message": "ErrorItemNotFound"}})
                return
            GRAPH["sends"] += 1
            GRAPH["sent"].append(dict(msg, id="sent-" + mid,
                                      sentDateTime=time.strftime("%Y-%m-%dT%H:%M:%SZ")))
            if step == "drop":
                # Sent — and the connection drops before the answer.
                self.close_connection = True
                self.connection.shutdown(2)
                return
            self._json(202)
            return
        self._json(404, {"error": {"message": "unknown"}})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if not self._authed():
            self._json(401, {"error": {"message": "InvalidAuthenticationToken"}})
            return
        def find(mid):
            msg = GRAPH["messages"].get(mid)
            if msg:
                return msg, True
            sent = next((x for x in GRAPH["sent"] if x["id"] == mid), None)
            return sent, False
        m = re.match(r"^/v1\.0/users/([^/]+)/messages/([^/]+)/attachments$", parsed.path)
        if m:
            msg, _draft = find(urllib.parse.unquote(m.group(2)))
            if not msg:
                self._json(404, {"error": {"message": "ErrorItemNotFound"}})
                return
            # Graph reports the attachment item's size (a little over the file's).
            self._json(200, {"value": [{"name": msg["attachment"],
                                        "size": len(msg["attachment_bytes"]) + 200,
                                        "contentType": "application/vnd.openxmlformats"}]})
            return
        m = re.match(r"^/v1\.0/users/([^/]+)/messages/([^/]+)$", parsed.path)
        if m:
            # A message by id: an unsent draft is still here; a sent one is in
            # Sent Items under its own id (as Exchange does).
            msg, draft = find(urllib.parse.unquote(m.group(2)))
            if not msg:
                self._json(404, {"error": {"message": "ErrorItemNotFound"}})
                return
            self._json(200, {"id": msg["id"], "isDraft": draft, "subject": msg["subject"],
                             "internetMessageId": msg["internetMessageId"],
                             "sentDateTime": msg.get("sentDateTime"),
                             "toRecipients": [{"emailAddress": {"address": msg["to"]}}],
                             "from": {"emailAddress": {"address": msg["mailbox"]}}})
            return
        folder = re.search(r"/mailFolders/(SentItems|Drafts)/messages$", parsed.path)
        if folder:
            q = urllib.parse.parse_qs(parsed.query).get("$filter", [""])[0]
            want = re.search(r"internetMessageId eq '(.+)'", q)
            subject = re.search(r"subject eq '(.+)'", q)
            pool = GRAPH["sent"] if folder.group(1) == "SentItems" else \
                list(GRAPH["messages"].values())
            if folder.group(1) == "SentItems" and GRAPH["hide_sent"]:
                pool = []
            rows = [{"id": x["id"], "sentDateTime": x.get("sentDateTime"),
                     "internetMessageId": x["internetMessageId"], "subject": x["subject"],
                     "isDraft": folder.group(1) == "Drafts"}
                    for x in pool
                    if (want and x["internetMessageId"] == want.group(1)) or
                    (subject and x["subject"] == subject.group(1).replace("''", "'"))]
            self._json(200, {"value": rows})
            return
        self._json(404, {"error": {"message": "unknown"}})


graph_srv = ThreadingHTTPServer(("127.0.0.1", 0), Graph)
graph_srv.daemon_threads = True
threading.Thread(target=graph_srv.serve_forever, daemon=True).start()
GRAPH_URL = "http://127.0.0.1:{0}".format(graph_srv.server_address[1])
os.environ.update({"GRAPH_TENANT_ID": "tenant-ata", "GRAPH_CLIENT_ID": "client-ata",
                   "GRAPH_CLIENT_SECRET": SECRET, "GRAPH_BASE_URL": GRAPH_URL + "/v1.0",
                   "GRAPH_LOGIN_URL": GRAPH_URL, "PO_MAIL_SENDER": "ata@mantrac.com",
                   "PO_MAIL_RECIPIENT": "accounts.ghana@mantrac.com",
                   "PO_DEFAULT_SUPPLIER": "CAT", "PO_CONFIRM_WAIT_S": "3"})
CONFIG = P.config_from_env()
NOSLEEP = lambda s: None                                       # noqa: E731

# ─────────────────────────────────────────────────────────────────────────
# A REAL BROWSER
# ─────────────────────────────────────────────────────────────────────────
from playwright.sync_api import sync_playwright              # noqa: E402


def launch(pw):
    options = [{"headless": True}]
    if Path("/opt/pw-browsers").is_dir():
        options += [{"headless": True, "executable_path": str(b)} for b in
                    sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))]
    for o in options:
        try:
            return pw.chromium.launch(**o)
        except Exception:
            continue
    return None


PW = sync_playwright().start()
BROWSER = launch(PW)
if BROWSER is None:
    print("  FAIL  a real browser launched")
    print("0 passed, 1 failed")
    sys.exit(1)
CTX = BROWSER.new_context(http_credentials={"username": HUB_USER, "password": HUB_PASS})
PAGE = CTX.new_page()


def hub_source(skip=()):
    """The eHub source the job uses, on the stand-in's navigation."""
    return EH.EHubSource(PAGE, find_stub, manage_stub, skip=skip)


def new_store(name):
    return S.Store(folder=WORK / name)


def run_job(store, reference, request=None, source=None):
    record = store.create(doctypes.DEFAULT, reference, request or {"invoice_no": "9116093"},
                          started_by="omar.ops@mantrac.com")
    return P.process(store, record, source or hub_source(), CONFIG, sleep=NOSLEEP)


# ═════════════════════════════════════════════════════════════════════════
rule("1. eHUB DISCOVERY — Under Clearance → Manage → Documents → Bill Entry → identifier")
# ═════════════════════════════════════════════════════════════════════════
FILES.update({"good": GOOD, "other": OTHER_SHIPMENT, "inv": pdf_of("INVOICE 9116093\n" + "x" * 200),
              "flaky": pdf_of(boe_text(bl="176-66600033")), "notpdf": b"<html>not a pdf</html>",
              "e2e": pdf_of(boe_text(bl="176-12121212", number="40799887766 / 00")),
              "idmis": pdf_of(boe_text(bl="176-88800099"))})
ehub_row("176-30000001", status="Cleared", docs=[("Bill Entry 40711111111.pdf", "good", "postback")])
ehub_row("176-30000002", status="Under Clearance - Hold",
         docs=[("Bill Entry 40722222222.pdf", "good", "postback")])
ehub_row("176-88452310", carrier="Air France KLM Cargo",
         docs=[("Invoice 9116093.pdf", "inv", "link"),
               ("Bill Entry 40726534505.pdf", "good", "postback"),
               ("Packing List.pdf", "inv", "link")])
ehub_row("176-55500011", docs=[("Invoice 9116094.pdf", "inv", "link")])
ehub_row("176-77700022", docs=[("Bill Entry 40726534505.pdf", "good", "postback"),
                               ("Bill Entry 40799112233.pdf", "other", "postback")])
ehub_row("176-77700023", docs=[("Bill Entry 40726534505.pdf", "good", "postback"),
                               ("Bill Entry 40726534505 (1).pdf", "other", "postback")])
ehub_row("176-77700024", docs=[("Bill Entry 40726534505.pdf", "good", "postback"),
                               ("Bill Entry 40726534505 (1).pdf", "good", "postback")])
ehub_row("176-20000001", docs=[("Bill Entry.pdf", "good", "postback")])
ehub_row("176-10000001", section=False)
ehub_row("176-66600033", docs=[("Bill Entry 40726534505.pdf", "flaky", "link")])
ehub_row("176-44400044", docs=[("Bill Entry 4072.pdf", "notpdf", "link")])
ehub_row("176-99001122", docs=[("Bill Entry 40726534505.pdf", "good", "postback")])
ehub_row("176-88800099", docs=[("Bill Entry 40711111111.pdf", "idmis", "postback")])
ehub_row("176-12121212", carrier="Air France KLM Cargo",
         docs=[("Bill Entry 40799887766.pdf", "e2e", "postback")])
FILES["real"] = pdf_of(boe_text(bl="176-40926696", number="40926696852 / 01"))
FILES["kia"] = pdf_of("KIA1-G assessment 40926696852-01\n" + "x" * 200)
FILES["scan"] = pdf_of("scan 20260910094658174\n" + "x" * 200)
ehub_row("176-40926696", carrier="Kia Motors", layout="real",
         docs=[("BillofEntry_40926696852 (1) (1).pdf", "real", "link"),
               ("KIA1-G-40926696852-01 (1).pdf", "kia", "link"),
               ("20260910094658174 (1).pdf", "scan", "link")])

found = hub_source().fetch("176 88452310")
tr = found["trail"]
names = [x["step"] for x in tr["steps"]]
check("Steps run in the business order: record → clearance → Manage → identity → Documents → "
      "Bill Entry → identifier → download",
      names == ["ehub_record", "clearance_status", "manage", "identity", "documents_section",
                "bill_entry", "identifier", "download"] and all(x["ok"] for x in tr["steps"]),
      names)
check("1. The eHub record, with eHub's own values", found["hub"]["bol_awb"] == "176-88452310"
      and found["hub"]["carrier"] == "Air France KLM Cargo", found["hub"])
check("   ...its Status is exactly 'Under Clearance'",
      tr["clearance"] == {"required": "Under Clearance", "found": "Under Clearance", "ok": True})
check("2. Manage was opened (the record's details page)",
      tr["manage"]["url"].endswith("/ehub/manage/176-88452310"), tr["manage"])
check("3. The Documents section was reached (its tab opened) and its documents listed",
      tr["documents"]["found"] and tr["documents"]["entries"] ==
      ["Invoice 9116093.pdf", "Bill Entry 40726534505.pdf", "Packing List.pdf"], tr["documents"])
check("4. The document whose name starts with 'Bill Entry' — and only it",
      tr["bill_entry"]["selected"] == "Bill Entry 40726534505.pdf"
      and len(tr["bill_entry"]["candidates"]) == 1, tr["bill_entry"])
check("5. The identifier after 'Bill Entry': 40726534505", found["identifier"] == "40726534505")
check("6. Downloaded through the WebForms postback, with eHub's own sign-in, as served",
      found["data"] == GOOD and tr["download"]["method"] == "download"
      and tr["download"]["served_filename"] == "Bill Entry 40726534505.pdf", tr["download"])
check("7. The identifier is handed on with the record", found["hub"]["identifier"] == "40726534505"
      and found["hub"]["bill_entry"] == "Bill Entry 40726534505.pdf")
import urllib.error                                           # noqa: E402
import urllib.request                                         # noqa: E402
try:
    urllib.request.urlopen(HUB + "/ehub/manage/176-88452310", timeout=5)
    anon = 200
except urllib.error.HTTPError as error:
    anon = error.code
check("eHub pages without the sign-in are refused (401)", anon == 401, anon)


def stopped(ref, skip=()):
    try:
        hub_source(skip).fetch(ref)
        return None
    except P.SourceError as error:
        return error


for ref, status in (("176-30000001", "Cleared"), ("176-30000002", "Under Clearance - Hold")):
    before = HITS.get("/ehub/manage/" + ref, 0)
    e = stopped(ref)
    check("Status '{0}' → skipped, the reason recorded".format(status),
          e is not None and e.kind == "skipped" and status in str(e)
          and e.trail["clearance"]["ok"] is False, getattr(e, "kind", None))
    check("   ...and Manage was never opened", HITS.get("/ehub/manage/" + ref, 0) == before)
e = stopped("176-00000000")
check("A BOL/AWB eHub does not list as Under Clearance → skipped, not opened",
      e is not None and e.kind == "skipped" and "not listed" in str(e), str(e))
e = stopped("176-55500011")
check("No document starting with 'Bill Entry' → DOCUMENT_NOT_FOUND, no identifier invented",
      e is not None and e.kind == "no_bill_entry" and e.trail["bill_entry"]["identifier"] is None
      and "Invoice 9116094.pdf" in str(e), str(e))
e = stopped("176-77700022")
check("Two Bill Entry documents with different identifiers → review, none chosen",
      e is not None and e.kind == "review" and len(e.candidates) == 2
      and e.trail["bill_entry"]["selected"] is None and "a person decides" in str(e), str(e))
e = stopped("176-77700023")
check("Two entries with the same identifier but DIFFERENT files → review, none chosen "
      "(list order decides nothing)",
      e is not None and e.kind == "review" and "different files" in str(e), str(e))
r2 = hub_source().fetch("176-77700024")
check("Two entries with the same identifier, byte-identical → one document, by a recorded rule",
      r2["filename"] == "Bill Entry 40726534505.pdf" and r2["data"] == GOOD
      and "byte-identical" in r2["trail"]["bill_entry"]["rule"]
      and len(r2["trail"]["bill_entry"]["compared"]) == 2, r2["trail"]["bill_entry"])
e = stopped("176-20000001")
check("'Bill Entry' with no number after it → review, nothing guessed",
      e is not None and e.kind == "review" and "no identifier" in str(e), str(e))
e = stopped("176-10000001")
check("Manage opened but no Documents section → not found, nothing guessed",
      e is not None and e.kind == "not_found" and e.trail["manage"]
      and e.trail["documents"]["found"] is False, str(e))
e = stopped("176-44400044")
check("A Bill Entry link that is not a PDF → refused as unreadable (never extracted)",
      e is not None and e.kind == "unreadable")
row, looked = find_stub(PAGE, None, ())
check("Next Under Clearance record: the first listed one that is exactly Under Clearance",
      row["bol_awb"] == "176-88452310", row)
check("...the rows passed over are recorded with why",
      [(l["bol_awb"], l["reason"]) for l in looked] ==
      [("176-30000001", "status 'Cleared' is not exactly 'Under Clearance'"),
       ("176-30000002", "status 'Under Clearance - Hold' is not exactly 'Under Clearance'")],
      looked)
row, looked = find_stub(PAGE, None, ("176-88452310",))
check("...and one already handled is passed over too", row["bol_awb"] == "176-55500011"
      and looked[-1]["reason"] == "already processed")
for name, ident in (("Bill Entry 40726534505.pdf", "40726534505"),
                    ("Bill_Entry-40726534505.PDF", "40726534505"),
                    ("BILL ENTRY No. 40726534505", "40726534505"),
                    ("Bill Entry 40726534505 (1).pdf", "40726534505"),
                    ("BillofEntry_40926696852 (1) (1).pdf", "40926696852"),
                    ("Bill of Entry 40726534505.pdf", "40726534505"),
                    ("KIA1-G-40926696852-01 (1).pdf", None), ("20260910094658174 (1).pdf", None),
                    ("Bill Entry.pdf", None), ("Billing Entry 4.pdf", None),
                    ("Invoice 40726534505.pdf", None)):
    check("identifier_of({0!r}) = {1!r}".format(name, ident), EH.identifier_of(name) == ident,
          EH.identifier_of(name))
shot = hub_source().fetch("176-40926696")
st_tr = shot["trail"]
check("THE REAL LAYOUT (screenshot): Documents lists exactly the three files, not the controls",
      st_tr["documents"]["entries"] == ["BillofEntry_40926696852 (1) (1).pdf",
                                        "KIA1-G-40926696852-01 (1).pdf",
                                        "20260910094658174 (1).pdf"], st_tr["documents"])
check("...BillofEntry_40926696852 (1) (1).pdf is the Bill Entry document",
      st_tr["bill_entry"]["selected"] == "BillofEntry_40926696852 (1) (1).pdf"
      and [c["name"] for c in st_tr["bill_entry"]["candidates"]] ==
      ["BillofEntry_40926696852 (1) (1).pdf"], st_tr["bill_entry"])
check("...identifier 40926696852 (the KIA1-G file with the same number is not taken)",
      shot["identifier"] == "40926696852" and shot["data"] == FILES["real"])
check("...and Save, Correction Required, Complete and Upload were never pressed", PRESSED == [],
      PRESSED)
check("Status rule: exact (spacing normalised), nothing else",
      EH.status_ok("Under Clearance") and EH.status_ok(" Under  Clearance ")
      and not EH.status_ok("under clearance") and not EH.status_ok("Under Clearance - Hold")
      and not EH.status_ok("Cleared") and not EH.status_ok(None))


# ═════════════════════════════════════════════════════════════════════════
rule("1b. THE JOB — the trail on the record, the identifier passed on")
# ═════════════════════════════════════════════════════════════════════════
st1 = new_store("s1b")
j = run_job(st1, "176-88452310")
check("A full job: the discovery trail is on the record, step by step",
      [x["step"] for x in j["discovery"]["steps"]][-1] == "download"
      and j["discovery"]["bill_entry"]["selected"] == "Bill Entry 40726534505.pdf", j["state"])
check("The identifier 40726534505 becomes the job's number",
      j["identifier"] == "40726534505" and j["number"] == "40726534505")
idc = [c for c in j["validation"]["checks"] if c["name"] == "hub:identifier"][0]
check("...and the PDF's declaration (40726534505 / 00) is checked against it → MATCH",
      idc["status"] == "MATCH" and idc["hub"] == "40726534505" and idc["pdf"] == "40726534505 / 00",
      idc)
ev = [e["event"] for e in st1.events(j["po_id"]) if e["event"] not in ("STATE_CHANGED",
                                                                       "MILESTONE")]
check("Each step is an event: EHUB_RECORD_FOUND → CLEARANCE_CHECKED → MANAGE_OPENED → "
      "IDENTITY_CHECKED → "
      "DOCUMENTS_SECTION_FOUND → BILL_ENTRY_FOUND → IDENTIFIER_EXTRACTED → BILL_ENTRY_DOWNLOADED",
      ev[1:9] == ["EHUB_RECORD_FOUND", "CLEARANCE_CHECKED", "MANAGE_OPENED", "IDENTITY_CHECKED",
                  "DOCUMENTS_SECTION_FOUND", "BILL_ENTRY_FOUND", "IDENTIFIER_EXTRACTED",
                  "BILL_ENTRY_DOWNLOADED"], ev[:10])
check("The document record says where it came from, when, and its hash",
      j["document"]["source"] == "ehub" and j["document"]["retrieved_at"]
      and len(j["document"]["sha256"]) == 64 and j["document"]["method"] == "download")
rj = run_job(st1, "176-40926696")
rjc = [c for c in rj["validation"]["checks"] if c["name"] == "hub:identifier"][0]
check("A job on the real layout: identifier 40926696852 vs the PDF's 40926696852 / 01 → MATCH, "
      "EMAIL READY", rj["state"] == S.EMAIL_PREPARED and rjc["status"] == "MATCH"
      and rj["number"] == "40926696852", (rj["state"], rjc))
check("...still nothing pressed on the record", PRESSED == [])
sk = run_job(st1, "176-30000001")
check("A record not Under Clearance → SKIPPED, with its status and why",
      sk["state"] == S.SKIPPED and sk["failure"]["category"] == "NOT_UNDER_CLEARANCE"
      and sk["skip_reason"] == "SKIPPED_NOT_UNDER_CLEARANCE"
      and sk["failure"]["status"] == "Cleared" and sk["document"] is None, sk["state"])
check("...nothing downloaded, nothing extracted", sk["fields"] is None and sk["output"] is None)
rv = run_job(st1, "176-77700022")
check("Two Bill Entry identifiers → DOCUMENT_AMBIGUOUS, the candidates kept, nothing guessed",
      rv["state"] == S.DOCUMENT_AMBIGUOUS and len(rv["failure"]["candidates"]) == 2, rv["state"])
nb = run_job(st1, "176-55500011")
check("No Bill Entry document → PDF_NOT_FOUND / DOCUMENT_NOT_FOUND, no number invented",
      nb["state"] == S.PDF_NOT_FOUND and nb["failure"]["category"] == "DOCUMENT_NOT_FOUND"
      and nb["identifier"] is None and nb["number"] is None, (nb["state"], nb.get("failure")))
mm = run_job(st1, "176-88800099")
mmc = [c for c in mm["validation"]["checks"] if c["name"] == "hub:identifier"][0]
check("eHub says Bill Entry 40711111111, the PDF prints 40726534505 → IDENTITY_MISMATCH "
      "(the document is not the one eHub lists), nothing generated",
      mm["state"] == S.IDENTITY_MISMATCH and mmc["status"] == "MISMATCH"
      and mm["failure"]["code"] == "IDENTITY_MISMATCH" and not mm.get("output")
      and mmc["hub"] == "40711111111", (mm["state"], mmc))
svc1 = SV.PoService(store=st1, launcher=lambda r: None, config=CONFIG)
nx = svc1.start("omar.ops@mantrac.com", "", {"invoice_no": "9116093"})
check("'Next Under Clearance record' skips every record already handled",
      set(nx["request"]["skip_references"]) >= {"176-88452310", "176-30000001", "176-77700022",
                                                 "176-55500011", "176-88800099"},
      nx["request"]["skip_references"])
nx = P.process(st1, st1.get(nx["po_id"]),
               hub_source(nx["request"]["skip_references"]), CONFIG, sleep=NOSLEEP)
check("...takes the next one eHub lists as Under Clearance and adopts its BOL/AWB",
      nx["reference"] == "176-77700023" and nx["po_key"].endswith("17677700023")
      and nx["discovery"]["looked_at"][0]["reason"].startswith("status 'Cleared'"), nx["reference"])
probe_out = WORK / "probe"
from po import __main__ as CLI                                 # noqa: E402
report = CLI.probe(PAGE, "176-88452310", probe_out, source=hub_source())
check("The probe (steps 1–7, read-only) reports filename, identifier, size, SHA-256, fields "
      "and the next stage's input",
      report["result"] == "FOUND" and report["document"]["filename"] == "Bill Entry 40726534505.pdf"
      and report["identifier"] == "40726534505" and report["document"]["bytes"] == len(GOOD)
      and report["document"]["sha256"] == hashlib.sha256(GOOD).hexdigest()
      and report["pdf"]["fields"]["document_number"]["value"] == "40726534505 / 00"
      and report["next_stage_input"] == {"reference": "176-88452310", "identifier": "40726534505",
                                         "document_sha256": hashlib.sha256(GOOD).hexdigest()}
      and report["writes_to_ehub"] is False and report["sends_email"] is False,
      {k: report.get(k) for k in ("result", "identifier")})
check("...and writes it to a report file, with the PDF beside it",
      Path(report["report_file"]).is_file() and Path(report["document"]["saved_as"]).read_bytes() == GOOD)
bad_report = CLI.probe(PAGE, "176-30000001", probe_out, source=hub_source())
check("The probe on a record that is not Under Clearance says so, and stops",
      bad_report["result"] == "STOPPED" and bad_report["kind"] == "skipped")


# ═════════════════════════════════════════════════════════════════════════
rule("2. PDF PARSING — the text layer, OCR for a scan, and what cannot be read")
# ═════════════════════════════════════════════════════════════════════════
read = X.read_pdf(GOOD)
check("A PDF with a text layer is read from it", read["methods"] == ["text"] and
      "Declaration No" in read["text"], read["methods"])
scan = pdf_of(boe_text(), scanned=True)
check("The scan has no text layer at all", fitz.open(stream=scan, filetype="pdf")[0].get_text().strip() == "")
def unreadable(data):
    try:
        X.read_pdf(data)
        return None
    except X.Unreadable as error:
        return str(error)


if X._tesseract():
    rs = X.read_pdf(scan)
    fs = X.extract(rs["text"], doctypes.get())
    check("A scanned page is read with OCR", rs["methods"] == ["ocr"], rs["methods"])
    check("...and the key fields come out of the OCR text",
          fs["bl_awb"]["value"] == "176-88452310" and fs["duty_amount_ghs"]["value"] == 653492.35,
          {k: fs[k]["value"] for k in ("bl_awb", "duty_amount_ghs")})
else:
    check("OCR is not installed here: a scan is reported unreadable, not guessed",
          "OCR is not installed" in (unreadable(scan) or ""))

empty_doc = fitz.open()
empty_doc.new_page()
check("Not a PDF → unreadable, said plainly", "not a PDF" in (unreadable(b"<html>x</html>") or ""))
check("A password-protected PDF → unreadable", "password" in (unreadable(pdf_of(boe_text(), password="x1")) or ""))
blank = unreadable(empty_doc.tobytes())
check("A PDF with no text on any page → unreadable (with or without OCR)", blank is not None, blank)


# ═════════════════════════════════════════════════════════════════════════
rule("3. EXTRACTION — typed fields; missing and ambiguous are never filled in")
# ═════════════════════════════════════════════════════════════════════════
doctype = doctypes.get()
f = X.extract(read["text"], doctype)
# Every value a complete template needs (template tests fill whole documents:
# a required cell is never left blank).
FULL_VALUES = {n: v["value"] for n, v in f.items() if v["status"] == X.FOUND}
FULL_VALUES.update(invoice_no="9116093", supplier="CAT")
expect = {"document_number": "40726534505 / 00", "bl_awb": "176-88452310",
          "document_date": "2026-07-16", "cif_usd": 169740.11, "exchange_rate": 11.2,
          "duty_amount_ghs": 653492.35, "stated_import_duty": 246320.05}
for name, value in expect.items():
    check("{0} = {1!r}".format(name, value), f[name]["status"] == X.FOUND and f[name]["value"] == value,
          f[name])
check("Five VAT / levy lines, each with its own line as evidence",
      len(f["vat_lines"]["value"]) == 5 and all(l["line"] for l in f["vat_lines"]["value"]))
check("Each value keeps the line it was read from", "BL/AWB No: 176-88452310" in f["bl_awb"]["evidence"])
nodate = X.extract(boe_text(date="sometime soon").replace("Date of Assessment: sometime soon", "Date of Assessment: 31/13/2026"), doctype)
check("An unreadable date is MISSING — never today's date",
      nodate["document_date"]["status"] == X.MISSING and nodate["document_date"]["value"] is None
      and "could not be read" in nodate["document_date"].get("note", ""), nodate["document_date"])
amb = X.extract(boe_text(extra="Total Amount Payable GHS 700,000.00\n"), doctype)
check("Two different totals → AMBIGUOUS, both shown, none chosen",
      amb["duty_amount_ghs"]["status"] == X.AMBIGUOUS and amb["duty_amount_ghs"]["value"] is None
      and len(amb["duty_amount_ghs"]["candidates"]) == 2, amb["duty_amount_ghs"])
check("Not a declaration at all is recognised as such",
      not X.looks_like(X.extract("Dear customer, thank you for your order.", doctype)))


# ═════════════════════════════════════════════════════════════════════════
rule("4. VALIDATION SUCCESS")
# ═════════════════════════════════════════════════════════════════════════
req = P._request_fields(doctype, {"invoice_no": "9116093"}, CONFIG)
req["invoice_no"], _issue = P._g4_source(doctype, {"invoice_no": "9116093"},
                                         X.printed_invoice_no(read["text"]))
HUBREC = {"bol_awb": "176-88452310", "identifier": "40726534505"}   # what eHub discovery hands on
ok = V.validate(doctype, f, HUBREC, req)
check("Every check passes, so the gate opens", ok["passed"] and not ok["reasons"], ok["reasons"])
hubrow = [c for c in ok["checks"] if c["name"] == "hub:bl_awb"][0]
check("BL/AWB: PDF 176-88452310 vs Hub 176-88452310 → MATCH",
      hubrow["pdf"] == "176-88452310" and hubrow["hub"] == "176-88452310" and hubrow["status"] == "MATCH")
check("The duty adds up: 653,492.35 − 407,172.30 = 246,320.05 (the BOE's line)",
      [c for c in ok["checks"] if c["name"] == "arithmetic:duty"][0]["status"] == "OK")
check("The supplier comes from configuration and says so",
      req["supplier"]["value"] == "CAT" and req["supplier"]["origin"] == "configuration")


# ═════════════════════════════════════════════════════════════════════════
rule("5. VALIDATION MISMATCH — the gate stops everything")
# ═════════════════════════════════════════════════════════════════════════
bad = V.validate(doctype, X.extract(X.read_pdf(OTHER_SHIPMENT)["text"], doctype), HUBREC, req)
row = [c for c in bad["checks"] if c["name"] == "hub:bl_awb"][0]
check("PDF 176-99001122 vs Hub 176-88452310 → MISMATCH, blocking",
      not bad["passed"] and row["status"] == "MISMATCH" and row["blocking"]
      and row["pdf"] == "176-99001122" and row["hub"] == "176-88452310", row)
check("The reason names both values and chooses neither",
      bad["reasons"] == ["BL / AWB mismatch — PDF: 176-99001122, Hub: 176-88452310"], bad["reasons"])
noinv = V.validate(doctype, f, HUBREC, P._request_fields(doctype, {}, CONFIG))
check("No supplier invoice number → MISSING, blocking (it is not on the BOE)",
      not noinv["passed"] and any(c["status"] == "MISSING" and c["label"] == "Supplier invoice No."
                                  for c in noinv["checks"]))
off = V.validate(doctype, X.extract(boe_text(duty="653,670.54"), doctype), HUBREC, req)
check("Duty that does not add up (variance 178.19) → FAILED, blocking",
      not off["passed"] and any(c["name"] == "arithmetic:duty" and c["status"] == "FAILED" for c in off["checks"]))
store = new_store("s5")
rec = run_job(store, "176-99001122")
check("A whole job on a document whose BL/AWB is another shipment's stops at "
      "IDENTITY_MISMATCH (the precise validation failure)",
      rec["state"] == S.IDENTITY_MISMATCH and rec["identity"]["decision"] == "MISMATCH",
      rec["state"])
check("...nothing generated, nothing prepared, email BLOCKED with the reason",
      rec["output"] is None and rec["email"]["status"] == "BLOCKED"
      and "BL / AWB mismatch" in rec["email"]["reasons"][0], rec.get("email"))
check("...and the output folder is empty", not store.output_dir.exists() or not any(store.output_dir.iterdir()))
check("...events: VALIDATION_FAILED and EMAIL_BLOCKED, no TEMPLATE_* event",
      [e["event"] for e in store.events(rec["po_id"])][-2:] == ["VALIDATION_FAILED", "EMAIL_BLOCKED"]
      and not any(e["event"].startswith("TEMPLATE") for e in store.events(rec["po_id"])))
_, outcome = P.send(store, rec, M.GraphMailer(), by="omar")
check("Sending it anyway is refused before any Graph call", outcome == "BLOCKED" and GRAPH["counter"] == 0)


# ═════════════════════════════════════════════════════════════════════════
rule("6. TEMPLATE GENERATION — only input cells, formulas kept, read back")
# ═════════════════════════════════════════════════════════════════════════
store = new_store("s6")
rec = run_job(store, "176-88452310")
check("A valid document reaches EMAIL_PREPARED", rec["state"] == S.EMAIL_PREPARED,
      (rec["state"], rec.get("failure")))
out = rec["output"]
wb = load_workbook(out["path"])
ws = wb["Duty Template"]
check("Mapped cells hold the validated values",
      ws["C13"].value == "40726534505 / 00" and ws["G6"].value == 653492.35
      and ws["C19"].value == 169740.11 and ws["C21"].value == 11.2 and ws["G4"].value == 9116093
      and ws["G10"].value == "CAT" and ws["G8"].value.date().isoformat() == "2026-07-16",
      [ws[c].value for c in ("C13", "G6", "C19", "C21", "G4", "G10", "G8")])
check("G20 is written in the template's own style",
      ws["G20"].value == "=304446.44+1066.03+50741.08+50741.08+177.67", ws["G20"].value)
check("Every template formula is still there",
      all(ws[c].value == fm for c, fm in doctype["template"]["formulas"].items()))
check("Cells with no value stay empty — nothing invented (branch, charge-to)",
      ws["G11"].value is None and ws["G13"].value is None)
check("The logo and the layout survive (image, merged cells, both sheets)",
      len(ws._images) == 1 and len(ws.merged_cells.ranges) == 6
      and wb.sheetnames == ["Duty Template", "BOE Template Capture"])
check("'Generated' means read back: verified, with the written cells",
      out["verified"] is True and out["cells"]["C13"] == "40726534505 / 00")


# ═════════════════════════════════════════════════════════════════════════
rule("7. TEMPLATE VERSIONING")
# ═════════════════════════════════════════════════════════════════════════
man = T.manifest(doctype)
check("The output records template DUTY_REQUEST_V1 and its SHA-256",
      out["template_version"] == "DUTY_REQUEST_V1" and out["template_sha256"] == man["sha256"])
check("The manifest records where the template came from (the original's SHA-256)",
      len(man["source_sha256"]) == 64 and man["source_file"].endswith(".xlsx"))
tpl = doctypes.template_path(doctype)
backup = tpl.read_bytes()
try:
    tpl.write_bytes(backup + b"tampered")
    try:
        T.fill(doctype, dict(FULL_VALUES, document_number="1"), WORK / "tamper", "1", "x")
        refused = False
    except T.TemplateError as error:
        refused = "does not match its manifest" in str(error)
    check("A template changed outside the build step is refused, not used", refused)
finally:
    tpl.write_bytes(backup)


# ═════════════════════════════════════════════════════════════════════════
rule("8. OUTPUT STORAGE — deterministic name, never overwritten")
# ═════════════════════════════════════════════════════════════════════════
check("The name is deterministic: type, Bill Entry identifier, BOL/AWB, timestamp, job",
      re.match(r"^DUTY_REQUEST_40726534505_176-88452310_\d{8}-\d{6}_[0-9a-f]{6}\.xlsx$",
               out["filename"]) and out["filename"].endswith(rec["po_id"][-6:] + ".xlsx"),
      out["filename"])
check("It is in the configured output folder", Path(out["path"]).parent == store.output_dir)
fixed = T.fill(doctype, dict(FULL_VALUES, document_number="1 / 00"), WORK / "ow", "1 / 00", "R1",
               stamp="20260101-000000")
first_bytes = Path(fixed["path"]).read_bytes()
again = T.fill(doctype, dict(FULL_VALUES, document_number="1 / 00"), WORK / "ow", "1 / 00", "R1",
               stamp="20260101-000000")
check("An existing output is never overwritten: the same name again takes the next free "
      "name (-2), the first file is untouched",
      again["path"] != fixed["path"] and again["filename"].endswith("-2.xlsx")
      and Path(fixed["path"]).read_bytes() == first_bytes, (fixed["filename"], again["filename"]))
check("Recorded: filename, path, template, PO number, run ID, timestamp, generated by",
      all(out.get(k) for k in ("filename", "path", "template_version", "generated_at", "generated_by"))
      and rec["number"] and rec["run_id"])


# ═════════════════════════════════════════════════════════════════════════
rule("9. EMAIL PREPARATION")
# ═════════════════════════════════════════════════════════════════════════
email = rec["email"]
check("Prepared for the ONE configured recipient, from the ATA mailbox",
      email["recipient"] == "accounts.ghana@mantrac.com" and email["sender"] == "ata@mantrac.com")
check("Subject and attachment are this document's",
      email["subject"] == "Duty Payment Request — 40726534505 — 176-88452310"
      and email["attachment"] == out["filename"], email)
check("READY — not sent: nothing goes out until someone presses Send PO",
      email["status"] == "READY" and GRAPH["counter"] == 0)
nocfg = dict(CONFIG, recipient=None)
st_nr = new_store("s10nr")
r2 = P.process(st_nr, st_nr.create(doctypes.DEFAULT, "176-88452310", {}),
               hub_source(), nocfg, sleep=NOSLEEP)
check("With no recipient configured, the email is BLOCKED with that reason (the output is SAVED)",
      r2["state"] == S.SAVED and r2["email"]["status"] == "BLOCKED"
      and "No recipient" in r2["email"]["reasons"][0])


# ═════════════════════════════════════════════════════════════════════════
rule("10. GRAPH SEND SUCCESS → CONFIRMED IN SENT ITEMS")
# ═════════════════════════════════════════════════════════════════════════
rec, outcome = P.send(store, rec, M.GraphMailer(), by="omar.ops@mantrac.com", confirm_wait_s=3,
                      sleep=NOSLEEP)
sent = GRAPH["sent"][-1]
check("Microsoft Graph accepted it and it is in Sent Items → CONFIRMED",
      outcome == "CONFIRMED" and rec["state"] == S.EMAIL_CONFIRMED, (outcome, rec["state"]))
check("Exactly one email, to the recipient, with the generated file attached byte for byte",
      GRAPH["sends"] == 1 and sent["to"] == "accounts.ghana@mantrac.com"
      and hashlib.sha256(sent["attachment_bytes"]).hexdigest() == out["sha256"])
check("Sent from the ATA mailbox", sent["mailbox"] == "ata%40mantrac.com" or sent["mailbox"] == "ata@mantrac.com",
      sent["mailbox"])
check("The record keeps the Graph evidence: internetMessageId, confirmation time",
      rec["email"]["internet_message_id"] == sent["internetMessageId"]
      and rec["email"]["status"] == "CONFIRMED" and rec["email"]["confirmed_at"])
check("Events: EMAIL_SEND_STARTED → EMAIL_SENT → EMAIL_VERIFIED → EMAIL_CONFIRMED → "
      "PO_COMPLETED (state changes and milestones recorded alongside)",
      [e["event"] for e in store.events(rec["po_id"])
       if e["event"] not in ("STATE_CHANGED", "MILESTONE")][-5:] ==
      ["EMAIL_SEND_STARTED", "EMAIL_SENT", "EMAIL_VERIFIED", "EMAIL_CONFIRMED", "PO_COMPLETED"])
GRAPH["hide_sent"] = True
st2 = new_store("s10b")
r3 = run_job(st2, "176-88452310")
r3, out3 = P.send(st2, r3, M.GraphMailer(), by="omar", confirm_wait_s=1, sleep=NOSLEEP)
check("Accepted but not (yet) in Sent Items → SENT, never claimed CONFIRMED",
      out3 == "SENT" and r3["state"] == S.EMAIL_SENT
      and "not yet found in Sent Items" in r3["email"]["confirmation"], (out3, r3["state"]))
GRAPH["hide_sent"] = False
svc3 = SV.PoService(store=st2, launcher=lambda r: None, mailer_factory=M.GraphMailer, config=CONFIG)
r3 = svc3.reconfirm(r3["po_id"])
check("...Check delivery finds it later → CONFIRMED", r3["state"] == S.EMAIL_CONFIRMED)


# ═════════════════════════════════════════════════════════════════════════
rule("11. GRAPH SEND FAILURE — no success claimed")
# ═════════════════════════════════════════════════════════════════════════
st = new_store("s11")
r = run_job(st, "176-88452310")
GRAPH["plan"]["create"] = [403]
r, o = P.send(st, r, M.GraphMailer(), by="omar", sleep=NOSLEEP)
check("Graph refuses (403) → EMAIL_FAILED, permanent, nothing sent",
      o == "FAILED" and r["state"] == S.EMAIL_FAILED and r["failure"]["kind"] == "permanent"
      and r["email"]["status"] == "FAILED", (o, r["state"], r.get("failure")))
check("...no SENT or CONFIRMED event exists for it",
      not any(e["event"] in ("EMAIL_SENT", "EMAIL_CONFIRMED") for e in st.events(r["po_id"])))
check("...and sending again is refused while the failure is permanent",
      any("failed permanently" in x for x in P.blocked_reasons(st, r)), P.blocked_reasons(st, r))
saved_secret = os.environ["GRAPH_CLIENT_SECRET"]
os.environ["GRAPH_CLIENT_SECRET"] = "wrong"
st11 = new_store("s11b")
r = run_job(st11, "176-88452310")
r, o = P.send(st11, r, M.GraphMailer(), by="omar", sleep=NOSLEEP)
os.environ["GRAPH_CLIENT_SECRET"] = saved_secret
check("A rejected sign-in to Microsoft identity → EMAIL_FAILED at the token step",
      o == "FAILED" and r["failure"]["step"] == "token", r.get("failure"))
for path in Path(WORK).rglob("*"):
    if path.is_file() and path.suffix in (".json", ".jsonl"):
        if SECRET in path.read_text(encoding="utf-8", errors="ignore"):
            check("The Graph client secret is in " + path.name, False)
            break
else:
    check("The Graph client secret is in no record, event or ledger", True)


# Microsoft 365 not configured: blocked, the job left ready — not failed.
saved = {k: os.environ.pop(k) for k in ("GRAPH_CLIENT_SECRET", "PO_MAIL_SENDER")}
st11c = new_store("s11c")
nc = run_job(st11c, "176-88452310")
svc11 = SV.PoService(store=st11c, launcher=lambda r: None, config=CONFIG)
before = GRAPH["counter"]
nc2, outcome, why = svc11.send("omar.ops@mantrac.com", nc["po_id"])
os.environ.update(saved)
check("Email not configured: Send PO is BLOCKED, naming what is missing",
      outcome == "BLOCKED" and "GRAPH_CLIENT_SECRET" in why[0] and "PO_MAIL_SENDER" in why[0], why)
check("...no Graph call, and the job stays EMAIL READY (not failed)",
      GRAPH["counter"] == before and st11c.get(nc["po_id"])["state"] == S.EMAIL_PREPARED)
nc3, outcome, why = svc11.send("omar.ops@mantrac.com", nc["po_id"], wait=True)
check("...once configured, the same job sends and is confirmed",
      outcome == "STARTED" and st11c.get(nc["po_id"])["state"] == S.EMAIL_CONFIRMED,
      st11c.get(nc["po_id"])["state"])


# ═════════════════════════════════════════════════════════════════════════
rule("12. DUPLICATE-SEND PREVENTION")
# ═════════════════════════════════════════════════════════════════════════
st = new_store("s12")
first = run_job(st, "176-88452310")
first, o1 = P.send(st, first, M.GraphMailer(), by="omar", confirm_wait_s=3, sleep=NOSLEEP)
sends_before = GRAPH["sends"]
dup = run_job(st, "176-88452310")
check("The same Bill of Entry processed again → SKIPPED_DUPLICATE before anything is generated",
      dup["state"] == S.SKIPPED and dup["skip_reason"] == "SKIPPED_DUPLICATE"
      and dup["duplicate_of"] == first["po_id"] and not dup.get("output"),
      (dup["state"], dup.get("skip_reason")))
# The ledger is the second, independent guard: a job that reached EMAIL READY
# by any other route (here: a copy of the first, as a job from before the
# idempotency claim existed would be) still cannot send the same document.
import copy as _copy                                            # noqa: E402
again = _copy.deepcopy(first)
again.update(po_id=S.new_po_id(), state=S.EMAIL_PREPARED, label=S.LABELS[S.EMAIL_PREPARED])
again["email"] = {k: v for k, v in first["email"].items() if k in (
    "recipient", "sender", "subject", "attachment", "template_version")}
again["email"]["status"] = "READY"
st.save(again)
again, o2 = P.send(st, again, M.GraphMailer(), by="omar", sleep=NOSLEEP)
check("A copy of a sent job cannot send it again — its attachment is another job's document",
      o1 == "CONFIRMED" and o2 == "BLOCKED" and GRAPH["sends"] == sends_before and
      any("generated by another job" in r for r in st.events(again["po_id"])[-1]["metadata"]
          ["reasons"]), (o1, o2, GRAPH["sends"] - sends_before))
key = st.ledger_key(first["po_key"], first["document"]["sha256"], "DUTY_REQUEST_V1",
                    "accounts.ghana@mantrac.com")
check("The ledger holds po + document hash + template + recipient → CONFIRMED",
      st.sent_before(key)["status"] == "CONFIRMED")
ok_other, holder = st.reserve(key, again["po_id"], "omar")
check("The ledger alone refuses a second sender for the same document + recipient",
      ok_other is False and holder["po_id"] == first["po_id"], (ok_other, holder))
blocked = P.send(st, st.get(first["po_id"]), M.GraphMailer(), by="omar", sleep=NOSLEEP)[1]
check("...and the job itself, once CONFIRMED, does not send again without authorization",
      blocked == "BLOCKED" and GRAPH["sends"] == sends_before)
again2, o3 = P.send(st, st.get(first["po_id"]), M.GraphMailer(), by="ada.admin",
                    authorize_resend=True, reason="Accounts lost the first one", confirm_wait_s=3,
                    sleep=NOSLEEP)
check("An explicitly authorized resend goes, once, with who and why on the ledger",
      o3 == "CONFIRMED" and GRAPH["sends"] == sends_before + 1
      and any(h.get("by") == "ada.admin" for h in st.sent_before(key)["history"])
      and len(again2["email"].get("previous_sends") or []) == 1, o3)
GRAPH["plan"]["send"] = ["drop"]
st12 = new_store("s12b")
d1 = run_job(st12, "176-88452310")
before = GRAPH["sends"]
d1, o4 = P.send(st12, d1, M.GraphMailer(), by="omar", confirm_wait_s=3, sleep=NOSLEEP)
check("Connection dropped after Graph sent it: found in Sent Items, NOT sent twice",
      o4 == "CONFIRMED" and GRAPH["sends"] == before + 1, (o4, GRAPH["sends"] - before))


# ═════════════════════════════════════════════════════════════════════════
rule("13. RETRY BEHAVIOUR — stage-aware")
# ═════════════════════════════════════════════════════════════════════════
st = new_store("s13")
r = run_job(st, "176-66600033")
retries = [e for e in st.events(r["po_id"]) if e["event"] == "RETRY"]
check("A temporary Hub failure (503) is retried and the job goes on",
      r["state"] == S.EMAIL_PREPARED and len(retries) == 2 and r["attempts"]["pdf_retrieval"] == 2,
      (r["state"], len(retries)))


class Counting(object):
    def __init__(self, inner):
        self.inner, self.calls = inner, 0

    def fetch(self, ref):
        self.calls += 1
        return self.inner.fetch(ref)


counting = Counting(hub_source())
r = run_job(st, "176-99001122", source=counting)
check("A validation mismatch is NOT retried: one fetch, no RETRY event",
      r["state"] == S.IDENTITY_MISMATCH and counting.calls == 1
      and not any(e["event"] == "RETRY" for e in st.events(r["po_id"])))
check("The policy says so: validation and template are never retried",
      P.RETRY_POLICY["validation"][0] == 1 and P.RETRY_POLICY["template"][0] == 1
      and P.RETRY_POLICY["pdf_retrieval"][2] == "transient")
st13 = new_store("s13b")
r = run_job(st13, "176-88452310")
GRAPH["plan"]["send"] = [503]
before = GRAPH["sends"]
r, o = P.send(st13, r, M.GraphMailer(), by="omar", confirm_wait_s=3, sleep=NOSLEEP)
check("A temporary Graph failure (503) on send: checked, retried on the SAME draft, sent once",
      o == "CONFIRMED" and GRAPH["sends"] == before + 1, (o, GRAPH["sends"] - before))


# ═════════════════════════════════════════════════════════════════════════
rule("14. STATE MACHINE")
# ═════════════════════════════════════════════════════════════════════════
st = new_store("s14")
r = st.create(doctypes.DEFAULT, "X1", {})
for frm, to in ((S.QUEUED, S.TEMPLATE_GENERATED), (S.QUEUED, S.EMAIL_SENT)):
    try:
        st.transition(dict(r, state=frm), to)
        allowed = True
    except S.IllegalTransition:
        allowed = False
    check("{0} → {1} is refused".format(frm, to), not allowed)
reach = {S.VALIDATED}
frontier = [S.QUEUED]
seen = set()
while frontier:
    s0 = frontier.pop()
    if s0 in seen or s0 == S.VALIDATED:
        continue
    seen.add(s0)
    frontier += list(S.TRANSITIONS.get(s0, ()))
check("No path reaches TEMPLATE_GENERATED or any email state without VALIDATED",
      not ({S.TEMPLATE_GENERATED, S.EMAIL_PREPARED, S.EMAIL_SENT, S.EMAIL_CONFIRMED} & seen), seen)
check("Every failure state the brief names exists — precise, no generic FAILED",
      set(S.FAILED_STATES) == {"PDF_NOT_FOUND", "PDF_UNREADABLE", "EXTRACTION_FAILED",
                               "VALIDATION_FAILED", "TEMPLATE_FAILED", "EMAIL_FAILED",
                               "DISCOVERY_FAILED", "AUTH_REQUIRED", "MANAGE_NAVIGATION_FAILED",
                               "DOCUMENT_AMBIGUOUS", "PDF_DOWNLOAD_FAILED", "SAVE_FAILED",
                               "IDENTITY_MISMATCH", "EMAIL_RECONCILIATION_FAILED"}
      and "FAILED" not in S.TRANSITIONS)
ev = new_store("s6").events()[0]
check("Each event carries event_id, run_id, po_id, timestamp, stage, status, source, "
      "evidence_reference, metadata",
      all(k in ev for k in ("event_id", "run_id", "po_id", "timestamp", "stage", "status",
                            "source", "evidence_reference", "metadata")), sorted(ev))


# ═════════════════════════════════════════════════════════════════════════
rule("15–16. RBAC AND AUDIT — the control plane, over HTTP")
# ═════════════════════════════════════════════════════════════════════════
from controlplane.config import Settings                     # noqa: E402
from controlplane.app import App, make_server                 # noqa: E402
from fixtures.platform_client import Client                   # noqa: E402
from worker.agent import Agent, ControlPlane                  # noqa: E402

CP_PO = WORK / "cp_po"
app = App(Settings({"DATABASE_URL": "sqlite:///{0}/cp.db".format(WORK),
                    "ATA_INSECURE_COOKIES": "1", "PO_DATA_DIR": str(CP_PO)}))
httpd = make_server(app, "127.0.0.1", 0)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:{0}".format(httpd.server_address[1])
PWD = {"ADMIN": "Tower-Key-2026!", "OPERATOR": "Hub-Write-2026!", "VIEWER": "Read-Only-2026!"}
users = {}
for role, email in (("ADMIN", "ada.admin@mantrac.com"), ("OPERATOR", "omar.ops@mantrac.com"),
                    ("VIEWER", "vera.view@mantrac.com")):
    users[role] = app.users.create(email, role.title(), role, password=PWD[role])[0]


def client(role):
    c = Client(BASE)
    assert c.login(users[role]["work_email"], PWD[role])[0] == 200
    return c


op, viewer, admin = client("OPERATOR"), client("VIEWER"), client("ADMIN")
s_, d_, _ = viewer.get("/api/po")
check("A viewer sees the PO queue", s_ == 200 and "kpis" in d_, s_)
s_, d_, _ = viewer.post("/api/po", {"reference": "176-88452310", "invoice_no": "9116093"})
check("A viewer cannot start a job (403, server-side)", s_ == 403, s_)
denied = app.audit.list(action="ACCESS_DENIED")
check("...and the refusal is audited with the permission", any(
    (r["metadata"] or {}).get("route") == "/api/po" and r["target_id"] == "po.process" for r in denied))
s_, d_, _ = op.post("/api/po", {"reference": "176-88452310", "invoice_no": "9116093"})
check("No worker online: the job ends visibly, not stuck",
      s_ == 200 and app.po.store.get(d_["po_id"])["state"] == S.PDF_NOT_FOUND
      and app.po.store.get(d_["po_id"])["failure"]["category"] == "WORKER_UNAVAILABLE",
      app.po.store.get(d_["po_id"])["state"])
worker_id, token = app.orch.add_worker("Hub PC", users["ADMIN"])
cp = ControlPlane(BASE, token)
cp.request("POST", "/worker/v1/heartbeat", {"state": "IDLE"})
s_, d_, _ = op.post("/api/po", {"reference": "176-88452310", "invoice_no": "9116093"})
po_id = d_["po_id"]
check("An operator starts a job; it is queued for the worker", s_ == 200 and d_["accepted"]
      and app.po.store.get(po_id)["worker_id"] == worker_id, d_)
status, cmds, _ = cp.request("GET", "/worker/v1/commands?wait=1")
cmd = [c for c in cmds["commands"] if c["kind"] == "po_process"]
check("The worker receives a po_process command with the job", cmd and cmd[0]["payload"]["po_id"] == po_id)


class DummySup(object):
    process = None

    def is_running(self):
        return False


agent = Agent(cp, DummySup(), log=lambda *a: None, heartbeat_s=60, intel=False,
              runtime=WORK / "agent_rt")
wstore = S.Store(folder=WORK / "worker_po")
wrec = dict(cmd[0]["payload"]["record"], state=S.QUEUED)
wstore.save(wrec)
P.process(wstore, wrec, hub_source(), dict(CONFIG, auto_send=False), sleep=NOSLEEP)
status = agent._po_push(wstore, po_id, files=True)
cp_rec = app.po.store.get(po_id)
check("The worker's push (record, events, PDF, document) is accepted",
      status == 200 and cp_rec["state"] == S.EMAIL_PREPARED, (status, cp_rec["state"]))
check("...the generated document arrived and matches its SHA-256",
      Path(cp_rec["output"]["path"]).is_file() and Path(cp_rec["output"]["path"]).parent == CP_PO / "output"
      and hashlib.sha256(Path(cp_rec["output"]["path"]).read_bytes()).hexdigest() == cp_rec["output"]["sha256"])
forged = dict(app.po.store.get(po_id), state=S.EMAIL_CONFIRMED)
s_, d_, _ = cp.request("POST", "/worker/v1/po/" + po_id, {"record": forged, "events": []})
check("A worker cannot report a send — sending is the control plane's alone", s_ == 409, (s_, d_))
other_id, other_token = app.orch.add_worker("Other PC", users["ADMIN"])
s_, d_, _ = ControlPlane(BASE, other_token).request("POST", "/worker/v1/po/" + po_id,
                                                    {"record": wrec, "events": []})
check("Another worker cannot write to this job", s_ == 409)
s_, d_, _ = viewer.post("/api/po/{0}/send".format(po_id), {})
check("A viewer cannot send (403)", s_ == 403)
s_, d_, _ = op.post("/api/po/{0}/send".format(po_id), {})
check("An operator can send", s_ == 200 and d_["accepted"], (s_, d_))
app.po.wait_idle(30)
cp_rec = app.po.store.get(po_id)
check("...and it is confirmed in Sent Items", cp_rec["state"] == S.EMAIL_CONFIRMED, cp_rec["state"])
s_, d_, _ = op.post("/api/po/{0}/send".format(po_id), {"authorize_resend": True, "reason": "x"})
check("An operator cannot authorize a resend (po.resend is the admin's)", s_ == 403, s_)
s_, raw, hdr = op.call("GET", "/api/po/{0}/output".format(po_id))
check("The generated document can be downloaded, and that is audited",
      s_ == 200 and hashlib.sha256(raw if isinstance(raw, bytes) else b"").hexdigest() ==
      cp_rec["output"]["sha256"] and app.audit.list(action="PO_OUTPUT_DOWNLOADED"), s_)
audit = app.audit.list(limit=200)
acts = {r["action"] for r in audit}
check("Audit: PO_PROCESS_STARTED, PO_PROCESSED, PO_EMAIL_CONFIRMED",
      {"PO_PROCESS_STARTED", "PO_PROCESSED", "PO_EMAIL_CONFIRMED"} <= acts, acts)
started = [r for r in audit if r["action"] == "PO_PROCESS_STARTED" and r["target_id"] == po_id][0]
check("...who started it", started["user_email"] == "omar.ops@mantrac.com")
proc = [r for r in audit if r["action"] == "PO_PROCESSED" and r["target_id"] == po_id][0]["metadata"]
check("...the document, the validation result, the template",
      proc["document"] == "Bill Entry 40726534505.pdf" and proc["validation"] == "PASSED"
      and proc["template"] == "DUTY_REQUEST_V1", proc)
conf = [r for r in audit if r["action"] == "PO_EMAIL_CONFIRMED" and r["target_id"] == po_id][0]
check("...the recipient, the email result, the run ID, by whom",
      conf["metadata"]["recipient"] == "accounts.ghana@mantrac.com" and conf["result"] == "SUCCESS"
      and conf["run_id"] and conf["user_email"] == "omar.ops@mantrac.com", conf)
blob = json.dumps(audit)
check("No secret, token or password in any audit row",
      SECRET not in blob and HUB_PASS not in blob and "tok-" not in blob)


# ═════════════════════════════════════════════════════════════════════════
rule("17. ATLAS — PO questions answered from the jobs' own records")
# ═════════════════════════════════════════════════════════════════════════
from dashboard import assistant                                # noqa: E402
SV.register(SV.PoService(store=new_store("atlas"), launcher=lambda r: None,
                         mailer_factory=M.GraphMailer, config=CONFIG))
ast = SV.current().store
mis = run_job(ast, "176-99001122")
# A Bill of Entry that prints no Invoice No. (G4 has no source).
FILES["noinv"] = pdf_of(boe_text(bl="176-60600066", number="40726534777 / 00", invoice=None))
ehub_row("176-60600066", docs=[("Bill Entry 40726534777.pdf", "noinv", "postback")])
noinv = run_job(ast, "176-60600066", {"branch": "ACCRA"})      # its BOE prints no invoice No.
FALLBACK = "I don't have that information"


def po_ask(q, po=None, domain="po"):
    return assistant.answer(q, {}, {"domain": domain, "po_id": po or ""})


missing_answer = po_ask("What's missing from this PO?", noinv["po_id"])
check("No invoice No. printed on the Bill of Entry: the job waits in NEEDS_REVIEW, nothing "
      "generated",
      noinv["state"] == S.NEEDS_REVIEW and noinv["failure"]["code"] == "G4_SOURCE_UNPROVEN"
      and not noinv.get("output"), noinv["state"])
done = run_job(ast, "176-55500011")                    # no document attached
# A typed G4 is refused: G4 comes only from the Bill of Entry.
still, _p = P.supply(ast, ast.get(noinv["po_id"]), {"invoice_no": "9116093"}, by="omar",
                     config=CONFIG)
check("...a person cannot type G4 in: refused (not printed on the Bill of Entry), still in review",
      still["state"] == S.NEEDS_REVIEW and _p and "not printed" in _p[0], (still["state"], _p))
good = run_job(ast, "176-88452310")                    # a Bill of Entry that prints it
good, _o = P.send(ast, good, M.GraphMailer(), by="omar", confirm_wait_s=3, sleep=NOSLEEP)


r = po_ask("Why wasn't this PO sent?", mis["po_id"])
# The Hub row 176-99001122 has a declaration attached that is printed for 176-88452310.
check("'Why wasn't this PO sent?' — the mismatch, both values, from the record",
      "was not sent because the PDF BL / AWB (176-88452310) does not match the Hub record "
      "(176-99001122)" in r["answer"], r["answer"][:600])
check("...typed: Fact lines, a Recommendation, and the honest recovery line",
      "**Fact**" in r["answer"] and "**Recommendation**" in r["answer"]
      and "No verified recovery strategy exists for this failure" in r["answer"])
r = missing_answer
check("'What's missing?' — the supplier invoice number, required, and nothing invented",
      "Supplier invoice No.: missing — required" in r["answer"]
      and "Left empty (optional): Charge to, Priority" in r["answer"], r["answer"])
r = po_ask("Did the PDF match the Hub?", mis["po_id"])
check("'Did the PDF match the Hub?' — PDF vs Hub → MISMATCH",
      "PDF 176-88452310 · Hub 176-99001122 → MISMATCH" in r["answer"], r["answer"])
r = po_ask("Did the PDF match the Hub?", good["po_id"])
check("...and for the good one → MATCH", "→ MATCH" in r["answer"], r["answer"])
r = po_ask("Who was it sent to?", good["po_id"])
check("'Who was it sent to?' — the recipient, from Graph's confirmation",
      "accounts.ghana@mantrac.com" in r["answer"] and "Sent Items" in r["answer"], r["answer"])
r = po_ask("Who was it sent to?", mis["po_id"])
check("...a blocked one: not sent, and why", "Not sent" in r["answer"] and "blocked" in r["answer"], r["answer"])
r = po_ask("What template was used?", good["po_id"])
check("'What template was used?' — DUTY_REQUEST_V1, read back", "DUTY_REQUEST_V1" in r["answer"]
      and "read back" in r["answer"])
r = po_ask("What failed?", done["po_id"])
check("'What failed?' — no Bill Entry document in the record's Documents section",
      "none whose name starts with 'Bill Entry'" in r["answer"]
      and "Attach the Bill Entry document" in r["answer"], r["answer"])
r = po_ask("What should I do next?", mis["po_id"])
check("'What should I do next?' — check the attachment, with both values",
      "Check which document is attached to 176-99001122 in the Hub: the PDF says 176-88452310, "
      "the Hub says 176-99001122" in r["answer"], r["answer"])
r = po_ask("What should I do next?", good["po_id"])
check("...for a verified one: nothing needs you", "Nothing needs you" in r["answer"], r["answer"])
r = assistant.answer("Why wasn't the duty request for 176-99001122 sent?", {}, {})
check("Asked from anywhere, naming the job's BOL/AWB, it finds that job",
      r.get("po_id") == mis["po_id"] and "does not match the Hub record" in r["answer"], r.get("po_id"))
for q in ("Why wasn't this PO sent?", "What failed?", "What's missing from this PO?",
          "Who was it sent to?", "What template was used?", "What should I do next?"):
    r = po_ask(q, mis["po_id"])
    check("'{0}' never falls back to 'I don't have that information'".format(q),
          FALLBACK not in r["answer"] and r.get("grounded"))
r = po_ask("How was the document found?", good["po_id"])
check("'How was the document found?' — the eHub steps from the job's own trail",
      all(x in r["answer"] for x in ("eHub record 176-88452310", "Status in eHub: 'Under Clearance'",
                                       "Manage opened", "Documents section found",
                                       "Bill Entry 40726534505.pdf → identifier 40726534505",
                                       "passed to the next stage", "→ MATCH")), r["answer"])
sk_job = run_job(ast, "176-30000002")
r = po_ask("Why was it skipped?", sk_job["po_id"])
check("'Why was it skipped?' — the status eHub showed, and that Manage was not opened",
      "Under Clearance - Hold" in r["answer"] and "Manage was not opened" in r["answer"], r["answer"])
r = po_ask("Why wasn't this PO sent?", sk_job["po_id"])
check("...and 'why wasn't it sent' says the same, from the record",
      "not exactly 'Under Clearance'" in r["answer"], r["answer"][:300])
r = po_ask("What identifier was used?", good["po_id"])
check("'What identifier was used?' — 40726534505, from the Bill Entry document's name",
      "identifier 40726534505" in r["answer"], r["answer"])
r = assistant.answer("How is the run going?", {"shipments": [], "counters": {}}, {})
check("A shipment question with no PO words is still a shipment answer",
      not str(r.get("intent", "")).startswith("po_"), r.get("intent"))
r = assistant.answer("What are the POs?", {}, {"po": "0"})
check("A role that may not see PO jobs gets no PO answer from ATLAS",
      not str(r.get("intent", "")).startswith("po_"), r.get("intent"))


# ═════════════════════════════════════════════════════════════════════════
rule("18. PO FAILURE INTELLIGENCE AND LEARNING")
# ═════════════════════════════════════════════════════════════════════════
fi = F.from_po(mis, ast.events(mis["po_id"]))
check("A blocked PO is a failure record: VALIDATION_FAILURE, declared, root cause verified",
      fi["classification"] == "VALIDATION_FAILURE" and fi["classification_basis"] == "declared"
      and fi["root_cause_status"] == "VERIFIED" and fi["domain"] == "po")
check("Every statement is typed", {i["type"] for i in fi["facts"] + fi["recommendations"]}
      <= {"FACT", "RECOMMENDATION"})
check("No invented recovery: no verified strategy, nothing to execute",
      fi["recovery_plan"]["status"] == "NO_VERIFIED_STRATEGY" and not fi["recovery_plan"]["steps"])
check("A confirmed PO is not a failure", F.from_po(good) is None)
svc = SV.current()
for rr in (mis, noinv, done, good):
    svc.processed(ast.get(rr["po_id"]))
svc.learn(ast.get(good["po_id"]))
rows = E.all_events(["po"])
check("Outcomes reach the learning store; only the confirmed send is verified",
      {r["state"]: r["verified"] for r in rows if r["reference"] in ("176-99001122", "176-55500011")}
      == {"IDENTITY_MISMATCH": False, "PDF_NOT_FOUND": False}
      and any(r["verified"] and r["state"] == "EMAIL_CONFIRMED" for r in rows), rows)
L.invalidate()
snap = L.snapshot()
check("The learning snapshot has a PO section that counts them apart from shipments",
      snap["po"]["confirmed"] >= 1 and snap["po"]["failed"] >= 2, snap["po"])
months = snap.get("months") or []
check("...and PO jobs are never counted as shipments or runs",
      sum(m.get("shipments", 0) for m in months) == 0)
learned = [i for i in snap["issues"] if i["provider"] == "PO"]
check("Each PO failure category is an issue ATLAS can recognise later",
      {"VALIDATION_FAILURE", "DOCUMENT_NOT_FOUND"} <= {i["issue"] for i in learned}, [i["issue"] for i in learned])


# ═════════════════════════════════════════════════════════════════════════
rule("19. THE WORKER PATH — a real `python -m po process`, reported honestly")
# ═════════════════════════════════════════════════════════════════════════
cp.request("POST", "/worker/v1/heartbeat", {"state": "IDLE"})
s_, d_, _ = op.post("/api/po", {"reference": "176-88452310", "invoice_no": "1"})
wid = d_["po_id"]
status, cmds, _ = cp.request("GET", "/worker/v1/commands?wait=1")
cmd = [c for c in cmds["commands"] if c["kind"] == "po_process"][0]
ok, message = agent.po_process(cmd["payload"]["po_id"], cmd["payload"]["record"], wait=True)
final = app.po.store.get(wid)
check("Without the Hub's credentials file the job cannot sign in — AUTH_REQUIRED, and says so",
      ok and final["state"] == S.AUTH_REQUIRED and "could not be opened" in final["failure"]["detail"],
      (final["state"], final.get("failure")))
check("...which ATLAS explains with the next step (sign the worker in, then recover)",
      final["failure"]["category"] == "AUTH_REQUIRED"
      and "Sign the worker's browser in to eHub" in " ".join(
          r["text"] for r in F.from_po(final)["recommendations"]))


# ═════════════════════════════════════════════════════════════════════════
rule("20. END TO END — Process PO → … → Graph → verified → audit → ATLAS")
# ═════════════════════════════════════════════════════════════════════════
e2e_store = new_store("e2e")
jobs = []
e2e = SV.register(SV.PoService(store=e2e_store, launcher=jobs.append, mailer_factory=M.GraphMailer,
                               config=CONFIG))
kind, status, body = W.handle(e2e, "POST", "/api/po", {"reference": "176-12121212"},
                              "omar.ops@mantrac.com", lambda p: True)
check("POST /api/po starts a job and returns at once (QUEUED)",
      status == 200 and e2e_store.get(body["po_id"])["state"] == S.QUEUED and len(jobs) == 1)
pid = body["po_id"]
P.process(e2e_store, e2e_store.get(pid), hub_source(), CONFIG, sleep=NOSLEEP)  # what the job process does
e2e.processed(e2e_store.get(pid))
rec = e2e_store.get(pid)
check("Hub → PDF → extract → validate → template → saved: EMAIL READY",
      rec["state"] == S.EMAIL_PREPARED and rec["validation"]["passed"]
      and Path(rec["output"]["path"]).is_file(), rec["state"])
kind, status, body = W.handle(e2e, "POST", "/api/po/{0}/send".format(pid), {}, "omar.ops@mantrac.com",
                              lambda p: True)
e2e.wait_idle(30)
rec = e2e_store.get(pid)
msg = [m for m in GRAPH["sent"] if m["subject"].endswith("176-12121212")]
check("Send PO → Graph → found in Sent Items → VERIFIED",
      rec["state"] == S.EMAIL_CONFIRMED and len(msg) == 1, rec["state"])
check("The email carries the generated document, unchanged",
      hashlib.sha256(msg[0]["attachment_bytes"]).hexdigest() == rec["output"]["sha256"])
local_audit = [json.loads(l) for l in (e2e_store.folder / "audit.jsonl").read_text().splitlines()]
check("Audit (single machine): started, processed, confirmed",
      [a["action"] for a in local_audit] == ["PO_PROCESS_STARTED", "PO_PROCESSED", "PO_EMAIL_CONFIRMED"],
      [a["action"] for a in local_audit])
r = assistant.answer("What happened with this PO?", {}, {"domain": "po", "po_id": pid})
check("ATLAS explains what happened, from the events",
      "EMAIL_CONFIRMED" in r["answer"] and "VALIDATION_PASSED" in r["answer"], r["answer"][:400])
r = assistant.answer("Who was it sent to?", {}, {"domain": "po", "po_id": pid})
check("...and who it went to", "accounts.ghana@mantrac.com" in r["answer"])
cli = subprocess.run([sys.executable, "-m", "po", "read",
                      str(e2e_store.folder / "documents" / (rec["document"]["sha256"] + ".pdf")),
                      "--hub-bol", "176-12121212", "--identifier", "40799887766",
                      "--invoice-no", "9116093"],
                     cwd=str(HERE), capture_output=True, text=True, timeout=60)
check("`python -m po read` shows the same extraction and validation (tuning tool)",
      cli.returncode == 0 and '"passed": true' in cli.stdout, cli.stderr[-300:])

# The page, drawn from these records, in a real browser.
from dashboard import server as tower_server                   # noqa: E402
os.environ["PO_DATA_DIR"] = str(WORK / "atlas")
SV._CURRENT["service"] = None
srv = ThreadingHTTPServer(("127.0.0.1", 0), tower_server.Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
DASH = "http://127.0.0.1:{0}".format(srv.server_address[1])
ui = BROWSER.new_page(viewport={"width": 1440, "height": 1000})
errors = []
ui.on("pageerror", lambda e: errors.append(str(e)))
ui.add_init_script("try{sessionStorage.setItem('ct-intro','1')}catch(e){}")
ui.goto(DASH + "/")
ui.wait_for_timeout(800)
ui.evaluate("go('po')")
ui.wait_for_selector("#poRows tr[data-po]", timeout=10000)
check("PO Automation is in the navigation", ui.locator("[data-nav='po']").count() >= 1)
kpis = ui.inner_text("#poKpis")
check("The KPI strip shows the six counts", all(k in kpis for k in (
    "Pending", "Processing", "Validation required", "Generated", "Sent", "Failed")), kpis)
heads = ui.inner_text(".po-t thead").upper()
check("The queue has the brief's columns", all(h.upper() in heads for h in (
    "Supplier", "Document", "Status", "Validation", "Template", "Email", "Created", "Last update")))
check("Each job is a row, one per job in the store",
      ui.locator("#poRows tr[data-po]").count() == len(ast.all()),
      (ui.locator("#poRows tr[data-po]").count(), len(ast.all())))
ui.click("#poRows tr[data-po='{0}']".format(mis["po_id"]))
ui.wait_for_selector("#poDwBody .po-vt", timeout=8000)
drawer = ui.inner_text("#poDwBody")
check("The drawer shows the validation: PDF 176-88452310, Hub 176-99001122, MISMATCH",
      "176-99001122" in drawer and "176-88452310" in drawer and "MISMATCH" in drawer.upper(),
      drawer[:400])
check("...EMAIL BLOCKED with the reason, and 'No email was sent.'",
      "EMAIL BLOCKED" in drawer.upper() and "No email was sent." in drawer)
check("...and no Send PO button for a blocked job", ui.locator("#poSend").count() == 0)
ui.locator("#poDwX").click()
ui.click("#poRows tr[data-po='{0}']".format(good["po_id"]))
ui.wait_for_function("document.querySelector('#poDwBody') && document.querySelector('#poDwBody')"
                     ".innerText.toLowerCase().indexOf('ehub discovery') >= 0", timeout=8000)
drawer = ui.inner_text("#poDwBody")
check("The drawer shows the eHub steps: record, clearance, Manage, Documents, Bill Entry, "
      "identifier, PDF retrieved",
      all(x.lower() in drawer.lower() for x in ("eHub record", "Clearance status", "Manage opened",
                                                "Documents section", "Bill Entry 40726534505.pdf",
                                                "40726534505", "PDF retrieved")), drawer[:600])
steps = ui.inner_text("#poDwBody .po-steps")
check("...and the progress strip in the business order",
      [x.strip() for x in steps.split("\n") if x.strip()][:6] ==
      ["eHub record", "Clearance", "Manage", "Bill Entry", "Identifier", "PDF retrieved"], steps)
check("ATLAS appears once, as PO intelligence, not a second assistant",
      "PO intelligence" in ui.inner_text(".po-ax") and ui.locator("#poAxFig .atlas-fig").count() == 1)
check("No page errors", not errors, errors)
SHOTS = os.environ.get("PO_SHOTS")
if SHOTS:
    # Visual QA only: the page and the drawer, light and dark, desktop and phone.
    Path(SHOTS).mkdir(parents=True, exist_ok=True)
    ui.screenshot(path=str(Path(SHOTS) / "po-drawer-light.png"))
    ui.locator("#poDwX").click()
    ui.wait_for_timeout(400)
    ui.screenshot(path=str(Path(SHOTS) / "po-page-light.png"), full_page=True)
    for scheme, width in (("dark", 1440), ("light", 390)):
        pg = BROWSER.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
        pg.add_init_script("try{sessionStorage.setItem('ct-intro','1')}catch(e){}")
        pg.goto(DASH + "/")
        pg.wait_for_timeout(700)
        pg.evaluate("go('po')")
        pg.wait_for_selector("#poRows tr[data-po]", timeout=10000)
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(Path(SHOTS) / "po-page-{0}-{1}.png".format(scheme, width)),
                      full_page=True)
        pg.click("#poRows tr[data-po='{0}']".format(good["po_id"]))
        pg.wait_for_selector("#poDwBody .po-vt", timeout=8000)
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(Path(SHOTS) / "po-drawer-{0}-{1}.png".format(scheme, width)))
        print("    scrollWidth", scheme, width, pg.evaluate("document.documentElement.scrollWidth"))
        pg.close()
ui.close()

# ═════════════════════════════════════════════════════════════════════════
rule("21. THE VERIFICATION GATE — REAL only from the real eHub; TEST never passes as REAL")
# ═════════════════════════════════════════════════════════════════════════
every = [r for store_dir in WORK.iterdir() if (store_dir / "jobs").is_dir()
         for r in S.Store(folder=store_dir).all(500)]
with_trail = [r for r in every if r.get("discovery")]
check("Every stand-in job in this suite ({0}) is TEST / UNVERIFIED — not one REAL".format(
      len(with_trail)), with_trail and all(
          (r.get("provenance") or {}).get("source") == "TEST" and
          (r.get("provenance") or {}).get("verification") == "UNVERIFIED" for r in with_trail),
      [(r["po_id"], r.get("provenance")) for r in with_trail
       if (r.get("provenance") or {}).get("source") != "TEST"][:3])
host = EH.ehub_host()
check("The rule: production navigation + every page on {0} + complete → REAL / VERIFIED".format(
      host), EH.provenance(True, {"list": host, "manage": host, "download": host}, True)
      ["verification"] == "VERIFIED")
check("...production navigation but pages from 127.0.0.1 → TEST",
      EH.provenance(True, {"list": "127.0.0.1", "manage": "127.0.0.1"}, True)["source"] == "TEST")
check("...stand-in navigation, even showing the real host → TEST",
      EH.provenance(False, {"list": host, "manage": host, "download": host}, True)["source"]
      == "TEST")
check("...one page off the real host → TEST",
      EH.provenance(True, {"list": host, "manage": host, "download": "files.example.com"},
                    True)["source"] == "TEST")
check("...real but stopped before the document → REAL / UNVERIFIED",
      EH.provenance(True, {"list": host}, False) ==
      dict(EH.provenance(True, {"list": host}, False), source="REAL", verification="UNVERIFIED"))
check("...and nothing observed → TEST", EH.provenance(True, {}, True)["source"] == "TEST")
os.environ.pop("PO_ALLOW_TEST_SEND")
gst = new_store("gate")
gj = run_job(gst, "176-88452310")
before = GRAPH["counter"]
gj2, gout = P.send(gst, gj, M.GraphMailer(), by="omar", sleep=NOSLEEP)
check("A TEST document is never emailed for real: BLOCKED, no Graph call",
      gout == "BLOCKED" and GRAPH["counter"] == before
      and any("TEST / UNVERIFIED" in r for r in P.blocked_reasons(gst, gj2)),
      P.blocked_reasons(gst, gj2))
os.environ["PO_ALLOW_TEST_SEND"] = "1"
from po.evidence import stages as STAGES                       # noqa: E402
sg = STAGES(gst.get(gj["po_id"]))
check("Every stage carries its status, evidence and source (TEST here)",
      [x["stage"] for x in sg] == ["ehub_record", "clearance", "manage", "documents", "bill_entry",
                                   "identifier", "pdf", "fields", "validation", "template",
                                   "output", "email", "verified"]
      and all(x["source"] == "TEST" for x in sg)
      and [x["status"] for x in sg][:11] == ["OK"] * 11, [(x["stage"], x["status"]) for x in sg])
check("...OK only with its evidence: the file name, the identifier, the SHA-256",
      sg[4]["evidence"]["filename"] == "Bill Entry 40726534505.pdf"
      and sg[5]["evidence"]["identifier"] == "40726534505" and len(sg[6]["evidence"]["sha256"]) == 64)
pr = CLI.probe(PAGE, "176-88452310", WORK / "probe2", source=hub_source())
check("A probe on the stand-in reports source TEST / UNVERIFIED",
      pr["source"] == "TEST" and pr["verification"] == "UNVERIFIED", pr.get("provenance"))
check("...with everything the real proof needs: domain, path, row and status, file name, "
      "identifier, link, time",
      pr["ehub_domain"] == host and [p["page"] for p in pr["navigation_path"]] ==
      ["shipment list", "Manage", "Documents", "Bill Entry document"]
      and pr["selected_row"]["bol_awb"] == "176-88452310"
      and pr["selected_row"]["status"] == "Under Clearance"
      and pr["bill_entry_filename"] == "Bill Entry 40726534505.pdf"
      and pr["po_identifier"] == "40726534505" and pr["document_reference"]["method"]
      and pr["started"] and pr["document"]["retrieved_at"], {k: pr.get(k) for k in (
          "selected_row", "bill_entry_filename", "po_identifier")})

rule("22. eHUB CONNECTIVITY — the diagnostic, and every way it can fail")
from po import diagnose as DG                                  # noqa: E402


CHROMIUM = next(iter(sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))), None)


def diag(url, creds=None, browser=False, executable=None):
    """The diagnostic in its own process (it runs its own browser)."""
    code = ("import json,sys; sys.path.insert(0,'.'); from po import diagnose as D; "
            "print(json.dumps(D.check(url={0!r}, launch_browser={1}, credentials={2!r}, "
            "timeout=5)))").format(url, browser, creds)
    out = subprocess.run([sys.executable, "-c", code], cwd=str(HERE), capture_output=True,
                         text=True, timeout=180,
                         env=dict(os.environ, PO_BROWSER_CHANNEL="",
                                  PO_BROWSER_EXECUTABLE=str(executable or CHROMIUM or "")))
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return {"result": "CRASH", "stderr": out.stderr[-400:]}


d = diag("https://ehub.invalid/WorkFlow/ShipmentTracking/ShipmentList.aspx")
check("No connectivity (DNS): NETWORK at dns, nothing later attempted",
      d["result"] == "FAILED" and d["category"] == "NETWORK" and d["failed_stage"] == "dns"
      and "credentials" in d["not_reached"], d)
d = diag("https://127.0.0.1:1/x")
check("No connectivity (port closed): NETWORK at tcp", d.get("category") == "NETWORK"
      and d.get("failed_stage") == "tcp", d)
d = diag(HUB + "/ehub/list")
check("Reachable, but no credentials file: AUTHENTICATION at credentials (nothing shown)",
      d.get("category") == "AUTHENTICATION" and d.get("failed_stage") == "credentials"
      and HUB_PASS not in json.dumps(d), d)
d = diag(HUB + "/ehub/list", (HUB_USER, HUB_PASS), browser=True,
         executable="/nonexistent/browser")
check("A browser that cannot start: BROWSER at browser", d.get("category") == "BROWSER"
      and d.get("failed_stage") == "browser", d)
d = diag(HUB + "/ehub/list", ("hub.reader", "wrong-password"), browser=True)
check("Wrong credentials: AUTHENTICATION at sign_in", d.get("category") == "AUTHENTICATION"
      and d.get("failed_stage") == "sign_in" and "wrong-password" not in json.dumps(d), d)
d = diag(HUB + "/ehub/manage/176-88452310", (HUB_USER, HUB_PASS), browser=True)
check("Signed in, but no shipment list there: APPLICATION at shipment_list",
      d.get("category") == "APPLICATION" and d.get("failed_stage") == "shipment_list", d)
d = diag(HUB + "/ehub/list", (HUB_USER, HUB_PASS), browser=True)
check("The stand-in list passes every stage — named a stand-in, and never REAL",
      d.get("result") == "REACHABLE" and d.get("target") == "stand-in"
      and "REAL" not in str(d.get("level")) and HUB_PASS not in json.dumps(d), d)
check("A connectivity check is never REAL, even against the real host",
      "REAL" not in str(DG.check(url="https://" + EH.ehub_host() + "/x", launch_browser=False,
                                 timeout=3).get("level")))
check("Credentials are never in a report", all(HUB_PASS not in json.dumps(x) for x in (d,)))

rule("23. DISCOVERY FAILURE MODES, at the boundary")
cleared_only = [{"bol_awb": "176-1", "status": "Cleared", "view": "BU", "table_page": 1},
                {"bol_awb": "176-2", "status": "Released", "view": "BU", "table_page": 1}]
src = EH.EHubSource(PAGE, lambda page, ref, skip: EH.choose(cleared_only, ref, skip), manage_stub)
try:
    src.fetch(None)
    e = None
except P.SourceError as error:
    e = error
check("eHub reachable but no Under Clearance row: stops, nothing opened, rows recorded",
      e is not None and e.kind == "not_found" and len(e.trail["looked_at"]) == 2
      and "Manage" not in [p["page"] for p in e.trail["navigation_path"]], str(e))


def manage_down(page, row):
    raise RuntimeError("the Manage button for {0} was not found".format(row["bol_awb"]))


try:
    EH.EHubSource(PAGE, find_stub, manage_down).fetch("176-88452310")
    e = None
except P.SourceError as error:
    e = error
check("Manage page unavailable: stops at Manage with the reason, nothing else read",
      e is not None and [x["step"] for x in e.trail["steps"]][-1] == "manage"
      and not e.trail["steps"][-1]["ok"] and "documents" not in e.trail, str(e))
for name in ("BillofEntry_.pdf", "Bill Entry ABC.pdf", "Bill Entry - .pdf"):
    picked = EH.select([{"name": name}])
    check("Malformed {0!r}: needs review, no identifier invented".format(name),
          picked[0] is None and picked[3] == "review", picked)

rule("24. THE PO WORKFLOW CANNOT AFFECT THE SHIPMENT WORKFLOW")
ETA_SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")
check("update_eta.py does not import the PO module",
      not re.search(r"^\s*(from|import)\s+po\b", ETA_SRC, re.M))
indep = S.Store(folder=WORK / "indep")
fail_job = indep.create(doctypes.DEFAULT, "176-88452310", {"invoice_no": "1"})
po_proc = subprocess.Popen([sys.executable, "-m", "po", "process", "--po-id", fail_job["po_id"]],
                           cwd=str(HERE), env=dict(os.environ, PO_DATA_DIR=str(indep.folder)),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
eta = subprocess.run([sys.executable, "fixtures/eta_fake_run.py"], cwd=str(HERE),
                     capture_output=True, text=True, timeout=300)
po_proc.wait(120)
eta_out = json.loads(eta.stdout.strip().splitlines()[-1])
check("A PO job failing alongside: the ETA run still finishes, 3 written, 0 failed",
      eta_out["status"] == "finished" and eta_out["counters"]["successful"] == 3
      and eta_out["counters"]["failed"] == 0 and eta_out["writes"] == ["E1", "E2", "E3"], eta_out)
check("...nothing of the PO job in the ETA run's state", eta_out["po_in_eta_state"] is False)
pf = indep.get(fail_job["po_id"])
check("...and the PO job ended visibly, on its own job ID: {0}".format(fail_job["po_id"]),
      po_proc.returncode != 0 and pf["state"] == S.AUTH_REQUIRED
      and pf["failure"]["category"] == "AUTH_REQUIRED", pf.get("failure"))

rule("26. THE BRIEF'S STATE MACHINE, THE WORKER CHECK A-J, THE QUEUE ROW, ATLAS")
from dashboard import atlas_po as AP                          # noqa: E402
from intelligence import verification as VER                  # noqa: E402
from worker import po_check as PC                             # noqa: E402

BRIEF = ("PO_DISCOVERED", "PDF_FOUND", "PDF_READ", "FIELDS_EXTRACTED", "VALIDATING", "VALIDATED",
         "TEMPLATE_GENERATED", "SAVED", "EMAIL_PREPARED", "EMAIL_SENT", "EMAIL_CONFIRMED")
FAILS = ("PDF_NOT_FOUND", "PDF_UNREADABLE", "EXTRACTION_FAILED", "VALIDATION_FAILED",
         "TEMPLATE_FAILED", "EMAIL_FAILED", "NEEDS_REVIEW")
names = {getattr(S, n) for n in dir(S) if n.isupper() and isinstance(getattr(S, n), str)}
check("Every state the brief names exists, under that exact name",
      set(BRIEF) | set(FAILS) <= names, sorted(set(BRIEF) | set(FAILS) - names))
check("The brief's success path is the state machine's: each step leads to the next",
      all(BRIEF[i + 1] in S.TRANSITIONS.get(BRIEF[i], ()) for i in range(len(BRIEF) - 1)
          if BRIEF[i] not in ("EMAIL_PREPARED",))
      and S.EMAIL_SENDING in S.TRANSITIONS[S.EMAIL_PREPARED]
      and S.EMAIL_SENT in S.TRANSITIONS[S.EMAIL_SENDING])
check("Generated is not saved: TEMPLATE_GENERATED cannot jump to EMAIL_PREPARED",
      S.EMAIL_PREPARED not in S.TRANSITIONS[S.TEMPLATE_GENERATED]
      and set(S.TRANSITIONS[S.SAVED]) == {S.EMAIL_PREPARED, S.WORKER_DISCONNECTED}
      and S.EMAIL_PREPARED not in S.TRANSITIONS[S.WORKER_DISCONNECTED])
st26 = new_store("s26")
legacy = st26.create(doctypes.DEFAULT, "176-LEGACY01", {"invoice_no": "1"})
raw = json.loads(st26._path(legacy["po_id"]).read_text(encoding="utf-8"))
raw["state"] = "DISCOVERED"
st26._path(legacy["po_id"]).write_text(json.dumps(raw), encoding="utf-8")
check("A job stored before the rename reads as PO_DISCOVERED",
      st26.get(legacy["po_id"])["state"] == "PO_DISCOVERED")

good26 = run_job(st26, "176-88452310")
ev26 = [e["event"] for e in st26.events(good26["po_id"])]
check("A good job: TEMPLATE_GENERATED, then OUTPUT_SAVED, then EMAIL_PREPARED — in that order",
      good26["state"] == S.EMAIL_PREPARED and ev26.index("TEMPLATE_GENERATED") <
      ev26.index("OUTPUT_SAVED") < ev26.index("EMAIL_PREPARED"), ev26)
check("...the generated template is recorded (version, cells read back in memory)",
      (good26.get("template") or {}).get("cells") and good26["template"]["read_back"])
check("...and the saved file is in the configured output folder, on disk, with its SHA-256",
      Path(good26["output"]["path"]).exists() and good26["output"]["folder"] == str(st26.output_dir)
      and hashlib.sha256(Path(good26["output"]["path"]).read_bytes()).hexdigest()
      == good26["output"]["sha256"])
check("...and no email went out: EMAIL READY, nothing sent", good26["email"]["status"] == "READY")

broken = new_store("s26b")
blocker = WORK / "s26b-not-a-folder"
blocker.write_text("x")
broken.output_dir = blocker / "out"
nosave = run_job(broken, "176-88452310")
check("The output folder cannot be written: SAVE_FAILED at the output stage, after "
      "TEMPLATE_GENERATED — never SAVED, no email prepared",
      nosave["state"] == S.SAVE_FAILED and nosave["failure"]["stage"] == "output"
      and nosave.get("template") and not nosave.get("output")
      and "EMAIL_PREPARED" not in [e["event"] for e in broken.events(nosave["po_id"])],
      (nosave["state"], nosave.get("failure")))
bad26 = run_job(st26, "176-99001122")
check("Validation fails (identity mismatch): no template, nothing saved, no email",
      bad26["state"] == S.IDENTITY_MISMATCH and not bad26.get("template")
      and not bad26.get("output") and not {"TEMPLATE_GENERATED", "OUTPUT_SAVED", "EMAIL_PREPARED",
                                           "EMAIL_SENT"} & {e["event"] for e in st26.events(
                                               bad26["po_id"])})

# The worker check, A-J, read off the job record.
obs = {"kind": "po-pipeline", "po_steps": {}, "result": "COMPLETED",
       "provenance": good26.get("provenance")}
PC._step(obs, "A", "OK", {"page": "stand-in"})
PC._from_record(obs, good26)
check("B-I are read off the job record: every one OK for the good job",
      all(obs["po_steps"][k]["status"] == "OK" for k in "BCDEFGHI"),
      {k: obs["po_steps"][k]["status"] for k in "BCDEFGHI"})
obs_bad = {"kind": "po-pipeline", "po_steps": {}, "provenance": bad26.get("provenance")}
PC._step(obs_bad, "A", "OK")
PC._from_record(obs_bad, bad26)
check("For the mismatching job: G FAILED with the reason, H and I not reached",
      obs_bad["po_steps"]["G"]["status"] == "FAILED"
      and "mismatch" in str(obs_bad["po_steps"]["G"]["reason"]).lower()
      and obs_bad["po_steps"]["H"]["status"] == "NOT_RUN"
      and obs_bad["po_steps"]["I"]["status"] == "NOT_RUN", obs_bad["po_steps"]["G"])
PC.controlled_email(obs, good26, None)
check("J without --email-to: NOT_RUN — the business recipient is never emailed from the check",
      obs["po_steps"]["J"]["status"] == "NOT_RUN" and "never emailed" in obs["po_steps"]["J"]["reason"])
saved_env = {k: os.environ.pop(k) for k in ("GRAPH_CLIENT_SECRET",) if k in os.environ}
PC.controlled_email(obs, good26, "tester@mantrac.com")
check("J with Graph not configured: BLOCKED, naming the missing setting (never its value)",
      obs["po_steps"]["J"]["status"] == "BLOCKED"
      and "GRAPH_CLIENT_SECRET" in obs["po_steps"]["J"]["reason"]
      and SECRET not in json.dumps(obs))
os.environ.update(saved_env)
sent_before = len(GRAPH["sent"])
PC.controlled_email(obs, good26, "tester@mantrac.com", wait_s=3)
check("J controlled send: created, accepted (202) and found in Sent Items — to the test address",
      obs["po_steps"]["J"]["status"] == "OK" and obs["po_steps"]["J"]["evidence"]["send_http"] == 202
      and obs["po_steps"]["J"]["evidence"]["sent_items"] is True
      and len(GRAPH["sent"]) == sent_before + 1, obs["po_steps"]["J"])
check("...marked a test in its subject, and the job itself was NOT marked sent",
      "VERIFICATION — TEST" in obs["po_steps"]["J"]["evidence"]["subject"]
      and st26.get(good26["po_id"])["state"] == S.EMAIL_PREPARED)
check("The stand-in job is never REAL: its discovery is TEST, so the worker level is BLOCKED",
      VER.classify(obs, "worker")[0] == "BLOCKED"
      and "not REAL / VERIFIED" in VER.classify(obs, "worker")[1][0])
real_like = dict(obs, provenance={"source": "REAL", "verification": "VERIFIED"})
check("Were the same steps observed REAL on the worker, A-J all OK -> REAL VERIFIED",
      VER.classify(real_like, "worker")[0] == "REAL VERIFIED")
check("...and never from the cloud or the suite",
      VER.classify(real_like, "cloud")[0] == "BLOCKED" and VER.classify(real_like, "test")[0] == "TEST")
partial = json.loads(json.dumps(real_like))
partial["po_steps"]["J"] = {"step": "J", "label": "Graph", "status": "NOT_RUN", "reason": "no --email-to"}
check("A-I real but no controlled email -> REAL OBSERVED, NOT VERIFIED (J)",
      VER.classify(partial, "worker")[0] == "REAL OBSERVED"
      and any("step J" in r for r in VER.classify(partial, "worker")[1]))
import update_eta as _A                                       # noqa: E402
_load = _A.load_credentials
_A.load_credentials = lambda: (_ for _ in ()).throw(Exception("Missing credentials file: x"))
blocked_run = PC.run(store=new_store("s26c"))
_A.load_credentials = _load
check("The whole check where eHub cannot be opened: A BLOCKED with the reason, B-J NOT_RUN, "
      "no job created", blocked_run["po_steps"]["A"]["status"] == "BLOCKED"
      and all(blocked_run["po_steps"][k]["status"] == "NOT_RUN" for k in "BCDEFGHIJ")
      and not new_store("s26c").all(5), blocked_run["po_steps"]["A"])
check("...and the network as this machine sees it is recorded with it",
      "host" in (blocked_run.get("network_probe") or {}))

# The queue row.
row = SV.card(good26)
check("Queue row: Declaration No. from the PDF, Bill Entry No., extraction, template SAVED, "
      "email READY (not sent)", row["declaration"] == good26["fields"]["document_number"]["value"]
      and row["identifier"] == good26["identifier"] and row["extraction"].endswith("fields")
      and row["template"] == "SAVED" and row["email"] == "READY (not sent)", row)
rowb = SV.card(bad26)
check("...for a validation failure: no template, email NOT SENT",
      rowb["validation"] == "FAILED" and rowb["template"] is None and rowb["email"] == "NOT SENT", rowb)
UIH = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
check("The PO queue shows the brief's columns", all(c in UIH for c in (
    "<th>Declaration No.</th>", "<th>BOL / AWB</th>", "<th>Supplier</th>",
    "<th>Bill Entry / PO No.</th>", "<th>Document</th>", "<th>Status</th>", "<th>Extraction</th>",
    "<th>Validation</th>", "<th>Template</th>", "<th>Email</th>", "<th>Created</th>",
    "<th>Last update</th>")))

# ATLAS, on these job records.
SV.register(SV.PoService(store=st26, launcher=lambda r: None))


def ask_po(q, job):
    ctx = {"domain": "po", "po_id": job["po_id"]}
    kind = AP.detect(q, ctx)
    return kind, (AP.answer(kind, q, ctx) or {}).get("answer", "")


k, a = ask_po("What happened to this PO?", good26)
check("'What happened to this PO?' — stage by stage, from the record",
      k == "happened" and "Template generated: OK" in a and "Output saved: OK" in a
      and "Email sent (Graph): WAITING" in a, (k, a[:500]))
check("...and it says first that this job was not on the real eHub (stand-in, not evidence)",
      a.find("**Not established** — This job did not run against the real eHub") >= 0
      and a.find("Not established") < a.find("Template generated"), a[:400])
k, a = ask_po("Where is the Bill Entry PDF?", good26)
check("'Where is the Bill Entry PDF?' — the file, its SHA-256, where the job kept it",
      k == "document" and good26["document"]["filename"] in a
      and good26["document"]["sha256"][:16] in a and good26["document"]["evidence"] in a, (k, a[:400]))
k, a = ask_po("What did we extract?", good26)
check("'What did we extract?' — each value with the PDF line it came from, and the duty "
      "calculation", k == "extracted" and "from the line" in a
      and "duty request rule (duty amount less the VAT/levy lines)" in a, (k, a[:600]))
k, a = ask_po("Why did validation fail?", bad26)
check("'Why did validation fail?' — the mismatch, both values, and that nothing was generated "
      "or sent", k == "match" and "MISMATCH" in a
      and "No template was generated and no email was sent." in a, (k, a[:600]))
k, a = ask_po("Was the template generated?", good26)
check("'Was the template generated?' — yes, with the saved file and folder",
      k == "template" and a.count("Yes.") == 1 and good26["output"]["filename"] in a, (k, a[:400]))
k, a = ask_po("Was the template generated?", bad26)
check("...and for the failed job: no", k == "template" and "No template was filled" in a, a[:300])
k, a = ask_po("Was the email actually sent?", good26)
check("'Was the email actually sent?' — No: prepared, waiting for Send PO",
      k == "email_sent" and "**Fact** — No." in a and "Yes" not in a.split("\n\n")[-1], (k, a[:400]))
k, a = ask_po("Was the email actually sent?", bad26)
check("...for a validation failure: No, never sent for a document that did not pass",
      "No. Validation failed" in a, a[:300])
sent26, _o = P.send(st26, st26.get(good26["po_id"]), M.GraphMailer(), by="omar",
                    confirm_wait_s=3, sleep=NOSLEEP)
k, a = ask_po("Was the email actually sent?", sent26)
check("...after a real (stand-in Graph) send: Yes — accepted (HTTP 202) and found in Sent Items",
      sent26["state"] == S.EMAIL_CONFIRMED and "Yes. Microsoft Graph accepted it (HTTP 202)" in a
      and "found in the mailbox's Sent Items" in a, (sent26["state"], a[:400]))

rule("27. THE EMPLOYEE'S WAY: Shipments list → Under Clearance rows → Manage → Bill Entry")
import update_eta as A27                                       # noqa: E402
# be906 prints no Invoice No.; be905 does (it is redefined below without one).
FILES.update({"be905": pdf_of(boe_text(bl="KKLUENR260174", number="40926698905 / 00")),
              "be906": pdf_of(boe_text(bl="KKLUENR260175", number="40926698906 / 00",
                                       invoice=None)),
              "assess": pdf_of("ASSESSMENT NOTICE\n" + "a" * 200),
              "tmag": pdf_of("TMA1-G\n" + "g" * 200), "tmap": pdf_of("TMA1-P\n" + "p" * 200),
              "inv": pdf_of("INVOICE 2600005261\n" + "i" * 200)})
SCREEN_DOCS = [("BillofEntry_40926698905.pdf", "be905", "dl"), ("Assessment.pdf", "assess", "dl"),
               ("TMA1-G-40926698905-01.pdf", "tmag", "dl"), ("TMA1-P-2026-1807484.pdf", "tmap", "dl"),
               ("2600005261 KKLUENR260174 MANTRAC GHANA (1).pdf", "inv", "dl")]
EHUB.append({"bol": "KKLUENR260174", "carrier": "K Line", "status": "Under Clearance",
             "docs": SCREEN_DOCS, "section": True, "layout": "screen", "una": "70076",
             "boe": "40926698905"})
EHUB.append({"bol": "KKLUENR260175", "carrier": "K Line", "status": "Under Clearance",
             "docs": [("Assessment.pdf", "assess", "dl"), ("TMA1-G-40926698906-01.pdf", "tmag", "dl"),
                      ("BillofEntry_40926698906.pdf", "be906", "dl")],
             "section": True, "layout": "screen", "una": "70077", "boe": "40926698906"})
EHUB.append({"bol": "KKLUENR260176", "carrier": "K Line", "status": "Cleared",
             "docs": SCREEN_DOCS, "section": True, "layout": "screen", "una": "70078"})
saved_url = A27.INTERNAL_URL
A27.INTERNAL_URL = HUB + "/ehub/shipments"
# STAND-IN for the Hub's own list navigation (Centralized Shipments Tracking →
# BU view → Status = Under Clearance → page N), which runs on the real eHub
# every ETA run: here it opens the stand-in list with that filter applied.
# Everything after it — reading the rows, their Status cells, Manage on the
# row, Documents, Bill Entry, Download — is the production code.
saved_nav = A27.ensure_filtered_page


def list_nav(page, view, number):
    if number > 1:
        raise A27.SkipShipment("the list has one page")
    page.goto(HUB + "/ehub/shipments?status=Under+Clearance", wait_until="domcontentloaded")


A27.ensure_filtered_page = list_nav
for key in ("be905", "be906", "assess", "tmag", "tmap", "inv"):
    HITS.pop("file:" + key, None)
PRESSED.clear()
real_nav = lambda: EH.EHubSource(PAGE, EH.find_in_ehub, EH.open_manage_in_ehub)  # noqa: E731
st27 = new_store("s27")
k174 = run_job(st27, "KKLUENR260174", source=real_nav())
trail = k174.get("discovery") or {}
check("KKLUENR260174 is found in the Shipments list's Under Clearance rows — nothing typed",
      not any(SEARCHED) and (trail.get("ehub_record") or {}).get("view") == "BU",
      (SEARCHED[-3:], trail.get("ehub_record")))
check("...its row's own Status cell reads 'Under Clearance'",
      (trail.get("clearance") or {}).get("found") == "Under Clearance", trail.get("clearance"))
check("...Manage on that row opened the record, whose Documents section was read",
      "/ehub/bu/KKLUENR260174" in str((trail.get("manage") or {}).get("url"))
      and (trail.get("documents") or {}).get("found"), (trail.get("manage"), trail.get("documents")))
check("...all five documents are listed by their names (rows are blocks, not table rows)",
      set((trail.get("documents") or {}).get("entries") or []) == {n for n, _k, _m in SCREEN_DOCS},
      (trail.get("documents") or {}).get("entries"))
check("...BillofEntry_40926698905.pdf is selected; identifier 40926698905",
      (trail.get("bill_entry") or {}).get("selected") == "BillofEntry_40926698905.pdf"
      and k174.get("identifier") == "40926698905", trail.get("bill_entry"))
check("...and THAT file was downloaded with its row's Download — no other file fetched",
      k174["document"]["sha256"] == hashlib.sha256(FILES["be905"]).hexdigest()
      and HITS.get("file:be905") == 1 and not any(HITS.get("file:" + k) for k in
                                                   ("assess", "tmag", "tmap", "inv")),
      {k: HITS.get("file:" + k) for k in ("be905", "assess", "tmag", "tmap", "inv")})
check("...Delete, Upload, Save, Correction Required and Complete: never pressed", not PRESSED, PRESSED)
check("...the job went through to a saved document, EMAIL READY",
      k174["state"] == S.EMAIL_PREPARED and Path(k174["output"]["path"]).exists(), k174["state"])
check("...the row's UNA+ Invoice Number (70076) is kept as eHub evidence",
      (k174.get("hub") or {}).get("una_invoice") == "70076", k174.get("hub"))
check("Production navigation against a stand-in host is still TEST, never REAL",
      (k174.get("provenance") or {}).get("source") == "TEST", k174.get("provenance"))
k175 = run_job(st27, "KKLUENR260175", source=real_nav())
check("Bill Entry in the THIRD row: its own Download is pressed, not the first one on the page",
      k175["document"] and k175["document"]["sha256"] == hashlib.sha256(FILES["be906"]).hexdigest(),
      (k175["state"], (k175.get("discovery") or {}).get("bill_entry")))
k176 = run_job(st27, "KKLUENR260176", source=real_nav())
check("A record that is not Under Clearance ('Cleared'): SKIPPED, Manage not opened",
      k176["state"] == S.SKIPPED and not (k176.get("discovery") or {}).get("manage"), k176["state"])
nope = run_job(st27, "KKLUENR999999", source=real_nav())
ev_nope = next((x.get("evidence") for x in (nope.get("discovery") or {}).get("steps") or []
                if x.get("step") == "ehub_record"), {}) or {}
check("A BOL/AWB the Under Clearance list does not carry: not opened, and it says why",
      nope["state"] in (S.SKIPPED, S.PDF_NOT_FOUND) and "is not listed under eHub's 'Under "
      "Clearance' filter" in nope["failure"]["detail"], nope.get("failure"))
check("...with the page kept as evidence: screenshot and text", ev_nope.get("screenshot")
      and Path(ev_nope["screenshot"]).exists() and Path(ev_nope["page_text"]).exists(), ev_nope)
st27b = new_store("s27b")
FILES["be905"] = pdf_of(boe_text(bl="KKLUENR260174", number="40926698905 / 00", invoice=None))
HITS.pop("file:be905", None)
noinv = st27b.create(doctypes.DEFAULT, "KKLUENR260174", {}, started_by="automatic")
noinv = P.process(st27b, noinv, real_nav(), CONFIG, sleep=NOSLEEP)
check("No explicit 'Invoice No.' on the Bill of Entry and none given: NEEDS_REVIEW — the UNA+ "
      "column (70076) is NOT used as a fallback, nothing generated",
      noinv["state"] == S.NEEDS_REVIEW and noinv["failure"]["code"] == "G4_SOURCE_UNPROVEN"
      and noinv["failure"]["g4_reason"] == "absent"
      and noinv["request_fields"]["invoice_no"]["status"] == "MISSING"
      and "70076" not in json.dumps(noinv["request_fields"]) and not noinv.get("output"),
      noinv["state"])
check("...and no PO_INVOICE_FROM switch exists any more",
      "invoice_from" not in P.config_from_env()
      and "PO_INVOICE_FROM" not in (HERE / "po" / "pipeline.py").read_text(encoding="utf-8"))
FILES["be905"] = pdf_of(boe_text(bl="KKLUENR260174", number="40926698905 / 00", invoice=None,
                                 extra="Invoice No: 2600005261\n"))
HITS.pop("file:be905", None)
st27c = new_store("s27c")
printed = st27c.create(doctypes.DEFAULT, "KKLUENR260174", {}, started_by="automatic")
printed = P.process(st27c, printed, real_nav(), CONFIG, sleep=NOSLEEP)
check("A Bill of Entry that PRINTS an explicit 'Invoice No: 2600005261': that is G4's source — "
      "filled from it, origin and evidence recorded, EMAIL READY",
      printed["state"] == S.EMAIL_PREPARED and printed["output"]["cells"]["G4"] == 2600005261
      and printed["request_fields"]["invoice_no"]["origin"] == "bill_of_entry"
      and "Invoice No: 2600005261" in printed["request_fields"]["invoice_no"]["evidence"],
      (printed["state"], printed["request_fields"]["invoice_no"]))
check("...and the provenance map names the Bill of Entry as G4's source",
      [m for m in printed["provenance_map"] if m["field"] == "invoice_no"][0]["source"]
      == "bill_of_entry")
# The job gives one number, the Bill of Entry prints another: no source is proven.
HITS.pop("file:be905", None)
st27d = new_store("s27d")
clash = st27d.create(doctypes.DEFAULT, "KKLUENR260174", {"invoice_no": "INV-999"},
                     started_by="omar")
clash = P.process(st27d, clash, real_nav(), CONFIG, sleep=NOSLEEP)
check("The job's invoice No. disagrees with the printed one: NEEDS_REVIEW (G4_SOURCE_UNPROVEN, "
      "conflict), nothing generated",
      clash["state"] == S.NEEDS_REVIEW and clash["failure"]["code"] == "G4_SOURCE_UNPROVEN"
      and clash["failure"]["g4_reason"] == "conflict" and not clash.get("output"),
      (clash["state"], clash.get("failure")))
clash, _p = P.supply(st27d, clash, {"invoice_no": "2600005261"}, by="omar", config=CONFIG)
check("...a person settles it at review by choosing the PRINTED value: G4 = 2600005261, origin "
      "'chosen at review', EMAIL READY",
      clash["state"] == S.EMAIL_PREPARED and clash["output"]["cells"]["G4"] == 2600005261
      and clash["request_fields"]["invoice_no"]["origin"] == "bill_of_entry:chosen_at_review",
      (clash["state"], clash["request_fields"]["invoice_no"]))
check("'Total Invoice Value (CIF)' and 'UNA+ Invoice Number' are never read as an invoice No.",
      X.printed_invoice_no("Total Invoice Value (CIF) USD 169,740.11\nUNA+ Invoice Number 70076")
      == {})
check("Two different printed invoice numbers: none chosen",
      X.printed_invoice_no("Invoice No: A1001\nCommercial Invoice Number: B2002")
      .get("candidates") == ["A1001", "B2002"])
FILES["be905"] = pdf_of(boe_text(bl="KKLUENR260174", number="40926698905 / 00", invoice=None))

rule("28. THE AUTOMATIC RUN: a job for every Under Clearance record that has none")
UIH2 = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")


def screen_rows(page):
    for r in EHUB:
        if r.get("layout") == "screen":
            yield {"bol_awb": r["bol"], "carrier": r["carrier"], "status": r["status"],
                   "una_invoice": r.get("una"), "table_page": 1, "view": "BU"}


real_rows = EH.ehub_rows
EH.ehub_rows = screen_rows
auto = new_store("s28")
ran = CLI.sweep(PAGE, auto, limit=10, log=lambda *a: None)
check("The sweep made one job per Under Clearance record, none for the Cleared one",
      sorted(r["reference"] for r in ran) == ["KKLUENR260174", "KKLUENR260175"],
      [(r["reference"], r["state"]) for r in ran])
check("...each found in the list's rows and processed by the real navigation, marked automatic",
      all((r.get("request") or {}).get("started_by") == "automatic"
          and (r.get("discovery") or {}).get("ehub_record", {}).get("view") == "BU" for r in ran))
check("...without an invoice number they wait in review (G4), nothing generated or sent",
      all(r["state"] == S.NEEDS_REVIEW and not r.get("output") for r in ran))
again = CLI.sweep(PAGE, auto, limit=10, log=lambda *a: None)
check("A second sweep starts nothing new: every record already has its job", again == [], again)
EH.ehub_rows = real_rows
from dashboard import supervisor as SUP                       # noqa: E402
os.environ["PO_AUTO"] = "0"
check("PO_AUTO=0 turns the automatic run off", SUP.Supervisor().start_po_sweep()[0] is False)
os.environ.pop("PO_AUTO", None)
check("Every ETA run starts it beside itself (Supervisor.start, unless PO_AUTO=0)",
      "self.start_po_sweep()" in (HERE / "dashboard" / "supervisor.py").read_text(encoding="utf-8")
      and "self.start_po_sweep()" in (HERE / "worker" / "agent.py").read_text(encoding="utf-8"))
cp = SV.PoService(store=new_store("s28cp"), launcher=lambda r: None)
ok, msg = cp.import_from_worker("w_auto", ran[0]["po_id"], {"record": ran[0],
                                                            "events": auto.events(ran[0]["po_id"])})
got = cp.store.get(ran[0]["po_id"]) or {}
check("The control plane takes a job the worker's automatic run created, as that worker's",
      ok and got.get("worker_id") == "w_auto" and got["state"] == S.NEEDS_REVIEW
      and (got.get("discovery") or {}).get("bill_entry"), (ok, msg, got.get("state")))
foreign = dict(ran[1], request={"started_by": "someone"})
ok2, _m = cp.import_from_worker("w_auto", ran[1]["po_id"], {"record": foreign, "events": []})
check("...but not a job it was never given that is not automatic", ok2 is False)

rule("28b. START PO AUTOMATION — the primary workflow; nothing to enter; pilot: no email")
# The PO page's primary action starts the automatic run itself — not one record.
calls = []


class RunLauncher(object):
    def __call__(self, record):
        calls.append(("one", record["po_id"]))

    def run_sweep(self, no_email=True):
        calls.append(("run", no_email))
        return True, "PO Automation started"


rs = SV.PoService(store=new_store("s28run"), launcher=RunLauncher(), config=CONFIG)
kind, status, body = W.handle(rs, "POST", "/api/po/run", {}, "omar.ops@mantrac.com",
                              lambda p: p == "po.process")
check("POST /api/po/run starts the WHOLE automatic run, with no record, invoice or supplier given",
      status == 200 and body["accepted"] and calls == [("run", True)], (status, body, calls))
check("...the pilot default: no email (every job stops at SAVED)", calls[-1][1] is True)
check("...it needs po.process, and is audited as PO_RUN_STARTED",
      W.permission_for("POST", "/api/po/run") == "po.process" and
      W.handle(rs, "POST", "/api/po/run", {}, "v", lambda p: False)[0] == "forbidden" and
      any(json.loads(l)["action"] == "PO_RUN_STARTED" for l in
          (rs.store.folder / "audit.jsonl").read_text().splitlines()))
check("GET /api/po/run reports whether it runs and the last run's list decisions",
      W.handle(rs, "GET", "/api/po/run", None, "v", lambda p: True)[1] == 200)
remote = []
wl = SV.WorkerLauncher(lambda r: "w1", lambda no_email: remote.append(no_email) or "w1")
check("Remote: the control plane sends the run to the worker as one `po_sweep` command",
      wl.run_sweep(no_email=True)[0] and remote == [True])
check("...and says so when no worker is online (nothing pretends to run)",
      SV.WorkerLauncher(lambda r: "w1", lambda n: (_ for _ in ()).throw(RuntimeError("none")))
      .run_sweep()[0] is False)
from worker import agent as AG                                   # noqa: E402
started = []


class FakeSup(object):
    po_sweep = None

    def start_po_sweep(self, env=None, explicit=False):
        started.append((env, explicit))
        return False, "test: not started"


fake = AG.Agent.__new__(AG.Agent)
fake.sup, fake.log = FakeSup(), (lambda *a: None)
fake.po_store = lambda: S.Store(folder=WORK / "agentpo")
os.environ["PO_AUTO"] = "0"
handled = fake.execute({"kind": "po_sweep", "payload": {"no_email": True}})
os.environ.pop("PO_AUTO", None)
check("The worker runs a `po_sweep` command even with PO_AUTO=0 (an explicit start), with "
      "PO_NO_EMAIL=1 and auto-send off",
      started and started[-1][1] is True and started[-1][0].get("PO_NO_EMAIL") == "1"
      and started[-1][0].get("PO_AUTO_SEND") == "0", (handled, started))
# The run itself, on the stand-in eHub list: no email anywhere.
FILES["be905"] = pdf_of(boe_text(bl="KKLUENR260174", number="40926698905 / 00"))
HITS.pop("file:be905", None)
EH.ehub_rows = screen_rows
pilot = new_store("s28pilot")
sends0 = GRAPH["counter"]
os.environ["PO_NO_EMAIL"] = "1"
ran_p = CLI.sweep(PAGE, pilot, limit=10, log=lambda *a: None)
os.environ.pop("PO_NO_EMAIL", None)
EH.ehub_rows = real_rows
by = {r["reference"]: r for r in ran_p}
check("Pilot run: every Under Clearance record processed from the list itself, the Cleared one "
      "skipped", sorted(by) == ["KKLUENR260174", "KKLUENR260175"], sorted(by))
check("...the one whose Bill of Entry prints its invoice No. stops at SAVED: output saved and "
      "read back, NO email prepared",
      by["KKLUENR260174"]["state"] == S.SAVED and by["KKLUENR260174"]["output"]["verified"]
      and by["KKLUENR260174"]["email"]["status"] == "BLOCKED"
      and "EMAIL_PREPARED" not in S.Store.milestones(by["KKLUENR260174"]),
      (by["KKLUENR260174"]["state"], by["KKLUENR260174"].get("email")))
check("...the one without an invoice No. waits in review — G4 is never guessed",
      by["KKLUENR260175"]["state"] == S.NEEDS_REVIEW, by["KKLUENR260175"]["state"])
check("...and not a single message was created in the mailbox",
      GRAPH["counter"] == sends0, GRAPH["counter"] - sends0)
check("...Send is refused for a pilot job (nothing was prepared)",
      P.send(pilot, by["KKLUENR260174"], M.GraphMailer(), by="omar")[1] == "BLOCKED")
sweeps = list((pilot.folder / "sweeps").glob("sweep-*.json"))
decided = json.loads(sweeps[0].read_text(encoding="utf-8")) if sweeps else {}
check("...every row of the list is decided and kept as evidence (sweeps/)",
      decided.get("counts", {}).get("ELIGIBLE") == 2 and
      decided.get("counts", {}).get("SKIPPED_NOT_UNDER_CLEARANCE"), decided.get("counts"))
check("The PO page's primary action is Start PO Automation; the one-record form is a labelled "
      "fallback with no invoice field",
      'id="poRun"' in UIH2 and "Start PO Automation" in UIH2 and "Manual fallback" in UIH2
      and 'name="invoice_no"' not in UIH2 and 'id="poNext"' not in UIH2)
A27.INTERNAL_URL = saved_url
A27.ensure_filtered_page = saved_nav

rule("25. SUCCESSFUL REAL DISCOVERY — only on a machine that reaches eHub")
# The test suite never touches the real eHub on its own: real verification
# is the worker's (python -m worker.verify). Opt in on the worker only.
real = DG.check(launch_browser=False, timeout=8) if os.environ.get("ATA_REAL_EHUB_TESTS") == "1" \
    else {"category": "NOT RUN", "failed_stage": "-", "reason": "the suite does not touch the real "
          "eHub unless ATA_REAL_EHUB_TESTS=1 on the Windows worker"}
if real.get("result") == "REACHABLE" and real.get("target") == "real eHub":
    code = "import json,sys;sys.path.insert(0,'.');from po.__main__ import main;sys.exit(main(['ehub-probe']))"
    run = subprocess.run([sys.executable, "-c", code], cwd=str(HERE), capture_output=True,
                         text=True, timeout=600)
    try:
        rep_ = json.loads(run.stdout[run.stdout.index("{"):])
    except Exception:
        rep_ = {}
    check("REAL eHub: an Under Clearance record → Manage → Documents → Bill Entry → identifier, "
          "observed in the real session",
          rep_.get("result") == "FOUND" and rep_.get("source") == "REAL"
          and rep_.get("verification") == "VERIFIED" and rep_.get("po_identifier"), rep_)
else:
    SKIP.append("successful real discovery")
    print("  SKIP  successful real discovery — the real eHub is not reachable here: {0} at {1}: "
          "{2}".format(real.get("category"), real.get("failed_stage"), real.get("reason")))

BROWSER.close()
PW.stop()
for srv_ in (hub_srv, graph_srv, httpd, srv):
    try:
        srv_.shutdown()
    except Exception:
        pass
print()
print("{0} passed, {1} failed, {2} skipped{3}".format(
    len(PASS), len(FAIL), len(SKIP), " (" + ", ".join(SKIP) + ")" if SKIP else ""))
sys.exit(1 if FAIL else 0)
