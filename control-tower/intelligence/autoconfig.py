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

DEFAULT_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_SEARCH = "http://127.0.0.1:8888"
RETRY_FOR_S = 600
RETRY_EVERY_S = 30
WARM_TIMEOUT_S = 900

_started = {"done": False}


def _get(url, timeout=4):
    with urllib.request.urlopen(url, timeout=timeout) as response:
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


def _use_model(url, model):
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
            with urllib.request.urlopen(request, timeout=WARM_TIMEOUT_S) as response:
                response.read()
        except Exception:
            _started["warming"] = False             # a later look tries again

    threading.Thread(target=load, name="atlas-model-load", daemon=True).start()


def _probe():
    """One look. -> (model_ok, model_detail, search_ok, search_detail)"""
    url = os.environ.get("ATLAS_LLM_URL") or DEFAULT_OLLAMA
    model = os.environ.get("ATLAS_LLM_MODEL") or DEFAULT_MODEL
    if (os.environ.get("ATLAS_LLM_PROVIDER") or "").strip().lower() not in ("", "ollama"):
        model_ok, model_detail = False, "set by ATLAS_LLM_PROVIDER"
    else:
        model_ok, model_detail = check_model(url, model)
        if model_ok:
            _use_model(url, model)
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


if __name__ == "__main__":
    # python -m intelligence.autoconfig  — what this machine has, nothing changed.
    m_ok, m_detail, s_ok, s_detail = _probe()
    print("ATLAS conversation: {0}".format(("READY — " if m_ok else "OFF — ") + m_detail))
    print("ATLAS web research: {0}".format(("READY — " if s_ok else "OFF — ") + s_detail))
