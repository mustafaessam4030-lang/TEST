# Luma (Mantrac Engineering Intelligence Assistant) — start here

Ask questions about your engineering PDFs and get answers quoted from them,
with the exact document and page for every statement. Everything runs on
your computer: no cloud service, no Snowflake, and no AI account is needed.

## What is in this folder

| Item | What it is |
|---|---|
| `setup.bat` / `setup.sh` | One-time install: creates `.venv`, installs the packages, downloads the search model |
| `start.bat` / `start.sh` | Starts the assistant for this computer only and opens it in the browser |
| `share.bat` / `share.sh` | Starts it **for the team**: prints a link colleagues open in their browsers |
| `stop.bat` / `stop.sh` | Stops it |
| `check.bat` / `check.sh` | Checks the installation and lists what is missing |
| `run-tests.bat` / `run-tests.sh` | Runs the automated tests |
| `config.env` | Settings (port, PDF folder, OCR, optional local AI model) |
| `pdfs/` | **Put your PDFs here.** They are indexed automatically |
| `models/` | The search model (bge-small, 65 MB), downloaded here by setup |
| `data/` | Created on first run: the library, index, conversations and log |
| `samples/` | Synthetic test PDFs (not real equipment data) |
| `eka/` | The application |
| `docs/` | Architecture, evaluation, UI notes, phase 2 (SharePoint) plan |

## Requirements

* Windows 10/11, Linux or macOS, 64-bit, about 1 GB free disk and 2 GB RAM.
* **Python 3.10 – 3.14.** On Windows install it from <https://www.python.org/downloads/>
  and tick **"Add python.exe to PATH"**.
* Internet access during setup only: about 70 MB of Python packages and the
  65 MB search model. After setup it runs without internet.
* Optional: **Tesseract OCR** to read scanned PDFs (see step 4).

## Windows

1. Right-click the zip → **Extract All…** (for example to `C:\Apps`). Do not run
   anything from inside the zip.
2. Open the `EngineeringKnowledgeAssistant` folder and double-click **`setup.bat`**.
   It takes 2–5 minutes and ends with a checklist; every line should say `OK`
   (a `WARN` for OCR is fine).
3. Copy your PDFs into the **`pdfs`** folder (sub-folders are fine).
4. Double-click **`start.bat`**. The browser opens at <http://127.0.0.1:8765>.
   Keep the black window open while you use the assistant.
5. Ask a question. Open **Document library** to see each PDF's status;
   new PDFs appear within about 15 seconds, or press **Check folder now**.
6. To stop: close the black window, or double-click **`stop.bat`**.

## Share with your team (one link for everyone)

One computer runs Luma; colleagues only need a browser. Nothing is installed on their PCs.

1. On the computer that will host it (a PC that stays on, on the company network), do steps 1–3 above.
2. Double-click **`share.bat`** (Linux/macOS: `./share.sh`). The window prints the team link, for example:

   ```
   Luma team link (copy one into any browser on the company network):
     http://ENG-PC-07:8765/#key=janreZV3agHY
     http://10.20.30.40:8765/#key=janreZV3agHY
   ```

   The same text is saved in `data\team-link.txt`.
3. Send one of those links to the team (email, Teams). They paste it into Chrome or Edge — that's all.
   If the name link does not open on their PC, use the number (IP) link.
4. The first time, Windows may ask whether Python may use the network: choose **Private networks → Allow**.
   If colleagues still cannot connect, IT needs to allow incoming connections on port 8765 on that PC.
5. Keep the window open while the team is testing. Close it (or run `stop.bat`) to stop sharing.

Good to know:

* **The key.** The part after `#key=` is the team key. Anyone opening the address without it is asked for it.
  It is created once and kept in `data\team-key.txt`; delete that file to make a new key (and a new link), or set
  your own with `EKA_API_TOKEN=` in `config.env`. The key is never sent in a way that ends up in the logs.
* **PDFs.** Add them to the host's `pdfs` folder (or point `EKA_INBOX_DIR` at a shared network folder). Teammates
  can also upload PDFs from the page; every upload goes into the one shared library.
* **Each person's conversations stay in their own list** (kept per browser). This separates people; it is not a
  login, so do not rely on it for confidential questions.
* **Outside the office network.** The link works on the company network (or VPN). Do not publish it on the internet
  with a public tunnel service: the documents are confidential. Ask IT for VPN access or an internal web address.
* Answers use the host's model and hardware. With a local language model, several people asking at the same time
  may wait for each other; quoted answers (the default) stay fast.

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

1. Install Ollama from <https://ollama.com> and pull an approved model, e.g. `ollama pull qwen2.5:7b-instruct`
   (a 4.7 GB download; 8 GB RAM recommended; on a laptop CPU expect 10–40 s per answer).
   The integration was tested with the smaller `qwen2.5:3b-instruct` (1.9 GB); see docs/LUMA.md.
2. In `config.env` set `EKA_GENERATOR=ollama`, then restart.

If the model is not running, the assistant falls back to quoted answers.

## PC without internet

Setup needs internet once. For a PC that has none, ask for the offline
package: it carries the Python packages and the model so setup works offline.

## Settings (`config.env`)

| Setting | Default | Meaning |
|---|---|---|
| `EKA_PORT` | 8765 | Browser address port |
| `EKA_HOST` | 127.0.0.1 | Only this computer. `share.bat` uses the network instead, with a team key |
| `EKA_OPEN_BROWSER` | 1 | Open the browser on start |
| `EKA_INBOX_DIR` | pdfs | PDF folder (`off` disables it) |
| `EKA_INBOX_SCAN_S` | 15 | Seconds between folder checks |
| `EKA_INBOX_REMOVE_DELETED` | 0 | 1 = remove documents whose PDF was deleted |
| `EKA_DATA_DIR` | data | Library, index, conversations, log |
| `EKA_EMBEDDER` | auto | Semantic search with the model in `models/`; keyword-only if it is missing |
| `EKA_GENERATOR` | extractive | `ollama` for phrased answers (see above) |
| `EKA_TESSERACT_CMD` | (auto) | Full path to `tesseract.exe` |
| `EKA_API_TOKEN` | (none) | Require this key; `share.bat` uses it as the team key (or creates one) |

Real environment variables override `config.env`. Restart after a change.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Python 3.10 or newer was not found" | Install Python and tick "Add python.exe to PATH"; open a new window |
| Setup says "semantic search could not be installed" (e.g. Python 3.15) | Luma still works, with keyword search. For better search install Python 3.13, delete `.venv`, run `setup.bat` again |
| `start.bat` says `No module named 'uvicorn'` | Setup did not finish. In this folder run `.venv\Scripts\python.exe -m pip install -r requirements.txt`, then `start.bat` |
| Setup fails downloading packages or the model | Company proxy: run `set HTTPS_PROXY=http://proxy:port`, then `setup.bat` in the same window |
| "Port 8765 already in use" | It is already running (open the browser), or change `EKA_PORT` |
| Colleagues cannot open the team link | Same network or VPN? Try the IP link; allow Python in Windows Firewall (Private networks) or ask IT to open port 8765 |
| Asked for a "team key" | Paste the part after `#key=` from the link (also in `data\team-link.txt`) |
| A PDF shows "Failed" | Open the library: the reason is shown (damaged file, password protected, …) |
| Scanned pages say "needs OCR" | Install Tesseract (step 4) and re-index |
| Search says "Keyword only" | The search model was not downloaded: run `setup.bat` again |
| Anything else | Run `check.bat` and look at `data\eka.log` |

## Moving to another PC / backing up

Copy the whole folder except `.venv`, then run `setup.bat` on the new PC.
`data/` holds the library and conversations; deleting it starts fresh (the
PDFs in `pdfs/` are indexed again automatically).

## Phase 2

Connecting to the SharePoint list (PDFs and videos) is planned; see
`docs/PHASE2_SHAREPOINT.md` for the design and the information we need from IT.
