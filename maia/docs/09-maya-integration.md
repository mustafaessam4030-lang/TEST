# 09 — What Was Added to Maia (and how to switch it on)

Maia was **not** rebuilt. Her NLU, intent classifier, retrieval corpus, planner,
local rules engine, action renderer and trace panel are untouched. The equipment
capability was added as a new layer plugged into nine existing hook points.

Files:

| File | Role |
|---|---|
| `frontend/mantrac-support-v8.html` | your original, byte-for-byte |
| `frontend/maia-equipment.js` | the new tool layer (standalone, reviewable) |
| `frontend/build_v9.py` | additive patcher: v8 + layer → v9 |
| `frontend/mantrac-support-v9.html` | the built result |
| `frontend/test_equipment.mjs` | 23 behavioural checks on the new layer |

`build_v9.py` asserts each anchor appears **exactly once** before patching, so
if you edit v8 and an anchor moves, the build fails instead of producing a
half-patched file. Rebuild any time with `make build-maia`.

## The nine insertion points

| # | Where | What was added |
|---|---|---|
| 1 | `<style>` | `.eq-*` card styles, in her existing yellow/dark language |
| 2 | `CFG` | `CFG.equipment {apiBase, actor, authToken, timeoutMs, pollMs, clientCacheMs}` |
| 3 | `RE` + `extractEntities` | Cat 17-char PIN and explicitly-labelled serials (AR/EN). The old `serial` pattern still matches first |
| 4 | `INTENTS` | a new `equipment_lookup` intent |
| 5 | `plan()` | a serial + a data request pins the intent (the call itself is not made here) |
| 6 | new module | `const EQUIP = (() => { … })()` — the whole tool layer |
| 7 | `dispatch()` | runs the tools **before** the model, injects the record as grounding, merges attribution, appends the card action, attaches the tool trace |
| 8 | `executeActions()` | `case 'equipment_card'` |
| 9 | `systemPrompt()` + `renderTrace()` | the EQUIPMENT RECORD block, and tool steps in "How I got this" |

## How a turn now runs

```
user: "مــايا، دوريلي على الداتا بتاعة المعدة Serial Number SN123456"
   → detectLang → ar
   → extractEntities → serial: SN123456
   → classifyIntent → equipment_lookup
   → retrieve() → local corpus docs                (unchanged)
   → EQUIP.maybeLookup()                           ← NEW, runs before the model
        ├ get_equipment_from_database → miss
        └ search_equipment_in_sis → record + attribution
   → EQUIP.injectDocs() puts the record at the top of the grounding docs
   → ENGINE.answer() / callClaude() with EQUIP.promptBlock() in the system prompt
   → EQUIP.mergeAnswer() guarantees source + retrieved_at + run id are in the reply
   → executeActions() renders the equipment card
   → trace panel shows: tools → run id → data source → freshness
```

The model is called **after** the data exists. It never decides whether to hit
SIS, never sees a URL or a selector, and never receives a credential.

## Switching it on

1. Deploy the API (`docker compose up maia-api`, or your cluster).
2. Capture the SIS2 selectors once (`python scripts/capture_selectors.py --serial …`),
   fill `config/sources/cat_sis.yaml`, bump `selector_version`.
3. Put the SIS credentials in Vault, set `auth.secret_ref`.
4. Set `MAIA_ALLOW_LIVE_AUTOMATION=true` (off by default so nothing touches a
   partner site by accident).
5. In v9, set `CFG.equipment.apiBase` to your API origin — **through your own
   BFF**, so the browser never holds a long-lived token. `authToken` should be a
   short-lived, per-session token your backend mints.

Until step 5 is done, the layer reports `NOT_CONFIGURED` and Maia says the
service isn't connected. It never fabricates equipment data to fill the gap —
that is the whole point, and `test_equipment.mjs` asserts it.

## Behaviour you get for free

* **Cache-first.** A fresh record answers with no browser, no SIS call.
* **Stale is labelled, never silent.** Old data is presented with its age and an
  explicit refresh offer; the card shows a "Stale · 210 days ago" badge.
* **Failures show the failure.** The failure card shows the error code and run
  id, states that nothing is guessed, and offers a retry or a ticket.
* **Absent ≠ unknown.** `null` fields render as "not published by SIS"; fields
  that could not be read render as "couldn't read this run" — two different
  sentences, because they mean two different things.
* **One question = one lookup.** In-flight de-duplication plus a short browser
  cache; a repeated question does not start a second SIS run.
* **Auditable in the UI.** "How I got this" now lists each tool call, the run id,
  the data source and the freshness — the same ids you can query in Snowflake.

## What deliberately did NOT change

Parts search, fault codes, quote basket, service/ticket forms, branches,
gensets, voice, photo upload, the local offline engine, and every existing
intent behave exactly as before. If `CFG.equipment.enabled = false`, v9 is
functionally identical to v8.
