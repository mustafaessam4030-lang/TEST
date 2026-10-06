# PO Automation: real Windows worker verification runbook

**Status: NOT EXECUTED.** Nothing in this runbook has been run against the real
eHub, the real Bills of Entry or the real Microsoft 365 mailbox. Until it has
been run and its evidence filed, PO Automation is **NOT READY**.

The tests in this repository run against stand-ins only. They are TESTED WITH
STAND-IN, not production evidence.

**Who:** an operator with eHub access, on the Windows worker PC that runs the ETA
automation.

**Where the evidence goes:** `<PO_DATA_DIR>` (default `ml\data\po`). Each step
below names the file or record that proves it.

**Email:** use a controlled recipient (your own address). The business recipient
is never emailed during verification.

## 0. Before you start

```bat
cd C:\Automation
python run_tests.py
python -m po readiness
```

- `run_tests.py` must end with `0 failed, 0 suite(s) with problems`. It writes
  `test_results.json`.
- `python -m po readiness` shows the A–F readiness levels. Expect E and F to be
  UNVERIFIED.

**Configuration.** The checks below confirm that each setting is present. They
never print a secret.

| Setting | Notes |
|---|---|
| eHub credentials file | `C:\Automation\credentials.txt` |
| `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET` | Mail.Send application permission, admin consent, application access policy |
| `PO_MAIL_SENDER` | the sender mailbox |
| `PO_MAIL_RECIPIENT` | **left unset** for verification |

## 1–12. One real job, through the real eHub

```bat
verify_po.bat
```

This runs the next Under Clearance record. To pick a record, use
`verify_po.bat <BOL/AWB>`. The optional second argument is a cross-check of the
invoice No.: G4 still comes only from the Bill of Entry.

The command runs the real pipeline in this worker's headed Edge session and
prints steps A–I. The job stays in the PO store.

| # | Evidence point | What to capture | Pass when |
|---|---|---|---|
| 1 | eHub sign-in | step A, page host | A = OK, the host is the real eHub, no sign-in page |
| 2 | Shipment discovery | step B, `discovery.ehub_record` | a real row, its BOL/AWB, view BU |
| 3 | Under Clearance verified | milestone `ELIGIBLE_VERIFIED` (`python -m po explain <po_id>`) | observed_status = `Under Clearance`, source and time recorded |
| 4 | Manage page | step C, milestone `MANAGE_OPENED` | the record's Manage URL |
| 5 | Shipment identity | `shipment_identity` in explain | decision MATCH, with its evidence (on the page, or from the document) |
| 6 | Bill Entry discovery | step D, milestones `DOCUMENT_DISCOVERED` / `DOCUMENT_SELECTED` | candidates listed, the selection rule recorded |
| 7 | PDF retrieval | step E, milestone `PDF_RETRIEVED` | bytes and SHA-256; `documents\<sha>.pdf` exists |
| 8 | PDF integrity | milestone `PDF_INTEGRITY_VERIFIED` | header, EOF, not repaired, verified true |
| 9 | Extraction | step F, `extracted` | every required field FOUND, page and method `text` |
| 10 | Validation | step G, `validation.decision` | VALID, or a precise stop you can explain from the PDF |
| 11 | Template | step H, milestones `TEMPLATE_GENERATED` / `TEMPLATE_VERIFIED` | cells and formulas read back |
| 12 | Save | step I, milestone `OUTPUT_PERSISTED` | the file in the output folder, its SHA-256 matches |

**What Accounts must check by hand.** Open the kept PDF and the generated
workbook side by side, and compare:

- C13 (declaration);
- G4, which must be the **Invoice No. printed on this Bill of Entry**;
- G6, C19, C21 and G20;
- the formulas G19, C24 and C26.

Record the result, and every difference, in `<PO_DATA_DIR>\readiness\pilot_review.txt`.

If the real Bill of Entry prints no explicit Invoice No., the job stops at NEEDS
REVIEW (`absent`). **That is the correct behaviour.** Record it: it decides
whether real jobs can ever be automated end to end (business blocker).

## 13–14. A controlled Graph send and its reconciliation

```bat
verify_po.bat <BOL/AWB> "" you@mantracgroup.com
```

Step J sends the generated document **only to the address given**. Pass when
**both** of these hold:

| # | Evidence point | Capture | Pass when |
|---|---|---|---|
| 13 | Graph send | step J | HTTP 202, with the draft's id and internetMessageId recorded |
| 14 | Graph reconciliation | step J | found in Sent Items; the recipient, subject and attachment read back are the ones prepared |

Then check that mailbox yourself: one message, the attachment opens, and the
figures match the PDF.

## 15. The final audit trail

```bat
python -m po explain <po_id>  > <PO_DATA_DIR>\readiness\explain_<po_id>.json
python -m po audit-verify
python -m po readiness
```

Pass when all of these hold:

- explain lists every milestone up to the furthest one reached;
- `audit-verify` prints `"ok": true`;
- readiness shows the real-world pilot gates for this job as PASS.

## Failure cases to observe on the real system (pilot)

Record what happens in each case. Do not force any of them.

- A record that is not Under Clearance, which must be SKIPPED without opening
  Manage.
- A record with no Bill Entry, which must stop at DOCUMENT NOT FOUND.
- A signed-out browser, which must stop at SIGN-IN REQUIRED.
- `python -m po recover` after closing the worker mid-job. The job must resume
  without a second output or email.

## Sign-off

After the pilot review, a security reviewer and the business owner write
`<PO_DATA_DIR>\readiness\signoff.json`:

```json
{"security_review": "clean", "by": "<name>", "at": "<date>", "notes": "<scope reviewed>"}
```

PRODUCTION READY additionally needs the production gates in `python -m po readiness`:
20 confirmed real jobs, recovery observed on real jobs, and duplicate prevention
observed.
