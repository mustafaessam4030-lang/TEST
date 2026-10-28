"""
Search (dashboard/search.py): an AWB, BOL or PO reference found wherever it
is, without spaces or dashes mattering, each result opening where it lives,
and only from what the person may open. Plus the console opening one
shipment or PO job from a link. No browser, no network.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="search_")
os.environ.update({"ATA_RUNTIME_DIR": TMP, "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"), "PO_DATA_DIR": os.path.join(TMP, "po")})
from dashboard import clearance, search                       # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


def row(**kw):
    base = dict(bu="EG", div="Spare Parts", mode="Air", ffw="FFW", agent="AGENT", status="Under Clearance",
                step="Submit to BU", eta="2026-09-01", ata=None, ontime=0, overdue=None, awb="X", ach="---------",
                cycle=None, target=None, due=None, next=None, submitted=None, release=None, completed=None)
    base.update(kw)
    return [base[k] for k in clearance.FIELDS]


clearance.data_path().write_text(json.dumps({"asof": "2026-10-20", "fields": clearance.FIELDS, "dq": {}, "count": 3,
    "rows": [row(awb="077-59534484", overdue=12), row(awb="MEDUW6936811", bu="GH", mode="Ocean", step="Completed"),
             row(awb="<script>-123")]}))
STATE = {"shipments": [{"reference": "157-49568713", "carrier": "Qatar Airways Cargo", "state": "updated",
                        "eta": "2026-10-30"}, {"reference": "MEDU1234567", "state": "failed"}]}
everyone = lambda _p: True                                     # noqa: E731

print("1. FOUND WHEREVER IT IS")
r = search.search("157 4956 8713", everyone, STATE)
g = {x["source"]: x["items"] for x in r["groups"]}
check("a run shipment, spaces ignored, with carrier, state and ETA",
      g.get("Shipments (this run)", [{}])[0].get("title") == "157-49568713" and
      "Qatar Airways Cargo · updated · ETA 2026-10-30" == g["Shipments (this run)"][0]["detail"], r)
check("…opening its detail in Shipments", g["Shipments (this run)"][0]["href"] == "/#shipments?ref=157-49568713")
r = search.search("0775953", everyone, STATE)
item = r["groups"][0]["items"][0]
check("a Clearance shipment without its dash, with BU, mode, step and days past due",
      r["groups"][0]["source"] == "Clearance (eHub export)" and item["title"] == "077-59534484"
      and item["detail"] == "EG · Air · Submit to BU · 12 days past due", r)
check("…opening it on Clearance", item["href"] == "/clearance#q=077-59534484")
r = search.search("meduw", everyone, STATE)
check("case does not matter; a completed one says so",
      r["groups"] and r["groups"][0]["items"][0]["detail"].endswith("completed"), r)
check("one query, matches from several places", len(search.search("MEDU", everyone, STATE)["groups"]) == 2)
check("fewer than 3 characters: not searched", search.search("1-5", everyone, STATE) == {
    "query": "1-5", "groups": [], "searched": False})
check("nothing found: an empty list, said as searched", search.search("ZZZ999", everyone, STATE)["groups"] == []
      and search.search("ZZZ999", everyone, STATE)["searched"] is True)
odd = search.search("script", everyone, STATE)["groups"][0]["items"][0]
check("a reference is returned as text (the page escapes it); its link is encoded",
      odd["title"] == "<script>-123" and odd["href"] == "/clearance#q=%3Cscript%3E-123", odd)
check("every link stays on this site", all(i["href"].startswith("/") and not i["href"].startswith("//")
                                          for q in ("157", "077", "MEDU") for x in search.search(q, everyone, STATE)["groups"]
                                          for i in x["items"]))

print("\n2. ONLY WHAT THE PERSON MAY OPEN")
r = search.search("MEDU", lambda p: p == "dashboard.view", STATE)
check("without clearance.view: run results only", [x["source"] for x in r["groups"]] == ["Shipments (this run)"], r)
r = search.search("MEDU", lambda p: p == "clearance.view", STATE)
check("without dashboard.view: no run results", [x["source"] for x in r["groups"]] == ["Clearance (eHub export)"], r)

print("\n3. THE PAGES")
idx = open(os.path.join(HERE, "dashboard", "static", "index.html"), encoding="utf-8").read()
check("the console opens one shipment or PO job from #shipments?ref= / #po?po=",
      "params.get('ref')" in idx and "openDrawer(ref)" in idx and "/^[0-9a-z-]{1,40}$/.test(po)" in idx)
for page in ("index.html", "clearance.html"):
    html = open(os.path.join(HERE, "dashboard", "static", page), encoding="utf-8").read()
    check("{0}: the search button in the top bar".format(page),
          "<span data-search-slot></span>" in html and '<script src="/static/search.js" defer></script>' in html)
js = open(os.path.join(HERE, "dashboard", "static", "search.js"), encoding="utf-8").read()
check("search.js escapes every value and follows only same-site links",
      "${esc(i.title)}" in js and "${esc(i.detail)}" in js and "/^\\/(?!\\/)/.test" in js)
check("its shortcut never takes a key the Human Action live view is forwarding, nor opens over a dialog",
      "e.defaultPrevented" in js and 'closest("#rv, #rvStage, [data-own-keys]")' in js and "overlayOpen()" in js
      and "#rv:not([hidden])" in js)
check("Escape closes the panel and nothing else (captured first)", js.count('}, true);') == 1 and "e.stopPropagation(); open(false)" in js)
check("a search that did not run is said so, never 'nothing found'", "Search could not run (HTTP ${r.status})" in js)
check("a result where the page already is still opens (same hash)", "new HashChangeEvent(\"hashchange\")" in js)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
