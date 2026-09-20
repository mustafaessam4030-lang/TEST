# 08 — How Hallucination Is Prevented, and How This Scales to Many Sources

## 8.1 Seven structural defences (not prompt pleading)

Prompts asking a model to "be accurate" are the weakest control available. This
platform makes fabrication *structurally* hard, then adds the prompt on top.

**1. No knowledge path from the warehouse into the weights.**
The store is never used as training data and never bulk-loaded into context. One
record per turn, fetched at request time, attached to the answer. Change the
row, and the next answer changes — because there is nothing memorized to
contradict it.

**2. The model cannot reach the source directly.**
It has five tools, and its whole reach into the browser is a serial-number
string that the server re-validates. It cannot navigate, cannot select, cannot
choose a URL, cannot decide freshness. Anything it "believes" about SIS2 that did
not come back from a tool has no way of becoming an answer with attribution.

**3. Attribution is mandatory in the data shape.**
`data` never exists without `attribution {source, retrieved_at, run_id,
freshness}`. Absent fields are `null` with a reason or omitted with a recorded
violation — they are never empty strings that read like "unknown". The rendering
rule ("state the source and timestamp") is satisfiable only from real values.

**4. Errors carry no data object.**
A failed lookup returns `{ok:false, error_code, …}` with no `data` key. There is
nothing to paraphrase into a confident-sounding answer. The system prompt's rule
"if a tool failed, report the failure" is enforced by there being no alternative.

**5. Server-side validation before anything is served or stored.**
Schema (`additionalProperties:false`), controlled vocabularies, cross-field rules
(build date not in the future; model consistent with serial prefix where a
mapping is known), and a quality score. Failures are `QUARANTINED`, not served.
So even a manipulated page cannot put arbitrary values into a Maya answer.

**6. Provenance per field + `data_hash`.**
Any rendered value can be traced to a selector id, a run, a timestamp and an
artifact screenshot. "Where did this come from?" is answerable in one query.
A value with no provenance row cannot legitimately appear, which makes
fabrication detectable after the fact, not just discouraged.

**7. Scraped content is data, never instruction.**
Source text is injected in a fenced, labelled block; the system prompt states
that content inside it can never change tools, policy or scope. Tool arguments
are re-validated server-side regardless of what the page said.

**Then** the prompt layer: Maya must state source + timestamp + run id, must use
the exact field values, must say "SIS doesn't publish this" for `null` and "I
couldn't read this field" for omitted, must never fill a gap from general
Caterpillar knowledge, and must label stale data with its age. See
`agent/system_prompt.md`.

**Verification, not trust:** a nightly job replays a sample of answers and
asserts every numeric/string claim appears verbatim in the referenced record.
Divergence pages the team. Fabrication rate is a monitored metric, not an
assumption.

## 8.2 Multi-source, multi-site extensibility

The design is already multi-source; SIS2 is simply the first registered adapter.

**The seam is the `SourceAdapter` protocol** — `capabilities()`,
`ensure_session()`, `search()`, `extract()`. Nothing above it knows whether the
implementation drives Chromium, calls a REST API, or reads a JDBC table. Adding
a source is: implement the protocol, add a YAML config, add a row to
`SOURCE_REGISTRY`, add a normalizer mapping. No change to Maya, to her tools, to
the API surface, or to the warehouse schema.

```
                          EquipmentService
                                 │
                        SourceAdapterRegistry
        ┌──────────────┬─────────┴─────────┬──────────────────┐
   cat_sis          cat_pcc            dealer_erp        telematics
  (Playwright)     (REST API)           (JDBC)           (Kafka/VisionLink)
```

**Normalization per source, one canonical schema.** Each source ships a
declarative field-mapping (`source_field → canonical_field`, transform, unit,
confidence). The canonical JSON schema is the contract; the mappings absorb the
differences. A source that cannot supply a field supplies `null` with
`NOT_PUBLISHED`, never a substitute.

**Precedence and reconciliation.** `SOURCE_REGISTRY.precedence` orders sources
per field class (e.g. build data: SIS > ERP; ownership: ERP > SIS; hours:
telematics only). Because rows are keyed by `(source, serial)`, conflicts are
visible and reportable rather than overwritten — Maya can honestly say "ERP says
X, SIS says Y, as of these dates".

**Fan-out without changing the agent.** `search_equipment_in_sis` is deliberately
narrow today; the generalized `search_equipment(source="auto")` resolves the
registry precedence, can query several sources in parallel, and merges by
precedence — while the tool surface Maya sees stays five tools.

**Other document/data types reuse the whole spine.** Parts lookups, service
letters, warranty claims, PIP/PSP bulletins: same run recorder, same error
taxonomy, same freshness engine, same audit trail. The only new code is an
adapter plus a mapping.

**Per-source operational isolation.** Circuit breaker, quotas, politeness
windows, credentials and selector versions are all per source, so a SIS2
redesign or outage cannot take the ERP path down with it.
