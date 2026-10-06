"""
REAL eHub verification — run ON THE WINDOWS WORKER.

    python -m worker.verify ehub [--reference BOL/AWB] [--no-report]
        Opens the real eHub exactly as a run does — update_eta.INTERNAL_URL,
        the credentials file, headed Edge (update_eta.hub_launch_options),
        the run's own sign-in — and checks, reading only:
          the eHub page loads (HTTP status, host, title)
          the BU shipment list renders with its BOL/AWB and Status columns
          a real shipment is on it (the one named, or the first one
          Under Clearance), and its status is read off its row
        Clicks nothing that changes eHub.

    python -m worker.verify eta --reference MEDUAHP69377 [--no-report]
        The ETA write path for ONE shipment, by the automation's own main():
        carrier page -> ETA -> identity -> the shipment in eHub -> WRITE ->
        reload -> READ BACK -> compare. This writes to eHub, as a run does.
        Only a matching read-back on the worker is REAL VERIFIED.

Each prints its observation (JSON), writes it beside the run logs, prints
one [VERIFY] line, and reports it to the control plane over the worker's
own channel (ATA_CONTROL_PLANE_URL + ATA_WORKER_TOKEN, as `python -m worker`
uses). The control plane computes the level again from the evidence.

When the worker cannot reach or sign in to eHub the result is
REAL VERIFICATION BLOCKED with the stage and the exact reason. Credentials
are read from the automation's file and never printed, logged or reported.
"""

import argparse
import json
import os
import platform
import secrets
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intelligence import verification as V          # noqa: E402

STAGES = ("config", "credentials", "browser", "sign_in", "page", "shipment_list",
          "shipment", "status")


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def environment():
    """Where this ran. Only a Windows machine is taken for the worker."""
    return {"host": socket.gethostname(), "platform": platform.platform()[:80],
            "python": platform.python_version(),
            "role": "windows-worker" if os.name == "nt" else "not-a-worker",
            "worker_id": worker_id()}


def worker_id():
    """The registered worker this machine is: the id part of its token
    (w_xxx.secret). The secret is never read out."""
    token = os.environ.get("ATA_WORKER_TOKEN") or ""
    head = token.split(".")[0]
    return head if head.startswith("w_") else None


def channel():
    return "worker" if os.name == "nt" else "local:" + platform.system().lower()


def base(kind, run_id=None):
    import update_eta as A
    return {"kind": kind, "run_id": run_id or "verify-{0:%Y%m%d-%H%M%S}-{1}".format(
                datetime.now(), secrets.token_hex(3)),
            "observed_at": _now(), "environment": environment(),
            "ehub_url": A.INTERNAL_URL, "ehub_host": V.host_of(A.INTERNAL_URL),
            "required_status": V.REQUIRED_STATUS, "stages": [], "result": None,
            "browser": {"real": False}, "page": {}, "shipment_list": {}, "shipment": {}}


def stage(obs, name, ok, **detail):
    obs["stages"].append(dict({"stage": name, "ok": bool(ok), "at": _now()}, **detail))
    return ok


def blocked(obs, name, category, reason, **detail):
    stage(obs, name, False, category=category, error=reason, **detail)
    obs.update(result="BLOCKED", blocked_stage=name, blocked_category=category,
               blocked_reason="{0} at {1}: {2}".format(category, name, reason))
    return obs


def category_of(text):
    """A browser/navigation error, filed by what it says."""
    text = str(text or "")
    if any(s in text for s in ("ERR_NAME_NOT_RESOLVED", "ERR_NAME_RESOLUTION_FAILED")):
        return "NETWORK", "the eHub host name does not resolve from this machine"
    if any(s in text for s in ("ERR_CONNECTION", "ERR_TIMED_OUT", "ERR_ADDRESS_UNREACHABLE",
                               "ERR_NETWORK", "ERR_INTERNET_DISCONNECTED", "ERR_PROXY",
                               "ERR_TUNNEL", "ERR_SSL", "ERR_CERT", "Timeout")):
        return "NETWORK", "eHub could not be reached: " + text.split("\n")[0][:200]
    if any(s in text for s in ("ERR_INVALID_AUTH_CREDENTIALS", "401", "403", "login fields")):
        return "AUTHENTICATION", "eHub refused the sign-in: " + text.split("\n")[0][:200]
    return "APPLICATION", text.split("\n")[0][:200]


def network_probe(host, port=443, timeout=8):
    """Python's own view of the network, for the reason only — never gating:
    Edge may use a system proxy this probe does not."""
    out = {"host": host}
    try:
        out["addresses"] = sorted({a[4][0] for a in socket.getaddrinfo(host, port)})[:4]
    except Exception as error:
        out["dns_error"] = str(error)
        return out
    try:
        with socket.create_connection((out["addresses"][0], port), timeout=timeout):
            out["tcp"] = "open"
    except Exception as error:
        out["tcp_error"] = str(error)
    return out


def read_rows(page):
    """The rows of the current eHub list page: reference, carrier, status, ETA
    — read off the cells, as collect_supported_shipments reads them."""
    import update_eta as A
    table = A.find_shipments_table(page)
    columns = A.build_header_map(table)
    rows = table.locator("tbody tr")
    out = []
    for index in range(rows.count()):
        cells = rows.nth(index).locator("td")

        def cell(name):
            i = columns.get(name)
            if not isinstance(i, int) or cells.count() <= i:
                return None
            return " ".join((cells.nth(i).inner_text() or "").split()) or None

        if cell("bol_awb"):
            out.append({"reference": cell("bol_awb"), "carrier": cell("carrier"),
                        "status": cell("status"), "eta": cell("eta")})
    return columns, out


def same_reference(a, b):
    def norm(value):
        return "".join(str(value or "").split()).replace("-", "").upper()
    return bool(a) and bool(b) and norm(a) == norm(b)


def pick(rows, reference=None):
    """The named shipment, or the first one Under Clearance."""
    for row in rows:
        if reference and same_reference(row["reference"], reference):
            return row
        if not reference and V._same_status(row.get("status")):
            return row
    return None


# ── ehub: the connection and the list, read-only ───────────────────────────

def observe_ehub(reference=None, credentials=None, launch=None, log=print):
    """
    The observation. `credentials` and `launch` exist for the test suite
    (which marks what it produces TEST); a worker uses neither.
    """
    import update_eta as A
    A.write_log = getattr(A, "write_log", None) or (lambda *a, **k: None)
    obs = base("ehub-connection")
    obs["reference_asked"] = reference or None
    host = obs["ehub_host"]
    if host != V.ehub_host():
        return blocked(obs, "config", "CONFIGURATION",
                       "update_eta.INTERNAL_URL is on {0!r}, but the eHub host is {1!r}"
                       .format(host, V.ehub_host()))
    stage(obs, "config", True, url=A.INTERNAL_URL)

    if credentials is None:
        try:
            credentials = A.load_credentials()
        except Exception as error:
            return blocked(obs, "credentials", "AUTHENTICATION",
                           "the automation's credentials file could not be used ({0}): {1}"
                           .format(A.CREDENTIALS_FILE, str(error).split(":")[0][:120]))
    stage(obs, "credentials", True, file=str(getattr(A, "CREDENTIALS_FILE", "")),
          detail="present (values not read out)")

    try:
        from playwright.sync_api import sync_playwright
    except Exception as error:
        return blocked(obs, "browser", "BROWSER", "Playwright is not installed: {0}".format(error))

    with sync_playwright() as playwright:
        options = dict(launch or A.hub_launch_options())
        try:
            browser = playwright.chromium.launch(**options)
        except Exception as error:
            return blocked(obs, "browser", "BROWSER",
                           "the browser could not be launched: {0}".format(
                               str(error).split("\n")[0][:200]))
        try:
            obs["browser"] = {"real": True, "engine": browser.browser_type.name,
                              "channel": options.get("channel"), "version": browser.version,
                              "headless": bool(options.get("headless")),
                              "same_launch_as_runs": launch is None}
            stage(obs, "browser", True, version=browser.version, channel=options.get("channel"))
            context = browser.new_context(**A.hub_context_options(*credentials))
            page = context.new_page()
            visited, documents = [], []
            page.on("framenavigated", lambda frame: visited.append(V.host_of(frame.url))
                    if frame == page.main_frame else None)
            page.on("response", lambda r: documents.append(
                {"host": V.host_of(r.url), "status": r.status})
                if r.request.resource_type == "document" and r.frame == page.main_frame
                else None)
            try:
                A.login_internal(page, *credentials)
            except Exception as error:
                category, reason = category_of(error)
                extra = {"network_probe": network_probe(host)} if category == "NETWORK" else {}
                return blocked(obs, "sign_in", category, reason, **extra)
            ehub_docs = [d for d in documents if d["host"] == host]
            status = ehub_docs[-1]["status"] if ehub_docs else None
            obs["page"] = {"host": V.host_of(page.url), "http_status": status,
                           "title": (page.title() or "")[:120], "hosts_visited":
                           sorted({h for h in visited if h})}
            if status in (401, 403):
                return blocked(obs, "sign_in", "AUTHENTICATION",
                               "eHub answered HTTP {0} with the automation's credentials"
                               .format(status), page=obs["page"])
            if obs["page"]["host"] != host or page.url.startswith("chrome-error"):
                return blocked(obs, "page", "APPLICATION",
                               "after sign-in the browser is on {0!r}, not eHub".format(page.url[:160]))
            stage(obs, "sign_in", True, http_status=status)
            stage(obs, "page", True, **obs["page"])

            try:
                A.ensure_filtered_page(page, A.SOURCE_VIEW, 1)
                columns, rows = read_rows(page)
            except Exception as error:
                return blocked(obs, "shipment_list", "APPLICATION",
                               "the shipment list did not render: {0}".format(
                                   str(error).split("\n")[0][:200]))
            have = sorted(k for k, v in columns.items() if isinstance(v, int))
            wanted = [c for c in ("bol_awb", "status") if c not in have]
            obs["shipment_list"] = {"rendered": not wanted, "view": A.SOURCE_VIEW,
                                    "filter": A.TARGET_STATUS, "columns": have,
                                    "page_1_rows": len(rows), "url": page.url[:200]}
            if wanted:
                return blocked(obs, "shipment_list", "APPLICATION",
                               "the shipment list has no {0} column".format(" / ".join(wanted)))
            stage(obs, "shipment_list", True, rows=len(rows), columns=have)

            seen, statuses, found, number = 0, {}, None, 1
            while True:
                seen += len(rows)
                for row in rows:
                    statuses[row.get("status") or "-"] = statuses.get(row.get("status") or "-", 0) + 1
                found = pick(rows, reference)
                if found:
                    found["table_page"] = number
                    break
                number += 1
                if number > A.MAX_TABLE_PAGES:
                    break
                try:
                    A.ensure_filtered_page(page, A.SOURCE_VIEW, number)
                except A.SkipShipment:
                    break
                _columns, rows = read_rows(page)
            obs["shipment_list"].update(rows_read=seen, pages_read=number if found else number - 1,
                                        statuses_seen=statuses)
            if not found:
                return blocked(obs, "shipment", "APPLICATION",
                               "{0} is not in the {1} list filtered to {2!r} ({3} rows read)"
                               .format(reference, A.SOURCE_VIEW, A.TARGET_STATUS, seen)
                               if reference else
                               "no row with status {0!r} in the {1} list ({2} rows read)"
                               .format(V.REQUIRED_STATUS, A.SOURCE_VIEW, seen))
            obs["shipment"] = found
            stage(obs, "shipment", True, reference=found["reference"], page=found["table_page"])
            if not V._same_status(found.get("status")):
                return blocked(obs, "status", "APPLICATION",
                               "{0} is {1!r} in eHub, not {2!r}".format(
                                   found["reference"], found.get("status"), V.REQUIRED_STATUS))
            stage(obs, "status", True, status=found["status"])
            obs["result"] = "OBSERVED"
            return obs
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ── eta: the real write path for one shipment ──────────────────────────────

def prove_eta(reference, log=print):
    """
    The automation's own main(), for one shipment: of the shipments it
    collects from eHub only `reference` is processed; the Human Action queue
    is not worked. Every other step is production code, unchanged.
    """
    import update_eta as A
    obs = base("eta-write", run_id=A.RUN_ID)
    obs.update(reference_asked=reference, ocean_write=A.OCEAN_WRITE,
               verify_after_save=A.VERIFY_AFTER_SAVE, dry_run=A.DRY_RUN,
               carrier_result=None, writes=[], read_backs=[])
    if obs["ehub_host"] != V.ehub_host():
        return blocked(obs, "config", "CONFIGURATION",
                       "update_eta.INTERNAL_URL is on {0!r}, but the eHub host is {1!r}"
                       .format(obs["ehub_host"], V.ehub_host()))
    if A.DRY_RUN:
        return blocked(obs, "config", "CONFIGURATION",
                       "this is a dry run (CT_DRY_RUN=1): nothing would be saved")
    stage(obs, "config", True, url=A.INTERNAL_URL, ocean_write=A.OCEAN_WRITE)

    real = {"collect": A.collect_supported_shipments, "lookup": A.get_provider_result,
            "fill": A.fill_date_field, "read_back": A.verify_saved_date,
            "login": A.login_internal}

    def login(page, username, password):
        real["login"](page, username, password)
        browser = page.context.browser
        obs["browser"] = {"real": True, "engine": browser.browser_type.name if browser else None,
                          "channel": A.hub_launch_options().get("channel"),
                          "version": browser.version if browser else None,
                          "headless": False, "same_launch_as_runs": True}
        obs["page"] = {"host": V.host_of(page.url), "title": (page.title() or "")[:120]}
        stage(obs, "sign_in", True, host=obs["page"]["host"])

    def collect(page, table_page):
        rows = real["collect"](page, table_page)
        mine = [r for r in rows if same_reference(r.get("bol_awb"), reference)
                or same_reference(r.get("tracking_reference"), reference)]
        if not obs["shipment_list"]:
            obs["shipment_list"] = {"rendered": True, "view": A.SOURCE_VIEW,
                                    "filter": A.TARGET_STATUS, "pages_read": 0}
        obs["shipment_list"]["pages_read"] += 1
        if not mine or obs["shipment"]:
            return []
        try:
            _columns, cells = read_rows(page)
            row = next((r for r in cells if same_reference(r["reference"], mine[0]["bol_awb"])), {})
        except Exception:
            row = {}
        obs["shipment"] = {"reference": mine[0]["bol_awb"], "carrier": mine[0].get("carrier"),
                           "status": row.get("status"), "eta_before": row.get("eta"),
                           "table_page": table_page}
        stage(obs, "shipment", True, reference=mine[0]["bol_awb"], status=row.get("status"))
        return mine[:1]

    def lookup(pages, shipment):
        result = real["lookup"](pages, shipment)
        provider = result.get("provider") or shipment.get("provider")
        config = A.PORTALS.get(provider) or {}
        obs["carrier_result"] = dict({k: result.get(k) for k in (
            "provider", "tracking_status", "eta", "eta_source", "ata", "ata_source")},
            identity_checked=bool(config.get("verify_identity")),
            identity_rule="the carrier page carries the reference letter for letter"
            if config.get("verify_identity") else "not established for this carrier",
            at=_now())
        return result

    def fill(page, field_name, value):
        obs["writes"].append({"field": field_name, "value": value, "at": _now(),
                              "page_host": V.host_of(page.url)})
        return real["fill"](page, field_name, value)

    def read_back(page, shipment, view_name, field_name, expected):
        verdict, detail = real["read_back"](page, shipment, view_name, field_name, expected)
        obs["read_backs"].append({
            "view": view_name, "field": field_name, "written": expected,
            "read_back": detail if verdict is True else None,
            "verdict": {True: "MATCH", False: "MISMATCH", None: "NOT READ"}[verdict],
            "detail": None if verdict is True else detail, "at": _now(),
            "page_host": V.host_of(page.url)})
        return verdict, detail

    A.login_internal, A.collect_supported_shipments = login, collect
    A.get_provider_result, A.fill_date_field, A.verify_saved_date = lookup, fill, read_back
    A.human_queue_service = lambda: []
    A.DASHBOARD_ENABLED = False          # a Control Tower already open keeps its port
    A.PAUSE_ON_FATAL_ERROR = False
    error = None
    try:
        A.main()
    except SystemExit:
        pass
    except Exception as failure:
        error = str(failure)
    finally:
        A.login_internal, A.collect_supported_shipments = real["login"], real["collect"]
        A.get_provider_result, A.fill_date_field = real["lookup"], real["fill"]
        A.verify_saved_date = real["read_back"]

    snap = A.tower.snapshot()
    record = next((r for r in snap.get("shipments") or []
                   if same_reference(r.get("reference"), reference)), None)
    obs.update(run_error=error, shipment_outcome=(record or {}).get("state"),
               shipment_detail=(record or {}).get("error"),
               declared_failure=(record or {}).get("failure"),
               hub_actions={"coe": (record or {}).get("coe_action"),
                            "bu": (record or {}).get("bu_action")})
    if error and not obs["browser"].get("real"):
        if "credentials" in error.lower():
            return blocked(obs, "credentials", "AUTHENTICATION", error[:200])
        category, reason = category_of(error)
        if category == "APPLICATION" and "Executable doesn't exist" in error:
            category, reason = "BROWSER", error.split("\n")[0][:200]
        extra = {"network_probe": network_probe(obs["ehub_host"])} if category == "NETWORK" else {}
        return blocked(obs, "sign_in", category, reason, **extra)
    if error and not obs["shipment_list"]:
        return blocked(obs, "shipment_list", "APPLICATION", error[:200])
    if record is None:
        return blocked(obs, "shipment", "APPLICATION",
                       "{0} is not in the {1} list filtered to {2!r} ({3} page(s) read)".format(
                           reference, A.SOURCE_VIEW, A.TARGET_STATUS,
                           (obs["shipment_list"] or {}).get("pages_read", 0)))
    if not obs["writes"]:
        return blocked(obs, "write", "APPLICATION",
                       "nothing was written to eHub: {0}".format(
                           (record or {}).get("error") or "the carrier gave no date"))
    obs["result"] = "COMPLETED"
    return obs


# ── reporting ──────────────────────────────────────────────────────────────

def report(obs):
    """POST the observation on the worker channel. (ok, message, response)."""
    url, token = os.environ.get("ATA_CONTROL_PLANE_URL"), os.environ.get("ATA_WORKER_TOKEN")
    if not url or not token:
        return False, "not reported: ATA_CONTROL_PLANE_URL / ATA_WORKER_TOKEN are not set " \
                      "on this machine", None
    from worker.agent import ControlPlane
    try:
        cp = ControlPlane(url, token, os.environ.get("ATA_CA_FILE"))
        status, data, _h = cp.request("POST", "/worker/v1/observations", obs)
    except Exception as error:
        return False, "not reported: the control plane could not be reached ({0})".format(
            type(error).__name__), None
    if status != 200:
        return False, "not reported: the control plane answered HTTP {0}".format(status), data
    return True, "reported to {0}".format(url), data


def finish(obs, send=True, out_dir=None, log=print):
    level, reasons = V.classify(obs, channel())
    obs["level_here"], obs["level_reasons"] = level, reasons
    obs["finished_at"] = _now()
    folder = Path(out_dir) if out_dir else _log_folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "verify-{0}-{1}.json".format(obs["kind"], obs["run_id"])
        path.write_text(json.dumps(obs, indent=2, default=str), encoding="utf-8")
        obs["saved_as"] = str(path)
    except Exception as error:
        obs["saved_as"] = "not saved: {0}".format(error)
    if send:
        ok, message, response = report(obs)
        obs["reported"] = {"ok": ok, "message": message,
                           "control_plane_level": (response or {}).get("level")
                           if isinstance(response, dict) else None,
                           "observation_id": (response or {}).get("observation_id")
                           if isinstance(response, dict) else None}
    text = V.line(level, obs, reasons)
    try:
        import update_eta as A
        A.write_log(text)
    except Exception:
        pass
    log(json.dumps(obs, indent=2, default=str))
    log(text)
    return level


def _log_folder():
    try:
        import update_eta as A
        return Path(A.LOG_FILE).parent
    except Exception:
        return ROOT / "logs"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m worker.verify",
                                     description="Real eHub verification, on the worker.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ehub", help="the connection, the list, one shipment and its status")
    e.add_argument("--reference")
    e.add_argument("--no-report", action="store_true")
    t = sub.add_parser("eta", help="the ETA write path for one shipment (writes to eHub)")
    t.add_argument("--reference", required=True)
    t.add_argument("--no-report", action="store_true")
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("NOTE: this is not the Windows worker ({0}). What it observes here is not "
              "taken as REAL.".format(platform.system()), flush=True)
    obs = observe_ehub(args.reference) if args.cmd == "ehub" else prove_eta(args.reference)
    level = finish(obs, send=not args.no_report)
    return 0 if level in (V.REAL_OBSERVED, V.REAL_VERIFIED) and \
        (args.cmd == "ehub" or level == V.REAL_VERIFIED) else 1


if __name__ == "__main__":
    sys.exit(main())
