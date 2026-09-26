# boe2duty

Turns a Ghana Customs **Bill of Entry** into a filled **Duty Payment Request**
(the CHEQUE REQUEST template).

```
BOE photo/PDF ──▶ [1 extract] ──▶ boe.json ──▶ [2 check] ──▶ [3 fill] ──▶ filled .xlsx
                   eyes / OCR      contract      arithmetic    Excel writer
                   fallible                      exact         exact
```

## Why it is split in two

Reading a BOE needs eyes. Doing six-term VAT sums and writing twenty cells
without a slip does not. The two halves are joined by one small JSON file, so
**anything** can produce it — a vision model, Azure/Google Document AI, or a
clerk typing eighteen numbers. The half that has to be perfect is the half a
computer is actually good at.

## Why `check` is not optional

A BOE prints its own **Total**. If the line items you transcribed do not add up
to that printed Total, something was misread and the tool refuses to write.

This is not hypothetical. On the first manual pass through the sample BOE the
lines summed to **654,165.54** against a printed **653,670.54** — Network Charge
read as 7,606.49 instead of 7,106.49, and the MoTI e-IDF Fee as 0.00 instead of
5.00. **GHS 495 out**, on a document four people sign. The checksum caught it in
a second. That is the whole reason this step exists.

## Install

```sh
pip install openpyxl          # only needed for the tests
```

`boe2duty.py` itself needs nothing but the Python standard library.

## Use

```sh
# 1. get the BOE into JSON  (needs your own ANTHROPIC_API_KEY)
export ANTHROPIC_API_KEY=sk-ant-...
python3 extract_boe.py scan.png -o boe.json --manual manual.json

# 2. reconcile and read every derived figure before anything is written
python3 boe2duty.py check boe.json

# 3. write it into a copy of the template
python3 boe2duty.py fill boe.json \
    --template "Ghana Duty Payment _ CHEQUE REQUEST TEMPLATE.xlsx" \
    --out "Duty Request 9116093.xlsx"
```

Skipping step 1 is fine — copy `samples/DDAO9116093.json`, change the numbers by
hand, and run steps 2 and 3. You still get the arithmetic and the cell writing.

## What comes from where

| Field | Source |
|---|---|
| BOE No, Date, BL/AWB, User Reference, Bill No | **the BOE** |
| all 18 tax lines and the printed Total | **the BOE** |
| Total Invoice Fcy, BOE rate of exchange | **the BOE** (reference only) |
| `invoice_cfr_cif` | **the supplier invoice** — not on the BOE |
| `bog_rate` | **Bank of Ghana rate** used for costing — not the BOE's own rate |
| payment date, priority, charge-to, supplier, branch | **entered by Logistics** |

The two most important rows in that table are the last two of the "not on the
BOE" group. On the sample, the BOE declares **174,071.09** and its own rate of
exchange is **11.4857**, but the request is prepared with **169,740.11** and
**11.2**. They are different figures from different documents. Anyone automating
this end to end will be tempted to take them off the BOE; that would be wrong,
and it would quietly change the % of duty on every request.

## Derivations

```
Import/Net VAT          = codes 02 + 33 + 47 + 88 + 48 + 89
Import Duty and Levies  = printed Total − Import/Net VAT
Total Duty              = printed Total
Total Duty (BOG Rate)   = printed Total ÷ bog_rate
% of Duty on Invoice    = Total Duty (BOG Rate) ÷ invoice_cfr_cif
Invoice No (G4)         = the digits of User Reference   (DDAO9116093 → 9116093)
```

Derived cells are written as **Excel formulas**, not as numbers, so a reviewer
can click C24 and see `=G6/C21`. Four people sign this sheet; none of them
should have to take a figure on trust.

## Two corrections to the "BOE Template Capture" sheet

Found while building this, both worth fixing in the master template:

1. **`Import/Net VAT` lists five components but the calculation uses six.**
   Rows D13–D17 name Import VAT, Network Charge VAT, Import NHIL, GET Fund
   Import and Network Charge GET Fund Levy. The formula in the filled Duty
   Template adds a sixth — **Network Charge NHIL (code 48, GHS 177.67)**. The
   number is right; the documentation is missing a line. This tool writes that
   component into a new row 18.

2. **`Total Duty (BOG Rate)` is documented backwards.** Cell D26 reads
   *"Exchange Rate / Duty Amount"*. The actual and correct calculation is
   **Duty Amount ÷ Exchange Rate** (653,670.54 ÷ 11.2 = 58,363.44). As written
   it would give 0.0000171.

## Why not openpyxl for writing

openpyxl rebuilds the package on save and **drops embedded images, drawings and
printer settings**. This template carries the Mantrac letterhead and the BOE
screenshot, and a cheque request that prints without its letterhead is not
usable. So `xlsxpatch.py` edits the sheet XML in place and repackages the zip.

Verified on every run: of 47 package parts, 41 come out **byte-identical**, all
six images survive, and only the two target sheets plus three package parts
change. `xl/calcChain.xml` is deliberately dropped and `fullCalcOnLoad="1"` set,
so Excel recalculates cleanly on open.

## Tests

```sh
python3 test_boe2duty.py "Ghana Duty Payment _ CHEQUE REQUEST TEMPLATE.xlsx"
```

The workbook Finance supplied already contained a Duty Template filled **by
hand from this very BOE**, which gives real ground truth. The suite asserts the
tool reproduces it cell for cell — including matching the house ordering of the
six terms in G20 — and that the historical GHS 495 misread is refused.

## Status

- `boe2duty.py`, `xlsxpatch.py` — **tested**, 27 assertions passing against
  real ground truth.
- `extract_boe.py` — **written but never executed.** This machine has no
  `ANTHROPIC_API_KEY`, so the request has never been sent. Treat your first run
  as the test. Step 2 is what makes that safe: a misread digit cannot reach the
  spreadsheet.
