"""
Control Tower logistics assistant.

Read-only. Deterministic. Grounded.

Two layers, deliberately separated:

  RunData  — a read-only view over a bridge snapshot. It owns every question
             about "what does this run contain", so no query logic is
             duplicated from the automation or the bridge.

  answer() — natural-language intent matching on top of RunData. It only ever
             renders values RunData handed it.

There is no language model, and that is the design. Every sentence is
assembled from fields that exist in the live state, so the assistant has no
mechanism for producing an ETA, carrier or status the automation never
collected. Anything missing is reported as missing.

It never writes: no automation control, no credential access, no mutation of
any shipment record. It receives a snapshot and nothing else.
"""

import re

UNKNOWN = "—"
NO_DATA = "I don't have that information in the current run."

STATE_WORDS = {
    "updated": "updated in the Logistics Hub",
    "partial": "partly updated — one date written, one failed",
    "processing": "being processed right now",
    "skipped": "skipped",
    "failed": "failed",
    "unknown": "in an unknown state",
}

PROVIDER_NAMES = {"DHL": "DHL", "QATAR": "Qatar Airways Cargo", "hub": "Mantrac Logistics Hub"}


try:
    from . import insights
except Exception:                       # pragma: no cover - flat layout
    try:
        import insights
    except Exception:
        insights = None


def _has(value):
    return value not in (None, "", UNKNOWN)


def _show(value):
    return value if _has(value) else UNKNOWN


def provider_name(key):
    return PROVIDER_NAMES.get(key, key or UNKNOWN)


# ══════════════════════════════════════════════════════════════
# READ-ONLY DATA LAYER
# ══════════════════════════════════════════════════════════════

class RunData:
    """
    Every question the assistant can ask about the current run.

    Wraps a snapshot without copying or reinterpreting it. If a field is not
    in the snapshot, the corresponding accessor returns None rather than
    deriving a plausible value.
    """

    def __init__(self, state):
        self.state = state or {}

    # -- run ---------------------------------------------------------------

    @property
    def run(self):
        return self.state.get("run") or {}

    @property
    def counters(self):
        return self.state.get("counters") or {}

    @property
    def progress(self):
        return self.state.get("progress") or {}

    @property
    def current(self):
        return self.state.get("current") or {}

    @property
    def status(self):
        return self.run.get("status") or "idle"

    @property
    def is_running(self):
        return self.status == "running"

    @property
    def runtime(self):
        seconds = self.run.get("runtime_seconds")
        if seconds is None:
            return None
        return "{0:02d}:{1:02d}:{2:02d}".format(
            seconds // 3600, seconds % 3600 // 60, seconds % 60
        )

    @property
    def target_status(self):
        """The hub filter this run is working through, e.g. Under Clearance."""
        return self.run.get("target_status")

    # -- shipments ---------------------------------------------------------

    @property
    def shipments(self):
        return self.state.get("shipments") or []

    def by_state(self, name):
        return [s for s in self.shipments if s.get("state") == name]

    @property
    def failed(self):
        return self.by_state("failed")

    @property
    def skipped(self):
        return self.by_state("skipped")

    @property
    def updated(self):
        return self.by_state("updated")

    @property
    def partial(self):
        """Wrote one date to the Hub but not the other."""
        return self.by_state("partial")

    @property
    def processing(self):
        return self.by_state("processing")

    @property
    def in_flight(self):
        """The shipment the automation has open right now, if any."""
        return self.current.get("shipment")

    def find(self, reference):
        """
        Locate a shipment by reference, tolerating spacing and partial entry.

        Only matches shipments this run has actually touched, so a number from
        somewhere else is reported as unknown rather than answered about.
        """
        wanted = re.sub(r"\D", "", str(reference or ""))
        if len(wanted) < 4:
            return None
        for record in self.shipments:
            digits = re.sub(r"\D", "", record.get("reference") or "")
            if digits and (digits == wanted
                           or digits.endswith(wanted)
                           or wanted.endswith(digits)):
                return record
        return None

    def references_in(self, question):
        """Every shipment-shaped number in a question, longest first."""
        chunks = re.findall(r"[\d][\d\s\-]{3,}", question)
        cleaned = [re.sub(r"\D", "", chunk) for chunk in chunks]
        return sorted({c for c in cleaned if len(c) >= 4}, key=len, reverse=True)

    # -- carriers ----------------------------------------------------------

    def by_carrier(self):
        """{carrier: {total, updated, failed, skipped}} for this run only."""
        groups = {}
        for record in self.shipments:
            name = record.get("carrier") or "Unknown carrier"
            bucket = groups.setdefault(
                name, {"total": 0, "updated": 0, "failed": 0,
                       "skipped": 0, "partial": 0}
            )
            bucket["total"] += 1
            state = record.get("state")
            if state in bucket:
                bucket[state] += 1
        return groups

    def worst_carrier(self):
        """(name, stats) with the most failures, or None when there are none."""
        groups = self.by_carrier()
        ranked = sorted(groups.items(), key=lambda item: item[1]["failed"], reverse=True)
        if not ranked or ranked[0][1]["failed"] == 0:
            return None
        return ranked[0]

    # -- analysis ----------------------------------------------------------

    def timed(self):
        return [s for s in self.shipments if s.get("duration_ms") is not None]

    def slowest(self, limit=3):
        return sorted(self.timed(), key=lambda s: -s["duration_ms"])[:limit]

    def fastest(self, limit=3):
        return sorted(self.timed(), key=lambda s: s["duration_ms"])[:limit]

    def average_ms(self, records=None):
        rows = self.timed() if records is None else [
            r for r in records if r.get("duration_ms") is not None]
        if not rows:
            return None
        return int(sum(r["duration_ms"] for r in rows) / len(rows))

    def by_page(self):
        pages = {}
        for record in self.shipments:
            key = record.get("table_page")
            pages[key] = pages.get(key, 0) + 1
        return pages

    def without_eta(self):
        return [s for s in self.shipments if not _has(s.get("provider_eta"))]

    def with_ata(self):
        return [s for s in self.shipments if _has(s.get("provider_ata"))]

    def outcome_classes(self):
        classes = {}
        for record in self.shipments:
            name = record.get("outcome")
            if name:
                classes[name] = classes.get(name, 0) + 1
        return classes

    def health_summary(self):
        """Plain-language read on how the run is going."""
        counters = self.counters
        processed = counters.get("processed", 0)
        if not processed:
            return "no shipments processed yet"
        rate = counters.get("success_rate")
        if rate is None:
            return "running"
        if rate >= 90:
            return "healthy"
        if rate >= 60:
            return "mixed — worth a look at the exceptions"
        return "struggling — most shipments are not completing"

    # -- systems -----------------------------------------------------------

    @property
    def systems(self):
        return self.state.get("systems") or []

    def system(self, key):
        for entry in self.systems:
            if entry.get("key") == key:
                return entry
        return None

    def system_named_in(self, question):
        lowered = question.casefold()
        if "dhl" in lowered:
            return self.system("DHL")
        if "qatar" in lowered:
            return self.system("QATAR")
        if "hub" in lowered or "logistics hub" in lowered:
            return self.system("hub")
        return None

    # -- events ------------------------------------------------------------

    @property
    def timeline(self):
        return self.state.get("timeline") or []

    def latest_events(self, limit=5):
        return self.timeline[:limit]

    def latest_step(self, record):
        """The most recent step recorded against one shipment."""
        steps = record.get("steps") or []
        return steps[-1] if steps else None

    @property
    def exceptions(self):
        return self.state.get("exceptions") or []

    @property
    def logs(self):
        return self.state.get("logs") or []


# ══════════════════════════════════════════════════════════════
# PRESENTATION
# ══════════════════════════════════════════════════════════════

def shipment_card(data, record):
    """
    Compact card for the UI. Only populated fields become rows; the UI shows
    an em dash for the rest rather than the assistant inventing one.
    """
    step = data.latest_step(record)
    latest_event = None
    if step:
        latest_event = "{0} — {1}".format(step.get("time"), step.get("text"))

    return {
        "type": "shipment",
        "reference": record.get("reference"),
        "state": record.get("state"),
        "rows": [
            ["Carrier", _show(record.get("carrier"))],
            ["Mode", MODE_WORDS.get(record.get("transport_mode"), "Unknown")],
            ["Tracking via", provider_name(record.get("provider"))],
            ["Current step", _show(record.get("step"))],
            ["Status", STATE_WORDS.get(record.get("state"), "unknown").capitalize()],
            ["Hub ETA (before)", _show(record.get("internal_eta"))],
            ["Carrier ETA", _show(record.get("provider_eta"))],
            ["Carrier ATA", _show(record.get("provider_ata"))],
            ["Carrier status", _show(record.get("provider_status"))],
            ["Latest event", _show(latest_event)],
            ["Last updated", _show(record.get("updated"))],
        ],
    }


def _list_shipments(records, limit=8):
    lines = []
    for record in records[:limit]:
        detail = record.get("error") or record.get("provider_status") or ""
        lines.append("• {0} ({1}){2}".format(
            record.get("reference"),
            _show(record.get("carrier")),
            " — {0}".format(detail) if detail else "",
        ))
    if len(records) > limit:
        lines.append("…and {0} more.".format(len(records) - limit))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# INTENT MATCHING
# ══════════════════════════════════════════════════════════════
#
# Scored keyword matching rather than exact commands, so "why did it break",
# "what went wrong with it" and "reason for failure" all land on the same
# intent. First highest score wins; ties resolve in listed order.

# Ordered MOST SPECIFIC FIRST, and matched first-hit-wins.
#
# Scoring by phrase length was wrong: "show me shipment" is a longer string
# than "no eta", so "Show me shipments with no ETA" scored as a generic
# shipment lookup. Specificity is a property of the intent, not of how many
# characters its trigger happens to have — so ordering carries it.
INTENTS = [
    # Conversation comes first so "hi, which shipments failed?" still routes to
    # the question rather than the greeting — greetings are matched on their own
    # in detect_intent() only when the message is short.
    ("greeting",    ["hello", "hi ", "hey", "good morning", "good afternoon",
                     "good evening", "salam", "hala", "yo "]),
    ("thanks",      ["thank", "thanks", "shukran", "appreciate", "well done",
                     "nice work", "great job", "perfect"]),
    ("identity",    ["who are you", "what are you", "your name", "are you human",
                     "are you ai", "are you a bot"]),
    ("capabilities",["what can you do", "what do you know", "how can you help",
                     "what should i ask"]),
    # ATLAS copilot. A request to solve a verification is refused before
    # anything else can match it.
    ("code_request", ["solve the code", "solve the security", "enter the code",
                      "type the code", "read the code", "fill the code", "fill in the code",
                      "bypass", "captcha", "security code for me", "do the verification"]),
    ("attention",   ["need my attention", "needs my attention", "needs attention",
                     "what should i look at", "where should i look", "anything urgent",
                     "what do i need to do", "requires attention", "what needs"]),
    ("human",       ["human action", "waiting for human", "waiting for a person",
                     "waiting shipments", "show waiting", "needs a person",
                     "human verification", "shipments waiting"]),
    ("latest_failure", ["latest failure", "last failure", "most recent failure",
                        "why did the latest", "why did the last"]),
    ("summary",     ["summarize", "summarise", "sum up", "summary of this run",
                     "summary of the run", "summarize this run"]),
    ("changed",     ["what changed", "changed recently", "what's new", "whats new",
                     "anything new", "recent changes"]),
    # A full written report, and the "all X" cuts of it.
    ("report",      ["analysis", "analyse", "analyze", "report", "breakdown",
                     "give me the analysis", "full picture", "overview of the run",
                     "statistics", "stats"]),
    ("all_of",      ["all completed", "all complete", "all done", "all updated",
                     "all successful", "all success", "all written",
                     "all failed", "all failures", "all skipped",
                     "all dhl", "all qatar", "all air france", "all klm",
                     "all astral", "all afkl", "show me all", "list all",
                     "everything for"]),
    ("download",    ["download", "export", "csv", "spreadsheet", "excel",
                     "send me the data", "give me the data", "report"]),
    # Questions about measured history rather than this run's snapshot.
    # These are answered from the telemetry file, with sample sizes, or not
    # at all — see dashboard/insights.py.
    ("carrier_timing", ["why is afkl slow", "why is dhl slow", "which carrier is slowest",
                        "carrier timing", "compare carriers", "carrier performance",
                        "slowest carrier", "fastest carrier", "carrier speed",
                        "which carrier has the highest failure",
                        "carrier failure rate", "carrier statistics"]),
    ("strategy_perf",  ["strategy performance", "which strategy", "which locator",
                        "locator performance", "field detection performance",
                        "how does it find the field", "strategy success"]),
    ("failure_cats",   ["failure categories", "what goes wrong", "why do writes fail",
                        "what kind of failures", "failure breakdown",
                        "common failures"]),
    ("verification",   ["was the ata verified", "was it verified", "verification result",
                        "did the write persist", "was the eta verified",
                        "read back", "read-back"]),
    ("ml_recs",        ["ml recommendation", "what did the model", "model recommendation",
                        "is ml being used", "did ml help", "model decisions",
                        "ai recommendation"]),
    ("slowest",     ["slowest", "took the longest", "longest", "taking so long",
                     "which was slow"]),
    ("fastest",     ["fastest", "quickest", "shortest"]),
    ("average",     ["average", "avg", "typical", "mean time", "how long does",
                     "how long did each"]),
    # "which carrier is" was too greedy — it swallowed "Which carrier is
    # handling 157-49568713?", which is a lookup, not a comparison.
    ("compare",     ["compare", "versus", " vs ", "difference between",
                     "better than", "which carrier is better",
                     "which carrier is faster", "which carrier is worse",
                     "carrier performance", "carriers doing"]),
    ("duration",    ["how long has", "how long is", "runtime", "running for",
                     "how long did the run"]),
    ("health",      ["how is it going", "how are we doing", "everything ok",
                     "everything okay", "is it healthy", "any problems",
                     "anything wrong", "all good"]),
    ("history",     ["history", "what happened before", "earlier", "so far",
                     "recap", "summary of everything"]),
    # Re-run is checked next: "re-run 157-49568713" must not be read as a
    # lookup of that shipment.
    ("reprocess",   ["re-run", "rerun", "reprocess", "re-process", "run again",
                     "try again", "retry", "update again", "refresh the eta",
                     "check again"]),
    ("help",        ["help", "what can you", "how do i use", "what do you know"]),
    ("run",         ["run status", "latest run", "how is the run", "run summary",
                     "what happened during", "overall run", "how is it going"]),
    ("under_clearance", ["under clearance", "clearance"]),
    ("worst_carrier", ["most failures", "worst carrier", "which carrier has",
                       "carrier with the most", "most problems"]),
    # Named systems must outrank generic verbs: "Is DHL currently processing?"
    # is a question about DHL, not about the processing queue.
    ("system",      ["is dhl", "is qatar", "dhl currently", "qatar currently",
                     "what happened with", "status of dhl", "status of qatar",
                     "system status", "integration", "how is dhl", "how is qatar"]),
    ("failed",      ["which shipments failed", "failed shipments", "failures",
                     "what failed", "list failed", "show me the failures"]),
    ("skipped",     ["skipped", "no eta or ata"]),
    ("no_eta",      ["no eta", "without eta", "missing eta", "no date"]),
    ("processing",  ["still processing", "currently processing", "in progress",
                     "being processed", "right now", "in flight"]),
    ("logs",        ["logs", "log lines", "raw log", "show the log"]),
    ("latest_event", ["latest event", "last event", "recent event",
                      "what just happened", "latest activity", "recent activity"]),
    ("counts",      ["how many", "count", "total", "numbers", "statistics"]),
    ("why",         ["why did", "why was", "why is", "reason", "what went wrong",
                     "whats wrong", "what's wrong", "cause of"]),
    ("eta",         ["eta", "arrival", "when will", "when does", "delivery date"]),
    ("carrier",     ["which carrier", "who is handling", "what carrier",
                     "carrier for", "carrier is handling"]),
    ("shipment",    ["where is", "status of shipment", "tell me about", "look up",
                     "show me shipment", "what happened to", "details for",
                     "status of"]),
]


# Common misspellings, so a typo does not become "I did not understand".
TYPOS = {
    "shippment": "shipment", "shipmnet": "shipment", "shipent": "shipment",
    "carier": "carrier", "carrer": "carrier", "faild": "failed",
    "failes": "failed", "skiped": "skipped", "statuss": "status",
    "etaa": "eta", "reprocces": "reprocess", "donwload": "download",
    "csvv": "csv", "analize": "analyse", "analyze": "analyse",
}


def normalise(question):
    lowered = " {0} ".format(question.casefold().strip())
    for wrong, right in TYPOS.items():
        lowered = lowered.replace(wrong, right)
    return lowered


def detect_intent(question):
    """
    First intent with a matching phrase wins; INTENTS is ordered by specificity.

    A greeting only wins when the message is essentially JUST a greeting —
    "hi" is a greeting, "hi, which shipments failed?" is a question.
    """
    lowered = normalise(question)
    words = len(lowered.split())

    for name, phrases in INTENTS:
        if not any(phrase in lowered for phrase in phrases):
            continue
        if name in ("greeting", "thanks") and words > 6:
            continue          # pleasantry attached to a real question
        return name
    return None


# ══════════════════════════════════════════════════════════════
# ANSWERS
# ══════════════════════════════════════════════════════════════

def _ms(value):
    if value is None:
        return UNKNOWN
    return "{0} ms".format(value) if value < 1000 else "{0:.1f} s".format(value / 1000)


def _answer_greeting(data):
    counters = data.counters
    if data.is_running:
        current = data.in_flight
        where = ("working on **{0}**".format(current.get("reference"))
                 if current else "between shipments")
        return ("Hello. The run is going — {0}, {1} processed so far.\n\n"
                "Ask me about any shipment, or what has failed.").format(
                    where, counters.get("processed", 0))
    if data.shipments:
        return ("Hello. Nothing is running at the moment. The last run handled "
                "{0} shipment(s) — {1} written to the Hub, {2} failed.\n\n"
                "Ask me about any of them.").format(
                    counters.get("processed", 0), counters.get("successful", 0),
                    counters.get("failed", 0))
    return ("Hello. No run has started yet, so I have nothing to report. "
            "I will have shipment data the moment one begins.")


def _answer_thanks(data):
    return "Any time. Ask if you need anything else from this run."


def _answer_identity(data):
    return ("I am the Control Tower assistant. I read this run's data and "
            "answer from it — nothing else.\n\n"
            "I have no language model behind me, which is deliberate: it means "
            "I cannot invent an ETA, a carrier or a status. If the automation "
            "did not collect something, I will tell you so rather than guess.")


def _answer_capabilities(data):
    return ("I can answer from the live run:\n\n"
            "• Any shipment — status, ETA, ATA, carrier, why it failed\n"
            "• Totals, success rate, what was skipped\n"
            "• Which carrier has the most failures\n"
            "• Slowest and fastest shipments, average processing time\n"
            "• System status for DHL, Qatar Airways and the Hub\n"
            "• A CSV of any of it\n\n"
            "I can also re-run a shipment: say \u201cre-run 157-49568713\u201d.")


def _answer_download(data):
    if not data.shipments:
        return NO_DATA
    counters = data.counters
    return ("Here is the data from this run. The CSV carries every field the "
            "automation collected — empty cells mean it never had that value.\n\n"
            "{0} processed · {1} written to the Hub · {2} skipped · {3} failed"
            ).format(counters.get("processed", 0), counters.get("successful", 0),
                     counters.get("skipped", 0), counters.get("failed", 0))


def _from_telemetry(kind, produce, topic):
    """
    Run one telemetry answer and carry its provenance.

    Returns (text, sources). A None from `produce` means the file held nothing
    to count, and the caller says so plainly rather than filling the gap.
    """
    if insights is None:
        return ("The telemetry reader is not available on this machine, so I "
                "cannot answer {0} from measured data.".format(topic), [])
    try:
        text = produce()
    except Exception as error:
        return ("I could not read the telemetry to answer {0}: {1}".format(
            topic, error), [])
    if not text:
        return (insights.no_data_answer(topic), [])
    return (text, insights.sources_used(kind))


def _answer_slowest(data):
    rows = data.slowest()
    if not rows:
        return NO_DATA
    lines = ["Slowest shipments in this run:"]
    for record in rows:
        lines.append("• **{0}** ({1}) — {2}".format(
            record.get("reference"), _show(record.get("carrier")),
            _ms(record.get("duration_ms"))))
    average = data.average_ms()
    if average is not None:
        lines.append("\nAverage across the run: {0}".format(_ms(average)))
    return "\n".join(lines)


def _answer_fastest(data):
    rows = data.fastest()
    if not rows:
        return NO_DATA
    return "Fastest shipments:\n\n" + "\n".join(
        "• **{0}** ({1}) — {2}".format(r.get("reference"), _show(r.get("carrier")),
                                       _ms(r.get("duration_ms"))) for r in rows)


def _answer_average(data):
    average = data.average_ms()
    if average is None:
        return NO_DATA
    lines = ["Average processing time: **{0}** across {1} shipment(s).".format(
        _ms(average), len(data.timed()))]
    for carrier, stats in sorted(data.by_carrier().items()):
        rows = [s for s in data.shipments if (s.get("carrier") or "Unknown") == carrier]
        carrier_average = data.average_ms(rows)
        if carrier_average is not None:
            lines.append("• {0}: {1} over {2} shipment(s)".format(
                carrier, _ms(carrier_average), stats["total"]))
    return "\n".join(lines)


def _answer_compare(data):
    groups = data.by_carrier()
    if len(groups) < 2:
        if not groups:
            return NO_DATA
        name, stats = list(groups.items())[0]
        return ("Only one carrier in this run so far — **{0}**, {1} shipment(s), "
                "{2} written to the Hub.").format(name, stats["total"], stats["updated"])

    lines = ["Carrier comparison for this run:"]
    for name, stats in sorted(groups.items(), key=lambda kv: -kv[1]["total"]):
        rows = [s for s in data.shipments if (s.get("carrier") or "Unknown") == name]
        average = data.average_ms(rows)
        rate = round((stats["updated"] + stats.get("partial", 0))
                     / stats["total"] * 100) if stats["total"] else 0
        lines.append("• **{0}** — {1} shipment(s), {2}% written, {3} failed, "
                     "average {4}".format(name, stats["total"], rate,
                                          stats["failed"], _ms(average)))
    return "\n".join(lines)


def _answer_duration(data):
    if data.runtime is None:
        return "No run has started, so there is nothing running yet."
    state = "so far" if data.is_running else "in total"
    return "The run has been going **{0}** {1}, processing {2} shipment(s).".format(
        data.runtime, state, data.counters.get("processed", 0))


def _answer_health(data):
    counters = data.counters
    if not data.shipments:
        return ("Nothing has been processed yet, so there is nothing to judge. "
                "The systems are {0}.".format(
                    ", ".join("{0} {1}".format(s["name"], s["state"])
                              for s in data.systems) or "not reporting"))

    lines = ["The run looks **{0}**.".format(data.health_summary()),
             "{0} processed — {1} written to the Hub, {2} skipped, {3} failed."
             .format(counters.get("processed", 0), counters.get("successful", 0),
                     counters.get("skipped", 0), counters.get("failed", 0))]

    broken = [s for s in data.systems if s.get("state") == "error"]
    if broken:
        lines.append("\nSystems reporting an error:")
        for system in broken:
            lines.append("• {0} — {1}".format(system["name"],
                                              system.get("last_error") or "error"))
    classes = data.outcome_classes()
    if classes:
        lines.append("\nWhat went wrong, by type:")
        for name, count in sorted(classes.items(), key=lambda kv: -kv[1]):
            lines.append("• {0} × {1}".format(name, count))
    return "\n".join(lines)


def _answer_history(data):
    if not data.shipments and not data.timeline:
        return NO_DATA
    counters = data.counters
    lines = ["Everything this run has done:",
             "",
             "Runtime {0} · {1} processed · {2} written · {3} skipped · {4} failed"
             .format(_show(data.runtime), counters.get("processed", 0),
                     counters.get("successful", 0), counters.get("skipped", 0),
                     counters.get("failed", 0))]
    pages = data.by_page()
    if pages:
        lines.append("Hub pages worked: " + ", ".join(
            "page {0} ({1})".format(k, v) for k, v in sorted(
                pages.items(), key=lambda kv: (kv[0] is None, kv[0]))))
    if data.timeline:
        lines.append("")
        lines.append("Most recent activity:")
        for event in data.latest_events(6):
            lines.append("• {0} — {1}".format(event.get("time"), event.get("text")))
    return "\n".join(lines)


def _answer_report(data):
    """
    The full written analysis of the run.

    Everything here is counted from the shipment records the dashboard shows,
    so the report can never disagree with the tables.
    """
    if not data.shipments:
        return ("No shipments have been processed yet, so there is nothing to "
                "analyse. Ask again once a run is under way.")

    counters = data.counters
    processed = counters.get("processed", 0)
    written = counters.get("successful", 0) + counters.get("partial", 0)
    lines = [
        "**Run analysis**",
        "",
        "Status {0} · runtime {1} · hub filter {2}".format(
            data.status, _show(data.runtime), _show(data.target_status)),
        "{0} processed — **{1} written to the Hub**, {2} skipped, {3} failed.".format(
            processed, written, counters.get("skipped", 0), counters.get("failed", 0)),
        "Success rate {0}. The run looks **{1}**.".format(
            "{0}%".format(counters["success_rate"])
            if counters.get("success_rate") is not None else UNKNOWN,
            data.health_summary()),
    ]

    groups = data.by_carrier()
    if groups:
        lines += ["", "**By carrier**"]
        for name, stats in sorted(groups.items(), key=lambda kv: -kv[1]["total"]):
            rows = [s for s in data.shipments if (s.get("carrier") or "Unknown") == name]
            ok = stats["updated"] + stats.get("partial", 0)
            lines.append("• {0} — {1} shipment(s), {2} written, {3} failed, "
                         "{4} skipped, average {5}".format(
                             name, stats["total"], ok, stats["failed"],
                             stats["skipped"], _ms(data.average_ms(rows))))

    classes = data.outcome_classes()
    if classes:
        lines += ["", "**Why things did not complete**"]
        for name, count in sorted(classes.items(), key=lambda kv: -kv[1]):
            lines.append("• {0} × {1}".format(name, count))

    no_eta = data.without_eta()
    lines += ["", "**Dates**",
              "• Carrier ETA found on {0} of {1}".format(
                  len(data.shipments) - len(no_eta), len(data.shipments)),
              "• Carrier ATA found on {0}".format(len(data.with_ata()))]

    slow = data.slowest(1)
    if slow:
        lines.append("• Slowest: {0} at {1}; average {2}".format(
            slow[0].get("reference"), _ms(slow[0].get("duration_ms")),
            _ms(data.average_ms())))

    broken = [s for s in data.systems if s.get("state") == "error"]
    if broken:
        lines += ["", "**Systems reporting an error**"]
        for system in broken:
            lines.append("• {0} — {1}".format(
                system["name"], system.get("last_error") or "error"))

    return "\n".join(lines)


# "all completed", "all DHL" and friends: which slice of the run is meant.
ALL_OF_STATES = [
    (("completed", "complete", "done", "updated", "successful", "success",
      "written"), "updated", "written to the Hub"),
    (("failed", "failure"), "failed", "failed"),
    (("skipped",), "skipped", "skipped"),
    (("partial", "partly"), "partial", "partly updated"),
    (("processing", "in progress"), "processing", "being processed"),
]


def _answer_all_of(data, question):
    """
    Answer "all completed", "all DHL", "all failed for Qatar" and so on.

    A state, a carrier, or both — whichever the question names.
    """
    if not data.shipments:
        return NO_DATA

    lowered = normalise(question)
    rows = data.shipments
    described = []

    for words, state, label in ALL_OF_STATES:
        if any(w in lowered for w in words):
            rows = [r for r in rows if r.get("state") == state]
            described.append(label)
            break

    # Match the carrier by SUBSTRING, not by exact name. "all dhl" must cover
    # DHL Express and DHL Aviation alike — matching the first exact carrier
    # name found returned only one of them and quietly hid the rest.
    carrier_terms = sorted(
        {word for name in {(r.get("carrier") or "") for r in data.shipments}
         for word in name.casefold().split() if len(word) > 2},
        key=len, reverse=True,
    )
    aliases = {"afkl": "af", "france": "france", "airways": None}

    chosen = None
    for term in carrier_terms:
        if aliases.get(term, term) and term in lowered:
            chosen = term
            break
    if chosen is None:
        for key in ("dhl", "qatar", "air france", "klm", "astral", "afkl"):
            if key in lowered:
                chosen = key
                break

    if chosen:
        matched = [r for r in rows
                   if chosen in (r.get("carrier") or "").casefold()
                   or chosen in (r.get("provider") or "").casefold()]
        names = sorted({r.get("carrier") for r in matched if r.get("carrier")})
        rows = matched
        described.append(", ".join(names) if names else chosen.upper())

    what = " · ".join(described) if described else "all shipments"

    if not rows:
        return ("Nothing in this run matches **{0}**. It has {1} shipment(s) in "
                "total — ask for \u201cthe analysis\u201d for the full picture."
                ).format(what, len(data.shipments))

    lines = ["**{0}** — {1} shipment(s)".format(what, len(rows)), ""]
    for record in rows[:15]:
        bits = [record.get("reference"), _show(record.get("carrier"))]
        if _has(record.get("provider_eta")):
            bits.append("ETA " + record["provider_eta"])
        if _has(record.get("provider_ata")):
            bits.append("ATA " + record["provider_ata"])
        if record.get("state") in ("failed", "skipped") and _has(record.get("error")):
            bits.append(record["error"])
        lines.append("• " + " — ".join(str(b) for b in bits))
    if len(rows) > 15:
        lines.append("…and {0} more. Download the CSV for the full list."
                     .format(len(rows) - 15))

    timed = [r for r in rows if r.get("duration_ms") is not None]
    if timed:
        lines += ["", "Average processing {0}".format(_ms(data.average_ms(timed)))]
    return "\n".join(lines)


def _answer_run(data):
    counters = data.counters
    progress = data.progress
    lines = [
        "Run status: **{0}**".format(data.status),
        "Runtime: {0}".format(_show(data.runtime)),
        "Mode: {0}".format(
            "dry run, nothing saved" if data.run.get("dry_run") is True
            else "live, hub records are saved" if data.run.get("dry_run") is False
            else UNKNOWN),
        "Processed {0} — {1} updated, {2} skipped, {3} failed.".format(
            counters.get("processed", 0), counters.get("successful", 0),
            counters.get("skipped", 0), counters.get("failed", 0)),
    ]
    if progress.get("total") is None:
        lines.append(
            "The run total isn't known yet — pagination hasn't finished, so "
            "there's no honest denominator to give you."
        )
    if data.current.get("step"):
        lines.append("Current step: {0}".format(data.current["step"]))
    return "\n".join(lines)


def _answer_under_clearance(data):
    target = data.target_status
    if not target:
        return NO_DATA
    if not data.shipments:
        return (
            "This run is filtering the hub on **{0}**, but no shipment has been "
            "picked up yet.".format(target)
        )
    counters = data.counters
    return (
        "Every shipment in this run is under the **{0}** hub filter — that's "
        "what the automation searches for.\n\n"
        "{1} handled so far: {2} updated, {3} skipped, {4} failed.\n\n{5}"
    ).format(
        target, counters.get("processed", 0), counters.get("successful", 0),
        counters.get("skipped", 0), counters.get("failed", 0),
        _list_shipments(data.shipments),
    )


def _answer_worst_carrier(data):
    worst = data.worst_carrier()
    if worst is None:
        if not data.shipments:
            return NO_DATA
        return "No carrier has recorded a failure in this run."
    name, stats = worst
    return (
        "**{0}** has the most failures: {1} of {2} shipment(s) failed "
        "({3} updated, {4} skipped)."
    ).format(name, stats["failed"], stats["total"], stats["updated"], stats["skipped"])


def _answer_failed(data):
    if not data.shipments:
        return NO_DATA
    if not data.failed:
        return "No shipment has failed in this run."
    return "{0} shipment(s) failed:\n\n{1}".format(
        len(data.failed), _list_shipments(data.failed))


def _answer_skipped(data):
    if not data.shipments:
        return NO_DATA
    if not data.skipped:
        return "No shipment has been skipped in this run."
    return (
        "{0} shipment(s) were skipped — the carrier returned no ETA or ATA:\n\n{1}"
    ).format(len(data.skipped), _list_shipments(data.skipped))


def _answer_processing(data):
    current = data.in_flight
    if current:
        return "Right now: **{0}** ({1}) — {2}".format(
            current.get("reference"), provider_name(current.get("provider")),
            _show(data.current.get("step")))
    if data.processing:
        return "{0} shipment(s) marked as processing:\n\n{1}".format(
            len(data.processing), _list_shipments(data.processing))
    if data.is_running:
        return ("Nothing is mid-flight at this instant — the runner is between "
                "shipments or scanning the next hub list page.")
    return "The automation isn't running, so nothing is being processed."


def _answer_no_eta(data):
    if not data.shipments:
        return NO_DATA
    rows = [s for s in data.shipments if not _has(s.get("provider_eta"))]
    if not rows:
        return "Every shipment in this run came back with a carrier ETA."
    return "{0} shipment(s) have no carrier ETA:\n\n{1}".format(
        len(rows), _list_shipments(rows, limit=10))


def _answer_counts(data):
    counters = data.counters
    progress = data.progress
    lines = [
        "Processed: {0}".format(counters.get("processed", 0)),
        "Updated in the hub: {0}".format(counters.get("successful", 0)),
        "Skipped: {0}".format(counters.get("skipped", 0)),
        "Failed: {0}".format(counters.get("failed", 0)),
        "Success rate: {0}".format(
            "{0}%".format(counters["success_rate"])
            if counters.get("success_rate") is not None else UNKNOWN),
        "Queued from the hub: {0} across {1} list page(s)".format(
            progress.get("discovered", 0), progress.get("pages_scanned", 0)),
    ]
    if progress.get("total") is None:
        lines.append("Run total: not known yet — pagination hasn't finished.")
    return "\n".join(lines)


def _answer_system(data, question):
    target = data.system_named_in(question)
    if target is None:
        if not data.systems:
            return NO_DATA
        lines = ["System status:"]
        for entry in data.systems:
            lines.append("• {0}: {1}{2}".format(
                entry.get("name"), entry.get("state"),
                " — {0}".format(entry["last_error"]) if entry.get("last_error") else ""))
        return "\n".join(lines)

    handled = [s for s in data.shipments if s.get("provider") == target.get("key")]
    updated = len([s for s in handled if s.get("state") == "updated"])
    lines = [
        "**{0}** is currently **{1}**.".format(target.get("name"), target.get("state")),
        "Last successful operation: {0}".format(_show(target.get("last_success"))),
    ]
    if target.get("activity"):
        lines.append("Activity: {0}".format(target["activity"]))
    if target.get("last_error"):
        lines.append("Last error: {0}".format(target["last_error"]))
    if handled:
        lines.append("Shipments via this carrier: {0}, of which {1} updated.".format(
            len(handled), updated))
    else:
        lines.append("No shipment has gone through this carrier yet in this run.")
    return "\n".join(lines)


def _answer_latest_event(data, record=None):
    if record is not None:
        step = data.latest_step(record)
        if step is None:
            return "No step has been recorded for {0} yet.".format(record.get("reference"))
        return "Latest step for **{0}**: {1} — {2}".format(
            record.get("reference"), step.get("time"), step.get("text"))

    events = data.latest_events(5)
    if not events:
        return NO_DATA
    return "Most recent activity:\n\n" + "\n".join(
        "• {0} — {1}".format(event.get("time"), event.get("text")) for event in events)


def _answer_logs(data):
    if not data.logs:
        return NO_DATA
    lines = ["Most recent log lines:"]
    for entry in data.logs[:10]:
        lines.append("{0} [{1}] {2}".format(
            entry.get("time"), (entry.get("level") or "").upper(), entry.get("message")))
    return "\n".join(lines)


def _help(data):
    example = data.shipments[0].get("reference") if data.shipments else None
    if example:
        head = (
            "I answer from this run's real data only. Try:\n\n"
            "• What's the status of {0}?\n"
            "• What's the ETA for {0}?\n"
            "• Why did {0} fail?\n"
        ).format(example)
    else:
        head = (
            "I answer from this run's real data only. Once shipments are picked "
            "up you can ask:\n\n"
            "• What's the status of <BOL or AWB>?\n"
            "• What's the ETA for <BOL or AWB>?\n"
            "• Why did <BOL or AWB> fail?\n"
        )
    return head + (
        "• Which shipments failed?\n"
        "• How many are still processing?\n"
        "• Which carrier has the most failures?\n"
        "• Is DHL currently processing?\n\n"
        "The automation never reads origin, destination, route or order numbers, "
        "so I can't answer those. If I don't have something, I'll say so."
    )




# ══════════════════════════════════════════════════════════════
# ATLAS — OPERATIONAL COPILOT
# ══════════════════════════════════════════════════════════════
#
# The same grounded assistant, made proactive. Everything below reads the
# run snapshot through RunData and nothing else: no network, no model, no
# write. Three additions:
#
#   notices(data)      what ATLAS noticed — only actionable conditions
#   suggestions(data)  the questions worth asking about THIS run, now
#   ui actions         safe dashboard navigation attached to a reply:
#                      filter Live operations, open a shipment, go to a page.
#                      Nothing here can change a shipment, the run, or a
#                      human verification — there is no action of that kind
#                      to attach.

import time as _time
from datetime import datetime as _datetime

MODE_WORDS = {"air": "Air", "ocean": "Ocean", "road": "Road", "rail": "Rail",
              "unknown": "Unknown"}
MODE_PHRASES = [("ocean", r"\b(ocean|sea|ship|ships|vessel|maritime)\b"),
                ("air", r"\b(air|plane|planes|flight|airline)\b"),
                ("road", r"\b(road|truck|trucks|van|courier)\b"),
                ("rail", r"\b(rail|train|trains|railway)\b")]
STATE_PHRASES = [("failed", r"\b(fail|failed|failure|failures|failing)\b"),
                 ("skipped", r"\bskipped\b"),
                 ("processing", r"\b(processing|in progress|in flight|running now)\b"),
                 ("human_timeout", r"\b(timed out|timeout)\b"),
                 ("updated", r"\b(completed|complete|successful|success|succeeded|written|updated)\b")]
IMPERATIVE = re.compile(r"^\s*(show|open|filter|list|display|view|find|go to|take me|bring up)\b", re.I)
UI_ACTIONS = ("filter", "open", "page")      # the whole vocabulary


def mode_of(record):
    mode = (record or {}).get("transport_mode")
    return mode if mode in MODE_WORDS else "unknown"


def _dur(seconds):
    if seconds is None:
        return UNKNOWN
    seconds = int(seconds)
    if seconds < 60:
        return "{0}s".format(seconds)
    if seconds < 3600:
        return "{0}m {1:02d}s".format(seconds // 60, seconds % 60)
    return "{0}h {1:02d}m".format(seconds // 3600, seconds % 3600 // 60)


def _since(stamp):
    """Seconds since a bridge 'YYYY-mm-dd HH:MM:SS' local stamp, or None."""
    try:
        return max(0, (_datetime.now() - _datetime.strptime(
            str(stamp), "%Y-%m-%d %H:%M:%S")).total_seconds())
    except Exception:
        return None


def human_waiting(data):
    action = data.state.get("human_action") or {}
    return action if action.get("waiting") else None


def carriers_in(data):
    """Carrier names this run has seen, with their provider keys."""
    seen = {}
    for record in data.shipments:
        if record.get("carrier"):
            seen[record["carrier"]] = record.get("provider")
    return seen


def carrier_named(data, question):
    """The carrier a question names, matched against THIS run's carriers."""
    lowered = question.casefold()
    best = None
    for name, provider in carriers_in(data).items():
        tokens = [name.casefold()] + [t for t in re.split(r"[\s/,-]+", name.casefold())
                                      if len(t) >= 3 and t not in ("lines", "cargo", "the",
                                                                   "express", "line", "shipping")]
        if provider:
            tokens.append(str(provider).casefold())
        for token in tokens:
            if re.search(r"\b" + re.escape(token) + r"\b", lowered):
                if best is None or len(token) > len(best[1]):
                    best = (name, token)
    return best


def _filter_of(data, question):
    """state / mode / carrier the question asks to see, or {}."""
    found = {}
    lowered = question.casefold()
    for state, pattern in STATE_PHRASES:
        if re.search(pattern, lowered):
            found["state"] = state
            break
    for mode, pattern in MODE_PHRASES:
        if re.search(pattern, lowered):
            found["mode"] = mode
            break
    named = carrier_named(data, question)
    if named:
        found["carrier"] = named[1]
        found["carrier_name"] = named[0]
    return found


def _filter_label(f):
    words = {"failed": "Failed", "skipped": "Skipped", "processing": "Processing",
             "updated": "Completed", "human_timeout": "Human timeout"}
    parts = []
    if f.get("state"):
        parts.append(words.get(f["state"], f["state"]))
    if f.get("carrier_name"):
        parts.append(f["carrier_name"])
    if f.get("mode"):
        parts.append(MODE_WORDS[f["mode"]])
    return " · ".join(parts)


def _matches(record, f):
    if f.get("state") and record.get("state") != f["state"]:
        return False
    if f.get("mode") and mode_of(record) != f["mode"]:
        return False
    if f.get("carrier"):
        hay = " ".join(str(x) for x in (record.get("carrier"), record.get("provider"))).casefold()
        if f["carrier"] not in hay:
            return False
    return True


def notices(data):
    """What ATLAS noticed. Only conditions an operator can act on — an empty
    list is the right answer when nothing needs anyone."""
    out = []
    waiting = human_waiting(data)
    if waiting:
        waited = _since(waiting.get("opened_at"))
        out.append({"level": "warn",
                    "text": "{0} needs human action on {1} — waiting {2}.".format(
                        waiting.get("reference"), waiting.get("carrier") or "the carrier page",
                        _dur(waited)),
                    "action": {"type": "page", "page": "human"}})
    if data.status == "fatal":
        out.append({"level": "error", "text": "The run stopped on an error.",
                    "action": {"type": "page", "page": "logs"}})
    groups = {}
    for record in data.failed:
        groups.setdefault(record.get("carrier") or "Unknown carrier", []).append(record)
    for name, rows in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:2]:
        out.append({"level": "error",
                    "text": "{0} {1} shipment{2} failed.".format(len(rows), name,
                                                                "" if len(rows) == 1 else "s"),
                    "action": {"type": "filter", "state": "failed", "carrier": name.casefold(),
                               "label": "Failed · " + name}})
    rest = len(data.failed) - sum(len(r) for _, r in sorted(
        groups.items(), key=lambda kv: -len(kv[1]))[:2])
    if rest > 0:
        out.append({"level": "error", "text": "{0} more failure{1} on other carriers.".format(
            rest, "" if rest == 1 else "s"),
            "action": {"type": "filter", "state": "failed", "label": "Failed"}})
    timeouts = data.by_state("human_timeout")
    if timeouts:
        out.append({"level": "warn",
                    "text": "{0} shipment{1} timed out waiting for a person.".format(
                        len(timeouts), "" if len(timeouts) == 1 else "s"),
                    "action": {"type": "filter", "state": "human_timeout", "label": "Human timeout"}})
    recovery = data.state.get("recovery") or {}
    if recovery.get("status") == "EXHAUSTED":
        out.append({"level": "error", "text": "No safe recovery succeeded for {0}.".format(
            recovery.get("error_class")), "action": {"type": "page", "page": "atlas"}})
    current = data.in_flight
    if current and current.get("started_epoch") and current.get("state") == "processing":
        elapsed = _time.time() - current["started_epoch"]
        if elapsed >= 120:
            out.append({"level": "info",
                        "text": "{0} has been on “{1}” for {2}.".format(
                            current.get("reference"),
                            data.current.get("step") or current.get("step") or "its current step",
                            _dur(elapsed)),
                        "action": {"type": "open", "reference": current.get("reference")}})
    done = len(data.updated)
    if done:
        out.append({"level": "ok",
                    "text": "{0} shipment{1} completed and verified in the Hub.".format(
                        done, "" if done == 1 else "s"),
                    "action": {"type": "filter", "state": "updated", "label": "Completed"}})
    return out[:5]


def suggestions(data):
    """3–6 questions worth asking about this run right now."""
    chips = []
    if human_waiting(data) or data.by_state("waiting_for_human"):
        chips += ["What needs my attention?", "Show waiting shipments"]
    if data.failed:
        chips += ["Show failed shipments", "Why did the latest shipment fail?"]
    if data.is_running:
        chips += ["What is still processing?", "How long has it been running?"]
    if data.shipments and not data.failed and not human_waiting(data):
        chips += ["Summarize this run", "Compare the carriers"]
    for extra in ("What changed recently?", "Summarize this run", "Compare the carriers",
                  "What needs my attention?"):
        if len(chips) >= 6:
            break
        if extra not in chips and data.shipments:
            chips.append(extra)
    if not chips:
        chips = ["How is the run going?", "What can you do?"]
    seen, out = set(), []
    for chip in chips:
        if chip not in seen:
            seen.add(chip)
            out.append(chip)
    return out[:6]


def atlas_brief(state):
    """The ATLAS panel's header: status, the counts that matter, notices,
    suggestions. One snapshot in, one dict out."""
    try:
        data = RunData(state)
        waiting = human_waiting(data)
        status = ("Waiting for your action" if waiting
                  else "Watching this run" if data.is_running
                  else "Run complete" if data.status == "finished"
                  else "Run stopped on an error" if data.status == "fatal"
                  else "Standing by")
        return {
            "status": status,
            "run_id": data.run.get("run_id"),
            "summary": {
                "completed": len(data.updated),
                "failed": len(data.failed),
                "waiting": len(data.by_state("waiting_for_human")),
                "processing": len(data.processing),
                "skipped": len(data.skipped),
            },
            "notices": notices(data),
            "suggestions": suggestions(data),
            "grounded": True,
        }
    except Exception as error:
        return {"status": "Unavailable", "summary": {}, "notices": [], "suggestions": [],
                "error": str(error)[:200], "grounded": True}


# -- answers ---------------------------------------------------------------

def _answer_attention(data):
    found = [n for n in notices(data) if n["level"] != "ok"]
    if not found:
        if not data.shipments:
            return "Nothing needs your attention — no shipment has been processed yet."
        return ("Nothing needs your attention right now. {0} completed, {1} failed, "
                "{2} still processing.").format(len(data.updated), len(data.failed),
                                                len(data.processing))
    lines = ["{0} thing{1} need{2} your attention:".format(
        len(found), "" if len(found) == 1 else "s", "s" if len(found) == 1 else "")]
    lines += ["• " + n["text"] for n in found]
    return "\n".join(lines)


def _answer_human(data):
    waiting = human_waiting(data)
    rows = data.by_state("waiting_for_human")
    if not waiting and not rows:
        timeouts = data.by_state("human_timeout")
        text = "No shipment is waiting for human action right now."
        if timeouts:
            text += " {0} timed out earlier in this run: {1}.".format(
                len(timeouts), ", ".join(r.get("reference") for r in timeouts[:5]))
        return text
    lines = []
    if waiting:
        lines.append("**{0}** is waiting for a person on {1}.".format(
            waiting.get("reference"), waiting.get("carrier") or "the carrier page"))
        lines.append("Action: {0}".format(waiting.get("instructions") or "human verification"))
        lines.append("Waiting: {0}".format(_dur(_since(waiting.get("opened_at")))))
        if waiting.get("deadline"):
            lines.append("Times out: {0}".format(str(waiting["deadline"])[11:]))
        lines.append("Run: {0}".format(waiting.get("run_id") or UNKNOWN))
    others = [r for r in rows if not waiting or r.get("reference") != waiting.get("reference")]
    if others:
        lines.append("Also waiting: " + ", ".join(r.get("reference") for r in others))
    lines.append("")
    lines.append("Open Human Action brings that tab to the front. The verification is "
                 "done by a person; the automation never reads or types a code.")
    return "\n".join(lines)


def _answer_code_request(data):
    text = ("I won't do that. Security verification is completed by a person, in the "
            "browser — the automation never reads, solves or types a verification code, "
            "and neither do I.")
    waiting = human_waiting(data)
    if waiting:
        text += ("\n\n**{0}** is waiting on {1}. Open Human Action to bring the tab "
                 "forward, complete the step there, then press Resume.").format(
                     waiting.get("reference"), waiting.get("carrier") or "the carrier page")
    return text


def _explain(data, record):
    """Why a shipment ended as it did — from its recorded events only."""
    reference = record.get("reference")
    state = record.get("state")
    lines = ["**{0}** — {1}.".format(reference, STATE_WORDS.get(state, state or "unknown"))]
    if record.get("outcome"):
        lines.append("Outcome: {0}".format(record["outcome"]))
    if record.get("error"):
        lines.append("Recorded reason: {0}".format(record["error"]))
    steps = record.get("steps") or []
    human = [e for e in (data.state.get("human_events") or []) if e.get("reference") == reference]
    atlas_events = ((record.get("atlas") or {}).get("events")) or []
    if steps:
        lines += ["", "What the automation recorded, in order:"]
        lines += ["• {0} — {1}".format(s.get("time"), s.get("text")) for s in steps[-10:]]
    if human:
        lines += ["", "Human action:"]
        lines += ["• {0} — {1}{2}".format(e.get("time"), e.get("event"),
                                          " — " + e["detail"] if e.get("detail") else "")
                  for e in reversed(human[:6])]
    if atlas_events:
        lines += ["", "ATLAS:"]
        lines += ["• {0} — {1}{2}".format(e.get("time"), e.get("label"),
                                          " — " + e["detail"] if e.get("detail") else "")
                  for e in atlas_events[-5:]]
    if not steps and not human and not atlas_events:
        lines += ["", "No step-by-step trace was recorded for it in this view, so this "
                      "is all I can confirm."]
    else:
        lines += ["", "That is everything recorded; I can't confirm anything beyond it."]
    return "\n".join(lines)


def _answer_latest_failure(data):
    if not data.failed:
        return "No shipment has failed in this run."
    return _explain(data, data.failed[0])


def _answer_summary(data):
    if not data.shipments:
        return "Nothing to summarize yet — no shipment has been processed in this run."
    lines = ["Run {0} · {1}{2}".format(
        data.run.get("run_id") or UNKNOWN, data.status,
        " for " + data.runtime if data.runtime else "")]
    lines.append("{0} completed · {1} failed · {2} waiting · {3} processing · {4} skipped".format(
        len(data.updated), len(data.failed), len(data.by_state("waiting_for_human")),
        len(data.processing), len(data.skipped)))
    modes = {}
    for record in data.shipments:
        modes[mode_of(record)] = modes.get(mode_of(record), 0) + 1
    lines.append("By mode: " + ", ".join("{0} {1}".format(n, MODE_WORDS[m].lower())
                                        for m, n in sorted(modes.items(), key=lambda kv: -kv[1])))
    noticed = [n for n in notices(data) if n["level"] != "ok"]
    if noticed:
        lines += ["", "ATLAS noticed:"] + ["• " + n["text"] for n in noticed]
    return "\n".join(lines)


def _answer_changed(data):
    events = data.latest_events(6)
    if not events:
        return NO_DATA
    return "Most recent changes:\n\n" + "\n".join(
        "• {0} — {1}".format(e.get("time"), e.get("text")) for e in events)


def _answer_filtered(data, f):
    rows = [r for r in data.shipments if _matches(r, f)]
    label = _filter_label(f) or "Matching"
    if not data.shipments:
        return NO_DATA
    if not rows:
        return "No {0} shipments in this run.".format(label.lower() if label else "matching")
    by_state = {}
    for record in rows:
        by_state[record.get("state")] = by_state.get(record.get("state"), 0) + 1
    words = {"updated": "completed", "failed": "failed", "skipped": "skipped",
             "processing": "processing", "waiting_for_human": "waiting for a person",
             "human_timeout": "human timeout", "partial": "partly updated"}
    head = "{0} shipment{1} — {2}: {3}.".format(
        label, "" if len(rows) == 1 else "s", len(rows),
        ", ".join("{0} {1}".format(n, words.get(s, s)) for s, n in by_state.items()))
    return head + "\n\n" + _list_shipments(rows)


def ui_actions(data, question, reply, intent):
    """
    The dashboard actions a reply may carry, and whether the question asked
    for one to happen now ("show…", "open…"). Navigation and filtering only.
    """
    question = question or ""
    buttons, actions = [], []
    imperative = bool(IMPERATIVE.search(question))
    reference = reply.get("reference")
    record = data.find(reference) if reference else None

    if record is not None and reply.get("card"):
        open_it = {"type": "open", "reference": record.get("reference")}
        buttons.append({"label": "Open shipment {0}".format(record.get("reference")), "action": open_it})
        if imperative:
            actions.append(open_it)

    if intent in ("human", "attention", "code_request") and (
            human_waiting(data) or data.by_state("waiting_for_human")):
        go = {"type": "page", "page": "human"}
        buttons.append({"label": "Open Human Action", "action": go})
        if imperative and intent == "human":
            actions.append(go)

    if intent == "latest_failure" and data.failed:
        go = {"type": "open", "reference": data.failed[0].get("reference")}
        buttons.append({"label": "Open {0}".format(data.failed[0].get("reference")), "action": go})
        if imperative:
            actions.append(go)

    if record is None and intent not in ("human", "code_request", "latest_failure"):
        f = _filter_of(data, question)
        if intent == "failed" and "state" not in f:
            f["state"] = "failed"
        if f:
            rows = [r for r in data.shipments if _matches(r, f)]
            if rows:
                act = {"type": "filter", "label": _filter_label(f)}
                for key in ("state", "mode", "carrier"):
                    if f.get(key):
                        act[key] = f[key]
                buttons.append({"label": "View {0} in Live operations".format(
                    _filter_label(f) or "these"), "action": act})
                if imperative:
                    actions.append(act)

    if intent == "attention":
        for n in notices(data):
            if n["level"] != "ok" and n.get("action") and n["action"].get("type") != "page":
                buttons.append({"label": n["text"].rstrip("."), "action": n["action"]})
    # Only the whitelisted vocabulary ever leaves this function.
    buttons = [b for b in buttons if b["action"].get("type") in UI_ACTIONS][:4]
    actions = [a for a in actions if a.get("type") in UI_ACTIONS][:1]
    return buttons, actions


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════

def follow_ups(data, record=None, intent=None):
    """
    Next questions worth asking, built from what this run actually contains.

    A suggestion is only offered when the data behind it exists, so the chips
    can never lead somewhere the assistant has to refuse.
    """
    chips = []

    if record is not None:
        reference = record.get("reference")
        if _has(record.get("provider_eta")) and intent != "eta":
            chips.append("What is the ETA for {0}?".format(reference))
        if record.get("state") in ("failed", "skipped") and intent != "why":
            chips.append("Why did {0} fail?".format(reference))
        if (record.get("steps") or []) and intent != "latest_event":
            chips.append("What is the latest event for {0}?".format(reference))
        if record.get("carrier"):
            chips.append("What happened with {0}?".format(
                provider_name(record.get("provider"))))
    else:
        if data.failed:
            chips.append("Which shipments failed?")
        if data.skipped:
            chips.append("How many shipments were skipped?")
        if data.worst_carrier():
            chips.append("Which carrier has the most failures?")
        if data.in_flight or data.processing:
            chips.append("What is being processed right now?")
        if data.shipments and intent != "run":
            chips.append("What happened during the latest run?")
        if data.timeline and intent != "latest_event":
            chips.append("What is the latest event?")

    return chips[:4]


def downloads_for(data, intent, record=None):
    """
    Offer a CSV only where one makes sense, and only when rows exist behind it.
    A download button that produces an empty file is worse than no button.
    """
    if not data.shipments:
        return []

    offers = []

    def add(label, state_filter, count):
        if count:
            offers.append({"label": "{0} ({1})".format(label, count),
                           "url": "/api/export.csv?state=" + state_filter})

    if intent in ("download", "history", "counts", "run", "health",
                  "report", "all_of"):
        add("All shipments", "all", len(data.shipments))
        add("Written to Hub", "updated", len(data.updated))
        add("Failed", "failed", len(data.failed))
        add("Skipped", "skipped", len(data.skipped))
    elif intent == "failed":
        add("Failed", "failed", len(data.failed))
        add("All shipments", "all", len(data.shipments))
    elif intent == "skipped":
        add("Skipped", "skipped", len(data.skipped))
        add("All shipments", "all", len(data.shipments))
    elif intent in ("compare", "average", "slowest", "fastest",
                    "worst_carrier", "no_eta", "under_clearance"):
        add("All shipments", "all", len(data.shipments))

    return offers[:4]


def answer(question, state, context=None):
    """
    Answer one question and attach contextual follow-up chips.

    The suggestions are computed once, here, from the reply's own reference —
    so every chip is backed by data the assistant has already seen.
    """
    reply = _answer_core(question, state, context)
    reply.setdefault("sources", ["bridge.snapshot"])
    try:
        data = RunData(state)
        record = data.find(reply.get("reference")) if reply.get("reference") else None
        intent = detect_intent((question or "").strip())
        reply.setdefault("intent", intent)
        reply["suggestions"] = (follow_ups(data, record, intent) if record is not None
                                else suggestions(data)[:4])
        reply["downloads"] = downloads_for(data, intent, record)
        buttons, actions = ui_actions(data, question, reply, reply.get("intent"))
        reply["buttons"] = buttons
        reply["actions"] = actions
    except Exception:
        reply["suggestions"] = []
        reply["downloads"] = []
        reply["buttons"] = []
        reply["actions"] = []
    return reply


def _answer_core(question, state, context=None):
    """
    Answer one question.

    `context` may carry {"reference": "..."} from the previous exchange so
    follow-ups like "what's its ETA?" resolve. The caller owns that context —
    this module keeps no state between calls.

    Returns {"answer", "card", "reference", "grounded"}. Never raises.
    """
    try:
        question = (question or "").strip()
        data = RunData(state)
        context = context or {}

        if not question:
            return {"answer": _help(data), "card": None,
                    "reference": context.get("reference"), "grounded": True}

        intent = detect_intent(question)

        # -- resolve which shipment is being discussed ---------------------
        record, wanted = None, None
        for candidate in data.references_in(question):
            found = data.find(candidate)
            if found is not None:
                record, wanted = found, candidate
                break
            wanted = wanted or candidate

        # A number that this run has never seen is refused outright.
        if record is None and wanted:
            if not data.shipments:
                return {
                    "answer": "No shipment has been processed in this run yet, so "
                              "I have nothing on {0}.".format(wanted),
                    "card": None, "reference": None, "grounded": True,
                }
            return {
                "answer": "I have no record of {0} in this run. I only know the "
                          "{1} shipment(s) this run has touched — I won't guess "
                          "at one it hasn't.".format(wanted, len(data.shipments)),
                "card": None, "reference": None, "grounded": True,
            }

        # "this shipment", "it", "that one" -> whatever we discussed last.
        if record is None and re.search(
            r"\b(this|that|it|its|the)\s+(shipment|one|awb|bol)\b|\bit\b|\bits\b",
            question, re.I
        ):
            if context.get("reference"):
                record = data.find(context["reference"])
            if record is None and data.in_flight:
                record = data.in_flight

        # -- a re-run is a REQUEST, not a lookup ---------------------------
        if intent == "reprocess":
            reference = (record or {}).get("reference") or wanted
            if not reference:
                return {
                    "answer": "Which shipment should I re-run? Give me the BOL or "
                              "AWB number.",
                    "card": None, "reference": context.get("reference"),
                    "grounded": True,
                }
            return {
                "answer": None,          # filled in by the caller after the request
                "card": None, "reference": reference, "grounded": True,
                "request": {"action": "reprocess", "reference": reference},
            }

        # -- shipment-scoped answers ---------------------------------------
        if record is not None:
            reference = record.get("reference")

            if intent == "eta":
                if _has(record.get("provider_eta")):
                    text = "**{0}**: carrier ETA is **{1}**{2}.".format(
                        reference, record["provider_eta"],
                        ", ATA {0}".format(record["provider_ata"])
                        if _has(record.get("provider_ata")) else "")
                else:
                    text = "**{0}** has no carrier ETA. {1} returned {2}.".format(
                        reference, provider_name(record.get("provider")),
                        "no dated entry" if not _has(record.get("provider_status"))
                        else "status '{0}' with no estimated date".format(
                            record["provider_status"]))
                return {"answer": text, "card": shipment_card(data, record),
                        "reference": reference, "grounded": True}

            if intent == "carrier":
                return {
                    "answer": "**{0}** is on **{1}**, tracked via {2}.".format(
                        reference, _show(record.get("carrier")),
                        provider_name(record.get("provider"))),
                    "card": shipment_card(data, record),
                    "reference": reference, "grounded": True,
                }

            if record.get("state") == "waiting_for_human" and intent in (
                    None, "why", "shipment", "human"):
                return {"answer": _answer_human(data), "card": shipment_card(data, record),
                        "reference": reference, "grounded": True}

            if intent == "why":
                if record.get("state") in ("failed", "skipped", "human_timeout", "partial"):
                    text = _explain(data, record) + "\n\nStep reached: {0}".format(
                        _show(record.get("step")))
                else:
                    text = "**{0}** didn't fail — it's {1}.".format(
                        reference, STATE_WORDS.get(record.get("state"), "unknown"))
                return {"answer": text, "card": shipment_card(data, record),
                        "reference": reference, "grounded": True}

            if intent == "latest_event":
                return {"answer": _answer_latest_event(data, record),
                        "card": shipment_card(data, record),
                        "reference": reference, "grounded": True}

            summary = "**{0}** is {1}.".format(
                reference, STATE_WORDS.get(record.get("state"), "in an unknown state"))
            if record.get("state") in ("failed", "skipped") and _has(record.get("error")):
                summary += " " + record["error"]
            summary += ("\n\nThe automation never reads origin, destination or "
                        "route, so those aren't available.")
            return {"answer": summary, "card": shipment_card(data, record),
                    "reference": reference, "grounded": True}

        # -- run-scoped answers --------------------------------------------
        handlers = {
            "code_request": lambda: _answer_code_request(data),
            "attention": lambda: _answer_attention(data),
            "human": lambda: _answer_human(data),
            "latest_failure": lambda: _answer_latest_failure(data),
            "summary": lambda: _answer_summary(data),
            "changed": lambda: _answer_changed(data),
            "greeting": lambda: _answer_greeting(data),
            "thanks": lambda: _answer_thanks(data),
            "identity": lambda: _answer_identity(data),
            "capabilities": lambda: _answer_capabilities(data),
            "download": lambda: _answer_download(data),
            "slowest": lambda: _answer_slowest(data),
            "fastest": lambda: _answer_fastest(data),
            "average": lambda: _answer_average(data),
            "compare": lambda: _answer_compare(data),
            "duration": lambda: _answer_duration(data),
            "health": lambda: _answer_health(data),
            "history": lambda: _answer_history(data),
            "report": lambda: _answer_report(data),
            "all_of": lambda: _answer_all_of(data, question),
            "help": lambda: _help(data),
            "run": lambda: _answer_run(data),
            "under_clearance": lambda: _answer_under_clearance(data),
            "worst_carrier": lambda: _answer_worst_carrier(data),
            "failed": lambda: _answer_failed(data),
            "skipped": lambda: _answer_skipped(data),
            "processing": lambda: _answer_processing(data),
            "no_eta": lambda: _answer_no_eta(data),
            "counts": lambda: _answer_counts(data),
            "system": lambda: _answer_system(data, question),
            "latest_event": lambda: _answer_latest_event(data),
            "logs": lambda: _answer_logs(data),
            # Telemetry-backed. Each returns a counted answer or an explicit
            # "there is nothing measured to answer that from".
            "carrier_timing": lambda: _from_telemetry(
                "carrier_timing", lambda: insights.carrier_timing(),
                "how the carriers compare"),
            "strategy_perf": lambda: _from_telemetry(
                "strategy_performance", lambda: insights.strategy_performance(),
                "which strategies work"),
            "failure_cats": lambda: _from_telemetry(
                "failure_categories", lambda: insights.failure_categories(),
                "what goes wrong"),
            "verification": lambda: _from_telemetry(
                "verification", lambda: insights.verification_summary(),
                "whether writes were verified"),
            "ml_recs": lambda: _from_telemetry(
                "ml_recommendations", lambda: insights.ml_recommendations(),
                "what the model recommended"),
        }

        if intent in handlers:
            produced = handlers[intent]()
            # The telemetry handlers return (text, sources) so the answer can
            # say what it was built from; the run-scoped ones return text.
            if isinstance(produced, tuple):
                text, sources = produced
            else:
                text, sources = produced, ["bridge.snapshot"]
            return {"answer": text, "card": None,
                    "reference": context.get("reference"), "grounded": True,
                    "sources": sources, "intent": intent}

        wanted_filter = _filter_of(data, question) if intent in (None, "system") else {}
        if wanted_filter and (intent is None or IMPERATIVE.search(question)):
            return {"answer": _answer_filtered(data, wanted_filter), "card": None,
                    "reference": context.get("reference"), "grounded": True,
                    "intent": "filter"}

        if intent in ("eta", "carrier", "why", "shipment"):
            return {
                "answer": "I need a shipment reference for that — give me the BOL "
                          "or AWB, or pick one from the Shipments page. This run "
                          "has touched {0} shipment(s).".format(len(data.shipments)),
                "card": None, "reference": None, "grounded": True,
            }

        # Rather than shrugging, say where the run stands and point at the
        # nearest useful thing.
        counters = data.counters
        if data.shipments:
            summary = (
                "I don't have that information in this run — I answer only from "
                "its data. Here is where the run stands:\n\n{0} processed — {1} written to the Hub, {2} skipped, "
                "{3} failed. The run looks {4}.\n\nTry a shipment number, "
                "\u201cwhich shipments failed\u201d, \u201ccompare the "
                "carriers\u201d, or \u201cdownload the data\u201d."
            ).format(counters.get("processed", 0), counters.get("successful", 0),
                     counters.get("skipped", 0), counters.get("failed", 0),
                     data.health_summary())
        else:
            summary = ("I don't have that information in this run, and no shipments "
                       "have been processed yet. Once a run starts I can answer "
                       "about any of them.")
        return {"answer": summary, "card": None,
                "reference": context.get("reference"), "grounded": True}

    except Exception as error:
        return {
            "answer": "The assistant hit an internal error and would rather say "
                      "so than guess: {0}".format(error),
            "card": None, "reference": None, "grounded": True,
        }
