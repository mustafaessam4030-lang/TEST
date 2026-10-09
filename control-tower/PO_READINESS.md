# PO Automation — production-readiness report

> Engine design and guarantees: `PO_ENGINE.md`. Real-worker evidence
> procedure: `PO_WORKER_RUNBOOK.md` (NOT EXECUTED). Readiness levels A–F:
> `python -m po readiness`.

Date: 6 Oct 2026. Branch `claude/maia-m6rhri`. Written from the code and test
runs in this repository. **No run on the real Windows worker or the real eHub was
possible from the environment this was written in.** The cloud container has no
network route to `logisticshub.mantracgroup.com` and no worker credentials. Every
"real" item below is therefore UNVERIFIED, and the commands that prove it are
listed in §19–21.

Legend: **REAL VERIFIED** · **TESTED WITH STAND-IN** (real code against
eHub-shaped pages / an HTTP Graph stand-in) · **MOCKED** (scripted source or
mailer in memory) · **UNVERIFIED** · **BLOCKED BY ENVIRONMENT**.

---

## 1. Current architecture

```
Windows worker (headed Edge, the ETA run's sign-in)        Control plane / dashboard
 python -m po sweep  ─ beside every ETA run                  /api/po (queue, drawer, send,
   recover → read Shipments list → eligibility → jobs         supply, quality) · RBAC · audit
   po.ehub.EHubSource  (list rows, Manage, Documents,       ATLAS (answers from job records)
                        Bill Entry, download)
   po.pipeline         (read → extract → validate →
                        template → save → prepare → send)
   po.store            (jobs/, events.jsonl, ledger.json,
                        claims.json, documents/<sha>.pdf,
                        output/, sweeps/, *.lock)
   po.mail.GraphMailer (create → send → confirm in Sent Items)
```

Python stdlib + PyMuPDF (PDF text), Tesseract (optional OCR) and openpyxl
(template). No external service other than eHub and Microsoft Graph.

## 2. Current workflow

eHub → Shipments (Centralized Shipments Tracking, BU view, Status = Under
Clearance, page by page) → every row decided (`sweeps/sweep-*.json`) → for each
eligible row: Manage on **that row** → identity and current status re-read on the
Manage page → BU Shipment Info / General → Documents → the "Bill Entry /
BillofEntry" document (deterministic rule; several → DOCUMENT_AMBIGUOUS) → the
row's own Download → PDF integrity → existing ICUMS field rules → validation
gate → approved template (DUTY_REQUEST_V1, manifest-checked) → saved, re-opened
and read back → email prepared → (Send PO, or PO_AUTO_SEND=1) Graph create →
send → confirm in Sent Items.

## 3. What is REAL

Nothing in PO Automation is REAL VERIFIED by this report. The ETA automation's list navigation (`ensure_filtered_page`,
`find_row_by_bol`) runs on the real eHub during ETA runs, and the PO discovery reuses it. That is
evidence the list can be read, not evidence that the PO path works there.

## 4. What is MOCK / STAND-IN

| Capability | Status |
|---|---|
| Shipments list → row → Manage → Documents → Bill Entry → row's Download (pages shaped from the 6 Oct screenshots) | TESTED WITH STAND-IN (`test_po.py` §1, §27–28) |
| Sign-in page detection, Manage identity/status re-read | MOCKED (fake page) + stand-in pages |
| PDF reading, extraction, calculation | TESTED on synthetic PDFs (PyMuPDF), not eHub documents |
| Template fill, save, read-back | TESTED with the real approved template file |
| Graph create/send/confirm, throttling, dropped connection | TESTED WITH STAND-IN (HTTP, `test_po.py` §10–13) |
| EMAIL_UNKNOWN reconciliation, crash/resume, concurrency | MOCKED (`test_po_hardening.py`) |

## 5. What is UNVERIFIED

Real eHub sign-in for the PO browser · the real Shipments list paging and
filter for PO · Manage on a real row · whether the real Manage page prints the
BOL/AWB, "Current Status" and the declaration number where the identity reader
looks · the real Documents block and Download · a real Bill of Entry PDF read
by these rules · the field rules on real ICUMS layouts (multi-item BOEs in
particular) · a real template output checked by Accounts · real Graph
(Mail.Send, mailbox, application access policy) · real-world timing.

## 6. Critical weaknesses found (before this pass)

1. A worker dying mid-send marked the job **EMAIL_FAILED** although Graph may
   have sent it, and a later resend would duplicate the email.
2. An unknown send outcome re-sent the same draft; if Sent Items lagged, the
   retry got 404 and the job was marked **FAILED although the email went out**.
3. `to_float` rounded **every** number to 2 places: an exchange rate printed
   11.2045 became 11.20 (C21), silently changing C24/C26.
4. Negative amounts (`-500`, `(500.00)`) were read as **positive**.
5. Malformed numbers (`1,2,3`) were coerced (→ 123).
6. A VAT label printed twice was **summed twice**.
7. The send ledger's duplicate check was thread-safe only; **two processes**
   could both send. No processing idempotency: two jobs for one Bill of Entry
   each generated an output.
8. The Manage page's identity and current status were never re-checked; a
   **sign-in page** was never detected.
9. G4 was filled from an "Invoice No." printed on the Bill of Entry (added in
   the previous round). That was **an inference, not existing business logic**.
   It has since been decided by the business (see §9): an explicit printed
   "Invoice No." is the source. Absent, ambiguous or conflicting values go to
   review, and there is never a UNA+ fallback.
10. Coarse failure states (download, sign-in, navigation and save failures
    became PDF_NOT_FOUND / TEMPLATE_FAILED); no recovery; no stage timings; no
    reliability metrics; evidence screenshots could include a sign-in page and
    URLs with session queries.
11. Saving again in the same second (a resume) collided on the output name →
    SAVE_FAILED. Found by the new crash test.

## 7. Fixes implemented

All in this commit series; each is covered by a test in `test_po_hardening.py`
or `test_po.py`.

- Precise states: DISCOVERY_FAILED, AUTH_REQUIRED, MANAGE_NAVIGATION_FAILED,
  DOCUMENT_AMBIGUOUS, PDF_NOT_FOUND (= DOCUMENT_NOT_FOUND), PDF_DOWNLOAD_FAILED,
  PDF_UNREADABLE, EXTRACTION_FAILED, VALIDATION_FAILED, NEEDS_REVIEW,
  TEMPLATE_FAILED, SAVE_FAILED, EMAIL_FAILED, EMAIL_UNKNOWN,
  WORKER_DISCONNECTED, SKIPPED (+ skip reasons). Each stop carries `code`,
  `detail`, evidence pointer and `next_action`.
- Idempotency claim (doctype + BOL/AWB + Bill Entry No. + PDF SHA-256),
  atomic across processes; the duplicate stops before generation.
- Ledger reservation atomic across processes (lock file); explicit,
  audited resend path for a confirmed job.
- Email: SUBMITTED → ACCEPTED (202) → CONFIRMED (Sent Items). Unknown outcome →
  Sent Items, then whether the draft is still a draft → EMAIL_UNKNOWN if neither
  (ledger UNKNOWN blocks every sender). `python -m po recover` reconciles.
- Pre-send verification: attachment re-opened from disk; C13/G4/G6 and the
  trace cell I4 must be this job's; output hash unchanged; ≤ 3 MB.
- Recovery: job leases; `interrupt` / `resume` / `recover` — never restarts from
  zero, never passes VALIDATED without validating again.
- eHub: sign-in detection, Manage identity + status re-read
  (SKIPPED_STATUS_CHANGED), no sign-in screenshots, URLs without queries.
- Extraction: exact precision, signs, strict number grammar, duplicate VAT →
  AMBIGUOUS, PDF integrity (empty, HTML, truncated, repaired, oversize).
- G4 (business rule 12): ONLY the Bill of Entry's explicit "Invoice No." (one
  distinct, well-formed value). Absent, ambiguous, malformed or in conflict →
  NEEDS_REVIEW; a reviewer may only choose among the PRINTED values, fetch
  again, or reject. No typed value, never UNA+. Enforced again by the store
  (`PO_ENGINE.md` §F).
- Observability: stage timings, provenance map, `python -m po explain`,
  `quality`, `readiness`, `/api/po/quality`, ATLAS "Show me the calculation" and
  "How reliable is PO automation?".

## 8. Extraction / calculation audit

| Field | Source → method | Normalisation | Validation | Cell |
|---|---|---|---|---|
| Declaration No. | PDF, "Declaration No"/"Bill of Entry No" rule | `40726534505 / 00` | required; = eHub Bill Entry identifier; = Manage-page declaration when shown | C13 |
| BL/AWB | PDF, "BL/AWB No" rule | upper case | required; = eHub row BOL/AWB | — |
| Declaration date | PDF, "Date of Assessment/Entry" | ISO date; unreadable → MISSING (never today) | required | G8 |
| CIF (USD) | PDF, "Total Invoice Value / CIF" | exact decimal, sign kept | required, > 0 | C19 |
| Exchange rate | PDF, "Exchange Rate" | **exact (no 2-dp rounding)** | required, > 0 | C21 |
| Total duty (GHS) | PDF, "Total Duty and Levies" | exact, sign kept | required, > 0, arithmetic | G6 |
| Import duty line | PDF, "Import Duty" | exact | arithmetic only | — |
| VAT/levy lines (5 labels) | PDF, rightmost figure per labelled line | one per label; repeat → AMBIGUOUS | required | G20 (`=a+b+…`) |
| Supplier invoice No. | PDF, explicit "Invoice No." / "Invoice Number" (one well-formed value) only | text; digits → int | required; job value must agree → NEEDS_REVIEW if absent / ambiguous / malformed / conflict | G4 |
| Supplier, branch, charge to, priority | configuration / request | text | supplier required | G10, G11, G13, C9 |

Calculations (written as formulas by the approved template, never by the job):
G20 = Σ VAT/levy lines · G19 = G6 − G20 (import duty) · C24 = G6 ÷ C21 (duty in
USD) · C26 = C24 ÷ C19 (duty as share of CIF). Cross-check in validation:
|(G6 − G20) − printed import duty| ≤ 1.00 GHS. Missing inputs are MISSING, never
zero.

## 9. G4 source and mapping

SOURCE: the Bill of Entry's own text, the value under an explicit "Invoice
No." / "Invoice Number" label (`po/extract.printed_invoice_no`). Lines carrying
"UNA+" are skipped, "Invoice Value" does not match, and only one distinct value
is accepted. It goes to FIELD `invoice_no` (origin `bill_of_entry`, with the
printed line as evidence in the provenance map). TRANSFORMATION: trimmed, and
an all-digit value is written as a number. CELL: **G4** (F16 = G4 by template
formula).

- A value given with the job must agree with the printed one.
- If nothing is printed, several values are printed, or the job's value
  disagrees, the job goes to NEEDS_REVIEW (`G4_SOURCE_UNPROVEN`, `g4_reason`
  absent / ambiguous / conflict). Nothing is generated or sent.
- A reviewer may only choose one of the values the document PRINTS (origin
  `bill_of_entry:chosen_at_review`), fetch again, or reject. A typed value is
  refused; the store refuses VALIDATED for any other G4 origin.
- eHub's "UNA+ Invoice Number" is never a source or a fallback.

Regression tests: `test_po_hardening.py` §3 and `test_po.py` §27. This rule
has been exercised only on synthetic PDFs. Real ICUMS Bills of Entry are needed
to confirm where (and whether) they print an explicit "Invoice No.".

## 10. Validation rules

Required fields present and single-valued · BL/AWB (PDF) = eHub row ·
Declaration No. (PDF) = eHub Bill Entry identifier · = Manage-page declaration
(when the page shows one) · arithmetic within 1.00 GHS · CIF, rate, duty > 0 ·
no mismatch resolved by picking a side. All run before the template and before
any email; an email additionally re-checks the attachment.

## 11. Idempotency strategy

Processing: `idempotency_key = doctype | BOL/AWB | Bill Entry No. | PDF SHA-256`
claimed in `claims.json` under a cross-process lock; a holder that failed or
whose worker is gone releases it. Sending: ledger key
`po_key | document hash | template version | recipient`, reserved atomically;
SENT/CONFIRMED/UNKNOWN block every sender; a resend needs `po.resend` + reason.
Sweep: single-flight per data folder; already-handled references skipped.

## 12. Recovery strategy

Every write renews the job lease (pid, host, time). `recover` (CLI, and the
start of every sweep): an unfinished job whose holder is gone →
WORKER_DISCONNECTED (or EMAIL_UNKNOWN if it was sending) → resumed: discovery
again if nothing was proven; the **kept PDF** re-read (no download) after a
crash past PDF_FOUND; validation again from the **stored fields** after a crash
past extraction (an earlier saved file is kept and named "superseded").
EMAIL_UNKNOWN → reconciled with the mailbox, never resent automatically.

## 13. Email safety

Sent only from EMAIL_PREPARED / EMAIL_FAILED (or an authorized resend), only
with validation passed, a verified output whose hash is unchanged, an
attachment that re-opens to this job's C13/G4/G6 and trace, this job's subject
and filename, a configured recipient, a document observed in the real eHub
session (provenance REAL / VERIFIED; `PO_ALLOW_TEST_SEND` exists for tests
only), and a ledger reservation.

## 14. Evidence / audit design

Per job: correlation ID (= job ID), worker ID (ATA_WORKER_ID), the eHub row,
clearance check, Manage URL, identity read, Documents entries, the selected
Bill Entry, download method, PDF SHA-256 + kept copy, integrity, extracted
fields with raw text and source line, provenance map, validation checks,
template version + SHA, output path/SHA/read-back, email IDs and Graph
statuses, stage timings, every event with timestamp; failure screenshots and
page text (never of a sign-in page). Control-plane audit rows for start,
processed, send, confirm, review supplied. `python -m po explain <po_id>`.

## 15. Security findings

Fixed: sign-in pages could be photographed into evidence; evidence URLs could
carry session query strings. Confirmed: secret-named keys are stripped from
jobs/events/ledger; the Graph secret comes from the environment only; the
control-plane audit strips secret-named metadata. Remaining for the security
review: the kept PDFs and outputs hold commercial data in `PO_DATA_DIR` — protect the folder with NTFS permissions; the
Exchange application access policy on the sender mailbox is not verified from
here.

## 16. Performance findings

Mocked source, synthetic PDFs, this container: 10 jobs in 2.6 s; per stage
≈ read 2 ms, extraction < 1 ms, validation < 1 ms, template 177 ms, save
51 ms. The real cost will be eHub navigation (list paging per job, Manage,
download), which is not measured. Discovery re-reads the list per job, which is
the obvious bottleneck to measure on the worker before optimising.

## 17. Test matrix

| Area | Where | Kind |
|---|---|---|
| Numbers, signs, precision, malformed, duplicate VAT, missing ≠ 0, arithmetic, provenance map | hardening §1 | unit, real code |
| PDF integrity | hardening §2 | unit |
| G4 mapping, review, supply | hardening §3, test_po §17/§27 | regression |
| Idempotency, 1/5/10 concurrent jobs, 10-process claim and ledger races | hardening §4 | concurrency |
| Crash after download / during extraction / after generation / after save / mid-send | hardening §5–6 | recovery |
| Email accepted/confirmed, unknown-sent, unknown-not-sent, vanished, reconcile | hardening §6, test_po §10–13 | Graph stand-in + mocked |
| Attachment swap, tamper, 3 MB, validation failed | hardening §7 | safety |
| Retry backoff, Retry-After, no business retry | hardening §8, test_po §13 | retry |
| Every discovery stop → precise state + next action; sign-in; identity | hardening §9, test_po §1/§27 | taxonomy |
| Eligibility decisions, single-flight sweep | hardening §10, test_po §28 | discovery |
| explain, timings, quality (real apart), readiness | hardening §11 | observability |
| eHub-shaped navigation, Download pairing, NEVER_CLICK | test_po §1, §26–28 | stand-in |

## 18. Test results

Not typed here: counts go stale. The current result is what `python run_tests.py`
prints and writes to `test_results.json` (complete runs only), and what
`python -m po readiness` reports as levels A and B. The PO suites are
`test_po.py` (its real-discovery section runs only with `ATA_REAL_EHUB_TESTS=1`
on the worker), `test_po_hardening.py` and `test_po_engine.py` — all against
stand-ins.

## 19. Real worker result

**UNVERIFIED: no real Windows worker was reachable from this environment.** To run on the worker:

```bat
verify_po.bat                       REM python -m worker.verify po  (steps A–I)
python -m worker.verify po --email-to you@mantrac.com   REM + J, a controlled send
python -m po readiness
```

## 20. Real eHub result

**UNVERIFIED: real eHub was not reachable from this environment.**

## 21. Controlled email result

**UNVERIFIED: Microsoft Graph was not called against the real tenant.** Step J of the worker check
sends to one named address only, and records the Graph answer on the job.

## 22. Remaining blockers

1. A real worker run (A–I) and a controlled send (J) — mandatory for the pilot.
2. Confirmation that the real Manage page shows the BOL/AWB, "Current Status"
   and the declaration number where the identity reader looks (otherwise those
   checks report "not on page" and rely on the PDF's BL/AWB match).
3. Real ICUMS PDFs through the rules, compared by Accounts against a
   hand-made request, including a multi-item Bill of Entry (duplicate VAT
   labels now stop as AMBIGUOUS — that may be too strict for multi-item BOEs).
4. G4 on real Bills of Entry: confirm real ICUMS PDFs print an explicit
   "Invoice No." where the reader looks (else every job will wait in review).
5. Graph: Mail.Send application permission, admin consent, application access
   policy, sender mailbox — and confirmation that Graph's read-back
   (`GraphMailer.inspect`) returns recipient, subject and attachments for the
   ATA mailbox (without it a send stays EMAIL SENT, never CONFIRMED).
6. Security review and sign-off (`<PO_DATA_DIR>/readiness/signoff.json`).
7. A business rule for multi-item Bills of Entry (per-item VAT / totals): none
   exists in the current logic and none was invented; until defined from real
   documents, a repeated VAT label stops the job (fail closed).
8. OCR: a scanned Bill of Entry's required values go to review
   (LOW_CONFIDENCE_EXTRACTION); whether real Bills of Entry are text PDFs is
   unverified.

## 23. Final status

# NOT READY

The readiness gate (`python -m po readiness`) evaluates this from evidence and
will say READY FOR CONTROLLED PILOT only after a real worker job with REAL /
VERIFIED provenance has reached SAVED with a whole audit chain, and a controlled
send has been confirmed in Sent Items.
