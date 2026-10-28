"""
Build dashboard/places/places.tsv.gz: the airports, ports and towns the
Tracking map can place a carrier's reported location at.

Sources, downloaded once and passed by path (nothing is fetched here or at
run time):

  airports.csv          OurAirports (public domain), ourairports.com/data
  cities15000.zip       GeoNames, towns of 15,000 people or more (CC BY 4.0),
                        download.geonames.org/export/dump
  countryInfo.txt       GeoNames country names and ISO codes (CC BY 4.0)
  ports.geojson         Natural Earth 1:10m ports (public domain)
  countries-50m.json    Natural Earth 1:50m countries via world-atlas 2.0.2,
                        only to say which country each port is in

    python tools/build_gazetteer.py DIR

One row per place, tab-separated:

  kind  A airport | P port | C town | N country
  code  IATA code (airports), ISO 3166 alpha-2 (countries), else empty
  name  the place's own name
  alt   other names it is known by, '|'-separated (towns of 100,000 or
        more: their Latin-script GeoNames names; airports: their town)
  cc    ISO 3166 alpha-2 country
  lat, lon
  pop   population (towns), else 0
"""
import csv
import gzip
import io
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "dashboard" / "places" / "places.tsv.gz"


def ascii_name(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(text.split())


def clean(text):
    return str(text or "").replace("\t", " ").replace("\n", " ").strip()


def countries(path):
    rows = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("\t")
        rows[f[0]] = {"iso3": f[1], "num": f[2], "name": f[4]}
    return rows


def airports(path):
    out = []
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            code = (r["iata_code"] or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{3}", code) or r["type"] not in ("large_airport", "medium_airport"):
                continue
            out.append(("A", code, clean(r["name"]), clean(r["municipality"]), r["iso_country"],
                        float(r["latitude_deg"]), float(r["longitude_deg"]), 0))
    return out


ALT_OK = re.compile(r"^[A-Za-z][A-Za-z .'\-()]{2,39}$")


def towns(path):
    out = []
    with zipfile.ZipFile(path) as z:
        text = z.read("cities15000.txt").decode("utf-8")
    for line in text.splitlines():
        f = line.split("\t")
        name, asc, alts, cc, pop = f[1], f[2], f[3], f[8], int(f[14] or 0)
        names = []
        # Other names only for towns of 100,000 or more: a carrier prints a
        # big town's local name ("Bruxelles"), a small one's own.
        for a in [asc] + (alts.split(",") if alts and pop >= 100000 else []):
            a = ascii_name(a)
            # Latin-script names only (what a carrier page prints), no codes
            # or abbreviations, no duplicates.
            if ALT_OK.match(a) and not a.isupper() and a.lower() != ascii_name(name).lower() \
                    and a.lower() not in (n.lower() for n in names):
                names.append(a)
        out.append(("C", "", clean(name), "|".join(names[:40]), cc,
                    float(f[4]), float(f[5]), pop))
    return out


def topo_rings(topo):
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(ids):
        out = []
        for i in ids:
            pts = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
            out.extend(pts if not out else pts[1:])
        return out

    shapes = {}
    for g in topo["objects"]["countries"]["geometries"]:
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]] if g["type"] == "Polygon" else []
        shapes[g.get("id")] = [[ring(r) for r in poly] for poly in polys]
    return shapes


def inside(pt, rings):
    x, y = pt
    hit = False
    for r in rings:
        for i in range(len(r)):
            (x1, y1), (x2, y2) = r[i - 1], r[i]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                hit = not hit
    return hit


def ports(path, topo_path, by_num):
    shapes = topo_rings(json.loads(Path(topo_path).read_text(encoding="utf-8")))
    cc_of = {v["num"]: k for k, v in by_num.items()}
    out = []
    for f in json.loads(Path(path).read_text(encoding="utf-8"))["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        name = clean(f["properties"].get("name"))
        if not name:
            continue
        # The country whose outline holds the port, else the nearest outline
        # point within ~0.3 degrees (ports sit on the coast line itself).
        cc, best = "", 0.3
        for num, polys in shapes.items():
            for poly in polys:
                if inside((lon, lat), poly[:1]):
                    cc, best = cc_of.get(num, ""), 0
                    break
                if best:
                    for p in poly[0][::2]:
                        d = abs(p[0] - lon) + abs(p[1] - lat)
                        if d < best:
                            cc, best = cc_of.get(num, ""), d
            if best == 0:
                break
        if cc:
            out.append(("P", "", name, "", cc, lat, lon, 0))
    return out


def main(folder):
    d = Path(folder)
    cinfo = countries(d / "countryInfo.txt")
    rows = [("N", k, v["name"], v["iso3"], k, 0.0, 0.0, 0) for k, v in sorted(cinfo.items())]
    rows += airports(d / "airports.csv")
    rows += ports(d / "ports.geojson", d / "countries-50m.json", cinfo)
    rows += towns(d / "cities15000.zip")
    buf = io.StringIO()
    buf.write("# kind\tcode\tname\talt\tcc\tlat\tlon\tpop\n")
    for kind, code, name, alt, cc, lat, lon, pop in rows:
        buf.write("\t".join([kind, code, name, alt, cc, "%.4f" % lat, "%.4f" % lon, str(pop)]) + "\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(OUT, "wb", mtime=0) as fh:
        fh.write(buf.getvalue().encode("utf-8"))
    kinds = {}
    for r in rows:
        kinds[r[0]] = kinds.get(r[0], 0) + 1
    print("{0}: {1} rows {2}, {3:,} bytes".format(OUT, len(rows), kinds, OUT.stat().st_size))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
