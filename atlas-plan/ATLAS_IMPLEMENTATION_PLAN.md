# ATLAS — Prioritised Implementation Plan (for approval; nothing implemented)

Effort is in focused developer-days, including tests. It assumes the existing suite stays the
safety net and every change lands on its own branch with focused + full suite runs. **Live (WIN)
verification needs an operator on the Windows worker and isn't counted in the days.**

## P0 — Protect the release (in progress)

| # | Item | Reuse | Status |
|---|---|---|---|
| 0.1 | LOG_LINE replay crash + regression test | `server.replay` | Done on `claude/atlas-demo-stabilize` (local, **not pushed**) |
| 0.2 | `test_atlas_skills` `ATLAS_SEARCH` isolation | — | Done, same branch |
| 0.3 | `test_flight_status` escape warning | — | Done, same branch |
| 0.4 | Windows demo validation | `START_ATLAS_DEMO.bat`, `verify_ehub.bat` | **Blocked here**: needs the Windows PC |

## P1 — Truthful knowledge (low risk, no runtime behaviour change). About 4–5 days

| # | Capability | Reuse | Missing piece | Test | Evidence it works |
|---|---|---|---|---|---|
| 1.1 | Sources + freshness on every reply | `reply["sources"]` | `{kind, ref, as_of}` from snapshot version, record timestamps, `file#section` | B1, A1 | Every OFF reply has valid sources; UI shows "as of" |
| 1.2 | Stale-doc guard | `handbook.GUIDES`, `search_guides` | Section `superseded` markers for the known stale sections; topic `sources` with a symbol-exists test | A5, A7 | Stale sentence never returned; removing a cited symbol fails CI |
| 1.3 | Capability registry | `handbook._carriers/_held/_limits`, `llm.health`, `research.enabled`, `dhl_api.enabled` | `intelligence/capabilities.py` + handbook topic | A4, F1–F4 | Answer equals values computed from code; changing `PRODUCTION_HOLD` changes the answer |
| 1.4 | Fix ATLAS wording issues found | `brief.py`, `failures.py` stage mapping | "Wrote to Hub" only with read-back; stage from record | B2, G2 | New cases pass; no success word without `verdict_ref` |

## P2 — Auditable answers and narrow tools. About 5–6 days

| # | Capability | Reuse | Missing piece | Test | Evidence |
|---|---|---|---|---|---|
| 2.1 | `request_class` on every reply | `detect_intent`, skill/PO/handbook detectors | Field + UI badge | Routing table in A–F | Class matches expected on the evaluation set |
| 2.2 | Closed tool registry | `UI_ACTIONS`, `OPERATIONS`, `ControlChannel` | One registry with typed args, role and confirmation flag | D1–D3, D9 | Model-proposed unknown tool / bad arg rejected |
| 2.3 | Role check on chat actions (local dashboard) | `dashboard/accounts.py`, `controlplane/rbac.py` | Enforce on `/api/ask` operations | D2 | Viewer gets refusal + audit line |
| 2.4 | ATLAS action/answer audit | `controlplane/audit.py`, accounts audit | `atlas_actions.jsonl` locally; platform audit action | G1 | Every execute reply has an audit record |
| 2.5 | Re-run page-scope fix | `update_eta.py:12106`, `take_reprocess` | Keep unmatched requests until their page is reached, or report the drop truthfully | D4 | Stub run with the ref on page 2 is re-run |

## P3 — Controlled carrier research (higher risk, touches the runner). About 8–12 days, plus WIN time

| # | Capability | Reuse | Missing piece | Test | Evidence |
|---|---|---|---|---|---|
| 3.1 | `resolve(ref, carrier)` | `carrier_provider`, `hub_reference_kind`, `airline_from_awb` | Pure wrapper with reason | C3 | Table test over all prefixes / ocean names |
| 3.2 | `lookup_only` request through the control channel | `ControlChannel`, `get_provider_result`, pacing/holds | New request type; runner runs the adapter and validation, **stops before** `update_internal_shipment` | C1, C4, C7, C9 | Hub-write counter stays 0; holds respected |
| 3.3 | Lookup record + evidence on success | `intelligence/evidence.py`, failure captures | Capture page text, screenshot, URL and time for FOUND too | C1, C2 | Record has SHA-256 refs; file exists |
| 3.4 | Validation verdicts as data | `arrival_events.decide/basic_check/check_before_write` | Return structured reasons; stale flag | C5, C6 | Conflicts/stale → NEEDS_REVIEW, not offered for write |
| 3.5 | Human step for lookups | `human_queue`, Open & Continue | NEEDS_PERSON in the record | C8, E2 | Task appears; code never read |
| 3.6 | Hand-off to write | `reprocess` + 2.2 confirmation | Only from FOUND + PASS lookups, Operator, confirmed | D3, D8 | Read-back verdict + run ID quoted from records |

**Live gate for P3:** C2 and D8 on the Windows worker with an approved, non-sensitive shipment
list and written approval for D8's single write.

## P4 — Evaluation harness. About 3–4 days (parallel with P1–P2)

- `eval/atlas_cases.json` from `ATLAS_ACCEPTANCE_TESTS.md` (OFF cases).
- `tools/evaluate_atlas.py`: runs cases against `assistant.answer` (rules) and optionally the model;
  scores correctness, attribution, false success, abstention, safety, latency; writes a report.
  Extends the pattern of `tools/evaluate_eta_diagnostic.py` and `atlas_demo_acceptance.py`.
- Added to `run_tests.py` (OFF subset, blocking on false success and safety).

## Dependencies

| Dependency | Needed for | Status |
|---|---|---|
| Windows worker with eHub credentials, Edge, Playwright | 0.4, C2, D8, E2-WIN | Not reachable from this environment |
| Approved, non-sensitive test shipments per carrier | C2, D8 | **Needs the operations owner** |
| Written approval for any Hub write | D8 | Required |
| Ollama + model on the serving PC | MODEL cases, latency | Optional; **not verified** |
| SearXNG | C10 web results | Optional; not installed |
| `DHL_API_KEY` | DHL API path | Optional; unknown whether one exists |

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `update_eta.py` is 12k lines; P3 touches the main loop | Regressions in the core run | Smallest possible seam (one request type), full suite + `test_work_list.py`/`test_human_queue.py` which drive the real `main()` |
| Carrier anti-bot / access restrictions (CMA CGM held already) | Lookups blocked, operator load | Pacing and holds reused; NEEDS_PERSON path; no bypassing |
| Selectors drift on real sites | Lookups fail silently | Evidence on every lookup; `pagecheck.py` site-change detection |
| CPU-only model latency (docs claim 24–91 s) | Poor chat UX | Rules answer first; model optional; measure with `autoconfig.bench` before rollout |
| Stale docs quoted as fact | Wrong answers to the team | P1.2 before anything else |
| Hub writes from chat | Data integrity | Only via existing `reprocess` + read-back; Operator + confirmation + audit |
| Scope creep into a "generic chatbot" | Untestable claims | Closed tool list; capability registry answers "no" truthfully |

## Suggested order

P0 (finish, push on approval) → P1 → P4 skeleton → P2 → WIN session for C2 → P3 → WIN session for
D8. Total ≈ 20–27 developer-days plus operator sessions. **Approval is requested before P1 starts.**
