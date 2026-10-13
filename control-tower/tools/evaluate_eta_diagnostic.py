"""
Offline, repeatable evaluation of ETA/ATA decisions on the sanitized pages.

Ground truth: fixtures/eta_ata_truth.json, made by tools/label_eta_ata_truth.py
(an independent reader of the raw page lines, NOT arrival_events or ATLAS)
plus hand-checked labels. Cases the page cannot settle are UNRESOLVED and are
scored only on abstention.

Systems compared, each on the same pages:
  original   what the 12 Oct 2026 run wrote (from the run log; DHL cases only)
  baseline   the deterministic rules in production now (arrival_events for
             DHL; the current Qatar and label readers for airlines)
  atlas      intelligence/eta_diagnostic.py (advisory)

    python tools/evaluate_eta_diagnostic.py [--out DIR]
No network, no Hub, no carrier.
"""
import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = ROOT / "fixtures"
TODAY = date(2026, 10, 12)

import arrival_events as E                                     # noqa: E402
from intelligence import eta_diagnostic as D                    # noqa: E402

ACCEPT, REVIEW, NONE = D.ACCEPT, D.REVIEW, D.NONE


class _Page(object):
    def __init__(self, text):
        self.text = text

    def locator(self, _s):
        t = self.text

        class L(object):
            def inner_text(self, timeout=None):
                return t
        return L()

    def get_by_text(self, *a, **k):
        class N(object):
            def count(self):
                return 0
        return N()


def _baseline_airline(key, text):
    import update_eta as A
    A.write_log = lambda *a, **k: None
    A._page_text = lambda page: page.text
    if key.startswith("QATAR"):
        r = A.extract_qatar_result(_Page(text)) or {}
    else:
        A.PORTALS.setdefault("ETHIOPIAN", {"label": "Ethiopian Airlines"})
        r = A._read_generic_portal_page(_Page(text), "ETHIOPIAN") or {}
    if r.get("review"):
        return {"decision": REVIEW, "eta": None, "ata": None}
    if r.get("eta") or r.get("ata"):
        return {"decision": ACCEPT, "eta": r.get("eta"), "ata": r.get("ata")}
    return {"decision": NONE, "eta": None, "ata": None}


def predictions(key, truth, manifest):
    text = (FIX / truth["fixture"]).read_text(encoding="utf-8")
    ref = key if key.startswith("OCT12-") else None
    out = {}
    if key in manifest:
        m = manifest[key]
        out["original"] = {"decision": ACCEPT, "eta": None if m["old_eta"] == "None" else m["old_eta"],
                           "ata": None if m["old_ata"] == "None" else m["old_ata"]}
    if truth["carrier"] == "DHL":
        events, _p, _d = E.dhl_events(text, ref)
        d = E.decide(events, today=TODAY)
        out["baseline"] = {"decision": REVIEW if d["review"] else (ACCEPT if d["eta"] or d["ata"]
                                                                   else NONE),
                           "eta": d["eta"], "ata": d["ata"]}
    else:
        out["baseline"] = _baseline_airline(key, text)
    diag = D.diagnose(text, ref, truth["carrier"], today=TODAY)
    acc = diag["recommendation"] == ACCEPT
    out["atlas"] = {"decision": diag["recommendation"],
                    "eta": (diag["eta_candidate"] or {}).get("date") if acc else None,
                    "ata": (diag["ata_candidate"] or {}).get("date") if acc else None,
                    "destination": diag["destination_match"]["value"],
                    "why": diag["conflicts"] + diag["missing"] + diag["uncertainty"]}
    return out


def truth_class(t):
    if t["status"] == "UNRESOLVED":
        return "UNRESOLVED"
    return "ATA" if t["ata"] else ("ETA" if t["eta"] else "NO_EVENT")


def score(cases, system):
    """
    Per field (ETA, ATA), on page-resolved cases:
      tp         a date proposed and it is the true date
      wrong      a date proposed that is not the true date
      missed     the truth has a date, nothing proposed although answering
      abstained  the truth has a date, the system abstained (review)
      true_none  the truth has no date and none was proposed
    Unresolved cases are scored only on abstention. Destination evidence is
    split: explicit (station / country printed on the page) vs carrier-label
    (the carrier filed it as the arrival; not independent evidence).
    """
    s = {"eta": Counter(), "ata": Counter(), "destination": Counter(),
         "abstention_on_unresolved": Counter(), "confusion": Counter(), "mismatches": []}
    for key, (t, p) in cases.items():
        if system not in p:
            continue
        pr = p[system]
        cls = truth_class(t)
        s["confusion"][(cls, pr["decision"])] += 1
        if cls == "UNRESOLVED":
            s["abstention_on_unresolved"]["abstained" if pr["decision"] != ACCEPT
                                          else "answered"] += 1
            if pr["decision"] == ACCEPT:
                s["mismatches"].append({"case": key, "issue": "answered an unresolved case",
                                        "eta": pr["eta"], "ata": pr["ata"],
                                        "truth_why": t.get("why")})
            continue
        for field in ("eta", "ata"):
            truth_value = t[field]
            got = pr[field] if pr["decision"] == ACCEPT else None
            if got and got == truth_value:
                s[field]["tp"] += 1
            elif got:
                s[field]["wrong"] += 1
                s["mismatches"].append({"case": key, "issue": field.upper() + " wrong",
                                        "predicted": got, "truth": truth_value,
                                        "evidence": t.get("why") or t["fixture"]})
            elif truth_value:
                s[field]["abstained" if pr["decision"] != ACCEPT else "missed"] += 1
                if pr["decision"] == ACCEPT:
                    s["mismatches"].append({"case": key, "issue": field.upper() + " missed",
                                            "truth": truth_value})
            else:
                s[field]["true_none"] += 1
        if t.get("ata"):
            if system == "atlas":
                v = pr.get("destination")
                s["destination"][("explicit" if v and v.startswith("explicit") else
                                  "carrier-label only" if v else "none")] += 1
            else:
                s["destination"]["not reported"] += 1
    return s


def as_rate(c):
    n = sum(c.values())
    parts = ["{0} {1}".format(v, k) for k, v in sorted(c.items())]
    return "{0} (n={1})".format(", ".join(parts) or "-", n)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    truth = json.loads((FIX / "eta_ata_truth.json").read_text(encoding="utf-8"))
    manifest = json.loads((FIX / "dhl_oct12" / "manifest.json").read_text(encoding="utf-8"))
    cases = {k: (t, predictions(k, t, manifest)) for k, t in sorted(truth.items())}
    report = {}
    lines = ["# ETA/ATA offline evaluation", "",
             "{0} cases ({1} unresolved by the page). Truth: independent labeller + {2} "
             "hand-checked labels.".format(len(cases), sum(1 for t, _ in cases.values()
                                                            if t["status"] == "UNRESOLVED"),
                                           sum(1 for t, _ in cases.values() if t.get("reviewed"))),
             ""]
    reviewed = {k: v for k, v in cases.items() if v[0].get("reviewed")}
    for label, subset in (("all", cases), ("hand-checked only", reviewed)):
      for system in ("original", "baseline", "atlas"):
        s = score(subset, system)
        if label != "all":
            lines += ["## {0} ({1}, n={2})".format(system, label, len(subset)), "",
                      "- ETA: " + as_rate(s["eta"]), "- ATA: " + as_rate(s["ata"]),
                      "- Abstention on unresolved: " + as_rate(s["abstention_on_unresolved"]), ""]
            report.setdefault("hand_checked", {})[system] = {
                "eta": dict(s["eta"]), "ata": dict(s["ata"]),
                "abstention_on_unresolved": dict(s["abstention_on_unresolved"])}
            continue
        report[system] = {k: (dict(v) if isinstance(v, Counter) else v) for k, v in s.items()}
        report[system]["confusion"] = {"{0} -> {1}".format(*k): v for k, v in s["confusion"].items()}
        lines += ["## " + system, "",
                  "- ETA: " + as_rate(s["eta"]),
                  "- ATA: " + as_rate(s["ata"]),
                  "- Destination evidence for true arrivals: " + as_rate(s["destination"]),
                  "- Abstention on unresolved cases: " + as_rate(s["abstention_on_unresolved"]),
                  "- Truth -> decision: " + ", ".join("{0}: {1}".format(k, v) for k, v in
                                                      sorted(report[system]["confusion"].items())),
                  "- Mismatches: {0}".format(len(s["mismatches"]))]
        for m in s["mismatches"]:
            lines.append("  - " + json.dumps(m, ensure_ascii=False))
        lines.append("")
    diffs = [(k, p["baseline"]["decision"], p["atlas"]["decision"], p["atlas"]["why"])
             for k, (t, p) in cases.items() if p["baseline"]["decision"] != p["atlas"]["decision"]]
    lines += ["## Where ATLAS and the baseline differ ({0})".format(len(diffs)), ""]
    for k, b, a, why in diffs:
        lines.append("- {0}: baseline {1}, ATLAS {2} - {3}".format(k, b, a, "; ".join(why)[:300]))
    report["differences"] = [{"case": k, "baseline": b, "atlas": a, "why": w} for k, b, a, w in diffs]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "eta_ata_evaluation.md").write_text(text, encoding="utf-8")
        (out / "eta_ata_evaluation.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
