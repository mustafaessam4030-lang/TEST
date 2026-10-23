"""
PDF reports ATLAS hands out from the chat: the run, the failed shipments, the
review list, or one shipment with its checks and screenshots.

Built with PyMuPDF (already a dependency, for PO PDFs): HTML laid out onto A4
pages. Everything in a report comes from the run snapshot the dashboard shows
(dashboard/bridge.py) and the evidence store; nothing is inferred, and an
empty field stays empty.

    build(kind, state, reference=None) -> (pdf_bytes, filename)
        kind: "run" | "failed" | "review" | "shipment"
"""

import html
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "dashboard" / "static" / "brand" / "mantrac-logo.png"

STATE_LABEL = {
    "updated": "Written to Hub", "partial": "Partly written", "skipped": "Skipped",
    "failed": "Failed", "processing": "Processing", "waiting_for_human": "Waiting for a person",
    "human_timeout": "Human timeout", "queued": "Queued",
}

CSS = """
* { font-family: sans-serif; }
body { font-size: 9.5pt; color: #1F1E1C; }
h1 { font-size: 18pt; margin: 0 0 2pt 0; }
h2 { font-size: 12pt; margin: 14pt 0 5pt 0; color: #1F1E1C; }
p { margin: 0 0 5pt 0; line-height: 1.35; }
.sub { color: #5F5B54; font-size: 9pt; }
.band { background-color: #FFC20E; height: 5pt; }
table { border-collapse: collapse; width: 100%; }
th { background-color: #1F1E1C; color: #FFFFFF; text-align: left; padding: 4pt 5pt; font-size: 8.5pt; }
td { border-bottom: 0.5pt solid #D9D5CC; padding: 3.5pt 5pt; font-size: 8.5pt; vertical-align: top; }
.kpi td { border: none; padding: 2pt 10pt 2pt 0; }
.k { font-size: 16pt; font-weight: bold; }
.lbl { font-size: 8pt; color: #5F5B54; }
.pass { color: #1E7B34; font-weight: bold; }
.fail { color: #B42318; font-weight: bold; }
.info { color: #5F5B54; font-weight: bold; }
.note { color: #5F5B54; font-size: 8pt; margin-top: 10pt; }
"""


def _e(value):
    return html.escape("" if value is None else str(value))


def _has(value):
    return value not in (None, "", "-", "—")


def _header(title, subtitle):
    logo = '<img src="logo.png" width="120" height="30"/>' if LOGO.exists() else ""
    return ('<div class="band"></div><table><tr><td style="border:none;padding:8pt 0 4pt 0">{0}'
            '</td><td style="border:none;text-align:right;padding:8pt 0 4pt 0" class="sub">ATA Control '
            'Tower · ATLAS report</td></tr></table><h1>{1}</h1><p class="sub">{2}</p>').format(
                logo, _e(title), _e(subtitle))


def _table(head, rows):
    out = ["<table><tr>" + "".join("<th>{0}</th>".format(_e(h)) for h in head) + "</tr>"]
    for row in rows:
        out.append("<tr>" + "".join("<td>{0}</td>".format(c) for c in row) + "</tr>")
    out.append("</table>")
    return "".join(out)


def _shipment_rows(records):
    rows = []
    for r in records:
        rows.append([_e(r.get("reference")), _e(r.get("carrier")),
                     _e(STATE_LABEL.get(r.get("state"), r.get("state"))),
                     _e(r.get("provider_eta") if _has(r.get("provider_eta")) else ""),
                     _e(r.get("provider_ata") if _has(r.get("provider_ata")) else ""),
                     _e((r.get("error") or "")[:220])])
    return rows


def _footer_note(state):
    run = (state or {}).get("run") or {}
    return ('<p class="note">Made by ATLAS on {0} from the run records{1}. Every value is as '
            'the run recorded it; an empty cell means the run recorded nothing there. Nothing '
            'in this report was inferred.</p>').format(
                time.strftime("%d/%m/%Y %H:%M"),
                " (run {0})".format(_e(run.get("run_id"))) if run.get("run_id") else "")


def _needs_review(r):
    return "NEEDS REVIEW" in str(r.get("error") or "").upper()


def _run_html(state, kind):
    from dashboard.assistant import RunData
    data = RunData(state)
    run = (state or {}).get("run") or {}
    records = list(reversed(data.shipments))         # oldest first, as processed
    c = data.counters or {}
    title = {"run": "Run report", "failed": "Failed shipments",
             "review": "Shipments for review"}[kind]
    started = str(run.get("started_at") or "").replace("T", " ")[:16]
    subtitle = "Run {0} · started {1} · status {2}".format(
        run.get("run_id") or "—", started or "—", run.get("status") or "—")
    parts = [_header(title, subtitle)]
    parts.append('<table class="kpi"><tr>' + "".join(
        '<td width="16%"><div class="k">{0}</div><div class="lbl">{1}</div></td>'.format(_e(v), _e(l))
        for v, l in (
            (len(records), "Shipments"), (c.get("successful", len(data.updated)), "Written to Hub"),
            (c.get("skipped", len(data.skipped)), "Skipped"), (c.get("failed", len(data.failed)), "Failed"),
            (sum(1 for r in records if _needs_review(r)), "Needs review"),
            (sum(1 for r in records if r.get("state") == "waiting_for_human"), "Waiting for a person"))) +
        "</tr></table>")
    if kind == "failed":
        chosen = [r for r in records if r.get("state") in ("failed", "human_timeout")]
        empty = "No shipment failed in this run."
    elif kind == "review":
        chosen = [r for r in records if _needs_review(r)]
        empty = "No shipment was sent for review in this run."
    else:
        chosen = records
        empty = "No shipment has been processed in this run yet."
    parts.append("<h2>{0}</h2>".format("Shipments" if kind == "run" else title))
    if chosen:
        parts.append(_table(["Reference", "Carrier", "Result", "ETA", "ATA", "Reason / note"],
                            _shipment_rows(chosen)))
    else:
        parts.append("<p>{0}</p>".format(_e(empty)))
    if kind == "run":
        problems = [r for r in records if r.get("state") in ("failed", "human_timeout")
                    or _needs_review(r)]
        if problems:
            parts.append("<h2>Needs attention</h2>")
            parts.append(_table(["Reference", "Carrier", "Result", "ETA", "ATA", "Reason"],
                                _shipment_rows(problems)))
    parts.append(_footer_note(state))
    return "".join(parts), []


def _shipment_html(state, reference, with_images=True):
    from dashboard.assistant import RunData, shipment_card
    from dashboard import atlas_skills
    data = RunData(state)
    record = data.find(reference)
    if record is None:
        return None, []
    ref = record.get("reference")
    parts = [_header("Shipment {0}".format(ref), "{0} · {1}".format(
        record.get("carrier") or "Carrier not recorded",
        STATE_LABEL.get(record.get("state"), record.get("state") or "—")))]
    card = shipment_card(data, record)
    parts.append("<h2>Details</h2>")
    parts.append(_table(["Field", "Value"], [[_e(k), _e(v)] for k, v in card["rows"]]))
    parts.append("<h2>Checks</h2>")
    rows = []
    for status, text in atlas_skills.shipment_checks(record):
        cls = {"PASS": "pass", "FAIL": "fail"}.get(status, "info")
        rows.append(['<span class="{0}">{1}</span>'.format(cls, _e(status)), _e(text)])
    parts.append(_table(["Result", "Check"], rows))
    if _has(record.get("error")):
        parts.append("<h2>Reason recorded</h2><p>{0}</p>".format(_e(record.get("error"))))
    steps = (record.get("steps") or [])[-15:]
    if steps:
        parts.append("<h2>Steps</h2>")
        parts.append(_table(["Time", "Step"], [[_e(s.get("time")), _e(s.get("text"))] for s in steps]))
    images = []
    try:
        if not with_images:
            raise LookupError("screenshots are not for this role")
        from intelligence import evidence as EV
        for entry in EV.search(reference=ref, limit=6):
            if len(images) >= 2:
                break
            _e2, path = EV.file_for(entry.get("id"))
            if path is not None and str(entry.get("mime") or "").startswith("image/"):
                images.append(("shot{0}.png".format(len(images)), path, entry))
    except Exception:
        images = []
    if images:
        parts.append("<h2>Screenshots captured by the run</h2>")
        for name, _path, entry in images:
            parts.append('<p class="sub">{0} · {1}</p><img src="{2}" width="480"/>'.format(
                _e(entry.get("event") or entry.get("source") or "capture"), _e(entry.get("at")), name))
    parts.append(_footer_note(state))
    return "".join(parts), images


def build(kind, state, reference=None, images=True):
    """(pdf_bytes, filename) or (None, reason)."""
    try:
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
    except ImportError:
        return None, "PyMuPDF is not installed on this machine (pip install pymupdf)"
    kind = kind if kind in ("run", "failed", "review", "shipment") else "run"
    if kind == "shipment":
        body, images = _shipment_html(state, reference, with_images=images)
        if body is None:
            return None, "no shipment {0} in this run".format(reference)
    else:
        body, images = _run_html(state, kind)
    archive = pymupdf.Archive()
    if LOGO.exists():
        archive.add(LOGO.read_bytes(), "logo.png")
    for name, path, _entry in images:
        try:
            archive.add(Path(path).read_bytes(), name)
        except OSError:
            body = body.replace('src="{0}"'.format(name), 'src=""')
    import io
    buffer = io.BytesIO()
    story = pymupdf.Story(html="<body>" + body + "</body>", user_css=CSS, archive=archive)
    writer = pymupdf.DocumentWriter(buffer)
    page = pymupdf.paper_rect("a4")
    where = page + (36, 36, -36, -40)
    more = True
    while more:
        device = writer.begin_page(page)
        more, _filled = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    data = buffer.getvalue()
    # Page numbers, added after layout.
    try:
        doc = pymupdf.open("pdf", data)
        for i, p in enumerate(doc):
            p.insert_text((p.rect.width - 90, p.rect.height - 20), "Page {0} of {1}".format(
                i + 1, doc.page_count), fontsize=7.5, color=(0.37, 0.36, 0.33))
        data = doc.tobytes(garbage=3, deflate=True)
    except Exception:
        pass
    stamp = time.strftime("%Y%m%d_%H%M")
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(reference or ""))[:40]
    name = "ATLAS_{0}{1}_{2}.pdf".format(kind, "_" + safe if safe else "", stamp)
    return data, name
