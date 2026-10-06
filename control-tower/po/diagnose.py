"""
Real eHub connectivity, checked step by step, on the machine that runs it.

    python -m po ehub-check            (run it on the Windows worker)

Each stage either passes, fails, or is not reached because an earlier one
failed. The first failure is classified into exactly one category:

    NETWORK          DNS does not resolve, TCP 443 cannot be reached, or the
                     HTTPS connection is refused (a proxy, a firewall)
    AUTHENTICATION   no credentials file, or eHub refuses the sign-in
    BROWSER          the browser (Edge / Chromium) cannot be launched or
                     cannot open a page
    APPLICATION      signed in, but the shipment list (the table with its
                     BOL/AWB and Status columns) is not where the automation
                     expects it

Nothing is printed, logged or stored about the credentials beyond whether
the file exists and holds the two keys. Nothing in eHub is clicked or
changed: the check opens the shipment list and reads its header row.
"""

import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urlparse

STAGES = ("config", "dns", "tcp", "https", "credentials", "browser", "sign_in", "shipment_list")
CATEGORY = {"dns": "NETWORK", "tcp": "NETWORK", "https": "NETWORK",
            "credentials": "AUTHENTICATION", "sign_in": "AUTHENTICATION",
            "browser": "BROWSER", "shipment_list": "APPLICATION", "config": "APPLICATION"}


def ehub_url():
    import update_eta as A
    return A.INTERNAL_URL


def ehub_host():
    return urlparse(ehub_url()).hostname


def _stage(report, name, ok, **detail):
    report["stages"].append(dict({"stage": name, "ok": ok, "at": datetime.now().astimezone()
                                  .isoformat(timespec="seconds")}, **detail))
    if not ok and report["result"] == "REACHABLE":
        report["result"] = "FAILED"
        report["failed_stage"] = name
        report["category"] = CATEGORY[name]
        report["reason"] = detail.get("error") or detail.get("detail")
    return ok


def check(url=None, launch_browser=True, credentials=None, timeout=15):
    """
    The report: {"result": "REACHABLE" | "FAILED", "category", "failed_stage",
    "reason", "stages": [...], "url", "host", "machine", "at"}.
    `credentials` overrides the credentials file (tests); never printed.
    """
    url = url or ehub_url()
    host = urlparse(url).hostname
    port = urlparse(url).port or (443 if url.startswith("https") else 80)
    real_host = urlparse(ehub_url()).hostname
    report = {"check": "ehub-connectivity",
              # REAL only when the host checked IS the real eHub — never a label.
              "source": "REAL" if host == real_host else "TEST", "url": url, "host": host,
              "machine": socket.gethostname(), "at": datetime.now().astimezone().isoformat(
                  timespec="seconds"), "result": "REACHABLE", "stages": []}
    _stage(report, "config", bool(host), detail="eHub address from update_eta.INTERNAL_URL")

    # NETWORK — DNS, TCP, HTTPS
    try:
        addrs = sorted({a[4][0] for a in socket.getaddrinfo(host, port)})
        _stage(report, "dns", True, addresses=addrs[:4])
    except Exception as error:
        _stage(report, "dns", False, error="{0} does not resolve: {1}".format(host, error))
        return _finish(report)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    try:
        with socket.create_connection((addrs[0], port), timeout=timeout):
            pass
        _stage(report, "tcp", True, address=addrs[0], port=port)
    except Exception as error:
        _stage(report, "tcp", False, error="TCP {0}:{1} not reachable: {2}".format(
            addrs[0], port, error), proxy_configured=bool(proxy))
        return _finish(report)
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=ssl.create_default_context()) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code                    # 401 / 403 here is the server answering
    except Exception as error:
        _stage(report, "https", False, error="HTTPS to {0} failed: {1}".format(host, error),
               proxy_configured=bool(proxy))
        return _finish(report)
    _stage(report, "https", status < 500, http_status=status,
           detail="the server answered (a 401 before sign-in is expected)",
           error=None if status < 500 else "eHub answered HTTP {0}".format(status))
    if status >= 500:
        return _finish(report)

    # AUTHENTICATION — the credentials file exists and holds both keys
    if credentials is None:
        import update_eta as A
        try:
            credentials = A.load_credentials()
            _stage(report, "credentials", True, file=str(A.CREDENTIALS_FILE),
                   detail="USERNAME and PASSWORD present (values not read out)")
        except Exception as error:
            _stage(report, "credentials", False, file=str(A.CREDENTIALS_FILE),
                   error=str(error).split(":")[0] if "credentials" in str(error).lower()
                   else "the credentials file could not be read")
            return _finish(report)
    else:
        _stage(report, "credentials", True, detail="supplied by the caller (values not read out)")
    if not launch_browser:
        return _finish(report)

    # BROWSER, SIGN-IN, the SHIPMENT LIST
    try:
        from playwright.sync_api import sync_playwright
    except Exception as error:
        _stage(report, "browser", False, error="Playwright is not installed: {0}".format(error))
        return _finish(report)
    with sync_playwright() as playwright:
        browser = None
        try:
            launch = {"headless": os.environ.get("PO_HEADLESS", "1") not in ("0", "false")}
            executable = os.environ.get("PO_BROWSER_EXECUTABLE")
            channel = os.environ.get("PO_BROWSER_CHANNEL", "msedge")
            if executable:
                launch["executable_path"] = executable
            elif channel:
                launch["channel"] = channel
            try:
                browser = playwright.chromium.launch(**launch)
            except Exception:
                if "channel" not in launch:
                    raise
                launch.pop("channel", None)
                browser = playwright.chromium.launch(**launch)
            context = browser.new_context(http_credentials={"username": credentials[0],
                                                            "password": credentials[1]})
            page = context.new_page()
            _stage(report, "browser", True, engine=browser.browser_type.name,
                   version=browser.version, channel=launch.get("channel"))
        except Exception as error:
            _stage(report, "browser", False, error=str(error).split("\n")[0][:200])
            if browser:
                browser.close()
            return _finish(report)
        try:
            import update_eta as A
            A.write_log = lambda *a, **k: None
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1200)
            status = response.status if response else None
            if page.url.startswith("chrome-error") or status in (401, 403):
                _stage(report, "sign_in", False, http_status=status, landed=_host(page.url),
                       error="eHub refused the sign-in (HTTP {0})".format(status))
                return _finish(report, browser)
            # The automation's own sign-in, pointed at the address being checked.
            saved_url = A.INTERNAL_URL
            A.INTERNAL_URL = url
            try:
                A.login_internal(page, credentials[0], credentials[1])
            finally:
                A.INTERNAL_URL = saved_url
            _stage(report, "sign_in", True, http_status=status, landed=_host(page.url))
        except Exception as error:
            _stage(report, "sign_in", False, error=str(error).split("\n")[0][:200])
            return _finish(report, browser)
        try:
            table = A.find_shipments_table(page)
            columns = A.build_header_map(table)
            wanted = [c for c in ("bol_awb", "status") if not isinstance(columns.get(c), int)]
            rows = table.locator("tbody tr").count()
            _stage(report, "shipment_list", not wanted, rows=rows, columns=sorted(
                k for k, v in columns.items() if isinstance(v, int)),
                error="the shipment list has no {0} column".format(" / ".join(wanted))
                if wanted else None)
        except Exception as error:
            _stage(report, "shipment_list", False, error="the shipment list was not found: "
                   "{0}".format(str(error).split("\n")[0][:200]))
        return _finish(report, browser)


def _host(url):
    return urlparse(url or "").hostname


def _finish(report, browser=None):
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
    reached = {s["stage"] for s in report["stages"]}
    report["not_reached"] = [s for s in STAGES if s not in reached]
    return report
