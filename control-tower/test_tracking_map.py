"""
Tracking map: carrier-reported places, put on a real map, and nothing else.

    python test_tracking_map.py

Covers dashboard/geo.py (the place table and its rules), the places each
carrier reader hands over (DHL page, DHL API, DHL Aviation, AFKL), the
bridge record, ATLAS's answer, the page (static/trackmap.js), the map files
and the release. No network, no browser.
"""
import gzip
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import arrival_events as AE  # noqa: E402
import dhl_api  # noqa: E402
import update_eta as U  # noqa: E402
from dashboard import geo  # noqa: E402
from dashboard.bridge import ControlTowerState  # noqa: E402

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "" if condition or not detail else "  -- " + str(detail)[:200]))


def section(title):
    print("\n" + "=" * 72 + "\n" + title + "\n" + "=" * 72)


def at(text, country=None):
    p = geo.resolve(text, country)
    return p and (p["kind"], p["name"], p["cc"])


section("1. PLACE NAMES, AS CARRIERS PRINT THEM")
check("A DHL Express place, 'CITY - COUNTRY'", at("LEIPZIG - GERMANY") == ("town", "Leipzig", "DE"))
check("A DHL Forwarding airport, by its own name",
      at("Kotoka International Airport") == ("airport", "Kotoka International Airport", "GH"))
check("A town by its local name", at("Bruxelles (Brussel)") == ("town", "Brussels", "BE"))
check("'Town, Country'", at("Accra, Ghana") == ("town", "Accra", "GH"))
check("An airport named by its town ('EAST MIDLANDS - UK') and the UK spelling",
      at("EAST MIDLANDS - UK") == ("airport", "East Midlands Airport", "GB"))
check("A part of a place ('JEBEL ALI FREE ZONE') is the place",
      at("JEBEL ALI FREE ZONE - UNITED ARAB EMIRATES") == ("town", "Jebel Ali", "AE"))
check("'LOS (lagos)' is the IATA code it starts with",
      at("LOS (lagos)") == ("airport", "Murtala Muhammed International Airport", "NG"))
check("A station code, where the carrier gives codes", (geo.resolve_iata("BRU") or {}).get("name") == "Brussels Airport")
check("A country alone has no coordinates: it is a country, not a point",
      geo.resolve("Ghana") == {"name": "Ghana", "kind": "country", "cc": "GH", "country": "Ghana", "said": "Ghana"})

section("2. NOTHING GUESSED")
check("A place the table does not hold stays unplaced", geo.resolve("Machelen") is None)
check("A name shared by towns of similar size stays unplaced", geo.resolve("Springfield") is None)
check("A town in the wrong country stays unplaced", geo.resolve("Accra, Nigeria") is None)
check("A bare three letters is not read as an airport outside a station field", geo.resolve("MUC") is None)
check("An unknown station code is not an airport", geo.resolve_iata("ZZZ") is None)
check("Empty, None and oversized text are unplaced",
      geo.resolve("") is None and geo.resolve(None) is None and geo.resolve("x" * 200) is None)
check("A name shared with a far smaller town is the big one (London, UK over London, Canada)",
      at("London") == ("town", "London", "GB"))

section("3. ONLY PLACES THE SHIPMENT HAS BEEN")
ev = [
    {"date": "08/10/2026", "time": "6:00 PM", "text": "Estimated Pick Up Date", "place": "Grimbergen"},
    {"date": "08/10/2026", "time": "2:45 PM", "text": "Import Customs Cleared", "place": "Accra"},
    {"date": "08/10/2026", "time": "11:37 AM", "text": "Actual Pickup Time", "place": "Grimbergen"},
    {"date": "09/10/2026", "time": "9:00 AM", "text": "All Export Documents Received", "place": "Machelen"},
    {"date": "11/10/2026", "time": "12:23 PM", "tz": "UTC +02:00", "text": "Carrier: Departed", "place": "Brussels Airport"},
    {"date": "11/10/2026", "time": "5:00 PM", "text": "Estimated Carrier Arrival (Destination)", "place": "Accra"},
]
w = geo.where({"source": "t", "origin": "Brussel (Bruxelles), Belgium", "destination": "Accra, Ghana", "events": ev})
names = [p["name"] for p in w["path"]]
check("Forecasts are not places it has been", "Accra" not in names, names)
check("Paperwork ('Customs Cleared', 'Documents Received') is not a place it has been",
      w["unplaced"] == [] and names == ["Grimbergen", "Brussels Airport"], (names, w["unplaced"]))
check("The last reported place is the newest physical event",
      w["last"]["pt"]["name"] == "Brussels Airport" and w["last"]["text"] == "Carrier: Departed")
check("Origin and destination are the page's own lines",
      w["origin"]["name"] == "Brussels" and w["destination"]["name"] == "Accra")
late = geo.where({"events": [
    {"date": "24/09/2026", "time": "6:46 AM", "tz": "UTC +02:00", "text": "Shipment has departed", "place": "LEIPZIG - GERMANY"},
    {"date": "24/09/2026", "time": "6:09 AM", "tz": "UTC +01:00", "text": "Arrived at DHL Sort Facility", "place": "LONDON-HEATHROW - UK"}]})
check("Events are ordered in UTC across time zones (06:46 +02 is before 06:09 +01)",
      [p["name"] for p in late["path"]] == ["Leipzig", "London"], [p["name"] for p in late["path"]])
newest = geo.where({"events": [
    {"date": "01/10/2026", "time": "09:00", "text": "Arrived", "place": "Accra"},
    {"date": "02/10/2026", "time": "09:00", "text": "Arrived", "place": "Machelen"}]})
check("A newest place that cannot be placed is said, not replaced by an older one",
      newest["last"]["said"] == "Machelen" and newest["last"]["pt"] is None and newest["unplaced"] == ["Machelen"])
check("Nothing named, nothing returned", geo.where({"events": []}) is None and geo.where(None) is None)
check("The sentence ATLAS uses names the place and the event",
      geo.sentence(w).startswith("The carrier last reported it at Brussels Airport (BRU), Belgium: Carrier: Departed"))
check("...and says so when the newest place cannot be placed",
      "cannot put on the map" in geo.sentence(newest))

section("4. EVERY SAVED DHL TIMELINE")
fixtures = sorted((HERE / "fixtures" / "dhl_oct12").glob("OCT12-*.txt"))
placed = 0
for f in fixtures:
    text = f.read_text(encoding="utf-8")
    events, _product, dest = AE.dhl_events(text, f.stem)
    w = geo.where(U.dhl_places(text, events, dest))
    if w and w["last"] and w["last"]["pt"]:
        placed += 1
check("Each of the {0} saved DHL pages gives a last reported place".format(len(fixtures)),
      len(fixtures) >= 40 and placed == len(fixtures), "{0}/{1}".format(placed, len(fixtures)))
t3 = (HERE / "fixtures" / "dhl_oct12" / "OCT12-03.txt").read_text(encoding="utf-8")
e3, _p, d3 = AE.dhl_events(t3, "OCT12-03")
w3 = geo.where(U.dhl_places(t3, e3, d3))
check("OCT12-03: picked up in Grimbergen, flown from Brussels, arrived at Kotoka",
      [p["name"] for p in w3["path"]] == ["Grimbergen", "Brussels", "Brussels Airport", "Kotoka International Airport"],
      [p["name"] for p in w3["path"]])

section("5. DHL AVIATION, AFKL, DHL API")
av = U.dhl_aviation_places((HERE / "fixtures" / "dhl_aviation_61562308396.txt").read_text(encoding="utf-8"))
wa = geo.where(av)
check("DHL Aviation: 'From DHL Org BRU to DHL Dest LOS' are the route's stations",
      av["origin_code"] == "BRU" and av["destination_code"] == "LOS"
      and wa["origin"]["code"] == "BRU" and wa["destination"]["code"] == "LOS")
check("...checked in at BRU, awaiting collection at LOS; 'Manifested' and 'Scheduled' are not places",
      [p["code"] for p in wa["path"]] == ["BRU", "LOS"] and wa["last"]["text"].startswith("NFD"))
AFKL = "MUC ✈ ACC\nDELIVERY OK NOTIFIED 074-46285514\n20 SEP 23:00 - 1 piece ready to be picked up at ACC\n" \
       "20 SEP 22:58 - 1 piece received at ACC from KL0589\n19 SEP 10:00 - 1 piece departed from MUC on KL1234"
af = U.afkl_places(AFKL)
wf = geo.where(af)
check("AFKL: the route header gives origin and destination",
      af["origin_code"] == "MUC" and af["destination_code"] == "ACC" and wf["origin"]["code"] == "MUC")
check("...and each milestone its station; a flight number is not a station",
      [p["code"] for p in wf["path"]] == ["MUC", "ACC"] and all(e["place"] != "KL0" for e in af["events"]))
payload = {"shipments": [{"id": "1234567890", "status": {"statusCode": "transit"},
                          "origin": {"address": {"addressLocality": "LEIPZIG - GERMANY", "countryCode": "DE"}},
                          "destination": {"address": {"countryCode": "GH"}},
                          "estimatedTimeOfDelivery": "2026-10-30T10:00:00",
                          "events": [{"timestamp": "2026-10-20T05:00:00+02:00", "status": "Processed at LEIPZIG",
                                      "location": {"address": {"addressLocality": "LEIPZIG - GERMANY", "countryCode": "DE"}}}]}]}
res, _why, _stop = dhl_api.interpret(payload, "1234567890")
wd = geo.where(res["places"])
check("DHL API: each event's locality and country, its offset kept",
      res["places"]["events"][0]["tz"] == "UTC +02:00" and wd["last"]["pt"]["name"] == "Leipzig")
check("...a destination given only as a country is that country, not a point",
      wd["destination"]["kind"] == "country" and wd["destination"]["cc"] == "GH" and "lat" not in wd["destination"])

section("6. THE BRIDGE RECORD AND ATLAS")
tower = ControlTowerState()
tower.run_started(target_status="In Transit")
tower.shipment_started({"bol_awb": "K179801", "carrier": "KLM", "provider": "DHL", "table_page": 1})
tower.provider_result({"provider": "DHL", "tracking_status": "Arrived", "eta": None, "ata": "11/10/2026",
                       "places": U.dhl_places(t3, e3, d3)})
tower.shipment_finished("K179801", "SUCCESS")
rec = next(r for r in tower.snapshot()["shipments"] if r["reference"] == "K179801")
check("The record carries where: the last reported place, resolved",
      rec["where"]["last"]["pt"]["code"] == "ACC")
tower.shipment_started({"bol_awb": "157-1", "carrier": "Qatar Airways Cargo", "provider": "QATAR", "table_page": 1})
tower.provider_result({"provider": "QATAR", "tracking_status": "Estimated arrival", "eta": "02/11/2026", "ata": None})
rec2 = next(r for r in tower.snapshot()["shipments"] if r["reference"] == "157-1")
check("A carrier that names no place leaves where empty", rec2.get("where") is None)
tower.shipment_started({"bol_awb": "1570046231", "carrier": "DHL", "provider": "DHL", "table_page": 1})
tower.provider_result({"provider": "DHL", "eta": "01/11/2026", "places": {"events": "not a list"}})
rec3 = next(r for r in tower.snapshot()["shipments"] if r["reference"] == "1570046231")
check("Malformed places never break the record", rec3.get("provider_eta") == "01/11/2026" and rec3.get("where") is None)
from dashboard import assistant, atlas_intel  # noqa: E402
ans = assistant.answer("where is K179801", tower.snapshot())["answer"]
check("ATLAS answers 'where is' with the carrier's last reported place",
      "Kotoka International Airport (ACC), Ghana" in ans, ans[:200])
check("ATLAS says nothing about a place for a shipment whose carrier named none",
      atlas_intel.where_line(rec2) is None)

section("7. THE PAGE")
page = (HERE / "dashboard" / "static" / "trackmap.js").read_text(encoding="utf-8")
index = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
check("The Tracking card draws the map in place of the abstract route",
      'id="routeMap"' in index and "window.ctTrackMap.paint({el:$('routeMap')" in index
      and "function routeMapSVG" not in index and "ROUTE_PATHS" not in index)
check("The page never makes up coordinates: every point is a record's lat/lon, projected",
      "Math.random" not in page and re.search(r"lat:\s*-?\d", page.split("function boundsAll")[0]) is None)
check("The marker is the newest reported place, or the destination once arrived — nothing between",
      "if (w.last && placed(w.last.pt)) return {pt: w.last.pt, ev: w.last};" in page
      and 'if (it.k === "arrived" && placed(w.destination))' in page)
check("It says on the map that it is not live GPS, and how many are placed",
      "not a live GPS position" in page and "placed</span>" in page)
check("Interactive: drag, Ctrl + scroll, buttons, keyboard, click to follow a shipment",
      all(x in page for x in ('"pointerdown"', "e.ctrlKey || e.metaKey", 'data-z="in"', 'data-z="all"',
                              '"keydown"', "data.onSelect(it.ref)")))
check("Plain scrolling still scrolls the page (the map shows a hint instead)",
      "Ctrl + scroll to zoom" in page)
check("Text from records is escaped before it reaches the page",
      "${esc(it.ref)}" in page and "${esc(ev.text)}" in page)
check("Light and dark: its colours are tokens, redefined for dark",
      ':root[data-theme="dark"]{--tm-sea' in page)

section("8. MAP FILES AND RELEASE")
world = json.loads((HERE / "dashboard" / "static" / "maps" / "world.json").read_text(encoding="utf-8"))
check("world.json: Natural Earth outlines, Mercator, its source named",
      world["projection"]["type"] == "mercator" and "Natural Earth" in world["source"] and len(world["countries"]) > 180)
check("...and each country keeps its ISO code (for the destination's shading)",
      sum(1 for c in world["countries"] if c["cc"]) > 180)
with gzip.open(HERE / "dashboard" / "places" / "places.tsv.gz", "rt", encoding="utf-8") as fh:
    rows = [line.split("\t")[0] for line in fh if not line.startswith("#")]
check("places.tsv.gz: airports, ports, towns and countries",
      all(rows.count(k) > n for k, n in (("A", 4000), ("P", 900), ("C", 30000), ("N", 240))))
readme = (HERE / "dashboard" / "places" / "README.txt").read_text(encoding="utf-8")
check("Its sources and licences are named", all(x in readme for x in ("OurAirports", "GeoNames", "CC BY 4.0", "Natural Earth")))
import make_release  # noqa: E402
check("The release ships the place table and the world map",
      make_release.excluded("dashboard/places/places.tsv.gz") is None
      and make_release.excluded("dashboard/static/maps/world.json") is None
      and make_release.excluded("dashboard/static/trackmap.js") is None)

print("\n{0} passed, {1} failed".format(len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
