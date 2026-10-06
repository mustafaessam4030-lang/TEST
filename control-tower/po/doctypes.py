"""
Document types the PO module processes — one explicit definition each.

A document type says, in one place:

    what the source document is and how its fields are read  (SCHEMA)
    which of those fields are required before anything is sent (required)
    which values come from the operator or configuration, never the PDF
    which checks against the Hub must pass                     (HUB_CHECKS)
    the approved template, its version and fingerprint          (TEMPLATE)
    exactly which template cell each field fills               (MAPPING)

Nothing outside this table decides what goes into a template cell. The
extraction never writes into a template; the mapper reads only validated
fields named here.

Version 1 ships ONE type, DUTY_REQUEST_V1: the customs declaration (Bill of
Entry) attached to a Hub shipment, filled into Mantrac Ghana's approved
"Duty Payment Request" cheque-request workbook. Its field rules are the ones
the earlier BOE automation (boe_to_duty_request.py) used against ICUMS
wording. Further types (a Purchase Order template, other forms) are added
here as new entries; nothing else changes.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"

# Where a field's value may come from. A PDF field is read from the document
# and nothing else; a REQUEST field is typed by the operator when they start
# the job; a CONFIG field comes from the module's configuration. None is ever
# guessed.
PDF, REQUEST, CONFIG = "pdf", "request", "config"


DUTY_REQUEST_V1 = {
    "id": "DUTY_REQUEST_V1",
    "label": "Duty Payment Request (from the BOE)",
    "document": "Bill of Entry (customs declaration) attached to the Hub shipment",
    "number_field": "document_number",
    "number_label": "Declaration No.",
    # The document's own fields, in the order the drawer shows them.
    "fields": [
        {"name": "document_number", "label": "Declaration (BOE) No.", "source": PDF,
         "required": True, "kind": "text"},
        {"name": "bl_awb", "label": "BL / AWB No.", "source": PDF, "required": True,
         "kind": "reference"},
        {"name": "document_date", "label": "Declaration date", "source": PDF,
         "required": True, "kind": "date"},
        {"name": "user_reference", "label": "User reference", "source": PDF,
         "required": False, "kind": "text"},
        {"name": "cif_usd", "label": "Total invoice value CFR/CIF (USD)", "source": PDF,
         "required": True, "kind": "amount"},
        {"name": "exchange_rate", "label": "Exchange rate", "source": PDF, "required": True,
         "kind": "amount"},
        {"name": "duty_amount_ghs", "label": "Total duty (GHS)", "source": PDF,
         "required": True, "kind": "amount"},
        {"name": "stated_import_duty", "label": "Import duty line (GHS)", "source": PDF,
         "required": False, "kind": "amount"},
        {"name": "vat_lines", "label": "VAT / levy lines", "source": PDF, "required": True,
         "kind": "lines"},
        # Typed by the operator for this request: the BOE does not print it.
        {"name": "invoice_no", "label": "Supplier invoice No.", "source": REQUEST,
         "required": True, "kind": "text"},
        {"name": "supplier", "label": "Supplier", "source": CONFIG, "required": True,
         "kind": "text", "request_overrides": True},
        {"name": "branch", "label": "Receiving branch", "source": CONFIG, "required": False,
         "kind": "text", "request_overrides": True},
        {"name": "charge_to", "label": "Charge to", "source": CONFIG, "required": False,
         "kind": "text", "request_overrides": True},
        {"name": "priority", "label": "Priority", "source": CONFIG, "required": False,
         "kind": "text", "request_overrides": True},
    ],
    # Checks the document must pass against the Hub record it was found on.
    "hub_checks": [
        {"name": "bl_awb", "label": "BL / AWB", "pdf": "bl_awb", "hub": "bol_awb",
         "compare": "reference"},
        # The identifier eHub's own document name carries ("Bill Entry
        # 40726534505.pdf") must be the declaration the PDF itself prints.
        {"name": "identifier", "label": "Bill Entry No. (eHub) vs Declaration No. (PDF)",
         "pdf": "document_number", "hub": "identifier", "compare": "declaration"},
    ],
    # The document's own arithmetic: total duty less the VAT block must equal
    # the import-duty line it prints, within this tolerance (GHS).
    "duty_tolerance": 1.00,
    "template": {
        "version": "DUTY_REQUEST_V1",
        "file": "DUTY_REQUEST_V1.xlsx",
        "sheet": "Duty Template",
        "source_file": "source/Ghana_Duty_Payment_-_CHEQUE_REQUEST_TEMPLATE.xlsx",
        "keep_sheets": ["Duty Template", "BOE Template Capture"],
        # Every cell the operator would otherwise type. Cleared before
        # filling, so a missing value shows blank — never an earlier figure.
        "input_cells": ["D2", "G4", "G6", "G8", "G10", "G11", "G13", "C9", "C13", "C19",
                        "C21", "G20"],
        # Formulas the template computes itself; never written.
        "formulas": {"G19": "=G6-G20", "C24": "=G6/C21", "C26": "=C24/C19",
                     "G41": "=SUM(G19:G40)", "F16": "=G4"},
    },
    # field -> template cell. The ONLY route from data to the document.
    "mapping": [
        {"field": "request_reference", "cell": "D2", "placeholder": "{{REFERENCE}}"},
        {"field": "priority", "cell": "C9", "placeholder": "{{PRIORITY}}"},
        {"field": "invoice_no", "cell": "G4", "placeholder": "{{INVOICE_NO}}"},
        {"field": "duty_amount_ghs", "cell": "G6", "placeholder": "{{DUTY_AMOUNT}}"},
        {"field": "document_date", "cell": "G8", "placeholder": "{{DATE}}"},
        {"field": "supplier", "cell": "G10", "placeholder": "{{SUPPLIER}}"},
        {"field": "branch", "cell": "G11", "placeholder": "{{BRANCH}}"},
        {"field": "charge_to", "cell": "G13", "placeholder": "{{CHARGE_TO}}"},
        {"field": "document_number", "cell": "C13", "placeholder": "{{DECLARATION_NO}}"},
        {"field": "cif_usd", "cell": "C19", "placeholder": "{{CIF_USD}}"},
        {"field": "exchange_rate", "cell": "C21", "placeholder": "{{EXCHANGE_RATE}}"},
        {"field": "vat_lines", "cell": "G20", "placeholder": "{{VAT_FORMULA}}"},
    ],
    # Deterministic from the job: the same job always gets the same name, two
    # jobs never share one (the job id ends it), and nothing is overwritten.
    "output_name": "DUTY_REQUEST_{number}_{reference}_{stamp}_{job}.xlsx",
    "email_subject": "Duty Payment Request — {number} — {reference}",
}

DOCTYPES = {DUTY_REQUEST_V1["id"]: DUTY_REQUEST_V1}
DEFAULT = DUTY_REQUEST_V1["id"]


def get(doctype_id=None):
    doctype = DOCTYPES.get(doctype_id or DEFAULT)
    if doctype is None:
        raise KeyError("unknown document type: {0}".format(doctype_id))
    return doctype


def field(doctype, name):
    return next((f for f in doctype["fields"] if f["name"] == name), None)


def template_path(doctype):
    return TEMPLATES / doctype["template"]["file"]
