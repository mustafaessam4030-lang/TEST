"""
Build dashboard/static/maps/region.json: the outlines the Clearance map draws.

Source: Natural Earth 1:50m countries (public domain) as packaged by
world-atlas 2.0.2 (ISC licence), countries-50m.json. Download it once and
pass its path; nothing is fetched here.

    python tools/build_region_map.py countries-50m.json

The output holds, for the region where Mantrac's BUs are (Africa and the
Middle East), each country's outline as an SVG path in a fixed viewBox, its
ISO 3166 numeric id and name, and a label point (the centre of its largest
area). Which BU is which country is NOT decided here: dashboard/clearance.py
maps a BU code to a country only where the code is that country's ISO code.
"""
import json
import math
import sys
from pathlib import Path

LON0, LON1, LAT0, LAT1 = -20.0, 63.0, -14.0, 42.0      # the region
WIDTH = 820.0
KX = math.cos(math.radians(14.0))                       # equirectangular at ~14°N
HEIGHT = WIDTH * (LAT1 - LAT0) / ((LON1 - LON0) * KX)


def project(lon, lat):
    return ((lon - LON0) / (LON1 - LON0) * WIDTH, (LAT1 - lat) / (LAT1 - LAT0) * HEIGHT)


def decode(topo):
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
    return arcs


def ring(arcs, ids):
    out = []
    for i in ids:
        pts = arcs[i] if i >= 0 else list(reversed(arcs[~i]))
        out.extend(pts if not out else pts[1:])
    return out


def simplify(points, tol=1.6):
    keep = [points[0]]
    for p in points[1:]:
        if abs(p[0] - keep[-1][0]) + abs(p[1] - keep[-1][1]) >= tol:
            keep.append(p)
    return keep


def area_centroid(pts):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    if abs(a) < 1e-9:
        return abs(a), pts[0]
    return abs(a) / 2, (cx / (3 * a), cy / (3 * a))


def main(path):
    topo = json.loads(Path(path).read_text(encoding="utf-8"))
    arcs = decode(topo)
    countries = []
    for g in topo["objects"]["countries"]["geometries"]:
        if g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
        d, best = [], (0, None)
        inside = False
        for poly in polys:
            for k, ids in enumerate(poly):
                lonlat = ring(arcs, ids)
                if any(LON0 - 8 <= lo <= LON1 + 8 and LAT0 - 8 <= la <= LAT1 + 8 for lo, la in lonlat):
                    inside = True
                pts = simplify([project(lo, la) for lo, la in lonlat])
                if len(pts) < 3:
                    continue
                d.append("M" + "L".join("{0:.0f} {1:.0f}".format(x, y) for x, y in pts) + "Z")
                if k == 0:
                    size, c = area_centroid(pts)
                    if size > best[0]:
                        best = (size, c)
        if not inside or not d or best[1] is None:
            continue
        lx, ly = best[1]
        if not (-0.08 * WIDTH <= lx <= 1.08 * WIDTH and -0.08 * HEIGHT <= ly <= 1.08 * HEIGHT):
            continue                          # centred outside the map: not drawn
        countries.append({"id": str(g.get("id") or ""), "name": (g.get("properties") or {}).get("name", ""),
                          "d": "".join(d), "label": [round(best[1][0], 1), round(best[1][1], 1)]})
    out = {"source": "Natural Earth 1:50m (public domain) via world-atlas 2.0.2 (ISC)",
           "viewBox": [0, 0, round(WIDTH), round(HEIGHT)], "countries": countries}
    target = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "maps" / "region.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print("{0} countries, {1:,} bytes -> {2}".format(len(countries), target.stat().st_size, target))


if __name__ == "__main__":
    main(sys.argv[1])
