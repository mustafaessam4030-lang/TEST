#!/usr/bin/env python3
"""extract_boe.py — read a BOE image or PDF into boe.json using a vision model.

    export ANTHROPIC_API_KEY=sk-ant-...
    python3 extract_boe.py scan.png  -o boe.json
    python3 extract_boe.py scan.pdf  -o boe.json --manual manual.json

Then ALWAYS reconcile before filling:

    python3 boe2duty.py check boe.json

NOT TESTED END-TO-END. It was written against the documented Messages API
shape but this machine has no API key, so the request has never actually been
sent. Treat the first run as the test. The reconciliation step in boe2duty.py
is what makes that acceptable: a misread digit cannot reach the spreadsheet,
because the line items will not add up to the BOE's own printed Total.

Only the `boe` block is extracted. invoice_cfr_cif and bog_rate are NOT on the
BOE — they come from the supplier invoice and the Bank of Ghana rate — so they
are merged in from --manual, or left as nulls for you to fill.
"""
from __future__ import annotations
import argparse, base64, json, os, sys
from pathlib import Path

MODEL = "claude-sonnet-4-5"
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp", ".pdf": "application/pdf"}

SCHEMA = {
    "name": "boe_fields",
    "description": "Fields transcribed from a Ghana Customs Bill of Entry.",
    "input_schema": {
        "type": "object",
        "required": ["user_reference", "boe_no", "date", "printed_total", "tax_lines"],
        "properties": {
            "user_reference": {"type": "string", "description": "e.g. DDAO9116093, from the page footer"},
            "boe_no":  {"type": "string", "description": "Bill of Entry(BOE) No exactly as printed, e.g. '40726534505 / 00'"},
            "bill_no": {"type": "string", "description": "Bill No. from the footer, e.g. KIA1-G-40726534505-01"},
            "bl_awb":  {"type": "string"},
            "date":    {"type": "string", "description": "the BOE Date as ISO YYYY-MM-DD (printed DD/MM/YYYY)"},
            "office_code": {"type": "string"},
            "regime": {"type": "string"},
            "importer": {"type": "string"},
            "declarant": {"type": "string"},
            "delivery_terms": {"type": "string", "description": "box 12, e.g. 'CPT ACCRA'"},
            "country_of_consignment": {"type": "string"},
            "total_invoice_fcy": {"type": "number", "description": "box 13"},
            "currency": {"type": "string"},
            "boe_rate_of_xchange": {"type": "number", "description": "box 16"},
            "gross_mass_kg": {"type": "number"},
            "printed_total": {"type": "number", "description": "the Total on the Amount Payable column — transcribe, never compute"},
            "tax_lines": {
                "type": "array",
                "description": "every row of the tax table, including rows whose amount is 0.00",
                "items": {
                    "type": "object",
                    "required": ["code", "name", "payable"],
                    "properties": {
                        "code": {"type": "string", "description": "2-digit Code column, zero-padded"},
                        "name": {"type": "string", "description": "the Taxes label"},
                        "payable": {"type": "number", "description": "Amount Payable column"},
                    },
                },
            },
        },
    },
}

PROMPT = """Transcribe this Ghana Revenue Authority Customs declaration (Bill of Entry).

Rules:
- Transcribe only. Never calculate, infer or correct a figure.
- The tax table at the bottom right has three columns: Code, Amount
  Exempted/Suspended, and Amount Payable. Take the AMOUNT PAYABLE column.
- Include every tax row, including ones showing 0.00.
- Read the printed Total exactly as shown. Do not add the rows up yourself —
  the total is checked against your rows later, and that check only works if
  you report both independently.
- Digits matter more than anything else here: this becomes a duty cheque.
  Where a digit is genuinely ambiguous, prefer what the character shape most
  supports; do not guess to make a total balance.
- Dates print as DD/MM/YYYY. Return ISO YYYY-MM-DD.

Return via the boe_fields tool."""

BLANK_MANUAL = {
    "_why": "Not present on the BOE. Fill these before running boe2duty.py fill.",
    "invoice_cfr_cif": None,
    "bog_rate": None,
    "payment_date": None,
    "ref": None,
    "priority": "AIR FREIGHT - TOP URGENT",
    "from_dept": "LOGISTICS",
    "to_dept": "ACCOUNT DEPT",
    "payable_to": "GHANA REVENUE AUTHORITY(CUSTOMS)",
    "supplier": None,
    "receiving_branch": " ",
    "charge_to": None,
}


def block(path: Path) -> dict:
    mt = MEDIA.get(path.suffix.lower())
    if not mt:
        sys.exit(f"unsupported file type: {path.suffix} (need {', '.join(sorted(MEDIA))})")
    b64 = base64.standard_b64encode(path.read_bytes()).decode()
    kind = "document" if mt == "application/pdf" else "image"
    return {"type": kind, "source": {"type": "base64", "media_type": mt, "data": b64}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scan", type=Path, help="BOE image or PDF")
    ap.add_argument("-o", "--out", type=Path, default=Path("boe.json"))
    ap.add_argument("--manual", type=Path, help="JSON of the non-BOE fields to merge in")
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY is not set")
    if not a.scan.exists():
        sys.exit(f"not found: {a.scan}")

    import requests
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={
            "model": a.model,
            "max_tokens": 4096,
            "tools": [SCHEMA],
            "tool_choice": {"type": "tool", "name": "boe_fields"},
            "messages": [{"role": "user",
                          "content": [block(a.scan), {"type": "text", "text": PROMPT}]}],
        },
        timeout=180,
    )
    if r.status_code != 200:
        sys.exit(f"API {r.status_code}: {r.text[:600]}")

    use = next((c for c in r.json().get("content", []) if c.get("type") == "tool_use"), None)
    if not use:
        sys.exit("model did not return the boe_fields tool call")
    boe = use["input"]
    boe["_source_document"] = a.scan.name

    manual = json.loads(a.manual.read_text()) if a.manual else dict(BLANK_MANUAL)
    manual = manual.get("manual", manual)

    a.out.write_text(json.dumps({"boe": boe, "manual": manual}, indent=2, ensure_ascii=False))

    lines = boe.get("tax_lines", [])
    s = round(sum(float(x["payable"]) for x in lines), 2)
    t = round(float(boe["printed_total"]), 2)
    print(f"wrote {a.out}  ({len(lines)} tax lines)")
    print(f"  sum of lines {s:>14,.2f}")
    print(f"  printed Total{t:>14,.2f}   {'reconciles' if abs(s-t) < 0.011 else 'DOES NOT RECONCILE — re-check the scan'}")
    print(f"\nNext: python3 boe2duty.py check {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
