"""
The Remote Desktop server package: INSTALL_SERVER.bat / install_server.ps1,
disconnect_keep_running.bat and check_server.py.

    python test_server_install.py

What can be checked off Windows: the scripts' content and safety rules, the
PowerShell syntax (when PowerShell is installed), and check_server.py running
for real. The Windows-only steps (scheduled task, tscon, powercfg) run only on
the server itself.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


ps1 = (HERE / "install_server.ps1").read_text(encoding="utf-8")
bat = (HERE / "INSTALL_SERVER.bat").read_bytes()
off = (HERE / "disconnect_keep_running.bat").read_bytes()

print("1. THE INSTALLER")
check("It asks for administrator rights and refuses without them",
      b"-Verb RunAs" in bat and "IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)" in ps1)
check("It checks Python 3.11+ and Edge before changing anything",
      ps1.index('"3.11"') < ps1.index("pip install") and ps1.index("msedge.exe") < ps1.index("pip install"))
check("The dashboard port is opened to the company network only, never public",
      "profile=domain,private" in ps1 and "public" not in ps1.split("profile=")[1].split("|")[0])
check("The machine never sleeps on mains power",
      "standby-timeout-ac 0" in ps1 and "hibernate-timeout-ac 0" in ps1)
check("The task starts the tower at sign-in, restarts it, needs no admin rights to run",
      "-AtLogOn" in ps1 and "-RestartCount 999" in ps1 and "-RunLevel Limited" in ps1
      and "-LogonType Interactive" in ps1 and "dashboard.supervisor --share" in ps1)
check("It never asks for, stores or prints a password",
      "Read-Host" not in ps1 and "AutoAdminLogon" not in ps1 and "DefaultPassword" not in ps1)
check("It ends with the health check", "check_server.py" in ps1)
check("Windows line endings on the .bat files", b"\r\n" in bat and b"\r\n" in off)

print("\n2. LEAVING REMOTE DESKTOP")
check("disconnect_keep_running.bat hands the session to the console with tscon",
      b"tscon.exe" in off and b"/dest:console" in off and b"query user" in off)
check("...and asks for the rights tscon needs", b"-Verb RunAs" in off)

print("\n3. POWERSHELL SYNTAX")
pwsh = shutil.which("pwsh") or ("/tmp/pwsh/pwsh" if Path("/tmp/pwsh/pwsh").exists() else None)
if not pwsh:
    print("  SKIP  PowerShell is not installed here")
else:
    for name in ("install_server.ps1", "deploy/azure/install_worker.ps1"):
        out = subprocess.run([pwsh, "-NoProfile", "-Command",
                              "$t=$null;$e=$null;[void][System.Management.Automation.Language."
                              "Parser]::ParseFile('{0}',[ref]$t,[ref]$e);$e.Count".format(
                                  HERE / name)], capture_output=True, text=True, timeout=120)
        check("{0} parses with no errors".format(name), out.stdout.strip() == "0",
              out.stdout + out.stderr)

print("\n4. THE HEALTH CHECK RUNS")
out = subprocess.run([sys.executable, str(HERE / "check_server.py")],
                     capture_output=True, text=True, timeout=300,
                     env=dict(os.environ, ATA_DASHBOARD_PORT="8799"))
text = out.stdout
check("It runs to the end and prints a summary", "passed" in text and "skipped" in text, text[-300:])
check("Python and the packages are checked", "PASS  Python" in text and "Playwright" in text)
check("A real browser is opened and closed", "A browser window opens and closes" in text)
check("A stopped dashboard is a WARN with what to do, not a crash",
      "WARN  Dashboard on this machine (port 8799)" in text)
check("The Hub and carriers are probed", "Logistics Hub" in text and "Carrier site" in text)
check("Windows-only items are SKIPPED off Windows, not failed",
      os.name == "nt" or text.count("Windows only") == 5)
check("Its exit code is 1 when anything FAILs", out.returncode == (1 if "FAIL " in text else 0))

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
