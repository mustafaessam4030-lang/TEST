"""
ATLAS's skills in the chat — things it DOES, beside what it answers:

    VALIDATE    "validate MEDUAHP69377", "is the ETA for K358403 right?",
                "check all the dates", "is 31/02/2026 a valid ATA?"
                The same rules the automation applies before a write
                (arrival_events.basic_check), run again now on what the run
                recorded, plus what the run itself recorded: ATLAS's DHL check,
                the Hub write and its read-back. Each line says PASS, FAIL or
                INFO. Nothing is written or changed.
    PDF         "PDF report of the run", "PDF of the failed shipments",
                "PDF for MEDUAHP69377" -> a download button
                (/api/report.pdf, intelligence/pdf_report.py)
    SCREENSHOT  "screenshot for MEDUAHP69377" -> the captures the run took
                for that shipment (the evidence store), shown in the chat
    WEB         "search the web for Tema port congestion", "search MSC
                website for advisories" -> the self-hosted search
                (intelligence/research.py), carrier sites by site: filter;
                results labelled web, never run facts. Without a search
                service: the official links, and how to turn search on.
    HANDBOOK    "how does the PO process work?", "which carriers do you
                support?" -> intelligence/handbook.py

All read-only. A carrier TRACKING page is never opened from the chat: that is
the automation's job, behind its own checks and Human Action rules.
"""

import re
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PO_WORDS = re.compile(r"\b(po|p\.o\.|pos|purchase orders?|duty (payment )?requests?|bill of entry|"
                      r"boe|declarations?)\b", re.I)
DATE = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b")

PDF_ASK = re.compile(
    r"\b(make|create|generate|produce|give|send|export|download|print|prepare|want|need|get)\b"
    r"[^.?!]{0,40}\bpdf\b|\bpdf (report|summary|version|copy|file)\b|\b(as|in|to) (a )?pdf\b|"
    r"\breport (as|in) (a )?pdf\b|\bprint(able)? (the |a )?report\b|^\s*pdf\b|\bpdf (of|for)\b", re.I)
SHOT_ASK = re.compile(
    r"\b(show|see|send|give|get|have|any|view|open|where (is|are))\b[^.?!]{0,30}"
    r"\b(screen ?shots?|captures?|snapshots?|photos?|pictures?|images?)\b|"
    r"\b(screen ?shots?|captures?) (of|for|from)\b", re.I)
VALIDATE_ASK = re.compile(
    r"\b(validate|validation|re-?validate|re-?check|double[- ]check|verify|sanity[- ]check)\b|"
    r"\bcheck ((the|its|this|that|all|every|each|all the|all of the)\s+)?(shipments?|dates?|etas?|atas?|it|them|run)\b|"
    r"\bcheck (\w+[- ]?\d[\w-]*)\b|"
    r"\bis (the |its |this |that )?(eta|ata|date)s?( (for|of|on) [\w-]+)? (correct|right|valid|ok|okay|good|wrong|sound)\b|"
    r"\b(are|were) (the |its |these |those )?(dates?|etas?|atas?) (correct|right|valid|ok|okay|good|wrong)\b|"
    r"\b(valid|invalid|correct|wrong) (eta|ata|date)\b", re.I)
WEB_ASK = re.compile(
    r"\b(search|google|browse|look ?up|find)\b[^.?!]{0,40}\b(web|internet|online|websites?|sites?|google|news)\b|"
    r"\bsearch (the )?(web|internet|online)\b|\bsearch (for|about)\b|\bgoogle\b|"
    r"\bon (the )?[\w\- ]{2,25} (website|site|web ?site)\b|\b(latest|any) (news|advisor(y|ies)|notices?|alerts?)\b|"
    r"\b(port|terminal) (congestion|delays?|strike|closure|notice)s?\b", re.I)
DATE_ASK = re.compile(r"\b(valid|invalid|ok|okay|correct|right|wrong|check|validate|verify|real|"
                      r"allowed|acceptable)\b", re.I)
CAN_YOU_SEARCH = re.compile(r"^\s*(can|could|do|will|are) (you|atlas)( be able to)? (search|browse|"
                            r"google|go online|look things up|use the (web|internet))"
                            r"( (on )?the (web|internet)| online| websites?)?\s*\??\s*$", re.I)
ALL_WORDS = re.compile(r"\b(all|every|each|the run|this run|whole run|everything|them)\b", re.I)
CLEARANCE_ASK = re.compile(
    r"\b(clearance|customs|clearing agents?|duty (payment|assessment)|submit(ted)? to (the )?bu|"
    r"assign(ing)? (the )?clearing|ehub export|shipment update export)\b", re.I)
# The run, its Human Actions, the PO worker and sign-in are not the export.
NOT_CLEARANCE = re.compile(r"\b(this run|the run|run (fail|stop)\w*|human action|log ?in|sign(ed)? in|worker|"
                           r"job|carriers?)\b", re.I)
# "How does clearance work?" is for the handbook, not the export's figures.
HOW_IT_WORKS = re.compile(r"\bhow (does|do|is|are)\b[^?]{0,40}\bwork|\bexplain\b|"
                          r"\bwhat (is|are|does) (a |the )?(customs|clearance)\b", re.I)


# Asked on the Clearance page about what is on screen: the view's filters apply.
VIEW_ASK = re.compile(r"\b(this|the|my|current) (view|page|dashboard|screen|selection|filters?|list|table)\b|"
                      r"\bwhat am i (looking at|seeing)\b|\bwhat (do|can) i see\b|\bsummari[sz]e\b|"
                      r"\bwhat stands out\b|\bon (my |the )?screen\b|\bhere\b", re.I)
# "overall", "all BUs": the whole export, whatever the page is filtered to.
WHOLE_ASK = re.compile(r"\b(overall|in total|whole|entire|every bu|all (bus?|business units|"
                       r"shipments|modes|divisions|of them)|ignore (the |my )?filters?|without (the |my )?filters?)\b", re.I)
VIEW_FLAGS = ("open", "pastdue", "done", "late")


def clean_view(view, values):
    """The page's filters, kept only where they name something in the export."""
    out = {}
    if not isinstance(view, dict):
        return out
    from dashboard import clearance
    for key in ("bu", "mode", "div", "ffw", "agent", "status"):
        v = view.get(key)
        if isinstance(v, str) and v in values.get(key, ()):
            out[key] = v
    if view.get("group") in clearance.GROUPS:
        out["group"] = view["group"]
    if isinstance(view.get("month"), str) and re.match(r"^20\d\d-(0[1-9]|1[0-2])$", view["month"]):
        out["month"] = view["month"]
    if view.get("flag") in VIEW_FLAGS:
        out["flag"] = view["flag"]
    return out


def view_has_more(view):
    """The screen also has a filter ATLAS cannot count by (a search, a missed
    stage, "on time" only, escalated or slow)."""
    return isinstance(view, dict) and view.get("more") == "1"


# ── carriers and resources ───────────────────────────────────────────────

def carrier_sites():
    """{label: url} of the carriers' own sites, read from update_eta.py."""
    out = {}
    try:
        text = (ROOT / "update_eta.py").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for m in re.finditer(r'"label":\s*"([^"]+)"(.{0,600}?)"(https://[^"]+)"', text, re.S):
        out.setdefault(m.group(1), m.group(3))
    out.setdefault("AFKL myCargo", "https://www.afklcargo.com/")
    out.setdefault("DHL", "https://www.dhl.com/")
    out.setdefault("Qatar Airways Cargo", "https://www.qrcargo.com/")
    return out


ALIASES = {
    "msc": "MSC", "maersk": "Maersk", "cma": "CMA CGM", "cma cgm": "CMA CGM", "cosco": "COSCO Shipping",
    "hapag": "Hapag-Lloyd", "hapag-lloyd": "Hapag-Lloyd", "one": "Ocean Network Express (ONE)",
    "ocean network express": "Ocean Network Express (ONE)", "grimaldi": "Grimaldi Lines",
    "dhl": "DHL", "qatar": "Qatar Airways Cargo", "emirates": "Emirates SkyCargo",
    "lufthansa": "Lufthansa Cargo", "turkish": "Turkish Cargo", "ethiopian": "Ethiopian Cargo",
    "egyptair": "EgyptAir Cargo", "saudia": "Saudia Cargo", "rwandair": "RwandAir Cargo",
    "air france": "AFKL myCargo", "klm": "AFKL myCargo", "afkl": "AFKL myCargo",
    "virgin": "Virgin Atlantic Cargo", "astral": "Astral Aviation",
    "south african": "South African Airways Cargo",
}

RESOURCES = [
    ("World Customs Organization — HS codes", "https://www.wcoomd.org/", r"\bhs ?codes?\b|\btariff\b|\bcustoms\b|\bharmoni[sz]ed\b"),
    ("ICC — Incoterms rules", "https://iccwbo.org/", r"\bincoterms?\b|\b(fob|cif|exw|ddp|dap|fca|cfr)\b"),
    ("IATA — air cargo", "https://www.iata.org/", r"\biata\b|\bair (cargo|waybill)\b|\bawb\b|\bdangerous goods\b"),
    ("IMO — shipping rules", "https://www.imo.org/", r"\bimo\b|\bimdg\b|\bsolas\b|\bmaritime\b"),
    ("Ghana Revenue Authority — Customs", "https://gra.gov.gh/", r"\bgra\b|\bghana customs\b|\bduty\b|\bicums\b|\bclearance\b"),
    ("Ghana Ports and Harbours Authority", "https://ghanaports.gov.gh/", r"\btema\b|\btakoradi\b|\bghana ports?\b|\bport\b|\bgpha\b"),
    ("UN/LOCODE — port and place codes", "https://unece.org/", r"\blocode\b|\bport codes?\b|\bplace codes?\b"),
]


def _carrier_in(text):
    lowered = " " + (text or "").lower() + " "
    sites = carrier_sites()
    for alias in sorted(ALIASES, key=len, reverse=True):
        if re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", lowered):
            label = ALIASES[alias]
            if alias == "one" and not re.search(r"\b(one|ONE) (line|website|site|ocean)\b|\bONE\b",
                                                text or ""):
                continue
            url = sites.get(label) or next((u for l, u in sites.items() if label.split()[0] in l), None)
            return label, url
    return None, None


def _domain(url):
    from urllib.parse import urlparse
    host = (urlparse(url or "").hostname or "").lower()
    parts = host.split(".")
    if len(parts) > 2 and parts[-2] in ("co", "com", "gov", "org") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


# ── detection ─────────────────────────────────────────────────────────────

def detect(question, data=None, context=None):
    """The skill a question asks for, or None."""
    q = (question or "").replace("’", "'").strip()
    if not q:
        return None
    context = context or {}
    if PDF_ASK.search(q) and not (PO_WORDS.search(q) and not re.search(r"\breport\b", q, re.I)):
        return "pdf"
    if context.get("evidence_id") and re.search(r"\b(this|that|the|attached|my)\b.{0,15}"
                                                r"\b(photo|picture|image|screen ?shot)\b", q, re.I):
        return None                      # about the image just attached: atlas_learning
    if SHOT_ASK.search(q) and _reference(q, data, context, named_only=True):
        return "screenshot"
    if DATE.search(q) and DATE_ASK.search(q) and not PO_WORDS.search(q) and \
            not _reference(q, data, context, named_only=True):
        return "validate_date"
    if VALIDATE_ASK.search(q) and not PO_WORDS.search(q) and \
            not re.search(r"\b(did|has|have) atlas\b|\bwas (it|the \w+) verified\b|"
                          r"\bverification (result|status)\b", q, re.I):
        if DATE.search(q) and not _reference(q, data, context, named_only=True):
            return "validate_date"
        if _reference(q, data, context) or ALL_WORDS.search(q):
            return "validate"
    if _clearance_named(q, data, context):
        return "clearance"
    if CLEARANCE_ASK.search(q) and not PO_WORDS.search(q) and not WEB_ASK.search(q) and \
            not _reference(q, data, context, named_only=True) and not HOW_IT_WORKS.search(q) and \
            not re.search(r"\bunder clearance\b", q, re.I) and \
            (context or {}).get("domain") != "po" and not NOT_CLEARANCE.search(q):   # the run, PO, sign-in
        return "clearance"
    if WEB_ASK.search(q) and not CAN_YOU_SEARCH.search(q):
        return "web"
    return None


def _clearance_named(q, data, context):
    """A question about the loaded eHub export (dashboard/clearance.py), not the run, PO or handbook."""
    try:
        from dashboard import clearance
        if clearance.load() is None:
            return False
        context = context or {}
        if context.get("clearance") == "0":           # a role without clearance.view
            return False
        on_page = context.get("page") == "clearance"
        # "Summarise this view", "explain what I'm looking at": the page itself.
        # Not a PO question, a run shipment, the run, or how something works.
        if on_page and VIEW_ASK.search(q) and not NOT_CLEARANCE.search(q) and \
                context.get("domain") != "po" and \
                not re.search(r"\b(po|p\.o\.|purchase orders?|boe|bill of entry)\b", q, re.I) and \
                not _reference(q, data, context, named_only=True) and \
                not re.search(r"\bhow (does|do|is|are)\b[^?]{0,40}\bwork", q, re.I):
            return True
        # Never the PO page's questions, the run's, sign-in, or how something works.
        if context.get("domain") == "po" or HOW_IT_WORKS.search(q) or NOT_CLEARANCE.search(q) or \
                (re.search(r"\bunder clearance\b", q, re.I) and not on_page):
            return False
        if _reference(q, data, context, named_only=True):
            return False
        po = re.search(r"\b(po|p\.o\.|purchase orders?|boe|bill of entry)\b", q, re.I) if on_page else PO_WORDS.search(q)
        if po:
            return False
        if re.search(r"\bescalat\w*", q, re.I):
            return True
        # Asked from the Clearance page: a question that names something in the
        # export (a BU, mode, step, agent, forwarder, month) or asks for shipments.
        if on_page:
            whole = clearance.query(None)
            if whole and (_cl_filters(q, dict(whole["values"], years=[])) or
                          re.search(r"\b(shipments?|how many|which|list|show|top|worst|summary|status)\b", q, re.I)):
                return True
        # The export's own words: the run has carriers, not forwarders or clearing agents.
        if re.search(r"\b(past[- ]?due|forwarders?|clearing agents?|ehub export)\b", q, re.I):
            return True
        # Anywhere: a question the run's own rules do not recognise that names a
        # BU or the export's measures ("which BU is the worst?", "how is EG
        # doing?", "top 5 late shipments") is about the export: answered from
        # it at once, instead of going to the model and finding nothing.
        # While a run has shipments, "air shipments", "failed ones"… are the
        # run's own filters (assistant._filter_of): those stay with the run.
        from dashboard.assistant import detect_intent, _filter_of
        run_busy = bool(getattr(data, "shipments", None)) and bool(_filter_of(data, q))
        if detect_intent(q) is None and not run_busy and not CL_NOT.search(q) and not WEB_ASK.search(q):
            whole = clearance.query(None)
            named = whole and _cl_filters(q, dict(whole["values"], years=[]))
            if (named and named.get("bu") and CL_SHIP.search(q)) or CL_CUE.search(q):
                return True
        return bool(clearance.awbs_in(q))
    except Exception:
        return False


def _reference(question, data, context, named_only=False):
    """The shipment a question names (or 'this one' from the conversation)."""
    if data is None:
        return None
    for candidate in data.references_in(question):
        if data.find(candidate) is not None:
            return data.find(candidate).get("reference")
    if named_only:
        return None
    if re.search(r"\b(this|that|it|its|the)\s+(shipment|one|awb|bol)\b|\bit\b|\bits\b", question, re.I):
        ref = (context or {}).get("reference")
        if ref and data.find(ref) is not None:
            return data.find(ref).get("reference")
    return None


# ── validation ────────────────────────────────────────────────────────────

def _has(value):
    return value not in (None, "", "-", "—")


def shipment_checks(record, today=None):
    """[(PASS|FAIL|INFO, text)] for one shipment, from its record."""
    import arrival_events as AE
    today = today or date.today()
    out = []
    eta = record.get("provider_eta") if _has(record.get("provider_eta")) else None
    ata = record.get("provider_ata") if _has(record.get("provider_ata")) else None
    state = record.get("state")
    if state in ("processing", "waiting_for_human", "queued"):
        out.append(("INFO", "Still in progress ({0}): the checks run before anything is "
                            "written.".format(state.replace("_", " "))))
    if not eta and not ata:
        out.append(("INFO", "The carrier gave no ETA or ATA, so there was no date to check "
                            "or write."))
    for name, value in (("ETA", eta), ("ATA", ata)):
        if value:
            if AE._day(value):
                out.append(("PASS", "{0} {1} is a real, complete dd/mm/yyyy date.".format(name, value)))
            else:
                out.append(("FAIL", "{0} '{1}' is not an unambiguous dd/mm/yyyy date.".format(name, value)))
    if ata and AE._day(ata):
        if AE._day(ata) > today:
            out.append(("FAIL", "ATA {0} is in the future.".format(ata)))
        else:
            out.append(("PASS", "ATA {0} is not in the future.".format(ata)))
    if eta and ata:
        if eta == ata:
            out.append(("FAIL", "ETA and ATA are the same date ({0}): the estimate may have been "
                                "read as the arrival, so a person checks it.".format(eta)))
        else:
            out.append(("PASS", "ETA and ATA are different dates."))
    if record.get("provider_ata_source"):
        out.append(("INFO", "ATA read from the carrier's '{0}'.".format(record["provider_ata_source"])))
    if record.get("provider_eta_source"):
        out.append(("INFO", "ETA read from the carrier's '{0}'.".format(record["provider_eta_source"])))
    check = record.get("atlas_check") or {}
    verdict = check.get("verdict")
    if verdict == "AGREES":
        out.append(("PASS", "ATLAS read the carrier page itself and agreed with the dates."))
    elif verdict == "AGREES_WITH_NOTES":
        out.append(("PASS", "ATLAS agreed, with a note: " + "; ".join(check.get("notes") or [])))
    elif verdict:
        out.append(("FAIL", "ATLAS's own reading: {0}.".format(verdict)))
    error = str(record.get("error") or "")
    if "NEEDS REVIEW" in error.upper():
        out.append(("FAIL", "Sent to a person, nothing written: " + error.split(":", 1)[-1].strip()))
    written = [a for a in (record.get("coe_action"), record.get("bu_action")) if _has(a)]
    verification = record.get("verification") or {}
    if written:
        out.append(("PASS", "Written to the Hub: " + "; ".join(str(a) for a in written) + "."))
        if verification and all(v is True for v in verification.values()):
            out.append(("PASS", "Read back from the Hub and it matched."))
        elif verification and any(v is False for v in verification.values()):
            out.append(("FAIL", "The Hub read-back did not match what was written."))
        elif state == "updated":
            out.append(("PASS", "Recorded as SUCCESS: the run counts a write as done only after "
                                "the read-back."))
        else:
            out.append(("INFO", "No read-back result is recorded for this write."))
    elif state in ("updated", "partial", "failed", "skipped", "human_timeout"):
        out.append(("INFO", "Nothing was written to the Hub for this shipment."))
    if _has(record.get("internal_eta")):
        out.append(("INFO", "The Hub's ETA before this run: {0}.".format(record["internal_eta"])))
    return out


def _mark(status):
    return {"PASS": "✅ PASS", "FAIL": "❌ FAIL"}.get(status, "ℹ️ INFO")


def validate_one(data, reference):
    record = data.find(reference)
    checks = shipment_checks(record)
    fails = [t for s, t in checks if s == "FAIL"]
    head = ("**{0}: every check passes.**".format(record.get("reference")) if not fails else
            "**{0}: {1} check(s) need attention.**".format(record.get("reference"), len(fails)))
    lines = [head, ""] + ["{0} — {1}".format(_mark(s), t) for s, t in checks]
    lines += ["", "_Checked again now with the same rules the automation uses before a write. "
                  "Nothing was changed._"]
    return "\n".join(lines)


def validate_run(data):
    rows = [r for r in data.shipments if _has(r.get("provider_eta")) or _has(r.get("provider_ata"))]
    if not rows:
        return ("No shipment in this run has a carrier date yet, so there is nothing to validate. "
                "Ask me again once the run has read some dates.")
    clean, flagged = [], []
    for r in rows:
        fails = [t for s, t in shipment_checks(r) if s == "FAIL"]
        (flagged if fails else clean).append((r, fails))
    lines = ["**Validated {0} shipment(s) with dates: {1} pass every check, {2} need "
             "attention.**".format(len(rows), len(clean), len(flagged))]
    if flagged:
        lines += ["", "**Need attention**"]
        lines += ["• {0} — {1}".format(r.get("reference"), fails[0]) for r, fails in flagged[:12]]
        if len(flagged) > 12:
            lines.append("• … and {0} more (ask for the PDF of the review list).".format(len(flagged) - 12))
    if clean:
        lines += ["", "**Pass** — " + ", ".join(r.get("reference") for r, _f in clean[:20]) +
                  (" …" if len(clean) > 20 else "")]
    lines += ["", "Ask “validate <reference>” for the full list of checks on one shipment."]
    return "\n".join(lines)


def validate_date(question):
    import arrival_events as AE
    m = DATE.search(question)
    raw = m.group(0)
    day, month, year = m.group(1), m.group(2), m.group(3)
    which = "ATA" if re.search(r"\bata\b|\barriv(al|ed)\b", question, re.I) else \
        "ETA" if re.search(r"\beta\b|\bestimat", question, re.I) else None
    lines = []
    if len(year) != 4:
        lines.append("❌ FAIL — '{0}' has a two-digit year. The automation only accepts a full "
                     "dd/mm/yyyy date, so it would not write it.".format(raw))
        return "\n".join(lines)
    canonical = "{0:02d}/{1:02d}/{2}".format(int(day), int(month), year)
    parsed = AE._day(canonical)
    if not parsed:
        lines.append("❌ FAIL — {0} is not a real calendar date (day {1}, month {2}). It would "
                     "never be written, and never repaired.".format(raw, int(day), int(month)))
        return "\n".join(lines)
    lines.append("✅ PASS — {0} is a real date: {1}.".format(canonical, parsed.strftime("%A %d %B %Y")))
    if int(day) <= 12 and int(month) <= 12 and day != month:
        lines.append("ℹ️ INFO — read as day/month (dd/mm/yyyy), the Hub's format: {0}, not "
                     "{1}.".format(parsed.strftime("%d %B"), date(parsed.year, int(day), int(month))
                                   .strftime("%d %B") if int(day) <= 12 else ""))
    today = date.today()
    if which == "ATA":
        if parsed > today:
            lines.append("❌ FAIL — as an ATA it is in the future; an arrival can't be after today "
                         "({0}).".format(today.strftime("%d/%m/%Y")))
        else:
            lines.append("✅ PASS — as an ATA it is not in the future.")
    elif which == "ETA":
        if parsed < today:
            lines.append("ℹ️ INFO — as an ETA it is in the past. It isn't blocked, but it usually "
                         "means the carrier has a newer date or the shipment has arrived.")
        else:
            lines.append("✅ PASS — as an ETA it is in the future, as an estimate should be.")
    else:
        lines.append("ℹ️ INFO — say whether it's an ETA or an ATA and I'll check that rule too "
                     "(an ATA can't be in the future).")
    return "\n".join(lines)


# ── PDF ───────────────────────────────────────────────────────────────────

def pdf_offer(question, data, context):
    ref = _reference(question, data, context)
    if ref and not re.search(r"\b(run|all|failed|failures|review)\b", question, re.I):
        return ("Here's the PDF for **{0}**: its details, the checks, the steps and any screenshots "
                "the run captured.".format(ref),
                [{"label": "PDF — {0}".format(ref), "url": "/api/report.pdf?kind=shipment&ref=" + ref}])
    if re.search(r"\bfail", question, re.I):
        n = len([r for r in data.shipments if r.get("state") in ("failed", "human_timeout")])
        return ("Here's the PDF of the failed shipments ({0}).".format(n),
                [{"label": "PDF — failed shipments", "url": "/api/report.pdf?kind=failed"}])
    if re.search(r"\breview|flagged\b", question, re.I):
        n = len([r for r in data.shipments if "NEEDS REVIEW" in str(r.get("error") or "").upper()])
        return ("Here's the PDF of the shipments sent for review ({0}).".format(n),
                [{"label": "PDF — review list", "url": "/api/report.pdf?kind=review"}])
    if not data.shipments:
        return ("No shipment has been processed in this run yet, so the report would be empty. "
                "Ask me again once the run has started.", [])
    return ("Here's the PDF report of this run: the counts, every shipment with its result and "
            "dates, and what needs attention ({0} shipments).".format(len(data.shipments)),
            [{"label": "PDF — run report", "url": "/api/report.pdf?kind=run"}])


# ── screenshots ───────────────────────────────────────────────────────────

def screenshots(reference):
    try:
        from intelligence import evidence as EV
    except Exception:
        return [], "the evidence store isn't available here"
    found = [e for e in EV.search(reference=reference, limit=20)
             if str(e.get("mime") or "image/").startswith("image/")]
    found.sort(key=lambda e: str(e.get("at") or ""), reverse=True)
    return [EV.public(e) for e in found[:4]], None


# ── web ───────────────────────────────────────────────────────────────────

LEAD = re.compile(r"^\s*(please\s+)?(can you\s+|could you\s+)?(search|google|browse|look ?up|find)"
                  r"(\s+(the|on the|in the))?(\s+(web|internet|online|google))?(\s+(for|about))?\s+", re.I)


def web_query(question):
    q = LEAD.sub("", (question or "").strip()).rstrip("?.! ")
    q = re.sub(r"\b(on|in|from) (the )?[\w\- ]{2,25} (website|site|web ?site)\b", "", q, flags=re.I)
    return re.sub(r"\s{2,}", " ", q).strip() or question


def web_answer(question):
    """(text, web_sources, research) for a web question."""
    try:
        from intelligence import research as R
    except Exception:
        R = None
    label, url = _carrier_in(question)
    query = web_query(question)
    wants_site = bool(re.search(r"\b(website|site|web ?site)\b", question, re.I)) and url
    searches = []
    resources = [(n, u) for n, u, p in RESOURCES if re.search(p, question, re.I)]
    if R is not None and R.enabled():
        results, errors = [], []
        tries = (["site:{0} {1}".format(_domain(url), query)] if wants_site else []) + [
            "{0} {1}".format(label, query) if label and label.lower() not in query.lower() else query]
        for q in tries:
            searches.append(q)
            try:
                for item in R.search(q):
                    if all(item["url"] != r["url"] for r in results):
                        host, kind = R.publisher(item["url"])
                        results.append(dict(item, publisher=host, kind=kind))
            except Exception as error:
                errors.append(str(error)[:120])
            if len(results) >= 5:
                break
        if results:
            order = {"Carrier (official)": 0, "Port / terminal": 1, "Government": 2,
                     "Official documentation": 3, "Industry body": 3, "Industry source": 4}
            results.sort(key=lambda r: order.get(r["kind"], 9))
            lines = ["**From the web** (public sources, not run facts) — “{0}”:".format(query), ""]
            for i, r in enumerate(results[:5]):
                lines.append("[{0}] **{1}** — {2}{3}".format(
                    i + 1, r["title"] or r["publisher"], r["publisher"],
                    ": " + r["snippet"][:220] if r.get("snippet") else ""))
            lines += ["", "_Check anything important on the source itself. Live shipment status "
                          "comes only from the automation, not from a web search._"]
            sources = [{"url": r["url"], "title": r["title"], "publisher": r["publisher"],
                        "kind": r["kind"]} for r in results[:6]]
            return "\n".join(lines), sources, {"searches": searches}
        why = "the search found nothing" + (" ({0})".format(errors[0]) if errors else "")
    else:
        why = R.why_off() if R is not None else "web research isn't available here"
    lines = ["I couldn't search the web for that: {0}.".format(why)]
    links = []
    if label and url:
        links.append(("{0} (official site)".format(label), url))
    links += resources
    if links:
        lines += ["", "**Official links you can open:**"]
        lines += ["• {0}: {1}".format(n, u) for n, u in links[:5]]
    if R is not None and not R.enabled():
        lines += ["", "To turn web search on, double-click **SETUP_WEB_SEARCH.bat** once on the "
                      "tower PC. It installs a private search engine (SearXNG) on that PC, free."]
    sources = [{"url": u, "title": n, "publisher": _domain(u), "kind": "Official link"}
               for n, u in links[:5]]
    return "\n".join(lines), sources, {"searches": searches}


# ── clearance (the eHub export on the Clearance page) ─────────────────────

def _step(name):
    return "Awaiting Receipt" if name == "Awaiting Reciept" else name


MAIL_ASK = re.compile(r"\b(e-?mail|mail|draft|write|compose)\b", re.I)
# The export's measures, in the words people use for them. Specific phrases
# only: "on time", "bus" or "clearing" alone are everyday words.
CL_CUE = re.compile(r"\b(business units?|which bu|bu is|bus? (has|have|with)|on[- ]?time (rate|shipments?|percentage)|"
                    r"on[- ]?time %|late shipments?|delayed shipments?|in customs|customs release|duty payment|"
                    r"clearing agents?|bottlenecks?)\b", re.I)
# With a BU or a country named, the question must be about its shipments.
CL_SHIP = re.compile(r"\b(shipments?|late|past[- ]?due|overdue|on[- ]?time|delayed|stuck|open|completed|"
                     r"doing|performance|clearance|customs|bottleneck|worst|best|escalat\w*)\b", re.I)
# Never the export's figures: the web, news, weather, a meaning or a definition.
CL_NOT = re.compile(r"\b(news|weather|capital|search the web|on the web|online|internet|google|"
                    r"what does \w+( \w+)? mean|meaning of|define|definition)\b", re.I)
CL_RANK = re.compile(r"\b(which|what|who|top|worst|most|slowest|biggest|rank)\b", re.I)
CL_ESC = re.compile(r"\bescalat\w*", re.I)
CL_FLAG = [("pastdue", re.compile(r"\b(past[- ]?due|overdue|late at|stuck|waiting longest|delayed)\b", re.I)),
           ("open", re.compile(r"\b(open|pending|in progress|(not|yet to be|still not) (yet )?"
                               r"(completed|finished|done|cleared))\b", re.I)),
           ("done", re.compile(r"\b(completed|finished|done|cleared)\b", re.I)),
           ("late", re.compile(r"\b(not on time|late)\b", re.I))]
CL_STEPS = [("Submit to BU", r"submit(ted)? to (the )?bu"), ("Assign Clearing Agent", r"assign(ing|ed)?\b[^.?]{0,12}agent"),
            ("Customs Pre Entry", r"pre[- ]?entry"), ("Customs Declaration", r"declaration"),
            ("Duty Assessment", r"duty assessment|assessment"), ("Duty Payment", r"duty payment|paying duty"),
            ("Customs Release", r"customs release|release"), ("Awaiting Reciept", r"awaiting receipt|receipt|reciept"),
            ("Correction Required", r"correction")]
MONTHS_RX = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


# A country named in words: only where the BU code IS that country's ISO
# code, and only if the export has that BU. (JR, LB… are not mapped.)
def _country_bu():
    from dashboard import clearance
    return {name.lower(): bu for bu, (_iso, name) in clearance.BU_COUNTRY.items()}


def _cl_filters(question, values):
    """What a question narrows to, from the loaded export's own values only."""
    q, f = question, {}
    for v in values["bu"]:
        if re.search(r"\b{0}\b".format(re.escape(v)), q):
            f["bu"] = v
    if "bu" not in f:
        for name, code in _country_bu().items():
            if code in values["bu"] and re.search(r"\b" + name + r"\b", q, re.I):
                f["bu"] = code
                break
    for v in values["mode"]:
        if re.search(r"\b{0}\b".format(re.escape(v)), q, re.I):
            f["mode"] = v
    for v in values["div"]:                  # "Spare Parts", "Power System & Industrial"…
        if re.search(re.escape(" ".join(v.split()[:2])), q, re.I):
            f["div"] = v
    for name, rx in CL_STEPS:
        if name in values["step"] and re.search(r"\b(" + rx + r")", q, re.I):
            f["step"] = name
            break
    if "step" not in f:
        if re.search(r"\b(in|at|with|through) customs\b", q, re.I):
            f["group"] = "In customs"
        elif re.search(r"\b(with|at|waiting (on|for)) (the )?bu\b", q, re.I):
            f["group"] = "With BU"
    for key in ("agent", "ffw"):
        best = None
        for v in values[key]:
            if len(v) >= 3 and re.search(r"(?<![\w])" + re.escape(v) + r"(?![\w])", q, re.I) and \
                    (best is None or len(v) > len(best)):
                best = v
        if best:
            f[key] = best
    m = re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
                  r"sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?\s*(20\d\d)?", q, re.I)
    if m and m.group(1).lower() == "may" and not (m.group(2) or re.search(r"\b(eta|month)\b", q, re.I)):
        m = None
    if m and re.search(r"\b(eta|month|in|for|during)\b", q, re.I):
        year = m.group(2)
        if not year:
            years = sorted({str(x) for x in values.get("years", [])}) or [time.strftime("%Y")]
            year = years[-1]
        f["month"] = "{0}-{1:02d}".format(year, MONTHS_RX.index(m.group(1).lower()[:3]) + 1)
    for flag, rx in CL_FLAG:
        if rx.search(q):
            f["flag"] = flag
            break
    return f


def _cl_link(f):
    from urllib.parse import urlencode
    h = {}
    for k in ("bu", "mode", "div", "month", "status", "group"):
        if f.get(k):
            h[k] = f[k]
    if f.get("flag") in ("pastdue", "open", "done", "late") and not (f.get("group") and f["flag"] == "open"):
        h["flag"] = f["flag"]
    term = f.get("agent") or f.get("ffw") or f.get("step")
    if term:
        h["q"] = term
    return "/clearance" + ("#" + urlencode(h) if h else "")


def _plain(text):
    """Text people typed (reasons, recipients): shown as written, never as a link."""
    return str(text or "").replace("[", "(").replace("]", ")")


def _cl_shipment(rows, escalations, log):
    lines = []
    for r in rows:
        state = "completed" if r["step"] == "Completed" else "open at **{0}**".format(_step(r["step"]))
        lines.append("**{0}** ({1}, {2}, {3}) is {4}.".format(r["awb"], r["bu"], r["mode"], r["div"], state))
        facts = [("eHub status", r["status"]), ("Next step", r["next"]), ("ETA", r["eta"]), ("ATA", r["ata"]),
                 ("Submitted to BU", r["submitted"]), ("Customs release", r["release"]),
                 ("Completed", r["completed"]), ("Forwarder", r["ffw"]), ("Clearing agent", r["agent"])]
        if r["step"] != "Completed":
            facts.insert(2, ("Step due", r["due"] or "no usable due date in the export"))
            if r["overdue"] is not None:
                facts.insert(3, ("Days past due", "{0:,}".format(r["overdue"]) if r["overdue"] > 0
                                 else "not past due ({0} days left)".format(-r["overdue"])))
        facts.append(("On time (eHub flag)", "yes" if r["ontime"] else "no"))
        lines += ["- {0}: {1}".format(k, v) for k, v in facts if v not in (None, "")]
        names = ["Submit to BU", "Assign agent", "Pre entry", "Declaration", "Duty assessment",
                 "Duty payment", "Customs release", "Receipt", "End to end"]
        missed = [n for n, a in zip(names, r["ach"]) if a == "N"]
        met = sum(1 for a in r["ach"] if a == "Y")
        lines.append("- Stage targets: {0} met{1}".format(met, "; missed: " + ", ".join(missed) if missed else ""))
    for e in escalations:
        lines.append("- Escalation ({0}, {1}) by {2} on {3}: {4}".format(e["status"], e["bu"], e["by"], e["at"][:10],
                                                                    _plain(e["reason"])))
    for c in log[-3:]:
        lines.append("- {0} {1} by {2} on {3}".format("Call started" if c["kind"] == "call" else "Mail drafted",
                                                    ("to " + _plain(c["to"])) if c["to"] else "", c["by"], c["at"][:16].replace("T", " ")))
    lines.append("Open it on the [Clearance page](/clearance#q={0}).".format(rows[0]["awb"].replace(" ", "%20")))
    return "\n".join(lines)


CL_DIM = (("past_due_by_bu", re.compile(r"\b(bus?|business units?)\b", re.I), "BU", str),
          ("past_due_by_agent", re.compile(r"\b(clearing )?agents?\b", re.I), "clearing agent", str),
          ("past_due_by_ffw", re.compile(r"\b(forwarders?|ffw)\b", re.I), "forwarder", str),
          ("past_due_by_step", re.compile(r"\b(steps?|stages?|bottleneck)\b", re.I), "step", _step))


def _cl_lead(question, f, s):
    """The direct answer first, when the question asks for one: which BU,
    agent, forwarder or step is worst, or the on-time rate. From the same counts."""
    if CL_RANK.search(question):
        for key, rx, what, name in CL_DIM:
            if rx.search(question) and s[key] and not (key == "past_due_by_bu" and f.get("bu")):
                top, n = s[key][0]
                return "**{0}** is the {1} with the most past-due shipments: **{2:,}** of {3:,}.".format(
                    name(top), what, n, s["past_due"])
    if re.search(r"\bon[- ]?time\b", question, re.I) and s["total"]:
        return "The on-time rate is **{0}%**: {1:,} of {2:,} shipments (target 95%).".format(
            round(100.0 * s["on_time"] / s["total"]), s["on_time"], s["total"])
    return None


def clearance_answer(question, view=None):
    """Answers about the loaded eHub export, counted from its rows only (dashboard/clearance.py).
    `view`: the filters on the Clearance page when the question was asked there."""
    from dashboard import clearance
    data = clearance.load()
    if data is None:
        return ("No eHub shipment export is loaded yet, so I have no clearance figures. An Operator "
                "or Admin can load one on the [Clearance page](/clearance) (Load new export), or put "
                "the .xlsx in the clearance folder on the server.")
    named = clearance.awbs_in(question, data)
    if named and MAIL_ASK.search(question):
        out = []
        for awb in named[:2]:
            for row in clearance.find(awb, data)[:2]:
                d = clearance.draft_mail(row)
                out.append("\n".join(x for x in
                    ["**Email for {0} ({1})** - situation: {2}.".format(row["awb"], row["bu"], d["situation"]),
                     "To: {0}".format(d["to"] or "(no address on file)"),
                     "Cc: {0}".format(d["cc"]) if d["cc"] else None,
                     "Subject: {0}".format(d["subject"]), "", d["body"].rstrip()] +
                    (["", "Missing in Contacts: " + "; ".join(d["missing"]) + "."] if d["missing"] else []) +
                    ["", "[Open it ready in Outlook from the Clearance page](/clearance#q={0}&mail={1})".format(
                        row["awb"].replace(" ", "%20"), row["bu"]) +
                     " - nothing is sent until you press Send."] if x is not None))
        return "\n\n".join(out)
    if named:
        out = []
        for awb in named[:3]:
            rows = clearance.find(awb, data)
            bus = {r["bu"] for r in rows}
            out.append(_cl_shipment(rows, [e for e in clearance.escalations() if e["awb"] == awb and e["bu"] in bus],
                                    [c for c in clearance.contact_log(awb) if c.get("bu") in bus | {None}]))
        return "\n\n".join(out)
    whole = clearance.query(None, data=data)
    values = dict(whole["values"], years=sorted({(r[data["fields"].index("eta")] or "")[:4]
                                                  for r in data["rows"] if r[data["fields"].index("eta")]}))
    f = _cl_filters(question, values)
    # What the person has on screen fills in what the question does not say;
    # anything the question names itself wins. "Overall" ignores the view.
    from_view = []
    # A question that names its own BU, mode, step, agent, month… gets exactly
    # that; "past due" alone does not count as naming one.
    use_view = bool(view) and not WHOLE_ASK.search(question) and not any(k != "flag" for k in f)
    if use_view:
        for k, v in clean_view(view, values).items():
            if k not in f:
                f[k] = v
                from_view.append(k)
    if CL_ESC.search(question):
        items = [e for e in clearance.escalations() if e["status"] == "open" and
                 all(not f.get(k) or e.get(k) == f[k] for k in ("bu", "mode", "agent"))]
        scope = ", ".join(f[k] for k in ("bu", "mode", "agent") if f.get(k))
        if not items:
            return "There are no open escalations{0}{1}. Escalate a shipment with the Escalate button in the " \
                   "[Clearance page](/clearance)'s shipment list.".format(
                       " for " + scope if scope else "",
                       " (that is the view on your screen; ask “overall” for all of them)"
                       if from_view and scope else "")
        lines = ["**{0}** open escalation{1}{2}:".format(len(items), "" if len(items) == 1 else "s",
                                                       " in the view you have open ({0})".format(scope)
                                                       if from_view and scope else "")]
        for e in sorted(items, key=lambda e: e["at"], reverse=True)[:10]:
            lines.append("- {0} ({1}, {2}) at {3}{4}: “{5}” by {6} on {7}".format(
                e["awb"], e["bu"], e["mode"], _step(e["step"]),
                ", {0:,} days past due".format(e["overdue"]) if e.get("overdue") and e["overdue"] > 0 else "",
                _plain(e["reason"][:140]), e["by"], e["at"][:10]))
        return "\n".join(lines)
    s = clearance.query(f, data=data)
    if not s["total"]:
        words = {"pastdue": "past due", "open": "open", "done": "completed", "late": "not on time"}
        said = [_step(v) if k == "step" else words.get(v, v).lower() if k in ("flag", "group") else v
                for k, v in f.items()]
        return "None of the shipments in the loaded export (data as of {0}) match: {1}{2}.".format(
            s["asof"] or "an unknown date", ", ".join(said),
            " (that includes the filters on your screen; ask “overall” for the whole export)" if from_view else "")
    label = ", ".join(_step(v) if k == "step" else v for k, v in f.items() if k not in ("flag",))
    flag_txt = {"pastdue": "past due", "open": "open", "done": "completed", "late": "not on time"}.get(f.get("flag"))
    head = "From the eHub export with data as of **{0}**{1}{2}: **{3:,}** shipment{4}".format(
        s["asof"] or "an unknown date (the export has no Last Date)",
        " for **{0}**".format(label) if label else "", " ({0})".format(flag_txt) if flag_txt else "",
        s["total"], "" if s["total"] == 1 else "s")
    if from_view:
        head = "In the view you have open, " + head[0].lower() + head[1:]
    lead = _cl_lead(question, f, s)
    lines = ([lead, ""] if lead else []) + [
        head + ", **{0}%** on time (the export's own On Time flag; target 95%).".format(
            round(100.0 * s["on_time"] / s["total"]))]
    if f.get("flag") not in ("done",):
        lines.append("**{0:,}** open, **{1:,}** past their current step's due date, {2:,} completed.".format(
            s["open"], s["past_due"], s["done"]))
    rank = CL_RANK.search(question)
    groups = [("Past due by step", "past_due_by_step", _step)]
    if not f.get("bu"):
        groups.append(("By BU", "past_due_by_bu", str))
    if re.search(r"\bagents?\b", question, re.I) or (rank and not f.get("agent")):
        groups.append(("By clearing agent", "past_due_by_agent", str))
    if re.search(r"\b(forwarders?|ffw)\b", question, re.I):
        groups.append(("By forwarder", "past_due_by_ffw", str))
    for title, key, name in groups:
        if s[key] and not (key == "past_due_by_step" and f.get("step")):
            lines.append("{0}: {1}.".format(title, ", ".join("{0} {1:,}".format(name(k), v) for k, v in s[key][:6])))
    if s["longest"]:
        lines.append("Longest waits:")
        for x in s["longest"]:
            lines.append("- {0} ({1}, {2}) at {3}: {4:,} days past its due date {5}{6}".format(
                x["awb"] or "no AWB/BOL", x["bu"], x["mode"], _step(x["step"]), x["overdue"], x["due"],
                "; agent " + x["agent"] if x["agent"] else ""))
    if s["escalated_open"]:
        lines.append("{0} of these have an open escalation.".format(len(s["escalated_open"])))
    checks = sum(v for k, v in (s.get("dq") or {}).items() if k != "agent_name_variants")
    if checks:
        lines.append("The export also has {0:,} values that could not be used (no ETA, empty due "
                     "dates…); the Clearance page lists them under Data checks.".format(checks))
    if from_view:
        lines.append("I used the filters on your screen; ask “overall” for the whole export.")
    if use_view and view_has_more(view):
        lines.append("Your screen also has a filter I can't count by (a search, a missed stage, "
                     "on time only, escalated or slow), so the page may show fewer shipments.")
    lines.append("[Open this on the Clearance page]({0}) to see every shipment, act on it and download "
                 "the list.".format(_cl_link(f)))
    return "\n".join(lines)


# ── the answer ────────────────────────────────────────────────────────────

def answer(kind, question, data, context=None):
    """A reply dict for the skill, or None to let the rules answer."""
    context = context or {}
    base = {"card": None, "grounded": True, "final": True, "sources": ["atlas.skills"]}
    if kind == "pdf":
        text, downloads = pdf_offer(question, data, context)
        return dict(base, answer=text, intent="skill_pdf", downloads=downloads,
                    reference=_reference(question, data, context))
    if kind == "validate_date":
        return dict(base, answer=validate_date(question), intent="skill_validate", reference=None)
    if kind == "validate":
        ref = _reference(question, data, context)
        if ref:
            from dashboard.assistant import shipment_card
            return dict(base, answer=validate_one(data, ref), intent="skill_validate",
                        reference=ref, card=shipment_card(data, data.find(ref)))
        return dict(base, answer=validate_run(data), intent="skill_validate", reference=None)
    if kind == "screenshot":
        ref = _reference(question, data, context, named_only=True)
        shots, problem = screenshots(ref)
        if shots:
            text = ("Here {0} the {1} screenshot{2} the run captured for **{3}**, newest first."
                    .format("is" if len(shots) == 1 else "are", len(shots),
                            "" if len(shots) == 1 else "s", ref))
        else:
            text = ("The run didn't capture a screenshot for **{0}**{1}. Screenshots are taken when "
                    "something goes wrong or a carrier asks for a person. Ask “PDF for {0}” for its "
                    "full record.".format(ref, " ({0})".format(problem) if problem else ""))
        return dict(base, answer=text, intent="evidence", reference=ref, evidence=shots)
    if kind == "clearance":
        return dict(base, answer=clearance_answer(
                        question, context.get("view") if context.get("page") == "clearance" else None),
                    intent="skill_clearance",
                    reference=None, sources=["clearance.export"])
    if kind == "web":
        text, sources, research = web_answer(question)
        return dict(base, answer=text, intent="skill_web", reference=None,
                    web_sources=sources, research=research)
    return None
