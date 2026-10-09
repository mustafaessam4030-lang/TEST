"""
Carrier health — per carrier, from the shipment outcomes the runs recorded.

Source: the `shipment` events in the ATLAS event store (events.jsonl), one per
shipment when it finished, written by the dashboard bridge on production runs.
Each carries the carrier, the result, whether the carrier gave dates, whether
the Hub write was read back and matched (`verified`), whether a person was
needed, how long it took and, from 9 Oct 2026, the failure category.

Nothing is estimated. A rate is given only when at least MIN_SAMPLE shipments
back it; below that the carrier is listed with its raw counts and
"insufficient data". A trend is given only when BOTH windows reach the
minimum. A carrier with no records is not listed at all — no record is not a
0% success rate.

    python -m intelligence.carrier_health
"""

import os
import statistics
import sys
import time

from . import events

FINAL = ("SUCCESS", "SKIPPED", "FAILED", "PARTIAL", "HUMAN_TIMEOUT")


def _min_sample():
    try:
        return max(1, int(os.environ.get("ATLAS_HEALTH_MIN_SAMPLE") or 5))
    except ValueError:
        return 5


def _rate(part, whole, minimum):
    return round(part / float(whole), 3) if whole >= minimum else None


def _summarise(rows, minimum):
    n = len(rows)
    durations = [r["duration_ms"] for r in rows if isinstance(r.get("duration_ms"), (int, float))]
    causes = {}
    for r in rows:
        if r.get("result") in ("FAILED", "SKIPPED", "PARTIAL", "HUMAN_TIMEOUT"):
            key = r.get("failure_category") or r.get("outcome_class") or r.get("result")
            causes[key] = causes.get(key, 0) + 1
    counts = {
        "shipments": n,
        "dates_read": sum(1 for r in rows if r.get("extracted")),
        "verified_updates": sum(1 for r in rows if r.get("verified") is True),
        "failed": sum(1 for r in rows if r.get("result") == "FAILED"),
        "no_result": sum(1 for r in rows if "NO RESULT" in str(r.get("outcome_class") or "").upper()),
        "needed_person": sum(1 for r in rows if r.get("human_step")),
    }
    return {
        "counts": counts,
        "rates": {
            "dates_read": _rate(counts["dates_read"], n, minimum),
            "verified_updates": _rate(counts["verified_updates"], n, minimum),
            "failed": _rate(counts["failed"], n, minimum),
            "needed_person": _rate(counts["needed_person"], n, minimum),
        },
        "median_seconds": (round(statistics.median(durations) / 1000.0, 1)
                           if len(durations) >= minimum else None),
        "top_causes": sorted(causes.items(), key=lambda kv: -kv[1])[:3],
        "sufficient": n >= minimum,
    }


def health(rows=None, now=None, window_days=7):
    """-> {"carriers": [...], "window_days", "minimum", "records", "since"}"""
    rows = events.all_events(("shipment",)) if rows is None else rows
    rows = [r for r in rows if r.get("kind", "shipment") == "shipment"
            and str(r.get("result") or "").upper() in FINAL]
    now = time.time() if now is None else now
    minimum = _min_sample()
    span = window_days * 86400
    current = [r for r in rows if (r.get("epoch") or 0) >= now - span]
    previous = [r for r in rows if now - 2 * span <= (r.get("epoch") or 0) < now - span]
    names = sorted({r.get("provider") or r.get("carrier") or "Unknown" for r in current})
    carriers = []
    for name in names:
        mine = [r for r in current if (r.get("provider") or r.get("carrier") or "Unknown") == name]
        before = [r for r in previous if (r.get("provider") or r.get("carrier") or "Unknown") == name]
        entry = dict(_summarise(mine, minimum), carrier=name,
                     label=next((r.get("carrier") for r in mine if r.get("carrier")), name))
        earlier = _summarise(before, minimum)
        if entry["sufficient"] and earlier["sufficient"]:
            entry["trend_verified"] = round(entry["rates"]["verified_updates"]
                                            - earlier["rates"]["verified_updates"], 3)
            entry["previous_shipments"] = len(before)
        else:
            entry["trend_verified"] = None
        carriers.append(entry)
    carriers.sort(key=lambda c: (not c["sufficient"], -(c["counts"]["failed"]),
                                 -c["counts"]["shipments"]))
    return {"carriers": carriers, "window_days": window_days, "minimum": minimum,
            "records": len(current), "all_records": len(rows),
            "since": min((r.get("at") for r in rows if r.get("at")), default=None)}


def render(result):
    if not result["records"]:
        return ("Carrier health: no finished shipments recorded in the last {0} days "
                "({1} in the store overall). Nothing to measure yet.".format(
                    result["window_days"], result["all_records"]))
    lines = ["Carrier health — last {0} days, {1} finished shipments "
             "(rates need {2}+ shipments)".format(
                 result["window_days"], result["records"], result["minimum"])]
    for c in result["carriers"]:
        k = c["counts"]
        if not c["sufficient"]:
            lines.append("  {0}: {1} shipment(s) — insufficient data for rates; "
                         "{2} with dates, {3} verified, {4} failed".format(
                             c["label"], k["shipments"], k["dates_read"],
                             k["verified_updates"], k["failed"]))
            continue
        r = c["rates"]
        trend = ("" if c["trend_verified"] is None else
                 "; verified {0:+.0%} vs the week before ({1} shipments)".format(
                     c["trend_verified"], c["previous_shipments"]))
        causes = ", ".join("{0} x{1}".format(k2, v) for k2, v in c["top_causes"])
        lines.append("  {0}: {1} shipments — dates {2:.0%}, verified in Hub {3:.0%}, "
                     "failed {4:.0%}, needed a person {5:.0%}{6}{7}{8}".format(
                         c["label"], k["shipments"], r["dates_read"], r["verified_updates"],
                         r["failed"], r["needed_person"],
                         "; median {0}s".format(c["median_seconds"])
                         if c["median_seconds"] is not None else "",
                         trend, "; top causes: " + causes if causes else ""))
    return "\n".join(lines)


def main():
    print(render(health()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
