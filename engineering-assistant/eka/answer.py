"""Grounded answering: route -> retrieve -> select evidence -> generate -> validate.

The default answer is *extractive*: claims are sentences, list items or table
rows copied from the retrieved passages, each with its citation, so a value
can only appear in an answer if it is printed in a document. When an Ollama
model is configured it phrases the answer from the same passages, and its
claims pass the same validation; failures fall back to the extractive answer.

Status values:
    answered               claims backed by validated citations
    needs_clarification    the answer depends on equipment the question does not name
    conflict               documents disagree and nothing says which one governs
    insufficient_evidence  the documents searched do not establish the answer
    smalltalk / library / help   conversational turns, no retrieval
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from . import citations as cite
from .compose import question_topic, short_equipment
from .config import Settings
from .conversation import Conversations, Route, Turn, route
from .generation import GenerationError, OllamaGenerator
from .modes import ModesMixin
from .retrieval import Candidate, Retriever
from .store import Store
from .textutil import content_terms, identifiers, numbers, sentences, tokens, compact

log = logging.getLogger(__name__)

VALUE_QUESTION = re.compile(
    r"\b(what is|what's|what are|how much|how many|how often|how long|how far|which|value|rating|rated|"
    r"capacity|torque|pressure|setting|interval|limit|maximum|minimum|max|min|weight|speed|size|"
    r"temperature|flow|power|part number|number)\b", re.I)
SAFETY_TERMS = re.compile(
    r"\b(pressure|torque|lift\w*|load|voltage|electric\w*|brake|lockout|tagout|hydraulic|weld\w*|crane|"
    r"rigging|temperature|safety|warning|caution|danger|hazard\w*|relief|injur\w*)\b", re.I)
STEP_LINE = re.compile(r"^\s*(?:\(continued\)|\d{1,2}[.)]\s)", re.I)
MIN_EVIDENCE = 0.34        # weighted share of the question's terms a supporting line must cover
MIN_TOPIC_COVERAGE = 0.5   # ...and of its topic words (identifiers excluded), headings at half credit
MIN_TOPIC_COVERAGE_SEMANTIC = 0.25  # topic coverage still required when the embedder rates a passage strongly similar
# Words that frame a question ("how *long*", "parts *needed*") rather than name its topic.
FRAME_WORDS = frozenset(tokens(
    "long much many often heavy big large small far wide high tall needed required specified recommended "
    "allowed permitted use used using value values kind type mean means called "
    "compare comparison difference differences versus summarize summary explain describe show tell", keep_stopwords=True))
MAX_CLAIMS = 4


@dataclass
class Unit:
    """A citable piece of evidence: one sentence, list of steps, table row or warning."""
    cand: Candidate
    text: str
    context: str
    score: float = 0.0


@dataclass
class Draft:
    claims: list[dict] = field(default_factory=list)
    status: str = "answered"
    message: str = ""
    conflicts: list[dict] = field(default_factory=list)
    clarification: dict | None = None
    unresolved: list[str] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


class AnswerEngine(ModesMixin):
    def __init__(self, settings: Settings, store: Store, retriever: Retriever,
                 conversations: Conversations, generator: OllamaGenerator | None = None):
        self.settings = settings
        self.store = store
        self.retriever = retriever
        self.conversations = conversations
        self.generator = generator
        self._pending: dict[str, tuple] = {}
        self._local = threading.local()   # per-request progress callback (requests run in parallel)

    # ── entry point ──
    def ask(self, message: str, conversation_id: str | None = None, doc_ids: list[str] | None = None,
            progress=None, persist: bool = True, use_generator: bool = True) -> dict:
        """Answer one message. ``progress(text, phase, docs)`` receives real pipeline stages for streaming.

        With ``persist=False`` nothing is recorded until ``commit(answer)`` is called, so a
        response the user stopped can be discarded.
        """
        started = time.perf_counter()
        conversation_id = conversation_id or self.conversations.new_id()
        history = self.conversations.history(conversation_id)
        self._local.progress = progress
        self._progress("Understanding the question", "understanding")
        r = route(message, history)
        self._local.use_generator = use_generator
        self._local.conversation_id = conversation_id
        self._local.visual_note = ""
        try:
            if r.intent == "smalltalk":
                answer = self._smalltalk(r.smalltalk_kind)
            elif r.intent == "library":
                answer = self._library()
            elif r.intent == "not_found_help":
                answer = self._not_found_help(history)
            elif r.intent == "web":
                answer = self._web(r.question)
            else:
                answer = self._question(r, doc_ids, history)
                if answer.get("status") == "insufficient_evidence" and not answer.get("clarification"):
                    # not in the documents: offer the web, and (with a model) an answer from general knowledge
                    answer["web_query"] = r.question or message
                    # general knowledge only for general questions: never guess a named machine's or part's values
                    if not identifiers(r.question or message) and r.mode != "visual":
                        self._general(answer, r.question or message, found_nothing=True)
        except Exception as exc:  # never return an empty answer; say what failed
            log.exception("answering failed")
            answer = self._base("error", "Something went wrong while answering ({0}). "
                                "The documents were not changed; please try again.".format(type(exc).__name__))
        answer["conversation_id"] = conversation_id
        answer["intent"] = r.intent
        answer.setdefault("mode", r.mode if r.intent == "question" else r.intent)
        answer["latency_ms"] = int((time.perf_counter() - started) * 1000)
        if not answer["message"].strip() and not answer["claims"]:
            answer["status"] = "error"
            answer["message"] = "No answer could be produced. Please rephrase the question."
        answer["answer_text"] = render_text(answer)
        answer["message_id"] = uuid.uuid4().hex[:16]
        answer["question"] = message
        self._pending[answer["message_id"]] = (r, Turn(
            message, r.retrieval_query if r.intent == "question" else "", answer["status"],
            [d["doc_id"] for d in answer["documents_used"]]))
        while len(self._pending) > 200:
            self._pending.pop(next(iter(self._pending)))
        if persist:
            self.commit(answer)
        return answer

    def suggestions(self, limit: int = 4) -> list[str]:
        """Example questions built from the indexed documents, each verified to be answerable.

        A suggestion is only offered if the engine answers it from the documents,
        so a chip can never lead to a refusal.
        """
        chunks, *_ = self.retriever.index.snapshot()
        key = (len(chunks), tuple(sorted((d["id"], d["updated_at"]) for d in self.store.documents())))
        if getattr(self, "_suggest_key", None) == key:
            return self._suggest_cache[:limit]
        docs = [d for d in self.store.documents() if d["status"] in cite.SEARCHABLE and not d["superseded_by"]]
        per_doc: list[list[str]] = []
        for doc in docs:
            ids = sorted(identifiers(" ".join((doc["equipment"], doc["title"]))), key=len)
            model = ids[0] if ids else (doc["equipment"] or doc["title"])[:40]
            mine: list[str] = []
            for c in chunks:
                if c.doc_id != doc["id"]:
                    continue
                if c.kind == "table_row":
                    first = c.text.split("\n")[-1].split(" | ")[0]
                    param = first.split(": ", 1)[-1].strip()
                    if 3 <= len(param) <= 40 and not any(ch.isdigit() for ch in param):
                        mine.append("What is the {0} of the {1}?".format(param.lower(), model))
                elif c.kind == "text" and sum(bool(STEP_LINE.match(l)) for l in c.text.split("\n")) >= 3:
                    topic = re.sub(r"^[\d.\s]+", "", c.section.split(" > ")[-1]).strip().lower()
                    if topic:
                        mine.append("What are the steps for {0} on the {1}?".format(topic, model))
            per_doc.append(list(dict.fromkeys(mine)))
        ordered = [q for group in zip(*[g[:6] for g in per_doc if g]) for q in group] if per_doc else []
        ordered += [q for g in per_doc for q in g[6:12]]
        # Luma's other abilities, offered only when the library supports them (and verified below)
        extra = []
        figures = self.store.figures()
        shown = next((f for f in figures if f["caption"] and any(d["id"] == f["doc_id"] for d in docs)), None)
        if shown:
            topic = re.sub(r"^(figure|fig\.?)\s*\d+[.:]?\s*", "", shown["caption"], flags=re.I)
            topic = re.sub(r"\(.*?\)", "", topic).strip()
            if topic:
                extra.append("Show me the {0}".format(topic[0].lower() + topic[1:] if not topic[:2].isupper() else topic))
        biggest = max(docs, key=lambda d: d["page_count"], default=None)
        if biggest is not None and biggest["page_count"] >= 3:
            ids = sorted(identifiers(" ".join((biggest["equipment"], biggest["title"]))), key=len)
            extra.append("Summarize the {0} manual".format(ids[0] if ids else biggest["title"]))
        if any(c.kind == "warning" for c in chunks):
            extra.append("What are the important safety warnings?")
        mixed = []
        for i in range(max(len(ordered), len(extra))):
            if i < len(ordered):
                mixed.append(ordered[i])
            if i < len(extra):
                mixed.append(extra[i])
        ordered = mixed
        verified = []
        for question in ordered:
            if len(verified) >= 6:
                break
            # Answerability is a property of the documents; checking it never needs the model.
            out = self.ask(question, persist=False, use_generator=False)
            self._pending.pop(out["message_id"], None)
            if out["status"] == "answered" and out["citations"]:
                verified.append(question)
        self._suggest_key, self._suggest_cache = key, verified
        return verified[:limit]

    # Pipeline phases reported with each progress stage. The UI (Luma's mascot) follows these;
    # each is sent only when that step actually starts.
    PHASES = ("understanding", "searching", "reading", "analyzing", "writing", "checking")

    def _progress(self, text: str, phase: str, docs: list | None = None):
        """Report a pipeline step that has just started. ``docs`` names the documents the step opened
        (titles with revisions), so the UI can show exactly what was read."""
        callback = getattr(self._local, "progress", None)
        if callback:
            try:
                try:
                    callback(text, phase, docs or [])
                except TypeError:          # an older callback that takes only the text
                    callback(text)
            except Exception:  # a disconnected stream must not break answering
                log.debug("progress callback failed", exc_info=True)
            if self.settings.debug_stage_delay_ms > 0:
                time.sleep(self.settings.debug_stage_delay_ms / 1000)

    def commit(self, answer: dict, owner: str = ""):
        """Record a delivered answer: conversation memory, persistent history (under ``owner``, the
        browser that asked), query log."""
        pending = self._pending.pop(answer["message_id"], None)
        if pending is None:
            return
        r, turn = pending
        self.conversations.add(answer["conversation_id"], turn)
        if self.store is not None and turn.question.strip():
            self.store.save_turn(answer["conversation_id"], answer["message_id"], turn.question, answer,
                                 turn.retrieval_query, turn.status, turn.doc_ids, owner)
        if r.intent == "question":
            self._log(answer, r)

    # ── conversational turns ──
    def _base(self, status: str, message: str) -> dict:
        return {"status": status, "message": message, "claims": [], "citations": [], "conflicts": [],
                "clarification": None, "safety_notice": "", "documents_used": [], "searched": {},
                "unresolved": [], "generator": "none", "fallback_reason": "", "rejected_claims": [],
                "trace": {}, "table": None, "visual": None, "figures": [], "follow_ups": [], "mode_note": ""}

    def _ready_docs(self) -> list[dict]:
        return [d for d in self.store.documents() if d["status"] in cite.SEARCHABLE]

    def _web(self, topic: str) -> dict:
        """A request to search the internet. Luma does not browse: the page opens the search in a new tab.
        With a model, a short answer from general knowledge comes with it, labelled as such."""
        if not topic:
            return self._base("help", "Sure. What should I look up? Ask your question and I'll check your documents "
                                      "first; if they don't cover it, I'll give you a web search for it.")
        out = self._base("web", "Here's a web search for \u201c{0}\u201d.".format(topic))
        out["web_query"] = topic
        self._general(out, topic, found_nothing=False)
        out["web_query"] = topic          # asked for explicitly: keep the button prominent
        return out

    def _general(self, out: dict, question: str, found_nothing: bool):
        """Add the model's general-knowledge answer, kept apart from document claims and labelled."""
        if not self.generator or not getattr(self._local, "use_generator", True):
            return
        self._progress("Answering from general engineering knowledge with {0}".format(self.generator.model), "writing")
        try:
            text = self.generator.general(question)
        except GenerationError as exc:
            log.warning("general answer failed: %s", exc)
            return
        # shown as a plain answer; the page adds one quiet line saying it is general knowledge
        out["claims"] = [{"text": text, "kind": "general", "citations": []}]
        out["generator"] = self.generator.name
        out["status"] = "general"
        out["message"] = ""
        out.pop("web_query", None)

    def _smalltalk(self, kind: str) -> dict:
        n = len(self._ready_docs())
        library = ("{0} document{1} indexed".format(n, "" if n == 1 else "s") if n
                   else "no documents indexed yet, so upload a PDF to get started")
        # what to do next, said plainly: with an empty library, how to give Luma something to read
        nxt = ("Ask me about a specification, a procedure or a part number, or say \"summarize\" and a manual's name."
               if n else "I don't have any documents yet: put PDFs in the pdfs folder or press Upload documents, "
                         "and I'll read them within a few seconds.")
        text = {
            "greeting": "Hi, I'm Luma, Mantrac's engineering assistant. I can look up specifications, procedures "
                        "and warnings, compare equipment, summarize manuals and show figures from your technical "
                        "library ({0}). What are you working on?",
            "how_are_you": "I'm doing well, thanks for asking, and ready to help. " + nxt,
            "thanks": "You're welcome. Anything else you'd like to check?",
            "ack": "Great. Whenever you're ready, I'm here. " + nxt,
            "help": "Of course, happy to help. " + ("Tell me what you're working on, the machine and what you need "
                    "(a value, a procedure, a warning), and I'll find it in the manuals and show you the page. "
                    "For example: \"What is the rated pressure of the HX-200?\"" if n else nxt +
                    " Then ask me anything about them, for example a rated pressure, a torque value or a "
                    "maintenance procedure."),
            "identity": "I'm Luma, Mantrac's engineering assistant, an AI that reads your technical documents. I'm not "
                        "a person, but I'll answer like a colleague would, and I'll always show you the page an answer "
                        "comes from. " + nxt,
            "praise": "Thank you, that's kind of you! What else can I look up for you?",
            "sorry": "No problem at all. What would you like to check?",
            "frustrated": "Sorry, that's not the help you wanted. Let's sort it out. Ask me in a full sentence, for "
                          "example \"What is the power formula?\" or \"How do I test a relay?\". I'll look in your "
                          "documents first and show the page; if they don't cover it, I'll answer from general "
                          "engineering knowledge and give you a web search for it.",
            "bye": "Goodbye. Your library stays indexed for next time.",
            "capabilities": "I'm Luma, Mantrac's engineering assistant. I can answer questions from your technical "
                            "library ({0}): specifications, procedures and warnings, comparing equipment or "
                            "revisions, summarizing a manual and showing its figures. Every value links to the page "
                            "it comes from. If your documents don't cover something, I'll answer from general "
                            "engineering knowledge, clearly labelled, and give you a web search for it. Try: "
                            "\"What is the power formula?\" or \"Summarize the electrical basics module\".",
            "unclear": "I'm not sure what you'd like to know. Try a specification, a procedure, a part number, "
                       "or ask me to summarize a manual. There are {0}.",
        }[kind]
        return self._base("smalltalk", text.format(library))

    def _library(self) -> dict:
        docs = self.store.documents()
        if not docs:
            return self._base("library", "No documents have been uploaded yet.")
        lines = []
        for d in docs:
            rev = ", Rev {0}".format(d["revision"]) if d["revision"] else ""
            old = ", superseded" if d["superseded_by"] else ""
            lines.append("{0}{1} ({2}, {3} pages{4})".format(d["title"] or d["filename"], rev, d["status"],
                                                          d["page_count"], old))
        out = self._base("library", "The library has {0} document{1}:\n- {2}".format(
            len(docs), "" if len(docs) == 1 else "s", "\n- ".join(lines)))
        return out

    def _not_found_help(self, history: list[Turn]) -> dict:
        last = next((t for t in reversed(history) if t.retrieval_query), None)
        if last is None:
            return self._base("help", "Ask your question and I'll show which documents and pages I searched. "
                                      "Naming the equipment model, document or a specific term helps.")
        docs = self._ready_docs()
        titles = ", ".join(d["title"] or d["filename"] for d in docs[:8])
        msg = ("For your last question (\"{0}\") I searched {1} indexed document{2}: {3}. "
               "If the answer should be in one of them, try the exact term the document uses, the equipment "
               "model, or select that document in the library to search it alone. If it is in a document that "
               "isn't uploaded yet, upload it and ask again.").format(
            last.question[:120], len(docs), "" if len(docs) == 1 else "s", titles or "none")
        return self._base("help", msg)

    # ── questions ──
    def _question(self, r: Route, doc_ids: list[str] | None, history: list[Turn]) -> dict:
        docs = {d["id"]: d for d in self.store.documents()}
        if doc_ids:
            doc_ids = [d for d in doc_ids if d in docs]
        if not any(d["status"] in cite.SEARCHABLE for d in docs.values()):
            out = self._base("insufficient_evidence", "I'd love to help with that, but I don't have any documents to "
                             "look in yet. Put your PDFs in the pdfs folder (or press Upload documents) and I'll "
                             "read them within a few seconds, then ask me again.")
            return out
        # Modes that work on the library or on the previous answer rather than a fresh search.
        special = None
        stage = {"where": ("Finding where that is shown", "reading"),
                 "simplify": ("Reading the previous answer's sources", "reading"),
                 "revision_diff": ("Comparing the document revisions", "analyzing"),
                 "summary": ("Reading the document to summarize it", "reading"),
                 "visual": ("Looking for figures in your documents", "searching")}.get(r.mode)
        if stage:
            self._progress(*stage)
        if r.mode == "where":
            special = self._where(docs)
        elif r.mode == "simplify":
            special = self._simplify(r, docs)
        elif r.mode == "revision_diff":
            special = self._revision_diff(r, docs)
        elif r.mode == "summary":
            special = self._summary(r, docs, doc_ids, history)
        elif r.mode == "visual":
            special = self._visual(r, docs, doc_ids)
        if special is not None:
            special.setdefault("mode", r.mode)
            special["follow_ups"] = self._follow_ups(r, special, docs)
            return special

        top_k = self.settings.top_k * (2 if r.compare else 1)
        scope = len(doc_ids) if doc_ids else sum(d["status"] in cite.SEARCHABLE for d in docs.values())
        self._progress("Searching {0} document{1}".format(scope, "" if scope == 1 else "s"), "searching")
        result = self.retriever.search(r.retrieval_query, doc_ids or None, r.prefer_doc_ids, top_k=top_k)
        searched = {"documents": len(result.searched_doc_ids),
                    "titles": [docs[d]["title"] or docs[d]["filename"] for d in result.searched_doc_ids][:12],
                    "missing_identifiers": result.missing_identifiers,
                    "scope": "selected documents" if doc_ids else "all documents"}

        found = {c.chunk.doc_id for c in result.selected}
        self._progress("Reading {0} passage{1} from {2} document{3}".format(
            len(result.selected), "" if len(result.selected) == 1 else "s", len(found), "" if len(found) == 1 else "s")
            if result.selected else "No passage matched closely enough", "reading",
            [_doc_label(docs[d]) for d in dict.fromkeys(c.chunk.doc_id for c in result.selected) if d in docs])
        draft = self._extractive(r, result, docs)
        extra: dict = {}
        if draft.status == "answered":
            if r.compare:
                self._progress("Comparing values across documents", "analyzing")
            self._progress("Preparing your answer", "writing")
            self._compose(r, draft, docs)
            if r.compare:
                self._compose_compare(r, draft, docs, extra)
        generator_name, fallback = "extractive", ""
        use_llm = getattr(self._local, "use_generator", True)
        # The model phrases answers the evidence supports. Clarification stays deterministic (it offers
        # the equipment as options), and a model that declines never hides a validated quoted answer.
        if self.generator and use_llm and draft.status in ("answered", "conflict") and result.selected \
                and not extra.get("table"):
            self._progress("Writing the answer with {0}".format(self.generator.model), "writing")
            try:
                llm = self._llm_draft(r, result, docs, history)
                if llm is None:
                    fallback = "the model's answer had no claim that passed citation checks"
                elif llm.status != "answered" and draft.status == "answered":
                    fallback = "the model did not answer ({0}); the quoted answer is shown".format(
                        llm.status.replace("_", " "))
                else:
                    draft, generator_name = llm, self.generator.name
            except GenerationError as exc:
                fallback = str(exc)
            if fallback:
                log.warning("LLM fallback: %s", fallback)

        out = self._base(draft.status, draft.message)
        out.update(claims=draft.claims, conflicts=draft.conflicts, clarification=draft.clarification,
                   unresolved=draft.unresolved, generator=generator_name, fallback_reason=fallback,
                   searched=searched, trace=result.trace(), rejected_claims=draft.rejected, mode=r.mode, **extra)
        self._progress("Checking every citation against its page", "checking")
        self._finalise_citations_general(out, docs)
        if out["status"] == "insufficient_evidence":
            out["message"] = self._insufficient_message(r, result, docs, searched)
            if r.mode == "visual" and getattr(self._local, "visual_note", ""):
                out["message"] = ("I couldn't find a figure of that in your documents, and their text doesn't cover "
                                  "it either. Do you have the manual that includes it? Upload it and I'll take a look.")
                self._local.visual_note = ""
            out["claims"], out["citations"], out["table"], out["visual"] = [], [], None, None
        out["safety_notice"] = self._safety_notice(r.question, out)
        if out["status"] == "answered" and r.mode == "procedure":
            out["visual"] = self._procedure_flow(out)
        note = getattr(self._local, "visual_note", "")
        self._local.visual_note = ""
        if note and r.mode == "visual":
            out["mode_note"] = note
        if r.mode == "explain" and generator_name == "extractive" and out["status"] == "answered":
            out["mode_note"] = ("This is the source text itself. A plain-language explanation needs the local "
                                "language model, which isn't turned on.")
        out["follow_ups"] = self._follow_ups(r, out, docs)
        return out

    @staticmethod
    def _procedure_flow(out: dict) -> dict | None:
        """A conceptual step flow for a cited procedure (only when it has at least three steps)."""
        for claim in out["claims"]:
            steps = [l.strip() for l in claim["text"].split("\n") if STEP_LINE.match(l) and not l.strip().startswith("(")]
            if len(steps) >= 3:
                return {"kind": "flow", "label": "Conceptual flow of the cited steps, not a manufacturer drawing. "
                        "The full wording, warnings and values are in the steps above.",
                        "steps": [re.sub(r"^\d{1,2}[.)]\s*", "", s_) for s_ in steps], "citations": claim["citations"]}
        return None

    # ── extractive evidence selection ──
    def _units(self, r: Route, cands: list[Candidate]) -> list[Unit]:
        units = []
        for cand in cands:
            ch = cand.chunk
            if ch.kind == "table_row":
                lines = ch.text.split("\n")
                units.append(Unit(cand, lines[-1], " ".join(lines[:-1]) + " " + ch.section))
            elif ch.kind == "warning" or r.troubleshoot or (
                    r.procedure and sum(bool(STEP_LINE.match(l)) for l in ch.text.split("\n")) >= 2):
                # Warnings, step lists and symptom/cause/remedy blocks are only useful whole.
                units.append(Unit(cand, ch.text, ch.section))
            else:
                units.extend(Unit(cand, s, ch.section) for s in sentences(ch.text))
        return units

    def _score(self, units: list[Unit], query: str, value_q: bool, own_terms: set[str] | None = None):
        terms = content_terms(query)
        weights = {t: self.retriever.index.idf(t) for t in terms}
        if own_terms:  # follow-up: words carried from the earlier question count half
            weights = {t: w if t in own_terms else 0.5 * w for t, w in weights.items()}
        total = sum(weights.values()) or 1.0
        for u in units:
            own = set(tokens(u.text))
            own_compact = compact(u.text)
            ctx = set(tokens(u.context + " " + u.cand.chunk.search_text.split("\n")[0]))
            score = 0.0
            for t in terms:
                if t in own or (any(c.isdigit() for c in t) and compact(t) in own_compact):
                    score += weights[t]
                elif t in ctx:
                    score += 0.5 * weights[t]
            u.score = score / total
            if value_q and not (numbers(u.text) or identifiers(u.text)):
                u.score *= 0.6

    def _extractive(self, r: Route, result, docs: dict) -> Draft:
        if not result.selected:
            return Draft(status="insufficient_evidence")
        value_q = bool(VALUE_QUESTION.search(r.question)) and not r.procedure
        units = self._units(r, result.selected)
        self._score(units, r.retrieval_query, value_q, set(content_terms(r.question)) if r.follow_up else None)
        # Lexical gates. Strong semantic similarity lowers the bar (it bridges wording such as
        # "hold" vs "capacity") but never removes it: similar-sounding passages that share none
        # of the question's specific words are not evidence.
        strong = self.retriever.min_semantic
        topic = self._topic_terms(r)

        def passes(u: Unit) -> bool:
            coverage = self._topic_coverage(u, topic) if topic else 1.0
            if u.cand.cosine >= strong:
                return coverage >= MIN_TOPIC_COVERAGE_SEMANTIC
            return u.score >= MIN_EVIDENCE and coverage >= MIN_TOPIC_COVERAGE

        units = [u for u in units if passes(u)]
        if not units:
            return Draft(status="insufficient_evidence")
        best = max(u.score for u in units)
        keep = [u for u in sorted(units, key=lambda u: -u.score) if u.score >= best * (0.8 if value_q else 0.7)]

        # One unit per identical text (the same sentence in two revisions counts once).
        unique: dict[str, Unit] = {}
        for u in keep:
            key = compact(u.text)
            current = unique.get(key)
            if current is None or (docs[current.cand.chunk.doc_id]["superseded_by"]
                                   and not docs[u.cand.chunk.doc_id]["superseded_by"]):
                unique[key] = u
        keep = list(unique.values())

        if r.procedure:
            keep = self._procedure_units(keep, docs)
        draft = Draft()
        keep = self._resolve_revisions(keep, docs, draft)
        if not r.compare:
            keep = self._resolve_equipment(r, keep, docs, draft)
            if draft.status != "answered":
                return draft
        keep = self._diversify(keep, r.compare)[: MAX_CLAIMS * (2 if r.compare or r.procedure else 1)]
        for u in keep:
            draft.claims.append(self._claim(u, "direct"))
        self._attach_warnings(draft, keep, docs)
        doc_titles = sorted({docs[u.cand.chunk.doc_id]["title"] or docs[u.cand.chunk.doc_id]["filename"] for u in keep})
        if r.compare and len(doc_titles) > 1:
            draft.message = "Here is what each document states, side by side:"
        elif r.compare:
            used_doc = docs[keep[0].cand.chunk.doc_id]
            own = {compact(i) for i in identifiers(" ".join((used_doc["title"], used_doc["equipment"])))}
            others = [i for i in identifiers(r.question) if compact(i) not in own]
            draft.message = ("I could only find this for the {0}{1}, so there's nothing to compare it with yet:".format(
                short_equipment(used_doc), "; the {0} documents don't state it".format(" or ".join(others)) if others else ""))
        elif r.procedure:
            draft.message = "The documented procedure:"
        else:
            draft.message = ""
        if result.missing_identifiers:
            draft.unresolved.append("No searched document mentions {0}.".format(", ".join(result.missing_identifiers)))
        used = list(dict.fromkeys(u.cand.chunk.doc_id for u in keep))
        if len(used) == 1 and not r.compare and not self._names_equipment(r, docs[used[0]]):
            others = {compact(d["equipment"] or d["title"]) for d in docs.values()
                      if d["status"] in cite.SEARCHABLE and d["id"] not in used and not d["superseded_by"]}
            others.discard(compact(docs[used[0]]["equipment"] or docs[used[0]]["title"]))
            if others:
                draft.unresolved.append(
                    "The question doesn't name the equipment; this answer comes only from {0}. Other equipment in "
                    "the library may differ, so name the model if you meant another one.".format(
                        docs[used[0]]["equipment"] or docs[used[0]]["title"]))
        return draft

    @staticmethod
    def _names_equipment(r: Route, doc: dict) -> bool:
        text = " ".join((doc["title"], doc["equipment"], doc["doc_number"]))
        named = {compact(i) for i in identifiers(text)} | {t for t in content_terms(text) if len(t) > 3}
        asked = {compact(i) for i in identifiers(r.retrieval_query)} | set(content_terms(r.retrieval_query))
        generic = set(tokens("manual service datasheet checklist document daily inspection"))
        return bool((named - generic) & asked)

    @staticmethod
    def _topic_terms(r: Route) -> list[str]:
        """What the question asks about, without the identifiers that only select a document.

        A follow-up's own words are its topic ("what about its dry weight" -> dry,
        weight); a reply to a clarification, or a bare "and for the CV-90?", keeps
        the earlier question's topic.
        """
        def plain(text):
            return [t for t in content_terms(text) if not any(ch.isdigit() for ch in t) and t not in FRAME_WORDS]
        own = plain(r.question)
        if r.follow_up and (r.clarification_reply or r.differences_only or not own):
            return plain(r.retrieval_query)
        return own

    def _topic_coverage(self, u: Unit, topic: list[str]) -> float:
        """Weighted share of the topic terms present in the evidence text itself (not its headings)."""
        weights = {t: self.retriever.index.idf(t) for t in topic}
        own = set(tokens(u.text))
        context = set(tokens(u.context + " " + u.cand.chunk.search_text.split("\n")[0]))
        got = sum(w if t in own else 0.5 * w if t in context else 0.0 for t, w in weights.items())
        return got / (sum(weights.values()) or 1.0)

    def _claim(self, u: Unit, kind: str) -> dict:
        return {"text": u.text, "kind": kind, "_chunk": u.cand.chunk, "citations": []}

    def _diversify(self, units: list[Unit], compare: bool) -> list[Unit]:
        if not compare:
            return units
        by_doc: dict[str, list[Unit]] = {}
        for u in units:
            by_doc.setdefault(u.cand.chunk.doc_id, []).append(u)
        ordered = []
        while any(by_doc.values()):
            for doc_units in by_doc.values():
                if doc_units:
                    ordered.append(doc_units.pop(0))
        return ordered

    def _procedure_units(self, units: list[Unit], docs: dict) -> list[Unit]:
        """Expand to the whole documented section of the best match, in document order."""
        best = units[0]
        doc_id, section = best.cand.chunk.doc_id, best.cand.chunk.section
        chunks, *_ = self.retriever.index.snapshot()
        section_chunks = [c for c in chunks if c.doc_id == doc_id and c.section == section and c.kind == "text"]
        out = []
        for c in sorted(section_chunks, key=lambda c: c.seq):
            out.append(Unit(Candidate(c, decision="section context"), c.text, c.section, best.score))
        return out or units

    def _resolve_revisions(self, units: list[Unit], docs: dict, draft: Draft) -> list[Unit]:
        """Prefer the current revision; report where an older revision says something different."""
        present = {u.cand.chunk.doc_id for u in units}
        keep = []
        for u in units:
            doc = docs[u.cand.chunk.doc_id]
            newer = doc["superseded_by"]
            if not newer:
                keep.append(u)
                continue
            if newer in present:
                current_numbers = set().union(*(numbers(x.text) for x in units if x.cand.chunk.doc_id == newer))
                if numbers(u.text) and not numbers(u.text) <= current_numbers:
                    summary = ("An older revision ({0} Rev {1}, superseded by Rev {2}) gives different values. "
                               "The current revision is used above.").format(
                        doc["title"], doc["revision"] or "?", docs[newer]["revision"] or "?")
                    note = next((c for c in draft.conflicts if c["summary"] == summary), None)
                    if note is None:
                        note = {"kind": "revision", "summary": summary, "claims": []}
                        draft.conflicts.append(note)
                    note["claims"].append(self._claim(u, "superseded"))
                continue  # same content as the current revision: cite the current one only
            draft.unresolved.append("{0} Rev {1} is superseded by Rev {2}, which does not repeat this; "
                                    "confirm it is still current.".format(doc["title"], doc["revision"] or "?",
                                                                          docs[newer]["revision"] or "?"))
            keep.append(u)
        return keep

    def _resolve_equipment(self, r: Route, units: list[Unit], docs: dict, draft: Draft) -> list[Unit]:
        doc_ids = list(dict.fromkeys(u.cand.chunk.doc_id for u in units))
        if len(doc_ids) < 2:
            return units
        query_terms = set(content_terms(r.retrieval_query)) | {compact(i) for i in identifiers(r.retrieval_query)}

        def doc_terms(d):
            text = " ".join((d["title"], d["equipment"], d["doc_number"]))
            return set(content_terms(text)) | {compact(i) for i in identifiers(text)}

        terms = {d: doc_terms(docs[d]) for d in doc_ids}
        specific = []
        for d in doc_ids:
            others = set().union(*(terms[o] for o in doc_ids if o != d))
            if query_terms & (terms[d] - others):
                specific.append(d)
        if specific:
            return [u for u in units if u.cand.chunk.doc_id in specific]
        values = {d: frozenset().union(*(numbers(u.text) for u in units if u.cand.chunk.doc_id == d)) for d in doc_ids}
        if len({v for v in values.values() if v}) <= 1:
            return units  # the documents agree (or state no values): answer from all of them
        equipment = {d: (docs[d]["equipment"] or docs[d]["title"] or docs[d]["filename"]) for d in doc_ids}
        same_equipment = len({compact(e) for e in equipment.values()}) == 1
        if same_equipment:
            draft.claims = [self._claim(u, "direct") for u in units[: MAX_CLAIMS * 2]]
            draft.status = "conflict"
            draft.message = ("The documents disagree and none of them is marked as superseding the other. "
                             "Each value is shown with its source; check which document is authoritative.")
            draft.conflicts.append({"kind": "source", "summary": "Different values for the same equipment in "
                                    "{0} documents.".format(len(doc_ids)), "claims": []})
        else:
            # Ask first: no values are shown until the equipment is chosen, so a
            # reader cannot pick up the wrong model's number by accident.
            draft.status = "needs_clarification"
            draft.message = ("The documents give different values for different equipment, and the question "
                             "doesn't say which one you mean.")
            draft.clarification = {
                "question": "Which equipment do you mean?",
                "options": [{"label": equipment[d], "doc_id": d,
                             "title": docs[d]["title"] or docs[d]["filename"]} for d in doc_ids]}
        return units

    def _attach_warnings(self, draft: Draft, units: list[Unit], docs: dict):
        """Safety warnings printed in the same section as the evidence travel with the answer."""
        chunks, *_ = self.retriever.index.snapshot()
        sections = {(u.cand.chunk.doc_id, u.cand.chunk.section) for u in units}
        have = {compact(c["text"]) for c in draft.claims}
        added = 0
        for c in chunks:
            if c.kind == "warning" and (c.doc_id, c.section) in sections and compact(c.text) not in have and added < 3:
                draft.claims.append({"text": c.text, "kind": "warning", "_chunk": c, "citations": []})
                have.add(compact(c.text))
                added += 1

    # ── LLM path ──
    def _llm_draft(self, r: Route, result, docs: dict, history: list[Turn]) -> Draft | None:
        sources = [("S{0}".format(i + 1), c.chunk, docs[c.chunk.doc_id]) for i, c in enumerate(result.selected)]
        context = history[-1].question if r.follow_up and history else ""
        data = self.generator.generate(r.question, sources, context, mode=r.mode)
        return self._llm_claims(data, sources, docs)

    def _llm_claims(self, data: dict, sources: list, docs: dict) -> Draft | None:
        """Turn model JSON into claims; anything not grounded in the passages it cites is dropped."""
        by_id = {sid: chunk for sid, chunk, _ in sources}
        draft = Draft(status=data["status"])
        rejected = []
        for claim in data["claims"]:
            text = str(claim.get("text", "")).strip()
            if claim.get("kind") == "general":
                # general knowledge may explain a concept but never carry an equipment-specific value
                bad = sorted(numbers(text)) + sorted(identifiers(text))
                if bad:
                    rejected.append({"text": text, "reason": "general claim states specific values: " + ", ".join(bad)})
                else:
                    draft.claims.append({"text": text, "kind": "general", "_chunk": None, "citations": []})
                continue
            sids = [s_ for s_ in claim.get("sources", []) if s_ in by_id]
            if not sids:
                rejected.append({"text": text, "reason": "cites no retrieved passage"})
                continue
            bad = cite.unsupported_values(text, [by_id[s_].search_text for s_ in sids])
            if bad:
                rejected.append({"text": text, "reason": "values not in cited passages: " + ", ".join(bad)})
                continue
            kind = "inference" if claim.get("kind") == "inference" else "direct"
            draft.claims.append({"text": text, "kind": kind, "_chunk": by_id[sids[0]],
                                 "_extra": [by_id[s_] for s_ in sids[1:]], "citations": []})
        draft.rejected = rejected
        if draft.status == "needs_clarification":
            draft.message = "That depends on which equipment or revision you mean."
            draft.claims = []
            draft.clarification = {"question": str(data.get("clarifying_question") or
                                                   "Which equipment or document revision do you mean?"), "options": []}
            return draft
        if draft.status == "insufficient_evidence":
            return Draft(status="insufficient_evidence")
        if not any(c["kind"] != "general" for c in draft.claims):
            if rejected:
                log.warning("all %d model claims failed validation", len(rejected))
            return None
        # general claims follow the grounded ones, never lead the answer
        draft.claims = [c for c in draft.claims if c["kind"] != "general"] + \
                       [c for c in draft.claims if c["kind"] == "general"]
        draft.message = ""
        draft.unresolved = [str(u) for u in data.get("unresolved") or [] if str(u).strip()][:5]
        units = [Unit(Candidate(c["_chunk"]), c["text"], "") for c in draft.claims if c["_chunk"] is not None]
        self._attach_warnings(draft, units, docs)
        superseded = {c["_chunk"].doc_id for c in draft.claims
                      if c["_chunk"] is not None and docs[c["_chunk"].doc_id]["superseded_by"]}
        for d in superseded:
            draft.unresolved.append("{0} Rev {1} is superseded; check the current revision.".format(
                docs[d]["title"], docs[d]["revision"] or "?"))
        return draft

    def _llm_rewrite(self, r: Route, previous: dict, chunks: list, docs: dict) -> dict:
        """'Explain that in simpler terms': the model rewords the previous answer from its own sources."""
        sources = [("S{0}".format(i + 1), c, docs[c.doc_id]) for i, c in enumerate(chunks) if c.doc_id in docs]
        self._progress("Rewording with {0}".format(self.generator.model), "writing")
        try:
            data = self.generator.generate(r.question, sources, "Previous answer: " + previous.get("answer_text", ""),
                                           mode="simplify")
            draft = self._llm_claims(data, sources, docs)
        except GenerationError as exc:
            return self._base("help", "I couldn't reach the language model to reword that ({0}). The answer above "
                              "is quoted exactly from the source.".format(exc))
        if draft is None or draft.status != "answered":
            return self._base("help", "I couldn't produce a simpler version that stays faithful to the source, so "
                              "I'd rather not paraphrase it. The answer above is quoted exactly.")
        out = self._base("answered", "")
        out.update(claims=draft.claims, unresolved=draft.unresolved, generator=self.generator.name,
                   rejected_claims=draft.rejected, mode="simplify")
        self._finalise_citations_general(out, docs)
        out["safety_notice"] = self._safety_notice(r.question, out)
        return out

    def _finalise_citations_general(self, out: dict, docs: dict):
        general = [c for c in out["claims"] if c["kind"] == "general"]
        out["claims"] = [c for c in out["claims"] if c["kind"] != "general"]
        self._finalise_citations(out, docs)
        out["claims"] += [{**{k: v for k, v in c.items() if not k.startswith("_")}, "citations": []} for c in general]

    # ── citations, validation, messages ──
    def _finalise_citations(self, out: dict, docs: dict):
        """Number citations, validate them against the store, drop claims and rows left without support."""
        registry: dict[str, dict] = {}

        def register(chunk, quote):
            for existing in registry.values():
                if existing["chunk_id"] == chunk.id:
                    return existing["id"]
            cid = "S{0}".format(len(registry) + 1)
            registry[cid] = cite.build(cid, chunk, docs[chunk.doc_id],
                                       quote if quote and cite.quote_in_source(quote, chunk.text) else None)
            return cid

        groups = [out["claims"]] + [c["claims"] for c in out["conflicts"]]
        for claims in groups:
            for claim in claims:
                chunks = [claim.pop("_chunk")] + claim.pop("_extra", [])
                describe_source(claim, chunks[0])
                quote = None
                if claim["kind"] not in ("inference", "general", "location") and not claim.get("composed"):
                    quote = claim.get("quote") or claim["text"]
                claim["citations"] = [register(ch, quote) for ch in chunks]
        table = out.get("table") or {}
        for row in table.get("rows", []):
            chunks = row.pop("_chunks", None) or [row.pop("_chunk")]
            row["citations"] = [register(ch, None) for ch in chunks]
        for figure in out.get("figures") or []:
            chunk = figure.pop("_chunk", None)
            figure["citations"] = [register(chunk, None)] if chunk is not None else []
        valid, problems = cite.validate(list(registry.values()), self.store)
        ok = {c["id"] for c in valid}
        for claims in groups:
            claims[:] = [dict(c, citations=[x for x in c["citations"] if x in ok]) for c in claims]
            claims[:] = [c for c in claims if c["citations"] or c["kind"] in ("inference", "general")]
        if table:
            table["rows"] = [dict(r_, citations=[x for x in r_["citations"] if x in ok]) for r_ in table["rows"]]
            table["rows"] = [r_ for r_ in table["rows"] if r_["citations"]]
            if not table["rows"]:
                out["table"] = None
        if out.get("figures"):
            out["figures"] = [dict(f, citations=[x for x in f["citations"] if x in ok]) for f in out["figures"]]
            out["figures"] = [f for f in out["figures"] if f["citations"]]
        out["citations"] = valid
        out["rejected_claims"] = out["rejected_claims"] + [{"reason": p} for p in problems]
        substantive = any(c["kind"] in ("direct", "inference", "outline", "location") for c in out["claims"]) \
            or bool(out.get("table")) or bool(out.get("figures")) \
            or (out.get("mode") == "summary" and any(c["kind"] == "warning" for c in out["claims"]))
        if out["status"] in ("answered", "conflict") and not substantive:
            out["status"] = "insufficient_evidence"
        used: dict[str, dict] = {}
        for c in valid:
            d = used.setdefault(c["doc_id"], {"doc_id": c["doc_id"], "title": c["title"], "revision": c["revision"],
                                              "superseded": c["superseded"], "pages": []})
            if c["page"] not in d["pages"]:
                d["pages"].append(c["page"])
        for d in used.values():
            d["pages"].sort()
        out["documents_used"] = list(used.values())

    def _insufficient_message(self, r: Route, result, docs: dict, searched: dict) -> str:
        topic = question_topic(r.question)
        parts = ["I couldn't verify {0} from the available technical information.".format(topic) if topic
                 else "I couldn't find that in the available technical information."]
        if result.missing_identifiers:
            parts.append("None of your documents mention {0}.".format(", ".join(result.missing_identifiers)))
        parts.append("I searched {0} document{1}{2}.".format(
            searched["documents"], "" if searched["documents"] == 1 else "s",
            " (" + ", ".join(searched["titles"][:6]) + ")" if searched["titles"] else ""))
        near = [c for c in result.rejected + result.selected if c.chunk.doc_id in docs][:2]
        if near and not result.missing_identifiers:
            parts.append("The closest passages ({0}) don't cover it.".format("; ".join(
                "{0} p. {1}".format(docs[c.chunk.doc_id]["title"] or docs[c.chunk.doc_id]["filename"], c.chunk.page_no)
                for c in near)))
        parts.append("Do you have the manual or table that covers it? Upload it and I'll take a look.")
        return " ".join(parts)

    def _safety_notice(self, question: str, out: dict) -> str:
        if out["status"] in ("insufficient_evidence", "smalltalk") or not (out["claims"] or out.get("table")):
            return ""   # nothing was stated, so there is nothing to verify yet
        notes = []
        text = question + " " + " ".join(c["text"] for c in out["claims"]) + " " + " ".join(
            " ".join(r_["cells"]) for r_ in (out.get("table") or {}).get("rows", []))
        if SAFETY_TERMS.search(text) or any(c["kind"] == "warning" for c in out["claims"]):
            notes.append("Safety-relevant: confirm against the cited page of the controlled document and with "
                         "the responsible engineer before acting.")
        if any(c.get("ocr") for c in out["citations"]):
            notes.append("Some passages were read by OCR from a scanned page; check them against the page image.")
        if any(c.get("superseded") for c in out["citations"]):
            notes.append("A cited document is an older, superseded revision.")
        return " ".join(notes)

    def _log(self, answer: dict, r: Route):
        record = {"ts": round(time.time(), 3), "conversation": answer["conversation_id"], "status": answer["status"],
                  "follow_up": r.follow_up, "latency_ms": answer["latency_ms"], "generator": answer["generator"],
                  "fallback": answer["fallback_reason"][:200], "query_chars": len(r.question),
                  "retrieval": answer["trace"]}
        if self.settings.log_query_text:
            record["query"] = r.question[:500]
        try:
            with open(self.settings.log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.warning("could not write query log: %s", exc)



def _doc_label(doc: dict) -> str:
    """A document as the UI names it: its title (or file name) and revision."""
    name = doc.get("title") or doc.get("filename") or "Document"
    return name + (" Rev " + doc["revision"] if doc.get("revision") else "")


def describe_source(claim: dict, chunk) -> None:
    """Tell the UI what kind of passage a claim quotes, with table cells for spec rows."""
    claim["source_kind"] = chunk.kind
    if chunk.kind == "table_row":
        lines = chunk.text.split("\n")
        if (claim.get("quote") or claim["text"]).strip() == lines[-1].strip():
            claim["caption"] = lines[0] if len(lines) > 1 else ""
            claim["cells"] = [cell.split(": ", 1) if ": " in cell else ["", cell] for cell in lines[-1].split(" | ")]


def render_text(answer: dict) -> str:
    """Plain-text rendering with [S#] markers, for the API and the evaluation report."""
    lines = [answer["message"]] if answer["message"] else []
    for c in answer["claims"]:
        label = {"warning": "Safety: ", "inference": "Inference (not stated directly): ",
                 "general": "General engineering knowledge (not from your documents): ",
                 "superseded": "Older revision: "}.get(c["kind"], "")
        if c.get("heading"):
            label = c["heading"] + ": "
        marks = "".join("[{0}]".format(x) for x in c["citations"])
        lines.append("- {0}{1} {2}".format(label, c["text"].replace("\n", "; "), marks).rstrip())
    table = answer.get("table") or {}
    if table.get("rows"):
        lines.append(" | ".join(table["columns"]))
        for row in table["rows"]:
            lines.append(" | ".join(row["cells"]) + " " + "".join("[{0}]".format(x) for x in row["citations"]))
    for figure in answer.get("figures") or []:
        lines.append("Figure: {0} {1}".format(figure["caption"], "".join("[{0}]".format(x) for x in figure["citations"])))
    for conflict in answer["conflicts"]:
        lines.append("Note: " + conflict["summary"])
        for c in conflict["claims"]:
            lines.append("  - {0} {1}".format(c["text"], "".join("[{0}]".format(x) for x in c["citations"])))
    if answer.get("clarification"):
        options = [o["label"] for o in answer["clarification"].get("options", [])]
        lines.append(answer["clarification"]["question"] + (" " + " / ".join(options) if options else ""))
    for u in answer["unresolved"]:
        lines.append("Unresolved: " + u)
    if answer["citations"]:
        lines.append("Sources: " + "; ".join(
            "[{0}] {1}{2}, p. {3}".format(c["id"], c["title"], " Rev " + c["revision"] if c["revision"] else "",
                                          c["page"]) for c in answer["citations"]))
    if answer["safety_notice"]:
        lines.append(answer["safety_notice"])
    return "\n".join(lines)
