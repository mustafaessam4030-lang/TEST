"""
PO Automation from the command line — what the worker and the local
dashboard run in the background, and the proof tools.

    python -m po process --po-id ID        run one queued job:
                                           eHub → Under Clearance → Manage →
                                           Documents → Bill Entry → identifier →
                                           PDF → … → EMAIL READY
    python -m po send --po-id ID           send a prepared job through Graph
    python -m po ehub-probe [--reference BOL/AWB]
                                           THE PROOF, on the real eHub, read-only:
                                           finds an Under Clearance record (or
                                           the one named), opens Manage, reads
                                           the Documents section, picks the
                                           Bill Entry document, extracts its
                                           identifier, downloads and reads the
                                           PDF — and writes a report. Nothing
                                           is written to eHub, nothing is sent.
    python -m po read FILE.pdf [--hub-bol X] [--identifier N] [--invoice-no N]
                                           extraction and validation of a PDF
                                           on disk — nothing generated or sent

A job runs in its own process and its own browser, beside (never inside)
the ETA run. eHub is opened with the automation's own credentials file and
sign-in; nothing here stores or prints a credential.
"""

import argparse
import hashlib
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from po import doctypes, extract as X, pipeline as P, store as S, validate as V  # noqa: E402


def _browser(playwright):
    import update_eta as A
    username, password = A.load_credentials()
    headless = os.environ.get("PO_HEADLESS", "1").strip().lower() not in ("0", "false", "no")
    launch = {"headless": headless}
    if os.environ.get("PO_BROWSER_CHANNEL", "msedge"):
        launch["channel"] = os.environ.get("PO_BROWSER_CHANNEL", "msedge")
    browser = playwright.chromium.launch(**launch)
    context = browser.new_context(http_credentials={"username": username, "password": password},
                                  accept_downloads=True)
    page = context.new_page()
    A.login_internal(page, username, password)
    return browser, page


def _source(page, record):
    from po.ehub import EHubSource, find_in_ehub, open_manage_in_ehub
    return EHubSource(page, find_in_ehub, open_manage_in_ehub,
                      skip=(record.get("request") or {}).get("skip_references") or ())


def cmd_process(args):
    store = S.Store()
    record = store.get(args.po_id)
    if record is None:
        print("no such job: {0}".format(args.po_id), file=sys.stderr)
        return 2
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            try:
                browser, page = _browser(playwright)
            except Exception as error:
                record = store.transition(record, S.DISCOVERED, "Signing in to eHub…")
                record = store.transition(record, S.PDF_NOT_FOUND, "eHub could not be opened",
                                          P.failure("NAVIGATION_FAILURE", "pdf_retrieval",
                                                    "eHub could not be opened: {0}".format(
                                                        str(error)[:200])))
                store.event(record, "PDF_NOT_FOUND", "pdf_retrieval", "FAILED",
                            reason=str(error)[:200])
                return 1
            try:
                record = P.process(store, record, _source(page, record))
            finally:
                browser.close()
        config = P.config_from_env()
        if record["state"] == S.EMAIL_PREPARED and config["auto_send"]:
            from po.mail import GraphMailer
            P.send(store, record, GraphMailer(), by="auto-send",
                   confirm_wait_s=config["confirm_wait_s"])
        return 0
    except Exception as error:
        traceback.print_exc()
        current = store.get(args.po_id) or record
        P.abandon(store, current, "the job stopped unexpectedly: {0}".format(str(error)[:200]))
        return 1


def probe(page, reference=None, out_dir=None, source=None):
    """
    Steps 1–7 against eHub, read-only, and a report that proves each one.
    Returns the report (a dict) — written to <out_dir>/probe-<stamp>.json with
    the downloaded PDF beside it.
    """
    from po.ehub import EHubSource, find_in_ehub, open_manage_in_ehub
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = {"probe": "ehub", "started": datetime.now().astimezone().isoformat(timespec="seconds"),
              "reference_asked": reference or None, "writes_to_ehub": False,
              "sends_email": False}
    source = source or EHubSource(page, find_in_ehub, open_manage_in_ehub)
    try:
        found = source.fetch(reference)
    except P.SourceError as error:
        report.update(result="STOPPED", kind=error.kind, reason=str(error),
                      trail=getattr(error, "trail", None))
        found = None
    if found is not None:
        data = found["data"]
        sha = hashlib.sha256(data).hexdigest()
        out = Path(out_dir or (S.Store().folder / "probes"))
        out.mkdir(parents=True, exist_ok=True)
        pdf_path = out / "probe-{0}-{1}.pdf".format(stamp, sha[:12])
        pdf_path.write_bytes(data)
        report.update(result="FOUND", trail=found["trail"],
                      document={"filename": found["filename"], "bytes": len(data),
                                "sha256": sha, "url": found["url"],
                                "saved_as": str(pdf_path),
                                "retrieved_at": datetime.now().astimezone().isoformat(
                                    timespec="seconds")},
                      identifier=found["identifier"])
        doctype = doctypes.get()
        try:
            read = X.read_pdf(data)
            fields = X.extract(read["text"], doctype)
            report["pdf"] = {"pages": read["pages"], "methods": read["methods"],
                             "fields": {n: {k: f.get(k) for k in ("status", "value", "evidence",
                                                                  "candidates")}
                                        for n, f in fields.items()}}
            check = V.validate(doctype, fields, found["hub"],
                               P._request_fields(doctype, {}, P.config_from_env()))
            report["identifier_check"] = [c for c in check["checks"]
                                          if c["name"].startswith("hub:")]
        except X.Unreadable as error:
            report["pdf"] = {"unreadable": str(error)}
        # Step 7: exactly what the next stage receives.
        report["next_stage_input"] = {"reference": found["hub"].get("bol_awb"),
                                      "identifier": found["identifier"],
                                      "document_sha256": sha}
    out = Path(out_dir or (S.Store().folder / "probes"))
    out.mkdir(parents=True, exist_ok=True)
    path = out / "probe-{0}.json".format(stamp)
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["report_file"] = str(path)
    return report


def cmd_probe(args):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser, page = _browser(playwright)
        try:
            report = probe(page, args.reference, args.out)
        finally:
            browser.close()
    print(json.dumps(report, indent=2, default=str))
    return 0 if report.get("result") == "FOUND" else 1


def cmd_send(args):
    from po.mail import GraphMailer
    store = S.Store()
    record = store.get(args.po_id)
    if record is None:
        return 2
    record, outcome = P.send(store, record, GraphMailer(), by=args.by or "cli",
                             authorize_resend=args.authorize_resend, reason=args.reason)
    print(outcome, "—", (record.get("email") or {}).get("reasons") or
          (record.get("email") or {}).get("confirmation") or "")
    return 0 if outcome in ("SENT", "CONFIRMED") else 1


def cmd_read(args):
    doctype = doctypes.get(args.doctype)
    data = Path(args.file).read_bytes()
    try:
        read = X.read_pdf(data)
    except X.Unreadable as error:
        print("UNREADABLE:", error)
        return 1
    fields = X.extract(read["text"], doctype)
    request = P._request_fields(doctype, {"invoice_no": args.invoice_no}, P.config_from_env())
    result = V.validate(doctype, fields, {"bol_awb": args.hub_bol, "identifier": args.identifier},
                        request)
    print(json.dumps({"pages": read["pages"], "methods": read["methods"],
                      "sha256": hashlib.sha256(data).hexdigest(),
                      "fields": {n: {k: f.get(k) for k in ("status", "value", "evidence",
                                                           "candidates")}
                                 for n, f in fields.items()},
                      "validation": result}, indent=2, default=str))
    if args.dump_text:
        print("\n----- TEXT -----\n" + read["text"])
    return 0 if result["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m po", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("process")
    p.add_argument("--po-id", required=True)
    s = sub.add_parser("send")
    s.add_argument("--po-id", required=True)
    s.add_argument("--by")
    s.add_argument("--authorize-resend", action="store_true")
    s.add_argument("--reason")
    e = sub.add_parser("ehub-probe")
    e.add_argument("--reference")
    e.add_argument("--out")
    r = sub.add_parser("read")
    r.add_argument("file")
    r.add_argument("--hub-bol")
    r.add_argument("--identifier")
    r.add_argument("--invoice-no")
    r.add_argument("--doctype")
    r.add_argument("--dump-text", action="store_true")
    args = parser.parse_args(argv)
    return {"process": cmd_process, "send": cmd_send, "ehub-probe": cmd_probe,
            "read": cmd_read}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
