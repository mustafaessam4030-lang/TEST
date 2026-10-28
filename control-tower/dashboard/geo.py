"""
Where the carrier says a shipment has been: its reported place names, put on
the map.

Carriers report places, not positions. DHL names the place of each timeline
event ("LEIPZIG - GERMANY", "Kotoka International Airport"), DHL Aviation and
AFKL name the stations ("BRU", "MUC ✈ ACC"). This module matches those names
against places/places.tsv.gz (airports, ports, towns; see
tools/build_gazetteer.py) and nothing else:

  * exact names only, after accents, case and punctuation are folded; an
    airport, port or town the table does not hold stays unplaced
  * a country named beside the place ("Accra, Ghana") must agree
  * a town name shared by several towns is placed only when one of them is
    far bigger (London, UK over London, Canada); otherwise it stays unplaced
  * only events that put the shipment physically somewhere (picked up,
    received, departed, arrived, delivered) are places it has been;
    estimates ("Estimated Carrier Arrival") and paperwork ("Import Customs
    Cleared", often done at the destination before it leaves) are not

So a point on the map is the place a carrier event names, at town or airport
precision. It is never a GPS position and never filled in between events.
"""
import gzip
import re
import threading
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

TABLE = Path(__file__).resolve().parent / "places" / "places.tsv.gz"

# Country spellings carriers print that are not the ISO short name.
COUNTRY_ALIASES = {
    "UK": "GB", "U K": "GB", "GREAT BRITAIN": "GB", "ENGLAND": "GB", "SCOTLAND": "GB",
    "UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND": "GB",
    "USA": "US", "U S A": "US", "UNITED STATES OF AMERICA": "US",
    "UAE": "AE", "U A E": "AE",
    "THE NETHERLANDS": "NL", "HOLLAND": "NL",
    "IVORY COAST": "CI", "COTE D IVOIRE": "CI",
    "CONGO THE DEMOCRATIC REPUBLIC OF THE": "CD", "DEMOCRATIC REPUBLIC OF THE CONGO": "CD",
    "DR CONGO": "CD", "CONGO DR": "CD", "CONGO REPUBLIC OF": "CG",
    "TANZANIA UNITED REPUBLIC OF": "TZ", "UNITED REPUBLIC OF TANZANIA": "TZ",
    "KOREA REPUBLIC OF": "KR", "SOUTH KOREA": "KR", "REPUBLIC OF KOREA": "KR",
    "RUSSIAN FEDERATION": "RU", "VIET NAM": "VN", "TURKIYE": "TR", "TURKEY": "TR",
    "CZECH REPUBLIC": "CZ", "CZECHIA": "CZ", "HONG KONG SAR CHINA": "HK",
    "CHINA PEOPLES REPUBLIC": "CN", "PEOPLES REPUBLIC OF CHINA": "CN", "PR CHINA": "CN",
}

# Words after a place name that name a part of it, not another place:
# "JEBEL ALI FREE ZONE", "LEIPZIG HUB", "BRUSSELS GATEWAY".
PART_WORDS = re.compile(r"\s+(?:FREE\s+ZONE|FREEZONE|HUB|GATEWAY|SERVICE\s+AREA|SERVICE\s+CENTER|"
                        r"SERVICE\s+CENTRE|FACILITY|STATION|DEPOT|TERMINAL|WAREHOUSE|CARGO\s+CENTER|"
                        r"CARGO\s+CENTRE|CARGO)$")

# Events that put the shipment physically at their place: picked up,
# received, departed, arrived, delivered, and the air waybill codes for those
# (RCS, DEP, ARR, RCF, NFD, AWD, DLV). Everything else — customs cleared,
# entered into system, booked, manifested — is paperwork, often done at the
# destination before the shipment leaves, and is not drawn.
PHYSICAL = re.compile(r"arriv|depart|receiv|pick\s?-?up|picked|collected|deliver|\bloaded|unload|"
                      r"discharg|transferr|processed\s+at|sort\s+facility|in\s+transit\s+at|"
                      r"handed\s+over|gate[ds]?\s+(?:in|out)|out\s+for\s+delivery|"
                      r"awaiting\s+(?:consignee\s+)?collection|landed|on\s+board|accepted|"
                      r"\b(?:RCS|DEP|ARR|RCF|NFD|AWD|DLV)\b", re.I)
PAPERWORK = re.compile(r"document|customs|clearance|entered\s+into|booked|booking|manifest|"
                       r"notif|invoice|information|data\s+received", re.I)
# Forecasts name where the shipment will be, not where it has been.
FORECAST = re.compile(r"estimat|expect|scheduled|planned|\bET[AD]\b|forecast", re.I)

_lock = threading.Lock()
_index = None


def fold(text):
    """Accents, case and punctuation folded: 'Bruxelles (Brussel)' -> 'BRUXELLES BRUSSEL'."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", text).upper().split())


def _load():
    global _index
    with _lock:
        if _index is not None:
            return _index
        idx = {"iata": {}, "airport": {}, "port": {}, "town": {}, "country": {}, "cname": {}}
        try:
            with gzip.open(TABLE, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("#"):
                        continue
                    kind, code, name, alt, cc, lat, lon, pop = line.rstrip("\n").split("\t")
                    row = {"name": name, "cc": cc, "lat": float(lat), "lon": float(lon),
                           "pop": int(pop)}
                    if kind == "N":
                        idx["cname"][cc] = name
                        for n in (name, code, alt):
                            if n:
                                idx["country"][fold(n)] = cc
                    elif kind == "A":
                        row.update(kind="airport", code=code, town=alt)
                        idx["iata"][code] = row
                        idx["airport"].setdefault(fold(name), []).append(row)
                    elif kind == "P":
                        row["kind"] = "port"
                        idx["port"].setdefault(fold(name), []).append(row)
                    elif kind == "C":
                        row["kind"] = "town"
                        for n in {fold(name)} | {fold(a) for a in alt.split("|") if a}:
                            idx["town"].setdefault(n, []).append(row)
        except (OSError, ValueError):
            idx = {"iata": {}, "airport": {}, "port": {}, "town": {}, "country": {}, "cname": {}}
        for k, v in COUNTRY_ALIASES.items():
            idx["country"].setdefault(k, v)
        _index = idx
        return idx


def _point(row, said):
    out = {"name": row["name"], "kind": row["kind"], "cc": row["cc"],
           "country": _load()["cname"].get(row["cc"], row["cc"]),
           "lat": round(row["lat"], 3), "lon": round(row["lon"], 3), "said": str(said)}
    if row.get("code"):
        out["code"] = row["code"]
    if row.get("town"):
        out["town"] = row["town"]
    return out


def country_of(text):
    """ISO alpha-2 for a country name or code, or None."""
    return _load()["country"].get(fold(text)) if text else None


def resolve_iata(code, said=None):
    """The airport a three-letter IATA code names, or None."""
    code = str(code or "").strip().upper()
    row = _load()["iata"].get(code) if re.fullmatch(r"[A-Z]{3}", code) else None
    return _point(row, said or code) if row else None


def _pick(rows, cc):
    if cc:
        rows = [r for r in rows if r["cc"] == cc]
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        rows = sorted(rows, key=lambda r: -r["pop"])
        # The same name more than once: placed only when one is far the
        # bigger, and never between two equal unknowns (airports, ports).
        if rows[0]["pop"] and rows[0]["pop"] >= 10 * max(rows[1]["pop"], 1):
            return rows[0]
    return None


def _variants(head):
    """The place name as printed, then the readings of it that are still it."""
    seen = []

    def add(v):
        v = fold(v)
        if v and v not in seen:
            seen.append(v)

    add(head)
    paren = re.match(r"^(.*?)\s*\((.+)\)\s*$", head)
    if paren:
        add(paren.group(1))
        add(paren.group(2))
    for v in list(seen):
        add(PART_WORDS.sub("", v))
    if "-" in head and " - " not in head:                # LONDON-HEATHROW
        add(head.split("-")[0])
    return seen


def resolve(text, country=None):
    """
    The place a carrier's place text names, or None.

    'Accra, Ghana', 'LEIPZIG - GERMANY', 'Kotoka International Airport',
    'LOS (lagos)', 'Bruxelles (Brussel)'. A country on its own ('Ghana')
    comes back as {"kind": "country", "cc": ...} with no coordinates.
    """
    said = " ".join(str(text or "").split())
    if not said or len(said) > 120:
        return None
    idx = _load()
    code = re.fullmatch(r"([A-Z]{3})\s*\((.+)\)", said)
    if code:
        return resolve_iata(code.group(1), said)
    cc = country_of(country) if country else None
    head = said
    for sep in (" - ", ", ", ","):
        if sep in said:
            left, right = said.rsplit(sep, 1)
            named = idx["country"].get(fold(right))
            if named and left.strip():
                if cc and named != cc:
                    return None
                head, cc = left.strip(), named
                break
    whole = idx["country"].get(fold(head))
    if whole and head == said:
        return {"name": idx["cname"].get(whole, whole), "kind": "country", "cc": whole,
                "country": idx["cname"].get(whole, whole), "said": said}
    # An airport by its own name first, then the town, then a port; an
    # airport by its town's name ("EAST MIDLANDS") only when no town has it.
    names = _variants(head)
    for look in (lambda v: idx["airport"].get(v), lambda v: idx["town"].get(v),
                 lambda v: idx["port"].get(v), lambda v: idx["airport"].get(v + " AIRPORT"),
                 lambda v: idx["airport"].get(v + " INTERNATIONAL AIRPORT")):
        for v in names:
            rows = look(v)
            hit = _pick(rows, cc) if rows else None
            if hit:
                return _point(hit, said)
    return None


def _when(e, i):
    """When an event happened, in UTC when the carrier gave its offset ("UTC
    +03:00"): a timeline crosses time zones, and local clocks misorder it."""
    day = str(e.get("date") or "")
    clock = str(e.get("time") or "").upper().replace(" ", "")
    tz = re.match(r"^UTC\s*([+-])(\d{1,2}):?(\d{2})?", str(e.get("tz") or "").upper().strip())
    shift = timedelta(0)
    if tz:
        shift = timedelta(hours=int(tz.group(2)), minutes=int(tz.group(3) or 0))
        shift = shift if tz.group(1) == "+" else -shift
    for fmt in ("%d/%m/%Y%I:%M%p", "%d/%m/%Y%H:%M"):
        try:
            return (datetime.strptime(day + clock, fmt) - shift, -i)
        except ValueError:
            continue
    try:
        return (datetime.strptime(day, "%d/%m/%Y"), -i)
    except ValueError:
        return (datetime.min, -i)


def where(places):
    """
    The map's reading of one carrier result's "places":

        {"source": "DHL timeline",
         "origin": "Brussel (Bruxelles), Belgium" | None, "origin_code": "BRU" | None,
         "destination": "Accra, Ghana" | None, "destination_code": "ACC" | None,
         "events": [{"date": "11/10/2026", "time": "5:02 PM", "tz": "UTC +02:00",
                     "text": "...",
                     "place": "Kotoka International Airport", "code": False,
                     "estimated": False}]}

    returns {"source", "origin", "destination", "path", "last", "unplaced"}:
    path is the places the shipment has physically been, each once, in the
    order it first reached them, with the event that named it; last is the
    newest such event, with its point when its place could be placed. None
    when nothing is named.
    """
    if not isinstance(places, dict):
        return None

    def at(text, code, country=None):
        if code:
            p = resolve_iata(code, text or code)
            if p:
                return p
        if text:
            return resolve(text, country)
        return resolve(country) if country else None      # the country alone

    origin = at(places.get("origin"), places.get("origin_code"), places.get("origin_country"))
    dest = at(places.get("destination"), places.get("destination_code"),
              places.get("destination_country"))
    events = [e for e in (places.get("events") or []) if isinstance(e, dict)]
    ordered = sorted(enumerate(events), key=lambda p: _when(p[1], p[0]))
    path, unplaced, last = [], [], None
    for _i, e in ordered:
        said = " ".join(str(e.get("place") or "").split())
        text = str(e.get("text") or "")
        if not said or e.get("estimated") or FORECAST.search(text) or PAPERWORK.search(text) \
                or not PHYSICAL.search(text):
            continue
        pt = resolve_iata(said) if e.get("code") else resolve(said, e.get("country"))
        mark = {"date": e.get("date"), "time": e.get("time"),
                "text": str(e.get("text") or "")[:120], "said": said}
        last = dict(mark, pt=pt)
        if not pt:
            if said not in unplaced:
                unplaced.append(said)
            continue
        if pt.get("kind") == "country":
            continue
        # Each place once, where the shipment first reached it: a shipment
        # in several pieces reports them at different places at once, and
        # drawing every back-and-forth would be a scribble, not a route.
        key = (pt["lat"], pt["lon"])
        seen = next((p for p in path if (p["lat"], p["lon"]) == key), None)
        if seen:
            seen["seen"] = mark["date"]
        else:
            path.append(dict(pt, **mark))
    if not (origin or dest or path or last):
        return None
    return {"source": str(places.get("source") or "")[:60], "origin": origin,
            "destination": dest, "path": path[-24:], "last": last, "unplaced": unplaced[:12]}


def sentence(w):
    """
    Where the carrier last reported a shipment, in one sentence, from a
    where() reading — the place its newest event named, never a position in
    between. None when the carrier named no place.
    """
    w = w if isinstance(w, dict) else {}
    last = w.get("last") or {}
    pt = last.get("pt") or {}
    if pt.get("name") and pt.get("kind") != "country":
        name = pt["name"] + (" ({0})".format(pt["code"]) if pt.get("code") else "")
        return "The carrier last reported it at {0}, {1}: {2} on {3}{4}.".format(
            name, pt.get("country") or pt.get("cc") or "", last.get("text") or "an event",
            last.get("date") or "an unstated date", " " + last["time"] if last.get("time") else "")
    if last.get("said"):
        return "The carrier's newest event names \u201c{0}\u201d, a place I cannot put on the map.".format(
            last["said"])
    dest = (w.get("destination") or {}).get("name")
    if dest:
        return "The carrier names its destination, {0}, but no place it has reached yet.".format(dest)
    return None
