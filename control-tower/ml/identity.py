"""
ATLAS — Adaptive Logistics Strategy Engine.

The name, the version scheme, and the vocabulary of things ATLAS is allowed to
say about itself. Everything else in the package and in the automation imports
from here, so the name is spelled once and a rename is one edit.

WHAT ATLAS IS

The strategy layer, and only that. It sits between the deterministic safety
rules and the existing automation:

    Deterministic Safety      the candidate SET, the ETA/ATA guards
            |                 — ATLAS cannot touch any of this
       Safe Candidates
            |
         ATLAS               chooses an ORDER, and a wait no longer than
            |                the call site's own constant
    Existing Automation      unchanged; it does the work
            |
          Save
            |
    Read-back Verification   deterministic; ATLAS has no say
            |
     Verified Result

ATLAS is not a second automation. It never decides which field to write, what
to write into it, whether a value is valid, or whether verification may be
skipped. Remove it and the automation runs exactly as it did before.

THE ONE RULE ABOUT ATTRIBUTION

A line beginning "ATLAS →" is a claim that ATLAS did something. It may only be
written when ATLAS actually did that thing:

  * `Strategy selected` requires that a recommendation was USED — the
    automation reordered its candidates because of it. In shadow mode nothing
    is used, so nothing is selected, and the honest line is
    `Deterministic fallback`.

  * `Action completed` requires that read-back verification CONFIRMED the
    value is in the Hub, AND that ATLAS influenced the write. Neither half is
    optional. With verification off there is no completion to claim and the
    line is `Action unverified`.

Everything here is a plain string. Nothing in this module can fail, so nothing
in it needs a try/except at its call sites.
"""

NAME = "ATLAS"
FULL_NAME = "Adaptive Logistics Strategy Engine"
DISPLAY = "{0} — {1}".format(NAME, FULL_NAME)

# What ATLAS is, in one line, for a tooltip or a status panel.
ROLE = ("the strategy layer: it chooses the ORDER in which known-safe "
        "candidates are tried, and nothing else")

# ── the vocabulary ───────────────────────────────────────────────────
#
# Ordered roughly as they occur in a write. Each has exactly one truth
# condition and it is written next to it, because the temptation to reach for
# a nicer-sounding label is what turns a status line into a decoration.

STRATEGY_SELECTED = "Strategy selected"
# ATLAS's order was USED. Not shadow, not declined.

STRATEGY_FAILED = "Strategy failed"
# The strategy ATLAS put first was tried and did not work — it did not find
# the field, or the value it led to did not survive the save.

FALLBACK_ACTIVATED = "Fallback activated"
# ATLAS steered, its candidates missed, and the automation's own safety net
# (the visibility-free lookup) took over. A runtime fallback INSIDE an
# ATLAS-influenced attempt.

DETERMINISTIC_FALLBACK = "Deterministic fallback"
# ATLAS did not steer at all: no model, too little evidence, below threshold,
# drift, or shadow mode. The automation's hand-tuned order ran. This is the
# normal, expected line — it is what a healthy install prints all day.

VERIFICATION_PASSED = "Verification passed"
# Read-back confirmed the value, on a write ATLAS influenced.

ACTION_COMPLETED = "Action completed"
# Verified AND influenced. The only success claim in the vocabulary, and the
# most tightly guarded line in the system.

ACTION_UNVERIFIED = "Action unverified"
# The write went through but nothing read it back (VERIFY_AFTER_SAVE off).
# Not a success and never reported as one.

LABELS = (
    STRATEGY_SELECTED, STRATEGY_FAILED, FALLBACK_ACTIVATED,
    DETERMINISTIC_FALLBACK, VERIFICATION_PASSED, ACTION_COMPLETED,
    ACTION_UNVERIFIED,
)

# Labels that assert ATLAS influenced the outcome. The tests check that none
# of these can be emitted for an episode ATLAS did not steer.
INFLUENCE_LABELS = (
    STRATEGY_SELECTED, STRATEGY_FAILED, FALLBACK_ACTIVATED,
    VERIFICATION_PASSED, ACTION_COMPLETED,
)

# Labels that assert a verified success. Only reachable after read-back.
SUCCESS_LABELS = (ACTION_COMPLETED,)

ARROW = "→"


def line(label, detail=""):
    """
    One ATLAS event, formatted.

        ATLAS → Strategy selected: xpath_ata_date (0.84 over 696 observations)

    Callers pass a label from this module rather than a string of their own, so
    a typo cannot invent a new kind of claim.
    """
    text = "{0} {1} {2}".format(NAME, ARROW, label)
    detail = str(detail or "").strip()
    return "{0}: {1}".format(text, detail) if detail else text


def identifier(model_version, feature_version):
    """
    The model identifier, e.g. `ATLAS/4.2`.

    Two numbers because two things can change independently and both break
    compatibility: the file schema (4) and the feature space the cells are
    keyed on (2). A model whose second number does not match this build is
    refused rather than loaded into a table it does not fit.
    """
    return "{0}/{1}.{2}".format(NAME, model_version, feature_version)


def describe(model_version, feature_version):
    """The full identity, for a startup banner or an about box."""
    return "{0} ({1})".format(identifier(model_version, feature_version), FULL_NAME)
