"""Minimal Ollama client (standard library only): chat completions, JSON mode and images."""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request

THINK = re.compile(r"<think>.*?</think>", re.S)          # reasoning models (qwen3, deepseek-r1) print their thoughts


class OllamaError(RuntimeError):
    pass


class Ollama:
    def __init__(self, host: str = "http://localhost:11434", num_ctx: int = 16384, timeout_s: int = 600):
        self.host = host.rstrip("/")
        self.num_ctx = num_ctx
        self.timeout_s = timeout_s

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.host + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise OllamaError(f"Ollama {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
        except urllib.error.URLError as exc:
            raise OllamaError(f"Ollama is not reachable at {self.host} ({exc.reason}). Is the Ollama app running?") from exc

    def models(self) -> list[str]:
        req = urllib.request.Request(self.host + "/api/tags")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return [m.get("name", "") for m in json.loads(r.read().decode("utf-8")).get("models", [])]
        except (urllib.error.URLError, OSError) as exc:
            raise OllamaError(f"Ollama is not reachable at {self.host}. Start the Ollama app first.") from exc

    def chat(self, model: str, messages: list[dict], max_tokens: int = 1500, json_mode: bool = False,
             images: list[bytes] | None = None) -> str:
        msgs = [dict(m) for m in messages]
        if images:
            msgs[-1]["images"] = [base64.b64encode(b).decode("ascii") for b in images]
        body = {"model": model, "messages": msgs, "stream": False,
                "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": self.num_ctx}}
        if json_mode:
            body["format"] = "json"
        data = self._post("/api/chat", body)
        text = (data.get("message") or {}).get("content") or ""
        return THINK.sub("", text).strip()


def wants_json(messages: list[dict]) -> bool:
    """The planner and the SQL repair step expect one JSON object; JSON mode keeps local models on format."""
    first, last = messages[0].get("content", ""), messages[-1].get("content", "")
    return "ONE JSON object" in first or "Reply with JSON only" in last
