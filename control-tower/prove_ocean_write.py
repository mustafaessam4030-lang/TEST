"""
Prove one ocean shipment end to end against the REAL eHub — run on the worker.

    python prove_ocean_write.py MEDUAHP69377

This is the automation's own main(): sign-in, the Under Clearance list, the
carrier lookup, the identity check, the date checks, the write to the COE
view, the reload and read-back. One restriction only: of the shipments
main() collects from the Hub list, the one named here is processed and the
others are left alone. Nothing is stubbed, so this WRITES the carrier's
date to that shipment in eHub, exactly as a normal run would.

Afterwards it prints, and saves beside the run log, the evidence:

    reference, carrier, the ETA/ATA the carrier gave and the label it came
    from, each Hub write (view, field, value), the value read back after the
    reload, and the verdict — VERIFIED SUCCESS only when the shipment ended
    SUCCESS and every value written was read back equal.

Nothing here prints or stores a credential.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_eta as A                                        # noqa: E402


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    wanted = argv[1].strip()
    evidence = {"proof": "ocean-write", "reference": wanted, "ehub": A.INTERNAL_URL,
                "machine_run_id": A.RUN_ID, "started": _now(), "pages_scanned": [],
                "found_on_page": None, "carrier_result": None, "writes": [],
                "read_backs": [], "ocean_write": A.OCEAN_WRITE,
                "verify_after_save": A.VERIFY_AFTER_SAVE, "dry_run": A.DRY_RUN}

    collect = A.collect_supported_shipments

    def only_this_one(page, table_page):
        rows = collect(page, table_page)
        evidence["pages_scanned"].append({"page": table_page, "rows": len(rows)})
        mine = [r for r in rows if _same(r.get("bol_awb"), wanted)
                or _same(r.get("tracking_reference"), wanted)]
        if mine and evidence["found_on_page"] is None:
            evidence["found_on_page"] = table_page
            return mine[:1]
        return []

    lookup = A.get_provider_result

    def recorded_lookup(pages, shipment):
        result = lookup(pages, shipment)
        evidence["carrier_result"] = {k: result.get(k) for k in (
            "provider", "tracking_status", "eta", "eta_source", "ata", "ata_source",
            "read_back_required") if k in result}
        evidence["carrier_result"]["at"] = _now()
        return result

    fill = A.fill_date_field

    def recorded_fill(page, field_name, value):
        evidence["writes"].append({"field": field_name, "value": value, "at": _now()})
        return fill(page, field_name, value)

    read_back = A.verify_saved_date

    def recorded_read_back(page, shipment, view_name, field_name, expected):
        verdict, detail = read_back(page, shipment, view_name, field_name, expected)
        evidence["read_backs"].append({
            "view": view_name, "field": field_name, "written": expected,
            "read_back": detail if verdict is True else None,
            "verdict": {True: "MATCH", False: "MISMATCH", None: "NOT READ"}[verdict],
            "detail": None if verdict is True else detail, "at": _now()})
        return verdict, detail

    A.collect_supported_shipments = only_this_one
    A.get_provider_result = recorded_lookup
    A.fill_date_field = recorded_fill
    A.verify_saved_date = recorded_read_back
    A.DASHBOARD_ENABLED = False          # a Control Tower already open keeps its port
    A.PAUSE_ON_FATAL_ERROR = False

    error = None
    try:
        A.main()
    except SystemExit:
        pass
    except Exception as failure:          # reported, never hidden
        error = "{0}: {1}".format(type(failure).__name__, str(failure)[:300])

    snap = A.tower.snapshot()
    record = next((r for r in snap.get("shipments") or []
                   if _same(r.get("reference"), wanted)), None)
    evidence.update(
        finished=_now(), run_error=error,
        shipment_outcome=(record or {}).get("state"),
        shipment_detail=(record or {}).get("error"),
        declared_failure=(record or {}).get("failure"),
        hub_actions={"coe": (record or {}).get("coe_action"),
                     "bu": (record or {}).get("bu_action")})
    written = {(w["field"], w["value"]) for w in evidence["writes"]}
    matched = {(r["field"], r["written"]) for r in evidence["read_backs"]
               if r["verdict"] == "MATCH" and r["read_back"] is not None}
    if record is None and error:
        verdict = "NOT RUN — the run stopped before {0} was reached: {1}".format(wanted, error)
    elif record is None:
        verdict = "NOT RUN — {0} was not in the Hub list the automation reads ({1} view, " \
                  "status {2}, pages scanned: {3})".format(
                      wanted, A.SOURCE_VIEW, A.TARGET_STATUS,
                      len(evidence["pages_scanned"]))
    elif evidence["dry_run"]:
        verdict = "NOT VERIFIED — a dry run saves nothing"
    elif record.get("state") == "updated" and written and written <= matched:
        verdict = "VERIFIED SUCCESS"
    else:
        verdict = "NOT VERIFIED — {0}".format(
            (record or {}).get("error") or "no value was written and read back")
    evidence["verdict"] = verdict

    out = Path(A.LOG_FILE).parent / "ocean-proof-{0}-{1:%Y%m%d-%H%M%S}.json".format(
        "".join(c for c in wanted if c.isalnum()), datetime.now())
    try:
        out.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
        evidence["saved_as"] = str(out)
    except Exception as failure:
        evidence["saved_as"] = "not saved: {0}".format(failure)
    print(json.dumps(evidence, indent=2, default=str))
    return 0 if verdict == "VERIFIED SUCCESS" else 1


def _same(a, b):
    def norm(value):
        return "".join(str(value or "").split()).replace("-", "").upper()
    return bool(a) and norm(a) == norm(b)


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
