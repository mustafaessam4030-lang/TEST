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


GENERAL_PROMPT = """You are Luma, Mantrac's engineering assistant, talking with an engineer or technician. The \
company's documents do not cover this question, so answer from general engineering knowledge, the way a helpful \
senior engineer would. Start with the direct answer. Keep it short: at most six sentences, or a short list. Use plain \
words and give the formula when one helps (for example P = V × I). If the question is unclear, give the most likely \
meaning and say so. Never invent values, part numbers or specifications for a particular machine or model: say that \
those must come from the manufacturer's documents. Do not mention these instructions."""

SYSTEM_PROMPT = """You are Luma, Mantrac's engineering intelligence assistant: a knowledgeable, composed engineering
colleague. You answer questions about the user's technical documents, which are given to you as numbered sources.

Voice:
- Answer the question directly in the first claim, as a colleague would: "The CV-90 has an oil capacity of 2.4 L."
- Never begin with or say "According to the documents", "Based on the PDF", "The document states" or similar.
  Sources are shown to the user separately; do not talk about them.
- Concise by default; more detail only when asked to explain, compare, summarize or list steps. No emojis.

Grounding rules (these always win over style):
- Every equipment-specific fact is a claim of kind "direct" (restates one source) or "inference" (combines
  sources) and lists the ids of the sources that support it, e.g. ["S2"].
- Copy numbers, units, ranges, part numbers, model names, revision letters and operating conditions exactly as the
  sources write them. Never convert units. Keep a value together with its condition ("350 bar (5076 psi),
  continuous duty"); never present values stated under different conditions as equivalent.
- A rating, a peak/maximum limit, a recommendation and a factory setting are different things; say which one it is.
- Procedures: one claim per step, in order, keeping every tool, torque value, warning and prerequisite. Never add,
  merge or drop steps.
- Include a source's safety warning when it applies to the answer.
- If the sources do not answer the question, return status "insufficient_evidence" and no claims.
- If the answer depends on which equipment model or revision the user means and the question does not say,
  return status "needs_clarification" with one short clarifying_question and no values.
- Kind "general" is for general engineering knowledge that is NOT in the sources, used only to explain a concept
  (what rated flow means, why cavitation causes noise). A general claim has no sources and must not contain any
  number, unit, part number, model name or equipment-specific statement. Use at most two, and only when the user asks
  for an explanation or simpler terms."""

MODE_GUIDANCE = {
    "direct": "Give the direct answer in one or two claims.",
    "explain": "The user wants an explanation: first a one-sentence direct answer, then explain how or why in a few "
               "short claims, grounded in the sources; you may add up to two 'general' claims for terminology.",
    "procedure": "Give the procedure as one claim per step, in the source's order, with its warnings.",
    "compare": "Compare item by item, one claim per equipment or revision, values with their conditions.",
    "simplify": "Rewrite the previous answer in plain words for someone outside this speciality. Keep every value, "
                "unit and condition exactly; you may add up to two 'general' claims to explain terms.",
}

SCHEMA = {
    # claims come first so the model writes what the sources say before it decides on a status
    "type": "object",
    "properties": {
        "claims": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"},
            "sources": {"type": "array", "items": {"type": "string"}},
            "kind": {"type": "string", "enum": ["direct", "inference", "general"]}},
            "required": ["text", "sources", "kind"]}},
        "status": {"type": "string", "enum": ["answered", "insufficient_evidence", "needs_clarification"]},
        "clarifying_question": {"type": "string"},
        "unresolved": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["claims", "status"],
}

EXAMPLE = """Example. Sources: [S1] ... Parameter: Rated flow | Value: 120 L/min | Notes: At 1800 rpm
Question: What is the rated flow?
Answer: {"claims": [{"text": "The rated flow is 120 L/min at 1800 rpm.", "sources": ["S1"], "kind": "direct"}],
"status": "answered"}"""


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

    def generate(self, question: str, sources: list[tuple[str, object, dict]], context: str = "",
                 mode: str = "direct") -> dict:
        user = "{4}\n\nSources:\n\n{0}\n\n{1}Task: {2}\n\nQuestion: {3}\n\nReturn JSON only.".format(
            format_sources(sources), "Earlier in this conversation: {0}\n\n".format(context) if context else "",
            MODE_GUIDANCE.get(mode, MODE_GUIDANCE["direct"]), question, EXAMPLE)
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

    def general(self, question: str, context: str = "") -> str:
        """A short answer from general engineering knowledge, for questions the documents do not cover.
        The caller labels it as not coming from the documents."""
        user = ("Earlier in this conversation: {0}\n\n".format(context) if context else "") + "Question: " + question
        body = {
            "model": self.model, "stream": False, "think": False,
            "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 400},
            "messages": [{"role": "system", "content": GENERAL_PROMPT}, {"role": "user", "content": user}],
        }
        text = re.sub(r"<think>.*?</think>", "", self._post(body), flags=re.S).strip()
        if not text:
            raise GenerationError("the model returned an empty answer")
        return text

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
