"""
ATLAS on PO Automation — the same ATLAS, another operational domain.

Answers only from PO job records and their events (po/store.py), through
the same failure intelligence the shipment answers use
(intelligence/failures.from_po). Every line keeps its knowledge type:

    Fact            read from the job record or its events
    Learned         from the verified learning store (past PO jobs)
    Recommendation  what a person can do; ATLAS does not do it
    Not established what the record does not show

A PO question is one that names PO work (PO, the duty request, the
declaration, the template, the email…) or that comes from the PO page
(context.domain == "po"). Everything else is left to the shipment answers.
Nothing here sends, retries or changes a job; a document value that is not
in the record is never supplied.
"""

import re

PO_WORDS = re.compile(r"\b(po|p\.o\.|pos|purchase orders?|duty (payment )?requests?|cheque requests?|"
                      r"declarations?|bill of entry|boe|po automation)\b", re.I)
DOC_WORDS = re.compile(r"\b(pdf|template|e-?mail|recipient|sent to|attachment|document|"
                       r"bill entry|ehub|e-hub)\b", re.I)

KINDS = (
    # "Who was it sent to?" is about the recipient, not whether it was sent.
    ("recipient", r"\bwho (was|is) it sent to\b|\bsent to whom\b|\bwho (did|will) (it|you) "
                  r"(send|email)\b|\bwho got it\b"),
    ("document", r"\bwhere (is|are|was|were|did) (the )?(bill ?(of )?entry )?(pdf|document|file|"
                 r"bill ?(of )?entry)s?\b|\bwhere'?s the (bill ?(of )?entry|pdf|document)\b"),
    ("extracted", r"\bwhat did (we|you|it|atlas) (extract|read|get)\b|\bextracted (values?|fields?|"
                  r"data|figures?)\b|\bwhat (was|were|values were) extracted\b|"
                  r"\bwhat values\b|\bwhat (is|was) in the pdf\b"),
    ("email_sent", r"\b(was|has|is) (the )?(e-?mail|po|it|message) (actually |really )?(been )?sent\b|"
                   r"\bdid (the )?(e-?mail|it|message) (actually |really )?(go( out)?|send)\b|"
                   r"\be-?mail (actually )?sent\b"),
    ("template", r"\b(was|is|has) (the )?(template|document|output) (been )?(generated|filled|"
                 r"created|made|saved)\b|\btemplate generated\b|\b(what|which) template\b|"
                 r"\btemplate (version|used)\b|\bwhere (is|was) (the )?output\b"),
    ("happened", r"\bwhat happened (to|with) (this|that|the) (po|job|one|declaration)\b|"
                 r"\bwhat happened\??$"),
    ("discovery", r"\bhow (was|did|is) (it|this|the (document|pdf|po|file)) (found|discovered|"
                  r"picked|chosen|get found)\b|\bbill entry\b|\bidentifier\b|\bunder clearance\b|"
                  r"\bwhy (was|is) (it|this|the \w+) skipped\b|\bwhy skipped\b|\bmanage\b|"
                  r"\bdocuments? section\b|\bwhich (document|file|pdf)\b|\bwhere did (it|the "
                  r"(document|pdf)) come from\b|\b(ehub|e-hub) (record|steps?)\b"),
    ("why_not_sent", r"\bwhy (wasn'?t|was not|isn'?t|is not|hasn'?t|has not|didn'?t|did not)\b.*"
                     r"\b(sent|send|go out|emailed|mailed|generated)\b|\bwhy (was|is) (it|this|the \w+) "
                     r"blocked\b|\bwhy blocked\b"),
    ("missing", r"\bwhat'?s? (is )?missing\b|\bmissing (fields?|values?|data)\b|\bwhat (fields? )?"
                r"(are|is) missing\b"),
    ("match", r"\b(did|does) (the )?pdf match\b|\bmatch(ed)? (the )?hub\b|\bpdf (vs|versus|against) "
              r"(the )?hub\b|\b(mismatch|validation)\b"),
    ("recipient", r"\bwho (was|is) it sent to\b|\bsent to whom\b|\brecipient\b|\bwho (did|will) "
                  r"(it|you) (send|email)\b|\bwho got it\b"),
    ("what_failed", r"\bwhat (failed|went wrong|broke)\b|\bwhy did (it|this|the \w+) fail\b|"
                    r"\bwhy the error\b|\bwhat'?s? (is )?wrong\b|\bwhy\s*\??$"),
    ("next", r"\bwhat should (i|we) do\b|\bnext step\b|\bwhat now\b|\bwhat do (i|we) do\b"),
    ("status", r"\b(status|pending|queue|how many|which pos?|what pos?|list)\b|\bwas it sent\b|"
               r"\bhas it been sent\b|\bdid it (send|go)\b|\bsent\?$"),
    ("timeline", r"\b(timeline|events?|history|what happened)\b"),
    ("known", r"\b(seen|happened) (this )?before\b|\bknown (failure|issue|problem)\b|"
              r"\blearn(ed|t)?\b"),
)


def _services():
    try:
        from po import service as po_service
        return po_service.current()
    except Exception:
        return None


def detect(question, context=None):
    """A PO question kind, or None for a question about shipments."""
    context = context or {}
    if context.get("po") == "0":
        return None                     # this role may not see PO jobs
    text = str(question or "").strip()
    on_page = context.get("domain") == "po" or bool(context.get("po_id"))
    named = bool(PO_WORDS.search(text))
    about_doc = bool(DOC_WORDS.search(text))
    if not (on_page or named or about_doc):
        return None
    service = _services()
    if service is None:
        return None
    if not on_page and not named:
        # "email" or "template" alone: only when PO jobs exist to talk about.
        try:
            if not service.store.all(1):
                return None
        except Exception:
            return None
    lowered = text.lower()
    for kind, pattern in KINDS:
        if re.search(pattern, lowered):
            return kind
    return "status" if (named or on_page) else None


def _pick(records, question, context):
    if context.get("po_id"):
        for r in records:
            if r["po_id"] == context["po_id"]:
                return r
    norm = lambda v: re.sub(r"[^A-Z0-9]", "", str(v or "").upper())      # noqa: E731
    text = norm(question)
    for r in records:
        for value in (r.get("reference"), r.get("number")):
            v = norm(value)
            if len(v) >= 5 and v in text:
                return r
    return None


def _lines(items):
    names = {"FACT": "Fact", "LEARNED": "Learned (past jobs)", "INFERENCE": "Inference",
             "RECOMMENDATION": "Recommendation", "UNVERIFIED": "Not established"}
    return ["**{0}** — {1}".format(names.get(i["type"], i["type"]), i["text"]) for i in items]


def _status_line(r):
    email = r.get("email") or {}
    return "{0} {1} — {2}{3}{4}".format(
        r.get("number") or "", r["reference"], r.get("label"),
        "; email {0}".format(email.get("status").lower()) if email.get("status") else "",
        " to {0}".format(email["recipient"]) if email.get("status") in ("SENT", "CONFIRMED")
        else "")


def answer(kind, question, context=None):
    """{"answer", ...} from PO records, or None when there is nothing to answer from."""
    context = context or {}
    service = _services()
    if service is None:
        return None
    try:
        records = service.store.all(200)
    except Exception:
        return None
    from intelligence import failures as F
    reply = {"card": None, "grounded": True, "intent": "po_" + kind,
             "sources": ["PO job records"], "reference": context.get("reference"),
             "preset_buttons": [{"label": "Open PO Automation",
                                 "action": {"type": "page", "page": "po"}}]}
    if not records:
        reply["answer"] = ("No PO job has been processed yet, so there is nothing to report. "
                           "Start one from the PO Automation page with + Process PO.")
        return reply
    record = _pick(records, question, context)
    if kind == "status" and record is None:
        summary = service.summary(200)
        k = summary["kpis"]
        lines = ["PO Automation: {0}. {1} pending, {2} processing, {3} need validation, {4} "
                 "generated, {5} sent, {6} failed.".format(
                     summary["status"], k["pending"], k["processing"], k["validation_required"],
                     k["generated"], k["sent"], k["failed"])]
        lines += ["• " + _status_line(r) for r in records[:6]]
        reply["answer"] = "\n".join(lines)
        return reply
    if record is None:
        failing = [r for r in records if F.from_po(r) is not None]
        record = failing[0] if kind in ("what_failed", "why_not_sent", "missing", "match",
                                        "next", "known") and failing else records[0]
    events = service.store.events(record["po_id"])
    try:
        from intelligence import learning as L
        learning = L.snapshot()
    except Exception:
        learning = None
    failure = F.from_po(record, events, learning)
    reply["po_id"] = record["po_id"]
    reply["reference"] = record["reference"]
    title = "**PO {0}{1}** — {2}.\n\n".format(
        (record.get("number") + " · ") if record.get("number") else "", record["reference"],
        record.get("label"))
    email = record.get("email") or {}
    validation = record.get("validation") or {}
    # Every answer about one job says first whether it ran against the real
    # eHub: a stand-in's values are not production evidence.
    prov = record.get("provenance") or {}
    if prov.get("source") != "REAL" or prov.get("verification") != "VERIFIED":
        title += ("**Not established** — This job did not run against the real eHub "
                  "({0} / {1}{2}). Nothing below is production evidence: its document and "
                  "values are not proven to come from a real Bill Entry.\n\n".format(
                      prov.get("source") or "no source recorded",
                      prov.get("verification") or "UNVERIFIED",
                      " — " + prov["why"] if prov.get("why") else ""))

    if kind == "document":
        doc = record.get("document") or {}
        trail = record.get("discovery") or {}
        manage = next((s for s in trail.get("steps") or [] if s.get("step") == "manage"), {})
        bill = trail.get("bill_entry") or {}
        if doc.get("sha256"):
            lines = ["**Fact** — The Bill Entry PDF is {0} ({1:,} bytes, SHA-256 {2}…), "
                     "retrieved at {3}{4}.".format(
                         doc.get("filename"), doc.get("bytes") or 0, str(doc["sha256"])[:16],
                         doc.get("retrieved_at"), " by " + doc["method"] if doc.get("method") else ""),
                     "**Fact** — The copy the job read is kept at {0}.".format(doc.get("evidence"))]
            if manage.get("url"):
                lines.append("**Fact** — It was taken from the record's Manage page: {0}.".format(
                    manage["url"]))
            if doc.get("served_filename") and doc.get("served_filename") != doc.get("filename"):
                lines.append("**Fact** — eHub served it as {0}.".format(doc["served_filename"]))
            if bill.get("rule"):
                lines.append("**Fact** — Chosen by the rule: {0}.".format(bill["rule"]))
        else:
            lines = ["**Fact** — No PDF was retrieved for this job: it is {0}.".format(
                record.get("label", "").lower())]
            if (record.get("failure") or {}).get("detail"):
                lines.append("**Fact** — " + record["failure"]["detail"])
            if bill.get("candidates"):
                lines.append("**Fact** — Documents seen on the Manage page: {0}.".format(
                    ", ".join(c["name"] for c in bill["candidates"])))
            if failure is not None:
                lines += _lines(failure["recommendations"])
        text = title + "\n".join(lines)
    elif kind == "extracted":
        fields = record.get("fields") or {}
        if not fields:
            text = title + "**Fact** — Nothing was extracted: the job stopped at {0}, before the " \
                           "PDF was read.".format(record.get("label", "").lower())
        else:
            doc = record.get("document") or {}
            lines = ["**Fact** — Read from {0} (SHA-256 {1}…) by {2}:".format(
                doc.get("filename"), str(doc.get("sha256"))[:16],
                ", ".join(doc.get("read_methods") or []) or "the PDF reader")]
            for f in fields.values():
                if f.get("status") == "FOUND":
                    value = f["value"]
                    if f.get("kind") == "lines":
                        value = "; ".join("{0} {1:,.2f}".format(l["label"], l["amount"])
                                          for l in value)
                    elif isinstance(value, float):
                        value = "{0:,.2f}".format(value)
                    lines.append("**Fact** — {0}: {1}{2}".format(
                        f["label"], value, " — from the line “{0}”".format(f["evidence"])
                        if f.get("evidence") else ""))
                elif f.get("status") == "AMBIGUOUS":
                    lines.append("**Fact** — {0}: ambiguous — the PDF prints {1}; none chosen.".format(
                        f["label"], ", ".join(str(c["value"]) for c in f.get("candidates") or [])))
                else:
                    lines.append("**Fact** — {0}: not found in the PDF.".format(f["label"]))
            duty = next((c for c in validation.get("checks") or []
                         if c["name"] == "arithmetic:duty"), None)
            if duty:
                lines.append("**Fact** — Calculated by the duty request rule (duty amount less the "
                             "VAT/levy lines): {0}.".format(duty.get("detail") or duty.get("pdf")))
            text = title + "\n".join(lines)
    elif kind == "email_sent":
        state = record["state"]
        if state == "EMAIL_CONFIRMED":
            text = title + "**Fact** — Yes. Microsoft Graph accepted it (HTTP {0}) at {1}, and it " \
                           "was found in the mailbox's Sent Items{2}. Sent to {3}.".format(
                               email.get("graph_status"), email.get("sent_at"),
                               " (" + email["confirmation"] + ")" if email.get("confirmation")
                               else "", email.get("recipient"))
        elif state == "EMAIL_SENT":
            text = title + "**Fact** — Microsoft Graph accepted it (HTTP {0}) at {1}.\n" \
                           "**Not established** — It has not been found in Sent Items yet, so " \
                           "delivery is not confirmed.".format(email.get("graph_status"),
                                                              email.get("sent_at"))
        elif state == "EMAIL_FAILED":
            text = title + "**Fact** — No. The send failed: {0}.".format(
                email.get("error") or (record.get("failure") or {}).get("detail"))
        elif state == "VALIDATION_FAILED":
            text = title + "**Fact** — No. Validation failed, and an email is never sent for a " \
                           "document that did not pass validation."
        elif state == "EMAIL_PREPARED":
            text = title + "**Fact** — No. It is prepared for {0} and waits for someone to press " \
                           "Send PO.".format(email.get("recipient"))
        else:
            text = title + "**Fact** — No. It is {0}{1}.".format(
                record.get("label", "").lower(),
                "; " + "; ".join(email["reasons"]) if email.get("reasons") else "")
    elif kind == "happened":
        from po import evidence as _ev
        lines = []
        for s in _ev.stages(record):
            if s["status"] == "NOT_RUN":
                continue
            lines.append("**Fact** — {0}: {1}{2}".format(
                s["label"], s["status"], " — " + _short(s["evidence"]) if s["evidence"] else ""))
        not_run = [s["label"] for s in _ev.stages(record) if s["status"] == "NOT_RUN"]
        if not_run:
            lines.append("**Fact** — Not reached: {0}.".format(", ".join(not_run)))
        if failure is not None:
            lines += _lines(failure["unverified"]) + _lines(failure["recommendations"])
        if events:
            lines.append("\n**Events** (newest last):")
            lines += ["• {0} — {1} ({2})".format(e.get("timestamp"), e.get("event"),
                                                 str(e.get("status")).lower())
                      for e in events[-14:]]
        text = title + "\n".join(lines)
    elif kind == "discovery":
        prov = record.get("provenance") or {}
        lines = ["**Fact** — Source: {0} / {1} — {2}.".format(
            prov.get("source", "UNKNOWN"), prov.get("verification", "UNVERIFIED"),
            prov.get("why", "no discovery trail"))] if prov else []
        lines += ["**Fact** — " + t for t in F.discovery_lines(record)]
        if not lines:
            lines = ["**Fact** — No eHub step is recorded for this job yet (it is {0}).".format(
                record.get("label", "").lower())]
        if record.get("identifier"):
            lines.append("**Fact** — The identifier {0} was passed to the next stage: it is "
                         "this job's number, and the PDF's declaration number is checked "
                         "against it.".format(record["identifier"]))
            check = next((c for c in (validation.get("checks") or [])
                          if c["name"] == "hub:identifier"), None)
            if check:
                lines.append("**Fact** — Identifier check: eHub {0} · PDF {1} → {2}.".format(
                    check["hub"], check["pdf"], check["status"]))
        if failure is not None and record["state"] in ("SKIPPED", "NEEDS_REVIEW", "PDF_NOT_FOUND"):
            lines += _lines(failure["recommendations"])
        text = title + "\n".join(lines)
    elif kind in ("what_failed", "why_not_sent"):
        if failure is None:
            if record["state"] in ("EMAIL_SENT", "EMAIL_CONFIRMED"):
                text = title + "**Fact** — Nothing failed: it was sent to {0}{1}.".format(
                    email.get("recipient"), " and confirmed in Sent Items"
                    if record["state"] == "EMAIL_CONFIRMED" else
                    " (accepted by Microsoft 365, not yet confirmed in Sent Items)")
            elif record["state"] == "EMAIL_PREPARED":
                text = title + "**Fact** — Nothing failed. It passed validation and the document " \
                               "was generated; it is ready and waits for someone to press Send PO."
            else:
                text = title + "**Fact** — Nothing has failed; the job is {0}.".format(
                    record.get("label", "").lower())
        else:
            lead = ""
            if kind == "why_not_sent":
                lead = "PO {0} was not sent because {1}.\n\n".format(
                    record.get("number") or record["reference"],
                    _because(failure, record))
            text = title + lead + "\n".join(
                _lines(failure["facts"]) + _lines(failure["unverified"]) +
                ["**Root cause** — {0}: {1}".format(
                    "declared by the pipeline stage that stopped it" if
                    failure["root_cause_status"] == "VERIFIED" else "not established",
                    failure["classification_label"])] +
                _lines(failure["learned"]) + _lines(failure["recommendations"]) +
                ["**Recovery** — " + failure["recovery_plan"]["statement"]])
            reply["failure_id"] = failure["failure_id"]
            reply["sources"].append("failure intelligence")
    elif kind == "missing":
        fields = dict(record.get("fields") or {})
        fields.update(record.get("request_fields") or {})
        if not fields:
            text = title + "**Fact** — No fields were extracted: the job stopped at {0}.".format(
                record.get("label", "").lower())
        else:
            from po import doctypes as _dt
            required = {f["name"] for f in _dt.get(record.get("doctype"))["fields"]
                        if f["required"]}
            missing = [f for f in fields.values() if f.get("status") == "MISSING"]
            ambiguous = [f for f in fields.values() if f.get("status") == "AMBIGUOUS"]
            lines = []
            for f in [m for m in missing if m["name"] in required]:
                lines.append("**Fact** — {0}: missing — required, so it blocks the job{1}.".format(
                    f["label"], "; " + f["note"] if f.get("note") else
                    " (not provided for this request)" if f["name"] in
                    (record.get("request_fields") or {}) else " (not printed on the document)"))
            for f in ambiguous:
                lines.append("**Fact** — {0}: ambiguous — the document prints {1}; none is "
                             "chosen.".format(f["label"], ", ".join(
                                 str(c["value"]) for c in f.get("candidates") or [])))
            optional = [m["label"] for m in missing if m["name"] not in required]
            if not lines:
                lines.append("**Fact** — Nothing required is missing: every field the template "
                             "needs was found once.")
            if optional:
                lines.append("**Fact** — Left empty (optional): {0}.".format(", ".join(optional)))
            text = title + "\n".join(lines)
    elif kind == "match":
        checks = [c for c in validation.get("checks") or [] if c["name"].startswith("hub:")
                  or (c.get("blocking") and c["status"] not in ("OK", "PASSED"))]
        if not checks:
            text = title + "**Fact** — It was not compared with the Hub: the job stopped at " \
                           "{0}.".format(record.get("label", "").lower())
        else:
            text = title + "\n".join(
                "**Fact** — {0}: PDF {1} · Hub {2} → {3}.".format(c["label"], c["pdf"], c["hub"],
                                                                  c["status"])
                for c in checks)
            if any(c["status"] == "MISMATCH" for c in checks):
                text += "\n**Fact** — A mismatch blocks generation and sending; neither value " \
                        "was chosen."
            for reason in validation.get("reasons") or []:
                text += "\n**Fact** — " + reason
            if record["state"] == "VALIDATION_FAILED":
                text += "\n**Fact** — No template was generated and no email was sent."
                if failure is not None:
                    text += "\n" + "\n".join(_lines(failure["recommendations"]))
    elif kind == "recipient":
        if email.get("status") in ("SENT", "CONFIRMED"):
            text = title + "**Fact** — Sent to {0} from {1}, subject “{2}”, attachment {3}. " \
                           "{4}".format(email.get("recipient"), email.get("sender") or
                                        "the ATA mailbox", email.get("subject"),
                                        email.get("attachment"),
                                        email.get("confirmation") or "")
        elif email.get("recipient"):
            text = title + "**Fact** — Not sent. It is addressed to {0}; status {1}{2}.".format(
                email["recipient"], (email.get("status") or "not prepared").lower(),
                " — " + "; ".join(email["reasons"]) if email.get("reasons") else "")
        else:
            text = title + "**Fact** — It was not sent to anyone; no email was prepared."
    elif kind == "template":
        out = record.get("output") or {}
        generated = record.get("template") or {}
        if out.get("verified"):
            text = title + "**Fact** — Yes. Template {0} (SHA-256 {1}…) was filled from the " \
                           "validated values and read back cell by cell ({2} cells).\n" \
                           "**Fact** — Saved as {3} in {4} at {5} ({6:,} bytes, SHA-256 {7}…), " \
                           "re-opened from disk and read back.".format(
                               out.get("template_version"), str(out.get("template_sha256"))[:12],
                               len(out.get("cells") or {}), out.get("filename"),
                               out.get("folder") or "the output folder", out.get("saved_at") or
                               out.get("generated_at"), out.get("bytes") or 0,
                               str(out.get("sha256"))[:16])
        elif generated:
            text = title + "**Fact** — The template was generated and read back in memory, but " \
                           "it was NOT saved: {0}.".format(
                               (record.get("failure") or {}).get("detail") or "the job stopped")
        else:
            text = title + "**Fact** — No template was filled: the job stopped at {0}. The " \
                           "template it would use is {1}.".format(
                               record.get("label", "").lower(), record.get("doctype"))
    elif kind == "next":
        if failure is not None:
            text = title + "\n".join(_lines(failure["recommendations"]) or
                                     ["**Recommendation** — Ask the automation's owner; no "
                                      "verified recovery exists for this failure."])
        elif record["state"] == "EMAIL_PREPARED":
            text = title + "**Recommendation** — Review the extracted values and press Send PO."
        elif record["state"] == "EMAIL_SENT":
            text = title + "**Recommendation** — Nothing to do: it was accepted by Microsoft " \
                           "365. Use Check delivery to look for it in Sent Items again."
        else:
            text = title + "**Fact** — Nothing needs you for this job."
    elif kind == "timeline":
        text = title + "\n".join("• {0} — {1} ({2})".format(e.get("timestamp"), e.get("event"),
                                                            e.get("status").lower())
                                 for e in events[-14:]) if events else \
            title + "**Fact** — No events are recorded for this job."
    elif kind == "known":
        text = title + ("\n".join(_lines(failure["learned"])) if failure and failure["learned"]
                        else "**Fact** — The learning store has no earlier PO job with this "
                             "outcome.")
    else:
        text = title + "**Fact** — " + _status_line(record)
    reply["answer"] = text
    return reply


def _short(evidence):
    """A stage's evidence, in one line."""
    if isinstance(evidence, dict):
        return "; ".join("{0} {1}".format(k, v if not isinstance(v, (list, dict)) else
                                          (", ".join(map(str, v)) if isinstance(v, list) else "…"))
                         for k, v in evidence.items() if v not in (None, "", []))[:240]
    return str(evidence)[:240]


def _because(failure, record):
    if record.get("state") in ("SKIPPED", "NEEDS_REVIEW") or \
            failure.get("classification") == "DOCUMENT_NOT_FOUND":
        detail = (record.get("failure") or {}).get("detail") or failure["classification_label"]
        return detail[0].lower() + detail[1:].rstrip(".")
    validation = record.get("validation") or {}
    mismatch = next((c for c in validation.get("checks") or [] if c["status"] == "MISMATCH"),
                    None)
    if mismatch:
        return "the PDF {0} ({1}) does not match the Hub record ({2})".format(
            mismatch["label"], mismatch["pdf"], mismatch["hub"])
    reasons = validation.get("reasons") or (record.get("email") or {}).get("reasons")
    if reasons:
        return reasons[0][0].lower() + reasons[0][1:]
    return failure["classification_label"]
