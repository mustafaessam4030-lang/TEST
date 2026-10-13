"""DHL tracking API: ETA and ATA decided from the shipment's own events."""
import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dhl_api as D

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, " - " + detail if detail else ""))


def ev(ts, text, cc):
    return {"timestamp": ts, "description": text,
            "location": {"address": {"addressLocality": "X", "countryCode": cc}}}


def ship(events, status="transit", eta=None, sid="1234567890", dest="GH"):
    s = {"id": sid, "status": {"statusCode": status},
         "destination": {"address": {"countryCode": dest}}, "events": events}
    if eta:
        s["estimatedTimeOfDelivery"] = eta
    return {"shipments": [s]}


IN_TRANSIT = ship([ev("2026-07-10T09:00:00+02:00", "Shipment picked up", "BE"),
                   ev("2026-07-11T22:00:00+02:00", "Departed Facility in BRUSSELS", "BE")],
                  eta="2026-07-15T23:59:00+00:00")
DELIVERED = ship([ev("2026-07-10T09:00:00+02:00", "Shipment picked up", "BE"),
                  ev("2026-07-12T06:10:00+00:00", "Arrived at DHL Sort Facility ACCRA", "GH"),
                  ev("2026-07-13T08:00:00+00:00", "Arrived at DHL Delivery Facility TEMA", "GH"),
                  ev("2026-07-14T20:45:00+00:00", "Import Customs Cleared", "GH"),
                  ev("2026-07-22T18:40:00+00:00", "Shipment Delivered", "GH")],
                 status="delivered", eta="2026-07-22T18:40:00+00:00")
NO_ARRIVAL = ship([ev("2026-07-14T20:45:00+00:00", "Import Customs Cleared", "GH"),
                   ev("2026-07-22T18:40:00+00:00", "Shipment Delivered", "GH")],
                  status="delivered", eta="2026-07-22T18:40:00+00:00")

r, why, stop = D.interpret(IN_TRANSIT, "1234567890")
check("In transit: ETA from DHL's estimate", r and r["eta"] == "15/07/2026", str(r and r["eta"]))
check("In transit: no ATA", r and r["ata"] is None)

r, why, stop = D.interpret(DELIVERED, "1234567890")
check("Delivered: ATA = FIRST arrival in the destination country, not the delivery",
      r and r["ata"] == "12/07/2026", str(r and r["ata"]))
check("Delivered: ETA not written once arrived", r and r["eta"] is None)
check("Latest event recorded", r and r["latest_event"]["text"] == "Shipment Delivered")

r, why, stop = D.interpret(NO_ARRIVAL, "1234567890")
check("At destination but no arrival event: nothing written, needs review",
      r is None and stop and "no arrival" in why, why)

check("Arrival outside destination country is not the ATA",
      D.interpret(ship([ev("2026-07-11T05:00:00+00:00", "Arrived at DHL Hub LEIPZIG", "DE")],
                       eta="2026-07-15T00:00:00+00:00"), "1234567890")[0]["ata"] is None)
check("'Expected arrival' is not an arrival",
      D.interpret(ship([ev("2026-07-11T05:00:00+00:00", "Expected arrival ACCRA", "GH")],
                       eta="2026-07-15T00:00:00+00:00"), "1234567890")[1] is not None)
r, why, stop = D.interpret(ship([], sid="999"), "1234567890")
check("Answer for another number is refused (identity)", r is None and stop, why)
check("Spaces in the number do not break identity",
      D.interpret(IN_TRANSIT, "12345 67890")[0] is not None)
check("No shipment: page fallback allowed", D.interpret({"shipments": []}, "1")[2] is False)

# The key: only in the header, never in the URL.
os.environ["DHL_API_KEY"] = "SECRET-KEY-123"
seen = {}


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def opener(req, timeout):
    seen["url"], seen["hdr"] = req.full_url, req.get_header("Dhl-api-key")
    return Resp(json.dumps(IN_TRANSIT).encode())


D.fetch("1234567890", opener=opener, sleep=lambda s: None)
check("Key sent in the DHL-API-Key header", seen["hdr"] == "SECRET-KEY-123")
check("Key not in the URL", "SECRET" not in seen["url"])

# Integration: no key -> page path; API failure -> page path; review -> stop.
import update_eta as A
A.write_log = lambda m: None
os.environ.pop("DHL_API_KEY")
check("No key: API not used", A.get_dhl_result_from_api("1234567890") is None)
os.environ["DHL_API_KEY"] = "k"
D.fetch = lambda n: (_ for _ in ()).throw(urllib.error.URLError("down"))
check("API down: falls back to the web page", A.get_dhl_result_from_api("1234567890") is None)
D.fetch = lambda n: NO_ARRIVAL
try:
    A.get_dhl_result_from_api("1234567890"); check("Review case stops the shipment", False)
except A.SkipShipment:
    check("Review case stops the shipment (no page fallback)", True)
D.fetch = lambda n: DELIVERED
check("API result used", A.get_dhl_result_from_api("1234567890")["ata"] == "12/07/2026")
os.environ.pop("DHL_API_KEY")

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
