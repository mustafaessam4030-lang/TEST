"""
Email through Microsoft Graph — never the Outlook desktop UI.

    ATA backend → Microsoft Graph → the ATA mailbox → the recipient

Application permission Mail.Send on an app registration, restricted to the
ATA mailbox with an Exchange application access policy (see PO.md). The
secret comes from the environment, which App Service fills from Key Vault;
it is never written to a file, a log, an event or an exception.

THREE STEPS, each with its own evidence:

  1. create   POST /users/{mailbox}/messages            -> 201, message id
              and internetMessageId (the draft, attachment included)
  2. send     POST /users/{mailbox}/messages/{id}/send  -> 202 Accepted
  3. confirm  the message is in the mailbox's Sent Items, found by its
              internetMessageId

"Sent" is claimed only on 202, "confirmed" only when step 3 finds it AND
inspect() reads back that its recipient, subject and attachment are the
ones prepared.

Every call carries a client-request-id; Graph's request-id comes back with
it. Both are kept on the job (never the token).

Sending the SAME draft twice cannot produce two emails: after the first send
the draft no longer exists, so a retry of step 2 fails rather than
duplicating. A send whose outcome is unknown (the connection dropped) is
checked in Sent Items before any retry.
"""

import base64
import json
import os
import socket
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class MailError(Exception):
    """kind: 'transient' (retry may help), 'permanent' (it will not), 'unknown'
    (the send may or may not have happened — check before retrying)."""

    def __init__(self, message, kind="permanent", status=None, step=None):
        Exception.__init__(self, message)
        self.kind, self.status, self.step = kind, status, step


def configured():
    """(ok, missing) — the settings a Graph send needs, never their values."""
    need = {"GRAPH_TENANT_ID": tenant(), "GRAPH_CLIENT_ID": client_id(),
            "GRAPH_CLIENT_SECRET": bool(os.environ.get("GRAPH_CLIENT_SECRET") or
                                        os.environ.get("ENTRA_CLIENT_SECRET")),
            "PO_MAIL_SENDER": os.environ.get("PO_MAIL_SENDER")}
    missing = [k for k, v in need.items() if not v]
    return not missing, missing


def tenant():
    return os.environ.get("GRAPH_TENANT_ID") or os.environ.get("ENTRA_TENANT_ID")


def client_id():
    return os.environ.get("GRAPH_CLIENT_ID") or os.environ.get("ENTRA_CLIENT_ID")


class GraphMailer(object):
    def __init__(self, sender=None, graph=None, login=None, timeout=30):
        self.sender = sender or os.environ.get("PO_MAIL_SENDER")
        self.graph = (graph or os.environ.get("GRAPH_BASE_URL") or GRAPH).rstrip("/")
        self.login = (login or os.environ.get("GRAPH_LOGIN_URL") or LOGIN).rstrip("/")
        self.timeout = timeout
        self._token = None
        self._token_until = 0
        self.last = {}

    # -- HTTP ------------------------------------------------------------------
    def _call(self, method, url, body=None, headers=None, step=None, form=False):
        data = None
        headers = dict(headers or {})
        correlation = str(uuid.uuid4())
        headers["client-request-id"] = correlation
        headers["return-client-request-id"] = "true"
        self.last = {"step": step, "client_request_id": correlation, "request_id": None,
                     "status": None}
        if body is not None:
            if form:
                data = urllib.parse.urlencode(body).encode("utf-8")
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                self.last.update(status=response.status,
                                 request_id=response.headers.get("request-id"))
                return response.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as error:
            status = error.code
            self.last.update(status=status, request_id=error.headers.get("request-id")
                             if error.headers else None)
            try:
                detail = json.loads(error.read() or b"{}").get("error")
                detail = detail.get("message") if isinstance(detail, dict) else \
                    (detail if isinstance(detail, str) else None)
            except Exception:
                detail = None
            kind = "transient" if status in (408, 429, 500, 502, 503, 504) else "permanent"
            # A send whose answer was a gateway error may still have gone out.
            if step == "send" and status in (500, 502, 503, 504):
                kind = "unknown"
            err = MailError("Microsoft Graph returned {0} at {1}{2}".format(
                status, step, ": " + detail[:200] if detail else ""), kind, status, step)
            err.request_id = self.last.get("request_id")
            err.client_request_id = correlation
            # Throttling: Graph says how long to wait; the retry honours it.
            try:
                err.retry_after = min(120.0, float(error.headers.get("Retry-After") or 0))
            except (TypeError, ValueError, AttributeError):
                err.retry_after = 0
            raise err
        except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as error:
            reason = getattr(error, "reason", error)
            err = MailError("Microsoft Graph could not be reached at {0}: {1}".format(
                step, str(reason)[:160]), "unknown" if step == "send" else "transient",
                None, step)
            err.client_request_id = correlation
            raise err

    def token(self):
        if self._token and time.time() < self._token_until - 60:
            return self._token
        ok, missing = configured()
        if not ok:
            raise MailError("email is not configured: {0} not set".format(", ".join(missing)),
                            "permanent", None, "token")
        status, body = self._call(
            "POST", "{0}/{1}/oauth2/v2.0/token".format(self.login, tenant()),
            {"client_id": client_id(),
             "client_secret": os.environ.get("GRAPH_CLIENT_SECRET") or
             os.environ.get("ENTRA_CLIENT_SECRET"),
             "scope": "https://graph.microsoft.com/.default",
             "grant_type": "client_credentials"}, step="token", form=True)
        self._token = (body or {}).get("access_token")
        if not self._token:
            raise MailError("Microsoft identity returned no access token", "permanent", status,
                            "token")
        self._token_until = time.time() + int((body or {}).get("expires_in") or 600)
        return self._token

    def _auth(self):
        return {"Authorization": "Bearer " + self.token()}

    def _mailbox(self):
        return "{0}/users/{1}".format(self.graph, urllib.parse.quote(self.sender, safe="@."))

    # -- the three steps ---------------------------------------------------------
    def create(self, to, subject, text, attachment_name, attachment_bytes,
               content_type=XLSX):
        status, body = self._call("POST", self._mailbox() + "/messages", {
            "subject": subject,
            "body": {"contentType": "Text", "content": text},
            "toRecipients": [{"emailAddress": {"address": to}}],
            "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment",
                             "name": attachment_name, "contentType": content_type,
                             "contentBytes": base64.b64encode(attachment_bytes).decode("ascii")}],
        }, self._auth(), step="create")
        if status != 201 or not (body or {}).get("id"):
            raise MailError("the draft was not created (HTTP {0})".format(status), "permanent",
                            status, "create")
        return {"message_id": body["id"], "internet_message_id": body.get("internetMessageId"),
                "http_status": status, "request_id": self.last.get("request_id"),
                "client_request_id": self.last.get("client_request_id")}

    def send(self, message_id):
        status, _ = self._call("POST", "{0}/messages/{1}/send".format(
            self._mailbox(), urllib.parse.quote(message_id, safe="")), None, self._auth(),
            step="send")
        if status != 202:
            raise MailError("the send was not accepted (HTTP {0})".format(status), "unknown",
                            status, "send")
        return {"accepted": True, "http_status": status, "request_id": self.last.get("request_id"),
                "client_request_id": self.last.get("client_request_id")}

    def get_message(self, message_id):
        """The message by id ({'id', 'isDraft', ...}), or None when it no longer exists."""
        try:
            status, body = self._call("GET", "{0}/messages/{1}?$select=id,isDraft,sentDateTime,"
                                      "internetMessageId".format(
                                          self._mailbox(), urllib.parse.quote(message_id, safe="")),
                                      None, self._auth(), step="reconcile")
        except MailError as error:
            if error.status == 404:
                return None
            raise
        return body

    def find_sent(self, internet_message_id):
        """The message in Sent Items, or None. Read-only."""
        if not internet_message_id:
            return None
        query = urllib.parse.urlencode({
            "$filter": "internetMessageId eq '{0}'".format(internet_message_id.replace("'", "''")),
            "$select": "id,sentDateTime,internetMessageId,subject"})
        # (inspect() then reads the recipient and attachments of the row found.)
        status, body = self._call("GET", "{0}/mailFolders/SentItems/messages?{1}".format(
            self._mailbox(), query), None, self._auth(), step="confirm")
        rows = (body or {}).get("value") or []
        return rows[0] if rows else None

    def inspect(self, message_id):
        """
        What the message really is, read back from the mailbox: recipients,
        subject, sender and attachment names and sizes (never content).
        {"to": [...], "subject", "from", "attachments": [{"name", "size"}]}
        """
        quoted = urllib.parse.quote(message_id, safe="")
        status, body = self._call("GET", "{0}/messages/{1}?$select=id,subject,toRecipients,"
                                  "from,sentDateTime,internetMessageId".format(
                                      self._mailbox(), quoted), None, self._auth(),
                                  step="verify")
        status, att = self._call("GET", "{0}/messages/{1}/attachments?$select=name,size,"
                                 "contentType".format(self._mailbox(), quoted), None,
                                 self._auth(), step="verify")
        body = body or {}
        return {"to": [((r or {}).get("emailAddress") or {}).get("address")
                       for r in body.get("toRecipients") or []],
                "subject": body.get("subject"),
                "from": ((body.get("from") or {}).get("emailAddress") or {}).get("address"),
                "attachments": [{"name": a.get("name"), "size": a.get("size")}
                                for a in (att or {}).get("value") or []]}

    def find_by_attachment(self, folder, subject, attachment_name):
        """
        A message in Sent Items / Drafts that carries this job's attachment —
        for a crash before the message id was recorded. The attachment name is
        unique to the job (it ends with the job id). Read-only.
        """
        folder = {"sentitems": "SentItems", "drafts": "Drafts"}.get(folder, folder)
        query = urllib.parse.urlencode({
            "$filter": "subject eq '{0}'".format(str(subject or "").replace("'", "''")),
            "$select": "id,subject,sentDateTime,internetMessageId,isDraft", "$top": "10"})
        status, body = self._call("GET", "{0}/mailFolders/{1}/messages?{2}".format(
            self._mailbox(), folder, query), None, self._auth(), step="reconcile")
        for row in (body or {}).get("value") or []:
            names = [a["name"] for a in self.inspect(row["id"])["attachments"]]
            if attachment_name in names:
                return row
        return None

    def confirm(self, internet_message_id, wait_s=30, every_s=2.0):
        """Poll Sent Items until the message appears or the wait ends."""
        deadline = time.time() + wait_s
        while True:
            found = self.find_sent(internet_message_id)
            if found:
                return found
            if time.time() >= deadline:
                return None
            time.sleep(every_s)
