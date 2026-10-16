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
# The stronger model, used by itself when it is pulled and the PC has the
# memory for it: a "mixture of experts" model, 30B in all but ~3B working per
# word, so it understands far better than the 4B at about the same speed on
# a CPU. It needs ~19 GB of RAM while loaded. Text only: pictures still go to
# the 4B (ATLAS_LLM_VISION_MODEL), which reads images.
STRONG_MODELS = ("qwen3:30b", "qwen3:30b-a3b")
STRONG_MIN_RAM_GB = 30
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_SEARCH = "http://127.0.0.1:8888"
RETRY_FOR_S = 600
RETRY_EVERY_S = 30
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
    strong = next((m for m in STRONG_MODELS if m in pulled), None)
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


def apply(log=print):
    """Turn on what is installed; keep looking a while for what is not yet up."""
    if _started["done"] or (os.environ.get("ATLAS_AI") or "").strip() == "0":
        return
    _started["done"] = True
    model_ok, model_detail, search_ok, search_detail = _probe()
    log("ATLAS conversation: {0}".format(
        "ON — " + model_detail if model_ok else "off — " + model_detail))
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
BENCH_MODELS = (DEFAULT_MODEL,) + STRONG_MODELS + ("gpt-oss:20b",)
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
