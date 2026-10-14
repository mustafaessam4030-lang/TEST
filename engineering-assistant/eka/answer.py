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
import time
from dataclasses import dataclass, field

from . import citations as cite
from .config import Settings
from .conversation import Conversations, Route, Turn, route
from .generation import GenerationError, OllamaGenerator
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
    "allowed permitted use used using value values kind type mean means called", keep_stopwords=True))
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


class AnswerEngine:
    def __init__(self, settings: Settings, store: Store, retriever: Retriever,
                 conversations: Conversations, generator: OllamaGenerator | None = None):
        self.settings = settings
        self.store = store
        self.retriever = retriever
        self.conversations = conversations
        self.generator = generator

    # ── entry point ──
    def ask(self, message: str, conversation_id: str | None = None, doc_ids: list[str] | None = None) -> dict:
        started = time.perf_counter()
        conversation_id = conversation_id or self.conversations.new_id()
        history = self.conversations.history(conversation_id)
        r = route(message, history)
        try:
            if r.intent == "smalltalk":
                answer = self._smalltalk(r.smalltalk_kind)
            elif r.intent == "library":
                answer = self._library()
            elif r.intent == "not_found_help":
                answer = self._not_found_help(history)
            else:
                answer = self._question(r, doc_ids, history)
        except Exception as exc:  # never return an empty answer; say what failed
            log.exception("answering failed")
            answer = self._base("error", "Something went wrong while answering ({0}). "
                                "The documents were not changed; please try again.".format(type(exc).__name__))
        answer["conversation_id"] = conversation_id
        answer["intent"] = r.intent
        answer["latency_ms"] = int((time.perf_counter() - started) * 1000)
        if not answer["message"].strip() and not answer["claims"]:
            answer["status"] = "error"
            answer["message"] = "No answer could be produced. Please rephrase the question."
        answer["answer_text"] = render_text(answer)
        self.conversations.add(conversation_id, Turn(
            message, r.retrieval_query if r.intent == "question" else "", answer["status"],
            [d["doc_id"] for d in answer["documents_used"]], ))
        if r.intent == "question":
            self._log(answer, r)
        return answer

    # ── conversational turns ──
    def _base(self, status: str, message: str) -> dict:
        return {"status": status, "message": message, "claims": [], "citations": [], "conflicts": [],
                "clarification": None, "safety_notice": "", "documents_used": [], "searched": {},
                "unresolved": [], "generator": "none", "fallback_reason": "", "rejected_claims": [],
                "trace": {}}

    def _ready_docs(self) -> list[dict]:
        return [d for d in self.store.documents() if d["status"] in cite.SEARCHABLE]

    def _smalltalk(self, kind: str) -> dict:
        n = len(self._ready_docs())
        library = ("{0} document{1} indexed".format(n, "" if n == 1 else "s") if n
                   else "no documents indexed yet, so upload a PDF to get started")
        text = {
            "greeting": "Hello! I'm doing well, thanks for asking. I answer questions about your engineering "
                        "PDFs and cite the page each answer comes from. There are {0}. What would you like to look up?",
            "thanks": "You're welcome. Ask me anything else about the documents.",
            "bye": "Goodbye. Your documents stay indexed for next time.",
            "capabilities": "I answer questions from the engineering PDFs in your library (there are {0}). "
                            "Every fact I give cites the document and page it comes from, and you can open that "
                            "page in the viewer. If the documents don't say something, I will tell you rather than guess.",
            "unclear": "I'm not sure what you'd like to know. Ask a question about the documents, for example a "
                       "specification, a procedure or a part number. There are {0}.",
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
            out = self._base("insufficient_evidence", "There are no indexed documents to search yet. "
                             "Upload a PDF and wait until it shows Ready, then ask again.")
            return out
        top_k = self.settings.top_k * (2 if r.compare else 1)
        result = self.retriever.search(r.retrieval_query, doc_ids or None, r.prefer_doc_ids, top_k=top_k)
        searched = {"documents": len(result.searched_doc_ids),
                    "titles": [docs[d]["title"] or docs[d]["filename"] for d in result.searched_doc_ids][:12],
                    "missing_identifiers": result.missing_identifiers,
                    "scope": "selected documents" if doc_ids else "all documents"}

        draft = self._extractive(r, result, docs)
        generator_name, fallback = "extractive", ""
        if self.generator and draft.status != "insufficient_evidence" and result.selected:
            try:
                llm = self._llm_draft(r, result, docs, history)
                if llm is not None:
                    draft, generator_name = llm, self.generator.name
                else:
                    fallback = "the model's answer had no claim that passed citation checks"
            except GenerationError as exc:
                fallback = str(exc)
            if fallback:
                log.warning("LLM fallback: %s", fallback)

        out = self._base(draft.status, draft.message)
        out.update(claims=draft.claims, conflicts=draft.conflicts, clarification=draft.clarification,
                   unresolved=draft.unresolved, generator=generator_name, fallback_reason=fallback,
                   searched=searched, trace=result.trace(), rejected_claims=draft.rejected)
        self._finalise_citations(out, docs)
        if out["status"] == "insufficient_evidence":
            out["message"] = self._insufficient_message(result, docs, searched)
            out["claims"], out["citations"] = [], []
        out["safety_notice"] = self._safety_notice(r.question, out)
        return out

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
            draft.message = ("I found relevant passages in only one document ({0}), so there is nothing to "
                             "compare it against. What it states:").format(doc_titles[0])
            draft.unresolved.append("Only one document matched; a comparison needs a second source.")
        elif r.procedure:
            draft.message = "The documented procedure:"
        else:
            draft.message = "According to the documents:"
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
        if r.follow_up and (r.clarification_reply or not own):
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
        draft.claims = [self._claim(u, "direct") for u in units[: MAX_CLAIMS * 2]]
        if same_equipment:
            draft.status = "conflict"
            draft.message = ("The documents disagree and none of them is marked as superseding the other. "
                             "Each value is shown with its source; check which document is authoritative.")
            draft.conflicts.append({"kind": "source", "summary": "Different values for the same equipment in "
                                    "{0} documents.".format(len(doc_ids)), "claims": []})
        else:
            draft.status = "needs_clarification"
            options = [equipment[d] for d in doc_ids]
            draft.message = ("The answer depends on the equipment, and the question doesn't say which. "
                             "Here is what each document states:")
            draft.clarification = {"question": "Which equipment do you mean: {0}?".format(" or ".join(options)),
                                   "options": options}
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
        data = self.generator.generate(r.question, sources, context)
        by_id = {sid: chunk for sid, chunk, _ in sources}
        draft = Draft(status=data["status"])
        rejected = []
        for claim in data["claims"]:
            text = str(claim.get("text", "")).strip()
            sids = [s for s in claim.get("sources", []) if s in by_id]
            if not sids:
                rejected.append({"text": text, "reason": "cites no retrieved passage"})
                continue
            bad = cite.unsupported_values(text, [by_id[s].search_text for s in sids])
            if bad:
                rejected.append({"text": text, "reason": "values not in cited passages: " + ", ".join(bad)})
                continue
            kind = "inference" if claim.get("kind") == "inference" else "direct"
            for i, sid in enumerate(sids):
                entry = {"text": text if i == 0 else "", "kind": kind, "_chunk": by_id[sid], "citations": []}
                if i == 0:
                    draft.claims.append(entry)
                else:
                    draft.claims[-1].setdefault("_extra", []).append(by_id[sid])
        draft.rejected = rejected
        if draft.status == "needs_clarification":
            draft.message = "The answer depends on details the question doesn't give."
            draft.clarification = {"question": str(data.get("clarifying_question") or
                                                   "Which equipment or document revision do you mean?"), "options": []}
            return draft
        if draft.status == "insufficient_evidence":
            return Draft(status="insufficient_evidence")
        if not draft.claims:
            if rejected:
                log.warning("all %d model claims failed validation", len(rejected))
            return None
        draft.message = "According to the documents:"
        draft.unresolved = [str(u) for u in data.get("unresolved") or [] if str(u).strip()][:5]
        units = [Unit(Candidate(c["_chunk"]), c["text"], "") for c in draft.claims]
        self._attach_warnings(draft, units, docs)
        superseded = {c["_chunk"].doc_id for c in draft.claims if docs[c["_chunk"].doc_id]["superseded_by"]}
        for d in superseded:
            draft.unresolved.append("{0} Rev {1} is superseded; check the current revision.".format(
                docs[d]["title"], docs[d]["revision"] or "?"))
        return draft

    # ── citations, validation, messages ──
    def _finalise_citations(self, out: dict, docs: dict):
        """Number citations, validate them against the store, drop claims left without support."""
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
                claim["citations"] = [register(ch, claim["text"] if claim["kind"] != "inference" else None)
                                      for ch in chunks]
        valid, problems = cite.validate(list(registry.values()), self.store)
        ok = {c["id"] for c in valid}
        for claims in groups:
            claims[:] = [dict(c, citations=[x for x in c["citations"] if x in ok]) for c in claims]
            claims[:] = [c for c in claims if c["citations"] or c["kind"] == "inference"]
        out["citations"] = valid
        out["rejected_claims"] = out["rejected_claims"] + [{"reason": p} for p in problems]
        if out["status"] in ("answered", "conflict", "needs_clarification") and not any(
                c["kind"] in ("direct", "inference") for c in out["claims"]):
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

    def _insufficient_message(self, result, docs: dict, searched: dict) -> str:
        parts = ["The indexed documents don't establish this."]
        if result.missing_identifiers:
            parts.append("No searched document mentions {0}.".format(", ".join(result.missing_identifiers)))
        parts.append("I searched {0} document{1}{2}.".format(
            searched["documents"], "" if searched["documents"] == 1 else "s",
            " (" + ", ".join(searched["titles"][:6]) + ")" if searched["titles"] else ""))
        near = [c for c in result.rejected + result.selected if c.chunk.doc_id in docs][:2]
        if near and not result.missing_identifiers:
            parts.append("The closest passages ({0}) don't answer the question directly.".format("; ".join(
                "{0} p. {1}".format(docs[c.chunk.doc_id]["title"] or docs[c.chunk.doc_id]["filename"], c.chunk.page_no)
                for c in near)))
        parts.append("Try the exact term or model the document uses, or upload the document that covers it.")
        return " ".join(parts)

    def _safety_notice(self, question: str, out: dict) -> str:
        if out["status"] in ("insufficient_evidence", "smalltalk"):
            return ""
        notes = []
        text = question + " " + " ".join(c["text"] for c in out["claims"])
        if SAFETY_TERMS.search(text) or any(c["kind"] == "warning" for c in out["claims"]):
            notes.append("Safety-relevant: confirm values against the cited page of the controlled document "
                         "and with the responsible engineer before acting.")
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



def render_text(answer: dict) -> str:
    """Plain-text rendering with [S#] markers, for the API and the evaluation report."""
    lines = [answer["message"]] if answer["message"] else []
    for c in answer["claims"]:
        label = {"warning": "Safety: ", "inference": "Inference (not stated directly): "}.get(c["kind"], "")
        marks = "".join("[{0}]".format(x) for x in c["citations"])
        lines.append("- {0}{1} {2}".format(label, c["text"].replace("\n", "; "), marks).rstrip())
    for conflict in answer["conflicts"]:
        lines.append("Note: " + conflict["summary"])
        for c in conflict["claims"]:
            lines.append("  - {0} {1}".format(c["text"], "".join("[{0}]".format(x) for x in c["citations"])))
    if answer.get("clarification"):
        lines.append(answer["clarification"]["question"])
    for u in answer["unresolved"]:
        lines.append("Unresolved: " + u)
    if answer["citations"]:
        lines.append("Sources: " + "; ".join(
            "[{0}] {1}{2}, p. {3}".format(c["id"], c["title"], " Rev " + c["revision"] if c["revision"] else "",
                                          c["page"]) for c in answer["citations"]))
    if answer["safety_notice"]:
        lines.append(answer["safety_notice"])
    return "\n".join(lines)
