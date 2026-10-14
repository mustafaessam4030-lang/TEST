"""Optional LLM generation through a local Ollama server.

The model only sees the retrieved passages, numbered S1..Sn, and must return
JSON claims that each name the passages supporting them. Its output is never
trusted directly: answer.py drops any claim that cites an unknown passage or
states a number/identifier its cited passages do not contain, and falls back
to the extractive answer when nothing valid remains or the model fails.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class GenerationError(RuntimeError):
    pass


SYSTEM_PROMPT = """You are an engineering knowledge assistant. Answer ONLY from the numbered sources.
Rules:
- Every factual claim must list the source ids (e.g. "S2") that directly support it.
- Copy numbers, units, part numbers and model names exactly as the sources write them. Never convert units yourself.
- Never use outside knowledge for specifications, values, part numbers, procedures or warnings.
- If the sources do not answer the question, return status "insufficient_evidence" and no claims.
- If sources disagree, state each value as a separate claim with its own source.
- If the answer depends on which equipment model or document revision the user means and the question does not say, return status "needs_clarification" and a clarifying_question.
- Mark a claim "inference" only when it combines sources rather than restating one; keep inferences rare.
- Keep claims short: one fact per claim. Include safety warnings from the sources when they apply."""

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "insufficient_evidence", "needs_clarification"]},
        "claims": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string"}},
            "kind": {"type": "string", "enum": ["direct", "inference"]}},
            "required": ["text", "sources", "kind"]}},
        "clarifying_question": {"type": "string"},
        "unresolved": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "claims"],
}


def format_sources(sources: list[tuple[str, object, dict]]) -> str:
    blocks = []
    for sid, chunk, doc in sources:
        rev = " Rev {0}".format(doc["revision"]) if doc.get("revision") else ""
        old = " (SUPERSEDED by a newer revision)" if doc.get("superseded_by") else ""
        blocks.append("[{0}] Document: {1}{2}{3} | page {4} | section: {5}\n{6}".format(
            sid, doc.get("title") or doc.get("filename"), rev, old, chunk.page_no, chunk.section or "-", chunk.text))
    return "\n\n".join(blocks)


class OllamaGenerator:
    def __init__(self, url: str, model: str, timeout_s: float = 120.0, retries: int = 1, num_ctx: int = 8192):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.retries = retries
        self.num_ctx = num_ctx
        self.name = "ollama:" + model

    def health(self, timeout: float = 3.0) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=timeout) as response:
                names = [m.get("name", "") for m in json.loads(response.read()).get("models", [])]
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            return False, "Ollama is not reachable at {0}: {1}".format(self.url, exc)
        wanted = self.model if ":" in self.model else self.model + ":latest"
        if wanted not in names:
            return False, "model {0} is not pulled (available: {1})".format(self.model, ", ".join(names) or "none")
        return True, "ok"

    def generate(self, question: str, sources: list[tuple[str, object, dict]], context: str = "") -> dict:
        user = "Sources:\n\n{0}\n\n{1}Question: {2}\n\nReturn JSON only.".format(
            format_sources(sources), "Earlier in this conversation: {0}\n\n".format(context) if context else "", question)
        body = {
            "model": self.model, "stream": False, "format": SCHEMA, "think": False,
            "options": {"temperature": 0, "num_ctx": self.num_ctx},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        }
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._parse(self._post(body))
            except GenerationError as exc:
                last_error = exc
                log.warning("generation attempt %d failed: %s", attempt + 1, exc)
                if attempt < self.retries:
                    time.sleep(min(2.0 * (attempt + 1), 5.0))
        raise GenerationError(str(last_error))

    def _post(self, body: dict) -> str:
        request = urllib.request.Request(self.url + "/api/chat", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read())
        except TimeoutError as exc:
            raise GenerationError("the model did not answer within {0:.0f}s".format(self.timeout_s)) from exc
        except urllib.error.HTTPError as exc:
            raise GenerationError("Ollama returned HTTP {0}".format(exc.code)) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise GenerationError("Ollama call failed: {0}".format(exc)) from exc
        content = ((payload.get("message") or {}).get("content") or "").strip()
        if not content:
            raise GenerationError("the model returned an empty response")
        return content

    @staticmethod
    def _parse(content: str) -> dict:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise GenerationError("the model response was not JSON")
        try:
            data = json.loads(match.group(0))
        except ValueError as exc:
            raise GenerationError("the model returned malformed JSON") from exc
        if not isinstance(data, dict) or data.get("status") not in SCHEMA["properties"]["status"]["enum"]:
            raise GenerationError("the model response is missing a valid status")
        claims = data.get("claims")
        if not isinstance(claims, list):
            raise GenerationError("the model response has no claims list")
        data["claims"] = [c for c in claims if isinstance(c, dict) and str(c.get("text", "")).strip()]
        return data
