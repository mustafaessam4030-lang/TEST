"""
deploy/iis/ (the IIS front door for IT) says what the tower needs: Host kept,
no buffering of the live stream, https only, and the two settings the tower
reads. Static checks; the scripts themselves have not been run on IIS.

    python test_iis_package.py
"""
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
IIS = HERE / "deploy" / "iis"
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


for name in ("web.config", "setup_iis.ps1", "check_iis.ps1", "README_FOR_IT.md"):
    check("deploy/iis/" + name + " exists", (IIS / name).is_file())

web = ET.parse(IIS / "web.config").getroot()
rules = web.findall("./system.webServer/rewrite/rules/rule")
check("web.config: http redirects to https first",
      rules and rules[0].find("action").get("type") == "Redirect"
      and rules[0].find("action").get("url").startswith("https://"))
cond = rules[0].find("conditions/add") if rules else None
check("web.config: the redirect only applies to plain http (no loop)",
      cond is not None and cond.get("input") == "{HTTPS}" and cond.get("pattern") == "^OFF$")
fwd = [r for r in rules if r.find("action").get("type") == "Rewrite"]
check("web.config: everything else goes to the tower on loopback only",
      len(fwd) == 1 and fwd[0].find("action").get("url") == "http://127.0.0.1:8787/{R:1}")
comp = web.find("./system.webServer/urlCompression")
check("web.config: dynamic compression off (live stream)",
      comp is not None and comp.get("doDynamicCompression") == "false")

setup = (IIS / "setup_iis.ps1").read_text(encoding="utf-8")
check("setup_iis.ps1's port substitution still matches web.config",
      r'-replace "127\.0\.0\.1:8787"' in setup and "127.0.0.1:8787" in (IIS / "web.config").read_text())
lim = web.find("./system.webServer/security/requestFiltering/requestLimits")
sys.path.insert(0, str(HERE))
from intelligence.evidence import MAX_UPLOAD                       # noqa: E402
check("web.config: upload limit fits ATLAS's largest attachment, base64 included",
      int(lim.get("maxAllowedContentLength")) >= MAX_UPLOAD * 4 // 3 + 65536)
check("setup_iis.ps1 re-runs safely: an existing certificate binding is kept or replaced",
      "IIS:\\SslBindings" in setup and "RemoveSslCertificate" in setup)
check("setup_iis.ps1 stops when the site did not start", '.State -ne "Started"' in setup)
for opt in ('/preserveHostHeader:"True"', '/responseBufferLimit:"0"', '/timeout:"00:30:00"'):
    check("setup_iis.ps1 sets " + opt, opt in setup)
server = (HERE / "dashboard" / "server.py").read_text(encoding="utf-8")
for var in ("ATA_PUBLIC_URL", "ATA_TRUST_PROXY"):
    check("setup_iis.ps1 sets " + var + ", which the tower reads",
          re.search(r'SetEnvironmentVariable\("' + var + '"', setup) and var in server)
check("setup_iis.ps1 stops when not Administrator", "IsInRole" in setup and "exit 1" in setup)
check("setup_iis.ps1 needs no secret on its command line",
      not re.search(r"(?i)password|-Key\b|token", setup))

checker = (IIS / "check_iis.ps1").read_text(encoding="utf-8")
check("check_iis.ps1 changes nothing",
      not re.search(r"(?i)\b(Set|New|Remove|Add|Start|Stop)-(?!Object\b)\w|appcmd[^\n]*\bset\b", checker))
check("check_iis.ps1 exits with its FAIL count", "exit $fails" in checker)
check("check_iis.ps1 does not assign PowerShell's automatic $args", "$args =" not in checker)
check("check_iis.ps1 expects sign-in (401) on /api/state", "/api/state" in checker and "401" in checker)

readme = (IIS / "README_FOR_IT.md").read_text(encoding="utf-8")
check("README says the scripts are not yet run on IIS", "not been run on an" in readme)
check("README puts personal sign-in first", "CREATE_ADMIN.bat" in readme)
check("DEPLOY_SERVER.md points to the package", "deploy/iis/" in (HERE / "DEPLOY_SERVER.md").read_text(encoding="utf-8"))

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
