# Engineering Knowledge Assistant — start here

Ask questions about your engineering PDFs and get answers quoted from them,
with the exact document and page for every statement. Everything runs on
your computer: no cloud service, no Snowflake, and no AI account is needed.

## The files you receive

| File | Needed? |
|---|---|
| `EngineeringKnowledgeAssistant-<version>.zip` | **Yes**: the application |
| `EngineeringKnowledgeAssistant-<version>-model-part1of3.zip` … `part3of3.zip` | **Yes**: the search model, cut into parts so each file stays small. Setup joins them and checks they are complete |
| `…-offline-packages-win_amd64-py3.13-part1of3.zip` … | Only for a PC **without internet**: Python packages for 64-bit Windows, one set of 3 zips per Python version (3.12, 3.13, 3.14). Take only the set matching the PC's Python |

If you received a single larger zip that already contains `models/…/model_optimized.onnx`,
there are no model parts to add.

**Extract every zip into the same place.** Each one contains an
`EngineeringKnowledgeAssistant` folder, and they merge into one folder. In Windows
Explorer, right-click each zip, choose **Extract All…**, and enter the same
destination each time, for example `C:\Apps`. If Windows asks, choose
**Replace the files** (no file is in more than one zip).

## What is in this folder

| Item | What it is |
|---|---|
| `setup.bat` / `setup.sh` | One-time install (creates `.venv` and installs the packages) |
| `start.bat` / `start.sh` | Starts the assistant and opens it in the browser |
| `stop.bat` / `stop.sh` | Stops it |
| `check.bat` / `check.sh` | Checks the installation and lists what is missing |
| `run-tests.bat` / `run-tests.sh` | Runs the automated tests |
| `config.env` | Settings (port, PDF folder, OCR, optional local AI model) |
| `pdfs/` | **Put your PDFs here.** They are indexed automatically |
| `models/` | The search model (bge-small, 65 MB), included so nothing is downloaded |
| `data/` | Created on first run: the library, index, conversations and log |
| `samples/` | Synthetic test PDFs (not real equipment data) |
| `eka/` | The application |
| `docs/` | Architecture, evaluation, UI notes, phase 2 (SharePoint) plan |

## Requirements

* Windows 10/11, Linux or macOS, 64-bit, about 1 GB free disk and 2 GB RAM.
* **Python 3.10 – 3.14.** On Windows install it from <https://www.python.org/downloads/>
  and tick **"Add python.exe to PATH"**.
* Internet access during setup only (about 70 MB of packages), **or** the
  offline packages zip (see below).
* Optional: **Tesseract OCR** to read scanned PDFs (see step 4).

## Windows

1. Extract the app zip and the model-part zips to the same place (see above),
   giving for example `C:\Apps\EngineeringKnowledgeAssistant`. Do not run anything from
   inside a zip.
2. Double-click **`setup.bat`**. It takes 1–3 minutes and ends with a checklist;
   every line should say `OK` (a `WARN` for OCR is fine).
3. Copy your PDFs into the **`pdfs`** folder (sub-folders are fine).
4. Double-click **`start.bat`**. The browser opens at <http://127.0.0.1:8765>.
   Keep the black window open while you use the assistant.
5. Ask a question. Open **Document library** to see each PDF's status;
   new PDFs appear within about 15 seconds, or press **Check folder now**.
6. To stop: close the black window, or double-click **`stop.bat`**.

## Linux / macOS

```sh
cd EngineeringKnowledgeAssistant
./setup.sh                 # once
cp /path/to/manuals/*.pdf pdfs/
./start.sh                 # foreground; or ./start.sh --background, then ./stop.sh
```

Open <http://127.0.0.1:8765>. Use `PYTHON=/usr/bin/python3.12 ./setup.sh` to pick a Python.

## Step 4 (optional): OCR for scanned PDFs

Without OCR, text PDFs work normally and scanned pages are listed as
"needs OCR" instead of being silently skipped.

* Windows: install Tesseract from <https://github.com/UB-Mannheim/tesseract/wiki>
  (the default folder is found automatically), then restart the assistant.
* Linux: `sudo apt install tesseract-ocr` · macOS: `brew install tesseract`
* Elsewhere: set `EKA_TESSERACT_CMD=` in `config.env` to the full path of `tesseract.exe`.

Then remove and re-add the scanned PDFs (or press **Re-index** in the library).

## The PDF folder (phase 1)

* Everything in `pdfs/` (and its sub-folders) is checked every 15 seconds.
* New PDF → indexed. Replaced PDF (same name, new content) → re-indexed and
  the old version is dropped. Unchanged PDFs are never re-read.
* Files still being copied are left alone until they stop changing.
* Deleting a PDF from the folder does **not** remove it from the assistant
  unless `EKA_INBOX_REMOVE_DELETED=1` is set in `config.env`.
* Point at a shared folder with a full path: `EKA_INBOX_DIR=\\server\share\manuals`.
* Videos, Word files etc. are listed as "skipped" — videos come in phase 2.
* You can also drag PDFs onto the page; those are stored in `data/`.

The PDFs are only read, never moved, changed or uploaded anywhere.

## Optional: phrased answers with a local Qwen model

By default answers are quoted directly from the PDFs (no AI model). To have
answers phrased by a local model (still grounded and still cited):

1. Install Ollama from <https://ollama.com> and run `ollama pull qwen2.5:7b-instruct`
   (a 4.7 GB download; 8 GB RAM recommended).
2. In `config.env` set `EKA_GENERATOR=ollama`, then restart.

If the model is not running, the assistant falls back to quoted answers.

## Offline installation (no internet on the target PC)

Use the **offline-packages** zips that match the Python on that PC (for example
`…-offline-packages-win_amd64-py3.13-part1of3.zip` to `part3of3.zip` for Python 3.13;
check with `py --version`). Extract all of them into the same place as the app,
so a `wheels` folder appears next to `setup.bat`, then run `setup.bat`. Setup
then installs from `wheels` and does not contact the internet. Packages for
other Python versions can be built with `scripts/build_release.py`.

## Settings (`config.env`)

| Setting | Default | Meaning |
|---|---|---|
| `EKA_PORT` | 8765 | Browser address port |
| `EKA_HOST` | 127.0.0.1 | `0.0.0.0` makes it reachable from other PCs — set `EKA_API_TOKEN` first |
| `EKA_OPEN_BROWSER` | 1 | Open the browser on start |
| `EKA_INBOX_DIR` | pdfs | PDF folder (`off` disables it) |
| `EKA_INBOX_SCAN_S` | 15 | Seconds between folder checks |
| `EKA_INBOX_REMOVE_DELETED` | 0 | 1 = remove documents whose PDF was deleted |
| `EKA_DATA_DIR` | data | Library, index, conversations, log |
| `EKA_EMBEDDER` | auto | Semantic search with the included model; keyword-only if it is missing |
| `EKA_GENERATOR` | extractive | `ollama` for phrased answers (see above) |
| `EKA_TESSERACT_CMD` | (auto) | Full path to `tesseract.exe` |
| `EKA_API_TOKEN` | (none) | Require this token to use the assistant |

Real environment variables override `config.env`. Restart after a change.

## Troubleshooting

| Problem | Fix |
|---|---|
| "The search model is incomplete" | One or more `model-part` zips were not extracted into the same place; extract all of them and run setup again |
| "Python 3.10 or newer was not found" | Install Python and tick "Add python.exe to PATH"; open a new window |
| Setup fails downloading packages | Company proxy: `set HTTPS_PROXY=http://proxy:port` before `setup.bat`, or use the offline packages zip |
| "Port 8765 already in use" | It is already running (open the browser), or change `EKA_PORT` |
| A PDF shows "Failed" | Open the library: the reason is shown (damaged file, password protected, …) |
| Scanned pages say "needs OCR" | Install Tesseract (step 4) and re-index |
| Search says "Keyword only" | The `models` folder is missing or moved; restore it from the zip |
| Anything else | Run `check.bat` and look at `data\eka.log` |

## Moving to another PC / backing up

Copy the whole folder except `.venv`, then run `setup.bat` on the new PC.
`data/` holds the library and conversations; deleting it starts fresh (the
PDFs in `pdfs/` are indexed again automatically).

## Phase 2

Connecting to the SharePoint list (PDFs and videos) is planned; see
`docs/PHASE2_SHAREPOINT.md` for the design and the information we need from IT.
