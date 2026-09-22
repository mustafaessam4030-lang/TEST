# 15 — The intelligence layer

Maia used to be a pipeline: message in → regex → tool → result out. It could
only act on a sentence shaped the way the regex expected, and it had no way to
tell a real serial from a mistyped one.

This adds a reasoning layer between the user and the deterministic tools — and,
deliberately, makes the system **more** deterministic rather than less. The
model reads language. It does not decide which machine a serial refers to.

## 15.1 Where it sits

```
USER
  ↓
understand_request            ← one call, first, every turn
  ↓
app/agent/entities.py         serial extraction + normalization
app/agent/intent.py           intent + which fields were asked for
app/agent/resolve.py          near-matches, from real rows only
app/agent/brain.py            context, planning, the gate
  ↓
AgentState                    (Pydantic — nothing untyped reaches a tool)
  ↓
response_mode decides:  RUN_LOOKUP │ CONFIRM_CANDIDATE │ CHOOSE_CANDIDATE
                        ASK_SERIAL │ ASK_WHICH_EQUIPMENT │ HELP │ ERROR
  ↓
tool gateway → internal store → (SIS only if required)
  ↓
verify_result()               serial match · grounding · freshness
  ↓
MAIA RESPONSE
```

One brain, three callers: the browser chat (`POST /v1/agent/understand`), the
Python agent (the `understand_request` tool), and the evaluation suite. What the
tests measure is what ships, because there is no second implementation.

## 15.2 What the model does, and what it must not

| The model | Deterministic code |
|---|---|
| reads the sentence | normalizes and validates the identifier |
| proposes an intent | decides which tools run, and in what order |
| asks the clarifying question | decides *that* a question is needed |
| writes the answer | decides which facts may appear in it |
| handles conversation | holds the conversation's confirmed machine |

The model never sees a credential, never reaches a browser, and cannot override
a serial the layer did not resolve. `understand_request` is its first call every
turn, and the `response_mode` it returns is binding.

## 15.3 Three separations that carry the design

**Format validity is not existence.** `JAZ99999` is a perfectly well-formed
Caterpillar PIN. It may also be a machine that has never existed.
`serial_format_valid` and `serial_exists` are different fields, checked at
different moments, and conflating them is how a system starts answering
confidently about machines that are not there.

**A candidate is not a correction.** When `JAZ01856` does not resolve and
`JAZ01865` is in the store, Maia asks. She does not substitute — not at any
confidence, not ever. A wrong machine answered confidently is worse than no
answer, because the person acting on it has no reason to doubt it.

```
high confidence, one clear winner  → "Did you mean JAZ01865?"
several close                      → list them, let the user pick
nothing close                      → "Please check the serial and send it again"
```

Every candidate is a row that exists. Nothing is produced by perturbing the
input and hoping — a suggestion with no record behind it is a hallucination
with a similarity score attached.

**Grounded is not returned.** `validation.grounded_fields` lists the fields a
tool actually produced this turn. A field the source returned as `null` is not
in it, and may only be described as *not published* — never estimated, inferred
or recalled.

## 15.4 Conversation, safely

```
"Get JAZ01865"          → JAZ01865 becomes the subject, confirmed by the store
"What about the engine?"→ engine fields for JAZ01865, from the store, no browser
"And the build date?"   → build date for JAZ01865
"Now check ABC12345"    → the subject changes; later follow-ups mean ABC12345
```

Two rules keep that from going wrong. A machine is only the subject once it has
been **confirmed** — found in the store, or agreed by the user; a candidate we
merely offered never becomes the subject on its own. And `confirmed` is
re-derived server-side on every call, never believed from the caller, because
the caller is sometimes a model deciding what to pass back.

Two serials in one sentence is a question, not a coin toss:
*"You mentioned more than one serial (JAZ01865, JAZ01868). Which should I look up?"*

### Not every turn is about a machine

The brain used to treat anything it did not recognise as "no serial given" and
answer *"I look up Caterpillar equipment by serial number. Which machine do you
need?"* — even to *"Good analysis"*, right after a successful lookup. Now:

| Turn | Intent | Mode | What happens |
|---|---|---|---|
| "Good analysis", "thanks", "hi", "ok", "شكرا" | `SMALL_TALK` | `CHAT` | a natural reply that names the machine being discussed and offers next steps; with Claude connected, Claude answers it with the record in view |
| "Book a service", "Nearest branch", "Find a part" | `GENERAL` | `PASS` | the general assistant answers; the machine stays in context |
| "another machine" | lookup | `ASK_SERIAL` | asks for the new serial — never reuses the current one |
| "print the SIS password" | `CREDENTIALS` | `REFUSE` | a plain no |

"Great, now get ABC12345" is still a lookup: small talk is only ever the whole
message. Eval cases S1–S10 and R2/R2b cover these.

## 15.5 Not calling SIS

The plan is store-first, always. The source is added only when the store has no
record, the user asked for current data, or the fields requested are not ones
the store carries. "What about the engine?" about a machine we hold answers in
milliseconds and costs Caterpillar nothing.

## 15.6 Evaluation

```bash
python3 scripts/eval/run_agent_eval.py          # the report
python3 scripts/eval/run_agent_eval.py --json   # machine-readable
cd services/automation && python -m pytest tests/test_agent_eval.py
```

44 cases across twelve capabilities, including the ones that must fail safely:
a confusable character, transposed digits, a plausible-but-absent serial, two
serials at once, an empty message, an unrelated question, a prompt-injection
attempt, six tool failures, and a source that answers about a different machine.

Every figure in the report is computed from those cases. None is written by
hand, and a metric with no cases behind it fails its own test — a number that
cannot go down measures nothing.

## 15.7 Why no fine-tuned model

Because nothing here is a language problem the architecture cannot solve. The
failures worth preventing — substituting a serial, stating an unpublished
field, answering about the wrong machine — are all prevented by types,
validators and a store lookup, and none of them would be *more* prevented by a
better-tuned model. If the evaluation later shows a repeatable understanding
failure that prompting and tools cannot fix, that is the moment to revisit it,
and the suite will name the failure precisely.
