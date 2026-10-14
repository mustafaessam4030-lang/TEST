"""Hybrid retrieval: BM25 over identifier-preserving tokens + dense vectors, fused with RRF.

Pipeline for one query:

1. Filter: only chunks of searchable documents the caller is allowed to see
   (``doc_ids``) are scored at all. Filtering happens before scoring, not after.
2. Score: BM25 (lexical) and cosine similarity (semantic), independently.
3. Fuse: reciprocal-rank fusion of the two rankings, plus a boost for passages
   containing the exact identifiers the user typed (part numbers, models).
4. Gate: a candidate is kept only if it is relevant on evidence we can explain:
   it covers enough of the query's weighted terms, or its semantic similarity is
   above the embedder's "strong" threshold. Every rejected candidate records why.
5. De-duplicate identical passages within a document and cut to top_k.

The result carries a trace (scores and decisions per candidate) so a wrong or
empty answer can be diagnosed as a retrieval problem or a generation problem.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .embeddings import Embedder
from .store import Store
from .textutil import compact, content_terms, identifiers, tokens

log = logging.getLogger(__name__)

K1, B = 1.2, 0.75
RRF_K = 60
NUMBER_WITH_UNIT = re.compile(r"^\d+(?:\.\d+)?[A-Z]{1,4}$")


@dataclass
class Indexed:
    id: str
    doc_id: str
    page_no: int
    seq: int
    kind: str
    section: str
    text: str
    search_text: str
    bboxes: list
    ocr: bool
    tf: Counter
    length: int
    terms: frozenset
    compact: str


@dataclass
class Candidate:
    chunk: Indexed
    bm25: float = 0.0
    cosine: float = 0.0
    fused: float = 0.0
    coverage: float = 0.0
    matched: list[str] = field(default_factory=list)
    decision: str = ""

    def trace(self) -> dict:
        return {"chunk_id": self.chunk.id, "doc_id": self.chunk.doc_id, "page": self.chunk.page_no,
                "kind": self.chunk.kind, "bm25": round(self.bm25, 3), "cosine": round(self.cosine, 3),
                "fused": round(self.fused, 4), "coverage": round(self.coverage, 2), "decision": self.decision}


@dataclass
class RetrievalResult:
    query: str
    selected: list[Candidate]
    rejected: list[Candidate]
    missing_identifiers: list[str]
    searched_doc_ids: list[str]
    terms: list[str]

    def trace(self) -> dict:
        return {"terms": self.terms, "missing_identifiers": self.missing_identifiers,
                "searched_documents": len(self.searched_doc_ids),
                "selected": [c.trace() for c in self.selected],
                "rejected": [c.trace() for c in self.rejected[:15]]}


class HybridIndex:
    """In-memory search structures rebuilt from the store; swapped atomically."""

    def __init__(self, store: Store, embedder: Embedder):
        self.store = store
        self.embedder = embedder
        self._lock = threading.Lock()
        self._chunks: list[Indexed] = []
        self._postings: dict[str, list[tuple[int, int]]] = {}
        self._df: Counter = Counter()
        self._avg_len = 1.0
        self._matrix = np.zeros((0, max(1, embedder.dim)), dtype=np.float32)

    def __len__(self):
        return len(self._chunks)

    def rebuild(self):
        rows = self.store.all_chunks()
        missing = self.store.chunks_missing_vectors(self.embedder.name)
        if missing:  # embedder changed or a previous embed failed: fill the gap
            log.info("embedding %d chunks with %s", len(missing), self.embedder.name)
            vectors = self.embedder.embed_documents([m["search_text"] for m in missing])
            self.store.put_vectors([(m["id"], vectors[i]) for i, m in enumerate(missing)], self.embedder.name)
        stored = self.store.vectors(self.embedder.name)
        chunks, postings, df = [], {}, Counter()
        matrix = np.zeros((len(rows), max(1, self.embedder.dim)), dtype=np.float32)
        for i, row in enumerate(rows):
            toks = tokens(row["search_text"])
            tf = Counter(toks)
            chunk = Indexed(row["id"], row["doc_id"], row["page_no"], row["seq"], row["kind"],
                            row["section"], row["text"], row["search_text"], json.loads(row["bboxes"]),
                            bool(row["ocr"]), tf, len(toks), frozenset(tf), compact(row["search_text"]))
            chunks.append(chunk)
            for term, count in tf.items():
                postings.setdefault(term, []).append((i, count))
                df[term] += 1
            vec = stored.get(row["id"])
            if vec is not None and len(vec) == matrix.shape[1]:
                matrix[i] = vec
        with self._lock:
            self._chunks, self._postings, self._df, self._matrix = chunks, postings, df, matrix
            self._avg_len = (sum(c.length for c in chunks) / len(chunks)) if chunks else 1.0

    def snapshot(self):
        with self._lock:
            return self._chunks, self._postings, self._df, self._matrix, self._avg_len

    def idf(self, term: str, df: Counter | None = None, n: int | None = None) -> float:
        df = self._df if df is None else df
        n = len(self._chunks) if n is None else n
        return math.log(1 + (n - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))


class Retriever:
    def __init__(self, index: HybridIndex, top_k: int = 6, pool: int = 40, min_coverage: float = 0.5,
                 min_semantic: float = 0.0):
        self.index = index
        self.top_k = top_k
        self.pool = pool
        self.min_coverage = min_coverage
        self.min_semantic = min_semantic or index.embedder.strong_similarity

    def search(self, query: str, doc_ids: list[str] | None = None, prefer_doc_ids: list[str] | None = None,
               top_k: int | None = None) -> RetrievalResult:
        chunks, postings, df, matrix, avg_len = self.index.snapshot()
        allowed = set(doc_ids) if doc_ids else None
        mask = [allowed is None or c.doc_id in allowed for c in chunks]
        searched = sorted({c.doc_id for c, ok in zip(chunks, mask) if ok})
        terms = content_terms(query)
        result = RetrievalResult(query, [], [], [], searched, terms)
        if not any(mask) or not terms:
            return result

        # Identifiers the user typed that no visible document contains.
        query_ids = [i for i in identifiers(query) if not NUMBER_WITH_UNIT.match(i)]
        visible = [c for c, ok in zip(chunks, mask) if ok]
        present_ids = [i for i in query_ids if any(compact(i) in c.compact for c in visible)]
        result.missing_identifiers = [i for i in query_ids if i not in present_ids]

        n = len(chunks)
        weights = {t: self.index.idf(t, df, n) for t in terms}
        bm25 = np.zeros(n, dtype=np.float32)
        for term in set(tokens(query)):
            idf = self.index.idf(term, df, n)
            for i, count in postings.get(term, ()):
                if mask[i]:
                    length = chunks[i].length
                    bm25[i] += idf * count * (K1 + 1) / (count + K1 * (1 - B + B * length / avg_len))
        qvec = self.index.embedder.embed_query(query)
        cos = matrix @ qvec if len(chunks) else np.zeros(0)
        allowed_idx = np.array([i for i, ok in enumerate(mask) if ok])
        lexical = [i for i in allowed_idx[np.argsort(-bm25[allowed_idx])] if bm25[i] > 0][: self.pool]
        semantic = list(allowed_idx[np.argsort(-cos[allowed_idx])][: self.pool])

        fused: dict[int, float] = {}
        for ranking in (lexical, semantic):
            for rank, i in enumerate(ranking):
                fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
        prefer = set(prefer_doc_ids or ())
        candidates = []
        total_weight = sum(weights.values()) or 1.0
        for i, score in fused.items():
            chunk = chunks[i]
            # Identifier-like terms (with digits) also match their punctuation-free form.
            matched = [t for t in terms if t in chunk.terms
                       or (any(ch.isdigit() for ch in t) and compact(t) in chunk.compact)]
            cand = Candidate(chunk, float(bm25[i]), float(cos[i]), score,
                             sum(weights[t] for t in matched) / total_weight, matched)
            if present_ids and any(compact(x) in chunk.compact for x in present_ids):
                cand.fused += 0.02
            if chunk.doc_id in prefer:
                cand.fused += 0.01
            candidates.append(cand)
        candidates.sort(key=lambda c: -c.fused)

        seen_text: set[tuple[str, str]] = set()
        limit = top_k or self.top_k
        for cand in candidates:
            key = (cand.chunk.doc_id, cand.chunk.compact[-400:])
            relevant_terms = cand.coverage >= self.min_coverage
            relevant_meaning = cand.cosine >= self.min_semantic
            if result.missing_identifiers and not present_ids:
                cand.decision = "rejected: query names {0}, which no searched document contains".format(
                    ", ".join(result.missing_identifiers))
            elif present_ids and not any(compact(x) in cand.chunk.compact for x in present_ids):
                cand.decision = "rejected: does not mention {0}".format(", ".join(present_ids))
            elif not (relevant_terms or relevant_meaning):
                cand.decision = "rejected: low relevance (term coverage {0:.2f} < {1}, cosine {2:.2f} < {3})".format(
                    cand.coverage, self.min_coverage, cand.cosine, self.min_semantic)
            elif key in seen_text:
                cand.decision = "rejected: duplicate passage"
            elif len(result.selected) >= limit:
                cand.decision = "rejected: below top_k cut-off"
            else:
                cand.decision = "selected"
                seen_text.add(key)
                result.selected.append(cand)
                continue
            result.rejected.append(cand)
        return result
