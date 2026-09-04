"""
The learning layer as seen from the automation.

The contract being tested: with ML off — the default — update_eta.py must do
exactly what it did before the layer existed. Then, that the safety rules are
still deterministic no matter what the model says.

Run:  python test_ml_integration.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import with ML off, which is the default and the state that matters most.
os.environ.pop("ML_ENABLED", None)
import update_eta as A
from ml import config, episodes, features, model as M, predictor

PASS, FAIL = [], []
SRC = Path("update_eta.py").read_text(encoding="utf-8")


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


print("=" * 70)
print("1. ON BY DEFAULT, BUT INERT WITHOUT A MODEL")
print("=" * 70)
check("ML defaults to ON", config.ML_ENABLED is True)
check("...but the switch alone activates nothing without a model",
      predictor.active() is False, str(predictor.status()["error"]))
check("The adapter reports the layer is present", A.ML_AVAILABLE is True)

fake = [("first", "L1"), ("second", "L2"), ("third", "L3")]
ordered, top = A.ml_order(fake, A.ml_context(provider="HUB", field="ATA"))
check("ml_order returns the caller's own order untouched", ordered == fake, str(ordered))
check("...and names no winner", top is None)
check("ml_wait_budget returns the caller's own budget",
      A.ml_wait_budget(A.ml_context(provider="HUB"), 15000) == 15000)
check("A None context is handled", A.ml_order(fake, None) == (fake, None))
check("A one-item list is handled", A.ml_order(fake[:1], None)[0] == fake[:1])

print()
print("=" * 70)
print("2. THE LAYER CANNOT TAKE THE AUTOMATION DOWN")
print("=" * 70)
check("ml_record never raises on nonsense",
      A.ml_record({"provider": object()}, "s", True) is None)
check("ml_category never raises", A.ml_category(RuntimeError("boom")) in
      ("FIELD_NOT_FOUND", "NETWORK_ERROR", "TIMEOUT", "OK", "PAGE_NOT_READY",
       "FIELD_NOT_VISIBLE", "SCROLL_REQUIRED", "INPUT_REJECTED",
       "CHANGE_EVENT_FAILED", "BOT_CHALLENGE", "VALIDATION_FAILURE",
       "VERIFICATION_FAILURE"))
check("The import is guarded so a missing package is survivable",
      "except Exception as _ml_import_error" in SRC and "ML_AVAILABLE = False" in SRC)
check("ml_order swallows its own errors",
      "note_suppressed(\"asking the model for a candidate order\"" in SRC)


class ExplodingPredictor:
    @staticmethod
    def recommend_strategy(*args, **kwargs):
        raise RuntimeError("model exploded")

    @staticmethod
    def recommend_wait(*args, **kwargs):
        raise RuntimeError("model exploded")


_real = A.ml_predictor
A.ml_predictor = ExplodingPredictor
try:
    ordered, top = A.ml_order(fake, A.ml_context(provider="HUB", field="ATA"))
    check("A predictor that raises falls back to the caller's order",
          ordered == fake and top is None, str(ordered))
    check("A timing predictor that raises falls back",
          A.ml_wait_budget(A.ml_context(provider="HUB"), 15000) == 15000)
finally:
    A.ml_predictor = _real

print()
print("=" * 70)
print("3. THE SAFETY RULES ARE STILL DETERMINISTIC")
print("=" * 70)
check("ETA still routes to the COE view",
      "COE_VIEW,\n            \"ETA\"," in SRC or
      ("COE_VIEW" in SRC and "\"ETA\"" in SRC and
       SRC.index("COE_VIEW,") < SRC.index("BU_VIEW,")))
check("ATA still routes to the BU view", "BU_VIEW,\n                \"ATA\"," in SRC)
check("ETA candidates still exclude ATA",
      "input[id*='ETA' i]:not([id*='ATA' i]):visible" in SRC)
check("ATA candidates still anchor on the ATA label",
      "[starts-with(normalize-space(),'ATA Date')]" in SRC)
check("The visibility-free fallback keeps the cross-field guard",
      "input[id*='{0}' i]:not([id*='{1}' i]), " in SRC)
check("A missing field still raises rather than writing elsewhere",
      "field was not found on the Manage page." in SRC)
check("The model is never asked what to write",
      "recommend_value" not in SRC and "ml_value" not in SRC)
check("The model is never asked which field this is",
      "ml_field" not in SRC and "recommend_field" not in SRC)
check("The model is never asked whether to skip verification",
      "recommend_verify" not in SRC and "ml_skip" not in SRC)
check("ml_order only ever reorders",
      "The SET of\n    candidates is never changed" in SRC)
check("...and refuses its own output if the set ever changed",
      "internal ordering error" in Path("ml/predictor.py").read_text(encoding="utf-8"))

# The new machinery gets the same treatment. Quarantine, drift and shadow are
# all reasons the model may DECLINE or DEMOTE — none of them is a reason to
# remove a candidate the caller offered.
PRED = Path("ml/predictor.py").read_text(encoding="utf-8")
check("A quarantined strategy is demoted, never removed",
      "keep their place in the caller's list" in PRED
      and "s in blocked" in PRED)
ordered_names, _top = A.ml_order(
    [("first", 1), ("second", 2), ("third", 3)],
    A.ml_context(provider="HUB", page="manage", field="ATA"))
check("...and the caller's list comes back complete no matter what",
      [n for n, _ in ordered_names] == ["first", "second", "third"])
check("Drift makes the model stand down, it does not change the write",
      "keeping the original order" in PRED)
check("The episode verdict is recorded, never consulted mid-write",
      "ml_episode_end" in SRC and "ml_episode_end" not in
      SRC.split("def write_date_value")[1].split("\ndef ")[0])
check("Closing an episode cannot raise into the automation",
      "except Exception:\n        pass" in
      SRC.split("def ml_episode_end")[1].split("\ndef ")[0])
check("The reward has no say in what gets written — it is a training signal",
      "reward" not in SRC.split("def write_date_value")[1].split("\ndef ")[0])

print()
print("=" * 70)
print("4. THE EXISTING RETRY AND WAIT MACHINERY IS INTACT")
print("=" * 70)
check("classify_failure is unchanged in its outcomes",
      A.classify_failure(RuntimeError("timed out")) == A.TIMEOUT)
check("...network errors still map to a retryable outcome",
      A.classify_failure(RuntimeError("net::ERR_HTTP2_PROTOCOL_ERROR"))
      == A.TEMPORARY_WEBSITE_ISSUE)
check("...and RETRYABLE is the same set",
      A.RETRYABLE == {A.TIMEOUT, A.TEMPORARY_WEBSITE_ISSUE, A.UNEXPECTED_PAGE_STATE})
check("run_with_retry still exists and still backs off",
      "delay = base_delay_seconds * (2 ** (attempt - 1))" in SRC)
check("The hub budgets are unchanged",
      A.HUB_FORM_READY_MAX_MS == 15000 and A.HUB_SAVE_MAX_MS == 8000
      and A.HUB_TABLE_REFRESH_MAX_MS == 12000)
check("The tab wait still cannot exceed its own constant",
      "ml_wait_budget(panel_context, HUB_FORM_READY_MAX_MS" in SRC)
check("...and has a floor", "floor_ms=3000" in SRC)
check("The candidate probe timeout is untouched",
      "first_visible(candidates, 1500)" in SRC)

print()
print("=" * 70)
print("5. VERIFICATION IS OFF BY DEFAULT AND DETERMINISTIC WHEN ON")
print("=" * 70)
check("VERIFY_AFTER_SAVE defaults to off", A.VERIFY_AFTER_SAVE is False)
check("An unverified write raises rather than reporting success",
      "was saved but not verified for" in SRC)
check("Verification compares dates, not strings",
      "normalize_date(actual) or actual" in SRC)
check("The model has no say in verification",
      "ml_record(context, \"verify_reload\"" in SRC
      and "recommend" not in SRC.split("def verify_saved_date")[1].split("def ")[0])

print()
print("=" * 70)
print("6. WITH ML ON, A RECOMMENDATION REACHES THE AUTOMATION")
print("=" * 70)
tmp = Path(tempfile.mkdtemp())
ctx = features.context(provider="HUB", page="manage", field="ATA", view="BU",
                       page_ready="yes", frames="one", attempt="first")
built = M.StrategyModel()
for _ in range(50):
    built.observe(features.keys(ctx), "third", 1.0, duration_ms=700)
for _ in range(50):
    built.observe(features.keys(ctx), "first", 0.0)
    built.observe(features.keys(ctx), "second", 0.0)
built.finalise()
built.meta["feature_version"] = features.FEATURE_VERSION
path = tmp / "m.json"
path.write_text(built.to_json(), encoding="utf-8")

os.environ["ML_ENABLED"] = "1"
os.environ["ML_MODE"] = "active"
os.environ["ML_MODEL_PATH"] = str(path)
os.environ["ML_CONFIDENCE_THRESHOLD"] = "0.5"
config.reload_from_environment()
predictor.reset()

ordered, top = A.ml_order(fake, ctx)
check("The winning strategy is moved to the front", top == "third", str(ordered))
check("...and nothing is dropped", {n for n, _ in ordered} == {"first", "second", "third"})
check("...and nothing is invented", len(ordered) == 3)
budget = A.ml_wait_budget(ctx, 15000, floor_ms=3000)
check("A shorter wait reaches the automation", 3000 <= budget < 15000, str(budget))

os.environ["ML_CONFIDENCE_THRESHOLD"] = "0.99"
config.reload_from_environment()
predictor.reset()
ordered, top = A.ml_order(fake, ctx)
check("Below the confidence gate the automation keeps its own order",
      ordered == fake and top is None, str(ordered))

# SHADOW. Same model, same evidence, same threshold — and the automation's
# list comes back untouched. This is the default the layer ships in.
os.environ["ML_CONFIDENCE_THRESHOLD"] = "0.5"
os.environ["ML_MODE"] = "shadow"
config.reload_from_environment()
predictor.reset()
ordered, top = A.ml_order(fake, ctx)
check("In SHADOW mode the automation's own order survives untouched",
      ordered == fake and top is None, str(ordered))
check("...even though the model had a clear opinion",
      predictor.recommend_strategy(ctx, ["first", "second", "third"]
                                   ).shadow_order[:1] == ["third"])

for key in ("ML_ENABLED", "ML_MODE", "ML_MODEL_PATH", "ML_CONFIDENCE_THRESHOLD"):
    os.environ.pop(key, None)
config.reload_from_environment()
predictor.reset()
check("The switch returns to its default", config.ML_ENABLED is True)
check("...and the default MODE is shadow, so a fresh install changes nothing",
      config.ML_MODE == "shadow")

print()
print("=" * 70)
print("7. EVERY STRATEGY ATTEMPT IS JOINED TO THE WRITE IT CAUSED")
print("=" * 70)
check("update_one_view opens an episode", "ml_episode_begin(" in SRC)
check("...and always closes it, even when the write raises",
      "finally:" in SRC.split("def update_one_view")[1].split("\ndef ")[0]
      and "ml_episode_end(" in SRC.split("def update_one_view")[1].split("\ndef ")[0])
body = SRC.split("def update_one_view")[1].split("\ndef ")[0]
check("A confirmed read-back is recorded as VERIFIED",
      "ML_EPISODE_VERIFIED" in body)
check("A read-back that disagreed is recorded as MISMATCH — and BEFORE the "
      "raise, so the most informative failure there is survives",
      body.index("ML_EPISODE_MISMATCH") < body.index("was saved but not verified"))
check("A save nobody checked is UNVERIFIED with a null verdict, not a failure",
      "ML_EPISODE_UNVERIFIED" in body and "episode_verified = None" in body)
check("A dry run teaches the model nothing", "dry run; filled but not saved" in body)

# The real adapter, writing to a real file, joined by the real trainer.
telemetry_path = tmp / "episode_join.jsonl"
os.environ["ML_TELEMETRY_PATH"] = str(telemetry_path)
config.reload_from_environment()
A.ml_episode_begin("9451291275", "COE", "ETA", "04/09/2026")
first_ctx = A.ml_context(provider="HUB", page="manage", field="ETA", view="COE",
                         page_ready=True, frames=1,
                         attempt=A.ml_episode_attempt("find:ETA"))
A.ml_record(first_ctx, "label_exact", False, None, "FIELD_NOT_VISIBLE", rank=0)
A.ml_record(first_ctx, "css_id_visible", True, 700, "OK", rank=1)
second_ctx = A.ml_context(provider="HUB", page="manage", field="ETA", view="COE",
                          page_ready=True, frames=1,
                          attempt=A.ml_episode_attempt("find:ETA"))
A.ml_record(second_ctx, "verify_reload", True, 1500, "OK")
A.ml_episode_end(A.ML_EPISODE_VERIFIED, True, "ok")

check("The first look at a field is attempt=first",
      first_ctx["attempt"] == "first", str(first_ctx))
check("...and the read-back pass is attempt=later",
      second_ctx["attempt"] == "later", str(second_ctx))
check("page_ready and frames are actually populated now, not 'unknown'",
      first_ctx["page_ready"] == "yes" and first_ctx["frames"] == "one",
      str(first_ctx))

written = [json.loads(line) for line in
           telemetry_path.read_text(encoding="utf-8").splitlines() if line.strip()]
interactions = [e for e in written if e["kind"] == "interaction"]
episode_events = [e for e in written if e["kind"] == "episode"]
check("Every interaction carries an episode id",
      interactions and all(e.get("episode_id") for e in interactions),
      str(interactions))
check("...all of them the SAME episode",
      len({e["episode_id"] for e in interactions}) == 1)
check("...matching the episode event that closed it",
      len(episode_events) == 1
      and episode_events[0]["episode_id"] == interactions[0]["episode_id"])
check("The shipment reference is attached without the caller passing it",
      all(e.get("reference") == "9451291275" for e in interactions))
check("Retries are counted per strategy inside the episode",
      all("retries" in e for e in interactions))

# The suite tags its own telemetry, so join() must refuse it. Read the file
# directly to confirm the JOIN itself works, then confirm the refusal.
rows, report = episodes.join(events=[dict(e, source="automation") for e in written])
check("The trainer joins those rows to the verified outcome",
      report["kept"] == 3 and report["positive"] == 2 and report["negative"] == 1,
      str(report))
check("...the locator that missed is the negative",
      [r.strategy for r in rows if not r.label] == ["label_exact"],
      str([(r.strategy, r.label) for r in rows]))
real_rows, real_report = episodes.join(path=telemetry_path)
check("...but as written by a TEST run it is refused as training data",
      real_report["kept"] == 0 and real_report["dropped_not_real"] == 3,
      str(real_report))
os.environ.pop("ML_TELEMETRY_PATH", None)
config.reload_from_environment()

print()
print("=" * 70)
print("8. NOTHING THAT WORKED BEFORE WAS REMOVED")
print("=" * 70)
for name in ("get_dhl_result", "get_qatar_result", "get_portal_result",
             "_read_afkl_page", "extract_afkl_result", "run_with_retry",
             "classify_failure", "first_visible", "fill_date_field",
             "write_date_value", "find_field_ignoring_visibility",
             "save_manage_page", "update_one_view", "update_internal_shipment",
             "select_shipment_info_tab", "describe_manage_fields",
             "wait_for_any", "wait_until_settled", "wait_for_table_change",
             "type_into", "note_suppressed", "redact_secrets", "write_log"):
    check("{0}() still exists".format(name), hasattr(A, name))
check("PORTALS still carries AFKL and Astral",
      "AFKL" in A.PORTALS and "ASTRAL" in A.PORTALS)
check("The AWB prefix routing is unchanged",
      A.carrier_provider("whatever", "057-12345678") == "AFKL")
check("Telemetry is on by default so a model can eventually be trained",
      config.TELEMETRY_ENABLED is True)

print()
print("=" * 70)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 70)
sys.exit(1 if FAIL else 0)
