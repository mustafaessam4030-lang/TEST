"""
Stand-ins for the PO transaction-engine tests (test_po_engine.py) — usable
from the test process AND from the child processes it kills mid-job.

    boe_text(...) / pdf_of(text)   a Bill of Entry, as ICUMS words it
    Source(...)                    a MOCKED eHub source that reports every
                                   discovery step it took (its trail)
    GraphStub                      a stand-in Microsoft Graph over HTTP: drafts,
                                   send, Sent Items, read-back, scripted
                                   failures (429 + Retry-After, 503, a dropped
                                   connection after the send, a message that
                                   goes out different from the draft)

Nothing here is the real eHub or the real Graph: results from these are
TESTED WITH STAND-IN, never production evidence.
"""

import base64
import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = "graph-secret-for-tests-only"


def boe_text(bl="176-88452310", number="40726534505 / 00", duty="653,492.35",
             import_duty="246,320.05", rate="11.20", vat=None, extra="", invoice="9116093"):
    vat = vat if vat is not None else (
        "Import VAT                        304,446.44\n"
        "Network Charge VAT                  1,066.03\n"
        "Import NHIL                        50,741.08\n"
        "GETFund Import                     50,741.08\n"
        "Network Charge VAT Fund Levy          177.67\n")
    inv = "Invoice No: {0}\n".format(invoice) if invoice else ""
    return ("GHANA REVENUE AUTHORITY - CUSTOMS DIVISION\nICUMS BILL OF ENTRY / ASSESSMENT NOTICE\n"
            "Declaration No: {number}\nUser Reference: MTG-LOG-2026-0341\nBL/AWB No: {bl}\n"
            "Date of Assessment: 16/07/2026\nTotal Invoice Value (CIF) USD 169,740.11\n"
            "Exchange Rate {rate}\nImport Duty                       {imp}\n{vat}"
            "Total Duty and Levies GHS         {duty}\n{inv}{extra}").format(
                number=number, bl=bl, duty=duty, imp=import_duty, rate=rate, vat=vat, inv=inv,
                extra=extra)


def pdf_of(text):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), text, fontsize=9)
    return doc.tobytes()


class Source(object):
    """MOCKED eHub: one Bill of Entry on one Under Clearance row, with the
    trail a real discovery records (status seen, Manage, identity, the
    Documents section, the selection rule, the download)."""

    def __init__(self, data, ref="176-88452310", identifier="40726534505", on_manage=True,
                 page_shows=None, page_declaration=None, fail_times=0):
        self.data, self.ref, self.identifier = data, ref, identifier
        self.on_manage, self.page_shows = on_manage, page_shows
        self.page_declaration, self.fail_times, self.calls = page_declaration, fail_times, 0

    def fetch(self, reference):
        from po.pipeline import SourceError
        self.calls += 1
        if self.calls <= self.fail_times:
            raise SourceError("eHub answered 503 (transient)", "transient")
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        name = "BillofEntry_{0}.pdf".format(self.identifier)
        trail = {"steps": [{"step": s, "ok": True, "at": now} for s in (
                     "ehub_record", "clearance_status", "manage", "identity", "documents_section",
                     "bill_entry", "identifier", "download")],
                 "provenance": {"source": "TEST", "verification": "UNVERIFIED",
                                "why": "stand-in source"},
                 "clearance": {"required": "Under Clearance", "found": "Under Clearance",
                               "ok": True},
                 "ehub_record": {"bol_awb": self.ref, "status": "Under Clearance", "view": "BU"},
                 "manage": {"url": "https://ehub.test/manage/" + self.ref},
                 "identity": {"reference_on_page": self.on_manage, "status": "Under Clearance"},
                 "bill_entry": {"candidates": [{"name": name, "identifier": self.identifier}],
                                "selected": name, "identifier": self.identifier,
                                "rule": "the only document whose name starts with 'Bill Entry'"},
                 "download": {"method": "link", "bytes": len(self.data),
                              "content_type": "application/pdf"}}
        return {"data": self.data, "filename": name, "identifier": self.identifier,
                "origin": "ehub", "url": "https://ehub.test/f/" + name,
                "hub": {"bol_awb": self.ref, "status": "Under Clearance",
                        "identifier": self.identifier, "identity_on_manage": self.on_manage,
                        "identity_conflict": self.page_shows,
                        "manage_declaration": self.page_declaration},
                "trail": trail}


class GraphStub(object):
    """
    A stand-in Graph, over HTTP, in this process. state["plan"] scripts the
    next answers:
        "create": [429, ...]          an HTTP status for the next create(s)
        "send":   [503 | "drop" | "mutate" | 400, ...]
            503     not sent; answers 503 (an outcome Graph leaves unknown)
            "drop"  SENT, then the connection drops before any answer
            "mutate" SENT — to another recipient than the draft's
            400     not sent; a permanent error
    state["sent"] is Sent Items: every message that really went out.
    """

    def __init__(self):
        self.state = {"messages": {}, "sent": [], "plan": {"create": [], "send": []},
                      "tokens": set(), "counter": 0, "retry_after": 1}
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def handle(self):
                try:
                    BaseHTTPRequestHandler.handle(self)
                except (ConnectionResetError, BrokenPipeError, OSError):
                    pass

            def _json(self, status, body=None, headers=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("request-id", "req-{0}".format(time.time()))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _authed(self):
                auth = self.headers.get("Authorization") or ""
                return auth.startswith("Bearer ") and auth[7:] in stub.state["tokens"]

            def do_POST(self):
                st = stub.state
                path = urllib.parse.urlparse(self.path).path
                raw = self._body()
                if path.endswith("/oauth2/v2.0/token"):
                    form = urllib.parse.parse_qs(raw.decode())
                    if form.get("client_secret", [""])[0] != SECRET:
                        self._json(401, {"error": "invalid_client"})
                        return
                    token = "tok-{0}".format(len(st["tokens"]) + 1)
                    st["tokens"].add(token)
                    self._json(200, {"access_token": token, "expires_in": 3600})
                    return
                if not self._authed():
                    self._json(401, {"error": {"message": "InvalidAuthenticationToken"}})
                    return
                m = re.match(r"^/v1\.0/users/([^/]+)/messages$", path)
                if m:
                    if st["plan"]["create"]:
                        code = st["plan"]["create"].pop(0)
                        self._json(code, {"error": {"message": "planned"}},
                                   {"Retry-After": str(st["retry_after"])} if code == 429 else
                                   None)
                        return
                    body = json.loads(raw)
                    st["counter"] += 1
                    mid = "msg-{0}".format(st["counter"])
                    att = body["attachments"][0]
                    st["messages"][mid] = {
                        "id": mid, "internetMessageId": "<{0}@stub>".format(mid),
                        "subject": body["subject"], "mailbox": m.group(1),
                        "to": body["toRecipients"][0]["emailAddress"]["address"],
                        "attachment": att["name"],
                        "bytes": len(base64.b64decode(att["contentBytes"]))}
                    self._json(201, {"id": mid, "internetMessageId": "<{0}@stub>".format(mid)})
                    return
                m = re.match(r"^/v1\.0/users/([^/]+)/messages/([^/]+)/send$", path)
                if m:
                    mid = urllib.parse.unquote(m.group(2))
                    step = st["plan"]["send"].pop(0) if st["plan"]["send"] else None
                    if isinstance(step, int):
                        self._json(step, {"error": {"message": "planned"}},
                                   {"Retry-After": str(st["retry_after"])} if step in (429, 503)
                                   else None)
                        return
                    msg = st["messages"].pop(mid, None)
                    if msg is None:
                        self._json(404, {"error": {"message": "ErrorItemNotFound"}})
                        return
                    sent = dict(msg, id="sent-" + mid, sentDateTime=time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ"))
                    if step == "mutate":
                        sent["to"] = "someone.else@example.com"
                    st["sent"].append(sent)
                    if step == "drop":
                        self.close_connection = True
                        self.connection.shutdown(2)
                        return
                    self._json(202)
                    return
                self._json(404, {"error": {"message": "unknown"}})

            def do_GET(self):
                st = stub.state
                parsed = urllib.parse.urlparse(self.path)
                if not self._authed():
                    self._json(401, {"error": {"message": "InvalidAuthenticationToken"}})
                    return

                def find(mid):
                    if mid in st["messages"]:
                        return st["messages"][mid], True
                    return next((x for x in st["sent"] if x["id"] == mid), None), False
                m = re.match(r"^/v1\.0/users/([^/]+)/messages/([^/]+)/attachments$",
                             parsed.path)
                if m:
                    msg, _d = find(urllib.parse.unquote(m.group(2)))
                    if not msg:
                        self._json(404, {"error": {"message": "ErrorItemNotFound"}})
                        return
                    self._json(200, {"value": [{"name": msg["attachment"],
                                                "size": msg["bytes"] + 200}]})
                    return
                m = re.match(r"^/v1\.0/users/([^/]+)/messages/([^/]+)$", parsed.path)
                if m:
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
                    pool = st["sent"] if folder.group(1) == "SentItems" else \
                        list(st["messages"].values())
                    rows = [{"id": x["id"], "internetMessageId": x["internetMessageId"],
                             "subject": x["subject"], "sentDateTime": x.get("sentDateTime"),
                             "isDraft": folder.group(1) == "Drafts"} for x in pool
                            if (want and x["internetMessageId"] == want.group(1)) or
                            (subject and x["subject"] == subject.group(1).replace("''", "'"))]
                    self._json(200, {"value": rows})
                    return
                self._json(404, {"error": {"message": "unknown"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:{0}".format(self.server.server_address[1])

    def env(self):
        """The settings a GraphMailer needs to talk to this stand-in."""
        return {"GRAPH_TENANT_ID": "tenant", "GRAPH_CLIENT_ID": "client",
                "GRAPH_CLIENT_SECRET": SECRET, "GRAPH_BASE_URL": self.url + "/v1.0",
                "GRAPH_LOGIN_URL": self.url, "PO_MAIL_SENDER": "ata@mantrac.test"}

    def sent_for(self, attachment_prefix=None, subject=None):
        return [m for m in self.state["sent"]
                if (subject is None or m["subject"] == subject) and
                (attachment_prefix is None or m["attachment"].startswith(attachment_prefix))]

    def stop(self):
        self.server.shutdown()
