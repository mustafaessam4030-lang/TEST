#!/usr/bin/env python3
"""Regression tests. Run: python3 test_boe2duty.py TEMPLATE.xlsx

The important test is the first one: the workbook shipped by Finance already
contained a Duty Template filled by hand from this very BOE, so we have real
ground truth. The tool must reproduce it cell for cell.
"""
import json, subprocess, sys, tempfile, zipfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import boe2duty as B

SAMPLE = HERE / "samples" / "DDAO9116093.json"

# transcribed from the hand-filled 'Duty Template' in the original workbook
GROUND_TRUTH = {
    "G4": 9116093,
    "G6": 653670.54,
    "C13": "40726534505 / 00",
    "C19": 169740.11,
    "C21": 11.2,
    "G20": "=304446.44+1066.03+50741.08+50741.08+177.67+177.67",
    "G19": "=G6-G20",
    "C24": "=G6/C21",
    "C26": "=C24/C19",
    "G41": "=SUM(G19:G40)",
}

fails = []


def ok(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def main(template):
    d = json.loads(SAMPLE.read_text())

    print("\n1. derived figures")
    r = B.derive(d)
    ok("lines reconcile with printed Total", abs(r["line_sum"] - r["duty_amount"]) < 0.011)
    ok("Import/Net VAT = 407,349.97", r["import_net_vat"] == 407349.97, r["import_net_vat"])
    ok("Duty and Levies = 246,320.57", r["duty_and_levies"] == 246320.57, r["duty_and_levies"])
    ok("Total Duty (BOG) = 58,363.44", r["total_duty_bog"] == 58363.44, r["total_duty_bog"])
    ok("% of duty ≈ 34.38%", abs(r["pct_duty"] - 0.343840) < 1e-5, r["pct_duty"])
    ok("invoice no = 9116093", r["invoice_no"] == 9116093)

    print("\n2. the real misread is refused")
    # Reproduces the exact mistake made on the first manual pass through this
    # BOE: Network Charge read as 7,606.49 instead of 7,106.49 (+500.00) and
    # the MoTI e-IDF Fee read as 0.00 instead of 5.00 (-5.00). Net +495.00 —
    # which is precisely the kind of error no one spots by eye.
    bad = json.loads(SAMPLE.read_text())
    for ln in bad["boe"]["tax_lines"]:
        if ln["code"] == "32": ln["payable"] = 7606.49
        if ln["code"] == "72": ln["payable"] = 0.00
    try:
        B.derive(bad); ok("refuses a non-reconciling BOE", False, "it accepted it")
    except B.Fail as e:
        ok("refuses a non-reconciling BOE", "do not reconcile" in str(e))
        ok("reports the GHS 495.00 discrepancy", "495.00" in str(e), str(e).replace(chr(10), " ")[:160])

    print("\n3. a missing VAT component is refused")
    bad2 = json.loads(SAMPLE.read_text())
    bad2["boe"]["tax_lines"] = [l for l in bad2["boe"]["tax_lines"] if l["code"] != "48"]
    bad2["boe"]["printed_total"] = round(653670.54 - 177.67, 2)
    try:
        B.derive(bad2); ok("refuses a missing VAT code", False, "it accepted it")
    except B.Fail as e:
        ok("refuses a missing VAT code", "48" in str(e))

    print("\n4. fill reproduces the hand-filled sheet")
    import openpyxl
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.xlsx"
        rc = B.main(["fill", str(SAMPLE), "--template", str(template), "--out", str(out)])
        ok("fill exits 0", rc == 0)
        ws = openpyxl.load_workbook(out)["Duty Template"]
        for a, want in GROUND_TRUTH.items():
            got = ws[a].value
            if isinstance(want, str) and want.startswith("="):
                m = str(got).replace(" ", "") == want.replace(" ", "")
            elif isinstance(want, float):
                m = got is not None and abs(float(got) - want) < 0.005
            else:
                m = got == want
            ok(f"{a} == {want!r}", m, repr(got))

        print("\n5. the template survives intact")
        a_, b_ = zipfile.ZipFile(template), zipfile.ZipFile(out)
        na, nb = set(a_.namelist()), set(b_.namelist())
        ok("all 6 embedded images kept",
           len([n for n in nb if "media/" in n]) == len([n for n in na if "media/" in n]))
        ok("only calcChain removed", na - nb == {"xl/calcChain.xml"}, str(na - nb))
        changed = {n for n in na & nb if a_.read(n) != b_.read(n)}
        ok("only the 2 target sheets + 3 package parts changed", len(changed) == 5, str(sorted(changed)))
        ok("drawings untouched", not any("drawing" in n for n in changed))

    print("\n" + ("ALL TESTS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: test_boe2duty.py TEMPLATE.xlsx")
    raise SystemExit(main(sys.argv[1]))
