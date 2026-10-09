"""
The dashboard access key: never hardcoded, never shipped, always required
off loopback.

    python test_dashboard_access.py
"""

import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

KEYDIR = Path(tempfile.mkdtemp(prefix="ct_access_"))
os.environ["DASHBOARD_ACCESS_KEY_FILE"] = str(KEYDIR / "access_key")
os.environ.pop("DASHBOARD_ACCESS_KEY", None)

from dashboard import access  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not ok else ""))


def rule(title):
    print("\n" + "=" * 74 + "\n" + title + "\n" + "=" * 74)


rule("1. WHERE THE KEY COMES FROM")
key, source = access.resolve(generate=False)
check("Nothing configured and generation off: no key, no file", key is None and
      not (KEYDIR / "access_key").exists())
key, source = access.resolve()
check("First start: a random key is generated for this installation",
      source == "generated" and len(key) >= 20 and (KEYDIR / "access_key").exists(), source)
if os.name != "nt":
    mode = (KEYDIR / "access_key").stat().st_mode & 0o777
    check("...in a file only this user can read (0600)", mode == 0o600, oct(mode))
again, source2 = access.resolve()
check("Next start: the same key, from the file", again == key and source2 == "file")
os.environ["DASHBOARD_ACCESS_KEY"] = "env-chosen-key-123"
check("DASHBOARD_ACCESS_KEY wins over the file",
      access.resolve() == ("env-chosen-key-123", "environment"))
check("An explicit --key wins over everything", access.resolve("option-key-12345")[1] == "option")
os.environ["DASHBOARD_ACCESS_KEY"] = "short"
try:
    access.resolve()
    refused = False
except ValueError:
    refused = True
check("A configured key shorter than 12 characters is refused, not silently replaced", refused)
os.environ.pop("DASHBOARD_ACCESS_KEY")
check("The console explanation never contains the key itself",
      all(key not in access.explain(s) for s in ("option", "environment", "file", "generated")))
other = Path(tempfile.mkdtemp(prefix="ct_access2_"))
os.environ["DASHBOARD_ACCESS_KEY_FILE"] = str(other / "access_key")
check("Each installation gets its own key", access.resolve()[0] != key)
os.environ["DASHBOARD_ACCESS_KEY_FILE"] = str(KEYDIR / "access_key")

rule("2. A NETWORK-SHARED DASHBOARD ALWAYS HAS A KEY")
from dashboard import server  # noqa: E402

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
link = server.start(port=port, open_browser=False, host="0.0.0.0", access_key=None)
time.sleep(0.4)
check("Shared without a key given: started with this installation's key",
      server.ACCESS_KEY == key and link and link.endswith("?key=" + key), link)


def status(url, cookie=None):
    request = urllib.request.Request(url, headers={"Cookie": cookie} if cookie else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers.get("Set-Cookie") or ""
    except urllib.error.HTTPError as error:
        return error.code, ""


base = "http://127.0.0.1:{0}".format(port)
check("No key: refused (401)", status(base + "/api/state")[0] == 401)
check("Wrong key: refused (401)", status(base + "/api/state?key=wrong-key-0000")[0] == 401)
code, cookie = status(base + "/?key=" + key)
check("The right key: the dashboard opens and a cookie is set", code == 200 and
      server.COOKIE_NAME in cookie, (code, cookie[:40]))
check("...and the cookie alone opens the API afterwards",
      status(base + "/api/state", cookie="{0}={1}".format(server.COOKIE_NAME, key))[0] == 200)
check("A wrong cookie is refused",
      status(base + "/api/state", cookie="{0}=nope".format(server.COOKIE_NAME))[0] == 401)

rule("3. NO KEY IN THE PRODUCT")
product = ""
for path in HERE.rglob("*"):
    rel = path.relative_to(HERE).parts
    if path.is_file() and not rel[0].startswith((".", "C:", "dist", "ml")) \
            and not path.name.startswith("test_") and "__pycache__" not in rel \
            and path.suffix in (".py", ".bat", ".ps1", ".md", ".txt", ".json", ".html", ".js"):
        product += path.read_text(encoding="utf-8", errors="replace")
check("The old hardcoded key appears nowhere in code, scripts or docs",
      "mantrac" + "2026" not in product)
eta = (HERE / "update_eta.py").read_text(encoding="utf-8")
check("update_eta.py holds no key (DASHBOARD_ACCESS_KEY = None)",
      re.search(r"^DASHBOARD_ACCESS_KEY = None$", eta, re.M) is not None)
sup = (HERE / "dashboard" / "supervisor.py").read_text(encoding="utf-8")
check("The supervisor's --key has no default value",
      re.search(r'"--key", default=None', sup) is not None)

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
