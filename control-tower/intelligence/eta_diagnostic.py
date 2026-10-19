"""
ATLAS ETA/ATA diagnostic - ADVISORY ONLY.

For one saved carrier page it explains, event by event, what the page says
about the estimated and actual arrival, and recommends one of:

    ACCEPT_CANDIDATE    the deterministic validator has a candidate, it quotes
                        its page line, nothing is missing (identity,
                        destination) and no conflict was found. Uncertainty
                        notes (e.g. a landing far from DHL's own estimate) are
                        reported, not blocking.
    NEEDS_REVIEW        the validator asks for review, or the evidence does
                        (identity, destination, an estimate that had already
                        passed, conflicting events)
    NO_SUPPORTED_EVENT  the page carries no estimated or actual arrival

Boundaries (enforced by construction, tested):
  * arrival_events.py is the authority. DHL event types and candidates come
    from its classify/decide. For airlines the diagnostic reads the page
    itself (Qatar banners, Ethiopian legs) into arrival_events event types and
    runs arrival_events.decide on them; those readers are diagnostic-only and
    stricter than production (identity, every arrival banner). Its checks can
    only move a case towards NEEDS_REVIEW, never towards ACCEPT.
  * Nothing is invented: every candidate, destination and finding quotes the
    page line it came from; what the page does not say is reported missing.
  * It imports nothing that writes, opens a browser or contacts a carrier,
    and a recommendation is never an instruction to write.

    python -m intelligence.eta_diagnostic PAGE.txt [--reference REF] [--carrier DHL|QATAR|ETHIOPIAN]
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import arrival_events as E                                  # noqa: E402

ACCEPT, REVIEW, NONE = "ACCEPT_CANDIDATE", "NEEDS_REVIEW", "NO_SUPPORTED_EVENT"
ADVISORY = ("Advisory only: arrival_events.py decides; nothing here writes to the Hub, "
            "and the run's guards, holds, limits and read-back still apply.")


def _d(value):
    try:
        return datetime.strptime(value, "%d/%m/%Y")
    except (TypeError, ValueError):
        return None


def _line_of(text, *needles):
    """The first page line containing every needle - the quoted evidence."""
    for line in str(text or "").replace("\r", "").split("\n"):
        if all(n and n in line for n in needles):
            return line.strip()[:200]
    return None


def _cite(event, text):
    if not event:
        return None
    return {"date": event["date"], "time": event.get("time"), "tz": event.get("tz"),
            "code": event.get("code"), "description": event["description"],
            "location": event.get("location"), "type": event["type"],
            "estimated": event["estimated"],
            "evidence": _line_of(text, event["description"], event.get("time") or "")
            or _line_of(text, event["description"])}


def _base(reference, carrier, product, mode, destination):
    return {"reference": reference, "carrier": carrier, "product": product,
            "transport_mode": mode, "destination": destination, "events": [],
            "eta_candidate": None, "ata_candidate": None,
            "destination_match": {"value": None, "evidence": None},
            "conflicts": [], "missing": [], "uncertainty": [],
            "baseline": None, "recommendation": None, "advisory": ADVISORY}


# -- DHL --------------------------------------------------------------------

def _dhl(text, reference, today):
    events, product, destination = E.dhl_events(text, reference)
    seen, unique = set(), []
    for e in events:
        key = (e["date"], e["time"], e["description"], e["location"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    events = unique
    mode = None
    if _line_of(text, "Received at airline") or _line_of(text, "Airport"):
        mode = "air"
    out = _base(reference, "DHL", product, mode, destination)
    out["events"] = [_cite(e, text) for e in events if e["type"] != E.OTHER]
    d = E.decide(events, today=today)
    out["baseline"] = {"eta": d["eta"], "ata": d["ata"], "review": d["review"]}
    out["eta_candidate"] = _cite(d["eta_event"], text)
    out["ata_candidate"] = _cite(d["ata_event"], text)
    out["conflicts"] += d["review"]

    code = re.search(r"Tracking Code:\s*([A-Z0-9-]+)", text or "")
    if code and reference and code.group(1) != reference:
        out["conflicts"].append("the page is for {0}, not {1}".format(code.group(1), reference))
    if not code:
        out["missing"].append("the page names no tracking code (identity not shown)")
    if not destination:
        out["missing"].append("the page names no destination")

    ata = d["ata_event"]
    if ata:
        if ata.get("code") == "EXP:ARRIVED_DESTINATION_COUNTRY":
            out["destination_match"] = {"value": "explicit-country", "evidence": _line_of(
                text, ata["description"])}
        elif ata.get("code", "").startswith("DGF:"):
            # The carrier itself files this as the arrival (not a transit
            # point). Supporting evidence: its own "(Destination)" estimate.
            est = [e for e in events if e["type"] == E.EST_ARRIVAL]
            near = [e for e in est if _d(e["date"]) and _d(ata["date"]) and
                    abs((_d(e["date"]) - _d(ata["date"])).days) <= 2]
            out["destination_match"] = {
                "value": "carrier-label",
                "evidence": _line_of(text, ata["description"], ata.get("time") or "")}
            if est and not near:
                out["uncertainty"].append(
                    "the landing ({0}) is more than 2 days from DHL's own estimated arrival at "
                    "the destination ({1})".format(ata["date"], ", ".join(e["date"] for e in est)))
            if not est:
                out["uncertainty"].append("no '(Destination)' estimate on the page to support "
                                          "that the arrival airport is the destination")
    eta = d["eta_event"]
    if eta:
        last = re.search(r"Last Update:\s*[A-Za-z]+,\s*([A-Z][a-z]+ \d{1,2}, \d{4})", text or "")
        last_day = E.parse_date(last.group(1)) if last else None
        if last_day and _d(last_day) and _d(eta["date"]) and _d(last_day) > _d(eta["date"]):
            out["conflicts"].append(
                "the estimated arrival {0} had already passed when DHL last updated the page "
                "({1}) and no arrival is recorded: the estimate may be stale".format(
                    eta["date"], last_day))
    later = [e for e in events if e["type"] in (E.POST_ARRIVAL, E.ACT_DELIVERY)]
    if later and not ata:
        out["missing"].append("steps after arrival are shown ({0}) but no arrival event"
                              .format(later[0]["description"]))
    if not events:
        out["missing"].append("no DHL event rows were recognised on the page")
    return out


# -- airlines (evidence readers for the diagnostic only) ---------------------

def _qatar(text, reference, today):
    route = re.search(r"\(\s*([A-Z]{3})\s*-\s*([A-Z]{3})\s*\)", text or "")
    dest = route.group(2) if route else None
    out = _base(reference, "QATAR", "AIR", "air", dest)
    arrivals = re.findall(r"\bArrived\s*\(\s*([A-Z]{3})\s*\)\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", text or "")
    estimate = re.search(r"Estimated\s+Arrival\s*\(\s*([A-Z]{3})\s*\)\s*(\d{1,2}-[A-Za-z]{3}-\d{4})",
                         text or "")
    events = []
    for station, when in arrivals:                       # every banner, not the first
        kind = E.ACT_ARRIVAL if dest and station == dest else E.TRANSIT
        events.append(E.event(kind, E.parse_date(when), "Arrived ({0})".format(station),
                              "Qatar page", reference, location=station, code="QR:ARRIVED"))
    awb = re.search(r"\b(\d{3})\s*-\s*(\d{8})\s*\(", text or "")
    if reference and awb and re.sub(r"\D", "", reference) != awb.group(1) + awb.group(2):
        out["conflicts"].append("the page is for {0}-{1}, not {2}".format(
            awb.group(1), awb.group(2), reference))
    if not awb:
        out["missing"].append("the page names no air waybill (identity not shown)")
    if estimate:
        kind = E.EST_ARRIVAL if dest and estimate.group(1) == dest else E.OTHER
        events.append(E.event(kind, E.parse_date(estimate.group(2)), "Estimated Arrival ({0})"
                              .format(estimate.group(1)), "Qatar page", reference,
                              location=estimate.group(1), code="QR:EST_ARRIVAL"))
    if not route:
        out["missing"].append("the page shows no route, so the destination is unknown")
    d = E.decide(events, today=today)
    out["events"] = [dict(_cite(e, text), evidence=_line_of(text, e["description"].split(" (")[0],
                                                            e["location"])) for e in events]
    out["baseline"] = {"eta": d["eta"], "ata": d["ata"], "review": d["review"]}
    out["conflicts"] += d["review"]
    for key, ev in (("eta_candidate", d["eta_event"]), ("ata_candidate", d["ata_event"])):
        if ev:
            out[key] = dict(_cite(ev, text), evidence=_line_of(
                text, ev["description"].split(" (")[0], ev["location"]))
    if d["ata_event"]:
        out["destination_match"] = {"value": "explicit-station", "evidence": " / ".join(
            x for x in (_line_of(text, route.group(0)) if route else None,
                        out["ata_candidate"]["evidence"]) if x)}
    return out


def _generic_air(text, reference, carrier, today):
    """Airlines read by label (e.g. Ethiopian): legs and their status only."""
    legs = re.findall(r"([A-Z]{3})\s*→\s*([A-Z]{3})\s*\n\s*(\S+)\s*•\s*(\d{4}-\d{2}-\d{2})\s*\n\s*"
                      r"([A-Z]+)", text or "")
    # The destination is the page's own "Destination" field; failing that, the
    # last leg's arrival, then the route list printed before the flight
    # schedule. Never just the last three-letter line on the page: the
    # shipment history below has status codes such as "DEP" (17 Oct, 071-
    # 62757085 to ACC was read as going to "DEP" and its arrival vetoed).
    labelled = re.search(r"\bDestination\s*\n\s*([A-Z]{3})\s*$", text or "", re.M)
    head = re.split(r"\bFlight Schedule\b|\bShipment History\b", text or "")[0]
    route_codes = re.findall(r"^([A-Z]{3})$", head, re.M)
    dest = (labelled.group(1) if labelled else legs[-1][1] if legs else
            route_codes[-1] if route_codes else None)
    out = _base(reference, carrier, "AIR", "air", dest)
    for origin, to, flight, when, status in legs:
        final = to == dest
        out["events"].append({"date": E.parse_date(when), "time": None, "tz": None,
                              "code": "LEG:" + status, "description": "{0} {1}-{2} {3}".format(
                                  flight, origin, to, status),
                              "location": to, "type": E.OTHER, "estimated": None,
                              "evidence": _line_of(text, flight)})
        if final and status == "DELIVERED":
            out["uncertainty"].append("the last leg to {0} is marked DELIVERED on {1}: a flight "
                                      "status, not a labelled arrival time".format(to, when))
        if final and status != "DELIVERED":
            out["missing"].append("the last leg {0}-{1} is only {2} (flight date {3}); no "
                                  "estimated or actual arrival time at {1} is on the page"
                                  .format(origin, to, status, when))
        if not final and status == "DELIVERED":
            out["conflicts"].append("leg {0}-{1} is DELIVERED at a transit station ({1}), not "
                                    "at the destination {2}: not an arrival".format(origin, to, dest))
    out["baseline"] = {"eta": None, "ata": None, "review": ["the label reader cannot place this "
                                                            "carrier's arrival wording"]}
    return out


# -- the recommendation ------------------------------------------------------

def recommend(diag):
    """Can only add caution to the validator's decision, never remove it."""
    base = diag.get("baseline") or {}
    if base.get("review") or diag["conflicts"]:
        return REVIEW
    if not base.get("eta") and not base.get("ata"):
        return REVIEW if diag["missing"] else NONE
    if diag["missing"]:
        return REVIEW            # e.g. identity or destination not shown
    for key in ("eta_candidate", "ata_candidate"):
        if diag.get(key) and not diag[key].get("evidence"):
            return REVIEW        # a candidate that cannot quote its page line
    return ACCEPT


def diagnose(text, reference=None, carrier=None, today=None):
    """One structured, evidence-quoting diagnostic for one saved page."""
    carrier = (carrier or "").upper() or ("DHL" if E.dhl_product(text) else None)
    if carrier == "DHL":
        diag = _dhl(text, reference, today)
    elif carrier == "QATAR":
        diag = _qatar(text, reference, today)
    elif carrier:
        diag = _generic_air(text, reference, carrier, today)
    else:
        diag = _base(reference, None, None, None, None)
        diag["missing"].append("the carrier could not be recognised from the page")
        diag["baseline"] = {"eta": None, "ata": None, "review": []}
    diag["recommendation"] = recommend(diag)
    return diag


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="python -m intelligence.eta_diagnostic")
    p.add_argument("page")
    p.add_argument("--reference")
    p.add_argument("--carrier")
    a = p.parse_args(argv)
    text = Path(a.page).read_text(encoding="utf-8", errors="replace")
    print(json.dumps(diagnose(text, a.reference, a.carrier), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
