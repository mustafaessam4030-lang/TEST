"""
Fill the approved template from validated fields — and prove it.

    fill(doctype, values, output_dir, number, reference)
        -> {"path", "filename", "sha256", "bytes", "template_version",
            "template_sha256", "cells": {cell: value}, "verified": True}

Only the type's MAPPING decides what goes where; only its input cells are
written; its formulas are left to compute. The approved template is
checked against its manifest first: a template that was changed outside
the build step is refused, not used.

The output is created exclusively (never overwrites) under a deterministic
name, then OPENED AGAIN and every mapped cell read back. "Template
generated" means that read-back matched — not that save() returned.
"""

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path

from . import doctypes


class TemplateError(Exception):
    pass


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def manifest(doctype):
    path = doctypes.TEMPLATES / (doctype["template"]["version"] + ".manifest.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise TemplateError("the template manifest is missing: {0}".format(error))


def approved_template(doctype):
    """(bytes, manifest) of the approved template, verified against its manifest."""
    m = manifest(doctype)
    path = doctypes.template_path(doctype)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise TemplateError("the approved template is missing: {0}".format(error))
    if _sha256_bytes(data) != m["sha256"]:
        raise TemplateError("the approved template {0} does not match its manifest — it was "
                            "changed outside the build step, so it is not used".format(
                                m["version"]))
    return data, m


def vat_formula(lines):
    """The template's own style for G20: =304446.44+1066.03+..."""
    return "=" + "+".join("{0:.2f}".format(l["amount"]) for l in lines) if lines else None


def cell_value(field, value):
    if value is None:
        return None
    if field == "vat_lines":
        return vat_formula(value)
    if field == "document_date":
        return datetime.combine(date.fromisoformat(value), datetime.min.time())
    if field == "invoice_no" and str(value).strip().isdigit():
        return int(str(value).strip())
    return value


def safe_part(text, limit=40):
    out = "".join(c if c.isalnum() else "-" for c in str(text or ""))
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:limit] or "NA"


def output_name(doctype, number, reference, stamp, job=None):
    return doctype["output_name"].format(number=safe_part(number), reference=safe_part(reference),
                                         stamp=stamp, job=safe_part(str(job or "")[-6:], 6))


def fill(doctype, values, output_dir, number, reference, stamp=None, job=None):
    """build() then save() — for callers that want both in one step."""
    return save(doctype, build(doctype, values), output_dir, number, reference, stamp, job)


def _read_back(doctype, sheet, written):
    """Every mapped cell and every template formula, as the workbook holds them."""
    spec = doctype["template"]
    mismatched = []
    for ref, value in written.items():
        got = sheet[ref].value
        if isinstance(value, datetime):
            ok = isinstance(got, datetime) and got.date() == value.date()
        else:
            ok = got == value
        if not ok:
            mismatched.append("{0}: wrote {1!r}, read {2!r}".format(ref, value, got))
    for ref, formula in spec["formulas"].items():
        if sheet[ref].value != formula:
            mismatched.append("{0}: the template formula {1} was not kept".format(ref, formula))
    return mismatched


def build(doctype, values, trace=None):
    """
    TEMPLATE_GENERATED: the approved template filled with the validated values,
    serialised, then re-opened from those bytes and every mapped cell and
    template formula read back. Nothing is written to disk here.
    -> {"payload", "written", "manifest", "cells"}
    """
    from io import BytesIO
    from openpyxl import load_workbook
    data, m = approved_template(doctype)
    spec = doctype["template"]
    wb = load_workbook(BytesIO(data))
    if spec["sheet"] not in wb.sheetnames:
        raise TemplateError("the template has no sheet {0!r}".format(spec["sheet"]))
    ws = wb[spec["sheet"]]
    for ref in spec["input_cells"]:
        ws[ref] = None
    written = {}
    for row in doctype["mapping"]:
        value = cell_value(row["field"], values.get(row["field"]))
        if value is None:
            continue
        if row["cell"] not in spec["input_cells"]:
            raise TemplateError("mapping writes {0}, which is not an input cell".format(row["cell"]))
        ws[row["cell"]] = value
        written[row["cell"]] = value
    # Provenance, outside the printed area (the earlier automation used I2:I4).
    ws["I2"] = "Generated by ATA PO Automation · template {0} · {1}".format(
        m["version"], datetime.now().isoformat(timespec="seconds"))
    ws["I3"] = "Verify every figure against the attached declaration before release."
    # Traceability inside the file itself: which job, which shipment, which
    # source document (its SHA-256) produced it.
    if trace:
        ws["I4"] = "Job {0} · shipment {1} · Bill of Entry sha256 {2}".format(
            trace.get("job"), trace.get("reference"), str(trace.get("document") or "")[:16])

    # No unexpected blank: every required field's cell must have been written.
    required = {f["name"] for f in doctype["fields"] if f.get("required")}
    blank = [row["cell"] for row in doctype["mapping"]
             if row["field"] in required and row["cell"] not in written]
    if blank:
        raise TemplateError("required cells would be blank: {0}".format(", ".join(blank)))

    buffer = BytesIO()
    wb.save(buffer)
    payload = buffer.getvalue()
    mismatched = _read_back(doctype, load_workbook(BytesIO(payload))[spec["sheet"]], written)
    if mismatched:
        raise TemplateError("the generated document did not read back: " +
                            "; ".join(mismatched[:4]))
    return {"payload": payload, "written": written, "manifest": m,
            "cells": {k: (v.date().isoformat() if isinstance(v, datetime) else v)
                      for k, v in written.items()},
            "sha256": _sha256_bytes(payload), "bytes": len(payload),
            "template_version": m["version"], "template_sha256": m["sha256"]}


def _publish(tmp, final):
    """Make `tmp` visible as `final` atomically, never replacing an existing file."""
    if os.name == "nt":
        os.rename(str(tmp), str(final))           # fails if `final` exists
        return
    os.link(str(tmp), str(final))                 # fails if `final` exists
    os.unlink(str(tmp))


def save(doctype, built, output_dir, number, reference, stamp=None, job=None):
    """
    OUTPUT_PERSISTED: the generated document written to the output folder
    atomically — to a temporary file first, flushed to disk, then published
    under its final name in one step that never replaces an existing file —
    then OPENED FROM DISK and read back. A crash can leave a temporary file,
    never a partial or overwritten output.
    """
    import uuid
    from openpyxl import load_workbook
    spec, m, written = doctype["template"], built["manifest"], built["written"]
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    name = output_name(doctype, number, reference, stamp, job)
    payload = built["payload"]
    tmp = out_dir / ".{0}.{1}.partial".format(name, uuid.uuid4().hex[:8])
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    # A second save of the same job in the same second (a resume) takes the
    # next free name, "-2", "-3"…, rather than failing or replacing the first.
    stem, suffix = os.path.splitext(name)
    try:
        for n in range(1, 50):
            candidate = name if n == 1 else "{0}-{1}{2}".format(stem, n, suffix)
            path = out_dir / candidate
            try:
                _publish(tmp, path)
                name = candidate
                break
            except FileExistsError:
                continue
        else:
            raise TemplateError("an output named {0} already exists; it is not overwritten"
                                .format(name))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    if os.name != "nt":
        try:
            fd = os.open(str(out_dir), os.O_RDONLY)
            os.fsync(fd)
            os.close(fd)
        except OSError:
            pass

    # PROOF: open what is on disk and read every mapped cell back.
    mismatched = _read_back(doctype, load_workbook(str(path))[spec["sheet"]], written)
    if mismatched:
        raise TemplateError("the saved output did not read back: " + "; ".join(mismatched[:4]))
    disk = path.read_bytes()
    if _sha256_bytes(disk) != built["sha256"]:
        raise TemplateError("the saved output's bytes differ from what was generated")
    return {"path": str(path.resolve()), "filename": name, "folder": str(out_dir.resolve()),
            "created_epoch": round(path.stat().st_mtime, 3), "job": job,
            "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "sha256": _sha256_bytes(disk),
            "bytes": len(disk), "template_version": m["version"],
            "template_sha256": m["sha256"],
            "cells": {k: (v.date().isoformat() if isinstance(v, datetime) else v)
                      for k, v in written.items()},
            "verified": True}


def verify_output(record):
    """
    Before an email: the saved file re-opened from disk is THIS job's document —
    every cell written from the validated values reads back the same, every
    template formula is intact, and its trace cell names this job. [] when it is.
    """
    from openpyxl import load_workbook
    out = record.get("output") or {}
    doctype = doctypes.get(record["doctype"])
    try:
        sheet = load_workbook(out["path"])[doctype["template"]["sheet"]]
    except Exception as error:
        return ["the output could not be re-opened: {0}".format(str(error)[:120])]
    reasons = []
    for ref, want in (out.get("cells") or {}).items():
        got = sheet[ref].value
        if want is None:
            continue
        if isinstance(got, datetime):
            got = got.date().isoformat()
        if str(got) != str(want):
            reasons.append("the output's {0} reads {1!r}, not this job's {2!r}".format(ref, got,
                                                                                    want))
    for ref, formula in doctype["template"]["formulas"].items():
        if sheet[ref].value != formula:
            reasons.append("the output's formula {0} is not the template's {1}".format(ref,
                                                                                       formula))
    trace = str(sheet["I4"].value or "")
    if not trace:
        reasons.append("the output carries no job trace (I4): it cannot be tied to this job")
    elif record.get("po_id") and record["po_id"] not in trace:
        reasons.append("the output was generated by another job ({0})".format(trace[:60]))
    return reasons
