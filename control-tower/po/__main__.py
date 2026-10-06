"""
PO Automation from the command line — what the worker and the local
dashboard run in the background, plus two diagnostics.

    python -m po process --po-id ID        run one queued job (Hub → … → EMAIL READY)
    python -m po send --po-id ID           send a prepared job through Graph
    python -m po read FILE.pdf [--hub-bol X] [--invoice-no N]
                                           read a PDF and show what would be
                                           extracted and validated — nothing
                                           is generated or sent
    python -m po hub-links BOL/AWB         open the shipment in the Hub and
                                           list the document links found there

A job runs in its own process so the dashboard never waits on a browser.
The Hub is opened with the automation's own credentials file and sign-in;
nothing here stores or prints a credential.
"""

import argparse
import json
import os
import sys
import traceback
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


def cmd_process(args):
    store = S.Store()
    record = store.get(args.po_id)
    if record is None:
        print("no such job: {0}".format(args.po_id), file=sys.stderr)
        return 2
    try:
        from playwright.sync_api import sync_playwright
        from po.hub import HubDocumentSource, open_in_hub
        with sync_playwright() as playwright:
            try:
                browser, page = _browser(playwright)
            except Exception as error:
                record = store.transition(record, S.DISCOVERED, "Signing in to the Hub…")
                record = store.transition(record, S.PDF_NOT_FOUND, "The Hub could not be opened",
                                          P.failure("NAVIGATION_FAILURE", "pdf_retrieval",
                                                    "the Hub could not be opened: {0}".format(
                                                        str(error)[:200])))
                store.event(record, "PDF_NOT_FOUND", "pdf_retrieval", "FAILED",
                            reason=str(error)[:200])
                return 1
            try:
                source = HubDocumentSource(page, open_in_hub,
                                           wanted=(record.get("request") or {}).get("document_name"))
                record = P.process(store, record, source)
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
    result = V.validate(doctype, fields, {"bol_awb": args.hub_bol}, request)
    print(json.dumps({"pages": read["pages"], "methods": read["methods"],
                      "fields": {n: {k: f.get(k) for k in ("status", "value", "evidence",
                                                           "candidates")}
                                 for n, f in fields.items()},
                      "validation": result}, indent=2, default=str))
    if args.dump_text:
        print("\n----- TEXT -----\n" + read["text"])
    return 0 if result["passed"] else 1


def cmd_hub_links(args):
    from playwright.sync_api import sync_playwright
    from po.hub import candidates, links_on, open_in_hub
    with sync_playwright() as playwright:
        browser, page = _browser(playwright)
        try:
            hub = open_in_hub(page, args.reference)
            print("Hub row:", json.dumps(hub))
            found, best, distinct = candidates(links_on(page))
            for link in found:
                print("  {0} score={1} text={2!r} href={3!r}".format(
                    "*" if link in best else " ", link["score"], link["text"], link["href"]))
            print("{0} candidate(s); {1} distinct best".format(len(found), len(distinct)))
        finally:
            browser.close()
    return 0


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
    r = sub.add_parser("read")
    r.add_argument("file")
    r.add_argument("--hub-bol")
    r.add_argument("--invoice-no")
    r.add_argument("--doctype")
    r.add_argument("--dump-text", action="store_true")
    h = sub.add_parser("hub-links")
    h.add_argument("reference")
    args = parser.parse_args(argv)
    return {"process": cmd_process, "send": cmd_send, "read": cmd_read,
            "hub-links": cmd_hub_links}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
