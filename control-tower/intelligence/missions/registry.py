"""
What a mission can use: specialists, websites and mission templates.

A specialist is configuration (a purpose, the tools and data it may use, the
model capability it needs, what it must return), implemented by one function
in specialists.py. A new role is a new entry plus its function; the engine
does not change. Only the roles a mission's plan needs are started.

The website registry says what each carrier integration can do *for a
mission*. In this phase missions do not open carrier websites: live lookups
stay with the ATA run (its own pacing, sessions and Human Actions for
CAPTCHA / sign-in). A mission reads what the run recorded. Each site keeps
its own status words; they are never assumed to mean the same thing.

Templates are the preferred plan: a known task graph for a known kind of
investigation. A model never invents a specialist, a tool or a site.
"""

SPECIALISTS = {
    "shipment":   {"title": "Shipment Intelligence", "purpose": "The shipment's own record: export row, run record, step, dates, days past due",
                   "needs": "records", "returns": "facts"},
    "analyst":    {"title": "Data Analyst", "purpose": "The same step, BU, clearing agent and forwarder across the export: is this one shipment or a pattern?",
                   "needs": "records", "returns": "facts"},
    "logistics":  {"title": "Logistics Intelligence", "purpose": "What the current step means and who owns it, from ATLAS's reviewed knowledge",
                   "needs": "knowledge", "returns": "knowledge"},
    "carrier":    {"title": "Carrier Intelligence", "purpose": "What the carrier's record and the carrier's recent health say",
                   "needs": "records", "returns": "facts"},
    "risk":       {"title": "Risk Analyst", "purpose": "Exposure: days past due, cycle against target, escalation already raised or not",
                   "needs": "records", "returns": "facts"},
    "hypotheses": {"title": "Mission Commander: hypotheses", "purpose": "Possible causes, each with the evidence that supports or rejects it",
                   "needs": "reason", "returns": "hypotheses"},
    "verify":     {"title": "Independent Verification", "purpose": "Recompute every number from the source, check freshness, find contradictions",
                   "needs": "checks", "returns": "gate"},
    "review":     {"title": "Independent Review", "purpose": "A different model family reads the draft conclusion and challenges it",
                   "needs": "review", "returns": "review"},
    "actions":    {"title": "Automation Engineer", "purpose": "Safe next steps, each classed READ ONLY / PREPARATION / APPROVAL REQUIRED; nothing is executed",
                   "needs": "records", "returns": "actions"},
    "item":       {"title": "Shipment Intelligence (one of many)", "purpose": "One shipment's record and exposure, in a fan-out over many",
                   "needs": "records", "returns": "facts"},
    "merge":      {"title": "Data Analyst: merge", "purpose": "What the shipments examined have in common: step, agent, exposure",
                   "needs": "records", "returns": "facts"},
    "carriers":   {"title": "Carrier Performance Analyst", "purpose": "Each carrier's recorded outcomes: verified updates, failures, sample size",
                   "needs": "records", "returns": "facts"},
    "eta":        {"title": "Date Reconciliation", "purpose": "The export's ETA / ATA against the carrier's, as the run read them",
                   "needs": "records", "returns": "facts"},
    "failure":    {"title": "Automation Failure Analyst", "purpose": "Why the run's lookup failed (VERIFIED / INFERRED / UNKNOWN) and its history",
                   "needs": "records", "returns": "facts"},
    "validate":   {"title": "Validation", "purpose": "The automation's own date rules, run again on the run's record",
                   "needs": "checks", "returns": "facts"},
    "web":        {"title": "Web Investigation", "purpose": "Official public sources (self-hosted search); never tracking pages, never a login",
                   "needs": "records", "returns": "external"},
    "report":     {"title": "Report Generator", "purpose": "One evidence-backed report; the wording is checked against the evidence",
                   "needs": "word", "returns": "report"},
}

WEBSITES = [
    # site, operations a mission may use, how
    {"id": "ehub", "title": "eHub (the Hub)", "ops": ["read the shipment export"], "auth": "export file", "live": False,
     "how": "The loaded eHub export (Clearance); data as of its date", "human": False},
    {"id": "ata_run", "title": "ATA run records", "ops": ["read carrier status, ETA, ATA, outcome"], "auth": "none", "live": False,
     "how": "What the ATA run recorded; live lookups stay with the run", "human": False},
    {"id": "afkl", "title": "Air France KLM Cargo", "ops": [], "auth": "carrier site", "live": False,
     "how": "Through the ATA run only (navigation ladder, pacing)", "human": True, "semantics": "AFKL milestones (RCS, DEP, ARR, NFD, DLV)"},
    {"id": "qatar", "title": "Qatar Airways Cargo", "ops": [], "auth": "carrier site", "live": False, "how": "Through the ATA run only", "human": True},
    {"id": "dhl", "title": "DHL Aviation", "ops": [], "auth": "API / site", "live": False, "how": "Through the ATA run only", "human": False},
    {"id": "ocean", "title": "Ocean carriers (MSC, Maersk, CMA CGM, Hapag, COSCO, ONE, Grimaldi)", "ops": [], "auth": "carrier sites", "live": False,
     "how": "Through the ATA run only; CMA CGM automation hold stays on", "human": True, "semantics": "Each line's own event names"},
]

# A task graph per kind of investigation: (id, specialist, objective, depends_on)
_CORE = [
    ("facts", "shipment", "Read the shipment records in scope", []),
    ("pattern", "analyst", "Compare with the same step, BU, agent and forwarder", ["facts"]),
    ("step", "logistics", "Explain the step and who owns it", ["facts"]),
    ("carrier", "carrier", "Read the carrier's record and recent health", ["facts"]),
    ("risk", "risk", "Measure the exposure", ["facts", "pattern"]),
    ("hyp", "hypotheses", "Rank possible causes against the evidence", ["facts", "pattern", "step", "carrier", "risk"]),
    ("verify", "verify", "Recompute, check freshness and contradictions", ["hyp"]),
    ("review", "review", "Independent review of the draft conclusion", ["verify"]),
    ("actions", "actions", "Prepare safe next steps", ["verify"]),
    ("report", "report", "Write the evidence-backed report", ["review", "actions"]),
]

_FAN = [
    ("items", "item", "Read each shipment and its exposure (one task per shipment, up to 25)", []),
    ("merge", "merge", "Merge: what the shipments have in common", ["items"]),
    ("pattern", "analyst", "Compare with the whole BU: step, agent, forwarder", []),
    ("step", "logistics", "Explain the steps and who owns them", ["merge"]),
    ("risk", "risk", "Measure the exposure", ["merge"]),
    ("hyp", "hypotheses", "Rank common causes against the evidence", ["merge", "pattern", "step", "risk"]),
    ("verify", "verify", "Recompute every count, check freshness and contradictions", ["hyp"]),
    ("review", "review", "Independent review of the draft conclusion", ["verify"]),
    ("actions", "actions", "Prepare safe next steps per shipment", ["verify"]),
    ("report", "report", "Write the evidence-backed report with the share per cause", ["review", "actions"]),
]

_ETA = [
    ("facts", "shipment", "Read the shipment records in scope", []),
    ("eta", "eta", "Compare the export's dates with the carrier's", ["facts"]),
    ("carrier", "carrier", "Read the carrier's record and recent health", ["facts"]),
    ("hyp", "hypotheses", "Rank possible causes: tracking sync, mapping, extraction", ["facts", "eta", "carrier"]),
    ("verify", "verify", "Recompute, check freshness and contradictions", ["hyp"]),
    ("review", "review", "Independent review of the draft conclusion", ["verify"]),
    ("actions", "actions", "Prepare safe next steps (a re-run needs approval)", ["verify"]),
    ("report", "report", "Write the evidence-backed report", ["review", "actions"]),
]

_FAIL = [
    ("facts", "shipment", "Read the shipment records in scope", []),
    ("failure", "failure", "Classify the run's failure and read its history", ["facts"]),
    ("carrier", "carrier", "Read the carrier's record and recent health", ["facts"]),
    ("hyp", "hypotheses", "Rank possible causes against the evidence", ["facts", "failure", "carrier"]),
    ("verify", "verify", "Recompute, check freshness and contradictions", ["hyp"]),
    ("actions", "actions", "Propose the next safe strategy (a re-run needs approval)", ["verify"]),
    ("report", "report", "Write the remediation report", ["actions"]),
]

_EXC = [
    ("facts", "shipment", "Read the shipment records in scope", []),
    ("validate", "validate", "Run the automation's own date rules on the run's record", ["facts"]),
    ("step", "logistics", "Explain the step and who owns it", ["facts"]),
    ("verify", "verify", "Recompute every fact from the source", ["validate", "step"]),
    ("report", "report", "Write the exception report", ["verify"]),
]

_CMP = [
    ("carriers", "carriers", "Compare carriers' recorded outcomes (minimum sample rule)", []),
    ("verify", "verify", "Recount every rate from the run's records", ["carriers"]),
    ("report", "report", "Write the comparison", ["verify"]),
]

_WEB = [
    ("web", "web", "Search official public sources for the carrier's tracking workflow", []),
    ("verify", "verify", "Grade what was found: external, unverified", ["web"]),
    ("report", "report", "Write a proposed integration brief for engineering", ["verify"]),
]

# Tasks a person may not remove from a plan: without them there is no checked report.
REQUIRED = ("facts", "items", "verify", "report")

TEMPLATES = {
    "shipment_delay": {"title": "Shipment delay", "focus": "delay", "tasks": _CORE},
    "clearance_blockage": {"title": "Clearance blockage", "focus": "customs", "tasks": _CORE},
    "missing_ata": {"title": "Missing or contradictory ATA", "focus": "ata", "tasks": _CORE},
    "carrier_failure": {"title": "Carrier tracking failure", "focus": "carrier", "tasks": _CORE},
    "data_inconsistency": {"title": "Data inconsistency", "focus": "data", "tasks": _CORE},
    "backlog": {"title": "Past-due backlog", "focus": "backlog", "tasks": _CORE},
    "multi_delay": {"title": "Many delayed shipments: common causes", "focus": "backlog", "tasks": _FAN,
                    "fanout": {"task": "items", "max": 25}},
    "eta_discrepancy": {"title": "ETA / ATA discrepancy: carrier vs Hub", "focus": "ata", "tasks": _ETA},
    "ata_failure": {"title": "Failed ATA automation: remediation", "focus": "automation", "tasks": _FAIL},
    "exception_report": {"title": "Shipment exception report", "focus": "exceptions", "tasks": _EXC, "main": "validate"},
    "carrier_compare": {"title": "Carrier performance comparison", "focus": "carrier", "tasks": _CMP, "main": "carriers",
                        "scope_free": True},
    "carrier_research": {"title": "Unfamiliar carrier: tracking workflow research", "focus": "web", "tasks": _WEB, "main": "web",
                         "scope_free": True},
}

ACTION_CLASSES = ("READ_ONLY", "PREPARATION", "APPROVAL_REQUIRED", "RESTRICTED")
