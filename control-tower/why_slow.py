"""
Why is the dashboard slow ON THIS MACHINE?

    python why_slow.py

Nothing here changes anything. It measures the parts that can actually be
slow, on the machine that is actually slow, and prints where the time goes —
because the same build measures 99ms to load and 0 long tasks on the
development machine, so the cause is local and guessing at it from the other
end has not worked.

Send the whole output back. Every line is a number, not an opinion.
"""

import json
import os
import shutil
import socket
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))
PORT = int(os.environ.get("PORT", "8791"))


def head(title):
    print("")
    print("=" * 72)
    print(title)
    print("=" * 72)


def line(label, value, note=""):
    print("  {0:<34} {1:>14}  {2}".format(label, value, note))


def verdict(text):
    print("")
    print("  >> " + text)


# ── 1. the machine ───────────────────────────────────────────────────
head("1. THE MACHINE")
line("python", sys.version.split()[0])
line("platform", sys.platform)
try:
    line("cpu cores", str(os.cpu_count()))
except Exception:
    pass
try:
    import psutil
    line("cpu in use right now", "{0:.0f}%".format(psutil.cpu_percent(interval=1.0)))
    mem = psutil.virtual_memory()
    line("memory in use", "{0:.0f}%".format(mem.percent),
         "{0:.1f} GB free".format(mem.available / 1e9))
    # The automation drives a VISIBLE Edge with slow_mo, on this same machine.
    browsers = []
    for proc in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        name = (proc.info.get("name") or "").lower()
        if any(b in name for b in ("msedge", "chrome", "chromium", "playwright")):
            browsers.append(proc)
    line("browser processes running", str(len(browsers)),
         "the automation's own Edge counts here")
    if browsers:
        rss = sum((p.info.get("memory_info").rss if p.info.get("memory_info") else 0)
                  for p in browsers)
        line("...using memory", "{0:.1f} GB".format(rss / 1e9))
    if psutil.cpu_percent(interval=None) > 70 or mem.percent > 85:
        verdict("This machine is already loaded before the dashboard is asked "
                "to do anything. A dashboard cannot be fast on a machine that "
                "is out of CPU or memory.")
except ImportError:
    line("psutil", "not installed", "pip install psutil for CPU/memory figures")

free = shutil.disk_usage(str(HERE)).free
line("free disk", "{0:.1f} GB".format(free / 1e9))

# ── 2. how much data has accumulated ─────────────────────────────────
head("2. HOW MUCH DATA HAS ACCUMULATED")
big = []
try:
    from ml import config as ml_config
    tel = Path(ml_config.TELEMETRY_PATH)
    if tel.exists():
        size = tel.stat().st_size
        rows = sum(1 for _ in tel.open("r", encoding="utf-8", errors="replace"))
        line("ATLAS telemetry", "{0:.1f} MB".format(size / 1e6), "{0} rows".format(rows))
        if size > 20e6:
            big.append("telemetry is {0:.0f} MB".format(size / 1e6))
    else:
        line("ATLAS telemetry", "none yet", str(tel))
except Exception as error:
    line("ATLAS telemetry", "unreadable", str(error)[:60])

for name, pattern in (("run logs", "logs/*.log"), ("results", "*.csv"),
                      ("screenshots", "screenshots/*")):
    try:
        files = list(HERE.glob(pattern)) + list(Path("C:/Automation").glob(pattern))
    except Exception:
        files = list(HERE.glob(pattern))
    total = sum(f.stat().st_size for f in files if f.is_file())
    if files:
        line(name, "{0:.1f} MB".format(total / 1e6), "{0} files".format(len(files)))

# ── 3. the server's own cost ─────────────────────────────────────────
head("3. THE SERVER'S OWN COST")
try:
    import mlstatus
    mlstatus.invalidate()
    t0 = time.time()
    snap = mlstatus.snapshot()
    cold = (time.time() - t0) * 1000
    t0 = time.time()
    for _ in range(20):
        mlstatus.snapshot()
    warm = (time.time() - t0) * 1000 / 20
    line("ATLAS panel, first build", "{0:.0f} ms".format(cold))
    line("ATLAS panel, from cache", "{0:.2f} ms".format(warm))
    if cold > 400:
        big.append("the ATLAS panel takes {0:.0f} ms to build".format(cold))
        verdict("The ATLAS panel is expensive to build because the telemetry "
                "history is long. It is cached for up to 20s, so this is paid "
                "about three times a minute rather than per request — but if "
                "this number keeps climbing, the telemetry file wants "
                "archiving.")
except Exception as error:
    line("ATLAS panel", "could not measure", str(error)[:60])

# ── 4. the dashboard, end to end ─────────────────────────────────────
head("4. THE DASHBOARD, END TO END")
started = False
try:
    import server
    server.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=None)
    started = True
    time.sleep(1.2)
except Exception as error:
    line("could not start the server", "", str(error)[:70])

if started:
    base = "http://127.0.0.1:{0}".format(PORT)

    def hit(path, timeout=60):
        t0 = time.time()
        try:
            with urllib.request.urlopen(base + path, timeout=timeout) as response:
                body = response.read()
            return (time.time() - t0) * 1000, len(body), None
        except Exception as error:                      # noqa: BLE001
            return (time.time() - t0) * 1000, 0, str(error)[:50]

    for path, label in (("/", "the page itself"),
                        ("/api/state", "the run data"),
                        ("/api/ml", "the ATLAS panel")):
        times, size, err = [], 0, None
        for _ in range(8):
            ms, size, err = hit(path)
            times.append(ms)
            time.sleep(0.15)
        if err:
            line(label, "FAILED", err)
            continue
        times.sort()
        note = "{0:.0f} KB".format(size / 1024)
        if times[-1] > 1000:
            note += "   <-- SLOW"
            big.append("{0} peaks at {1:.0f} ms".format(label, times[-1]))
        line(label, "{0:.0f} ms median".format(times[len(times) // 2]),
             "worst {0:.0f} ms   {1}".format(times[-1], note))

    # Is the port reachable the way a colleague would reach it?
    try:
        host = socket.gethostbyname(socket.gethostname())
        line("this machine's LAN address", host, "share http://{0}:{1}/".format(host, PORT))
    except Exception:
        pass

# ── 5. what the browser has to draw ──────────────────────────────────
head("5. WHAT THE BROWSER HAS TO DRAW")
index = HERE / "dashboard" / "static" / "index.html"
if index.exists():
    text = index.read_text(encoding="utf-8", errors="replace")
    line("dashboard page", "{0:.0f} KB".format(len(text.encode()) / 1024))
    line("external requests", str(text.count("http://") + text.count("https://") - 1),
         "should be 0 apart from the SVG namespace")
    line("<audio> elements", str(text.count("<audio ")), "should be 0")
assets = HERE / "dashboard" / "static" / "assets"
if assets.is_dir():
    total = sum(f.stat().st_size for f in assets.rglob("*") if f.is_file())
    line("bundled assets", "{0:.1f} MB".format(total / 1e6))

# ── the verdict ──────────────────────────────────────────────────────
head("WHAT THIS SAYS")
if big:
    print("  Slow parts found on this machine:")
    for item in big:
        print("    - " + item)
else:
    print("  Nothing here is slow. Every measured part of the dashboard is")
    print("  responding in milliseconds on this machine.")
    print("")
    print("  If it still FEELS slow, the remaining candidates are things this")
    print("  script cannot see from the outside:")
    print("")
    print("    1. The startup sequence. It runs for about 6.6 seconds before")
    print("       the dashboard appears, once per browser session. Click Skip,")
    print("       or press Escape, to go straight in.")
    print("    2. Contention with the automation itself. update_eta.py drives")
    print("       a VISIBLE Edge window with a deliberate 150ms pause between")
    print("       actions. While a run is going, that browser and this")
    print("       dashboard are competing for the same machine.")
    print("    3. The browser you are viewing it in, or a remote/RDP session")
    print("       between you and the machine, rather than the dashboard.")
print("")
print("  Send this whole output back — it is all measurements, no guesses.")
print("")
if started:
    print("  The dashboard is running at {0}/ for as long as this window is".format(base))
    print("  open, so you can click around and see whether it feels slow.")
    print("  Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n  stopped.")
