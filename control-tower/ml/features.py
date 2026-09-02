"""
An interaction context turned into something a table can be keyed on.

The feature space is small and entirely categorical on purpose. Every question
the layer answers is of the form "on this kind of page, looking for this kind
of field, which of these known strategies has worked?" — and that is a lookup,
not a regression. Keeping the features discrete is what lets the estimator be
honest about how much evidence it has for each cell.

Nothing here touches Playwright or the network. A context is a plain dict built
by the caller, so every function in this module is directly testable.
"""

# Contexts are described by these keys and no others. An unknown key is dropped
# rather than silently becoming part of a table key, so a typo at a call site
# cannot quietly fragment the model.
FEATURE_KEYS = (
    "provider",      # DHL | AFKL | QATAR | ASTRAL | HUB
    "page",          # manage | tab_panel | carrier_result | hub_table | portal_entry
    "field",         # ETA | ATA | awb | track_button | save_button
    "view",          # COE | BU | none
    "visible",       # yes | no | unknown
    "page_ready",    # yes | no | unknown
    "frames",        # one | many
    "attempt",       # first | later
)

# Backoff chain, most specific first. When a context has too little evidence
# the estimator drops to the next level. The order encodes what actually
# transfers between situations: which field is being looked for matters more
# than which view it sits in, and the provider matters more than the attempt.
BACKOFF_LEVELS = (
    ("provider", "page", "field", "view", "visible", "page_ready", "frames", "attempt"),
    ("provider", "page", "field", "view", "visible", "page_ready"),
    ("provider", "page", "field", "visible"),
    ("provider", "page", "field"),
    ("page", "field"),
    ("field",),
    (),
)


def _norm(value):
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value).strip()
    return text or "unknown"


def context(provider=None, page=None, field=None, view=None, visible=None,
            page_ready=None, frames=None, attempt=None):
    """
    Build a context. Every argument is optional; anything omitted becomes
    "unknown", which is a real value the table can hold evidence for — a field
    whose visibility could not be determined is its own situation, and on the
    Manage page it turned out to be the interesting one.
    """
    if isinstance(frames, int):
        frames = "one" if frames <= 1 else "many"
    if isinstance(attempt, int):
        attempt = "first" if attempt <= 1 else "later"
    return {
        "provider": _norm(provider),
        "page": _norm(page),
        "field": _norm(field),
        "view": _norm(view),
        "visible": _norm(visible),
        "page_ready": _norm(page_ready),
        "frames": _norm(frames),
        "attempt": _norm(attempt),
    }


def clean(raw):
    """Drop unknown keys and normalise the rest. Accepts a partial dict."""
    if not isinstance(raw, dict):
        return context()
    return context(**{k: v for k, v in raw.items() if k in FEATURE_KEYS})


def key(ctx, level):
    """
    The table key for `ctx` at one backoff level.

    Levels are tuples of feature names; the key is those values in that order,
    joined. The empty level is the global cell.
    """
    ctx = clean(ctx)
    if not level:
        return "*"
    return "|".join("{0}={1}".format(name, ctx.get(name, "unknown"))
                    for name in level)


def keys(ctx):
    """Every key for `ctx`, most specific first."""
    return [key(ctx, level) for level in BACKOFF_LEVELS]


def describe(ctx):
    """A short human string for the run log."""
    ctx = clean(ctx)
    text = "{provider}/{page}/{field}".format(**ctx)
    if ctx["view"] != "unknown":
        text += " view={0}".format(ctx["view"])
    if ctx["visible"] != "unknown":
        text += " visible={0}".format(ctx["visible"])
    return text
