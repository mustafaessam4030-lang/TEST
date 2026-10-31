"""
ATLAS's read-only platform tools: numbers only from the tools (counted with
the hub's own rules), "no data" said plainly, English and Arabic, the
person's BUs and role respected, nothing written, no question text logged,
and the hub telling the chat which page and list are on screen.

    python test_atlas_tools.py
"""
import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="atlas_tools_")
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "DASHBOARD_ACCOUNTS": "0", "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"),
                   "PO_DATA_DIR": os.path.join(TMP, "po"), "PO_AUTO": "0",
                   "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key")})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

PASSED, FAILED, SKIPPED = [], [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:400]))


from dashboard import clearance as C, hub as H, atlas_tools as A, server as S  # noqa: E402


def row(bu, step, awb, eta=None, ata=None, overdue=None, ontime=1):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, eta=eta, ata=ata, overdue=overdue, ontime=ontime, awb=awb, mode="Air", status="x",
             due="2026-10-01")
    return r


ROWS = [row("EG", "Submit to BU", "EG-0001", "2026-10-01", None, 12.0, 0),
        row("EG", "Customs Declaration", "EG-0002", "2026-10-02", "2026-10-03", 3.0, 0),
        row("EG", "Customs Declaration", "EG-0003", "2026-11-20", None, None, 1),     # customs step, not arrived: in transit
        row("EG", "Awaiting Reciept", "EG-0004", "2026-09-01", "2026-09-02", None, 1),
        row("GH", "Completed", "GH-0001", "2026-09-01", "2026-09-02", None, 1),
        row("GH", "Assign Clearing Agent", "GH-0002", "2026-10-10", None, 40.0, 0),
        row("GH", "Customs Pre Entry", "GH-0003", "2026-11-01", None, None, 1)]
DATA = {"asof": "2026-10-26", "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS], "dq": {}}
C.load = lambda: DATA
LOADED = {"on": True}
_real_load = C.load
C.load = lambda: DATA if LOADED["on"] else None
C.escalations = lambda: []
STAGES = {s["id"]: s["count"] for s in H.stages(DATA)["stages"]}


def ask(q, **ctx):
    return A.answer(q, {}, ctx)


print("\n1. NUMBERS ONLY FROM THE TOOLS, WITH THE HUB'S RULES")
for q, sid in (("How many shipments are in transit?", "transit"), ("How many shipments are at customs?", "customs"),
               ("How many shipments are cleared and awaiting delivery?", "awaiting"),
               ("How many shipments are completed?", "done")):
    a = ask(q)
    check("'{0}' -> the hub's {1} count ({2})".format(q, sid, STAGES[sid]),
          a and "**{0}**".format(STAGES[sid]) in a["answer"] and "list=" + sid in a["answer"], a and a["answer"])
a = ask("How many shipments are at customs?")
check("At customs says how many more are at a customs step but not arrived (counted In transit, as on the hub)",
      "2 more are at a customs step" in a["answer"], a["answer"])
a = ask("How many shipments are past due?")
check("Past due: open with Performance above 0 (3 of 6 open)", "**3** shipments are past due, of 6 open" in a["answer"], a["answer"])
a = ask("Which BU has the most past due shipments?")
check("Which BU has the most past due: EG 2 of 3, then GH", "**EG**" in a["answer"] and "**2** of 3" in a["answer"]
      and "GH (1)" in a["answer"], a["answer"])
a = ask("What is the on-time rate for EG?")
check("On time for EG: 2 of 4 = 50.0%", "**50.0%** (2 of 4" in a["answer"], a["answer"])
a = ask("How many shipments does GH have?")
check("A BU's total, open and past due", "**3** shipments for GH: 2 open, 1 of them past due" in a["answer"], a["answer"])
check("Every answer says where its numbers come from and links to the rows behind them",
      "Source: eHub export, data as of 26 Oct 2026" in a["answer"] and "[Open the list](/hub#overview?list=all&bu=GH)" in a["answer"])
check("Every answer is marked with the tools it used", a["tools"] == ["stages"] and a["grounded"])

print("\n2. WHAT A NUMBER MEANS: THE DEFINITION FIRST, FROM THE HUB")
a = ask("How is past due counted?")
check("How is past due counted: the definition leads, then the number now",
      a["answer"].startswith("**How it's counted:** Open (not Completed)") and "Now: **3**" in a["answer"], a["answer"])
a = ask("What does 'Cleared, awaiting delivery' mean?")
defn = next(d for i, _l, _t, d in H.STAGES if i == "awaiting")
check("A stage's meaning is hub.STAGES' own definition (one source)", defn in a["answer"], a["answer"])
a = ask("Explain this number", page="hub")
check("'Explain this number' on the hub's Overview: the stage strip, each with its count and definition",
      all("**{0}** — {1}".format(STAGES[i], l) in a["answer"] for i, l, _t, _d in H.STAGES), a["answer"])
a = ask("Explain this number", page="hub", view={"stage": "pastdue"})
check("...with the past-due list open: what past due means", a["answer"].startswith("**How it's counted:** Open (not"), a["answer"])
a = ask("Explain this number", page="hub", view={"stage": "customs", "bu": "GH"})
check("...with one BU's list open: that BU's number (GH at customs: 0), not everyone's",
      "Now: **0** shipments are at customs in GH" in a["answer"], a["answer"])
check("'Explain this number' with no page to go on is left to the rules", ask("Explain this number") is None)

print("\n3. NO DATA, SAID PLAINLY")
for q in ("What are our freight rates to Lagos?", "Show me this week's spot tenders",
          "Which documents are missing for EG shipments?"):
    a = ask(q)
    check("'{0}': no data, no figure".format(q), a["answer"].startswith("No data") and not re.search(r"\d", re.sub(r"\]\([^)]*\)", "", a["answer"])),
          a["answer"])
check("An on-time rate is not taken for a freight rate", ask("What is the on-time rate for EG?")["intent"] == "platform_ontime")
LOADED["on"] = False
a = ask("How many shipments are past due?")
check("No export loaded: says so, no number", a["answer"] == "No data: no eHub export is loaded yet.", a["answer"])
LOADED["on"] = True

print("\n4. ENGLISH AND ARABIC")
a = ask("كم شحنة متأخرة في مصر؟")
check("Arabic question, Arabic answer, with the country's name and code", a["lang"] == "ar" and "متأخرة في مصر (EG)" in a["answer"]
      and "**2**" in a["answer"] and "26 أكتوبر 2026" in a["answer"], a["answer"])
a = ask("كيف يتم حساب الشحنات المتأخرة؟")
check("Arabic: how past due is counted, definition first", a["answer"].startswith("**طريقة العد:**"), a["answer"])
check("Arabic: no data said plainly", ask("ما هي أسعار الشحن إلى لاغوس؟")["answer"].startswith("لا توجد بيانات"))

print("\n5. SCOPE AND ROLE")
a = ask("How many shipments are past due?", _bus={"GH"})
check("A BU-limited person is counted only their BUs, and told so", "**1** shipments are past due, of 2 open" in a["answer"]
      and "only your business units: GH" in a["answer"], a["answer"])
a = ask("How many past due shipments in EG?", _bus={"GH"})
check("...and asking about another BU gets no figure", a["intent"] == "platform_denied" and not re.search(r"\d", a["answer"]), a["answer"])
a = ask("Which BU has the most past due shipments?", _bus={"GH"})
check("...a ranking never names a BU outside their scope", "EG" not in a["answer"], a["answer"])
a = ask("How many shipments are past due?", clearance="0")
check("A role without Clearance gets no export figures", a["intent"] == "platform_denied" and not re.search(r"\d", a["answer"]), a["answer"])
a = ask("How many shipments are past due?", _can=lambda p: p != "clearance.view")
check("...whichever way the role arrives", a["intent"] == "platform_denied")
check("The tools never ask for an action (no re-run, no Human Action)",
      all(k not in ask(q) for q in ("How many shipments are past due?", "What is on my To Do list?") for k in ("request", "operation")))

print("\n6. LEFT TO THE RULES WHEN THE TOOLS CAN'T ANSWER IT")
for q in ("which clearing agent has the most past due?", "How many Air shipments are past due?",
          "How many shipments are past due at Submit to BU?", "Why is EG-0001 late?", "Is the ATA run running?",
          "count of shipments waiting for delivery"):
    check("'{0}' is left to the rules".format(q), ask(q) is None, ask(q))
check("On Clearance with filters on screen (mode, agent…), its own filter-aware answer is kept",
      ask("How many shipments are past due?", page="clearance", view={"mode": "Air"}) is None and
      ask("How many shipments are past due?", page="clearance", view={"bu": "EG"}) is not None)
check("Asked from the BOE page, PO questions stay with the PO answers", ask("How many shipments are past due?", domain="po") is None)

print("\n7. THE GUARD: A NUMBER NO TOOL GAVE IS NEVER WRITTEN")
ok, bad = A.grounded("**12** shipments, see [list](/hub#overview?list=all&bu=EG). Data as of 26 Oct 2026.", [12])
check("grounded(): figures from the tools pass; links and dates are not figures", ok, bad)
ok, bad = A.grounded("**12** shipments, 7 past due.", [12])
check("grounded(): a figure no tool gave is caught", not ok and bad == ["7"], bad)
r = A._reply("**12** shipments, 7 past due.", "platform_total", [12], ["stages"])
check("...and the reply fails closed: it says it has no data", r["answer"] == "I don't have data for that." and r["intent"] == "platform_no_data")
check("The rules' own definitions ('above 0') are not mistaken for figures", A.grounded("Now: **3**. " + A.PAST_DUE, [3])[0])

print("\n8. LOGGING: WHAT WAS USED, NEVER WHO OR WHAT WAS ASKED")
ask("How many shipments does GH have?")
log = (C.runtime() / "atlas_tools.jsonl").read_text(encoding="utf-8").strip().splitlines()
last = json.loads(log[-1])
check("The log keeps intent, tools, language and time only", set(last) == {"at", "intent", "tools", "lang", "ms"}, last)
check("...no question text, BU, reference or person in it", not re.search(r"GH|EG-|shipments|how many", "\n".join(log), re.I))
src = (HERE / "dashboard" / "atlas_tools.py").read_text(encoding="utf-8")
check("Read-only: the tools module writes nothing but its own log, and sends nothing",
      src.count("write_text(") == 1 and not re.search(r"urlopen|requests\.|control\.request|smtplib|subprocess", src))
check("The platform map is built from hub.MODULES and hub.STAGES (no copy of the rules)",
      [p["id"] for p in A.platform()["pages"]] == [m["id"] for m in H.MODULES] and
      [s["definition"] for s in A.platform()["stages"]] == [d for *_x, d in H.STAGES])
check("...and lists only the pages a role may open", "access" not in [p["id"] for p in A.platform(lambda p: p != "users.manage")["pages"]])

print("\n9. THROUGH /api/ask, AND THE HUB'S CHAT KNOWS THE PAGE")
with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    PORT = so.getsockname()[1]
S.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=None)
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


def api_ask(q, ctx):
    req = urllib.request.Request(BASE + "/api/ask", data=json.dumps({"question": q, "context": ctx}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": BASE})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


d = api_ask("How many shipments are in transit?", {})
check("/api/ask answers a platform number from the tools", d.get("intent") == "platform_stage" and "**{0}**".format(STAGES["transit"]) in d["answer"], d)
d = api_ask("Explain this number", {"page": "hub", "view": {"stage": "pastdue", "bu": "EG", "evil": "x" * 500}})
check("The server accepts the hub's page and its open list (known keys only)", d.get("intent") == "platform_how", d)
d = api_ask("How many shipments are in transit?", {"page": "nonsense"})
check("An unknown page is dropped, the answer still comes", d.get("intent") == "platform_stage")
srv = (HERE / "dashboard" / "server.py").read_text(encoding="utf-8")
check("The server gives the tools this person's role and BUs, not the browser",
      "_can=self._acc_can, _bus=self._acc_bus()" in srv)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright is None:
    SKIPPED.append("browser")
else:
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=exe) if os.path.exists(exe) else p.chromium.launch()
        pg = br.new_page(viewport={"width": 1366, "height": 768})
        pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','light')")
        pg.goto(BASE + "/hub?map=2d#overview?list=pastdue&bu=EG"); pg.wait_for_timeout(2500)
        check("ATLAS's 'Open the list' link opens that list on the hub (/hub#overview?list=pastdue&bu=EG)",
              not pg.evaluate("document.getElementById('dl').hidden") and pg.locator("#dlT").inner_text() == "EG · Open and past due",
              pg.locator("#dlT").inner_text())
        sent = []
        pg.route("**/api/ask", lambda r: (sent.append(json.loads(r.request.post_data or "{}")), r.continue_()))
        pg.locator("#dlAsk").click(); pg.wait_for_timeout(2500)
        ch = pg.frame_locator("#chatF")
        ch.locator("#chatIn").fill("Explain this number"); ch.locator("#chatIn").press("Enter"); pg.wait_for_timeout(2500)
        ctx = sent[-1]["context"] if sent else {}
        check("The hub's chat tells ATLAS the hub page and the list on screen", ctx.get("page") == "hub" and
              ctx.get("view") == {"stage": "pastdue", "bu": "EG"}, ctx)
        bot = ch.locator(".msg.bot").last.inner_text() if ch.locator(".msg.bot").count() else ""
        check("...and the answer explains that list's number (EG's)", "How it's counted" in bot and "Open (not Completed)" in bot
              and "past due in EG" in bot, bot[:300])
        link = ch.locator(".msg.bot a[href^='/hub#overview?list=']").last
        check("A link in ATLAS's answer is a real link (with its ?list=)", ch.locator(".msg.bot a[href^='/hub#overview?list=']").count() >= 1)
        check("The list sheet has its own Ask ATLAS (the top bar is behind it)", pg.locator("#dlAsk").count() == 1)
        br.close()

print("\n{0} passed, {1} failed{2}".format(len(PASSED), len(FAILED), ", {0} skipped".format(len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
