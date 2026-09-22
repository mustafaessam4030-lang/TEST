"""Learn a source's page contract on demand, by running the capture tool.

This is what lets the operator never run a script: when a source has no verified
selectors, the gateway runs the automatic capture itself, proves each selector by
using it, reloads the contract and continues the original search.

It is still the same capture tool with the same rules — nothing is guessed, MFA
is never bypassed, and anything unprovable comes back TODO_CAPTURE.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.adapters import selector_store
from app.core.errors import AutomationError, ErrorCode
from app.core.logging import log

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
CAPTURE_TOOL = REPO_ROOT / "scripts" / "capture" / "capture_selectors.py"
WRITTEN_RE = re.compile(r"^SELECTORS_WRITTEN=(.+)$", re.M)

# The capture tool reports these as its own exit codes.
EXIT_OK, EXIT_INCOMPLETE, EXIT_UNRESOLVED, EXIT_AUTH, EXIT_NAV = 0, 1, 2, 3, 6


class CaptureService:
    """Runs the capture tool out-of-process, so a browser crash cannot take the
    gateway with it, and reloads the resulting contract into the live registry."""

    def __init__(self, *, registry: Any, settings: Any) -> None:
        self.registry = registry
        self.settings = settings
        self._locks: dict[str, asyncio.Lock] = {}
        self._attempted: set[str] = set()
        #: what a capture proved, held until a real lookup vindicates it
        self._learned: dict[str, dict[str, Any]] = {}

    def contract_is_usable(self, source_id: str) -> bool:
        config = self.registry.entry(source_id).config
        flat = selector_store.flatten_config(config)
        return all(flat.get(name) and flat[name] != selector_store.TODO
                   for name in selector_store.REQUIRED_SELECTORS)

    def contract_path(self) -> Path:
        return Path(os.environ.get(
            "MAIA_SIS_SELECTORS",
            str(Path(self.settings.sources_dir).parent / "sis_selectors.json")))

    def promote(self, source_id: str) -> str | None:
        """Write the learned contract to disk, now that a run has used it.

        Called only after a lookup came back with a record, so what lands in
        `config/sis_selectors.json` is not "the capture believed this" — it is
        "this drove a real search and returned a real record". Every later start
        reads it and skips the learning step entirely.

        Nothing is written for a source whose base URL is not the real site, and
        nothing is written unless every required selector is present.
        """
        payload = self._learned.get(source_id)
        if not payload:
            return None
        config = self.registry.entry(source_id).config
        host = urlparse(str(config.get("base_url", ""))).hostname or ""
        if not host.endswith("cat.com"):
            log(logger, logging.WARNING, "capture.promote_refused",
                source=source_id, reason="base url is not Caterpillar SIS", host=host)
            self._learned.pop(source_id, None)
            return None
        missing = selector_store.missing_required(payload)
        if missing:
            return None

        target = self.contract_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            body = dict(payload)
            body["promoted_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            body["promoted_because"] = (
                "a live lookup using these selectors returned a record")
            target.write_text(json.dumps(body, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        except OSError as exc:
            # The contract is a convenience; the run already succeeded without it.
            log(logger, logging.WARNING, "capture.promote_failed",
                source=source_id, error=str(exc)[:160])
            return None
        self._learned.pop(source_id, None)
        log(logger, logging.INFO, "capture.promoted", source=source_id,
            contract=str(target), version=payload.get("selector_version"))
        return str(target)

    def already_attempted(self, source_id: str) -> bool:
        return source_id in self._attempted

    async def learn(self, source_id: str, *, good_serial: str,
                    missing_serial: str = "ZZZ00000", timeout_s: int = 600) -> dict[str, Any]:
        """Run one automatic capture for this source and load what it proved."""
        lock = self._locks.setdefault(source_id, asyncio.Lock())
        async with lock:
            if self.contract_is_usable(source_id):
                return {"status": "already_known", "source": source_id}
            self._attempted.add(source_id)

            entry = self.registry.entry(source_id)
            argv = [sys.executable, str(CAPTURE_TOOL), "--auto",
                    "--base", entry.config.get("base_url", ""),
                    "--serial", good_serial, "--missing-serial", missing_serial]
            if self.settings.headless:
                # Nobody can satisfy MFA in a hidden window; the tool stops instead.
                argv.append("--headless")
            if self.settings.chromium_path:
                argv += ["--chromium", self.settings.chromium_path]

            log(logger, logging.INFO, "capture.started", source=source_id,
                serial=good_serial, headless=self.settings.headless)

            env = {**os.environ, "PYTHONUNBUFFERED": "1"}
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(REPO_ROOT), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                raise AutomationError(
                    ErrorCode.TIMEOUT,
                    "Learning the source's page layout took too long.",
                    details={"source": source_id}) from None

            output = (stdout or b"").decode("utf-8", "replace")
            written = WRITTEN_RE.search(output)
            tail = "\n".join(output.strip().splitlines()[-12:])
            log(logger, logging.INFO, "capture.finished", source=source_id,
                exit_code=proc.returncode, wrote=written.group(1) if written else None)

            if proc.returncode == EXIT_AUTH:
                raise AutomationError(
                    ErrorCode.LOGIN_FAILED,
                    "Could not sign in to the source while learning its layout.",
                    details={"source": source_id, "capture_output": tail})
            if proc.returncode == EXIT_NAV:
                raise AutomationError(
                    ErrorCode.NETWORK_ERROR,
                    "Could not reach the source while learning its layout.",
                    details={"source": source_id, "capture_output": tail})

            if not written:
                raise AutomationError(
                    ErrorCode.WEBSITE_CHANGED,
                    "The source's layout could not be learned.",
                    details={"source": source_id, "capture_output": tail})

            # Windows line endings put a CR inside the captured group; a path
            # ending in \r exists nowhere.
            written_path = Path(written.group(1).strip())
            payload = selector_store.load(written_path)
            missing = selector_store.missing_required(payload)
            if missing:
                raise AutomationError(
                    ErrorCode.WEBSITE_CHANGED,
                    "The source's layout was only partly learned; nothing was guessed.",
                    details={"source": source_id, "unresolved": missing,
                             "report": str(written_path.parent),
                             "capture_output": tail})

            merged = selector_store.merge_into_config(entry.config, payload)
            self.registry.update_config(source_id, merged)
            # Held, not yet written to the contract file: a capture that proves
            # its own selectors is good evidence, a lookup that comes back with
            # a record is better. `promote` writes it once that happens.
            self._learned[source_id] = payload
            verified = selector_store.verified_selectors(payload)
            log(logger, logging.INFO, "capture.applied", source=source_id,
                verified=len(verified), version=payload.get("selector_version"))
            return {"status": "learned", "source": source_id,
                    "verified_selectors": len(verified),
                    "selector_version": payload.get("selector_version"),
                    "written_to": str(written_path)}
