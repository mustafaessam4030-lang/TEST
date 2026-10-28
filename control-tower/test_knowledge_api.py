"""
The Knowledge Engine over HTTP: who may read, propose, review and ingest,
and a knowledge question asked through ATLAS's chat endpoint.

    python test_knowledge_api.py
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
TMP = tempfile.mkdtemp(prefix="kbapi_")
os.environ.update({
    "DASHBOARD_ACCOUNTS_DB": os.path.join(TMP, "accounts.db"), "DASHBOARD_ACCOUNTS": "auto",
    "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"), "PO_DATA_DIR": os.path.join(TMP, "po"),
    "PO_AUTO": "0", "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key"),
    "ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
    "ATLAS_AI": "0",
})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

from dashboard import accounts  # noqa: E402
from dashboard import server as tower_server  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "" if condition or not detail else "  -- " + str(detail)[:240]))


acc = accounts.store()
PW = "Knowledge-Test-2026!"
for email, name, role in (("admin@mantrac.example", "Ama Admin", "ADMIN"),
                          ("admin2@mantrac.example", "Abena Admin", "ADMIN"),
                          ("oper@mantrac.example", "Kofi Operator", "OPERATOR"),
                          ("view@mantrac.example", "Esi Viewer", "VIEWER")):
    acc.users.create(email, name, role, password=PW)
s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1", access_key="knowledge-key-123456")
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(opener, method, path, body=None, csrf=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Origin", BASE)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if csrf:
        req.add_header("X-CSRF-Token", csrf)
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def sign_in(email):
    jar = http.cookiejar.CookieJar()
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect())
    st, b = call(o, "POST", "/api/auth/login", {"email": email, "password": PW})
    return o, b.get("csrf")


print("\nREADING")
V, Vc = sign_in("view@mantrac.example")
st, b = call(V, "GET", "/api/knowledge/search?q=demurrage")
check("A Viewer can search the knowledge", st == 200 and b["results"][0]["id"] == "C.demurrage", (st, b))
st, b = call(V, "GET", "/api/knowledge/item?id=C.incoterm-fob")
check("…and read an item with its citations and graph",
      st == 200 and b["item"]["term"].startswith("FOB") and b["citations"][0]["id"] == "icc-incoterms-2020" and b["related"])
st, b = call(V, "GET", "/api/knowledge/stats")
check("…and the statistics, but not the reviewers' retrieval log", st == 200 and b["knowledge"]["items"] >= 150
      and "retrieval" not in b)
st, _b = call(V, "GET", "/api/knowledge/proposals")
check("A Viewer cannot see proposals", st == 403, st)
st, _b = call(V, "POST", "/api/knowledge/propose", {"kind": "observation", "observation": "A viewer's note about KLM."}, Vc)
check("A Viewer cannot propose", st == 403, st)
st, b = call(urllib.request.build_opener(NoRedirect()), "GET", "/api/knowledge/search?q=demurrage")
check("Signed out, nothing", st in (302, 401, 403), st)

print("\nPROPOSING AND REVIEWING")
O, Oc = sign_in("oper@mantrac.example")
item = {"id": "B.reefer-plug-in", "kind": "term", "domain": "B", "category": "equipment", "difficulty": 2,
        "term": "Reefer plug-in charge", "terms_ar": ["رسوم توصيل الحاوية المبردة"],
        "definition": "A reefer plug-in charge is what a terminal bills per day for powering a refrigerated container.",
        "definition_ar": "رسوم يومية تحصلها المحطة لتشغيل الحاوية المبردة بالكهرباء.",
        "evidence_status": "general_knowledge", "scope": "general"}
st, b = call(O, "POST", "/api/knowledge/propose", {"item": item}, Oc)
check("An Operator proposes", st == 200 and b["ok"], b)
pid = b["result"]["proposal_id"]
st, _b = call(O, "POST", "/api/knowledge/review", {"proposal_id": pid, "decision": "approve"}, Oc)
check("…but cannot review", st == 403, st)
A1, A1c = sign_in("admin@mantrac.example")
st, b = call(A1, "GET", "/api/knowledge/proposals")
check("An Admin sees the pending proposal with its checks",
      st == 200 and any(p["proposal_id"] == pid and p["status"] == "pending" and "checks" in p for p in b["proposals"]))
st, b = call(A1, "POST", "/api/knowledge/review", {"proposal_id": pid, "decision": "approve", "note": "ok"}, A1c)
check("An Admin approves another person's proposal", st == 200 and b["ok"], b)
st, b = call(A1, "GET", "/api/knowledge/item?id=B.reefer-plug-in")
check("…it is approved knowledge, with its history", b["item"]["review_status"] == "approved"
      and [h["action"] for h in b["history"]] == ["propose", "review"])
st, b = call(A1, "POST", "/api/knowledge/propose", {"kind": "observation", "observation": "Admin note for the record."}, A1c)
own = b["result"]["proposal_id"]
st, b = call(A1, "POST", "/api/knowledge/review", {"proposal_id": own, "decision": "approve", "note": "x"}, A1c)
check("Nobody approves their own proposal, over the API too", st == 200 and not b["ok"] and "another reviewer" in b["message"], b)
st, b = call(A1, "POST", "/api/knowledge/status", {"id": "C.gh-cif-valuation", "status": "approved"}, A1c)
check("A reviewer approves a repository item as Mantrac's position", b.get("ok"), b)
st, b = call(A1, "POST", "/api/knowledge/status", {"id": "B.cross-docking", "status": "retired"}, A1c)
check("Retiring without a reason is refused", not b.get("ok") and "say why" in b.get("message", ""), b)

print("\nINGESTING")
sop = "# Clearance SOP\n## Terms\nPre-alert: the forwarder's advance notice of a shipment with copies of the documents.\n"
st, b = call(O, "POST", "/api/knowledge/ingest", {"name": "sop.md", "text": sop}, Oc)
check("An Operator cannot ingest", st == 403, st)
st, b = call(A1, "POST", "/api/knowledge/ingest", {"name": "sop.md", "text": sop, "title": "Clearance SOP"}, A1c)
check("An Admin ingests: a proposal per definition, with its location",
      b.get("ok") and b["result"]["proposals"][0]["provenance"]["location"] == "sop.md › Terms, line 3", b)
st, b = call(A1, "GET", "/api/knowledge/stats")
check("Reviewers see retrieval quality: queries, hit rate, latency, recent misses",
      "retrieval" in b and ("hit_rate" in b["retrieval"] or b["retrieval"].get("queries") == 0), b.get("retrieval"))

print("\nATLAS")
st, b = call(V, "POST", "/api/ask", {"question": "Under FOB who pays the ocean freight?"}, Vc)
check("A knowledge question through ATLAS's chat gets the cited, labelled answer",
      st == 200 and b.get("intent") == "general_knowledge" and "iccwbo.org" in b.get("answer", "")
      and "_Knowledge status" in b.get("answer", ""), (st, str(b)[:300]))
st, b = call(V, "POST", "/api/ask", {"question": "what is a reefer plug-in charge"}, Vc)
check("…an approved item is answered as approved", "approved by a Mantrac reviewer" in b.get("answer", ""), b.get("answer", "")[:300])
st, b = call(V, "POST", "/api/ask", {"question": "what is the past due share"}, Vc)
check("…and 'in your data' respects the role (a Viewer may open Clearance)",
      "cannot open the Clearance page" not in b.get("answer", ""), b.get("answer", "")[:300])

print("\n{0} passed, {1} failed".format(len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
