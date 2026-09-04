"""
ATLAS — the one interface the automation talks to.

Everything here answers with an opinion the caller is free to ignore, and
every path — no model, bad model, thin evidence, unknown context, an outright
bug in this file — ends in the same place: `Recommendation(used=False)`, which
means "keep doing what you were doing".

The automation never imports model.py, dataset.py or trainer.py. It imports
this, and this is the only thing that has to be defensive.

THREE MODES

    off      no opinion, ever. Identical to the package not being installed.
    shadow   the model is consulted and what it WOULD have chosen is recorded,
             and then the automation does exactly what it would have done
             anyway. THIS IS THE DEFAULT.
    active   the recommendation is acted on, subject to every gate below.

Shadow is the default because an off-policy replay of past telemetry cannot
settle whether the model is better — late candidates only ever appear in the
data when the earlier ones failed, so the holdout is a biased sample of hard
cases. Shadow mode records a decision for EVERY situation, easy ones included,
and that log is the unbiased evidence. Until it exists, the honest status of
any model here is "ready for learning, not proven superior", and the code says
so rather than letting a good-looking Wilson score speak for it.

WHAT "CONFIDENCE" MEANS HERE

Not a probability. The score is a Wilson lower bound — a pessimistic ranking
number — and this module never calls it a likelihood. Whether the model's
predicted rates are probabilities at all is a separate measured question,
answered by ml/calibration.py, and reported separately.
"""

import os
import random
import threading

from . import config, features, identity, model as model_module, telemetry

_lock = threading.Lock()
_model = None
_load_error = None
_loaded_from = None
_load_attempted = False


class Recommendation:
    """
    An answer the caller may take or leave.

    `used` False means the automation must proceed exactly as it would have
    without this package. Every other field is then advisory only.
    """

    __slots__ = ("used", "order", "top", "scores", "reason", "level",
                 "trials", "mode", "would_have_used", "shadow_order")

    def __init__(self, used=False, order=None, top=None, scores=None,
                 reason="", level=None, trials=0, mode=None,
                 would_have_used=False, shadow_order=None):
        self.used = used
        self.order = order or []
        self.top = top
        self.scores = scores or {}
        self.reason = reason
        self.level = level
        self.trials = trials
        self.mode = mode or config.ML_MODE
        # In shadow mode `used` is False but the model still had an opinion.
        # These two carry it, so the decision log can score shadow choices
        # later against what actually happened.
        self.would_have_used = would_have_used
        self.shadow_order = shadow_order or []

    def __repr__(self):
        return "Recommendation(used={0}, mode={1}, top={2!r}, reason={3!r})".format(
            self.used, self.mode, self.top, self.reason)


def _no(reason, **extra):
    return Recommendation(used=False, reason=reason, **extra)


def load_model(force=False):
    """
    Load the model file, once, lazily. Returns (model, error_text).

    A missing file is not an error worth shouting about — it is the normal
    state before anyone has trained anything. A corrupted file IS worth
    shouting about, and is reported, but still only results in the layer
    standing down.
    """
    global _model, _load_error, _loaded_from, _load_attempted
    with _lock:
        if _load_attempted and not force:
            return _model, _load_error
        _load_attempted = True
        _model = None
        _load_error = None
        _loaded_from = None
        path = config.active_model_path()
        try:
            if not os.path.exists(path):
                _load_error = "no model file at {0}".format(path)
                return None, _load_error
            with open(path, "r", encoding="utf-8") as handle:
                _model = model_module.StrategyModel.from_json(
                    handle.read(), feature_version=features.FEATURE_VERSION)
            _loaded_from = str(path)
            return _model, None
        except Exception as error:
            _model = None
            _load_error = "model at {0} could not be loaded: {1}".format(
                path, error)
            return None, _load_error


def reset():
    """Drop the cached model. Used by the tests and after retraining."""
    global _model, _load_error, _loaded_from, _load_attempted
    with _lock:
        _model = None
        _load_error = None
        _loaded_from = None
        _load_attempted = False


def status():
    model, error = load_model()
    meta = (model.meta if model else {}) or {}
    return {
        "enabled": config.ML_ENABLED,
        "mode": config.ML_MODE,
        "model_loaded": model is not None,
        "loaded_from": _loaded_from,
        "error": error,
        "summary": model.summary() if model else None,
        "label_rule": meta.get("label_rule"),
        "feature_version": features.FEATURE_VERSION,
        "promoted_at": meta.get("promoted_at"),
        "promotion_verdict": meta.get("promotion_verdict"),
        # The one sentence that must never be overstated. A model is only
        # "proven" once a promotion recorded a BETTER verdict against real
        # held-out production telemetry.
        "proven": bool(meta.get("promotion_verdict") == "BETTER"
                       and not meta.get("promotion_forced")),
        "config": config.snapshot(),
    }


def initialize(log=print):
    """
    Load the model once at startup and say plainly what state the layer is in.

    Called from update_eta.py's main(). Returns True when the model is loaded
    AND the switch is on — that is the only combination in which anything
    changes. Never raises: an unreadable model file means FALLBACK, not a
    failed run.
    """
    def emit(text):
        try:
            log("[{0}] {1}".format(identity.NAME, text))
        except Exception:
            pass

    def announce(label, detail=""):
        """An ATLAS-vocabulary line. Same shape as the ones a run emits."""
        try:
            log(identity.line(label, detail))
        except Exception:
            pass

    emit(identity.FULL_NAME)
    emit("Initializing...")
    try:
        if not config.ML_ENABLED:
            emit("ML_ENABLED is off")
            emit("Status: DISABLED")
            announce(identity.DETERMINISTIC_FALLBACK,
                     "the engine is switched off; the automation uses its own "
                     "hand-tuned order")
            return False

        emit("Mode: {0}".format(config.ML_MODE.upper()))
        model, error = load_model(force=True)
        if model is None:
            if error and "no model file" in error:
                emit("No trained model found at {0}".format(
                    config.active_model_path()))
                emit("Status: FALLBACK")
                announce(identity.DETERMINISTIC_FALLBACK,
                         "no trained model; the automation uses its own "
                         "hand-tuned order")
                emit("Telemetry is being collected; run "
                     "'python -m ml.trainer' once there is enough of it")
            else:
                emit("Model load failed: {0}".format(error))
                emit("Status: FALLBACK")
                announce(identity.DETERMINISTIC_FALLBACK,
                         "the model could not be loaded")
            return False

        summary = model.summary()
        meta = summary.get("meta") or {}
        emit("Model found: {0}".format(config.active_model_path()))
        emit("Model loaded successfully")
        emit("Model: {0}".format(identity.describe(
            model_module.MODEL_VERSION, features.FEATURE_VERSION)))
        emit("Model version: {0}   feature space: v{1}   built: {2}".format(
            model_module.MODEL_VERSION, features.FEATURE_VERSION,
            meta.get("built_at", "unknown")))
        emit("Label rule: {0}".format(meta.get("label_rule", "unknown")))
        emit("Model contents: {0} contexts, {1} observations".format(
            summary["cells"], summary["observations"]))
        emit("Support required: {0} per cell, {1} per strategy".format(
            config.MIN_SUPPORT, config.MIN_SUPPORT_PER_ARM))
        emit("Ranking score threshold: {0} (a Wilson lower bound, NOT a "
             "probability)".format(config.ML_CONFIDENCE_THRESHOLD))
        emit("Exploration: {0}".format(
            "on ({0:.0%})".format(config.ML_EXPLORATION_RATE)
            if config.ML_EXPLORATION_ENABLED else "off"))

        # The same word the dashboard shows, so the log and the UI can never
        # tell different stories. SHADOW is deliberately not ENABLED: a model
        # that is loaded and learning but steering nothing is a third state.
        emit("Status: {0}".format(
            "SHADOW" if config.ML_MODE == "shadow" else "ENABLED"))

        if meta.get("promotion_verdict") == "BETTER" and not meta.get("promotion_forced"):
            emit("Promoted {0} — {1}".format(
                meta.get("promoted_at", "?"), meta.get("promotion_reason", "")))
        else:
            # The claim this codebase is not allowed to overstate.
            emit("READY FOR LEARNING, NOT YET PROVEN SUPERIOR")
            emit("This model has not passed an evaluation gate against real "
                 "production telemetry.")

        if config.ML_MODE == "shadow":
            emit("SHADOW: recommendations are recorded and DISCARDED. The "
                 "deterministic order runs. Nothing this model says changes "
                 "what the automation does.")
            announce(identity.DETERMINISTIC_FALLBACK,
                     "shadow mode; every write this run is completed by the "
                     "deterministic order")
        else:
            emit("{0} may reorder candidates and shorten waits. It decides "
                 "nothing else — not which field, not what to write, not "
                 "whether a write is safe.".format(identity.NAME))
        return True
    except Exception as error:              # pragma: no cover - belt and braces
        emit("Initialization failed: {0}".format(error))
        emit("Status: FALLBACK")
        announce(identity.DETERMINISTIC_FALLBACK,
                 "initialisation failed; the automation uses its own order")
        return False


def active():
    """True when a recommendation could actually be acted on right now."""
    if not config.ML_ENABLED:
        return False
    model, _error = load_model()
    return model is not None


def recommend_strategy(context, strategies, log=None):
    """
    Rank `strategies` for `context`.

    `strategies` is the caller's own list of named approaches, in the caller's
    own preferred order. What comes back is either a reordering of exactly
    that list, or nothing.

    The result never contains a strategy the caller did not offer, and never
    omits one — reordering only. That is what keeps every existing safety
    guard intact: the candidates themselves, and the ETA/ATA guards baked into
    them, are the caller's and are untouched.
    """
    try:
        if not config.ML_ENABLED:
            return _no("ML_ENABLED is off")
        if not strategies or len(strategies) < 2:
            return _no("nothing to choose between")

        model, error = load_model()
        if model is None:
            return _no(error or "no model")

        keys = features.keys(context)
        verdict = model.assess(
            keys, list(strategies),
            min_observations=int(config.ML_MIN_OBSERVATIONS),
            min_support=int(config.MIN_SUPPORT),
            min_support_per_arm=int(config.MIN_SUPPORT_PER_ARM),
            quarantine_after=int(config.QUARANTINE_FAILURES))
        scores = verdict["scores"]
        level = verdict["level"]
        trials = verdict["trials"]
        best = max(scores.values()) if scores else 0.0

        # SUPPORT FIRST. A high score on four observations is not a finding,
        # and this gate refuses it before the threshold ever gets a say. It is
        # the difference between "the evidence points this way" and "the
        # arithmetic happened to come out high".
        if not verdict["has_support"]:
            result = _no("not enough evidence: {0}".format(
                "; ".join(verdict["support_reasons"])))
            _log_decision(context, None, scores, False, result.reason, log,
                          verdict=verdict)
            return result

        if best < config.ML_CONFIDENCE_THRESHOLD:
            result = _no("top ranking score {0:.2f} is below the {1:.2f} "
                         "threshold ({2:.0f} observations)".format(
                             best, config.ML_CONFIDENCE_THRESHOLD, trials))
            _log_decision(context, None, scores, False, result.reason, log,
                          verdict=verdict)
            return result

        # Quarantined strategies keep their place in the caller's list; they
        # are simply never promoted to the front. Removing them is not on the
        # table — the caller's candidate SET is the caller's.
        blocked = set(verdict["quarantined"])
        order = sorted(strategies,
                       key=lambda s: (s in blocked, -scores.get(s, 0.0),
                                      strategies.index(s)))

        # Exploration, when switched on, occasionally promotes a strategy the
        # model does not favour so an unlucky one can recover. It reorders;
        # it never removes.
        if (config.ML_EXPLORATION_ENABLED
                and random.random() < config.ML_EXPLORATION_RATE
                and len(order) > 1):
            pick = random.randrange(1, len(order))
            order = [order[pick]] + [s for i, s in enumerate(order) if i != pick]
            reason = "exploring: promoted {0}".format(order[0])
        else:
            reason = ("top ranking score {0:.2f} at backoff level {1} "
                      "({2:.0f} observations, margin {3:.2f})".format(
                          best, level, trials, verdict["margin"]))
            if blocked:
                reason += "; quarantined: " + ", ".join(sorted(blocked))

        drifted, drift_detail = model.drift(
            keys, order[0], threshold=config.DRIFT_THRESHOLD)
        if drifted:
            # The cell's recent behaviour disagrees with its history. That is
            # exactly when a learned order is least trustworthy, so it stands
            # down and the hand-tuned order takes over until the model is
            # retrained on the newer data.
            result = _no("drift detected ({0}); keeping the original order"
                         .format(drift_detail))
            _log_decision(context, None, scores, False, result.reason, log,
                          verdict=verdict)
            return result

        if set(order) != set(strategies) or len(order) != len(strategies):
            # Cannot happen, and if it ever does the automation keeps its own
            # order rather than running a list this module invented.
            return _no("internal ordering error; kept the original order")

        # SHADOW. The model has an opinion and it is recorded in full — but
        # `used` is False, so ml_order() in the automation keeps the original
        # list. This is the only way to collect unbiased evidence about
        # decisions the deterministic order gets right, which an off-policy
        # replay of past telemetry structurally cannot provide.
        if config.ML_MODE != "active":
            result = Recommendation(
                used=False, order=[], top=None, scores=scores,
                reason="shadow: would have put {0} first ({1})".format(
                    order[0], reason),
                level=level, trials=trials, would_have_used=True,
                shadow_order=order)
            _log_decision(context, order[0], scores, False, result.reason, log,
                          verdict=verdict, shadow=True)
            return result

        result = Recommendation(used=True, order=order, top=order[0],
                                scores=scores, reason=reason, level=level,
                                trials=trials)
        _log_decision(context, order[0], scores, True, reason, log,
                      verdict=verdict)
        return result
    except Exception as error:
        if not config.ML_FALLBACK_ENABLED:
            raise
        return _no("prediction failed ({0}); using the original order".format(error))


def recommend_wait(context, default_ms, floor_ms=500, log=None):
    """
    A wait budget for this context, or the caller's own default.

    The returned value is ALWAYS between `floor_ms` and `default_ms`. The model
    can make a wait shorter, never longer — the call site's own constant stays
    the ceiling, and ML_MAX_WAIT is a second ceiling on top of it. There is no
    path here that produces an unbounded wait.
    """
    try:
        if not config.ML_ENABLED:
            return default_ms, "ML_ENABLED is off"
        model, error = load_model()
        if model is None:
            return default_ms, error or "no model"

        observed, samples = model.timing(features.keys(context), q=0.90)
        if observed is None:
            return default_ms, "no timing evidence"

        # p90 of what has worked, plus half again as headroom.
        proposed = observed * 1.5
        capped = max(float(floor_ms),
                     min(float(default_ms), float(config.ML_MAX_WAIT), proposed))
        if capped >= default_ms:
            return default_ms, "evidence does not justify a shorter wait"

        # SHADOW changes NOTHING, and a wait budget is something. Shortening a
        # wait is a smaller intervention than reordering candidates, but it is
        # still the model steering the run — a shadow run whose timings differ
        # from a deterministic one is not the comparison shadow mode exists to
        # provide. The proposal is logged and dropped.
        if config.ML_MODE != "active":
            if log:
                try:
                    log(identity.line(
                        identity.DETERMINISTIC_FALLBACK,
                        "shadow; kept the {0:.0f}ms wait. Would have proposed "
                        "{1:.0f}ms (p90 of {2} successful waits was {3:.0f}ms)"
                        .format(float(default_ms), capped, samples, observed)))
                except Exception:
                    pass
            return default_ms, "shadow: would have proposed {0:.0f}ms".format(capped)

        if log:
            try:
                log(identity.line(
                    identity.STRATEGY_SELECTED,
                    "wait shortened to {0:.0f}ms from {1:.0f}ms (p90 of {2} "
                    "successful waits was {3:.0f}ms)".format(
                        capped, float(default_ms), samples, observed)))
            except Exception:
                pass
        return capped, "p90 of {0} successful waits was {1:.0f}ms".format(
            samples, observed)
    except Exception as error:
        if not config.ML_FALLBACK_ENABLED:
            raise
        return default_ms, "timing prediction failed ({0})".format(error)


def _log_decision(context, chosen, scores, used, reason, log,
                  verdict=None, shadow=False):
    """
    Record the decision, including the ones where ATLAS stood down.

    Declines are as informative as choices: a log full of "not enough
    evidence" is what tells an operator the layer is behaving, and a log full
    of shadow choices is the material the next evaluation is built from.

    THE ATTRIBUTION RULE LIVES HERE. `Strategy selected` is written only when
    `used` is true — when the automation really did reorder its candidates
    because of this. Shadow mode produces a choice and uses none of it, so it
    gets `Deterministic fallback` with the choice named as detail. Calling
    that "selected" would be the single easiest lie in the whole system to
    tell, and it would make every downstream count wrong.
    """
    try:
        if used:
            label = identity.STRATEGY_SELECTED
            detail = "{0} — {1}".format(chosen, reason)
        elif shadow:
            label = identity.DETERMINISTIC_FALLBACK
            detail = ("shadow; the automation's own order ran. Would have "
                      "selected {0}".format(chosen))
        else:
            label = identity.DETERMINISTIC_FALLBACK
            detail = reason

        telemetry.decision(context, chosen, scores, used, reason,
                           mode=config.ML_MODE, shadow=shadow,
                           support=(verdict or {}).get("has_support"),
                           trials=(verdict or {}).get("trials"),
                           level_key=(verdict or {}).get("level_key"),
                           label=label)
        if log:
            log(identity.line(label, "{0} [{1}]".format(
                detail, features.describe(context))))
    except Exception:
        pass
