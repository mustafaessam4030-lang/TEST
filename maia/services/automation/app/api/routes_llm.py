"""The chat model, behind the gateway.

The browser used to be expected to call the model directly with a key in the
page. That is a key anyone can read. Here the gateway holds the key and the
browser sends only the conversation:

    browser ── {system, messages} ──▶ /v1/llm/messages ── key ──▶ Claude

The model is chosen here, not by the page. Nothing in this module logs a
message body or the key.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import ROOT
from app.core.logging import log

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/llm", tags=["llm"])

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_MESSAGES = 40
MAX_CHARS = 60_000
# Chat is latency-sensitive: start the visible answer rather than deliberating.
LATENCY_HINT = "\n\nLatency-sensitive; begin your visible answer immediately."


def resolve_key(settings: Any) -> str | None:
    """ANTHROPIC_API_KEY, else a local claude.txt (`key=sk-ant-…` or the bare key).

    The file is excluded from git and from the release package, like login.txt.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    name = getattr(settings, "llm_key_file", "claude.txt") or "claude.txt"
    candidates = [Path(name) if Path(name).is_absolute() else ROOT / name]
    # Windows hides extensions: claude.txt is often really claude.txt.txt.
    candidates += sorted(p for p in ROOT.glob("claude*.txt*")
                         if "example" not in p.name.lower())
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            value = line.partition("=")[2].strip() if "=" in line else line
            value = value.strip().strip('"').strip("'")
            if value.startswith("sk-ant-") and "PASTE" not in value.upper():
                return value
    return None


def _client(request: Request) -> Any:
    state = request.app.state
    client = getattr(state, "llm_client", None)
    if client is None:
        key = resolve_key(state.settings)
        if not key:
            return None
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=key, timeout=60.0, max_retries=1)
        state.llm_client = client
    return client


def _clean_messages(raw: Any) -> list[dict[str, Any]]:
    """Only user/assistant turns with text or image content, starting with the
    user, consecutive same-role turns merged. Anything else is dropped."""
    out: list[dict[str, Any]] = []
    for msg in (raw or [])[-MAX_MESSAGES:]:
        if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            blocks = [{"type": "text", "text": content[:MAX_CHARS]}] if content.strip() else []
        elif isinstance(content, list):
            blocks = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and str(b.get("text", "")).strip():
                    blocks.append({"type": "text", "text": str(b["text"])[:MAX_CHARS]})
                elif b.get("type") == "image" and msg["role"] == "user":
                    blocks.append({"type": "image", "source": b.get("source")})
        else:
            blocks = []
        if not blocks:
            continue
        if out and out[-1]["role"] == msg["role"]:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": msg["role"], "content": blocks})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    try:
        import anthropic  # noqa: F401
        installed = True
    except ImportError:
        installed = False
    return {"configured": bool(resolve_key(settings)) and installed,
            "sdk_installed": installed, "model": settings.llm_model}


@router.post("/messages")
async def messages(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    try:
        client = _client(request)
    except ImportError:
        return JSONResponse({"error": "sdk_missing",
                             "message": "pip install anthropic"}, status_code=503)
    if client is None:
        # 401 tells the page to stop asking and use its local engine.
        return JSONResponse({"error": "no_api_key",
                             "message": "No Claude API key: set ANTHROPIC_API_KEY or "
                                        "create claude.txt next to the project."},
                            status_code=401)

    msgs = _clean_messages(body.get("messages"))
    if not msgs:
        return JSONResponse({"error": "no_messages"}, status_code=400)
    system = str(body.get("system") or "")[:MAX_CHARS] + LATENCY_HINT
    max_tokens = max(256, min(int(body.get("max_tokens") or settings.llm_max_tokens),
                              settings.llm_max_tokens))

    import anthropic

    started = time.monotonic()
    try:
        resp = await client.beta.messages.create(
            model=settings.llm_model, max_tokens=max_tokens, system=system,
            messages=msgs, output_config={"effort": settings.llm_effort},
            # A declined turn is re-run on Anthropic's recommended fallback
            # model inside the same call, instead of reaching the customer as
            # an empty answer.
            betas=[FALLBACK_BETA], fallbacks="default")
    except anthropic.AuthenticationError:
        return JSONResponse({"error": "key_rejected"}, status_code=401)
    except anthropic.PermissionDeniedError:
        return JSONResponse({"error": "not_permitted"}, status_code=403)
    except anthropic.RateLimitError:
        return JSONResponse({"error": "rate_limited"}, status_code=429)
    except anthropic.APIConnectionError:
        return JSONResponse({"error": "model_unreachable"}, status_code=502)
    except anthropic.APIStatusError as exc:
        log(logger, logging.WARNING, "llm.error", status=exc.status_code)
        return JSONResponse({"error": "model_error", "status": exc.status_code},
                            status_code=502)

    text_blocks = ([{"type": "text", "text": b.text} for b in resp.content
                    if getattr(b, "type", None) == "text"]
                   if resp.stop_reason != "refusal" else [])
    usage = {"input_tokens": resp.usage.input_tokens,
             "output_tokens": resp.usage.output_tokens}
    log(logger, logging.INFO, "llm.reply", model=resp.model, stop=resp.stop_reason,
        ms=int((time.monotonic() - started) * 1000),
        tokens_in=usage["input_tokens"], tokens_out=usage["output_tokens"])
    return JSONResponse({"content": text_blocks, "usage": usage, "model": resp.model,
                         "stop_reason": resp.stop_reason})
