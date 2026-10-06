"""
Carrier access diagnostic — run ON THE WINDOWS WORKER.

    python -m worker.verify carrier --carrier CMA_CGM --reference <B/L> [--no-manual]

Why a carrier refuses this worker is a question about the worker's
environment before it is one about the automation. This records, on the
worker itself:

    1  VPN        adapters that are up and named like a VPN client; Windows
                  VPN connections that are connected
    2  proxy      HTTP(S)_PROXY variables, the Windows (WinINET) proxy and
                  PAC settings, the WinHTTP proxy
    3  public IP  as Python sees it, and as the automation's browser sees it
                  (one request each to an IP-echo service, --ip-service)
    4  network    connection profile (domain / private / public), Wi-Fi SSID,
                  default gateway, mobile-broadband and metered flags — the
                  indicators of a mobile hotspot, reported as indicators
    5  Edge       the installed Edge version; the version the automation's
                  Edge reports; the automation's profile (a fresh, empty one
                  per run) and what the page sees (navigator.webdriver,
                  user agent, languages, time zone)
    6  manual     the installed Edge, opened normally on the carrier's page —
                  no automation — and the operator records what it shows
    7  automation the same page through the automation's own browser launch
                  and lookup (update_eta.get_portal_result); a verification
                  challenge is left to the operator, as in a run
    8/9/10        both results side by side, the restriction page and URL,
                  and what the restriction follows — stated only as far as
                  the evidence goes

Nothing here tries to get past a carrier's controls: no retries against the
restriction, no other browser identity, no change of network. Credentials
and proxy passwords are never printed.
"""

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

from intelligence import verification as V

VPN_NAMES = re.compile(
    r"\bVPN\b|TAP-Windows|Wintun|WireGuard|AnyConnect|GlobalProtect|PANGP|Forti(?:Client|net)|"
    r"Pulse\s*Secure|Juniper|Zscaler|OpenVPN|NordLynx|ExpressVPN|Proton|Check\s*Point|"
    r"Netskope|SonicWall|Cloudflare\s*WARP|WAN\s+Miniport\s+\((?:IKEv2|SSTP|L2TP|PPTP)\)", re.I)
HOTSPOT_GATEWAYS = {"172.20.10.1": "the default gateway of an iPhone Personal Hotspot",
                    "192.168.43.1": "the default gateway of an Android hotspot"}
HOTSPOT_SSID = re.compile(r"iphone|android|galaxy|pixel|hotspot|huawei|redmi|xiaomi|oppo|"
                          r"\bmifi\b|\b4g\b|\b5g\b|\blte\b", re.I)
DEFAULT_IP_SERVICE = "https://ipinfo.io/json"


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _run(args, timeout=20):
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return done.stdout.strip(), (done.stderr or "").strip()[:200]
    except Exception as error:
        return "", str(error)[:200]


def _ps_json(script):
    """A PowerShell result as JSON (a list), or (None, why)."""
    if os.name != "nt":
        return None, "not Windows"
    out, err = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     script + " | ConvertTo-Json -Depth 3 -Compress"])
    if not out:
        return None, err or "no output"
    try:
        data = json.loads(out)
    except ValueError:
        return None, "unreadable output"
    return (data if isinstance(data, list) else [data]), None


def _redact_proxy(value):
    """A proxy URL without any user:password@ in it."""
    return re.sub(r"(//)[^/@\s]+@", r"\1***@", str(value or ""))


# ── 1. VPN ────────────────────────────────────────────────────────────────

def vpn():
    adapters, why = _ps_json("Get-NetAdapter | Where-Object Status -eq 'Up' | "
                             "Select-Object Name,InterfaceDescription,MediaType,"
                             "PhysicalMediaType,LinkSpeed")
    connections, why_vpn = _ps_json("@(Get-VpnConnection; Get-VpnConnection -AllUserConnection) "
                                    "| Select-Object Name,ServerAddress,ConnectionStatus")
    named = [a for a in adapters or [] if VPN_NAMES.search(
        "{0} {1}".format(a.get("Name"), a.get("InterfaceDescription")))]
    connected = [c for c in connections or [] if str(c.get("ConnectionStatus")) == "Connected"]
    if adapters is None:
        return {"detected": None, "why": "adapters could not be listed: {0}".format(why)}
    return {"detected": bool(named or connected),
            "adapters_up": [{k: a.get(k) for k in ("Name", "InterfaceDescription", "MediaType",
                                                   "PhysicalMediaType", "LinkSpeed")}
                            for a in adapters],
            "vpn_named_adapters_up": [a.get("Name") for a in named],
            "windows_vpn_connected": [c.get("Name") for c in connected],
            "how": "an adapter that is up and named like a VPN client, or a connected Windows "
                   "VPN connection; a VPN that hides its adapter name is not detected this way"}


# ── 2. proxy ──────────────────────────────────────────────────────────────

def proxy():
    env = {k: _redact_proxy(os.environ[k]) for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                                                     "NO_PROXY", "http_proxy", "https_proxy")
           if os.environ.get(k)}
    out = {"environment": env, "wininet": None, "winhttp": None}
    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            values = {}
            for name in ("ProxyEnable", "ProxyServer", "AutoConfigURL", "AutoDetect"):
                try:
                    values[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    pass
            if "ProxyServer" in values:
                values["ProxyServer"] = _redact_proxy(values["ProxyServer"])
            out["wininet"] = values
        except Exception as error:
            out["wininet"] = {"error": str(error)[:120]}
        text, _err = _run(["netsh", "winhttp", "show", "proxy"])
        out["winhttp"] = " ".join(text.split())[:300] or None
    wininet = out["wininet"] or {}
    out["configured"] = bool(env) or bool(wininet.get("ProxyEnable")) or \
        bool(wininet.get("AutoConfigURL")) or \
        bool(out["winhttp"] and "Direct access" not in out["winhttp"])
    out["browser_uses"] = ("Edge (manual and automated) follows the Windows proxy settings "
                           "(WinINET / PAC); Python follows HTTP(S)_PROXY")
    return out


# ── 3. public IP ──────────────────────────────────────────────────────────

def public_ip_python(service):
    try:
        with urllib.request.urlopen(service, timeout=15) as response:
            body = response.read(4096).decode("utf-8", "replace")
    except Exception as error:
        return {"error": "{0}: {1}".format(type(error).__name__, str(error)[:120]), "via": "python"}
    return dict(_ip_fields(body), via="python", service=service)


def _ip_fields(body):
    try:
        data = json.loads(body)
    except ValueError:
        return {"ip": body.strip()[:64]}
    return {k: data.get(k) for k in ("ip", "org", "city", "region", "country", "hostname")
            if data.get(k)}


# ── 4. network ────────────────────────────────────────────────────────────

def network():
    profiles, why = _ps_json("Get-NetConnectionProfile | Select-Object Name,InterfaceAlias,"
                             "@{n='NetworkCategory';e={$_.NetworkCategory.ToString()}},"
                             "@{n='IPv4Connectivity';e={$_.IPv4Connectivity.ToString()}}")
    gateways, _w = _ps_json("Get-NetIPConfiguration | Where-Object {$_.IPv4DefaultGateway} | "
                            "Select-Object InterfaceAlias,@{n='Gateway';e={$_.IPv4DefaultGateway"
                            ".NextHop}}")
    cost, _c = _ps_json(
        "[void][Windows.Networking.Connectivity.NetworkInformation,Windows.Networking."
        "Connectivity,ContentType=WindowsRuntime]; $p=[Windows.Networking.Connectivity."
        "NetworkInformation]::GetInternetConnectionProfile(); if ($p) { $c=$p."
        "GetConnectionCost(); [pscustomobject]@{Profile=$p.ProfileName; Cost=$c.NetworkCostType"
        ".ToString(); Roaming=$c.Roaming; Wwan=$p.IsWwanConnectionProfile; Wlan=$p."
        "IsWlanConnectionProfile} }")
    wlan = {}
    if os.name == "nt":
        text, _e = _run(["netsh", "wlan", "show", "interfaces"])
        for line in text.splitlines():
            if ":" in line:
                key, _sep, value = line.partition(":")
                key = key.strip().lower()
                if key in ("ssid", "radio type", "network type", "profile", "state"):
                    wlan[key] = value.strip()
    indicators = []
    for g in gateways or []:
        if g.get("Gateway") in HOTSPOT_GATEWAYS:
            indicators.append("{0} is {1}".format(g["Gateway"], HOTSPOT_GATEWAYS[g["Gateway"]]))
    internet = (cost or [{}])[0] if cost else {}
    if internet.get("Wwan"):
        indicators.append("the internet connection is a mobile-broadband (WWAN) profile")
    if internet.get("Cost") in ("Fixed", "Variable"):
        indicators.append("Windows marks the connection metered ({0})".format(internet["Cost"]))
    if wlan.get("ssid") and HOTSPOT_SSID.search(wlan["ssid"]) and wlan.get("state", "").lower() \
            in ("connected", ""):
        indicators.append("the Wi-Fi network is named {0!r}".format(wlan["ssid"]))
    categories = [p.get("NetworkCategory") for p in profiles or []]
    return {"profiles": profiles, "profiles_error": why if profiles is None else None,
            "gateways": gateways, "internet_profile": internet or None, "wifi": wlan or None,
            "domain_network": "DomainAuthenticated" in categories,
            "hotspot_indicators": indicators,
            "reading": ("a domain (corporate) network profile is active"
                        if "DomainAuthenticated" in categories else
                        "no domain network profile is active") +
                       ("; mobile-hotspot indicators: " + "; ".join(indicators) if indicators
                        else "; no mobile-hotspot indicator found")}


# ── 5. Edge ───────────────────────────────────────────────────────────────

def edge_installed():
    from worker.agent import EDGE_PATHS
    for path in EDGE_PATHS:
        if os.path.exists(path):
            version, _e = _run(["powershell", "-NoProfile", "-Command",
                                "(Get-Item '{0}').VersionInfo.ProductVersion".format(path)])
            return {"path": path, "version": version or None}
    found = shutil.which("msedge") or shutil.which("microsoft-edge")
    return {"path": found, "version": None} if found else {"path": None, "version": None}


# ── 6. manual ─────────────────────────────────────────────────────────────

MANUAL_CHOICES = {"1": "ACCESS", "2": "RESTRICTED", "3": "CHALLENGE", "4": "OTHER"}


def manual_check(url, reference, edge_path, ask=input, say=print):
    """Open the installed Edge normally and record what the operator sees."""
    if not edge_path:
        return {"result": "NOT_RUN", "why": "the installed Edge was not found"}
    try:
        subprocess.Popen([edge_path, url])
    except Exception as error:
        return {"result": "NOT_RUN", "why": "Edge could not be opened: {0}".format(error)}
    say("")
    say("A normal Edge window (your own profile, no automation) is opening on:")
    say("    " + url)
    if reference:
        say("Search for {0} there yourself, as you normally would.".format(reference))
    started = _now()
    while True:
        say("")
        say("What does that Edge window show now?")
        say("  1  the tracking page / the shipment's details")
        say("  2  the carrier's restriction page (browser behaviour, hotspot / proxy / VPN)")
        say("  3  a verification challenge — complete it yourself, then choose again")
        say("  4  something else")
        answer = str(ask("Choose 1-4: ")).strip()
        if answer in MANUAL_CHOICES and answer != "3":
            break
        if answer == "3":
            ask("Complete it in the Edge window, then press Enter... ")
    shown = str(ask("Paste the address from Edge's address bar (Enter to skip): ")).strip()
    account = str(ask("Is this Edge signed in to a carrier account on that site? (y/n/unsure): "
                      )).strip().lower()[:1]
    note = str(ask("Anything else on the page worth noting? (Enter to skip): ")).strip()
    import update_eta as A
    return {"result": MANUAL_CHOICES[answer], "url_shown": A.safe_url(shown, reference) or
            (shown[:300] or None),
            "signed_in_to_carrier": {"y": True, "n": False}.get(account),
            "operator_note": note[:300] or None, "opened": url, "started": started,
            "recorded": _now(), "browser": "installed Edge, the user's own profile, no automation",
            "how": "recorded by the operator at the worker"}


# ── 7. automation ─────────────────────────────────────────────────────────

PROBE_JS = """() => ({webdriver: navigator.webdriver, userAgent: navigator.userAgent,
  languages: navigator.languages, timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  platform: navigator.platform})"""


def automation_check(provider, reference, ip_service, launch=None):
    """The carrier page through the automation's own browser and lookup."""
    import update_eta as A
    config = A.portal_config_for(provider, reference or "")
    url = (config.get("urls") or [None])[0]
    out = {"url_opened": url, "browser": {"real": False}}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as error:
        return dict(out, result="ERROR", error="Playwright is not installed: {0}".format(error))
    with sync_playwright() as playwright:
        options = dict(launch or A.hub_launch_options())
        try:
            browser = playwright.chromium.launch(**options)
        except Exception as error:
            return dict(out, result="ERROR", error="the browser could not be launched: {0}".format(
                str(error).split("\n")[0][:200]))
        try:
            try:
                credentials = A.load_credentials()
                context = browser.new_context(**A.hub_context_options(*credentials))
                context_note = "the run's own context (eHub basic auth attached, as in a run)"
            except Exception:
                context = browser.new_context()
                context_note = "a plain context (the credentials file was not available)"
            out["browser"] = {"real": True, "engine": browser.browser_type.name,
                              "channel": options.get("channel"), "version": browser.version,
                              "headless": bool(options.get("headless")),
                              "profile": "a fresh, empty profile created for this launch "
                                         "(as every run)", "context": context_note,
                              "same_launch_as_runs": launch is None}
            ip_page = context.new_page()
            try:
                ip_page.goto(ip_service, timeout=20000)
                out["public_ip"] = dict(_ip_fields(ip_page.locator("body").inner_text(
                    timeout=5000)), via="automation browser", service=ip_service)
            except Exception as error:
                out["public_ip"] = {"error": str(error).split("\n")[0][:160],
                                    "via": "automation browser"}
            finally:
                ip_page.close()
            page = context.new_page()
            started = time.time()
            if reference:
                shipment = {"bol_awb": reference, "carrier": config.get("label"),
                            "provider": provider, "current_eta": ""}
                try:
                    result = A.get_portal_result(page, provider, reference, shipment)
                    out.update(result="ACCESS", carrier_result={k: result.get(k) for k in (
                        "tracking_status", "eta", "eta_source", "ata", "ata_source")})
                except A.CarrierAccessRestricted as error:
                    out.update(result="RESTRICTED", restriction=error.failure.get("observed"),
                               message=str(error))
                except A.CaptchaRequired as error:
                    out.update(result="CHALLENGE_NOT_COMPLETED", message=str(error)[:300])
                except A.SkipShipment as error:
                    out.update(result="NO_RESULT", message=str(error)[:300])
                except Exception as error:
                    out.update(result="ERROR", message=str(error).split("\n")[0][:300])
            else:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(4000)
                except Exception as error:
                    out.update(result="ERROR", message=str(error).split("\n")[0][:300])
                found = A.carrier_restriction(page)
                if found:
                    out.update(result="RESTRICTED", restriction=found)
                elif A.captcha_on_page(page):
                    out.update(result="CHALLENGE_SHOWN")
                elif "result" not in out:
                    out.update(result="PAGE_OPENED")
            out["seconds"] = int(time.time() - started)
            try:
                out["final_url"] = A.safe_url(page.url, reference)
                out["final_title"] = (page.title() or "")[:160]
                out["page_sees"] = page.evaluate(PROBE_JS)
            except Exception as error:
                out["final_url"] = out.get("final_url") or None
                out["page_error"] = str(error)[:160]
            return out
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ── 8/10. comparison and determination ─────────────────────────────────────

def determine(evidence):
    """
    What the restriction follows, as far as the evidence goes:
    {"follows", "confidence", "because", "ruled_out", "not_established"}.
    confidence: ESTABLISHED (the comparison shows it) · CONSISTENT (the
    evidence fits it, nothing here proves it) · NOT_ESTABLISHED.
    """
    manual = (evidence.get("manual") or {}).get("result", "NOT_RUN")
    auto = (evidence.get("automation") or {}).get("result", "NOT_RUN")
    env = evidence.get("environment") or {}
    vpn_on = (env.get("vpn") or {}).get("detected")
    proxy_on = (env.get("proxy") or {}).get("configured")
    hotspot = (env.get("network") or {}).get("hotspot_indicators") or []
    py_ip = (env.get("public_ip_python") or {}).get("ip")
    br_ip = ((evidence.get("automation") or {}).get("public_ip") or {}).get("ip")
    signed_in = (evidence.get("manual") or {}).get("signed_in_to_carrier")
    because, ruled_out, not_established = [], [], []
    page_names = []
    for name, present in (("VPN", vpn_on), ("proxy server", proxy_on),
                          ("mobile hotspot", bool(hotspot))):
        if present:
            page_names.append(name)
    if py_ip and br_ip:
        because.append("public IP: {0} from Python, {1} from the automation's browser{2}".format(
            py_ip, br_ip, "" if py_ip == br_ip else " — they differ, so the browser leaves "
            "through another route (a proxy/PAC)"))
    if page_names:
        because.append("present on this worker, and named by the carrier's page as possible "
                       "causes: {0}".format(", ".join(page_names)))
    elif vpn_on is False and proxy_on is False and not hotspot:
        because.append("no VPN adapter, no proxy setting and no mobile-hotspot indicator was "
                       "found on this worker")
    ruled_out.append("a carrier account: the automation signs in to no carrier account")

    if auto == "RESTRICTED" and manual == "RESTRICTED":
        follows = "the worker's network / public IP, or a carrier-side condition for it"
        confidence = "CONSISTENT"
        because.insert(0, "a normal Edge with the user's own profile and the automation's "
                          "browser are both restricted on this worker")
        ruled_out.append("the automation itself: a browser with no automation is restricted too")
        not_established.append("network vs. carrier-side: only a check of the same browser "
                               "from a different network separates them")
    elif auto == "RESTRICTED" and manual == "ACCESS":
        follows = "the automation's browser / session / environment on this worker"
        confidence = "ESTABLISHED"
        because.insert(0, "on the same worker and network, a normal Edge reaches the page and "
                          "the automation's browser is restricted")
        if py_ip and br_ip and py_ip == br_ip:
            ruled_out.append("the worker's public IP alone: both left through {0}".format(br_ip))
        sees = (evidence.get("automation") or {}).get("page_sees") or {}
        not_established.append(
            "which property of the automation's browser the carrier reacts to. Differences "
            "recorded: fresh empty profile vs. the user's profile{0}{1}".format(
                "; navigator.webdriver = true in the automation" if sees.get("webdriver") else "",
                "; the manual Edge is signed in to a carrier account" if signed_in else ""))
    elif auto == "ACCESS" and manual == "RESTRICTED":
        follows = "the manual browser's own profile / session"
        confidence = "ESTABLISHED"
        because.insert(0, "the automation's fresh browser reaches the page and the user's own "
                          "Edge profile is restricted")
        ruled_out.append("the worker's network: the automation reaches the page from it")
    elif auto == "ACCESS" and manual in ("ACCESS", "NOT_RUN"):
        follows = "nothing now: the restriction did not occur during this check"
        confidence = "NOT_ESTABLISHED"
        because.insert(0, "the automation's browser reached the shipment page{0}".format(
            " and so did a normal Edge" if manual == "ACCESS" else ""))
        not_established.append("why the run was restricted earlier: it was not reproduced — "
                               "intermittent, or carrier-side at that time")
    elif auto == "RESTRICTED":
        follows = "not established: the manual comparison was not made"
        confidence = "NOT_ESTABLISHED"
        because.insert(0, "the automation's browser is restricted; no manual check was recorded")
        not_established.append("network vs. browser: run again with the manual check")
    else:
        follows = "not established"
        confidence = "NOT_ESTABLISHED"
        because.insert(0, "automation: {0}; manual: {1}".format(auto, manual))
        not_established.append("the carrier page did not reach a restriction or a result in "
                               "the automation's browser")
    return {"follows": follows, "confidence": confidence, "because": because,
            "ruled_out": ruled_out, "not_established": not_established}


def diagnose(provider, reference=None, manual=True, ip_service=DEFAULT_IP_SERVICE,
             ask=input, say=print):
    import update_eta as A
    if provider not in A.PORTALS:
        raise SystemExit("Unknown carrier {0}. Carriers: {1}".format(
            provider, ", ".join(sorted(A.OCEAN_PORTALS))))
    config = A.portal_config_for(provider, reference or "")
    url = (config.get("urls") or [None])[0]
    say("Recording this worker's environment (VPN, proxy, IP, network, Edge)...")
    env = {"machine": socket.gethostname(), "platform": platform.platform()[:80],
           "vpn": vpn(), "proxy": proxy(), "public_ip_python": public_ip_python(ip_service),
           "network": network(), "edge_installed": edge_installed()}
    manual_result = manual_check(url, reference, env["edge_installed"].get("path"), ask, say) \
        if manual else {"result": "NOT_RUN", "why": "--no-manual"}
    say("")
    say("Now the same page through the automation's own browser"
        " (complete any verification in that window yourself, as in a run)...")
    automated = automation_check(provider, reference, ip_service)
    installed = env["edge_installed"].get("version")
    used = (automated.get("browser") or {}).get("version")
    # Same version number = the installed Edge is what the automation launched
    # (Playwright's "msedge" channel starts the installed Edge).
    env["edge_same_installation"] = None if not (installed and used) else installed == used
    evidence = {"carrier": config.get("label"), "provider": provider, "reference": reference,
                "url": url, "environment": env, "manual": manual_result, "automation": automated}
    evidence["comparison"] = {"manual": manual_result.get("result"),
                              "automation": automated.get("result"),
                              "same_public_ip": (None if not (
                                  env["public_ip_python"].get("ip") and
                                  (automated.get("public_ip") or {}).get("ip"))
                                  else env["public_ip_python"].get("ip") ==
                                  automated["public_ip"].get("ip")),
                              "edge_version_installed": installed, "edge_version_automation": used,
                              "restriction_url": (automated.get("restriction") or {}).get("url")
                              or (manual_result.get("url_shown")
                                  if manual_result.get("result") == "RESTRICTED" else None)}
    evidence["determination"] = determine(evidence)
    return evidence
