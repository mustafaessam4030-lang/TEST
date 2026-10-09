"""
What one strategy attempt was actually worth.

The first version of this layer ranked on a single bit: did the locator become
visible. That bit cannot tell a clean instant win from one that took nine
seconds, needed three attempts, and wrote a value that did not survive the save
— and those are exactly the differences an operator cares about. This module
turns an attempt into a scalar that separates them.

    reward =  W_SUCCESS      * verified
            - W_LATENCY      * latency_cost
            - W_RETRY        * retry_cost
            - W_FAILURE      * fault(category)
            - W_VERIFY_FAIL  * verification_failed

Every term is bounded, so the whole reward is bounded: +1.0 for a verified,
instant, first-attempt success, and -1.20 at the pathological worst. Bounded
matters because the ranker converts reward into fractional success credit, and
an unbounded penalty would let one bad attempt erase a cell's whole history.

ATTRIBUTION IS THE POINT

`fault` is not "how bad was this failure", it is "how much of this failure is
the STRATEGY's". A locator that could not find a field earns the full penalty.
A locator that never got the chance because the network dropped earns none —
charging it would teach the model that a perfectly good selector is unreliable
on days when the VPN is flaky. Getting this wrong is the classic way a bandit
learns the weather instead of the arms.

Weights live in config.py, are read at call time (so a test can change them),
and are visible in the startup snapshot.
"""

from . import config, telemetry

# How much of a failure in each category belongs to the strategy that was
# tried. 1.0 = entirely its fault, 0.0 = nothing to do with it.
FAULT = {
    # The strategy looked and did not find it. This is the thing being learned.
    telemetry.FIELD_NOT_FOUND: 1.0,
    telemetry.FIELD_NOT_VISIBLE: 1.0,
    # It found the element but the write did not take. Mostly the approach's
    # fault, partly the page's.
    telemetry.INPUT_REJECTED: 0.8,
    telemetry.CHANGE_EVENT_FAILED: 0.8,
    # It found the field somewhere awkward. That is a real cost but a mild one
    # — the fallback exists precisely for this and it works.
    telemetry.SCROLL_REQUIRED: 0.5,
    # A timeout is usually the selector probing something that will never
    # match, but not always. Charged, not fully.
    telemetry.TIMEOUT: 0.6,
    # The value was rejected by the form. Occasionally the locator hit the
    # wrong input; usually the date itself was the problem.
    telemetry.VALIDATION_FAILURE: 0.3,
    # The write did not survive the save. Charged in full here AND through
    # W_VERIFY_FAIL — a value that does not persist is the failure this
    # automation exists to prevent.
    telemetry.VERIFICATION_FAILURE: 1.0,
    # Not the strategy's doing. The panel had not rendered, the site was down,
    # a challenge appeared. Recorded, never charged.
    telemetry.PAGE_NOT_READY: 0.1,
    telemetry.NETWORK_ERROR: 0.0,
    telemetry.BOT_CHALLENGE: 0.0,
    telemetry.NONE: 0.0,
}

# Retries are charged linearly and then capped: three retries is already a
# clear signal, and a run that somehow logged forty should not be able to
# dominate every other observation in the cell.
RETRY_REF = 3.0


def fault(category):
    """How much of a failure in `category` is attributable to the strategy."""
    return FAULT.get(category, 1.0)


def latency_cost(duration_ms):
    """
    Latency as a number in [0, 1], relative to LATENCY_REF_MS.

    Unknown duration costs nothing rather than costing the maximum: a missing
    measurement is missing data, and treating it as "very slow" would penalise
    exactly the call sites that do not time themselves.
    """
    if duration_ms is None:
        return 0.0
    try:
        value = float(duration_ms)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    return min(1.0, value / max(1.0, float(config.LATENCY_REF_MS)))


def retry_cost(retries):
    """Retries as a number in [0, 1]."""
    try:
        count = float(retries or 0)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, count) / RETRY_REF)


def compute(found, verified, duration_ms=None, retries=0,
            category=telemetry.NONE, verification_failed=False):
    """
    The reward for one attempt.

    `found`    — the strategy located the field it was asked for
    `verified` — the write this attempt belongs to was read back and confirmed
    `verification_failed` — read-back ran and DISAGREED (distinct from never
                            having run, which is `verified=False` with this
                            False, and which belongs to an episode the dataset
                            excludes rather than scores)
    """
    value = config.W_SUCCESS * (1.0 if (found and verified) else 0.0)
    value -= config.W_LATENCY * latency_cost(duration_ms)
    value -= config.W_RETRY * retry_cost(retries)
    if not found or category not in (telemetry.NONE,):
        value -= config.W_FAILURE * fault(category)
    if verification_failed:
        value -= config.W_VERIFY_FAIL * 1.0
    return value


def credit(reward_value):
    """
    Reward turned into fractional success credit in [0, 1].

    The estimator counts successes over trials and ranks on a Wilson lower
    bound; that machinery is sound and stays. What changes is that a "success"
    is no longer worth a flat 1. A verified win that took eight seconds and two
    retries banks around 0.6 of a success, a plain failure banks 0, and the
    confidence interval still means what it says because credit can never
    exceed the trial that produced it.
    """
    if config.W_SUCCESS <= 0:
        return 0.0
    return max(0.0, min(1.0, reward_value / config.W_SUCCESS))


def bounds():
    """The reachable reward range, for the tests and the documentation."""
    best = config.W_SUCCESS
    worst = -(config.W_LATENCY + config.W_RETRY
              + config.W_FAILURE * max(FAULT.values())
              + config.W_VERIFY_FAIL)
    return worst, best


def explain(found, verified, duration_ms=None, retries=0,
            category=telemetry.NONE, verification_failed=False):
    """Every term, for the run log and for anyone auditing a number."""
    return {
        "success": config.W_SUCCESS * (1.0 if (found and verified) else 0.0),
        "latency": -config.W_LATENCY * latency_cost(duration_ms),
        "retry": -config.W_RETRY * retry_cost(retries),
        "failure": (-config.W_FAILURE * fault(category)
                    if (not found or category != telemetry.NONE) else 0.0),
        "verify_fail": -config.W_VERIFY_FAIL * (1.0 if verification_failed else 0.0),
        "total": compute(found, verified, duration_ms, retries,
                         category, verification_failed),
    }
