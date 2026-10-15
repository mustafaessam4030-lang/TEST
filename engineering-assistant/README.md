# Luma — Mantrac Engineering Intelligence Assistant

*(the Engineering Knowledge Assistant engine, as the Luma product; see [docs/LUMA.md](docs/LUMA.md) for answer modes, the language-model integration, figures and what is implemented vs planned)*


Upload engineering PDFs and ask questions about them. Every factual answer quotes the documents and cites the exact document and page. Click a citation to open that page with the passage highlighted. When the documents don't support an answer, the assistant says so and lists what it searched. It does not fill the gap with a guess.

This is a self-contained application. It does not depend on, or change, anything else in this repository. The interface follows the ATLAS visual system (same design tokens and components, see [docs/UI.md](docs/UI.md)) with a ChatGPT-style conversation layout.

![Luma](docs/screenshots/luma/desktop-compare.jpg)

```
engineering-assistant/
├── eka/                  the application (Python package)
│   ├── extract.py        PDF -> pages: text blocks, columns, tables, warnings, OCR fallback, page status
│   ├── chunking.py       page-bounded chunks with section path; one chunk per table row
│   ├── ingest.py         upload, duplicate detection, background worker, re-index, revisions
│   ├── embeddings.py     auto (bge-small if installed locally, else keyword) | fastembed | Ollama
│   ├── retrieval.py      BM25 + vectors, RRF fusion, relevance gating, per-query trace
│   ├── answer.py         evidence selection, revisions, clarification, validation, answer object
│   ├── modes.py          Luma answer modes: compare, summary, revision diff, where, simplify, visual, follow-ups
│   ├── compose.py        natural sentences built only from source values
│   ├── figures.py        figures (images, vector drawings) found at ingest, searched and cropped from the page
│   ├── generation.py     optional Ollama (Qwen) generation with JSON claims
│   ├── citations.py      citation construction and validation
│   ├── conversation.py   small talk, follow-ups, conversation memory (persisted in SQLite)
│   ├── api.py            FastAPI endpoints, server-sent-event streaming, static UI
│   └── static/           web UI in the ATLAS visual family (vanilla JS, no build step)
├── samples/              synthetic, non-confidential test PDFs (+ scripts/make_samples.py)
├── eval/                 evaluation questions (tuning set + held-out set)
├── scripts/              make_samples.py, run_eval.py, eka.sh, build_release.py
├── release/              launchers and config.env for the distributable zip (see START_HERE.md)
├── tests/                133 Python tests (incl. live-model tests) + tests/ui (128-check browser suite)
└── docs/                 ARCHITECTURE.md, EVALUATION.md, UI.md, screenshots/
```

## Packaged release (Windows, Linux, macOS)

The distributable zip has double-click launchers (`setup.bat`, `start.bat`, `stop.bat`), a `config.env`, a `pdfs/` folder that is indexed automatically, and the bge-small search model, so nothing is downloaded at run time. Instructions for end users: [START_HERE.md](START_HERE.md). Build it with:

```sh
# --wheels: optional offline packages for Windows; --split-mb: optional, no zip over ~25 MB (model shipped in parts)
python scripts/build_release.py --model-cache data/models --wheels win_amd64:3.12,3.13,3.14 --split-mb 25
```

Output goes to `dist/`. With `--no-model` the zip is about 2 MB, and setup downloads the model instead (`python -m eka.fetch_model`, 65 MB). This is the single-file delivery. With `--split-mb`, the model is shipped as `model-partXofY` zips. Setup joins them and checks the SHA-256 (`python -m eka.assemble`). `python -m eka.check` (or `check.bat`) checks an installation.

## PDF folder (phase 1) and SharePoint (phase 2)

Every PDF in the folder `EKA_INBOX_DIR` (default `pdfs/`, sub-folders included) is indexed automatically. The folder is checked every `EKA_INBOX_SCAN_S` seconds, or at once with **Check folder now** in the library.

- New files are indexed.
- A replaced file is re-indexed, and its old version is removed.
- Unchanged files are not read again.
- Files still being copied are left until they stop changing.
- Deleted files are kept unless `EKA_INBOX_REMOVE_DELETED=1`.
- Non-PDFs, videos included, are listed as skipped.

The folder is only read, never written. Phase 2 adds a SharePoint list as a second source through the same interface (`eka/sources/`); its design and the information it needs are in [docs/PHASE2_SHAREPOINT.md](docs/PHASE2_SHAREPOINT.md). It is not implemented yet.

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

Everything is set with environment variables (see `.env.example`), and every variable has a safe default. `python -m eka` also reads `config.env` in the current folder (or the file named by `EKA_CONFIG`). Real environment variables take precedence over it, and relative folder paths in it are relative to the file.

| Variable | Default | Meaning |
|---|---|---|
| `EKA_DATA_DIR` | `./data` | SQLite index, stored PDFs, query log, cached models |
| `EKA_HOST` / `EKA_PORT` | `127.0.0.1` / `8765` | Bind address. Localhost only by default |
| `EKA_API_TOKEN` | *(empty)* | When set, every `/api` call needs `Authorization: Bearer <token>`; the UI prompts for it |
| `EKA_MAX_UPLOAD_MB` | `100` | Upload size limit |
| `EKA_OPEN_BROWSER` | `0` | Open the browser on start (`1` in the release `config.env`) |
| `EKA_INBOX_DIR` | `pdfs` | Folder whose PDFs are indexed automatically; `off` disables it |
| `EKA_INBOX_SCAN_S` / `EKA_INBOX_SETTLE_S` | `15` / `5` | Seconds between folder checks / a file must be unchanged this long before it is read |
| `EKA_INBOX_REMOVE_DELETED` | `0` | `1`: remove a document when its file is deleted from the folder |
| `EKA_TESSERACT_CMD` | *(auto)* | Path to tesseract; otherwise PATH and the Windows default install folders are searched |
| `EKA_EMBEDDER` | `auto` | `auto`: semantic bge-small **if it is already on disk** (never downloads), otherwise keyword-only. `fastembed`: bge-small, downloading it once if needed (65 MB). `hashing`: keyword-only. `ollama`: an Ollama embedding model |
| `EKA_EMBED_MODEL` | per embedder | e.g. `BAAI/bge-small-en-v1.5`, `nomic-embed-text` |
| `EKA_MODEL_DIR` | `data/models` | Where embedding models are cached / looked for (`models` in the release) |
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

**Embeddings.** Retrieval is labelled honestly everywhere: `/api/health` (`retrieval_mode`, `semantic_search`), the sidebar ("Keyword search" or "Semantic + keyword") and Settings.

- **Semantic.** With bge-small, retrieval is hybrid, combining BM25 with real 384-dimension ONNX embeddings. A test checks that it ranks "how much oil does the gearbox hold" to the capacity row by meaning.
- **Keyword-only.** Without a semantic model, retrieval is keyword-only. The `hashing` back-end is character n-gram overlap, not a semantic embedding, and is never called one.

Measured on the same questions (details in [docs/EVALUATION.md](docs/EVALUATION.md)):

| | keyword-only (`hashing`) | semantic (`fastembed` bge-small-en-v1.5, recommended) |
|---|---|---|
| Download | none | ~65 MB once (from Hugging Face), cached in `EKA_MODEL_DIR` |
| Held-out answerable correct | 75.0% | 83.3% |
| Held-out retrieval recall@k | 75.0% | 100% |
| Query latency p50 | ~4 ms | ~90 ms |
| Ingestion, 240-page manual (this 4-vCPU container) | 11 s | 261 s |
| Peak memory during that ingestion | ~110 MB | ~500 MB |

The default `auto` never downloads anything. To turn semantic search on, run once with `pip install -r requirements-optional.txt` and `EKA_EMBEDDER=fastembed`; that fetches the model into `EKA_MODEL_DIR`. From then on `auto` uses it offline. Semantic search measurably improves answers to paraphrased questions. Both modes made zero unsupported claims in evaluation.

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

To enable it on a machine that can run it:

```sh
# 1. install Ollama: Linux `curl -fsSL https://ollama.com/install.sh | sh`; macOS/Windows: installer from ollama.com
ollama serve &                                        # if it is not already running as a service
ollama pull qwen2.5:7b-instruct                       # you choose when to download (4.7 GB)
EKA_GENERATOR=ollama EKA_LLM_MODEL=qwen2.5:7b-instruct python -m eka
python scripts/run_eval.py --generator ollama --llm-model qwen2.5:7b-instruct   # measure before relying on it
```

`/api/health` reports whether the model is reachable and pulled. No document text is sent anywhere except the configured Ollama URL, which is local by default.

**Vision models** (e.g. `qwen2.5vl:7b`) are not used. Diagrams and images are counted per page and reported as "not interpreted"; answers never describe a diagram.

## Using it

The layout is a collapsible sidebar, a centred conversation and a composer. The document panel is closed until you click a citation. Screenshots for desktop, tablet, phone and dark mode are in [docs/screenshots](docs/screenshots); the design and the tested interactions are in [docs/UI.md](docs/UI.md).

- **Chat.**
  - Answers stream their real progress ("Searching 4 documents" → "Reading 5 passages…" → "Checking every citation…").
  - **Stop** cancels an answer, and a stopped answer is not saved.
  - Each answer has Copy, Retry (latest answer) and helpful/not-helpful.
  - **Enter** sends, **Shift+Enter** adds a line, **/** focuses the box, **Ctrl+Shift+O** starts a new chat, **Ctrl+K** searches history and **Ctrl+.** toggles the sidebar.
- **History.**
  - Conversations are stored on the server, grouped by date, searchable, and can be renamed or deleted.
  - Follow-ups keep working after a restart.
- **Sources.** The "Sources" pill restricts a question to chosen documents.
- **Citations.** A source pill sits beside each claim. Clicking it opens the document panel at the cited page, with the passage highlighted and quoted. The panel offers:
  - page navigation, zoom and search in the document (scanned pages are searched through their OCR text);
  - opening the original PDF;
  - resizing.

  Esc closes it and returns you to the same place in the conversation. On phones it opens full screen with a Back button.
- **Library.** Upload by drag-and-drop, the Upload button, or the paperclip in the composer; an attachment chip follows processing. Each document shows a status:
  - **Queued** or **Processing**, with page progress;
  - **Ready**;
  - **Partial**: some pages could not be read, and they are listed;
  - **Failed**: with the reason (corrupted, password protected, no text, scanned page without OCR).

  *Page status* shows how each page was read (text, OCR, blank, failed). Re-uploading an identical file is detected and not indexed twice.
- **Revisions.** Documents sharing a document number (`Document No: …`) are ordered by revision. Older ones are marked *superseded*, answers use the current revision, and a differing value in an older revision is shown as a flagged note.
- **Answer types.**
  - *From the documents*: quoted claims with citations.
  - *Needs clarification*: the value depends on equipment the question didn't name. The assistant asks first and shows no values; clicking an equipment option answers from that model's document only.
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

Other endpoints:

- `POST /api/chat/stream`: server-sent events, `stage`… then `answer`.
- `GET /api/conversations?q=`, plus `GET`, `PATCH` and `DELETE /api/conversations/{id}`.
- `POST /api/messages/{id}/feedback`.
- `GET /api/documents/{id}/search?q=`.
- `GET /api/suggestions`, which returns only questions the engine can answer.

The chat response is the structured answer object: `status`, `message`, `claims` (each with `kind` and `citations`), `citations` (document, page, section, excerpt, `open_url`, `page_image_url`), `conflicts`, `clarification`, `unresolved`, `safety_notice`, `documents_used`, `searched`, `generator`, `fallback_reason`, `trace`, `latency_ms`, `message_id` and `conversation_id`. Claims quoting a table row also carry `source_kind`, `caption` and `cells` for table rendering.

## Tests and evaluation

```sh
pip install -r requirements-dev.txt
python -m pytest                                       # 133 tests; 7 skip without a model/embedder configured
EKA_TEST_OLLAMA_MODEL=qwen2.5:3b-instruct python -m pytest tests/test_llm_live.py   # against a REAL local model
EKA_TEST_MODEL_DIR=data/models python -m pytest        # also runs the real-embedding tests (otherwise skipped)
python scripts/run_eval.py                             # tuning set, keyword-only
python scripts/run_eval.py --questions eval/heldout.json --embedder fastembed

# Browser end-to-end (128 checks; starts its own servers on :8765, :8766, :8799)
npm i playwright && npx playwright install chromium    # once
PLAYWRIGHT_DIR=$PWD tests/ui/run.sh /tmp/eka-ui        # screenshots + e2e-results.json in /tmp/eka-ui
```

What is real and what is mocked:

| Suite | Real components | Mocked |
|---|---|---|
| `tests/test_pdf_pipeline.py`, `test_ingest_and_retrieval.py`, `test_answers.py` | PyMuPDF, tesseract OCR, SQLite, BM25 + vectors, answer engine, citation validator | nothing |
| `tests/test_api.py`, `test_conversations_api.py` | the FastAPI app in-process (TestClient), streaming, history, search, everything above | nothing |
| `tests/test_embeddings.py` | the real bge-small ONNX model (skipped unless `EKA_TEST_MODEL_DIR` points at it) | nothing |
| `tests/test_generation.py` | answer engine, claim validation, fallback | **the Ollama server** (stand-in HTTP server: no real LLM here) |
| `tests/ui/e2e.mjs`, desktop / tablet / phone / dark | the real server, real documents, real browser (Chromium) | nothing |
| `tests/ui/e2e.mjs`, "slow model" block | the real server and browser | **the model** (`tests/ui/slow_ollama.py` replies after 6 s) |

The default pytest run uses the keyword-only embedder, for speed and determinism.

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
- **Conversation history** is stored per server, not per user: anyone with access to the server sees the same history.
- **The UI** was verified in Chromium (desktop, tablet and phone emulation) only. Safari, Firefox, real touch devices and screen readers have not been tested.
- **Streaming** carries real pipeline stages, not tokens. Answers are shown only after citation validation, so a model's draft is never displayed before it has been checked.
- **Ollama/Qwen generation has not been evaluated** with a real model.
- The evaluation uses small synthetic documents written for this project. Expect lower numbers on real manuals. Run `scripts/run_eval.py` with your own question set before relying on it.

## Snowflake

Snowflake is not used. The application runs and is tested without it. If Snowflake should later hold the document index (for sharing across sites or for audit), the boundary is `eka/store.py`: a Snowflake implementation of the same methods would hold documents, pages, chunks and vectors, with the in-memory `HybridIndex` loading from it unchanged. That needs schema approval, and it has not been built or tested. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#snowflake-adapter-boundary).

## Licensing note

PyMuPDF is licensed under AGPL-3.0, or commercially by Artifex. Internal use is generally fine, but distributing this application, or offering it as a network service to third parties, may trigger AGPL obligations. Check with your legal team, or swap the extraction layer (`eka/extract.py`) for `pypdfium2` and `pdfplumber`. Tesseract is Apache-2.0 and bge-small-en-v1.5 is MIT. Qwen2.5 7B/14B and Qwen3 are Apache-2.0, but the Qwen2.5 3B and 72B sizes use separate Qwen licences, so check the model card before commercial use.
