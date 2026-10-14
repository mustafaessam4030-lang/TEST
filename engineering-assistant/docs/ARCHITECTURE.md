# Architecture

## Constraints that shaped it

Inspected before any code was written:

- **Repository.** It held unrelated projects: a web film, Snowflake data pipelines, the ATLAS control tower and the Maia equipment assistant. None of them did PDF retrieval-augmented generation (RAG). The assistant was built as a separate directory and nothing outside `engineering-assistant/` was touched.
- **Machine.** A 4-vCPU, 15 GB RAM container with no GPU and no Ollama. The design therefore had to:
  - run fully on CPU with no model at all;
  - make every model optional and swappable;
  - never download a model without an explicit setting.
- **Snowflake.** The connector was unavailable in this session. The core must not depend on it.

## Components and data flow

```
                 ingestion (background worker)                          query time (request thread)
 upload ─► sha256 ─► dup? ─► extract.py ───────► chunking.py ─► embed ─►  store.py (SQLite, 1 transaction)
           (ingest.py)       │ per page:                         │                 │
                             │  text blocks + font sizes         │                 ▼
                             │  column ordering                  │      retrieval.HybridIndex (in memory,
                             │  ruled tables -> rows + headers   │      rebuilt & swapped after each doc)
                             │  WARNING/CAUTION blocks           │                 │
                             │  no text layer -> tesseract OCR   │                 ▼
                             │  status: ok/ocr/empty/failed...   │   chat ─► conversation.route ─► Retriever.search
                             ▼                                   │          (smalltalk: no retrieval)  │ BM25 + cosine
                      revision metadata (doc no., rev, date)     │                                     │ RRF fusion
                      -> superseded_by                           │                                     │ gating + trace
                                                                 │                                     ▼
                                                                 │   answer.AnswerEngine: evidence units, revisions,
                                                                 │   equipment ambiguity, warnings, [Ollama claims],
                                                                 │   citation validation ─► structured answer object
```

### Ingestion (`ingest.py`, `extract.py`, `ocr.py`, `chunking.py`)

- **Upload.** An upload returns immediately with a `queued` document. A single worker thread processes documents in order and records the stage and page progress, which the UI polls.
- **Duplicates.** Detected by SHA-256 of the bytes. The document id is the first 16 hex digits of that hash, and file paths are derived only from that id, so there is no path traversal.
- **Page records.** Every page gets a record with `status`, `method`, `chars`, `images`, `ocr_confidence` and `reason`. A page is never dropped silently:
  - blank pages are `empty`;
  - scanned pages without OCR are `ocr_unavailable`;
  - OCR errors are `ocr_failed`;
  - exceptions are `error`.
- **Document status.**
  - `ready`: all pages read.
  - `partial`: some pages failed, and they are listed.
  - `failed`: nothing could be extracted (corrupt, encrypted, not a PDF, or every page unreadable), with the reason.
- **Page cleanup.** Running headers and footers (lines repeated in the top or bottom 8% of most pages) and "Page N" furniture are removed so they don't pollute retrieval.
- **Columns.** Two-column reading order comes from block geometry: left column, then right, between full-width bands.
- **Tables.** Detected with PyMuPDF `find_tables`, which needs ruled lines. A caption line directly above a table ("Table 2 …") becomes the table caption. Each row becomes one chunk, rendered as `Column: value | Column: value` with the caption and section, so a retrieved row is understandable on its own.
- **Chunks.** A chunk never spans pages, so its page number is the exact source page from extraction. Page numbers are never produced by a model.
- **Re-indexing.** Replaces a document's pages, chunks and vectors in one SQLite transaction. Re-ingestion cannot duplicate records.
- **Revisions.** Documents sharing a document number are ordered by revision date, then revision. The older ones get `superseded_by`.

### Retrieval (`retrieval.py`, `textutil.py`, `embeddings.py`)

- **Tokenizer.** Keeps identifiers whole (`1R-0750`, `M12x1.5`, `HX-200`, `3.50`) and also emits joined and part forms (`1r0750`, `hx`, `200`). Values and units are never rewritten. Light suffix stripping applies identically to documents and queries.
- **Filtering.** Document scoping (`document_ids`) and searchable-status filtering happen *before* scoring.
- **Scoring.**
  - BM25 runs over `title + section + passage` text.
  - Cosine similarity runs over normalised vectors.
  - The two rankings are fused with reciprocal-rank fusion.
  - Exact matches of identifiers typed in the question get a small boost.
- **Gating.** A candidate is kept only if one of these holds:
  - its weighted query-term coverage is at least 0.5;
  - its cosine is at or above the embedder's "strong" threshold.

  A query naming an identifier that no searched document contains (e.g. `HX-300`) selects nothing.
- **Trace.** Every candidate in the trace records `bm25`, `cosine`, `fused`, `coverage` and the decision with its reason. The trace is returned with the answer and appended to `data/logs/queries.jsonl`, which holds no document text and no question text unless enabled.
- **Reranking.** No cross-encoder reranker was added. Measured retrieval recall@k was already 94–97% on the tuning set, and the misses were vocabulary gaps that a reranker over the same candidates would not fix.
- **Storage.** No vector database: a numpy matrix in memory is enough for tens of thousands of passages. A 240-page manual gives 920 passages, with 6 ms median query latency using the default embedder.

### Answering (`answer.py`, `conversation.py`, `generation.py`, `citations.py`)

1. **Routing.** Greetings, thanks, "what can you do", "which documents do you have" and "I can't find the answer" are handled without retrieval.

2. **Follow-ups.** Pronouns, fragments or a reply to a clarification add the previous question's terms to the retrieval query, at half weight, and prefer the previously used documents. A new full question ("What is the warranty period?") is not rewritten, so earlier messages cannot override it.

3. **Evidence units.** Retrieved passages are split into citable units:
   - sentences;
   - whole step lists, for "how do I" questions;
   - whole symptom/cause/remedy blocks, for "why" and "what should I do" questions;
   - table rows;
   - warnings.

   A unit must cover enough of the question's weighted terms. It must also cover its **topic words**: the question minus the identifiers that only select a document, and minus framing words like "how long". Section headings count at half credit. Strong semantic similarity lowers that bar (0.5 → 0.25) but never removes it, so a semantically similar but non-answering passage is rejected.

4. **Revisions.** Current revisions win. A differing value in a superseded revision is moved to a flagged revision note, with its own citation.

5. **Equipment ambiguity.** When evidence spans documents for different equipment with different values, and the question names none, the answer is `needs_clarification`. Each option is shown with its citation. Same equipment with different values and no supersession gives `conflict`. An answer from one equipment's document, when the question named no equipment and other equipment exists in the library, carries a caveat.

6. **Safety.** Warnings in the same document section as the evidence are attached to the answer. Safety-relevant topics get a notice to verify against the controlled document and the responsible engineer. OCR-derived citations and superseded sources are also called out.

7. **Generation.**
   - `extractive` (default): the claims *are* source text.
   - `ollama`: the model receives only the numbered passages and must return JSON claims with source ids, at temperature 0 with a JSON schema. Each claim is validated: cited ids must exist, and every number and identifier in the claim must appear in its cited passages. Invalid claims are dropped and listed in `rejected_claims`. No valid claims, a timeout, an HTTP error or an empty response all fall back to the extractive answer, with `fallback_reason` set.

8. **Citation validation.** Before returning, every citation is re-checked against the store:
   - the chunk exists;
   - it belongs to the cited document;
   - the page matches the chunk's page and is within the document;
   - the document is searchable;
   - the excerpt is contained in the chunk text.

   Claims left without a valid citation are removed. If nothing substantive remains, the status becomes `insufficient_evidence`.

9. **Answers are never empty.** Any exception becomes an `error` status with a message, and a final check guarantees a non-empty message. Regression tests cover empty, nonsense and very long inputs.

### Answer object (`POST /api/chat`)

```json
{
  "status": "answered | needs_clarification | conflict | insufficient_evidence | smalltalk | library | help | error",
  "message": "...",
  "claims": [{"text": "...", "kind": "direct | inference | warning", "citations": ["S1"]}],
  "citations": [{"id": "S1", "doc_id": "...", "title": "...", "revision": "B", "superseded": false,
                 "page": 2, "section": "...", "excerpt": "...", "ocr": false,
                 "open_url": "/api/documents/<id>/file#page=2",
                 "page_image_url": "/api/documents/<id>/pages/2/image?chunk=<chunk>"}],
  "conflicts": [{"kind": "revision | source", "summary": "...", "claims": [...]}],
  "clarification": {"question": "...", "options": ["..."]},
  "unresolved": ["..."], "safety_notice": "...",
  "documents_used": [{"doc_id": "...", "title": "...", "pages": [2]}],
  "searched": {"documents": 4, "titles": ["..."], "missing_identifiers": [], "scope": "all documents"},
  "generator": "extractive", "fallback_reason": "", "rejected_claims": [],
  "trace": {"selected": [...], "rejected": [...]}, "latency_ms": 4
}
```

### Interface (`static/`)

No framework and no build step. All document-derived text is inserted with `textContent`, never as HTML.

- **Wide screens:** three panes, with the library, the chat and a page viewer.
- **Narrow screens:** the library and the viewer become modal `<dialog>`s.
- **Viewer:** renders the cited page server-side as a PNG with the passage's bounding boxes highlighted. It also offers "Open PDF at this page" (`#page=N`) in the browser's PDF viewer.
- **Themes:** light and dark follow `prefers-color-scheme`. Verified in Chromium at 1440 px and 390 px with no console errors and no horizontal overflow.

## Security and resource notes

- **Network:** binds to `127.0.0.1` by default. An optional bearer token is compared in constant time. Responses carry `nosniff`, `no-referrer` and frame headers.
- **Uploads:** the size limit is enforced while streaming. The `%PDF` header is checked before storage. Only id-validated paths are served.
- **SQL and outbound calls:** all SQL is parameterised. Nothing is sent to external services; Ollama is called only when configured, at a local URL by default.
- **Logs:** they hold scores, ids and pages, never document text. Question text is logged only with `EKA_LOG_QUERY_TEXT=1`.
- **Resource use, measured on this container:**
  - default configuration: ~110–130 MB RSS;
  - fastembed: ~330–500 MB;
  - OCR: ~1–2 s per page at 300 dpi.

  The ingestion worker is a single thread, so a large upload cannot starve chat requests, which run in the server's thread pool.
- **Not provided:** per-user permissions, rate limiting, TLS (put a reverse proxy in front), virus scanning of uploads, and multi-process deployment. The index lives in process memory, so run one server process per data directory.

## Snowflake adapter boundary

The current design does not use Snowflake. If it is wanted later:

- `store.Store` is the only persistence interface: documents, pages, chunks, vectors, `all_chunks()` and `vectors(model)`. A `SnowflakeStore` implementing the same methods with parameterised queries and a least-privilege role would let several servers share one library.
- `HybridIndex.rebuild()` already loads everything through that interface, so retrieval and answering would not change.
- Snowflake's own vector search (`VECTOR` columns, Cortex Search) could replace the in-memory index. That would be a separate, measured change, because the gating and trace logic depends on raw BM25 and cosine scores.
- PDFs could stay in a stage. `ingest.file_path` is the single place that resolves file locations.

None of this is built. It needs an agreed schema, credentials and a non-production account to test against.
