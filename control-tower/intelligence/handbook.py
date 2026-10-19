"""
ATLAS's handbook: how the Control Tower itself works, so anyone can ask.

Two layers, both read-only and both written down here so they can be read,
corrected and tested:

    TOPICS   short, plain answers to the questions people actually ask about
             the system ("How does the ETA update work?", "Which carriers do
             you support?", "How is a PO checked?", "I can't open the
             dashboard"). Each states what the code does today; the facts that
             change with the code (the carrier list, the held carriers, the
             run limits) are read from the code when the answer is given.
    GUIDES   when no topic fits, the project's own guides (README, START_HERE,
             PO.md, PLATFORM.md, SERVER_SETUP.md ...) are searched section by
             section and the best section is quoted with where it came from.

Nothing here reads the run or decides a run fact: a question about a shipment,
a failure or a PO job is answered from the records by dashboard/assistant.py.
"""

import math
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A question about how something works, what something is, or how to do it.
HOW_ASK = re.compile(
    r"\b(how|what|which|why|where|when|who)\b|\?\s*$|\bexplain\b|\bwalk me through\b|"
    r"\btell me (about|how)\b|\bcan (i|we|you|it|someone|people|others)\b|\bis (it|there)\b|"
    r"\bdo (you|i|we)\b|\b(can'?t|cannot|unable to|not able to|couldn'?t)\b|\bhelp me\b|\bguide\b|"
    r"كيف|ازاي|إزاي|ما هو|ما هي|ايه هو|اشرح", re.I)


def _carriers():
    """The carriers the automation looks up, read from update_eta.py."""
    try:
        text = (ROOT / "update_eta.py").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    labels = []
    for label in re.findall(r'"label":\s*"([^"]+)"', text):
        if label not in labels:
            labels.append(label)
    known = [name for name, provider in re.findall(
        r'"\d{3}":\s*\{"name":\s*"([^"]+)",\s*"code":\s*"[A-Z0-9]+",\s*"provider":\s*([A-Za-z_"]+)',
        text) if provider == "None"]
    return labels, known


def _held():
    try:
        import carrier_pacing
        return sorted(carrier_pacing.held_carriers())
    except Exception:
        return ["CMA_CGM"]


def _limits():
    out = {"max_hub_writes": "150", "max_records": "200",
           "ocean_per_run": "30", "other_per_run": "150"}
    try:
        text = (ROOT / "update_eta.py").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'MAX_HUB_WRITES = int\(os\.environ\.get\("MAX_HUB_WRITES", "(\d+)"\)\)', text)
        if m:
            out["max_hub_writes"] = os.environ.get("MAX_HUB_WRITES") or m.group(1)
        m = re.search(r"^MAX_RECORDS_PER_RUN = (\d+)", text, re.M)
        if m:
            out["max_records"] = m.group(1)
    except OSError:
        pass
    try:
        import carrier_pacing
        out["ocean_per_run"] = str(carrier_pacing.MAX_PER_RUN["ocean"])
        out["other_per_run"] = str(carrier_pacing.MAX_PER_RUN["other"])
    except Exception:
        pass
    return out


def _carrier_answer():
    found = _carriers()
    held = _held()
    if not found:
        return ("I couldn't read the carrier list from the automation on this machine. The "
                "Shipments page shows which carrier each shipment used.")
    labels, known = found
    labels = [l for l in labels if l]
    lines = ["The automation looks up dates on these carriers' own tracking pages:", ""]
    lines += ["• " + ", ".join(labels) + ", plus DHL Express and Qatar Airways Cargo."]
    if held:
        lines += ["", "**On hold:** {0}. Kept in the system but not looked up; those shipments "
                      "go to a person (see “Why is CMA CGM on hold?”).".format(
                          ", ".join(h.replace("_", " ") for h in held))]
    if known:
        lines += ["", "**Recognised but not automated yet:** {0}. A run names them, so you "
                      "know who they are, but someone updates them by hand.".format(
                          ", ".join(known[:12]) + (" and others" if len(known) > 12 else ""))]
    return "\n".join(lines)


def _limits_answer():
    l = _limits()
    return ("**Run limits** keep a run small enough to watch:\n\n"
            "• At most **{max_records}** shipments are read from the Hub list per run.\n"
            "• At most **{max_hub_writes}** writes to the Hub per run (MAX_HUB_WRITES). "
            "An unreadable setting allows no writes at all.\n"
            "• Per carrier per run: up to **{ocean_per_run}** look-ups for each ocean carrier, "
            "**{other_per_run}** for the others.\n"
            "• A carrier that blocks access is rested before it is tried again.\n\n"
            "When a limit is reached the run stops writing and says so in the log; nothing is "
            "lost, the rest wait for the next run.").format(**l)


def _hold_answer():
    held = _held()
    names = ", ".join(h.replace("_", " ") for h in held) or "none"
    return ("**Carriers on hold: {0}.**\n\n"
            "CMA CGM is held because its site blocked access even after a person completed "
            "the security check. A held carrier stays in the system, but the automation does "
            "not look it up: its shipments go to the deferred list for a person to update in "
            "the Hub. The hold is fixed in the code, so a restart or a mistyped setting can't "
            "lift it; it will be lifted only once a permitted channel (an API or alert emails) "
            "is in place.").format(names)


# (key, title, pattern, answer — text or a function returning text)
TOPICS = [
    ("overview", "What the Control Tower is",
     r"\bwhat (is|does) (the |this )?(ata )?(control tower|tower|system|project|platform|tool|app)\b|"
     r"\bexplain (the |this )?(control tower|system|project|platform)\b|"
     r"\bwhat (is|does) (this|it) do\b|\bhow does (the |this )?(control tower|system|platform|project) work\b|"
     r"\btell me about (the |this )?(control tower|system|project)\b",
     "The **ATA Control Tower** keeps shipment dates in the Hub up to date, and prepares POs "
     "from customs documents. It has four parts:\n\n"
     "• **The ETA/ATA automation** reads the Hub's list, opens each carrier's tracking page, "
     "finds the ETA and ATA, checks them and writes them to the Hub, then reads them back.\n"
     "• **PO Automation** finds the Bill of Entry in eHub for shipments Under Clearance, reads "
     "and checks it, and fills the approved Duty Payment Request template.\n"
     "• **The dashboard** shows the run live: counters, each shipment, what needs a person.\n"
     "• **ATLAS** (me) answers questions from the run's records, explains failures, checks "
     "dates, makes PDF reports and reads screenshots.\n\n"
     "It never guesses: anything unclear goes to a person, and carrier security checks are "
     "always left to a person."),
    ("eta_flow", "How the ETA/ATA update works",
     r"\bhow (does|do) (the )?(eta|ata|date|update|automation|run|process)s? (update|updates|work|works|get updated|get written|get found)\b|"
     r"\bhow (are|is) (the )?(eta|ata|dates?) (updated|written|found|chosen|decided|picked|set)\b|"
     r"\b(eta|ata|update|automation) (process|flow|steps)\b|\bwalk me through (the )?(process|automation|run)\b|"
     r"\bwhat happens (in|during) (a|the) run\b|\bhow (does|do) (it|you|the system|the tower|the automation) "
     r"(update|find|get|read|choose|decide|write) (the )?(eta|ata|dates?)\b",
     "**For each shipment, the run does five things:**\n\n"
     "1. **Reads the Hub** list (status Under Clearance by default) and picks up shipments that "
     "still need a date.\n"
     "2. **Works out the carrier** from the air waybill prefix or the bill of lading, and opens "
     "that carrier's own tracking page.\n"
     "3. **Finds the ETA and ATA** for that exact shipment number. The number must appear on "
     "the carrier page, or nothing is read.\n"
     "4. **Checks the dates** (see “What checks does it do?”). Anything doubtful goes to "
     "NEEDS REVIEW and nothing is written.\n"
     "5. **Writes and reads back:** saves the date in the Hub, reopens the shipment and reads "
     "the field. It counts as SUCCESS only when the Hub holds the date it wrote.\n\n"
     "If a carrier shows a security check, that shipment waits for a person under Human "
     "Action, and the run carries on with the others."),
    ("dhl", "How ATLAS checks the DHL dates",
     r"\bhow (does|do|did|will) (atlas|you) (check|validate|verify) (the )?dhl\b|\bhow (does|do) (the )?dhl "
     r"(check|checks|validation|picks?) work\b|\bhow (does|is) dhl (checked|handled|validated)\b",
     "DHL pages show many dates (estimated delivery, arrival at a hub, transit stops), so DHL "
     "gets extra checks:\n\n"
     "• The ATA must come from an event that really is an arrival at the destination, not a "
     "transit stop.\n"
     "• A delivery estimate is not written as the ETA.\n"
     "• Once arrived, the ETA is not written; the ATA is.\n"
     "• **ATLAS reads the same page on its own** and must agree, quoting the line it read. If "
     "it reads a different date, can't quote the line, or hits an error, the shipment goes to "
     "a person and nothing is written.\n\n"
     "Ask “Did ATLAS check the DHL dates?” for this run's results."),
    ("checks", "The checks before anything is written",
     r"\b(what|which) (checks|validations?|rules|safety)\b|\bhow (do|does|did) (it|you|atlas|the "
     r"(automation|system|tower|run)) (validate|check|verify|make sure)\b|\bvalidation (rules|process|steps)\b|"
     r"\bis it safe\b|\bhow (is|are) (it|the dates?|the (eta|ata)) (checked|validated|verified)\b|"
     r"\bwhat (stops|prevents) (it|a) (from )?(writ|wrong)",
     "**Every ETA and ATA is checked before it goes to the Hub:**\n\n"
     "• **Right shipment:** the carrier page must carry the Hub's reference (ocean: letter for "
     "letter).\n"
     "• **Real dates:** each date must be a complete, unambiguous dd/mm/yyyy date. A bad date "
     "is dropped, never repaired.\n"
     "• **No future arrival:** an ATA can't be after today.\n"
     "• **ETA = ATA is a warning sign:** the same date read as both may be an estimate read "
     "twice, so a person checks it.\n"
     "• **No overwriting an ATA:** if the Hub already holds a different ATA, it is never "
     "replaced automatically; a person decides.\n"
     "• **DHL:** each date must come from a label that really means arrival, and ATLAS reads "
     "the page independently; if it reads a different date, the shipment goes to a person.\n"
     "• **Read-back:** after saving, the Hub is read again; only a matching read-back is a "
     "SUCCESS.\n\n"
     "Any failed check means **nothing is written** for that shipment and it shows as NEEDS "
     "REVIEW with the reason. Ask me “validate <shipment>” to see the checks for one."),
    ("overwrite", "Why an existing ATA is not overwritten",
     r"\boverwrit|\breplace (the|an?) (ata|eta|date)\b|\balready (has|holds|had) an? (ata|eta|date)\b|"
     r"\bwhy (didn'?t|did not|won'?t|doesn'?t) it (overwrite|replace) (the |an? )?(ata|date)\b",
     "When the Hub **already holds an ATA** that differs from the carrier's, the automation "
     "does not write over it, newer or older. An ATA is a recorded fact, so a person compares "
     "the two and decides. The shipment shows as NEEDS REVIEW with both dates.\n\n"
     "An **ETA** can be updated, because estimates change. When only an ETA is found and the "
     "Hub already shows an arrival, the ETA is not written either."),
    ("carriers", "Which carriers are supported",
     r"\b(which|what) (carriers?|airlines?|shipping lines?|lines)( (are|do|does|is|can))?\b.*\b(support|automat|cover|track)|"
     r"\bsupported (carriers?|airlines?)\b|\bcarriers? (list|supported)\b|\blist (of )?(the )?carriers\b|"
     r"\bdo you (support|track|cover) (\w+ )?(carriers?|airlines?)\b",
     _carrier_answer),
    ("hold", "Why a carrier is on hold",
     r"\b(cma ?cgm|carrier)s? (is |are )?(on )?hold\b|\bwhy (is )?cma ?cgm\b.*\b(hold|held|skipped|not)\b|"
     r"\bheld carriers?\b|\bcarrier hold\b|\bwhy (is|are) (\w+ )?(held|on hold)\b",
     _hold_answer),
    ("limits", "Run limits",
     r"\b(run|write|hub) limits?\b|\bmax(imum)? (writes|records|shipments|look-?ups)\b|"
     r"\bhow many (shipments|records|writes) (per|in a|each) run\b|\bmax_hub_writes\b",
     _limits_answer),
    ("human_action", "How to handle a Human Action",
     r"\bhow (do|can|should) (i|we) (handle|complete|do|solve|clear|deal with) (a |the )?(human action|captcha|security check|verification)\b|"
     r"\bhuman action (process|steps|works?)\b|\bwhat (do|should) (i|we) do (when|if) .*(captcha|security check|human action)\b|"
     r"\bhow does human action work\b",
     "**When a carrier asks for a person** (a CAPTCHA or a code):\n\n"
     "1. The shipment pauses and appears under **Human Action**; the run carries on with the "
     "others.\n"
     "2. An Operator opens it (**Open Session** / Open & Continue) and completes the check in "
     "the browser, as they would by hand.\n"
     "3. Press **Continue**: the automation reads the result and carries on as usual.\n\n"
     "ATLAS and the automation **never** solve, type or store a security check. If nobody acts "
     "in time, the shipment is left for the next run and nothing is written. Viewers can see "
     "Human Actions but only Operators and Admins can complete them."),
    ("roles", "Roles and access",
     r"\b(access levels?|roles?|permissions?)\b|\bwho can (start|stop|send|approve|see|open|"
     r"manage|add|complete)\b|\b(viewer|operator|admin)s? (role|can|access)\b|\bwhat can (a |an )?"
     r"(viewer|operator|admin)\b",
     "**Three access levels:**\n\n"
     "• **Viewer:** sees the dashboard, shipments, PO status and history, exports CSV, asks "
     "ATLAS.\n"
     "• **Operator:** everything a Viewer can, plus start and stop runs, complete Human "
     "Actions, open screenshots, attach images in chat, and process and send POs.\n"
     "• **Admin:** everything, plus users, roles, settings, the audit log, approving ATLAS "
     "proposals and authorising a PO re-send.\n\n"
     "Everyone has their own account and every action is logged by name. On the shared "
     "office set-up, colleagues open the dashboard with the access key instead."),
    ("po_flow", "How PO Automation works",
     r"\bhow (does|do|is|are) (the )?(po|p\.o\.|purchase order|duty (payment )?request)s?( automation| process)? "
     r"(work|made|created|generated|prepared|done)\b|\bpo (process|flow|steps|automation works?)\b|"
     r"\bexplain (the )?(po|purchase order)s?( automation| process)?\b|\bwalk me through (the )?po\b",
     "**PO Automation, step by step:**\n\n"
     "1. **eHub:** opens the shipment. It must be exactly **Under Clearance**, otherwise it is "
     "SKIPPED and not opened.\n"
     "2. **Document:** Manage → Documents → the file whose name starts with **Bill Entry**. "
     "None: PDF_NOT_FOUND. Several: NEEDS_REVIEW, a person chooses.\n"
     "3. **Read and extract** the values from the PDF.\n"
     "4. **Validate** (see “How is a PO checked?”). Any failed check stops the job: nothing is "
     "generated, nothing is sent.\n"
     "5. **Template:** fills the approved **Duty Payment Request** and reads every cell back.\n"
     "6. **Save** to the output folder (never overwriting), then **email** through Microsoft "
     "365 when an Operator sends it, and confirm it in Sent Items.\n\n"
     "PDF_NOT_FOUND usually means the shipment wasn't Under Clearance yet, so the Bill of "
     "Entry wasn't there."),
    ("po_checks", "How a PO is checked",
     r"\bhow (is|are) (the |a )?(po|purchase order)s? (checked|validated|verified)\b|\bpo "
     r"(validation|checks|gate|rules)\b|\bvalidation gate\b|\bwhat (checks|rules) (does|do|are)( the)? po\b",
     "**A PO is generated only when every blocking check passes:**\n\n"
     "• Every required field is present and unambiguous.\n"
     "• The BL/AWB on the PDF matches the Hub (both shown; neither is picked for you).\n"
     "• The declaration number on the PDF matches the “Bill Entry” number in eHub.\n"
     "• The document's own sums add up (to within GHS 1.00).\n"
     "• On the ICUMS form, the invoice currency is USD and the exchange rate is for USD.\n"
     "• No amount is zero or negative.\n"
     "• The **Invoice No.** is taken only from an explicit “Invoice No.” on the Bill of Entry. "
     "If it isn't there, the PO goes to review; it is never filled from another field.\n\n"
     "Any failure stops at VALIDATION_FAILED with the reason. Ask “why wasn't PO <id> sent?”."),
    ("invoice", "Where the invoice number comes from",
     r"\binvoice (no|number|#)\b|\bwhere (does|is) the invoice\b",
     "The PO's **Invoice No.** comes only from a field explicitly labelled “Invoice No.” on the "
     "Bill of Entry. When the document has no such field, the PO goes to review. It is never "
     "taken from UNA+ or any other shipment field, and never guessed."),
    ("dashboard", "What the dashboard shows",
     r"\b(what|how) (does|do|is|are) (the )?(dashboard|counters|cards|kpis?|overview)\b|"
     r"\bexplain (the )?(dashboard|counters|overview)\b|\bwhat (do|does) (the )?(counters|numbers|cards) (mean|show)\b",
     "**The overview, top to bottom:**\n\n"
     "• **Counters:** Total shipments, Processing, Completed (written and read back), Waiting "
     "for human, Failed.\n"
     "• **Tracking:** the selected shipment's journey, carrier, ETA, source and its steps.\n"
     "• **On the way:** shipments with an ETA that haven't arrived.\n"
     "• **ATLAS card:** what ATLAS is doing and what it noticed.\n"
     "• **Ask ATLAS** (bottom right) opens this chat from any page.\n\n"
     "The top menu has **Shipments** (every shipment and its result), **PO Automation**, "
     "**ATLAS** and **Human Action**."),
    ("atlas", "How ATLAS works",
     r"\bhow (do|does) (you|atlas) work\b|\bwhere (do|does) (you|atlas) get (the |your |its )?"
     r"(answers?|information|data|facts)\b|\bwhat can (you|atlas) do\b.*\b(skills?|features?)\b|"
     r"\b(your|atlas'?s?) (skills|features|abilities)\b|\bare you (an )?ai\b",
     "I answer from the **run's own records**: every shipment, date, write, failure, Human "
     "Action and PO job the system recorded. I never invent a number, date or result; when I "
     "can't confirm something I say so.\n\n"
     "**What I can do:**\n"
     "• Answer about shipments, failures, carriers, POs and how the system works.\n"
     "• **Validate** a shipment's dates or a date you type: “validate MEDUAHP69377”.\n"
     "• Make a **PDF report**: “PDF report of the run”, “PDF for <shipment>”.\n"
     "• Show **screenshots** the run captured: “screenshot for <shipment>”.\n"
     "• **Read a photo** or screenshot you attach (OCR, plus the AI vision model when set up).\n"
     "• **Search the web** and carrier sites for advisories and logistics information, when "
     "web search is set up on the PC.\n\n"
     "With a local AI model I also understand free wording and Arabic; without one I still "
     "answer from the rules."),
    ("web", "Web search",
     r"\b(can|do) (you|atlas) (search|browse|google|go online|use the (web|internet))\b|"
     r"\b(web|internet|online) search\b|\bset ?up (the )?(web )?search\b|\bsearxng\b",
     "I can search the web, carrier websites and logistics sources through the Control Tower's "
     "**own private search** (SearXNG), which runs on the same PC. No paid service is used.\n\n"
     "• Ask “search the web for Tema port congestion” or “search MSC website for "
     "advisories”.\n"
     "• Results are always labelled **web**, with their source, and never count as run "
     "facts.\n"
     "• I read only official pages (carriers' notices, port authorities, documentation). I "
     "never open a tracking page; the automation does that, with its own checks.\n\n"
     "**To turn it on:** on the tower PC, double-click **SETUP_WEB_SEARCH.bat** once. After "
     "that the tower starts it by itself."),
    ("model", "The AI model",
     r"\b(ai|language|local) model\b|\bollama\b|\bqwen\b|\bgpt-oss\b|\b(stronger|smarter|better) model\b|"
     r"\bwhich model\b",
     "ATLAS can use a **local AI model** through Ollama (free, on the same PC, nothing leaves "
     "the building). It chooses the model automatically: on a machine with enough memory (about "
     "32 GB) it downloads and switches to the stronger qwen3:30b by itself, and falls back to the "
     "smaller model if the strong one keeps timing out.\n\n"
     "The model only helps with wording and understanding: every fact still comes from the run "
     "records and passes the fact guard. Set it up with **SETUP_ATLAS_AI.bat**."),
    ("photos", "Photos and screenshots",
     r"\b(can|do) (you|atlas) (read|understand|see|look at|check) (photos?|images?|pictures?|screenshots?)\b|"
     r"\bhow (do|can) (i|we) (upload|attach|send) (a )?(photo|image|picture|screenshot)\b",
     "Yes. **Attach a photo or screenshot** with the paperclip in the chat and ask about it: "
     "“what does this show?”, “what's the ETA on this?”.\n\n"
     "• I read the text with OCR (Tesseract, when installed) and, if set up, the local AI "
     "vision model. I say what I can see and flag anything I can't read reliably.\n"
     "• A security check (CAPTCHA, code) in a picture is never read.\n"
     "• To see what the run itself captured, ask “screenshot for <shipment>”."),
    ("pdf_how", "PDF reports",
     r"\b(can|do) (you|atlas) (make|create|give|produce|export|generate) (a )?(pdf|reports?)\b",
     "Yes. Ask “**PDF report of the run**”, “**PDF of the failed shipments**”, “**PDF for "
     "<shipment>**” or “**PDF of the review list**”, and I'll give you a download button. The "
     "PDF holds only what the run recorded, with the Mantrac logo and the time it was made."),
    ("not_reachable", "The dashboard won't open",
     r"\b(can'?t|cannot|unable to|not able to|couldn'?t) (open|reach|connect( to)?|access|load|see) (the )?(dashboard|tower|link|page)\b|"
     r"\bdashboard (is )?(not|isn'?t|won'?t) (loading|opening|working|load|open)\b|\bfirewall\b|\bport 8787\b",
     "**If a colleague can't open the dashboard:**\n\n"
     "• Use the link the tower's window prints: **http://<machine-name>:8787**, with the access "
     "key if there is one.\n"
     "• Windows Firewall is the usual cause: START_SHARED.bat / INSTALL_SERVER.bat open port "
     "8787 to the company network; run it again and accept the administrator prompt.\n"
     "• The tower PC must be on, awake and on the company network.\n"
     "• Run **check_server.bat** on the tower PC: each line says PASS, WARN or FAIL."),
    ("playwright", "“No module named playwright”",
     r"\bno module named\b|\bplaywright (error|missing|not (installed|found))\b|\bmodulenotfounderror\b",
     "That means the Python that started the tower doesn't have Playwright. Use the latest "
     "**START_TOWER.bat** / **START_SHARED.bat**: they look for the Python that has it (py or "
     "python). If it still happens, run `py -m pip install -r requirements.txt` and "
     "`py -m playwright install chromium` once."),
    ("rdp", "Remote Desktop",
     r"\bremote desktop\b|\brdp\b|\bedge (froze|freezes|frozen|stuck)\b|\bdisconnect(_keep_running)?\b",
     "On the server, **don't close or minimise the Remote Desktop window** while a run may be "
     "going: Windows can stop drawing the session and Edge freezes. Double-click "
     "**disconnect_keep_running.bat** instead. You are disconnected and the automation keeps "
     "working."),
    ("data_where", "Where data and logs are kept",
     r"\bwhere (are|is|do|does) (the )?(logs?|data|files?|output|reports?|backups?|templates?|screenshots?)"
     r"(\s(kept|stored|saved|live|go))?\b|\blog folder\b|\bbackups?\b",
     "• **Run logs and credentials:** C:\\Automation (logs in C:\\Automation\\logs).\n"
     "• **PO outputs:** the PO output folder set on the PO page; files are never overwritten.\n"
     "• **ATLAS memory, evidence and screenshots:** the tower's intelligence folder.\n"
     "• **Back up:** C:\\Automation, ml\\data, controlplane\\data and the PO data folder.\n\n"
     "CSV exports and PDF reports download straight from the dashboard or this chat."),
    ("update_tower", "Installing an update",
     r"\bhow (do|can|should) (i|we) (update|upgrade|install) (the )?(tower|system|software|zip|new version)\b|"
     r"\binstall (the )?(new|latest) (version|zip|release)\b",
     "**To install a new version:**\n\n"
     "1. Stop the tower (on the server: Task Scheduler → Mantrac Tower → End; on a PC: close "
     "its window).\n"
     "2. Replace the tower folder with the new ZIP's contents. Keep C:\\Automation.\n"
     "3. Start it again (START_TOWER.bat, or sign in on the server).\n"
     "4. Press **Ctrl+F5** in the browser so the dashboard reloads.\n\n"
     "Run **check_server.bat** afterwards to confirm everything is healthy."),
    ("share", "Giving colleagues access",
     r"\baccess key\b|\bshare (the )?(dashboard|tower|link)\b|\bgive (someone|colleagues|the team|people|them) access\b|"
     r"\bhow (do|can) (others|colleagues|people|the team) (open|see|access|use)\b",
     "• **Shared office set-up:** colleagues open **http://<machine-name>:8787** with the "
     "access key. A new key: delete dashboard\\.runtime\\access_key and restart; everyone then "
     "needs the new link.\n"
     "• **Platform set-up:** an Admin creates each person's account (Viewer, Operator or "
     "Admin) under Access; they get a one-time link to set their password."),
    ("needs_review_fix", "What to do with a NEEDS REVIEW shipment",
     r"\bwhat (do|should) (i|we) do (with|about) (a )?(needs review|review|flagged)\b|"
     r"\bhow (do|can|should) (i|we) (fix|resolve|handle|clear) (a )?(needs review|review)\b",
     "**For a NEEDS REVIEW shipment:**\n\n"
     "1. Read the reason on the shipment (or ask me “why was <shipment> flagged?”).\n"
     "2. Open the carrier's page and the Hub, and compare the dates yourself.\n"
     "3. If the carrier's date is right, enter it in the Hub by hand; if not, leave it.\n\n"
     "The automation wrote nothing for that shipment, so there is nothing to undo."),
]

_COMPILED = [(k, t, re.compile(p, re.I), a) for k, t, p, a in TOPICS]


def topic(question):
    """The handbook topic a question is about: (key, title, answer) or None."""
    text = (question or "").replace("\u2019", "'").strip()
    if not text:
        return None
    for key, title, pattern, answer in _COMPILED:
        if pattern.search(text):
            body = answer() if callable(answer) else answer
            return key, title, body
    return None


def asks_how(question):
    return bool(HOW_ASK.search((question or "").replace("\u2019", "'")))


# ── the project's own guides, searched section by section ─────────────────
GUIDES = ("START_HERE.md", "README.md", "PO.md", "SERVER_SETUP.md", "PLATFORM.md",
          "ARCHITECTURE.md", "PO_WORKER_RUNBOOK.md", "ML.md")
STOP = set("""a an the and or of to in on for is are was were be been it its this that these
those what which who how why when where do does did can could should would i we you he she
they me my our your with from by at as about into than then there here not no yes if so
just please tell show me give get explain much many any some all one""".split())
_index = {"key": None, "sections": [], "df": {}}


def _words(text):
    return [w for w in re.findall(r"[a-z0-9][a-z0-9_\-]*", text.lower()) if w not in STOP
            and len(w) > 1]


def _sections():
    files = [ROOT / name for name in GUIDES if (ROOT / name).exists()]
    key = tuple((str(f), f.stat().st_mtime) for f in files)
    if _index["key"] == key:
        return _index["sections"], _index["df"]
    sections = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title, body = f.stem, []
        for line in text.splitlines() + ["# END"]:
            if re.match(r"^#{1,4} ", line):
                content = "\n".join(body).strip()
                if len(content) > 80:
                    words = _words(title + " " + title + " " + content)
                    sections.append({"doc": f.name, "title": title, "text": content,
                                     "words": words, "tf": _tf(words)})
                title, body = line.lstrip("#").strip(), []
            else:
                body.append(line)
    df = {}
    for s in sections:
        for w in s["tf"]:
            df[w] = df.get(w, 0) + 1
    _index.update(key=key, sections=sections, df=df)
    return sections, df


def _tf(words):
    out = {}
    for w in words:
        out[w] = out.get(w, 0) + 1
    return out


def search_guides(question, limit=1):
    """The best sections of the project's guides for a question (BM25)."""
    sections, df = _sections()
    if not sections:
        return []
    terms = list(dict.fromkeys(_words(question)))
    if not terms:
        return []
    n = len(sections)
    avg = sum(len(s["words"]) for s in sections) / float(n)
    scored = []
    for s in sections:
        score, hits = 0.0, 0
        for t in terms:
            f = s["tf"].get(t)
            if not f:
                continue
            hits += 1
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * len(s["words"]) / avg))
        if hits:
            scored.append((score * hits / len(terms), s))
    scored.sort(key=lambda x: -x[0])
    return [dict(s, score=round(sc, 2)) for sc, s in scored[:limit]]


def _excerpt(text, limit=900):
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    cut = cut[:max(cut.rfind("\n"), cut.rfind(". ") + 1, limit // 2)]
    return cut.rstrip() + " …"


GUIDE_MIN_SCORE = 2.2


def answer(question, how_required=True):
    """
    {"answer", "intent", "topic"} for a question about how the system works,
    or None. A topic match needs a how/what/why question unless
    how_required is False; the guides are searched only when asked to.
    """
    if how_required and not asks_how(question):
        return None
    hit = topic(question)
    if hit:
        key, title, body = hit
        return {"answer": body, "intent": "handbook", "topic": key, "title": title}
    return None


def from_guides(question):
    """The best guide section for the question, quoted with its source, or None."""
    best = search_guides(question)
    if not best or best[0]["score"] < GUIDE_MIN_SCORE:
        return None
    s = best[0]
    return {"answer": "From the project guide (**{0} › {1}**):\n\n{2}".format(
                s["doc"], s["title"], _excerpt(s["text"])),
            "intent": "handbook", "topic": "guide:" + s["doc"], "title": s["title"]}
