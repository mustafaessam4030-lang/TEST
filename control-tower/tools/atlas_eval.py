"""
ATLAS evaluation v2: routing, retrieval, calculations and scenarios on an
independently written question set, through ATLAS's own entry point.

    python tools/atlas_eval.py --set knowledge/eval/dev_v2.json
    python tools/atlas_eval.py --set knowledge/eval/heldout_v2.json --summary-only
    python tools/atlas_eval.py --root /path/to/other/checkout ...   (baseline)

Runs without a language model (ATLAS_LLM_PROVIDER=none): it measures what
ATLAS itself decides and finds. The writer's expected concepts are matched to
knowledge items by name at scoring time, so an item counts only if its name
(term, acronym, synonym) is the concept the writer named.

What each check is, honestly:
  routing      the route ATLAS took, read from its reply (see route_of)
  retrieval    R@1: the answer's main item is an expected concept; R@3 / MRR
               from the search ranking
  unsupported  a confident answer from an item that is none of the expected
               concepts (a "closest entry" answer is not counted as confident)
  abstention   when no item in the knowledge base matches the expected
               concepts, the right outcome is no answer or a "closest entry"
  calculation  the exact value; a refusal or a request for inputs when expected
  scenario     automated proxies only — concept coverage, missing information
               asked for, no confirmed cause, abstention when expected. They
               do not grade reasoning quality; read the answers for that.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
ap.add_argument("--set", required=True)
ap.add_argument("--out")
ap.add_argument("--summary-only", action="store_true")
A = ap.parse_args()
ROOT = Path(A.root).resolve()
SET = Path(A.set).resolve()
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="atlas_eval_")
os.environ["ATLAS_LLM_PROVIDER"] = "none"
for k in ("ATLAS_SEARCH_URL", "ATLAS_LLM_MODEL"):
    os.environ.pop(k, None)

from dashboard import assistant  # noqa: E402
from dashboard.bridge import ControlTowerState  # noqa: E402
from intelligence import kb as KB  # noqa: E402
import atlas_demo  # noqa: E402

bridge = ControlTowerState()
atlas_demo.seed_test_run(bridge)
STATE = bridge.snapshot()
K = KB.kb()

TROUBLE = {"why", "failure_why", "failure_what", "failure_cats", "analysis", "why_recovery_failed",
           "failure_evidence", "learn_failure", "keeps_failing", "recovery", "what_fixes", "try_next"}
RULES = {"handbook", "handbook_guide", "identity", "capabilities", "help", "glossary", "setup_admin_only",
         "atlas_checks", "verification", "system"}
ACTION = {"run_control", "reprocess", "download", "code_request", "human_resume", "skill_pdf"}
CHATTY = {"greeting", "thanks", "chat", "conduct"}
HEDGE = re.compile(r"\b(may|might|could|possibl[ey]|likely|unlikely|hypothes|if |whether|check|confirm|"
                   r"not (?:yet )?(?:known|confirmed|proven)|unknown)\b", re.I)
CONFIRMED_CAUSE = re.compile(r"\b(the (?:root )?cause (?:is|was)|this (?:was|is) caused by|definitely|certainly)\b", re.I)
ABSTAIN = re.compile(r"\b(not enough|insufficient|can(?:no|')t (?:tell|say|determine|confirm)|unable to (?:tell|determine)|"
                     r"no evidence|would need|need (?:more|the)|without (?:the|more))\b", re.I)
CLARIFY = re.compile(r"\b(do you mean|which (?:one|shipment|carrier)|could you (?:clarify|say)|can you clarify|"
                     r"what do you mean|did you mean)\b", re.I)


def concept_ids(names):
    """Item ids whose own names are the concepts named (exact name keys)."""
    out = set()
    for n in names or []:
        for key in KB.name_keys(n):
            out |= set(K.names.get(key, ()))
    return {i for i in out if i in K.items and K.current(K.items[i])}


def route_of(r):
    intent = r.get("intent")
    kn = r.get("knowledge") or {}
    if r.get("request") or intent in ACTION:
        return "action"
    if intent == "general_knowledge":
        if kn.get("mode") == "calculation":
            return "calculation"
        if str((kn.get("items") or [""])[0]).startswith("P."):
            return "atlas_rules"
        if kn.get("mode") == "scenario":
            return "troubleshooting"
        return "knowledge"
    if intent in RULES:
        return "atlas_rules"
    if intent in TROUBLE or str(intent or "").startswith("failure_"):
        return "troubleshooting"
    if intent in CHATTY:
        return "chat"
    if r.get("clarify") or CLARIFY.search(r.get("answer") or ""):
        return "ambiguous"
    if r.get("fallback") or not intent or r.get("understood") is False:
        return "none"
    return "operational"


def route_ok(expected, got, r, case):
    if expected == got:
        return True
    if expected == "out_of_scope":
        return got in ("none", "chat")                  # declined, not answered as logistics
    if expected == "follow_up":
        ref = re.findall(r"\b\d{8,10}\b", json.dumps(case.get("previous_turn") or ""))
        return got in ("operational", "troubleshooting") and bool(ref) and r.get("reference") == ref[0]
    if expected == "troubleshooting" and got == "knowledge":
        return (r.get("knowledge") or {}).get("mode") == "scenario"
    return False


def ask(case):
    ctx = {}
    prev = case.get("previous_turn")
    if prev:
        ref = re.findall(r"\b\d{8,10}\b", json.dumps(prev))
        if ref:
            ctx["reference"] = ref[0]
    t = time.perf_counter()
    r = assistant.answer(case["question"], STATE, ctx)
    return r, (time.perf_counter() - t) * 1000


def words(text):
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower())}


def score(case):
    r, ms = ask(case)
    text = r.get("answer") or ""
    kn = r.get("knowledge") or {}
    used = kn.get("items") or []
    got = route_of(r)
    row = {"id": case["id"], "type": case["type"], "lang": case.get("lang"), "difficulty": case.get("difficulty"),
           "expected_route": case.get("expected_route"), "route": got, "ms": round(ms, 2),
           "route_ok": route_ok(case.get("expected_route"), got, r, case), "used": used[:3]}
    caveat = "Closest entry" in text
    if case["type"] in ("knowledge", "scenario") or case.get("expected_route") == "knowledge":
        want = concept_ids(case.get("expected_concepts"))
        row["coverable"] = bool(want)
        top = [x[0] for x in (kn.get("top") or [])]
        ranked = [u for u in used] + [t for t in top if t not in used]
        if kn.get("mode") == "scenario":
            # A scenario answer rests on a playbook and the concepts behind it.
            row["r1"] = any(u in want for u in used[:4])
        else:
            row["r1"] = bool(used) and used[0] in want and not caveat
        row["r3"] = any(i in want for i in ranked[:3])
        rank = next((n + 1 for n, i in enumerate(ranked) if i in want), None)
        row["rr"] = 1.0 / rank if rank else 0.0
        confident = bool(used) and not caveat
        row["unsupported"] = confident and bool(want) and not any(u in want for u in used)
        row["abstain_ok"] = (not want) and (not confident)
        row["cited"] = (not used) or ("_Knowledge status" in text)
        if case["type"] == "scenario":
            names = [n.lower() for n in case.get("expected_concepts") or []]
            row["concepts"] = sum(1 for n in names if n in text.lower()) / float(len(names) or 1)
            miss = case.get("missing_info") or []
            got_miss = 0
            for m in miss:
                mw = words(m) - {"what", "which", "when", "where", "whether", "that", "with", "from", "this", "there"}
                if mw and len(mw & words(text)) / float(len(mw)) >= 0.5:
                    got_miss += 1
            row["missing_asked"] = got_miss / float(len(miss) or 1)
            row["confirmed_cause"] = bool(CONFIRMED_CAUSE.search(text)) and not HEDGE.search(text)
            row["abstained"] = bool(ABSTAIN.search(text))
            row["abstain_expected"] = bool(case.get("should_abstain"))
            row["structured"] = all(h in text for h in ("**Known", "**Missing", "**Possible causes"))
    if case["type"] == "calculation" or case.get("expected_route") == "calculation":
        calc = kn.get("calculation") or {}
        if case.get("should_refuse"):
            row["calc_ok"] = "I can't calculate" in text or "can't calculate" in text or bool(calc.get("error"))
        elif case.get("should_ask") or (case.get("expected_value") is None and not case.get("should_refuse")):
            row["calc_ok"] = "won't assume" in text or bool(calc.get("missing"))
        else:
            v = calc.get("value")
            exp = case.get("expected_value")
            row["calc_ok"] = v is not None and exp is not None and abs(float(v) - float(exp)) <= max(0.01, abs(float(exp)) * 0.001)
    row["answer"] = text[:600]
    return row


def agg(rows, key):
    vals = [r[key] for r in rows if key in r]
    if not vals:
        return None
    return round(sum(float(v) for v in vals) / len(vals), 3), len(vals)


def summary(rows):
    out = {"cases": len(rows)}
    out["routing_accuracy"] = agg(rows, "route_ok")
    kr = [r for r in rows if "r1" in r and r["type"] == "knowledge"]
    cov = [r for r in kr if r["coverable"]]
    out["knowledge"] = {"cases": len(kr), "coverable": len(cov), "correct_all": agg(kr, "r1"),
                        "R@1": agg(cov, "r1"), "R@3": agg(cov, "r3"), "MRR": agg(cov, "rr"),
                        "unsupported_rate": agg(kr, "unsupported"), "abstention_ok_when_not_covered":
                            agg([r for r in kr if not r["coverable"]], "abstain_ok"), "cited": agg(kr, "cited")}
    out["calculation"] = agg(rows, "calc_ok")
    sr = [r for r in rows if r["type"] == "scenario"]
    out["scenario"] = {"cases": len(sr), "concepts": agg(sr, "concepts"), "missing_info_asked": agg(sr, "missing_asked"),
                       "confirmed_cause_rate": agg(sr, "confirmed_cause"), "structured": agg(sr, "structured"),
                       "abstained_when_expected": agg([r for r in sr if r.get("abstain_expected")], "abstained"),
                       "unsupported_rate": agg(sr, "unsupported")}
    by = {}
    for r in rows:
        for k in ("type:" + r["type"], "difficulty:" + str(r["difficulty"]), "lang:" + str(r["lang"])):
            ok = r.get("route_ok") and r.get("r1", True) is not False and r.get("calc_ok", True) is not False
            b = by.setdefault(k, [0, 0])
            b[0] += bool(ok)
            b[1] += 1
    out["strict_pass_by_group"] = {k: "{0}/{1}".format(*v) for k, v in sorted(by.items())}
    ms = sorted(r["ms"] for r in rows)
    out["latency_ms_no_model"] = {"p50": ms[len(ms) // 2], "p90": ms[int(len(ms) * 0.9)], "max": ms[-1]}
    out["items_in_kb"] = len(K.items)
    return out


cases = json.loads(SET.read_text(encoding="utf-8"))["cases"]
rows = [score(c) for c in cases]
rep = {"set": SET.name, "root": str(ROOT), "summary": summary(rows)}
if not A.summary_only:
    rep["rows"] = rows
if A.out:
    Path(A.out).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(rep["summary"], ensure_ascii=False, indent=1))
