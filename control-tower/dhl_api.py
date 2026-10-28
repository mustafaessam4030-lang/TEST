"""
DHL Shipment Tracking - Unified API: the shipment's own event list, as data.

Reading DHL's web page means guessing which date belongs to which event. The
API returns every event with its own timestamp, location and status code, and
the shipment's destination country, so ETA and ATA are decided from facts:

    ATA   the FIRST event in the destination country that says the shipment
          arrived ("arrived", "arrival"). Customs cleared, out for delivery
          or delivered are later steps, never the arrival.
    ETA   DHL's estimatedTimeOfDelivery, only while no arrival is recorded.
    none  delivered/cleared at destination but no arrival event: no ATA is
          written; a person reviews it.

Free key from developer.dhl.com ("Shipment Tracking - Unified"), kept in the
DHL_API_KEY environment variable. Without a key this module does nothing and
the web page is read as before. The key is sent only in the DHL-API-Key
header to api-eu.dhl.com and is never logged or saved.
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

URL = "https://api-eu.dhl.com/track/shipments"
MIN_GAP_SECONDS = 5.0          # DHL's starting limit: one call every 5 seconds
_last_call = [0.0]

ARRIVAL = re.compile(r"\barriv(ed|al)\b", re.I)
NOT_ARRIVAL = re.compile(r"\b(depart|expected|estimated|scheduled|planned)", re.I)


def key():
    return (os.environ.get("DHL_API_KEY") or "").strip()


def enabled():
    return bool(key())


def fetch(tracking_number, timeout=30, opener=urllib.request.urlopen, sleep=time.sleep):
    """The API's JSON for one tracking number. Raises on any failure."""
    wait = MIN_GAP_SECONDS - (time.monotonic() - _last_call[0])
    if wait > 0:
        sleep(wait)
    query = urllib.parse.urlencode({"trackingNumber": tracking_number})
    request = urllib.request.Request(URL + "?" + query, headers={
        "DHL-API-Key": key(), "Accept": "application/json"})
    try:
        with opener(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    finally:
        _last_call[0] = time.monotonic()


def _day(timestamp):
    """dd/mm/yyyy from DHL's ISO timestamp, as DHL states it (local time at
    the event's location; no conversion)."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(timestamp or ""))
    return "{2}/{1}/{0}".format(*m.groups()) if m else None


def _country(place):
    return str(((place or {}).get("address") or {}).get("countryCode") or "").upper()


def _locality(place):
    """The place name DHL gives ("LEIPZIG - GERMANY"), or None."""
    town = str(((place or {}).get("address") or {}).get("addressLocality") or "").strip()
    return town or None


def _clock(timestamp):
    """(HH:MM, "UTC +hh:mm") from DHL's timestamp, the offset only when DHL
    states one."""
    m = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?",
                 str(timestamp or ""))
    if not m:
        return None, None
    zone = m.group(2)
    return m.group(1), ("UTC" if zone == "Z" else "UTC " + zone if zone else None)


def places(s, events):
    """What the Tracking map needs: the places DHL names, as DHL names them
    (dashboard/geo.py decides which can be placed)."""
    out = []
    for e in events:
        clock, zone = _clock(e.get("timestamp"))
        out.append({"date": _day(e.get("timestamp")), "time": clock, "tz": zone,
                    "text": _text(e).strip()[:160], "place": _locality(e.get("location")),
                    "country": _country(e.get("location")) or None})
    return {"source": "DHL Shipment Tracking API", "order": "oldest_first",
            "origin": _locality(s.get("origin")), "origin_country": _country(s.get("origin")) or None,
            "destination": _locality(s.get("destination")),
            "destination_country": _country(s.get("destination")) or None,
            "events": out}


def _text(event):
    return " ".join(str(event.get(k) or "") for k in ("status", "description", "remark"))


def interpret(payload, tracking_number):
    """
    -> (result, None, False), or (None, why, needs_review). needs_review is
    True when DHL answered but the dates cannot be trusted: the caller stops
    there (no page fallback). The result has the same shape as the
    web-page reader's. Nothing is guessed: identity, destination and every
    date come from the payload.
    """
    shipments = (payload or {}).get("shipments") or []
    if len(shipments) != 1:
        return None, "DHL API returned {0} shipments for {1}".format(
            len(shipments), tracking_number), False
    s = shipments[0]
    wanted = re.sub(r"\s+", "", str(tracking_number)).upper()
    if re.sub(r"\s+", "", str(s.get("id") or "")).upper() != wanted:
        return None, "DHL API answered for {0}, not {1}".format(
            s.get("id"), tracking_number), True
    dest = _country(s.get("destination"))
    events = sorted((e for e in s.get("events") or [] if _day(e.get("timestamp"))),
                    key=lambda e: str(e.get("timestamp")))
    status = (s.get("status") or {}).get("statusCode") or "unknown"
    latest = events[-1] if events else None

    ata, ata_event = None, None
    if dest:
        for e in events:
            if _country(e.get("location")) == dest and ARRIVAL.search(_text(e)) \
                    and not NOT_ARRIVAL.search(_text(e)):
                ata, ata_event = _day(e["timestamp"]), e
                break
    at_destination = dest and any(_country(e.get("location")) == dest for e in events)
    review = None
    if not ata and (status == "delivered" or at_destination):
        review = ("DHL shows the shipment at or past its destination ({0}) but no arrival "
                  "event, so no ATA is written; a person checks it.".format(dest or "?"))

    if review:
        return None, review, True
    eta = None if ata else _day(s.get("estimatedTimeOfDelivery"))
    if not ata and not eta:
        return None, "DHL API gave no arrival event and no estimated delivery", False
    return {
        "provider": "DHL",
        "source": "DHL Shipment Tracking API",
        "tracking_status": "Arrived ({0})".format(_text(ata_event).strip()[:80]) if ata
                           else "Estimated delivery",
        "eta": eta, "ata": ata,
        "eta_source": "estimatedTimeOfDelivery" if eta else None,
        "ata_source": _text(ata_event).strip()[:120] if ata else None,
        "timeline_status": status,
        "destination_country": dest or None,
        "latest_event": {"date": _day(latest.get("timestamp")),
                         "text": _text(latest).strip()[:200]} if latest else None,
        "places": places(s, events),
    }, None, False
