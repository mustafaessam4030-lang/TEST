"""Browser pool + session vault.

The browser *process* is pooled (expensive); a BrowserContext is created and
destroyed per run (isolation). Authenticated `storage_state` is persisted per
account slot so we log in rarely — session reuse is what makes volume viable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.errors import AutomationError, ErrorCode
from app.core.logging import log

logger = logging.getLogger(__name__)


@dataclass
class RunContext:
    """Everything a step needs. Handed to the adapter; never to the model."""

    run_id: str
    page: Any
    context: Any
    source_id: str
    artifact_dir: Path
    step_timeout_ms: int
    captured_xhr: list[dict[str, Any]]


class SessionVault:
    """Encrypted-at-rest storage_state per (source, slot). Keys live in the KMS."""

    def __init__(self, directory: str) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, source_id: str, slot: str) -> Path:
        return self.dir / f"{source_id}__{slot}.json"

    def load(self, source_id: str, slot: str, max_age_s: int = 3600 * 8) -> dict[str, Any] | None:
        path = self._path(source_id, slot)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > max_age_s:
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, source_id: str, slot: str, state: dict[str, Any]) -> None:
        path = self._path(source_id, slot)
        path.write_text(json.dumps(state))
        os.chmod(path, 0o600)

    def invalidate(self, source_id: str, slot: str) -> None:
        self._path(source_id, slot).unlink(missing_ok=True)


class BrowserPool:
    def __init__(self, *, headless: bool = True, max_contexts: int = 4,
                 artifact_dir: str = "/tmp/maya-artifacts",
                 session_dir: str = "/tmp/maya-sessions") -> None:
        self.headless = headless
        self.semaphore = asyncio.Semaphore(max_contexts)
        self.artifact_dir = Path(artifact_dir)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.vault = SessionVault(session_dir)
        self._playwright: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        async with self._lock:
            if self._browser is not None:
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-dev-shm-usage", "--disable-gpu", "--no-first-run"],
            )
            log(logger, logging.INFO, "browser.launched", headless=self.headless)

    async def stop(self) -> None:
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None

    async def acquire(self, *, run_id: str, source_id: str, slot: str,
                      step_timeout_ms: int, use_saved_session: bool = True,
                      user_agent: str | None = None) -> RunContext:
        if self._browser is None:
            await self.start()
        await self.semaphore.acquire()
        try:
            state = self.vault.load(source_id, slot) if use_saved_session else None
            context = await self._browser.new_context(
                storage_state=state,
                viewport={"width": 1536, "height": 960},
                user_agent=user_agent,
                accept_downloads=False,
            )
            context.set_default_timeout(step_timeout_ms)
            page = await context.new_page()

            captured: list[dict[str, Any]] = []

            async def _on_response(response: Any) -> None:
                # SIS2 is an Angular SPA: its own JSON is better data than scraped DOM text.
                try:
                    ctype = (response.headers or {}).get("content-type", "")
                    if "application/json" in ctype and response.request.resource_type == "xhr":
                        captured.append({"url": response.url, "status": response.status,
                                         "body": await response.json()})
                except Exception:  # a body we cannot read is simply not captured
                    pass

            page.on("response", lambda r: asyncio.create_task(_on_response(r)))

            artifact_dir = self.artifact_dir / run_id
            artifact_dir.mkdir(parents=True, exist_ok=True)
            return RunContext(run_id=run_id, page=page, context=context, source_id=source_id,
                              artifact_dir=artifact_dir, step_timeout_ms=step_timeout_ms,
                              captured_xhr=captured)
        except Exception as exc:
            self.semaphore.release()
            raise AutomationError(ErrorCode.INTERNAL_ERROR, "Could not acquire a browser context.",
                                  details={"error": str(exc)[:200]}) from exc

    async def release(self, ctx: RunContext, *, persist_session_slot: str | None = None) -> None:
        try:
            if persist_session_slot:
                state = await ctx.context.storage_state()
                self.vault.save(ctx.source_id, persist_session_slot, state)
        except Exception as exc:  # pragma: no cover - best effort
            log(logger, logging.WARNING, "session.persist_failed", error=str(exc)[:200])
        finally:
            try:
                await ctx.context.close()
            finally:
                self.semaphore.release()

    async def capture_artifacts(self, ctx: RunContext, step: str) -> dict[str, str]:
        """Screenshot + trimmed HTML for forensics. Never logged inline."""
        out: dict[str, str] = {}
        try:
            shot = ctx.artifact_dir / f"{step}.png"
            await ctx.page.screenshot(path=str(shot), full_page=False)
            out["screenshot"] = str(shot)
            html = ctx.artifact_dir / f"{step}.html"
            html.write_text((await ctx.page.content())[:500_000])
            out["html"] = str(html)
            out["url"] = ctx.page.url
        except Exception as exc:  # pragma: no cover
            log(logger, logging.WARNING, "artifact.capture_failed", step=step, error=str(exc)[:200])
        return out
