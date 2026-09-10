"""
Configuration for the learning layer.

Every default here reproduces the Control Tower's original behaviour. Reading
this file should make it obvious that an unconfigured install is the old
install.
"""

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _number(name, default, cast=float):
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


# ── how the layer participates ───────────────────────────────────────
#
#   off     the package is inert; identical to it not being installed
#   shadow  it predicts, records what it WOULD have done, and changes
#           nothing. This is the default, and it is how a model earns the
#           right to be trusted: by being scored against what the
#           deterministic order actually did, on real runs.
#   active  its recommendation is followed, subject to every gate below
#
# Default is shadow rather than active. A model that has never been scored
# against production has no claim on production.
ML_MODE = (os.environ.get("ML_MODE") or "shadow").strip().lower()
if ML_MODE not in ("off", "shadow", "active"):
    ML_MODE = "shadow"


# ── the master switch ────────────────────────────────────────────────
# ON by default, because a layer nobody remembers to switch on is a layer
# nobody uses. It is safe to default on because the switch alone activates
# nothing: without a valid trained model the predictor answers "no opinion"
# to every question and the automation follows its own hand-tuned order.
# Enabled means "consult the model if there is one", not "behave differently".
# Set ML_ENABLED=0 to silence it completely.
ML_ENABLED = _flag("ML_ENABLED", True) and ML_MODE != "off"

# A recommendation is only used when the model is this sure. Below it the
# automation keeps its own order. 0.65 is deliberately cautious: at the
# default the model has to be clearly better than a coin toss before it is
# allowed to reorder anything.
ML_CONFIDENCE_THRESHOLD = _number("ML_CONFIDENCE_THRESHOLD", 0.65)

ML_MODEL_PATH = Path(os.environ.get("ML_MODEL_PATH")
                     or (HERE / "models" / "strategy_model.json"))

# When a prediction fails for any reason, fall back to the original order.
# Turning this off makes prediction errors fatal, which is only ever useful
# in a test.
ML_FALLBACK_ENABLED = _flag("ML_FALLBACK_ENABLED", True)

# Occasionally try a strategy the model does not favour, so a strategy that
# was unlucky early can recover. Off by default: exploration costs real
# seconds on a production run.
ML_EXPLORATION_ENABLED = _flag("ML_EXPLORATION_ENABLED", False)
ML_EXPLORATION_RATE = _number("ML_EXPLORATION_RATE", 0.10)

# A hard ceiling on any wait the model may recommend, in milliseconds. The
# model can only ever propose a budget BELOW the call site's own constant;
# this is a second ceiling on top of that, so no learned value can produce a
# long wait even if the telemetry is strange.
ML_MAX_WAIT = _number("ML_MAX_WAIT", 15000.0)

# How many observations a context needs before its own rate is trusted rather
# than its parent's. Below this the estimator backs off to a coarser context.
ML_MIN_OBSERVATIONS = _number("ML_MIN_OBSERVATIONS", 8, int)

# ── the label ────────────────────────────────────────────────────────
# A strategy attempt is only POSITIVE when the write it belongs to was read
# back from the Hub and confirmed. Anything short of that is not success.
# Episodes with no verification at all are UNLABELLED and excluded from
# training — they are not evidence either way, and counting them as negatives
# would be manufacturing data.
REQUIRE_VERIFIED_LABEL = _flag("ML_REQUIRE_VERIFIED_LABEL", True)

# ── the reward ───────────────────────────────────────────────────────
# Success dominates; the rest are costs that separate a fast clean win from a
# slow one that needed three attempts. Weights are deliberately visible and
# tunable rather than buried in the model.
W_SUCCESS = _number("ML_W_SUCCESS", 1.0)
W_LATENCY = _number("ML_W_LATENCY", 0.25)
W_RETRY = _number("ML_W_RETRY", 0.15)
W_FAILURE = _number("ML_W_FAILURE", 0.20)
W_VERIFY_FAIL = _number("ML_W_VERIFY_FAIL", 0.60)
LATENCY_REF_MS = _number("ML_LATENCY_REF_MS", 4000.0)

# ── recency and drift ────────────────────────────────────────────────
# Websites change. An observation from six months ago should not weigh the
# same as one from this morning.
HALF_LIFE_DAYS = _number("ML_HALF_LIFE_DAYS", 30.0)
DRIFT_WINDOW_DAYS = _number("ML_DRIFT_WINDOW_DAYS", 14.0)
DRIFT_THRESHOLD = _number("ML_DRIFT_THRESHOLD", 0.25)

# ── support and calibration ──────────────────────────────────────────
# Below this a cell has no opinion worth acting on, and confidence is
# reported as unavailable rather than guessed.
MIN_SUPPORT = _number("ML_MIN_SUPPORT", 30, int)
MIN_SUPPORT_PER_ARM = _number("ML_MIN_SUPPORT_PER_ARM", 8, int)
CALIBRATION_MIN_ROWS = _number("ML_CALIBRATION_MIN_ROWS", 200, int)

# ── model files ──────────────────────────────────────────────────────
CHAMPION_PATH = Path(os.environ.get("ML_CHAMPION_PATH")
                     or (HERE / "models" / "champion.json"))
CHALLENGER_PATH = Path(os.environ.get("ML_CHALLENGER_PATH")
                       or (HERE / "models" / "challenger.json"))

# A strategy that has failed this many times in a row recently is quarantined
# — never recommended — until it is seen to work again.
QUARANTINE_FAILURES = _number("ML_QUARANTINE_FAILURES", 5, int)


def active_model_path():
    """
    What the predictor loads.

    The CHAMPION, unless ML_MODEL_PATH was set explicitly. Training writes the
    challenger; only a promotion that passed the evaluator's gate writes the
    champion. Pointing production at the champion by default is what makes the
    gate mean something — otherwise every train would silently go live.
    """
    override = os.environ.get("ML_MODEL_PATH")
    return Path(override) if override else CHAMPION_PATH

# ── telemetry ────────────────────────────────────────────────────────
# Telemetry is ON by default and independent of ML_ENABLED: you cannot train
# a model without first collecting data, and collecting it changes nothing
# about how the automation behaves.
TELEMETRY_ENABLED = _flag("ML_TELEMETRY_ENABLED", True)
TELEMETRY_PATH = Path(os.environ.get("ML_TELEMETRY_PATH")
                      or (HERE / "data" / "telemetry.jsonl"))
TELEMETRY_MAX_BYTES = _number("ML_TELEMETRY_MAX_BYTES", 32 * 1024 * 1024, int)


def snapshot():
    """The live configuration, for the run log and the tests."""
    return {
        "ML_MODE": ML_MODE,
        "ML_ENABLED": ML_ENABLED,
        "REQUIRE_VERIFIED_LABEL": REQUIRE_VERIFIED_LABEL,
        "MIN_SUPPORT": MIN_SUPPORT,
        "MIN_SUPPORT_PER_ARM": MIN_SUPPORT_PER_ARM,
        "HALF_LIFE_DAYS": HALF_LIFE_DAYS,
        "CHAMPION_PATH": str(CHAMPION_PATH),
        "CHALLENGER_PATH": str(CHALLENGER_PATH),
        "ML_CONFIDENCE_THRESHOLD": ML_CONFIDENCE_THRESHOLD,
        "ACTIVE_MODEL_PATH": str(active_model_path()),
        "ML_MODEL_PATH": str(ML_MODEL_PATH),
        "ML_FALLBACK_ENABLED": ML_FALLBACK_ENABLED,
        "ML_EXPLORATION_ENABLED": ML_EXPLORATION_ENABLED,
        "ML_EXPLORATION_RATE": ML_EXPLORATION_RATE,
        "ML_MAX_WAIT": ML_MAX_WAIT,
        "ML_MIN_OBSERVATIONS": ML_MIN_OBSERVATIONS,
        "TELEMETRY_ENABLED": TELEMETRY_ENABLED,
        "TELEMETRY_PATH": str(TELEMETRY_PATH),
    }


def reload_from_environment():
    """Re-read the environment. Used by the tests to flip flags in-process."""
    module = globals()
    mode = (os.environ.get("ML_MODE") or "shadow").strip().lower()
    module["ML_MODE"] = mode if mode in ("off", "shadow", "active") else "shadow"
    module["ML_ENABLED"] = _flag("ML_ENABLED", True) and module["ML_MODE"] != "off"
    module["REQUIRE_VERIFIED_LABEL"] = _flag("ML_REQUIRE_VERIFIED_LABEL", True)
    module["MIN_SUPPORT"] = _number("ML_MIN_SUPPORT", 30, int)
    module["MIN_SUPPORT_PER_ARM"] = _number("ML_MIN_SUPPORT_PER_ARM", 8, int)
    module["HALF_LIFE_DAYS"] = _number("ML_HALF_LIFE_DAYS", 30.0)
    module["CHAMPION_PATH"] = Path(os.environ.get("ML_CHAMPION_PATH")
                                   or (HERE / "models" / "champion.json"))
    module["CHALLENGER_PATH"] = Path(os.environ.get("ML_CHALLENGER_PATH")
                                     or (HERE / "models" / "challenger.json"))
    module["ML_CONFIDENCE_THRESHOLD"] = _number("ML_CONFIDENCE_THRESHOLD", 0.65)
    module["ML_MODEL_PATH"] = Path(os.environ.get("ML_MODEL_PATH")
                                   or (HERE / "models" / "strategy_model.json"))
    module["ML_FALLBACK_ENABLED"] = _flag("ML_FALLBACK_ENABLED", True)
    module["ML_EXPLORATION_ENABLED"] = _flag("ML_EXPLORATION_ENABLED", False)
    module["ML_EXPLORATION_RATE"] = _number("ML_EXPLORATION_RATE", 0.10)
    module["ML_MAX_WAIT"] = _number("ML_MAX_WAIT", 15000.0)
    module["ML_MIN_OBSERVATIONS"] = _number("ML_MIN_OBSERVATIONS", 8, int)
    module["TELEMETRY_ENABLED"] = _flag("ML_TELEMETRY_ENABLED", True)
    module["TELEMETRY_PATH"] = Path(os.environ.get("ML_TELEMETRY_PATH")
                                    or (HERE / "data" / "telemetry.jsonl"))
    return snapshot()
