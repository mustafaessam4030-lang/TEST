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

TEMPLATES = {
    "shipment_delay": {"title": "Shipment delay", "focus": "delay", "tasks": _CORE},
    "clearance_blockage": {"title": "Clearance blockage", "focus": "customs", "tasks": _CORE},
    "missing_ata": {"title": "Missing or contradictory ATA", "focus": "ata", "tasks": _CORE},
    "carrier_failure": {"title": "Carrier tracking failure", "focus": "carrier", "tasks": _CORE},
    "data_inconsistency": {"title": "Data inconsistency", "focus": "data", "tasks": _CORE},
    "backlog": {"title": "Past-due backlog", "focus": "backlog", "tasks": _CORE},
}

ACTION_CLASSES = ("READ_ONLY", "PREPARATION", "APPROVAL_REQUIRED", "RESTRICTED")
