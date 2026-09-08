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
ELEMENT_DISABLED = "ELEMENT_DISABLED"
BLOCKING_DIALOG = "BLOCKING_DIALOG"
SESSION_EXPIRED = "SESSION_EXPIRED"
WRONG_DESTINATION = "WRONG_DESTINATION"
PARTIAL_LOAD = "PARTIAL_LOAD"
SERVER_ERROR = "SERVER_ERROR"
UNKNOWN = "UNKNOWN"

ERROR_CLASSES = (
    ELEMENT_NOT_FOUND, ELEMENT_NOT_VISIBLE, PAGE_NOT_READY, STALE_ELEMENT,
    TIMEOUT, FRAME_NOT_READY, LOCATOR_CHANGED, UNEXPECTED_PAGE_STATE,
    NAVIGATION_FAILURE, NETWORK_TRANSIENT, SAVE_FAILURE,
    VERIFICATION_FAILURE, HUMAN_VERIFICATION, INPUT_REJECTED,
    VALIDATION_FAILURE, AUTHENTICATION, ELEMENT_DISABLED, BLOCKING_DIALOG,
    SESSION_EXPIRED, WRONG_DESTINATION, PARTIAL_LOAD, SERVER_ERROR, UNKNOWN,
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
# A HUMAN CHECKPOINT is stronger than "not recoverable": the run should stop
# and say so, rather than carry on to the next shipment as though nothing
# happened. Each of these needs a person to look.
HUMAN_CHECKPOINT = {
    HUMAN_VERIFICATION: "a person must complete the challenge",
    AUTHENTICATION: "the session is not authenticated",
    SESSION_EXPIRED: "the session expired and must be re-established",
    VERIFICATION_FAILURE: ("a value was read back and CONTRADICTED what was "
                           "written; a person must decide what is correct"),
}

NOT_RECOVERABLE = {
    HUMAN_VERIFICATION: ("a person must complete the challenge; recovery "
                         "would be a bypass attempt"),
    SESSION_EXPIRED: ("the session expired; re-authenticating automatically "
                      "is not a page-recovery action"),
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


# ── EVIDENCE ────────────────────────────────────────────────────────
#
# What was actually observed when the failure happened. The automation
# collects it (only the automation can read a page); this module only
# describes the shape and reasons about it.
#
# Compact and structured on purpose: no page HTML, no field values, no
# shipment data beyond a yes/no on whether the expected marker was present.
# Enough to tell "the tab is not active" from "the locator changed", and
# nothing that would be unpleasant to find in a telemetry file.
EVIDENCE_FIELDS = (
    "url", "title", "expected_view", "active_view", "view_matches",
    "page_ready", "frames", "frame_count_changed",
    "field_visible_count", "field_any_count", "field_disabled",
    "other_field_visible_count", "editable_inputs",
    "shipment_marker_present", "modal_present", "consent_present",
    "alert_present", "login_present", "error_banner_present",
    "recent_action", "previous_result", "elapsed_ms", "attempt",
)


def evidence(**observed):
    """
    A normalised evidence record. Unobserved facts stay None, never False.

    The distinction matters: "we looked and the tab was wrong" and "we could
    not tell which tab was active" support completely different conclusions,
    and collapsing them into False is how a confident wrong diagnosis gets
    made.
    """
    return {key: observed.get(key) for key in EVIDENCE_FIELDS}


def describe_evidence(ev):
    """The observed facts, in the operator's words. Only what was measured."""
    ev = ev or {}
    lines = []
    if ev.get("view_matches") is False:
        lines.append("the {0} view is expected but {1} is active".format(
            ev.get("expected_view") or "target",
            ev.get("active_view") or "another view"))
    elif ev.get("view_matches") is True:
        lines.append("the expected view is active")
    if ev.get("field_visible_count") is not None:
        lines.append("{0} visible matching inputs".format(
            ev["field_visible_count"]))
    if ev.get("field_any_count") is not None:
        lines.append("{0} matching inputs in the DOM at all".format(
            ev["field_any_count"]))
    if ev.get("other_field_visible_count"):
        lines.append("{0} visible inputs for the OTHER field".format(
            ev["other_field_visible_count"]))
    if ev.get("field_disabled") is True:
        lines.append("the field is present but disabled")
    if ev.get("frames") is not None:
        lines.append("{0} frame(s)".format(ev["frames"]))
    if ev.get("page_ready") is False:
        lines.append("the page has not settled")
    if ev.get("modal_present"):
        lines.append("a modal is open")
    if ev.get("consent_present"):
        lines.append("a consent dialog is showing")
    if ev.get("alert_present"):
        lines.append("an alert is showing")
    if ev.get("login_present"):
        lines.append("a sign-in form is showing")
    if ev.get("error_banner_present"):
        lines.append("the page is showing an error")
    if ev.get("shipment_marker_present") is False:
        lines.append("the expected shipment marker is NOT on the page")
    elif ev.get("shipment_marker_present") is True:
        lines.append("the expected shipment marker is present")
    return lines


# ── HYPOTHESES ──────────────────────────────────────────────────────
#
# Between "it failed" and "try this" there has to be "because". A hypothesis
# is a named cause with a confidence that comes from the evidence, and the
# actions attached to it are the ones that would fix THAT cause.
#
# Confidence here is not a probability and is not learned. It is how strongly
# the OBSERVED evidence points at this cause, and it is stated as such
# wherever it is shown.
class Hypothesis(object):
    __slots__ = ("name", "cause", "confidence", "because", "actions")

    def __init__(self, name, cause, confidence, because, actions):
        self.name = name
        self.cause = cause
        self.confidence = float(confidence)
        self.because = because
        self.actions = tuple(actions)

    def __repr__(self):
        return "Hypothesis({0} {1:.2f})".format(self.name, self.confidence)


HIGH, MEDIUM, LOW = 0.85, 0.5, 0.2


def hypotheses(error_class, ev=None):
    """
    Two to five plausible causes for this failure, best-supported first.

    Every confidence is raised or lowered by something that was actually
    OBSERVED. With no evidence the hypotheses still come back, at their
    prior confidence, so the caller degrades to the deterministic ladder
    rather than to nothing.
    """
    ev = ev or {}
    out = []

    def add(name, cause, confidence, because, actions):
        actions = [a for a in actions if a in ACTIONS_BY_NAME]
        if actions:
            out.append(Hypothesis(name, cause, confidence, because, actions))

    view_wrong = ev.get("view_matches") is False
    in_dom = ev.get("field_any_count")
    visible = ev.get("field_visible_count")
    other_visible = ev.get("other_field_visible_count")

    if error_class in (ELEMENT_NOT_FOUND, ELEMENT_NOT_VISIBLE, LOCATOR_CHANGED):
        # H1 — the wrong panel is showing. The strongest single signal in this
        # application: the other field's inputs are on screen and ours are not.
        confidence = (HIGH if view_wrong
                      else 0.7 if (other_visible and not in_dom)
                      else MEDIUM if in_dom == 0
                      else LOW)
        add("wrong_view", "the expected view or tab is not active", confidence,
            ("the {0} view is active instead".format(ev.get("active_view"))
             if view_wrong else
             "the other field's inputs are visible and this field is not"
             if other_visible and not in_dom else
             "nothing matching this field is in the DOM at all"
             if in_dom == 0 else
             "no view evidence was collected"),
            ["reselect_tab", "reopen_view"])

        # H2 — it is in the DOM but not visible.
        if in_dom and not visible:
            add("hidden_but_present", "the field is rendered but not visible",
                HIGH, "{0} in the DOM, {1} visible".format(in_dom, visible),
                ["find_ignoring_visibility", "wait_for_element_visible"])

        # H3 — a frame boundary.
        if (ev.get("frames") or 1) > 1:
            add("frame_mismatch", "the field is in a different frame",
                MEDIUM if in_dom == 0 else LOW,
                "the page has {0} frames".format(ev.get("frames")),
                ["switch_frame", "reacquire_locator"])

        # H4 — still loading.
        if ev.get("page_ready") is False:
            add("not_ready", "the page has not finished rendering", HIGH,
                "the page had not settled when the lookup ran",
                ["wait_for_page_ready", "reacquire_locator"])

        # H5 — the locator no longer matches a page that changed.
        add("locator_changed",
            "the page changed and the known locators no longer match",
            LOW if (view_wrong or in_dom) else MEDIUM,
            "every known locator missed" if in_dom == 0
            else "the field is present, so the locators are probably fine",
            ["try_alternate_locator", "reacquire_locator"])

    elif error_class == STALE_ELEMENT:
        add("dom_replaced", "a postback replaced the element", HIGH,
            "the handle no longer resolves", ["reacquire_locator"])
        add("not_ready", "the replacement has not finished rendering", MEDIUM,
            "a postback was in flight",
            ["wait_for_page_ready", "reload_page"])

    elif error_class in (PAGE_NOT_READY, TIMEOUT):
        add("still_loading", "the page had not finished", HIGH,
            "readiness never became true",
            ["wait_for_page_ready", "reread_page_state"])
        if view_wrong:
            add("wrong_view", "the wrong view is loaded", MEDIUM,
                "the active view is not the expected one",
                ["reselect_tab", "reopen_view"])
        add("transient", "a slow response", LOW, "no other signal",
            ["retry_interaction_once", "reload_page"])

    elif error_class == FRAME_NOT_READY:
        add("frame_changed", "the frame was replaced or removed", HIGH,
            "the frame no longer resolves",
            ["switch_frame", "wait_for_page_ready"])
        add("not_ready", "the frame has not loaded", MEDIUM,
            "the document is still settling", ["wait_for_page_ready"])

    elif error_class in (NAVIGATION_FAILURE, NETWORK_TRANSIENT):
        add("transient_network", "the request did not complete", HIGH,
            "the navigation raised a transport error",
            ["retry_navigation", "wait_for_page_ready"])
        add("wrong_destination", "navigation landed somewhere unexpected",
            MEDIUM if ev.get("url") else LOW,
            "the final URL is not the expected one",
            ["reopen_view", "retry_navigation"])

    elif error_class == UNEXPECTED_PAGE_STATE:
        if ev.get("modal_present") or ev.get("consent_present"):
            add("blocking_dialog", "a dialog is covering the page", HIGH,
                "a modal or consent dialog was detected",
                ["dismiss_dialog", "reread_page_state"])
        if view_wrong:
            add("wrong_view", "the wrong view is showing", HIGH,
                "the active view is not the expected one",
                ["reselect_tab", "reopen_view"])
        add("unknown_state", "the page is in a state we do not recognise",
            LOW, "no specific marker matched",
            ["reread_page_state", "reload_page"])

    elif error_class == ELEMENT_DISABLED:
        add("awaiting_precondition",
            "the control is present but not yet enabled", HIGH,
            "the element is disabled",
            ["wait_for_enabled", "wait_for_page_ready"])

    elif error_class == BLOCKING_DIALOG:
        add("blocking_dialog", "a dialog is covering the page", HIGH,
            "a modal, consent or alert dialog was detected",
            ["dismiss_dialog", "reread_page_state"])

    elif error_class in (SAVE_FAILURE, INPUT_REJECTED):
        add("transient_save", "the save did not complete", MEDIUM,
            "the save control did not resolve",
            ["retry_save_once", "wait_for_page_ready"])
        add("not_ready", "the form was not ready", MEDIUM,
            "the page had not settled", ["wait_for_page_ready"])

    elif error_class == UNKNOWN:
        add("unclassified", "the failure is not one we recognise", LOW,
            "nothing in the message or the evidence matched a known class",
            ["reread_page_state"])

    out.sort(key=lambda h: -h.confidence)
    return out[:5]


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
# SAFETY LEVELS. `risk` is the chance an action changes something other than
# what it was asked to change, and it is a first-class ranking term: a
# slightly better success rate does not justify throwing the page away.
#   0.0  observes only, changes nothing
#   0.2  re-queries or waits; no navigation, no state change
#   0.5  changes UI state (a tab, a dialog) but not data
#   0.8  discards and rebuilds page state (reload, reopen, renavigate)
SAFE_OBSERVE, SAFE_REQUERY, SAFE_UI, SAFE_REBUILD = 0.0, 0.2, 0.5, 0.8


class Action(object):
    __slots__ = ("name", "applies_to", "reloads", "navigates", "cost_ms",
                 "needs_write", "detail", "risk", "requires", "verifies",
                 "max_attempts", "timeout_ms")

    def __init__(self, name, applies_to, detail, cost_ms=0,
                 reloads=False, navigates=False, needs_write=False,
                 risk=SAFE_REQUERY, requires=(), verifies="field_present",
                 max_attempts=1, timeout_ms=8000):
        self.name = name
        self.applies_to = frozenset(applies_to)
        self.detail = detail
        self.cost_ms = cost_ms
        self.reloads = reloads
        self.navigates = navigates
        self.needs_write = needs_write
        # How much this action could disturb, on the scale above.
        self.risk = float(risk)
        # Evidence keys that must be known before this action makes sense.
        self.requires = tuple(requires)
        # What the caller should check to decide whether it WORKED. Naming it
        # here is what stops "no exception" being mistaken for success.
        self.verifies = verifies
        self.max_attempts = int(max_attempts)
        self.timeout_ms = int(timeout_ms)

    @property
    def telemetry_id(self):
        return "recovery:{0}".format(self.name)

    def __repr__(self):
        return "Action({0})".format(self.name)


ACTIONS = (
    Action("wait_for_page_ready",
           (PAGE_NOT_READY, TIMEOUT, UNEXPECTED_PAGE_STATE, FRAME_NOT_READY,
            PARTIAL_LOAD),
           "wait for the page to settle using the existing readiness check",
           cost_ms=1500, risk=SAFE_OBSERVE, verifies="page_ready"),
    Action("wait_for_element_visible",
           (ELEMENT_NOT_VISIBLE, TIMEOUT, PAGE_NOT_READY),
           "wait for the field to become visible within the existing budget",
           cost_ms=1500, risk=SAFE_OBSERVE, verifies="field_visible"),
    Action("wait_for_enabled",
           (ELEMENT_DISABLED, ELEMENT_NOT_VISIBLE, PAGE_NOT_READY),
           "wait for the control to become enabled, within its own budget",
           cost_ms=2000, risk=SAFE_OBSERVE, verifies="field_enabled"),
    Action("reread_page_state",
           (UNEXPECTED_PAGE_STATE, PAGE_NOT_READY, UNKNOWN, PARTIAL_LOAD),
           "re-read the page state without changing anything",
           cost_ms=300, risk=SAFE_OBSERVE, verifies="state_reread"),
    Action("return_to_main_frame",
           (FRAME_NOT_READY, ELEMENT_NOT_FOUND),
           "go back to the main document before looking again",
           cost_ms=250, risk=SAFE_REQUERY),
    Action("reacquire_locator",
           (STALE_ELEMENT, ELEMENT_NOT_FOUND, LOCATOR_CHANGED),
           "resolve the field again from the same candidate list",
           cost_ms=400, risk=SAFE_REQUERY),
    Action("switch_frame",
           (FRAME_NOT_READY, ELEMENT_NOT_FOUND),
           "look in the other already-enumerated frames of this page",
           cost_ms=500, risk=SAFE_REQUERY),
    Action("find_ignoring_visibility",
           (ELEMENT_NOT_VISIBLE, ELEMENT_NOT_FOUND),
           "use the existing visibility-free lookup, which keeps the "
           "cross-field guard",
           cost_ms=600, risk=SAFE_REQUERY),
    Action("try_alternate_locator",
           (ELEMENT_NOT_FOUND, LOCATOR_CHANGED, ELEMENT_NOT_VISIBLE),
           "try the next candidate in the automation's own list",
           cost_ms=800, risk=SAFE_REQUERY),
    Action("reselect_tab",
           (UNEXPECTED_PAGE_STATE, ELEMENT_NOT_VISIBLE, ELEMENT_NOT_FOUND),
           "select the COE/BU tab again for this field",
           cost_ms=700, risk=SAFE_UI, verifies="field_present"),
    Action("dismiss_dialog",
           (BLOCKING_DIALOG, UNEXPECTED_PAGE_STATE, ELEMENT_NOT_VISIBLE),
           "dismiss a KNOWN dialog using the automation's own handler",
           cost_ms=900, risk=SAFE_UI, verifies="dialog_gone"),
    Action("retry_interaction_once",
           (INPUT_REJECTED, TIMEOUT, STALE_ELEMENT),
           "repeat the same interaction once",
           cost_ms=600, risk=SAFE_UI, verifies="value_entered"),
    Action("retry_save_once",
           (SAVE_FAILURE, TIMEOUT, NETWORK_TRANSIENT),
           "press Save once more, then read back as usual",
           cost_ms=3000, risk=SAFE_UI, verifies="saved", needs_write=True),
    Action("reopen_view",
           (UNEXPECTED_PAGE_STATE, ELEMENT_NOT_FOUND, PAGE_NOT_READY,
            WRONG_DESTINATION),
           "reopen Manage for this shipment in the same view",
           cost_ms=4000, navigates=True, risk=SAFE_REBUILD,
           verifies="field_present"),
    Action("reload_page",
           (PAGE_NOT_READY, UNEXPECTED_PAGE_STATE, STALE_ELEMENT,
            FRAME_NOT_READY, PARTIAL_LOAD, SERVER_ERROR),
           "reload the current page",
           cost_ms=5000, reloads=True, risk=SAFE_REBUILD,
           verifies="page_ready"),
    Action("retry_navigation",
           (NAVIGATION_FAILURE, NETWORK_TRANSIENT, WRONG_DESTINATION,
            SERVER_ERROR),
           "retry the navigation once through the existing bounded retry",
           cost_ms=6000, navigates=True, risk=SAFE_REBUILD,
           verifies="page_ready"),
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


def rank(actions, scores=None, quarantined=(), hypos=None):
    """
    Order the actions. Deterministic: no randomness anywhere.

    MULTI-OBJECTIVE, and hypothesis-first. A real run spent its whole
    three-attempt budget on reacquire_locator, switch_frame and
    find_ignoring_visibility — three ways of asking "is the field in the DOM
    somewhere?" — while reselect_tab, the action for the cause that was
    actually true, sat fourth and was never reached. Cheapest-first is a fine
    tie-break and a terrible plan.

    So the ordering is:

      1. one action per HYPOTHESIS, best-supported hypothesis first, so a
         bounded budget spends its attempts on DIFFERENT ideas rather than on
         variations of one;
      2. then the rest, by learned score, then by risk, then by cost.

    `risk` is a first-class term, not a tie-break: a slightly better success
    rate does not justify throwing the page away, so a reload never outranks
    a re-query at similar confidence.
    """
    scores = scores or {}
    blocked = set(quarantined)
    by_name = {a.name: a for a in actions}
    position = {name: i for i, name in enumerate(ACTION_NAMES)}

    def value(action):
        """Higher is better. Learned score, penalised by risk and by cost."""
        return (float(scores.get(action.name, 0.0))
                - 0.30 * action.risk
                - 0.10 * min(1.0, action.cost_ms / 6000.0))

    ordered, seen = [], set()
    for hypothesis in (hypos or []):
        # The best available action for this cause, then move to the NEXT
        # cause. One idea each before any idea gets a second try.
        options = [by_name[n] for n in hypothesis.actions
                   if n in by_name and n not in seen]
        if not options:
            continue
        pick = sorted(options,
                      key=lambda a: (a.name in blocked, -value(a),
                                     position.get(a.name, 99)))[0]
        ordered.append(pick)
        seen.add(pick.name)

    rest = sorted((a for a in actions if a.name not in seen),
                  key=lambda a: (a.name in blocked, -value(a), a.risk,
                                 a.cost_ms, position.get(a.name, 99)))
    return ordered + rest


def explain_ranking(action, hypos=None, scores=None):
    """
    Why this action is first, in one sentence, from real inputs only.

    Never invents a reason: with no hypothesis and no score it says exactly
    that, which is the honest answer during the shadow phase.
    """
    scores = scores or {}
    for hypothesis in (hypos or []):
        if action.name in hypothesis.actions:
            parts = ["it addresses the best-supported cause ({0}: {1})".format(
                hypothesis.name, hypothesis.because)]
            if action.name in scores:
                parts.append("historical ranking score {0:.2f}".format(
                    scores[action.name]))
            parts.append("risk {0:.1f}".format(action.risk))
            parts.append("about {0}ms".format(action.cost_ms))
            return "; ".join(parts)
    if action.name in scores:
        return ("no hypothesis matched it, but it has the best historical "
                "ranking score ({0:.2f})".format(scores[action.name]))
    return ("no evidence favours any option, so the safest and cheapest "
            "deterministic action goes first (risk {0:.1f}, about {1}ms)"
            .format(action.risk, action.cost_ms))
