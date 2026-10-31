"""
ATLAS's read-only tools over the platform.

Every number ATLAS gives about the eHub export's stages, past due, on time,
a BU or To Do comes from one of the tools below, and each tool counts with
the hub's own rules (dashboard/hub.py STAGES, dashboard/clearance.py
past_due) — one definition of each number, the same one the hub draws.

    platform(can)            the pages this person may open and what each number means
    answer(q, state, ctx)    a short answer from the tools, or None (the rules answer as before)

Read-only: nothing here writes, starts or sends anything. Scoped: a
BU-limited person is counted only their BUs (context "_bus", set by the
server); a role without Clearance gets no export figures. A number that
is not in a tool's result is never written: the reply is checked
(grounded) and, if anything slipped, it says it has no data instead.
"""

import json
import re
import time
from datetime import date

from dashboard import clearance as _C
from dashboard import hub as _H

AR = re.compile(r"[؀-ۿ]")

# ── the platform map: built from the hub's own registry and rules ──────────
_STAGE_AR = {"transit": "في الطريق", "arrived": "وصلت، لدى وحدة العمل", "customs": "في الجمارك",
             "awaiting": "تم التخليص، بانتظار التسليم", "done": "مكتملة"}
_STAGE_EN = {"transit": "in transit", "arrived": "arrived and with the BU", "customs": "at customs",
             "awaiting": "cleared and awaiting delivery", "done": "completed"}
_DEF_AR = {
    "transit": "مفتوحة، بلا تاريخ وصول فعلي (ATA)، وتاريخ الوصول المتوقع (ETA) في يوم بيانات التصدير أو بعده.",
    "arrived": "مفتوحة، وصلت (لها ATA أو مرّ الـETA)، وخطوتها Submit to BU أو Assign Clearing Agent أو Correction Required.",
    "customs": "مفتوحة، وصلت، وخطوتها جمركية: Pre Entry أو Declaration أو Duty Assessment أو Duty Payment أو Release.",
    "awaiting": "مفتوحة، خطوتها Awaiting Receipt: أفرجت الجمارك ولم تؤكد وحدة العمل الاستلام.",
    "done": "خطوة eHub هي Completed.",
}
PAST_DUE = ("Open (not Completed) and the export's Performance column is above 0: the days the "
            "current step is past its due date. Counted from the export as it is; nothing estimated.")
PAST_DUE_AR = ("مفتوحة (غير مكتملة) وعمود Performance في التصدير أكبر من صفر: عدد أيام تأخر الخطوة الحالية "
               "عن موعدها. تُعد من التصدير كما هو، بلا تقدير.")
ON_TIME = "The export's own On Time flag, over every shipment in it (open and completed)."
ON_TIME_AR = "علامة On Time في التصدير نفسه، على كل الشحنات (المفتوحة والمكتملة)."
SOURCE = "eHub export"


def platform(can=lambda p: True):
    """The map ATLAS answers from: pages (with links) and the definitions of
    the numbers, taken from hub.MODULES and hub.STAGES — never restated."""
    return {"pages": [{"id": m["id"], "title": m["title"], "status": m["status"], "link": "/hub#" + m["id"],
                       "note": m.get("note") or ""} for m in _H.MODULES if can(m["perm"])],
            "stages": [{"id": i, "label": l, "definition": d, "link": "/hub#overview?list=" + i}
                       for i, l, _t, d in _H.STAGES],
            "past_due": PAST_DUE, "on_time": ON_TIME}


# ── what a question asks ────────────────────────────────────────────────────
_STAGE_WORDS = [
    ("awaiting", r"awaiting (delivery|receipt|reciept)|cleared,? (and )?awaiting|cleared but not delivered|"
                 r"بانتظار التسليم|في انتظار التسليم|تم التخليص"),
    ("transit", r"in transit|on the way|en route|في الطريق|في العبور|قيد الشحن"),
    ("customs", r"at customs|in customs|through customs|customs stage|في الجمارك|بالجمارك|لدى الجمارك"),
    ("arrived", r"arrived,? with the bu|with the bu\b|waiting (on|for) the bu|وصلت"),
    ("done", r"\bcompleted\b|\bfinished\b|مكتملة|اكتملت"),
]
COUNT = re.compile(r"how many|number of|count\b|\btotal\b|كم\b|عدد", re.I)
PASTDUE = re.compile(r"past[- ]?due|overdue|\blate\b|delayed|متأخر|المتأخر|تأخير|متاخر", re.I)
ONTIME = re.compile(r"on[- ]?time|في الموعد|بالموعد|الالتزام", re.I)
MOST = re.compile(r"which bu|what bu|worst bu|most past|most overdue|most late|highest|أي وحدة|أكثر", re.I)
HOW = re.compile(r"how (is|are|do you|does it|do we) .*(count|calculat|defin|work)|how .* counted|"
                 r"what does .* mean|what is meant by|meaning of|define\b|definition|explain (this|that|the) (number|figure|count)|"
                 r"where does (this|that) number|كيف (يتم )?(حساب|تحسب|يحسب|تعد)|ماذا يعني|ما معنى|اشرح (هذا|هذه) (الرقم|العدد)", re.I)
THIS_NUMBER = re.compile(r"(this|that) (number|figure|count)|هذا الرقم|هذا العدد", re.I)
TODO = re.compile(r"\bto[- ]?do\b|my tasks|what needs (me|a person|attention)|قائمة المهام|مهامي|المهام", re.I)
NO_SOURCE = [
    ("rates", re.compile(r"freight rate|\brates?\b.*\b(to|for|from)\b|rate card|أسعار الشحن|سعر الشحن", re.I)),
    ("tender", re.compile(r"\btenders?\b|spot tender|مناقص", re.I)),
    ("documents", re.compile(r"(missing|which|what) (documents|docs|paperwork)|documents? (are )?missing|"
                             r"المستندات الناقصة|مستندات ناقصة|الوثائق الناقصة", re.I)),
]
# The tools cut by BU only: a question by agent, forwarder, mode, step,
# division, carrier or month is left to the rules' own filter-aware answer.
OTHER_CUT = re.compile(r"\bagents?\b|clearing agent|forwarder|\bffw\b|\bmode\b|\bair\b|\bocean\b|\bsea\b|\bsteps?\b|"
                       r"division|\bdiv\b|carrier|airline|\bmonth\b|january|february|march|april|\bmay\b|june|july|"
                       r"august|september|october|november|december|this week|last week|وكيل|المخلص|الشاحن|جوي|بحري|شهر", re.I)
# A state the tools do not count (waiting, stuck, on hold…): a total would
# answer a different question, so the rules answer it.
UNKNOWN_STATUS = re.compile(r"waiting|pending|stuck|hold|blocked|deliver|releas|cleared|arriv|transit|customs|"
                            r"escalat|correction|انتظار|معلق|متوقف", re.I)
_COUNTRY = {"egypt": "EG", "مصر": "EG", "ghana": "GH", "غانا": "GH", "tanzania": "TZ", "تنزانيا": "TZ",
            "nigeria": "NG", "نيجيريا": "NG", "kenya": "KE", "كينيا": "KE", "iraq": "IQ", "العراق": "IQ",
            "uganda": "UG", "أوغندا": "UG", "اوغندا": "UG", "djibouti": "DJ", "جيبوتي": "DJ",
            "sierra leone": "SL", "سيراليون": "SL", "liberia": "LR", "ليبيريا": "LR",
            "ethiopia": "ET", "إثيوبيا": "ET", "اثيوبيا": "ET"}


_AR_NAME = {"EG": "مصر", "GH": "غانا", "TZ": "تنزانيا", "NG": "نيجيريا", "KE": "كينيا", "IQ": "العراق",
            "UG": "أوغندا", "DJ": "جيبوتي", "SL": "سيراليون", "LR": "ليبيريا", "ET": "إثيوبيا"}


def _in_bu(bu, ar, word="in"):
    if not bu:
        return ""
    if ar:
        return " في {0}".format(_AR_NAME[bu] + " (" + bu + ")" if bu in _AR_NAME else bu)
    return " {0} {1}".format(word, bu)


def _bus_in(question, known):
    """BU codes named in the question: an upper-case code of the export
    ("EG", "JR") or a country name (English or Arabic) whose code is one."""
    found = []
    for w in re.findall(r"\b[A-Z]{2,3}\b", question or ""):
        if w in known and w not in found:
            found.append(w)
    low = (question or "").lower()
    for name, code in _COUNTRY.items():
        if name in low and code in known and code not in found:
            found.append(code)
    return found


def _stage_in(question):
    for sid, pat in _STAGE_WORDS:
        if re.search(pat, question or "", re.I):
            return sid
    return None


# ── numbers: written only from a tool's result ─────────────────────────────
def _n(x):
    return "{0:,}".format(x)


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


_NUM = re.compile(r"(?<![\w/#=.-])\d[\d,]*(?:\.\d+)?%?")


def _fixed():
    """The rules' own wording (definitions), whose numbers are not figures."""
    return [PAST_DUE, PAST_DUE_AR, ON_TIME, ON_TIME_AR] + list(_DEF_AR.values()) + [d for *_x, d in _H.STAGES]


def grounded(text, facts):
    """Every number in `text` (outside links, dates and the rules' own
    definitions) is one of `facts`. The answer's own guard, and the eval's."""
    plain = text or ""
    for f in _fixed():
        plain = plain.replace(f, " ")
    plain = re.sub(r"\]\([^)]*\)", "]", plain)                     # link targets
    plain = re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2} [A-Z][a-z]{2} \d{4}\b|\b\d{1,2} (?:" + "|".join(_AR_MONTHS) +
                   r") \d{4}\b", " ", plain)   # dates
    allowed = {str(f).replace(",", "") for f in facts}
    bad = [m for m in _NUM.findall(plain) if m.replace(",", "").rstrip("%") not in allowed]
    return not bad, bad


_AR_MONTHS = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")


def _dmy(iso, ar=False):
    try:
        d = date.fromisoformat(str(iso)[:10])
        return "{0} {1} {2}".format(d.day, _AR_MONTHS[d.month - 1] if ar else d.strftime("%b"), d.year)
    except Exception:
        return str(iso or "")


# ── the tools (each read-only; each takes the person's scope) ───────────────
def t_rows(allowed, data):
    return _H.scoped_rows(data, allowed)


def t_stages(rows, data):
    return _H.stages(data, rows=rows)


# ── composing a reply ───────────────────────────────────────────────────────
def _how_first(text, ar):
    """For "how is it counted / what does it mean": the definition leads, the
    number as it is now follows."""
    key = "\n\n**طريقة العد:** " if ar else "\n\n**How it's counted:** "
    if key not in text:
        return text
    head, rest = text.split(key, 1)
    definition, _sep, tail = rest.partition("\n\n")
    return (key.strip() + " " + definition + "\n\n" + ("الآن: " if ar else "Now: ") + head + ("\n\n" + tail if tail else ""))


def _reply(text, intent, facts, tools, links=()):
    ok, bad = grounded(text, facts)
    if not ok:                                   # fail closed: never a number no tool gave
        text, intent = "I don't have data for that.", "platform_no_data"
    return {"answer": text, "card": None, "reference": None, "grounded": True, "final": True,
            "intent": intent, "understood": True, "sources": [SOURCE] if tools else [],
            "tools": list(tools), "links": [dict(l) for l in links],
            "suggestions": [], "downloads": [], "buttons": [], "actions": []}


def _src_line(asof, ar):
    return ("المصدر: تصدير eHub، بيانات يوم {0}." if ar else "Source: eHub export, data as of {0}.").format(_dmy(asof, ar))


def _scope_note(allowed, ar):
    if allowed is None:
        return ""
    bus = ", ".join(sorted(allowed)) or "—"
    return (" (وحدات العمل المسموح لك بها فقط: {0})" if ar else " (only your business units: {0})").format(bus)


def _link(label, href):
    return "[{0}]({1})".format(label, href)


def _ans_no_source(kind, ar):
    m = {x["id"]: x for x in _H.MODULES}
    if kind == "documents":
        text = ("لا توجد بيانات: تصدير eHub لا يحتوي قائمة مستندات، لذا لا أستطيع معرفة المستندات الناقصة." if ar else
                "No data: the eHub export has no document list, so I can't tell which documents are missing.")
        return _reply(text, "platform_no_data", [], [])
    mod = m.get("rates" if kind == "rates" else "tender") or {}
    text = ("لا توجد بيانات: {0} غير متصلة بعد ({1})." if ar else "No data: {0} isn't connected yet ({1}).").format(
        mod.get("title", kind), mod.get("note", "no source").replace("Not connected: ", ""))
    return _reply(text + "\n\n" + _link("Open " + mod.get("title", kind), "/hub#" + mod.get("id", "")),
                  "platform_no_data", [], [], links=[{"label": mod.get("title"), "href": "/hub#" + mod.get("id", "")}])


def _ans_stage(sid, bu, rows, data, allowed, ar, how=False):
    if bu:
        rows = [r for r in rows if r.get("bu") == bu]
    s = t_stages(rows, data)
    st = next(x for x in s["stages"] if x["id"] == sid)
    n, label = st["count"], (_STAGE_AR[sid] if ar else st["label"])
    where = _in_bu(bu, ar)
    href = "/hub#overview?list={0}{1}".format(sid, "&bu=" + bu if bu else "")
    facts, extra = [n], ""
    if sid == "customs":
        # The same customs steps before arrival are counted In transit, as on the hub.
        asof = (data.get("asof") or "")[:10]
        early = sum(1 for r in rows if _C.is_open(r) and r.get("step") in _H.AT_CUSTOMS
                    and not r.get("ata") and (r.get("eta") or "")[:10] >= asof and r.get("eta"))
        if early:
            facts.append(early)
            extra = ("\n\n{0} شحنة أخرى في خطوة جمركية لكنها لم تصل بعد، فتُعد «في الطريق»." if ar else
                     "\n\n{0} more are at a customs step but have not arrived yet, so they count as In transit.").format(_n(early))
    if ar:
        head = "**{0}** شحنة {1}{2}.".format(_n(n), label, where)
    else:
        head = "**{0}** shipments are {1}{2}.".format(_n(n), _STAGE_EN[sid], where)
    text = head + _scope_note(allowed, ar) + extra
    text += "\n\n" + _link("عرض القائمة" if ar else "Open the list", href)
    text += "\n\n" + ("**طريقة العد:** " + _DEF_AR[sid] if ar else "**How it's counted:** " + st["definition"])
    text += "\n\n" + _src_line(data.get("asof"), ar)
    return _reply(text, "platform_stage", facts, ["stages"], links=[{"label": "list", "href": href}])


def _ans_pastdue(bu, rows, data, allowed, ar, most=False):
    open_ = [r for r in rows if _C.is_open(r)]
    late = [r for r in open_ if _C.past_due(r)]
    if most and not bu:
        per = {}
        for r in late:
            per[r["bu"]] = per.get(r["bu"], 0) + 1
        if not per:
            return _reply("لا توجد شحنات متأخرة." if ar else "No shipments are past due.", "platform_pastdue", [], ["pastdue"])
        top = sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))
        b, k = top[0]
        others = top[1:3]
        facts = [k, len(late)] + [v for _b, v in others]
        if ar:
            text = "**{0}** لديها أكثر الشحنات المتأخرة: **{1}** من {2}.".format(b, _n(k), _n(len(late)))
            if others:
                text += " تليها " + "، ".join("{0} ({1})".format(x, _n(v)) for x, v in others) + "."
        else:
            text = "**{0}** has the most past-due shipments: **{1}** of {2}.".format(b, _n(k), _n(len(late)))
            if others:
                text += " Next: " + ", ".join("{0} ({1})".format(x, _n(v)) for x, v in others) + "."
        href = "/hub#overview?list=pastdue&bu=" + b
    else:
        if bu:
            open_ = [r for r in open_ if r.get("bu") == bu]
            late = [r for r in late if r.get("bu") == bu]
        facts = [len(late), len(open_)]
        where = _in_bu(bu, ar)
        text = ("**{0}** شحنة متأخرة{1}، من {2} شحنة مفتوحة." if ar else
                "**{0}** shipments are past due{1}, of {2} open.").format(_n(len(late)), where, _n(len(open_)))
        href = "/hub#overview?list=pastdue" + ("&bu=" + bu if bu else "")
    text += _scope_note(allowed, ar)
    text += "\n\n" + _link("عرض القائمة" if ar else "Open the list", href)
    text += "\n\n" + ("**طريقة العد:** " + PAST_DUE_AR if ar else "**How it's counted:** " + PAST_DUE)
    text += "\n\n" + _src_line(data.get("asof"), ar)
    return _reply(text, "platform_pastdue", facts, ["pastdue"], links=[{"label": "list", "href": href}])


def _ans_ontime(bu, rows, data, allowed, ar):
    if bu:
        rows = [r for r in rows if r.get("bu") == bu]
    on = sum(1 for r in rows if r.get("ontime"))
    p = _pct(on, len(rows))
    if p is None:
        return _reply("لا توجد شحنات في التصدير." if ar else "No shipments in the export.", "platform_no_data", [], ["stages"])
    where = _in_bu(bu, ar, "for")
    text = ("نسبة الالتزام بالموعد{0}: **{1}%** ({2} من {3})." if ar else
            "On time{0}: **{1}%** ({2} of {3} shipments).").format(where, p, _n(on), _n(len(rows)))
    text += _scope_note(allowed, ar)
    href = "/hub#overview?list=late" + ("&bu=" + bu if bu else "")
    text += "\n\n" + _link("الشحنات غير الملتزمة" if ar else "Open the late ones", href)
    text += "\n\n" + ("**طريقة العد:** " + ON_TIME_AR if ar else "**How it's counted:** " + ON_TIME)
    text += "\n\n" + _src_line(data.get("asof"), ar)
    return _reply(text, "platform_ontime", [p, on, len(rows)], ["stages"], links=[{"label": "late", "href": href}])


def _ans_total(bu, rows, data, allowed, ar):
    if bu:
        rows = [r for r in rows if r.get("bu") == bu]
    open_ = [r for r in rows if _C.is_open(r)]
    late = [r for r in open_ if _C.past_due(r)]
    where = _in_bu(bu, ar, "for")
    text = ("**{0}** شحنة{1}: {2} مفتوحة، منها {3} متأخرة." if ar else
            "**{0}** shipments{1}: {2} open, {3} of them past due.").format(_n(len(rows)), where, _n(len(open_)), _n(len(late)))
    text += _scope_note(allowed, ar)
    href = "/hub#overview?list=all" + ("&bu=" + bu if bu else "")
    text += "\n\n" + _link("عرض القائمة" if ar else "Open the list", href)
    text += "\n\n" + _src_line(data.get("asof"), ar)
    return _reply(text, "platform_total", [len(rows), len(open_), len(late)], ["stages"], links=[{"label": "list", "href": href}])


def _ans_how(subject, rows, data, allowed, ar, page, bu=None):
    """What a number means, with the number as it is now (for the BU on
    screen when the question is about the list that is open)."""
    one = None
    if subject in ("pastdue", "ontime"):
        one = (_ans_pastdue if subject == "pastdue" else _ans_ontime)(bu, rows, data, allowed, ar)
    elif subject in dict(_STAGE_WORDS):
        one = _ans_stage(subject, bu, rows, data, allowed, ar, how=True)
    if one is not None:
        if one["intent"] != "platform_no_data":
            one["answer"], one["intent"] = _how_first(one["answer"], ar), "platform_how"
        return one
    # "Explain this number" on the hub's Overview with nothing selected: its stage strip.
    s = t_stages(rows, data)
    facts = [x["count"] for x in s["stages"]] + [s["total"], s["other"]]
    lines = [("**{0}** — {1}: {2}" if not ar else "**{0}** — {1}: {2}").format(
        _n(x["count"]), _STAGE_AR[x["id"]] if ar else x["label"], _DEF_AR[x["id"]] if ar else x["definition"])
        for x in s["stages"]]
    head = ("أرقام شريط المراحل في النظرة العامة، من {0} شحنة:" if ar else
            "The Overview's stage strip, counted from {0} shipments:").format(_n(s["total"]))
    text = head + "\n" + "\n".join("- " + l for l in lines)
    if s["other"]:
        text += "\n\n" + ("{0} شحنة مفتوحة لا تُصنف (بلا ETA أو بخطوة غير معروفة)." if ar else
                          "{0} open shipments fit no stage (no ETA, or a step the hub doesn't know).").format(_n(s["other"]))
    text += _scope_note(allowed, ar) + "\n\n" + _src_line(data.get("asof"), ar)
    return _reply(text, "platform_how", facts, ["stages"])


def _ans_todo(can, state, ar):
    from dashboard import todo as _TD
    t = _TD.build(can, state)
    items = [i for i in t["items"] if i["state"] != "completed"]
    facts = [t["outstanding"]]
    if not items:
        return _reply("لا شيء ينتظرك في قائمة المهام." if ar else "Nothing is waiting in To Do.",
                      "platform_todo", facts, ["todo"], links=[{"label": "To Do", "href": "/hub#todo"}])
    text = ("**{0}** مهمة مفتوحة في قائمة المهام. الأهم:" if ar else "**{0}** open in To Do. First:").format(_n(t["outstanding"]))
    for i in items[:3]:
        text += "\n- " + i["title"]
        facts += [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", i["title"])]
    text += "\n\n" + _link("فتح قائمة المهام" if ar else "Open To Do", "/hub#todo")
    return _reply(text, "platform_todo", facts, ["todo"], links=[{"label": "To Do", "href": "/hub#todo"}])


# ── the entry point ─────────────────────────────────────────────────────────
def detect(question, context=None):
    """-> (kind, detail) when a tool answers this question, else None."""
    q = (question or "").strip()
    if not q or len(q) > 300:
        return None
    ctx = context or {}
    if ctx.get("domain") == "po":
        return None
    for kind, pat in NO_SOURCE:
        if kind == "rates" and (ONTIME.search(q) or re.search(r"success rate|failure rate|pass rate", q, re.I)):
            continue                     # an on-time rate is a count, not a freight rate
        if pat.search(q):
            return ("no_source", kind)
    if TODO.search(q):
        return ("todo", None)
    stage = _stage_in(q)
    if OTHER_CUT.search(q) and not HOW.search(q):
        return None
    if HOW.search(q):
        if PASTDUE.search(q):
            return ("how", "pastdue")
        if ONTIME.search(q):
            return ("how", "ontime")
        if stage:
            return ("how", stage)
        if THIS_NUMBER.search(q):
            view = ctx.get("view") or {}
            sel = view.get("stage") if isinstance(view, dict) else None
            if sel in ("pastdue", "late"):
                return ("how", "pastdue" if sel == "pastdue" else "ontime")
            if sel in dict(_STAGE_WORDS):
                return ("how", sel)
            if ctx.get("page") in ("hub", "tracking"):
                return ("how", "strip")
        return None
    if ONTIME.search(q) and re.search(r"rate|percent|%|how many|نسبة|معدل", q, re.I):
        return ("ontime", None)
    if PASTDUE.search(q) and MOST.search(q):
        return ("pastdue", "most")
    if PASTDUE.search(q) and (COUNT.search(q) or re.search(r"^(show|list)\b", q, re.I)):
        return ("pastdue", None)
    if stage and (COUNT.search(q) or re.search(r"^(show|list)\b", q, re.I)):
        return ("stage", stage)
    if COUNT.search(q) and re.search(r"\bshipments?\b|شحن", q, re.I) and \
            re.search(r"\b(have|has|does|in|for|total|open)\b|لدى|في|لديها", q, re.I) and \
            not UNKNOWN_STATUS.search(q):
        return ("total", None)
    return None


def answer(question, state, context=None):
    """A short answer from the tools, or None when no tool answers it."""
    started = time.perf_counter()
    ctx = context or {}
    hit = detect(question, ctx)
    if hit is None:
        return None
    kind, detail = hit
    view = ctx.get("view") if isinstance(ctx.get("view"), dict) else {}
    if ctx.get("page") == "clearance" and kind not in ("no_source", "todo") and \
            any(v for k, v in view.items() if k != "bu"):
        return None          # Clearance's filters on screen (mode, agent…): its own filter-aware answer
    ar = bool(AR.search(question or ""))
    can = ctx.get("_can") or (lambda p: True)
    if kind == "no_source":
        reply = _ans_no_source(detail, ar)
    elif kind == "todo":
        reply = _ans_todo(can, state, ar)
    else:
        if ctx.get("clearance") == "0" or not can("clearance.view"):
            reply = _reply("مستوى وصولك لا يتيح أرقام تصدير eHub." if ar else
                           "Your access level can't see the eHub export's figures.", "platform_denied", [], [])
        else:
            data = _C.load()
            if not data:
                reply = _reply("لا توجد بيانات: لم يُحمَّل تصدير eHub بعد." if ar else
                               "No data: no eHub export is loaded yet.", "platform_no_data", [], [])
            else:
                low = (question or "").lower()
                named = {str(r[k]).lower() for r in _C.records(data) for k in ("step", "ffw", "agent", "div", "mode")
                         if r.get(k) and len(str(r[k])) >= 4} - {"completed"}      # a stage of its own
                if kind != "how" and any(v in low for v in named):
                    return None      # a step, forwarder, agent, division or mode: the rules' filter-aware answer
                allowed = ctx.get("_bus")
                rows = t_rows(allowed, data)
                known = {r["bu"] for r in _C.records(data) if r.get("bu")}
                asked = _bus_in(question, known)
                bu = asked[0] if asked else None
                this_list = kind == "how" and THIS_NUMBER.search(question or "") and view.get("stage")
                if bu is None and view.get("bu") in known and (kind != "how" or this_list) and \
                        not (kind == "pastdue" and detail == "most"):
                    bu = view.get("bu")              # the BU on screen (or the open list's), when none is named
                if bu is not None and allowed is not None and bu not in allowed:
                    reply = _reply(("لا تملك صلاحية الاطلاع على {0}." if ar else
                                    "You don't have access to {0}.").format(bu), "platform_denied", [], [])
                elif kind == "stage":
                    reply = _ans_stage(detail, bu, rows, data, allowed, ar)
                elif kind == "pastdue":
                    reply = _ans_pastdue(bu, rows, data, allowed, ar, most=detail == "most")
                elif kind == "ontime":
                    reply = _ans_ontime(bu, rows, data, allowed, ar)
                elif kind == "total":
                    reply = _ans_total(bu, rows, data, allowed, ar)
                else:
                    reply = _ans_how(detail, rows, data, allowed, ar, ctx.get("page"), bu)
    reply["lang"] = "ar" if ar else "en"
    reply["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    _log(reply)
    return reply


# ── logging: what was used, never who asked or what was asked ──────────────
_LOG_MAX = 2000


def _log(reply):
    try:
        path = _C.runtime() / "atlas_tools.jsonl"
        line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "intent": reply.get("intent"),
                           "tools": reply.get("tools"), "lang": reply.get("lang"),
                           "ms": reply.get("latency_ms")}, separators=(",", ":"))
        lines = path.read_text(encoding="utf-8").splitlines()[-(_LOG_MAX - 1):] if path.exists() else []
        path.write_text("\n".join(lines + [line]) + "\n", encoding="utf-8")
    except Exception:
        pass
