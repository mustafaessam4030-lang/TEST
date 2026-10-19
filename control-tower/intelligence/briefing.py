"""
The morning briefing — what the last 24 hours of runs did, what broke, and
the one thing to do first.

Built only from recorded evidence:

    shipment outcomes   events.jsonl `shipment` events (the bridge, production
                        runs): result, verified Hub write, needed a person
    carrier pages       carrier_pages.jsonl (intelligence/pagecheck.py): why a
                        tracking box was missing, and whether that is a site
                        change and how sure
    carrier health      intelligence/carrier_health.py over the last 7 days

Every line says what it counts. With nothing recorded it says exactly that
and recommends nothing. The recommended action is chosen by fixed rules, in
this order: a CONFIRMED or LIKELY site change; the carrier with the most
failures (when its sample is big enough); shipments that needed a person;
otherwise "no action needed".

    python -m intelligence.briefing
"""

import sys
import time

from . import carrier_health, events, pagecheck

DAY = 86400


def build(now=None, shipment_rows=None, page_rows=None):
    now = time.time() if now is None else now
    rows = events.all_events(("shipment",)) if shipment_rows is None else shipment_rows
    rows = [r for r in rows if str(r.get("result") or "").upper() in carrier_health.FINAL]
    recent = [r for r in rows if (r.get("epoch") or 0) >= now - DAY]
    pages = pagecheck.assess(page_rows)
    health = carrier_health.health(rows, now=now)
    lines, sources = [], []

    if not recent:
        lines.append("No shipments finished in the last 24 hours ({0} recorded in "
                     "total).".format(len(rows)))
    else:
        runs = sorted({r.get("run_id") for r in recent if r.get("run_id")})
        verified = sum(1 for r in recent if r.get("verified") is True)
        lines.append(
            "Last 24 hours: {0} shipments in {1} run(s) — {2} updated and verified in "
            "the Hub, {3} skipped, {4} failed, {5} needed a person.".format(
                len(recent), len(runs) or 1, verified,
                sum(1 for r in recent if r.get("result") == "SKIPPED"),
                sum(1 for r in recent if r.get("result") == "FAILED"),
                sum(1 for r in recent if r.get("human_step"))))
        sources.append("{0} shipment outcome records".format(len(recent)))

    changes = [f for f in pages if f["status"] == "SITE_CHANGE"]
    ordinary = [f for f in pages if f["status"] == "NOT_A_SITE_CHANGE"]
    for f in changes:
        lines.append(pagecheck.describe(f))
        if f.get("evidence"):
            lines.append("    evidence: " + ", ".join(f["evidence"][:2]))
    for f in ordinary:
        lines.append(pagecheck.describe(f))
    if pages:
        sources.append("{0} carrier page check(s)".format(len(pages)))

    troubled = [c for c in health["carriers"] if c["sufficient"] and c["counts"]["failed"]]
    thin = [c for c in health["carriers"] if not c["sufficient"]]
    for c in troubled[:3]:
        cause = ", ".join("{0} x{1}".format(k, v) for k, v in c["top_causes"][:2])
        lines.append("{0}: {1} of {2} shipments failed this week{3}.".format(
            c["label"], c["counts"]["failed"], c["counts"]["shipments"],
            " ({0})".format(cause) if cause else ""))
    if thin:
        lines.append("Too few shipments to judge this week: {0}.".format(
            ", ".join("{0} ({1})".format(c["label"], c["counts"]["shipments"]) for c in thin)))
    if health["records"]:
        sources.append("{0} shipment records for carrier health (7 days)".format(
            health["records"]))

    # ONE action, by fixed priority.
    firm = [f for f in changes if f["confidence"] in ("CONFIRMED", "LIKELY")]
    person = sum(1 for r in recent if r.get("human_step"))
    if firm:
        f = firm[0]
        action = ("Check {0}'s tracking page: site change {1} ({2}). The saved page "
                  "text and screenshot show what the run saw.".format(
                      f["label"], f["confidence"],
                      "its address now answers 'not found'" if f["cause"] == "PAGE_MOVED"
                      else "the tracking box is gone from a page that loads normally"))
    elif troubled:
        c = troubled[0]
        action = "Look at {0}: {1} of {2} shipments failed this week{3}.".format(
            c["label"], c["counts"]["failed"], c["counts"]["shipments"],
            " — mostly {0}".format(c["top_causes"][0][0]) if c["top_causes"] else "")
    elif person:
        action = ("{0} shipment(s) needed a person in the last 24 hours — clear the "
                  "Human Action queue.".format(person))
    elif recent:
        action = "No action needed."
    else:
        action = "Nothing to act on: no recorded runs in the last 24 hours."
    return {"lines": lines, "action": action, "sources": sources,
            "site_changes": changes, "health": health,
            "generated_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(now))}


def render(result):
    out = ["ATLAS morning briefing — {0}".format(result["generated_at"]), ""]
    out += ["- " + line if not line.startswith("    ") else line for line in result["lines"]]
    out += ["", "First thing to do: " + result["action"]]
    out += ["", "Built from: " + ("; ".join(result["sources"]) if result["sources"]
                                  else "no records — nothing was estimated")]
    return "\n".join(out)


def main():
    print(render(build()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
