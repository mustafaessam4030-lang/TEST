"""
ATLAS in conversation: any wording, English or Arabic, answered from ELAP's
own records — with web research when the question is about the outside world.

    question (any language)
        │  1. UNDERSTAND   local model -> {question_en, needs_web, web_query}
        ▼
    the existing ATLAS rules (dashboard/assistant.py), on question_en:
        runs, shipments, failures, PO jobs, carriers, briefing — read-only,
        from the run snapshot and the ATLAS store. This answer is the TRUTH.
        │  2. SEARCH       only when needed: the self-hosted search
        │                  (intelligence/research.py), results labelled WEB
        ▼
        │  3. COMPOSE      local model -> JSON sections, in the operator's
        │                  language: direct answer / verified / likely /
        │                  missing / external. ATLAS adds the labels and the
        │                  source links itself; the model never writes them.
        ▼
    4. FACT GUARD (intelligence/factguard.py, + Arabic claim words): every
       number, reference, link, status and success word must already be in
       the records or the web results. Otherwise -> ATLAS's own answer.

Any failure at any step — no model, slow model, bad JSON, guard rejects, no
search service — returns ATLAS's rule-based answer, which is what ATLAS did
before this module existed. Nothing here writes, triggers or changes
anything: the model sees text and returns text.

    ATLAS_CONVERSE      1 (default when a model is configured) | 0 = off
    plus ATLAS_LLM_* (intelligence/llm.py) and ATLAS_SEARCH_* (research.py)
"""

import json
import os
import re
import time

from . import factguard, llm
from . import research as R

ARABIC = re.compile(r"[؀-ۿ]")
# Arabic success words, and the English word the records must affirm first.
ARABIC_CLAIMS = [
    (re.compile(r"تم التحقق|تحقق|موثق|مؤكد|تأكيد"), ("verified", "confirmed")),
    (re.compile(r"نجح|بنجاح|ناجح"), ("success", "succeeded", "successful", "successfully")),
    (re.compile(r"تمت? (?:كتابة|الكتابة|تحديث|التحديث|حفظ|الحفظ)|تم حفظ|تم تحديث"),
     ("written", "saved", "updated")),
    (re.compile(r"وصل|وصلت"), ("arrived", "delivered")),
]
# "not", "never", "without", "no" (with "and"/"so" attached; not "ما", which
# also means "what"): a claim word just after one is negated.
ARABIC_NEGATION = re.compile(r"(?:^|\s)[وف]?(?:لم|لن|لا|ليس|ليست|غير|دون|بدون|عدم)(?:\s|$)")
LABELS = {
    "en": {"verified": "Verified (ELAP records)", "likely": "Likely, not proven",
           "missing": "Not in the records", "external":
           "External research (web, not live carrier status)", "sources": "Sources"},
    "ar": {"verified": "مؤكد (من سجلات ELAP)", "likely": "محتمل، غير مثبت",
           "missing": "غير موجود في السجلات", "external":
           "بحث خارجي (من الإنترنت، وليس حالة الناقل المباشرة)", "sources": "المصادر"},
}

UNDERSTAND = """You route questions for ATLAS, the assistant of Mantrac's logistics
automation platform (ELAP). ELAP's records hold: automation runs, shipments and their
carriers, ETAs/ATAs, failures and their evidence, human checks, PO (customs duty)
jobs, carrier health and site-change checks, and a morning briefing.
Return ONLY a JSON object:
{"chat": true or false, "question_en": "...", "needs_web": true or false, "web_query": "..."}
- chat: true when the message is personal or casual rather than a question about the
  work or the outside world: feelings ("I'm so sad", "I'm tired"), greetings, thanks,
  jokes, small talk. false for anything that asks about runs, shipments, PO jobs,
  carriers, errors or the world.
- question_en: the question restated as a short plain-English question. Keep every
  shipment reference, PO number, carrier and error text exactly as written.
- needs_web: true only when the answer needs public information ELAP cannot hold
  (general or technical knowledge, what an error means in general, a carrier's or
  port's public website, news or notices). false for anything about our runs,
  shipments, failures, PO jobs, carrier status in our runs, or the briefing.
- web_query: a short English web search query when needs_web is true, else "".
When it fits, use ATLAS's own wording for question_en: "summarize this run",
"how is the run going", "which shipments failed", "why did <reference> fail",
"where is shipment <reference>", "compare the carriers", "morning briefing",
"carrier health". Never turn a question into a request to start, stop, pause,
retry or change anything.
"""

CHAT = """You are ATLAS, the friendly assistant of Mantrac's logistics team. The
operator wrote something personal or casual. Reply in {language} like a kind, warm
colleague: 1-3 short, natural sentences with real empathy or a light touch of humour as
fits, and at most one emoji. Put the person first. Do not bring up failures, shipments or
numbers unless the operator mentioned work; then you may mention ONE fact from RUN, copied
exactly. You may end with a gentle offer to help. Never invent anything, never lecture,
never say you cannot feel or cannot check feelings.
Return ONLY a JSON object: {{"answer": "..."}}
"""

COMPOSE = """You are ATLAS, the friendly assistant of Mantrac's Enterprise Logistics
Automation Platform (ELAP). Answer the operator in {language}, warmly and naturally, like
a helpful colleague who knows the run: plain words, the direct answer first; a short
human touch is welcome ("Good news —", "Heads up:", "Sorry, that one's stuck"), at most
one emoji. Never robotic, never stiff.
Use ONLY the RECORDS and WEB below. RECORDS come from ELAP and are authoritative.
WEB is public research: never present it as ELAP data or as live carrier status.
Return ONLY a JSON object:
{{"answer": "1-3 sentence direct answer",
 "verified": ["facts RECORDS state as done, recorded or checked"],
 "likely": ["causes or explanations RECORDS mark as likely, possible or inferred"],
 "missing": ["parts of the operator's question that RECORDS cannot answer"],
 "external": ["full sentences of facts from WEB, each ending with its number like [1]"]}}
Rules — absolute:
- Never add a number, date, reference, shipment, link, metric or event that is not in
  RECORDS or WEB. Copy numbers and references exactly, with Western digits.
- Never say something succeeded, was verified, written, saved or confirmed unless
  RECORDS say so. Keep uncertainty uncertain. If RECORDS say there is no data, say so.
- "verified" and "likely" are only for RECORDS; everything from WEB goes in "external".
- "missing" is only for a fact the operator explicitly asked for that RECORDS lack; it
  is usually empty. Do not list things RECORDS mention.
- Leave a list empty rather than guess. No URLs. Never suggest bypassing CAPTCHAs,
  carrier security or access restrictions.
"""


def enabled():
    if (os.environ.get("ATLAS_CONVERSE") or "1").strip() == "0":
        return False
    return llm.provider().name != "none"


def language_of(text):
    return "ar" if ARABIC.search(text or "") else "en"


def _json(text):
    """The first JSON object in `text`, or None."""
    try:
        return json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text or "", re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except ValueError:
            return None


def understand(question, timeout=None):
    """-> {"question_en", "needs_web", "web_query"} or raises llm.LLMError."""
    text = llm.provider().generate(UNDERSTAND, "Question: " + question[:800],
                                   timeout=timeout, json_mode=True, max_tokens=160)
    data = _json(text) or {}
    q_en = str(data.get("question_en") or "").strip()
    if not q_en and data.get("chat") is True:
        q_en = question.strip()           # "im so sad" has no question to restate
    if not q_en:
        raise llm.LLMError("the model did not restate the question")
    return {"question_en": q_en[:500], "needs_web": bool(data.get("needs_web")),
            "web_query": str(data.get("web_query") or "").strip()[:200],
            "chat": data.get("chat") is True}


def web_research(query, limit=5):
    """-> (results, problem). Search only through the configured self-hosted
    service; nothing is pretended when it is off or fails."""
    if not query:
        return [], None
    if not R.enabled():
        return [], R.why_off()
    try:
        found = R.search(query)
    except Exception as error:
        return [], "the search service failed: {0}".format(str(error)[:120])
    out = []
    for item in found[:limit]:
        host, kind = R.publisher(item["url"])
        out.append({"url": item["url"], "title": item.get("title") or item["url"],
                    "snippet": item.get("snippet") or "", "publisher": host, "kind": kind})
    return out, (None if out else "the search returned no results")


def _guard(sections, inputs, lang):
    """factguard over the model's text, plus Arabic success words."""
    text = "\n".join([sections.get("answer") or ""] + [
        x for k in ("verified", "likely", "missing", "external") for x in sections.get(k) or []])
    ok, violations = factguard.check(text, inputs)
    if lang == "ar":
        affirmed = factguard._affirmed(" ".join(str(x) for x in inputs if x))
        for pattern, english in ARABIC_CLAIMS:
            claims = [m for m in pattern.finditer(text)
                      if not ARABIC_NEGATION.search(text[max(0, m.start() - 14):m.start()])]
            if claims and not affirmed.intersection(english):
                ok = False
                violations.append("Arabic claim '{0}' the records do not make".format(
                    claims[0].group(0)))
    return ok, violations


CITATION = re.compile(r"\[\d+\]")


WORD = re.compile(r"[a-z][a-z0-9_/-]{3,}")


def _from_records(item, records_low):
    """An English item is from the records when most of its words are; an
    Arabic one cannot be compared word for word and is judged by its numbers."""
    words = set(WORD.findall(CITATION.sub("", item).lower()))
    if ARABIC.search(item) or not words:
        return True
    return sum(w in records_low for w in words) >= 0.5 * len(words)


def _sort(sections, records, web):
    """A record-labelled item whose numbers are not ALL in the records — or,
    in English, whose words mostly are not — is not an ELAP fact: it goes
    under the web label when there was research, and is dropped otherwise.
    (factguard lets small numbers through; this does not.)"""
    rec = set(re.findall(r"\d+", records))
    records_low = records.lower()
    web_nums = set(re.findall(r"\d+", " ".join(w["snippet"] + " " + w["title"] for w in web)))
    out = dict(sections)
    moved = []
    for key in ("verified", "likely"):
        kept = []
        for item in sections.get(key) or []:
            nums = set(re.findall(r"\d+", CITATION.sub("", str(item))))
            if nums <= rec and _from_records(str(item), records_low):
                kept.append(item)
            elif web and nums <= web_nums | rec:
                moved.append(item)
        out[key] = kept
    out["external"] = list(sections.get("external") or []) + moved
    return out


def render(sections, lang, web):
    """ATLAS writes the labels and the links. Anything citing a web result
    goes under the external label, whichever list the model put it in."""
    labels = LABELS[lang]
    lists = {key: [] for key in ("verified", "likely", "missing", "external")}
    for key in lists:
        for item in sections.get(key) or []:
            # The model sometimes drops a citation's closing bracket: "[2".
            item = re.sub(r"\[(\d{1,2})(?![\d\]])", r"[\1]", str(item).strip())
            if not CITATION.sub("", item).strip(" .,;-"):
                continue                                # only a citation mark
            lists["external" if (web and CITATION.search(item)) or key == "external"
                  else key].append(item)
    parts = [str(sections.get("answer") or "").strip()]
    for key in ("verified", "likely", "missing"):
        if lists[key]:
            parts.append("**{0}**\n".format(labels[key]) +
                         "\n".join("- " + x for x in lists[key][:6]))
    if web:
        block = "**{0}**".format(labels["external"])
        if lists["external"]:
            block += "\n" + "\n".join("- " + x for x in lists["external"][:6])
        block += "\n\n{0}:\n".format(labels["sources"]) + "\n".join(
            "[{0}] {1} — {2}".format(i + 1, w["title"][:90], w["url"]) for i, w in enumerate(web))
        parts.append(block)
    return "\n\n".join(p for p in parts if p)


def answer(question, state, context, rules):
    """
    The conversation answer, or ATLAS's rule-based one. `rules(question,
    state, context)` is the existing ATLAS (dashboard/assistant.py).
    Never raises.
    """
    started = time.time()
    lang = language_of(question)
    pid = R.clean_progress_id((context or {}).get("progress_id"))
    timings = {}
    inner = dict(context or {}, _raw=True)          # the rules: no phrasing, no research

    def fallback(reason, base=None):
        # The rules on the operator's own words, with their own research but
        # without a second model call: the model already failed this question.
        reply = base or rules(question, state, dict(context or {}, _no_phrase=True))
        reply["llm"] = {"used": False, "reason": reason, "mode": "conversation"}
        return reply

    try:
        R.stage(pid, "Understanding the question…")
        t = time.time()
        plan = understand(question)
        timings["understand_s"] = round(time.time() - t, 1)
    except llm.LLMError as error:
        return fallback(str(error))
    except Exception as error:
        return fallback("the model's reading of the question failed: {0}".format(error))

    if plan["chat"] and not plan["needs_web"]:
        return _chat(question, state, inner, rules, lang, plan, timings, started)

    R.stage(pid, "Reading ELAP's records…")
    base = rules(plan["question_en"], state, inner)
    if base.get("request"):
        # An action is only ever taken from the operator's own words, through
        # the rules and the dashboard's own checks — never from the model's
        # restatement of them.
        return fallback("the question asks for an action; the rules answer it directly")
    records = base.get("answer") or ""
    web, web_problem = [], None
    if plan["needs_web"]:
        R.stage(pid, "Searching the web…")
        t = time.time()
        web, web_problem = web_research(plan["web_query"] or plan["question_en"])
        timings["search_s"] = round(time.time() - t, 1)

    web_text = "\n".join("[{0}] {1} ({2}) — {3}: {4}".format(
        i + 1, w["publisher"], w["kind"], w["title"], w["snippet"]) for i, w in enumerate(web))
    if plan["needs_web"] and not web:
        web_text = "(no web research: {0})".format(web_problem)
    prompt = "OPERATOR'S QUESTION: {0}\n\nRECORDS (ELAP, authoritative):\n{1}\n\nWEB:\n{2}".format(
        question[:800], records[:5000], web_text[:3500] or "(none)")
    try:
        R.stage(pid, "Writing the answer…")
        t = time.time()
        text = llm.provider().generate(
            COMPOSE.format(language="Arabic" if lang == "ar" else "English"), prompt,
            json_mode=True, max_tokens=int(os.environ.get("ATLAS_LLM_MAX_TOKENS") or 450))
        timings["compose_s"] = round(time.time() - t, 1)
    except llm.LLMError as error:
        out = fallback(str(error), base)
        return _with_web(out, web, web_problem, plan)

    sections = _json(text)
    if not isinstance(sections, dict) or not str(sections.get("answer") or "").strip():
        return _with_web(fallback("the model's answer was not in the expected form", base),
                         web, web_problem, plan)
    inputs = [question, records, plan["question_en"]] + \
        [w["snippet"] for w in web] + [w["title"] for w in web] + [w["publisher"] for w in web]
    ok, violations = _guard(sections, inputs, lang)
    if not ok:
        return _with_web(fallback("the model's answer added things the records do not hold: "
                                  + "; ".join(violations[:4]), base), web, web_problem, plan)

    sections = _sort(sections, "\n".join([question, plan["question_en"], records]), web)
    reply = dict(base, answer=render(sections, lang, web), understood=True)
    # The run's own answer stays one click away only when it is one: not the
    # "I didn't understand" text, which would read as a second, robotic reply.
    if base.get("intent") and not base.get("fallback"):
        reply["details"] = records
    ref = reply.get("reference")
    if plan["needs_web"] and ref and ref not in question + plan["question_en"]:
        # A question about the outside world is not about the shipment the
        # tab last discussed: no shipment badge or card on its answer.
        reply.pop("reference", None)
        reply.pop("card", None)
    reply["llm"] = {"used": True, "model": llm.provider().model, "mode": "conversation",
                    "language": lang, "question_en": plan["question_en"],
                    "timings": dict(timings, total_s=round(time.time() - started, 1))}
    return _with_web(reply, web, web_problem, plan, rendered=True)


WORK = re.compile(r"\b(run|runs|shipment|shipments|automation|work|working|carrier|carriers|"
                  r"hub|eta|ata|po|failed|failure|error|stuck|slow|taking|queue)\b|"
                  r"شحن|تشغيل|عمل|أتمتة|ناقل", re.I)

CHAT_FALLBACK = {
    "en": "I hear you 💛 I'm right here if you need anything — just ask me about the run.",
    "ar": "أنا معك 💛 إذا احتجت أي شيء، اسألني عن التشغيل في أي وقت.",
}


def _chat(question, state, inner, rules, lang, plan, timings, started):
    """A personal or casual message: a warm, short reply, with at most one fact
    from the run — and the same fact guard. Never the rules' "I don't know"."""
    run = rules("summarize this run", state, inner)
    # The run's facts only when the operator brought up work: "I'm so sad"
    # is about the person, and a 4B model will not keep shipments out of it
    # when they are in front of it.
    about_work = WORK.search(question + " " + plan["question_en"])
    brief = (run.get("answer") or "")[:1500] if about_work else "(not needed: personal message)"
    reply = {"answer": CHAT_FALLBACK[lang], "card": None, "intent": "chat",
             "understood": True, "grounded": True,
             "suggestions": run.get("suggestions") or []}
    try:
        t = time.time()
        text = llm.provider().generate(
            CHAT.format(language="Arabic" if lang == "ar" else "English"),
            "OPERATOR: {0}\n\nRUN:\n{1}".format(question[:500], brief),
            json_mode=True, max_tokens=160)
        timings["compose_s"] = round(time.time() - t, 1)
        said = str((_json(text) or {}).get("answer") or "").strip()
        ok, violations = _guard({"answer": said}, [question, brief], lang) if said else (False, [])
        if ok:
            reply["answer"] = said
            reply["llm"] = {"used": True, "model": llm.provider().model, "mode": "chat",
                            "language": lang, "question_en": plan["question_en"],
                            "timings": dict(timings, total_s=round(time.time() - started, 1))}
            return reply
        reason = "the model's reply added things the run does not hold: " + \
            "; ".join(violations[:3]) if violations else "the model gave no reply"
    except llm.LLMError as error:
        reason = str(error)
    reply["llm"] = {"used": False, "reason": reason, "mode": "chat"}
    return reply


def _with_web(reply, web, problem, plan, rendered=False):
    """Attach the web research to any reply, labelled; say plainly when a
    search was needed and could not be done."""
    if plan.get("needs_web"):
        reply["research"] = {"ok": bool(web), "query": plan.get("web_query"),
                             "reason": problem, "kind": "web"}
    if web:
        reply["web_sources"] = [{"url": w["url"], "title": w["title"],
                                 "publisher": w["publisher"], "kind": w["kind"]} for w in web]
        reply["sources"] = list(reply.get("sources") or []) + ["web search (self-hosted)"]
        if not rendered:
            reply["answer"] = (reply.get("answer") or "").rstrip() + "\n\n**{0}**\n".format(
                LABELS["en"]["external"]) + "\n".join(
                "[{0}] {1} — {2}".format(i + 1, w["title"][:90], w["url"])
                for i, w in enumerate(web))
    elif plan.get("needs_web") and problem:
        reply["answer"] = (reply.get("answer") or "").rstrip() + \
            "\n\nI did not search the web: {0}.".format(problem)
    return reply
