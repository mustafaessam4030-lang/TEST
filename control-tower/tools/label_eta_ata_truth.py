"""
Independent ground-truth labeller for the ETA/ATA evaluation.

Deliberately NOT built on arrival_events.py or ATLAS: it reads the raw lines
of each sanitized page with its own small rules, written from the definitions
the operations owner confirmed (October 2026):
  ATA = the date the transport arrived at the DESTINATION airport/port
  ETA = the carrier's estimated arrival at the destination
A case the page cannot settle is labelled UNRESOLVED, never guessed.

LIMITATION, stated plainly: it applies the SAME operations-confirmed
definition as arrival_events.py (independent code, not an independent rule),
so agreement with the baseline on auto-labelled cases measures two
implementations of one rule, not correctness. Only the hand-checked labels
("reviewed": true) are independent checks; the evaluation reports them
separately.
The output (fixtures/eta_ata_truth.json) is reviewed by hand; cases marked
"reviewed": true were checked line by line against the page.

    python tools/label_eta_ata_truth.py   (rewrites the auto-labels only)
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"
MONTH_DATE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})$")
ROW = re.compile(r"^\d{1,2}:\d{2} [AP]M \(UTC[^)]*\)\t([^\t]+)\t?(.*)$")
AIRPORT = re.compile(r"airport|port\b|terminal", re.I)


def day(text):
    return datetime.strptime(text, "%B %d, %Y").strftime("%d/%m/%Y")


def dhl_rows(text):
    rows, current, lines = [], None, text.replace("\r", "").split("\n")
    for i, raw in enumerate(lines):
        line = raw.strip()
        m = MONTH_DATE.match(line)
        if m:
            current = day(m.group(1))
            continue
        r = ROW.match(line)
        if r and current:
            place = r.group(2).strip()
            if not place:
                nxt = [l.strip() for l in lines[i + 1:i + 4] if l.startswith("\t") and l.strip()]
                place = nxt[0] if nxt else ""
            rows.append((current, r.group(1).strip(), place))
    return rows


def label_dhl(text):
    express = "handled by: DHL Express" in text
    dest = (re.search(r"^Destination:\s*(.+)$", text, re.M) or [None, ""])[1].strip()
    rows = dhl_rows(text)
    if express:
        country = dest.split(" - ")[-1].strip().upper()
        arr = sorted({d for d, s, p in rows if s.startswith("Arrived at DHL")
                      and s.upper().endswith("- " + country)},
                     key=lambda d: datetime.strptime(d, "%d/%m/%Y"))
        eta = None                                    # Express pages give no estimated arrival
        ata = arr[0] if arr else None
        dest_match = "explicit-country" if ata else None
        status = "RESOLVED" if ata else ("UNRESOLVED" if any("Delivered" in s for _, s, _ in rows)
                                          else "RESOLVED")
        return {"product": "EXPRESS", "destination": dest, "eta": eta, "ata": ata,
                "arrival_at_destination": dest_match, "status": status}
    landing = {d for d, s, p in rows if s == "Carrier: Arrived" or
               (s == "Arrived Final Destination" and AIRPORT.search(p))}
    est = {d for d, s, p in rows if s == "Estimated Carrier Arrival (Destination)"}
    after = any(s in ("Shipment Delivered", "Freight Recovered at") or
                (s == "Arrived Final Destination" and not AIRPORT.search(p)) for _, s, p in rows)
    if len(landing) > 1:
        return {"product": "DGF", "destination": dest, "eta": None, "ata": None,
                "arrival_at_destination": None, "status": "UNRESOLVED",
                "why": "several landing dates"}
    if landing:
        return {"product": "DGF", "destination": dest, "eta": None, "ata": landing.pop(),
                "arrival_at_destination": "carrier-label", "status": "RESOLVED"}
    if after:
        return {"product": "DGF", "destination": dest, "eta": None, "ata": None,
                "arrival_at_destination": None, "status": "UNRESOLVED",
                "why": "past arrival on the page, but no landing event"}
    return {"product": "DGF", "destination": dest, "eta": est.pop() if len(est) == 1 else None,
            "ata": None, "arrival_at_destination": None,
            "status": "RESOLVED" if len(est) <= 1 else "UNRESOLVED"}


def main():
    out_path = FIX / "eta_ata_truth.json"
    old = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    truth = {}
    for f in sorted((FIX / "dhl_oct12").glob("OCT12-*.txt")):
        t = label_dhl(f.read_text(encoding="utf-8"))
        t["carrier"] = "DHL"
        t["fixture"] = "dhl_oct12/" + f.name
        if old.get(f.stem, {}).get("reviewed"):         # a hand-checked label is kept as is
            t = old[f.stem]
        truth[f.stem] = t
    for key, value in old.items():                     # hand-written airline labels
        if key not in truth:
            truth[key] = value
    out_path.write_text(json.dumps(truth, indent=1, sort_keys=True), encoding="utf-8")
    print(len(truth), "labels;", sum(1 for t in truth.values() if t["status"] == "UNRESOLVED"),
          "unresolved")


if __name__ == "__main__":
    sys.exit(main())
