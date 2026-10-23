"""
The dashboard behind a company domain: an HTTPS reverse proxy on the server
forwards https://ata.<company> to the tower on this machine.

  1. Behind the proxy (ATA_PUBLIC_URL or ATA_TRUST_PROXY), every request
     comes from loopback, so the access key is required even when the tower
     is bound to 127.0.0.1. Without those settings, loopback is unchanged.
  2. With ATA_TRUST_PROXY=1, the person's address (sign-in rate limits, the
     audit trail) is the entry the proxy added to X-Forwarded-For, believed
     only from a loopback peer.

    python test_domain_proxy.py
"""

import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORK = Path(tempfile.mkdtemp(prefix="ct_proxy_"))
os.environ.update(DASHBOARD_ACCESS_KEY_FILE=str(WORK / "access_key"),
                  ATLAS_INTEL_DIR=str(WORK / "intel"), ATLAS_DATA_ORIGIN="test",
                  DASHBOARD_ACCOUNTS="0")
for name in ("DASHBOARD_ACCESS_KEY", "ATA_PUBLIC_URL", "ATA_TRUST_PROXY"):
    os.environ.pop(name, None)

from dashboard import server as S                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not ok else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def status(url):
    try:
        return urllib.request.urlopen(url, timeout=5).status
    except urllib.error.HTTPError as error:
        return error.code


def serve():
    port = free_port()
    S.start(port=port, open_browser=False, host="127.0.0.1")
    base = "http://127.0.0.1:%d/" % port
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "favicon.ico", timeout=1)
            break
        except urllib.error.HTTPError:
            break
        except Exception:
            time.sleep(0.1)
    return base


print("=" * 72)
print("1. THE ACCESS KEY BEHIND A DOMAIN")
print("=" * 72)
base = serve()
check("Loopback without the domain settings: no key, as before",
      S.ACCESS_KEY is None and status(base + "api/state") == 200, S.ACCESS_KEY)

os.environ["ATA_PUBLIC_URL"] = "https://ata.example.com"
base = serve()
check("ATA_PUBLIC_URL set: the tower takes a key even bound to 127.0.0.1",
      bool(S.ACCESS_KEY))
check("...and a request without it is refused", status(base + "api/state") in (401, 403),
      status(base + "api/state"))
check("...while the key opens it", status(base + "api/state?key=" + S.ACCESS_KEY) == 200)
os.environ.pop("ATA_PUBLIC_URL")
(WORK / "access_key").unlink()

os.environ["ATA_TRUST_PROXY"] = "1"
serve()
check("ATA_TRUST_PROXY=1 alone: a key is required too", bool(S.ACCESS_KEY))

print()
print("=" * 72)
print("2. WHO IS ASKING, BEHIND THE PROXY")
print("=" * 72)


def ip_of(peer, forwarded=None, trust=True):
    if trust:
        os.environ["ATA_TRUST_PROXY"] = "1"
    else:
        os.environ.pop("ATA_TRUST_PROXY", None)
    handler = S.Handler.__new__(S.Handler)
    handler.client_address = (peer, 50000)
    handler.headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    return handler._acc_ip()


check("Through the proxy on this machine, the person's address is used",
      ip_of("127.0.0.1", "10.20.30.40") == "10.20.30.40")
check("...the entry the proxy added (the last), not one a browser supplied",
      ip_of("127.0.0.1", "1.2.3.4, 10.20.30.40") == "10.20.30.40")
check("...with a port stripped", ip_of("127.0.0.1", "10.20.30.40:51515") == "10.20.30.40")
check("A peer on the network cannot claim another address",
      ip_of("10.9.9.9", "10.20.30.40") == "10.9.9.9")
check("Without ATA_TRUST_PROXY the header is ignored",
      ip_of("127.0.0.1", "10.20.30.40", trust=False) == "127.0.0.1")
check("No header: the peer", ip_of("127.0.0.1") == "127.0.0.1")

print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 72)
sys.exit(1 if FAIL else 0)
