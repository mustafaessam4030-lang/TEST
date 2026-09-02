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


# ── the master switch ────────────────────────────────────────────────
# OFF. The layer is inert until someone has telemetry, has trained a model,
# and has seen the evaluator say it beats the baseline.
ML_ENABLED = _flag("ML_ENABLED", False)

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
        "ML_ENABLED": ML_ENABLED,
        "ML_CONFIDENCE_THRESHOLD": ML_CONFIDENCE_THRESHOLD,
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
    module["ML_ENABLED"] = _flag("ML_ENABLED", False)
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
