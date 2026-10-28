"""
ATLAS's answer to a logistics question, built from the Knowledge Engine
(intelligence/kb.py) — never from memory, never from a model.

    1 intent        a calculation, an Incoterms "who does what", a comparison,
                    or a definition — or not a knowledge question at all
    2 retrieve      the items the question names or best matches
    3 compose       in layers, each labelled:
                        the general logistics answer (with its review and
                        evidence status), the ATLAS rule where this platform
                        applies its own, the reading from your data where you
                        may see it, related concepts, and the sources
    4 refuse        nothing relevant found: None, so ATLAS says it does not
                    know rather than guess

A calculation is done by intelligence/kb_calc.py from the numbers given;
a missing number is asked for, never assumed. A country rule says its
country, its status and its review date. A superseded or retired item is
never answered as current.
"""
import re
import time

from . import kb as KB
from . import kb_calc as CALC

# A question that asks what something is, how it differs, or how it is
# measured — in English or Arabic. (A question about a shipment or the run is
# answered from the records, not from here.)
# Any question form: how a question is asked, not what it is about.
BROAD_ASK = re.compile(r"^\s*(what|why|how|when|where|does|do|is|are|can|should|could|would|will)\b|\?\s*$|"
                       r"\b(vs\.?|versus)\b", re.I)
GENERAL_ASK = re.compile(
    r"^\s*(what('?s| is| are| does| do| was| were)|define|definition of|explain|meaning of|"
    r"how (does|do|is|are|to|can i|should i|should we|do i|do we) |how (?:is|are) .{0,40} (calculated|measured)|"
    r"why (do|does|are|is|would)|difference between|tell me (what|about)|is (a|an|the|it)\b|"
    r"can (a|an|the|i|we|fob|cif|exw|fca|cpt|cip|dap|dpu|ddp|fas|cfr)\b|who (pays|bears|is responsible|does|clears|insures|books)|"
    r"under (exw|fca|cpt|cip|dap|dpu|ddp|fas|fob|cfr|cif)\b|when (does|do) .{0,30}(risk|title)|calculate|compute|"
    r"which (incoterm|kpi|document|mode))"
    r"|\b(vs\.?|versus|compared (to|with)|or)\b.{0,30}\?$|\bmean\??$|\bmeaning\b|"
    r"ما هو|ما هي|ماذا يعني|ايه هو|إيه هو|يعني ايه|يعني إيه|ما معنى|ما الفرق|الفرق بين|اشرح|عرّف|عرف|كيف (يتم|نحسب)|احسب", re.I)
# A question about the data — counts, lists, statuses, this run, our
# shipments — is answered from the records, even when it names a concept.
DATA_ASK = re.compile(
    r"\b(how many|which|show|list|count|number of|status of|latest|today|yesterday|this (run|week|month)|"
    r"right now|currently|our|my|we|did|has|have|were|was there|any)\b|\d{6,}|"
    r"كم عدد|كام|أي شحنات|اعرض|حالة الشحنة", re.I)
# "Why was MY shipment delayed?" is about one of your shipments: the run's
# records answer it, never a general explanation.
RUN_SUBJECT = re.compile(r"\b(my|our|this|that|these|those)\s+(shipments?|containers?|orders?|consignments?|cargo|"
                         r"deliver(y|ies)|flights?|vessels?|awbs?|b/?ls?|bols?)\b|شحنتي|شحنتنا|الحاوية دي", re.I)
# A shipment, container or document reference: the question is about that one.
REFERENCE = re.compile(r"\b[A-Z]{0,4}-?\d{6,}\b|\b[A-Z]\d{5,}\b|\b\d{3}-\d{8}\b")
# "What is the ETA?" asks for a shipment's value, not the meaning of ETA.
RUN_FIELD = re.compile(r"^\s*what(?:'s| is| was) the (?:eta|ata|etd|atd|status|latest\s+\w+)\s*\??\s*$", re.I)
# "What does X mean?", "define X": a definition, whatever else X is.
DEFINE = re.compile(r"^\s*(what does .{1,60} mean|what is the meaning of|meaning of|define|definition of)|"
                    r"ماذا يعني|ما معنى|يعني ايه|يعني إيه", re.I)
COMPARE = re.compile(r"difference between|\bvs\.?\b|\bversus\b|compared? (to|with)|\bor\b|الفرق بين|مقارنة", re.I)
ARABIC = re.compile(r"[؀-ۿ]")
CALC_ITEM = {"OTIF": "E.otif", "On-time delivery": "E.on-time-delivery", "Chargeable weight": "B.chargeable-weight",
             "Chargeable days": "C.demurrage", "Cost variance": "E.cost-variance", "Cost per shipment": "E.cost-per-shipment",
             "Exception rate": "E.exception-rate", "Past-due share": "E.past-due-share", "Transit time": "E.transit-time-kpi"}
MIN_SCORE = 8.0            # below this an unnamed match is not an answer

STATUS_TEXT = {
    "approved": "approved by a Mantrac reviewer",
    "reviewed": "reviewed, not yet approved as Mantrac's position",
    "unreviewed": "not yet reviewed by a Mantrac expert",
}
EVIDENCE_TEXT = {
    "project_verified": "this platform's own rule, checkable in its code",
    "source_cited": "explained in ATLAS's words; a checked source covers it",
    "general_knowledge": "general logistics knowledge",
    "unverified": "UNVERIFIED — not yet supported by a checked source",
}


def _intent(question, hits):
    if CALC.calculate(question) is not None:
        return "calculation"
    inc = CALC.incoterm_question(question) if CALC.RULE.search(question or "") else None
    if inc and inc.get("aspect"):
        return "incoterm"
    named = [h for h in hits if h["named"]]
    if len(named) >= 2 and COMPARE.search(question or ""):
        return "compare"
    return "definition"


def match(question, k=None):
    """The best results for a knowledge question, or [] when the question is
    not one (or nothing relevant is known)."""
    q = " ".join(str(question or "").split())
    if not q or len(q) > 400:
        return []
    k = k or KB.kb()
    hits = k.search(q, limit=6)
    if not hits:
        return []
    strong = bool(GENERAL_ASK.search(q)) or CALC.calculate(q) is not None
    asks = strong or bool(BROAD_ASK.search(q))
    # A data question ("how many…", "which…", "our…") reaches the knowledge
    # only when it plainly asks what something is ("what is our OTIF").
    if RUN_FIELD.search(q) or RUN_SUBJECT.search(q) or REFERENCE.search(q) or (DATA_ASK.search(q) and not strong):
        return []                                       # a data question: the records answer it
    top = hits[0]
    # A bare name ("HS code", "telex release", "OTIF") is a knowledge question;
    # a short question that merely contains a known word ("which shipments
    # failed") is not — it is about the run.
    bare = set(KB.tokens(q)) <= set(t for key in k.names for t in key.split()
                                     if top["id"] in k.names[key] and " " + key + " " in " " + " | ".join(KB.name_keys(q)) + " ")
    if top["named"] and (asks or bare):
        return hits
    if asks and top["score"] >= MIN_SCORE:
        return hits
    return []


def applies(question):
    return bool(match(question))


def decisive(question):
    """True for a question only the Knowledge Engine can answer, whatever the
    run's rules made of it: an Incoterms 'who does what', a calculation with
    its numbers, or a comparison of two concepts it knows by name."""
    q = " ".join(str(question or "").split())
    if DATA_ASK.search(q):
        return False
    if CALC.RULE.search(q):
        inc = CALC.incoterm_question(q)
        if inc and inc.get("aspect"):
            return True
    calc = CALC.calculate(q)
    if calc is not None and not calc.get("missing"):
        return True
    hits = match(q)
    if not hits or not hits[0]["named"]:
        return False
    if DEFINE.search(q):
        return True
    return bool(COMPARE.search(q)) and bool(GENERAL_ASK.search(q))


def _status_line(k, it):
    review = it.get("review_status", "unreviewed")
    bits = [EVIDENCE_TEXT.get(it.get("evidence_status"), it.get("evidence_status")),
            STATUS_TEXT.get(review, review)]
    if it.get("edition"):
        bits.append(it["edition"])
    return "_Knowledge status ({0}, v{1}): {2}._".format(it["id"], it.get("version", 1), "; ".join(bits))


def _jurisdiction(k, it):
    if it.get("scope") != "jurisdiction":
        return ""
    auth = next((c for c in k.cite(it) if c.get("authority") == "government"), None)
    line = "⚠ **Country-specific ({0})**".format(it.get("jurisdiction"))
    if it.get("evidence_status") == "unverified":
        line += " and **unverified**"
    line += ": check {0} before relying on it.".format(
        "the current rules of the " + auth["publisher"] if auth else "the national authority's current rules")
    if k.stale(it):
        line += " Its review date ({0}) has passed, so it may be out of date.".format(it.get("review_by"))
    elif it.get("review_by"):
        line += " Review due {0}.".format(it.get("review_by"))
    return line


def _sources(k, items):
    """Evidence for the items. A project file cited only as where a general
    definition came from is provenance, not evidence, and is not listed."""
    seen, lines = set(), []
    for it in items:
        cites = k.cite(it)
        if it.get("evidence_status") == "general_knowledge":
            cites = [c for c in cites if c.get("authority") != "project"]
            if not cites:
                key = ("none", it["id"])
                if key not in seen:
                    seen.add(key)
                    lines.append("• {0}: no specific source checked (general logistics knowledge)".format(it["term"]))
                continue
        for c in cites:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            where = c.get("url") or c.get("path")
            what = c["title"] + (" — " + c["publisher"] if c.get("publisher") and c.get("authority") != "project" else "")
            if c.get("section"):
                what += " § " + c["section"]
            if c.get("page"):
                what += ", p. " + str(c["page"])
            checked = c.get("checked") or {}
            note = ""
            if c.get("authority") == "project":
                note = " (this platform's code)"
            elif checked.get("url_status") == 200:
                note = " (page checked {0})".format(checked.get("on"))
            elif checked.get("url_status"):
                note = " (not confirmed reachable)"
            lines.append("• {0}{1}{2}".format(what, " — " + where if where else "", note))
    return lines


def _related(k, it, limit=4):
    out = []
    for rid, rtype, direction, _d in k.related(it["id"], depth=1):
        other = k.get(rid)
        if not other or not k.current(other):
            continue
        out.append((rid, rtype, direction, other))
    # Contrasts first: they are what a reader most often needs next.
    out.sort(key=lambda r: (r[1] != "contrasts_with", r[3].get("scope") != "project"))
    return out[:limit]


def _project_rule(k, it):
    """The ATLAS rule that applies where this general item meets the platform."""
    if it.get("scope") == "project":
        return None
    for rid, rtype, direction, other in _related(k, it, limit=12):
        if other.get("scope") in ("project", "business"):
            return other
    return None


def _data_line(it, data, context):
    """What your data shows for this item — only for items whose meaning is
    one of this platform's own figures, and only where you may see them."""
    context = context or {}
    iid = it["id"]
    try:
        if iid in ("E.past-due-share", "E.clearance-on-time"):
            if str(context.get("clearance", "1")) == "0":
                return "Your role cannot open the Clearance page, so I won't show its figures."
            from dashboard import clearance as CL
            q = CL.query(None)
            if not q or not q.get("total"):
                return "No eHub export is loaded, so there are no figures to show."
            asof = q.get("asof") or "the export's date"
            if iid == "E.past-due-share":
                share = 100.0 * q["past_due"] / q["open"] if q.get("open") else 0
                return "{0} of {1} open shipments are past due ({2:.0f}%), as of {3}.".format(
                    q["past_due"], q["open"], share, asof)
            share = 100.0 * q["on_time"] / q["total"] if q.get("total") else 0
            return "{0} of {1} shipments are marked on time by eHub ({2:.0f}%), as of {3}.".format(
                q["on_time"], q["total"], share, asof)
        if iid in ("P.needs-review", "P.skipped", "P.partial") and data is not None:
            rows = list(getattr(data, "shipments", None) or [])
            if iid == "P.needs-review":
                n = sum(1 for r in rows if "REVIEW" in str(r.get("outcome") or "").upper())
                return "In this run, {0} shipment{1} {2} marked Needs review.".format(n, "" if n == 1 else "s", "is" if n == 1 else "are")
            state = {"P.skipped": "skipped", "P.partial": "partial"}[iid]
            n = sum(1 for r in rows if r.get("state") == state)
            return "In this run, {0} shipment{1} ended {2}.".format(n, "" if n == 1 else "s", state)
    except Exception:
        return None
    return None


def _definition_block(it, arabic):
    parts = []
    if arabic and it.get("definition_ar"):
        parts.append(it["definition_ar"] + "\n(EN: **{0}**{1})".format(
            it["term"], " — " + it["acronym"] + ": " + it["full_form"] if it.get("acronym") and it.get("full_form") else ""))
        parts.append(it["definition"])
    else:
        parts.append(it["definition"])
    if it.get("explanation"):
        parts.append(it["explanation"])
    return parts


def _kpi_block(it):
    kpi = it.get("kpi")
    if not kpi:
        return []
    conv = {"industry": "industry convention", "business": "business-defined (agree it locally)",
            "atlas": "this platform's own definition"}.get(kpi.get("convention"), kpi.get("convention"))
    lines = ["**How it is measured** ({0})".format(conv),
             "• Formula: {0}".format(kpi["formula"]), "• Unit: {0} · Aggregation: {1} · Period: {2}".format(
                 kpi["unit"], kpi["aggregation"], kpi["period"]),
             "• Data needed: {0}".format(", ".join(kpi["fields"])),
             "• Leave out: {0}".format("; ".join(x.rstrip(".") for x in kpi["exclusions"])),
             "• Limits: {0}".format("; ".join(x.rstrip(".") for x in kpi["limitations"]))]
    return ["\n".join(lines)]


def answer(question, context=None, data=None):
    """
    {"answer", "intent", "knowledge": {...}, "sources", "grounded"} for a
    logistics question, or None when it is not one ATLAS's knowledge covers.
    """
    t0 = time.perf_counter()
    k = KB.kb()
    q = " ".join(str(question or "").split())
    hits = match(q, k)
    calc = CALC.calculate(q)
    inc = CALC.incoterm_question(q) if CALC.RULE.search(q) else None
    if not hits and calc is None and not (inc and inc.get("aspect")):
        KB.log_query(q, [], (time.perf_counter() - t0) * 1000, "kb_answer:none")
        return None
    mode = _intent(q, hits)
    arabic = bool(ARABIC.search(q))
    used, parts = [], []

    if mode == "calculation":
        parts.append(CALC.describe(calc))
        # The item for what was calculated; else the KPI the question names.
        kpi_item = k.get(CALC_ITEM.get((calc or {}).get("kpi"), "")) or \
            next((h["item"] for h in hits if h["item"].get("kind") == "kpi"), None)
        if kpi_item:
            used.append(kpi_item)
            parts.append("**{0}** — {1}".format(kpi_item["term"], kpi_item["definition"]))
            parts += _kpi_block(kpi_item)
            if kpi_item.get("limitations"):
                parts.append("_" + " ".join(kpi_item["limitations"]) + "_")
    elif mode == "incoterm":
        item = k.get("C.incoterm-" + inc["rule"].lower())
        parts.append(inc["text"])
        if item:
            used.append(item)
            parts.append("**{0}** — {1}".format(item["term"], item["definition"]))
            if item.get("explanation"):
                parts.append(item["explanation"])
        parts.append("_The Incoterms rule only settles these points if the sale contract names it "
                     "(with the edition, e.g. Incoterms 2020) and does not vary it._")
    elif mode == "compare":
        named = [h["item"] for h in hits if h["named"]]
        low = q.lower()

        def pos(it):
            spots = [low.find(n.lower()) for n in [it["term"], it.get("acronym") or ""] + list(it.get("synonyms") or []) if n]
            spots = [x for x in spots if x >= 0]
            return min(spots) if spots else 10 ** 6
        a, b = sorted(named[:2], key=pos)                 # in the order the question names them
        used += [a, b]
        for it in (a, b):
            parts.append("**{0}** — {1}".format(it["term"], it["definition_ar"] if arabic and it.get("definition_ar") else it["definition"]))
        # The difference from the item whose explanation names the other one.
        def names_other(it, other):
            text = (it.get("explanation") or "").lower()
            return any(n and n.lower() in text for n in [other["term"], other.get("acronym")] + list(other.get("synonyms") or []))
        contrast = next((it for it, other in ((a, b), (b, a)) if names_other(it, other)), None) or \
            next((it for it in (a, b) if any(r.get("id") == (b if it is a else a)["id"] for r in it.get("related") or [])), None)
        if contrast and contrast.get("explanation"):
            parts.append("**The difference** — " + contrast["explanation"])
        else:
            parts += [x for x in (a.get("explanation"), b.get("explanation")) if x]
    else:
        it = hits[0]["item"]
        used.append(it)
        parts += _definition_block(it, arabic)
        if it.get("example"):
            parts.append("*Example:* " + it["example"])
        if it.get("scenario"):
            parts.append("*Scenario:* " + it["scenario"])
        if it.get("definitions"):
            parts.append("**Its meaning depends on context**\n" + "\n".join(
                "• {0}: {1}".format(d["context"], d["text"]) for d in it["definitions"]))
        parts += _kpi_block(it)
        for m in (it.get("misconceptions") or [])[:2]:
            parts.append("*Watch out:* " + m)
        for lim in (it.get("limitations") or [])[:2]:
            if not it.get("kpi"):
                parts.append("*Limits:* " + lim)

    main = used[0] if used else None
    if main is not None:
        rule = _project_rule(k, main)
        if main.get("scope") == "project":
            parts.append("**This is a rule of this platform.** Where a general definition differs, "
                         "the platform applies this one.")
        elif main.get("scope") == "business":
            doc = next((c for c in k.cite(main) if c.get("authority") == "internal"), None)
            parts.append("**This is a Mantrac rule**{0}. Where a general definition differs, this one applies "
                         "to Mantrac's work.".format(" (from " + doc["title"] + ")" if doc else ""))
        elif rule is not None:
            used.append(rule)
            parts.append("**{0}** — {1}: {2}".format(
                "In this platform" if rule.get("scope") == "project" else "Mantrac's rule",
                rule["term"], rule["definition"]))
        line = _data_line(main, data, context)
        if line:
            parts.append("**In your data** — " + line)
        elif main.get("kind") == "kpi" and DATA_ASK.search(q):
            # "What is OUR OTIF?" — the definition is known; the figure is not.
            parts.append("**Your figure** — this platform does not hold the data this KPI needs ({0}), "
                         "so I can't calculate yours and I won't estimate it. Give me the counts and I'll "
                         "calculate it exactly.".format(", ".join(main["kpi"]["fields"])))
        warn = _jurisdiction(k, main)
        if warn:
            parts.append(warn)
        rel = [r for r in _related(k, main) if r[3] not in used]
        if rel:
            parts.append("**Related:** " + " · ".join(
                "{0} ({1})".format(r[3]["term"], k.curriculum["relations"].get(r[1], r[1]) if r[2] == "out" else "see also")
                for r in rel))
    src = _sources(k, used)
    if src:
        parts.append("**Sources**\n" + "\n".join(src))
    if main is not None:
        parts.append(_status_line(k, main))
    ms = (time.perf_counter() - t0) * 1000
    KB.log_query(q, hits, ms, "kb_answer:" + mode, {"used": [it["id"] for it in used]})
    return {
        "answer": "\n\n".join(p for p in parts if p),
        "intent": "general_knowledge", "understood": True, "grounded": True,
        "knowledge": {"mode": mode, "items": [it["id"] for it in used],
                      "review": {it["id"]: it.get("review_status") for it in used},
                      "evidence": {it["id"]: it.get("evidence_status") for it in used},
                      "citations": [c for it in used for c in k.cite(it)],
                      "calculation": calc if mode == "calculation" else None,
                      "top": [[h["id"], h["score"]] for h in hits[:3]], "ms": round(ms, 1)},
        "sources": ["knowledge base"],
    }
