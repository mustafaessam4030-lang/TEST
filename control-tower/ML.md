# ATLAS

**Adaptive Logistics Strategy Engine.**

A layer that sits **beside** the Control Tower, not inside it. It is on by
default, but "on" only means it will consult a model if one exists: with no
trained model the automation behaves exactly as it did before ATLAS existed,
and the test suite proves that rather than asserting it.

ATLAS is **not a second automation.** It chooses the ORDER in which known-safe
candidates are tried, and nothing else. Delete `ml/` and the Control Tower runs
exactly as it always has.

```
Deterministic Safety      the candidate SET, the ETA/ATA guards
        |                 — ATLAS cannot touch any of this
   Safe Candidates
        |
     ATLAS               an ORDER, and a wait no longer than the call
        |                site's own constant
Existing Automation      unchanged; it does the work
        |
      Save
        |
Read-back Verification   deterministic; ATLAS has no say
        |
 Verified Result
```

## What ATLAS says about itself

Every line beginning `ATLAS →` is a claim that ATLAS did something, and each
one has exactly one truth condition:

| Line | Written when |
| --- | --- |
| `ATLAS → Strategy selected` | its order was **used** — the automation really did reorder because of it |
| `ATLAS → Strategy failed` | the strategy it put first was tried and did not work, or its value did not persist |
| `ATLAS → Fallback activated` | it steered, its candidates missed, and the automation's own visibility-free lookup took over |
| `ATLAS → Deterministic fallback` | it did **not** steer: no model, thin evidence, below threshold, drift, or shadow mode |
| `ATLAS → Verification passed` | read-back confirmed a value on a write it influenced |
| `ATLAS → Action completed` | read-back **confirmed** the value **and** ATLAS influenced the write |
| `ATLAS → Action unverified` | the write was saved but nothing read it back |

`Action completed` is the only success claim in the vocabulary and it is the
most tightly guarded line in the system: there is exactly one place in
`update_eta.py` that can emit it, it sits behind
`if verified is True and episode.atlas_influenced:`, and a test pins both
halves. With `VERIFY_AFTER_SAVE` off it can never be reached — the run says
`Action unverified` instead.

Shadow mode never says `Strategy selected`. It used nothing, so it selected
nothing, and the honest line is `Deterministic fallback` with the choice it
*would* have made named as detail.

## Where you see it

* **Run log** — the `[ATLAS]` startup block and the `ATLAS →` event lines.
* **Dashboard** — the ATLAS card, with a live feed of the run's events;
  influenced ones in the accent colour, fallbacks muted.
* **Shipments table and live panel** — a small `ATLAS` tag on rows the engine
  steered, and a muted `Deterministic` tag on the rest. Never blank: "the
  deterministic order did this" is a statement, not an absence.
* **Telemetry** — every row carries `engine`, every episode carries
  `atlas_influenced`, `atlas_chosen` and `atlas_mode`, and every decision
  carries the vocabulary `label` it was announced under.
* **Model file** — `engine`, `engine_full_name`, and the identifier
  `ATLAS/<model version>.<feature version>`, e.g. `ATLAS/4.2`.

## What it does and does not decide

| ATLAS may | ATLAS may never |
| --- | --- |
| Reorder the field-lookup candidates | Decide whether a field is ETA or ATA |
| Choose which write method to try first | Decide which shipment a field belongs to |
| Propose a **shorter** wait than the call site's own constant | Decide whether a date is valid |
| | Decide whether a write is safe |
| | Decide whether verification can be skipped |

Everything in the right-hand column is a deterministic rule in `update_eta.py`
and stays there. ATLAS's entire output is *an ordering* and *a number smaller
than one you already had*.

## Status: READY FOR LEARNING — NOT YET PROVEN SUPERIOR

This is the honest headline and it should stay there until real production
telemetry says otherwise. Every mechanism below is built, wired and tested. No
model has been trained from a real Hub run, so no model has beaten the
hand-tuned order at anything. The layer's own startup log says exactly this,
and so does the dashboard panel.

## Three modes

| `ML_MODE` | What happens |
| --- | --- |
| `off` | Inert. Identical to the package not being installed. |
| `shadow` | **Default.** The model is consulted on every lookup and what it *would* have chosen is recorded — then discarded. The deterministic order runs. |
| `active` | The recommendation is acted on, subject to every gate below. |

Shadow is the default because a replay of past telemetry cannot settle whether
the model is better. A candidate late in the automation's list is only ever
observed when the earlier ones failed, so the holdout is a biased sample of the
situations the deterministic order found *hard*. Shadow mode records a decision
for every situation, easy ones included, and that log is the unbiased evidence
the replay cannot be.

Every run prints its own state before it starts:

```
[ML] Initializing...
[ML] Mode: SHADOW
[ML] Model found: ml\models\champion.json
[ML] Model loaded successfully
[ML] Model version: 4   feature space: v2   built: 2026-09-04 07:41:12
[ML] Label rule: verified_persisted_success
[ML] Model contents: 8 contexts, 5567 observations
[ML] Support required: 30 per cell, 8 per strategy
[ML] Ranking score threshold: 0.65 (a Wilson lower bound, NOT a probability)
[ML] Status: SHADOW
[ML] READY FOR LEARNING, NOT YET PROVEN SUPERIOR
[ML] This model has not passed an evaluation gate against real production telemetry.
[ML] SHADOW: recommendations are recorded and DISCARDED. ...
```

or, with no model yet:

```
[ML] Initializing...
[ML] Mode: SHADOW
[ML] No trained model found at ml\models\champion.json
[ML] Status: FALLBACK
[ML] Using deterministic automation
```

Training is never triggered by a run. `python -m ml.trainer` is a separate,
deliberate act — a model that retrained itself on the way past would be a
different model every time and impossible to hold responsible for anything.

**Not trained yet.** There is no model file, because there is no telemetry
yet. `python -m ml.trainer` will refuse and tell you exactly what is missing.
That refusal is the design working.

## Champion and challenger

`python -m ml.trainer` writes **`challenger.json`**. Always. It never touches
production.

`python -m ml.trainer --promote` runs the evaluator and copies the challenger
to **`champion.json`** *only* if the verdict is `BETTER` on enough held-out
observations. The predictor loads the champion. That separation is what makes
the gate mean something — otherwise every training run would silently go live.
The previous champion is kept alongside, timestamped, so a bad promotion is
undoable without a retrain.

## The label: verified persisted success

A strategy attempt is a **positive** only when the write it belonged to was
read back out of the Hub afterwards and confirmed. Not "the locator matched an
element" — that was the old label and it measured the wrong thing.

| Situation | Label |
| --- | --- |
| Found the field, write read back and correct | **positive** |
| Tried and did not find the field | **negative** |
| Found the field, write read back and **wrong** | **negative** (the heaviest penalty there is) |
| Episode never read back at all | **excluded** — not a negative |

That last row is the one that matters. An unverified episode is not a failed
episode, and counting it as one would put invented failures into the training
data — asymmetrically, because unverified episodes can only ever contribute
negatives. The whole episode is dropped and the trainer reports how many.

**This means `VERIFY_AFTER_SAVE=1` is what turns a run into training data.**
Without it every episode is excluded and the trainer will keep refusing.

## The reward

A single success bit cannot tell a clean instant win from a nine-second one
that needed three attempts. The reward can:

```
reward =  W_SUCCESS     * verified            (1.00)
        - W_LATENCY     * latency_cost        (0.25)
        - W_RETRY       * retry_cost          (0.15)
        - W_FAILURE     * fault(category)     (0.20)
        - W_VERIFY_FAIL * verification_failed (0.60)
```

`fault` is *how much of the failure belongs to the strategy*, not how bad it
was. A locator that could not find a field is charged in full. A locator that
never got the chance because the network dropped is charged nothing — charging
it would teach the model that a good selector is unreliable on days when the
VPN is flaky. The reward is bounded (`+1.00` to `-1.20`) and converted to
fractional success credit, so a verified win that took eight seconds banks
about 0.6 of a success rather than a flat 1.

## Recency, drift and quarantine

Observations are weighted `0.5 ** (age_days / 30)`. A cell's effective sample
size shrinks as its evidence goes stale, which widens the Wilson interval on
its own, which makes the gate decline — staleness turns into caution rather
than into confident wrong answers.

If a cell's recent-window rate disagrees with its history by more than
`ML_DRIFT_THRESHOLD`, the model stands down for that decision and the
hand-tuned order takes over until it is retrained. A strategy that has failed
`ML_QUARANTINE_FAILURES` times in a row most recently is never recommended
until it is seen to work again.

## Support is not the same as score

The Wilson lower bound is a *ranking score*, not a probability, and nothing in
this codebase calls it one. A separate **support** gate decides whether a cell
may have an opinion at all — `MIN_SUPPORT` across the cell and
`MIN_SUPPORT_PER_ARM` on the arm being recommended — and it is checked *before*
the score. A high score on four observations is arithmetic, not a finding.

Whether the model's predicted rates are probabilities at all is a separate,
measured question. `ml/calibration.py` bins predictions against held-out
outcomes and reports ECE and Brier. Below `ML_CALIBRATION_MIN_ROWS` the answer
is **unknown** — which is a different answer from "poorly calibrated" and is
treated as one.

## Getting from here to a working model

```bat
REM 1 · Collect, WITH VERIFICATION ON. Without this every episode is
REM      unlabelled and nothing downstream will work.
set VERIFY_AFTER_SAVE=1
python update_eta.py
python -c "from ml import episodes; print(episodes.join()[1])"

REM 2 · Train a challenger. 60 labelled rows is the floor; a few hundred is
REM      better. It refuses and says exactly what is missing if there are not.
python -m ml.trainer --show

REM 3 · Prove it beats the automation's own order. Trains on the earlier
REM      rows, scores the later ones. Read the VERDICT line.
python -m ml.evaluator

REM 4 · Promote — only happens if the evaluator says BETTER.
python -m ml.trainer --promote
python -m ml.trainer --status

REM 5 · The champion is now loaded, in SHADOW. It still changes nothing.
python update_eta.py

REM 6 · Only after shadow decisions confirm it on real runs:
set ML_MODE=active
python update_eta.py
```

Steps 3 and 6 are not optional. `NO DIFFERENCE` and `INSUFFICIENT DATA` both
mean leave it in shadow.

## Settings

All are environment variables. Every default reproduces the original
behaviour.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ML_MODE` | `shadow` | `off` / `shadow` / `active`. Only `active` changes what the automation does. |
| `ML_ENABLED` | `true` | Consult the model if there is one. `0` silences the layer entirely. |
| `ML_CONFIDENCE_THRESHOLD` | `0.65` | A ranking score below this is discarded. Not a probability. |
| `ML_CHAMPION_PATH` | `ml/models/champion.json` | What the predictor loads. |
| `ML_CHALLENGER_PATH` | `ml/models/challenger.json` | What the trainer writes. |
| `ML_MODEL_PATH` | — | Overrides the champion path. For tests and one-offs. |
| `ML_REQUIRE_VERIFIED_LABEL` | `true` | Strict labelling. `0` falls back to "found the field", and the model records that it did. |
| `ML_MIN_SUPPORT` | `30` | Observations a cell needs before it may have an opinion. |
| `ML_MIN_SUPPORT_PER_ARM` | `8` | Observations the recommended strategy itself needs. |
| `ML_HALF_LIFE_DAYS` | `30` | Recency half-life for observation weights. |
| `ML_DRIFT_WINDOW_DAYS` | `14` | The "recent" window drift is measured against. |
| `ML_DRIFT_THRESHOLD` | `0.25` | Rate gap that makes the model stand down. |
| `ML_QUARANTINE_FAILURES` | `5` | Consecutive recent failures that benches a strategy. |
| `ML_CALIBRATION_MIN_ROWS` | `200` | Below this, calibration is reported as unknown. |
| `ML_W_SUCCESS` / `ML_W_LATENCY` / `ML_W_RETRY` / `ML_W_FAILURE` / `ML_W_VERIFY_FAIL` | `1.0` / `0.25` / `0.15` / `0.20` / `0.60` | Reward weights. |
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


## Proving it is really being used

`python demo_ml_live.py` runs the production functions against a stand-in
Manage page — two tabs, an ETA field and an ATA field, the ATA one labelled by
a table cell rather than a `<label for=…>`, exactly the shape that made
`get_by_label` miss it on the real page. No Hub, no credentials, no internet.

It trains a model with the ordinary trainer, prints the startup block, shows
the model's actual scores, then runs `fill_date_field` twice — once with the
model choosing the order and once with `ML_ENABLED=0` — and times both. On that
page the fixed order spends a 1500ms timeout on `label_exact` before reaching
the candidate that works:

```
took  109ms with the model choosing the order
took 1592ms with the fixed order (ML_ENABLED=0)
```

It then checks the ETA field and the three other date fields on the panel were
untouched, saves, reloads, reads the value back, and confirms a value that did
NOT persist is rejected.

That page is a stand-in, not your Hub. It proves the layer is wired in and the
guards hold; only a run against the real Hub proves your markup.
