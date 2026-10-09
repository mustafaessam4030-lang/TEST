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

THE ONE GATE. validate() returns a single DECISION:

    VALID          every check passed — the only result that may go on to
                   the template
    INVALID        a check failed: a mismatch, a missing / ambiguous /
                   malformed required value, a business rule broken. A
                   precise failure code says which kind (FAILURE_CODES).
    NEEDS_REVIEW   nothing failed, but something is not PROVEN: the supplier
                   invoice No. (G4) has no single explicit printed source, the
                   shipment identity has insufficient evidence, or a value
                   was read by OCR. A person decides; nothing is generated.

with the failed checks, the affected fields, the evidence, and what to do
(remediation). Cross-validation is kept as COMPARISONS — {field, source_a,
value_a, source_b, value_b, result: MATCH | MISMATCH | NOT_AVAILABLE |
NOT_COMPARABLE, evidence} — and the calculations as a TRACE: rule id,
inputs, formula, intermediate values, rounding, result — in exact decimal
arithmetic, so the same inputs always give the same outputs.
"""

from decimal import ROUND_HALF_UP, Decimal

from . import extract as X

BLOCKING_FAILURES = ("MISMATCH", "MISSING", "AMBIGUOUS", "FAILED", "MALFORMED")
VALID, INVALID, NEEDS_REVIEW = "VALID", "INVALID", "NEEDS_REVIEW"
# Most specific first: the code a VALIDATION_FAILED job carries.
FAILURE_CODES = ("IDENTITY_MISMATCH", "CROSS_VALIDATION_FAILED", "NORMALIZATION_FAILED",
                 "REQUIRED_FIELDS_FAILED", "BUSINESS_RULE_FAILED")
REVIEW_CODES = ("IDENTITY_UNPROVEN", "G4_SOURCE_UNPROVEN", "LOW_CONFIDENCE_EXTRACTION")
REMEDIATION = {
    "IDENTITY_MISMATCH": "The document or page belongs to another shipment: open the record by "
                         "hand in eHub; nothing is generated.",
    "CROSS_VALIDATION_FAILED": "Compare the mismatched values (PDF and eHub, shown side by "
                               "side) and correct the source in eHub; then run the job again.",
    "NORMALIZATION_FAILED": "A printed value is not a well-formed number: read it on the kept "
                            "PDF and have the document corrected; nothing is guessed.",
    "REQUIRED_FIELDS_FAILED": "A required value is missing or printed more than once with "
                              "different values: read the kept PDF; nothing is chosen for you.",
    "BUSINESS_RULE_FAILED": "The document's own figures do not reconcile (see the calculation "
                            "trace): have the Bill of Entry checked before any payment.",
    "IDENTITY_UNPROVEN": "Neither the Manage page nor the document ties this record to the "
                         "shipment with enough evidence: fetch again or reject.",
    "G4_SOURCE_UNPROVEN": "The Bill of Entry gives no single explicit Invoice No.: choose one of "
                          "its printed values, fetch it again once corrected, or reject.",
    "LOW_CONFIDENCE_EXTRACTION": "A required value was read by OCR: compare it with the PDF and "
                                 "confirm, fetch a text PDF, or reject.",
}


def D(value):
    """An exact decimal of a parsed value (floats come from to_float, 6 places)."""
    return Decimal(str(value))


def _money(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculations(doctype, fields):
    """
    The calculation trace of the approved template's formulas, recomputed
    exactly from the validated inputs. The template computes the same
    figures itself (its formulas are kept, never written); this trace is what
    a reviewer and the read-back compare against. Display rounding: 2 places,
    half up; the comparisons use the exact values.
    """
    f = {k: v for k, v in (fields or {}).items()}

    def val(name):
        x = f.get(name) or {}
        return D(x["value"]) if x.get("status") == X.FOUND and x.get("value") is not None \
            else None
    out = []
    vat = f.get("vat_lines") or {}
    lines = vat.get("value") if vat.get("status") == X.FOUND else None
    g6, c21, c19 = val("duty_amount_ghs"), val("exchange_rate"), val("cif_usd")
    stated = val("stated_import_duty")
    g20 = sum((D(l["amount"]) for l in lines), Decimal("0")) if lines else None
    out.append({"rule": "G20_VAT_BLOCK", "cell": "G20", "formula": "G20 = Σ VAT / levy lines",
                "inputs": {l["label"]: str(D(l["amount"])) for l in lines or []},
                "intermediate": None, "rounding": "none (exact sum)",
                "result": str(g20) if g20 is not None else None})
    g19 = (g6 - g20) if g6 is not None and g20 is not None else None
    out.append({"rule": "G19_IMPORT_DUTY", "cell": "G19", "formula": "G19 = G6 − G20",
                "inputs": {"G6": str(g6) if g6 is not None else None,
                           "G20": str(g20) if g20 is not None else None},
                "intermediate": None, "rounding": "none (exact)",
                "result": str(g19) if g19 is not None else None})
    c24 = (g6 / c21) if g6 is not None and c21 else None
    out.append({"rule": "C24_DUTY_USD", "cell": "C24", "formula": "C24 = G6 ÷ C21",
                "inputs": {"G6": str(g6) if g6 is not None else None,
                           "C21": str(c21) if c21 is not None else None},
                "intermediate": str(c24) if c24 is not None else None,
                "rounding": "2 places, half up (display)",
                "result": str(_money(c24)) if c24 is not None else None})
    c26 = (c24 / c19) if c24 is not None and c19 else None
    out.append({"rule": "C26_DUTY_SHARE", "cell": "C26", "formula": "C26 = C24 ÷ C19",
                "inputs": {"C24": str(c24) if c24 is not None else None,
                           "C19": str(c19) if c19 is not None else None},
                "intermediate": str(c26) if c26 is not None else None,
                "rounding": "4 places, half up (display)",
                "result": str(c26.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
                if c26 is not None else None})
    tolerance = D(doctype.get("duty_tolerance", 1.0))
    tax = f.get("tax_lines") or {}
    taxes = tax.get("value") if tax.get("status") == X.FOUND else None
    if taxes:
        # The ICUMS form: every line of B ACCOUNTING DETAILS adds up to its
        # Total (G6). G19 = G6 − G20 is then the sum of the non-VAT lines.
        summed = sum((D(l["amount"]) for l in taxes), Decimal("0"))
        variance = (summed - g6) if g6 is not None else None
        out.append({"rule": "XCHECK_TAX_TOTAL", "cell": "G6",
                    "formula": "|Σ B accounting lines − Total (G6)| ≤ {0}".format(tolerance),
                    "inputs": {"lines": str(len(taxes)), "sum_of_lines": str(summed),
                               "G6": str(g6) if g6 is not None else None},
                    "intermediate": str(variance) if variance is not None else None,
                    "rounding": "none (exact)",
                    "result": None if variance is None else
                    ("OK" if abs(variance) <= tolerance else "FAILED")})
        return out
    variance = (g19 - stated) if g19 is not None and stated is not None else None
    out.append({"rule": "XCHECK_IMPORT_DUTY", "cell": None,
                "formula": "|(G6 − G20) − printed import duty| ≤ {0}".format(tolerance),
                "inputs": {"G19": str(g19) if g19 is not None else None,
                           "printed_import_duty": str(stated) if stated is not None else None},
                "intermediate": str(variance) if variance is not None else None,
                "rounding": "none (exact)",
                "result": None if variance is None else
                ("OK" if abs(variance) <= tolerance else "FAILED")})
    return out


def _value_text(field):
    if field is None:
        return None
    if field.get("status") == X.AMBIGUOUS:
        return " | ".join(str(c["value"]) for c in field.get("candidates") or [])
    value = field.get("value")
    if isinstance(value, list):
        return "{0} line(s)".format(len(value))
    return value


def validate(doctype, fields, hub, request_fields, identity=None, g4_issue=None,
             ocr_confirmed=False):
    """
    fields          the PDF fields (extract.extract)
    hub             the Hub record the document was found on ({"bol_awb": ...})
    request_fields  configuration fields and G4 (decided from the Bill of Entry)
    identity        the shipment identity decision ({"decision": MATCH | MISMATCH |
                    INSUFFICIENT_EVIDENCE, ...})
    g4_issue        why G4 has no proven source, or None
    ocr_confirmed   a person confirmed OCR-read values against the PDF at review
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
        elif status == X.MALFORMED:
            detail = f.get("note") or "printed but not a well-formed value"
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
            blocking = not (spec.get("optional") and pdf_value is not None)
        else:
            if spec["compare"] == "reference":
                same = X.normal_reference(pdf_value) == X.normal_reference(hub_value)
            elif spec["compare"] == "declaration":
                # "40726534505" or "40726534505-00" is the declaration
                # "40726534505 / 00": the same digits, the suffix optional.
                whole = X.normal_reference(pdf_value)
                base = X.normal_reference(str(pdf_value).split("/")[0])
                same = X.normal_reference(hub_value) in (whole, base)
            else:
                same = str(pdf_value) == str(hub_value)
            status = "MATCH" if same else "MISMATCH"
            blocking = not same
            detail = None if same else (
                "eHub's Bill Entry document names a different declaration than the PDF prints"
                if spec["compare"] == "declaration" else
                "the document belongs to a different {0}".format(spec["label"]))
        checks.append({"name": "hub:" + spec["name"], "label": spec["label"],
                       "pdf": _value_text(f) if f else None, "hub": hub_value,
                       "status": status, "blocking": blocking, "detail": detail,
                       "source": "hub"})

    # 2b. Whose Manage page was read: shown, never blocking on its own — the
    # BL/AWB check above is what proves the document is this shipment's.
    if hub and "identity_on_manage" in hub:
        on = hub.get("identity_on_manage")
        checks.append({"name": "identity:manage", "label": "BOL/AWB shown on the Manage page",
                       "pdf": None, "hub": hub.get("bol_awb"),
                       "status": "MATCH" if on else "NOT_CHECKED", "blocking": False,
                       "detail": None if on else "the Manage page does not print the BOL/AWB; "
                       "the record is tied to this row by the BL/AWB the PDF prints",
                       "source": "hub"})

    # 3. The document's own arithmetic. On the ICUMS form: the lines of B
    # ACCOUNTING DETAILS add up to its Total. (Its "Import Duty" line is one
    # tax among many — total duty − VAT block is NOT that line.) Without the
    # table: total duty − VAT block = the import duty line printed.
    duty = fields.get("duty_amount_ghs") or {}
    vat = fields.get("vat_lines") or {}
    stated = fields.get("stated_import_duty") or {}
    taxes = fields.get("tax_lines") or {}
    tolerance = D(doctype.get("duty_tolerance", 1.0))
    if taxes.get("status") == X.FOUND and duty.get("status") == X.FOUND:
        summed = sum((D(l["amount"]) for l in taxes["value"]), Decimal("0"))
        variance = summed - D(duty["value"])
        ok = abs(variance) <= tolerance
        checks.append({"name": "arithmetic:taxes", "label": "Tax lines add up to the Total",
                       "pdf": float(summed), "hub": None,
                       "status": "OK" if ok else "FAILED", "blocking": not ok,
                       "detail": "the {0} lines of B ACCOUNTING DETAILS add up to {1:,.2f}; the "
                                 "document's Total is {2:,.2f} (variance {3:,.2f})".format(
                                     len(taxes["value"]), float(summed), duty["value"],
                                     float(variance)),
                       "source": "pdf"})
    elif duty.get("status") == X.FOUND and vat.get("status") == X.FOUND:
        exact = D(duty["value"]) - sum((D(l["amount"]) for l in vat["value"]), Decimal("0"))
        derived = float(exact)
        if stated.get("status") == X.FOUND:
            variance = float(exact - D(stated["value"]))
            ok = abs(exact - D(stated["value"])) <= tolerance
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

    # 3b. Currencies (the ICUMS form prints them): C19 is "Amount (USD)", so
    # the invoice must be in USD, and the exchange rate must be for it.
    currency = fields.get("invoice_currency")
    if currency is not None:
        value = currency.get("value") if currency.get("status") == X.FOUND else None
        ok = value == "USD"
        checks.append({"name": "currency:invoice", "label": "Invoice currency (C19 is USD)",
                       "pdf": value, "hub": None, "status": "OK" if ok else "FAILED",
                       "blocking": not ok,
                       "detail": None if ok else (
                           "the invoice currency (box 13, CC) is not printed" if value is None
                           else "the invoice is in {0}; the Duty Template's C19 is in USD"
                           .format(value)),
                       "source": "pdf"})
        rate = fields.get("rate_currency") or {}
        if value and rate.get("status") == X.FOUND and rate["value"] != value:
            checks.append({"name": "currency:rate", "label": "Exchange-rate currency",
                           "pdf": rate["value"], "hub": None, "status": "FAILED",
                           "blocking": True,
                           "detail": "the rate of exchange is for {0}, the invoice is in {1}"
                           .format(rate["value"], value), "source": "pdf"})

    # 4. Amounts must be positive.
    for name in ("cif_usd", "exchange_rate", "duty_amount_ghs"):
        f = fields.get(name) or {}
        if f.get("status") == X.FOUND and not (f["value"] > 0):
            checks.append({"name": "positive:" + name, "label": f.get("label"), "pdf": f["value"],
                           "hub": None, "status": "FAILED", "blocking": True,
                           "detail": "must be greater than zero", "source": "pdf"})

    # 5. Shipment identity: decided from the evidence, never assumed.
    if identity is not None:
        decision = identity.get("decision")
        checks.append({"name": "identity:shipment", "label": "Shipment identity",
                       "pdf": None, "hub": (hub or {}).get("bol_awb"),
                       "status": {"MATCH": "MATCH", "MISMATCH": "MISMATCH"}.get(
                           decision, "NOT_CHECKED"),
                       "blocking": decision != "MATCH",
                       "detail": identity.get("why"), "source": "identity"})

    # 6. G4: only an explicit Invoice No. printed on the Bill of Entry.
    if g4_issue:
        checks.append({"name": "g4:source", "label": "Supplier invoice No. (G4) source",
                       "pdf": " | ".join(g4_issue.get("candidates") or []) or None, "hub": None,
                       "status": "NOT_PROVEN", "blocking": True, "detail": g4_issue["detail"],
                       "source": "pdf"})

    # 7. Confidence: a required value read by OCR is not proven by itself.
    low = [spec["name"] for spec in doctype["fields"]
           if spec["required"] and spec["source"] == "pdf" and
           (fields.get(spec["name"]) or {}).get("confidence") == "LOW"]
    if low and not ocr_confirmed:
        checks.append({"name": "confidence:ocr", "label": "Values read by OCR",
                       "pdf": ", ".join(low), "hub": None, "status": "NOT_PROVEN",
                       "blocking": True, "detail": "read from a scanned page by OCR: {0}".format(
                           ", ".join(low)), "source": "pdf"})

    return decide(doctype, fields, hub, checks)


def comparisons(checks):
    """Cross-validation, one row per compared field."""
    out = []
    for c in checks:
        if not c["name"].startswith(("hub:", "identity:")):
            continue
        result = {"MATCH": "MATCH", "MISMATCH": "MISMATCH"}.get(c["status"])
        if result is None:
            result = "NOT_AVAILABLE" if c["pdf"] is None or c["hub"] is None else "NOT_COMPARABLE"
        out.append({"field": c["label"], "source_a": "Bill of Entry PDF", "value_a": c["pdf"],
                    "source_b": "eHub" if c["name"].startswith("hub:") else "Manage page",
                    "value_b": c["hub"], "result": result, "evidence": c.get("detail")})
    return out


def decide(doctype, fields, hub, checks):
    failures = [c for c in checks if c["blocking"]]
    codes = set()
    review = set()
    for c in failures:
        name, status = c["name"], c["status"]
        if name == "identity:shipment":
            (codes if status == "MISMATCH" else review).add(
                "IDENTITY_MISMATCH" if status == "MISMATCH" else "IDENTITY_UNPROVEN")
        elif name == "g4:source":
            review.add("G4_SOURCE_UNPROVEN")
        elif name == "confidence:ocr":
            review.add("LOW_CONFIDENCE_EXTRACTION")
        elif name == "required:invoice_no" and status == X.MISSING:
            review.add("G4_SOURCE_UNPROVEN")
        elif name.startswith("hub:") and status == "MISMATCH":
            codes.add("CROSS_VALIDATION_FAILED")
        elif name.startswith("hub:"):
            codes.add("CROSS_VALIDATION_FAILED")       # nothing to compare: not proven
        elif status == X.MALFORMED:
            codes.add("NORMALIZATION_FAILED")
        elif name.startswith("required:"):
            codes.add("REQUIRED_FIELDS_FAILED")
        else:
            codes.add("BUSINESS_RULE_FAILED")
    if codes:
        decision = INVALID
        code = next(c for c in FAILURE_CODES if c in codes)
    elif review:
        decision = NEEDS_REVIEW
        code = next(c for c in REVIEW_CODES if c in review)
    else:
        decision, code = VALID, None
    affected = sorted({c["name"].split(":", 1)[1] for c in failures})
    return {"decision": decision, "passed": decision == VALID, "code": code,
            "codes": sorted(codes | review), "checks": checks,
            "failed_checks": [c["name"] for c in failures], "affected_fields": affected,
            "reasons": [reason(c) for c in failures],
            "remediation": [REMEDIATION[c] for c in FAILURE_CODES + REVIEW_CODES
                            if c in codes | review],
            "comparisons": comparisons(checks),
            "calculations": calculations(doctype, fields)}


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
    if check["status"] == X.MALFORMED:
        return "{0} is malformed — {1}".format(check["label"], check["detail"])
    if check["status"] == "NOT_PROVEN":
        return "{0} is not proven — {1}".format(check["label"], check["detail"])
    return "{0} failed — {1}".format(check["label"], check["detail"])
