"""
The optional learning layer for the Control Tower.

Nothing in here is required. `update_eta.py` imports it defensively and, if the
import fails for any reason at all, runs exactly as it did before this package
existed. With ML_ENABLED unset — the default — the predictor returns "no
opinion" to every question and the automation follows its own hand-tuned order,
so the switch being off is indistinguishable from the package being absent.

The pieces:

    config      flags, all defaulting to the original behaviour
    features    an interaction context -> a stable, hashable feature key
    telemetry   append-only JSONL of what was tried and what happened
    dataset     read that JSONL back into rows
    model       the estimator itself, and its JSON form
    trainer     telemetry -> model file
    predictor   model file -> recommendations, with a confidence gate
    evaluator   baseline vs model, measured on held-out telemetry

Standard library only. The automation's sole dependency is Playwright, and a
learning layer is not a good reason to make an operator install a toolchain.
"""

__all__ = [
    "config", "features", "telemetry", "dataset", "model",
    "trainer", "predictor", "evaluator",
]
