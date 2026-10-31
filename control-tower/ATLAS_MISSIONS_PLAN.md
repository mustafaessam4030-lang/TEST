# ATLAS — Multi-Provider Intelligence, Deep Mission Mode and Emergency Mode

**Status: PLAN ONLY (30 Oct 2026).** No production code changed, no paid model called,
no Snowflake setting changed, no live automation run. Every account fact below was read
with read-only metadata queries (SHOW / ACCOUNT_USAGE) on 30 Oct 2026; anything that could
not be checked from here is marked **TO VERIFY**.

---

## Build status (30 Oct 2026, commit 06c57c9 and after)

Built, tested (test_missions.py, 53 checks), **not yet verified on the Windows server**:

| Phase | Status |
|---|---|
| P1 Ledger + registries | Done for missions: `intelligence/missions/models.py` (registry with live health, router, `model_invocations.jsonl`). The existing chat's own Cortex chain is unchanged; the router is not yet applied to it. |
| P2 Missions core | Done: `missions.db` store, task graph runner, workload manager (workers, priority with ageing, backpressure, pause/resume/cancel, retry once, restart recovery), evidence vault with classes and provenance, classifier and the ask-before-escalating proposal (chat offer + Missions page), declines remembered 24 h. |
| P3 Commander + specialists | Done: six templates sharing one 10-task graph; specialists for records, pattern, step owner + knowledge, carrier, risk, hypotheses decided by evidence, independent verification (recompute, freshness, contradictions, grade), independent review by a different model family, next steps (never executed), report checked by factguard. |
| P4 Mission Control | Done: **Missions** in the hub sidebar (Insight): list, proposal card, live task graph, evidence, hypotheses, conflicts, usage, report, timeline. |
| P5 Emergency Mode | Done: CRITICAL from record impact only; automatic incidents are **proposed, never started**; manual activation by an Admin; resource profiles (normal, deep, emergency, drill); duplicate incidents merged; escalation suggested from a running mission. |
| P6 Approved actions | Partly: next steps are classed (READ ONLY / PREPARATION / APPROVAL REQUIRED) and link to the page that owns them; nothing is executed by a mission. Handing an approved step into the Human Action queue automatically is not built. |
| P7 Advanced | Not started (comparison, what-if, replay, handoff, collaboration, scheduled analysis, analytics). |

**Safe defaults standing in for the seven decisions (change any of them):**
1. Data residency: Cortex only for T0/T1 (shipment refs, statuses, dates), as already approved for ATLAS's wording on 27 Oct; never T2/T3.
2. PUBLIC's Cortex grant: unchanged (an admin decision in Snowflake).
3. Opus: not registered, so never used.
4. Budgets: 80,000 tokens per mission, 150,000 per critical incident, within ATLAS's existing 500,000 a day.
5. Approvers: Operator/Admin for Advanced and Deep (`missions.run`), Admin for Critical (`missions.emergency`).
6. Automatic Emergency activation: propose only.
7. Cortex on the server: whatever `python -m intelligence.cortex status` says; without it, missions run on ATLAS's own code and the local model.

---

## 0. In one page

- **Build on what exists.** ATLAS already has a Cortex-first / Ollama-fallback provider chain
  with a daily token budget, a 15-minute answer cache and per-purpose token counts
  (`intelligence/llm.py`, `intelligence/cortex.py`); a microsecond question router
  (`intelligence/router.py`); a model gate (`converse.py`: slots, queue, backpressure); an
  evidence store with SHA-256 provenance (`evidence.py`); one verification vocabulary
  (`verification.py`: TEST / SIMULATED / REAL OBSERVED / REAL VERIFIED / BLOCKED); failure
  root cause marked VERIFIED / INFERRED / UNKNOWN (`failures.py`); a resolution backlog that
  closes only on read-back (`backlog.py`); governed lessons that a reviewer must approve
  (`kb_lessons.py`, `kb_govern.py`); `factguard` (a model may not add a number, reference,
  link or success claim). Missions are a new **orchestration layer on top of these**, not
  a rewrite.
- **The rule that does not change.** Facts, numbers and outcomes come from records, tools
  and deterministic code. Models plan, extract, classify, explain and critique. A model's
  output is a *hypothesis* until evidence or deterministic validation confirms it.
- **The Mission Commander is code, not a chatbot.** A deterministic orchestrator (DAG,
  queue, budgets, gates) that may *ask* a model for a draft plan or a critique, and
  validates every draft against the registries before anything runs.
- **Ask before escalating, every time.** ATLAS proposes a mission (why, plan, agents,
  models, websites, cost, limits); the user chooses *Continue normally / Enter Deep
  Mission / Review the plan*. Silence is never consent; a "no" is remembered for that task.
- **Snowflake reality (verified):** account FY56654, Azure West Europe, cross-region
  inference to **AWS US**; Claude Sonnet 4.5 / 4.6 / 5 are allowed; **Claude Opus 4.5 / 4.6
  exist in the account but are not on the allowlist**; Cortex rights are granted to
  **PUBLIC**; in the last 30 days the Cortex REST API served **only llama3.1-8b** (190
  requests) and **no Claude traffic** — ATLAS's Claude integration is built but appears
  not to be in use on the server (TO VERIFY).
- **Seven owner decisions** are needed before Phase 2 (section 13).

---

## 1. What exists today (audit)

| Area | Where | What it already does | What missions add |
|---|---|---|---|
| Providers | `intelligence/llm.py` | `OllamaProvider`, `NullProvider`, `ChainProvider(Cortex → local)`; localhost-only unless `ATLAS_LLM_ALLOW_REMOTE` | A registry of many models; routing per task instead of one chain |
| Cortex | `intelligence/cortex.py` | REST `/api/v2/cortex/v1/chat/completions`, PAT in Windows Credential Manager, default `claude-sonnet-4-5`, 30 s timeout, **500,000 tokens/day** budget, 900 s cache, cool-down after failure, usage per purpose | Per-mission and per-user budgets; model choice per task |
| Local model | `intelligence/autoconfig.py` | `qwen3.5:4b` by default; `qwen3:30b` auto-pulled when the PC has 30 GB+ RAM; falls back after 3 timeouts | Health/latency feed into the router |
| Concurrency | `intelligence/converse.py` | `ATLAS_LLM_SLOTS / QUEUE / WAIT_S` gate; Cortex slots 4; measured: 4 CPU cores, 1 local answer at a time | Becomes one of the Workload Manager's semaphores |
| Question routing | `intelligence/router.py` | operational / troubleshooting / atlas_rules / action / calculation / knowledge / scenario / … | Mission classifier sits after it |
| Hub tools | `dashboard/atlas_tools.py` | grounded platform answers, numbers only from tool results, role/BU scoped, read-only | Tools become specialist capabilities |
| Timing | `intelligence/trace.py` | per-request spans, model wall time and tokens (memory only) | Persistent invocation ledger (section 9) |
| Evidence | `intelligence/evidence.py` | real images only, SHA-256, never verification screens | Evidence Vault generalises to every claim type |
| Verification | `intelligence/verification.py` | one vocabulary for "how real is this" | Reused as the vault's `verification_status` |
| Failures | `intelligence/failures.py`, `plans.py`, `backlog.py` | root cause VERIFIED/INFERRED/UNKNOWN; plans from policy + verified history; resolved only by read-back | Hypothesis engine and Resolution plan reuse them |
| Learning | `kb_lessons.py`, `kb_govern.py`, `review.py` | lessons never become rules without a reviewer | Mission lessons go the same way |
| Safety | `factguard.py`, `store.py` | rejects invented facts; redacts secrets; refuses secret-bearing fields | Applied to every model output and every log line |
| Human actions | run page / To Do | pause → person → resume; idempotency; read-back | Missions never bypass it: actions are *proposals* into it |

---

## 2. Verified Snowflake facts (read-only, 30 Oct 2026)

| Fact | Value | Why it matters |
|---|---|---|
| Account / region | FY56654 · `AZURE_WESTEUROPE` | Home region of the data |
| `CORTEX_ENABLED_CROSS_REGION` | `AWS_US` | Prompts may be processed in the US: a **data-residency decision** (section 7) |
| `CORTEX_MODELS_ALLOWLIST` | claude-sonnet-5, claude-sonnet-4-6, claude-sonnet-4-5, llama3.1-8b/70b, llama3.2-1b/3b/11b/90b, llama3.3-70b, mistral-7b, mixtral-8x7b, gemma-7b, snowflake-arctic, reka-flash, jamba-1.5-mini, codestral-22b | What ATLAS may call today |
| Models present (`SHOW MODELS IN SNOWFLAKE.MODELS`) | adds **claude-opus-4-5, claude-opus-4-6**; embeddings: snowflake-arctic-embed (m, m-v1.5, l-v2.0, l-v2.0-8k), e5-base/large-v2, multilingual-e5-large, voyage-multilingual-2, voyage-multimodal-3, nv-embed-qa-4, twelvelabs-marengo-embed-3-0 | Opus needs an allowlist change (owner decision); embeddings could replace local ones |
| `SNOWFLAKE.CORTEX_USER` granted to | ACCOUNTADMIN, SYSADMIN, **PUBLIC**, SVC_CORTEX_R (×2), CORTEX_LIMITED_ROLE, DATA_ENGINEER, AXIANS_R, CUSTOM_ADMIN_ROLE, STREAMLIT_ACC, SF_MAN_* | **Every role can use Cortex** (via PUBLIC): no least privilege today |
| `CORTEX_MCP_SERVER_FORCE_SQL_EXEC_READ_ONLY` | `false` | An MCP SQL tool could write; ATLAS must not rely on it being read-only |
| `STATEMENT_TIMEOUT_IN_SECONDS` | 3600 | Upper bound for any SQL a mission runs |
| Cortex REST usage, last 30 days | **llama3.1-8b only**: 190 requests, 194,824 tokens (115,295 input, 70,176 cached input, 9,353 output), 9 users, 8–29 Oct | **No Claude via REST**: ATLAS's default `claude-sonnet-4-5` is not being called (TO VERIFY on the server: `python -m intelligence.cortex status`) |

**TO VERIFY on the tower server** (cannot be reached from here): `ollama list` (which local
models are really installed), RAM / GPU, the Cortex PAT's user and role (expected:
SVC_CORTEX_R or CORTEX_LIMITED_ROLE), whether a network policy binds the PAT to the
server's IP, `python -m intelligence.cortex status` (today's calls).

**Pricing.** Cortex bills credits per million tokens per model, published in Snowflake's
*Service Consumption Table*; it changes, so the plan does not copy numbers. The cost model
(section 8) takes the per-model rate as configuration and is reconciled monthly against
`ACCOUNT_USAGE.CORTEX_REST_API_USAGE_HISTORY` (tokens, per model, per user; observed
columns: tokens, tokens_granular {input, output, cache_read_input}, inference_region,
query_tag).

---

## 3. Provider comparison

| Provider · model | Where it runs | Strengths for ATLAS | Limits | Proposed role |
|---|---|---|---|---|
| Ollama `qwen3.5:4b` | Tower server CPU | Free, private, always there, vision | Weak multi-step reasoning; 1 answer at a time on 4 cores | Classification, extraction, wording fallback, pictures, drills |
| Ollama `qwen3:30b` | Tower server, 30 GB+ RAM | Better reasoning, still private | Slow on CPU; memory pressure next to Chrome automation | Only when the resource profile allows; never 2 large local models at once |
| Cortex `claude-sonnet-4-5 / 4-6 / 5` | Snowflake, **AWS US** (cross-region) | Best planning, reasoning, critique, long context | Billed; data leaves EU region; 30 s timeout today | Planner, Commander's critique, Verification reviewer, report |
| Cortex `claude-opus-4-5 / 4-6` | Snowflake | Deepest reasoning | **Not allowlisted**; highest cost | Only if the owner allowlists it, for SEVERE incidents |
| Cortex `llama3.3-70b`, `llama3.1-70b` | Snowflake (region TO VERIFY) | Independent second opinion (different model family); cheaper than Claude | Weaker than Claude | **Independent cross-check** model (not the same family as the planner) |
| Cortex `llama3.1-8b`, `mistral-7b`, `jamba-1.5-mini` | Snowflake | Cheap, fast | Shallow | Bulk classification / extraction when the local model is busy |
| Cortex embeddings (`snowflake-arctic-embed-l-v2.0`, `voyage-multilingual-2`, `multilingual-e5-large`) | Snowflake | Arabic + English retrieval quality | Billed per token | Optional upgrade of `kb_embed.py` (Phase 7) |
| Deterministic code | Tower | Exact, free, auditable | Only what was programmed | **The authority**: numbers, statuses, calculations, verification |

No other provider is proposed: none is configured or approved.

---

## 4. Architecture

```
 user ──► Hub chat / Mission Control
            │
            ▼
   router.py (exists) ──► Mission Classifier ──► NORMAL ──► today's ATLAS, unchanged
                               │ ADVANCED / DEEP / CRITICAL
                               ▼
                     Escalation Proposal  ──(user: Continue · Enter · Review)──► consent record
                               │ approved
                               ▼
   ┌──────────────────── Mission Commander (deterministic) ────────────────────┐
   │ mission spec → DAG of tasks → assign specialist + model + tool + site      │
   │ budgets · deadlines · replanning · conflict handling · sufficiency gate    │
   └───────┬───────────────┬──────────────────┬───────────────────┬────────────┘
           │               │                  │                   │
   Specialist Registry  Model Router     Website Registry    Workload Manager
   (roles, tools,       (capability      (operations, auth,  (persistent queue,
    scopes, schemas)     registry,        limits, health,     priorities, semaphores
                         scoring,         human-needed)       per model/site/CPU,
                         budgets)                             backpressure, recovery)
           │               │                  │                   │
           └──────► Task execution: existing tools · carriers · records · models
                               │
                               ▼
        Evidence Vault ──► Hypothesis Engine ──► Verification Gates ──► Report
        (provenance,         (ranked, tested,      (recompute, re-read     │
         classes,             never "confirmed"     sources, independent   ▼
         contradictions)      without evidence)     review, sufficiency)  Proposed actions
                                                                           │
                                                 existing Human-Action queue / To Do,
                                                 idempotency, audit, read-back (unchanged)
```

### 4.1 Mission Classifier and "ask before escalating"
- Input: `router.route()` result, the question, page context, records touched.
- Signals (each 0–3, weights in config): operations count, websites/systems count, need
  for cross-source verification, data volume, dependencies, domain breadth, ambiguity,
  business impact (from records: shipment criticality, SLA exposure, past-due days,
  clearance risk), time sensitivity, need for independent verification.
- Output: `NORMAL | ADVANCED | DEEP_MISSION | CRITICAL_INCIDENT` + the reasons, as text.
  Complexity alone never makes CRITICAL; CRITICAL needs a **business-impact** signal
  from records.
- Proposal card in the chat: why, the plan (tasks), specialists, models, websites, an
  estimate (tokens/credits range, expected time), known limits. Buttons: *Continue
  normally*, *Enter Deep Mission*, *Review the plan*. Consent recorded (user, time,
  plan hash). A decline is stored against the task fingerprint (question intent +
  identifiers) for 24 h: ATLAS does not ask again for the same task.
- Role check: only Operator/Admin may approve DEEP; CRITICAL needs Admin or an approver
  role (configurable).

### 4.2 Mission Commander (deterministic orchestrator)
- Builds a **mission spec**: objective, scope (shipments, BUs, sites), constraints,
  deadline, budget, resource profile, action ceiling (READ_ONLY by default).
- Asks the planner model for a *draft* DAG in a strict JSON schema; validates it: every
  task's specialist, tool, site and model must exist in the registries and be permitted
  for the user's role/BU; unknown ones are rejected, not improvised. Templates (section
  12) are preferred over free planning.
- Runs the DAG through the Workload Manager; collects each task's output as **separate
  findings** (no specialist sees another's conclusion before consolidation).
- Challenges: a finding without evidence IDs is sent back (bounded: max 2 rounds per
  task); contradictions open a "conflict" task; the **sufficiency gate** decides answer /
  investigate more / say "insufficient evidence".
- Replans when evidence changes the picture (new hypothesis, site down, auth expired),
  keeping completed evidence.

### 4.3 Specialist Registry
A specialist = configuration, not new code: `id, purpose, allowed_tools[], data_scopes[],
allowed_sites[], required_capabilities[] (e.g. reasoning, extraction, logistics,
code), output_schema, max_tokens, timeout, may_propose_actions (bool)`.
Initial roles: Logistics Intelligence, Shipment Intelligence, Carrier Intelligence, Web
Investigation, Data Analyst, Automation Engineer, Research, **Independent Verification**,
Risk Analyst, Report Generator (+ Incident Commander = the Commander in CRITICAL).
Only the roles a mission's DAG needs are instantiated. New roles are registered as a
config entry + prompt template + schema; the engine does not change.

### 4.4 Model Capability Registry and Router
Registry row: `model_id, provider (ollama|cortex), location (local|snowflake:region),
capabilities {planning, reasoning, extraction, classification, logistics, code, review,
vision, embed}, context_window (known/unknown), ram_gb/vram_gb (local), p50/p95 latency
(measured), health (from health() + failures), cost_per_mtok (config), data_tiers_allowed,
fallbacks[]`.

Routing: filter by **capability** and **data tier** (hard rules), then by **health** and
**budget** (hard), then score: `fit × quality_weight − latency × urgency − cost ×
budget_pressure`, with a preference for the local model when it is good enough. Parallel
calls only for independent DAG nodes, and only when the profile's semaphores allow.

| Task type | Primary | Fallback | Never |
|---|---|---|---|
| Plan a mission (draft DAG) | Claude Sonnet (Cortex) | template only (no model) | local 4B |
| Classify / route / extract fields | local qwen | Cortex llama3.1-8b | Claude (waste) |
| Logistics explanation (general knowledge) | KB + local qwen | Claude Sonnet | — |
| Root-cause reasoning over evidence | Claude Sonnet | qwen3:30b if profile allows | 4B alone |
| Independent review | a **different family** from the one that drafted (llama3.3-70b if Claude drafted) + deterministic recompute | deterministic checks only, flagged | same model reviewing itself |
| Numbers, dates, statuses, totals | **deterministic code** | — | any model |
| Report wording | Claude Sonnet | local qwen | — |
| Pictures | local vision model | — | Cortex (already refused in `cortex.py`) |

### 4.5 Website Capability Registry
Built from what the carrier modules already know (`carrier_profile`, airlines/carrier
sheets, `carrier_health`, human-action flags): `site_id, operations[], auth (none / login /
human), session (isolated profile), rate_limit, concurrency, known_limits, freshness,
last_verified, needs_human (CAPTCHA/MFA → Human Action), status_semantics` (each site's
own status vocabulary, mapped explicitly; never assumed equal across sites).
Web Investigation agents use **only** existing integrations and approved public sources;
CAPTCHA/MFA always become a Human Action; nothing is bypassed.

### 4.6 Mission DAG
Task: `task_id, objective, specialist, model|tool, site, inputs, depends_on[], priority,
status (queued/running/blocked/needs_human/done/failed/cancelled), timeout, retries,
idempotent (bool), evidence_required[], completion_criteria, output_ref`.
Independent tasks run in parallel within limits; dependents start when prerequisites are
`done`; a failed prerequisite blocks dependents and triggers replanning.

### 4.7 Mission Queue and Workload Manager
- Persistent store (SQLite in the intelligence folder, WAL; one writer) so missions and
  tasks survive restarts; on start, `running` tasks become `queued` (idempotent) or
  `needs_check` (non-idempotent: verify the previous outcome first).
- Priority = business impact × urgency × SLA exposure × deadline proximity, plus
  **aging** so low-priority work is not starved.
- Semaphores: local model (1 on 4 cores), Cortex (per profile, e.g. 4), per website
  (from registry), Chrome sessions, total CPU/RAM guard (pause new work above e.g. 85%
  RAM); **the ATA automation always keeps its reserved capacity**.
- Backpressure: queue limits per level; beyond them the user is told plainly and the
  mission waits or runs reduced; duplicate missions (same fingerprint) are merged.
- Cancel, pause, resume per mission and per task.

### 4.8 Hypothesis Engine
Known facts → missing information → hypotheses from a **fixed taxonomy first** (carrier
delay, customs clearance delay, missing documentation, tracking sync issue, milestone
mapping error, extraction failure, data quality, unknown) plus model-suggested ones →
for each: evidence that would confirm / reject → tasks → status `open / supported /
rejected / inconclusive`. Ranking is by evidence, not by model confidence. "Unknown cause"
is a valid outcome. Builds on `failures.py`'s VERIFIED/INFERRED/UNKNOWN.

### 4.9 Evidence Vault
Extends `evidence.py` from images to every claim: `evidence_id, mission_id, task_id,
claim, class (VERIFIED_LIVE | HISTORICAL | GENERAL_KNOWLEDGE | PLATFORM_RULE |
HYPOTHESIS | EXTERNAL_UNVERIFIED), source (system/site), record id or URL, captured_at,
freshness, collected_by (agent + model), verification_status (verification.py vocabulary),
confidence basis, identifiers (AWB/BOL/PO/shipment), sha256 of the raw capture`.
Append-only; contradictions are kept side by side; secrets redacted by `store.py` rules.

### 4.10 Verification gates and confidence
Before a mission answers: every material claim has evidence IDs; every number is
**recomputed** by code from the source records; key claims are re-read from the source if
older than its freshness limit; contradictions are resolved or shown; proposed actions
cite the findings they rest on. Confidence is **not a percentage**: a documented grade —
*Strong* (verified live sources agree, recomputed, no open contradiction) · *Moderate*
(historical or single source, no contradiction) · *Weak* (inferred, or contradictions) ·
*Insufficient* (ATLAS says it cannot conclude) — with the reasons listed.

### 4.11 Actions and human control
`READ_ONLY` (may run with user + integration permission) · `PREPARATION` (draft a change,
commit nothing) · `APPROVAL_REQUIRED` (shipments, carrier portals, clearance, financial,
production records: show action, evidence, impact, risks; an authorized approver
approves) · `RESTRICTED` (existing restricted-action policy). Approved actions are handed
to the **existing** human-action queue and automation with their idempotency keys; a
result is reported only after **read-back**. Missions never write directly.

### 4.12 Memory and learning
Mission record: objective, plan (with hash), consent, agents, models, sites, findings,
evidence, decisions, approvals, outcomes, verification, lessons. Similar past missions
are retrieved (identifiers, carrier, failure class, embeddings) to inform planning.
Lessons become **proposals** in `kb_govern.py`; nothing becomes a rule without a reviewer.

### 4.13 Mission Control (hub)
A new **Missions** item in the hub sidebar (Insight group); nothing existing is renamed
or removed. Views: proposal and consent; mission header (objective, level, phase,
consent, budget used); task graph (parallel / blocked / needs person); agents and models
at work; website activity; evidence list with classes; hypotheses board; conflicts;
resource meter; approval requests (linked to To Do); final report. Live updates over the
existing polling cadence (diffed, no full redraws). Same glass design system and motion.

---

## 5. Emergency Mode (CRITICAL / SEVERE)

- **Activation:** manual (authorized user), automatic by rule (configurable thresholds on
  verified signals: e.g. N critical shipments past due > X days, clearance blocked with
  demurrage exposure, carrier failure across > Y shipments), or escalation from a running
  task. Automatic activation **proposes** and notifies; the Commander does not start
  billed or parallel work until an authorized user approves (or a pre-approved policy
  explicitly allows a read-only start). Override by an authorized user is recorded.
- **Severity:** NORMAL → HIGH (more checks) → CRITICAL (Emergency Mode) → SEVERE (maximum
  approved resources, Opus only if allowlisted).
- **Team:** Incident Commander (= Commander with the incident template), Logistics
  Intelligence, Data Analyst, Carrier & Research, Automation Engineer, Independent
  Verification. Each with explicit inputs and outputs; findings kept separate until
  consolidation.
- **Output:** the 11-part resolution plan you specified (summary, root-cause
  investigation, confirmed facts, suspected causes, impact, options, recommendation,
  risks/dependencies, approvals needed, verification criteria, recovery/rollback).
- **Resource profiles** (config): *Normal* (local 1, Cortex 2, 1 site at a time), *Deep*
  (local 1, Cortex 4, 2 sites), *Emergency* (local 1, Cortex 6, 3 sites, own token budget),
  *Drill* (synthetic/historical data, no writes, local only by default). Never two large
  local models at once; automation reservation always kept.
- **Duplicate activation:** one incident per fingerprint; later triggers attach to it.

---

## 6. Model governance (every invocation)

Append-only `model_invocations.jsonl` (intelligence store, redacted, 90-day retention,
exported daily):

`invocation_id, mission_id, task_id, purpose, provider, model_id, location/region,
started_at, latency_ms, input_tokens, output_tokens, cached_tokens, cost_estimate
(tokens × configured rate), status (ok/timeout/error/refused/budget), error_class,
output_ref (sha256 + vault id), verification_status (unverified/supported/contradicted),
user_id (pseudonymous), data_tier`.

Snowflake side: every Cortex call carries a **query tag / request id** so ATLAS's ledger
reconciles with `CORTEX_REST_API_USAGE_HISTORY` monthly (tokens per model and user).
`trace.py` keeps its in-memory timing; the ledger is the durable record.

---

## 7. Security assessment

| Risk | Today | Proposal |
|---|---|---|
| **Data residency** — prompts processed in AWS US (cross-region on) | Shipment refs, statuses, BU names may leave the EU region when Claude is used | Owner/DPO decision. Data tiers: **T0** general knowledge → any model; **T1** operational (refs, statuses, dates) → Cortex allowed if approved; **T2** personal/contact/financial values → local only, or redacted; **T3** secrets, passwords, codes → never to any model (already enforced) |
| Cortex rights on **PUBLIC** | Any role can call Cortex and spend credits | Revoke from PUBLIC (admin decision); ATLAS uses a dedicated service user with **SVC_CORTEX_R** / CORTEX_LIMITED_ROLE only |
| Opus not allowlisted | Good default | Keep off unless SEVERE profile is approved |
| PAT scope and theft | PAT in Windows Credential Manager (good) | Bind PAT to the service role; network policy to the server's IP; rotation every 90 days; least privilege (no table access unless a Data Analyst scope is approved) |
| Prompt injection from websites / web search / emails | Web content already labelled "from public sources" | All external text is **data**, wrapped and never executed as instructions; agents cannot widen their own tools; action proposals from external text are flagged |
| MCP SQL not forced read-only | `CORTEX_MCP_SERVER_FORCE_SQL_EXEC_READ_ONLY=false` | Missions use a read-only role for SQL; do not rely on the MCP flag |
| Model hallucinated facts | `factguard` | Kept, plus recomputation gates and evidence classes |
| Logs leaking data | `store.py` redaction | Ledger stores hashes and vault ids, not prompts; prompts kept only in the vault for missions, redacted |
| Runaway cost | 500k tokens/day budget | Per-mission, per-user, per-day budgets; Emergency budget separate; hard stop → local model or "insufficient" |
| Load on the automation server | Model gate exists | Workload Manager reserves automation capacity; RAM guard |

---

## 8. Cost model

`credits = Σ_models (input_tokens − cached_input_tokens) × rate_in(model) + cached ×
rate_cached(model) + output_tokens × rate_out(model)` (rates from the Consumption Table,
kept in config; Snowflake reports tokens split as input / cache_read_input / output).

Measured baseline (30 days, REST): 190 requests, 194,824 tokens, llama3.1-8b only.

**Estimated tokens per mission** (estimates to be replaced by measurements in Phase 3):

| Level | Model calls | Tokens (approx.) | Notes |
|---|---|---|---|
| NORMAL | 0–1 | 0–2k | Today's behaviour; many answers use no model |
| ADVANCED | 2–4 | 8–15k | One specialist + review |
| DEEP MISSION | 6–12 | 30–60k | Planner, 3–5 specialists, independent review, report |
| CRITICAL | 10–20 | 60–120k | Plus re-reads, conflict rounds |
| SEVERE | 15–30 | 100–200k | Opus only if allowlisted |

Budgets (proposal, configurable): day 500k (today's), per DEEP mission 80k, per CRITICAL
150k, per user/day 150k; at 80% ATLAS warns, at 100% it switches to local/deterministic
and says so. Monthly report: credits per level, per model, per user; cost per resolved
incident.

---

## 9. Resource strategy

- Server today (benchmark 2026-10-09): **4 CPU cores, CPU-only**; local model 1 answer at
  a time. RAM/GPU TO VERIFY.
- Order of preference: deterministic → local small model → Cortex small → Cortex Claude.
- Never run `qwen3:30b` while Chrome automation is active above a RAM threshold; never
  two large local models.
- Parallelism lives mostly in Cortex (remote) and in independent record reads; website
  parallelism is limited per site and by Chrome sessions.
- Cancel redundant work as soon as the sufficiency gate passes.

---

## 10. Failure handling

| Failure | Handling |
|---|---|
| Model unavailable / timeout | Router picks the fallback; task marked with the model change; if no fallback, task `blocked`, mission continues on other branches |
| Cortex 401/403 | Stop Cortex for the mission, alert admin (token/role), continue locally |
| Budget reached | Stop billed calls; finish with deterministic/local work; say so in the report |
| Browser crash | Restart isolated session; re-run idempotent read; keep evidence |
| Website down / rate-limited | Back off per registry; mark data freshness; replan with other sources |
| Auth expired / CAPTCHA / MFA | Human Action (existing flow); task `needs_human` |
| Conflicting site results | Keep both; open conflict task; never pick silently |
| Extraction failed | Retry once with another extractor; then `failed` with the raw capture kept |
| Resource exhaustion | Pause new tasks; shed lowest priority; protect automation |
| Agent output invalid (schema) | Reject; one retry with the error; then `failed` |
| Server restart | Resume from the persistent queue; non-idempotent tasks re-verified first |
| Non-idempotent operation uncertain | **Never repeated** until its outcome is read back |

---

## 11. Evaluation

Scenarios (synthetic + replayed historical, Drill profile, no writes): critical shipment
delay; missing / contradictory ATA; carrier tracking failure; customs blockage;
conflicting ETAs; failed automation write; model timeout; model disagreement; resource
exhaustion; duplicate activation; prompt injection in a web page; budget exhaustion.

Metrics: time to first useful finding; time to verified plan; evidence coverage
(material claims with evidence ÷ all); root-cause accuracy vs. labelled outcome; false
escalations; unnecessary model calls; tokens/credits per mission; approval compliance
(100% required); human-action correctness; resource peaks vs. limits.

Release gates: approval compliance 100%; no invented numbers (factguard + recompute) in
100% of cases; false escalation ≤ 10% on the labelled set; ATA automation throughput
unchanged under a concurrent Emergency drill.

---

## 12. Phased implementation (each phase ships behind a flag, tests first, no live writes)

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 Verify** (no code) | On the server: `ollama list`, RAM/GPU, `cortex status`, PAT role; owner decisions (section 13) | Facts recorded in this plan |
| **P1 Ledger + registries** | `model_invocations` ledger wrapping existing providers; Model Capability Registry (from config + health); Website Registry (from carrier modules); router in **shadow** (logs its choice, changes nothing) | Ledger reconciles with Snowflake usage within 5%; zero behaviour change in the test suite |
| **P2 Missions core (read-only)** | Mission/task store, DAG runner, Workload Manager with semaphores and recovery, Evidence Vault, classifier + **ask-before-escalate** proposal card | Restart recovery test; decline remembered; no billed call without consent |
| **P3 Commander + specialists** | Commander with templates (delay, clearance, carrier failure, missing ATA, data inconsistency), 5 core specialists, verification gates, report | Drill scenarios pass; evidence coverage ≥ 90%; tokens measured per level |
| **P4 Mission Control UI** | Hub "Missions" page, task graph, evidence, hypotheses, approvals linked to To Do | UX review; no regressions in hub suites |
| **P5 Emergency Mode** | Activation rules, severity, incident template, resource profiles, parallel execution, duplicate handling | Concurrency drill with automation unaffected; budgets enforced |
| **P6 Approval actions** | APPROVAL_REQUIRED proposals into the existing human-action queue, idempotency, read-back | 100% approval compliance; no direct writes |
| **P7 Advanced** | Comparison, what-if, replay, handoff, collaboration, scheduled analysis, analytics, optional Cortex embeddings | Each feature with its own tests |

---

## 13. Decisions needed from the owner

1. **Data residency:** may operational data (tier T1) be sent to Claude while cross-region
   inference routes it to AWS US? If not: Claude only for T0 / redacted prompts, and
   in-region models for T1 (which models run in Azure West Europe: TO VERIFY with Snowflake).
2. **Revoke `SNOWFLAKE.CORTEX_USER` from PUBLIC** and keep ATLAS on its service role?
3. **Allowlist Claude Opus** for SEVERE incidents, or keep Sonnet as the ceiling?
4. **Budgets:** day / per mission / per user (section 8 proposals).
5. **Who may approve** DEEP, CRITICAL, SEVERE and automatic activation?
6. **Automatic Emergency activation:** propose-only, or a read-only auto-start?
7. **Is ATLAS's Claude integration meant to be on** on the server today (no Claude REST
   traffic in 30 days)?
