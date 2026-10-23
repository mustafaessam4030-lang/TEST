"""
PO Automation on the REAL eHub — run ON THE WINDOWS WORKER.

    python -m worker.verify po [--reference BOL/AWB] [--invoice-no N] [--email-to you@mantrac]
    (verify_po.bat)

Not a separate code path: it opens eHub the way a run does (the run's
URL, credentials file, headed Edge and sign-in) and then runs the PO
pipeline itself (po.pipeline.process with po.ehub.EHubSource) on ONE real
record, as a real job in the PO store. Each step below is then read off that
job's own record — nothing is assumed:

    A  eHub reachable         the run's sign-in, the page on the eHub host
    B  Under Clearance row    a row of the BU list, status read off the row
    C  Manage opened          the row's Manage page, its URL
    D  Bill Entry found       the document named "Bill Entry…", its number
    E  PDF retrieved          downloaded from eHub: bytes, SHA-256, pages
    F  extraction             the duty request rules on that PDF's text
    G  validation             PDF vs. the eHub record (BOL/AWB, Bill Entry
                              number) and the document's own arithmetic
    H  template generated     the approved template filled, read back
    I  output saved           in the configured output folder, read back
    J  Graph email            ONLY with --email-to: the generated document
                              sent to that address as a controlled test,
                              accepted (202) and found in Sent Items. The
                              business recipient is never emailed from here.

The job stays in the PO store as what it is — a real job, EMAIL READY or
stopped where it stopped — for the dashboard and ATLAS. Nothing is written
to eHub; credentials and Graph secrets are never read out.
"""

import os
from pathlib import Path

from intelligence import verification as V

STEPS = (("A", "eHub reachable from this worker"), ("B", "Under Clearance row located"),
         ("C", "Manage opened"), ("D", "Bill Entry document found"),
         ("E", "PDF retrieved and opened"), ("F", "Fields extracted (duty request rules)"),
         ("G", "Validated against eHub data"), ("H", "Template generated"),
         ("I", "Output saved"), ("J", "Microsoft Graph email (controlled test)"))


def _step(obs, key, status, evidence=None, reason=None):
    obs["po_steps"][key] = {"step": key, "label": dict(STEPS)[key], "status": status,
                            "evidence": evidence, "reason": reason}


def _from_record(obs, record):
    """B..I, read off the job record the pipeline wrote."""
    from po import evidence as E
    stages = {s["stage"]: s for s in E.stages(record)}

    def put(key, names, need_all=True):
        picked = [stages[n] for n in names]
        if all(s["status"] == "OK" for s in picked):
            _step(obs, key, "OK", {n: stages[n]["evidence"] for n in names})
        elif any(s["status"] in ("FAILED", "STOPPED", "BLOCKED") for s in picked):
            bad = next(s for s in picked if s["status"] in ("FAILED", "STOPPED", "BLOCKED"))
            _step(obs, key, bad["status"], bad["evidence"],
                  "{0}: {1}".format(bad["label"], (record.get("failure") or {}).get("detail")
                                    or bad["status"].lower()))
        else:
            _step(obs, key, "NOT_RUN", None, "not reached: the job stopped at {0}".format(
                record["state"]))

    put("B", ("ehub_record", "clearance"))
    put("C", ("manage",))
    put("D", ("bill_entry", "identifier"))
    put("E", ("pdf",))
    put("F", ("fields",))
    put("G", ("validation",))
    put("H", ("template",))
    put("I", ("output",))
    out = record.get("output") or {}
    if obs["po_steps"]["I"]["status"] == "OK" and not (out.get("path") and
                                                       Path(out["path"]).exists()):
        _step(obs, "I", "FAILED", {"path": out.get("path")},
              "the job records an output, but the file is not on disk")


def controlled_email(obs, record, email_to, mailer=None, wait_s=None):
    """J: the generated document to ONE named address, as a test. Never the job's email."""
    from po import mail as M
    out = record.get("output") or {}
    if not email_to:
        _step(obs, "J", "NOT_RUN", None, "a controlled send needs --email-to <your address>; "
                                         "the business recipient is never emailed from here")
        return
    if obs["po_steps"]["I"]["status"] != "OK":
        _step(obs, "J", "NOT_RUN", None, "no saved output to send (step I did not pass)")
        return
    if mailer is None:
        ok, missing = M.configured()
        if not ok:
            _step(obs, "J", "BLOCKED", {"missing_settings": missing},
                  "Microsoft Graph is not configured on this worker: {0} not set".format(
                      ", ".join(missing)))
            return
        mailer = M.GraphMailer()
    subject = "[ATA PO VERIFICATION — TEST, not a duty request] {0} {1}".format(
        record.get("number") or "", record.get("reference") or "").strip()
    try:
        created = mailer.create(email_to, subject,
                                "Controlled test of the PO Automation email path from the "
                                "Windows worker. Not a duty request; do not act on it.",
                                out["filename"], Path(out["path"]).read_bytes())
        accepted = mailer.send(created["message_id"])
        found = mailer.confirm(created["internet_message_id"],
                               wait_s=wait_s if wait_s is not None else
                               float(os.environ.get("PO_CONFIRM_WAIT_S") or 45))
    except M.MailError as error:
        _step(obs, "J", "FAILED", {"step": error.step, "http_status": error.status},
              "Graph: {0}".format(str(error)[:200]))
        return
    except Exception as error:
        _step(obs, "J", "FAILED", None, "{0}: {1}".format(type(error).__name__, str(error)[:200]))
        return
    evidence = {"to": email_to, "subject": subject, "attachment": out["filename"],
                "create_http": created.get("http_status"), "send_http": accepted.get("http_status"),
                "internet_message_id": created.get("internet_message_id"),
                "sent_items": bool(found), "sent_at": (found or {}).get("sentDateTime")}
    if found:
        _step(obs, "J", "OK", evidence)
    else:
        _step(obs, "J", "STOPPED", evidence, "Graph accepted the send (202) but it was not "
                                             "found in Sent Items within the wait")


def run(reference=None, invoice_no=None, email_to=None, credentials=None, launch=None,
        store=None, mailer=None, source_factory=None, log=print):
    """The observation (kind po-pipeline)."""
    from worker import verify as W
    from po import doctypes, ehub as EH, pipeline as P, store as S
    store = store or S.Store()
    holder = {}

    def po_pipeline(page, obs):
        _step(obs, "A", "OK", {"page": obs.get("page"), "browser": obs.get("browser")})
        skip = ()
        if not reference:
            try:
                from po import service as PS
                skip = PS.PoService(store=store).handled_references()
            except Exception:
                skip = ()
        request = {"invoice_no": invoice_no, "started_by": "worker verification",
                   "skip_references": list(skip)}
        record = store.create(doctypes.DEFAULT, reference or None, request,
                              started_by="worker verification")
        source = (source_factory or (lambda pg: EH.EHubSource(
            pg, EH.find_in_ehub, EH.open_manage_in_ehub, skip=skip)))(page)
        try:
            record = P.process(store, record, source, P.config_from_env())
        except Exception as error:
            record = store.get(record["po_id"]) or record
            P.abandon(store, record, "the verification stopped: {0}".format(str(error)[:200]))
            record = store.get(record["po_id"]) or record
        holder["record"] = record

    obs = W.observe_ehub(reference, credentials=credentials, launch=launch, log=log,
                         continue_with=po_pipeline, kind="po-pipeline", downloads=True)
    obs.setdefault("po_steps", {})
    if "A" not in obs["po_steps"]:
        _step(obs, "A", "BLOCKED", None, obs.get("blocked_reason") or "eHub was not opened")
    record = holder.get("record")
    if record is not None:
        _from_record(obs, record)
        controlled_email(obs, record, email_to, mailer)
        # The controlled send's result, kept on the job itself (the readiness
        # gate reads it): what Graph answered and whether Sent Items had it.
        j = obs["po_steps"].get("J") or {}
        if j.get("status") not in (None, "NOT_RUN"):
            current = store.get(record["po_id"]) or record
            current["controlled_email"] = {"status": j.get("status"),
                                           "evidence": j.get("evidence"),
                                           "reason": j.get("reason"), "at": S.now_iso()}
            store.save(current)
        obs["po_job"] = {"po_id": record["po_id"], "state": record["state"],
                         "reference": record.get("reference"), "number": record.get("number"),
                         "identifier": record.get("identifier"),
                         "provenance": record.get("provenance"),
                         "document": {k: (record.get("document") or {}).get(k) for k in
                                      ("filename", "bytes", "sha256", "retrieved_at", "evidence")},
                         "output": {k: (record.get("output") or {}).get(k) for k in
                                    ("filename", "folder", "path", "sha256", "saved_at")},
                         "failure": (record.get("failure") or {}).get("detail")}
        obs["shipment"] = {"reference": record.get("reference"),
                           "status": ((record.get("discovery") or {}).get("clearance") or {})
                           .get("found")}
        obs["provenance"] = record.get("provenance")
    for key, _label in STEPS:
        if key not in obs["po_steps"]:
            _step(obs, key, "NOT_RUN", None, "not reached: {0}".format(
                obs.get("blocked_reason") or "an earlier step did not pass"))
    if obs.get("result") != "BLOCKED":
        obs["result"] = "COMPLETED"
    return obs
