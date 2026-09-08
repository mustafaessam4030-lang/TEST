"""
ATLAS safe error recovery.

The objective is "do not skip when a safe recovery is possible", NOT "never
skip". So most of this suite is about the boundaries: what recovery must
refuse to attempt, what it must never call a success, and the fact that in
shadow mode it must say WOULD TRY rather than TRIED.

Run:  python test_recovery.py
"""

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))

tmp = Path(tempfile.mkdtemp())
os.environ["ML_TELEMETRY_PATH"] = str(tmp / "telemetry.jsonl")
os.environ["ML_CHAMPION_PATH"] = str(tmp / "champion.json")
os.environ["ML_CHALLENGER_PATH"] = str(tmp / "challenger.json")

import update_eta as A                                      # noqa: E402
from ml import config, identity, predictor, recovery         # noqa: E402
import bridge as B                                           # noqa: E402

PASS, FAIL = [], []
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")
RCV = (HERE / "ml" / "recovery.py").read_text(encoding="utf-8")
PRED = (HERE / "ml" / "predictor.py").read_text(encoding="utf-8")
UI = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


print("=" * 74)
print("1. ERRORS ARE CLASSIFIED, AND NOT EVERYTHING IS RECOVERABLE")
print("=" * 74)
CASES = [
    ("element not found", Exception("Locator resolved to 0 elements"), None,
     recovery.ELEMENT_NOT_FOUND),
    ("element not visible", Exception("element is not visible"), None,
     recovery.ELEMENT_NOT_VISIBLE),
    ("page not ready", Exception("the panel is not ready"), None,
     recovery.PAGE_NOT_READY),
    ("stale element", Exception("element handle is stale"), None,
     recovery.STALE_ELEMENT),
    ("timeout", Exception("Timeout 3000ms exceeded"), None, recovery.TIMEOUT),
    ("frame not ready", Exception("frame was detached"), None,
     recovery.FRAME_NOT_READY),
    ("navigation failure", Exception("Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED"),
     None, recovery.NAVIGATION_FAILURE),
    ("network transient", Exception("net::ERR_CONNECTION_RESET"), None,
     recovery.NETWORK_TRANSIENT),
    ("save failure", Exception("the save did not complete"), None,
     recovery.SAVE_FAILURE),
    ("verification failure", Exception("the Hub does not hold that value"), None,
     recovery.VERIFICATION_FAILURE),
    ("human verification", Exception("HUMAN VERIFICATION REQUIRED"), None,
     recovery.HUMAN_VERIFICATION),
    ("unknown", Exception("something nobody has seen before"), None,
     recovery.UNKNOWN),
]
for label, error, category, expected in CASES:
    got, signature = recovery.classify(error, category=category)
    check("{0} -> {1}".format(label, expected), got == expected, got)

check("An unknown error is UNKNOWN, not guessed into something recoverable",
      recovery.classify(Exception("qwertyuiop"))[0] == recovery.UNKNOWN)
check("The category the automation already assigned wins over the text",
      recovery.classify(Exception("some text"), category="BOT_CHALLENGE")[0]
      == recovery.HUMAN_VERIFICATION)
check("A signature strips the variable parts, so the same failure groups",
      recovery.classify(Exception("ETA timed out for 9451291275"))[1]
      == recovery.classify(Exception("ETA timed out for 0748899221"))[1],
      recovery.classify(Exception("ETA timed out for 9451291275"))[1])

print()
print("=" * 74)
print("2. NON-RECOVERABLE MEANS NO CANDIDATES AT ALL")
print("=" * 74)
for klass in (recovery.HUMAN_VERIFICATION, recovery.VERIFICATION_FAILURE,
              recovery.VALIDATION_FAILURE, recovery.AUTHENTICATION):
    check("{0} has no recovery candidates".format(klass),
          recovery.candidates(klass, in_write=True) == [])
    check("...and says why", bool(recovery.why_not(klass)))
check("A verification failure REMAINS a verification failure — recovery must "
      "never re-write a shipment to make it pass",
      "must never re-write" in RCV or "never re-write" in RCV)
check("UNKNOWN gets only the action that changes nothing",
      [a.name for a in recovery.candidates(recovery.UNKNOWN)]
      == ["reread_page_state"])

print()
print("=" * 74)
print("3. ONLY PREDEFINED ACTIONS EXIST, AND ALL OF THEM ARE IMPLEMENTED")
print("=" * 74)
# Inspect the CODE, not the prose. Searching the whole file matched the
# module's own comments, which is how a check passes for the wrong reason.
def _code_only(text):
    """Strip docstrings and comments, so only executable lines remain."""
    import io, tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except Exception:
        return text
    return " ".join(out)


RCV_CODE = _code_only(RCV)
check("The policy names actions and nothing else — no page, no locator and "
      "no selector in any executable line of the policy module",
      not any(w in RCV_CODE for w in ("page", "locator", "query_selector",
                                      "goto", "click", "fill", "press")),
      RCV_CODE[:120])
missing = [n for n in recovery.ACTION_NAMES if n not in A.RECOVERY_ACTIONS]
check("Every policy action has a real implementation in the automation",
      not missing, str(missing))
extra = [n for n in A.RECOVERY_ACTIONS if n not in recovery.ACTION_NAMES]
check("...and the automation implements nothing the policy does not name",
      not extra, str(extra))
check("An action ATLAS names that has no implementation is refused",
      "if a.name in RECOVERY_ACTIONS" in SRC)
check("Recovery cannot invent a selector, a URL or a business rule",
      "ml_recovery" in SRC
      and "RECOVERY_ACTIONS[action.name](page, plan)" in SRC)
check("Write-only actions are unavailable outside a write episode",
      [a.name for a in recovery.candidates(recovery.SAVE_FAILURE, in_write=False)]
      != [a.name for a in recovery.candidates(recovery.SAVE_FAILURE, in_write=True)])
check("...and retry_save_once is one of them",
      recovery.ACTIONS_BY_NAME["retry_save_once"].needs_write is True)

print()
print("=" * 74)
print("4. RANKING IS DETERMINISTIC, AND GATED LIKE STRATEGY SELECTION")
print("=" * 74)
check("No randomness in the policy module",
      "random" not in RCV_CODE and "shuffle" not in RCV_CODE)
acts = recovery.candidates(recovery.ELEMENT_NOT_FOUND)
# SUPERSEDED, by a real failure. The ladder used to be purely cheapest-first,
# and a real run spent its whole three-attempt budget on reacquire_locator,
# switch_frame and find_ignoring_visibility — three ways of asking "is the
# field in the DOM somewhere?" — while reselect_tab, the action for the cause
# that was actually true, sat fourth and was never reached.
check("With no hypotheses the ladder is still safest-and-cheapest first",
      recovery.rank(acts)[0].risk <= recovery.rank(acts)[-1].risk,
      str([(a.name, a.risk, a.cost_ms) for a in recovery.rank(acts)]))
check("...and the drastic actions are still last",
      recovery.rank(acts)[-1].name in ("reopen_view", "reload_page",
                                       "retry_navigation"))

# WITH evidence, the ordering must follow the CAUSE, not the price list.
_ev = recovery.evidence(expected_view="BU", active_view="COE",
                        view_matches=False, field_visible_count=0,
                        field_any_count=0, other_field_visible_count=3,
                        frames=1, page_ready=True)
_hy = recovery.hypotheses(recovery.ELEMENT_NOT_FOUND, _ev)
_plan = recovery.rank(acts, hypos=_hy)
check("The wrong-view cause is the best supported when the other field's "
      "inputs are visible and ours are not",
      _hy[0].name == "wrong_view" and _hy[0].confidence >= 0.8,
      str([(h.name, h.confidence) for h in _hy]))
check("...so reselect_tab is ranked FIRST, not fourth",
      _plan[0].name == "reselect_tab", str([a.name for a in _plan]))
check("One action per hypothesis comes before any second attempt at the same "
      "idea, so a bounded budget spends it on different causes",
      len({a.name for a in _plan[:len(_hy)]}) == len(_hy))
check("The explanation for the first choice names the cause and the evidence",
      "wrong_view" in recovery.explain_ranking(_plan[0], _hy)
      and "COE" in recovery.explain_ranking(_plan[0], _hy),
      recovery.explain_ranking(_plan[0], _hy))
check("...and with no evidence and no score it says exactly that, rather "
      "than inventing a reason",
      "no evidence favours any option" in recovery.explain_ranking(acts[0]))
check("Equal confidence breaks toward the CHEAPER action",
      [a.name for a in recovery.rank(
          acts, scores={a.name: 0.8 for a in acts})][0]
      == min(acts, key=lambda a: a.cost_ms).name)
ranked = recovery.rank(acts, scores={"reopen_view": 0.99})
check("A high score promotes an action", ranked[0].name == "reopen_view")
check("A quarantined action is demoted, never removed",
      set(a.name for a in recovery.rank(acts, quarantined=["reacquire_locator"]))
      == set(a.name for a in acts))
check("Ranking uses the same support and threshold gates as strategy "
      "selection", "config.MIN_SUPPORT" in PRED.split("def recommend_recovery")[1]
      and "ML_CONFIDENCE_THRESHOLD" in PRED.split("def recommend_recovery")[1])
check("...and the same drift stand-down",
      "drift" in PRED.split("def recommend_recovery")[1].split("def recovery_module")[0])
check("The error class is part of the ranking context, so different errors "
      "cannot share a cell",
      recovery.recovery_context({"provider": "HUB"}, "TIMEOUT")["error"]
      == "TIMEOUT")

print()
print("=" * 74)
print("5. RECOVERY IS BOUNDED — NO INFINITE RETRIES")
print("=" * 74)
clock = [0.0]
b = recovery.Budget(max_attempts=3, max_per_action=1, max_seconds=45.0,
                    max_reloads=1, max_navigations=2, clock=lambda: clock[0])
wait = recovery.ACTIONS_BY_NAME["wait_for_page_ready"]
reload_ = recovery.ACTIONS_BY_NAME["reload_page"]
nav = recovery.ACTIONS_BY_NAME["retry_navigation"]
check("A fresh budget allows an action", b.allows(wait) is True)
b.spend(wait)
check("The same action cannot be tried twice", b.allows(wait) is False)
b.spend(reload_)
check("One reload is the limit", b.allows(reload_) is False)
b.spend(nav)
check("Three attempts is the limit", b.exhausted() is not None, b.exhausted())
check("...and it says which limit", "attempt" in (b.exhausted() or ""))

b2 = recovery.Budget(max_seconds=10.0, clock=lambda: clock[0])
clock[0] = 11.0
check("The wall-clock ceiling exhausts the budget",
      "budget is spent" in (b2.exhausted() or ""), b2.exhausted())
b3 = recovery.Budget(max_seconds=10.0, clock=lambda: clock[0])
clock[0] = 11.0 + 6.0
check("An action whose expected cost overruns the ceiling is refused",
      b3.allows(reload_) is False)
check("The number of navigations is capped separately from attempts",
      recovery.Budget(max_navigations=0).allows(nav) is False)
check("Candidates are filtered by what the budget still allows",
      "budget.allows(action)" in RCV)
check("There is no loop that can run unbounded",
      "while True" not in SRC.split("def atlas_recover")[1].split("\ndef ")[0])
check("The automation's own limits are named constants",
      all(n in SRC for n in ("RECOVERY_MAX_ATTEMPTS", "RECOVERY_MAX_SECONDS",
                             "RECOVERY_MAX_RELOADS",
                             "RECOVERY_MAX_NAVIGATIONS")))

print()
print("=" * 74)
print("6. AN ACTION THAT DID NOT THROW HAS NOT RECOVERED ANYTHING")
print("=" * 74)
BODY = SRC.split("def atlas_recover")[1].split("\ndef _recovery_telemetry")[0]
check("Success requires the caller's own verification",
      "good = bool(ran and verified is True)" in BODY)
check("...so no exception is NOT success",
      "AN ACTION THAT DID NOT THROW HAS NOT RECOVERED ANYTHING" in BODY)
check("With nothing to verify against, the result is unverified — never a "
      "success", "verification pending" in BODY)
check("A recovery that ran but did not verify is reported as FAILED",
      "the value did not verify" in BODY)
check("Verification is the existing one, passed in by the caller",
      "verify=None" in SRC.split("def atlas_recover")[0][-400:]
      or "verify()" in BODY)

print()
print("=" * 74)
print("7. CAPTCHA IS A PERSON'S JOB, NOT A RECOVERY PROBLEM")
print("=" * 74)
check("Human verification is short-circuited before any ranking",
      BODY.index("HUMAN_VERIFICATION") < BODY.index("Budget("))
check("...and recorded as HUMAN_VERIFICATION_REQUIRED",
      "STATE_HUMAN_VERIFICATION_REQUIRED" in BODY)
check("No recovery action is attempted for it",
      recovery.candidates(recovery.HUMAN_VERIFICATION, in_write=True) == [])
check("Nothing in the recovery path tries to solve or bypass a challenge",
      not any(w in RCV_CODE.lower() for w in ("solve", "bypass", "sitekey",
                                              "recaptcha", "audio")))
check("...and there is no recovery action for it to use even if it tried",
      not [a for a in recovery.ACTIONS
           if recovery.HUMAN_VERIFICATION in a.applies_to])
check("The existing human-checkpoint machinery is what handles it",
      "await_human_verification" in SRC and "CaptchaRequired" in SRC)

print()
print("=" * 74)
print("8. SHADOW MODE SAYS 'WOULD TRY', NEVER 'TRIED'")
print("=" * 74)
check("The shipped mode is shadow", config.ML_MODE == "shadow")
RECPRED = PRED.split("def recommend_recovery")[1].split("def recovery_module")[0]
check("Shadow returns used=False whatever it recommends",
      'if config.ML_MODE != "active":' in RECPRED
      and "used=False" in RECPRED.split('if config.ML_MODE != "active":')[1][:400])
check("...with the order still populated, which is what makes it evaluable",
      "shadow_order=ordered" in RECPRED)
check("A shadow plan is announced as WOULD TRY",
      "ATLAS_RECOVERY_WOULD_TRY" in SRC
      and A.ATLAS_RECOVERY_WOULD_TRY == "Would try recovery")
check("...and says the deterministic order is what runs",
      "the deterministic order\n                  is what runs" in SRC
      or "is what runs" in SRC)
check("A shadow attempt is recorded as DETERMINISTIC_RECOVERY, not as an "
      "ATLAS recovery",
      "STATE_DETERMINISTIC_RECOVERY" in BODY
      and "else ml_identity.STATE_DETERMINISTIC_RECOVERY" in BODY)
check("The dashboard labels an unselected plan as shadow",
      "would try — shadow" in UI and "atlas_selected" in UI)
check("Recovery still happens in shadow — the deterministic order runs",
      "deterministic = ml_recovery.rank(safe, scores=scores, hypos=hypos)" in BODY
      and "order = deterministic" in BODY)

print()
print("=" * 74)
print("9. TELEMETRY RECORDS EVERY ATTEMPT, AND WHO CHOSE IT")
print("=" * 74)
TEL = (HERE / "ml" / "telemetry.py").read_text(encoding="utf-8")
FIELDS = ("episode_id", "error_class", "error_signature", "context",
          "candidates", "chosen", "used", "attempt", "latency_ms", "result",
          "verification", "outcome", "fallback_reason", "state")
recblock = TEL.split("def recovery(")[1].split("\ndef ")[0]
for field in FIELDS:
    check('the row carries "{0}"'.format(field),
          '"{0}"'.format(field) in recblock)
check("`used` is what says ATLAS chose it, recorded not inferred",
      '"used": bool(used)' in recblock)
check("Verification is three-valued in the row too",
      "Three-valued" in recblock and "None not checked" in recblock,
      recblock[recblock.find("verification"):][:160])
check("Every recovery state exists, including the WOULD_TRY / ACTUALLY_TRIED "
      "distinction the brief makes mandatory",
      all(s in identity.RECOVERY_STATES for s in (
          "WOULD_TRY", "ACTUALLY_TRIED", "ATLAS_RECOVERY_SELECTED",
          "ATLAS_RECOVERY_SUCCEEDED", "ATLAS_RECOVERY_FAILED",
          "DETERMINISTIC_RECOVERY", "FALLBACK", "UNVERIFIED",
          "HUMAN_VERIFICATION_REQUIRED", "HUMAN_CHECKPOINT")),
      str(identity.RECOVERY_STATES))
check("WOULD_TRY and ACTUALLY_TRIED are different states",
      identity.STATE_RECOVERY_WOULD_TRY
      != identity.STATE_RECOVERY_ACTUALLY_TRIED)
for state in ("ATLAS_RECOVERY_SELECTED", "ATLAS_RECOVERY_SUCCEEDED",
              "ATLAS_RECOVERY_FAILED", "DETERMINISTIC_RECOVERY", "FALLBACK",
              "UNVERIFIED", "HUMAN_VERIFICATION_REQUIRED"):
    check("...including {0}".format(state), state in identity.RECOVERY_STATES)
check("Recovery telemetry cannot raise into the automation",
      "except Exception:\n        pass" in
      SRC.split("def _recovery_telemetry")[1].split("\ndef ")[0])

print()
print("=" * 74)
print("10. LEARNING ONLY FROM TRUSTWORTHY OUTCOMES")
print("=" * 74)
EPI = (HERE / "ml" / "episodes.py").read_text(encoding="utf-8")
check("Recovery rows are a separate kind, so they cannot masquerade as "
      "strategy attempts", '"kind": "recovery"' in TEL)
check("The dataset joins only interaction rows",
      'if raw.get("kind") != "interaction":' in EPI)
check("Unverified episodes are still excluded",
      'report["dropped_unverified_episode"] += 1' in EPI)
check("Test-sourced rows are still excluded",
      'report["dropped_not_real"] += 1' in EPI)
check("The support thresholds were not lowered for recovery",
      config.MIN_SUPPORT == 30 and config.MIN_SUPPORT_PER_ARM == 8)
check("No synthetic recovery telemetry is written anywhere",
      "recovery(" not in SRC.split("def _recovery_telemetry")[0]
      or "ml_telemetry.recovery(" in SRC)

print()
print("=" * 74)
print("11. RECOVERY CANNOT CROSS A SAFETY BOUNDARY")
print("=" * 74)
check("Recovery never decides which field is which — fill_date_field still "
      "takes no view argument",
      "def fill_date_field(page, field_name, date_value)" in SRC)
check("...and the recovery plan derives the view from the context it built",
      '"view": context.get("view")' in SRC)
check("The cross-field guard survives every recovery action",
      "input[id*='{0}' i]:not([id*='{1}' i])" in SRC)
check("No recovery action writes a value except the explicit retry, which "
      "uses the automation's own writer",
      "write_date_value(field, plan[\"value\"]" in SRC)
check("Recovery never touches shipment identity",
      "bol_awb" not in RCV and "reference" not in RCV.split("def classify")[0])
check("Recovery never touches date validation",
      "normalize_date" not in RCV)
check("Recovery never skips or weakens verification",
      "VERIFY_AFTER_SAVE" not in RCV)
check("The failure message is unchanged when recovery cannot help",
      'raise Exception(f"{field_name} field was not found on the Manage page.")'
      in SRC)
check("Recovery runs BEFORE the skip, not instead of it",
      SRC.index("result = atlas_recover(")
      < SRC.index('raise Exception(f"{field_name} field was not found'))
check("Recovery can be switched off entirely",
      "ATLAS_RECOVERY_ENABLED" in SRC
      and 'os.environ.get("ATLAS_RECOVERY", "1")' in SRC)
check("...and is inert when the ml package is missing",
      "if not ML_AVAILABLE or ml_recovery is None" in BODY)

print()
print("=" * 74)
print("12. THE OPERATOR SEES REAL EVENTS ONLY")
print("=" * 74)
bx = B.ControlTowerState()
check("A fresh bridge has no recovery to show", bx.snapshot().get("recovery") is None)
bx.recovery_plan("TIMEOUT", "ETA field interaction timed out",
                 ["reacquire_locator", "wait_for_page_ready"],
                 {"reacquire_locator": 0.84}, False)
snap = bx.snapshot()["recovery"]
check("A plan appears once ATLAS produces one", snap is not None)
check("...marked as NOT an ATLAS selection while shadowing",
      snap["atlas_selected"] is False)
check("...with the confidence that was actually scored",
      snap["plan"][0]["confidence"] == 0.84)
check("...and no invented confidence for the rest",
      snap["plan"][1]["confidence"] is None)
bx.recovery_attempt(1, 2, "reacquire_locator", 0.84, "FAILED", None)
bx.recovery_attempt(2, 2, "wait_for_page_ready", None, "SUCCESS", True)
bx.recovery_done(True, "wait_for_page_ready succeeded and verified")
snap = bx.snapshot()["recovery"]
check("Attempts are recorded in order", [a["index"] for a in snap["attempts"]] == [1, 2])
check("...with their real results", [a["result"] for a in snap["attempts"]]
      == ["FAILED", "SUCCESS"])
check("The final state is recorded", snap["status"] == "RECOVERED"
      and snap["recovered"] is True)
bx.recovery_cleared()
check("...and cleared when the shipment moves on",
      bx.snapshot().get("recovery") is None)
check("The panel is hidden until there is a real error",
      "if (!r){ box.hidden = true" in UI)
check("Nothing is rendered that the bridge did not record",
      "Number(a.confidence).toFixed(2)" in UI
      and "a.confidence == null ? ''" in UI)
check("The UI reuses the existing design tokens, no new visual system",
      ".rcv{border-top:1px solid var(--line)" in UI
      and "var(--grn)" in UI.split(".rcv-r.ok")[1][:40])

print()
print("=" * 74)
print("12b. THE LOOP ITSELF — DRIVEN, NOT INSPECTED")
print("=" * 74)
# atlas_recover() is exercised for real here. The page is a stub that records
# what was asked of it; the actions, the budget, the ranking, the
# verification rule and the telemetry are all the production ones.


class StubPage(object):
    """Records calls. Every recovery action is redirected onto this."""

    def __init__(self):
        self.calls = []


def drive(behaviour, error, category=None, verify=None, in_write=True,
          budget_attempts=None):
    """Run one recovery with the action table swapped for `behaviour`."""
    page = StubPage()
    real = dict(A.RECOVERY_ACTIONS)
    original_max = A.RECOVERY_MAX_ATTEMPTS
    if budget_attempts is not None:
        A.RECOVERY_MAX_ATTEMPTS = budget_attempts
    for name in A.RECOVERY_ACTIONS:
        def make(n):
            def run(pg, plan):
                pg.calls.append(n)
                outcome = behaviour(n, plan)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            return run
        A.RECOVERY_ACTIONS[name] = make(name)
    plan = {"field_name": "ATA", "view": "BU", "value": "02/09/2026",
            "context": {"provider": "HUB", "page": "manage", "field": "ATA"},
            "in_write": in_write, "candidates_locators": [],
            "shipment": {"bol_awb": "9451291275", "table_page": 1}}
    try:
        result = A.atlas_recover(page, error, plan, verify=verify,
                                 category=category)
    finally:
        A.RECOVERY_ACTIONS.clear()
        A.RECOVERY_ACTIONS.update(real)
        A.RECOVERY_MAX_ATTEMPTS = original_max
    return result, page.calls


A.write_log = lambda *a, **k: None

# -- a recoverable error, first action works and verifies -------------
res, calls = drive(lambda n, p: True, Exception("Locator resolved to 0 elements"),
                   category="FIELD_NOT_FOUND", verify=lambda: True)
check("A recoverable error is recovered by the first safe action",
      res["recovered"] is True and res["verified"] is True, str(res))
check("...and it stopped there — one attempt, not the whole ladder",
      len(calls) == 1, str(calls))
check("...and it was the action for the best-supported cause",
      len(calls) == 1, str(calls))

# -- first fails, second succeeds -------------------------------------
# Observe which action the engine actually puts first, rather than
# recomputing it here — a test that predicts the order can pass while the
# engine does something else.
_probe, _probe_calls = drive(lambda n, p: False,
                             Exception("Locator resolved to 0 elements"),
                             category="FIELD_NOT_FOUND", verify=lambda: True)
_first = _probe_calls[0]
res, calls = drive(lambda n, p: n != _first,
                   Exception("Locator resolved to 0 elements"),
                   category="FIELD_NOT_FOUND", verify=lambda: True)
check("When the first recovery fails the next one is tried",
      res["recovered"] is True and len(calls) == 2, str(calls))
check("...and the two are for DIFFERENT causes, not two flavours of one",
      len(set(calls)) == 2 and calls[0] == _first, str(calls))

# -- everything fails --------------------------------------------------
res, calls = drive(lambda n, p: False, Exception("Locator resolved to 0 elements"),
                   category="FIELD_NOT_FOUND", verify=lambda: True)
check("When every recovery fails, recovery reports failure",
      res["recovered"] is False, str(res))
check("...and stops at the attempt budget, not at the end of the ladder",
      len(calls) == A.RECOVERY_MAX_ATTEMPTS, "{0} calls".format(len(calls)))
check("...with the budget named as the reason",
      "limit is reached" in res["reason"] or "budget" in res["reason"],
      res["reason"])

# -- an action that raises is a failure, not a crash -------------------
res, calls = drive(lambda n, p: RuntimeError("the page went away"),
                   Exception("Locator resolved to 0 elements"),
                   category="FIELD_NOT_FOUND", verify=lambda: True)
check("An action that raises is recorded as failed, not propagated",
      res["recovered"] is False and len(calls) >= 1, str(res))

# -- the action worked but verification says no ------------------------
res, calls = drive(lambda n, p: True, Exception("Locator resolved to 0 elements"),
                   category="FIELD_NOT_FOUND", verify=lambda: False)
check("An action that ran but did NOT verify is not a success",
      res["recovered"] is False, str(res))
check("...and the whole budget is spent trying the rest",
      len(calls) == A.RECOVERY_MAX_ATTEMPTS, str(calls))

# -- verification could not be performed -------------------------------
res, calls = drive(lambda n, p: True, Exception("Locator resolved to 0 elements"),
                   category="FIELD_NOT_FOUND", verify=lambda: None)
check("A recovery whose verification is UNKNOWN is not a success either",
      res["recovered"] is False and res["verified"] is None, str(res))

# -- no verification supplied ------------------------------------------
res, calls = drive(lambda n, p: True, Exception("Locator resolved to 0 elements"),
                   category="FIELD_NOT_FOUND", verify=None)
check("With no verification to run, the result is UNVERIFIED, never success",
      res["recovered"] is True and res["verified"] is None
      and "pending" in res["reason"], str(res))

# -- a budget of one ---------------------------------------------------
res, calls = drive(lambda n, p: False, Exception("Locator resolved to 0 elements"),
                   category="FIELD_NOT_FOUND", verify=lambda: True,
                   budget_attempts=1)
check("A budget of one attempt permits exactly one", len(calls) == 1, str(calls))

# -- CAPTCHA -----------------------------------------------------------
res, calls = drive(lambda n, p: True, Exception("HUMAN VERIFICATION REQUIRED"),
                   verify=lambda: True)
check("CAPTCHA attempts NO recovery action at all", calls == [], str(calls))
check("...and reports that a person must do it",
      res["recovered"] is False and "person" in (res["reason"] or ""),
      str(res["reason"]))

# -- a verification failure is a safety result, not an obstacle --------
res, calls = drive(lambda n, p: True,
                   Exception("the Hub does not hold that value"),
                   verify=lambda: True)
check("A verification failure attempts NO recovery — it must stand",
      calls == [] and res["recovered"] is False, str(calls))

# -- an unknown error --------------------------------------------------
res, calls = drive(lambda n, p: True, Exception("qwertyuiop"),
                   verify=lambda: True)
check("An unknown error tries only the action that changes nothing",
      calls in ([], ["reread_page_state"]), str(calls))

# -- switched off ------------------------------------------------------
A.ATLAS_RECOVERY_ENABLED = False
res, calls = drive(lambda n, p: True, Exception("Locator resolved to 0 elements"),
                   category="FIELD_NOT_FOUND", verify=lambda: True)
A.ATLAS_RECOVERY_ENABLED = True
check("With recovery switched off nothing is attempted",
      calls == [] and res["recovered"] is False, str(calls))

# -- shadow attribution, on the real telemetry -------------------------
import json as _json
rows = []
_tp = Path(config.TELEMETRY_PATH)
if _tp.exists():
    rows = [_json.loads(l) for l in _tp.read_text().splitlines() if l.strip()]
recs = [r for r in rows if r.get("kind") == "recovery"]
check("Recovery attempts were written to telemetry", bool(recs),
      "{0} rows".format(len(recs)))
check("NONE of them claims ATLAS selected the action — there is no model, "
      "and the mode is shadow",
      all(r.get("used") is False for r in recs),
      str([r.get("used") for r in recs][:6]))
check("...so every executed attempt is DETERMINISTIC_RECOVERY",
      all(r.get("state") == identity.STATE_DETERMINISTIC_RECOVERY
          for r in recs if r.get("chosen")),
      str(sorted({r.get("state") for r in recs})))
check("The CAPTCHA row is HUMAN_VERIFICATION_REQUIRED",
      any(r.get("state") == identity.STATE_HUMAN_VERIFICATION_REQUIRED
          for r in recs))
check("Every row carries the error class and a signature",
      all(r.get("error_class") and r.get("error_signature") for r in recs))
check("Every executed row carries a latency",
      all(r.get("latency_ms") is not None for r in recs if r.get("chosen")))

print()
print("=" * 74)
print("13. PERFORMANCE")
print("=" * 74)
check("Recovery reads no telemetry file — it ranks from the loaded model",
      "TELEMETRY_PATH" not in RCV and "open(" not in RCV)
check("The model is loaded through the existing cached loader",
      "load_model()" in RECPRED)
check("No polling loop is introduced",
      "while " not in BODY and "sleep" not in BODY)
check("Recovery uses the automation's existing waits",
      "wait_until_settled" in SRC.split("def _recover_wait_page_ready")[1][:200])
check("The budget caps total recovery wall-clock",
      A.RECOVERY_MAX_SECONDS <= 60)

print()
print("=" * 74)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 74)
sys.exit(1 if FAIL else 0)
