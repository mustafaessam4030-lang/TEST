"""
The validation gate. Nothing is generated or sent unless this passes.

Each check is a row the drawer shows as it is:

    {"name", "label", "pdf", "hub", "status", "blocking", "detail"}

    status   MATCH / MISMATCH        a PDF value against the Hub's
             PRESENT / MISSING / AMBIGUOUS   a required field
             OK / FAILED             the document's own arithmetic
             NOT_CHECKED             nothing to compare (says why)

`passed` is True only when no blocking check failed. A mismatch is never
resolved by picking one side: both values are shown and processing stops.
"""

from . import extract as X

BLOCKING_FAILURES = ("MISMATCH", "MISSING", "AMBIGUOUS", "FAILED")


def _value_text(field):
    if field is None:
        return None
    if field.get("status") == X.AMBIGUOUS:
        return " | ".join(str(c["value"]) for c in field.get("candidates") or [])
    value = field.get("value")
    if isinstance(value, list):
        return "{0} line(s)".format(len(value))
    return value


def validate(doctype, fields, hub, request_fields):
    """
    fields          the PDF fields (extract.extract)
    hub             the Hub record the document was found on ({"bol_awb": ...})
    request_fields  operator / configuration fields, already typed
    """
    checks = []
    everything = dict(fields)
    everything.update(request_fields)

    # 1. Required fields: present, single-valued.
    for spec in doctype["fields"]:
        if not spec["required"]:
            continue
        f = everything.get(spec["name"]) or {"status": X.MISSING}
        status = {X.FOUND: "PRESENT"}.get(f.get("status"), f.get("status") or X.MISSING)
        detail = None
        if status == X.MISSING:
            detail = f.get("note") or ("not printed on the document" if spec["source"] == "pdf"
                                       else "not provided for this request" if
                                       spec["source"] == "request" else
                                       "not set in the PO configuration")
        elif status == X.AMBIGUOUS:
            detail = "the document prints more than one value; none is chosen"
        checks.append({"name": "required:" + spec["name"], "label": spec["label"],
                       "pdf": _value_text(f), "hub": None, "status": status,
                       "blocking": status != "PRESENT", "detail": detail,
                       "source": spec["source"]})

    # 2. Against the Hub record.
    for spec in doctype["hub_checks"]:
        f = fields.get(spec["pdf"]) or {}
        pdf_value = f.get("value") if f.get("status") == X.FOUND else None
        hub_value = (hub or {}).get(spec["hub"])
        if pdf_value is None or not hub_value:
            status, detail = "NOT_CHECKED", ("the document has no single value to compare"
                                             if pdf_value is None else
                                             "the Hub record has no value to compare")
            blocking = True
        else:
            same = (X.normal_reference(pdf_value) == X.normal_reference(hub_value)
                    if spec["compare"] == "reference" else str(pdf_value) == str(hub_value))
            status = "MATCH" if same else "MISMATCH"
            blocking = not same
            detail = None if same else "the document belongs to a different {0}".format(
                spec["label"])
        checks.append({"name": "hub:" + spec["name"], "label": spec["label"],
                       "pdf": _value_text(f) if f else None, "hub": hub_value,
                       "status": status, "blocking": blocking, "detail": detail,
                       "source": "hub"})

    # 3. The document's own arithmetic: total duty − VAT block = import duty line.
    duty = fields.get("duty_amount_ghs") or {}
    vat = fields.get("vat_lines") or {}
    stated = fields.get("stated_import_duty") or {}
    if duty.get("status") == X.FOUND and vat.get("status") == X.FOUND:
        derived = round(duty["value"] - sum(l["amount"] for l in vat["value"]), 2)
        if stated.get("status") == X.FOUND:
            variance = round(derived - stated["value"], 2)
            ok = abs(variance) <= doctype.get("duty_tolerance", 1.0)
            checks.append({"name": "arithmetic:duty", "label": "Duty and levies cross-check",
                           "pdf": derived, "hub": None,
                           "status": "OK" if ok else "FAILED", "blocking": not ok,
                           "detail": "total duty {0:,.2f} − VAT block {1:,.2f} = {2:,.2f}; the "
                                     "document's import duty line is {3:,.2f} (variance {4:,.2f})"
                           .format(duty["value"], duty["value"] - derived, derived,
                                   stated["value"], variance),
                           "source": "pdf"})
        else:
            checks.append({"name": "arithmetic:duty", "label": "Duty and levies cross-check",
                           "pdf": derived, "hub": None, "status": "NOT_CHECKED",
                           "blocking": False,
                           "detail": "the document prints no separate import duty line",
                           "source": "pdf"})

    # 4. Amounts must be positive.
    for name in ("cif_usd", "exchange_rate", "duty_amount_ghs"):
        f = fields.get(name) or {}
        if f.get("status") == X.FOUND and not (f["value"] > 0):
            checks.append({"name": "positive:" + name, "label": f.get("label"), "pdf": f["value"],
                           "hub": None, "status": "FAILED", "blocking": True,
                           "detail": "must be greater than zero", "source": "pdf"})

    failures = [c for c in checks if c["blocking"]]
    return {"passed": not failures, "checks": checks,
            "reasons": [reason(c) for c in failures]}


def reason(check):
    """One sentence a person reads: what blocked, with both sides when there are two."""
    if check["status"] == "MISMATCH":
        return "{0} mismatch — PDF: {1}, Hub: {2}".format(check["label"], check["pdf"],
                                                          check["hub"])
    if check["status"] == "AMBIGUOUS":
        return "{0} is ambiguous — the document prints {1}".format(check["label"], check["pdf"])
    if check["status"] == "MISSING":
        return "{0} is missing — {1}".format(check["label"], check["detail"])
    if check["status"] == "NOT_CHECKED":
        return "{0} could not be checked — {1}".format(check["label"], check["detail"])
    return "{0} failed — {1}".format(check["label"], check["detail"])
