"""
ATLAS conversations kept per signed-in person, for Admins only
(dashboard/conversations.py): what is saved, what is masked or not kept,
who may read it, the notice in the chat window, retention, and the audit
entry for every look. A real tower with personal sign-in.

    python test_atlas_conversations.py
"""
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
TMP = tempfile.mkdtemp(prefix="atlas_conv_")
os.environ.update(DASHBOARD_ACCOUNTS_DB=os.path.join(TMP, "accounts.db"), DASHBOARD_ACCOUNTS="auto",
                  ATLAS_INTEL_DIR=os.path.join(TMP, "intel"), PO_DATA_DIR=os.path.join(TMP, "po"),
                  PO_AUTO="0", DASHBOARD_ACCESS_KEY_FILE=os.path.join(TMP, "access_key"),
                  ATA_CONVERSATIONS_FILE=os.path.join(TMP, "conv.jsonl"))
os.environ.pop("ATA_CONVERSATION_DAYS", None)

from dashboard import accounts, conversations              # noqa: E402
from dashboard import server as tower_server               # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(str(detail)[:300])))


acc = accounts.store()
PW = "Tower-Pilot-2026!"
acc.users.create("admin@mantrac.example", "Ama Admin", "ADMIN", password=PW)
acc.users.create("oper@mantrac.example", "Kofi Operator", "OPERATOR", password=PW)
acc.users.create("view@mantrac.example", "Nourine Viewer", "VIEWER", password=PW)
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1", access_key="conv-key-123456")
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


def call(opener, method, path, body=None, csrf=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Origin", BASE)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if csrf:
        req.add_header("X-CSRF-Token", csrf)
    try:
        with opener.open(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except ValueError:
            return e.code, {}


def sign_in(email):
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    st, d = call(o, "POST", "/api/auth/login", {"email": email, "password": PW})
    return o, d.get("csrf")


view, vc = sign_in("view@mantrac.example")
st, me = call(view, "GET", "/api/auth/me")
check("the chat is told conversations are kept, and for how long",
      me.get("conversations") == {"saved": True, "days": 90}, me.get("conversations"))
call(view, "POST", "/api/ask", {"question": "How many shipments are left in this run?"}, vc)
call(view, "POST", "/api/ask", {"question": "my hub password is Hunter-2026x, why does login fail?"}, vc)
call(view, "POST", "/api/ask", {"question": "how do I change my password for the Hub?"}, vc)
oper, oc = sign_in("oper@mantrac.example")
call(oper, "POST", "/api/ask", {"question": "Which shipments need a person?"}, oc)

adm, ac = sign_in("admin@mantrac.example")
st, d = call(adm, "GET", "/api/admin/conversations")
rows = d.get("conversations") or []
check("an Admin sees everyone's questions, newest first", st == 200 and len(rows) == 4
      and rows[0]["email"] == "oper@mantrac.example", (st, len(rows)))
viewer_rows = [r for r in rows if r["email"] == "view@mantrac.example"]
check("each row names the person and carries ATLAS's answer",
      viewer_rows and viewer_rows[-1]["name"] == "Nourine Viewer" and viewer_rows[-1]["answer"], viewer_rows[-1:])
st, d = call(adm, "GET", "/api/admin/conversations?user=view@mantrac.example")
check("filter by one person", st == 200 and {r["email"] for r in d["conversations"]} == {"view@mantrac.example"})
raw = open(os.environ["ATA_CONVERSATIONS_FILE"], encoding="utf-8").read()
check("a password typed into a question is masked before it is written",
      "Hunter-2026x" not in raw and "password is" not in raw.split("Hunter")[0][-0:] and "[hidden]" in raw)
check("an ordinary question about passwords is kept as asked",
      "how do I change my password for the Hub?" in raw)
check("an Operator cannot read conversations", call(oper, "GET", "/api/admin/conversations")[0] == 403)
check("a Viewer cannot read conversations", call(view, "GET", "/api/admin/conversations")[0] == 403)
anon = urllib.request.build_opener()
check("nobody signed out can", call(anon, "GET", "/api/admin/conversations")[0] == 401)
events = acc.audit.list(limit=50, action="VIEW_CONVERSATIONS")
check("every look at conversations is in the audit log, with whose",
      len(events) >= 2 and any(e.get("target_id") == "view@mantrac.example" for e in events), events[:2])

conversations.record({"user_id": "u", "email": "x@y.z"}, "send me the code 482913",
                     {"intent": "code_request", "answer": "I never handle verification codes."})
raw = open(os.environ["ATA_CONVERSATIONS_FILE"], encoding="utf-8").read()
check("a question about a carrier verification code is not kept", "482913" not in raw and "not kept" in raw)

old = {"ts": int(time.time()) - 100 * 86400, "email": "old@y.z", "question": "old", "answer": "old"}
with open(os.environ["ATA_CONVERSATIONS_FILE"], "a", encoding="utf-8") as f:
    f.write(json.dumps(old) + "\n")
check("older than the retention: not shown", all(r["email"] != "old@y.z" for r in conversations.list_()))
conversations._prune(conversations.path())
check("...and removed from the file", "old@y.z" not in open(os.environ["ATA_CONVERSATIONS_FILE"]).read())

os.environ["ATA_CONVERSATION_DAYS"] = "0"
before = open(os.environ["ATA_CONVERSATIONS_FILE"]).read()
call(view, "POST", "/api/ask", {"question": "Is anything failing?"}, vc)
check("ATA_CONVERSATION_DAYS=0: nothing is recorded", open(os.environ["ATA_CONVERSATIONS_FILE"]).read() == before)
st, me = call(view, "GET", "/api/auth/me")
check("...and the chat no longer says it is kept", me["conversations"]["saved"] is False)
os.environ.pop("ATA_CONVERSATION_DAYS")

st, r = call(view, "POST", "/api/ask", {"question": "How do I add the DNS record for the server?"}, vc)
check("through the tower, a Viewer gets no server details",
      st == 200 and "man-dcaz" not in (r.get("answer") or "") and r.get("intent") == "setup_admin_only", r.get("intent"))
st, r = call(adm, "POST", "/api/ask", {"question": "How do I add the DNS record for the server?"}, ac)
check("...an Admin gets the lesson", st == 200 and "man-dcaz" in (r.get("answer") or ""), (r.get("answer") or "")[:120])

html = open(os.path.join(HERE, "dashboard/static/index.html"), encoding="utf-8").read()
check("the page has the Admin card and the chat notice",
      'id="acConvCard"' in html and 'id="chSaved"' in html and "/api/admin/conversations" in html)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
