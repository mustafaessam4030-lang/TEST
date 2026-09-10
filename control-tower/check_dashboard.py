"""
Control Tower diagnosis.

Put this next to update_eta.py and run it:

    python check_dashboard.py

It checks every reason the dashboard can fail to appear, prints a verdict for
each, and if everything passes it starts the server so you can confirm the
page loads.
"""

import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 8787
ok = True


def say(passed, label, detail=""):
    global ok
    if not passed:
        ok = False
    mark = "  OK  " if passed else " FAIL "
    print("[{0}] {1}".format(mark, label))
    if detail:
        for line in str(detail).splitlines():
            print("         " + line)


print("=" * 66)
print("CONTROL TOWER DIAGNOSIS")
print("Folder: {0}".format(HERE))
print("=" * 66)

# 1 — Python version
say(
    sys.version_info >= (3, 7),
    "Python {0}.{1}.{2}".format(*sys.version_info[:3]),
    "" if sys.version_info >= (3, 7) else "Python 3.7 or newer is required.",
)

# 2 — files in place
PACKAGE = ["dashboard/__init__.py", "dashboard/bridge.py",
           "dashboard/server.py", "dashboard/static/index.html"]
FLAT = ["bridge.py", "server.py", "index.html"]

package_ok = all((HERE / name).exists() for name in PACKAGE)
flat_ok = all((HERE / name).exists() for name in FLAT)

if package_ok:
    say(True, "Dashboard files present (dashboard package)")
elif flat_ok:
    say(True, "Dashboard files present (flattened layout — supported)",
        "The zip was extracted without its folders. This still runs, but the\n"
        "tidy layout is dashboard/bridge.py, dashboard/server.py and\n"
        "dashboard/static/index.html.")
else:
    have = sorted(x.name for x in HERE.iterdir() if x.is_file())
    say(False, "Dashboard files present",
        "Missing both layouts.\nExpected either:\n  " +
        "\n  ".join(PACKAGE) + "\nor:\n  " + "\n  ".join(FLAT) +
        "\n\nFiles actually in this folder:\n  " + "\n  ".join(have) +
        "\n\nExtract Control_Tower.zip with 'Extract All' so the folders\n"
        "are kept — do not drag single files out of the zip viewer.")

missing = [] if (package_ok or flat_ok) else PACKAGE

# 3 — nothing else named 'dashboard' shadowing the package
shadow = HERE / "dashboard.py"
say(
    not shadow.exists(),
    "No dashboard.py shadowing the package",
    "" if not shadow.exists() else
    "{0} exists and hides the dashboard folder. Rename or delete it.".format(shadow),
)

# 4 — imports
if not missing:
    sys.path.insert(0, str(HERE))
    try:
        try:
            from dashboard.bridge import bridge
            from dashboard import server
        except ImportError:
            sys.path.insert(0, str(HERE / "dashboard"))
            from bridge import bridge
            import server
        say(True, "Imports work")
    except Exception as error:
        say(False, "Imports work", "{0}: {1}".format(type(error).__name__, error))
        bridge = server = None
else:
    bridge = server = None

# 5 — port free
probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    probe.bind(("127.0.0.1", PORT))
    say(True, "Port {0} is free".format(PORT))
    free = True
except OSError as error:
    free = False
    say(
        False,
        "Port {0} is free".format(PORT),
        "{0}\nSomething is already listening. Either an earlier run is still\n"
        "open at http://127.0.0.1:{1}/ , or another program took the port.\n"
        "Change DASHBOARD_PORT in update_eta.py if you need a different one.".format(error, PORT),
    )
finally:
    probe.close()

# 6 — the switch in update_eta.py
script = HERE / "update_eta.py"
if script.exists():
    text = script.read_text(encoding="utf-8", errors="replace")
    on = "DASHBOARD_ENABLED = True" in text
    hooked = "tower.run_started(" in text
    say(on, "DASHBOARD_ENABLED is True in update_eta.py",
        "" if on else "Set DASHBOARD_ENABLED = True near the top of update_eta.py.")
    say(hooked, "update_eta.py is the patched version",
        "" if hooked else
        "This update_eta.py has no dashboard hooks. You are running the\n"
        "original script. Replace it with the patched one from the delivery.")
else:
    say(False, "update_eta.py found next to this script",
        "Put check_dashboard.py in the same folder as update_eta.py.")

print("=" * 66)

if not ok:
    print("Fix the FAIL lines above, then run this again.")
    try:
        input("Press ENTER to close...")
    except EOFError:
        pass
    sys.exit(1)

print("Everything checks out. Starting the dashboard so you can confirm it.")
print("A browser window should open. Press Ctrl+C here to stop.")
print("=" * 66)

bridge.log("Diagnosis run — the automation is not running, this is a test page.")
server.serve_forever(port=PORT)
