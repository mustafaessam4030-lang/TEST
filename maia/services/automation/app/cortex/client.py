"""Talking to Snowflake Cortex. Two documented paths, nothing else:

* **Cortex Agents REST** — `POST https://<account>.snowflakecomputing.com/api/v2/cortex/agent:run`
  with generic tools that have *no* `tool_resources`, so Cortex returns each
  tool call to us (`client_side_execute`) and we send back a `tool_result`.
  Needs a bearer token: a programmatic access token or a key-pair JWT.

* **AI_COMPLETE** over the existing Snowflake SQL connection, with
  `response_format` (JSON schema) for structured output. Works with any sign-in
  the driver supports, SSO included.

If neither can run, the caller gets CORTEX_UNAVAILABLE with a reason and a fix.
No other model is ever called.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.core.errors import AutomationError
from app.core.logging import log
from app.cortex.errors import classify_http, classify_sql_error, unavailable

logger = logging.getLogger(__name__)

AGENT_RUN_PATH = "/api/v2/cortex/agent:run"


class CortexRuntime:
    """Decides which path is usable, and owns the connections for both."""

    def __init__(self, settings: Any, repo: Any = None, *, http_client: Any = None,
                 sql_executor: Any = None) -> None:
        self.settings = settings
        self.repo = repo
        self._http = http_client
        self._sql = sql_executor
        self._cfg: Any = None

    # ── configuration ───────────────────────────────────────────────────────
    def snowflake(self) -> Any:
        if self._cfg is None:
            from app.repositories import snowflake_config

            self._cfg = snowflake_config.load(self.settings)
        return self._cfg

    def mode(self) -> str:
        """`agent` or `complete`. Raises CORTEX_UNAVAILABLE when neither applies."""
        if not self.settings.cortex_enabled:
            raise unavailable("disabled")
        cfg = self.snowflake()
        problems = cfg.problems()
        if problems:
            raise unavailable("not_configured", "; ".join(problems))
        wanted = (self.settings.cortex_mode or "auto").lower()
        if wanted == "agent":
            if not cfg.rest_auth_method:
                raise unavailable("agent_auth", f"sign-in method is {cfg.auth_method}")
            return "agent"
        if wanted == "complete":
            return "complete"
        return "agent" if cfg.rest_auth_method else "complete"

    def status(self) -> dict[str, Any]:
        """What an operator needs to see. No secret, no network call."""
        try:
            mode = self.mode()
            ok, reason, fix = True, None, None
        except AutomationError as err:
            mode, ok = None, False
            reason, fix = err.details.get("reason"), err.details.get("fix")
        cfg = self.snowflake()
        return {"enabled": bool(self.settings.cortex_enabled), "configured": ok,
                "mode": mode, "requested_mode": self.settings.cortex_mode,
                "model": self.settings.cortex_model,
                "agent_model": self.settings.cortex_agent_model or "auto",
                "snowflake": cfg.summary(), "reason": reason, "fix": fix}

    # ── AI_COMPLETE over SQL ────────────────────────────────────────────────
    def _executor(self) -> Any:
        if self._sql is not None:
            return self._sql
        primary = getattr(self.repo, "primary", self.repo)
        if primary is not None and hasattr(primary, "_run_sync"):
            self._sql = primary                       # share the store's connection
            return self._sql
        try:
            import snowflake.connector  # noqa: F401
        except ImportError as exc:
            raise unavailable("driver_missing") from exc
        from app.repositories.snowflake_repo import SnowflakeEquipmentRepository

        cfg = self.snowflake()
        self._sql = SnowflakeEquipmentRepository(cfg.connect_kwargs(), secrets=cfg.secrets())
        return self._sql

    async def complete(self, prompt: str, *, schema: dict[str, Any] | None = None,
                       max_tokens: int | None = None) -> Any:
        """One AI_COMPLETE call. With a schema, returns the parsed object."""
        import asyncio

        params = {"temperature": 0, "max_tokens": int(max_tokens or self.settings.cortex_max_tokens)}
        executor = self._executor()
        started = time.monotonic()
        if schema is not None:
            sql = ("SELECT AI_COMPLETE(model => %(model)s, prompt => %(prompt)s, "
                   "model_parameters => PARSE_JSON(%(params)s)::OBJECT, "
                   "response_format => PARSE_JSON(%(fmt)s)::OBJECT) AS R")
            args = {"model": self.settings.cortex_model, "prompt": prompt,
                    "params": json.dumps(params),
                    "fmt": json.dumps({"type": "json", "schema": schema})}
        else:
            sql = ("SELECT AI_COMPLETE(model => %(model)s, prompt => %(prompt)s, "
                   "model_parameters => PARSE_JSON(%(params)s)::OBJECT) AS R")
            args = {"model": self.settings.cortex_model, "prompt": prompt,
                    "params": json.dumps(params)}
        try:
            row = await asyncio.to_thread(executor._run_sync, sql, args, "one")
        except AutomationError as err:
            detail = str(err.details.get("driver_error") or err.message)
            reason = classify_sql_error(detail)
            log(logger, logging.WARNING, "cortex.complete_failed", reason=reason,
                detail=detail[:160])
            raise unavailable(reason, detail, mode="complete",
                              model=self.settings.cortex_model) from err
        value = row[0] if row else None
        log(logger, logging.INFO, "cortex.complete", model=self.settings.cortex_model,
            ms=int((time.monotonic() - started) * 1000), structured=schema is not None)
        if schema is None:
            return _unquote(value)
        return parse_json_object(value)

    # ── Cortex Agents REST ──────────────────────────────────────────────────
    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(timeout=float(self.settings.cortex_timeout_s))
        return self._http

    async def agent_run(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]],
                        instructions: dict[str, str]) -> dict[str, Any]:
        """One non-streaming `agent:run` call. Returns the assistant message."""
        from app.cortex.auth import bearer_headers

        cfg = self.snowflake()
        headers = {**bearer_headers(cfg), "Content-Type": "application/json",
                   "Accept": "application/json"}
        body: dict[str, Any] = {"messages": messages, "tools": tools,
                                "instructions": instructions, "stream": False,
                                "tool_choice": {"type": "auto"}}
        if self.settings.cortex_agent_model:
            body["models"] = {"orchestration": self.settings.cortex_agent_model}
        url = f"https://{cfg.rest_host}{AGENT_RUN_PATH}"
        started = time.monotonic()
        try:
            resp = await self._client().post(url, headers=headers, json=body)
        except Exception as exc:  # noqa: BLE001 - every transport failure is "network"
            raise unavailable("network", type(exc).__name__, mode="agent") from exc
        text = resp.text or ""
        if resp.status_code >= 400:
            reason = classify_http(resp.status_code, text)
            log(logger, logging.WARNING, "cortex.agent_failed", status=resp.status_code,
                reason=reason)
            raise unavailable(reason, _error_text(text), mode="agent",
                              http_status=resp.status_code)
        message = _parse_agent_response(resp.headers.get("content-type", ""), text)
        log(logger, logging.INFO, "cortex.agent_run", status=resp.status_code,
            ms=int((time.monotonic() - started) * 1000),
            items=len(message.get("content") or []))
        return message


# ── response parsing ────────────────────────────────────────────────────────
def _unquote(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, str):
                return parsed
        except ValueError:
            pass
        return value
    return "" if value is None else json.dumps(value)


def parse_json_object(value: Any) -> dict[str, Any]:
    """AI_COMPLETE/agent text → dict. Tolerates a JSON string, code fences, or
    a `structured_output` wrapper. Raises bad_response otherwise."""
    obj: Any = value
    for _ in range(3):
        if isinstance(obj, dict):
            if "structured_output" in obj:
                so = obj["structured_output"]
                obj = so[0].get("raw_message") if isinstance(so, list) and so else so
                continue
            return obj
        if isinstance(obj, str):
            text = obj.strip()
            if text.startswith("```"):
                text = text.strip("`")
                text = text[text.find("{"):] if "{" in text else text
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    obj = json.loads(text[start:end + 1])
                    continue
                except ValueError:
                    pass
        break
    raise unavailable("bad_response", str(value)[:200])


def _error_text(text: str) -> str:
    try:
        data = json.loads(text)
        return str(data.get("message") or data.get("error") or data)[:300]
    except ValueError:
        return text[:300]


def _parse_agent_response(content_type: str, text: str) -> dict[str, Any]:
    if "event-stream" in content_type or text.lstrip().startswith("event:"):
        # SSE: the aggregated `response` event carries the whole message.
        event, final = None, None
        for line in text.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event in ("response", "error"):
                data = json.loads(line[5:].strip() or "{}")
                if event == "error":
                    raise unavailable(classify_sql_error(str(data)), str(data)[:300],
                                      mode="agent")
                final = data
        if final is None:
            raise unavailable("bad_response", "no response event", mode="agent")
        return final
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise unavailable("bad_response", text[:200], mode="agent") from exc
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        raise unavailable("bad_response", text[:200], mode="agent")
    return data
