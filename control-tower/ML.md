# The learning layer

An optional layer that sits **beside** the Control Tower, not inside it. With
its switch off — which is the default — the automation behaves exactly as it
did before the layer existed, and the test suite proves that rather than
asserting it.

## What it does and does not decide

| The model may | The model may never |
| --- | --- |
| Reorder the field-lookup candidates | Decide whether a field is ETA or ATA |
| Choose which write method to try first | Decide which shipment a field belongs to |
| Propose a **shorter** wait than the call site's own constant | Decide whether a date is valid |
| | Decide whether a write is safe |
| | Decide whether verification can be skipped |

Everything in the right-hand column is a deterministic rule in `update_eta.py`
and stays there. The layer's entire output is *an ordering* and *a number
smaller than one you already had*.

## Current state

**Not trained, not enabled.** There is no model file, because there is no
telemetry yet. `python -m ml.trainer` will refuse and tell you how many rows
short you are. That refusal is the design working, not a bug: a model built
from a handful of rows would pass every smoke test and then make confident,
wrong recommendations on the Hub.

## Getting from here to a working model

```bat
REM 1 · Collect. Telemetry is already on and changes nothing about the run.
python update_eta.py
python -c "from ml import telemetry; print(telemetry.stats())"

REM 2 · Train, once there are enough rows (60 is the floor; a few hundred is
REM     better). It refuses and says why if there are not.
python -m ml.trainer --show

REM 3 · Prove it beats the automation's own order. Trains on the earlier
REM     rows, scores the later ones. Read the VERDICT line.
python -m ml.evaluator

REM 4 · Only if the verdict is BETTER:
set ML_ENABLED=1
python update_eta.py
```

Step 3 is not optional. `NO DIFFERENCE` and `INSUFFICIENT DATA` both mean
leave it off.

## Settings

All are environment variables. Every default reproduces the original
behaviour.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ML_ENABLED` | `false` | The master switch. Off = the original Control Tower. |
| `ML_CONFIDENCE_THRESHOLD` | `0.65` | A recommendation below this is discarded. |
| `ML_MODEL_PATH` | `ml/models/strategy_model.json` | Where the model lives. |
| `ML_FALLBACK_ENABLED` | `true` | A prediction error falls back. Off makes it fatal — for tests only. |
| `ML_EXPLORATION_ENABLED` | `false` | Occasionally try a strategy the model does not favour. |
| `ML_EXPLORATION_RATE` | `0.10` | How often, when exploration is on. |
| `ML_MAX_WAIT` | `15000` | Hard ceiling on any recommended wait, in ms. |
| `ML_MIN_OBSERVATIONS` | `8` | Evidence needed before a context is trusted over its parent. |
| `ML_TELEMETRY_ENABLED` | `true` | Collection. Independent of `ML_ENABLED`. |
| `ML_TELEMETRY_PATH` | `ml/data/telemetry.jsonl` | Where telemetry is written. |
| `VERIFY_AFTER_SAVE` | `false` | Re-read every write from the Hub. Not ML — see below. |

## The algorithm, and why this one

A **Laplace-smoothed Bernoulli success-rate table over discrete contexts, with
backoff, ranked by the lower bound of a Wilson score interval.**

The question is "which of these seven known locators works on this kind of
page?" The candidate set is fixed and small, the features are categorical, and
the answer is a per-cell success rate. That is a lookup problem. A
gradient-boosted tree or a small network would have to rediscover the same
table from far more data than a run of this size produces, and would be harder
to overrule.

Three properties matter more here than model capacity:

- **It is honest with little data.** Ranking on the Wilson *lower* bound means
  1 success out of 1 scores 0.21 while 45 out of 50 scores 0.80. A strategy
  earns its place with evidence, not luck — and the confidence gate rejects
  thin cells outright.
- **It backs off.** A context never seen before is answered from the coarser
  context containing it, degrading to a global rate.
- **It is inspectable.** `python -m ml.trainer --show` prints the whole table.

Timing uses the 90th percentile of durations that actually succeeded, times
1.5 for headroom, clamped below the call site's own constant.

Standard library only. The automation's sole dependency is Playwright, and a
learning layer is not a good reason to make an operator install a toolchain.

## Fallback

Every one of these ends in "use the existing deterministic automation":

- the `ml` package is missing or fails to import
- `ML_ENABLED` is off
- no model file
- the model file is corrupt, truncated, the wrong version, or has malformed cells
- the context has never been seen and backoff reaches the global cell with no evidence
- the top score is below `ML_CONFIDENCE_THRESHOLD`
- the predictor raises for any reason
- the returned ordering is not a permutation of what was asked about

## `VERIFY_AFTER_SAVE`

Not part of the ML layer, but found while mapping the code: `save_manage_page`
proves the **postback completed** — the Save control disappears, or the results
table returns. That is not the same as proving the value **persisted**.

With `VERIFY_AFTER_SAVE=1` the shipment is reopened after every save and the
field is read back. A mismatch raises, so the run cannot report a success it
cannot prove. Off by default because it adds a reload to every successful
write and so changes the shape of a run that already works. Turn it on for the
real-hub E2E check.
