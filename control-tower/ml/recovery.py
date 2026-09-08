"""
Safe error recovery: the POLICY, and nothing else.

Nothing in this module touches a browser, a page, a locator or a shipment. It
classifies an error, names the recovery actions that are safe for that class,
ranks them, and enforces a budget. The automation owns a dispatch table from
those names to its own existing functions, and it is the only thing that can
execute anything.

That split is the safety boundary. ATLAS can choose only from a fixed,
validated vocabulary of actions the automation already supports; it cannot
invent a selector, a URL, a navigation or a business rule, because there is
nowhere here for such a thing to be expressed.

    error
      -> classify()            which of the known error classes is this
      -> candidates()          which safe actions apply, given the budget
      -> rank()                the order to try them in
      -> [the automation executes, and verifies]
      -> Budget.spend()        until exhausted, then deterministic fallback

The ranking uses the same Beta-Bernoulli model and the same Wilson lower
bound as strategy selection, with the same support gates, so a recovery
action with four observations behind it cannot outrank a hand-tuned order.
"""

from . import features

# ── ERROR CLASSES ───────────────────────────────────────────────────
#
# One name per thing that can actually go wrong at a locator or a page. These
# are the classes recovery policy is written against; the finer-grained
# telemetry categories map onto them.
ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
ELEMENT_NOT_VISIBLE = "ELEMENT_NOT_VISIBLE"
PAGE_NOT_READY = "PAGE_NOT_READY"
STALE_ELEMENT = "STALE_ELEMENT"
TIMEOUT = "TIMEOUT"
FRAME_NOT_READY = "FRAME_NOT_READY"
LOCATOR_CHANGED = "LOCATOR_CHANGED"
UNEXPECTED_PAGE_STATE = "UNEXPECTED_PAGE_STATE"
NAVIGATION_FAILURE = "NAVIGATION_FAILURE"
NETWORK_TRANSIENT = "NETWORK_TRANSIENT"
SAVE_FAILURE = "SAVE_FAILURE"
VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
HUMAN_VERIFICATION = "HUMAN_VERIFICATION"
INPUT_REJECTED = "INPUT_REJECTED"
VALIDATION_FAILURE = "VALIDATION_FAILURE"
AUTHENTICATION = "AUTHENTICATION"
UNKNOWN = "UNKNOWN"

ERROR_CLASSES = (
    ELEMENT_NOT_FOUND, ELEMENT_NOT_VISIBLE, PAGE_NOT_READY, STALE_ELEMENT,
    TIMEOUT, FRAME_NOT_READY, LOCATOR_CHANGED, UNEXPECTED_PAGE_STATE,
    NAVIGATION_FAILURE, NETWORK_TRANSIENT, SAVE_FAILURE,
    VERIFICATION_FAILURE, HUMAN_VERIFICATION, INPUT_REJECTED,
    VALIDATION_FAILURE, AUTHENTICATION, UNKNOWN,
)

# NOT RECOVERABLE, and each for its own reason. Recovery is never attempted
# for these, and the reason is reported rather than being a silent no-op.
#
#   HUMAN_VERIFICATION    a person has to do it — see the CAPTCHA policy
#   VERIFICATION_FAILURE  a read-back that CONTRADICTED the write is a safety
#                         result, not an obstacle. Retrying the write to make
#                         verification pass is the one thing recovery must
#                         never do.
#   VALIDATION_FAILURE    the Hub rejected the value. That is a business rule
#                         answering, and it will answer the same way again.
#   AUTHENTICATION        credentials are not a locator problem.
NOT_RECOVERABLE = {
    HUMAN_VERIFICATION: ("a person must complete the challenge; recovery "
                         "would be a bypass attempt"),
    VERIFICATION_FAILURE: ("the value was read back and CONTRADICTED. That is "
                           "a safety result and must stand — recovery must "
                           "never re-write a shipment to make verification "
                           "pass"),
    VALIDATION_FAILURE: ("the Hub rejected the value itself; a business rule "
                         "answered and will answer the same way again"),
    AUTHENTICATION: "a credential problem is not a recoverable page problem",
}


def classify(error=None, category=None, text=None):
    """
    Which error class this is, plus a stable signature for grouping.

    Deliberately conservative: anything not recognised is UNKNOWN, and UNKNOWN
    has no recovery actions. A misclassification that invents recoverability
    is worse than one that gives up.
    """
    blob = " ".join(str(x) for x in (
        text or "",
        "" if error is None else "{0} {1}".format(type(error).__name__, error),
    )).casefold()

    # The category the automation already assigned is stronger evidence than
    # the message text, so it is read first.
    by_category = {
        "BOT_CHALLENGE": HUMAN_VERIFICATION,
        "VERIFICATION_FAILURE": VERIFICATION_FAILURE,
        "VALIDATION_FAILURE": VALIDATION_FAILURE,
        "FIELD_NOT_FOUND": ELEMENT_NOT_FOUND,
        "FIELD_NOT_VISIBLE": ELEMENT_NOT_VISIBLE,
        "SCROLL_REQUIRED": ELEMENT_NOT_VISIBLE,
        "PAGE_NOT_READY": PAGE_NOT_READY,
        "INPUT_REJECTED": INPUT_REJECTED,
        "CHANGE_EVENT_FAILED": INPUT_REJECTED,
        "NETWORK_ERROR": NETWORK_TRANSIENT,
        "TIMEOUT": TIMEOUT,
    }
    if category in by_category:
        klass = by_category[category]
        return klass, _signature(klass, blob)

    if ("human verification" in blob or "captcha" in blob
            or "turnstile" in blob or "challenge" in blob):
        klass = HUMAN_VERIFICATION
    elif "unauthor" in blob or "credential" in blob or "sign in" in blob:
        klass = AUTHENTICATION
    elif "not verified" in blob or "does not hold" in blob or "read back" in blob:
        klass = VERIFICATION_FAILURE
    elif "frame" in blob and ("detach" in blob or "not found" in blob
                              or "navigat" in blob or "not ready" in blob):
        # Checked BEFORE stale: a detached FRAME and a detached ELEMENT read
        # almost the same in a Playwright message and need different
        # recovery, and the more specific test has to win.
        klass = FRAME_NOT_READY
    elif "stale" in blob or "detach" in blob or "element handle" in blob:
        klass = STALE_ELEMENT
    elif "navigat" in blob or "goto" in blob or "err_tunnel" in blob:
        klass = NAVIGATION_FAILURE
    elif any(w in blob for w in ("net::", "err_", "connection", "socket",
                                 "dns", "502", "503", "504")):
        klass = NETWORK_TRANSIENT
    elif "save" in blob and ("fail" in blob or "did not" in blob):
        klass = SAVE_FAILURE
    elif "timeout" in blob or "timed out" in blob or "exceeded" in blob:
        klass = TIMEOUT
    elif "not visible" in blob or "hidden" in blob or "outside of the viewport" in blob:
        klass = ELEMENT_NOT_VISIBLE
    elif "not found" in blob or "no such element" in blob or "resolved to 0" in blob:
        klass = ELEMENT_NOT_FOUND
    elif "not ready" in blob or "still loading" in blob:
        klass = PAGE_NOT_READY
    else:
        klass = UNKNOWN
    return klass, _signature(klass, blob)


def _signature(klass, blob):
    """
    A short stable key for grouping the same failure across runs.

    Anything variable — a reference, a date, a port, a hex handle — is
    stripped, so "ETA field timed out for 9451291275" and the same failure on
    another shipment group together instead of each looking unique.
    """
    import re
    text = re.sub(r"\d+", "#", blob)
    text = re.sub(r"0x[0-9a-f]+", "#", text)
    text = re.sub(r"[^a-z#_ ]+", " ", text)
    words = [w for w in text.split() if len(w) > 2][:8]
    return "{0}:{1}".format(klass, "_".join(words)) if words else klass


# ── THE SAFE ACTION REGISTRY ────────────────────────────────────────
#
# Every action here is a name for something the automation ALREADY does. The
# automation holds the dispatch table; this holds only the policy.
#
#   applies_to   the error classes this action is safe for
#   reloads      does it reload a page (budgeted separately, they are dear)
#   navigates    does it re-navigate (budgeted separately)
#   cost_ms      a rough expected cost, used to break ties toward the cheaper
#                action when confidence is equal
#   needs_write  only valid inside a write episode, never on a read
class Action(object):
    __slots__ = ("name", "applies_to", "reloads", "navigates", "cost_ms",
                 "needs_write", "detail")

    def __init__(self, name, applies_to, detail, cost_ms=0,
                 reloads=False, navigates=False, needs_write=False):
        self.name = name
        self.applies_to = frozenset(applies_to)
        self.detail = detail
        self.cost_ms = cost_ms
        self.reloads = reloads
        self.navigates = navigates
        self.needs_write = needs_write

    def __repr__(self):
        return "Action({0})".format(self.name)


ACTIONS = (
    Action("wait_for_page_ready",
           (PAGE_NOT_READY, TIMEOUT, UNEXPECTED_PAGE_STATE, FRAME_NOT_READY),
           "wait for the page to settle using the existing readiness check",
           cost_ms=1500),
    Action("wait_for_element_visible",
           (ELEMENT_NOT_VISIBLE, TIMEOUT, PAGE_NOT_READY),
           "wait for the field to become visible within the existing budget",
           cost_ms=1500),
    Action("reacquire_locator",
           (STALE_ELEMENT, ELEMENT_NOT_FOUND, LOCATOR_CHANGED),
           "resolve the field again from the same candidate list",
           cost_ms=400),
    Action("try_alternate_locator",
           (ELEMENT_NOT_FOUND, LOCATOR_CHANGED, ELEMENT_NOT_VISIBLE),
           "try the next candidate in the automation's own list",
           cost_ms=800),
    Action("find_ignoring_visibility",
           (ELEMENT_NOT_VISIBLE, ELEMENT_NOT_FOUND),
           "use the existing visibility-free lookup, which keeps the "
           "cross-field guard",
           cost_ms=600),
    Action("switch_frame",
           (FRAME_NOT_READY, ELEMENT_NOT_FOUND),
           "look in the other already-enumerated frames of this page",
           cost_ms=500),
    Action("reopen_view",
           (UNEXPECTED_PAGE_STATE, ELEMENT_NOT_FOUND, PAGE_NOT_READY),
           "reopen Manage for this shipment in the same view",
           cost_ms=4000, navigates=True),
    Action("reselect_tab",
           (UNEXPECTED_PAGE_STATE, ELEMENT_NOT_VISIBLE, ELEMENT_NOT_FOUND),
           "select the COE/BU tab again for this field",
           cost_ms=700),
    Action("reload_page",
           (PAGE_NOT_READY, UNEXPECTED_PAGE_STATE, STALE_ELEMENT,
            FRAME_NOT_READY),
           "reload the current page",
           cost_ms=5000, reloads=True),
    Action("retry_navigation",
           (NAVIGATION_FAILURE, NETWORK_TRANSIENT),
           "retry the navigation once through the existing bounded retry",
           cost_ms=6000, navigates=True),
    Action("retry_interaction_once",
           (INPUT_REJECTED, TIMEOUT, STALE_ELEMENT),
           "repeat the same interaction once",
           cost_ms=600),
    Action("retry_save_once",
           (SAVE_FAILURE, TIMEOUT, NETWORK_TRANSIENT),
           "press Save once more, then read back as usual",
           cost_ms=3000, needs_write=True),
    Action("reread_page_state",
           (UNEXPECTED_PAGE_STATE, PAGE_NOT_READY, UNKNOWN),
           "re-read the page state without changing anything",
           cost_ms=300),
)

ACTIONS_BY_NAME = {a.name: a for a in ACTIONS}
ACTION_NAMES = tuple(a.name for a in ACTIONS)


def candidates(error_class, budget=None, in_write=False):
    """
    The safe actions for this error class, filtered by what is still allowed.

    Returns [] for a class with no policy, and for every non-recoverable
    class. An empty list is the correct answer to "how do I recover from a
    verification failure": you do not.
    """
    if error_class in NOT_RECOVERABLE:
        return []
    out = []
    for action in ACTIONS:
        if error_class not in action.applies_to:
            continue
        if action.needs_write and not in_write:
            continue
        if budget is not None and not budget.allows(action):
            continue
        out.append(action)
    return out


def why_not(error_class):
    """The reason this class is not recoverable, or None."""
    return NOT_RECOVERABLE.get(error_class)


# ── THE BUDGET ──────────────────────────────────────────────────────
class Budget(object):
    """
    Hard limits on recovery. Recovery that is not bounded is a retry loop
    with better branding.

    Every limit is checked BEFORE an action runs, and spent after, so a single
    expensive action cannot exceed the wall-clock ceiling and then be followed
    by another.
    """

    __slots__ = ("max_attempts", "max_per_action", "max_seconds",
                 "max_reloads", "max_navigations", "attempts", "per_action",
                 "reloads", "navigations", "started", "_clock")

    def __init__(self, max_attempts=3, max_per_action=1, max_seconds=45.0,
                 max_reloads=1, max_navigations=2, clock=None):
        self.max_attempts = int(max_attempts)
        self.max_per_action = int(max_per_action)
        self.max_seconds = float(max_seconds)
        self.max_reloads = int(max_reloads)
        self.max_navigations = int(max_navigations)
        self.attempts = 0
        self.per_action = {}
        self.reloads = 0
        self.navigations = 0
        self._clock = clock or _now
        self.started = self._clock()

    def elapsed(self):
        return self._clock() - self.started

    def exhausted(self):
        """True when nothing further may be attempted, and why."""
        if self.attempts >= self.max_attempts:
            return "the {0}-attempt recovery limit is reached".format(
                self.max_attempts)
        if self.elapsed() >= self.max_seconds:
            return "the {0:.0f}s recovery budget is spent".format(
                self.max_seconds)
        return None

    def allows(self, action):
        """Is there room for this specific action?"""
        if self.exhausted():
            return False
        if self.per_action.get(action.name, 0) >= self.max_per_action:
            return False
        if action.reloads and self.reloads >= self.max_reloads:
            return False
        if action.navigates and self.navigations >= self.max_navigations:
            return False
        # Would this action's expected cost run past the ceiling?
        if self.elapsed() + (action.cost_ms / 1000.0) > self.max_seconds:
            return False
        return True

    def spend(self, action):
        self.attempts += 1
        self.per_action[action.name] = self.per_action.get(action.name, 0) + 1
        if action.reloads:
            self.reloads += 1
        if action.navigates:
            self.navigations += 1

    def snapshot(self):
        return {
            "attempts": self.attempts, "max_attempts": self.max_attempts,
            "reloads": self.reloads, "max_reloads": self.max_reloads,
            "navigations": self.navigations,
            "max_navigations": self.max_navigations,
            "elapsed_s": round(self.elapsed(), 2),
            "max_seconds": self.max_seconds,
        }


def _now():
    import time
    return time.time()


# ── RANKING ─────────────────────────────────────────────────────────
def recovery_context(context, error_class):
    """
    The feature context a recovery decision is keyed on.

    The error class is part of the context, because "what recovers a missing
    element" and "what recovers a dead navigation" are different questions and
    must not share a cell.
    """
    merged = dict(features.clean(context or {}))
    merged["error"] = str(error_class)
    return merged


def rank(actions, scores=None, quarantined=()):
    """
    Order the actions. Deterministic: no randomness anywhere.

    THE DETERMINISTIC LADDER, when there are no scores, is cheapest first —
    and cheap here means least disruptive, which is the same ordering: a
    re-probe of the locator costs 400ms and changes nothing, while a reopen
    costs 4s and throws the page away. So the ladder tries the harmless
    things before the drastic ones, and a reload is never reached before a
    re-probe has been tried.

    With scores it is by score, then by cost, then by registry position, so
    equal confidence always breaks toward the cheaper action.
    """
    scores = scores or {}
    blocked = set(quarantined)
    order = list(ACTION_NAMES)
    return sorted(
        actions,
        key=lambda a: (a.name in blocked,
                       -float(scores.get(a.name, 0.0)),
                       a.cost_ms,
                       order.index(a.name) if a.name in order else 99))
