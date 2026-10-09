"""
What does the automation cost this SERVER while it runs?

Written for one specific observation, made on the machine itself: with the
automation off, Microsoft Edge opens AFKL myCargo; with the automation on, it
cannot; with the automation off again, it can. That is not a statement about
AFKL's markup or about Playwright's navigation — it is a statement about
something the whole machine shares.

This script measures the machine, not the carrier's page. Run it four times,
in this order, and it writes one snapshot per phase and then compares them.

    python diagnose_server.py A     automation OFF                (control)
    python diagnose_server.py B     automation RUNNING, no AFKL lookup yet
    python diagnose_server.py C     automation RUNNING, after an AFKL lookup
    python diagnose_server.py D     automation STOPPED            (recovery)
    python diagnose_server.py report

B is the phase that decides it. If AFKL already fails in B, the automation is
taking a shared resource before it has touched AFKL at all. If B passes and C
fails, the cost is specific to the AFKL lookup path.

Every probe is bounded and each phase makes at most a handful of requests to
the carrier. Nothing here changes any setting on the machine; it only reads.

    python diagnose_server.py D --watch    poll until AFKL answers again
"""

import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOTS = HERE / "logs" / "server_probe"
HOST = "www.afklcargo.com"
PATH = "/mycargo/shipment/singlesearch"
WINDOWS = os.name == "nt"
PHASES = {
    "A": "automation OFF (control)",
    "B": "automation RUNNING, no AFKL lookup executed",
    "C": "automation RUNNING, one AFKL lookup executed",
    "D": "automation STOPPED (recovery)",
}


# ─────────────────────────────────────────────────────────────────────────
# shelling out, carefully
# ─────────────────────────────────────────────────────────────────────────
def run(command, shell=None, timeout=45):
    """Run one command and return its stdout, or "" and never raise."""
    try:
        if shell == "powershell":
            argv = ["powershell", "-NoProfile", "-NonInteractive",
                    "-Command", command]
        elif shell == "cmd":
            argv = ["cmd", "/c", command]
        else:
            argv = command
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
        return (done.stdout or "").strip()
    except Exception as error:
        return "ERROR: {0}".format(str(error).split("\n")[0][:120])


def ps(command, timeout=45):
    return run(command, shell="powershell", timeout=timeout)


def number(text, default=None):
    found = re.search(r"-?\d+", str(text or ""))
    return int(found.group(0)) if found else default


# ─────────────────────────────────────────────────────────────────────────
# the metrics
# ─────────────────────────────────────────────────────────────────────────
def carrier_addresses():
    """Every IP the carrier currently resolves to, so its sockets can be told
    apart from everything else's."""
    addresses = set()
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            for entry in socket.getaddrinfo(HOST, 443, family, socket.SOCK_STREAM):
                addresses.add(entry[4][0])
        except Exception:
            pass
    return sorted(addresses)


def cpu_and_memory():
    if WINDOWS:
        load = number(ps("(Get-CimInstance Win32_Processor | "
                         "Measure-Object -Property LoadPercentage "
                         "-Average).Average"))
        free = number(ps("(Get-CimInstance Win32_OperatingSystem)"
                         ".FreePhysicalMemory"))
        total = number(ps("(Get-CimInstance Win32_OperatingSystem)"
                          ".TotalVisibleMemorySize"))
        return {"cpu_percent": load,
                "memory_free_mb": (free // 1024) if free else None,
                "memory_total_mb": (total // 1024) if total else None}
    load = run(["bash", "-lc", "cat /proc/loadavg"])
    free = run(["bash", "-lc", "free -m | awk '/Mem:/{print $7, $2}'"])
    parts = free.split()
    return {"load_average": load.split(" ")[0] if load else None,
            "memory_free_mb": number(parts[0]) if parts else None,
            "memory_total_mb": number(parts[1]) if len(parts) > 1 else None}


def browser_processes():
    """How many browser processes exist, and what they hold."""
    if WINDOWS:
        raw = ps("$names = 'msedge','chrome','chromium','chrome-headless-shell';"
                 "$out = @(); foreach ($n in $names) {"
                 "  $p = Get-Process -Name $n -ErrorAction SilentlyContinue;"
                 "  if ($p) { $out += [pscustomobject]@{name=$n;"
                 "    count=@($p).Count;"
                 "    handles=(($p | Measure-Object Handles -Sum).Sum);"
                 "    ws_mb=[int](($p | Measure-Object WorkingSet64 -Sum).Sum/1MB)} } };"
                 "$out | ConvertTo-Json -Compress")
        try:
            parsed = json.loads(raw) if raw.startswith(("{", "[")) else []
        except Exception:
            parsed = []
        if isinstance(parsed, dict):
            parsed = [parsed]
        return {"by_name": parsed,
                "total": sum(int(entry.get("count") or 0) for entry in parsed),
                "handles": sum(int(entry.get("handles") or 0) for entry in parsed)}
    count = number(run(["bash", "-lc",
                        "ps -eo comm | grep -ciE 'chrome|chromium|msedge' || true"]), 0)
    return {"by_name": [], "total": count, "handles": None}


def automation_processes():
    if WINDOWS:
        raw = ps("$p = Get-CimInstance Win32_Process -Filter "
                 "\"Name like '%python%'\" | Where-Object "
                 "{ $_.CommandLine -match 'update_eta' };"
                 "@($p).Count")
        return {"update_eta_running": (number(raw, 0) or 0) > 0,
                "update_eta_processes": number(raw, 0)}
    # The bracket keeps pgrep from matching the very command line that runs
    # it, which otherwise reports the automation as running when it is not.
    count = number(run(["bash", "-lc",
                        "pgrep -fc '[u]pdate_eta' || true"]), 0)
    return {"update_eta_running": (count or 0) > 0,
            "update_eta_processes": count}


def tcp_picture(addresses):
    """
    Connection counts overall, to the carrier, and against the machine's
    ephemeral port pool — the resource that is actually shared.
    """
    picture = {"by_state": {}, "total": None, "to_carrier": None,
               "to_carrier_by_state": {}, "to_carrier_by_process": [],
               "time_wait": None, "ephemeral_range": None,
               "ephemeral_ports_in_use": None, "ephemeral_headroom": None,
               "distinct_remote_hosts": None}
    if WINDOWS:
        raw = ps("Get-NetTCPConnection | Group-Object State | "
                 "Select-Object Name,Count | ConvertTo-Json -Compress")
        try:
            groups = json.loads(raw) if raw.startswith(("{", "[")) else []
            if isinstance(groups, dict):
                groups = [groups]
            picture["by_state"] = {str(g.get("Name")): int(g.get("Count") or 0)
                                   for g in groups}
        except Exception:
            pass
        picture["total"] = sum(picture["by_state"].values()) or None
        picture["time_wait"] = picture["by_state"].get("TimeWait")
        if addresses:
            listed = ",".join("'{0}'".format(a) for a in addresses)
            raw = ps("Get-NetTCPConnection -RemoteAddress @({0}) "
                     "-ErrorAction SilentlyContinue | Group-Object State | "
                     "Select-Object Name,Count | ConvertTo-Json -Compress"
                     .format(listed))
            try:
                groups = json.loads(raw) if raw.startswith(("{", "[")) else []
                if isinstance(groups, dict):
                    groups = [groups]
                picture["to_carrier_by_state"] = {
                    str(g.get("Name")): int(g.get("Count") or 0) for g in groups}
                picture["to_carrier"] = sum(
                    picture["to_carrier_by_state"].values())
            except Exception:
                pass
            # WHICH program is holding them. "Our automation" and "something
            # else on this server" are different problems with different
            # fixes, and the owning process is what tells them apart.
            raw = ps("Get-NetTCPConnection -RemoteAddress @({0}) "
                     "-ErrorAction SilentlyContinue | Group-Object "
                     "OwningProcess | ForEach-Object {{ "
                     "[pscustomobject]@{{ pid = $_.Name; count = $_.Count; "
                     "name = (Get-Process -Id $_.Name -ErrorAction "
                     "SilentlyContinue).ProcessName }} }} | "
                     "ConvertTo-Json -Compress".format(listed))
            try:
                owners = json.loads(raw) if raw.startswith(("{", "[")) else []
                if isinstance(owners, dict):
                    owners = [owners]
                picture["to_carrier_by_process"] = owners
            except Exception:
                pass
        # The ephemeral pool. Windows hands these out to every program on the
        # machine, so one program can starve the rest.
        pool = run("netsh int ipv4 show dynamicport tcp", shell="cmd")
        start = number(re.search(r"Start Port\s*:\s*(\d+)", pool).group(1)) \
            if re.search(r"Start Port\s*:\s*(\d+)", pool) else None
        size = number(re.search(r"Number of Ports\s*:\s*(\d+)", pool).group(1)) \
            if re.search(r"Number of Ports\s*:\s*(\d+)", pool) else None
        if start and size:
            picture["ephemeral_range"] = "{0}-{1} ({2} ports)".format(
                start, start + size - 1, size)
            used = number(ps(
                "@(Get-NetTCPConnection | Where-Object "
                "{{ $_.LocalPort -ge {0} -and $_.LocalPort -le {1} }}).Count"
                .format(start, start + size - 1)))
            picture["ephemeral_ports_in_use"] = used
            if used is not None:
                picture["ephemeral_headroom"] = size - used
        picture["distinct_remote_hosts"] = number(ps(
            "@(Get-NetTCPConnection -State Established | "
            "Select-Object -ExpandProperty RemoteAddress -Unique).Count"))
    else:
        raw = run(["bash", "-lc", "ss -tan 2>/dev/null | tail -n +2 | "
                                  "awk '{print $1}' | sort | uniq -c"])
        for line in raw.splitlines():
            bits = line.split()
            if len(bits) == 2:
                picture["by_state"][bits[1]] = int(bits[0])
        picture["total"] = sum(picture["by_state"].values()) or None
        picture["time_wait"] = picture["by_state"].get("TIME-WAIT")
        if addresses:
            joined = "|".join(re.escape(a) for a in addresses)
            picture["to_carrier"] = number(run(
                ["bash", "-lc", "ss -tan 2>/dev/null | grep -cE '{0}' || true"
                 .format(joined)]), 0)
        picture["ephemeral_range"] = run(
            ["bash", "-lc", "cat /proc/sys/net/ipv4/ip_local_port_range"])
    return picture


def network_configuration():
    """Anything machine-wide that could redirect or inspect traffic."""
    configuration = {
        "env_proxy": {name: os.environ.get(name)
                      for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy",
                                   "https_proxy", "NO_PROXY", "no_proxy")
                      if os.environ.get(name)},
        "carrier_addresses": carrier_addresses(),
    }
    if WINDOWS:
        configuration["winhttp_proxy"] = run("netsh winhttp show proxy",
                                             shell="cmd")
        configuration["wininet_proxy"] = ps(
            "$k = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\"
            "Internet Settings'; (Get-ItemProperty $k -ErrorAction "
            "SilentlyContinue | Select-Object ProxyEnable,ProxyServer,"
            "AutoConfigURL | ConvertTo-Json -Compress)")
        configuration["dns_cache_for_carrier"] = ps(
            "Get-DnsClientCache -ErrorAction SilentlyContinue | Where-Object "
            "{ $_.Entry -like '*afklcargo*' } | Select-Object Entry,Data,"
            "TimeToLive | ConvertTo-Json -Compress")
        configuration["tcp_timed_wait_delay"] = ps(
            "(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\"
            "Tcpip\\Parameters' -ErrorAction SilentlyContinue)"
            ".TcpTimedWaitDelay")
    return configuration


# ─────────────────────────────────────────────────────────────────────────
# the two probes
# ─────────────────────────────────────────────────────────────────────────
CHALLENGE_MARKERS = (
    "access denied", "reference #", "you don't have permission",
    "request blocked", "forbidden", "unusual traffic", "bot detected",
    "are you a human", "pardon our interruption", "incapsula", "cloudflare",
)
INTERESTING_HEADERS = (
    "server", "content-type", "retry-after", "x-reference-error",
    "x-akamai-request-id", "cf-ray", "x-cache", "via", "connection",
    "x-frame-options", "set-cookie",
)
# A site that is not the carrier, to tell "this machine cannot reach the
# internet" apart from "this machine cannot reach the carrier".
CONTROL_HOST = os.environ.get("CONTROL_HOST", "www.microsoft.com")


def raw_probe(timeout=20, host=None, path=None):
    """
    Reach the carrier with nothing but a socket: no browser, no Playwright.

    This is the line between "this machine cannot open a connection to the
    carrier" and "Chromium cannot render the carrier's page". A raw probe that
    fails while the automation runs points at sockets, ports or the carrier's
    own front door. A raw probe that succeeds while Edge hangs points at the
    browser.
    """
    host = host or HOST
    path = path or PATH
    result = {"host": host, "dns_ms": None, "connect_ms": None, "tls_ms": None,
              "first_byte_ms": None, "status": None, "bytes": None,
              "error": None, "local_port": None, "headers": {},
              "body_starts": None, "challenge": None, "tls_version": None}
    started = time.time()
    try:
        addresses = socket.getaddrinfo(host, 443, socket.AF_INET,
                                       socket.SOCK_STREAM)
        result["dns_ms"] = int((time.time() - started) * 1000)
        family, kind, proto, _, address = addresses[0]
        sock = socket.socket(family, kind, proto)
        sock.settimeout(timeout)
        mark = time.time()
        sock.connect(address)
        result["connect_ms"] = int((time.time() - mark) * 1000)
        result["local_port"] = sock.getsockname()[1]
        mark = time.time()
        wrapped = ssl.create_default_context().wrap_socket(
            sock, server_hostname=host)
        result["tls_ms"] = int((time.time() - mark) * 1000)
        try:
            result["tls_version"] = wrapped.version()
        except Exception:
            pass
        mark = time.time()
        # A browser's user agent, because a site that is refusing browsers
        # will happily answer a probe that does not look like one — and then
        # the probe proves nothing about what Edge is seeing.
        wrapped.sendall(
            ("GET {0} HTTP/1.1\r\nHost: {1}\r\nUser-Agent: Mozilla/5.0 "
             "(Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
             "Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0\r\n"
             "Accept: text/html,application/xhtml+xml\r\n"
             "Accept-Language: en-US,en;q=0.9\r\n"
             "Connection: close\r\n\r\n".format(path, host)
             ).encode("ascii"))
        chunk = wrapped.recv(8192)
        result["first_byte_ms"] = int((time.time() - mark) * 1000)
        head = chunk.split(b"\r\n\r\n")[0].decode("latin-1", "replace")
        lines = head.split("\r\n")
        result["status"] = number(lines[0].split(" ")[1]) if " " in lines[0] \
            else None
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, _, value = line.partition(":")
            if name.strip().lower() in INTERESTING_HEADERS:
                result["headers"][name.strip().lower()] = value.strip()[:120]
        body = b""
        total = len(chunk)
        if b"\r\n\r\n" in chunk:
            body = chunk.split(b"\r\n\r\n", 1)[1]
        while True:
            more = wrapped.recv(65536)
            if not more:
                break
            total += len(more)
            if len(body) < 4000:
                body += more
            if total > 400000:
                break
        result["bytes"] = total
        readable = " ".join(re.sub(r"<[^>]+>", " ", body.decode(
            "utf-8", "replace")).split())
        result["body_starts"] = readable[:300]
        lowered = readable.lower()
        # An edge that has decided this address is a robot says so, and the
        # words it uses are the whole answer to "what is blocking us".
        for marker in CHALLENGE_MARKERS:
            if marker in lowered:
                result["challenge"] = marker
                break
        wrapped.close()
    except Exception as error:
        result["error"] = "{0}: {1}".format(
            type(error).__name__, str(error).split("\n")[0][:140])
    return result


def edge_probe(timeout_ms=45000):
    """
    Open the carrier in a real Edge, the way the operator does, and time it.

    Deliberately a separate browser from the automation's: the question is
    whether a SECOND browser can still get through while the first one runs.
    """
    result = {"ran": False, "reached": False, "ms": None, "ready_state": None,
              "title": None, "error": None, "final_url": None,
              "requests": 0, "by_status": {}, "failed": [], "blocked": [],
              "still_pending": []}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as error:
        result["error"] = "playwright not importable: {0}".format(
            str(error)[:100])
        return result
    started = time.time()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge",
                                                 headless=False)
            try:
                page = browser.new_page()
                result["ran"] = True

                # A blank page is not one failure, it is a set of them. This
                # records every request the page made, what came back, and
                # what never came back at all — which is the difference
                # between "the site refused us" and "the site never answered".
                pending = {}

                def started_request(request):
                    result["requests"] += 1
                    pending[request] = time.time()

                def finished(response):
                    pending.pop(response.request, None)
                    code = str(response.status)
                    result["by_status"][code] = \
                        result["by_status"].get(code, 0) + 1
                    if response.status in (401, 403, 405, 429) or \
                            response.status >= 500:
                        if len(result["blocked"]) < 8:
                            result["blocked"].append("{0} {1}".format(
                                response.status, response.url[:110]))

                def failed(request):
                    pending.pop(request, None)
                    if len(result["failed"]) < 8:
                        result["failed"].append("{0} {1}".format(
                            (request.failure or "failed")[:40],
                            request.url[:110]))

                page.on("request", started_request)
                page.on("response", finished)
                page.on("requestfailed", failed)
                try:
                    page.goto("https://{0}{1}".format(HOST, PATH),
                              wait_until="commit", timeout=timeout_ms)
                except Exception as error:
                    result["error"] = str(error).split("\n")[0][:140]
                result["ms"] = int((time.time() - started) * 1000)
                try:
                    result["ready_state"] = page.evaluate(
                        "document.readyState")
                    result["title"] = (page.title() or "")[:80]
                    result["final_url"] = page.url[:160]
                    text = page.evaluate(
                        "document.body ? document.body.innerText.length : 0")
                    result["body_text_length"] = text
                    result["reached"] = bool(
                        result["ready_state"] in ("interactive", "complete")
                        and text > 400)
                    for request, when in list(pending.items())[:8]:
                        try:
                            result["still_pending"].append(
                                "{0}ms {1}".format(
                                    int((time.time() - when) * 1000),
                                    request.url[:110]))
                        except Exception:
                            continue
                except Exception as error:
                    result["error"] = (result["error"] or "") + " | read: " + \
                        str(error).split("\n")[0][:80]
            finally:
                browser.close()
    except Exception as error:
        result["error"] = str(error).split("\n")[0][:140]
        result["ms"] = int((time.time() - started) * 1000)
    return result


# ─────────────────────────────────────────────────────────────────────────
# one phase
# ─────────────────────────────────────────────────────────────────────────
def snapshot(phase, with_edge=True):
    addresses = carrier_addresses()
    taken = {
        "phase": phase,
        "meaning": PHASES.get(phase, phase),
        "at": datetime.now().isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "windows": WINDOWS,
        "cpu_memory": cpu_and_memory(),
        "browsers": browser_processes(),
        "automation": automation_processes(),
        "tcp": tcp_picture(addresses),
        "network": network_configuration(),
        "raw_probe": raw_probe(),
        "control_probe": raw_probe(host=CONTROL_HOST, path="/"),
    }
    if with_edge:
        taken["edge_probe"] = edge_probe()
    return taken


def describe(taken):
    tcp = taken["tcp"]
    print("=" * 74)
    print("PHASE {0} — {1}".format(taken["phase"], taken["meaning"]))
    print("=" * 74)
    print("  automation running        : {0} ({1} process(es))".format(
        taken["automation"]["update_eta_running"],
        taken["automation"]["update_eta_processes"]))
    print("  browser processes         : {0}   handles: {1}".format(
        taken["browsers"]["total"], taken["browsers"]["handles"]))
    print("  cpu / free memory         : {0} / {1} MB".format(
        taken["cpu_memory"].get("cpu_percent")
        or taken["cpu_memory"].get("load_average"),
        taken["cpu_memory"].get("memory_free_mb")))
    print("  TCP connections, total    : {0}".format(
        tcp["total"] if tcp["total"] is not None
        else "could not be read on this machine"))
    print("  ...by state               : {0}".format(tcp["by_state"]))
    print("  ...to the carrier         : {0}  {1}".format(
        tcp["to_carrier"], tcp["to_carrier_by_state"] or ""))
    print("  TIME_WAIT                 : {0}".format(tcp["time_wait"]))
    print("  ephemeral port range      : {0}".format(tcp["ephemeral_range"]))
    print("  ephemeral ports in use    : {0}   headroom: {1}".format(
        tcp["ephemeral_ports_in_use"], tcp["ephemeral_headroom"]))
    print("  distinct remote hosts     : {0}".format(
        tcp["distinct_remote_hosts"]))
    print("  carrier resolves to       : {0}".format(
        ", ".join(taken["network"]["carrier_addresses"]) or "NOTHING"))
    proxy = taken["network"]["env_proxy"]
    print("  proxy in the environment  : {0}".format(
        ", ".join("{0}={1}".format(name, str(value)[:40])
                  for name, value in sorted(proxy.items())) or "none"))
    if tcp.get("to_carrier_by_process"):
        print("  ...held by                : {0}".format(", ".join(
            "{0} (pid {1}) x{2}".format(owner.get("name") or "?",
                                        owner.get("pid"), owner.get("count"))
            for owner in tcp["to_carrier_by_process"])))
    raw = taken["raw_probe"]
    print("  raw socket probe          : {0}".format(
        "HTTP {0}, {1} bytes, connect {2}ms, TLS {3}ms, first byte {4}ms"
        .format(raw["status"], raw["bytes"], raw["connect_ms"], raw["tls_ms"],
                raw["first_byte_ms"]) if not raw["error"]
        else "FAILED — {0}".format(raw["error"])))
    if raw.get("headers"):
        print("  ...its headers            : {0}".format(", ".join(
            "{0}={1}".format(name, value)
            for name, value in sorted(raw["headers"].items()))[:400]))
    if raw.get("challenge"):
        print("  ...THE SITE IS REFUSING US: it said {0!r}".format(
            raw["challenge"]))
    if raw.get("body_starts"):
        print("  ...it answered            : {0}".format(
            raw["body_starts"][:200]))
    control = taken.get("control_probe") or {}
    print("  a site that is NOT them   : {0} -> {1}".format(
        control.get("host"),
        "HTTP {0} in {1}ms".format(control.get("status"),
                                   control.get("first_byte_ms"))
        if not control.get("error") else "FAILED — {0}".format(
            control.get("error"))))
    edge = taken.get("edge_probe")
    if edge:
        print("  Edge probe                : {0}".format(
            "reached the page in {0}ms (readyState={1})".format(
                edge["ms"], edge["ready_state"]) if edge["reached"]
            else "DID NOT REACH — {0} ({1}ms, readyState={2})".format(
                edge["error"] or "no content", edge["ms"],
                edge["ready_state"])))
        print("  ...requests it made       : {0}   by status: {1}".format(
            edge.get("requests"), edge.get("by_status")))
        for line in edge.get("blocked") or []:
            print("      REFUSED  {0}".format(line))
        for line in edge.get("failed") or []:
            print("      FAILED   {0}".format(line))
        for line in edge.get("still_pending") or []:
            print("      NEVER ANSWERED  {0}".format(line))
    print()


def store(taken):
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    where = SNAPSHOTS / "phase_{0}.json".format(taken["phase"])
    where.write_text(json.dumps(taken, indent=1, default=str),
                     encoding="utf-8")
    print("  written to {0}".format(where))
    return where


def watch_recovery(limit_seconds=420, every=10):
    """How long after the automation stops does the carrier answer again?"""
    print("=" * 74)
    print("RECOVERY — polling until the carrier answers, or {0}s".format(
        limit_seconds))
    print("=" * 74)
    started = time.time()
    while time.time() - started < limit_seconds:
        elapsed = int(time.time() - started)
        raw = raw_probe(timeout=15)
        addresses = carrier_addresses()
        held = tcp_picture(addresses)["to_carrier"]
        print("  +{0:>4}s  raw probe: {1:<38} connections to carrier: {2}"
              .format(elapsed,
                      "HTTP {0}".format(raw["status"]) if not raw["error"]
                      else raw["error"][:38], held))
        if not raw["error"] and raw["status"] and raw["status"] < 500:
            print()
            print("  The carrier answered {0}s after the automation stopped."
                  .format(elapsed))
            return elapsed
        time.sleep(every)
    print()
    print("  Still not answering after {0}s.".format(limit_seconds))
    return None


# ─────────────────────────────────────────────────────────────────────────
# the comparison
# ─────────────────────────────────────────────────────────────────────────
def report():
    taken = {}
    for phase in "ABCD":
        where = SNAPSHOTS / "phase_{0}.json".format(phase)
        if where.exists():
            try:
                taken[phase] = json.loads(where.read_text(encoding="utf-8"))
            except Exception as error:
                print("  could not read {0}: {1}".format(where, error))
    if not taken:
        print("No snapshots yet. Run phases A, B, C and D first.")
        return 1

    print("=" * 74)
    print("A / B / C / D — WHAT CHANGED")
    print("=" * 74)
    rows = [
        ("automation running", lambda s: s["automation"]["update_eta_running"]),
        ("browser processes", lambda s: s["browsers"]["total"]),
        ("browser handles", lambda s: s["browsers"]["handles"]),
        ("TCP total", lambda s: s["tcp"]["total"]),
        ("TIME_WAIT", lambda s: s["tcp"]["time_wait"]),
        ("ephemeral in use", lambda s: s["tcp"]["ephemeral_ports_in_use"]),
        ("ephemeral headroom", lambda s: s["tcp"]["ephemeral_headroom"]),
        ("connections to carrier", lambda s: s["tcp"]["to_carrier"]),
        ("distinct remote hosts", lambda s: s["tcp"]["distinct_remote_hosts"]),
        ("free memory MB", lambda s: s["cpu_memory"].get("memory_free_mb")),
        ("raw probe", lambda s: ("HTTP {0}".format(s["raw_probe"]["status"])
                                 if not s["raw_probe"]["error"]
                                 else "FAILED")),
        ("...refused us?", lambda s: s["raw_probe"].get("challenge") or "no"),
        ("raw connect ms", lambda s: s["raw_probe"]["connect_ms"]),
        ("raw first byte ms", lambda s: s["raw_probe"]["first_byte_ms"]),
        ("control site", lambda s: (
            "HTTP {0}".format((s.get("control_probe") or {}).get("status"))
            if not (s.get("control_probe") or {}).get("error") else "FAILED")),
        ("Edge requests", lambda s: (s.get("edge_probe") or {}).get(
            "requests")),
        ("Edge refused/failed", lambda s: len(
            ((s.get("edge_probe") or {}).get("blocked") or [])
            + ((s.get("edge_probe") or {}).get("failed") or []))),
        ("Edge reached AFKL", lambda s: (s.get("edge_probe") or {}).get(
            "reached")),
        ("Edge ms", lambda s: (s.get("edge_probe") or {}).get("ms")),
    ]
    order = [p for p in "ABCD" if p in taken]
    print("  {0:<24}{1}".format(
        "", "".join("{0:>12}".format(p) for p in order)))
    for name, read in rows:
        cells = []
        for phase in order:
            try:
                value = read(taken[phase])
            except Exception:
                value = None
            cells.append("{0:>12}".format(str(value)))
        print("  {0:<24}{1}".format(name, "".join(cells)))

    print()
    print("=" * 74)
    print("WHAT THAT MEANS")
    print("=" * 74)

    def reached(phase):
        """True, False, or None when the Edge probe did not run at all."""
        snap = taken.get(phase) or {}
        edge = snap.get("edge_probe") or {}
        if not edge.get("ran") and edge.get("reached") is None:
            return None
        return bool(edge.get("reached"))

    missing = [p for p in order if reached(p) is None]
    if missing:
        print("  Phase(s) {0} have no Edge measurement, so the conclusion "
              "below".format(", ".join(missing)))
        print("  rests on the raw socket probe alone. Re-run those phases "
              "without")
        print("  --no-edge to compare what the operator actually sees.")
        print()

    def raw_ok(phase):
        snap = taken.get(phase) or {}
        return bool(snap and not snap["raw_probe"]["error"]
                    and (snap["raw_probe"].get("status") or 0) < 400)

    def control_ok(phase):
        snap = (taken.get(phase) or {}).get("control_probe") or {}
        return bool(snap and not snap.get("error")
                    and (snap.get("status") or 0) < 400)

    def refused(phase):
        return ((taken.get(phase) or {}).get("raw_probe") or {}).get(
            "challenge")

    # The three mechanisms this experiment can tell apart, checked in the
    # order that makes each conclusion safe.
    named = False
    for phase in order:
        if refused(phase):
            snap = taken[phase]["raw_probe"]
            print("  THE CARRIER IS REFUSING THIS ADDRESS. In phase {0} a "
                  "plain".format(phase))
            print("  socket request — no Playwright, no automation — came "
                  "back HTTP {0}".format(snap.get("status")))
            print("  saying {0!r}. That is the carrier's edge deciding this "
                  "server".format(refused(phase)))
            print("  is a robot and denying it, which denies Edge on the "
                  "same address")
            print("  just as thoroughly. Nothing inside the automation's "
                  "process can")
            print("  undo it; what matters is how much traffic, and how "
                  "browser-like,")
            print("  this machine sends the carrier.")
            if snap.get("headers"):
                print("  The edge identified itself as: {0}".format(
                    snap["headers"].get("server")
                    or snap["headers"].get("via") or "unnamed"))
            named = True
            break
    if not named:
        for phase in order:
            if not raw_ok(phase) and control_ok(phase):
                print("  IT IS THE CARRIER SPECIFICALLY, NOT THIS MACHINE'S "
                      "NETWORK. In")
                print("  phase {0} a plain socket request to the carrier "
                      "failed while the".format(phase))
                print("  same request to {0} succeeded from the".format(
                    (taken[phase].get("control_probe") or {}).get("host")))
                print("  same process, seconds apart. Sockets and ports are "
                      "not exhausted;")
                print("  the path to that one host is.")
                named = True
                break
    if not named:
        for phase in order:
            if not raw_ok(phase) and not control_ok(phase):
                print("  THIS MACHINE COULD NOT REACH ANYTHING in phase "
                      "{0} — the carrier".format(phase))
                print("  and the control site both failed. That is a "
                      "machine-level limit,")
                print("  not a carrier one: check the ephemeral port "
                      "headroom and the")
                print("  TIME_WAIT row above.")
                named = True
                break

    if reached("A") is False:
        print("  Phase A already failed, with the automation off. Whatever is")
        print("  wrong is not caused by the automation. Look at the machine's")
        print("  network path to the carrier first.")
    elif reached("A") and reached("B") is False:
        print("  A passed and B failed: the automation costs a shared resource")
        print("  BEFORE it ever touches AFKL. Compare the TCP and handle rows")
        print("  between A and B — the row that moved is the resource.")
    elif reached("B") and reached("C") is False:
        print("  B passed and C failed: the cost is specific to the AFKL")
        print("  lookup path. Look at 'connections to carrier' between B and")
        print("  C. If it climbs and stays up, the lookup is leaving pages or")
        print("  contexts open on the carrier's host.")
    elif reached("C"):
        print("  Edge reached the carrier in every phase, including C. This")
        print("  run did not reproduce the fault; do not conclude it is fixed")
        print("  from that alone unless C ran a comparable number of AFKL")
        print("  shipments to the run that failed.")
    if "C" in taken and not raw_ok("C") and reached("C") is not True:
        print("  The RAW socket probe failed too, so this is not a Chromium")
        print("  rendering problem: this machine could not complete a plain")
        print("  TLS request to the carrier.")
    elif "C" in taken and raw_ok("C") and reached("C") is False:
        print("  The raw socket probe succeeded while Edge did not. The")
        print("  network path is open; the constraint is in the browser or in")
        print("  what the page itself has to fetch.")
    if all(reached(p) is None for p in order):
        answered = [p for p in order if raw_ok(p)]
        refused = [p for p in order if not raw_ok(p)]
        print("  On the raw socket probe alone: the carrier answered in "
              "{0} and".format(", ".join(answered) or "no phase"))
        print("  did not in {0}. A phase where the raw probe fails is a "
              "machine-".format(", ".join(refused) or "no phase"))
        print("  level failure, not a rendering one; a phase where it "
              "succeeds says")
        print("  nothing about what the browser could do there.")
    if "D" in taken and reached("D"):
        print("  D recovered, which matches the field observation and points")
        print("  at something the automation HOLDS while it runs, rather than")
        print("  something it changes and leaves behind.")
    print()
    return 0


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = {a for a in sys.argv[1:] if a.startswith("-")}
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    what = argv[0].upper()
    if what == "REPORT":
        return report()
    if what not in PHASES:
        print("Unknown phase {0}. Use A, B, C, D or report.".format(what))
        return 2
    print()
    print("  Phase {0}: {1}".format(what, PHASES[what]))
    print("  Measuring. The Edge probe opens a real window; leave it alone.")
    print()
    taken = snapshot(what, with_edge="--no-edge" not in flags)
    describe(taken)
    store(taken)
    if what == "D" and "--watch" in flags:
        print()
        watch_recovery()
    print()
    print("  When A, B, C and D are all done:  python diagnose_server.py report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
