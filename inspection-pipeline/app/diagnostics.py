"""Failure diagnostics for browser automation.

On a Playwright failure we save a screenshot plus a small JSON file (URL,
title, error, timestamp, run_id) under ``DIAGNOSTICS_DIR/<run_id>/``.
Password fields are cleared before the screenshot and URL query values are
masked, so no credentials end up on disk. Capture never raises: a diagnostics
failure must not hide the original error.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.logger import redact

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

_CLEAR_PASSWORDS_JS = "() => document.querySelectorAll('input[type=password]').forEach(e => { e.value = ''; })"


def mask_url(url: str) -> str:
    """Keep scheme/host/path and parameter names; drop parameter values and fragments."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    query = urlencode([(k, "***") for k, _ in parse_qsl(parts.query, keep_blank_values=True)])
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, query, ""))


class Diagnostics:
    def __init__(self, directory: Path, run_id: str) -> None:
        self.directory = directory / run_id
        self.run_id = run_id

    async def capture(self, page: Page | None, stage: str, error: BaseException) -> Path | None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            base = self.directory / f"{stamp}_{re.sub(r'[^A-Za-z0-9_-]', '_', stage)}"
            info = {
                "run_id": self.run_id,
                "stage": stage,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error_type": type(error).__name__,
                "error": redact(str(error))[:4000],
                "url": None,
                "title": None,
                "screenshot": None,
            }
            if page is not None and not page.is_closed():
                info["url"] = mask_url(page.url)
                try:
                    info["title"] = await page.title()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await page.evaluate(_CLEAR_PASSWORDS_JS)
                    screenshot = base.with_suffix(".png")
                    await page.screenshot(path=str(screenshot), full_page=True, timeout=10_000)
                    info["screenshot"] = screenshot.name
                except Exception as exc:  # noqa: BLE001
                    info["screenshot_error"] = type(exc).__name__
            base.with_suffix(".json").write_text(json.dumps(info, indent=2), encoding="utf-8")
            logger.error("Diagnostics captured for stage %r at %s", stage, base.with_suffix(".json"))
            return base.with_suffix(".json")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not capture diagnostics: %s", type(exc).__name__)
            return None
