"""
Read-only views for the Control Tower hub (CONTROL_TOWER_PLAN.md §2–4).

Nothing here writes, starts or decides anything: it reads the same stores the
existing pages read (the bridge snapshot of the run, the loaded eHub export)
and puts them in the shape the hub's pages draw. Every count is computed from
records here, once, so every page shows the same number.

    modules(can)        the navigation: every module, its real status
    stages(data)        the eHub export's stage strip (definitions in STAGES)
    overview(state,can) everything the Overview draws
"""

import json
import math
import re
from datetime import date
from pathlib import Path

from dashboard import clearance as _clearance

STATIC = Path(__file__).resolve().parent / "static"

# ── navigation ──────────────────────────────────────────────────────────────
# status: live = real data and working; partial = real data, view still the
# existing page; off = not connected (no data source) — shown, never faked.
MODULES = [
    {"id": "overview", "title": "Overview", "status": "live", "perm": "dashboard.view", "group": "Operations"},
    {"id": "tracking", "title": "Shipment Tracking", "status": "live", "perm": "dashboard.view", "group": "Operations",
     "note": "The eHub export and the run's record, joined by AWB/BOL"},
    # The existing pages, opened inside the hub exactly as they are (embed mode hides only their brand bar).
    {"id": "ata", "title": "ATA Automation", "status": "live", "perm": "dashboard.view", "group": "Operations",
     "embed": "/#overview", "note": "The run page: start, dry run, stop, live progress"},
    {"id": "runs", "title": "Run Shipments", "status": "live", "perm": "dashboard.view", "group": "Operations",
     "embed": "/#shipments", "note": "Every shipment in the run, with its carrier result and evidence"},
    {"id": "po", "title": "BOE Automation", "status": "live", "perm": "po.view", "group": "Operations",
     "embed": "/#po", "note": "Bill of Entry PDF to duty template, validated, sent"},
    {"id": "clearance", "title": "Clearance", "status": "live", "perm": "clearance.view", "group": "Operations",
     "embed": "/clearance", "note": "The eHub shipment export: escalations, contacts, mails"},
    {"id": "todo", "title": "To Do & Approvals", "status": "live", "perm": "dashboard.view", "group": "Operations"},
    {"id": "rates", "title": "Rate Management", "status": "off", "perm": "dashboard.view", "group": "Commercial",
     "note": "Not connected: no freight-rate source yet"},
    {"id": "tender", "title": "Spot Tender", "status": "off", "perm": "dashboard.view", "group": "Commercial",
     "note": "Not connected: no tender data yet"},
    {"id": "history", "title": "Run History", "status": "live", "perm": "runs.view", "group": "Insight",
     "embed": "/#analysis", "nav": False, "parent": "ata", "note": "Analysis, exceptions and the activity log (links only)"},
    {"id": "atlas", "title": "ATLAS", "status": "live", "perm": "atlas.chat", "group": "Insight", "embed": "/#atlas"},
    {"id": "access", "title": "Access", "status": "live", "perm": "users.manage", "group": "Admin", "embed": "/#access",
     "note": "Accounts and access levels"},
    # Opened from the top bar's gear icon, not the menu.
    {"id": "settings", "title": "Settings", "status": "live", "perm": "dashboard.view", "group": "Admin", "embed": "/#settings",
     "nav": False},
]


# Modules whose records (the run, the existing pages, To Do, PO documents,
# contacts) are not limited by business unit: hidden from a BU-limited person.
UNSCOPED = ("ata", "runs", "po", "clearance", "todo", "history", "atlas")


def modules(can):
    """The modules this person may open, each with its real status."""
    return [dict(m) for m in MODULES if can(m["perm"])]


# ── the stage strip (CONTROL_TOWER_PLAN.md §3) ──────────────────────────────
WITH_BU = ("Submit to BU", "Assign Clearing Agent", "Correction Required")
AT_CUSTOMS = ("Customs Pre Entry", "Customs Declaration", "Duty Assessment", "Duty Payment", "Customs Release")
AWAITING = ("Awaiting Reciept", "Awaiting Receipt")
STAGES = [
    ("transit", "In transit", "blue",
     "Open, no ATA, and an ETA on or after the export's data date."),
    ("arrived", "Arrived, with the BU", "amber",
     "Open, arrived (an ATA, or the ETA has passed), at Submit to BU, Assign Clearing Agent or Correction Required."),
    ("customs", "At customs", "amber",
     "Open, arrived, at a customs step: Pre Entry, Declaration, Duty Assessment, Duty Payment or Release."),
    ("awaiting", "Cleared, awaiting delivery", "teal",
     "Open, at Awaiting Receipt: customs released, the BU has not confirmed receipt."),
    ("done", "Completed", "green", "The eHub step is Completed."),
]


def scoped_rows(data, allowed=None):
    """The export's rows this person may see: every row, or only the BUs in
    `allowed` (a set of BU codes). Every hub number is counted from these,
    so a BU-restricted person never sees another BU in any total."""
    rows = _clearance.records(data)
    if allowed is None:
        return rows
    return [r for r in rows if r.get("bu") in allowed]


def _stage_of(r, asof):
    if not _clearance.is_open(r):
        return "done"
    eta = (r.get("eta") or "")[:10]
    if not r.get("ata") and eta and eta >= asof:
        return "transit"
    step = r.get("step") or ""
    if step in AWAITING:
        return "awaiting"
    if step in AT_CUSTOMS:
        return "customs"
    if step in WITH_BU:
        return "arrived"
    return "other"                       # open but not classifiable (no ETA, unknown step)


def stages(data=None, rows=None, allowed=None):
    """{asof, total, stages:[{id,label,tone,count,definition}], other, on_time, late} or None."""
    data = data or _clearance.load()
    if not data:
        return None
    rows = rows if rows is not None else scoped_rows(data, allowed)
    asof = (data.get("asof") or date.today().isoformat())[:10]
    counts = {}
    for r in rows:
        k = _stage_of(r, asof)
        counts[k] = counts.get(k, 0) + 1
    on = sum(1 for r in rows if r.get("ontime"))
    open_rows = [r for r in rows if _clearance.is_open(r)]
    return {"asof": data.get("asof"), "total": len(rows),
            "open": len(open_rows), "past_due": sum(1 for r in open_rows if _clearance.past_due(r)),
            "stages": [{"id": i, "label": l, "tone": t, "count": counts.get(i, 0), "definition": d}
                       for i, l, t, d in STAGES],
            "other": counts.get("other", 0), "on_time": on, "late": len(rows) - on,
            "source": "eHub Shipment Update export"}


LIST_FIELDS = ("awb", "bu", "mode", "step", "eta", "ata", "overdue", "ontime", "ffw", "agent", "status")


def stage_list(stage, bu=None, data=None, limit=None, allowed=None):
    """The export's shipments in one stage (or 'all', or 'late'), optionally of
    one BU: exactly the rows behind a number on the hub. -> {asof, total, rows}."""
    data = data or _clearance.load()
    if not data:
        return None
    asof = (data.get("asof") or date.today().isoformat())[:10]
    rows = []
    for r in scoped_rows(data, allowed):
        if bu and r.get("bu") != bu:
            continue
        if stage == "late":
            keep = not r.get("ontime")
        elif stage == "pastdue":
            keep = _clearance.is_open(r) and _clearance.past_due(r)
        elif stage == "all":
            keep = True
        else:
            keep = _stage_of(r, asof) == stage
        if keep:
            rows.append({k: r.get(k) for k in LIST_FIELDS})
    rows.sort(key=lambda r: -(r["overdue"] if isinstance(r.get("overdue"), (int, float)) else -10 ** 6))
    return {"asof": data.get("asof"), "stage": stage, "bu": bu, "total": len(rows), "rows": rows[:limit]}


# ── places on the map ───────────────────────────────────────────────────────
_CENTRES = {}


def _country_centres():
    """ISO alpha-2 -> (lat, lon): the centre of each country's largest outline
    in the map's own world file (the same outline the map draws)."""
    if _CENTRES:
        return _CENTRES
    try:
        world = json.loads((STATIC / "maps" / "world.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _CENTRES
    proj = world.get("projection") or {}
    width = float(proj.get("width") or 2000)
    top = math.radians(float(proj.get("lat_top") or 78))
    y_top = math.log(math.tan(math.pi / 4 + top / 2))
    scale = width / (2 * math.pi)
    for c in world.get("countries") or []:
        best, area = None, -1
        for ring in str(c.get("d") or "").split("Z"):
            pts = []
            for part in ring.replace("M", "L").split("L"):
                xy = part.strip().split()
                if len(xy) == 2:
                    try:
                        pts.append((float(xy[0]), float(xy[1])))
                    except ValueError:
                        pass
            if len(pts) < 3:
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            a = (max(xs) - min(xs)) * (max(ys) - min(ys))
            if a > area:
                best, area = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2), a
        if best and c.get("cc"):
            lon = best[0] / width * 360 - 180
            lat = math.degrees(2 * math.atan(math.exp(y_top - best[1] / scale)) - math.pi / 2)
            _CENTRES[c["cc"]] = (round(lat, 3), round(lon, 3))
    return _CENTRES


def bu_places(data=None, allowed=None, want_unplaced=False):
    """Per BU of the export: where it is (its country's centre), its counts
    by stage and its past-due. The BU is where a shipment clears: its country,
    never a position."""
    data = data or _clearance.load()
    if not data:
        return []
    asof = (data.get("asof") or date.today().isoformat())[:10]
    centres = _country_centres()
    per = {}
    for r in scoped_rows(data, allowed):
        bu = r.get("bu")
        if not bu:
            continue
        x = per.setdefault(bu, {"bu": bu, "n": 0, "open": 0, "late": 0, "stages": {}})
        x["n"] += 1
        k = _stage_of(r, asof)
        x["stages"][k] = x["stages"].get(k, 0) + 1
        if _clearance.is_open(r):
            x["open"] += 1
            if _clearance.past_due(r):
                x["late"] += 1
    out, unplaced = [], []
    for bu, x in per.items():
        iso_name = _clearance.BU_COUNTRY.get(bu)
        country = iso_name[1] if iso_name else None
        cc = bu if bu in centres else None
        if cc is None and country:
            cc = next((k for k, v in _country_names().items() if v == country), None)
        if not cc or cc not in centres:
            unplaced.append({"bu": bu, "n": x["n"], "open": x["open"], "late": x["late"]})
            continue                     # no country for this BU: not placed, never guessed
        lat, lon = centres[cc]
        out.append(dict(x, cc=cc, country=country or cc, lat=lat, lon=lon))
    out.sort(key=lambda b: (-b["late"], -b["n"]))
    if want_unplaced:
        return out, unplaced
    return out


_NAMES = {}


def _country_names():
    if not _NAMES:
        try:
            world = json.loads((STATIC / "maps" / "world.json").read_text(encoding="utf-8"))
            _NAMES.update({c["cc"]: c.get("name") for c in world.get("countries") or [] if c.get("cc")})
        except (OSError, ValueError):
            pass
    return _NAMES


def run_places(state):
    """The run's shipments the carrier placed: the last reported place of each
    (and its path), with when it was reported. Nothing without a place."""
    out = []
    for r in (state or {}).get("shipments") or []:
        w = r.get("where") or {}
        last = (w.get("last") or {}).get("pt")
        if not last or last.get("lat") is None:
            continue
        path = [p for p in (w.get("path") or []) if p.get("lat") is not None]
        dest = w.get("destination") if (w.get("destination") or {}).get("lat") is not None else None
        out.append({"ref": r.get("reference"), "carrier": r.get("carrier"), "state": r.get("state"),
                    "mode": r.get("transport_mode"), "eta": r.get("provider_eta"), "ata": r.get("provider_ata"),
                    "status": r.get("provider_status"), "source": w.get("source"),
                    "at": {"name": last.get("name"), "cc": last.get("cc"), "lat": last["lat"], "lon": last["lon"]},
                    "when": " ".join(x for x in ((w.get("last") or {}).get("date"), (w.get("last") or {}).get("time")) if x),
                    "event": (w.get("last") or {}).get("text"),
                    "path": [{"lat": p["lat"], "lon": p["lon"], "name": p.get("name")} for p in path],
                    "destination": {"name": dest.get("name"), "lat": dest["lat"], "lon": dest["lon"]} if dest else None})
    return out


def overview(state, can, allowed=None):
    """Everything the Overview draws, each part only for who may see it.
    `allowed`: the BU codes this person may see (None = all). The run's own
    records carry no BU, so a BU-restricted person does not get the run's
    figures or places at all (never another BU's shipments by accident)."""
    mods = modules(can)
    if allowed is not None:         # views whose records carry no BU are not for a BU-limited person
        mods = [m for m in mods if m["id"] not in UNSCOPED]
    out = {"modules": mods, "scope": sorted(allowed) if allowed is not None else None}
    if can("clearance.view"):
        data = _clearance.load()
        out["clearance"] = stages(data, allowed=allowed) if data else None
        out["bus"], out["unplaced"] = bu_places(data, allowed, want_unplaced=True) if data else ([], [])
    if allowed is not None:
        out["run_hidden"] = "The run's records carry no business unit, so they are not shown to a BU-restricted account."
        return out
    if can("runs.view") or can("dashboard.view"):
        run = (state or {}).get("run") or {}
        rows = (state or {}).get("shipments") or []
        by = {}
        for r in rows:
            by[r.get("state") or "unknown"] = by.get(r.get("state") or "unknown", 0) + 1
        out["run"] = {"id": run.get("run_id"), "status": (state or {}).get("status") or run.get("status"),
                      "dry_run": run.get("dry_run"), "shipments": len(rows), "by_state": by,
                      "counters": (state or {}).get("counters") or {}}
        out["places"] = run_places(state)
    return out


# ── one shipment: the export's row and the run's record, joined by AWB/BOL ───
# Each field carries where it came from; nothing is merged into a new value.
EXPORT_FIELDS = [
    ("bu", "Business unit", None), ("div", "Division", None), ("mode", "Mode", None),
    ("status", "eHub status", None), ("step", "Current step", None), ("next", "Next step", None),
    ("ffw", "Forwarder", None), ("agent", "Clearing agent", None),
    ("eta", "ETA", "date"), ("ata", "ATA", "date"), ("submitted", "Submitted to BU", "date"),
    ("due", "Step due (Expected Completion Date)", "date"), ("overdue", "Days past the step's due date", "days"),
    ("release", "Customs release", "date"), ("completed", "Completed", "date"),
    ("cycle", "Actual days", "days"), ("target", "Expected cycle days", "days"), ("ontime", "On time (eHub flag)", "bool"),
]
RUN_FIELDS = [
    ("carrier", "Carrier"), ("state", "Run state"), ("step", "Last step"), ("provider_status", "Carrier status"),
    ("provider_eta", "Carrier ETA"), ("provider_ata", "Carrier ATA"), ("internal_eta", "Hub ETA before the run"),
    ("outcome", "Outcome"), ("error", "Error"), ("updated", "Updated"),
]


def shipment(ref, state=None, can=lambda p: True, allowed=None, data=None):
    """-> {ref, export: {rows, source}, run: {record, source} | None, timeline}; None when nothing matches."""
    key = _clearance.norm_awb(ref)
    if not key or len(key) < 4:
        return None
    out = {"ref": ref, "export": None, "run": None, "timeline": []}
    if can("clearance.view"):
        data = data or _clearance.load()
        if data:
            rows = [r for r in scoped_rows(data, allowed) if _clearance.norm_awb(r.get("awb")) == key]
            if rows:
                src = "eHub export, data as of {0}".format(data.get("asof"))
                out["export"] = {"source": src, "rows": [
                    {"awb": r.get("awb"), "fields": [{"key": k, "label": label, "value": r.get(k), "kind": kind}
                                                     for k, label, kind in EXPORT_FIELDS],
                     "achieved": r.get("ach")} for r in rows]}
                r = rows[0]
                for k, label in (("eta", "ETA (eHub)"), ("ata", "ATA (eHub)"), ("submitted", "Submitted to BU"),
                                 ("due", "Current step due"), ("release", "Customs released"), ("completed", "Completed")):
                    if r.get(k):
                        out["timeline"].append({"date": r[k], "label": label, "source": src})
    if allowed is None and (can("runs.view") or can("dashboard.view")):
        for rec in (state or {}).get("shipments") or []:
            if _clearance.norm_awb(rec.get("reference")) != key:
                continue
            run = (state or {}).get("run") or {}
            src = "ATA run {0}{1}".format(run.get("run_id") or "", " (dry run)" if run.get("dry_run") else "")
            out["run"] = {"source": src, "fields": [{"key": k, "label": label, "value": rec.get(k)} for k, label in RUN_FIELDS],
                          "where": rec.get("where")}
            for k, label in (("provider_eta", "ETA (carrier)"), ("provider_ata", "ATA (carrier)")):
                if rec.get(k):
                    out["timeline"].append({"date": str(rec[k]), "label": label,
                                            "source": "{0}, {1}".format(rec.get("carrier") or "carrier", src)})
            last = ((rec.get("where") or {}).get("last") or {})
            if last.get("text"):
                out["timeline"].append({"date": " ".join(str(x) for x in (last.get("date"), last.get("time")) if x),
                                        "label": "Carrier event: " + str(last.get("text"))[:80],
                                        "source": (rec.get("where") or {}).get("source") or "carrier timeline"})
            break
    if not out["export"] and not out["run"]:
        return None
    # ISO dates in order; a carrier's own date text (03/10/2026 could be either way round) is
    # never reinterpreted: it follows the dated events, as the carrier wrote it.
    iso = re.compile(r"^\d{4}-\d\d-\d\d")
    out["timeline"].sort(key=lambda t: (0, str(t["date"])) if iso.match(str(t["date"])) else (1, ""))
    return out
