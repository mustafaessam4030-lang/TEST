"""
The one interface the automation talks to.

Everything here answers with an opinion the caller is free to ignore, and
every path — no model, bad model, thin evidence, unknown context, an outright
bug in this file — ends in the same place: `Recommendation(used=False)`, which
means "keep doing what you were doing".

The automation never imports model.py, dataset.py or trainer.py. It imports
this, and this is the only thing that has to be defensive.
"""

import os
import random
import threading

from . import config, features, model as model_module, telemetry

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

    __slots__ = ("used", "order", "top", "scores", "reason", "level", "trials")

    def __init__(self, used=False, order=None, top=None, scores=None,
                 reason="", level=None, trials=0):
        self.used = used
        self.order = order or []
        self.top = top
        self.scores = scores or {}
        self.reason = reason
        self.level = level
        self.trials = trials

    def __repr__(self):
        return "Recommendation(used={0}, top={1!r}, reason={2!r})".format(
            self.used, self.top, self.reason)


def _no(reason):
    return Recommendation(used=False, reason=reason)


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
        path = config.ML_MODEL_PATH
        try:
            if not os.path.exists(path):
                _load_error = "no model file at {0}".format(path)
                return None, _load_error
            with open(path, "r", encoding="utf-8") as handle:
                _model = model_module.StrategyModel.from_json(handle.read())
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
    return {
        "enabled": config.ML_ENABLED,
        "model_loaded": model is not None,
        "loaded_from": _loaded_from,
        "error": error,
        "summary": model.summary() if model else None,
        "config": config.snapshot(),
    }


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
        scores, level, trials = model.score(
            keys, list(strategies),
            min_observations=int(config.ML_MIN_OBSERVATIONS))

        best = max(scores.values()) if scores else 0.0
        if best < config.ML_CONFIDENCE_THRESHOLD:
            result = _no("top score {0:.2f} is below the {1:.2f} threshold "
                         "({2} observations)".format(
                             best, config.ML_CONFIDENCE_THRESHOLD, trials))
            _log_decision(context, None, scores, False, result.reason, log)
            return result

        order = sorted(strategies, key=lambda s: (-scores.get(s, 0.0),
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
            reason = "top score {0:.2f} at backoff level {1} ({2} observations)".format(
                best, level, trials)

        if set(order) != set(strategies) or len(order) != len(strategies):
            # Cannot happen, and if it ever does the automation keeps its own
            # order rather than running a list this module invented.
            return _no("internal ordering error; kept the original order")

        result = Recommendation(used=True, order=order, top=order[0],
                                scores=scores, reason=reason, level=level,
                                trials=trials)
        _log_decision(context, order[0], scores, True, reason, log)
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
        return capped, "p90 of {0} successful waits was {1:.0f}ms".format(
            samples, observed)
    except Exception as error:
        if not config.ML_FALLBACK_ENABLED:
            raise
        return default_ms, "timing prediction failed ({0})".format(error)


def _log_decision(context, chosen, scores, used, reason, log):
    try:
        telemetry.decision(context, chosen, scores, used, reason)
        if log:
            log("ML: {0} -> {1} ({2})".format(
                features.describe(context),
                chosen if used else "kept the original order", reason))
    except Exception:
        pass
