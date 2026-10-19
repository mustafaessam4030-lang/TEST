# ATLAS Assistant — Target Architecture (proposal, not implemented)

Status: **for review.** Nothing here is built. It extends what exists at `ba390cbc74` (see
`ATLAS_PROJECT_INTELLIGENCE_AUDIT.md`) and keeps its rules: deterministic code is the authority,
the model only phrases, actions come from the operator's own words, and a write counts only when
it's read back.

## 0. Principles

1. **Reuse before build.** Every capability below names the existing code it extends.
2. **Four request classes, four paths.** Explain, live state, research, execute. They are separated
   before any model is involved.
3. **Tools, not free text.** The model may *propose* only from a closed, typed tool list. Code
   validates and executes, checks the role, and audits it. No shell, SQL, URL or selector comes
   from model output.
4. **Evidence or it didn't happen.** Every answer carries its sources and freshness. "Done" is only
   said from a record such as a read-back verdict, a run ID or a job record, never from the model.
5. **Provider-replaceable.** Keep `intelligence/llm.Provider` (`health()`, `generate()`) as the
   only seam. `NullProvider` stays the default, and no paid service without approval.

## 1. Request routing

```
question ──► classify (deterministic first: assistant.detect_intent, atlas_skills.detect,
             atlas_po.detect, handbook.asks_how; model restatement only as a fallback)
   ├─ EXPLAIN   "what is ATA / how does the PO flow work / which carriers"   → Knowledge layer (§2)
   ├─ STATE     "where is X / why did X fail / what needs me"                → Runtime state (§2.2)
   ├─ RESEARCH  "look up X on the carrier now / any advisory for MSC"        → Research tools (§3)
   └─ EXECUTE   "re-run X / write it / open the task"                        → Execution tools (§4)
```

Reuse: `assistant._answer_core` already routes most of these. The new piece is an explicit
`request_class` field on every reply. It's computed before `converse` runs, and the UI and the
audit record both show it.

## 2. Project Knowledge Layer

### 2.1 Stable knowledge (versioned with the code)

| Source | Reuse | Missing piece |
|---|---|---|
| Curated topics | `intelligence/handbook.TOPICS` (27) | Every topic gets `sources=[file:symbol]` and `verified_at_commit`. A test fails when a cited symbol disappears |
| Facts read from code | `handbook._carriers`, `_held`, `_limits` | Widen into a **capability registry** (§2.3) |
| Guides | `handbook.GUIDES`, `search_guides` | Section-level `doc_version` / `superseded_by`. Mark the known stale sections (README "no language model", START_HERE §4 "no viewer role") so they're never quoted as current |
| Glossary and cause ranking | `intelligence/knowledge.py` | Add ETA/ATA *determination* entries that cite `arrival_events` rules |

Retrieval: there's **no embedding of the whole repo.** Answers come from a topic lookup, then a
scored section search over the guides (as today), and the reply quotes the `file#section` it used.
If retrieval later needs to grow, it stays a local index built at release time and stored with
its commit hash.

### 2.2 Runtime state (never mixed with stable docs)

| Question | Source | Freshness shown |
|---|---|---|
| Run, shipment, counters | `bridge.snapshot()` | Run ID, snapshot `version`, `updated_at` |
| Failures, work list | `intelligence/failures.py`, `backlog.py` | Record timestamp, run ID |
| Human tasks | `human_queue.json` via snapshot | Task state and waited time |
| Learned strategies | `intelligence/learning.py` | Sample size, last evaluation |
| PO jobs | `po/store.py` | Job ID, stage timestamps |
| Platform runs and workers | `controlplane/runs.py` | Heartbeat age |

Every reply gets `sources: [{kind, ref, as_of}]`. This extends today's `reply["sources"]`, which
holds bare names like `"bridge.snapshot"` or `"handbook"`. The UI shows "as of 10:42, run
20261019-…".

### 2.3 Capability registry (new, small, computed)

`intelligence/capabilities.py` would compute the table on request, never hand-maintained:

| Field | Computed from |
|---|---|
| Carriers automated / held / recognised only | `update_eta.PORTALS`, `AIRLINES`, `carrier_pacing.PRODUCTION_HOLD`, `OCEAN_WRITE` |
| Model | `llm.provider().health()` |
| Web search | `research.enabled()`, `why_off()` |
| DHL API | `dhl_api.enabled()` |
| OCR | Tesseract presence (`intelligence/vision.py`) |
| Control | `ControlChannel.enabled` |
| **Last live proof** | newest `worker/verify.py` observation per capability (REAL VERIFIED / BLOCKED / none) |

ATLAS then answers "can you track Lufthansa / search the web / write ocean ETAs?" with
*implemented / optional-off / held / not implemented*, plus the last live proof, if one exists.

## 3. Controlled carrier research

**Design decision:** the chat process **never drives a browser.** Today's rule (`atlas_skills`:
"A carrier TRACKING page is never opened from the chat"; `research.NEVER_FETCH`) stays. Research
goes to the **automation**, which already owns the adapters, pacing, holds, Human Action queue
and evidence capture.

```
ATLAS (chat)                         automation (runner / worker)
 research_shipment(ref) ── typed request via ControlChannel ──► between shipments:
   validate ref shape                                            carrier_provider(ref) → adapter
   resolve provider (carrier_provider)                           pacing/holds (carrier_pacing)
   role: runs.view? lookup.request                               get_provider_result(...)  READ ONLY
                                                                 capture: source URL, page text,
                                                                   screenshot, timestamps, fields
                                                                 arrival_events.decide/basic_check
 ◄──────────── lookup record {run_id, request_id, provider, fields, evidence_refs,
               validation, verdict: FOUND | NO_RESULT | NEEDS_PERSON | BLOCKED | ERROR}
```

| Step | Reuse | Missing piece |
|---|---|---|
| Identify carrier and identifier | `carrier_provider`, `hub_reference_kind`, `is_dhl_k_reference`, `airline_from_awb` | A pure `resolve(ref, carrier_name)` wrapper returning provider + reason |
| Select adapter | `get_provider_result`, `PORTALS` | — |
| Look up, **read only** | adapters | **New:** a per-request `lookup_only` path that runs the adapter and validation and stops before `update_internal_shipment`. `CT_DRY_RUN` is run-wide today |
| Capture evidence | failure screenshots and page text, `intelligence/evidence.py` (SHA-256), `<ref>_dhl_api.json` | Capture on success too, for lookup requests |
| Normalise and validate | `arrival_events.parse_date`, `classify`, `decide`, `basic_check` / `check_before_write` | Return the rule verdicts as data (they're log lines today) |
| Missing / conflicting / stale | `NeedsReview` reasons, `decide()` | Explicit `stale` flag (event older than N days vs `as_of`) |
| Human / CAPTCHA | `wait_for_human`, `human_queue`, Open & Continue | The lookup record carries `NEEDS_PERSON` + task ID |
| Hand over to automation | `ControlChannel.request("reprocess")`, `update_eta.py:12106` | Only after a FOUND + validation-PASS lookup and an operator's explicit "write it". Fix the page-scope drop (audit §7) |
| Verify result | `verify_saved_date`, `worker/verify.py` | Reply quotes the read-back verdict and run ID from the record |

**Not in scope, by rule:** new carrier selectors without a saved page to test against; any
CAPTCHA solving; paid APIs. The DHL API stays optional behind its free key, and only once that key
is confirmed to exist.

## 4. Reliable execution

### 4.1 Tool registry (closed)

| Tool | Class | Permission (rbac) | Executes via | Proof of result |
|---|---|---|---|---|
| `knowledge.answer` | explain | `atlas.chat` | handbook / knowledge | sources |
| `state.query` | state | `atlas.chat` | `RunData`, stores | snapshot version |
| `validate.dates` | analysis | `atlas.chat` | `atlas_skills.validate_*` | rule lines |
| `web.search` | research | `atlas.chat` | `research.investigate` | URLs returned this request |
| `carrier.lookup` (new) | research | `runs.start` (new `lookup.request` optional) | ControlChannel → runner `lookup_only` | lookup record + evidence |
| `shipment.rerun` | execute | `runs.start` | ControlChannel `reprocess` | run log line, CSV row, read-back verdict |
| `human.open` | execute | `human.act` | `/api/human` | task state |
| `run.pause/stop` | execute | `runs.stop` | ControlChannel | control state |

Rules:
- The model may name a tool and fill **typed** arguments (a reference that matches the reference
  patterns, an enum). Code validates the arguments again from the operator's original words. That's
  today's rule in `converse.py:456`, applied to every tool.
- Execute tools need an explicit confirmation turn ("Re-run 157-49568713? This writes to eHub")
  and are refused for Viewers. That's already enforced server-side in `controlplane/rbac.py`; the
  local dashboard needs the same check on `/api/ask` requests.
- Every tool call is audited: a new `atlas_actions.jsonl` locally, `controlplane/audit.py` on the
  platform. It records who, request class, tool, args, result record ID and answer text hash.
- The reply's success wording is generated from the result record (`brief._verified`-style), and
  `factguard` runs on it as today.

### 4.2 Honest failure reporting

Reuse `failures.from_record`, `knowledge.investigate_failure` and `backlog`. Every failed tool call
says which step failed, what was or wasn't written, and the next safe action. That's the existing
"diagnose-first plan" vocabulary.

## 5. Evaluation hooks

- Every reply has `{request_class, tool, sources, as_of, verdict_ref, latency_ms, model_used,
  guard_result}`. That's enough to score an evaluation set offline (see `ATLAS_ACCEPTANCE_TESTS.md`).
- Cost: local models only, so cost = latency and CPU/RAM. Measured by
  `autoconfig.bench` and logged per reply.

## 6. What stays out

Free-form shell or SQL; model-chosen URLs or selectors; the model deciding success; a hosted or
paid model without approval; any automatic change to production behaviour from learning
(proposals still need a person, `intelligence/review.py`).
