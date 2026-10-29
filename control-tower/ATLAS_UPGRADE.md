# ATLAS upgrade: logistics expertise and fast understanding

Measured on 28 Oct 2026. Every figure below comes from a run whose files are
named under **Reproducing**. Nothing here was measured on the production
Windows Server, and nothing was deployed.

## 1. Where the time went (baseline, before any change)

Each question's path was timed stage by stage (`intelligence/trace.py`). Two
setups were measured:

- **Without a model:** the original code at 11d47af.
- **With a model:** ATLAS's own unmodified model client, against the same
  quantised `qwen3.5:4b` that Ollama serves, run through llama.cpp on a
  4-core CPU with 15 GB RAM. This is not the server.

**No model.** The rules, the knowledge engine and the calculations answer in
a median of 3.5 ms (p90 9 ms). The first question after start can take up to
about 200 ms while the knowledge loads.

**With the 4B model**, all of the latency was in model calls:

- **Calls per question.** Every question the rules did not answer outright
  made **two sequential calls**:
  - "understand": about 14–15 s (a 416-token prompt at about 60 tok/s, then
    about 30 tokens at about 4 tok/s);
  - "compose": 20–75 s, mostly generation at 3.6–4.2 tok/s under the JSON
    constraint. Without the JSON constraint it is 5.4 tok/s.
- **What the operator saw.** The rules' answer existed after 1–3 ms, but
  nothing was shown until the model finished: 22–77 s.
- **Routing errors made it worse:**
  - A follow-up about the shipment in context ("and what is its ETA?") went
    to the model: 37 s.
  - Every Arabic question went to the model, a definition included: 48 s.
  - The model read "ايش الوضع" ("what's the status?") as small talk and
    replied "Hello my friend, how are you today?".

## 2. What changed

| Change | Effect (measured) |
|---|---|
| **Stage timing on every answer.** `Server-Timing` header, `timing` in the reply, `GET /api/atlas/perf` (Admins: p50/p90 per stage). Model calls carry Ollama's own load, prompt and generation durations. | The baseline above; ongoing measurement on the server |
| **Deterministic router** (`intelligence/router.py`), consulted before the run's rules. Labels: operational, troubleshooting, knowledge, calculation, the platform's rules, action, follow-up, ambiguous, chat, out of scope. Under 0.1 ms per question. | General questions are no longer answered with this run's figures. Previously "average", "count", "compare" or "clearance" in a general question returned run statistics |
| **No "understand" call when the rules already recognise the question**, Arabic included. Vague English readings ("which failed?" read as a filter) still get the model's restatement, which measurably helps the rules. | Arabic run questions: 2 calls → 1. Small talk: 2 → 1 |
| **Follow-ups about the shipment in context stay with the records** | "and what is its ETA?": 37 s → 1 ms |
| **The records' answer is shown at once** (the progress channel the chat already polls), labelled "Quick answer from the records — ATLAS is still writing the full answer". Never for actions. | Time to first useful answer on model questions: 22–77 s → under 0.65 s (the poll interval) |
| **Knowledge answers never go to the model**, Arabic included | Arabic definition: 48 s → 3 ms |
| **Clarifying question** when a message is too vague and nothing is in context | Replaces "That isn't something this run records" |
| **Knowledge: 165 → 223 items** in 7 domains. New areas: purchase orders and suppliers; warehousing and inventory; road (CMR, FTL/LTL), ocean (delivery order, THC/BAF, gate-in/out) and air (HAWB/MAWB, consolidation); customs facilitation (6 items cite WTO Trade Facilitation Agreement articles, read on 28 Oct); data quality (time zones, references, units); KPIs (fill rate, inventory turns, MAPE, perfect order); 10 diagnostic playbooks. | See section 4 |
| **Scenario reasoning:** for a described problem, ATLAS lists what is known (quoting the operator), possible causes (each a hypothesis, with what would confirm or rule it out, ranked by what the message mentions), what is missing, any calculation, next steps (consequential ones say who must approve), and what it cannot tell. When the message names one of this run's shipments, the records answer comes first and this reasoning follows, labelled separately. | See section 4 |
| **11 new calculators:** cubic metres, averages, lead time and transit days from written dates, hours between date-times, tiered demurrage tariffs, fill rate, inventory turns, MAPE (refused when an actual is zero), reorder point, cost variance in %. Arabic units and numbers are read ("سم", "كيلو", "186 ألف"). | See section 4 |
| **Optional local semantic retrieval** (`ATLAS_EMBED_MODEL`, Ollama's `/api/embed`; off by default). It only chooses *which* entry answers, never *whether* to answer. It needs logistics vocabulary in the question; item vectors are stored by text hash. | See section 4 |
| **Model-output cache:** available, off by default (`ATLAS_LLM_CACHE_S`) | A model's output is not deterministic; reusing it hid a later, rejected phrasing in the existing tests |

## 3. Latency after the change (same questions, same CPU)

These are 19 representative questions: run records, follow-ups, knowledge,
calculation, open questions, small talk and Arabic. In the table, "useful" is
when the operator first sees a usable answer: the final answer before, the
early answer now.

| Question | Before: total / useful / calls | After (4B): total / useful / calls |
|---|---|---|
| run, shipment, knowledge, calculation (11 questions) | under 0.04 s / same / 0 | under 0.02 s / same / 0 |
| "and what is its ETA?" (follow-up) | 37.1 s / 37.1 s / 2 | 0.001 s / 0.001 s / 0 |
| "show me only the DHL shipments" | 58.5 s / 58.5 s / 2 | 62.4 s / 0.002 s / 2 |
| "Why are DHL shipments slower this week?" | 77.0 s / 77.0 s / 2 | 92.5 s / 0.008 s / 2 |
| "what does UNEXPECTED PAGE STATE mean for DHL?" | 55.9 s / 55.9 s / 2 | 62.9 s / 0.004 s / 2 |
| "I'm so tired today" | 27.6 s / 27.6 s / 2 | 14.6 s / 14.6 s / 1 |
| "ايش الوضع" | 22.3 s / 22.3 s / 2 | 42.8 s / 0.002 s / 1 |
| "كم شحنة اتحدثت" | 38.9 s / 38.9 s / 2 | 28.9 s / 0.004 s / 1 |
| "فين الشحنات اللي فيها مشكلة" | 63.3 s / 63.3 s / 2 | 36.0 s / 0.004 s / 1 |
| "ما هو الديمراج؟" | 48.3 s / 48.3 s / 2 | 0.003 s / 0.003 s / 0 |
| **Model calls for the 19** | **18** | **10** |
| **p90 time to first useful answer** | **63.3 s** | **0.01 s** in the server; up to 0.65 s in the browser (the poll interval) |

These are single runs on a CPU. The variation between runs is several
seconds, so the open questions' totals (58→62 s, 77→93 s) mean "unchanged",
not "slower". They still make two model calls by design.

**Model size** (the new code, the same questions):

| Model | RAM (server process) | Generation | Prompt processing | Quality of the answers it wrote |
|---|---|---|---|---|
| qwen3.5:4b | about 6.0 GB | 3.6 tok/s | 59 tok/s | Correct in English and Arabic. One answer called a failure "likely just a temporary glitch" — framing the fact guard cannot catch |
| qwen3.5:2b | about 3.2 GB | 5.7 tok/s | 140 tok/s | Mistranslated "the Hub" into Arabic as "الحبكة" (a plot) |
| qwen3.5:0.8b | about 1.6 GB | 8.5 tok/s | 251 tok/s | Invented an Arabic account of the run that the fact guard did not catch; 2 of 3 open answers malformed |

**Keep the 4B.** The smaller models are 1.5–2.4× faster at generating but
fail the quality bar. The latency gain comes from making fewer calls and
showing the records' answer first, not from a smaller model. The bge-m3
embedding model adds about 2 GB of RAM (measured: 3.7 GB for the server process
with bge-m3 and the 1.6 GB 0.8B chat model loaded) and 70–120 ms per knowledge
question.
Its index of the 223 items takes about 1.5 minutes to build once, in the
background at server start.

## 4. Logistics understanding on unseen questions

The question pool was written by a separate agent that did not read the
repository or its knowledge items: 226 questions in operators' own wording,
39 of them in Arabic. It was split once (seed 20261028), stratified by type,
before any change:

- **Development (90):** used to find gaps and set thresholds.
- **Held-out (136):** frozen, never read and never used for tuning, scored
  once at the end. Its SHA-256 (`knowledge/eval/heldout_v2.sha256`) was
  recorded at the split and matched at scoring time.

Expected concepts are matched to items by name at scoring time.

**Held-out results** (`python tools/atlas_eval.py --set knowledge/eval/heldout_v2.json`):

| Measure | Original (11d47af) | New, keyword | New, + bge-m3 |
|---|---|---|---|
| Routing, as shown by the reply (136) | 25% | 42% | 43% |
| Knowledge: answered from the right concept (46) | 11% | 46% | 50% |
| Retrieval Recall@1 / Recall@3 / MRR (43 or 40 coverable) | 13% / 25% / 0.24 | 49% / 61% / 0.57 | 53% / 63% / 0.58 |
| Confident answer from a wrong concept | 0% | 9% | 11% |
| Abstained correctly when nothing covered it | 6/6 | 3/3 | 1/3 |
| Calculations right (exact value, refusal or request for inputs) (24) | 17% | 38% | 38% |
| Scenarios: structured reasoning given (30) | 0% | 27% | 27% |
| Scenarios: missing information asked for (word-overlap proxy) | 5% | 16% | 16% |
| Scenarios: abstained when the evidence was insufficient (7) | 14% | 29% | 43% |
| Scenarios: a cause stated as confirmed | 0% | 0% | 0% |
| Strictly passed, by difficulty 1 / 2 / 3 | 13/48, 7/64, 1/24 | 17/48, 18/64, 5/24 | 17/48, 22/64, 4/24 |
| Strictly passed, English / Arabic | 19/116, 2/20 | 36/116, 4/20 | 38/116, 5/20 |
| Time per question without a model, p50 / p90 | 3.5 / 9 ms | 4.2 / 13 ms | 124 / 205 ms |

What this does and does not show:

- **The improvement is real** on questions written by someone else and never
  used for tuning. Answering from the right concept went from 11% to 46–50%,
  and strict passes from 21 to 40–43 of 136.
- **The level is still modest.** About half of everyday-worded knowledge
  questions are still missed. Calculations parse only about 4 in 10 of the
  held-out phrasings.
- **I overfit the development set**, and the gap shows it: calculations 93%
  on development vs 38% held-out; routing 66% vs 42%; knowledge 57% vs 46%.
  The held-out numbers are the ones to believe.
- **Confident wrong answers rose from 0% to 9–11%.** The original answered
  almost nothing, so it rarely answered wrongly. Every answer still carries
  its item name, sources and "not yet reviewed" status. Low-confidence
  matches are labelled "Closest entry".
- **Semantic retrieval (bge-m3) is a small net gain:**
  - more questions answered right (+4 percentage points), and more scenarios
    that should abstain do;
  - but one more confident wrong answer, and worse abstention when nothing in
    the knowledge base covers a question.

  Keep it optional. Turn it on only after Mantrac's own reviewers check
  answers from both modes.
- **Scenario scores are automatic proxies:** headings, concept names and word
  overlap. They do not grade reasoning quality. That needs a logistics
  reviewer reading the answers.
- **ATLAS does not understand logistics deeply** because of this. It answers
  from 223 unreviewed entries, 10 playbooks and tested calculators.

## 5. Remaining bottlenecks and limits

- **Open English questions still take 30–90 s** for the full answer. They
  make two model calls, because the model's restatement helps the rules. The
  early answer covers the wait. The next saving would be to make the rules
  read those questions well enough to drop the restatement.
- **Generation speed on a CPU:** 3.6 tok/s for the 4B under the JSON
  constraint. A GPU, or shorter compose output, would cut the 20–60 s compose
  step.
- **One model at a time** (Ollama's default, and `ATLAS_LLM_SLOTS=1`). With
  semantic retrieval on, embedding calls go to the same Ollama. Ollama
  normally runs two loaded models side by side, but that is not verified on
  the server; the 2 s embedding timeout falls back to keywords.
- **Everyday wording** still defeats about half of the knowledge questions,
  and calculation parsing is narrow.
- **All 223 items are unreviewed.** 159 are general knowledge with no
  checked source. 58 new items have no Arabic definition (they have Arabic
  terms).
- **Latency figures are from a 4-core test container**, not the Windows
  Server. Run the same measurement there: `GET /api/atlas/perf` after a day
  of use.

## 6. Recommended next steps

1. **Measure on the server.** Read `/api/atlas/perf` after a day of real
   questions; it shows where time goes per stage, with no question text.
2. **Expert review of the 223 items and 10 playbooks**, through the existing
   review API.
3. **Have Mantrac staff write a second held-out set**, in their own words, and
   use it to decide on `ATLAS_EMBED_MODEL=bge-m3`.
4. **Widen the calculation phrasings and the router's cues from the query
   log** (misses are logged for reviewers), measured against a fresh held-out
   set, not this one.
5. **Shorten the compose output** (or allow a non-JSON format) and measure
   it, before considering hardware.

## Reproducing

```
python tools/atlas_eval.py --set knowledge/eval/dev_v2.json
python tools/atlas_eval.py --set knowledge/eval/heldout_v2.json
ATLAS_EMBED_MODEL=bge-m3 python tools/atlas_eval.py --set knowledge/eval/heldout_v2.json
python tools/atlas_eval.py --root <checkout of 11d47af> --set knowledge/eval/heldout_v2.json
python test_atlas_upgrade.py
```

The latency benchmark drove `dashboard.assistant.answer` with the
conversation layer on, against an Ollama-compatible server over llama.cpp:
the same Q4_K_M files that Ollama's `qwen3.5:4b`, `qwen3.5:2b` and
`qwen3.5:0.8b` tags serve. Ollama's release download was not reachable from
the test environment. The prompt prefix was reused between calls, as Ollama
does.

## Follow-up, 29 October: understanding first, page awareness, knowledge gaps

Reported problems from operators, and what changed:

- **"are you good?" answered with the cargo/freight entry.** Small talk
  (how are you, عامل ايه, مرحبا) is now a greeting answered at once in the
  operator's language, and "good" no longer matches "goods". A
  knowledge-base match on a question the router did not take as logistics is
  not answered as it stands: the model reads it first, and without a model
  ATLAS says it did not understand and names the entry it would have guessed.
- **"do you know Egypt?" dumped every EG figure.** A yes/no question gets one
  line with the details offered; a shipment number the export does not hold is
  said to be missing.
- **ATLAS knows the open page.** The dashboard sends the page with each
  question; "what am I looking at?" / "explain this page" / "الصفحة دي فيها
  ايه" is answered from that page's data.

Knowledge: 223 → 238 items. New: RoRo, breakbulk, out-of-gauge, direct vs
transit, shipper's load and count, lithium batteries, critical slow-moving
spares, service level, proforma invoice, gross/net/tare weight, CBM, freight
prepaid/collect, customs bond, and — **unverified, for review** — Egypt's
Nafeza/CargoX and the documents for an import into Egypt. Egyptian-Arabic
names added (الخط الأحمر, الأرضيات, الديمرج, ديتنشن, تليكس ريليز, منفستو…), and
Arabic attached prepositions (للـ بالـ والـ) are stripped before matching.

Routing: the knowledge base's own vocabulary counts as logistics; two named
concepts set against each other are compared; general customs questions no
longer go to the Clearance export's figures; "what is X in Y" answers X; a
quoted status is what the question is about; a question about one concept is
not turned into a scenario.

Measured (no model, same tool as above):

| Set | Metric | Start of 29 Oct | Now |
|---|---|---|---|
| dev (worked from) | routing | 66.7% | 74.4% |
| dev | knowledge correct | 53.3% | 96.7% |
| held-out (not tuned on) | routing | 42.6% | 49.3% |
| held-out | knowledge correct | 45.7% | 52.2% |
| held-out | unsupported (confident, wrong entry) | 8.7% | 13.0% |
| held-out | scenario unsupported | 13.3% | 23.3% |

The dev gain is mostly fitting to the dev set; the held-out gain is the
honest one, and it is modest. The unsupported rate went **up** on held-out
(two more of 46 knowledge questions, one more scenario): one traced to a
ranking threshold shifted by a longer entry, not investigated further to
avoid tuning on the held-out set. Two probe sets of 120 questions written on
29 Oct (not in either eval set) were read answer by answer; all but one
(an unusual Incoterms split with no standard rule) now land on the right entry.
