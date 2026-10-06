"""
PO Automation from the command line — what the worker and the local
dashboard run in the background, and the proof tools.

    python -m po process --po-id ID        run one queued job:
                                           eHub → Under Clearance → Manage →
                                           Documents → Bill Entry → identifier →
                                           PDF → … → EMAIL READY
    python -m po send --po-id ID           send a prepared job through Graph
    python -m po ehub-check                REAL eHub connectivity, stage by stage:
                                           DNS, TCP, HTTPS, credentials (present?
                                           — never shown), browser, sign-in, the
                                           shipment list. Classifies a failure
                                           as NETWORK / AUTHENTICATION / BROWSER /
                                           APPLICATION. Changes nothing.
    python -m po ehub-probe [--reference BOL/AWB]
                                           THE PROOF, on the real eHub, read-only:
                                           finds an Under Clearance record (or
                                           the one named), opens Manage, reads
                                           the Documents section, picks the
                                           Bill Entry document, extracts its
                                           identifier, downloads and reads the
                                           PDF — and writes a report. Nothing
                                           is written to eHub, nothing is sent.
    python -m po sweep [--limit N]          THE AUTOMATIC RUN, started beside every ETA
                                           run: a job for each Under Clearance record
                                           with none yet (PO_AUTO=0 turns it off)
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
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from po import doctypes, extract as X, pipeline as P, store as S, validate as V  # noqa: E402


def _browser(playwright):
    """
    eHub opened the way the ETA run opens it: the run's own browser launch
    (headed Edge, update_eta.hub_launch_options) and sign-in. PO_HEADLESS and
    PO_BROWSER_EXECUTABLE override it — for the test suite and diagnostics.
    """
    import update_eta as A
    username, password = A.load_credentials()
    launch = dict(A.hub_launch_options())
    if os.environ.get("PO_HEADLESS"):
        launch["headless"] = os.environ["PO_HEADLESS"].strip().lower() not in ("0", "false", "no")
    if os.environ.get("PO_BROWSER_EXECUTABLE"):
        launch.pop("channel", None)
        launch["executable_path"] = os.environ["PO_BROWSER_EXECUTABLE"]
    elif "PO_BROWSER_CHANNEL" in os.environ:
        launch.pop("channel", None)
        if os.environ["PO_BROWSER_CHANNEL"]:
            launch["channel"] = os.environ["PO_BROWSER_CHANNEL"]
    browser = playwright.chromium.launch(**launch)
    context = browser.new_context(**dict(A.hub_context_options(username, password),
                                         accept_downloads=True))
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
                auth = re.search(r"log ?in|sign ?in|credential|password|unauthori", str(error), re.I)
                state = S.AUTH_REQUIRED if auth else S.DISCOVERY_FAILED
                record = store.transition(record, state, "eHub could not be opened",
                                          P.failure(state, "auth" if auth else "ehub_record",
                                                    "eHub could not be opened: {0}".format(
                                                        str(error)[:200]), code=state,
                                                    next_action=S.NEXT_ACTION[state]))
                store.event(record, "AUTH_REQUIRED" if auth else "PDF_NOT_FOUND",
                            "auth" if auth else "ehub_record", "FAILED", reason=str(error)[:200])
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
    from po.ehub import ehub_host
    report = {"probe": "ehub", "started": datetime.now().astimezone().isoformat(timespec="seconds"),
              "ehub_domain": ehub_host(), "reference_asked": reference or None,
              "writes_to_ehub": False, "sends_email": False}
    source = source or EHubSource(page, find_in_ehub, open_manage_in_ehub)
    try:
        found = source.fetch(reference)
    except P.SourceError as error:
        trail = getattr(error, "trail", None) or {}
        report.update(result="STOPPED", kind=error.kind, reason=str(error), trail=trail,
                      source=(trail.get("provenance") or {}).get("source", "UNKNOWN"),
                      verification=(trail.get("provenance") or {}).get("verification",
                                                                        "UNVERIFIED"),
                      navigation_path=trail.get("navigation_path"))
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
                      identifier=found["identifier"],
                      source=found["trail"]["provenance"]["source"],
                      verification=found["trail"]["provenance"]["verification"],
                      provenance=found["trail"]["provenance"],
                      navigation_path=found["trail"]["navigation_path"],
                      selected_row={"bol_awb": found["hub"].get("bol_awb"),
                                    "status": found["hub"].get("status"),
                                    "carrier": found["hub"].get("carrier"),
                                    "table_page": found["hub"].get("table_page")},
                      bill_entry_filename=found["filename"],
                      po_identifier=found["identifier"],
                      document_reference={k: found["trail"]["download"].get(k) for k in
                                          ("url", "link", "element_id", "method",
                                           "served_filename")})
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


def cmd_check(args):
    from po import diagnose
    report = diagnose.check(launch_browser=not args.no_browser)
    print(json.dumps(report, indent=2, default=str))
    out = Path(args.out or (S.Store().folder / "probes"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "ehub-check-{0}.json".format(datetime.now().strftime("%Y%m%d-%H%M%S"))).write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0 if report["result"] == "REACHABLE" else 1


def eligibility(rows, handled):
    """
    Every row of the Shipments list, decided: ELIGIBLE, or skipped with a
    reason — DISCOVERED → STATUS_CHECK → ELIGIBLE / SKIPPED. Nothing is
    silently dropped. Returns [{"bol_awb", "status", "decision", ...}].
    """
    from po.ehub import status_ok
    from po.extract import normal_reference
    out, seen = [], set()
    for row in rows:
        ref = row.get("bol_awb")
        entry = {"bol_awb": ref, "status": row.get("status"), "carrier": row.get("carrier"),
                 "table_page": row.get("table_page"), "view": row.get("view")}
        key = normal_reference(ref)
        if not key:
            entry["decision"] = "SKIPPED_MISSING_REQUIRED_REFERENCE"
        elif not status_ok(row.get("status")):
            entry["decision"] = "SKIPPED_NOT_UNDER_CLEARANCE"
        elif key in seen:
            entry["decision"] = "SKIPPED_DUPLICATE"
        elif key in handled:
            entry["decision"] = "SKIPPED_ALREADY_PROCESSED"
        else:
            entry["decision"] = "ELIGIBLE"
        seen.add(key)
        out.append(entry)
    return out


def sweep(page, store, limit=20, source_factory=None, log=print, mailer_factory=None):
    """
    THE AUTOMATIC RUN, one at a time per data folder: interrupted jobs are
    recovered first; then every eHub Shipments-list row is decided (eligible
    or skipped, with its reason, written to sweeps/), and every eligible
    record gets one job — Manage, Documents, Bill Entry — one after another,
    in this one browser. Returns the jobs it ran.
    """
    from po.ehub import EHubSource, ehub_rows, find_in_ehub, open_manage_in_ehub
    from po import service as SV
    from po.extract import normal_reference
    config = P.config_from_env()
    make_source = source_factory or (lambda pg: EHubSource(pg, find_in_ehub,
                                                            open_manage_in_ehub))
    try:
        lock = store.xlock("sweep", timeout=1.0, stale_s=6 * 3600)
        lock.__enter__()
    except TimeoutError:
        log("[PO sweep] another PO automatic run holds this data folder; not starting a second")
        return []
    try:
        # 1. Recovery before new work: nothing half-done is left behind.
        mailer = None
        if config["auto_send"]:
            from po.mail import GraphMailer
            mailer = (mailer_factory or GraphMailer)()
        P.recover(store, config, source=make_source(page), mailer=mailer, log=log)
        # 2. The list, every row decided.
        handled = {normal_reference(r) for r in SV.PoService(
            store=store, launcher=lambda r: None).handled_references()}
        t0 = time.monotonic()
        decided = eligibility(ehub_rows(page), handled)
        picked = [d["bol_awb"] for d in decided if d["decision"] == "ELIGIBLE"][:limit]
        counts = {}
        for d in decided:
            counts[d["decision"]] = counts.get(d["decision"], 0) + 1
        folder = store.folder / "sweeps"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "sweep-{0}.json".format(datetime.now().strftime("%Y%m%d-%H%M%S"))).write_text(
            json.dumps({"at": S.now_iso(), "rows": decided, "counts": counts, "limit": limit,
                        "list_ms": int((time.monotonic() - t0) * 1000)}, indent=1),
            encoding="utf-8")
        log("[PO sweep] {0} row(s) read: {1}. Processing {2}: {3}".format(
            len(decided), ", ".join("{0} {1}".format(v, k) for k, v in sorted(counts.items()))
            or "none", len(picked), ", ".join(picked) or "none"))
        ran = []
        for ref in picked:
            record = store.create(doctypes.DEFAULT, ref, {"started_by": "automatic"},
                                  started_by="automatic")
            try:
                record = P.process(store, record, make_source(page), config)
            except Exception as error:
                current = store.get(record["po_id"]) or record
                P.interrupt(store, current, "the job stopped unexpectedly: {0}".format(
                    str(error)[:200]))
                record = store.get(record["po_id"]) or current
            log("[PO sweep] {0}: {1}".format(ref, record["state"]))
            if record["state"] == S.EMAIL_PREPARED and config["auto_send"] and mailer:
                record, _o = P.send(store, record, mailer, by="auto-send",
                                    confirm_wait_s=config["confirm_wait_s"])
            ran.append(record)
        return ran
    finally:
        lock.__exit__(None, None, None)


def cmd_sweep(args):
    store = S.Store()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        try:
            browser, page = _browser(playwright)
        except Exception as error:
            print("[PO sweep] eHub could not be opened: {0}".format(str(error)[:200]))
            return 1
        try:
            ran = sweep(page, store, limit=args.limit)
        except Exception as error:
            from po.ehub import capture
            print("[PO sweep] the Under Clearance list could not be read: {0} — evidence {1}"
                  .format(str(error)[:200], capture(page, "sweep_list")))
            return 1
        finally:
            browser.close()
    print(json.dumps([{"po_id": r["po_id"], "reference": r["reference"], "state": r["state"]}
                      for r in ran], indent=2))
    return 0


def cmd_recover(args):
    """Interrupted jobs resumed, unknown sends reconciled. eHub opened only when needed."""
    store = S.Store()
    config = P.config_from_env()
    from po.mail import GraphMailer, configured
    mailer = GraphMailer() if configured()[0] else None
    need_browser = any(r["state"] in (S.WORKER_DISCONNECTED, S.QUEUED, S.DISCOVERED) or
                       (r["state"] in S.ACTIVE_STATES and store.lease_stale(r))
                       for r in store.all(500))
    if need_browser and not args.no_browser:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser, page = _browser(playwright)
            try:
                done = P.recover(store, config, source=_source(page, {}), mailer=mailer)
            finally:
                browser.close()
    else:
        done = P.recover(store, config, source=None, mailer=mailer)
    print(json.dumps([{"po_id": a, "from": b, "to": c} for a, b, c in done], indent=2))
    return 0


def explain(store, po_id):
    """Everything that happened to one job, from its own record and events."""
    record = store.get(po_id)
    if record is None:
        return None
    events = store.events(po_id)
    doc, out, email = record.get("document") or {}, record.get("output") or {}, \
        record.get("email") or {}
    return {
        "po_id": po_id, "correlation_id": record.get("correlation_id") or po_id,
        "worker_id": record.get("worker_id"), "state": record["state"],
        "label": record.get("label"), "reference": record.get("reference"),
        "provenance": record.get("provenance"),
        "eligibility": (record.get("discovery") or {}).get("clearance"),
        "ehub_row": (record.get("discovery") or {}).get("ehub_record"),
        "manage": (record.get("discovery") or {}).get("manage"),
        "identity": (record.get("discovery") or {}).get("identity"),
        "bill_entry": (record.get("discovery") or {}).get("bill_entry"),
        "document": {k: doc.get(k) for k in ("filename", "sha256", "bytes", "pages",
                                             "read_methods", "integrity", "retrieved_at")},
        "extracted": {n: {k: f.get(k) for k in ("status", "value", "raw", "evidence")}
                      for n, f in (record.get("fields") or {}).items()},
        "provenance_map": record.get("provenance_map"),
        "validation": record.get("validation"),
        "template": record.get("template"),
        "output": {k: out.get(k) for k in ("path", "filename", "sha256", "bytes",
                                           "template_version", "verified", "saved_at")},
        "email": {k: email.get(k) for k in ("status", "recipient", "subject", "attachment",
                                            "graph_status", "internet_message_id",
                                            "sent_at", "confirmed_at", "confirmation")},
        "failure": record.get("failure"), "skip_reason": record.get("skip_reason"),
        "idempotency_key": record.get("idempotency_key"),
        "timings_ms": record.get("timings"),
        "timeline": [{"at": e["timestamp"], "event": e["event"], "stage": e["stage"],
                      "status": e["status"]} for e in events],
    }


def cmd_explain(args):
    report = explain(S.Store(), args.po_id)
    if report is None:
        print("no such job: {0}".format(args.po_id), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_quality(args):
    from po import quality
    print(json.dumps(quality.metrics(S.Store()), indent=2))
    return 0


def cmd_readiness(args):
    from po import readiness
    report = readiness.evaluate(S.Store())
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["status"] != "NOT READY" else 1


def cmd_supply(args):
    store = S.Store()
    record = store.get(args.po_id)
    if record is None:
        return 2
    record, problems = P.supply(store, record, {"invoice_no": args.invoice_no},
                                by=args.by or "cli")
    print(record["state"], "—", "; ".join(problems) or record.get("progress") or "")
    return 0 if not problems else 1


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
    c = sub.add_parser("ehub-check")
    c.add_argument("--no-browser", action="store_true")
    c.add_argument("--out")
    e = sub.add_parser("ehub-probe")
    e.add_argument("--reference")
    e.add_argument("--out")
    rc = sub.add_parser("recover", help="resume interrupted jobs; reconcile unknown sends")
    rc.add_argument("--no-browser", action="store_true")
    ex = sub.add_parser("explain", help="everything that happened to one job")
    ex.add_argument("po_id")
    sub.add_parser("quality", help="reliability metrics from the recorded jobs")
    sub.add_parser("readiness", help="the production-readiness gate, from evidence")
    su = sub.add_parser("supply", help="give a job in review the value it needs (G4)")
    su.add_argument("--po-id", required=True)
    su.add_argument("--invoice-no", required=True)
    su.add_argument("--by")
    w = sub.add_parser("sweep", help="the automatic run: a PO job for every Under Clearance "
                                     "record that has none yet")
    w.add_argument("--limit", type=int, default=int(os.environ.get("PO_SWEEP_LIMIT") or 20))
    r = sub.add_parser("read")
    r.add_argument("file")
    r.add_argument("--hub-bol")
    r.add_argument("--identifier")
    r.add_argument("--invoice-no")
    r.add_argument("--doctype")
    r.add_argument("--dump-text", action="store_true")
    args = parser.parse_args(argv)
    return {"process": cmd_process, "send": cmd_send, "ehub-probe": cmd_probe,
            "ehub-check": cmd_check, "sweep": cmd_sweep,
            "read": cmd_read, "recover": cmd_recover, "explain": cmd_explain,
            "quality": cmd_quality, "readiness": cmd_readiness, "supply": cmd_supply}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
