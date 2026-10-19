"""
ATLAS ETA/ATA diagnostic: advisory only, evidence-grounded, never more
permissive than arrival_events, and evaluated against independent labels.
Sanitized saved pages only; no carrier, no Hub, no network.
"""
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import arrival_events as E                                    # noqa: E402
from intelligence import eta_diagnostic as D                   # noqa: E402

FIX = HERE / "fixtures"
OCT12 = date(2026, 10, 12)
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


def month_name(day):
    d = date(int(day[6:]), int(day[3:5]), int(day[:2]))
    return "{0} {1}, {2}".format(d.strftime("%B"), d.day, d.year)


print("1. Every DHL page: nothing invented, never more permissive than the validator")
invented, looser, no_evidence = [], [], []
for f in sorted((FIX / "dhl_oct12").glob("OCT12-*.txt")):
    text = f.read_text(encoding="utf-8")
    diag = D.diagnose(text, f.stem, "DHL", today=OCT12)
    events, _p, _d = E.dhl_events(text, f.stem)
    base = E.decide(events, today=OCT12)
    for key in ("eta_candidate", "ata_candidate"):
        c = diag[key]
        if c and (month_name(c["date"]) not in text or not c["evidence"]
                  or c["description"] not in c["evidence"]):
            invented.append((f.stem, key, c))
    if diag["destination"] and ("Destination: " + diag["destination"]) not in text:
        invented.append((f.stem, "destination", diag["destination"]))
    if base["review"] and diag["recommendation"] != D.REVIEW:
        looser.append(f.stem)
    if not base["eta"] and not base["ata"] and diag["recommendation"] == D.ACCEPT:
        looser.append(f.stem)
    if diag["recommendation"] == D.ACCEPT:
        cand = diag["ata_candidate"] or diag["eta_candidate"]
        if (diag["ata_candidate"] or {}).get("date") != base["ata"] or \
                (diag["eta_candidate"] or {}).get("date") != base["eta"]:
            looser.append((f.stem, "candidate differs from validator"))
        if not cand.get("evidence"):
            no_evidence.append(f.stem)
check("No candidate date, event or destination that is not on the page (44 pages)",
      not invented, invented[:3])
check("Never ACCEPT where the validator reviews or has nothing; candidates = validator's",
      not looser, looser[:5])
check("Every accepted candidate quotes its page line", not no_evidence, no_evidence)

print("2. Known DHL failure patterns")
d = D.diagnose((FIX / "dhl_oct12" / "OCT12-12.txt").read_text(encoding="utf-8"), "OCT12-12", "DHL",
               today=OCT12)
check("'Estimated Delivery 06/11' (the date written as ATA on 12 Oct) is not the ETA or ATA",
      d["ata_candidate"]["date"] == "07/10/2026" and d["eta_candidate"] is None, d["ata_candidate"])
check("Landing at the airport: destination supported by the carrier's label",
      d["destination_match"]["value"] == "carrier-label" and "Kotoka" in d["destination_match"]["evidence"])
d = D.diagnose((FIX / "dhl_oct12" / "OCT12-33.txt").read_text(encoding="utf-8"), "OCT12-33", "DHL",
               today=OCT12)
check("Delivered but no landing event -> NEEDS_REVIEW, nothing inferred",
      d["recommendation"] == D.REVIEW and not d["ata_candidate"] and d["missing"], d["missing"])
d = D.diagnose((FIX / "dhl_oct12" / "OCT12-01.txt").read_text(encoding="utf-8"), "OCT12-01", "DHL",
               today=OCT12)
check("Not arrived: ETA candidate from 'Estimated Carrier Arrival (Destination)', no ATA",
      d["recommendation"] == D.ACCEPT and d["eta_candidate"]["description"].startswith(
          "Estimated Carrier Arrival") and not d["ata_candidate"], d)
stale = (FIX / "dhl_oct12" / "OCT12-01.txt").read_text(encoding="utf-8").replace(
    "Tracking Code: OCT12-01", "Tracking Code: OCT12-01\nLast Update: Friday, October 16, 2026 at 9:00 AM")
d = D.diagnose(stale, "OCT12-01", "DHL", today=date(2026, 10, 17))
check("An estimate that had already passed at DHL's last update -> NEEDS_REVIEW (stale)",
      d["recommendation"] == D.REVIEW and any("stale" in c for c in d["conflicts"]), d["conflicts"])
d = D.diagnose((FIX / "dhl_oct12" / "OCT12-01.txt").read_text(encoding="utf-8"), "K999999", "DHL",
               today=OCT12)
check("A page for another shipment -> NEEDS_REVIEW (identity)",
      d["recommendation"] == D.REVIEW and any("not K999999" in c for c in d["conflicts"]))
city = (FIX / "dhl_oct12" / "OCT12-12.txt").read_text(encoding="utf-8").replace(
    "Arrived Final Destination\tKotoka International Airport", "Arrived Final Destination\tAccra")
d = D.diagnose(city, "OCT12-12", "DHL", today=OCT12)
check("'Arrived Final Destination' at a CITY (after customs) is not the ATA",
      not d["ata_candidate"] or d["ata_candidate"]["description"] != "Arrived Final Destination", d)
express = (FIX / "dhl_oct12" / "OCT12-21.txt").read_text(encoding="utf-8")
d = D.diagnose(express, "OCT12-21", "DHL", today=OCT12)
check("Express: arrival in the destination COUNTRY, not the transit hub (Dubai)",
      d["ata_candidate"]["date"] == "03/10/2026" and d["destination_match"]["value"] == "explicit-country"
      and "ETHIOPIA" in d["ata_candidate"]["description"], d["ata_candidate"])

print("3. Known airline failure patterns")
eth = (FIX / "ethiopian_transit_delivered.txt").read_text(encoding="utf-8")
d = D.diagnose(eth, "071-00000001", "ETHIOPIAN", today=OCT12)
check("Ethiopian: transit legs 'DELIVERED' are not an arrival -> NEEDS_REVIEW",
      d["recommendation"] == D.REVIEW and not d["ata_candidate"]
      and sum("transit station" in c for c in d["conflicts"]) == 2, d["conflicts"])
check("...and it says the last leg to DAR is only scheduled",
      any("only SCHEDULED" in m for m in d["missing"]), d["missing"])
q = (FIX / "qatar_arrived_destination.txt").read_text(encoding="utf-8")
d = D.diagnose(q, None, "QATAR", today=OCT12)
check("Qatar: 'Arrived ( DAR)' on a BRU-DAR route -> ATA 26/09, destination explicit",
      d["recommendation"] == D.ACCEPT and d["ata_candidate"]["date"] == "26/09/2026"
      and d["destination_match"]["value"] == "explicit-station", d)
d = D.diagnose(q.replace("Arrived ( DAR)", "Arrived ( DOH)"), None, "QATAR", today=OCT12)
check("Qatar: an arrival at a TRANSIT station is not the ATA",
      not d["ata_candidate"] and d["recommendation"] != D.ACCEPT, d)
d = D.diagnose(q, "157-99999999", "QATAR", today=OCT12)
check("Qatar: a page for another air waybill -> NEEDS_REVIEW (identity)",
      d["recommendation"] == D.REVIEW and any("not 157-99999999" in c for c in d["conflicts"]),
      d["conflicts"])
two = q.replace("Arrived ( DAR) 26-Sep-2026 13:52",
                "Arrived ( DOH) 25-Sep-2026 02:10\nArrived ( DAR) 26-Sep-2026 13:52")
d = D.diagnose(two, "157-00000001", "QATAR", today=OCT12)
check("Qatar: a transit arrival printed first does not hide the destination arrival",
      d["ata_candidate"] and d["ata_candidate"]["date"] == "26/09/2026", d["ata_candidate"])
noid = (FIX / "dhl_oct12" / "OCT12-12.txt").read_text(encoding="utf-8").replace(
    "Tracking Code: OCT12-12\n", "")
d = D.diagnose(noid, "OCT12-12", "DHL", today=OCT12)
check("A candidate on a page that does not show its identity -> NEEDS_REVIEW",
      d["ata_candidate"] and d["recommendation"] == D.REVIEW, d["missing"])
d = D.diagnose((FIX / "qatar_in_transit.txt").read_text(encoding="utf-8"), None, "QATAR", today=OCT12)
check("Qatar in transit: ETA 14/10 to JRO, no ATA",
      d["eta_candidate"]["date"] == "14/10/2026" and not d["ata_candidate"], d)
d = D.diagnose("Nothing recognisable here.\n" + "x" * 50, None, None, today=OCT12)
check("An unrecognised page: no candidate, says what is missing",
      not d["eta_candidate"] and not d["ata_candidate"] and d["recommendation"] != D.ACCEPT
      and d["missing"], d)

print("4. Advisory only: no write path, no browser, no network")
src = (HERE / "intelligence" / "eta_diagnostic.py").read_text(encoding="utf-8")
imports = set(re.findall(r"^\s*(?:import|from)\s+([\w.]+)", src, re.M))
check("Imports nothing that writes or browses (no update_eta, playwright, urllib, requests, "
      "socket, http)", not imports & {"update_eta", "playwright", "urllib", "requests", "socket",
                                     "http", "dashboard"}, imports)
out = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, '.'); "
                      "import intelligence.eta_diagnostic as d; "
                      "print(sorted(m for m in sys.modules if m.split('.')[0] in "
                      "('update_eta','playwright','urllib3','requests','dashboard')))"],
                     cwd=str(HERE), capture_output=True, text=True)
check("...and loading it pulls none of them in", out.stdout.strip() == "[]", out.stdout + out.stderr)
check("Every diagnostic carries the advisory notice",
      "Advisory only" in D.diagnose(q, None, "QATAR")["advisory"])

print("5. The offline evaluation, against independent labels")
sys.path.insert(0, str(HERE / "tools"))
import evaluate_eta_diagnostic as EV                          # noqa: E402
import io, contextlib                                         # noqa: E402
with contextlib.redirect_stdout(io.StringIO()):
    rep = EV.main([])
truth = json.loads((FIX / "eta_ata_truth.json").read_text(encoding="utf-8"))
check("Truth is not ATLAS's output: the labeller imports neither arrival_events nor ATLAS",
      not set(re.findall(r"^\s*(?:import|from)\s+([\w.]+)",
                         (HERE / "tools" / "label_eta_ata_truth.py").read_text(), re.M))
      & {"arrival_events", "intelligence", "intelligence.eta_diagnostic", "update_eta"})
check("47 labelled cases, 3 unresolved by the page", len(truth) == 47 and
      sum(t["status"] == "UNRESOLVED" for t in truth.values()) == 3)
check("The 12 Oct run: 40 of 42 ATAs wrong, both unresolved cases answered",
      rep["original"]["ata"].get("wrong") == 40 and
      rep["original"]["abstention_on_unresolved"].get("answered") == 2, rep["original"]["ata"])
check("ATLAS: no wrong or missed ETA/ATA, abstains on all 3 unresolved",
      not rep["atlas"]["eta"].get("wrong") and not rep["atlas"]["ata"].get("wrong")
      and not rep["atlas"]["ata"].get("missed")
      and rep["atlas"]["abstention_on_unresolved"].get("abstained") == 3, rep["atlas"])
check("Scores are per class: 33 true ATAs and 11 true ETAs found, None==None counted apart",
      rep["atlas"]["ata"].get("tp") == 33 and rep["atlas"]["eta"].get("tp") == 11
      and rep["atlas"]["ata"].get("true_none") == 11, rep["atlas"])
check("Destination evidence is split: carrier-label is not counted as explicit",
      rep["atlas"]["destination"].get("explicit") == 3
      and rep["atlas"]["destination"].get("carrier-label only") == 30, rep["atlas"]["destination"])
check("Hand-checked subset reported separately",
      rep["hand_checked"]["atlas"]["ata"].get("tp") == 6, rep.get("hand_checked"))
# Non-DHL readers are back to their 12 Oct behaviour (operator, 14 Oct), with
# ATLAS's transit veto on Qatar and Ethiopian: the first leg's DELIVERED at a
# transit airport is no longer taken as the arrival, so production and ATLAS
# make the same decision on every case.
check("ATLAS and production (12 Oct readers + ATLAS's transit veto) make the same decisions",
      rep["differences"] == [], rep["differences"])

# 17 Oct, 071-62757085 (LHR -> ADD -> ACC): the shipment history lists status
# codes such as "DEP" on lines of their own; the destination was read as "DEP"
# and the real arrival at ACC was vetoed as a "transit airport".
ET_ACC = """AWB No : 62757085

Origin

LHR

Destination

ACC

LHR
ADD
ACC
Flight Schedule
LHR \u2192 ADD
ET701 \u2022 2026-10-09
DELIVERED
ADD \u2192 ACC
ET921 \u2022 2026-10-13
DELIVERED
Shipment History
DEP
1/10 Pcs | 0.39 m 3 | 66.1 Kg | from ADD to ACC
14-Oct-26 13:00:00
Departed on Flight
"""
d = D.diagnose(ET_ACC, None, "ETHIOPIAN")
check("Ethiopian: the destination is the page's Destination field (ACC), not a history code",
      d.get("destination") == "ACC", d.get("destination"))
check("Ethiopian: the last leg to ACC is not called a transit stop",
      not any("transit station (ACC)" in c for c in d.get("conflicts") or []), d.get("conflicts"))
import update_eta as U                                          # noqa: E402
kept = U.atlas_transit_veto(ET_ACC, "ETHIOPIAN", {"provider": "ETHIOPIAN", "ata": "13/10/2026"})
check("ATLAS's transit check lets the arrival at the destination through",
      kept.get("ata") == "13/10/2026", kept)
ET_DAR = ET_ACC.replace("Destination\n\nACC", "Destination\n\nDAR").replace(
    "ADD \u2192 ACC\nET921 \u2022 2026-10-13", "ADD \u2192 DAR\nET3805 \u2022 2026-10-16")
held = U.atlas_transit_veto(ET_DAR, "ETHIOPIAN", {"provider": "ETHIOPIAN", "ata": "09/10/2026"})
check("...and still stops a first-leg date on a route to DAR", held.get("ata") is None, held)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
