#!/usr/bin/env python3
"""boe2duty — turn a Ghana Customs Bill of Entry into a filled Duty Payment Request.

    python3 boe2duty.py check  boe.json
    python3 boe2duty.py fill   boe.json --template TEMPLATE.xlsx --out FILLED.xlsx

THE SPLIT THAT MATTERS
----------------------
Reading a BOE needs eyes (or OCR). Doing the arithmetic and writing 20 cells
without a slip does not. So this tool owns the second half only, and the
contract between the halves is one small JSON file.

That boundary is deliberate: extraction is where a machine is unreliable and
a human is fast; the fill is where a human is unreliable and a machine is
exact. Anything that can produce the JSON — a vision model, Document AI, or a
clerk typing 18 numbers — plugs in the same way.

WHY THE CHECK EXISTS
--------------------
A BOE prints its own Total. If the line items we read do not add up to that
printed Total, something was misread and the tool refuses to write. When this
was first built by hand the line items summed to 654,165.54 against a printed
653,670.54 — two digits misread, GHS 495 out. The checksum caught it
instantly. Never skip it.
"""
from __future__ import annotations
import argparse, datetime as dt, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import xlsxpatch as xp

# The six charge codes that make up "Import/Net VAT" on the Duty Template.
# Everything else on the BOE falls into "Import Duty and Levies".
VAT_CODES = {
    "02": "Import VAT",
    "33": "Network Charge VAT",
    "47": "Import NHIL",
    "48": "Network Charge NHIL",
    "88": "Ghana Education Trust (GET) Fund Import",
    "89": "Network Charge GET Fund Levy",
}
VAT_ORDER = ["02", "33", "47", "88", "48", "89"]
TOL = 0.011          # BOE prints 2dp; allow a cent of rounding noise


class Fail(Exception):
    pass


def money(x) -> float:
    """Accept 1234.56, '1,234.56', '1 234.56' or '(12.34)' for negatives."""
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace(" ", "").replace("GHS", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    v = float(s or 0)
    return -v if neg else v


def load(path: Path) -> dict:
    d = json.loads(Path(path).read_text())
    for k in ("boe", "manual"):
        if k not in d:
            raise Fail(f"{path}: missing top-level {k!r} block")
    return d


def derive(d: dict) -> dict:
    """Validate the BOE against its own printed total, then compute every
    figure the Duty Template needs. Raises Fail on anything that does not
    reconcile — a duty cheque is not a place to let a rounding error through."""
    boe, man = d["boe"], d["manual"]
    lines = boe.get("tax_lines") or []
    if not lines:
        raise Fail("boe.tax_lines is empty — nothing to reconcile")

    by_code: dict[str, float] = {}
    for ln in lines:
        code = str(ln["code"]).zfill(2)
        by_code[code] = by_code.get(code, 0.0) + money(ln["payable"])

    line_sum = round(sum(by_code.values()), 2)
    printed = round(money(boe["printed_total"]), 2)
    if abs(line_sum - printed) > TOL:
        raise Fail(
            f"line items do not reconcile with the BOE's printed Total.\n"
            f"    sum of {len(lines)} lines : {line_sum:>14,.2f}\n"
            f"    printed Total           : {printed:>14,.2f}\n"
            f"    difference              : {line_sum - printed:>14,.2f}\n"
            f"  A misread digit is the usual cause. Re-check the tax table "
            f"before this goes anywhere near a cheque."
        )

    missing = [f"{c} {n}" for c, n in VAT_CODES.items() if c not in by_code]
    if missing:
        raise Fail("BOE is missing expected VAT component code(s): "
                   + ", ".join(missing)
                   + "\n  If this BOE genuinely has none, add the code with payable 0.00.")

    # House ordering, taken from the requests already on file: the two GET/
    # NHIL pairs are grouped by size rather than by code. Same six numbers and
    # the same sum either way, but matching it keeps new sheets
    # indistinguishable from the ones Finance already signs.
    vat_parts = [(c, round(by_code[c], 2)) for c in VAT_ORDER]
    import_net_vat = round(sum(v for _, v in vat_parts), 2)
    duty_and_levies = round(printed - import_net_vat, 2)

    bog = money(man["bog_rate"])
    if bog <= 0:
        raise Fail("manual.bog_rate must be greater than zero")
    cfr = money(man["invoice_cfr_cif"])
    if cfr <= 0:
        raise Fail("manual.invoice_cfr_cif must be greater than zero "
                   "(it comes from the supplier invoice, not the BOE)")

    total_duty_bog = round(printed / bog, 2)
    pct = total_duty_bog / cfr

    # the invoice number on the request is the numeric tail of the BOE's
    # User Reference (DDAO9116093 -> 9116093)
    ref = str(boe.get("user_reference", ""))
    digits = "".join(ch for ch in ref if ch.isdigit())
    if not digits:
        raise Fail(f"cannot derive an invoice number from user_reference {ref!r}")

    return {
        "invoice_no": int(digits),
        "duty_amount": printed,
        "vat_parts": vat_parts,
        "import_net_vat": import_net_vat,
        "duty_and_levies": duty_and_levies,
        "total_duty_bog": total_duty_bog,
        "pct_duty": pct,
        "line_sum": line_sum,
        "line_count": len(lines),
    }


def cmd_check(args):
    d = load(args.json)
    r = derive(d)
    b, m = d["boe"], d["manual"]
    w = 34
    print("BOE reconciled\n")
    for label, val in [
        ("User Reference", b.get("user_reference", "")),
        ("Bill of Entry (BOE) No", b.get("boe_no", "")),
        ("BL/AWB No", b.get("bl_awb", "")),
        ("BOE Date", b.get("date", "")),
        ("Bill No", b.get("bill_no", "")),
    ]:
        print(f"  {label:<{w}} {val}")
    print()
    print(f"  {'Tax lines read':<{w}} {r['line_count']}")
    print(f"  {'Sum of lines':<{w}} {r['line_sum']:>16,.2f}")
    print(f"  {'BOE printed Total':<{w}} {r['duty_amount']:>16,.2f}  ✓ match")
    print()
    print("  Import/Net VAT components")
    for c, v in r["vat_parts"]:
        print(f"    {c}  {VAT_CODES[c]:<44} {v:>14,.2f}")
    print(f"    {'':<4}{'Import/Net VAT':<44} {r['import_net_vat']:>14,.2f}")
    print()
    print(f"  {'Import Duty and Levies':<{w}} {r['duty_and_levies']:>16,.2f}")
    print(f"  {'Total Duty':<{w}} {r['duty_amount']:>16,.2f}")
    print(f"  {'BOG Exchange Rate':<{w}} {money(m['bog_rate']):>16,.4f}   (manual)")
    print(f"  {'Total Duty (BOG Rate)':<{w}} {r['total_duty_bog']:>16,.2f}")
    print(f"  {'Invoice Value (CFR/CIF)':<{w}} {money(m['invoice_cfr_cif']):>16,.2f}   (supplier invoice)")
    print(f"  {'% of Duty on Invoice':<{w}} {r['pct_duty']*100:>15,.2f}%")
    return 0


def cmd_fill(args):
    d = load(args.json)
    r = derive(d)
    b, m = d["boe"], d["manual"]
    src, out = Path(args.template), Path(args.out)
    if not src.exists():
        raise Fail(f"template not found: {src}")

    def d8(s):
        return dt.date.fromisoformat(str(s)[:10])

    # ── The Duty Payment Request itself ────────────────────────────────
    # Inputs are written as values; everything derived stays a FORMULA, so a
    # reviewer can click C24 in Excel and see =G6/C21 rather than a number
    # they have to take on trust. Four people sign this sheet.
    duty = {
        "G4":  xp.num(r["invoice_no"]),
        "G6":  xp.num(r["duty_amount"]),
        "G8":  xp.date(d8(m.get("payment_date") or b["date"])),
        "C13": xp.text(b["boe_no"]),
        "C19": xp.num(money(m["invoice_cfr_cif"])),
        "C21": xp.num(money(m["bog_rate"])),
        # house style: the six components spelled out, so the sheet shows its work
        "G20": xp.formula("=" + "+".join(f"{v:.2f}" for _, v in r["vat_parts"])),
        "G19": xp.formula("=G6-G20"),
        "C24": xp.formula("=G6/C21"),
        "C26": xp.formula("=C24/C19"),
        "G41": xp.formula("=SUM(G19:G40)"),
        "G16": xp.formula("=G4"),
    }
    for cell, key in [("D2", "ref"), ("C6", "from_dept"), ("C9", "priority"),
                      ("C11", "to_dept"), ("C14", "payable_to"),
                      ("G10", "supplier"), ("G11", "receiving_branch"),
                      ("G13", "charge_to")]:
        if m.get(key) is not None:
            duty[cell] = xp.text(m[key])

    # ── Audit trail, in the capture sheet's spare column ──────────────
    # Column C is the label and D is the derivation, so E is where the value
    # belongs. Row 18 gets the sixth VAT component, which the printed sheet
    # omits (see README).
    cap = {
        "E4":  xp.text(b.get("user_reference", "")),
        "E5":  xp.text(b.get("boe_no", "")),
        "E6":  xp.text(b.get("bl_awb", "")),
        "E7":  xp.text(str(b["date"])[:10]),
        "E10": xp.num(r["duty_and_levies"]),
        "E19": xp.num(r["duty_amount"]),
        "E24": xp.num(money(m["bog_rate"])),
        "E26": xp.num(r["total_duty_bog"]),
        "E28": xp.num(money(m["invoice_cfr_cif"])),
        "E30": xp.num(round(r["pct_duty"], 6)),
        "D18": xp.text("Network Charge NHIL"),
    }
    # rows 13-17 follow the labels already printed in column D; the sixth
    # component (code 48) has no printed row, so it gets row 18 (see README)
    parts = dict(r["vat_parts"])
    for i, code in enumerate(["02", "33", "47", "88", "89"]):
        cap[f"E{13+i}"] = xp.num(parts[code])
    cap["E18"] = xp.num(parts["48"])
    cap["E20"] = xp.num(r["duty_and_levies"])
    cap["E21"] = xp.num(r["import_net_vat"])

    paths = xp.sheet_paths(src)
    for name in ("Duty Template", "BOE Template Capture"):
        if name not in paths:
            raise Fail(f"template has no sheet named {name!r}")

    notes = xp.patch(src, out, {"Duty Template": duty,
                                "BOE Template Capture": cap}, paths)
    print(f"wrote {out}")
    print(f"  Duty Template        {len(duty)} cells")
    print(f"  BOE Template Capture {len(cap)} cells")
    for n in notes:
        print(f"  · {n}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="boe2duty", description=__doc__,
                               formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="reconcile the BOE and print every derived figure")
    c.add_argument("json", type=Path)
    c.set_defaults(fn=cmd_check)
    f = sub.add_parser("fill", help="write the figures into the Excel template")
    f.add_argument("json", type=Path)
    f.add_argument("--template", required=True, type=Path)
    f.add_argument("--out", required=True, type=Path)
    f.set_defaults(fn=cmd_fill)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except Fail as e:
        print(f"\nREFUSED: {e}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
