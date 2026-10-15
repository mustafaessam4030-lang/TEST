"""Luma's answer modes on top of the grounded engine.

Each mode returns the same answer object as a normal question, built only from
indexed passages, so every value still carries a validated citation:

    compose        direct answers in natural sentences ("The CV-90's oil capacity is 2.4 L.")
    compare        side-by-side table with conditions kept; a chart only for like-for-like values
    summary        document outline, key specifications, safety warnings, procedures
    revision_diff  what changed between two revisions of one document
    where          where the previous answer is shown (document, page, table)
    simplify       plain-language rewording of the previous answer (language model only)
    follow_ups     suggested next questions that the library can actually answer
"""
from __future__ import annotations

import json
import re

from . import citations as cite
from .compose import lower_heading, numeric_value, plain_heading, row_cells, row_sentence, same_condition, short_equipment
from .figures import figure_terms
from .textutil import compact, content_terms, identifiers, numbers, sentences

STEP = re.compile(r"^\s*\d{1,2}[.)]\s")
WARNING_WORDS = re.compile(r"\b(warning|warnings|caution|cautions|safety|hazard\w*|danger)\b", re.I)
SPEC_WORDS = re.compile(r"\b(spec\w*|ratings?|key values|data|values)\b", re.I)
PROCEDURE_WORDS = re.compile(r"\b(procedure|procedures|maintenance|steps|service)\b", re.I)
THIS_DOC = re.compile(r"\b(this|that|the) (manual|document|datasheet|checklist|pdf|file)\b|\bit\b", re.I)
REV_NAMED = re.compile(r"\brev(?:ision)?\.?\s*([a-z0-9]{1,4})\b", re.I)
CONCEPTUAL = "Conceptual {0} drawn from the cited values, not a drawing from the manual."
PHOTO_WORDS = re.compile(r"\b(photo\w*|picture|image|looks? like)\b", re.I)
DRAWING_WORDS = re.compile(r"\b(diagram|schematic|circuit|drawing|wiring|layout|exploded)\b", re.I)
VISUAL_TERMS = set(content_terms("show me display see view picture photo photograph image diagram schematic "
                                 "drawing figure illustration chart graph visual draw"))


class ModesMixin:
    # ── helpers ──
    def _chunks(self):
        chunks, *_ = self.retriever.index.snapshot()
        return chunks

    def _current_docs(self, docs: dict) -> list[dict]:
        return [d for d in docs.values() if d["status"] in cite.SEARCHABLE and not d["superseded_by"]]

    def _docs_named(self, text: str, docs: dict, include_superseded: bool = False) -> list[dict]:
        """Documents whose equipment, title or number the text names."""
        asked = {compact(i) for i in identifiers(text)}
        out = []
        for d in docs.values():
            if d["status"] not in cite.SEARCHABLE or (d["superseded_by"] and not include_superseded):
                continue
            own = {compact(i) for i in identifiers(" ".join((d["title"], d["equipment"], d["doc_number"])))}
            if asked & own:
                out.append(d)
        return out

    @staticmethod
    def _doc_label(doc: dict) -> str:
        return (doc["title"] or doc["filename"]) + (" Rev " + doc["revision"] if doc["revision"] else "")

    def _previous_answer(self) -> dict | None:
        conversation_id = getattr(self._local, "conversation_id", None)
        if not conversation_id or self.store is None:
            return None
        for row in reversed(self.store.messages(conversation_id)):
            try:
                answer = json.loads(row["answer"])
            except ValueError:
                continue
            if answer.get("citations"):
                return answer
        return None

    # ── composition of extractive answers ──
    def _compose(self, r, draft, docs: dict):
        """Rewrite quoted table rows as sentences; the quote stays as the citation's excerpt."""
        for claim in draft.claims + [c for conflict in draft.conflicts for c in conflict["claims"]]:
            chunk = claim.get("_chunk")
            if chunk is None or chunk.kind != "table_row" or claim["kind"] not in ("direct", "superseded"):
                continue
            if claim["text"].strip() != chunk.text.split("\n")[-1].strip():
                continue
            doc = docs[chunk.doc_id]
            sentence = row_sentence(chunk.text, short_equipment(doc), r.question)
            if sentence and not cite.unsupported_values(sentence, [chunk.search_text]):
                claim["quote"] = claim["text"]
                claim["text"] = sentence
        for conflict in draft.conflicts:
            if conflict["kind"] == "revision" and conflict["claims"]:
                first = docs[conflict["claims"][0]["_chunk"].doc_id]
                conflict["summary"] = "Rev {0} ({1}) gave a different value; it has been superseded.".format(
                    first["revision"] or "?", first["revision_date"] or "older revision")
        if r.mode == "procedure" and draft.claims:
            # safety first: a procedure's warnings are read before its steps
            draft.claims = [c for c in draft.claims if c["kind"] == "warning"] + \
                           [c for c in draft.claims if c["kind"] != "warning"]
            chunk = next((c["_chunk"] for c in draft.claims if c["kind"] != "warning"), None)
            topic = plain_heading(chunk.section) if chunk is not None else ""
            if topic:
                draft.message = "Here's the {0} procedure for the {1}:".format(
                    lower_heading(topic), short_equipment(docs[chunk.doc_id]))
        elif r.mode in ("direct", "explain"):
            draft.message = ""

    def _compose_compare(self, r, draft, docs: dict, out_extra: dict):
        """One row per piece of equipment, values and conditions kept apart."""
        rows, seen = [], set()
        for claim in draft.claims:
            if claim["kind"] != "direct":
                continue
            chunk = claim["_chunk"]
            doc = docs[chunk.doc_id]
            name = short_equipment(doc)
            if compact(name) in seen:
                continue
            seen.add(compact(name))
            if chunk.kind == "table_row":
                cells = row_cells(chunk.text, r.question + " " + r.retrieval_query)
            else:
                cells = {"label": "", "value": claim.get("quote") or claim["text"], "condition": "", "caption": "",
                         "measure": ""}
            rows.append({"equipment": name, "document": self._doc_label(doc), **cells, "_chunk": chunk})
        if len(rows) < 2:
            return
        out_extra["table"] = {
            "kind": "comparison",
            "columns": ["Equipment", "Item", "Value", "Condition", "Source"],
            "rows": [{"cells": [x["equipment"], " ".join(p_ for p_ in (x["label"], x["measure"].lower()) if p_),
                                x["value"], x["condition"] or "—"], "_chunk": x["_chunk"]} for x in rows]}
        a, b = rows[0], rows[1]
        lead = None
        item = lambda x: lower_heading(" ".join(p_ for p_ in (x["label"], x["measure"]) if p_))  # noqa: E731
        if a["label"] and compact(item(a)) == compact(item(b)) and len(rows) == 2:
            lead = "The {0}'s {1} is {2}, while the {3}'s is {4}.".format(
                a["equipment"], item(a), a["value"], b["equipment"], b["value"])
        elif all(x["label"] for x in rows[:3]):
            lead = "; ".join("The {0}'s {1} is {2}".format(x["equipment"], item(x), x["value"]) for x in rows[:3]) + "."
        warnings = [c for c in draft.claims if c["kind"] == "warning"]
        draft.claims = []
        if lead and not cite.unsupported_values(lead, [x["_chunk"].search_text for x in rows]):
            draft.claims.append({"text": lead, "kind": "direct", "_chunk": rows[0]["_chunk"],
                                 "_extra": [x["_chunk"] for x in rows[1:]], "citations": [], "composed": True})
        draft.claims += warnings
        draft.message = "" if lead else "Here they are side by side:"
        conditions = [x["condition"] for x in rows]
        if any(conditions) and not all(same_condition(conditions[0], c) for c in conditions[1:]):
            draft.unresolved.append("These values are stated under different conditions ({0}), so they aren't "
                                    "directly comparable.".format(" vs ".join(c or "no condition stated" for c in conditions)))
        if r.differences_only and len({compact(x["value"]) for x in rows}) == 1:
            draft.message = "There's no difference here: both state {0}.".format(rows[0]["value"])
        # a chart only when the values are like for like: same item, unit and condition
        parsed = [numeric_value(x["value"]) for x in rows]
        if (all(parsed) and len({p[1] for p in parsed}) == 1 and len({compact(x["label"]) for x in rows}) == 1
                and all(same_condition(conditions[0], c) for c in conditions)):
            title = item(rows[0])
            out_extra["visual"] = {"kind": "bars", "title": title[:1].upper() + title[1:] if title else "Comparison",
                                   "unit": parsed[0][1],
                                   "label": CONCEPTUAL.format("chart"),
                                   "items": [{"name": x["equipment"], "value": p[0], "text": x["value"]}
                                             for x, p in zip(rows, parsed)]}

    # ── summary ──
    def _summary_targets(self, r, docs: dict, doc_ids, history) -> list[dict] | None:
        named = self._docs_named(r.retrieval_query, docs, include_superseded=bool(REV_NAMED.search(r.question)))
        if named:
            rev = REV_NAMED.search(r.question)
            if rev:
                exact = [d for d in named if d["revision"].lower() == rev.group(1).lower()]
                named = exact or named
            else:
                named = [d for d in named if not d["superseded_by"]] or named
            return named
        if doc_ids:
            return [docs[d] for d in doc_ids if d in docs]
        if THIS_DOC.search(r.question) and history:
            last = next((t for t in reversed(history) if t.doc_ids), None)
            if last:
                return [docs[d] for d in last.doc_ids if d in docs]
        current = self._current_docs(docs)
        if len(current) == 1:
            return current
        return None

    def _summary(self, r, docs: dict, doc_ids, history) -> dict | None:
        wants = ("warnings" if WARNING_WORDS.search(r.question) else "specs" if SPEC_WORDS.search(r.question)
                 else "procedures" if PROCEDURE_WORDS.search(r.question) else "document")
        targets = self._summary_targets(r, docs, doc_ids, history)
        if targets is None and wants == "warnings":
            targets = self._current_docs(docs)
        if not targets:
            options = [{"label": short_equipment(d), "doc_id": d["id"], "title": self._doc_label(d)}
                       for d in self._current_docs(docs)]
            out = self._base("needs_clarification", "Which document would you like me to summarize?")
            out["clarification"] = {"question": "Pick a document:", "options": options}
            return out
        chunks = self._chunks()
        mine = {d["id"]: [c for c in chunks if c.doc_id == d["id"]] for d in targets}
        self._progress("Building the summary", "analyzing")
        out = self._base("answered", "")
        out["mode"] = "summary"
        claims: list[dict] = []
        extra: dict = {}

        if wants == "warnings":
            for d in targets:
                for c in sorted(mine[d["id"]], key=lambda c: (c.page_no, c.seq)):
                    if c.kind == "warning":
                        claims.append({"text": c.text, "kind": "warning", "_chunk": c, "citations": [],
                                       "doc": short_equipment(d)})
            if not claims:
                return self._summary_nothing(targets, "safety warnings")
            where = (short_equipment(targets[0]) + " " + (targets[0]["doc_type"] or "document")).strip() \
                if len(targets) == 1 else "{0} documents".format(len(targets))
            out["message"] = "{0} safety warning{1} in the {2}:".format(
                len(claims), "" if len(claims) == 1 else "s", where) if len(targets) == 1 else \
                "Safety warnings across your {0}:".format(where)
        elif wants == "specs":
            d = targets[0]
            rows = [c for c in sorted(mine[d["id"]], key=lambda c: (c.page_no, c.seq)) if c.kind == "table_row"]
            if not rows:
                return self._summary_nothing(targets[:1], "specification tables")
            table_rows = []
            for c in rows[:16]:
                cells = row_cells(c.text)
                table_rows.append({"cells": [cells["label"], cells["value"], cells["condition"] or "—"], "_chunk": c,
                                   "group": cells["caption"]})
            extra["table"] = {"kind": "specs", "columns": ["Item", "Value", "Condition", "Source"], "rows": table_rows}
            out["message"] = "Here are the {0}'s key specifications from the {1}:".format(
                short_equipment(d), self._doc_label(d))
        elif wants == "procedures":
            d = targets[0]
            sections: dict[str, list] = {}
            for c in sorted(mine[d["id"]], key=lambda c: (c.page_no, c.seq)):
                if c.kind == "text" and sum(bool(STEP.match(l)) for l in c.text.split("\n")) >= 2:
                    sections.setdefault(c.section, []).append(c)
            if not sections:
                return self._summary_nothing(targets[:1], "step-by-step procedures")
            if len(sections) == 1:
                section, parts = next(iter(sections.items()))
                for c in parts:
                    claims.append({"text": c.text, "kind": "direct", "_chunk": c, "citations": []})
                out["message"] = "Here's the {0} procedure for the {1}:".format(
                    lower_heading(plain_heading(section)), short_equipment(d))
                out["mode"] = "procedure"
            else:
                out["message"] = "The {0} covers {1} procedures. Which one do you need?".format(
                    self._doc_label(d), len(sections))
                out["status"] = "needs_clarification"
                out["clarification"] = {"question": "Choose a procedure:", "options": [
                    {"label": "Show the {0} procedure for the {1}".format(lower_heading(plain_heading(s)), short_equipment(d)),
                     "doc_id": d["id"], "title": self._doc_label(d)} for s in sections]}
            self._attach_section_warnings(claims, d, sections)
            claims[:] = [c for c in claims if c["kind"] == "warning"] + [c for c in claims if c["kind"] != "warning"]
        else:  # an outline of the document: each section with its opening sentence
            d = targets[0]
            seen = []
            for c in sorted(mine[d["id"]], key=lambda c: (c.page_no, c.seq)):
                heading = plain_heading(c.section)
                if not heading or heading in seen or heading.upper() == (d["title"] or "").upper():
                    continue
                if c.kind == "table_row":
                    quote = c.text.split("\n")[0] if "\n" in c.text else c.text
                elif c.kind == "text":
                    first = sentences(c.text)
                    quote = first[0] if first else ""
                else:
                    continue
                if not quote or len(quote) < 12:
                    continue
                seen.append(heading)
                claims.append({"text": quote, "quote": quote, "kind": "outline", "heading": heading,
                               "_chunk": c, "citations": []})
            if not claims:
                return self._summary_nothing(targets[:1], "readable sections")
            first_warning = next((c for c in mine[d["id"]] if c.kind == "warning"), None)
            if first_warning is not None:
                claims.append({"text": first_warning.text, "kind": "warning", "_chunk": first_warning, "citations": []})
            out["message"] = "The {0} ({1} page{2}) covers {3} topic{4}:".format(
                self._doc_label(d), d["page_count"], "" if d["page_count"] == 1 else "s",
                len(seen), "" if len(seen) == 1 else "s")
        out["claims"] = claims
        out.update(extra)
        self._finalise_citations(out, docs)
        out["documents_used"] = out["documents_used"] or [
            {"doc_id": d["id"], "title": d["title"], "revision": d["revision"], "superseded": bool(d["superseded_by"]),
             "pages": []} for d in targets]
        out["safety_notice"] = self._safety_notice(r.question, out)
        return out

    def _summary_nothing(self, targets, what: str) -> dict:
        names = ", ".join(self._doc_label(d) for d in targets)
        out = self._base("insufficient_evidence", "I couldn't find any {0} in {1}.".format(what, names))
        return out

    def _attach_section_warnings(self, claims: list, doc: dict, sections: dict):
        have = {compact(c["text"]) for c in claims}
        for c in self._chunks():
            if c.doc_id == doc["id"] and c.kind == "warning" and c.section in sections and compact(c.text) not in have:
                claims.append({"text": c.text, "kind": "warning", "_chunk": c, "citations": []})
                have.add(compact(c.text))

    # ── revision differences ──
    def _revision_diff(self, r, docs: dict) -> dict | None:
        groups: dict[str, list[dict]] = {}
        for d in docs.values():
            if d["status"] in cite.SEARCHABLE and d["doc_number"]:
                groups.setdefault(d["doc_number"].upper(), []).append(d)
        groups = {k: v for k, v in groups.items() if len(v) > 1}
        if not groups:
            return self._base("insufficient_evidence", "Your library doesn't have two revisions of the same "
                              "document yet, so there's nothing to compare between revisions.")
        named = {compact(i) for i in identifiers(r.retrieval_query)}
        chosen = [g for g in groups.values()
                  if any(named & {compact(i) for i in identifiers(" ".join((d["title"], d["equipment"], d["doc_number"])))}
                         for d in g)]
        if not chosen and len(groups) == 1:
            chosen = list(groups.values())
        if not chosen:
            out = self._base("needs_clarification", "Several documents have more than one revision.")
            out["clarification"] = {"question": "Which document do you mean?", "options": [
                {"label": "What changed in the {0}?".format(short_equipment(g[0])), "doc_id": None,
                 "title": g[0]["title"]} for g in groups.values()]}
            return out
        group = sorted(chosen[0], key=lambda d: (d["revision_date"], d["revision"]))
        revs = [m.group(1).lower() for m in REV_NAMED.finditer(r.question)]
        pick = [d for d in group if d["revision"].lower() in revs]
        old, new = (pick[0], pick[-1]) if len(pick) >= 2 else (group[-2], group[-1])
        chunks = self._chunks()

        def rows_of(d):
            out = {}
            for c in chunks:
                if c.doc_id == d["id"] and c.kind == "table_row":
                    cells = row_cells(c.text)
                    out[compact(cells["label"])] = (cells, c)
            return out

        a_rows, b_rows = rows_of(old), rows_of(new)
        table_rows = []
        for key, (cells_b, chunk_b) in b_rows.items():
            if key in a_rows:
                cells_a, chunk_a = a_rows[key]
                if compact(cells_a["value"]) != compact(cells_b["value"]) or \
                        compact(cells_a["condition"]) != compact(cells_b["condition"]):
                    table_rows.append({"cells": [cells_b["label"], cells_a["value"], cells_b["value"]],
                                       "_chunks": [chunk_a, chunk_b]})
        # statements whose wording is the same apart from a number
        def statements(d):
            out = {}
            for c in chunks:
                if c.doc_id == d["id"] and c.kind == "text":
                    for s in sentences(c.text):
                        if numbers(s):
                            out[compact(re.sub(r"\d+(?:\.\d+)?", "#", s))] = (s, c)
            return out
        sa, sb = statements(old), statements(new)
        changed_text = [(sa[k], sb[k]) for k in sb if k in sa and compact(sa[k][0]) != compact(sb[k][0])]
        new_sections = []
        old_heads = {plain_heading(c.section) for c in chunks if c.doc_id == old["id"]}
        for c in sorted((c for c in chunks if c.doc_id == new["id"]), key=lambda c: (c.page_no, c.seq)):
            head = plain_heading(c.section)
            if head and head not in old_heads and head not in [n[0] for n in new_sections] \
                    and head.upper() != (new["title"] or "").upper():
                new_sections.append((head, c))

        out = self._base("answered", "")
        out["mode"] = "revision_diff"
        n = len(table_rows) + len(changed_text)
        out["message"] = ("Rev {0} of the {1} changes {2} value{3} from Rev {4}{5}.".format(
            new["revision"], new["title"], n, "" if n == 1 else "s", old["revision"],
            " and adds {0} section{1}".format(len(new_sections), "" if len(new_sections) == 1 else "s")
            if new_sections else "") if n or new_sections else
            "I found no differences in the values or sections of Rev {0} and Rev {1}.".format(
                old["revision"], new["revision"]))
        if table_rows:
            out["table"] = {"kind": "revision", "columns": ["Item", "Rev " + old["revision"], "Rev " + new["revision"],
                                                             "Source"], "rows": table_rows}
        claims = []
        for (s_old, c_old), (s_new, c_new) in changed_text[:5]:
            claims.append({"text": s_new, "kind": "direct", "_chunk": c_new, "citations": [], "label": "Rev " + new["revision"]})
            claims.append({"text": s_old, "kind": "superseded", "_chunk": c_old, "citations": [],
                           "label": "Rev " + old["revision"]})
        for head, c in new_sections[:8]:
            quote = c.text.split("\n")[0] if c.kind == "table_row" and "\n" in c.text else (sentences(c.text) or [c.text])[0]
            claims.append({"text": quote, "quote": quote, "kind": "outline", "heading": "New in Rev {0}: {1}".format(
                new["revision"], head), "_chunk": c, "citations": []})
        out["claims"] = claims
        self._finalise_citations(out, docs)
        if out["status"] == "insufficient_evidence" and (out.get("table") or {}).get("rows"):
            out["status"] = "answered"
        return out

    # ── where is that shown / simpler terms ──
    def _where(self, docs: dict) -> dict:
        previous = self._previous_answer()
        if previous is None:
            return self._base("help", "Ask a question first, and I'll show you exactly where the answer is in the manual.")
        by_id = {c.id: c for c in self._chunks()}
        claims, seen = [], set()
        for c in previous["citations"]:
            chunk = by_id.get(c["chunk_id"])
            if chunk is None or (c["doc_id"], c["page"]) in seen:
                continue
            seen.add((c["doc_id"], c["page"]))
            doc = docs.get(c["doc_id"])
            if doc is None:
                continue
            place = chunk.text.split("\n")[0] if chunk.kind == "table_row" and "\n" in chunk.text else \
                plain_heading(chunk.section)
            text = "Page {0} of the {1}{2}.".format(c["page"], self._doc_label(doc),
                                                    ", in {0}".format(place) if place else "")
            claims.append({"text": text, "kind": "location", "_chunk": chunk, "citations": []})
            if len(claims) == 3:
                break
        out = self._base("answered", "It's here — select a reference to open the page with the passage highlighted:"
                         if claims else "")
        out["claims"] = claims
        self._finalise_citations(out, docs)
        if not out["claims"]:
            return self._base("help", "The previous answer has no source I can point to.")
        out["status"] = "answered"
        return out

    def _simplify(self, r, docs: dict) -> dict:
        previous = self._previous_answer()
        if previous is None:
            return self._base("help", "Ask a question first; then I can explain the answer in simpler terms.")
        if not (self.generator and getattr(self._local, "use_generator", True)):
            return self._base("help", "Plain-language rewording needs the local language model, which isn't turned "
                              "on. The answer above is quoted exactly from the source; ask me about any term in it "
                              "and I'll look it up.")
        by_id = {c.id: c for c in self._chunks()}
        chunks = [by_id[c["chunk_id"]] for c in previous["citations"] if c["chunk_id"] in by_id]
        if not chunks:
            return self._base("help", "I can't find the sources of the previous answer any more.")
        return self._llm_rewrite(r, previous, chunks, docs)

    # ── figures (eka/figures.py) ──
    def _visual(self, r, docs: dict, doc_ids) -> dict | None:
        """'Show me the hydraulic circuit': the best matching original figure, with its page cited.

        Returns None when no figure matches, so the question is answered from the text instead
        (and the answer says that no figure was found)."""
        figures = self.store.figures()
        if not figures:
            self._local.visual_note = "Your documents don't contain any figures I could find, so here is the text."
            return None
        topic = [t for t in content_terms(r.retrieval_query) if t not in VISUAL_TERMS and not any(ch.isdigit() for ch in t)]
        asked_ids = {compact(i) for i in identifiers(r.retrieval_query)}
        scored = []
        for f in figures:
            doc = docs.get(f["doc_id"])
            if doc is None or doc["status"] not in cite.SEARCHABLE or (doc_ids and doc["id"] not in doc_ids):
                continue
            own_ids = {compact(i) for i in identifiers(" ".join((doc["title"], doc["equipment"], doc["doc_number"])))}
            if asked_ids and not asked_ids & own_ids:
                continue
            # the caption says what a figure shows; labels and section help; the document title barely
            fields = ((set(content_terms(f["caption"])), 1.0), (set(content_terms(f["labels"] + " " + f["section"])), 0.6),
                      (set(content_terms(doc["title"] + " " + doc["equipment"])), 0.35))
            weights = {t: self.retriever.index.idf(t) for t in topic}
            total = sum(weights.values())
            score = (sum(w * max((fw for terms, fw in fields if t in terms), default=0.0) for t, w in weights.items())
                     / total) if total else (1.0 if asked_ids else 0.0)
            if PHOTO_WORDS.search(r.question):
                score += 0.25 if f["kind"] == "image" else -0.1
            elif DRAWING_WORDS.search(r.question):
                score += 0.25 if f["kind"] == "drawing" else -0.1
            if doc["superseded_by"]:
                score *= 0.9
            scored.append((score, f, doc))
        scored = [x for x in sorted(scored, key=lambda x: -x[0]) if x[0] >= 0.5]
        if not scored:
            self._local.visual_note = "I couldn't find a figure of that in your documents, so here is the text."
            return None
        best = scored[0][0]
        chosen = [x for x in scored if x[0] >= best * 0.85][:2]
        chunks = self._chunks()
        out = self._base("answered", "")
        out["mode"] = "visual"
        claims, shown = [], []
        for _, f, doc in chosen:
            page_chunks = sorted((c for c in chunks if c.doc_id == f["doc_id"] and c.page_no == f["page_no"]),
                                 key=lambda c: c.seq)
            if not page_chunks:
                continue
            caption_chunk = next((c for c in page_chunks if f["caption"] and compact(f["caption"]) in compact(c.text)),
                                 page_chunks[0])
            shown.append({"id": f["id"], "doc_id": f["doc_id"], "page": f["page_no"], "kind": f["kind"],
                          "caption": f["caption"] or "Figure on page {0}".format(f["page_no"]),
                          "image_url": "/api/figures/{0}/image".format(f["id"]),
                          "document": self._doc_label(doc), "origin": "Original figure from the document",
                          "_chunk": caption_chunk})
            # what the document says about this figure: sentences in its section that share its words
            terms = figure_terms(f, doc)
            for c in page_chunks:
                if c.kind != "text" or c.section != f["section"]:
                    continue
                for sentence in sentences(c.text):
                    if compact(sentence) == compact(f["caption"]) or len(claims) >= 3:
                        continue
                    if len(terms & set(content_terms(sentence))) >= 2:
                        claims.append({"text": sentence, "kind": "direct", "_chunk": c, "citations": []})
        if not shown:
            return None
        self._progress("Reading the figure's page", "reading")
        first = shown[0]
        out["message"] = "Here's {0} from the {1}, page {2}.".format(
            _figure_phrase(first["caption"]), first["document"], first["page"])
        out["figures"] = shown
        out["claims"] = claims
        self._finalise_citations(out, docs)
        out["safety_notice"] = self._safety_notice(r.question, out)
        return out

    # ── follow-up suggestions ──
    def _follow_ups(self, r, out: dict, docs: dict) -> list[str]:
        if out["status"] != "answered" or not out.get("citations"):
            return []
        first = out["citations"][0]
        doc = docs.get(first["doc_id"])
        if doc is None:
            return []
        name = short_equipment(doc)
        chunks = self._chunks()
        ideas: list[str] = []
        mode = out.get("mode") or r.mode
        if mode in ("direct", "explain"):
            ideas.append("Where is that shown in the manual?")
            row = next((c for c in chunks if c.id == first["chunk_id"] and c.kind == "table_row"), None)
            if row is not None:
                label = compact(row_cells(row.text)["label"])
                for other in self._current_docs(docs):
                    if compact(short_equipment(other)) == compact(name):
                        continue
                    if any(c.doc_id == other["id"] and c.kind == "table_row" and
                           compact(row_cells(c.text)["label"]) == label for c in chunks):
                        ideas.append("Compare it with the {0}".format(short_equipment(other)))
                        break
            if any(c.doc_id == doc["id"] and c.kind == "table_row" for c in chunks):
                ideas.append("Summarize the key specifications of the {0}".format(name))
        elif mode == "procedure":
            if any(c.doc_id == doc["id"] and c.kind == "warning" for c in chunks):
                ideas.append("What are the safety warnings in the {0} manual?".format(name))
            ideas.append("Where is that shown in the manual?")
        elif mode == "compare" and not r.differences_only and out.get("table"):
            ideas.append("Summarize the differences")
        elif mode == "summary":
            figures = self.store.figures(doc["id"]) if hasattr(self.store, "figures") else []
            if figures:
                ideas.append("Show me the {0}".format(_figure_topic(figures[0]["caption"])))
            if any(d["doc_number"] == doc["doc_number"] and d["id"] != doc["id"] for d in docs.values() if doc["doc_number"]):
                ideas.append("What changed between revisions of the {0} manual?".format(name))
        if self.generator and mode in ("direct", "explain", "procedure"):
            ideas.append("Explain that in simpler terms")
        return list(dict.fromkeys(ideas))[:3]


def _figure_phrase(caption: str) -> str:
    """'Figure 1 HX-200 hydraulic circuit (open loop)' -> 'Figure 1, the HX-200 hydraulic circuit (open loop)'."""
    m = re.match(r"^((?:figure|fig\.?)\s*\d+)[.:]?\s*(.*)$", caption, re.I)
    if m and m.group(2):
        return "{0}, the {1}".format(m.group(1), m.group(2))
    return caption or "the figure"


def _figure_topic(caption: str) -> str:
    text = re.sub(r"^(figure|fig\.?)\s*\d+[.:]?\s*", "", caption, flags=re.I).strip()
    text = re.sub(r"\(.*?\)", "", text).strip()
    return lower_heading(text) if text else "figure"

