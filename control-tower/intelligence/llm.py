"""
ATLAS's optional language model — LOCAL only, no paid API.

The deterministic ATLAS (dashboard/assistant.py and intelligence/*) is the
authority for every run fact, every success or failure, every action. A model
here only re-phrases an answer ATLAS already built, from the evidence ATLAS
hands it — and intelligence/factguard rejects any phrasing that adds a
number, reference, link or success claim the inputs do not hold. When the
model is not configured, not running, slow or wrong, ATLAS answers exactly as
it does without one.

    ATLAS_LLM_PROVIDER      none (default) | ollama
    ATLAS_LLM_URL           default http://127.0.0.1:11434 (Ollama)
    ATLAS_LLM_MODEL         e.g. qwen2.5:7b-instruct  (no default: must be named)
    ATLAS_LLM_TIMEOUT_S     default 20
    ATLAS_LLM_RETRIES       default 1 (with backoff)
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

    def generate(self, system, prompt, timeout=None):
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
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
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

    def generate(self, system, prompt, timeout=None):
        if not self.health()["ok"]:
            raise LLMError(self.health()["detail"])
        body = {"model": self.model, "stream": False,
                "options": {"temperature": 0.2, "num_predict": 700},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt}]}
        last = None
        for attempt in range(self.retries + 1):
            try:
                answer = self._call("POST", "/api/chat", body, timeout)
                text = ((answer.get("message") or {}).get("content") or "").strip()
                if not text:
                    raise LLMError("the local model returned no text")
                return text
            except LLMError as error:
                last = error
                if attempt < self.retries:
                    time.sleep(0.5 * (2 ** attempt))
        raise last


_provider = {"key": None, "obj": None}


def provider():
    """The configured provider (rebuilt when the settings change)."""
    key = (os.environ.get("ATLAS_LLM_PROVIDER") or "none").strip().lower(), \
        os.environ.get("ATLAS_LLM_URL"), os.environ.get("ATLAS_LLM_MODEL")
    if _provider["key"] != key:
        _provider["key"] = key
        _provider["obj"] = OllamaProvider() if key[0] == "ollama" else NullProvider()
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


def phrase(answer, evidence, web=None, timeout=None):
    """The model's phrasing of ATLAS's answer, or raises LLMError."""
    prompt = "ANSWER (authoritative):\n{0}\n\nEVIDENCE (run data):\n{1}\n\nWEB (public sources, " \
             "may be empty):\n{2}".format(answer[:6000], json.dumps(evidence, default=str)[:5000],
                                          "\n".join("- {0}: {1}".format(w.get("publisher"),
                                                                        w.get("snippet") or "")
                                                    for w in (web or []))[:3000])
    return provider().generate(SYSTEM, prompt, timeout)
