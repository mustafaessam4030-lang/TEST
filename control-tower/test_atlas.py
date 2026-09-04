"""
ATLAS — Adaptive Logistics Strategy Engine: identity and attribution.

The engine now has a name, and a name is a liability. Every line beginning
"ATLAS →" is a claim that ATLAS did something, and the moment one of those
lines can appear for work ATLAS did not do, the whole panel becomes
decoration. This suite exists to make that impossible, and almost all of it is
about what must NOT be said:

    * `Action completed` requires BOTH read-back verification and ATLAS having
      steered the write. Neither half is optional.
    * Shadow mode never says `Strategy selected` — it used nothing.
    * A shipment the deterministic order handled is labelled as such, not left
      blank and not quietly credited.
    * The name is defined once; every mirror of it is checked against the
      original rather than trusted.

Run:  python test_atlas.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))

import update_eta as A                                          # noqa: E402
from ml import config, features, identity, model as M           # noqa: E402
from ml import predictor, telemetry                             # noqa: E402
import bridge as B                                              # noqa: E402

PASS, FAIL = [], []
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")
UI = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
tmp = Path(tempfile.mkdtemp())


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


def with_env(**pairs):
    for key, value in pairs.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = str(value)
    config.reload_from_environment()
    predictor.reset()


class Recorder:
    """Captures every line the automation logs, without silencing it."""

    def __init__(self):
        self.lines = []
        self._original = None

    def __enter__(self):
        self._original = A.write_log
        A.write_log = lambda message: self.lines.append(str(message))
        return self

    def __exit__(self, *_exc):
        A.write_log = self._original
        return False

    def atlas(self):
        return [l for l in self.lines if l.startswith(A.ATLAS_NAME + " ")]

    def said(self, label):
        return any(A.ATLAS_NAME + " → " + label in l for l in self.lines)


print("=" * 72)
print("1. THE NAME IS DEFINED ONCE")
print("=" * 72)
check("The engine is ATLAS", identity.NAME == "ATLAS")
check("...Adaptive Logistics Strategy Engine",
      identity.FULL_NAME == "Adaptive Logistics Strategy Engine")
check("The automation's local mirror agrees",
      A.ATLAS_NAME == identity.NAME and A.ATLAS_FULL_NAME == identity.FULL_NAME,
      "{0} / {1}".format(A.ATLAS_NAME, A.ATLAS_FULL_NAME))
check("The dashboard bridge's mirror agrees",
      B.ATLAS_NAME == identity.NAME and B.ATLAS_FULL_NAME == identity.FULL_NAME,
      "{0} / {1}".format(B.ATLAS_NAME, B.ATLAS_FULL_NAME))
check("Every label the automation can emit exists in the engine's vocabulary",
      all(getattr(A, "ATLAS_" + name.replace(" ", "_").upper()) in identity.LABELS
          for name in ("strategy selected", "strategy failed",
                       "fallback activated", "deterministic fallback",
                       "verification passed", "action completed",
                       "action unverified")))
check("The bridge's influence set matches the engine's",
      set(B.ATLAS_INFLUENCE_LABELS) == set(identity.INFLUENCE_LABELS),
      "{0} vs {1}".format(sorted(B.ATLAS_INFLUENCE_LABELS),
                          sorted(identity.INFLUENCE_LABELS)))
check("A line is formatted the same way on both sides",
      A.atlas_line("Strategy selected", "x")
      == identity.line(identity.STRATEGY_SELECTED, "x"))
check("The model identifier carries the name and both versions",
      identity.identifier(M.MODEL_VERSION, features.FEATURE_VERSION)
      == "ATLAS/{0}.{1}".format(M.MODEL_VERSION, features.FEATURE_VERSION),
      identity.identifier(M.MODEL_VERSION, features.FEATURE_VERSION))

print()
print("=" * 72)
print("2. `ACTION COMPLETED` REQUIRES VERIFICATION *AND* INFLUENCE")
print("=" * 72)
with_env(ML_TELEMETRY_PATH=tmp / "atlas.jsonl", ML_MODE="active", ML_ENABLED=1)


def episode(influenced, chosen, outcome, verified):
    """Run one episode to completion and return everything that was logged."""
    with Recorder() as recorder:
        A.ml_episode_begin("REF-1", "COE", "ETA", "04/09/2026")
        current = A.ml_episode_current()
        if current is not None:
            current.atlas_influenced = influenced
            current.atlas_chosen = chosen
        A.ml_episode_end(outcome, verified, "")
    return recorder


# The one case that may claim success.
good = episode(True, "css_id_visible", A.ML_EPISODE_VERIFIED, True)
check("Verified AND influenced -> Action completed",
      good.said(A.ATLAS_ACTION_COMPLETED), " / ".join(good.atlas()))
check("...and it names the strategy ATLAS chose",
      any("css_id_visible" in l for l in good.atlas()), " / ".join(good.atlas()))

# Every case that may not.
not_ours = episode(False, None, A.ML_EPISODE_VERIFIED, True)
check("Verified but NOT influenced -> no completion claim",
      not not_ours.said(A.ATLAS_ACTION_COMPLETED), " / ".join(not_ours.atlas()))
check("...it says Deterministic fallback instead",
      not_ours.said(A.ATLAS_DETERMINISTIC_FALLBACK), " / ".join(not_ours.atlas()))
check("...and states plainly who did the work",
      any("not by ATLAS" in l for l in not_ours.atlas()), " / ".join(not_ours.atlas()))

unverified = episode(True, "css_id_visible", A.ML_EPISODE_UNVERIFIED, None)
check("Influenced but NOT verified -> no completion claim",
      not unverified.said(A.ATLAS_ACTION_COMPLETED), " / ".join(unverified.atlas()))
check("...it says Action unverified", unverified.said(A.ATLAS_ACTION_UNVERIFIED),
      " / ".join(unverified.atlas()))
check("...and names the switch that would fix it",
      any("VERIFY_AFTER_SAVE" in l for l in unverified.atlas()),
      " / ".join(unverified.atlas()))

mismatch = episode(True, "css_id_visible", A.ML_EPISODE_MISMATCH, False)
check("A write that did not persist claims nothing",
      not mismatch.said(A.ATLAS_ACTION_COMPLETED), " / ".join(mismatch.atlas()))
errored = episode(True, "css_id_visible", A.ML_EPISODE_ERROR, None)
check("An episode that died before a save claims nothing",
      not errored.said(A.ATLAS_ACTION_COMPLETED), " / ".join(errored.atlas()))
check("...and does not claim it was unverified either — it never got there",
      not errored.said(A.ATLAS_ACTION_UNVERIFIED), " / ".join(errored.atlas()))

check("There is exactly ONE place in the automation that can say "
      "'Action completed'",
      SRC.count("ATLAS_ACTION_COMPLETED,") == 1, str(SRC.count("ATLAS_ACTION_COMPLETED,")))
completed_block = SRC.split("atlas_log(ATLAS_ACTION_COMPLETED")[0]
check("...and it sits behind `verified is True and episode.atlas_influenced`",
      completed_block.rstrip().endswith(
          "if verified is True and episode.atlas_influenced:"),
      completed_block.rstrip()[-90:])

print()
print("=" * 72)
print("3. INFLUENCE IS RECORDED, NEVER ASSUMED")
print("=" * 72)
check("An episode starts with no ATLAS attribution",
      not A.atlas_influenced() and A.atlas_chosen() is None)
A.ml_episode_begin("REF-2", "BU", "ATA", "05/09/2026")
check("...and a fresh episode is still unattributed",
      A.atlas_influenced() is False)
A.ml_episode_end(A.ML_EPISODE_ERROR, None, "")

check("Only ml_order sets the flag, and only when the order was USED",
      SRC.count("episode.atlas_influenced = True") == 1
      and "atlas_influenced = True" in
      SRC.split("def ml_order")[1].split("\ndef ")[0],
      str(SRC.count("episode.atlas_influenced = True")))
order_body = SRC.split("def ml_order")[1].split("\ndef ")[0]
check("...after the recommendation was checked for `used`",
      order_body.index("if not recommendation.used:")
      < order_body.index("episode.atlas_influenced = True"))
check("...and after the reordered list was proven complete",
      order_body.index("if len(reordered) != len(named_candidates):")
      < order_body.index("episode.atlas_influenced = True"))

print()
print("=" * 72)
print("4. SHADOW MODE NEVER CLAIMS A SELECTION")
print("=" * 72)
context = features.context(provider="HUB", page="manage", field="ATA",
                           view="BU", page_ready="yes", frames="one",
                           attempt="first")
built = M.StrategyModel()
for _ in range(50):
    built.observe(features.keys(context), "xpath_ata_date", 1.0, duration_ms=700)
    built.observe(features.keys(context), "label_exact", 0.0)
built.meta["feature_version"] = features.FEATURE_VERSION
built.finalise()
model_path = tmp / "champion.json"
model_path.write_text(built.to_json(), encoding="utf-8")

candidates = [("label_exact", 1), ("xpath_ata_date", 2)]

with_env(ML_ENABLED=1, ML_MODE="shadow", ML_MODEL_PATH=model_path,
         ML_CONFIDENCE_THRESHOLD=0.5)
with Recorder() as shadow_log:
    A.ml_episode_begin("REF-3", "BU", "ATA", "05/09/2026")
    ordered, top = A.ml_order(candidates, context)
    shadow_influenced = A.atlas_influenced()
    A.ml_episode_end(A.ML_EPISODE_VERIFIED, True, "")
check("In shadow the automation's own order is returned", ordered == candidates)
check("...ATLAS is NOT recorded as having influenced anything",
      shadow_influenced is False)
check("...the log says Deterministic fallback",
      shadow_log.said(A.ATLAS_DETERMINISTIC_FALLBACK), " / ".join(shadow_log.atlas()))
check("...it NEVER says Strategy selected",
      not shadow_log.said(A.ATLAS_STRATEGY_SELECTED), " / ".join(shadow_log.atlas()))
check("...and a verified write in shadow is credited to the deterministic order",
      not shadow_log.said(A.ATLAS_ACTION_COMPLETED)
      and any("not by ATLAS" in l for l in shadow_log.atlas()),
      " / ".join(shadow_log.atlas()))
check("...while still naming what it WOULD have chosen, for the record",
      any("Would have selected xpath_ata_date" in l for l in shadow_log.atlas()),
      " / ".join(shadow_log.atlas()))

with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=model_path,
         ML_CONFIDENCE_THRESHOLD=0.5)
with Recorder() as active_log:
    A.ml_episode_begin("REF-4", "BU", "ATA", "05/09/2026")
    ordered, top = A.ml_order(candidates, context)
    active_influenced = A.atlas_influenced()
    active_chosen = A.atlas_chosen()
    A.ml_episode_end(A.ML_EPISODE_VERIFIED, True, "")
check("In active mode the order really changes",
      [n for n, _ in ordered] == ["xpath_ata_date", "label_exact"], str(ordered))
check("...ATLAS is recorded as the influence",
      active_influenced is True and active_chosen == "xpath_ata_date")
check("...the log says Strategy selected",
      active_log.said(A.ATLAS_STRATEGY_SELECTED), " / ".join(active_log.atlas()))
check("...and only NOW may it say Action completed",
      active_log.said(A.ATLAS_ACTION_COMPLETED), " / ".join(active_log.atlas()))

with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=tmp / "nothing.json")
with Recorder() as none_log:
    A.ml_episode_begin("REF-5", "BU", "ATA", "05/09/2026")
    ordered, top = A.ml_order(candidates, context)
    no_model_influenced = A.atlas_influenced()
    A.ml_episode_end(A.ML_EPISODE_VERIFIED, True, "")
check("With no model at all, nothing is attributed to ATLAS",
      no_model_influenced is False and not none_log.said(A.ATLAS_STRATEGY_SELECTED),
      " / ".join(none_log.atlas()))

print()
print("=" * 72)
print("5. WHAT IS WRITTEN TO TELEMETRY")
print("=" * 72)
telemetry_path = Path(config.TELEMETRY_PATH)
rows = [json.loads(line) for line in
        telemetry_path.read_text(encoding="utf-8").splitlines() if line.strip()]
episodes_written = [r for r in rows if r.get("kind") == "episode"]
decisions_written = [r for r in rows if r.get("kind") == "decision"]
check("Every event names the engine that wrote it",
      rows and all(r.get("engine") == "ATLAS" for r in rows),
      str({r.get("engine") for r in rows}))
check("Every episode records whether ATLAS influenced it",
      episodes_written and all("atlas_influenced" in r for r in episodes_written))
check("...and the mode it ran under",
      all(r.get("atlas_mode") in ("off", "shadow", "active", "unavailable")
          for r in episodes_written),
      str({r.get("atlas_mode") for r in episodes_written}))
check("An influenced episode records the strategy chosen",
      any(r.get("atlas_influenced") and r.get("atlas_chosen")
          for r in episodes_written))
check("A shadow episode is recorded as NOT influenced",
      any(r.get("atlas_mode") == "shadow" and r.get("atlas_influenced") is False
          for r in episodes_written), str(episodes_written[-4:]))
check("Every decision carries the vocabulary label it was announced under",
      decisions_written and all(
          r.get("label") in identity.LABELS for r in decisions_written),
      str({r.get("label") for r in decisions_written}))
check("A shadow decision is labelled Deterministic fallback, not Selected",
      all(r["label"] == identity.DETERMINISTIC_FALLBACK
          for r in decisions_written if r.get("shadow")),
      str([(r.get("shadow"), r.get("label")) for r in decisions_written]))

print()
print("=" * 72)
print("6. THE MODEL FILE CARRIES THE IDENTITY")
print("=" * 72)
reloaded = M.StrategyModel.from_json(model_path.read_text(encoding="utf-8"),
                                     feature_version=features.FEATURE_VERSION)
check("The model names its engine", reloaded.meta.get("engine") == "ATLAS")
check("...and the full name", reloaded.meta.get("engine_full_name")
      == identity.FULL_NAME)
check("...and reports a consistent identifier",
      reloaded.identifier() == "ATLAS/{0}.{1}".format(
          M.MODEL_VERSION, features.FEATURE_VERSION), reloaded.identifier())
check("The summary the dashboard reads carries it too",
      reloaded.summary().get("engine") == "ATLAS"
      and reloaded.summary().get("identifier") == reloaded.identifier())

print()
print("=" * 72)
print("7. THE STARTUP BANNER")
print("=" * 72)
with_env(ML_ENABLED=1, ML_MODE="shadow", ML_MODEL_PATH=model_path,
         ML_CONFIDENCE_THRESHOLD=0.5)
lines = []
predictor.initialize(log=lines.append)
joined = " / ".join(lines)
check("It introduces itself by name and by what it is",
      any(l.startswith("[ATLAS] ") and identity.FULL_NAME in l for l in lines),
      joined)
check("Every prefixed line uses the engine's name, not '[ML]'",
      all("[ML]" not in l for l in lines), joined)
check("It reports the model identifier", any("ATLAS/" in l for l in lines), joined)
check("Shadow announces itself as a deterministic fallback for the whole run",
      any("ATLAS → Deterministic fallback" in l for l in lines), joined)
check("...and does not claim a selection before making one",
      not any("ATLAS → Strategy selected" in l for l in lines), joined)

lines = []
with_env(ML_ENABLED=1, ML_MODE="active", ML_MODEL_PATH=tmp / "nothing.json")
predictor.initialize(log=lines.append)
check("With no model it says so in the vocabulary",
      any("ATLAS → Deterministic fallback" in l for l in lines),
      " / ".join(lines))

print()
print("=" * 72)
print("8. THE DASHBOARD SHOWS INFLUENCE, AND ONLY REAL INFLUENCE")
print("=" * 72)
state = B.ControlTowerState()
state.shipment_started({"bol_awb": "REF-A", "carrier": "DHL",
                        "provider": "DHL", "table_page": 1})
state.atlas(identity.STRATEGY_SELECTED, "xpath_ata_date — top score 0.84")
state.atlas(identity.ACTION_COMPLETED, "BU ATA = 05/09/2026 verified")
snapshot = state.snapshot()
influenced_record = snapshot["current"]["shipment"]
check("A steered shipment is marked influenced",
      influenced_record["atlas"]["influenced"] is True, str(influenced_record["atlas"]))
check("...and remembers which strategy",
      influenced_record["atlas"]["chosen"] == "xpath_ata_date",
      str(influenced_record["atlas"]))

state2 = B.ControlTowerState()
state2.shipment_started({"bol_awb": "REF-B", "carrier": "DHL",
                         "provider": "DHL", "table_page": 1})
state2.atlas(identity.DETERMINISTIC_FALLBACK, "shadow")
plain = state2.snapshot()["current"]["shipment"]
check("A Deterministic fallback does NOT mark a shipment as ATLAS-influenced "
      "— the engine saying it stood down is the opposite of a claim",
      plain["atlas"]["influenced"] is False, str(plain["atlas"]))
check("...and it is still recorded, so the UI can say who did the work",
      len(plain["atlas"]["events"]) == 1, str(plain["atlas"]))
check("The run counts influenced actions and fallbacks separately",
      state2.snapshot()["atlas"]["fallbacks"] == 1
      and state2.snapshot()["atlas"]["influenced_actions"] == 0,
      str(state2.snapshot()["atlas"]))
check("A fresh run has no ATLAS activity to show",
      B.ControlTowerState().snapshot()["atlas"]["events"] == [])
check("The bridge cannot raise into the automation",
      B.ControlTowerState().atlas(None, object()) is None)

print()
print("=" * 72)
print("9. THE UI RENDERS ATTRIBUTION FROM RECORDED STATE ONLY")
print("=" * 72)
check("The card is titled ATLAS",
      '<div class="card-t">ATLAS</div>' in UI)
check("...with the full name beneath it",
      "Adaptive Logistics Strategy Engine" in UI)
check("...and the old generic title is gone",
      '<div class="card-t">AI engine</div>' not in UI)
check("The attribution tag reads the recorded flag and nothing else",
      "const a = (record && record.atlas) || null;" in UI
      and "a.influenced" in UI)
check("A shipment with no ATLAS record shows no tag at all",
      "if (!a) return '';" in UI)
check("An unsteered shipment is labelled Deterministic, not left blank",
      ">Deterministic</span>" in UI)
check("The activity feed renders only events the run emitted",
      "const events = (a && a.events) || [];" in UI
      and "if (!events.length){ box.hidden = true;" in UI)
check("There is no hardcoded ATLAS activity in the page",
      "Strategy selected'" not in UI.replace("e.label", "")
      or "atlasFeed" in UI)
check("The tag is styled, not shouted",
      ".atlas-t{" in UI and "font-size:9px" in UI)
check("The influenced state uses the existing accent colour",
      "var(--vio)" in UI.split(".atlas-t.on{")[1][:120])
check("Braces balance — a stray one silently drops every rule after it",
      UI.count("{") == UI.count("}"),
      "{0} open, {1} close".format(UI.count("{"), UI.count("}")))

print()
print("=" * 72)
print("10. ATLAS IS A STRATEGY LAYER, NOT A SECOND AUTOMATION")
print("=" * 72)
check("It still cannot decide what to write",
      "recommend_value" not in SRC and "ml_value" not in SRC)
check("It still cannot decide which field this is",
      "ml_field" not in SRC and "recommend_field" not in SRC)
check("It still cannot skip verification",
      "recommend_verify" not in SRC and "ml_skip" not in SRC)
check("Announcing an event cannot raise into the run",
      "except Exception:\n        pass" in
      SRC.split("def atlas_log")[1].split("\ndef ")[0])
# Call sites only — the `def atlas_log(label, ...)` line is not one.
CALL_SITES = [call.split(",")[0].strip()
              for call in SRC.replace("def atlas_log(", "").split("atlas_log(")[1:]]
check("Every announcement uses a label constant, never a literal",
      CALL_SITES and all(name.startswith("ATLAS_") for name in CALL_SITES),
      str(CALL_SITES))
check("...and every constant it uses is a real one",
      all(hasattr(A, name) and getattr(A, name) in identity.LABELS
          for name in CALL_SITES), str(CALL_SITES))
check("The architecture is written down where the name is defined",
      "Deterministic Safety" in identity.__doc__
      and "Read-back Verification" in identity.__doc__)
check("...including that ATLAS is not a second automation",
      "not a second automation" in identity.__doc__)

with_env(ML_ENABLED=None, ML_MODE=None, ML_MODEL_PATH=None,
         ML_CONFIDENCE_THRESHOLD=None, ML_TELEMETRY_PATH=None)

print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 72)
sys.exit(1 if FAIL else 0)
