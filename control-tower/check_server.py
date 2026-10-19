"""
Is this server ready to run the tower on its own?

    python check_server.py          (or check_server.bat)

Read-only: it changes nothing. Each line is PASS, WARN (works, but check it),
FAIL (must be fixed) or SKIP (not applicable on this system). The exit code
is 1 when anything FAILs.

    Python, packages      the versions the automation needs
    browser               Microsoft Edge present, and a real browser window
                          opens and closes
    dashboard             the tower is listening on 8787 — on this machine and
                          on the address colleagues use
    Hub, carriers         this machine can reach them (a plain connection
                          and an HTTP answer; nothing is signed in to)
    Windows set-up        the scheduled task, sleep settings, firewall rule,
                          credentials file, and which session this is
"""

import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
WINDOWS = os.name == "nt"
PORT = int(os.environ.get("ATA_DASHBOARD_PORT") or 8787)
CARRIERS = ("www.afklcargo.com", "www.qrcargo.com", "aviationcargo.dhl.com",
            "astral.fr8booking.com", "www.lufthansa-cargo.com")
RESULTS = []


def report(state, label, detail=""):
    RESULTS.append(state)
    print("  {0:<5} {1}{2}".format(state, label, ("  — " + detail) if detail else ""))


def hub_host():
    text = (HERE / "update_eta.py").read_text(encoding="utf-8", errors="replace")
    match = re.search(r'INTERNAL_URL\s*=\s*\(\s*"https?://([^/"]+)', text)
    return match.group(1) if match else None


def reach(host, timeout=8):
    """(ok, detail) for a TLS connection and one HTTP request to `host`."""
    started = time.time()
    try:
        request = urllib.request.Request("https://{0}/".format(host), method="GET",
                                         headers={"User-Agent": "Mozilla/5.0 (check_server)"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, "HTTP {0} in {1:.1f}s".format(response.status, time.time() - started)
    except urllib.error.HTTPError as error:
        # An answer, even a refusal, means the network path is there.
        return True, "HTTP {0} in {1:.1f}s".format(error.code, time.time() - started)
    except Exception as error:
        return False, "{0}: {1}".format(type(error).__name__, str(error)[:90])


def lan_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def dashboard(address):
    try:
        with urllib.request.urlopen("http://{0}:{1}/".format(address, PORT), timeout=5) as r:
            return True, "HTTP {0}".format(r.status)
    except urllib.error.HTTPError as error:
        return True, "HTTP {0} (asks for the access key, as it should)".format(error.code)
    except Exception as error:
        return False, type(error).__name__


def run(args):
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return out.returncode, (out.stdout or "") + (out.stderr or "")
    except Exception as error:
        return 1, str(error)


def main():
    print("Server readiness — {0} ({1})".format(platform.node(), platform.platform()))
    print()

    # Python and packages
    v = sys.version_info
    report("PASS" if v >= (3, 11) else "FAIL", "Python {0}.{1}".format(v.major, v.minor),
           "" if v >= (3, 11) else "3.11 or newer is required")
    for module, label in (("playwright", "Playwright"), ("fitz", "PyMuPDF (PO PDFs)"),
                          ("openpyxl", "openpyxl (PO Excel)")):
        try:
            __import__(module)
            report("PASS", label + " installed")
        except Exception:
            report("FAIL", label + " missing", "run: python -m pip install -r requirements.txt")

    # Browser
    edge = None
    if WINDOWS:
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            candidate = Path(base or "") / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if candidate.exists():
                edge = candidate
        report("PASS" if edge else "FAIL", "Microsoft Edge", str(edge or "not installed"))
    else:
        report("SKIP", "Microsoft Edge", "not Windows — Chromium is used for this check")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            options = {"headless": not WINDOWS}
            if WINDOWS:
                options["channel"] = "msedge"
            elif Path("/opt/pw-browsers/chromium").exists():
                options["executable_path"] = "/opt/pw-browsers/chromium"
            browser = pw.chromium.launch(**options)
            page = browser.new_page()
            page.set_content("<title>check</title><p>ok</p>")
            ok = page.title() == "check"
            browser.close()
        report("PASS" if ok else "FAIL", "A browser window opens and closes"
               + (" (Edge, visible)" if WINDOWS else " (Chromium, headless)"))
    except Exception as error:
        report("FAIL", "A browser window opens and closes", str(error).splitlines()[0][:120])

    # Dashboard
    ok, detail = dashboard("127.0.0.1")
    report("PASS" if ok else "WARN", "Dashboard on this machine (port {0})".format(PORT),
           detail if ok else "not running — start the tower (START_TOWER.bat or sign in)")
    address = lan_address()
    if ok and address:
        ok2, detail2 = dashboard(address)
        report("PASS" if ok2 else "FAIL",
               "Dashboard on the network address colleagues use: http://{0}:{1}".format(
                   address, PORT),
               detail2 if ok2 else "not reachable — started without --share, or the firewall")

    # Hub and carriers
    host = hub_host()
    if host:
        ok, detail = reach(host)
        report("PASS" if ok else "FAIL", "Logistics Hub ({0}) reachable".format(host), detail)
    for carrier in CARRIERS:
        ok, detail = reach(carrier)
        report("PASS" if ok else "WARN", "Carrier site {0} reachable".format(carrier), detail)

    # Disk
    free = shutil.disk_usage(str(HERE)).free / 1e9
    report("PASS" if free >= 10 else "WARN", "Free disk {0:.0f} GB".format(free),
           "" if free >= 10 else "keep at least 10 GB free for logs and evidence")

    # Windows set-up
    if not WINDOWS:
        for label in ("Scheduled task 'Mantrac Tower'", "Never sleeps on mains power",
                      "Firewall rule for port {0}".format(PORT),
                      r"C:\Automation\credentials.txt", "Session type"):
            report("SKIP", label, "Windows only")
    else:
        code, _out = run(["schtasks", "/query", "/tn", "Mantrac Tower"])
        report("PASS" if code == 0 else "WARN", "Scheduled task 'Mantrac Tower'",
               "" if code == 0 else "not installed — run INSTALL_SERVER.bat")
        code, out = run(["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE"])
        ac = re.search(r"AC Power Setting Index:\s*0x([0-9a-f]+)", out, re.I)
        never = bool(ac) and int(ac.group(1), 16) == 0
        report("PASS" if never else "WARN", "Never sleeps on mains power",
               "" if never else "sleep is on — run INSTALL_SERVER.bat")
        code, _out = run(["netsh", "advfirewall", "firewall", "show", "rule",
                          "name=Mantrac Control Tower"])
        report("PASS" if code == 0 else "WARN", "Firewall rule for port {0}".format(PORT),
               "" if code == 0 else "missing — colleagues cannot open the dashboard")
        creds = Path(r"C:\Automation\credentials.txt")
        report("PASS" if creds.exists() else "FAIL", r"C:\Automation\credentials.txt present",
               "" if creds.exists() else "the Hub login file is missing")
        session = os.environ.get("SESSIONNAME", "")
        report("PASS" if session.lower() == "console" else "WARN",
               "Session type: {0}".format(session or "unknown"),
               "" if session.lower() == "console" else
               "a Remote Desktop session — leave it with disconnect_keep_running.bat, "
               "not by closing the window")

    print()
    fails, warns = RESULTS.count("FAIL"), RESULTS.count("WARN")
    print("  {0} passed, {1} warning(s), {2} failed, {3} skipped".format(
        RESULTS.count("PASS"), warns, fails, RESULTS.count("SKIP")))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
