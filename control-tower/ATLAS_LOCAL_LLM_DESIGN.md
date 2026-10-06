# ATLAS — local LLM and free web research

Status: **implemented, optional, off by default.** Nothing is installed or
required; with no settings, ATLAS is the deterministic ATLAS. Not yet run
against a real Ollama or SearXNG install — only against stand-in servers in
`test_atlas_research.py`.

## 0. The paid integration was deleted

The earlier `intelligence/research.py` called the paid Anthropic Messages API
(web search + web fetch) when `ATLAS_RESEARCH_API_KEY` / `ANTHROPIC_API_KEY`
was set. That transport, its settings (`ATLAS_RESEARCH*`) and its docs are
removed — not disabled. `test_atlas_research.py` §8 fails the build if an
Anthropic, OpenAI or Gemini endpoint, key name, header or SDK reappears in the
product code or requirements.

## 1. What ATLAS is today (inspected)

| Layer | Files | Authority |
|---|---|---|
| Run facts, shipments, carriers, verification, human actions, writes/read-backs | `dashboard/assistant.py` (`RunData`, `_answer_core`, ~3,300 lines of rules) reading the bridge snapshot | **authoritative** |
| Failure intelligence: classification, RCA, recovery plan, learning status | `intelligence/failures.py`, `intelligence/learning.py`, `ml/recovery.py` | **authoritative** |
| PO answers | `dashboard/atlas_po.py` over `po.store` records | **authoritative** |
| Learned strategies, evidence images | `dashboard/atlas_learning.py`, `intelligence/evidence.py` | **authoritative** |
| Glossary, ranked causes (CONFIRMED/LIKELY/POSSIBLE/UNKNOWN) | `intelligence/knowledge.py` (deterministic) | deterministic knowledge |
| Shipment/error/general routing, conversational leads | `dashboard/atlas_intel.py` | deterministic |
| Web research (optional) | `intelligence/research.py` → self-hosted SearXNG + allow-listed fetch | labelled WEB, never a run fact |
| Phrasing (optional) | `intelligence/llm.py` (Ollama) behind `intelligence/factguard.py` | none — re-phrases only |

Contract: `POST /api/ask {question, context}` → `{answer, card, buttons,
actions, downloads, reference, evidence, suggestions, intent, sources,
details?, web_sources?, research?}`; `GET /api/ask/progress?id=` gives real
stage lines. Buttons and actions are whitelisted on the server and the page;
ATLAS never acts.

## 2. Design: the LLM is a phrasing layer over authoritative answers

```
ELAP run data / PO jobs / failure records ─► deterministic ATLAS (unchanged)
                                               │  reply + structured evidence brief
                         optional free web ──► │  (labelled WEB, with URLs)
                                               ▼
                                   LLMProvider (Ollama, local)  ── health-checked
                                               ▼
                                   FACT GUARD (deterministic)
                     every number, date, reference, status word and URL in the
                     LLM text must appear in the inputs; no success claim the
                     inputs don't make; labels kept
                                               ▼
                        pass → natural answer  (+ the deterministic reply as details)
                        fail / timeout / down → the deterministic answer, unchanged
```

The LLM never decides anything. Run status, extracted values, verification,
success and failure, and audit all stay with the deterministic code, and every
reply still carries the deterministic answer.

## 3. Components

**`intelligence/llm.py` (new): the provider abstraction.**
- `class Provider: name, health() -> {ok, model, detail}, generate(system, messages, timeout) -> text`.
- `OllamaProvider`: HTTP to `ATLAS_LLM_URL` (default `http://127.0.0.1:11434`).
  It uses `POST /api/chat` with `stream:false`, `options.temperature` 0.2, and a bounded
  `num_predict`. Health is `GET /api/tags`: the model must be listed. The health result is cached for 30 s.
- `NullProvider`: the default. ATLAS behaves exactly as today.
- Settings: `ATLAS_LLM_PROVIDER` (`none` | `ollama`), `ATLAS_LLM_MODEL` (for example
  `qwen2.5:7b-instruct` or `llama3.1:8b-instruct`), `ATLAS_LLM_TIMEOUT_S` (default 20),
  `ATLAS_LLM_RETRIES` (default 1, with backoff).
- `ATLAS_LLM_ALLOW_REMOTE=0` by default: only localhost or private-network hosts are
  accepted. This prevents pointing ATLAS at a hosted, billed endpoint by accident.
- stdlib `urllib` only. No SDK, no key field.

**`intelligence/factguard.py` (new).** It extracts numbers, dates, references
(BOL/AWB/container patterns), status words and URLs from the LLM output, and
rejects the output unless each one occurs in the deterministic reply, the
evidence brief or the labelled web snippets. This is a deterministic test, not a model judgement.

**`intelligence/research.py` (rewritten), web research that is free and optional.**
- Search goes through a **self-hosted SearXNG** instance (open source, JSON API) at
  `ATLAS_SEARCH_URL`. If that is unset, there is no search.
- Fetching only covers pages on an allow-list of official domains: carrier advisories,
  port authorities, vendor documentation. It honours robots.txt, sets a user agent,
  allows 8 s and caps pages at 512 KB.
- It never fetches carrier tracking pages, which carry CAPTCHAs.
- Results are cached for 30 minutes; asking for "latest" refreshes.
- The real progress lines (`/api/ask/progress`) are kept.
- Snippets reach the LLM labelled WEB with their URL. Without an LLM, ATLAS lists
  them as "Sources found" under the deterministic answer.

**`dashboard/atlas_intel.py` (changed).** `_enrich()` builds the deterministic
reply, appends labelled web results when SearXNG is set up, then `_phrased()`:
reply + brief (+ web) → provider → fact guard → reply. The
labels FACT / CURRENT_RUN / WEB_RESEARCH / LEARNED_PATTERN / INFERENCE /
RECOMMENDATION / UNVERIFIED stay in the brief and survive the guard.

**Servers.** `GET /api/atlas/llm` reports the local health status (provider, model,
ok). It never returns a prompt or any data.

## 4. Files changed

| File | Change | Why |
|---|---|---|
| `intelligence/research.py` | rewrite | remove the paid API; SearXNG + allow-listed fetch |
| `intelligence/llm.py` | new | provider abstraction, Ollama, Null |
| `intelligence/factguard.py` | new | the deterministic guard |
| `dashboard/atlas_intel.py` | edit | compose through the provider + guard; fall back |
| `dashboard/server.py`, `controlplane/app.py` | small | `/api/atlas/llm` health |
| `test_atlas_research.py` | rewrite | stand-in Ollama and SearXNG HTTP servers; guard, timeout and fallback tests |
| `run_tests.py` | env | `ATLAS_LLM_PROVIDER=none` for every other suite |
| `PLATFORM.md`, `START_HERE.md` | docs | install Ollama, pull a model, settings |

Nothing in `assistant.py`'s rules, failure intelligence, PO, the bridge or the
runner changes.

## 5. Zero paid dependencies: confirmed by design

Ollama is MIT-licensed and runs locally. Open-weight models are free to use;
licences need a review (Qwen2.5 Apache-2.0; Llama 3.1 community licence).
SearXNG is AGPL and self-hosted. Python stdlib only. There is no API key,
billing account or subscription anywhere. With nothing installed, ATLAS is exactly
today's deterministic ATLAS.

## 6. Production requirements, mapped

- **Bounded timeouts**: 20 s for the LLM, 8 s for a fetch, 10 s for a search.
- **Retries**: one retry with backoff.
- **Health check and fallback**: covered in §3; any failure returns the deterministic answer.
- **No secrets in logs**: prompts are built from the same redacted brief as today (no URL queries, no
  secret-named keys), and prompts are never logged.
- **Deterministic tests**: stand-in servers plus the fact guard.
- **No regression**: `NullProvider` is the default.
- **Hardware note**: a 7–8B model on CPU answers in roughly 10–40 s. A GPU, or a 3B model, is needed for
  conversational speed. Answers stay deterministic until the model is healthy.

## 7. Still open

- Choose the model and the host (control-plane box, or a GPU box on the same
  network) and review its licence.
- Install and run SearXNG and Ollama there; check `GET /api/atlas/llm`.
- Try both against real questions before relying on the phrasing; until then
  ATLAS's deterministic answers are what operators see.
