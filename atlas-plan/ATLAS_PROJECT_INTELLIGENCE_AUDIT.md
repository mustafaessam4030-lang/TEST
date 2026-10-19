# ATLAS — Project Intelligence Audit

**Audited:** commit `ba390cbc74` (`claude/atlas-potato-garden`, the stable demo copy), read-only,
19 Oct 2026. Paths are relative to `control-tower/`. Symbols are `file:function` or `file:CONSTANT`.

**Evidence levels used throughout**

| Level | Meaning |
|---|---|
| **TESTED-OFFLINE** | Exercised by the project's suite against local stand-ins (no eHub, no real carrier, no real model). I ran the suite: 71 suites, 4,594 checks passed. The 8 failures were all explained (see §9) |
| **VERIFIED-HERE** | I exercised it myself in this audit (Linux container, no Windows, no eHub, no carriers, no Ollama) |
| **CLAIMED** | The project's docs report it as measured or working on the Windows PC. **I could not verify it** |
| **OPTIONAL** | Implemented, but only active when a local service, key or setting exists |
| **NOT IMPLEMENTED** | No code exists for it |

Nothing below is described as working against eHub, a carrier or a model unless it carries a level that says so.

---

## 1. Architecture and component responsibilities

| Component | Files | Responsibility | Never does |
|---|---|---|---|
| Automation (runner) | `update_eta.py` (12,300 lines) `main()` | Hub sign-in, list "Under Clearance", route to carrier, read the carrier, validate, write COE ETA / BU ATA, read back, CSV row | — |
| ETA/ATA rules | `arrival_events.py` (`classify`, `decide`, `check_before_write`, `basic_check`, `parse_date`) | Decide which carrier event is the ETA/ATA; refuse ambiguous data (NEEDS_REVIEW) | Write anything |
| Pacing / holds | `carrier_pacing.py` (`PRODUCTION_HOLD = {'CMA_CGM'}`, `MAX_PER_RUN`, `RunLimits`) | Per-carrier caps, cooldown, production holds, run-wide Hub-write cap | — |
| Live state | `dashboard/bridge.py` (`ControlTowerState`, `bridge`) | In-memory run snapshot fed by the runner's hooks | Touch the browser |
| Dashboard server | `dashboard/server.py` | HTTP API (`/api/state`, `/api/ask`, `/api/stream`, `/api/control`, `/api/human`, `/api/report`, …), replay | Drive the automation except through `control.py` |
| Control channel | `dashboard/control.py` (`ControlChannel.request`, `take_reprocess`) | Pause / resume / stop / re-run requests, honoured by the runner **between shipments**. Off unless `DASHBOARD_ALLOW_CONTROL` or the supervisor | Act mid-write |
| Supervisor | `dashboard/supervisor.py` | Start/stop `update_eta.py` as a child; relay state and control files | Touch shipments |
| ATLAS chat (rules) | `dashboard/assistant.py` (`answer`, `_answer_rules`, `_answer_core`, `RunData`) + `atlas_intel.py`, `atlas_skills.py`, `atlas_learning.py`, `atlas_po.py`, `brief.py` | Answer from the snapshot and the stores; whitelisted UI actions | Write to the Hub, open a carrier page |
| ATLAS conversation (optional model) | `intelligence/converse.py`, `llm.py`, `factguard.py`, `autoconfig.py` | Restate the question, compose the reply in the operator's language, behind a deterministic guard | Decide a fact or take an action |
| ATLAS research (optional) | `intelligence/research.py` | Self-hosted SearXNG search + allow-listed official-page fetch, labelled WEB | Fetch carrier tracking pages (`NEVER_FETCH`) |
| ATLAS knowledge | `intelligence/handbook.py` (27 `TOPICS` + `GUIDES` search), `intelligence/knowledge.py` (glossary, ranked causes) | "How does it work?" answers and failure-cause ranking | Read the run |
| ATLAS learning | `ml/` (shadow), `intelligence/events.py`, `learning.py`, `failures.py`, `backlog.py`, `plans.py`, `evidence.py`, `maturity.py` | Telemetry, failure records, work list, proposals, evidence | Change production behaviour (proposals need a person) |
| Human Action queue | `human_queue.py`, `remote_session.py`, `dashboard/live_view.py` | Park CAPTCHA shipments, let an operator do the check in their browser, resume | Solve or read a code |
| Remote platform | `controlplane/` (`app.py`, `rbac.py`, `audit.py`, `runs.py`, `oidc.py`), `worker/agent.py` | Multi-user web app, roles, audit, runs on a Windows worker | Touch the Hub itself |
| PO Automation | `po/` | Bill Entry PDF → validate vs eHub → Excel template → Graph email after a named person authorises | Send without validation and authorisation |

## 2. The chat/assistant and its model configuration

**Dispatch** (`dashboard/assistant.py:answer`):
1. If a model is configured and the question isn't a profanity case or a code request, `intelligence/converse.answer` runs: UNDERSTAND (model restates in English, decides `needs_web`) → the rules answer the restatement → optional SEARCH → COMPOSE (model) → `factguard.check`. **Any failure falls back to the rules' answer.**
2. Otherwise `_answer_chat` → `_answer_rules` → `_answer_core` (skills → handbook → PO → glossary → failures/work list → learning → shipment/run intents), then `atlas_intel.enrich`, then `brief.concise` for short phrasing.
3. `_labelled` prefixes **TEST DATA** when `ATLAS_DEMO_TEST_DATA=1`.

**Action safety, as implemented:**
- `UI_ACTIONS = ("filter", "open", "page")` and `OPERATIONS = ("human_open",)` (`assistant.py:1432`, `:1975`) are the whole vocabulary. Everything else is stripped in `_answer_rules`.
- A re-run is returned as `{"request": {"action": "reprocess"}}` and goes through `ControlChannel.request` (`assistant.py:3651`).
- `converse.py:456`: "An action is only ever taken from the operator's own words … never from the model's restatement."
- `factguard.check` rejects model text containing any number, reference, URL, status or success word not present in the inputs.

**Model configuration** (`intelligence/llm.py`, `autoconfig.py`):

| Setting | Default | Notes |
|---|---|---|
| `ATLAS_LLM_PROVIDER` | `none` | `ollama` is the only real provider (`OllamaProvider`). `NullProvider` otherwise |
| `ATLAS_LLM_MODEL` | none (autoconfig picks `qwen3.5:4b`, or `qwen3:30b` with 30 GB+ RAM) | `autoconfig.auto_upgrade` can download the 30B in the background |
| `ATLAS_LLM_ALLOW_REMOTE` | `0` | Only localhost/private hosts are accepted |
| `ATLAS_AI` | on | `0` disables autoconfig |
| Timeout | 20 s (`llm.py`), 120 s in `atlas_demo.py` | — |

**Evidence:** routing, fallback and fact guard are **TESTED-OFFLINE** against stand-in Ollama servers (`test_converse.py`, `test_atlas_research.py`, `test_model_auto.py`, `test_model_bench.py`). Rules-only answering is **VERIFIED-HERE** (§8). Real-model latency (24–71 s median 38 s on 4 CPU cores, `START_HERE.md` §10b) is **CLAIMED**. `ATLAS_LOCAL_LLM_DESIGN.md` itself says the layer was "not yet run against a real Ollama or SearXNG install", so the docs disagree with each other here.

## 3. Carrier adapters: real supported operations

Routing: `update_eta.py:carrier_provider` (DHL K-reference → ocean carrier by name → 11-digit AWB prefix via `AIRLINES` → name fallback). Dispatch: `update_eta.py:get_provider_result`.

| Provider | Path | Operation | Write to Hub | Notes |
|---|---|---|---|---|
| DHL | `get_dhl_result` (page-state machine), `get_dhl_result_from_api` + `dhl_api.py` | Web tracking page. **OPTIONAL** official API when `DHL_API_KEY` is set | Yes, strict DHL rules (`arrival_events.check_before_write`) | API path unverified unless a key exists |
| Qatar Airways (157) | `get_qatar_result` | Web tracking page | Yes | — |
| AFKL (057/074) | `PORTALS['AFKL']`, navigation ladder, `check_afkl_flight_status` | AWB search; flight status card (filed as an **estimate** only) | Yes | Flight answers never become an ATA |
| 11 other airlines | `PORTALS` (Lufthansa, Saudia, Ethiopian, EgyptAir, SAA, Turkish, RwandAir, DHL Aviation, Virgin, Emirates, Astral) | Generic portal reader | Yes | — |
| 7 ocean carriers | `PORTALS` (CMA_CGM, MSC, GRIMALDI, COSCO, MAERSK, ONE, HAPAG) | Portal reader, `verify_identity`, `read_back_required=True` | Yes when `OCEAN_WRITE` (default on) | **CMA CGM is held** (`carrier_pacing.PRODUCTION_HOLD`). Grimaldi needs a person for every search (`needs_person`) |
| Other 80 airline prefixes | `AIRLINES` (94 known) | Recognised by name only | No | "not automated yet" (`describe_unsupported`) |

**Evidence:** all adapters are **TESTED-OFFLINE** against saved or stand-in pages (`test_automation.py`, `test_afkl_*`, `test_ocean.py`, `test_flight_status.py`, `test_dhl_*`, `test_nested_body.py`, …). **Live behaviour against the real sites is CLAIMED only.** Selectors were partly "read off a screenshot" (`START_HERE.md`, flight status), and the docs ask the operator to confirm them with `diagnose_flight_status.bat`.

## 4. Lookup → ETA/ATA → normalisation → validation → persistence

```
collect_supported_shipments (Hub table, route by prefix/name)
 → get_provider_result → adapter → result {eta, ata, *_source, tracking_status, …}
 → (ocean) validate_arrival_result, read_back_required
 → update_internal_shipment
     → arrival_events.check_before_write (DHL) | basic_check (others)   ← NEEDS_REVIEW on doubt
     → separate_eta_ata, read_hub_ata (ETA-only guard)
     → update_one_view(COE, ETA) / update_one_view(BU, ATA)
          fill_date_field → write_date_value → save_manage_page
          → verify_saved_date (VERIFY_AFTER_SAVE, default ON)          ← True / False / None
 → save_result → tracking_results.csv;  tower.shipment_finished → bridge
```

- **Dates:** `arrival_events.parse_date` accepts only one unambiguous full date (dd/mm/yyyy out). Two-digit years and mm/dd-looking values are refused.
- **ETA vs ATA:** an ATA is never taken from an estimate, a delivery, or an expired ETA. Conflicting actual arrivals go to NEEDS_REVIEW (`arrival_events.decide`). The Hub's existing ATA is never overwritten (`update_one_view`).
- **Persistence proof:** `verify_saved_date` is three-valued. A mismatch raises. `None` (couldn't read) is not counted as a success. Ocean writes *require* verification.
- **Outcomes:** SUCCESS, PARTIAL (COE saved, BU failed, `error.actions`), SKIPPED, FAILED, NEEDS_REVIEW, HUMAN_* states.

**Evidence:** **TESTED-OFFLINE** (`test_arrival_events.py` with 168 checks including the 12-Oct regression, `test_ata_field.py`, `test_verification.py`, `test_run_limits.py`). The offline ETA/ATA evaluation is `tools/evaluate_eta_diagnostic.py` against `fixtures/eta_ata_truth.json`. **A real Hub write and read-back is CLAIMED.** The proof tool exists (`worker/verify.py:prove_eta`, `verify_eta.bat`) but I could not run it.

## 5. Automation entry points and human-in-the-loop

| Entry point | Starts |
|---|---|
| `python update_eta.py` / `START_SHARED.bat` | One run; dashboard lives inside it |
| `START_TOWER.bat` → `dashboard/supervisor.py` | Dashboard always up; Start / Pause / Stop / Re-run |
| `python -m worker` (`worker/agent.py`) | Remote platform worker; runs `update_eta.py` via the supervisor with `CT_RUN_ID` |
| `python -m worker.verify ehub / ata-read / eta / carrier` | Read-only eHub checks; `eta` = one shipment through the real write path |
| `START_ATLAS_DEMO.bat` → `atlas_demo.py` | Dashboard + ATLAS over a replayed real run (`--replay`) or labelled `--test-data`. **Never starts the automation** |
| `run_dashboard.bat`, `share_dashboard.bat` | Review a finished run (`server.replay`) |

**Human-in-the-loop:** `wait_for_human()` → after `HUMAN_QUEUE_GRACE_MS` (30 s) the shipment is parked in `human_queue.py` (`WAITING_FOR_HUMAN` → … → `SUCCESS` / `TIMEOUT` / `HUMAN_SESSION_LOST` / `VERIFICATION_NOT_CONFIRMED` / `FAILED`). Open & Continue (`/api/human`, `control.validate_human_request`) brings the tab forward. The run re-reads the page twice, extracts, validates, writes and reads back. A completed verification is **not** carrier access (`CARRIER_ACCESS_CONFIRMED` only once the shipment page is read). **TESTED-OFFLINE** (`test_human_loop.py`, `test_human_queue.py`, `test_captcha.py`, `test_carrier_access*.py`, `test_remote_session.py`).

## 6. Logging, audit, run IDs, verification

| Record | Where | Written by |
|---|---|---|
| Run ID | `update_eta.RUN_ID` = `YYYYmmdd-HHMMSS-<6 hex>`, or `CT_RUN_ID` from the control plane (validated by regex) | runner |
| Run log | `logs/run_*.log` via `write_log` (`redact_secrets` on the way in) | runner |
| Results | `tracking_results.csv` (`save_result`) | runner |
| Human events | `logs/human_actions.jsonl`, `logs/human_action.json`, `logs/human_queue.json` | runner |
| Telemetry | `ml/data/telemetry.jsonl` (episodes with verdicts) | runner |
| ATLAS events / evidence / work list | `ml/data/intelligence/events.jsonl`, `evidence.jsonl` (SHA-256), `recovery_backlog.json`; origin-labelled (`intelligence/store.py`) | bridge, server |
| Carrier evidence | page text and screenshots on failure; `<ref>_dhl_api.json` | runner |
| Audit (local accounts) | `dashboard/accounts.py` | server |
| Audit (platform) | `controlplane/audit.py` (PostgreSQL/SQLite, secret scrubbing, closed action list) | control plane |
| Real-eHub proof | `worker/verify.py` JSON + `[VERIFY]` line + control-plane report | worker |

**Gap:** a chat question and the answer ATLAS gave aren't kept as an auditable record with the evidence it used. Only the intent label (when `learning=True`) and explicit feedback are stored (`dashboard/feedback.py`).

## 7. Documentation and known limitations

- **Stale statements:** `README.md` ("The chatbot … has no language model, deliberately") and `START_HERE.md` §3–4 ("no viewer-only role") contradict later work. Both are in `handbook.GUIDES`, so **ATLAS can quote these stale sections to the team.**
- **Contradicting docs:** model latency in `START_HERE.md` vs "not yet run against a real Ollama" in `ATLAS_LOCAL_LLM_DESIGN.md`.
- **Misleading log message:** `update_eta.py:12106-12116` says a re-run for a shipment on another Hub page "will be picked up when its page is reached". But `ControlChannel.take_reprocess` has already dropped the request, and an already-processed shipment won't be re-run.
- **Replay crash (fixed on `claude/atlas-demo-stabilize`, not yet pushed):** `server.replay` raised `NameError: LOG_LINE` on the stable commit, which takes down `START_ATLAS_DEMO.bat` (default `--replay`), `run_dashboard.bat` and `share_dashboard.bat` whenever a run log exists. **VERIFIED-HERE.**
- **ATLAS wording seen in the labelled test-data demo (VERIFIED-HERE):** "I wrote it to the Hub" for a seeded record with no read-back. For 8842001173 it says "stopped at looking the shipment up on the carrier site", although the recorded reason is a Hub Manage-page error.
- `update_eta.py` and `assistant.py` are very large single files, so every change there carries regression risk.

## 8. Capability matrix

| Capability | Status | Evidence |
|---|---|---|
| Answer run, shipment and failure questions from records | Implemented | TESTED-OFFLINE. VERIFIED-HERE (rules only, `--test-data`: refuses an unknown shipment and a destination it never reads, labels TEST DATA) |
| "How does it work?" (handbook + guides) | Implemented | TESTED-OFFLINE (`test_atlas_skills.py`). Stale guide risk (§7) |
| Natural language / Arabic via local model | OPTIONAL | Stand-ins only. Real model CLAIMED |
| Fact guard on model output | Implemented | TESTED-OFFLINE |
| Web research (SearXNG) | OPTIONAL | Stand-ins only. Not installed here |
| Validate dates, PDF report, screenshots | Implemented | TESTED-OFFLINE |
| Carrier lookup **from the chat** | **NOT IMPLEMENTED (forbidden by design)** | `atlas_skills.py` docstring; `research.NEVER_FETCH` |
| Re-run one shipment from the chat | Implemented, control-gated | `assistant.py:3651` → `control.py` → `update_eta.py:12106`; page-scope limitation (§7) |
| Read-only single-shipment lookup (no write) | **NOT IMPLEMENTED** | `CT_DRY_RUN` is run-wide; `prove_eta` writes |
| Real carrier reads | Implemented | TESTED-OFFLINE. Live CLAIMED |
| Hub write + read-back | Implemented | TESTED-OFFLINE. Live CLAIMED; proof tool `verify_eta.bat` |
| Human Action queue | Implemented | TESTED-OFFLINE |
| Audit of ATLAS answers with sources | **NOT IMPLEMENTED** | — |
| Capability/health self-report from code | Partial | `handbook._carriers`, `_held`, `_limits` read the code; `/api/atlas/llm` health; no single registry |
| Evaluation with metrics (accuracy, false success, latency) | Partial | `atlas_demo_acceptance.py` (A–F), `autoconfig.bench`, `tools/evaluate_eta_diagnostic.py`, `test_atlas_understanding.py` (31 phrasings) |

## 9. Test baseline (VERIFIED-HERE)

- Stable `ba390cb`, full suite: **71 suites, 4,594 passed, 8 failed, 7 skipped.**
  - 3 failures in `test_atlas_skills.py`: `run_tests.py` sets `ATLAS_SEARCH=0` and the suite didn't clear it. Proven: the suite passes 80/80 standalone and fails the same 3 checks with `ATLAS_SEARCH=0`.
  - 5 failures in `test_logging.py`: the suite drops to `nobody`, which couldn't read my root-only scratch folder (`ModuleNotFoundError: carrier_pacing`). Proven: 29/29 when re-run from a world-readable copy.
  - 7 skips, each with a stated precondition: Tesseract ×4, PostgreSQL ×1, real eHub ×1 (`ATA_REAL_EHUB_TESTS`), not a Git checkout ×1.
- Fix branch `claude/atlas-demo-stabilize`: results are in the stabilization report.
