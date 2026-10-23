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
    if WEB_ASK.search(q) and not CAN_YOU_SEARCH.search(q):
        return "web"
    return None


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
    if kind == "web":
        text, sources, research = web_answer(question)
        return dict(base, answer=text, intent="skill_web", reference=None,
                    web_sources=sources, research=research)
    return None
