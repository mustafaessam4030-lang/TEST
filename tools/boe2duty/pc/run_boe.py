#!/usr/bin/env python3
# ============================================================================
#  run_boe.py  —  drop BOE photos in a folder, get filled Duty Requests out.
#
#  SETUP (once)
#    1. Put this file in a folder together with your template .xlsx
#    2. Put your Anthropic API key in a file next to it called  key.txt
#    3. Run it once: it creates the  in\  and  out\  folders, then stop.
#
#  EVERY TIME
#    1. Drop BOE photos or PDFs into   in\
#    2. Double-click  run.bat   (or:  python run_boe.py )
#    3. Filled workbooks appear in   out\   plus summary.csv
#
#  Needs nothing installed. Plain Python 3.8+, standard library only.
# ============================================================================

# ---------------------------------------------------------------- SETTINGS --
# The Bank of Ghana rate used for costing. NOT the rate printed on the BOE.
# Override per-BOE in inputs.csv when it changes.
DEFAULT_BOG_RATE = 11.2

# Filled in on every request unless inputs.csv says otherwise.
DEFAULTS = {
    "priority":   "AIR FREIGHT - TOP URGENT",
    "from_dept":  "LOGISTICS",
    "to_dept":    "ACCOUNT DEPT",
    "payable_to": "GHANA REVENUE AUTHORITY(CUSTOMS)",
    "supplier":   "CAT",
    "charge_to":  "",
    "ref":        "",
}

MODEL = "claude-sonnet-4-5"
# ---------------------------------------------------------------------------

import base64, csv, datetime as dt, json, os, re, shutil, sys, urllib.request, zipfile
from pathlib import Path

HERE   = Path(__file__).resolve().parent
IN     = HERE / "in"
OUT    = HERE / "out"
CACHE  = HERE / "_read"          # extracted JSON, kept so re-runs are free
INPUTS = HERE / "inputs.csv"

VAT_ORDER = ["02", "33", "47", "88", "48", "89"]
VAT_NAMES = {"02": "Import VAT", "33": "Network Charge VAT", "47": "Import NHIL",
             "48": "Network Charge NHIL", "88": "GET Fund Import",
             "89": "Network Charge GET Fund Levy"}
IMG = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
       ".webp": "image/webp", ".gif": "image/gif", ".pdf": "application/pdf"}
TOL = 0.011


def say(*a): print(*a, flush=True)


class Stop(Exception):
    pass


# ============================================================ 1. EXTRACT ====
PROMPT = """Transcribe this Ghana Revenue Authority Customs declaration (Bill of Entry).

Rules:
- Transcribe only. Never calculate, infer or correct a figure.
- The tax table at the bottom right has three columns: Code, Amount
  Exempted/Suspended, Amount Payable. Take the AMOUNT PAYABLE column.
- Include every tax row, including rows showing 0.00.
- Report the printed Total exactly as shown. Do NOT add the rows up yourself:
  the total is checked against your rows afterwards, and that check only works
  if the two are reported independently.
- Digits matter more than anything here; this becomes a duty cheque. Where a
  digit is genuinely ambiguous, go with the character shape. Never adjust a
  figure to make a total balance.
- Dates print DD/MM/YYYY. Return ISO YYYY-MM-DD.

Return via the boe_fields tool."""

TOOL = {
    "name": "boe_fields",
    "description": "Fields transcribed from a Ghana Customs Bill of Entry.",
    "input_schema": {
        "type": "object",
        "required": ["user_reference", "boe_no", "date", "printed_total", "tax_lines"],
        "properties": {
            "user_reference": {"type": "string"},
            "boe_no": {"type": "string", "description": "exactly as printed, e.g. '40726534505 / 00'"},
            "bill_no": {"type": "string"},
            "bl_awb": {"type": "string"},
            "date": {"type": "string", "description": "ISO YYYY-MM-DD"},
            "total_invoice_fcy": {"type": "number"},
            "currency": {"type": "string"},
            "boe_rate_of_xchange": {"type": "number"},
            "printed_total": {"type": "number"},
            "tax_lines": {
                "type": "array",
                "items": {"type": "object",
                          "required": ["code", "name", "payable"],
                          "properties": {"code": {"type": "string"},
                                         "name": {"type": "string"},
                                         "payable": {"type": "number"}}},
            },
        },
    },
}


def api_key():
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if k:
        return k
    f = HERE / "key.txt"
    if f.exists():
        k = f.read_text(encoding="utf-8-sig").strip()
        if k:
            return k
    raise Stop("No API key.\n"
               f"  Put it in a file called key.txt next to this script:\n"
               f"    {HERE / 'key.txt'}\n"
               "  (one line, starts with sk-ant-)")


def read_boe(scan: Path) -> dict:
    """Ask the model to transcribe one BOE. Cached, so re-runs cost nothing."""
    cached = CACHE / (scan.stem + ".json")
    if cached.exists():
        say(f"    (using cached read: {cached.name})")
        return json.loads(cached.read_text(encoding="utf-8"))

    mt = IMG.get(scan.suffix.lower())
    if not mt:
        raise Stop(f"unsupported file type {scan.suffix} (use {', '.join(sorted(IMG))})")
    blk = {"type": "document" if mt == "application/pdf" else "image",
           "source": {"type": "base64", "media_type": mt,
                      "data": base64.standard_b64encode(scan.read_bytes()).decode()}}
    body = json.dumps({
        "model": MODEL, "max_tokens": 4096, "tools": [TOOL],
        "tool_choice": {"type": "tool", "name": "boe_fields"},
        "messages": [{"role": "user", "content": [blk, {"type": "text", "text": PROMPT}]}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"x-api-key": api_key(), "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise Stop(f"API error {e.code}: {e.read().decode()[:400]}")
    except Exception as e:
        raise Stop(f"could not reach the API: {e}")

    use = next((c for c in payload.get("content", []) if c.get("type") == "tool_use"), None)
    if not use:
        raise Stop("the model did not return the boe_fields tool call")
    boe = use["input"]
    CACHE.mkdir(exist_ok=True)
    cached.write_text(json.dumps(boe, indent=2, ensure_ascii=False), encoding="utf-8")
    return boe


# ============================================================== 2. CHECK ====
def money(x):
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "").replace(" ", "").replace("GHS", "")
    neg = s.startswith("(") and s.endswith(")")
    v = float(s.strip("()") or 0)
    return -v if neg else v


def reconcile(boe: dict, bog: float, cfr):
    """The gate. A BOE prints its own Total; if the transcribed lines do not add
    up to it, a digit was misread and we refuse to write anything."""
    lines = boe.get("tax_lines") or []
    if not lines:
        raise Stop("no tax lines were read from this BOE")

    by = {}
    for ln in lines:
        c = str(ln["code"]).zfill(2)
        by[c] = by.get(c, 0.0) + money(ln["payable"])

    total = round(money(boe["printed_total"]), 2)
    ssum = round(sum(by.values()), 2)
    if abs(ssum - total) > TOL:
        raise Stop(
            "DOES NOT ADD UP — a digit was misread.\n"
            f"      {'sum of ' + str(len(lines)) + ' lines':<16}: {ssum:>14,.2f}\n"
            f"      {'printed Total':<16}: {total:>14,.2f}\n"
            f"      {'difference':<16}: {ssum - total:>14,.2f}\n"
            "      Open the scan and check the Amount Payable column.")

    missing = [f"{c} ({VAT_NAMES[c]})" for c in VAT_ORDER if c not in by]
    if missing:
        raise Stop("BOE is missing VAT component(s): " + ", ".join(missing) +
                   "\n      If this BOE really has none, that is unusual — check the scan.")

    parts = [(c, round(by[c], 2)) for c in VAT_ORDER]
    vat = round(sum(v for _, v in parts), 2)
    ref = "".join(ch for ch in str(boe.get("user_reference", "")) if ch.isdigit())
    if not ref:
        raise Stop(f"no digits in User Reference {boe.get('user_reference')!r}")

    return {"invoice_no": int(ref), "total": total, "parts": parts, "vat": vat,
            "levies": round(total - vat, 2),
            "bog_total": round(total / bog, 2) if bog else None,
            "pct": (round(total / bog, 2) / cfr) if (bog and cfr) else None}


# =============================================================== 3. FILL ====
def xesc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def serial(d):
    return (d - dt.date(1899, 12, 30)).days


def coln(ref):
    n = 0
    for ch in re.match(r"([A-Z]+)", ref).group(1):
        n = n * 26 + ord(ch) - 64
    return n


def cell_xml(kind, val):
    if kind == "num":
        return "", f"<v>{val}</v>"
    if kind == "date":
        return "", f"<v>{serial(val)}</v>"
    if kind == "f":
        return "", f"<f>{xesc(str(val).lstrip('='))}</f>"
    return ' t="inlineStr"', f'<is><t xml:space="preserve">{xesc(val)}</t></is>'


def put_row(xml, r):
    for m in re.finditer(r'<row[^>]*\br="(\d+)"', xml):
        if int(m.group(1)) > r:
            return xml[:m.start()] + f'<row r="{r}"></row>' + xml[m.start():]
    m = re.search(r"</sheetData>", xml)
    return xml[:m.start()] + f'<row r="{r}"></row>' + xml[m.start():] if m else xml


def put_cells(xml, cells):
    for ref, (kind, val) in cells.items():
        extra, inner = cell_xml(kind, val)
        m = re.search(rf'<c r="{ref}"((?:\s+[a-zA-Z:]+="[^"]*")*)\s*(/>|>.*?</c>)', xml, re.S)
        if m:
            s = re.search(r'\s+s="(\d+)"', m.group(1))
            style = f' s="{s.group(1)}"' if s else ""
            xml = xml[:m.start()] + f'<c r="{ref}"{style}{extra}>{inner}</c>' + xml[m.end():]
            continue
        rn = re.match(r"[A-Z]+(\d+)", ref).group(1)
        if not re.search(rf'<row[^>]*\br="{rn}"', xml):
            xml = put_row(xml, int(rn))
        rm = re.search(rf'<row[^>]*\br="{rn}"[^>]*>(.*?)</row>', xml, re.S)
        body, new = rm.group(1), f'<c r="{ref}"{extra}>{inner}</c>'
        pos = len(body)
        for mm in re.finditer(r'<c r="([A-Z]+\d+)"', body):
            if coln(mm.group(1)) > coln(ref):
                pos = mm.start()
                break
        body = body[:pos] + new + body[pos:]
        xml = xml[:rm.start(1)] + body + xml[rm.end(1):]
    return xml


def sheet_map(z):
    wb = z.read("xl/workbook.xml").decode()
    rels = z.read("xl/_rels/workbook.xml.rels").decode()
    rel = {m.group(1): m.group(2) for m in
           re.finditer(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels)}
    return {m.group(1): "xl/" + rel.get(m.group(2), "").lstrip("/").replace("../", "")
            for m in re.finditer(r'<sheet name="([^"]+)"[^>]*r:id="([^"]+)"', wb)}


def fill(template: Path, out: Path, boe: dict, r: dict, man: dict):
    """Patch the sheet XML in place. openpyxl would drop the letterhead and the
    embedded BOE image on save, and a cheque request without its letterhead is
    not usable."""
    def D(s):
        return dt.date.fromisoformat(str(s)[:10])

    duty = {
        "G4":  ("num", r["invoice_no"]),
        "G6":  ("num", r["total"]),
        "G8":  ("date", D(man.get("payment_date") or boe["date"])),
        "C13": ("s", boe["boe_no"]),
        "G16": ("f", "=G4"),
        "G20": ("f", "=" + "+".join(f"{v:.2f}" for _, v in r["parts"])),
        "G19": ("f", "=G6-G20"),
        "G41": ("f", "=SUM(G19:G40)"),
    }
    if man.get("bog_rate"):
        duty["C21"] = ("num", money(man["bog_rate"]))
        duty["C24"] = ("f", "=G6/C21")
    if man.get("invoice_cfr_cif"):
        duty["C19"] = ("num", money(man["invoice_cfr_cif"]))
        duty["C26"] = ("f", "=C24/C19")
    for c, k in (("D2", "ref"), ("C6", "from_dept"), ("C9", "priority"),
                 ("C11", "to_dept"), ("C14", "payable_to"), ("G10", "supplier"),
                 ("G13", "charge_to")):
        if man.get(k):
            duty[c] = ("s", man[k])

    p = dict(r["parts"])
    cap = {"E4": ("s", boe.get("user_reference", "")),
           "E5": ("s", boe.get("boe_no", "")),
           "E6": ("s", boe.get("bl_awb", "")),
           "E7": ("s", str(boe["date"])[:10]),
           "E10": ("num", r["levies"]), "E19": ("num", r["total"]),
           "E20": ("num", r["levies"]), "E21": ("num", r["vat"]),
           "D18": ("s", "Network Charge NHIL"), "E18": ("num", p["48"])}
    for i, c in enumerate(["02", "33", "47", "88", "89"]):
        cap[f"E{13+i}"] = ("num", p[c])
    if man.get("bog_rate"):
        cap["E24"] = ("num", money(man["bog_rate"]))
        cap["E26"] = ("num", r["bog_total"])
    if man.get("invoice_cfr_cif"):
        cap["E28"] = ("num", money(man["invoice_cfr_cif"]))
        if r["pct"]:
            cap["E30"] = ("num", round(r["pct"], 6))

    zin = zipfile.ZipFile(template)
    sm = sheet_map(zin)
    for name in ("Duty Template", "BOE Template Capture"):
        if name not in sm:
            zin.close()
            raise Stop(f"the template has no sheet called {name!r}")
    targets = {sm["Duty Template"]: duty, sm["BOE Template Capture"]: cap}

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for it in zin.infolist():
            n = it.filename
            if n == "xl/calcChain.xml":
                continue                       # Excel rebuilds it
            d = zin.read(n)
            if n == "[Content_Types].xml":
                d = re.sub(rb'<Override PartName="/xl/calcChain\.xml"[^>]*/>', b"", d)
            elif n == "xl/_rels/workbook.xml.rels":
                d = re.sub(rb'<Relationship[^>]*calcChain\.xml"[^>]*/>', b"", d)
            elif n == "xl/workbook.xml":
                s = d.decode()
                if "fullCalcOnLoad" not in s:
                    s = s.replace("<calcPr ", '<calcPr fullCalcOnLoad="1" ', 1)
                d = s.encode()
            elif n in targets:
                d = put_cells(d.decode(), targets[n]).encode()
            zo.writestr(it, d)
    zin.close()


# ================================================================= MAIN ====
CSV_COLS = ["ref", "invoice_cfr_cif", "bog_rate", "payment_date",
            "charge_to", "supplier", "priority", "note_ref"]


def load_inputs():
    """inputs.csv lets you supply the two figures that are NOT on the BOE.
    Match on any part of the User Reference or the file name."""
    if not INPUTS.exists():
        with INPUTS.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(CSV_COLS)
            w.writerow(["9116093", "169740.11", "11.2", "2026-07-16",
                        "32600.CPA.G005", "CAT", "AIR FREIGHT - TOP URGENT",
                        "ETALATA:17/07"])
        say(f"  created {INPUTS.name} (example row inside — edit it)")
        return []
    with INPUTS.open(newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if (r.get("ref") or "").strip()]


def match_row(rows, boe, scan: Path):
    ref = str(boe.get("user_reference", ""))
    for r in rows:
        k = r["ref"].strip()
        if k and (k in ref or k in scan.stem or k in str(boe.get("boe_no", ""))):
            return r
    return {}


def find_template():
    c = [p for p in HERE.glob("*.xlsx")
         if not p.name.startswith("~$") and "Duty Request" not in p.name]
    if not c:
        raise Stop(f"No template .xlsx found in {HERE}\n"
                   "  Put the CHEQUE REQUEST TEMPLATE .xlsx next to this script.")
    return sorted(c, key=lambda p: -p.stat().st_size)[0]


def main():
    say("=" * 66)
    say("  BOE  ->  Duty Payment Request")
    say("=" * 66)
    for d in (IN, OUT):
        d.mkdir(exist_ok=True)

    template = find_template()
    say(f"  template : {template.name}")
    rows = load_inputs()

    scans = sorted(p for p in IN.iterdir()
                   if p.is_file() and p.suffix.lower() in IMG and not p.name.startswith("~"))
    if not scans:
        say(f"\n  Nothing to do — put BOE photos or PDFs in:\n    {IN}\n")
        return 0

    say(f"  found    : {len(scans)} file(s) in in\\\n")
    results = []
    for i, scan in enumerate(scans, 1):
        say(f"[{i}/{len(scans)}] {scan.name}")
        row, boe = {}, None
        try:
            boe = read_boe(scan)
            row = match_row(rows, boe, scan)
            bog = money(row.get("bog_rate") or 0) or DEFAULT_BOG_RATE
            cfr = money(row["invoice_cfr_cif"]) if (row.get("invoice_cfr_cif") or "").strip() else None
            r = reconcile(boe, bog, cfr)

            man = dict(DEFAULTS)
            man["bog_rate"] = bog
            man["invoice_cfr_cif"] = cfr
            man["payment_date"] = (row.get("payment_date") or "").strip() or None
            for k, col in (("charge_to", "charge_to"), ("supplier", "supplier"),
                           ("priority", "priority"), ("ref", "note_ref")):
                if (row.get(col) or "").strip():
                    man[k] = row[col].strip()

            name = f"Duty Request {r['invoice_no']}.xlsx"
            fill(template, OUT / name, boe, r, man)

            say(f"    reconciled  GHS {r['total']:>14,.2f}  ({len(boe['tax_lines'])} lines)")
            say(f"    VAT         GHS {r['vat']:>14,.2f}")
            say(f"    levies      GHS {r['levies']:>14,.2f}")
            if cfr is None:
                say("    NOTE: no invoice_cfr_cif in inputs.csv — C19/C26 left blank")
            say(f"    -> out\\{name}\n")
            results.append({"file": scan.name, "status": "OK",
                            "invoice_no": r["invoice_no"], "boe_no": boe.get("boe_no", ""),
                            "total": f"{r['total']:.2f}", "vat": f"{r['vat']:.2f}",
                            "levies": f"{r['levies']:.2f}",
                            "bog_total": f"{r['bog_total']:.2f}" if r["bog_total"] else "",
                            "pct": f"{r['pct']*100:.2f}%" if r["pct"] else "",
                            "output": name, "problem": ""})
        except Stop as e:
            say(f"    SKIPPED: {e}\n")
            results.append({"file": scan.name, "status": "SKIPPED",
                            "invoice_no": "", "boe_no": (boe or {}).get("boe_no", ""),
                            "total": "", "vat": "", "levies": "", "bog_total": "",
                            "pct": "", "output": "",
                            "problem": str(e).split("\n")[0]})

    cols = ["file", "status", "invoice_no", "boe_no", "total", "vat", "levies",
            "bog_total", "pct", "output", "problem"]
    with (OUT / "summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(results)

    good = sum(1 for r in results if r["status"] == "OK")
    say("=" * 66)
    say(f"  done: {good} filled, {len(results)-good} skipped")
    say(f"  {OUT}")
    say("=" * 66)
    return 0 if good else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Stop as e:
        say(f"\nSTOPPED: {e}\n")
        raise SystemExit(2)
    except KeyboardInterrupt:
        raise SystemExit(130)
