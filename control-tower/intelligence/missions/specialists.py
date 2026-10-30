"""
The specialists: one function each, ctx in, findings out.

    fn(ctx) -> {"evidence": [claim...], "out": {...}, "summary": str, "model": id | None}

A claim: {claim, class, source, record, freshness, identifiers, data, verification, basis}
Classes: VERIFIED_LIVE · HISTORICAL · CALCULATED · GENERAL_KNOWLEDGE · PLATFORM_RULE · HYPOTHESIS · EXTERNAL_UNVERIFIED

Facts come from the records (the loaded eHub export, the ATA run, the
escalations, carrier health); knowledge from ATLAS's reviewed knowledge base.
A model only suggests hypotheses, reviews a draft and words the report; its
words are checked against the evidence (factguard) and a hypothesis it
suggests stays "open — not tested" until evidence decides it.
"""
import json
import re
import statistics
import time

from . import models

WITH_BU = ("Submit to BU", "Assign Clearing Agent", "Correction Required")
AT_CUSTOMS = ("Customs Pre Entry", "Customs Declaration", "Duty Assessment", "Duty Payment", "Customs Release")
AWAITING = ("Awaiting Reciept", "Awaiting Receipt")
# Who owns each step: the Clearance page's own stage owners (a platform rule, not a model's guess)
OWNER = {"Submit to BU": "COE", "Assign Clearing Agent": "BU", "Customs Pre Entry": "Clearing agent",
         "Customs Declaration": "Clearing agent", "Duty Assessment": "Clearing agent", "Duty Payment": "Clearing agent / BU",
         "Customs Release": "Clearing agent", "Awaiting Reciept": "BU", "Awaiting Receipt": "BU", "Correction Required": "BU"}


def step_name(s):
    return "Awaiting Receipt" if s == "Awaiting Reciept" else (s or "unknown step")


def _days(a, b):
    try:
        from datetime import date
        da, dbb = date.fromisoformat(str(a)[:10]), date.fromisoformat(str(b)[:10])
        return (da - dbb).days
    except (TypeError, ValueError):
        return None


def claim(text, cls, source, record=None, data=None, identifiers=None, freshness=None, basis="read from the record"):
    return {"claim": text, "class": cls, "source": source, "record": record, "data": data or {},
            "identifiers": identifiers or [], "freshness": freshness, "basis": basis, "verification": "unverified"}


def _src(ctx):
    return "eHub export, data as of {0}".format(ctx.asof or "unknown date")


# ── Shipment Intelligence ────────────────────────────────────────────────
def shipment(ctx):
    rows = ctx.focus_rows()
    ev = []
    if not rows:
        ev.append(claim("No shipment in the loaded export matches the mission's scope.", "HISTORICAL", _src(ctx),
                        basis="searched the export"))
        return {"evidence": ev, "out": {"rows": []}, "summary": "No matching shipment in the export"}
    for r in rows:
        awb, ids = r["awb"], [r["awb"]]
        ev.append(claim("{0} ({1}, {2}) is at step {3}; eHub status {4}.".format(awb, r.get("bu"), r.get("mode"), step_name(r.get("step")), r.get("status") or "not given"),
                        "HISTORICAL", _src(ctx), awb, {"awb": awb, "step": r.get("step"), "status": r.get("status"), "bu": r.get("bu")}, ids, ctx.asof))
        if r.get("overdue") is not None:
            ev.append(claim("{0} is {1:,.0f} days past its step's due date ({2}).".format(awb, r["overdue"], r.get("due") or "no due date")
                            if r["overdue"] > 0 else "{0} is within its step's due date ({1}).".format(awb, r.get("due") or "no due date"),
                            "HISTORICAL", _src(ctx), awb, {"awb": awb, "overdue": r["overdue"], "due": r.get("due")}, ids, ctx.asof))
        ev.append(claim("{0}: ETA {1}; ATA {2}.".format(awb, r.get("eta") or "not given", r.get("ata") or "not recorded"),
                        "HISTORICAL", _src(ctx), awb, {"awb": awb, "eta": r.get("eta"), "ata": r.get("ata")}, ids, ctx.asof))
        ev.append(claim("{0}: clearing agent {1}; forwarder {2}.".format(awb, r.get("agent") or "not assigned", r.get("ffw") or "not given"),
                        "HISTORICAL", _src(ctx), awb, {"awb": awb, "agent": r.get("agent"), "ffw": r.get("ffw")}, ids, ctx.asof))
        rec = ctx.run_record(awb)
        if rec:
            verified = str(rec.get("verification") or rec.get("outcome") or "").upper() in ("REAL VERIFIED", "VERIFIED", "SUCCESS") and rec.get("verified", True)
            ev.append(claim("ATA run: {0} reported status {1}, ETA {2}, ATA {3} (outcome {4}).".format(
                rec.get("carrier") or "the carrier", rec.get("provider_status") or "none", rec.get("provider_eta") or "none",
                rec.get("provider_ata") or "none", rec.get("outcome") or rec.get("state") or "not finished"),
                "VERIFIED_LIVE" if verified else "HISTORICAL", ctx.run_source(), awb,
                {"awb": awb, "carrier": rec.get("carrier"), "provider_ata": rec.get("provider_ata"), "provider_eta": rec.get("provider_eta"),
                 "outcome": rec.get("outcome"), "error": rec.get("error")}, ids, ctx.run_when(),
                "read back from the Hub" if verified else "recorded by the run"))
    return {"evidence": ev, "out": {"rows": [ctx.slim(r) for r in rows]},
            "summary": "{0} shipment{1} read".format(len(rows), "" if len(rows) == 1 else "s")}


# ── Data Analyst ─────────────────────────────────────────────────────────
def analyst(ctx):
    rows, allr = ctx.focus_rows(), ctx.rows
    ev, out = [], {"peers": {}}
    openpd = [r for r in allr if r.get("step") != "Completed" and (r.get("overdue") or 0) > 0]
    if ctx.mission["template"] == "backlog" or not ctx.scope.get("refs"):
        bus = ctx.scope.get("bus") or sorted({r.get("bu") for r in rows})
        mine = [r for r in openpd if r.get("bu") in bus]
        by_step, by_agent = {}, {}
        for r in mine:
            by_step[r.get("step")] = by_step.get(r.get("step"), 0) + 1
            if r.get("agent"):
                by_agent[r["agent"]] = by_agent.get(r["agent"], 0) + 1
        ev.append(claim("{0} has {1:,} open shipments past their step's due date.".format(", ".join(bus), len(mine)), "HISTORICAL", _src(ctx),
                        data={"filter": {"bus": bus, "open": True, "past_due": True}, "count": len(mine)}, freshness=ctx.asof, basis="counted in the export"))
        for step, n in sorted(by_step.items(), key=lambda x: -x[1])[:4]:
            ev.append(claim("{0:,} of them are at {1}.".format(n, step_name(step)), "HISTORICAL", _src(ctx),
                            data={"filter": {"bus": bus, "open": True, "past_due": True, "step": step}, "count": n}, freshness=ctx.asof, basis="counted in the export"))
        for agent, n in sorted(by_agent.items(), key=lambda x: -x[1])[:3]:
            ev.append(claim("{0:,} of them are with clearing agent {1}.".format(n, agent), "HISTORICAL", _src(ctx),
                            data={"filter": {"bus": bus, "open": True, "past_due": True, "agent": agent}, "count": n}, freshness=ctx.asof, basis="counted in the export"))
        out["by_step"] = by_step
        out["backlog"] = len(mine)
        if mine:
            out["median_overdue"] = statistics.median([r["overdue"] for r in mine])
            ev.append(claim("Median days past due among them: {0:,.0f}.".format(out["median_overdue"]), "HISTORICAL", _src(ctx),
                            data={"filter": {"bus": bus, "open": True, "past_due": True}, "median": out["median_overdue"]}, freshness=ctx.asof, basis="computed from the export"))
    for r in rows[:6]:
        same = [x for x in openpd if x.get("bu") == r.get("bu") and x.get("step") == r.get("step") and x.get("awb") != r.get("awb")]
        agent = [x for x in openpd if r.get("agent") and x.get("agent") == r.get("agent") and x.get("awb") != r.get("awb")]
        out["peers"][r["awb"]] = {"same_step": len(same), "same_agent": len(agent)}
        ev.append(claim("{0:,} other open shipments in {1} are past due at {2}.".format(len(same), r.get("bu"), step_name(r.get("step"))),
                        "HISTORICAL", _src(ctx), r["awb"], {"filter": {"bus": [r.get("bu")], "open": True, "past_due": True, "step": r.get("step"), "exclude": r["awb"]}, "count": len(same)},
                        [r["awb"]], ctx.asof, "counted in the export"))
        if r.get("agent"):
            ev.append(claim("Clearing agent {0} has {1:,} other open past-due shipments.".format(r["agent"], len(agent)),
                            "HISTORICAL", _src(ctx), r["awb"], {"filter": {"open": True, "past_due": True, "agent": r["agent"], "exclude": r["awb"]}, "count": len(agent)},
                            [r["awb"]], ctx.asof, "counted in the export"))
    return {"evidence": ev, "out": out, "summary": "Pattern checked across {0:,} open past-due shipments".format(len(openpd))}


# ── Logistics Intelligence ───────────────────────────────────────────────
def logistics(ctx):
    steps = sorted({r.get("step") for r in ctx.focus_rows() if r.get("step")})
    ev = []
    for s in steps[:5]:
        if s in OWNER:
            ev.append(claim("{0} is owned by the {1} (the Clearance page's stage owners).".format(step_name(s), OWNER[s]),
                            "PLATFORM_RULE", "ATLAS platform: Clearance stages", data={"step": s, "owner": OWNER[s]}, basis="platform rule"))
        try:
            from .. import kb as KB
            hits = KB.kb().search(step_name(s), limit=1, approved_only=True)
            if hits:
                it = hits[0].get("item") or {}
                text = (it.get("summary") or it.get("definition") or it.get("text") or "").strip()
                if text:
                    ev.append(claim("{0}: {1}".format(it.get("term") or it.get("title") or step_name(s), text[:300]),
                                    "GENERAL_KNOWLEDGE", "ATLAS knowledge base ({0})".format(hits[0].get("id")), data={"kb": hits[0].get("id")},
                                    basis="reviewed knowledge"))
        except Exception:
            pass
    return {"evidence": ev, "out": {"steps": steps}, "summary": "{0} step{1} explained".format(len(steps), "" if len(steps) == 1 else "s")}


# ── Carrier Intelligence ─────────────────────────────────────────────────
def carrier(ctx):
    ev, carriers = [], set()
    rows = ctx.focus_rows()
    for r in rows[:8]:
        rec = ctx.run_record(r["awb"])
        if rec:
            carriers.add(rec.get("carrier"))
            if rec.get("error"):
                ev.append(claim("The ATA run's lookup of {0} ended with: {1}.".format(r["awb"], str(rec["error"])[:160]), "HISTORICAL", ctx.run_source(),
                                r["awb"], {"awb": r["awb"], "error": rec.get("error"), "outcome": rec.get("outcome")}, [r["awb"]], ctx.run_when(), "recorded by the run"))
    if not carriers:
        ev.append(claim("The ATA run has no record for the shipments in scope, so no carrier reading is available.", "HISTORICAL", ctx.run_source(),
                        data={"refs": [r["awb"] for r in rows[:8]]}, basis="searched the run's records"))
    try:
        from .. import carrier_health
        h = carrier_health.health()
        for c in h.get("carriers") or []:
            if c.get("label") in carriers or c.get("carrier") in carriers:
                ev.append(claim("{0}: {1} over the last {2} days.".format(c.get("label"), "insufficient data" if not c.get("sufficient") else
                                "{0:.0%} verified updates".format((c.get("rates") or {}).get("verified_updates") or 0), h.get("window_days")),
                                "HISTORICAL", "Carrier health (run outcomes)", data={"carrier": c.get("carrier")}, basis="computed from run outcomes"))
    except Exception:
        pass
    return {"evidence": ev, "out": {"carriers": sorted(c for c in carriers if c)}, "summary": "Carrier records read"}


# ── Risk Analyst ─────────────────────────────────────────────────────────
def risk(ctx):
    ev, grades = [], {}
    esc = ctx.escalations()
    for r in ctx.focus_rows()[:8]:
        od = r.get("overdue") or 0
        cust = r.get("step") in AT_CUSTOMS
        grade = "High" if od >= 30 or (cust and od >= 14) else "Medium" if od > 0 else "Low"
        grades[r["awb"]] = grade
        open_esc = [e for e in esc if e.get("awb") == r["awb"] and e.get("status") == "open"]
        ev.append(claim("{0}: exposure {1} ({2:,.0f} days past due{3}); {4}.".format(
            r["awb"], grade, od, ", at customs" if cust else "",
            "an escalation is already open" if open_esc else "no open escalation"), "HISTORICAL", _src(ctx), r["awb"],
            {"awb": r["awb"], "overdue": od, "grade": grade, "escalated": bool(open_esc)}, [r["awb"]], ctx.asof, "rule: 30+ days, or 14+ at customs = High"))
        if r.get("cycle") is not None and r.get("target"):
            ev.append(claim("{0}: {1:,.0f} actual days against a {2:,.0f}-day expected cycle.".format(r["awb"], r["cycle"], r["target"]),
                            "HISTORICAL", _src(ctx), r["awb"], {"awb": r["awb"], "cycle": r["cycle"], "target": r["target"]}, [r["awb"]], ctx.asof))
    return {"evidence": ev, "out": {"grades": grades}, "summary": "Exposure measured"}


# ── Hypotheses (the Commander) ────────────────────────────────────────────
TAXONOMY = [("customs", "Customs clearance delay"), ("agent", "Clearing agent not assigned, or assigned late"),
            ("docs", "Missing documents or a correction pending"), ("bu", "Waiting on the BU (submission or receipt)"),
            ("carrier", "Carrier delay (arrived later than its ETA)"), ("tracking", "Tracking out of sync (ATA missing or different)"),
            ("mapping", "Milestone mapping error (step and dates disagree)"), ("extract", "The run could not read the carrier"),
            ("pattern", "A wider pattern at the same step or agent"), ("unknown", "Cause not established")]


def hypotheses(ctx):
    rows = ctx.focus_rows()
    ev_ids = ctx.evidence_ids
    peers = (ctx.dep("pattern").get("out") or {}).get("peers", {})
    hyp = {k: {"id": k, "label": lbl, "status": "inconclusive", "why": [], "support": [], "against": [], "needed": [], "origin": "taxonomy"} for k, lbl in TAXONOMY}

    def mark(k, status, why, ids=(), against=False):
        h = hyp[k]
        if status == "supported" or h["status"] == "inconclusive" or (status == "rejected" and h["status"] != "supported"):
            h["status"] = status
        h["why"].append(why)
        (h["against"] if against else h["support"]).extend(ids)

    for r in rows[:25 if ctx.fanout() else 8]:
        awb, step, od = r["awb"], r.get("step"), r.get("overdue") or 0
        ids = ev_ids(awb)
        if step in AT_CUSTOMS:
            mark("customs", "supported" if od > 0 else "inconclusive", "{0} is at {1}, {2:,.0f} days past due".format(awb, step, od), ids)
        else:
            mark("customs", "rejected", "{0} is not at a customs step ({1})".format(awb, step_name(step)), ids, against=True)
        if step == "Assign Clearing Agent" or (not r.get("agent") and step in AT_CUSTOMS + AWAITING):
            mark("agent", "supported", "{0}: {1}".format(awb, "waiting for a clearing agent" if step == "Assign Clearing Agent" else "no clearing agent on record past assignment"), ids)
        elif r.get("agent"):
            mark("agent", "rejected", "{0} has clearing agent {1}".format(awb, r["agent"]), ids, against=True)
        if step == "Correction Required" or "correction" in str(r.get("status") or "").lower():
            mark("docs", "supported", "{0} is marked for correction in eHub".format(awb), ids)
        else:
            hyp["docs"]["needed"].append("the shipment's documents (not in the records ATLAS holds)")
        if step in ("Submit to BU",) + AWAITING:
            mark("bu", "supported" if od > 0 else "inconclusive", "{0} is at {1}".format(awb, step_name(step)), ids)
        elif step:
            mark("bu", "rejected", "{0} is past the BU's steps ({1})".format(awb, step_name(step)), ids, against=True)
        late = _days(r.get("ata"), r.get("eta"))
        if late is not None:
            mark("carrier", "supported" if late > 2 else "rejected",
                 "{0} arrived {1} day{2} {3} its ETA".format(awb, abs(late), "" if abs(late) == 1 else "s", "after" if late >= 0 else "before"), ids, against=late <= 2)
        else:
            hyp["carrier"]["needed"].append("an ATA for {0}".format(awb))
        rec = ctx.run_record(awb)
        if not r.get("ata") and r.get("eta") and ctx.asof and (_days(ctx.asof, r.get("eta")) or 0) > 7:
            mark("tracking", "supported", "{0}: no ATA, though its ETA ({1}) is more than 7 days before the export".format(awb, r.get("eta")), ids)
        if rec and rec.get("provider_ata") and r.get("ata") and abs(_days(rec.get("provider_ata"), r.get("ata")) or 0) > 1:
            mark("tracking", "supported", "{0}: the carrier's ATA ({1}) and eHub's ({2}) differ".format(awb, rec.get("provider_ata"), r.get("ata")), ids)
        if r.get("completed") and step != "Completed":
            mark("mapping", "supported", "{0} has a completion date but its step is {1}".format(awb, step_name(step)), ids)
        if rec and str(rec.get("outcome") or "").upper() in ("FAILED", "PARTIAL"):
            mark("extract", "supported", "the run's lookup of {0} ended {1}".format(awb, rec.get("outcome")), ids)
        elif rec:
            mark("extract", "rejected", "the run read {0} ({1})".format(awb, rec.get("outcome") or rec.get("state")), ids, against=True)
        p = peers.get(awb) or {}
        if (p.get("same_step") or 0) >= 10 or (p.get("same_agent") or 0) >= 10:
            mark("pattern", "supported", "{0:,} others at the same step in {1}, {2:,} with the same agent".format(p.get("same_step", 0), r.get("bu"), p.get("same_agent", 0)), ids)
    if ctx.mission["template"] in ("backlog", "multi_delay"):
        by_step = (ctx.dep("pattern").get("out") or {}).get("by_step") or {}
        total = sum(by_step.values()) or 1
        for step, n in by_step.items():
            share = n / total
            key = "customs" if step in AT_CUSTOMS else "agent" if step == "Assign Clearing Agent" else "docs" if step == "Correction Required" else "bu" if step in ("Submit to BU",) + AWAITING else None
            if key and share >= .2:
                mark(key, "supported", "{0:.0%} of the BU's past-due shipments are at {1}".format(share, step_name(step)), [])
        if by_step:
            mark("pattern", "supported", "the backlog spans {0} steps".format(len(by_step)), [])
    if not any(h["status"] == "supported" for k, h in hyp.items() if k != "unknown"):
        hyp["unknown"]["status"] = "supported"
        hyp["unknown"]["why"].append("no hypothesis is supported by the records")
    else:
        hyp["unknown"]["status"] = "rejected"
    model_used, council_out = None, None
    facts = [e["claim"] for e in ctx.all_evidence() if e["class"] in ("HISTORICAL", "VERIFIED_LIVE", "CALCULATED")][:30]
    if facts and ctx.allow_models():
        try:
            from . import council
            council_out = council.hypotheses(facts, [h["label"] for h in hyp.values()], ctx.mission["id"], ctx.task["id"],
                                             ctx.profile, ctx.budget)
            model_used = (council_out["members"] or [None])[0]
            for i, c in enumerate(council_out["causes"]):
                by = c["proposed_by"]
                hyp["m%d" % i] = {"id": "m%d" % i, "label": c["label"], "status": "open", "proposed_by": by,
                                  "why": ["suggested by {0}{1}; not tested against the records".format(
                                      ", ".join(by), " ({0} models agree)".format(len(by)) if len(by) > 1 else "")],
                                  "support": [], "against": [], "needed": ["evidence that would confirm or reject it"], "origin": "model:" + by[0]}
        except Exception:
            pass
    return {"evidence": [], "out": {"hypotheses": list(hyp.values()), "council": council_out}, "model": model_used,
            "summary": "{0} supported, {1} rejected, {2} open".format(*[sum(1 for h in hyp.values() if h["status"] == s) for s in ("supported", "rejected", "open")])}


# ── Independent Verification ──────────────────────────────────────────────
def verify(ctx):
    fresh = ctx.reload_rows()
    by_awb = {}
    for r in fresh:
        by_awb.setdefault(r.get("awb"), r)
    openpd = [r for r in fresh if r.get("step") != "Completed" and (r.get("overdue") or 0) > 0]
    checked = matched = 0
    mismatches, contradictions = [], []
    for e in ctx.all_evidence():
        d = e.get("data") or {}
        ok = None
        if "filter" in d and "count" in d:
            f = d["filter"]
            n = sum(1 for r in openpd if (not f.get("bus") or r.get("bu") in f["bus"]) and (not f.get("step") or r.get("step") == f["step"])
                    and (not f.get("agent") or r.get("agent") == f["agent"]) and r.get("awb") != f.get("exclude"))
            ok = n == d["count"]
        elif "awbs" in d and "field" in d and "count" in d:
            n = sum(1 for a in d["awbs"] if a in by_awb and by_awb[a].get(d["field"]) == d["value"])
            ok = n == d["count"]
        elif d.get("recount") == "carrier":
            ok = _carrier_recount(d)
        elif d.get("awb") and d["awb"] in by_awb:
            r = by_awb[d["awb"]]
            keys = [k for k in ("step", "overdue", "eta", "ata", "agent", "status") if k in d]
            if keys:
                ok = all(r.get(k) == d[k] for k in keys)
        if ok is None:
            continue
        checked += 1
        if ok:
            matched += 1
            ctx.set_verification(e["id"], "recomputed")
        else:
            mismatches.append(e["claim"])
            ctx.set_verification(e["id"], "mismatch")
    age = _days(time.strftime("%Y-%m-%d"), ctx.asof) if ctx.asof else None
    stale = age is not None and age > 3
    for r in ctx.focus_rows()[:8]:
        rec = ctx.run_record(r["awb"])
        if rec and rec.get("provider_ata") and r.get("ata") and abs(_days(rec["provider_ata"], r["ata"]) or 0) > 1:
            contradictions.append("{0}: carrier ATA {1}, eHub ATA {2}".format(r["awb"], rec["provider_ata"], r["ata"]))
        if r.get("completed") and r.get("step") != "Completed":
            contradictions.append("{0}: completed on {1} but the step is {2}".format(r["awb"], r["completed"], step_name(r.get("step"))))
    hyp = (ctx.dep("hyp").get("out") or {}).get("hypotheses") or []
    supported = [h for h in hyp if h["status"] == "supported" and h["id"] != "unknown"]
    live = any(e["class"] == "VERIFIED_LIVE" for e in ctx.all_evidence())
    reasons = []
    main = ctx.template().get("main")
    kind = ctx.mission["template"]
    if kind == "carrier_research":
        ext = [e for e in ctx.all_evidence() if e["class"] == "EXTERNAL_UNVERIFIED"]
        grade = "Weak" if ext else "Insufficient"
        reasons.append("{0} public source(s), unverified: never stronger than Weak".format(len(ext)) if ext else "no public source was found or search is not set up")
        return {"evidence": [], "summary": "{0} public source(s) found".format(len(ext)),
                "out": {"checked": 0, "matched": 0, "mismatches": [], "contradictions": [], "stale": False, "age_days": None,
                        "grade": grade, "reasons": reasons}}
    if kind == "carrier_compare":
        sufficient = (ctx.dep("carriers").get("out") or {}).get("sufficient") or []
        if checked == 0 or not sufficient:
            grade = "Insufficient"
            reasons.append("no carrier has enough finished shipments to compare" if checked else "nothing could be recounted")
        elif mismatches:
            grade = "Weak"
            reasons.append("{0} count(s) changed when recounted".format(len(mismatches)))
        else:
            grade = "Moderate"
            reasons.append("every count was recounted from the run's records ({0} carrier(s) with enough data)".format(len(sufficient)))
        return {"evidence": [], "summary": "{0} of {1} counts recounted and matched".format(matched, checked),
                "out": {"checked": checked, "matched": matched, "mismatches": mismatches, "contradictions": [], "stale": False,
                        "age_days": None, "grade": grade, "reasons": reasons}}
    if checked == 0 or not ctx.focus_rows():
        grade = "Insufficient"
        reasons.append("nothing in scope could be checked against the source")
    elif mismatches:
        grade = "Weak"
        reasons.append("{0} claim(s) no longer match the source".format(len(mismatches)))
    elif contradictions:
        grade = "Weak"
        reasons.append("sources contradict each other")
    elif main and not ctx.has_task("hyp"):
        grade = "Strong" if live and not stale else "Moderate"
        reasons.append("every fact was recomputed from the source{0}".format(" and live carrier data agrees" if live and not stale else ""))
    elif supported and live and not stale:
        grade = "Strong"
        reasons.append("live carrier data agrees and every number was recomputed")
    elif supported:
        grade = "Moderate"
        reasons.append("every number was recomputed from the export{0}, with no live carrier confirmation".format(" (more than 3 days old)" if stale else ""))
    else:
        grade = "Insufficient"
        reasons.append("no cause is supported by the records")
    if stale:
        reasons.append("the export is {0} days old".format(age))
    return {"evidence": [], "summary": "{0} of {1} claims recomputed and matched".format(matched, checked),
            "out": {"checked": checked, "matched": matched, "mismatches": mismatches, "contradictions": contradictions,
                    "stale": stale, "age_days": age, "grade": grade, "reasons": reasons}}


# ── Independent Review ────────────────────────────────────────────────────
def draft_conclusion(ctx):
    main = ctx.template().get("main")
    if main and not ctx.has_task("hyp"):
        return (ctx.dep(main).get("out") or {}).get("conclusion") or "Nothing was established."
    hyp = (ctx.dep("hyp").get("out") or {}).get("hypotheses") or []
    sup = [h["label"] for h in hyp if h["status"] == "supported" and h["id"] != "unknown"]
    return ("Supported by the records: " + "; ".join(sup) + ".") if sup else "No cause is established by the records."


def review(ctx):
    draft = draft_conclusion(ctx)
    facts = [e["claim"] for e in ctx.all_evidence()][:30]
    if not ctx.allow_models():
        return {"evidence": [], "out": {"reviewed_by": None, "agree": None, "concerns": [], "note": "Drill: deterministic checks only"},
                "summary": "Deterministic checks only (drill)"}
    try:
        from . import council
        hyp_model = (ctx.dep("hyp") or {}).get("model")
        reg = {m["id"]: m for m in models.registry()}
        avoid = models.family(reg.get(hyp_model) or {"id": hyp_model}) if hyp_model else "claude"
        v = council.review(facts, draft, avoid, ctx.mission["id"], ctx.task["id"], ctx.profile, ctx.budget)
        if not v["reviewers"]:
            raise models.ModelUnavailable("no reviewer answered")
        names = [r["model"] for r in v["reviewers"]]
        concerns = ["{0}: {1}".format(r["model"], c) for r in v["reviewers"] for c in r["concerns"]][:8]
        yes = sum(1 for r in v["reviewers"] if r["agree"])
        summary = ("{0} agree".format(" and ".join(names)) if yes == len(names) else
                   "Reviewers split: {0} of {1} agree".format(yes, len(names)) if v["split"] else
                   "{0} raised {1} concern(s)".format(" and ".join(names), len(concerns)))
        return {"evidence": [], "model": names[0], "out": {"reviewed_by": ", ".join(names), "agree": v["agree"], "split": v["split"],
                                                          "concerns": concerns, "reviewers": v["reviewers"], "failed": v["failed"]},
                "summary": summary}
    except Exception as error:
        return {"evidence": [], "out": {"reviewed_by": None, "agree": None, "concerns": [], "note": "No independent model available ({0}); deterministic checks only".format(type(error).__name__)},
                "summary": "No independent model available: deterministic checks only"}


# ── Automation Engineer: next steps, never executed ───────────────────────
def actions(ctx):
    hyp = {h["id"]: h for h in (ctx.dep("hyp").get("out") or {}).get("hypotheses") or []}
    grades = (ctx.dep("risk").get("out") or {}).get("grades") or {}
    esc = ctx.escalations()
    acts = []
    for r in ctx.focus_rows()[:25 if ctx.fanout() else 6]:
        awb = r["awb"]
        acts.append({"class": "READ_ONLY", "title": "Open {0} with both sources".format(awb), "href": "/hub#tracking?q=" + awb,
                     "why": "the export row and the run's record side by side"})
        if grades.get(awb) == "High" and not any(e.get("awb") == awb and e.get("status") == "open" for e in esc):
            acts.append({"class": "APPROVAL_REQUIRED", "title": "Escalate {0} on the Clearance page".format(awb), "href": "/hub#clearance?bu=" + str(r.get("bu")),
                         "why": "High exposure and no open escalation", "approver": "clearance.escalate", "kind": "escalate",
                         "args": {"awb": awb, "bu": r.get("bu"), "reason": "ATLAS mission {0}: {1:,.0f} days past due at {2}; exposure High".format(
                             ctx.mission["id"], r.get("overdue") or 0, step_name(r.get("step")))}})
        if hyp.get("agent", {}).get("status") == "supported" and r.get("step") == "Assign Clearing Agent":
            acts.append({"class": "PREPARATION", "title": "Ask {0} to assign a clearing agent for {1}".format(r.get("bu"), awb), "href": "/hub#clearance?bu=" + str(r.get("bu")),
                         "why": "waiting for a clearing agent (a To Do task for a person; nothing is sent)", "kind": "queue_human",
                         "args": {"awb": awb, "bu": r.get("bu")}})
        if hyp.get("tracking", {}).get("status") == "supported" or hyp.get("extract", {}).get("status") == "supported":
            if ctx.run_record(awb) or ctx.mission["template"] in ("eta_discrepancy", "ata_failure"):
                acts.append({"class": "APPROVAL_REQUIRED", "title": "Re-run {0} in the ATA run".format(awb), "href": "/hub#ata",
                             "why": "the carrier's milestones need a fresh lookup the run writes and reads back", "approver": "runs.start",
                             "kind": "reprocess", "args": {"reference": awb}})
    if ctx.mission["template"] == "backlog":
        for bu in ctx.scope.get("bus") or []:
            acts.append({"class": "READ_ONLY", "title": "Open {0}'s past-due list".format(bu), "href": "/hub#overview?list=pastdue&bu=" + bu, "why": "the full list behind the count"})
    seen, uniq = set(), []
    for a in acts:
        key = (a["title"], a.get("kind"))
        if key not in seen:
            seen.add(key)
            uniq.append(a)
    acts = uniq
    for a in acts:
        a["status"] = "not executed"
    return {"evidence": [], "out": {"actions": acts}, "summary": "{0} next step(s) proposed, none executed".format(len(acts))}


# ── Report ────────────────────────────────────────────────────────────────
def report(ctx):
    v = ctx.dep("verify").get("out") or {}
    rv = ctx.dep("review").get("out") or {}
    hyp = (ctx.dep("hyp").get("out") or {}).get("hypotheses") or []
    acts = (ctx.dep("actions").get("out") or {}).get("actions") or []
    ev = ctx.all_evidence()
    by_class = {}
    for e in ev:
        by_class.setdefault(e["class"], []).append(e["claim"])
    draft = draft_conclusion(ctx)
    rep = {"title": ctx.mission["title"], "objective": ctx.mission["objective"], "level": ctx.mission["level"],
           "severity": ctx.mission.get("severity"), "scope": ctx.scope, "sources": sorted({e["source"] for e in ev}),
           "summary": draft, "grade": v.get("grade") or "Insufficient", "grade_reasons": v.get("reasons") or [],
           "facts": (by_class.get("VERIFIED_LIVE") or []) + (by_class.get("HISTORICAL") or []) + (by_class.get("CALCULATED") or []),
           "knowledge": (by_class.get("PLATFORM_RULE") or []) + (by_class.get("GENERAL_KNOWLEDGE") or []),
           "hypotheses": [{"id": h["id"], "label": h["label"], "status": h["status"], "why": h["why"][:3], "needed": h.get("needed", [])[:2], "origin": h.get("origin")} for h in hyp],
           "contradictions": v.get("contradictions") or [], "mismatches": v.get("mismatches") or [],
           "review": rv, "actions": acts,
           "approvals": [a for a in acts if a["class"] == "APPROVAL_REQUIRED"],
           "verification": "{0} of {1} claims recomputed from the source and matched.".format(v.get("matched", 0), v.get("checked", 0)),
           "limits": ["Read from the eHub export (as of {0}) and the ATA run's records; no carrier website was opened.".format(ctx.asof or "unknown"),
                      "Nothing was changed by the investigation: a step that changes something is an action card a person approves."]}
    if ctx.fanout():
        items = ctx.dep("merge").get("out") or {}
        rep["fanout"] = {"examined": items.get("items"), "failed": items.get("failed_items"), "by_step": items.get("by_step"),
                         "by_agent": items.get("by_agent"), "grades": items.get("grades")}
    main = ctx.template().get("main")
    if main:
        rep["main"] = (ctx.dep(main).get("out") or {})
    if ctx.mission["template"] == "carrier_research":
        rep["external"] = by_class.get("EXTERNAL_UNVERIFIED") or []
        rep["limits"] = ["Public web sources, unverified; addresses are given with each one. No tracking page, login or carrier system was opened.",
                         "A real integration is a tested deployment; this is a brief for engineering."]
    if ctx.mission["level"] == "CRITICAL_INCIDENT":
        rep["incident"] = {"impact": [e for e in rep["facts"] if "past due" in e or "exposure" in e][:6],
                           "options": [a["title"] for a in acts],
                           "recommended": next((a["title"] for a in acts if a["class"] == "APPROVAL_REQUIRED"), acts[0]["title"] if acts else "Investigate further"),
                           "verification_criteria": ["the step advances in the next eHub export", "an escalation is open and answered", "the next ATA run reads the milestone back"],
                           "rollback": "Nothing is changed by the mission; an approved escalation can be resolved on the Clearance page."}
    narrative, model_used = None, None
    if ctx.allow_models() and rep["facts"]:
        inputs = rep["facts"] + rep["knowledge"] + [draft] + [h["label"] for h in hyp]
        try:
            from . import council
            w = council.word("You are ATLAS, writing the executive summary of a logistics investigation for operations staff. "
                             "Use ONLY the facts given; no new numbers, dates or references; keep uncertainty; 3 short sentences.",
                             "CONCLUSION:\n" + draft + "\n\nFACTS:\n- " + "\n- ".join(inputs[:40]),
                             inputs + [json.dumps(ctx.scope)], ctx.mission["id"], ctx.task["id"], ctx.profile, ctx.budget)
            rep["wording"] = {"members": w["members"], "chosen": w["model"], "rejected": w["rejected"]}
            if w["text"]:
                narrative, model_used = w["text"], w["model"]
            elif w["rejected"]:
                model_used = w["rejected"][0]["model"]
                rep["narrative_rejected"] = "{0}; ATLAS's own wording is used.".format(
                    "; ".join("{0}'s wording {1}".format(x["model"], x["why"]) for x in w["rejected"]))
        except Exception:
            pass
    rep["narrative"] = narrative or "{0} Confidence: {1} ({2}).".format(draft, rep["grade"], "; ".join(rep["grade_reasons"]) or "no reasons recorded")
    rep["narrative_by"] = model_used if narrative else "ATLAS"
    return {"evidence": [], "out": {"report": rep}, "model": model_used, "summary": "Report ready: confidence {0}".format(rep["grade"])}


# ── Fan-out: one shipment of many ─────────────────────────────────────────
def item(ctx):
    awb = (ctx.task.get("params") or {}).get("awb")
    r = next((x for x in ctx.rows if x.get("awb") == awb), None)
    if not r:
        return {"evidence": [claim("{0} is no longer in the loaded export.".format(awb), "HISTORICAL", _src(ctx), awb, identifiers=[awb],
                                   basis="searched the export")], "out": {"awb": awb, "missing": True}, "summary": "{0}: not in the export".format(awb)}
    od = r.get("overdue") or 0
    cust = r.get("step") in AT_CUSTOMS
    grade = "High" if od >= 30 or (cust and od >= 14) else "Medium" if od > 0 else "Low"
    ids = [awb]
    ev = [claim("{0} ({1}) is at step {2}, {3:,.0f} days past its step's due date.".format(awb, r.get("bu"), step_name(r.get("step")), od),
                "HISTORICAL", _src(ctx), awb, {"awb": awb, "step": r.get("step"), "overdue": r.get("overdue")}, ids, ctx.asof),
          claim("{0}: clearing agent {1}.".format(awb, r.get("agent") or "not assigned"), "HISTORICAL", _src(ctx), awb,
                {"awb": awb, "agent": r.get("agent")}, ids, ctx.asof)]
    rec = ctx.run_record(awb)
    if rec:
        ev.append(claim("ATA run: {0} reported ATA {1} (outcome {2}).".format(rec.get("carrier") or "the carrier", rec.get("provider_ata") or "none",
                                                                            rec.get("outcome") or "not finished"),
                        "HISTORICAL", ctx.run_source(), awb, {"awb": awb, "provider_ata": rec.get("provider_ata"), "outcome": rec.get("outcome")},
                        ids, ctx.run_when(), "recorded by the run"))
    return {"evidence": ev, "out": {"awb": awb, "bu": r.get("bu"), "step": r.get("step"), "agent": r.get("agent"), "ffw": r.get("ffw"),
                                    "overdue": od, "grade": grade, "carrier": (rec or {}).get("carrier")},
            "summary": "{0}: {1}, exposure {2}".format(awb, step_name(r.get("step")), grade)}


def merge(ctx):
    outs, failed = [], 0
    for t in ctx.tasks_of("item"):
        if t["status"] != "done":
            failed += 1
            continue
        o = (t.get("output") or {}).get("out") or {}
        if not o.get("missing"):
            outs.append(o)
    awbs = [o["awb"] for o in outs]
    by_step, by_agent, grades = {}, {}, {}
    for o in outs:
        by_step[o.get("step")] = by_step.get(o.get("step"), 0) + 1
        if o.get("agent"):
            by_agent[o["agent"]] = by_agent.get(o["agent"], 0) + 1
        grades[o["grade"]] = grades.get(o["grade"], 0) + 1
    ev = []
    for step, n in sorted(by_step.items(), key=lambda x: -x[1])[:4]:
        ev.append(claim("{0:,} of the {1:,} shipments examined are at {2}.".format(n, len(outs), step_name(step)), "CALCULATED",
                        _src(ctx), data={"awbs": awbs, "field": "step", "value": step, "count": n}, identifiers=awbs, freshness=ctx.asof,
                        basis="counted over the shipments examined"))
    for agent, n in sorted(by_agent.items(), key=lambda x: -x[1])[:3]:
        if n > 1:
            ev.append(claim("{0:,} of them are with clearing agent {1}.".format(n, agent), "CALCULATED", _src(ctx),
                            data={"awbs": awbs, "field": "agent", "value": agent, "count": n}, identifiers=awbs, freshness=ctx.asof,
                            basis="counted over the shipments examined"))
    return {"evidence": ev, "out": {"items": len(outs), "failed_items": failed, "awbs": awbs, "by_step": by_step, "by_agent": by_agent, "grades": grades},
            "summary": "{0:,} shipment(s) merged{1}".format(len(outs), ", {0} failed".format(failed) if failed else "")}


# ── Carrier performance comparison ────────────────────────────────────────
WINDOW_DAYS = 30


def _carrier_recount(d):
    from . import tools
    from .. import carrier_health
    h = carrier_health.health(tools.run_events(), window_days=d.get("window") or WINDOW_DAYS)
    c = next((x for x in h.get("carriers") or [] if x.get("carrier") == d.get("carrier")), None)
    return bool(c) and c["counts"]["shipments"] == d.get("shipments") and c["counts"]["verified_updates"] == d.get("verified")


def carriers(ctx):
    from . import tools
    from .. import carrier_health
    h = carrier_health.health(tools.read("run_events"), window_days=WINDOW_DAYS)
    ev, sufficient = [], []
    for c in h.get("carriers") or []:
        n, v = c["counts"]["shipments"], c["counts"]["verified_updates"]
        if c.get("sufficient"):
            sufficient.append({"carrier": c["carrier"], "label": c.get("label"), "shipments": n, "verified": v,
                               "rate": c["rates"]["verified_updates"], "failed": c["counts"]["failed"]})
            text = "{0}: {1:,} of {2:,} finished shipments were verified Hub updates ({3:.0%}); {4:,} failed.".format(
                c.get("label"), v, n, c["rates"]["verified_updates"] or 0, c["counts"]["failed"])
        else:
            text = "{0}: {1:,} finished shipment(s), below the minimum of {2} for a rate: insufficient data.".format(c.get("label"), n, h.get("minimum"))
        ev.append(claim(text, "CALCULATED", "ATA run outcomes (last {0} days)".format(WINDOW_DAYS),
                        data={"recount": "carrier", "carrier": c["carrier"], "shipments": n, "verified": v, "window": WINDOW_DAYS},
                        basis="counted from the run's recorded outcomes"))
    ranked = sorted(sufficient, key=lambda x: -(x["rate"] or 0))
    if len(ranked) >= 2:
        conclusion = "Over the last {0} days, {1} had the highest share of verified updates ({2:.0%}) and {3} the lowest ({4:.0%}), among carriers with enough data.".format(
            WINDOW_DAYS, ranked[0]["label"], ranked[0]["rate"] or 0, ranked[-1]["label"], ranked[-1]["rate"] or 0)
    elif ranked:
        conclusion = "Only {0} has enough finished shipments for a rate ({1:.0%} verified updates); the others have too few to compare.".format(ranked[0]["label"], ranked[0]["rate"] or 0)
    else:
        conclusion = "No carrier has enough finished shipments in the last {0} days to compare.".format(WINDOW_DAYS)
    return {"evidence": ev, "out": {"carriers": ranked, "sufficient": [x["carrier"] for x in ranked], "records": h.get("records"),
                                    "minimum": h.get("minimum"), "conclusion": conclusion},
            "summary": "{0} carrier(s) compared, {1} with enough data".format(len(h.get("carriers") or []), len(ranked))}


# ── ETA / ATA reconciliation ──────────────────────────────────────────────
def eta(ctx):
    ev, found = [], []
    for r in ctx.focus_rows()[:12]:
        awb, rec = r["awb"], ctx.run_record(r["awb"])
        if not rec:
            ev.append(claim("{0}: the ATA run has no record, so the carrier's dates are not known.".format(awb), "HISTORICAL", ctx.run_source(), awb,
                            identifiers=[awb], basis="searched the run's records"))
            continue
        for label, mine, theirs in (("ETA", r.get("eta"), rec.get("provider_eta")), ("ATA", r.get("ata"), rec.get("provider_ata"))):
            if mine and theirs:
                gap = _days(theirs, mine)
                if gap is not None and abs(gap) > 1:
                    found.append({"awb": awb, "field": label, "hub": mine, "carrier": theirs, "days": gap})
                    ev.append(claim("{0}: the carrier's {1} ({2}) differs from eHub's ({3}) by {4} day(s).".format(awb, label, theirs, mine, abs(gap)),
                                    "HISTORICAL", ctx.run_source(), awb, {"awb": awb, label.lower(): mine}, [awb], ctx.run_when(), "compared"))
                else:
                    ev.append(claim("{0}: the carrier's {1} and eHub's agree ({2}).".format(awb, label, mine), "HISTORICAL", _src(ctx), awb,
                                    {"awb": awb, label.lower(): mine}, [awb], ctx.asof, "compared"))
            elif theirs and not mine:
                found.append({"awb": awb, "field": label, "hub": None, "carrier": theirs, "days": None})
                ev.append(claim("{0}: the carrier reports {1} {2}; eHub has none.".format(awb, label, theirs), "HISTORICAL", ctx.run_source(), awb,
                                {"awb": awb, label.lower(): mine}, [awb], ctx.run_when(), "compared"))
    return {"evidence": ev, "out": {"discrepancies": found,
                                    "conclusion": "{0} date discrepanc{1} between the carrier and eHub.".format(len(found), "y" if len(found) == 1 else "ies")},
            "summary": "{0} discrepanc{1}".format(len(found), "y" if len(found) == 1 else "ies")}


# ── Automation failure analysis ──────────────────────────────────────────
def failure(ctx):
    from . import tools
    ev, out = [], []
    for r in ctx.focus_rows()[:12]:
        awb, rec = r["awb"], ctx.run_record(r["awb"])
        if not rec:
            continue
        try:
            f = tools.failure_class(rec)
        except Exception:
            f = None
        if f:
            cat = f.get("classification_label") or f.get("classification") or "unknown"
            basis = f.get("root_cause_status") or "UNKNOWN"
            out.append({"awb": awb, "category": f.get("classification"), "label": cat, "basis": basis, "root_cause": f.get("root_cause")})
            ev.append(claim("{0}: the run's lookup failed; cause {1} ({2}).".format(awb, cat, basis), "HISTORICAL", ctx.run_source(), awb,
                            {"awb": awb, "category": cat}, [awb], ctx.run_when(), "classified by the failure analysis ({0})".format(basis)))
        history = tools.run_events(awb)
        if history:
            ok = sum(1 for e in history if e.get("verified") is True)
            ev.append(claim("{0}: {1} earlier run result(s) on record, {2} verified.".format(awb, len(history), ok), "HISTORICAL",
                            "ATA run outcomes", awb, {"awb": awb}, [awb], None, "read from the run's events"))
    return {"evidence": ev, "out": {"failures": out,
                                    "conclusion": "{0} failed lookup(s) classified.".format(len(out)) if out else "The run's records show no failed lookup in scope."},
            "summary": "{0} failure(s) classified".format(len(out))}


# ── Validation (the automation's own date rules) ─────────────────────────
def validate(ctx):
    from . import tools
    ev, fails = [], 0
    checked = 0
    for r in ctx.focus_rows()[:12]:
        awb, rec = r["awb"], ctx.run_record(r["awb"])
        if not rec:
            ev.append(claim("{0}: no run record to validate.".format(awb), "HISTORICAL", ctx.run_source(), awb, identifiers=[awb],
                            basis="searched the run's records"))
            continue
        checked += 1
        for status, text in tools.validate_record(rec):
            if status == "FAIL":
                fails += 1
            ev.append(claim("{0}: {1} — {2}".format(awb, status, text), "PLATFORM_RULE", "The automation's date rules", awb,
                            {"check": status}, [awb], ctx.run_when(), "the same rules the automation uses before a write"))
    return {"evidence": ev, "out": {"checked": checked, "fails": fails,
                                    "conclusion": ("{0} check(s) failed across {1} shipment(s).".format(fails, checked) if checked
                                                   else "No shipment in scope has a run record to validate.")},
            "summary": "{0} check(s) failed".format(fails)}


# ── Web Investigation ────────────────────────────────────────────────────
_TRACKING = re.compile(r"(track|trace|tracking|shipment-status|cargo-status)\W*(\?|/)?.*\d{5,}", re.I)


def web(ctx):
    from . import tools
    q = re.sub(r"\b[A-Z0-9]{4,}\d{4,}\b|\S+@\S+", " ", ctx.mission.get("question") or "")
    q = " ".join((q + " cargo tracking milestones official").split())[:200]
    try:
        hits = tools.read("web_search", q)
    except LookupError as error:
        return {"evidence": [claim("Web search is not set up on this server ({0}); nothing was looked up.".format(error), "PLATFORM_RULE",
                                   "ATLAS configuration", basis="configuration")],
                "out": {"sources": [], "conclusion": "No public sources were searched: web search is not set up here."},
                "summary": "Web search not set up"}
    except Exception as error:
        return {"evidence": [], "out": {"sources": [], "conclusion": "The web search failed ({0}).".format(type(error).__name__)},
                "summary": "Web search failed"}
    ev, src = [], []
    for h in hits[:6]:
        if _TRACKING.search(h["url"]):
            continue                                   # never a tracking page
        src.append({"url": h["url"], "title": h["title"]})
        ev.append(claim("{0}: {1}".format(h["title"] or h["url"], h["snippet"][:240]), "EXTERNAL_UNVERIFIED", h["url"],
                        data={"url": h["url"]}, freshness=time.strftime("%Y-%m-%d"), basis="public web page, not verified"))
    return {"evidence": ev, "out": {"sources": src, "conclusion": "{0} public source(s) describe the carrier's tracking; none is verified.".format(len(src))
                                    if src else "No usable public source was found."},
            "summary": "{0} public source(s)".format(len(src))}


FUNCS = {"shipment": shipment, "analyst": analyst, "logistics": logistics, "carrier": carrier, "risk": risk,
         "hypotheses": hypotheses, "verify": verify, "review": review, "actions": actions, "report": report,
         "item": item, "merge": merge, "carriers": carriers, "eta": eta, "failure": failure, "validate": validate, "web": web}
