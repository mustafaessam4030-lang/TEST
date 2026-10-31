"""
ATLAS's optional language model: local (Ollama), with Snowflake Cortex in front when set up.

The deterministic ATLAS (dashboard/assistant.py and intelligence/*) is the
authority for every run fact, every success or failure, every action. A model
here only re-phrases an answer ATLAS already built, from the evidence ATLAS
hands it — and intelligence/factguard rejects any phrasing that adds a
number, reference, link or success claim the inputs do not hold. When the
model is not configured, not running, slow or wrong, ATLAS answers exactly as
it does without one.

    ATLAS_LLM_PROVIDER      none (default) | ollama
    (Snowflake Cortex, when set up with python -m intelligence.cortex set,
    answers first and the provider above is its fallback: intelligence/cortex.py)
    ATLAS_LLM_URL           default http://127.0.0.1:11434 (Ollama)
    ATLAS_LLM_MODEL         e.g. qwen2.5:7b-instruct  (no default: must be named)
    ATLAS_LLM_VISION_MODEL  the model for questions with a picture (default:
                            ATLAS_LLM_MODEL); for a text-only main model
    ATLAS_LLM_TIMEOUT_S     default 20
    ATLAS_LLM_RETRIES       default 1 (with backoff)
    ATLAS_LLM_THREADS       CPU threads for the model (default: Ollama's choice;
                            atlas_demo.py sets cores - 1)
    ATLAS_LLM_ALLOW_REMOTE  1 = allow a non-local host (default: localhost /
                            private network only, so a hosted, billed endpoint
                            cannot be configured by accident)

Nothing is installed or required by this module. Standard library only.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

from . import research as R

HEALTH_TTL_S = 30.0


class LLMError(Exception):
    pass


class NullProvider(object):
    """No model: ATLAS's deterministic answers, unchanged."""
    name = "none"
    model = None

    def health(self):
        return {"ok": False, "provider": "none", "model": None,
                "detail": "no local model configured (ATLAS_LLM_PROVIDER=none)"}

    def generate(self, system, prompt, timeout=None, json_mode=False, max_tokens=700,
                 images=None):
        raise LLMError("no local model is configured")


class OllamaProvider(object):
    name = "ollama"

    def __init__(self, url=None, model=None, timeout=None, retries=None):
        self.url = (url or os.environ.get("ATLAS_LLM_URL") or "http://127.0.0.1:11434").rstrip("/")
        self.model = model or (os.environ.get("ATLAS_LLM_MODEL") or "").strip() or None
        self.timeout = float(timeout or os.environ.get("ATLAS_LLM_TIMEOUT_S") or 20)
        self.retries = int(retries if retries is not None else
                           os.environ.get("ATLAS_LLM_RETRIES") or 1)
        self._health = None
        self._lock = threading.Lock()

    def _allowed(self):
        return R.local_host(self.url) or os.environ.get("ATLAS_LLM_ALLOW_REMOTE") == "1"

    def _call(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with R.open_url(request, timeout or self.timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            raise LLMError("the local model answered HTTP {0}".format(error.code))
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise LLMError("the local model is not reachable ({0})".format(
                str(getattr(error, "reason", error))[:120]))

    def health(self):
        """{"ok", "provider", "model", "detail"} — cached for 30 s."""
        with self._lock:
            if self._health and time.time() - self._health[0] < HEALTH_TTL_S:
                return self._health[1]
        if not self.model:
            out = {"ok": False, "provider": "ollama", "model": None,
                   "detail": "ATLAS_LLM_MODEL is not set"}
        elif not self._allowed():
            out = {"ok": False, "provider": "ollama", "model": self.model,
                   "detail": "the model host is not on this machine or its network "
                             "(ATLAS_LLM_ALLOW_REMOTE is not set)"}
        else:
            try:
                tags = self._call("GET", "/api/tags", timeout=min(5.0, self.timeout))
                names = {m.get("name") for m in tags.get("models") or []} | \
                    {m.get("model") for m in tags.get("models") or []}
                ok = self.model in names or (self.model + ":latest") in names
                out = {"ok": ok, "provider": "ollama", "model": self.model,
                       "detail": "ready" if ok else "the model {0} is not pulled "
                                                    "(ollama pull {0})".format(self.model)}
            except LLMError as error:
                out = {"ok": False, "provider": "ollama", "model": self.model,
                       "detail": str(error)}
        with self._lock:
            self._health = (time.time(), out)
        return out

    def generate(self, system, prompt, timeout=None, json_mode=False, max_tokens=700,
                 images=None):
        """The model's reply text. `json_mode` asks Ollama for a JSON object;
        `images` (base64 strings) go to a vision model with the prompt.
        Reasoning ("thinking") is switched off: an operator is waiting."""
        if not self.health()["ok"]:
            raise LLMError(self.health()["detail"])
        key = None if images else _cache_key(self.model, system, prompt, json_mode, max_tokens)
        hit = _cache_get(key)
        if hit is not None:
            return hit
        # gpt-oss takes a reasoning level, not on/off: the lowest, an operator waits.
        body = {"model": self.model, "stream": False,
                "think": "low" if str(self.model or "").startswith("gpt-oss") else False,
                "keep_alive": os.environ.get("ATLAS_LLM_KEEP_ALIVE") or "30m",
                # gpt-oss's reasoning counts against num_predict: room for it too.
                "options": {"temperature": 0.2, "num_predict": int(max_tokens) + (
                    768 if str(self.model or "").startswith("gpt-oss") else 0)},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}]}
        if images:
            body["messages"][1]["images"] = list(images)
            # A text-only main model cannot read a picture: the vision one does.
            body["model"] = (os.environ.get("ATLAS_LLM_VISION_MODEL") or "").strip() or self.model
        if json_mode:
            body["format"] = "json"
        threads = (os.environ.get("ATLAS_LLM_THREADS") or "").strip()
        if threads.isdigit() and int(threads) > 0:
            # Fewer threads than cores leaves room for the browser and the
            # automation; with every core taken, a CPU-only model stalls.
            body["options"]["num_thread"] = int(threads)
        last = None
        t0 = time.time()
        for attempt in range(self.retries + 1):
            try:
                started = time.perf_counter()
                answer = self._call("POST", "/api/chat", body, timeout)
                _trace_call(body["model"], started, answer)
                text = ((answer.get("message") or {}).get("content") or "").strip()
                if not text:
                    raise LLMError("the local model returned no text")
                if not images:
                    _report(True, body["model"], None)
                    _cache_put(key, text)
                    _ledger("local", "ollama", True, t0, system, prompt, text)
                return text
            except LLMError as error:
                last = error
                if attempt < self.retries:
                    time.sleep(0.5 * (2 ** attempt))
        if not images:
            _report(False, body["model"], str(last))
            _ledger("local", "ollama", False, t0, system, prompt, error=type(last).__name__)
        raise last


# Optional (ATLAS_LLM_CACHE_S=seconds; off by default): the same model given
# byte-identical input (system prompt, prompt — which carries the records it
# was shown — and settings) gives the stored text instead of a second
# CPU-bound call. Different records mean a different prompt, so a cached reply
# never stands in for newer data. Off by default because a model's output is
# not deterministic: reusing it hides a second, different (perhaps rejected)
# phrasing. ATLAS's own answers are deterministic and take milliseconds.
_cache, _cache_lock = {}, threading.Lock()


def _cache_ttl():
    try:
        return max(0.0, float(os.environ.get("ATLAS_LLM_CACHE_S") or 0))
    except ValueError:
        return 0.0


def _cache_key(model, system, prompt, json_mode, max_tokens):
    import hashlib
    raw = json.dumps([model, system, prompt, bool(json_mode), int(max_tokens)], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_get(key):
    if key is None or not _cache_ttl():
        return None
    with _cache_lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < _cache_ttl():
        try:
            from . import trace
            trace.model({"cached": True, "wall_ms": 0.0})
        except Exception:
            pass
        return hit[1]
    return None


def _cache_put(key, text):
    if key is None or not _cache_ttl():
        return
    with _cache_lock:
        if len(_cache) >= 256:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:64]:
                _cache.pop(k, None)
        _cache[key] = (time.time(), text)


def _trace_call(model, started, answer):
    """The call's timings for intelligence/trace: Ollama reports its load,
    prompt-processing and generation durations (nanoseconds). Time to first
    token is about load + prompt processing."""
    try:
        from . import trace
        ns = lambda k: (answer.get(k) or 0) / 1e6
        trace.model({"model": model, "wall_ms": (time.perf_counter() - started) * 1000,
                     "load_ms": ns("load_duration"), "prompt_ms": ns("prompt_eval_duration"),
                     "gen_ms": ns("eval_duration"), "prompt_tokens": answer.get("prompt_eval_count"),
                     "out_tokens": answer.get("eval_count")})
    except Exception:
        pass


# How each text answer went, for autoconfig's automatic model choice: it moves
# ATLAS back to the small model when the strong one keeps timing out.
WATCHERS = []


def _report(ok, model, error):
    for watch in list(WATCHERS):
        try:
            watch(ok, model, error)
        except Exception:
            pass


class ChainProvider(object):
    """Snowflake Cortex first (intelligence/cortex.py), the local model when it
    cannot answer: not set up, over today's budget, unreachable, or a picture."""
    name = "chain"

    def __init__(self, first, then):
        self.first, self.then = first, then
        self.answered_by = None          # the model behind the last answer (labels, reasons)

    def _first_ok(self):
        try:
            return bool(self.first.health().get("ok"))
        except Exception:
            return False

    @property
    def model(self):
        if self.answered_by:
            return self.answered_by
        return self.first.model if self._first_ok() else self.then.model

    def health(self):
        try:
            a = self.first.health()
        except Exception as error:
            a = {"ok": False, "provider": "cortex", "model": None, "detail": type(error).__name__}
        b = self.then.health()
        out = dict(a if a.get("ok") else b)
        out["chain"] = [a, b]
        out["ok"] = bool(a.get("ok") or b.get("ok"))
        return out

    def generate(self, system, prompt, timeout=None, json_mode=False, max_tokens=700, images=None):
        if not images and self._first_ok():
            started = time.time()
            try:
                text = self.first.generate(system, prompt, timeout, json_mode, max_tokens)
                self.answered_by = self.first.model
                _ledger(self.first.model, "cortex", True, started, system, prompt, text)
                return text
            except Exception as error:
                _ledger(self.first.model, "cortex", False, started, system, prompt, error=type(error).__name__)
                # the local model answers instead
        self.answered_by = self.then.model
        return self.then.generate(system, prompt, timeout, json_mode, max_tokens, images)


def _ledger(model, provider_name, ok, started, system, prompt, text=None, error=None):
    """The one model ledger (intelligence/missions/models.py) gets a row per chat call. Never raises."""
    try:
        from .missions import models as _mm
        _mm.record_chat(model, provider_name, ok, started, system, prompt, text, error)
    except Exception:
        pass


_provider = {"key": None, "obj": None}


def provider():
    """The configured provider (rebuilt when the settings change). With Snowflake
    Cortex set up, it answers first and the local one is the fallback."""
    key = (os.environ.get("ATLAS_LLM_PROVIDER") or "none").strip().lower(), \
        os.environ.get("ATLAS_LLM_URL"), os.environ.get("ATLAS_LLM_MODEL"), \
        (os.environ.get("ATLAS_CORTEX") or "").strip().lower()
    if _provider["key"] != key:
        _provider["key"] = key
        local = OllamaProvider() if key[0] == "ollama" else NullProvider()
        try:
            from . import cortex as _cortex
            _provider["obj"] = ChainProvider(_cortex.CortexProvider(), local) \
                if _cortex.settings() else local
        except Exception:
            _provider["obj"] = local
    return _provider["obj"]


SYSTEM = """You are ATLAS, the operations assistant in Mantrac's ATA Control Tower.
Rewrite the ANSWER below so it reads like a calm, direct, experienced colleague.
Rules — absolute:
- Use ONLY facts present in ANSWER, EVIDENCE and WEB. Do not add any number, date,
  reference, vessel, port, event, link or source that is not there.
- Do not claim anything succeeded, was verified, written, sent or confirmed unless
  ANSWER says so.
- Keep every uncertainty ("not established", "couldn't verify", "UNKNOWN") as uncertain.
- Information from WEB must be introduced as from public sources, never as a run fact.
- Never suggest bypassing CAPTCHA, carrier restrictions or security, or rotating IPs.
- Plain markdown: short paragraphs, "- " bullets when helpful, **bold** short headings.
  No tables. No URLs in the text."""


def compact(value):
    """The same facts in fewer tokens: no empty fields, no spaces in the JSON."""
    def strip(v):
        if isinstance(v, dict):
            return {k: strip(x) for k, x in v.items() if x not in (None, "", [], {})}
        if isinstance(v, (list, tuple)):
            return [strip(x) for x in v if x not in (None, "", [], {})]
        return v
    return json.dumps(strip(value), default=str, separators=(",", ":"), ensure_ascii=False)


def phrase(answer, evidence, web=None, timeout=None):
    """The model's phrasing of ATLAS's answer, or raises LLMError."""
    prompt = "ANSWER (authoritative):\n{0}\n\nEVIDENCE (run data):\n{1}\n\nWEB (public sources, " \
             "may be empty):\n{2}".format(answer[:6000], compact(evidence)[:5000],
                                          "\n".join("- {0}: {1}".format(w.get("publisher"),
                                                                        w.get("snippet") or "")
                                                    for w in (web or []))[:3000])
    return provider().generate(SYSTEM, prompt, timeout)
