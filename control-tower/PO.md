# PO Automation

A bounded module of ATA Control Tower that turns a document attached to a Hub
shipment into a filled, approved template and sends it through Microsoft 365 —
only after it has been validated against the Hub.

```
HUB → FIND PDF → RETRIEVE → READ → EXTRACT → VALIDATE ─┬─ passed → TEMPLATE → SAVE (read back)
                                                        │          → EMAIL READY → Send PO
                                                        │          → GRAPH → SENT → VERIFIED (Sent Items)
                                                        └─ failed → STOP. Nothing generated, nothing sent.
                         every step: event · audit · ATLAS failure intelligence · learning
```

It reuses the platform rather than adding to it: the Hub sign-in and
navigation of `update_eta.py`, the worker and its command channel, the control
plane's roles, sessions and audit log, ATLAS's failure intelligence and
learning store, and the dashboard's design system.

## Version 1: what document, what template

| | |
|---|---|
| Source document | The Bill of Entry (customs declaration) attached to the Hub shipment |
| Approved template | Mantrac Ghana **Duty Payment Request** (cheque request) — `po/templates/DUTY_REQUEST_V1.xlsx` |
| Hub check | The BL/AWB printed on the document must match the shipment's BOL/AWB in the Hub |
| Output | Excel (.xlsx), the approved template's own layout, formulas and logo |
| Email | One configured recipient, from one ATA mailbox, through Microsoft Graph |

The type is a single entry in `po/doctypes.py`. A real Purchase Order template
is a second entry there — fields, required flags, Hub checks, template file and
the field → cell mapping — and nothing else changes.

**The approved template** is derived once from your original workbook by
`python -m po.templates.build_template`: it keeps the *Duty Template* and *BOE
Template Capture* sheets (the other sheets are empty working copies, one of
~96,000 formatted rows that made every load take 16 s — the earlier BOE
automation dropped the same sheets) and clears the input cells. The manifest
records the SHA-256 of both files; a template changed outside that step is
refused at fill time.

## The fields (`po/doctypes.py`)

| Field | From | Required | Template cell |
|---|---|---|---|
| Declaration (BOE) No. | PDF | yes | C13 |
| BL / AWB No. | PDF | yes | — (validated against the Hub) |
| Declaration date | PDF | yes | G8 |
| Total invoice value CFR/CIF (USD) | PDF | yes | C19 |
| Exchange rate | PDF | yes | C21 |
| Total duty (GHS) | PDF | yes | G6 |
| VAT / levy lines | PDF | yes | G20 (`=a+b+…`, the template's own style) |
| Import duty line, User reference | PDF | no | — (cross-check / shown) |
| Supplier invoice No. | operator, per job | yes | G4 |
| Supplier, Branch, Charge to, Priority | configuration or operator | supplier only | G10, G11, G13, C9 |

G19, C24, C26, G41 and F16 are the template's formulas and are never written.

Each extracted field is **FOUND**, **MISSING** or **AMBIGUOUS** (two different
values printed). Missing and ambiguous values never reach the template; an
unreadable date is missing — it is not replaced with today's date, as the
earlier script did.

## The validation gate (`po/validate.py`)

Blocking, any one stops the job at `VALIDATION_FAILED`:

* a required field missing or ambiguous;
* BL/AWB: PDF ≠ Hub (compared letters-and-digits only, both values shown, neither chosen);
* the document's own arithmetic: total duty − VAT block ≠ import duty line (± GHS 1.00);
* an amount that is not positive.

## States and events (`po/store.py`)

```
QUEUED → DISCOVERED → PDF_FOUND → PDF_READ → FIELDS_EXTRACTED → VALIDATING → VALIDATED
       → TEMPLATE_GENERATED → EMAIL_PREPARED → EMAIL_SENDING → EMAIL_SENT → EMAIL_CONFIRMED
failures: PDF_NOT_FOUND · PDF_UNREADABLE · EXTRACTION_FAILED · VALIDATION_FAILED
          TEMPLATE_FAILED · EMAIL_FAILED
```

`store.transition()` refuses any step the table does not allow; no path reaches
the template or email without `VALIDATED`. Events: PO_DISCOVERED, PDF_FOUND,
PDF_NOT_FOUND, PDF_READ, PDF_UNREADABLE, FIELDS_EXTRACTED, EXTRACTION_FAILED,
VALIDATION_STARTED, VALIDATION_PASSED, VALIDATION_FAILED,
TEMPLATE_GENERATION_STARTED, TEMPLATE_GENERATED, TEMPLATE_FAILED,
EMAIL_PREPARED, EMAIL_BLOCKED, EMAIL_SEND_STARTED, EMAIL_SENT, EMAIL_CONFIRMED,
EMAIL_FAILED, PO_COMPLETED, RETRY — each with event_id, run_id, po_id,
timestamp, stage, status, source, evidence_reference and metadata.

**What each success means** — nothing is claimed without its evidence:

| Claim | Evidence |
|---|---|
| Validated | every blocking check passed |
| Template generated | the saved file was re-opened and every written cell read back |
| Email sent | Graph answered 202 to the send |
| Verified | the message was found in the mailbox's Sent Items by its internetMessageId |

## Email through Microsoft Graph (`po/mail.py`)

The Outlook desktop UI is never automated. Three Graph calls: create the draft
(with the attachment) → send it → find it in Sent Items. Sending the same draft
twice cannot produce two emails (the draft is gone after the first send), and
a send whose outcome is unknown is looked for in Sent Items before any retry.

**Setup (one time):**

1. Entra ID → App registrations → the ATA app (or a dedicated one) → API
   permissions → Microsoft Graph → **Application** → `Mail.Send` → grant admin consent.
2. Restrict it to the ATA mailbox (Exchange Online PowerShell):
   ```powershell
   New-ApplicationAccessPolicy -AppId <client-id> -PolicyScopeGroupId ata@mantrac.com `
     -AccessRight RestrictAccess -Description "ATA PO Automation sends only as ata@"
   ```
3. Add the client secret to Key Vault as `graph-client-secret`.
4. Deploy with `poMailSender=ata@mantrac.com poMailRecipient=<the one recipient>`
   (and `graphClientId` if it is a dedicated app). App Service reads the secret
   by managed identity; it is never in a file, a log, an event or the audit.

## Duplicate sends and retries

* **Ledger** (`ledger.json`): po identity + document SHA-256 + template version
  + recipient → SENDING / SENT / CONFIRMED / FAILED. The same document to the
  same recipient again is **BLOCKED** with the earlier job named, unless an
  admin authorizes a resend (`po.resend`) with a reason — recorded on the ledger
  and in the audit log.
* **Retry policy** (`pipeline.RETRY_POLICY`): retrieving the PDF and Graph
  calls are retried when the error is transient (503, timeouts, connection
  resets); validation, missing fields and template problems never are.

## Roles (server-side, `controlplane/rbac.py`)

| | ADMIN | OPERATOR | VIEWER |
|---|---|---|---|
| `po.view` — see jobs, documents, results | ✓ | ✓ | ✓ |
| `po.process` — start a job | ✓ | ✓ | |
| `po.send` — send a validated document | ✓ | ✓ | |
| `po.resend` — authorize sending the same document again | ✓ | | |

## Audit

PO_PROCESS_STARTED (who, reference, invoice no.), PO_PROCESSED (document and
its hash, validation result and reasons, template, output, recipient, run ID),
PO_EMAIL_SENT / CONFIRMED / FAILED / BLOCKED (recipient, subject, attachment,
template, Graph status, internetMessageId, run ID, by whom), PO_RESEND_AUTHORIZED
(reason), PO_OUTPUT_DOWNLOADED, and ACCESS_DENIED for every refused route. On a
single machine the same entries go to the PO store's `audit.jsonl`.

## Where it runs

* **Remote:** `POST /api/po` → the control plane queues a `po_process` command
  for a worker (the machine with the Hub browser) → the worker runs
  `python -m po process` in its own process and pushes the record, events, PDF
  and generated document (each checked against its SHA-256) → Send PO goes
  from the control plane through Graph. A worker can never report a send.
* **Single machine:** the dashboard runs the same `python -m po process` as a
  child process. Jobs run one at a time; the page never waits on them.

## ATLAS

The same ATLAS, another domain (`dashboard/atlas_po.py`): questions asked on
the PO page, or naming PO work, are answered from the job records — *Why
wasn't this PO sent? What's missing? Did the PDF match the Hub? Who was it
sent to? What template was used? What failed? What should I do next?* — with
the same Fact / Learned / Recommendation / Not established lines. PO failures
are failure-intelligence records (`intelligence/failures.from_po`); outcomes go
to the learning store as kind `po`, **verified only for a send confirmed in
Sent Items**, and never count as shipments or runs.

## Configuration

| Variable | Meaning |
|---|---|
| `PO_DATA_DIR` | Jobs, events, ledger, documents, output (default `ml/data/po`) |
| `PO_OUTPUT_DIR` | Generated documents (default `<PO_DATA_DIR>/output`) |
| `PO_MAIL_SENDER`, `PO_MAIL_RECIPIENT` | The ATA mailbox; the one recipient |
| `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET` | Graph app (default to the Entra sign-in values) |
| `PO_DEFAULT_SUPPLIER`, `…_BRANCH`, `…_CHARGE_TO`, `…_PRIORITY` | Template defaults |
| `PO_AUTO_SEND` | `1` sends as soon as a job is ready (default off: a person presses Send PO) |
| `PO_DOC_LINK_TEXT` | Regex for the document link's text on the Hub shipment page |
| `PO_HEADLESS`, `PO_BROWSER_CHANNEL` | The job's browser (default headless Edge) |

## Tools

```
python -m po read BOE.pdf --hub-bol 176-88452310 --invoice-no 9116093 [--dump-text]
    what would be extracted and validated — nothing generated or sent
python -m po hub-links 176-88452310
    opens the shipment in the real Hub and lists the document links found
```

## Known limits (version 1)

* **The real Hub's document link is not yet confirmed.** Retrieval is tested
  against a stand-in shipment page in a real browser; on the real Hub run
  `python -m po hub-links <BOL/AWB>` once and, if needed, set
  `PO_DOC_LINK_TEXT` to the link's wording.
* **The field rules are the earlier BOE automation's**, written against
  standard ICUMS wording and tested here on generated declarations. Check them
  on real BOEs with `python -m po read … --dump-text`.
* **Graph is tested against a local stand-in** of the three calls. The first
  real send needs the setup above; until then the page says Microsoft 365 is
  not configured and Send PO answers with that reason.
* One document type, one template, one recipient, Excel output (a PDF copy
  needs Excel or LibreOffice on the worker). Jobs run one at a time; a PO job
  signs in to the Hub alongside a running ETA run.
