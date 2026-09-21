"""Maia's orchestration loop (server-side reference implementation).

A manual tool loop rather than the SDK tool runner: every tool call is logged,
policy-checked and bounded before it executes, which is what an audited
enterprise flow needs. The model's only reach into the world is these five tools.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import anthropic

from agent.tools import MaiaToolDispatcher, load_tools

logger = logging.getLogger(__name__)

MODEL = os.environ.get("MAIA_AGENT_MODEL", "claude-opus-5")
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "system_prompt.md"
MAX_TOOL_CALLS_PER_TURN = 6      # a hard stop: no tool storm, no runaway browsing
MAX_SIS_CALLS_PER_TURN = 2


class MaiaAgent:
    def __init__(self, *, api_base: str | None = None, actor: str = "maia-agent") -> None:
        self.client = anthropic.AsyncAnthropic()
        self.dispatcher = MaiaToolDispatcher(
            api_base=api_base or os.environ.get("MAIA_API_BASE", "http://localhost:8080"),
            actor=actor)
        self.tools = load_tools()
        self.system = [{
            "type": "text",
            "text": SYSTEM_PROMPT_PATH.read_text(),
            # Stable prefix: system + tools are identical every turn, so they cache.
            "cache_control": {"type": "ephemeral"},
        }]

    async def turn(self, user_text: str, history: list[dict[str, Any]] | None = None
                   ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [*(history or []),
                                          {"role": "user", "content": user_text}]
        audit: list[dict[str, Any]] = []
        tool_calls = sis_calls = 0

        while True:
            response = await self.client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=self.system,
                tools=self.tools,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
                messages=messages,
            )

            if response.stop_reason == "refusal":
                return {"text": "I can't help with that request.", "audit": audit,
                        "stop_reason": "refusal"}
            if response.stop_reason != "tool_use":
                text = "".join(b.text for b in response.content if b.type == "text")
                return {"text": text, "audit": audit, "stop_reason": response.stop_reason}

            messages.append({"role": "assistant", "content": response.content})
            blocks = [b for b in response.content if b.type == "tool_use"]
            results: list[dict[str, Any]] = []

            for block in blocks:
                tool_calls += 1
                if block.name == "search_equipment_in_sis":
                    sis_calls += 1
                # Policy gates live here, in code — not in the model's judgement.
                if tool_calls > MAX_TOOL_CALLS_PER_TURN or sis_calls > MAX_SIS_CALLS_PER_TURN:
                    payload: dict[str, Any] = {
                        "ok": False, "error_code": "RATE_LIMITED",
                        "user_message_hint": "Tool-call budget for this turn is exhausted."}
                else:
                    started = time.monotonic()
                    payload = await self.dispatcher.dispatch(block.name, dict(block.input))
                    audit.append({
                        "tool": block.name,
                        "args": dict(block.input),
                        "ok": payload.get("ok"),
                        "error_code": payload.get("error_code"),
                        "automation_run_id": (payload.get("attribution") or {}).get(
                            "automation_run_id") or payload.get("automation_run_id"),
                        "source": (payload.get("attribution") or {}).get("source"),
                        "latency_ms": int((time.monotonic() - started) * 1000),
                    })
                    logger.info("maia.tool", extra={"extra_fields": audit[-1]})

                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(payload, ensure_ascii=False, default=str),
                    "is_error": not payload.get("ok", False),
                })

            # All results go back in ONE user message, or parallel tool use degrades.
            messages.append({"role": "user", "content": results})


async def _demo() -> None:  # pragma: no cover - manual smoke test
    logging.basicConfig(level=logging.INFO)
    agent = MaiaAgent()
    out = await agent.turn("مــايا، دوريلي على الداتا بتاعة المعدة Serial Number SN123456")
    print(out["text"])
    print(json.dumps(out["audit"], indent=2, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
