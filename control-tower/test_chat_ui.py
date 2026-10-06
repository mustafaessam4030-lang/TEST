"""
The ATLAS conversation, in a real browser, against the real dashboard
server and the real ATLAS answers.

The run state is built through the bridge exactly as a run builds it: two
shipments that completed and one CMA CGM shipment whose carrier kept access
restricted after the human verification. Every answer below comes from
/api/ask over that state; the page renders what came back.

Three checks replace the network on purpose, and say so: the backend made
unreachable (error and disconnected states), the first answer held back
(so the thinking state can be seen), and one reply carrying a button kind
the page must refuse to draw. Nothing else is stubbed.

    python test_chat_ui.py
"""

import os
import re
import socket
import sys
import tempfile
import time
import urllib.request
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("ATLAS_INTEL_DIR", tempfile.mkdtemp(prefix="ct_chat_intel_"))
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")
os.environ.setdefault("PO_DATA_DIR", tempfile.mkdtemp(prefix="ct_chat_po_"))
os.environ.setdefault("PO_AUTO", "0")

from dashboard import server as tower_server       # noqa: E402
from dashboard.bridge import bridge as B           # noqa: E402

PASS, FAIL = [], []
INDEX = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")


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


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ── the run, as the bridge records it ───────────────────────────────────
RUN = "20261006-090000-a1b2c3"
B.run_started(run_id=RUN, dry_run=False, target_status="Under Clearance",
              max_records=200, max_pages=10)
for ref, carrier, prov, eta in (("176-88452310", "Air France KLM", "AFKL", "12/10/2026"),
                                ("MEDUAHP69377", "MSC", "MSC", "10/11/2026")):
    B.shipment_started({"bol_awb": ref, "carrier": carrier, "provider": prov, "table_page": 1,
                        "current_eta": "05/10/2026"})
    B.provider_result({"provider": prov, "tracking_status": "Estimated arrival", "eta": eta,
                       "eta_source": "ETA"})
    B.view_updated("COE", "ETA", eta, verified=True)
    B.shipment_finished(ref, "SUCCESS", "", {"coe": "COE ETA updated with %s and saved" % eta})
REF = "CMAU7700001"
URL = "https://www.cma-cgm.com/ebusiness/tracking/search"
B.shipment_started({"bol_awb": REF, "carrier": "CMA CGM", "provider": "CMA_CGM", "table_page": 1,
                    "current_eta": "05/11/2026"})
B.human_verification_required(REF, "CMA CGM")
B.human_verification_cleared(REF, 48)
B.carrier_access(REF, "RESTRICTED", URL, "CMA CGM restriction page after the human verification",
                 facts={"carrier": "CMA CGM", "verification_completed": True,
                        "carrier_access": "restricted", "extraction": "not performed",
                        "hub_write": "not performed", "final_result": "FAILED — RECOVERY_REQUIRED"})
B.shipment_finished(REF, "FAILED",
    "CMA CGM restricted access after the human verification was completed for %s. Nothing was "
    "extracted or written. URL: %s" % (REF, URL), outcome="CARRIER ACCESS RESTRICTED",
    failure={"category": "CARRIER_ACCESS_RESTRICTED", "stage": "carrier_access",
             "operation": "Carrier access", "last_success": "human verification completed",
             "detail": "CMA CGM showed a restriction page instead of the shipment after the human "
                       "verification was completed; the lookup was stopped.",
             "cause": {"kind": "carrier", "name": "CMA CGM access restriction",
                       "value": "restricted", "decided_by": "CMA CGM's own page",
                       "stated_condition": "the page itself names possible causes: none named"},
             "observed": {"url": URL, "title": "Access is temporarily restricted",
                          "signals": "access temporarily restricted",
                          "after_human_verification": "yes", "extraction": "not performed",
                          "hub_write": "not performed"}})
B.counters(2, 1, 0, 0)

PORT = free_port()
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1")
BASE = "http://127.0.0.1:%d/" % PORT
for _ in range(50):
    try:
        urllib.request.urlopen(BASE + "api/atlas", timeout=1)
        break
    except Exception:
        time.sleep(0.1)


def api_ask(question, context=None):
    req = urllib.request.Request(BASE + "api/ask", data=json.dumps(
        {"question": question, "context": context or {}}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def plain(text):
    """An answer line as it reads on the page: markers and labels off."""
    line = re.sub(r"^\*\*[^*]+\*\*\s*[—–:-]\s*", "", text.strip())
    line = re.sub(r"^([-•*]|\d{1,2}[.)])\s+", "", line)
    return " ".join(line.replace("**", "").replace("`", "").split())


def launch(playwright):
    last = None
    options = [{"headless": True}]
    if Path("/opt/pw-browsers").is_dir():
        options = [{"headless": True, "executable_path": str(b)} for b in sorted(
            Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))] + options
    options += [{"headless": True, "channel": "msedge"}, {"headless": True, "channel": "chrome"}]
    for option in options:
        try:
            return playwright.chromium.launch(**option), None
        except Exception as error:
            last = str(error).split("\n")[0][:100]
    return None, last


try:
    from playwright.sync_api import sync_playwright
except Exception as error:                              # pragma: no cover
    print("Playwright is not available: {0}".format(error))
    print("0 passed, 1 failed")
    sys.exit(1)

pw = sync_playwright().start()
browser, why = launch(pw)
if browser is None:
    print("  FAIL  a real browser launched  ({0})".format(why))
    print("0 passed, 1 failed")
    sys.exit(1)


def new_page(width=1440, height=900, theme="light", reduced=False, touch=False):
    ctx = browser.new_context(viewport={"width": width, "height": height},
                              color_scheme=theme, has_touch=touch, is_mobile=touch,
                              reduced_motion="reduce" if reduced else "no-preference")
    page = ctx.new_page()
    page.add_init_script("sessionStorage.setItem('ct-intro','1');"
                         "try{localStorage.setItem('ct-theme','%s')}catch(e){}" % theme)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE)
    page.wait_for_function("() => typeof S !== 'undefined' && S && S.run && S.run.run_id", timeout=15000)
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
    return ctx, page, errors


def open_chat(page):
    page.click("#fab")
    page.wait_for_selector("#chat.on", timeout=5000)
    page.wait_for_timeout(600)


def ask(page, question, timeout=20000):
    before = page.locator("#chatBody .msg.bot:not(.think)").count()
    page.fill("#chatIn", question)
    page.press("#chatIn", "Enter")
    page.wait_for_function(
        "n => document.querySelectorAll('#chatBody .msg.bot:not(.think)').length > n && "
        "!document.querySelector('#chatBody .msg.think')", arg=before, timeout=timeout)
    page.wait_for_timeout(450)
    return page.locator("#chatBody .msg.bot:not(.think) .bub").last


def lum(rgb):
    vals = [int(v) / 255 for v in re.findall(r"[\d.]+", rgb)[:3]]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in vals]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ═════════════════════════════════════════════════════════════════════════
rule("1. OPEN — A WORKSPACE, THE CHARACTER, THE RUN, A WELCOME")
# ═════════════════════════════════════════════════════════════════════════
ctx, page, errors = new_page()
check("Closed at first: the dialog is hidden from assistive technology",
      page.get_attribute("#chat", "aria-hidden") == "true" and not page.is_visible("#chat"))
open_chat(page)
box = page.locator("#chat").bounding_box()
check("Open: a large centered surface on desktop (≥ 900px wide, ≥ 780px tall)",
      box["width"] >= 900 and box["height"] >= 780 and abs(box["x"] + box["width"] / 2 - 720) < 4,
      box)
check("...over a dimmed backdrop, the launcher out of the way",
      page.is_visible("#chScrim") and not page.is_visible("#fab"))
check("A modal dialog, named by its title",
      page.get_attribute("#chat", "role") == "dialog"
      and page.get_attribute("#chat", "aria-modal") == "true"
      and page.get_attribute("#chat", "aria-labelledby") == "chTitle"
      and page.get_attribute("#chat", "aria-hidden") == "false")
check("Header: ATLAS / Operational Intelligence",
      page.inner_text("#chTitle").split() == ["ATLAS", "Operational", "Intelligence"],
      page.inner_text("#chTitle"))
check("...with the existing ATLAS character, drawn by the Atlas component",
      page.locator("#chAtlas .atlas-fig").count() == 1
      and page.locator("#chAtlas .atlas-fig").bounding_box()["width"] >= 40)
page.wait_for_function("() => document.querySelector('#chStatus').textContent.trim().length > 0")
check("...and a live status from /api/atlas with its dot",
      page.inner_text("#chStatus").strip() != "" and
      page.get_attribute("#chRole", "data-s") in ("live", "wait", "bad", "idle"),
      page.inner_text("#chStatus"))
check("The run in context: the run id, as a button", page.is_visible("#chCtx")
      and page.inner_text("#chCtxV").strip() == RUN, page.inner_text("#chCtx"))
check("Empty state: the welcome, centered", page.is_visible("#chEmpty") and
      "Ask me about the current run, shipment status, failures, human actions, or PO automation."
      in page.inner_text("#chEmpty"))
chips = page.locator("#chEmptySug .ch-chip")
check("...with 3–4 suggestions taken from this run", 3 <= chips.count() <= 4,
      chips.all_inner_texts())
check("...and the composer focused, its placeholder the brief's",
      page.evaluate("document.activeElement.id") == "chatIn"
      and page.get_attribute("#chatIn", "placeholder") == "Ask ATLAS about this run…")
check("Send is disabled while the composer is empty", page.is_disabled("#chatSend"))
page.fill("#chatIn", "x")
check("...and enabled once there is something to send", not page.is_disabled("#chatSend"))
page.fill("#chatIn", "")

# ═════════════════════════════════════════════════════════════════════════
rule("2. SEND, THINK, RECEIVE — THE REAL ANSWER, WHOLE, GROUNDED")
# ═════════════════════════════════════════════════════════════════════════
# Held back 1.2 s in the page so the thinking state is on screen long enough
# to read; the answer itself is the server's.
page.evaluate("""() => { const f = window.fetch; window.__hold = 1200;
  window.fetch = (u, o) => String(u).indexOf('/api/ask') >= 0 && window.__hold
    ? new Promise((r) => setTimeout(() => r(f(u, o)), window.__hold)) : f(u, o); }""")
page.fill("#chatIn", "Why did this fail?")
page.press("#chatIn", "Enter")
page.wait_for_timeout(250)
check("The question appears at once, right-aligned in the operator's bubble",
      page.locator("#chatBody .msg.me .bub").last.inner_text().strip() == "Why did this fail?")
me = page.locator("#chatBody .msg.me").last.bounding_box()
body_box = page.locator("#chatBody").bounding_box()
check("...on the right", me["x"] + me["width"] > body_box["x"] + body_box["width"] * 0.7, me)
check("Thinking: ATLAS (analyzing) with what it is doing, as a status",
      page.is_visible("#chatBody .msg.think") and
      page.get_attribute("#chatBody .msg.think", "role") == "status" and
      page.inner_text("#chatBody .msg.think .th-label").strip() in (
          "Analyzing this run…", "Checking the latest run evidence…") and
      page.locator("#chatBody .msg.think .atlas.is-analyzing").count() == 1,
      page.inner_text("#chatBody .msg.think") if page.is_visible("#chatBody .msg.think") else "")
check("...the same words for screen readers", page.inner_text("#chTy").strip().endswith("…"),
      page.inner_text("#chTy"))
check("...send shows it is busy and cannot be pressed twice",
      page.is_disabled("#chatSend") and "busy" in page.get_attribute("#chatSend", "class"))
check("...and the suggestions step back", "busy" in page.get_attribute("#chat", "class"))
page.wait_for_selector("#chatBody .msg.think", state="detached", timeout=20000)
bub = page.locator("#chatBody .msg.bot:not(.think) .bub").last
first = bub.inner_text()
page.wait_for_timeout(400)
check("No fake streaming: the answer is whole the moment it arrives",
      first == bub.inner_text() and len(first) > 80, len(first))
check("...and no typing timer exists in the page",
      "typeOut" not in INDEX and "class=\"cur\"" not in INDEX)
page.evaluate("window.__hold = 0")
want = api_ask("Why did this fail?")["answer"]
shown = " ".join(bub.inner_text().split())
missing = [plain(l) for l in want.split("\n") if plain(l) and plain(l) not in shown]
check("Every line of the server's answer is on the page — nothing dropped or added",
      not missing, missing[:3])
kinds = page.locator("#chatBody .msg.bot .bub .cl-k").all_inner_texts()
check("Grounding stays visible: Fact and Not established labels",
      any(k.strip().lower() == "fact" for k in kinds)
      and any(k.strip().lower() == "not established" for k in kinds), kinds)
check("ATLAS answers on the left, no card around it, with its small avatar",
      page.locator("#chatBody .msg.bot .ax-av .atlas-fig").count() >= 1 and
      page.evaluate("getComputedStyle(document.querySelector('#chatBody .msg.bot .bub')).borderLeftWidth")
      == "0px")
check("The status returns to normal",
      not page.is_disabled("#chatSend") or page.input_value("#chatIn") == "")
sug = page.locator("#chatSug .ch-chip")
check("Suggestions reduce once the conversation is active (1–3)", 1 <= sug.count() <= 3,
      sug.all_inner_texts())
check("The welcome gives way to the conversation", not page.is_visible("#chEmpty"))

# ═════════════════════════════════════════════════════════════════════════
rule("3. QUICK ACTIONS, EVIDENCE, ACTION BUTTONS, RUN CONTEXT")
# ═════════════════════════════════════════════════════════════════════════
label = sug.first.inner_text().strip()
n_me = page.locator("#chatBody .msg.me").count()
sug.first.click()
page.wait_for_function("n => document.querySelectorAll('#chatBody .msg.me').length > n",
                       arg=n_me)
page.wait_for_selector("#chatBody .msg.think", state="detached", timeout=20000)
check("A suggestion sends its question",
      page.locator("#chatBody .msg.me .bub").last.inner_text().strip() == label, label)

reply = api_ask("What happened with %s?" % REF)
ask(page, "What happened with %s?" % REF)
ev = page.locator("#chatBody details.evx").last
check("Evidence from this run: folded under the answer",
      ev.count() == 1 and "Evidence from this run" in ev.inner_text()
      and ev.get_attribute("open") is None)
ev.locator("summary").click()
page.wait_for_timeout(300)
check("...opens to the shipment's own record", ev.get_attribute("open") is not None
      and REF in ev.locator(".mini").inner_text())
check("...keyboard-reachable (a native disclosure)",
      ev.locator("summary").evaluate("e => e.tabIndex") >= 0)
labels = page.locator("#chatBody .dl-row").last.locator("button").all_inner_texts()
check("Action buttons are exactly the ones the answer carried",
      labels == [b["label"] for b in reply["buttons"]], (labels, reply["buttons"]))
check("Run context follows the conversation: the shipment discussed",
      page.inner_text("#chCtxV").strip() == REF and
      page.inner_text("#chCtxK").strip().lower() == "shipment")
page.locator("#chatBody .dl-row").last.locator("button").first.click()
page.wait_for_timeout(500)
check("An Open button opens that shipment and steps the chat aside",
      not page.is_visible("#chat") and page.is_visible("#fab")
      and REF in page.evaluate("document.body.innerText"))
page.click("#dwX")
page.wait_for_timeout(400)
open_chat(page)
check("Ask ATLAS again: the conversation is kept",
      page.locator("#chatBody .msg.me").count() >= 3 and not page.is_visible("#chEmpty"))
page.click("#chCtx")
page.wait_for_timeout(500)
check("The context pill opens what it names", not page.is_visible("#chat")
      and page.is_visible("#drawer") and REF in page.inner_text("#drawer"))
page.click("#dwX")
page.wait_for_timeout(400)
open_chat(page)

# One reply carrying a kind the page must not draw — and one human_open,
# which must go through the queue's own request (hqOpen).
page.route("**/api/ask", lambda route: route.fulfill(status=200, content_type="application/json",
    body=json.dumps({"answer": "**Fact** — test reply.", "buttons": [
        {"label": "Open the verification", "action": {"type": "human_open", "action_id": "ha_x"}},
        {"label": "Delete everything", "action": {"type": "run_script", "cmd": "rm"}}],
        "suggestions": []})))
ask(page, "renderer check")
row = page.locator("#chatBody .dl-row").last
check("A button kind outside the allowed set is not drawn",
      row.locator("button").all_inner_texts() == ["Open the verification"],
      row.locator("button").all_inner_texts())
check("The human_open button is the primary one",
      "pri" in row.locator("button").first.get_attribute("class"))
row.locator("button").first.click()
page.wait_for_timeout(500)
check("...and goes through the queue's own request (the run answers, not the page)",
      "no longer in the queue" in page.locator("#chatBody .ch-done").last.inner_text())
page.unroute("**/api/ask")
check("The human_open path in the page is the queue's: hqOpen, on a click",
      "if (b.action && b.action.type === 'human_open'){" in INDEX
      and "hqOpen(b.action.action_id)" in INDEX)

# ═════════════════════════════════════════════════════════════════════════
rule("4. COMPOSER — MULTILINE, LONG MESSAGES, KEYBOARD")
# ═════════════════════════════════════════════════════════════════════════
h0 = page.locator("#chatIn").bounding_box()["height"]
page.click("#chatIn")
page.keyboard.type("first line")
page.keyboard.press("Shift+Enter")
page.keyboard.type("second line")
page.keyboard.press("Shift+Enter")
page.keyboard.type("third line")
check("Shift+Enter adds a line and does not send",
      page.input_value("#chatIn").count("\n") == 2)
check("...and the composer grows with it",
      page.locator("#chatIn").bounding_box()["height"] > h0 + 10)
n_me = page.locator("#chatBody .msg.me").count()
ask(page, page.input_value("#chatIn"))
check("Enter sends, the lines kept in the bubble",
      page.locator("#chatBody .msg.me .bub").last.inner_text().count("\n") == 2)
check("...and the composer shrinks back",
      page.locator("#chatIn").bounding_box()["height"] <= h0 + 2)
page.click("#chatIn")
page.keyboard.press("ArrowUp")
check("ArrowUp in an empty composer brings back the last question",
      page.input_value("#chatIn").startswith("first line"))
page.fill("#chatIn", "")
long_q = "Please explain " + ("the CMA CGM restriction and what it means for the run " * 30)
ask(page, long_q.strip())
check("A long message wraps inside its bubble; nothing scrolls sideways",
      page.evaluate("""() => { const b = document.querySelector('#chatBody');
        const m = [...b.querySelectorAll('.msg.me')].pop();
        return b.scrollWidth <= b.clientWidth + 1 &&
               m.getBoundingClientRect().right <= b.getBoundingClientRect().right; }"""))
last = page.locator("#chatBody > *").last.bounding_box()
composer = page.locator("#chat .ch-f").bounding_box()
page.evaluate("document.querySelector('#chatBody').scrollTop = 1e9")
page.wait_for_timeout(200)
last = page.locator("#chatBody > *").last.bounding_box()
sug_box = page.locator("#chatSug").bounding_box() or composer
check("The composer never covers the last message",
      last["y"] + last["height"] <= min(composer["y"], sug_box["y"]) + 1, (last, composer))
page.focus("#chatIn")
for _ in range(40):
    page.keyboard.press("Tab")
check("Tab stays inside the open dialog",
      page.evaluate("document.querySelector('#chat').contains(document.activeElement)"))
page.keyboard.press("Escape")
page.wait_for_timeout(400)
check("Esc closes it", not page.is_visible("#chat")
      and page.get_attribute("#chat", "aria-hidden") == "true")
check("...and focus returns to where it was", page.evaluate("document.activeElement.id") in
      ("fab", "chatIn") or page.evaluate("document.activeElement !== document.body"))
open_chat(page)
page.click("#chatX")
page.wait_for_timeout(400)
check("The close button closes it", not page.is_visible("#chat"))
open_chat(page)
page.mouse.click(30, 450)
page.wait_for_timeout(400)
check("A click on the backdrop closes it", not page.is_visible("#chat"))
check("No script errors in the session", not errors, errors[:3])
ctx.close()

# ═════════════════════════════════════════════════════════════════════════
rule("5. ERROR AND DISCONNECTED BACKEND")
# ═════════════════════════════════════════════════════════════════════════
ctx, page, errors = new_page()
page.route("**/api/atlas", lambda route: route.abort())
open_chat(page)
page.wait_for_timeout(500)
check("Backend unreachable: the header says so, in red",
      "Not connected" in page.inner_text("#chStatus")
      and page.get_attribute("#chRole", "data-s") == "bad", page.inner_text("#chStatus"))
page.unroute("**/api/atlas")
page.route("**/api/ask", lambda route: route.abort())
page.fill("#chatIn", "How is the run going?")
page.press("#chatIn", "Enter")
page.wait_for_selector("#chatBody .bub.err", timeout=10000)
err = page.locator("#chatBody .bub.err").last
check("A failed answer is said plainly — no data, no guess",
      "could not reach the Control Tower backend" in err.inner_text())
check("...with Try again", err.locator("button", has_text="Try again").count() == 1)
check("...and the composer usable again", not page.is_disabled("#chatIn")
      and "busy" not in (page.get_attribute("#chatSend", "class") or ""))
page.unroute("**/api/ask")
err.locator("button", has_text="Try again").click()
page.wait_for_function("() => [...document.querySelectorAll('#chatBody .msg.bot .bub')]"
                       ".filter(b => !b.classList.contains('err')).length > 0", timeout=20000)
check("Try again sends the same question, and the real answer arrives",
      page.locator("#chatBody .msg.me .bub").last.inner_text().strip() == "How is the run going?")
page.wait_for_timeout(800)
check("...and the status recovers", "Not connected" not in page.inner_text("#chStatus"),
      page.inner_text("#chStatus"))
check("No script errors", not errors, errors[:3])
ctx.close()

# ═════════════════════════════════════════════════════════════════════════
rule("6. MOBILE, TABLET, REDUCED MOTION, DARK AND LIGHT")
# ═════════════════════════════════════════════════════════════════════════
ctx, page, errors = new_page(390, 844, touch=True)
page.tap("#fab")
page.wait_for_selector("#chat.on")
page.wait_for_timeout(600)
box = page.locator("#chat").bounding_box()
check("Phone: the conversation is the whole screen",
      box["x"] == 0 and abs(box["width"] - 390) < 1 and box["height"] >= 840, box)
check("...the keyboard is not forced up on open",
      page.evaluate("document.activeElement.id") != "chatIn")
check("...the page behind cannot scroll",
      page.evaluate("getComputedStyle(document.body).overflow") == "hidden")
ask(page, "Why did this fail?")
comp = page.locator("#chat .ch-f").bounding_box()
check("...the composer sits at the bottom, inside the screen",
      comp["y"] + comp["height"] <= 844 + 1 and comp["y"] > 600, comp)
check("...nothing scrolls sideways",
      page.evaluate("document.querySelector('#chatBody').scrollWidth <= "
                    "document.querySelector('#chatBody').clientWidth + 1"))
check("...grounding labels still shown",
      page.locator("#chatBody .cl-k").count() >= 1)
check("No script errors", not errors, errors[:3])
ctx.close()

ctx, page, errors = new_page(834, 1112)
open_chat(page)
box = page.locator("#chat").bounding_box()
check("Tablet: a large sheet with a margin", 760 <= box["width"] <= 810 and box["x"] >= 12, box)
ctx.close()

ctx, page, errors = new_page(reduced=True)
open_chat(page)
anim = page.evaluate("getComputedStyle(document.querySelector('#chat')).animationName")
ask(page, "How is the run going?")
check("Reduced motion: no entrance animation, no message motion",
      anim == "none" and page.evaluate(
          "getComputedStyle(document.querySelector('#chatBody .msg')).animationName") == "none",
      anim)
page.keyboard.press("Escape")
page.wait_for_timeout(50)
check("...and it closes at once", not page.is_visible("#chat"))
ctx.close()

colors = {}
for theme in ("light", "dark"):
    ctx, page, errors = new_page(theme=theme)
    open_chat(page)
    ask(page, "How is the run going?")
    colors[theme] = page.evaluate("""() => {
      const c = (s, p) => getComputedStyle(document.querySelector(s))[p];
      return {panel: c('#chat', 'backgroundColor'), me: c('#chatBody .msg.me .bub', 'backgroundColor'),
              meInk: c('#chatBody .msg.me .bub', 'color'), ink: c('#chatBody .msg.bot .bub', 'color')}; }""")
    c = colors[theme]
    check("{0}: the operator's bubble reads (contrast ≥ 7)".format(theme.title()),
          contrast(c["me"], c["meInk"]) >= 7, (c, round(contrast(c["me"], c["meInk"]), 2)))
    check("{0}: ATLAS's text reads on the panel (contrast ≥ 7)".format(theme.title()),
          contrast(c["panel"], c["ink"]) >= 7, round(contrast(c["panel"], c["ink"]), 2))
    check("{0}: no script errors".format(theme.title()), not errors, errors[:3])
    ctx.close()
check("Dark and light are the theme's own: the panel changes with it",
      colors["light"]["panel"] != colors["dark"]["panel"])

# ═════════════════════════════════════════════════════════════════════════
rule("7. NOTHING ELSE CHANGED: CONTRACT, GROUNDING, SAFETY")
# ═════════════════════════════════════════════════════════════════════════
check("The request is the same: question + context (reference, action, evidence, conduct)",
      "context: {reference: lastReference, action_id: lastFocus," in INDEX
      and "evidence_id: lastEvidence, conduct: conductCount}" in INDEX)
check("Only the allowed UI actions run (filter, open, page) — the whitelist is unchanged",
      "if (a.type === 'filter'){" in INDEX and "UI_PAGES[a.page]" in INDEX)
check("Feedback is still recorded as material, never as verification",
      'data-v="helpful"' in INDEX and 'data-v="not_helpful"' in INDEX
      and "What should it have said?" in INDEX)
check("The chat never fills or reads a security code field",
      not re.search(r"security.?code|captcha", re.sub(r"/\*[\s\S]*?\*/|^\s*//.*$", "",
                    INDEX[INDEX.index("(function chat(){"):], flags=re.M), re.I))

browser.close()
pw.stop()

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
