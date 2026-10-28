"""
Build dashboard/static/maps/world.json: the world outline the Tracking map
draws its shipments on.

Source: Natural Earth 1:50m countries (public domain) as packaged by
world-atlas 2.0.2 (ISC licence), countries-50m.json, and GeoNames
countryInfo.txt (CC BY 4.0) for each country's ISO 3166 alpha-2 code.
Download both once and pass the folder; nothing is fetched here.

    python tools/build_world_map.py DIR

Web Mercator, cut at 78°N and 56°S (no Antarctica), WIDTH units wide. The
page projects a place with the same formula (static/trackmap.js), so a
point and the outline under it always agree.
"""
import json
import math
import sys
from pathlib import Path

WIDTH = 2000.0
LAT_TOP, LAT_BOTTOM = 78.0, -56.0


def merc_y(lat):
    lat = max(min(lat, 85.0), -85.0)
    return WIDTH / (2 * math.pi) * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


Y0 = merc_y(LAT_TOP)
HEIGHT = Y0 - merc_y(LAT_BOTTOM)


def project(lon, lat):
    return ((lon + 180.0) / 360.0 * WIDTH, Y0 - merc_y(lat))


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


def simplify(points, tol):
    """Douglas-Peucker: drops a point only when the outline stays within
    `tol` map units of where it was."""
    if len(points) < 4:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = points[a], points[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-9
        far, at = 0.0, None
        for i in range(a + 1, b):
            px, py = points[i]
            dist = abs(dy * (px - ax) - dx * (py - ay)) / norm if (dx or dy) else math.hypot(px - ax, py - ay)
            if dist > far:
                far, at = dist, i
        if at is not None and far > tol:
            keep[at] = True
            stack += [(a, at), (at, b)]
    return [p for p, k in zip(points, keep) if k]


def area_centroid(pts):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    if abs(a) < 1e-9:
        return 0.0, pts[0]
    return abs(a) / 2, (cx / (3 * a), cy / (3 * a))


def iso2_by_number(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("\t")
        out[f[2]] = f[0]
    return out


def main(folder):
    d = Path(folder)
    topo = json.loads((d / "countries-50m.json").read_text(encoding="utf-8"))
    iso2 = iso2_by_number(d / "countryInfo.txt")
    arcs = decode(topo)
    countries = []
    for g in topo["objects"]["countries"]["geometries"]:
        if g.get("type") not in ("Polygon", "MultiPolygon") or g.get("id") == "010":
            continue                                   # 010: Antarctica
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]]
        path, best = [], (0, None)
        for poly in polys:
            for k, ids in enumerate(poly):
                pts = [project(lo, max(min(la, LAT_TOP + 4), LAT_BOTTOM - 4))
                       for lo, la in ring(arcs, ids)]
                pts = simplify(pts, 0.35)
                if len(pts) < 3:
                    continue
                size, c = area_centroid(pts)
                if size < 1.2 and k == 0 and len(polys) > 1:
                    continue                           # specks of islands
                path.append("M" + "L".join("{0:.1f} {1:.1f}".format(x, y) for x, y in pts) + "Z")
                if k == 0 and size > best[0]:
                    best = (size, c)
        if not path or best[1] is None:
            continue
        countries.append({"cc": iso2.get(str(g.get("id") or ""), ""),
                          "name": (g.get("properties") or {}).get("name", ""),
                          "d": "".join(path), "label": [round(best[1][0], 1), round(best[1][1], 1)]})
    grid = []
    for lon in range(-180, 181, 30):
        x = project(lon, 0)[0]
        grid.append("M{0:.1f} 0V{1:.1f}".format(x, HEIGHT))
    for lat in (-30, 0, 30, 60):
        y = project(0, lat)[1]
        grid.append("M0 {0:.1f}H{1:.1f}".format(y, WIDTH))
    out = {"source": "Natural Earth 1:50m (public domain) via world-atlas 2.0.2 (ISC); "
                     "ISO codes from GeoNames (CC BY 4.0)",
           "projection": {"type": "mercator", "width": WIDTH, "lat_top": LAT_TOP,
                          "lat_bottom": LAT_BOTTOM},
           "viewBox": [0, 0, round(WIDTH), round(HEIGHT, 1)], "graticule": "".join(grid),
           "countries": countries}
    target = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "maps" / "world.json"
    target.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print("{0} countries, {1:,} bytes -> {2}".format(len(countries), target.stat().st_size, target))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
