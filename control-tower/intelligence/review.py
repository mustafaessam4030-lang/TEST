"""
ATLAS review — the human side of the loop: read, decide, evaluate.

    python -m intelligence.review status            level, and what the next needs
    python -m intelligence.review review [YYYY-MM]  the monthly learning review
    python -m intelligence.review learned           what has been learned
    python -m intelligence.review proposals         proposals with their evidence
    python -m intelligence.review approve ID --by NAME
    python -m intelligence.review reject ID --by NAME
    python -m intelligence.review evaluate          evaluate every ended month not yet evaluated

Approving a proposal RECORDS a person's decision, with their name and the
time. It does not change the automation: the change itself still goes
through a tested, approved deployment.
"""

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from intelligence import learning, maturity, store            # noqa: E402
else:
    from . import learning, maturity, store


def _pct(x):
    return "{0:.0%}".format(x) if isinstance(x, (int, float)) else "no data"


def cmd_status(_args):
    st = maturity.status()
    print("ATLAS {0} {1}".format(st["stars"], st["title"]))
    print("Last evaluated: {0}".format(st["last_evaluated"] or "never"))
    if st["next_level"]:
        print("\nNext: {0} {1} (measured now, {2} — a preview, not an evaluation)".format(
            maturity.stars(st["next_level"]), st["next_title"], st["preview_month"]))
        for c in st["next_criteria"]:
            print("  {0} {1}: {2} (needs {3}){4}".format(
                "✓" if c["met"] else "✗", c["label"], maturity._fmt(c["value"]),
                maturity._fmt(c["minimum"]), " — " + c["why"] if c["why"] else ""))
    for h in st["history"]:
        print("  {0}: {1} -> {2}".format(h["month"], h["level_before"], h["level_after"]))
    return 0


def cmd_review(args):
    print(maturity.render_review(maturity.review(args.month)))
    return 0


def cmd_learned(_args):
    snap = learning.snapshot()
    s = snap["summary"]
    print("Events: {0} · runs: {1} · issues learned: {2}/{3} · verified signals: {4} "
          "({5} successes) · unverified (not counted): {6}".format(
              snap["events"], s["runs"], s["issues_learned"], s["issues_seen"],
              s["verified_signals"], s["verified_successes"], s["unverified"]))
    for issue in snap["issues"][:15]:
        print("\n{0} {1} — {2}x in {3} run(s), {4} resolved verified".format(
            issue["carrier"] or issue["provider"], issue["issue"], issue["occurrences"],
            len(issue["runs"]), issue["resolved_verified"]))
        for st in sorted(issue["strategies"].values(), key=lambda x: -x["wilson"]):
            print("   {0}: {1}/{2} verified ({3}), lower bound {4}, {5}; unverified {6}, "
                  "skipped {7}".format(st["name"], st["successes"], st["decided"],
                                       _pct(st["rate"]), _pct(st["wilson"]), st["confidence"],
                                       st["unverified"], st["skipped"]))
    return 0


def cmd_proposals(_args):
    props = learning.snapshot()["proposals"]
    if not props:
        print("No proposals: nothing in the verified record argues for a change yet.")
        return 0
    for p in props:
        b, c = p["evidence"]["best"], p["evidence"]["current"]
        print("[{0}] {1}  ({2})".format(p["id"], p["change"], p["status"]))
        print("   {0}: {1}/{2} verified, lower bound {3}; runs {4}".format(
            b["name"], b["successes"], b["decided"], _pct(b["wilson"]), ", ".join(b["runs"])))
        print("   {0}: {1}/{2} verified, lower bound {3}".format(
            c["name"], c["successes"], c["decided"], _pct(c["wilson"])))
        if p.get("decided_by"):
            print("   decided by {0} at {1}".format(p["decided_by"], p["decided_at"]))
    print("\nApproving records a decision. The change still needs a tested deployment.")
    return 0


def _decide(args, status):
    ids = {p["id"] for p in learning.snapshot()["proposals"]}
    if args.id not in ids:
        print("No current proposal with id {0}.".format(args.id))
        return 1
    if not args.by.strip():
        print("Say who decides: --by NAME")
        return 1
    learning.decide(args.id, status, args.by)
    print("{0} {1} by {2}. Recorded in {3}. Nothing in the automation changed.".format(
        args.id, status, args.by, store.folder() / learning.PROPOSALS_FILE))
    return 0


def cmd_evaluate(_args):
    done = maturity.ensure_evaluated()
    if not done:
        print("Nothing to evaluate: every ended month with recorded runs is already evaluated.")
    for d in done:
        print("{0}: {1} -> {2}{3}".format(d["month"], d["level_before"], d["level_after"],
                                         " (promoted)" if d["promoted"] else ""))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="intelligence.review", description=__doc__.split("\n")[1])
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status")
    r = sub.add_parser("review")
    r.add_argument("month", nargs="?")
    sub.add_parser("learned")
    sub.add_parser("proposals")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("id")
        p.add_argument("--by", required=True)
    sub.add_parser("evaluate")
    args = parser.parse_args(argv)
    handlers = {"status": cmd_status, "review": cmd_review, "learned": cmd_learned,
                "proposals": cmd_proposals, "evaluate": cmd_evaluate,
                "approve": lambda a: _decide(a, "APPROVED"),
                "reject": lambda a: _decide(a, "REJECTED")}
    if args.cmd not in handlers:
        parser.print_help()
        return 0
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
