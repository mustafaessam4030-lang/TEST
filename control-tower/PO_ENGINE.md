# PO Automation: the transaction engine

**Scope.** This document covers how the PO subsystem processes a Bill of Entry
safely, and what the code enforces. It does **not** claim the system works on the
real eHub, the real ICUMS PDFs or the real Graph mailbox. None of those has been
exercised from this repository. Each such claim below is marked **NOT VERIFIED —
REQUIRES REAL-WORLD EVIDENCE**. `PO_WORKER_RUNBOOK.md` is how to obtain that
evidence.

Evidence levels used here:

| Level | Meaning |
|---|---|
| **TESTED WITH STAND-IN** | The real engine code ran against a mocked eHub source and an HTTP stand-in Graph. |
| **NOT VERIFIED** | Not exercised against the real system. |

---

## A. Architecture

```
eHub shipment list ──► EHubSource (po/ehub.py) ─ trail of every step, with evidence
                         │  row · status · Manage · identity · Documents · Bill Entry · download
                         ▼
po/pipeline.process ──► milestones + state transitions, all through po/store.py
  integrity (po/extract.read_pdf) → extraction (field-level evidence)
  → normalisation (strict number grammar) → THE GATE (po/validate.validate:
  identity, cross-validation, G4, business rules, calculation trace)
  → template (po/template.build, read back) → output (atomic publish, read back)
  → email prepared
po/pipeline.send ──────► ledger reservation → draft → SUBMITTED → Graph send →
                         reconciliation in Sent Items (recipient / subject /
                         attachment verified) → CONFIRMED → COMPLETED
po/pipeline.recover ───► interrupted jobs resumed from what was proven;
                         unknown sends reconciled; never resent blindly
```

ATLAS (`dashboard/atlas_po.py`) only reads the records. It never decides
anything, and PO answers never go through a language model
(`test_atlas_research.py`).

## B. State machine

There are two layers, both enforced by `po/store.py`.

**1. Persisted states** (`TRANSITIONS`). These are the operational status shown
in the queue. Anything not in the table raises `IllegalTransition`.

**2. Canonical milestones** (`CANONICAL`). These are your transaction stages.
Each one is recorded with its evidence by `Store.milestone()`, which refuses it
(`InvariantViolation`) unless its `PREREQUISITES` are already recorded in the
current attempt.

`Store.transition()` refuses a state unless two conditions hold:

- the milestones that state needs (`STATE_REQUIRES`) are recorded;
- the record's evidence agrees (`_evidence_problems`): the validation decision is
  VALID, the identity is MATCH, G4 comes from the Bill of Entry, PDF integrity is
  verified, the output is verified, Graph's acceptance evidence is present, and
  the reconciliation is verified.

These checks live in the state layer, not only in the pipeline's control flow.

| Canonical milestone | Recorded when | Persisted state reached |
|---|---|---|
| DISCOVERED | the job starts discovery | PO_DISCOVERED |
| ELIGIBLE_VERIFIED | the row's status was observed exactly `Under Clearance` (observed value, source, view, time) | — |
| MANAGE_OPENED | the Manage page opened (its URL) | — |
| SHIPMENT_IDENTITY_VERIFIED | identity decision = MATCH (Manage page, or the document itself) | — |
| DOCUMENT_DISCOVERED / DOCUMENT_SELECTED | Bill Entry candidates found; one selected by a recorded deterministic rule | — |
| PDF_RETRIEVED | bytes, SHA-256, method | PDF_FOUND |
| PDF_INTEGRITY_VERIFIED | `%PDF` header, `%%EOF`, size, opened, not repaired, not encrypted | PDF_READ |
| FIELDS_EXTRACTED | every field with its raw text, page, method and confidence | FIELDS_EXTRACTED |
| FIELDS_NORMALIZED | no MALFORMED value | VALIDATING |
| CROSS_VALIDATED | every comparison MATCH, identity MATCH | — |
| BUSINESS_RULES_VALIDATED | decision VALID, calculation trace | VALIDATED |
| TEMPLATE_GENERATED / TEMPLATE_VERIFIED | filled, re-opened, every cell and formula read back | TEMPLATE_GENERATED |
| OUTPUT_PERSISTED | atomic publish, re-opened from disk, hash matches | SAVED |
| EMAIL_PREPARED | recipient, subject, attachment and its hash | EMAIL_PREPARED |
| IDEMPOTENCY_CONFIRMED | the send ledger reserved this document + recipient | EMAIL_SENDING ("EMAIL SUBMITTED") |
| EMAIL_SUBMITTED | written **before** Graph's send is called | — |
| EMAIL_RECONCILING | Graph accepted (202), or the message was found after an error | EMAIL_SENT |
| EMAIL_CONFIRMED / COMPLETED | found in Sent Items; recipient, subject and attachment verified | EMAIL_CONFIRMED |

**Terminal failures are precise; there is no generic FAILED state.** Mapping to
your taxonomy:

| Your failure | Persisted state / code |
|---|---|
| DISCOVERY_FAILED | `DISCOVERY_FAILED` (also `SOURCE_EVIDENCE_MISSING`) |
| ELIGIBILITY_FAILED | `SKIPPED` + `SKIPPED_NOT_UNDER_CLEARANCE` / `SKIPPED_STATUS_CHANGED` |
| IDENTITY_MISMATCH | `IDENTITY_MISMATCH` |
| DOCUMENT_NOT_FOUND | `PDF_NOT_FOUND` (code `DOCUMENT_NOT_FOUND`) |
| DOCUMENT_AMBIGUOUS | `DOCUMENT_AMBIGUOUS` |
| PDF_RETRIEVAL_FAILED | `PDF_DOWNLOAD_FAILED` |
| PDF_INTEGRITY_FAILED | `PDF_UNREADABLE` |
| EXTRACTION_FAILED | `EXTRACTION_FAILED` |
| NORMALIZATION_FAILED | `EXTRACTION_FAILED` + code `NORMALIZATION_FAILED` |
| CROSS_VALIDATION_FAILED | `VALIDATION_FAILED` + code `CROSS_VALIDATION_FAILED` |
| BUSINESS_RULE_FAILED | `VALIDATION_FAILED` + code `BUSINESS_RULE_FAILED` / `REQUIRED_FIELDS_FAILED` |
| TEMPLATE_FAILED | `TEMPLATE_FAILED` |
| OUTPUT_PERSISTENCE_FAILED | `SAVE_FAILED` |
| IDEMPOTENCY_BLOCKED | `SKIPPED` + `SKIPPED_DUPLICATE`; a send refused by the ledger is BLOCKED |
| EMAIL_PREPARATION_FAILED | `EMAIL_FAILED` + code `EMAIL_PREPARATION_FAILED` |
| EMAIL_SUBMISSION_FAILED | `EMAIL_FAILED` + code `EMAIL_SUBMISSION_FAILED` |
| EMAIL_UNKNOWN | `EMAIL_UNKNOWN` |
| EMAIL_RECONCILIATION_FAILED | `EMAIL_RECONCILIATION_FAILED` |
| NEEDS_REVIEW | `NEEDS_REVIEW` (codes `G4_SOURCE_UNPROVEN`, `IDENTITY_UNPROVEN`, `LOW_CONFIDENCE_EXTRACTION`) |
| (closed by a person) | `REVIEW_REJECTED` |

The persisted names were kept, not renamed to your exact list. Stored jobs, the
dashboard and ATLAS read them, and renaming them would destabilise those readers
for no safety gain. The canonical names exist as enforced milestones. The failure
codes give the precision.

## C. Persistence model

All PO state lives in files under `<PO_DATA_DIR>`. The PO subsystem does not use
PostgreSQL, so database constraints do not apply. The same guarantees come from
the store layer:

- **Jobs** (`jobs/<id>.json`) are written to a unique temporary file, fsynced,
  then atomically renamed.
  - Every write carries a `version`.
  - A write from a stale copy raises `ConcurrentUpdate`, so there is no lost
    update between processes.
- **Locks** are operating-system file locks (`fcntl` / `msvcrt`) under `locks/`.
  - The OS releases a lock when its holder dies.
  - There is no stale-lock breaking, and no window where two processes both hold
    the same lock. Tested by killing a lock holder (`test_po_engine` §3).
- **Claims** (`claims.json`) and the **send ledger** (`ledger.json`) are
  read-modify-written under their lock and written atomically.
- **Documents** (`documents/<sha256>.pdf`) are content-addressed and written
  atomically.
- **Outputs** go to a temporary file, are fsynced, then published under their
  final name with `link` (POSIX) or `rename` (Windows).
  - The publish never replaces an existing file.
  - The output is re-opened from disk, and its hash must equal the generated
    bytes.
- **Events** (`events.jsonl`) are append-only and hash-chained (see J).

Every job records:

- `po_id`, `idempotency_key`, `reference`, `identifier`;
- `state`, `previous_state` and `attempt`;
- `created`, `started_at`, `updated` and `completed_at`;
- `worker` (host, pid, worker_id) and `lease`;
- `correlation_id`, `milestones`, `failure`, `review` and `interrupted`.

## D. Idempotency strategy

There are two independent, durable guards:

1. **One document, one job.** The claim key is doctype + shipment + Bill Entry
   identifier + document SHA-256. It is taken atomically under the claims lock.
   - A live holder blocks a new job, which becomes `SKIPPED_DUPLICATE`.
   - A failed or abandoned holder releases the claim.
2. **One document, one email.** The ledger key is shipment + document hash +
   template version + recipient. It is reserved atomically before anything is
   sent.
   - SENDING, SENT, CONFIRMED or UNKNOWN blocks every other sender.
   - Only an explicitly authorized resend (who and why recorded) passes.

Neither guard relies on memory or on "the file exists".

Tested with stand-ins (`test_po_engine` §13): 4 OS processes processing the same
Bill of Entry at the same instant produced 1 output and 3 SKIPPED_DUPLICATE. 4
processes pressing Send on one job produced one email.

## E. Validation strategy

`po/validate.validate()` is the single gate. It returns one decision:

- **VALID** is the only result that may proceed.
- **INVALID** carries a precise code, from most to least specific:
  1. `IDENTITY_MISMATCH`
  2. `CROSS_VALIDATION_FAILED`
  3. `NORMALIZATION_FAILED`
  4. `REQUIRED_FIELDS_FAILED`
  5. `BUSINESS_RULE_FAILED`
- **NEEDS_REVIEW** means something is unproven:
  - `IDENTITY_UNPROVEN`;
  - `G4_SOURCE_UNPROVEN`;
  - `LOW_CONFIDENCE_EXTRACTION` (OCR-read values).

A failure is never softened into a review.

Each decision also carries:

- the failed checks and affected fields;
- reasons and remediation;
- **comparisons**: `{field, source_a, value_a, source_b, value_b, result:
  MATCH | MISMATCH | NOT_AVAILABLE | NOT_COMPARABLE, evidence}`;
- the **calculation trace**.

**Shipment identity** (`pipeline.identity_decision`) is deterministic and
explained.

- **MISMATCH**: any contradiction, namely:
  - the Manage page shows another BOL/AWB;
  - the page's declaration is not the selected Bill Entry's;
  - the PDF's BL/AWB is not the row's;
  - the PDF's declaration is not eHub's Bill Entry identifier.
- **MATCH**: no contradiction, the PDF's BL/AWB is the row's, and either the
  Manage page shows it or the PDF's declaration is eHub's identifier.
- **INSUFFICIENT_EVIDENCE**: everything else. It is never treated as MATCH.

A Manage-page mismatch stops the job **before** the document is read.

**Number grammar** (`po/extract.py`): ICUMS / en-GH, comma thousands and dot
decimal, in one locale.

- **Accepted:** `1234`, `1234.56`, `1,234,567.89`, signs `-12.50` and `(12.50)`.
- **Rejected:**
  - `1.234,56`, `1234,56`, `12,50` (another locale's decimal comma);
  - `1 234.56` (space grouping);
  - `1,2,3`, `12,34,567`, `1,23` (grouping not in threes);
  - `1.2.3`, `.5`;
  - `10O0`, `1O00`, `GHS653` (letters in or against the number);
  - `45%`, `01/10/2026`, `12:30` (rates, dates, times).

A printed but rejected value makes the field MALFORMED. The job then stops at
`NORMALIZATION_FAILED` with the raw text kept. It is never coerced.

**Field evidence.** Every field records its raw text, normalised value, page,
method (text layer or OCR), confidence and status:
FOUND / MISSING / AMBIGUOUS / MALFORMED.

## F. G4 implementation

G4 comes **only** from the Bill of Entry: its explicit "Invoice No." /
"Invoice Number" (`extract.printed_invoice_no`, `pipeline._g4_source`); when it
prints none, the digits of its own "User Reference" (letters then digits:
DDAO9116093 → 9116093), as the business's completed Duty Template does —
origin `bill_of_entry:user_reference`, and the store's invariant accepts exactly
those digits and nothing else.

| What the Bill of Entry prints | Result |
|---|---|
| One well-formed value | G4 (origin `bill_of_entry`, page and line recorded) |
| Several different values | NEEDS_REVIEW `ambiguous` |
| The label with a malformed value (`2600 005261`, `INV#12`, `N/A`, over 30 characters) | NEEDS_REVIEW `malformed`. Never trimmed. |
| No label, a User Reference of letters then digits | G4 = its digits (origin `bill_of_entry:user_reference`) |
| No label and no such User Reference | NEEDS_REVIEW `absent` |
| A value that disagrees with the one given with the job | NEEDS_REVIEW `conflict` |

**Never a source:**

- eHub's UNA+ column;
- shipment metadata;
- a search value;
- a file name;
- a model;
- a typed value. A value given with the job is a cross-check only.

**Review.** A person may only:

- **choose one of the PRINTED values** (`choose`; anything else is refused);
- **fetch again** after the document is corrected in eHub;
- **reject the job**, with a reason.

**Defence in depth.** The store refuses `VALIDATED` and everything after it when
G4's origin is not the Bill of Entry.

**Changed this round.** The earlier "a person types G4 at review" path is
removed, per your rule 12.

## G. Business calculation protection

The business rule itself is unchanged: the approved template's own formulas,
which are kept and never written, plus the cross-check below. `validate.calculations()`
recomputes each figure exactly, in `Decimal`, as a trace:

- G20 = Σ VAT lines;
- G19 = G6 − G20;
- C24 = G6 ÷ C21;
- C26 = C24 ÷ C19;
- cross-check: |(G6 − G20) − printed import duty| ≤ 1.00.

Each trace entry records the rule, inputs, formula, intermediate value, rounding
and result. The same inputs always give the same outputs (tested).

VAT handling:

- A VAT label printed twice is AMBIGUOUS and the job stops. A repeat and a second
  item cannot be told apart, so the lines are never summed twice.
- A missing VAT block is MISSING, never assumed to be zero.
- A zero VAT line is kept.
- A negative total stops at the `> 0` rule.
- Malformed values stop at normalisation.

The output is verified after it is written. The saved file is re-opened before
any email, and two checks must pass:

- every written cell equals its validated value;
- every template formula is intact.

**Multi-item Bills of Entry: NOT VERIFIED — REQUIRES REAL-WORLD EVIDENCE.** The
existing rule reconciles the document's totals. It has no per-item rule, and none
was invented. Real multi-item ICUMS PDFs are needed to define one, and until
then they fail closed.

## H. Email reconciliation strategy

Microsoft Graph sends in three steps: create the draft, send it, then confirm.

- **EMAIL_SUBMITTED is written to disk before the send call.** Without it, the
  send was provably never called.
- **Unknown outcomes are reconciled, never resent blindly.** A timeout, a gateway
  error or a lost response leads to `EMAIL_UNKNOWN` and reconciliation:
  1. Look in Sent Items, by internetMessageId. If the id was never recorded, use
     the job's unique attachment name plus the subject.
  2. Check whether the draft still exists.
  3. Only a draft that is provably unsent is sent again, and on the same draft.
- **Throttling is retried within limits.** 429 and 503 are retried within the
  policy, honouring Retry-After.
- **A send is only confirmed after the message is read back.** The message found
  in Sent Items is read back (`GraphMailer.inspect`):
  - recipient, subject, attachment name and size match → `EMAIL_CONFIRMED`;
  - any difference → `EMAIL_RECONCILIATION_FAILED`, never resent;
  - not readable → stays `EMAIL_SENT` (accepted, not confirmed).
- **Correlation, never secrets.** `client-request-id` and Graph's `request-id`
  are kept on the job. Tokens never are.

**Real Graph: NOT VERIFIED — REQUIRES REAL-WORLD EVIDENCE.** This needs Mail.Send
permission, admin consent, an application access policy and the sender mailbox.

## I. Recovery strategy

`python -m po recover` runs at the start of every sweep.

| Situation | Recovery |
|---|---|
| A job whose worker is gone (OS-checked lease) | `WORKER_DISCONNECTED`, then resumed from what was proven: discovery again, the kept PDF re-read from its integrity check, or validation again in full (a superseded output is kept and named) |
| `EMAIL_SENDING` interrupted | `EMAIL_UNKNOWN`, then reconcile (H) |
| `EMAIL_SENT` | confirmation is retried |
| Validation and review failures | never retried blindly |
| `COMPLETED` | never resent |

Every recovery step is an event.

Tested with stand-ins: a worker killed at each of the 12 transitions, and after
the draft or the send. Every case recovered to EMAIL_CONFIRMED with exactly one
email (`test_po_engine` §12).

## J. Audit strategy

`events.jsonl` is append-only and **hash-chained**. Each event records:

- `event_id`, `seq`, `prev` (the previous event's hash) and `hash`;
- `po_id`, `correlation_id`, `attempt`, `actor`;
- stage, status and timestamp;
- evidence reference and metadata.

Every state change is a `STATE_CHANGED` event with its old and new state, actor,
reason and attempt.

`python -m po audit-verify` (`Store.verify_audit`) detects an altered, removed,
inserted or reordered event, and events cut from the end. Worker events imported
by the control plane join its own chain (`Store.import_event`).

Never stored: passwords, tokens, cookies, authorization headers, security codes
or CAPTCHA data. Such keys are dropped on write (`store.FORBIDDEN`), and a test
scans the trail.

## K. Test matrix

| Area | Suites |
|---|---|
| End to end, discovery, documents, Graph | `test_po.py` |
| Numbers, PDF integrity, G4, idempotency, recovery, email safety, observability, readiness | `test_po_hardening.py` |
| Canonical milestones, store-enforced invariants, persistence, audit tamper detection, number grammar, VAT/multi-item, G4, identity, validation gate, output, Graph matrix, 14 crash points, 4-process races, 60 random jobs against the invariants, metrics | `test_po_engine.py` |

All of these run against stand-ins.

## L. Exact test results

These are in the final report and in `test_results.json`. They are written by
`python run_tests.py` and are never typed in here.

## M. Real-worker verification

**NOT VERIFIED — REQUIRES REAL-WORLD EVIDENCE.** See `PO_WORKER_RUNBOOK.md`.
