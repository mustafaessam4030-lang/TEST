"""
Is this a mission? — and if so, the proposal ATLAS shows before anything starts.

    assess(question, records, allowed=None) -> {level, severity, reasons, template, scope, signals, fingerprint}
    proposal(assessment, profile)           -> the plan, specialists, models, websites, estimate, limits
    declined(fingerprint)                   -> True when the user said no to this task in the last 24 h

Levels: NORMAL (ATLAS as today), ADVANCED, DEEP_MISSION, CRITICAL_INCIDENT.
Severity (Emergency Mode): NORMAL, HIGH, CRITICAL, SEVERE.

Complexity alone never makes a request CRITICAL: that needs a business-impact
signal read from the records (days past due, shipments at customs, a BU's
past-due count). The thresholds are configuration (ATLAS_MISSION_RULES, JSON).
Nothing here calls a model; it runs in microseconds next to the chat.
"""
import hashlib
import json
import os
import re
import time

from . import db, models
from .registry import SPECIALISTS, TEMPLATES

RULES = {
    "deep_overdue_days": 14,        # one shipment this far past its step's due date: worth a deep look
    "critical_overdue_days": 30,    # ...and this far, with customs or urgency: critical
    "critical_bu_pastdue": 150,     # a BU with this many open past-due shipments
    "severe_bu_pastdue": 400,
    "decline_hours": 24,
}


def rules():
    r = dict(RULES)
    raw = os.environ.get("ATLAS_MISSION_RULES")
    if raw:
        try:
            r.update({k: v for k, v in json.loads(raw).items() if k in RULES})
        except ValueError:
            pass
    return r


INVESTIGATE = re.compile(r"\b(why|investigat\w*|root cause|analy[sz]\w*|find out|what happened|what went wrong|"
                         r"cross[- ]?check|verify|compare|diagnos\w*|explain why|dig into|look into|figure out|"
                         r"what is (?:wrong|blocking)|stuck|delay\w*|held|blocked|late)\b", re.I)
URGENT = re.compile(r"\b(urgent\w*|asap|critical|emergency|immediately|escalat\w*|right now|today)\b", re.I)
BROAD = re.compile(r"\b(all|every|each|whole|across|backlog|pattern|trend|most|worst)\b", re.I)
CROSS = re.compile(r"\b(carrier|ehub|hub|export|run|website|portal|source|sources|record|records)\b", re.I)
LOOKUP = re.compile(r"^\s*(what(?:'s| is) the (?:status|eta|ata)|status of|eta of|ata of|where is)\b", re.I)

TEMPLATE_WORDS = [
    ("missing_ata", re.compile(r"\b(no ata|missing ata|ata (?:is )?missing|ata (?:differ|mismatch|wrong|contradict)\w*|contradict\w* ata)\b", re.I)),
    ("clearance_blockage", re.compile(r"\b(customs?|clearance|declaration|duty|release|held|blocked)\b", re.I)),
    ("carrier_failure", re.compile(r"\b(carrier|tracking (?:fail|broke|down)|airline|shipping line|website down|portal)\b", re.I)),
    ("data_inconsistency", re.compile(r"\b(inconsisten\w*|mismatch\w*|contradict\w*|differen\w*|wrong data|doesn'?t match)\b", re.I)),
    ("backlog", re.compile(r"\b(backlog|past due|overdue|all|every|pattern|across)\b", re.I)),
]


def _bus_in(question, rows):
    codes = {r.get("bu") for r in rows if r.get("bu")}
    found = set(w for w in re.findall(r"\b[A-Z]{2,3}\b", question or "") if w in codes)
    try:
        from dashboard import clearance as C
        low = (question or "").lower()
        for bu, (_, name) in C.BU_COUNTRY.items():
            if bu in codes and re.search(r"\b" + re.escape(name.lower()) + r"\b", low):
                found.add(bu)
    except Exception:
        pass
    return sorted(found)


def _refs_in(question, data):
    try:
        from dashboard import clearance as C
        return C.awbs_in(question, data)[:12]
    except Exception:
        return []


def assess(question, data=None, rows=None, allowed=None):
    """The level, its reasons in words, the template and the scope. `rows` = export rows this person may see."""
    q = (question or "").strip()
    r = rules()
    rows = rows if rows is not None else []
    if allowed is not None:
        rows = [x for x in rows if x.get("bu") in allowed]
    refs = [x for x in _refs_in(q, data) if any(row.get("awb") == x for row in rows)] if data else []
    bus = _bus_in(q, rows)
    inv, urgent, broad, cross = bool(INVESTIGATE.search(q)), bool(URGENT.search(q)), bool(BROAD.search(q)), len(set(m.lower() for m in CROSS.findall(q)))
    scoped = [x for x in rows if x.get("awb") in refs] if refs else ([x for x in rows if x.get("bu") in bus] if bus else [])
    open_ = [x for x in scoped if x.get("step") != "Completed"]
    pastdue = [x for x in open_ if (x.get("overdue") or 0) > 0]
    max_over = max([x.get("overdue") or 0 for x in scoped] or [0])
    at_customs = [x for x in open_ if str(x.get("step") or "").startswith(("Customs", "Duty"))]
    reasons, signals = [], {"refs": len(refs), "bus": len(bus), "investigate": inv, "urgent": urgent, "broad": broad,
                            "systems": cross, "past_due": len(pastdue), "max_overdue_days": max_over, "at_customs": len(at_customs)}
    score = 0
    if inv:
        score += 2; reasons.append("it asks why or to investigate, not just to look something up")
    if len(refs) > 1:
        score += 2; reasons.append("it names {0} shipments".format(len(refs)))
    if bus and not refs:
        score += 2; reasons.append("it covers a whole business unit ({0})".format(", ".join(bus)))
    if broad:
        score += 1; reasons.append("it asks about a pattern across many shipments")
    if cross >= 2:
        score += 1; reasons.append("it needs more than one source checked against another")
    if max_over >= r["deep_overdue_days"]:
        score += 2; reasons.append("a shipment in scope is {0:,.0f} days past its step's due date".format(max_over))
    if urgent:
        score += 1; reasons.append("it is marked urgent")
    if LOOKUP.search(q) and not inv:
        score = min(score, 1)
    level = "NORMAL"
    if score >= 2 and (refs or bus):
        level = "ADVANCED"
    if score >= 4 and (refs or bus):
        level = "DEEP_MISSION"
    # impact, from the records only
    severity = "NORMAL"
    bu_pd = len(pastdue) if (bus and not refs) else 0
    if level != "NORMAL" and (max_over >= r["deep_overdue_days"] or bu_pd >= r["critical_bu_pastdue"] // 3):
        severity = "HIGH"
    critical_impact = (max_over >= r["critical_overdue_days"] and (at_customs or urgent)) or bu_pd >= r["critical_bu_pastdue"]
    if level != "NORMAL" and critical_impact:
        severity = "CRITICAL"
        if bu_pd >= r["severe_bu_pastdue"]:
            severity = "SEVERE"
        if urgent or bu_pd >= r["critical_bu_pastdue"]:
            level = "CRITICAL_INCIDENT"
            reasons.append("the records show critical business impact ({0})".format(
                "{0:,} open past-due shipments".format(bu_pd) if bu_pd else "{0:,.0f} days past due at customs".format(max_over)))
    template = "shipment_delay"
    for name, rx in TEMPLATE_WORDS:
        if rx.search(q):
            template = name
            break
    if bus and not refs and template in ("shipment_delay",):
        template = "backlog"
    if not refs and not bus and level != "NORMAL":
        level = "NORMAL"                     # nothing in the records to investigate: ATLAS answers as today
    fp = hashlib.sha1(json.dumps([template, sorted(refs), sorted(bus), bool(inv)]).encode()).hexdigest()[:16]
    return {"level": level, "severity": severity, "reasons": reasons, "template": template,
            "scope": {"refs": refs, "bus": bus, "allowed": sorted(allowed) if allowed is not None else None},
            "signals": signals, "fingerprint": fp, "question": q[:500]}


ESTIMATE = {"ADVANCED": (2, 4, 8000, 15000), "DEEP_MISSION": (3, 6, 15000, 40000),
            "CRITICAL_INCIDENT": (4, 8, 25000, 60000)}
APPROVER = {"ADVANCED": "missions.run", "DEEP_MISSION": "missions.run", "CRITICAL_INCIDENT": "missions.emergency"}


def proposal(a, profile=None):
    """What would run, shown before anything does."""
    level = a["level"]
    profile = profile or ("emergency" if level == "CRITICAL_INCIDENT" else "deep")
    t = TEMPLATES[a["template"]]
    plan, models_used = [], {}
    for tid, spec, objective, deps in t["tasks"]:
        need = SPECIALISTS[spec]["needs"]
        if need in ("records", "knowledge", "checks"):
            who = "ATLAS's own code" if need != "knowledge" else "ATLAS's reviewed knowledge"
        else:
            m, why = models.route(need, "T1", profile)
            who = m["id"] if m else "no model available: ATLAS's own code instead"
            models_used[who] = models_used.get(who, 0) + 1
        plan.append({"id": tid, "specialist": SPECIALISTS[spec]["title"], "objective": objective, "depends_on": deps, "by": who})
    lo_c, hi_c, lo_t, hi_t = ESTIMATE.get(level, (0, 0, 0, 0))
    billed = [k for k in models_used if not k.startswith(("local", "no model", "ATLAS"))]
    limits = ["Missions read records; they do not open carrier websites or change anything.",
              "Every number is recomputed from the source before the report.",
              "A proposed action still goes through the existing approval and the Human Action queue."]
    if not billed:
        limits.append("No Cortex model is available now: the report is worded by ATLAS's own code.")
    else:
        limits.append("Claude runs in Snowflake (cross-region: AWS US). Prompts carry shipment references and statuses, never contacts or secrets.")
    return {"level": level, "severity": a["severity"], "template": a["template"], "title": t["title"],
            "reasons": a["reasons"], "scope": a["scope"], "profile": profile, "profile_label": models.PROFILES[profile]["label"],
            "plan": plan, "specialists": sorted({p["specialist"] for p in plan}), "models": models_used,
            "websites": ["eHub export (as loaded)", "ATA run records"],
            # (keys never contain "token": the store drops such fields as possible secrets)
            "estimate": {"model_calls": [lo_c, hi_c] if billed else [0, 0], "usage": [lo_t, hi_t] if billed else [0, 0],
                         "budget": 80000 if level != "CRITICAL_INCIDENT" else 150000},
            "limits": limits, "approver": APPROVER.get(level, "missions.run")}


def decline(fingerprint, by=None):
    db.insert("declines", fingerprint=fingerprint, at=time.time(), by=str(by or "")[:80])


def declined(fingerprint):
    row = db.one("SELECT at FROM declines WHERE fingerprint=?", fingerprint)
    return bool(row and time.time() - row["at"] < rules()["decline_hours"] * 3600)
