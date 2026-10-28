"""
ATLAS's local AI, switched on by itself when it is installed on this machine.

    python update_eta.py            (or the supervisor) starts the dashboard;
                                    the dashboard calls apply() once

apply() looks for two local services and, when it finds them, turns them on
for this process only:

    Ollama    http://127.0.0.1:11434 with the model pulled (qwen3:30b when it
              is pulled and the PC has 30 GB+ of RAM, else qwen3.5:4b, unless
              ATLAS_LLM_MODEL names another)  -> ATLAS talks naturally
    SearXNG   http://127.0.0.1:8888 answering JSON  -> ATLAS can search the web

Neither is required: without them ATLAS answers with its rules, as before.
A setting the operator has already made wins.

THE MODEL IS CHOSEN AUTOMATICALLY. On a PC with the memory and disk for it
(30 GB+ RAM, 25 GB+ free disk) and the 4B already working, the stronger model
(qwen3:30b) is downloaded once in the background through Ollama; when it is
ready ATLAS switches to it without a restart, and keeps the 4B for pictures.
If the strong model then times out three answers in a row on this PC, ATLAS
goes back to the 4B by itself. ATLAS_AUTO_UPGRADE=0 turns this off; a model the
operator names in ATLAS_LLM_MODEL is never changed. When a service is not up yet (it may start after the
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
# The stronger model, used by itself when it is pulled and the PC has the
# memory for it: a "mixture of experts" model, 30B in all but ~3B working per
# word, so it understands far better than the 4B at about the same speed on
# a CPU. It needs ~19 GB of RAM while loaded. Text only: pictures still go to
# the 4B (ATLAS_LLM_VISION_MODEL), which reads images.
STRONG_MODELS = ("qwen3:30b", "qwen3:30b-a3b", "gpt-oss:20b")
STRONG_MIN_RAM_GB = 30
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_SEARCH = "http://127.0.0.1:8888"
RETRY_FOR_S = 600
RETRY_EVERY_S = 30
# After the first ten minutes: keep looking, less often, for as long as the
# tower runs (Ollama started by hand an hour later still switches ATLAS on).
RETRY_SLOW_S = 300
WARM_TIMEOUT_S = 900

_started = {"done": False}



def model_threads(cores=None):
    """
    CPU threads for the model, leaving room for Edge (the automation) and the
    dashboard: at least two cores, or a quarter of them, stay free. Using all
    but one made the dashboard stutter whenever ATLAS was thinking.
    """
    cores = cores or os.cpu_count() or 2
    return max(1, cores - max(2, cores // 4))

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
    # A CPU-only model needs time (a web answer took 137 s on 4 cores);
    # one attempt, then ATLAS's own answer.
    os.environ.setdefault("ATLAS_LLM_TIMEOUT_S", "180")
    os.environ.setdefault("ATLAS_LLM_RETRIES", "0")
    # Leave a core for Edge and the automation: with every core taken, a
    # CPU-only model stalls (measured) and the browser slows.
    os.environ.setdefault("ATLAS_LLM_THREADS", str(model_threads()))
    # Loaded once, kept for the working day: reloading takes minutes on a
    # cold disk.
    os.environ.setdefault("ATLAS_LLM_KEEP_ALIVE", "8h")
    # One question in the model at a time, two waiting, the rest answered
    # from the records at once (load test 2026-10-09: without it, 8 of 10
    # simultaneous questions timed out in Ollama's queue). 0 turns it off.
    os.environ.setdefault("ATLAS_LLM_SLOTS", "1")
    os.environ.setdefault("ATLAS_LLM_QUEUE", "2")
    os.environ.setdefault("ATLAS_LLM_WAIT_S", "150")
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
            _started["warm_done"] = True            # from now on, slowness is the model's
        except Exception:
            _started["warming"] = False             # a later look tries again

    threading.Thread(target=load, name="atlas-model-load", daemon=True).start()


def _pulled(url):
    try:
        return {m.get("name") for m in _get(url.rstrip("/") + "/api/tags").get("models") or []}
    except Exception:
        return set()


def choose_model(url, pulled=None, ram_gb=None):
    """
    -> (model, vision_model or None). The operator's ATLAS_LLM_MODEL wins.
    Otherwise the stronger model when it is pulled and the PC has at least
    STRONG_MIN_RAM_GB of memory, with the 4B kept for pictures; else the 4B.
    """
    pulled = _pulled(url) if pulled is None else pulled
    if os.environ.get("ATLAS_LLM_MODEL"):
        chosen = os.environ["ATLAS_LLM_MODEL"]
        # A text-only model named by the operator still has pictures read by the 4B.
        text_only = chosen in STRONG_MODELS or chosen.startswith("gpt-oss")
        return chosen, (DEFAULT_MODEL if text_only and DEFAULT_MODEL in pulled else None)
    if ram_gb is None:
        ram_gb = _machine()[1] or 0
    gave_up = fell_back_here()
    strong = next((m for m in STRONG_MODELS if m in pulled and m != gave_up), None)
    if strong and ram_gb >= STRONG_MIN_RAM_GB:
        return strong, (DEFAULT_MODEL if DEFAULT_MODEL in pulled else None)
    return DEFAULT_MODEL, None


def _probe(warm=True):
    """One look. -> (model_ok, model_detail, search_ok, search_detail)"""
    url = os.environ.get("ATLAS_LLM_URL") or DEFAULT_OLLAMA
    model, vision = choose_model(url)
    if vision:
        os.environ.setdefault("ATLAS_LLM_VISION_MODEL", vision)
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


def _ollama_exe():
    """Ollama's own program on this PC, or None. Where its Windows installer
    puts it (per user), else on PATH."""
    import shutil
    local = os.environ.get("LOCALAPPDATA")
    if local:
        exe = os.path.join(local, "Programs", "Ollama", "ollama.exe")
        if os.path.isfile(exe):
            return exe
    return shutil.which("ollama")


def _start_ollama():
    """Start Ollama when it is installed but not running and ATLAS uses this
    PC's default address (after a restart its tray app may not be up yet).
    -> True if started. Nothing is downloaded or installed here."""
    if (os.environ.get("ATLAS_LLM_URL") or DEFAULT_OLLAMA).rstrip("/") != DEFAULT_OLLAMA:
        return False
    if (os.environ.get("ATLAS_LLM_PROVIDER") or "").strip().lower() not in ("", "ollama"):
        return False
    exe = _ollama_exe()
    if not exe:
        return False
    import subprocess
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
        return True
    except OSError:
        return False


def _start_local_search():
    """Start this PC's SearXNG (intelligence/searxng_local.py) when it is
    installed and the default address is the one in use. -> True if started."""
    if (os.environ.get("ATLAS_SEARCH_URL") or DEFAULT_SEARCH).rstrip("/") != DEFAULT_SEARCH:
        return False
    try:
        from . import searxng_local
        return searxng_local.installed() and searxng_local.start()
    except Exception:
        return False


AUTO_UPGRADE_MODEL = "qwen3:30b"
AUTO_UPGRADE_DISK_GB = 25
FALLBACK_AFTER_TIMEOUTS = 3
_auto = {"operator_model": None, "pulling": False, "timeouts": 0, "fell_back": False,
         "log": print}


def _ollama_disk_free():
    """Free GB where Ollama keeps its models (OLLAMA_MODELS, else the home folder)."""
    import shutil
    where = os.environ.get("OLLAMA_MODELS") or os.path.expanduser("~")
    try:
        return shutil.disk_usage(where).free / 1e9
    except OSError:
        return 0


def _switch_to(model, vision, why):
    # The picture model first, so no picture is ever sent to a text-only model;
    # one the operator set is left alone.
    if not _auto.get("operator_vision"):
        if vision:
            os.environ["ATLAS_LLM_VISION_MODEL"] = vision
        else:
            os.environ.pop("ATLAS_LLM_VISION_MODEL", None)
    os.environ["ATLAS_LLM_MODEL"] = model          # llm.provider() follows at once
    _auto["log"]("ATLAS model: now {0} — {1}".format(model, why))


def _fallback_file():
    from pathlib import Path
    return Path(os.environ.get("ATLAS_INTEL_DIR") or Path(__file__).resolve().parent.parent
                / "ml" / "data" / "intelligence") / "model_fallback.json"


def fell_back_here():
    """The strong model ATLAS gave up on on this PC, or None (kept across restarts;
    delete ml/data/intelligence/model_fallback.json to try it again)."""
    try:
        return json.loads(_fallback_file().read_text(encoding="utf-8")).get("model")
    except Exception:
        return None


def _load_now(url, model, timeout=WARM_TIMEOUT_S):
    """Load `model` into memory and wait for it. -> True when loaded."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/api/generate", data=json.dumps(
            {"model": model, "keep_alive": os.environ.get("ATLAS_LLM_KEEP_ALIVE") or "8h",
             "options": {"num_thread": int(os.environ.get("ATLAS_LLM_THREADS") or
                                           model_threads())}}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with R.open_url(req, timeout) as response:
            response.read()
        return True
    except Exception:
        return False


def _unload(url, model):
    """Free a model's memory now (Ollama keep_alive 0)."""
    try:
        req = urllib.request.Request(url.rstrip("/") + "/api/generate", data=json.dumps(
            {"model": model, "keep_alive": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with R.open_url(req, 30) as response:
            response.read()
    except Exception:
        pass


def auto_upgrade(url, log=None):
    """
    Download the stronger model once, in the background, when this PC can run
    it; switch to it when ready. -> a sentence saying what was decided.
    """
    log = log or _auto["log"]
    if (os.environ.get("ATLAS_AUTO_UPGRADE") or "").strip() == "0":
        return "automatic upgrade is off (ATLAS_AUTO_UPGRADE=0)"
    if _auto["operator_model"]:
        return "the operator chose {0}; it is kept".format(_auto["operator_model"])
    from urllib.parse import urlparse
    if (urlparse(url).hostname or "") not in ("127.0.0.1", "localhost", "::1"):
        return "Ollama runs on another machine ({0}); its models are managed there".format(url)
    if fell_back_here():
        return "{0} was too slow on this PC before, so ATLAS stays on {1}".format(
            fell_back_here(), DEFAULT_MODEL)
    pulled = _pulled(url)
    if any(m in pulled for m in STRONG_MODELS):
        return "a stronger model is already here"
    if DEFAULT_MODEL not in pulled:
        return "the small model is not working yet; no upgrade before it is"
    _cores, total, _free, _disk = _machine()
    if (total or 0) < STRONG_MIN_RAM_GB:
        return "this PC has {0:.0f} GB of RAM; the stronger model needs {1}".format(
            total or 0, STRONG_MIN_RAM_GB)
    disk = _ollama_disk_free()
    if disk < AUTO_UPGRADE_DISK_GB:
        return "only {0:.0f} GB free disk; the stronger model needs {1}".format(
            disk, AUTO_UPGRADE_DISK_GB)
    if _auto["pulling"]:
        return "already downloading {0}".format(AUTO_UPGRADE_MODEL)
    _auto["pulling"] = True

    def pull():
        model = AUTO_UPGRADE_MODEL
        log("ATLAS model: downloading {0} (about 19 GB) in the background; ATLAS keeps "
            "answering with {1} meanwhile".format(model, DEFAULT_MODEL))
        try:
            req = urllib.request.Request(
                url.rstrip("/") + "/api/pull",
                data=json.dumps({"model": model, "name": model, "stream": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            shown, status = -10, ""
            with R.open_url(req, 6 * 3600) as response:
                for raw in response:
                    try:
                        line = json.loads(raw.decode("utf-8", "replace") or "{}")
                    except ValueError:
                        continue
                    if line.get("error"):
                        raise RuntimeError(line["error"])
                    status = line.get("status") or status
                    total_b, done_b = line.get("total"), line.get("completed")
                    if total_b and total_b > 1e9 and done_b:     # the model itself, not small layers
                        pct = int(100 * done_b / total_b)
                        if pct >= shown + 10:
                            shown = pct
                            log("ATLAS model: {0} download {1}%".format(model, pct))
            if status != "success" and model not in _pulled(url):
                raise RuntimeError("the download did not finish ({0})".format(status or "no status"))
        except Exception as error:
            log("ATLAS model: the download of {0} stopped ({1}); ATLAS stays on {2} and "
                "tries again at the next start".format(model, str(error)[:120], DEFAULT_MODEL))
            _auto["pulling"] = False
            return
        _auto["pulling"] = False
        if _auto["operator_model"] or _auto["fell_back"]:
            return
        # Switch only once it is in memory: the questions asked meanwhile are
        # answered by the 4B, and a cold load never counts as the model's fault.
        log("ATLAS model: {0} downloaded; loading it into memory…".format(model))
        if not _load_now(url, model):
            log("ATLAS model: {0} could not be loaded; ATLAS stays on {1}".format(
                model, DEFAULT_MODEL))
            return
        _started["warm_done"] = True
        _switch_to(model, DEFAULT_MODEL, "downloaded and loaded; pictures still go to " +
                   DEFAULT_MODEL)

    threading.Thread(target=pull, name="atlas-model-download", daemon=True).start()
    return "downloading {0} in the background".format(AUTO_UPGRADE_MODEL)


def _watch_model(ok, model, error):
    """Back to the small model when the strong one keeps timing out here."""
    if ok:
        _auto["timeouts"] = 0
        return
    if model not in STRONG_MODELS or _auto["operator_model"] or _auto["fell_back"]:
        return
    if "timed out" not in str(error or "").lower():
        return
    if not _started.get("warm_done"):
        return                   # still loading from disk: not the model's speed
    _auto["timeouts"] += 1
    if _auto["timeouts"] >= FALLBACK_AFTER_TIMEOUTS:
        _auto["fell_back"] = True
        _switch_to(DEFAULT_MODEL, None, "{0} timed out {1} answers in a row on this PC, so "
                   "ATLAS went back to the faster small model (remembered)".format(
                       model, FALLBACK_AFTER_TIMEOUTS))
        try:
            path = _fallback_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"model": model, "at": time.strftime("%Y-%m-%d %H:%M"),
                                        "reason": "timed out {0} answers in a row".format(
                                            FALLBACK_AFTER_TIMEOUTS)}), encoding="utf-8")
        except OSError:
            pass
        url = os.environ.get("ATLAS_LLM_URL") or DEFAULT_OLLAMA
        threading.Thread(target=_unload, args=(url, model), daemon=True).start()


def apply(log=print):
    """Turn on what is installed; keep looking a while for what is not yet up."""
    if _started["done"] or (os.environ.get("ATLAS_AI") or "").strip() == "0":
        return
    _started["done"] = True
    _auto["log"] = log
    _auto["operator_model"] = os.environ.get("ATLAS_LLM_MODEL") or None
    _auto["operator_vision"] = os.environ.get("ATLAS_LLM_VISION_MODEL") or None
    from . import llm as _llm
    if _watch_model not in _llm.WATCHERS:
        _llm.WATCHERS.append(_watch_model)
    model_ok, model_detail, search_ok, search_detail = _probe()
    if model_ok:
        log("ATLAS model choice: " + auto_upgrade(
            os.environ.get("ATLAS_LLM_URL") or DEFAULT_OLLAMA, log))
    try:
        from . import cortex as _cortex
        if _cortex.settings():
            log("ATLAS wording model: Snowflake Cortex ({0}) first, budget {1:,} tokens a day; "
                "the local model is the fallback".format(_cortex.model(), _cortex.budget()))
    except Exception:
        pass
    log("ATLAS conversation: {0}".format(
        "ON — " + model_detail if model_ok else "off — " + model_detail))
    if not model_ok and "not answering" in model_detail and _start_ollama():
        log("ATLAS conversation: Ollama is installed but was not running; started it "
            "(ATLAS switches on as soon as it answers)")
    if not search_ok and _start_local_search():
        log("ATLAS web research: starting the local search service (SETUP_WEB_SEARCH.bat "
            "installed it); it is used as soon as it answers")
    else:
        log("ATLAS web research: {0}".format(
            "ON — " + search_detail if search_ok else "off — " + search_detail))
    if model_ok and search_ok:
        return

    def keep_looking():
        found_model, found_search = model_ok, search_ok
        fast_until = time.time() + RETRY_FOR_S
        while not (found_model and found_search):
            time.sleep(RETRY_EVERY_S if time.time() < fast_until else RETRY_SLOW_S)
            m_ok, m_detail, s_ok, s_detail = _probe()
            if m_ok and not found_model:
                found_model = True
                log("ATLAS conversation: ON — " + m_detail)
                log("ATLAS model choice: " + auto_upgrade(
                    os.environ.get("ATLAS_LLM_URL") or DEFAULT_OLLAMA, log))
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
        # The stronger model needs about 19 GB while loaded, the 4B about 4 GB.
        chosen, _vision = choose_model(os.environ.get("ATLAS_LLM_URL") or DEFAULT_OLLAMA)
        need = 20 if chosen in STRONG_MODELS else 4.5
        line(True if free >= need else None, "Memory: {0:.1f} GB total, {1:.1f} GB free".format(
            total, free), "" if free >= need else "{0} needs about {1:.0f} GB free (already "
            "loaded in Ollama counts as used)".format(chosen, need))
    line(True if disk >= 5 else None, "Free disk here: {0:.0f} GB".format(disk))

    m_ok, m_detail, s_ok, s_detail = _probe(warm=False)
    line(m_ok, "Ollama and the model", m_detail)
    if os.environ.get("ATLAS_LLM_VISION_MODEL"):
        line(True, "Pictures go to", os.environ["ATLAS_LLM_VISION_MODEL"])
    elif m_ok and (os.environ.get("ATLAS_LLM_MODEL") or "") in STRONG_MODELS:
        line(None, "Pictures", "{0} cannot read images: run  ollama pull {1}".format(
            os.environ["ATLAS_LLM_MODEL"], DEFAULT_MODEL))
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


# The models worth comparing on a CPU-only PC (python -m intelligence.autoconfig
# --bench). Only those already pulled are tried; nothing is downloaded.
BENCH_MODELS = (DEFAULT_MODEL,) + STRONG_MODELS
BENCH_TASKS = (
    ("understand (English)", "understand", "why did 8842001173 fail yesterday?"),
    ("understand (Arabic)", "understand", "ليه الشحنة 8842001173 فشلت؟"),
    ("understand (casual)", "understand", "ok what should we look at first today"),
    ("answer from records", "compose",
     "OPERATOR'S QUESTION: why did 8842001173 fail?\n\nRECORDS (ELAP, authoritative):\n"
     "8842001173 on DHL GLOBAL FORWARDING stopped at the Hub write: the Save/Update button "
     "was not found on the Manage page. Nothing was written to the Hub. Likely cause: the "
     "Manage page had not finished loading.\n\nWEB:\n(none)"),
)


def bench(models=None, log=print):
    """
    python -m intelligence.autoconfig --bench [model ...]

    Times each pulled model on the same four ATLAS tasks — reading a question
    (English, Arabic, casual) and writing an answer from records — and checks
    each reply: valid JSON, and for the answer, the fact guard (nothing added
    that the records do not hold). Prints a table and a recommendation.
    Reads nothing from the Hub, changes nothing, downloads nothing.
    """
    from . import converse as CV, factguard as FG, llm as L
    url = os.environ.get("ATLAS_LLM_URL") or DEFAULT_OLLAMA
    pulled = _pulled(url)
    if not pulled:
        log("Ollama is not answering at {0}: start it, then run this again.".format(url))
        return 1
    wanted = [m for m in (models or BENCH_MODELS) if m in pulled]
    if not wanted:
        log("None of {0} is pulled. Pulled here: {1}".format(", ".join(models or BENCH_MODELS),
                                                            ", ".join(sorted(pulled))))
        return 1
    cores, total, free, _disk = _machine()
    log("ATLAS model benchmark — {0} CPU cores, {1} GB RAM ({2} GB free)".format(
        cores, round(total or 0), round(free or 0)))
    threads = int(os.environ.get("ATLAS_LLM_THREADS") or model_threads())
    rows = []
    for model in wanted:
        log("\n{0}: loading (the first load can take minutes)…".format(model))
        t = time.time()
        try:
            req = urllib.request.Request(url.rstrip("/") + "/api/generate", data=json.dumps(
                {"model": model, "keep_alive": "10m", "options": {"num_thread": threads}}).encode(),
                headers={"Content-Type": "application/json"})
            with R.open_url(req, WARM_TIMEOUT_S) as response:
                response.read()
        except Exception as error:
            log("  could not load: {0}".format(str(error)[:120]))
            continue
        load_s = time.time() - t
        provider = L.OllamaProvider(url=url, model=model, timeout=300, retries=0)
        times, good = [], 0
        for label, kind, prompt in BENCH_TASKS:
            t = time.time()
            ok, note = False, ""
            try:
                if kind == "understand":
                    text = provider.generate(CV.UNDERSTAND, "Question: " + prompt,
                                             json_mode=True, max_tokens=160)
                    data = CV._json(text) or {}
                    ok = bool(data.get("question_en")) and ("8842001173" in data["question_en"]
                                                            or "8842001173" not in prompt)
                    note = (data.get("question_en") or text or "")[:60]
                else:
                    text = provider.generate(CV.COMPOSE.format(language="English"), prompt,
                                             json_mode=True, max_tokens=450)
                    data = CV._json(text) or {}
                    guard_ok, bad = FG.check(str(data.get("answer") or ""), [prompt])
                    ok = bool(data.get("answer")) and guard_ok
                    note = (data.get("answer") or "")[:60] if guard_ok else "guard: " + "; ".join(bad)
            except Exception as error:
                note = str(error)[:60]
            took = time.time() - t
            times.append(took)
            good += ok
            log("  {0:<22} {1:>6.1f} s  {2}  {3}".format(label, took, "OK  " if ok else "FAIL", note))
        rows.append((model, load_s, sum(times) / len(times), max(times), good, len(BENCH_TASKS)))
    if not rows:
        return 1
    log("\n{0:<22} {1:>8} {2:>10} {3:>8} {4:>8}".format("model", "load s", "avg answer", "slowest",
                                                        "correct"))
    for m, load_s, avg, worst, good, n in rows:
        log("{0:<22} {1:>8.0f} {2:>9.1f}s {3:>7.1f}s {4:>5}/{5}".format(m, load_s, avg, worst, good, n))
    usable = [r for r in rows if r[4] == r[5] and r[3] <= 90]
    if usable:
        # The strongest model that got every task right within 90 s per answer.
        rank = {m: i for i, m in enumerate(BENCH_MODELS)}
        best = max(usable, key=lambda r: rank.get(r[0], -1))
        log("\nRecommended here: {0} (every task right, slowest answer {1:.0f} s).".format(
            best[0], best[3]))
    else:
        log("\nNo model got every task right within 90 s here; keep {0}.".format(DEFAULT_MODEL))
    return 0


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        sys.exit(self_test())
    if "--bench" in sys.argv:
        sys.exit(bench([a for a in sys.argv[sys.argv.index("--bench") + 1:]
                        if not a.startswith("-")] or None))
    # python -m intelligence.autoconfig  — what this machine has, nothing changed.
    m_ok, m_detail, s_ok, s_detail = _probe(warm=False)
    print("ATLAS conversation: {0}".format(("READY — " if m_ok else "OFF — ") + m_detail))
    print("ATLAS web research: {0}".format(("READY — " if s_ok else "OFF — ") + s_detail))
