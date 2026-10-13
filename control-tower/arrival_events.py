"""
Arrival events: the steps between a carrier page and a Hub write, each one
testable on its own.

    1 extract    a carrier adapter reads its page into EVENTS (dhl_events()
                 for DHL; other adapters report labelled dates, which
                 events_from_result() turns into events)
    2 classify   every event gets ONE type, from the carrier's own wording:
                     EST_ARRIVAL   estimated arrival at destination  -> ETA
                     ACT_ARRIVAL   actual arrival at destination     -> ATA
                     EST_DELIVERY  estimated delivery   (never the ETA)
                     ACT_DELIVERY  actual delivery      (never the ATA)
                     TRANSIT       arrival at a transit point
                     POST_ARRIVAL  a step that only happens after arrival
                     OTHER         anything else (kept as evidence only)
    3 validate   parse_date(): only unambiguous dates; anything else is None
    4 decide     decide(): the ETA/ATA to write, or the reasons a person must
                 review it. Never an ATA from an estimate, an expired ETA or a
                 delivery; conflicting actual arrivals are not chosen between.
    5 guard      check_before_write(): the shared, deterministic check every
                 carrier's result passes before the Hub (update_eta.py)
    6 write and read-back stay in update_eta.update_one_view (unchanged).

Every event keeps its code, description, date, time, timezone, location,
source, the shipment reference and whether it is estimated or actual.

DHL mappings were taken from the DHL pages the automation saved in October
2026 (59 DHL Global Forwarding, 5 DHL Express), not from documentation alone:
  * Forwarding: the ATA is the flight landing at the destination airport -
    confirmed by the operations owner. It appears as "Carrier: Arrived", or
    as "Arrived Final Destination" AT AN AIRPORT/PORT. "Arrived Final
    Destination" at a city is DHL's station after import clearance (16 days
    after landing on one shipment): POST_ARRIVAL, never the ATA. The ETA is
    "Estimated Carrier Arrival (Destination)"; "Estimated Delivery" is not.
  * Express: "Arrived at DHL Sort/Delivery Facility <place> - <COUNTRY>" is
    the arrival only when COUNTRY is the page's destination country. Express
    pages carry no estimated ARRIVAL, so they give no ETA.
Other DHL products or transport modes (ocean, road, eCommerce) are not
covered by these tables: their events classify as OTHER and go to review.
"""

import re
from datetime import date, datetime

EST_ARRIVAL, ACT_ARRIVAL = "EST_ARRIVAL", "ACT_ARRIVAL"
EST_DELIVERY, ACT_DELIVERY = "EST_DELIVERY", "ACT_DELIVERY"
TRANSIT, POST_ARRIVAL, OTHER = "TRANSIT", "POST_ARRIVAL", "OTHER"
ESTIMATED_TYPES = (EST_ARRIVAL, EST_DELIVERY)


# -- 3. dates --------------------------------------------------------------

_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%A, %B %d, %Y", "%d %B %Y", "%d %b %Y",
            "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%B-%Y")


def parse_date(text):
    """dd/mm/yyyy, or None when the text is not ONE unambiguous full date.
    A two-digit year, a missing year or a mm/dd-looking value is refused."""
    value = re.sub(r"\s+", " ", str(text or "")).strip().rstrip(".")
    value = re.split(r"\s+(?:at|-)\s+", value)[0].strip()       # "... at 8:28 PM", "- By End of Day"
    for fmt in _FORMATS:
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if fmt == "%d/%m/%Y" and not re.match(r"^\d{2}/\d{2}/\d{4}$", value):
            return None
        return parsed.strftime("%d/%m/%Y")
    return None


def _day(value):
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None


def event(kind, day, description, source, reference, time=None, tz=None, code=None,
          location=None):
    return {"type": kind, "date": day, "time": time, "tz": tz, "code": code,
            "description": description, "location": location, "source": source,
            "reference": reference, "estimated": kind in ESTIMATED_TYPES}


# -- 2. classification: DHL (from the saved pages) -------------------------

DGF_RULES = (                               # exact DHL Global Forwarding wording
    (r"^Estimated Carrier Arrival \(Destination\)$", EST_ARRIVAL, "DGF:EST_CARRIER_ARRIVAL"),
    (r"^Carrier: Arrived$", ACT_ARRIVAL, "DGF:CARRIER_ARRIVED"),
    (r"^Carrier: Arrived \(.*Transit Point\)$", TRANSIT, "DGF:CARRIER_ARRIVED_TRANSIT"),
    (r"^Estimated Delivery$", EST_DELIVERY, "DGF:EST_DELIVERY"),
    (r"^(Shipment Delivered|Delivered)$", ACT_DELIVERY, "DGF:DELIVERED"),
    (r"^Partial delivery", ACT_DELIVERY, "DGF:PARTIAL_DELIVERY"),
    (r"^Freight Recovered at", POST_ARRIVAL, "DGF:FREIGHT_RECOVERED"),
    (r"^Handover Freight to Forwarder$", POST_ARRIVAL, "DGF:HANDOVER_TO_FORWARDER"),
)
EXPRESS_RULES = (
    (r"^Estimated Delivery$", EST_DELIVERY, "EXP:EST_DELIVERY"),
    (r"^Delivered\b", ACT_DELIVERY, "EXP:DELIVERED"),
    (r"^Partial delivery", ACT_DELIVERY, "EXP:PARTIAL_DELIVERY"),
    (r"^Clearance processing complete at", POST_ARRIVAL, "EXP:CLEARANCE_COMPLETE"),
    (r"out with courier for delivery|with delivery courier", POST_ARRIVAL, "EXP:WITH_COURIER"),
)
EXPRESS_ARRIVAL = re.compile(r"^Arrived at DHL (?:Sort|Delivery) Facility (.+?) - ([A-Z .'&-]+)$")


def _country(place):
    """'Accra, Ghana' -> GHANA; 'LAGOS - NIGERIA' -> NIGERIA."""
    parts = re.split(r",|\s-\s", str(place or ""))
    return parts[-1].strip().upper() if parts and parts[-1].strip() else None


# "Arrived Final Destination" means two things on the saved pages: at an
# airport/port (37 of 42) it is the landing - 13 times with exactly the same
# timestamp as "Carrier: Arrived", 24 times the only arrival event; at a city
# (5 of 42, "Accra") it is DHL's station after import clearance.
ARRIVAL_PLACE = re.compile(r"airport|\bport\b|harbou?r|terminal|seaport", re.I)


def classify_dhl(description, product, destination=None, location=None):
    """(type, code) for one DHL event, by product. Unknown wording -> OTHER.
    "Import Customs Cleared" is OTHER: it came BEFORE the landing on 1 of 19
    saved shipments (pre-arrival clearance), so it proves nothing about arrival."""
    text = re.sub(r"\s+", " ", str(description or "")).strip()
    if product == "DGF" and re.match(r"^Arrived Final Destination$", text, re.I):
        if ARRIVAL_PLACE.search(str(location or "")):
            return ACT_ARRIVAL, "DGF:ARRIVED_FINAL_DESTINATION_AT_PORT"
        return POST_ARRIVAL, "DGF:ARRIVED_FINAL_DESTINATION_STATION"
    rules = DGF_RULES if product == "DGF" else EXPRESS_RULES
    for pattern, kind, code in rules:
        if re.search(pattern, text, re.I):
            return kind, code
    if product == "EXPRESS":
        m = EXPRESS_ARRIVAL.match(text)
        if m:
            here, there = m.group(2).strip().upper(), _country(destination)
            if there and here == there:
                return ACT_ARRIVAL, "EXP:ARRIVED_DESTINATION_COUNTRY"
            return TRANSIT, "EXP:ARRIVED_FACILITY"
    return OTHER, None


# -- 1. extraction: DHL page text -> events --------------------------------

_TIME_ROW = re.compile(r"^(\d{1,2}:\d{2}\s*[AP]M)\s*\((UTC[^)]*)\)\s*\t\s*(.+?)(?:\t\s*(.*))?$")


def dhl_product(text):
    if re.search(r"handled by:\s*DHL Express", text or "", re.I):
        return "EXPRESS"
    if re.search(r"Estimated Carrier (?:Arrival|Departure)|Carrier: (?:Arrived|Departed)|"
                 r"Shipment Entered into System", text or "", re.I):
        return "DGF"
    return None


def dhl_destination(text):
    m = re.search(r"^Destination:\s*(.+)$", text or "", re.M)
    return m.group(1).strip() if m else None


def dhl_events(text, reference, source="DHL page"):
    """Every dated Event Log row: a date line, then 'time (UTC..)<TAB>status<TAB>place'."""
    text = str(text or "").replace("\r", "")
    product = dhl_product(text)
    destination = dhl_destination(text)
    events, current = [], None
    lines = text.split("\n")
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        day = parse_date(line) if re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$", line) else None
        if day:
            current = day
            continue
        m = _TIME_ROW.match(line)
        if m and current:
            place = (m.group(4) or "").strip()
            if not place:                              # Express: place on a later line
                for nxt in lines[i + 1:i + 4]:
                    if nxt.startswith("\t") and nxt.strip():
                        place = nxt.strip()
                        break
            kind, code = classify_dhl(m.group(3), product, destination, place)
            events.append(event(kind, current, m.group(3).strip(), source + " " + (product or "?"),
                                reference, time=m.group(1), tz=m.group(2), code=code,
                                location=place or None))
    return events, product, destination


# -- other adapters: labelled dates -> events ------------------------------

_ACTUAL_ARRIVAL_LABEL = re.compile(
    r"actual\s+(?:time\s+of\s+)?arrival|\bATA\b|vessel\s+arrived|arrival\s*\(\s*actual|"
    r"discharg|\bRCF\b|received\s+from\s+flight|\barrived\b", re.I)
_ESTIMATE_LABEL = re.compile(r"estimat|expect|schedul|planned|forecast|predict|\bETA\b|due\s+date",
                             re.I)
_DELIVERY_LABEL = re.compile(r"deliver|\bDLV\b|signed\s+for", re.I)


def classify_label(label, field):
    """The type a labelled date really has, whatever field the adapter put it in."""
    text = str(label or "")
    if _DELIVERY_LABEL.search(text):
        return EST_DELIVERY if _ESTIMATE_LABEL.search(text) else ACT_DELIVERY
    if _ESTIMATE_LABEL.search(text):
        return EST_ARRIVAL
    if field == "ata" and _ACTUAL_ARRIVAL_LABEL.search(text):
        return ACT_ARRIVAL
    if field == "eta" and not text:
        return EST_ARRIVAL                 # an adapter's unlabelled ETA is still an estimate
    return OTHER


def events_from_result(result, reference):
    """Events for an adapter result that has no event list of its own."""
    out = []
    for field in ("eta", "ata"):
        value = (result or {}).get(field)
        if not value:
            continue
        label = (result or {}).get(field + "_source")
        kind = classify_label(label, field)
        out.append(event(kind, value if _day(value) else None, label or field.upper(),
                         (result or {}).get("provider") or "carrier", reference,
                         code="{0}:{1}".format((result or {}).get("provider"), field.upper())))
    return out


# Arrival events that form a sequence (sort facility, then delivery facility,
# in the destination country): the FIRST is the arrival, the rest are not a
# conflict. Every other kind of actual arrival must agree on one date.
SEQUENCE_ARRIVALS = ("EXP:ARRIVED_DESTINATION_COUNTRY",)


# -- 4. decision -----------------------------------------------------------

def decide(events, today=None):
    """
    -> {"eta", "ata", "eta_event", "ata_event", "review": [reasons], "notes": [...]}.
    A non-empty "review" means: write NOTHING, a person decides.
    """
    today = today or date.today()
    review, notes = [], []
    undated = [e for e in events if e["type"] in (EST_ARRIVAL, ACT_ARRIVAL) and not _day(e["date"])]
    if undated:
        review.append("an arrival event has no unambiguous date: '{0}'".format(
            undated[0]["description"]))
    acts = [e for e in events if e["type"] == ACT_ARRIVAL and _day(e["date"])]
    ests = [e for e in events if e["type"] == EST_ARRIVAL and _day(e["date"])]
    later = [e for e in events if e["type"] in (POST_ARRIVAL, ACT_DELIVERY) and _day(e["date"])]

    ata_event = None
    if acts and all(e.get("code") in SEQUENCE_ARRIVALS for e in acts):
        acts = [min(acts, key=lambda e: _day(e["date"]))]   # first arrival in the country
    if len({e["date"] for e in acts}) > 1:
        review.append("conflicting actual arrivals: " + ", ".join(
            "{0} ({1})".format(e["date"], e["description"]) for e in acts))
    elif acts:
        ata_event = min(acts, key=lambda e: _day(e["date"]))
        if _day(ata_event["date"]) > today:
            review.append("actual arrival {0} is in the future".format(ata_event["date"]))
            ata_event = None
        elif later and min(_day(e["date"]) for e in later) < _day(ata_event["date"]):
            first = min(later, key=lambda e: _day(e["date"]))
            review.append("arrival {0} is after a later step '{1}' on {2}".format(
                ata_event["date"], first["description"], first["date"]))
            ata_event = None
    elif later:
        review.append("the shipment shows '{0}' ({1}) but no actual arrival event: the arrival "
                      "date is not known, nothing is inferred".format(
                          later[0]["description"], later[0]["date"]))

    eta_event = None
    if not acts and not later:
        if len({e["date"] for e in ests}) > 1:
            review.append("several different estimated arrivals: " + ", ".join(
                e["date"] for e in ests))
        elif ests:
            eta_event = ests[0]
    elif ests:
        notes.append("estimated arrival ignored: the shipment has arrived")

    if review:
        return {"eta": None, "ata": None, "eta_event": None, "ata_event": None,
                "review": review, "notes": notes}
    return {"eta": eta_event and eta_event["date"], "ata": ata_event and ata_event["date"],
            "eta_event": eta_event, "ata_event": ata_event, "review": [], "notes": notes}


# -- 5. the shared guard before every Hub write -----------------------------

def _norm_ref(value):
    return re.sub(r"[\s-]", "", str(value or "")).upper()


def check_before_write(shipment, result, today=None):
    """
    The deterministic check every carrier result passes before the Hub.
    -> (eta, ata, review_reasons, notes). Non-empty review_reasons: write nothing.
    """
    today = today or date.today()
    result = result or {}
    review, notes = [], []
    seen = result.get("reference")
    if seen and _norm_ref(seen) not in (_norm_ref(shipment.get("bol_awb")),
                                        _norm_ref(shipment.get("tracking_reference"))):
        review.append("the carrier answered for {0}, not {1}".format(seen, shipment.get("bol_awb")))
    eta, ata = result.get("eta"), result.get("ata")
    for name, value in (("ETA", eta), ("ATA", ata)):
        if value and not _day(value):
            review.append("{0} '{1}' is not an unambiguous dd/mm/yyyy date".format(name, value))
    if ata and _day(ata) and _day(ata) > today:
        review.append("ATA {0} is in the future".format(ata))
    if ata and classify_label(result.get("ata_source"), "ata") != ACT_ARRIVAL:
        review.append("ATA {0} comes from '{1}', which is not named as an actual arrival".format(
            ata, result.get("ata_source") or "an unnamed source"))
    if eta and result.get("eta_source") and \
            classify_label(result.get("eta_source"), "eta") == EST_DELIVERY:
        notes.append("ETA {0} came from a delivery estimate ('{1}'): not written as the "
                     "ETA".format(eta, result.get("eta_source")))
        eta = None
    if eta and ata and eta == ata:
        # The same date read as both is evidence of mis-pairing (an estimate
        # read twice), not of arrival: a person checks it.
        review.append("ETA and ATA are the same date ({0}) from one read: the estimate may "
                      "have been read as the arrival".format(eta))
    elif eta and ata:
        notes.append("the shipment has arrived ({0}): the ETA {1} is not written".format(ata, eta))
        eta = None
    if review:
        return None, None, review, notes
    return eta, ata, review, notes


def ata_overwrite_problem(current, new):
    """The Hub already holds an ATA: a different one is never written over it
    automatically (neither newer nor older); a person decides."""
    if not re.search(r"\d", str(current or "")):
        return None             # empty, or an input mask / placeholder with no date in it
    raw = re.split(r"\s+\d{1,2}:\d{2}", str(current).strip())[0]
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
    cur = parse_date(raw) or (m and _day("{0:02d}/{1:02d}/{2}".format(
        int(m.group(1)), int(m.group(2)), m.group(3))) and "{0:02d}/{1:02d}/{2}".format(
        int(m.group(1)), int(m.group(2)), m.group(3)))
    if not cur:
        return "the Hub's current ATA '{0}' cannot be read as a date".format(current)
    if new and cur != new:
        return "the Hub already holds ATA {0}; the carrier now gives {1}".format(cur, new)
    return None
