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
import os
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
    r"^\s*(what('?s| is| are| does| do)|define|definition of|explain|meaning of|"
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
    r"right now|currently|our|my|we|did|has|have|were|was|any)\b|\d{6,}|"
    r"كم عدد|كام|أي شحنات|اعرض|حالة الشحنة", re.I)
# "Why was MY shipment delayed?" is about one of your shipments: the run's
# records answer it, never a general explanation.
RUN_SUBJECT = re.compile(r"\b(my|our|this|that|these|those)\s+(shipments?|containers?|orders?|consignments?|cargo|"
                         r"deliver(y|ies)|flights?|vessels?|awbs?|b/?ls?|bols?)\b|شحنتي|شحنتنا|الحاوية دي", re.I)
# A particular vessel, booking, container or carrier ("the Maersk vessel", "the
# containers at Tema", "Hapag"): about those, which the records answer.
SPECIFIC = re.compile(
    r"\bthe\s+(?:[\w-]+\s+){0,2}?(?:vessel|ship|booking|containers?|shipments?|flight|awb|b/?l|bol|cargo|"
    r"consignments?|orders?|deliver(?:y|ies)|trucks?)(?=\s*(?:$|[?.,!;:]|(?:at|in|from|to|for|of|on|with|that|"
    r"which|we|i|you|was|were|has|had|arrived|left|departed|got|went|been)\b))|"
    r"\b(?:maersk|msc|cma(?:\s*cgm)?|hapag(?:-lloyd)?|evergreen|cosco|yang ming|zim|dhl|fedex|ups|qatar|emirates|"
    r"lufthansa|turkish|klm|ethiopian|saudia|egyptair|air france|kenya airways)\b", re.I)
# "What was…", "when were…": something that happened, which the records hold.
PAST_ASK = re.compile(r"^\s*(?:what|when|where|why|how(?:\s+\w+)?)\s+(?:was|were)\b", re.I)
# Below this share of the question's words, a matched name is a passing mention.
PASSING = 0.34
# A shipment, container or document reference: the question is about that one.
# MSC-style bills of lading ("MEDUAHP69377") too: letters and 5+ digits, 8+ long.
REFERENCE = re.compile(r"\b[A-Z]{0,4}-?\d{6,}\b|\b[A-Z]\d{5,}\b|\b\d{3}-\d{8}\b|"
                       r"\b(?=[A-Za-z0-9]*\d{5})(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{8,}\b")
# "What is the ETA?" asks for a shipment's value, not the meaning of ETA.
RUN_FIELD = re.compile(r"^\s*what(?:'s| is| was) the (?:eta|ata|etd|atd|status|latest\s+\w+)"
                       r"(?:\s+(?:of|for|on)\s+\S+)?\s*\??\s*$", re.I)
# "What does X mean?", "define X": a definition, whatever else X is.
DEFINE = re.compile(r"^\s*(what does .{1,60} mean|what is the meaning of|meaning of|define|definition of)|"
                    r"ماذا يعني|ما معنى|يعني ايه|يعني إيه", re.I)
COMPARE = re.compile(r"difference between|\bvs\.?\b|\bversus\b|compared? (to|with)|\bor\b|الفرق بين|الفرق|مقارنة|غير|ولا", re.I)
ARABIC = re.compile(r"[؀-ۿ]")
CALC_ITEM = {"Reorder point": "A.reorder-point", "Inventory turnover": "E.inventory-turnover", "MAPE": "E.forecast-accuracy",
             "OTIF": "E.otif", "On-time delivery": "E.on-time-delivery", "Chargeable weight": "B.chargeable-weight",
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


def _is_compare(question, hits):
    """Two concepts ATLAS knows by name, set against each other."""
    named = [h for h in hits or [] if h["named"] and h.get("specific", True)]
    return len(named) >= 2 and bool(COMPARE.search(question or "")) and _joined(question, named[0], named[1])


def _intent(question, hits):
    calc = CALC.calculate(question)
    if calc is not None and not (calc.get("missing") and _is_compare(question, hits)):
        return "calculation"
    inc = CALC.incoterm_question(question) if CALC.RULE.search(question or "") else None
    if inc and inc.get("aspect"):
        return "incoterm"
    named = [h for h in hits if h["named"] and h.get("specific", True)]
    if len(named) >= 2 and COMPARE.search(question or "") and _joined(question, named[0], named[1]):
        return "compare"
    return "definition"


def _joined(question, a, b):
    """Both names are said, joined by vs / or / versus / and, or 'between …
    and …' — a comparison, not two concepts that happen to match."""
    keys = " | ".join(KB.name_keys(question))
    ta, tb = a.get("name"), b.get("name")
    if not ta or not tb:
        return True
    pa, pb = keys.find(ta), keys.find(tb)
    if pa < 0 or pb < 0:
        return True
    lo, hi = sorted([(pa, ta), (pb, tb)])
    between = keys[lo[0] + len(lo[1]):hi[0]]
    return len(between.split()) <= 4 or bool(re.search(r"differ|between|compar|versus|vs", question, re.I))


WHAT_IS_X = re.compile(r"^\s*(?:what(?:'s| is| are| does)|define|explain|meaning of|ايه هو|ايه هي|ما هو|ما هي)\s+(.+)", re.I)
# A question, not a described situation ("they rolled our box…").
ASK_START = re.compile(r"^\s*(how|what|which|when|where|who|why|should|can|could|do|does|is|are|will|would|"
                       r"ايه|إيه|هل|ازاي|إزاي|امتى|إمتى|مين|ينفع|ليه)\b", re.I)
QUOTED = re.compile(r"(?:(?<=\s)|^)['\"“‘]([^\"”]{2,40}?)['\"”’](?=[\s,.;:?!؟]|$)")
# Said to ATLAS about itself ("are you good", "you ok"): not a bare name.
TO_ATLAS = re.compile(r"\b(you|u|your|yours|yourself|ya)\b", re.I)


def exact_name(question, item_id, k=None):
    """The question, beyond its asking words, is just this item's name
    ("telex release", "what is OTIF?")."""
    k = k or KB.kb()
    return any(item_id in k.names.get(key, ()) for key in KB.name_keys(question))


def match(question, k=None, routed=False):
    """The best results for a knowledge question, or [] when the question is
    not one (or nothing relevant is known)."""
    q = " ".join(str(question or "").split())
    if not q or len(q) > 400:
        return []
    from . import trace
    with trace.span("kb.search"):
        k = k or KB.kb()
        hits = k.search(q, limit=6)
    asked = WHAT_IS_X.match(q)
    if asked and hits:
        rest = " " + " ".join(KB.tokens(asked.group(1))) + " "
        first = [h for h in hits if h.get("named") and h.get("name") and rest.startswith(" " + h["name"] + " ")]
        if first:
            # Longest name first; between equals, the item whose own term carries it
            # ("THC" is in "Terminal handling … (THC, BAF)", not in "Surcharges").
            first.sort(key=lambda h: (-len(h["name"]), 0 if set(h["name"].split()) <= set(KB.tokens(h["item"].get("term"))) else 1))
            ids = {h["id"] for h in first[:1]}
            hits = first[:1] + [h for h in hits if h["id"] not in ids]
    # A status or term the operator quotes ("carrier shows 'discharged'") is
    # what the question is about: its entry comes first.
    quoted = QUOTED.findall(q)
    if quoted and hits:
        keys = {key for x in quoted for key in KB.name_keys(x)}
        first = [dict(h, specific=True) for h in hits if h.get("named") and h.get("name") in keys]
        ids = {h["id"] for h in first}
        hits = first + [h for h in hits if h["id"] not in ids]
    strong = bool(GENERAL_ASK.search(q)) or CALC.calculate(q) is not None or routed
    asks = strong or bool(BROAD_ASK.search(q))
    # A data question ("how many…", "which…", "our…") reaches the knowledge
    # only when it plainly asks what something is ("what is our OTIF").
    # "Which Incoterm … when the supplier loads the truck": "the truck" names
    # no record here; an Incoterms question is general by nature.
    specific = SPECIFIC.search(q) and not re.search(r"\bincoterms?\b", q, re.I)
    if RUN_FIELD.search(q) or RUN_SUBJECT.search(q) or REFERENCE.search(q) or specific or PAST_ASK.search(q) or \
            (DATA_ASK.search(q) and not strong):
        return []                                       # a data question: the records answer it
    if asks:
        # Routing decided this is a knowledge question; semantic retrieval
        # (optional) may now say WHICH item — never whether to answer.
        chosen = _semantic(q, k, hits)
        if chosen is not None:
            return chosen
    if not hits:
        return []
    top = hits[0]
    # A bare name ("HS code", "telex release", "OTIF") is a knowledge question;
    # a short question that merely contains a known word ("which shipments
    # failed") is not — it is about the run.
    bare = set(KB.tokens(q)) <= set(t for key in k.names for t in key.split()
                                     if top["id"] in k.names[key] and " " + key + " " in " " + " | ".join(KB.name_keys(q)) + " ")
    if top["named"] and (asks or (bare and not TO_ATLAS.search(q))):
        return hits
    if asks and top["score"] >= MIN_SCORE:
        return hits
    return []


def _semantic(q, k, hits):
    """Semantic candidates over keyword hits, or None to keep the keyword
    result. An exact name that is most of the question always wins; a
    semantic candidate wins only when it is clearly ahead of the next one."""
    from . import kb_embed, router
    if not kb_embed.enabled():
        return None
    # Similarity is not relevance: without logistics words ("weather in Cairo"
    # scores 0.47 against Egypt's customs entry) there is nothing to find.
    if router.OFF_TOPIC.search(q) or not (router.DOMAIN.search(q) or router.INCOTERM.search(q)):
        return None
    if hits and hits[0]["named"] and hits[0].get("coverage", 0) >= PASSING:
        return None                      # the name is most of the question ("what is demurrage?")
    cands = kb_embed.candidates(q, k, limit=5)
    if not cands:
        return None
    best, cos = cands[0]
    margin = cos - (cands[1][1] if len(cands) > 1 else 0.0)
    lo, gap = SEM_THRESHOLDS()
    if cos >= lo and margin >= gap:
        first = k.hit(best, cos * 100, semantic=round(cos, 3), coverage=1.0, specific=True)
        return [first] + [h for h in hits if h["id"] != best]
    if cos >= SEM_CLOSEST and not (hits and hits[0]["named"] and hits[0].get("specific")):
        # Not decisive: offered only as the closest entry, said as such.
        first = k.hit(best, cos * 100, semantic=round(cos, 3), coverage=0.0, specific=False)
        return [first] + [h for h in hits if h["id"] != best]
    return None


def SEM_THRESHOLDS():
    """(minimum cosine, minimum lead over the second candidate) — set from the
    development set for the model in use (ATLAS_EMBED_MIN, ATLAS_EMBED_MARGIN)."""
    from . import kb_embed
    name = kb_embed.model() or ""
    lo, gap = next((v for key, v in SEM_DEFAULTS.items() if key in name), (0.6, 0.03))
    try:
        lo = float(os.environ.get("ATLAS_EMBED_MIN") or lo)
        gap = float(os.environ.get("ATLAS_EMBED_MARGIN") or gap)
    except ValueError:
        pass
    return lo, gap


# Calibrated on knowledge/eval/dev_v2.json only (never on the held-out set).
SEM_DEFAULTS = {"bge-m3": (0.5, 0.03), "qwen3-embedding": (0.6, 0.03)}
# A semantic candidate below the bar above is still the closest entry, labelled so.
SEM_CLOSEST = 0.5


def applies(question):
    return bool(match(question))


def decisive(question):
    """True for a question only the Knowledge Engine can answer, whatever the
    run's rules made of it: an Incoterms 'who does what', a calculation with
    its numbers, or a comparison of two concepts it knows by name."""
    q = " ".join(str(question or "").split())
    if DATA_ASK.search(q) or SPECIFIC.search(q) or REFERENCE.search(q) or RUN_SUBJECT.search(q):
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
                note = " (link reachable on {0})".format(checked.get("on"))
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
                if not q.get("open"):
                    return "The export has no open shipments, so there is no past-due share to show (as of {0}).".format(asof)
                share = 100.0 * q["past_due"] / q["open"]
                return "{0} of {1} open shipments are past due ({2:.0f}%), as of {3}.".format(
                    q["past_due"], q["open"], share, asof)
            share = 100.0 * q["on_time"] / q["total"]
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


# ── scenarios: reasoning about a described situation ─────────────────────────

SCENARIO_CUE = re.compile(
    r"\b(what (?:should|do|can) (?:we|i)|what now|is (?:that|it|this) (?:right|wrong|correct|normal|actually)|"
    r"why (?:is|did|was|has)|how (?:do|should) we (?:handle|fix|deal|recover|proceed)|who do i believe|what'?s going on|any idea|"
    r"says|shows|billed|charged|but|won'?t|didn'?t|doesn'?t|still)\b|ليه|ايه اللي|أصدق|أصدّق|بس|نعمل ايه", re.I)


DEFINE_START = re.compile(r"^\s*(what is|what's a|what are|define|explain|meaning of|ما هو|ما هي|يعني ايه)\b", re.I)


def is_scenario(question):
    """A described situation to reason about, not a term to define: ten words
    or more, not a definition request. Whether a playbook fits decides the
    rest (_playbook needs more evidence when no situation cue is present)."""
    q = " ".join(str(question or "").split())
    return len(q.split()) >= 10 and not DEFINE_START.search(q)


def _clauses(q):
    """The operator's own statements, verbatim: the facts the message gives."""
    parts = re.split(r"(?<=[.;!?؟])\s+|\s+(?:but|and then|so now|however)\s+|,\s+(?=[a-z\u0600-\u06ff])|\s*،\s*|\s+و(?=[\u0600-\u06ff]{2,}\s)", q)
    out = []
    for p in parts:
        p = p.strip(" ,.;")
        if len(p.split()) < 3:
            continue
        if p.endswith("?") or p.endswith("؟") or re.match(r"^(what|why|how|who|is|are|should|can|could|do|does|any)\b", p, re.I) \
                or re.match(r"^(نعمل ايه|ايه|ليه|مين|هل|ازاي)", p):
            continue                          # the question, not a fact
        out.append(p)
    return out[:5]


def _words(text):
    return {w for w in re.findall(r"[a-z\u0600-\u06ff]{4,}", (text or "").lower())}


def _playbook(q, k, hits, cue=True):
    low = q.lower()
    near = {h["id"] for h in hits[:6]}
    best, best_score = None, 0.0
    for it in k.items.values():
        diag = it.get("diagnostic")
        if not diag or not k.current(it):
            continue
        score = sum(1.0 for t in diag.get("triggers") or [] if t in low)
        score += 0.5 * sum(1 for r in it.get("related") or [] if r.get("id") in near)
        if score > best_score:
            best, best_score = it, score
    # Two of its triggers, or one and a concept it rests on among the matches:
    # one everyday word ("invoice") does not make a situation.
    return best if best_score >= (1.5 if cue else 2.0) else None


def _scenario(q, k, hits, context, data, records=False):
    pb = _playbook(q, k, hits, cue=bool(SCENARIO_CUE.search(q)))
    if pb is None:
        return None
    diag = pb["diagnostic"]
    known = _clauses(q) or [q.strip(" ?؟")]
    told = _words(" ".join(known))
    missing = [f for f in diag.get("facts_needed") or []
               if not (_words(f) and len(_words(f) & told) / float(len(_words(f))) >= 0.6)]
    parts = ["_Reasoning from ATLAS's logistics knowledge about the situation you describe — not a finding "
             "about a record, and nothing below is confirmed._"]
    if records:
        parts = ["**How to reason about it** _(general logistics knowledge, separate from the records above; "
                 "nothing below is confirmed)_"]
    if known and not records:
        parts.append("**Known (from your message)**\n" + "\n".join("• " + x for x in known))
    said = _words(q) | {w.lower() for w in re.findall(r"\b[A-Z]{3}\b", q)}
    hyps = []
    for x in diag.get("hypotheses") or []:
        cue = _words(x["cause"] + " " + x["confirm"]) | {w.lower() for w in re.findall(r"\b[A-Z]{3}\b", x["confirm"])}
        hyps.append((len(cue & said), x))
    hyps.sort(key=lambda t: -t[0])
    parts.append("**Possible causes — hypotheses, not confirmed**\n" + "\n".join(
        "• {0}{1} — confirmed by: {2}; ruled out by: {3}".format(
            x["cause"], " *(your message points to this)*" if n >= 2 else "", x["confirm"], x["rule_out"])
        for n, x in hyps[:4]))
    if missing:
        parts.append("**Missing — what to establish first**\n" + "\n".join("• " + x for x in missing[:5]))
    calc = CALC.calculate(q)
    if calc is not None:
        parts.append("**Calculation**\n" + CALC.describe(calc))
    parts.append("**Next steps**\n" + "\n".join(
        "• {0}{1}".format(x["text"], " — *needs approval: {0}*".format(x["approval"]) if x.get("approval") else "")
        for x in diag.get("next_steps") or []))
    refs = REFERENCE.findall(q)
    tail = "From the message alone I can't tell which cause it is; the checks above decide it."
    if refs:
        tail += " For {0}, ask me about it by its reference and I'll answer from our records.".format(refs[0])
    parts.append("**What I can't tell** — " + tail)
    used = [pb]
    rel = [k.items[r["id"]] for r in pb.get("related") or [] if r.get("id") in k.items][:4]
    if rel:
        parts.append("**Concepts behind it:** " + " · ".join(it["term"] for it in rel))
    src = _sources(k, used + rel[:2])
    if src:
        parts.append("**Sources**\n" + "\n".join(src))
    parts.append(_status_line(k, pb))
    return {"answer": "\n\n".join(parts), "intent": "general_knowledge", "understood": True, "grounded": True,
            "knowledge": {"mode": "scenario", "items": [pb["id"]] + [it["id"] for it in rel],
                          "review": {pb["id"]: pb.get("review_status")}, "evidence": {pb["id"]: pb.get("evidence_status")},
                          "citations": [c for it in used for c in k.cite(it)],
                          "calculation": calc, "top": [[h["id"], h["score"]] for h in hits[:3]],
                          "known": known, "missing": missing[:5]},
            "sources": ["knowledge base"]}


def answer(question, context=None, data=None, routed=False):
    """
    {"answer", "intent", "knowledge": {...}, "sources", "grounded"} for a
    logistics question, or None when it is not one ATLAS's knowledge covers.
    `routed`: intelligence/router already decided this is a knowledge
    question (so its wording need not look like "what is…").
    """
    t0 = time.perf_counter()
    k = KB.kb()
    q = " ".join(str(question or "").split())
    hits = match(q, k, routed=routed)
    # "How do we set stock for critical slow movers?": a question about one
    # concept it knows by its own name, with no situation described, is
    # answered from that concept, not reasoned through as a scenario.
    about_one = bool(ASK_START.match(q)) and bool(hits) and hits[0]["named"] and hits[0].get("specific", True) and hits[0]["score"] >= 30 and \
        not SCENARIO_CUE.search(re.sub(r"\bstill\b", " ", q))
    if routed and is_scenario(q) and not _is_compare(q, hits) and not about_one:
        sc = _scenario(q, k, hits or k.search(q, limit=6), context, data)
        if sc is not None:
            KB.log_query(q, hits, (time.perf_counter() - t0) * 1000, "kb_answer:scenario", {"used": sc["knowledge"]["items"]})
            return sc
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
            next((h["item"] for h in hits if h["named"] and h.get("specific", True)), None) or \
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
        named = [h["item"] for h in hits if h["named"] and h.get("specific", True)]
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
        if hits[0].get("coverage", 1.0) < PASSING and not hits[0].get("specific"):
            # Matched on a word the question only mentions in passing: said as the
            # closest entry, not as the answer.
            how = "by meaning (semantic search)" if hits[0].get("semantic") else \
                "on “{0}” in your question".format(hits[0].get("name") or "a few words")
            parts.append("_Closest entry I have: **{0}** — matched {1}, which may not be "
                         "what you are asking about. If it is not, name the term you mean._".format(it["term"], how))
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
