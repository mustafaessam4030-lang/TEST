"""
ATLAS's local AI, switched on by itself when it is installed on this machine.

    python update_eta.py            (or the supervisor) starts the dashboard;
                                    the dashboard calls apply() once

apply() looks for two local services and, when it finds them, turns them on
for this process only:

    Ollama    http://127.0.0.1:11434 with the model pulled (qwen3.5:4b unless
              ATLAS_LLM_MODEL names another)  -> ATLAS talks naturally
    SearXNG   http://127.0.0.1:8888 answering JSON  -> ATLAS can search the web

Neither is required: without them ATLAS answers with its rules, as before.
It never installs, downloads or writes anything, and a setting the operator
has already made wins. When a service is not up yet (it may start after the
tower at sign-in), it keeps looking in the background for a few minutes.

    ATLAS_AI=0      do not look; ATLAS stays rule-based
    ATLAS_LLM_*, ATLAS_SEARCH_URL   set them yourself to override
"""

import json
import os
import threading
import time
import urllib.request

from . import research as R

DEFAULT_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_SEARCH = "http://127.0.0.1:8888"
RETRY_FOR_S = 600
RETRY_EVERY_S = 30
WARM_TIMEOUT_S = 900

_started = {"done": False}


def _get(url, timeout=4):
    with R.open_url(url, timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def check_model(url, model):
    """(ok, detail) — Ollama answering, and the model pulled."""
    try:
        names = [m.get("name") for m in _get(url.rstrip("/") + "/api/tags").get("models") or []]
    except Exception as error:
        return False, "Ollama is not answering at {0} ({1}). Install it from ollama.com " \
                      "and start it.".format(url, type(error).__name__)
    wanted = model if ":" in model else model + ":latest"
    if wanted not in names:
        return False, "Ollama is running but {0} is not pulled. Run: ollama pull {0}".format(model)
    return True, "{0} ready at {1}".format(model, url)


def check_search(url):
    """(ok, detail) — a real query through the search service."""
    if not url:
        return False, "no search service configured"
    try:
        found = _get(url.rstrip("/") + "/search?format=json&q=air+waybill", timeout=10)
        count = len(found.get("results") or [])
    except Exception as error:
        return False, "no search service at {0} ({1})".format(url, type(error).__name__)
    if not count:
        return False, "the search service at {0} answered with no results " \
                      "(its engines may be blocked from this network)".format(url)
    return True, "{0} results for a test query at {1}".format(count, url)


def _use_model(url, model, warm=True):
    os.environ.setdefault("ATLAS_LLM_PROVIDER", "ollama")
    os.environ.setdefault("ATLAS_LLM_URL", url)
    os.environ.setdefault("ATLAS_LLM_MODEL", model)
    # A CPU-only model needs time; one attempt, then ATLAS's own answer.
    os.environ.setdefault("ATLAS_LLM_TIMEOUT_S", "120")
    os.environ.setdefault("ATLAS_LLM_RETRIES", "0")
    # Leave a core for Edge and the automation: with every core taken, a
    # CPU-only model stalls (measured) and the browser slows.
    os.environ.setdefault("ATLAS_LLM_THREADS", str(max(1, (os.cpu_count() or 2) - 1)))
    # Loaded once, kept for the working day: reloading takes minutes on a
    # cold disk.
    os.environ.setdefault("ATLAS_LLM_KEEP_ALIVE", "8h")
    if warm:
        _warm(url, model)


def _warm(url, model):
    """Load the model in the background, with all the time it needs. After a
    restart, reading it from disk took over 2 minutes (measured); a question
    asked meanwhile gets ATLAS's own answer, and an aborted load would start
    over with every question."""
    if _started.get("warming"):
        return
    _started["warming"] = True

    def load():
        body = {"model": model, "keep_alive": os.environ["ATLAS_LLM_KEEP_ALIVE"],
                "options": {"num_thread": int(os.environ["ATLAS_LLM_THREADS"])}}
        request = urllib.request.Request(url.rstrip("/") + "/api/generate",
                                         data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        try:
            with R.open_url(request, WARM_TIMEOUT_S) as response:
                response.read()
        except Exception:
            _started["warming"] = False             # a later look tries again

    threading.Thread(target=load, name="atlas-model-load", daemon=True).start()


def _probe(warm=True):
    """One look. -> (model_ok, model_detail, search_ok, search_detail)"""
    url = os.environ.get("ATLAS_LLM_URL") or DEFAULT_OLLAMA
    model = os.environ.get("ATLAS_LLM_MODEL") or DEFAULT_MODEL
    if (os.environ.get("ATLAS_LLM_PROVIDER") or "").strip().lower() not in ("", "ollama"):
        model_ok, model_detail = False, "set by ATLAS_LLM_PROVIDER"
    else:
        model_ok, model_detail = check_model(url, model)
        if model_ok:
            _use_model(url, model, warm)
    search = os.environ.get("ATLAS_SEARCH_URL") or DEFAULT_SEARCH
    search_ok, search_detail = check_search(search)
    if search_ok:
        os.environ.setdefault("ATLAS_SEARCH_URL", search)
    return model_ok, model_detail, search_ok, search_detail


def apply(log=print):
    """Turn on what is installed; keep looking a while for what is not yet up."""
    if _started["done"] or (os.environ.get("ATLAS_AI") or "").strip() == "0":
        return
    _started["done"] = True
    model_ok, model_detail, search_ok, search_detail = _probe()
    log("ATLAS conversation: {0}".format(
        "ON — " + model_detail if model_ok else "off — " + model_detail))
    log("ATLAS web research: {0}".format(
        "ON — " + search_detail if search_ok else "off — " + search_detail))
    if model_ok and search_ok:
        return

    def keep_looking():
        found_model, found_search = model_ok, search_ok
        deadline = time.time() + RETRY_FOR_S
        while time.time() < deadline and not (found_model and found_search):
            time.sleep(RETRY_EVERY_S)
            m_ok, m_detail, s_ok, s_detail = _probe()
            if m_ok and not found_model:
                found_model = True
                log("ATLAS conversation: ON — " + m_detail)
            if s_ok and not found_search:
                found_search = True
                log("ATLAS web research: ON — " + s_detail)

    threading.Thread(target=keep_looking, name="atlas-autoconfig", daemon=True).start()


def _machine():
    """CPU cores, total and free memory (GB), free disk (GB) here — or None."""
    import shutil
    total = free = None
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            total, free = status.ullTotalPhys / 1e9, status.ullAvailPhys / 1e9
        else:
            info = dict(line.split(":", 1) for line in open("/proc/meminfo"))
            total = int(info["MemTotal"].split()[0]) / 1e6
            free = int(info["MemAvailable"].split()[0]) / 1e6
    except Exception:
        pass
    disk = shutil.disk_usage(os.path.dirname(os.path.abspath(__file__))).free / 1e9
    return os.cpu_count(), total, free, disk


def self_test():
    """
    python -m intelligence.autoconfig --test

    On the machine that runs the automation: checks the PC, loads the model,
    asks ATLAS a real question the way the dashboard does, and prints PASS or
    FAIL with the reason. It reads nothing from the Hub and changes nothing.
    """
    results = []

    def line(ok, label, detail=""):
        results.append(ok)
        print("  {0:<5} {1}{2}".format("PASS" if ok else ("WARN" if ok is None else "FAIL"),
                                      label, ("  — " + detail) if detail else ""), flush=True)

    print("ATLAS AI self-test on this PC")
    cores, total, free, disk = _machine()
    # A short machine is a warning, not a failure: ATLAS still answers, slower.
    line(True if (cores or 0) >= 4 else None, "CPU cores: {0}".format(cores),
         "" if (cores or 0) >= 4 else "answers will be slow with fewer than 4")
    if total is not None:
        line(True if free >= 4.5 else None, "Memory: {0:.1f} GB total, {1:.1f} GB free".format(
            total, free), "" if free >= 4.5 else "the model needs about 4 GB free")
    line(True if disk >= 5 else None, "Free disk here: {0:.0f} GB".format(disk))

    m_ok, m_detail, s_ok, s_detail = _probe(warm=False)
    line(m_ok, "Ollama and the model", m_detail)
    line(s_ok if s_ok else None, "Web search (optional)", s_detail)
    if not m_ok:
        print("\nRESULT: ATLAS AI is NOT ready — fix the FAIL line above, then run this again.")
        return 1

    # The first load after a restart can take minutes; wait for it here.
    os.environ["ATLAS_LLM_TIMEOUT_S"] = "600"
    url, model = os.environ["ATLAS_LLM_URL"], os.environ["ATLAS_LLM_MODEL"]
    print("  ...   loading the model into memory (first time can take a few minutes)",
          flush=True)
    started = time.time()
    body = {"model": model, "keep_alive": os.environ["ATLAS_LLM_KEEP_ALIVE"],
            "options": {"num_thread": int(os.environ["ATLAS_LLM_THREADS"])}}
    try:
        request = urllib.request.Request(url.rstrip("/") + "/api/generate",
                                         data=json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        with R.open_url(request, WARM_TIMEOUT_S) as response:
            response.read()
        line(True, "Model loaded", "{0:.0f} s".format(time.time() - started))
    except Exception as error:
        line(False, "Model loaded", str(error)[:120])
        print("\nRESULT: ATLAS AI is NOT ready — the model did not load.")
        return 1

    from dashboard import assistant
    from dashboard.bridge import ControlTowerState
    state = ControlTowerState().snapshot()
    questions = [("How is the automation doing right now?", False)]
    if s_ok:
        questions.append(("What does ERR_HTTP2_PROTOCOL_ERROR mean in Playwright?", True))
    for question, needs_web in questions:
        started = time.time()
        reply = assistant.answer(question, state)
        used = (reply.get("llm") or {}).get("used")
        detail = "{0:.0f} s".format(time.time() - started)
        if not used:
            detail += "; " + str((reply.get("llm") or {}).get("reason"))
        line(bool(used), "ATLAS answered in conversation: " + question, detail)
        if needs_web:
            line(bool(reply.get("web_sources")), "Real web search with sources",
                 ", ".join(w["url"] for w in (reply.get("web_sources") or [])[:2]))
        print("\n" + "\n".join("        " + x for x in (reply.get("answer") or "").splitlines()[:8])
              + "\n", flush=True)

    if all(r is not False for r in results):
        print("RESULT: ATLAS AI is working on this PC. Start the automation as usual.")
        return 0
    print("RESULT: something failed — send a photo of this window.")
    return 1


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        sys.exit(self_test())
    # python -m intelligence.autoconfig  — what this machine has, nothing changed.
    m_ok, m_detail, s_ok, s_detail = _probe(warm=False)
    print("ATLAS conversation: {0}".format(("READY — " if m_ok else "OFF — ") + m_detail))
    print("ATLAS web research: {0}".format(("READY — " if s_ok else "OFF — ") + s_detail))
