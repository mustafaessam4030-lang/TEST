# Engineering Knowledge Assistant

Upload engineering PDFs and ask questions about them. Every factual answer quotes the documents and cites the exact document and page. Click a citation to open that page with the passage highlighted. When the documents don't support an answer, the assistant says so and lists what it searched. It does not fill the gap with a guess.

This is a self-contained application. It does not depend on, or change, anything else in this repository.

```
engineering-assistant/
├── eka/                  the application (Python package)
│   ├── extract.py        PDF -> pages: text blocks, columns, tables, warnings, OCR fallback, page status
│   ├── chunking.py       page-bounded chunks with section path; one chunk per table row
│   ├── ingest.py         upload, duplicate detection, background worker, re-index, revisions
│   ├── embeddings.py     hashing (default) | fastembed bge-small | Ollama embeddings
│   ├── retrieval.py      BM25 + vectors, RRF fusion, relevance gating, per-query trace
│   ├── answer.py         evidence selection, revisions, clarification, validation, answer object
│   ├── generation.py     optional Ollama (Qwen) generation with JSON claims
│   ├── citations.py      citation construction and validation
│   ├── conversation.py   small talk, follow-ups, conversation memory
│   ├── api.py            FastAPI endpoints + static UI
│   └── static/           web UI (vanilla JS, no build step)
├── samples/              synthetic, non-confidential test PDFs (+ scripts/make_samples.py)
├── eval/                 evaluation questions (tuning set + held-out set)
├── scripts/              make_samples.py, run_eval.py, eka.sh
├── tests/                73 automated tests
└── docs/                 ARCHITECTURE.md, EVALUATION.md
```

## Quick start

Requires Python 3.10+ (developed and tested on 3.13, Linux).

```sh
cd engineering-assistant
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # PyMuPDF, numpy, FastAPI, uvicorn (~40 MB)

# Optional but recommended: OCR for scanned PDFs (about 8 MB)
sudo apt-get install --no-install-recommends tesseract-ocr     # Debian/Ubuntu
# brew install tesseract                                       # macOS
# Windows: install from https://github.com/UB-Mannheim/tesseract/wiki and put it on PATH

python -m eka                            # http://127.0.0.1:8765
```

Open http://127.0.0.1:8765, upload PDFs (try the files in `samples/`), wait for **Ready**, and ask a question.

### Start, stop, status

| Action | Command |
|---|---|
| Run in the foreground | `python -m eka` (stop with Ctrl+C) |
| Run in the background | `scripts/eka.sh start` (log in `data/server.log`) |
| Stop | `scripts/eka.sh stop` |
| Status | `scripts/eka.sh status` (calls `/api/health`) |

Interrupted ingestion resumes automatically on the next start. Documents, the index and logs live in `data/` (or `EKA_DATA_DIR`). Back up that directory to keep the library.

## Configuration

Everything is set with environment variables (see `.env.example`), and every variable has a safe default.

| Variable | Default | Meaning |
|---|---|---|
| `EKA_DATA_DIR` | `./data` | SQLite index, stored PDFs, query log, cached models |
| `EKA_HOST` / `EKA_PORT` | `127.0.0.1` / `8765` | Bind address. Localhost only by default |
| `EKA_API_TOKEN` | *(empty)* | When set, every `/api` call needs `Authorization: Bearer <token>`; the UI prompts for it |
| `EKA_MAX_UPLOAD_MB` | `100` | Upload size limit |
| `EKA_EMBEDDER` | `hashing` | `hashing` (offline, no download), `fastembed` (bge-small, recommended), `ollama` |
| `EKA_EMBED_MODEL` | per embedder | e.g. `BAAI/bge-small-en-v1.5`, `nomic-embed-text` |
| `EKA_GENERATOR` | `extractive` | `extractive` (no LLM) or `ollama` |
| `EKA_LLM_MODEL` | `qwen2.5:7b-instruct` | Ollama model tag used when `EKA_GENERATOR=ollama` |
| `EKA_OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama server |
| `EKA_LLM_TIMEOUT_S` / `EKA_LLM_RETRIES` | `120` / `1` | Bounded model calls; on failure the extractive answer is used |
| `EKA_OCR_ENABLED`, `EKA_OCR_DPI`, `EKA_OCR_LANG` | `1`, `300`, `eng` | OCR settings (additional tesseract language packs needed for other languages) |
| `EKA_TOP_K`, `EKA_MIN_COVERAGE`, `EKA_MIN_SEMANTIC` | `6`, `0.5`, embedder default | Retrieval tuning |
| `EKA_LOG_QUERY_TEXT` | `0` | The query log records scores, documents and pages, never document text. Question text is logged only when this is `1` |

Changing `EKA_EMBEDDER` is safe. On start-up the stored passages are re-embedded with the new model, so vectors from different models are never mixed.

## Models: what is used and why

The embedding model and the answer generator are separate, independent choices.

**Embeddings.** Two configurations were measured on the same questions (details in [docs/EVALUATION.md](docs/EVALUATION.md)):

| | `hashing` (default) | `fastembed` bge-small-en-v1.5 (recommended) |
|---|---|---|
| Download | none | ~65 MB on first use (from Hugging Face), cached in `data/models` |
| Held-out answerable correct | 75.0% | 83.3% |
| Held-out retrieval recall@k | 75.0% | 100% |
| Query latency p50 | ~4 ms | ~90 ms |
| Ingestion, 240-page manual (this 4-vCPU container) | 11 s | 261 s |
| Peak memory during that ingestion | ~110 MB | ~500 MB |

`hashing` is the default because it needs no download and always works offline. `fastembed` measurably improves answers to paraphrased questions ("how much oil does it hold" vs "oil capacity"), so set `EKA_EMBEDDER=fastembed` (with `pip install -r requirements-optional.txt`) for real use. Both made zero unsupported claims in evaluation.

**Answer generation.** The default `extractive` mode needs no LLM. Answers are sentences, steps and table rows copied from the documents, so a value can only appear if it is printed in a cited passage. This mode produced every result in the evaluation.

A local Qwen model through Ollama can phrase answers instead (`EKA_GENERATOR=ollama`). Its output is never shown unchecked:

- any claim citing an unknown passage, or stating a number or part number its cited passages don't contain, is dropped;
- on a timeout, an error or an empty response, the extractive answer is used.

Real Ollama was not available where this was built: no Ollama binary, no GPU, 15 GB RAM. The Ollama path is tested only against a stand-in server, and **answer quality with a real Qwen model has not been measured.** Tags verified to exist on ollama.com (not pulled):

| Hardware | Suggested model | Size (q4) |
|---|---|---|
| CPU only, 16 GB RAM | `qwen2.5:3b-instruct` or `qwen3:4b-instruct` | ~2 GB |
| 16 GB+ RAM or 8 GB+ GPU | `qwen2.5:7b-instruct` (default tag) | 4.7 GB |
| 32 GB+ RAM or 16 GB+ GPU | `qwen2.5:14b-instruct` | ~9 GB |

To enable it:

```sh
ollama pull qwen2.5:7b-instruct                       # you choose when to download
EKA_GENERATOR=ollama EKA_LLM_MODEL=qwen2.5:7b-instruct python -m eka
python scripts/run_eval.py --generator ollama --llm-model qwen2.5:7b-instruct   # measure before relying on it
```

`/api/health` reports whether the model is reachable and pulled. No document text is sent anywhere except the configured Ollama URL, which is local by default.

**Vision models** (e.g. `qwen2.5vl:7b`) are not used. Diagrams and images are counted per page and reported as "not interpreted"; answers never describe a diagram.

## Using it

- **Library.** Upload by drag-and-drop or the file picker. Each document shows a status:
  - **Queued** or **Processing**, with page progress;
  - **Ready**;
  - **Partial**: some pages could not be read, and they are listed;
  - **Failed**: with the reason (corrupted, password protected, no text, scanned page without OCR).

  *Page status* shows how each page was read (text, OCR, blank, failed). Re-uploading an identical file is detected and not indexed twice. Tick documents to restrict questions to them.
- **Revisions.** Documents sharing a document number (`Document No: …`) are ordered by revision. Older ones are marked *superseded*, answers use the current revision, and a differing value in an older revision is shown as a flagged note.
- **Answer types.**
  - *From the documents*: quoted claims with citations.
  - *Needs clarification*: the value depends on equipment the question didn't name. Each option is shown with its citation, and you can click one.
  - *Sources conflict*: documents disagree and none supersedes the other.
  - *Not established by the documents*: says what was searched and why nothing qualified.

  Safety-relevant answers (pressure, torque, lifting, warnings…) carry a notice to confirm against the controlled document and the responsible engineer. Answers from OCR text are labelled.
- **How this answer was found** under each answer lists the documents searched and used, plus every candidate passage with its BM25, cosine and coverage scores and the reason it was kept or rejected.

## API

See `eka/api.py` (OpenAPI UI at `/api/docs`). Examples:

```sh
curl -X POST "localhost:8765/api/documents?filename=manual.pdf" -H "Content-Type: application/pdf" --data-binary @manual.pdf
curl localhost:8765/api/documents
curl -X POST localhost:8765/api/chat -H "Content-Type: application/json" -d '{"message":"What is the rated pressure of the HX-200?"}'
```

The chat response is the structured answer object: `status`, `message`, `claims` (each with `kind` and `citations`), `citations` (document, page, section, excerpt, `open_url`, `page_image_url`), `conflicts`, `clarification`, `unresolved`, `safety_notice`, `documents_used`, `searched`, `generator`, `fallback_reason`, `trace` and `latency_ms`.

## Tests and evaluation

```sh
pip install -r requirements-dev.txt
python -m pytest                                       # 73 tests, ~12 s (OCR tests skip if tesseract is absent)
python scripts/run_eval.py                             # tuning set, default config
python scripts/run_eval.py --questions eval/heldout.json --embedder fastembed
```

Results and methodology: [docs/EVALUATION.md](docs/EVALUATION.md). Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Scanned PDF is **Failed** or **Partial** with "tesseract is not installed" | Install tesseract (above), then click **Re-index** |
| Pages listed as "OCR (low confidence)" | Poor scan quality. Answers from those pages are labelled; check against the page image, or rescan at 300 dpi |
| "The PDF is password protected" | Remove the password (with authorisation) and upload again |
| An answer you expect comes back "not established" | Use the document's own term or the equipment model, or tick the document to search it alone. Open *How this answer was found* to see whether retrieval found the page (a retrieval problem) or found it and rejected it (gating). Paraphrases work better with `EKA_EMBEDDER=fastembed` |
| `/api/health` shows the generator unavailable | Start Ollama (`ollama serve`) and pull the model named in `EKA_LLM_MODEL`; meanwhile answers are extractive |
| UI asks for a token | `EKA_API_TOKEN` is set on the server; enter it (kept for the browser session only) |
| Port in use | Set `EKA_PORT` |
| fastembed download fails behind a proxy | Set `HTTPS_PROXY` and, for a corporate CA, `REQUESTS_CA_BUNDLE`; or stay on `hashing` |

## Limitations

- **Vocabulary gaps.** With the default embedder, questions phrased very differently from the document ("how fast does the shaft turn" vs "output speed") are often answered "not established". bge-small narrows but does not close this gap. The system is tuned to refuse rather than guess.
- **Tables** are detected only when they have ruled lines (PyMuPDF `find_tables`). Borderless tables are read as text, without column names. Tables on scanned pages are read as plain OCR lines.
- **Layout**: two-column pages are handled by a geometric heuristic. Complex layouts (side-bars, callouts over images) may be read out of order.
- **Diagrams and images** are not interpreted.
- **Conflict detection** compares numeric values across documents. A disagreement expressed only in words is not flagged.
- **Access control** is a single shared token plus per-question document scoping. There are no per-user document permissions yet.
- **Conversation memory** is in-process; it is lost on restart. The UI keeps its transcript in the browser session.
- **Ollama/Qwen generation has not been evaluated** with a real model.
- The evaluation uses small synthetic documents written for this project. Expect lower numbers on real manuals. Run `scripts/run_eval.py` with your own question set before relying on it.

## Snowflake

Snowflake is not used. The application runs and is tested without it. If Snowflake should later hold the document index (for sharing across sites or for audit), the boundary is `eka/store.py`: a Snowflake implementation of the same methods would hold documents, pages, chunks and vectors, with the in-memory `HybridIndex` loading from it unchanged. That needs schema approval, and it has not been built or tested. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#snowflake-adapter-boundary).

## Licensing note

PyMuPDF is licensed under AGPL-3.0, or commercially by Artifex. Internal use is generally fine, but distributing this application, or offering it as a network service to third parties, may trigger AGPL obligations. Check with your legal team, or swap the extraction layer (`eka/extract.py`) for `pypdfium2` and `pdfplumber`. Tesseract is Apache-2.0 and bge-small-en-v1.5 is MIT. Qwen2.5 7B/14B and Qwen3 are Apache-2.0, but the Qwen2.5 3B and 72B sizes use separate Qwen licences, so check the model card before commercial use.
