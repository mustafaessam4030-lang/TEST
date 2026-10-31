"""
Missions on the domain: the tower as it runs behind IIS at https://ata.mantracgroup.com (ATA_PUBLIC_URL, ATA_TRUST_PROXY=1),
with personal sign-in. Admin, Operator and Viewer sign in; every POST carries the session's CSRF token;
the cookie is Secure (IIS terminates https, so it is sent by hand here). Missions are proposed, approved,
followed on the live stream, and their action cards are approved through the real Clearance escalation
(in a temporary runtime folder). Permissions, To Do and the audit log are checked. No model is billed.

    python test_missions_domain.py
"""
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
TMP = tempfile.mkdtemp(prefix="missions_domain_")
os.environ.update(DASHBOARD_ACCOUNTS_DB=os.path.join(TMP, "accounts.db"), DASHBOARD_ACCOUNTS="auto",
                  ATLAS_INTEL_DIR=os.path.join(TMP, "intel"), PO_DATA_DIR=os.path.join(TMP, "po"), PO_AUTO="0",
                  DASHBOARD_ACCESS_KEY_FILE=os.path.join(TMP, "access_key"), ATA_RUNTIME_DIR=os.path.join(TMP, "rt"),
                  ATA_CLEARANCE_INBOX=os.path.join(TMP, "inbox"), ATA_CONVERSATIONS_FILE=os.path.join(TMP, "conv.jsonl"),
                  ATA_PUBLIC_URL="https://ata.mantracgroup.com", ATA_TRUST_PROXY="1", ATLAS_AI="0", ATLAS_MISSION_ACTIONS="1")
for k in ("ATLAS_MISSION_POLICIES", "ATLAS_MISSION_PLANNER", "ATLAS_COUNCIL_SIZE"):
    os.environ.pop(k, None)
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"], exist_ok=True)
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(str(detail)[:400])))


from dashboard import accounts, clearance as C          # noqa: E402


def row(bu, step, awb, overdue, agent="AGENT X"):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, awb=awb, overdue=overdue, agent=agent, eta="2026-09-01", ata="2026-09-06", mode="Air",
             status="InProgress", due="2026-10-01", ffw="Ceva")
    return r


ROWS = [row("EG", "Customs Declaration", "DM-00001", 45.0)] + [row("EG", "Customs Declaration", "DM-%05d" % i, 20.0) for i in range(2, 16)] + \
       [row("GH", "Customs Release", "DM-9%04d" % i, 3.0) for i in range(5)]
DATA = {"asof": time.strftime("%Y-%m-%d"), "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS], "dq": {}}
C.load = lambda: DATA

from intelligence.missions import models  # noqa: E402
models.PROVIDERS["cortex"] = lambda mid: None            # no billed model: missions run in Drill
from dashboard import server as tower                    # noqa: E402

acc = accounts.store()
PW = "Tower-Pilot-2026!"
acc.users.create("admin@mantrac.example", "Ama Admin", "ADMIN", password=PW)
acc.users.create("oper@mantrac.example", "Kofi Operator", "OPERATOR", password=PW)
acc.users.create("view@mantrac.example", "Nourine Viewer", "VIEWER", password=PW)
s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
tower.start(port=PORT, open_browser=False, host="127.0.0.1", access_key="domain-key-123456")
BASE = "http://127.0.0.1:{0}".format(PORT)
ORIGIN = "https://ata.mantracgroup.com"
time.sleep(0.5)


class Session(object):
    """A browser behind IIS: https to the domain, so the Secure cookie is sent; IIS adds X-Forwarded-For."""
    def __init__(self):
        self.cookies, self.csrf = {}, None

    def call(self, method, path, body=None, csrf=True, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method)
        req.add_header("Origin", ORIGIN)
        req.add_header("Host", "ata.mantracgroup.com")      # IIS keeps the Host (preserveHostHeader)
        req.add_header("X-Forwarded-For", "10.20.30.40")
        req.add_header("X-Forwarded-Proto", "https")
        if self.cookies:
            req.add_header("Cookie", "; ".join("{0}={1}".format(k, v) for k, v in self.cookies.items()))
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if csrf and self.csrf and method != "GET":
            req.add_header("X-CSRF-Token", self.csrf)
        try:
            r = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            r = e
        for h in r.headers.get_all("Set-Cookie") or []:
            k, v = h.split(";")[0].split("=", 1)
            self.cookies[k.strip()] = v.strip()
            if "secure" in h.lower():
                self.secure = True
        if raw:
            return r
        body = r.read()
        try:
            return r.status if hasattr(r, "status") else r.code, json.loads(body or b"{}")
        except ValueError:
            return getattr(r, "status", getattr(r, "code", 0)), {}


def sign_in(email):
    s = Session()
    st, d = s.call("POST", "/api/auth/login", {"email": email, "password": PW})
    s.csrf = d.get("csrf")
    return s, st


print("\n1. SIGN-IN BEHIND THE DOMAIN")
admin, st_a = sign_in("admin@mantrac.example")
oper, st_o = sign_in("oper@mantrac.example")
view, st_v = sign_in("view@mantrac.example")
check("Admin, Operator and Viewer sign in and get a CSRF token", st_a == st_o == st_v == 200 and admin.csrf and oper.csrf and view.csrf,
      (st_a, st_o, st_v))
check("The session cookie is Secure (the domain is https)", getattr(admin, "secure", False))
anon = Session()
st, _ = anon.call("GET", "/api/missions")
check("Not signed in: Missions are refused", st in (401, 403), st)

print("\n2. MISSIONS WITH REAL PERMISSIONS")
st, lst = view.call("GET", "/api/missions")
check("A Viewer sees Missions, and that they may not run them", st == 200 and lst["can"]["run"] is False, (st, lst.get("can")))
st, d = oper.call("POST", "/api/missions/propose", {"question": "Why is DM-00001 stuck at customs? investigate the delay"}, csrf=False)
check("A POST without the CSRF token is refused", st == 403, (st, d))
st, d = oper.call("POST", "/api/missions/propose", {"question": "Why is DM-00001 stuck at customs? investigate the delay"})
mid = (d.get("mission") or {}).get("id")
check("An Operator proposes a mission", st == 200 and mid, (st, d))
st, d = view.call("POST", "/api/missions/{0}/approve".format(mid), {"profile": "drill"})
check("A Viewer cannot approve it (missions.run)", st == 403, (st, d))
st, d = oper.call("POST", "/api/missions/{0}/approve".format(mid), {"profile": "drill", "remove": ["review"], "deadline_min": 5})
check("The Operator approves it, with an edited plan", st == 200 and d.get("ok"), (st, d))
r = oper.call("GET", "/api/missions/{0}/stream".format(mid), raw=True)
first = r.readline().decode() + r.readline().decode()
r.close()
check("The live stream works for a signed-in person", first.startswith("event: mission") and mid in first, first)
r = anon.call("GET", "/api/missions/{0}/stream".format(mid), raw=True)
check("...and not for anyone else", getattr(r, "code", getattr(r, "status", 0)) in (401, 403))
end = time.time() + 90
m = {}
while time.time() < end:
    st, m = oper.call("GET", "/api/missions/" + mid)
    if m.get("status") not in ("queued", "running"):
        break
    time.sleep(0.5)
check("It runs to a graded report", m.get("status") in ("done", "insufficient") and m.get("report"), m.get("status"))
esc = next((a for a in m.get("actions") or [] if a["kind"] == "escalate"), None)
check("A checked finding makes an escalation card for DM-00001", esc and esc["args"]["awb"] == "DM-00001" and esc["status"] == "proposed",
      [(a["kind"], a["status"]) for a in m.get("actions") or []])

print("\n3. APPROVING AN ACTION, AUDITED")
st, d = view.call("POST", "/api/missions/{0}/actions/{1}/approve".format(mid, esc["id"]), {})
check("A Viewer cannot approve the escalation (clearance.escalate)", st == 403, (st, d))
st, todo = oper.call("GET", "/api/todo")
check("The Operator finds it in To Do", st == 200 and any(i["id"] == "mact:" + esc["id"] for i in todo.get("items") or []),
      [i["id"] for i in todo.get("items") or []][:10])
st, d = oper.call("POST", "/api/missions/{0}/actions/{1}/approve".format(mid, esc["id"]), {})
after = next((a for a in (d.get("mission") or {}).get("actions") or [] if a["id"] == esc["id"]), {})
check("The Operator approves it: the Clearance page's own escalation opens and is read back",
      st == 200 and d.get("ok") and after.get("status") == "verified" and any(e["awb"] == "DM-00001" and e["status"] == "open" for e in C.escalations()),
      (st, d.get("message"), after.get("status")))
check("It is recorded as opened by the Operator", any(e["awb"] == "DM-00001" and e["by"] == "oper@mantrac.example" for e in C.escalations()),
      C.escalations())
st, d = oper.call("POST", "/api/missions/{0}/actions/{1}/approve".format(mid, esc["id"]), {})
check("Approving it again is refused (never twice)", not d.get("ok"), d)
rows = [dict(r) for r in acc.audit.list(limit=500)]
acts = [str(r.get("action")) for r in rows]
mine = [r for r in rows if str(r.get("action")).startswith(("MISSION_ACTION_APPROVED", "ESCALATE_SHIPMENT"))]
check("The audit log has the mission approval, the action approval and the escalation",
      "MISSION_APPROVE" in acts and "MISSION_ACTION_APPROVED" in acts and "ESCALATE_SHIPMENT" in acts, sorted(set(acts))[:20])
check("...each with the person and the address IIS forwarded", mine and all(
      "oper@mantrac.example" in json.dumps(r, default=str) and "10.20.30.40" in json.dumps(r, default=str) for r in mine), mine[:2])

print("\n4. THE HUB PAGE BEHIND THE DOMAIN")
st, _ = admin.call("GET", "/hub")
st2, _ = oper.call("GET", "/hub")
check("The hub (with Mission Control) loads for an Admin; it is still an Admin-only preview (hub.preview)", st == 200 and st2 == 403, (st, st2))
st, reg = oper.call("GET", "/api/missions/registry")
check("Mission Control can read the model registry and the tool catalogue", st == 200 and reg.get("tools") and "actions_enabled" in reg)
st, an = admin.call("GET", "/api/missions/analytics")
check("Analytics for the Admin", st == 200 and an.get("missions") >= 1)
st, pol = oper.call("GET", "/api/missions/policies")
check("Level 4 policies are shown to Admins only", st == 403)

print("\n5. THE LAUNCH PAGE AND THE PROXY'S ORIGIN RULE")
st, _ = anon.call("GET", "/launch")
check("/launch loads for a signed-out visitor", st == 200, st)
bad = Session(); bad.cookies, bad.csrf = dict(oper.cookies), oper.csrf
req = urllib.request.Request(BASE + "/api/missions/propose", data=json.dumps({"question": "x"}).encode(), method="POST")
for k, v in (("Origin", "https://evil.example"), ("Host", "ata.mantracgroup.com"), ("Content-Type", "application/json"),
             ("X-CSRF-Token", oper.csrf), ("Cookie", "; ".join("{0}={1}".format(k2, v2) for k2, v2 in oper.cookies.items()))):
    req.add_header(k, v)
try:
    code = urllib.request.urlopen(req, timeout=20).status
except urllib.error.HTTPError as e:
    code = e.code
check("A POST from another site (wrong Origin) is refused, even with the cookie and token", code == 403, code)

js = open(os.path.join(HERE, "controlplane", "static", "login", "login.js"), encoding="utf-8").read()
check("Sign-in keeps the part after # (a mission link from ATLAS lands on the mission, not the Overview)",
      "location.hash" in js and "safe + h" in js)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
