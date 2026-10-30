# ATLAS Agentic AI — Architecture and Implementation Plan

**Status: PLAN ONLY (30 Oct 2026).** Written from a read-only audit of this repository
(branch `claude/atlas-potato-garden`, head `99bd75c`). No application code, configuration,
Snowflake setting, role, grant or model was changed; no paid model was called; no
automation ran; nothing was pushed. Snowflake facts come from read-only metadata queries
made on 30 Oct 2026 (see `ATLAS_MISSIONS_PLAN.md` §2). Anything that could not be checked
from here is marked **TO VERIFY**.

This plan **extends** what exists. In particular, the Missions layer
(`intelligence/missions/`, commit `06c57c9`) is already the beginning of the mission engine
this brief asks for; it is audited below like everything else, and the plan grows it
rather than adding a second one.

---

## 1. Executive summary

ATLAS today is a deterministic logistics control tower with AI around it:

- **The execution engine** is the ATA automation (`update_eta.py`, 12,670 lines): carrier
  lookups in a real browser, the Hub write, and **read-back verification**; Human Actions
  (`human_queue.py`) when a carrier page needs a person; run control (`dashboard/control.py`:
  pause, resume, stop, **reprocess one reference**). It is the only thing that writes to
  the Hub, and it should stay that way.
- **The intelligence layer** answers and explains, never decides facts: a question router
  (`intelligence/router.py`), conversation (`converse.py`) on a Cortex-first / Ollama-fallback
  chain (`llm.py`, `cortex.py`), `factguard`, a governed knowledge base (`kb*.py`), failure
  analysis and recovery plans (`failures.py`, `plans.py`, `ml/recovery.py`), learning that
  only counts **verified** outcomes (`learning.py`), the hub's read-only tools
  (`dashboard/atlas_tools.py`) and chat skills (`dashboard/atlas_skills.py`).
- **The Missions layer** (new) proposes multi-step investigations, asks before escalating,
  runs ten specialists over a persistent task graph, recomputes every number, routes and
  counts model calls, and produces a report whose next steps are **never executed**.

The transformation is therefore not "add agents". It is four things:

1. **From templates to planned missions.** The Commander plans a task graph from the
   objective (bounded by a catalogue of tools, specialists and templates), fans out over
   many shipments, and replans when evidence changes.
2. **From proposals to approved execution.** An approved action is handed to the **existing**
   engines (the run's reprocess queue, the Clearance escalation, the PO flow) and the mission
   waits for the **persisted, read-back** outcome before it reports success.
3. **One model router and one ledger** for the whole platform (chat and missions), with
   capability profiles, budgets, health, fallbacks and cost attribution per mission and agent.
4. **Mission Control** as a first-class part of the hub (already started), fed by the
   existing SSE stream instead of polling.

Everything stays deterministic-first, read-only by default, and approval-gated. The Claude
models the account allows (Sonnet 4.5 / 4.6 / 5 through Cortex) are reserved for planning,
reasoning and review; most tasks need no model at all.

---

## 2. Existing architecture audit

Traced execution paths (not just files):

```
Hub chat ─► POST /api/ask (dashboard/server.py)
            ├─ atlas_tools.answer()          platform numbers, read-only, BU-scoped
            ├─ assistant.answer()            rules, skills, intents (dashboard/assistant.py)
            │    └─ converse.answer()        when a model is configured (intelligence/converse.py)
            │         ├─ llm.provider()      ChainProvider(CortexProvider → OllamaProvider) or Ollama/None
            │         └─ factguard.check()   rejects added numbers / references / success claims
            ├─ _mission_offer()              classify → propose a mission (link), never starts it
            └─ control.request()             ONLY if the assistant asked for "reprocess" and the person may (runs.start)

Run control ─► dashboard/control.py  pause | resume | stop | reprocess <ref>
                └─ update_eta.py consumes the control file: re-tracks the reference, writes the Hub, reads back

Remote platform ─► controlplane/app.py (runs, workers, audit DB, SSE) ◄─ worker/agent.py (Windows, drives the same Supervisor)

Missions ─► /api/missions* ─► intelligence/missions/engine.py (workers, missions.db)
             └─ specialists.py ─► records (eHub export, run state, escalations, carrier health, KB)
             └─ models.call() ─► Cortex / Ollama stand-ins, ledger model_invocations.jsonl
```

**Activation facts that matter:**
- Ollama is switched on by `intelligence/autoconfig.apply()`, called once at dashboard
  start (`server.py`); default model `qwen3.5:4b`, `qwen3:30b` when the PC has 30 GB+ RAM.
  Which models are actually installed on the tower: **TO VERIFY** (`ollama list`).
- Cortex is used only when `cortex.settings()` finds a URL + programmatic access token in
  Windows Credential Manager (`MantracTower/SnowflakeCortex`). Snowflake's
  `CORTEX_REST_API_USAGE_HISTORY` shows **no Claude call in the last 30 days** (only
  `llama3.1-8b`, 190 requests, from other users/tools). So **Claude is integrated but not
  active on the server today** — **TO VERIFY** with `python -m intelligence.cortex status`.
- Live updates use **Server-Sent Events** (`/api/stream` in `dashboard/server.py`,
  `controlplane/app.py`), not WebSocket.

---

## 3. Existing vs missing capability matrix

Status: **Implemented** · **Partial** · **Missing** · **Refactor** · **External config**

| Capability | Existing implementation | Current behaviour | Missing | Proposed extension | Status |
|---|---|---|---|---|---|
| Model routing (chat) | `intelligence/llm.py` `ChainProvider` | Cortex first, Ollama fallback; one model per provider; no task awareness | capability/cost/latency routing for chat | chat calls `missions.models.route()` with purpose profiles | **Refactor** |
| Model routing (missions) | `intelligence/missions/models.py` | registry, hard filters (capability, data tier, health, budget, profile), score, fallback, different-family reviewer | context windows, measured p95, rate limits, per-agent caps | extend registry; becomes the single router | **Partial** |
| Ollama | `llm.OllamaProvider`, `autoconfig.py` | local only, health TTL, retries, auto-upgrade 4B→30B, vision | none critical | registry entry fed by `autoconfig` facts (RAM, model) | **Implemented** |
| Snowflake Cortex | `intelligence/cortex.py` | REST chat completions, PAT in Credential Manager, daily 500k budget, 15-min cache, cool-down, usage per purpose | per-mission attribution, query tag, model per call | `models.py` already names the model per call; add request tag | **Partial** |
| Claude (via Cortex) | `cortex.DEFAULT_MODEL = claude-sonnet-4-5` | allowed by the account; **not called in 30 days** | server activation | owner decision + `cortex set` on the tower | **External config** |
| Question / intent router | `intelligence/router.py`, `assistant` intents | microsecond routing of chat questions | — | mission classifier runs after it (done) | **Implemented** |
| Mission classification + consent | `missions/classify.py`, `server._mission_offer` | NORMAL / ADVANCED / DEEP / CRITICAL from records; proposal; decline 24 h | user-edited plans ("modify") | plan editor (remove tasks / change profile) | **Partial** |
| Planning | `plans.py` (recovery plans), `missions/registry.TEMPLATES` | fixed 10-task graph per template | objective decomposition, fan-out, replanning | Commander planner (§9) | **Partial** |
| Mission engine | `missions/engine.py`, `missions/db.py` | persistent queue, deps, workers, priority+ageing, backpressure, pause/resume/cancel, retry once, restart recovery | deadlines, per-mission agent/model-call caps, fan-out sub-graphs, duplicate *task* prevention across missions | §9 | **Partial** |
| Evidence store | `intelligence/evidence.py` (images, SHA-256); `missions` evidence table | claims with class, source, freshness, verification | raw capture hashes for text evidence, cross-mission reuse | §11 | **Partial** |
| Review / verification | `verification.py` (REAL VERIFIED…), `factguard.py`, `missions.specialists.verify/review` | recompute numbers, contradictions, grade; different-family review | verification of *executed actions* | action verifier reading run outcomes (§10) | **Partial** |
| Token budgets | `cortex.budget()` daily; `missions` per-mission budget | daily cap; mission budget enforced in router | per-agent / per-task call caps, per-user | §15 | **Partial** |
| Caching | `cortex._cache` (900 s), KB caches, research cache | identical paid requests reused | mission-level evidence reuse | evidence reuse window (§15) | **Partial** |
| Knowledge & lessons | `kb.py`, `kb_govern.py`, `kb_lessons.py`, `learning.py`, `review.py` | governed proposals, verified-only learning | mission lessons into the same flow | §11 | **Partial** |
| Tool gateway | `atlas_tools.py` (read-only platform tools), `atlas_skills.py` (validate, PDF, screenshot, web, handbook) | each called from chat code paths | a declared tool catalogue with permissions for agents | Tool Catalogue (§9.3) wrapping these | **Missing** (as a gateway) |
| Web research | `intelligence/research.py` | self-hosted SearXNG, allow-listed official domains, never tracking pages | — | Web Investigation agent uses it as is | **Implemented** (needs `ATLAS_SEARCH_URL`: **External config**) |
| Browser automation | `update_eta.py` (Playwright/Edge), `worker/agent.py`, `remote_session.py` | carrier lookups only inside the run, pacing, Human Actions | agent-requested lookups | through `control.request("reprocess")` only | **Implemented** (agents must not open browsers) |
| Human-action queue | `human_queue.py`, To Do (`dashboard/todo.py`) | park, open & continue, verify, resume, read back | mission approvals as To Do tasks | add a To Do source for mission approvals | **Partial** |
| ATA automation engine | `update_eta.py` + `dashboard/control.py` | the only Hub writer; read-back verified | — | unchanged; missions request through control | **Implemented** |
| Run outcome verification | `bridge._verified_outcome` → `events.verified_success` | SUCCESS only when every Hub write was read back | mission waits on it | action verifier (§10) | **Implemented** (to reuse) |
| Audit logging | `controlplane/audit.py` (append-only), `server._acc_audit` | logins, runs, Human Actions, escalations, missions approve/decline | mission action execution events | add MISSION_ACTION_* | **Partial** |
| APIs / live updates | REST + SSE `/api/stream` | Mission Control polls every 1.5 s | mission events on SSE | §13 | **Partial** |
| Permissions | `controlplane/rbac.py` | missions.view / run / emergency added | autonomy levels per role, Level-4 policy | §10 | **Partial** |
| Deep Mission Mode UI | `static/hub/missions.js` | proposal, live graph, evidence, hypotheses, report | plan editing, approvals queue, agent activity feed, history filters | §13 | **Partial** |

---

## 4. Target architecture

```
                         ┌─────────────────────────── Hub (sidebar app) ───────────────────────────┐
                         │ ATLAS chat · Missions (Mission Control) · To Do · Tracking · ATA · …    │
                         └───────────────┬───────────────────────────────▲─────────────────────────┘
                                         │ REST                           │ SSE (mission events)
                         ┌───────────────▼───────────────────────────────┴─────────────────────────┐
                         │ dashboard/server.py  (auth, RBAC, CSRF, audit)                          │
                         └───────────────┬─────────────────────────────────────────────────────────┘
                                         │
        ┌────────────────────────────────▼────────────────────────────────────────┐
        │ intelligence/missions  (the Agent Orchestration Layer)                    │
        │  classify ── Commander (planner + replanner + sufficiency gate)          │
        │                 │                                                         │
        │  engine: persistent queue · DAG · workers · budgets · deadlines · locks   │
        │                 │                                                         │
        │  agents (specialists.py, dynamic, config-registered)                     │
        │                 │ uses only                                               │
        │  Tool Catalogue (tools.py) ──► existing read tools + 3 action adapters    │
        │  Model Router (models.py) ───► Ollama · Cortex(Claude Sonnet, Llama…)     │
        │  Evidence Vault (db) · Ledger (model_invocations.jsonl) · Memory          │
        └───────────┬──────────────────────────────┬───────────────────────────────┘
                    │ read                          │ approved actions only
   eHub export · run state · events · KB    ┌──────▼───────────────────────────────────────┐
   carrier health · escalations · research  │ EXISTING engines (unchanged):                 │
                                            │  control.request("reprocess")  → update_eta   │
                                            │  clearance.escalate()          → escalations  │
                                            │  human_queue / To Do           → a person     │
                                            │  po service                    → PO flow      │
                                            └──────┬───────────────────────────────────────┘
                                                   │ persisted outcome (read back)
                                            Action Verifier ◄── events.verified_success / escalations / PO record
```

Principles: one router, one ledger, one engine; agents are functions, not processes;
tools are declared; actions go only through existing engines; success only after
read-back.

---

## 5. Agent responsibility definitions

Agents are **logical roles** implemented as functions over a task context
(`specialists.py`) and registered in `registry.SPECIALISTS`. A mission instantiates only
the roles its graph needs; the engine's worker threads run them. A new role = registry
entry + function + output schema.

| Agent | Responsibility | Tools it may use | Model need | Output | Exists |
|---|---|---|---|---|---|
| **Mission Commander** | objective → mission spec; plan graph (template-first, model draft validated); assign agents, models, tools; monitor; replan; sufficiency gate; consolidate | catalogue, registries, plan validator | `plan` (Claude Sonnet) or none (template) | mission spec, graph, decisions | Partial (`engine` + `hypotheses`) |
| Logistics Intelligence | step meaning, owners, bottlenecks, Incoterms/customs knowledge | KB (`kb.py`), platform rules (stage owners) | `explain` (local) | knowledge claims | Done (`logistics`) |
| Shipment Intelligence | the shipment's records: step, dates, overdue, ETA/ATA, run record | eHub export, run state | none | facts | Done (`shipment`) |
| Carrier Intelligence | carrier record, health, status semantics; **live** evidence only via a reprocess request | run state, `carrier_health`, website registry; *action:* reprocess (Level 3) | none | facts, action proposal | Partial (no live path yet) |
| Web Investigation | official sources (advisories, port notices, carrier workflow docs) with timestamps | `research.py` (SearXNG, allow-list), never tracking pages | `extract` (local) | EXTERNAL claims | Missing (agent); tool exists |
| Data Analyst | KPIs, patterns, anomalies, carrier comparisons, impacts | export, events, `carrier_health`, `hub.stages` | none (code) | facts with filters | Done (`analyst`), extend |
| Automation Engineer | which existing workflow can act; prepare the action with its idempotency key and verification criterion | tool catalogue (action adapters), `plans.py`, `ml/recovery` policy | none | action proposals | Partial (`actions`) |
| Risk Analyst | exposure, consequences, safer alternatives | export, escalations, SLA rules | `reason` optional | risk claims | Done (`risk`), extend |
| Independent Verification | recompute, re-read, freshness, contradictions, reject unsupported claims, grade | source re-reads, evidence vault | none | gate result | Done (`verify`) |
| Independent Review | a different model family challenges the draft | facts only | `review` (Llama 3.3 70B when Claude drafted) | concerns | Done (`review`) |
| Report Generator | structured report; facts vs hypotheses vs calculations | vault | `word` + factguard | report | Done (`report`) |

Limits: max agents per mission (config, default 10), one instance per role per
sub-graph, fan-out sub-graphs share the Commander.

---

## 6. Multi-model routing strategy

**One router**: `intelligence/missions/models.py` becomes the platform router (it already
has registry, health, filters, scoring, fallback, ledger). Chat keeps its behaviour by
calling it with a fixed purpose profile (`word`, Cortex first, local fallback) — the
`ChainProvider` becomes a thin adapter, then retires.

**Registry fields** (extend): `id, provider, where/region, caps{plan,reason,review,word,
classify,extract,explain,vision}, context_window, max_output, ram_gb (local), tiers allowed,
cost_per_mtok_in/out (config), rate_limit (config), timeout_s, family, fallbacks, health
(live), p50/p95 (measured)`.

**Verified account facts** (30 Oct): allowlist = claude-sonnet-5, claude-sonnet-4-6,
claude-sonnet-4-5, llama3.1-8b/70b, llama3.2-1b/3b/11b/90b, llama3.3-70b, mistral-7b,
mixtral-8x7b, gemma-7b, snowflake-arctic, reka-flash, jamba-1.5-mini, codestral-22b;
Claude Opus present but **not allowlisted** → never registered. Cross-region inference
`AWS_US` → Claude prompts may be processed in the US → data tiers (§16).

**Rules**: capability → tier → health → budget → profile (hard); then score; local
preferred when good enough; reviewer must differ in family; numbers never from a model;
pictures local only; no call when the answer is deterministic; per-task max calls
(default 2: primary + one fallback); per-provider concurrency (local 1, Cortex profile
slots); timeouts per model; availability validated by a real health check (Cortex: the
existing cool-down + a 1-token probe only when the owner allows; otherwise "configured,
unproven").

| Purpose | Primary | Fallback | Never |
|---|---|---|---|
| Plan a mission graph | Claude Sonnet | template only | local 4B |
| Classify / extract | local qwen | Cortex llama3.1-8b | Claude |
| Explain logistics | KB + local | Claude Sonnet | — |
| Root-cause reasoning | Claude Sonnet | qwen3:30b (profile permitting) | 4B alone |
| Independent review | other family (Llama 3.3 70B) | code checks only, flagged | same model |
| Numbers / statuses | code | — | any model |
| Report wording | Claude Sonnet + factguard | local + factguard | — |
| Chat wording | as today (Cortex → local) | rules | — |

---

## 7. Mission lifecycle

```
PROPOSED ──approve──► QUEUED ──capacity──► RUNNING ──► VERIFYING ──► REPORTED
   │  └─decline─► DECLINED (24 h memory)     │  ▲ pause/resume          │
   │                                         │  └ replan                ▼
   └─cancel─► CANCELLED                      ├─► AWAITING_APPROVAL (actions) ──approve──► EXECUTING ──► AWAITING_OUTCOME
                                             │                              └─reject──► REPORTED
                                             └─► BLOCKED (needs a person) ──resolved──► RUNNING
AWAITING_OUTCOME ── read-back verified ──► DONE_VERIFIED
                 ── failed / not read back ──► DONE_UNVERIFIED (said plainly) ──► lessons
any ── deadline / budget ──► REPORTED (partial, said plainly)
```

Today's statuses (`proposed, queued, running, paused, done, insufficient, failed,
cancelled, declined`) are kept; `awaiting_approval, executing, awaiting_outcome, blocked,
done_verified, done_unverified` are added.

---

## 8. Mission data model

Extends `missions.db` (SQLite, WAL). Existing tables: `missions, tasks, evidence,
hypotheses, events, declines`. Added columns / tables:

- `missions`: `+ deadline_at, max_agents, max_calls_per_task, autonomy_level, parent_id`
  (fan-out), `verification_status` (final), `cost_estimate`.
- `tasks`: `+ agent_instance, tool, tool_args (hash + JSON), dedupe_key` (UNIQUE per
  mission + across live missions for read tools), `deadline_at, max_calls`.
- **`actions`** (new): `id, mission_id, task_id, kind (reprocess | escalate | po_send |
  draft_mail …), class (READ_ONLY…RESTRICTED), args, idempotency_key, proposed_at,
  approved_by/at, executed_at, engine_ref (control queue entry / escalation id / PO job),
  outcome (pending | verified | not_verified | failed), outcome_evidence_id`.
- **`memory`** (new): `mission_id, kind (lesson | outcome | pattern), text, evidence_ids,
  status (draft | proposed_to_kb | rejected)`.
- Ledger stays in the intelligence store (`model_invocations.jsonl`) with `+ agent,
  mission_id, task_id, request_tag`.

Note: the intelligence store drops any JSON key containing "token" (secret guard) —
ledger/report keys are named `usage_*` / `budget` (already the case).

---

## 9. Orchestration and dependency design

### 9.1 Commander planning
1. Classify (exists). 2. Pick a **template** when one fits (fast, free). 3. Otherwise ask
the planner model for a graph in a strict JSON schema whose nodes may only name
**catalogue tools** and **registered agents**. 4. Validate: unknown tool/agent → reject;
permission check per tool for the approver's role/BUs; cycle check; size limits (agents,
calls, deadline). 5. On failure → template + note.

### 9.2 Fan-out ("all delayed shipments")
A mission may create a **sub-graph per item** (shipment, BU, carrier) from a deterministic
selector (e.g. `hub.stage_list("pastdue", bu)`), capped (default 25 items; the rest
summarised statistically by the Data Analyst), then a **merge** task. Common-root-cause
analysis runs on the merged evidence.

### 9.3 Tool Catalogue (`missions/tools.py`, new, thin)
Each tool: `name, reads|acts, permission, autonomy_level, idempotent, rate limit, adapter`.
Read tools wrap existing functions (`clearance.records/find/escalations`, `hub.stage_list`,
`hub.shipment`, `carrier_health.health`, `kb.search`, `research.search`, `events`,
`atlas_skills.validate`). Action tools (**three**, all existing engines):
`reprocess(ref)` → `control.request("reprocess")`; `escalate(awb, reason)` →
`clearance.escalate`; `queue_human(...)` → a To Do task. No tool opens a browser.

### 9.4 Execution
Existing workers; ready tasks by priority + ageing; **dedupe_key** so the same read in two
live missions runs once (result shared as evidence with provenance); deadlines per task
and mission; replanning triggers: a hypothesis newly supported/rejected, a source
unavailable, an approval rejected, a contradiction.

### 9.5 Deadlock and loop guards
Graph validated acyclic; a task blocked > deadline → Commander decides (retry, fallback,
reassign, pause, ask a person); retries ≤ 1 per task and ≤ N per mission; model calls ≤
max per task; no task re-queued by replanning more than twice.

---

## 10. Autonomy and governance model

| Level | What may happen | Who enables | Examples |
|---|---|---|---|
| **1 Read only** | read records, research, analyse | default for every mission | everything in today's Missions |
| **2 Planning** | prepare proposals, drafts | default | escalation text, re-run list, email draft (not sent) |
| **3 Approval required** | execute a prepared action after explicit approval | approver with the action's own permission (`runs.start`, `clearance.escalate`, `po.send`) | reprocess a reference; open an escalation |
| **4 Restricted autonomous** | pre-authorised low-risk actions inside a written policy | Admin, per policy, time-boxed | **proposal only**: e.g. reprocess in a dry run of ≤ N references for a verified tracking-sync hypothesis |

Rules (enforced in the engine, tested): no silent escalation of level; level shown on the
proposal; every action carries an idempotency key and a verification criterion; actions
only via the three adapters; a person's approval is recorded (who, when, plan/action
hash) and audited (`MISSION_ACTION_APPROVED / EXECUTED / VERIFIED / FAILED`); **success
only when the engine's persisted outcome confirms it** (run: `events.verified_success`
for that reference after the request; escalation: record present; PO: job state);
non-idempotent actions are never retried before their previous outcome is read; the
CMA CGM automation hold and all carrier verification / Human Action rules are unchanged
(they live in the engine missions call).

---

## 11. Evidence and memory design

- **Classes** (exist): VERIFIED_LIVE, HISTORICAL, GENERAL_KNOWLEDGE, PLATFORM_RULE,
  HYPOTHESIS, EXTERNAL_UNVERIFIED; **add** CALCULATED (numbers computed by code, with the
  filter used) and ACTION_OUTCOME.
- Provenance per claim (exists): source, record, captured_at, freshness, agent, model,
  identifiers; **add** `raw_sha256` of the captured text/record and the `dedupe_key` that
  produced it.
- Contradictions kept side by side (exists in verify); **add** a `conflicts` table so the
  UI and replanner see them as first-class.
- **Memory**: each finished mission writes outcome + lessons as `memory` drafts; similar
  missions are retrieved by identifiers / carrier / template / failure class (later:
  embeddings via `kb_embed.py`). A lesson becomes knowledge **only** through
  `kb_govern.py` proposals and a reviewer; model conclusions never promote themselves.
- Reproducibility: the plan hash, the evidence set (hashes), the model ids and prompts'
  hashes are enough to replay a mission against its preserved evidence (Mission Replay).

---

## 12. Failure recovery strategy

| Failure | Commander decision |
|---|---|
| Model unavailable / timeout | fallback once (router); else task continues without the model (deterministic path) and is flagged |
| Cortex 401/403/404/429 | 401/403: stop Cortex for the mission, alert admin; 404 model: drop from registry until health passes; 429: back off, local fallback |
| Ollama down | local tasks go to Cortex small models if tier allows, else deterministic only |
| Carrier website failure / session expiry | not a mission concern: the run handles it (retry ladder, Human Action); the mission sees the outcome and records BLOCKED / needs person |
| Network interruption | tasks are reads → retry once after back-off |
| Incomplete evidence | sufficiency gate → "insufficient" with what is missing; optional replan if a tool could fill it |
| Conflicting results | conflict task; never picks silently; report shows both |
| Agent timeout | task failed after deadline; dependents run with partial input or are blocked; report says so |
| Deadlock | graph acyclic by validation; blocked-task watchdog → ask a person |
| Server restart | `engine.recover()` (exists): reads re-queued; actions in EXECUTING → AWAITING_OUTCOME (re-check outcome; never re-execute) |
| Runaway cost | budget hard stop in the router (exists per mission); add per-day per-user |

---

## 13. Mission Control UI proposal (inside the existing hub)

The hub with its sidebar is the product. **Missions** already sits in the sidebar (Insight
group). Nothing else in the hub changes; Overview, map, Tracking, To Do slide-out, Clearance
and ATA Automation stay as they are.

Additions to the existing Missions view (`static/hub/missions.js`), Liquid Glass styling:
- **Active missions** strip at the top (status pill, phase, deadline countdown, budget bar).
- **New mission**: the existing box plus a *Review or modify* step (remove tasks, choose
  profile Normal / Deep / Drill, set a deadline) before approval.
- **Commander activity** feed (from `events`): plans, replans, challenges, decisions.
- **Task graph** (exists) with agent + model chips, fan-out groups collapsed by item.
- **Evidence timeline** (exists as list) with class filters and source links.
- **Findings & contradictions** panel (conflicts table).
- **Pending approvals**: action cards (class, args, evidence, impact, risks, verification
  criterion) with Approve / Reject; mirrored as **To Do** tasks (new To Do source) so
  approvals are found where people already look.
- **Outcome verification**: per action, the engine reference and the read-back status.
- **Resource usage**: model calls, usage (estimated tokens), cost estimate per agent.
- **History** with filters (template, BU, outcome).
- Live updates through **SSE** (mission events) instead of 1.5 s polling.
No invented metrics or activity: every number comes from the mission store or ledger.

---

## 14. Real-world logistics use cases

References below are placeholders (`<AWB>`, `<BU>`); no claim about current ATLAS data.

**1. One delayed shipment → recovery options.** Graph: facts (Shipment) → pattern (Data
Analyst) ‖ step owner (Logistics) ‖ carrier record (Carrier) → risk → hypotheses
(Commander) → verify → review → actions → report. Models: none until hypotheses (Sonnet,
optional), review (Llama 3.3 70B), wording (Sonnet + factguard). Evidence: export row,
run record, escalations, KB. Approval points: *reprocess <AWB>* (runs.start), *escalate*
(clearance.escalate). Output: graded report; after approval, the run's read-back outcome.

**2. Many delayed shipments → common root causes.** Selector `hub.stage_list("pastdue",
<BU>)` → fan-out ≤ 25 sub-graphs (facts + risk) + Data Analyst over all items (by step,
agent, forwarder, carrier) → merge → hypotheses across items (share ≥ threshold) → verify
(recompute every count) → report with the share per cause. Models: one planning call, one
review, one wording. Approval: bulk escalation list (each item approved or not).

**3. Carrier performance comparison.** Data Analyst over `events` (verified outcomes per
carrier, `carrier_health` minimum sample rule) + export on-time flag by mode; Risk
Analyst; verification recomputes rates; report says "insufficient data" where samples are
below the minimum. No model needed except wording.

**4. ETA discrepancy carrier vs Hub.** Facts: export ETA/ATA, run `provider_eta/ata`,
`internal_eta`; contradiction detection; hypotheses: tracking sync, milestone mapping,
extraction failure; action (Level 3): *reprocess <AWB>* → wait for `verified_success` →
compare again → DONE_VERIFIED or DONE_UNVERIFIED.

**5. Shipment exception report.** Template "exception report": facts + validation skill
(`atlas_skills.validate`: the automation's own date rules) + screenshots from the evidence
store (`evidence.py`, real captures only) + report (PDF via `pdf_report.py`). Read-only.

**6. Customs clearance delay.** Template `clearance_blockage` (exists); Logistics agent
explains the step and owner (clearing agent); Data Analyst checks the agent's other
past-due shipments; Risk: days at customs vs target; action: escalation + draft mail to
the agent (draft only; sending stays a person's act — "DO NOT SEND EMAIL" holds).

**7. Failed ATA automation → remediation.** Facts from the run (`failures.py`
classification, VERIFIED/INFERRED/UNKNOWN root cause), recovery candidates from
`ml/recovery` policy + `learning.py` verified history (`plans.py`); Automation Engineer
proposes the next safe strategy; action: reprocess (dry run first if the owner's policy
says so); outcome from the run; lesson recorded through `kb_lessons` (reviewer gate).

**8. Unfamiliar carrier: tracking workflow.** Web Investigation via `research.py` (official
domains only, never tracking pages) → EXTERNAL_UNVERIFIED claims with URLs and timestamps
→ Logistics agent maps to known milestone vocabulary → Report: a proposed integration
brief for engineering. No browser, no login, no CAPTCHA; a real integration remains a
tested deployment.

---

## 15. Performance and cost strategy

Defaults (configurable): ≤ 3 running missions, ≤ 20 queued; ≤ 10 agents per mission;
≤ 2 model calls per task; fan-out ≤ 25 items; mission deadline 15 min (DEEP), 30 min
(CRITICAL); Cortex slots by profile (2 / 4 / 6), local 1; no local model while the ATA run
is active (exists); browser concurrency unchanged (the run's own).
Cost: deterministic-first (most tasks call no model); cache (Cortex 15 min; add evidence
reuse for identical reads within 15 min across missions); context minimisation (facts only,
`compact()` as `llm.py` does); ledger attribution per mission/agent/task; monthly
reconciliation with `CORTEX_REST_API_USAGE_HISTORY`.

Metrics: time to first finding, time to verified report, evidence coverage (claims with
evidence ÷ claims), recompute match rate, false escalations, model calls per mission,
usage per mission, approval compliance (must be 100%), action verified rate, ATA run
throughput during missions (must not drop).

---

## 16. Security considerations

- **Data tiers** (exists in router): T0 general → any; T1 operational (refs, statuses,
  dates) → Cortex only as already approved for wording; T2 personal/contact/financial →
  local or redacted; T3 secrets → never.
- **Cross-region** (`AWS_US`): owner decision on T1 to Claude (§20).
- **Cortex on PUBLIC**: every role in the account can call Cortex — recommend revoking
  (admin decision; this plan changes nothing).
- **Prompt injection**: external/web text is data; agents cannot add tools or raise their
  level; action proposals originating from external text are flagged.
- **Least privilege**: tools check the approver's permission and BUs; missions inherit the
  creator's BU scope (exists).
- **Audit**: every approval/execution/verification audited; ledger keeps hashes, not prompts.
- **MCP**: `CORTEX_MCP_SERVER_FORCE_SQL_EXEC_READ_ONLY=false` in the account → any future
  SQL tool uses a read-only role.

---

## 17. Testing strategy

All external systems mocked (stand-in providers, synthetic exports, simulated run state).
Existing: `test_missions.py` (53 checks) covers classification, consent, graph order,
provenance, recompute, mismatch → Weak, factguard rejection, routing (fallback, budget,
drill, tiers, automation busy), recovery, backpressure, incidents, API, UI.

Add: planner validation (unknown tools rejected, cycles, size caps); fan-out and merge;
dedupe across missions; deadlines; per-task call caps; permission enforcement per tool and
level; approval workflow (approve/reject, To Do mirror, audit); **action adapters with a
fake control queue and fake `verified_success`** (DONE_VERIFIED vs DONE_UNVERIFIED);
restart during EXECUTING (never re-executes); conflict persistence; SSE events; load
(20 queued missions, 3 running, ATA run simulated active → throughput unchanged);
regression: the full suite (101 suites) green, especially ATA automation, Human Action,
carrier hold and read-back tests.

Acceptance: approval compliance 100%; zero direct writes from missions (static check +
tests); no invented numbers (factguard + recompute) in 100% of drills; restart loses no
mission state; false CRITICAL ≤ 10% on a labelled set; full suite green.

---

## 18. Incremental implementation roadmap

Each stage ships behind a switch, tests first, no live writes, and is reversible by
switching it off (`ATLAS_MISSIONS*` env flags) or reverting its commit.

| Stage | Change | Reuse | New | Tests / acceptance | Rollback |
|---|---|---|---|---|---|
| **S0 Verify (no code)** | `ollama list`, RAM; `cortex status`; PAT role; owner decisions | — | — | facts recorded here | — |
| **S1 One router, one ledger** | `llm.py` chat goes through `missions.models.route("word")`; `cortex.py` adds request tag + ledger row | `models.py`, `cortex.py`, `trace.py` | none | chat answers identical in `test_converse`, `test_cortex`; ledger reconciles | flag `ATLAS_ROUTER=legacy` |
| **S2 Tool catalogue** | `missions/tools.py` wrapping existing reads; specialists call tools | atlas_tools, skills, clearance, hub, research | `tools.py` | permission tests per tool; same reports | specialists keep direct calls |
| **S3 Planner + fan-out + dedupe + deadlines** | Commander planner (validated JSON), fan-out selector, merge, dedupe_key, deadlines, call caps | engine, registry templates | planner functions in `engine.py` (or `plan.py`) | planner validation, fan-out, dedupe, deadline tests; drills | template-only flag |
| **S4 Actions (Level 2→3)** | `actions` table; adapters: reprocess / escalate / queue_human; approval cards; To Do source; audit; Action Verifier | control.request, clearance.escalate, todo.build, events.verified_success | adapters in `tools.py` | fake engines; 100% approval compliance; restart in EXECUTING | flag `ATLAS_MISSION_ACTIONS=0` (proposals only, as today) |
| **S5 Mission Control upgrades** | plan editor, approvals panel, Commander feed, conflicts, outcomes, SSE | `missions.js`, `/api/stream` | none | UI tests; no regressions in hub suites | keep polling |
| **S6 Memory & lessons** | memory drafts, similar-mission retrieval, kb_govern proposals | `kb_lessons`, `kb_govern`, `kb_embed` | `memory` table | no auto-promotion test | flag off |
| **S7 Web Investigation agent** | agent over `research.py` | research.py | specialist function | allow-list + never-tracking-page tests | not registered |
| **S8 Level 4 policies** | written policies (e.g. dry-run reprocess ≤ N for tracking-sync) | S4 | policy file | policy tests; time-box; audit | no policy = no Level 4 |
| **S9 Advanced** | replay, comparison, what-if, handoff, collaboration, scheduled analysis, analytics | all above | per feature | per feature | per feature |

---

## 19. Risks and trade-offs

- **Router refactor (S1)** touches every chat answer → behind a flag, identical-output tests.
- **Actions (S4)** are the first time a mission causes a write → only through existing
  engines, approval per action, outcome read-back, flag off by default.
- **Planner hallucination** → schema + catalogue validation; templates first.
- **Cost** with Claude on → budgets + deterministic-first; most missions need ≤ 3 calls.
- **Load on the tower** (4 cores measured) → automation reservation, local model off during
  runs, Cortex for parallel work.
- **Data residency** of Claude prompts in AWS US → decision 1.
- **Over-proposing** missions in chat → only DEEP/CRITICAL offered; decline memory.
- **Trade-off**: functions-as-agents (simple, testable, cheap) over separate processes;
  separate processes only if a future agent needs isolation.

---

## 20. Decisions required from the platform owner

1. May T1 operational data go to Claude while cross-region inference routes it to AWS US?
2. Switch Claude on for ATLAS on the tower (`python -m intelligence.cortex set`)? Today no
   Claude traffic is recorded.
3. Revoke `SNOWFLAKE.CORTEX_USER` from PUBLIC (Snowflake admin)?
4. Keep Opus off (current) or allowlist it for SEVERE incidents?
5. Budgets: per mission (80k / 150k usage units), per user per day, per day (500k today).
6. Who may approve each action kind (reprocess, escalate, PO send) from a mission?
7. Level 4: allow any pre-authorised policy (e.g. dry-run reprocess ≤ 10 references)?
8. Automatic incident activation: propose only (current) or read-only auto-start?
9. Self-hosted web search for the Web Investigation agent (`ATLAS_SEARCH_URL`)?
10. Where missions run in the remote setup: on the tower (current) or via the control plane?

---

## 21. Implementation status (30 Oct 2026)

Built and tested with stand-ins only (`test_missions_agentic.py`, 90 checks; `test_missions.py`, 53). An independent review found 5 blockers (a double-execution race, a BU leak in To Do, an escalation on another BU's row when an AWB repeats, the live stream never closing, the polling fallback stopping) and several warnings; all are fixed and each has a test.
Nothing live was touched: no Snowflake setting, no model enabled, no paid call, no Hub write, no email.

| Stage | Status | Where | Switch |
|---|---|---|---|
| S1 one ledger | Done: chat calls (Cortex and local) write the same ledger rows as missions (purpose `chat`, prompt hash only). Chat **routing is unchanged on purpose** (answers identical). | `llm.py`, `missions/models.py` (`record_chat`, `QUIET`) | `ATLAS_ROUTER=legacy` stops the rows |
| S2 tool catalogue | Done: 9 read tools wrapping existing functions, 3 action tools (reprocess, escalate, queue_human); permission, level, idempotency; reads shared for 15 min across missions | `missions/tools.py` | — |
| S3 planner | Done: template first; a planning model may only pick a catalogued template or skip optional steps (validated; off unless `ATLAS_MISSION_PLANNER=1`); graph validation (agents, dependencies, cycles, size); fan-out ≤ 25 with merge; plan editor (remove optional steps, profile, deadline); mission deadlines and task timeouts (late results discarded); per-task call cap (default 2); one replanning rule (tracking/extraction supported → date reconciliation before verification). New templates: multi_delay, eta_discrepancy, ata_failure, exception_report, carrier_compare, carrier_research | `missions/plan.py`, `registry.py`, `engine.py`, `classify.py` | `ATLAS_MISSION_PLANNER`, `ATLAS_MISSION_CALLS_PER_TASK`, `ATLAS_MISSION_TASK_TIMEOUT_S` |
| S4 actions | Done: action cards only from a Strong/Moderate finding (a carrier-vs-eHub contradiction allows only a re-run); approval per action with the action's **own** permission; execution only through `control.request("reprocess")`, `clearance.escalate`, a To Do task; Action Verifier: re-run = the run's `verified_success` for that reference after the request; escalation = present in the Clearance store; To Do = listed. Never re-executed after a restart. Audit `MISSION_ACTION_APPROVED / REJECTED / FAILED` + the engine's own audit name. Pending approvals in To Do | `missions/actions.py`, `server.py`, `todo.py` | **`ATLAS_MISSION_ACTIONS=1` to execute; OFF by default** |
| S5 Mission Control | Done: plan editor, approvals and outcomes, conflicts, model use by agent, lessons, notes, handoff, replay, analytics strip, Commander timeline, live stream (`/api/missions/<id>/stream`, SSE) with polling fallback | `static/hub/missions.js` | — |
| S6 memory | Done: outcome + lesson drafts; similar missions shown on a proposal; a person sends a lesson to knowledge review (`kb_govern`, observation); never automatic | `missions/memory.py` | — |
| S7 web | Done: Web Investigation over the self-hosted search; external claims unverified, never above Weak; tracking pages skipped; says plainly when search is not set up | `specialists.web` | `ATLAS_SEARCH_URL` |
| S8 Level 4 | Done: policy file (none by default); only valid, ≤ 30-day, unexpired policies; matching kind, grade, hypothesis and count; recorded as `policy:<id>` | `actions.policies()` | `ATLAS_MISSION_POLICIES` (file) |
| S9 advanced | Done: replay (a new proposal against today's records) and comparison, handoff, notes, analytics. **Not built**: scheduled analysis and what-if simulation | `engine.py` | — |

Not claimed: production readiness and Windows end-to-end verification; both need an authorised run on the tower.
The ten owner decisions in §20 remain open; the safe defaults stand (actions off, no planning model, no Level 4 policy).

### Model council (added 30 Oct)

Several models now work on the same step (`intelligence/missions/council.py`, `test_missions_council.py`, 20 checks):
- **Every model, found**: every chat model installed in the tower's Ollama is registered as `local:<name>`
  (capabilities by model family; embedding models skipped; vision models only for pictures), plus the
  allowlisted Cortex models that were missing (claude-sonnet-5, llama3.1-70b, mixtral-8x7b). Opus stays out.
  `ATLAS_MISSION_LOCAL_MODELS=configured` limits it to the configured model.
- **Council**: up to N members from different families (drill 0, normal 1, deep 2, emergency 3;
  `ATLAS_COUNCIL_SIZE`). Hypotheses: each member suggests causes in parallel; similar ones merge with
  "N models agree"; all stay open until the records decide. Review: a panel with no member from the
  drafter's family; every verdict and concern kept with its author; a split is shown. Wording: drafts in
  parallel, each fact-checked (plus a stricter check for numbers with units), the best that passes is used.
- **The tower stays free for the ATA run**: one local model call at a time (`ATLAS_LOCAL_PARALLEL`), Cortex
  calls up to `ATLAS_CORTEX_PARALLEL` (4); no local model while the run is busy (unchanged).
- Agreement raises attention, never the grade: the grade comes from the recomputed evidence.
