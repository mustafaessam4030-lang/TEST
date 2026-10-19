"""
Phase 1 gates: what may become learning data, when the data is good enough to
train on, and what a candidate model must show before it may act.

    python test_learning_gates.py

Offline, on synthetic telemetry in a temporary folder — mechanism only. None
of this is evidence that a model helps on the real Hub.
"""

import json
import os
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="ct_gates_"))
os.environ["ML_TELEMETRY_PATH"] = str(TMP / "telemetry.jsonl")
os.environ["ML_CHAMPION_PATH"] = str(TMP / "champion.json")
os.environ["ML_CHALLENGER_PATH"] = str(TMP / "challenger.json")
os.environ.pop("ML_MODEL_PATH", None)
sys.path.insert(0, str(HERE))

from ml import config, episodes, features, predictor, readiness, shadow, trainer  # noqa: E402
from ml import model as M                                           # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


def write(path, events):
    with open(path, "w", encoding="utf-8") as handle:
        for e in events:
            handle.write(json.dumps(e) + "\n")
    return path


CTX = features.context(provider="HUB", page="manage", field="ATA", view="BU",
                       page_ready="yes", frames="one", attempt="first")


def episode(i, verified, day, ref):
    return {"kind": "episode", "episode_id": "e{0}".format(i), "reference": ref,
            "outcome": {True: "VERIFIED", False: "MISMATCH", None: "UNVERIFIED"}[verified],
            "verified": verified, "ts": "2026-09-{0:02d}".format(day)}


def attempt(i, strategy, found, day, rank):
    return {"kind": "interaction", "episode_id": "e{0}".format(i), "context": CTX,
            "strategy": strategy, "success": found, "rank": rank,
            "ts": "2026-09-{0:02d}".format(day)}


print("=" * 72)
print("1. ONLY READ-BACK OUTCOMES BECOME LEARNING DATA")
print("=" * 72)
mixed = write(TMP / "mixed.jsonl", [
    episode(1, True, 1, "A"), attempt(1, "a", True, 1, 0),
    episode(2, False, 1, "B"), attempt(2, "a", True, 1, 0),
    episode(3, None, 1, "C"), attempt(3, "a", True, 1, 0),        # saved, never read back
    attempt(4, "a", True, 1, 0),                                    # no episode at all
    dict(attempt(5, "a", True, 1, 0), source="test"), episode(5, True, 1, "E"),
])
rows, report = episodes.join(mixed)
check("A write read back and matching is a positive",
      any(r.episode_id == "e1" and r.label for r in rows))
check("A write read back WRONG is a negative, even though the field was found",
      any(r.episode_id == "e2" and r.label is False for r in rows))
check("A write never read back is EXCLUDED — not counted as a failure",
      not any(r.episode_id == "e3" for r in rows)
      and report["dropped_unverified_episode"] == 1, str(report))
check("An attempt with no episode is excluded", report["dropped_unknown_episode"] == 1)
check("Test-run rows are excluded", report["dropped_not_real"] == 1)

print()
print("=" * 72)
print("2. 60 ROWS IS A CHECKPOINT, NOT PERMISSION")
print("=" * 72)
narrow = []
for i in range(80):                       # 80 rows, one day, one shipment
    ok = i % 3 != 0
    narrow += [episode(i, ok, 1, "SAME"), attempt(i, "a" if i % 2 else "b", ok, 1, i % 2)]
narrow_path = write(TMP / "narrow.jsonl", narrow)
result = readiness.assess_file(narrow_path)
names = {c["name"]: c for c in result["criteria"]}
check("80 rows from one day and one shipment are NOT READY", result["ready"] is False)
check("...the checkpoint itself is reached",
      names["labelled rows (checkpoint)"]["passed"] is True)
check("...but too few shipments, days and span are each named",
      not names["distinct shipments"]["passed"]
      and not names["time span (days)"]["passed"]
      and not names["distinct days with data"]["passed"], result["summary"])
ok, message, _ = trainer.train(narrow_path, TMP / "c1.json", echo=lambda *a: None)
check("Training refuses data that is not ready, and says why",
      ok is False and "NOT READY" in message and "distinct shipments" in message, message)
check("...and writes no model", not (TMP / "c1.json").exists())

no_refs = [dict(e, reference=None) if e["kind"] == "episode" else e for e in narrow]
result = readiness.assess(*episodes.join(write(TMP / "norefs.jsonl", no_refs)),
                          events=no_refs)
check("Missing shipment references count as NOT shown — never as diverse",
      not {c["name"]: c for c in result["criteria"]}["distinct shipments"]["passed"])

one_sided = []
for i in range(120):
    one_sided += [episode(i, True, i % 28 + 1, "S{0}".format(i % 40)),
                  attempt(i, "a" if i % 2 else "b", True, i % 28 + 1, i % 2)]
result = readiness.assess_file(write(TMP / "onesided.jsonl", one_sided))
check("Successes only, however many, are NOT READY (no verified failures)",
      not {c["name"]: c for c in result["criteria"]}["verified failures"]["passed"])

random.seed(3)
diverse = []
for i in range(400):
    day, ref = i % 28 + 1, "S{0}".format(i % 90)
    first = random.choice(["a", "b"])
    found = random.random() < (0.9 if first == "b" else 0.4)
    good = found and random.random() < 0.97
    diverse += [episode(i, good, day, ref), attempt(i, first, found, day, 0 if first == "a" else 1)]
diverse_path = write(TMP / "diverse.jsonl", diverse)
result = readiness.assess_file(diverse_path)
check("Varied data — many shipments, 4 weeks, both outcomes, two strategies — is READY",
      result["ready"] is True, result["summary"])
check("...and READY means 'go to offline evaluation', nothing more",
      "nothing is trained or promoted automatically" in result["summary"])

print()
print("=" * 72)
print("3. SHADOW SCORECARD: ONLY AGAINST WHAT THE HUB READ BACK")
print("=" * 72)


def decision(i, chosen, candidates=("a", "b"), shadow_flag=True, used=False, ep=True):
    return {"kind": "decision", "episode_id": "e{0}".format(i) if ep else None,
            "candidates": list(candidates), "chosen": chosen, "shadow": shadow_flag,
            "used": used, "ts": "2026-09-01"}


events = [
    # e1: automation's first (a) failed, b found it, read back OK -> model (b) wins
    episode(1, True, 1, "A"), attempt(1, "a", False, 1, 0), attempt(1, "b", True, 1, 1),
    decision(1, "b"),
    # e2: a found it first, read back OK; model wanted b, never tried -> baseline wins
    episode(2, True, 1, "B"), attempt(2, "a", True, 1, 0), decision(2, "b"),
    # e3: read back WRONG -> no winner, nobody scores
    episode(3, False, 1, "C"), attempt(3, "a", True, 1, 0), decision(3, "b"),
    # e4: never read back -> excluded
    episode(4, None, 1, "D"), attempt(4, "a", True, 1, 0), decision(4, "b"),
    # no episode link -> excluded
    decision(5, "b", ep=False),
    # an ACTIVE decision is not a shadow one -> not scored here
    decision(6, "b", shadow_flag=False, used=True),
]
card = shadow.score(events=events, min_decisions=1, min_disagreements=1)
check("Model right where the automation's first choice failed: a model win",
      card["model_wins"] == 1, str(card))
check("Model's pick never tried while the automation's worked: counted as a MISS",
      card["baseline_wins"] == 1 and card["model_untried"] >= 1, str(card))
check("A write read back wrong has no winner: neither side scores",
      card["both_missed"] == 1, str(card))
check("A write never read back is excluded and counted", card["no_verdict"] == 1)
check("A decision with no episode link is excluded and counted", card["no_episode_id"] == 1)
check("Only shadow decisions are scored", card["decisions"] == 5, str(card))
check("Below the minimums the verdict is INSUFFICIENT DATA",
      shadow.score(events=events)["verdict"] == shadow.INSUFFICIENT)

big = []
for i in range(300):
    first_works = i % 4 == 0                   # the automation's first is right 25%
    big += [episode(i, True, i % 28 + 1, "S{0}".format(i)),
            attempt(i, "a", first_works, i % 28 + 1, 0)]
    if not first_works:
        big.append(attempt(i, "b", True, i % 28 + 1, 1))
    big.append(decision(i, "b"))
card = shadow.score(events=big)
check("A model that picks the verified winner far more often: BETTER",
      card["verdict"] == shadow.BETTER, card["reason"])
worse = [dict(e, chosen="a") if e["kind"] == "decision" else e for e in big]
worse = [dict(e, candidates=["b", "a"]) if e["kind"] == "decision" else e for e in worse]
check("The same evidence with the roles swapped: WORSE",
      shadow.score(events=worse)["verdict"] == shadow.WORSE)

print()
print("=" * 72)
print("4. APPROVAL BEFORE ACTIVE USE — NEVER AUTOMATIC")
print("=" * 72)
telemetry = write(TMP / "telemetry.jsonl", big)
ok, message = trainer.approve("Mustafa", telemetry)
check("No champion: nothing to approve", ok is False and "no champion" in message, message)
built = M.StrategyModel()
for _ in range(40):
    built.observe(features.keys(CTX), "b", 1.0)
    built.observe(features.keys(CTX), "a", 0.0)
built.finalise()
built.meta.update(feature_version=features.FEATURE_VERSION, promotion_verdict="BETTER",
                  promotion_forced=True)
Path(config.CHAMPION_PATH).write_text(built.to_json(), encoding="utf-8")
ok, message = trainer.approve("Mustafa", telemetry)
check("A FORCED promotion cannot be approved", ok is False, message)
built.meta["promotion_forced"] = False
Path(config.CHAMPION_PATH).write_text(built.to_json(), encoding="utf-8")
ok, message = trainer.approve("", telemetry)
check("Approval needs a name", ok is False and "--by" in message, message)
thin = write(TMP / "thin.jsonl", events)
ok, message = trainer.approve("Mustafa", thin)
check("Approval is refused without a BETTER shadow scorecard",
      ok is False and "INSUFFICIENT DATA" in message, message)

os.environ.update(ML_MODE="active", ML_ENABLED="1", ML_CONFIDENCE_THRESHOLD="0.5")
config.reload_from_environment()
predictor.reset()
r = predictor.recommend_strategy(CTX, ["a", "b"])
check("ML_MODE=active with the unapproved champion changes nothing", r.used is False, repr(r))
ok, message = trainer.approve("Mustafa", telemetry)
check("With a promoted champion and a BETTER scorecard, approval is recorded",
      ok is True and "Mustafa" in message, message)
meta = json.loads(Path(config.CHAMPION_PATH).read_text(encoding="utf-8")).get("meta", {})
check("...with who, when and on which evidence",
      meta.get("approval", {}).get("approved_by") == "Mustafa"
      and meta["approval"].get("shadow_verdict") == "BETTER"
      and meta["approval"].get("approved_at"))
predictor.reset()
r = predictor.recommend_strategy(CTX, ["a", "b"])
check("Only now does ML_MODE=active reorder (approved AND the mode switched on)",
      r.used is True and r.top == "b", repr(r))
os.environ["ML_MODE"] = "shadow"
config.reload_from_environment()
predictor.reset()
check("Approval alone does not act: in shadow mode nothing is reordered",
      predictor.recommend_strategy(CTX, ["a", "b"]).used is False)

print()
print("=" * 72)
print("5. DECISIONS CAN BE SCORED, AND NOTHING TRAINS ITSELF")
print("=" * 72)
predictor.recommend_strategy(CTX, ["a", "b"], episode_id="ep-xyz")
written = Path(os.environ["ML_TELEMETRY_PATH"]).read_text(encoding="utf-8")
check("A decision row now carries its write episode", '"episode_id": "ep-xyz"' in written)
src = (HERE / "update_eta.py").read_text(encoding="utf-8")
check("The automation passes the episode to every strategy decision",
      "context, names, log=write_log, episode_id=ml_episode_id())" in src)
callers = []
for path in list(HERE.glob("*.py")) + list((HERE / "dashboard").glob("*.py")) \
        + list((HERE / "worker").glob("*.py")) + list((HERE / "controlplane").glob("*.py")):
    if path.name.startswith("test_") or path.name in ("demo_ml_live.py", "proof_runtime.py"):
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    if any(call in text for call in ("trainer.train(", "trainer.promote(", "trainer.approve(")):
        callers.append(path.name)
check("No production code trains, promotes or approves on its own", not callers, callers)
check("The two demos that do train use throwaway folders",
      all("mkdtemp" in (HERE / n).read_text(encoding="utf-8")
          for n in ("demo_ml_live.py", "proof_runtime.py")))

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
