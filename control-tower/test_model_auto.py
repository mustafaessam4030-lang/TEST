"""
ATLAS chooses its model by itself: on a PC with the memory and disk, the
stronger model is downloaded in the background and switched to when ready;
if it then keeps timing out, ATLAS goes back to the small model. A model the
operator named is never changed. Stand-in Ollama, no real model, no network.
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="auto_")
from intelligence import autoconfig as AC, llm as L          # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


PULLS = []


class Ollama(BaseHTTPRequestHandler):
    models = ["qwen3.5:4b"]
    fail_pull = False

    def log_message(self, *a):
        pass

    def do_GET(self):
        data = json.dumps({"models": [{"name": m} for m in self.models]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.startswith("/api/pull"):
            PULLS.append(body["model"])
            self.send_response(200); self.end_headers()
            if Ollama.fail_pull:
                self.wfile.write(b'{"error":"registry unreachable"}\n'); return
            for done in (0, 9e9, 18e9):
                self.wfile.write(json.dumps({"status": "pulling", "total": 18e9,
                                             "completed": done}).encode() + b"\n")
                self.wfile.flush()
            Ollama.models = Ollama.models + [body["model"]]
            self.wfile.write(b'{"status":"success"}\n'); return
        data = b"{}"
        self.send_response(200); self.send_header("Content-Length", "2")
        self.end_headers(); self.wfile.write(data)


server = ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
threading.Thread(target=server.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:{0}".format(server.server_address[1])
VARS = ("ATLAS_AI", "ATLAS_LLM_PROVIDER", "ATLAS_LLM_URL", "ATLAS_LLM_MODEL", "ATLAS_LLM_VISION_MODEL",
        "ATLAS_SEARCH_URL", "ATLAS_AUTO_UPGRADE", "ATLAS_LLM_TIMEOUT_S", "ATLAS_LLM_THREADS",
        "ATLAS_LLM_KEEP_ALIVE", "ATLAS_LLM_RETRIES")


def fresh(models, ram=34.3, disk=200.0, **env):
    for v in VARS:
        os.environ.pop(v, None)
    os.environ.update(dict(ATLAS_LLM_URL=URL, ATLAS_SEARCH_URL="http://127.0.0.1:9"), **env)
    Ollama.models, Ollama.fail_pull = list(models), False
    AC._started.clear(); AC._started["done"] = False
    AC._auto.update(operator_model=None, operator_vision=None, pulling=False, timeouts=0,
                    fell_back=False)
    AC._fallback_file().unlink() if AC._fallback_file().exists() else None
    AC._machine = lambda: (4, ram, 20.0, 100.0)
    AC._ollama_disk_free = lambda: disk
    del PULLS[:]


def wait_for(cond, s=5):
    end = time.time() + s
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


LOG = []
print("1. Upgrade by itself")
fresh(["qwen3.5:4b"])
AC.apply(LOG.append)
# On a busy machine the (instant, fake) download can already have finished and
# switched by now: started on the 4B means 4B now, or 30B only after the pull.
check("Starts on the small model at once", os.environ.get("ATLAS_LLM_MODEL") == "qwen3.5:4b" or
      (os.environ.get("ATLAS_LLM_MODEL") == "qwen3:30b" and PULLS == ["qwen3:30b"]), os.environ.get("ATLAS_LLM_MODEL"))
check("Downloads the stronger model in the background", wait_for(lambda: PULLS == ["qwen3:30b"]), PULLS)
check("...and switches to it when ready, keeping the 4B for pictures",
      wait_for(lambda: os.environ.get("ATLAS_LLM_MODEL") == "qwen3:30b")
      and os.environ.get("ATLAS_LLM_VISION_MODEL") == "qwen3.5:4b", LOG[-3:])
check("ATLAS's provider follows without a restart", L.provider().model == "qwen3:30b")
check("The log says each step", any("download 100%" in x or "download 50%" in x for x in LOG)
      and any("now qwen3:30b" in x for x in LOG), LOG)

print("2. Back to the small model when the strong one keeps timing out")
check("It was loaded into memory before the switch", AC._started.get("warm_done") is True)
AC._started["warm_done"] = False
for _ in range(5):
    L._report(False, "qwen3:30b", "the local model is not reachable (timed out)")
check("Timeouts while it is still loading from disk do not count",
      os.environ["ATLAS_LLM_MODEL"] == "qwen3:30b" and AC._auto["timeouts"] == 0)
AC._started["warm_done"] = True
for _ in range(2):
    L._report(False, "qwen3:30b", "the local model is not reachable (timed out)")
check("Two timeouts: still on the strong model", os.environ["ATLAS_LLM_MODEL"] == "qwen3:30b")
L._report(True, "qwen3:30b", None)
for _ in range(2):
    L._report(False, "qwen3:30b", "the local model is not reachable (timed out)")
check("A good answer in between resets the count", os.environ["ATLAS_LLM_MODEL"] == "qwen3:30b")
L._report(False, "qwen3:30b", "the local model is not reachable (timed out)")
check("Three in a row: back to the 4B, pictures too",
      os.environ["ATLAS_LLM_MODEL"] == "qwen3.5:4b" and "ATLAS_LLM_VISION_MODEL" not in os.environ,
      LOG[-2:])
check("Other errors never switch the model", AC._watch_model(False, "qwen3:30b", "HTTP 500") is None)
check("The fallback is remembered on this PC", AC.fell_back_here() == "qwen3:30b")
check("...so after a restart ATLAS stays on the 4B and does not download again",
      AC.choose_model(URL, {"qwen3.5:4b", "qwen3:30b"}, 34.3) == ("qwen3.5:4b", None)
      and "too slow on this PC before" in AC.auto_upgrade(URL))
AC._fallback_file().unlink()

print("3. When it does not upgrade")
fresh(["qwen3.5:4b"], ram=16.0)
LOG = []
AC.apply(LOG.append)
wait_for(lambda: any("needs 30" in x for x in LOG))
check("16 GB RAM: no download, and it says why", PULLS == [] and
      any("needs 30" in x for x in LOG), LOG)
fresh(["qwen3.5:4b"], disk=10.0)
LOG = []
AC.apply(LOG.append)
wait_for(lambda: any("free disk" in x for x in LOG))
check("Not enough free disk: no download, and it says why", PULLS == [] and
      any("free disk" in x for x in LOG), LOG)
fresh(["qwen3.5:4b"], ATLAS_LLM_MODEL="qwen3.5:4b")
LOG = []
AC.apply(LOG.append)
wait_for(lambda: any("operator chose" in x for x in LOG))
check("A model the operator named is kept", PULLS == [] and
      os.environ["ATLAS_LLM_MODEL"] == "qwen3.5:4b" and any("operator chose" in x for x in LOG), LOG)
fresh(["qwen3.5:4b"], ATLAS_LLM_URL="http://10.9.9.9:11434")
check("Ollama on another machine: its models are not managed from here",
      "another machine" in AC.auto_upgrade("http://10.9.9.9:11434"))
fresh(["qwen3.5:4b"], ATLAS_AUTO_UPGRADE="0")
LOG = []
AC.apply(LOG.append)
wait_for(lambda: any("off" in x for x in LOG))
check("ATLAS_AUTO_UPGRADE=0: off", PULLS == [] and any("off" in x for x in LOG), LOG)
fresh(["qwen3.5:4b", "qwen3:30b"])
LOG = []
AC.apply(LOG.append)
wait_for(lambda: os.environ.get("ATLAS_LLM_MODEL") == "qwen3:30b")
check("Already downloaded: used at once, no download", PULLS == [] and
      os.environ["ATLAS_LLM_MODEL"] == "qwen3:30b", LOG)
fresh(["qwen3.5:4b"])
Ollama.fail_pull = True
LOG = []
AC.apply(LOG.append)
check("A download that fails: stays on the 4B and says it will try again",
      wait_for(lambda: any("stopped" in x for x in LOG)) and
      os.environ["ATLAS_LLM_MODEL"] == "qwen3.5:4b", LOG)

print("\n2. Ollama installed but not running (after a restart)")
STARTED = []


class _Popen:
    def __init__(self, args, **kw):
        STARTED.append(args)


import subprocess as _sp                                     # noqa: E402
_real_popen, _real_default = _sp.Popen, AC.DEFAULT_OLLAMA
_real_every, _real_for, _real_slow = AC.RETRY_EVERY_S, AC.RETRY_FOR_S, AC.RETRY_SLOW_S
_sp.Popen = _Popen
AC._ollama_exe = lambda: r"C:\Users\x\AppData\Local\Programs\Ollama\ollama.exe"
try:
    DEAD = "http://127.0.0.1:9"
    AC.DEFAULT_OLLAMA = DEAD
    AC.RETRY_EVERY_S, AC.RETRY_FOR_S, AC.RETRY_SLOW_S = 0.05, 0, 0.05
    fresh(["qwen3.5:4b"], ram=16.0, ATLAS_LLM_URL=DEAD)
    LOG = []
    AC.apply(LOG.append)
    check("Ollama installed but not answering: the tower starts it, hidden",
          STARTED and STARTED[0][1:] == ["serve"] and any("started it" in x for x in LOG), (STARTED, LOG))
    # Ollama answers only after the first (fast) ten minutes are over: still found.
    os.environ["ATLAS_LLM_URL"] = URL
    check("...and ATLAS switches on when it answers, even after the first ten minutes",
          wait_for(lambda: any(x.startswith("ATLAS conversation: ON") for x in LOG)), LOG)
    del STARTED[:]
    fresh(["qwen3.5:4b"], ram=16.0, ATLAS_LLM_URL="http://10.9.9.9:9")
    AC._probe = lambda warm=True: (False, "Ollama is not answering at x", True, "")
    LOG = []
    AC.apply(LOG.append)
    check("Ollama on another machine: never started from here", STARTED == [], STARTED)
    del STARTED[:]
    fresh(["qwen3.5:4b"], ram=16.0, ATLAS_LLM_URL=DEAD)
    AC._ollama_exe = lambda: None
    LOG = []
    AC.apply(LOG.append)
    check("Not installed: nothing is started or downloaded", STARTED == [] and PULLS == [], STARTED)
finally:
    _sp.Popen, AC.DEFAULT_OLLAMA = _real_popen, _real_default
    AC.RETRY_EVERY_S, AC.RETRY_FOR_S, AC.RETRY_SLOW_S = _real_every, _real_for, _real_slow

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
