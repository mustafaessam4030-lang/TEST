"""Selector health checks — the pre-flight gate for every real automation run.

One implementation, two callers: the capture tool runs it to verify what it just
captured, and the adapter runs it before every live run. If a required element
is not where the contract says it is, the answer is WEBSITE_CHANGED. There is no
fallback clicking, no "try another selector", no guessing — a changed page is an
engineering event, not something to improvise around.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.errors import AutomationError, ErrorCode

Requirement = Literal["reachable", "attached", "visible"]


@dataclass
class CheckResult:
    name: str
    ok: bool
    requirement: Requirement
    selector: str | None = None
    detail: str = ""
    elapsed_ms: int = 0
    match_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "requirement": self.requirement,
                "selector": self.selector, "detail": self.detail,
                "elapsed_ms": self.elapsed_ms, "match_count": self.match_count}


@dataclass
class HealthReport:
    ok: bool = True
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> CheckResult:
        self.checks.append(result)
        if not result.ok:
            self.ok = False
        return result

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.ok]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [c.as_dict() for c in self.checks],
                "failed": [c.name for c in self.failed]}

    def raise_if_failed(self, *, source: str = "cat_sis") -> None:
        if self.ok:
            return
        first = self.failed[0]
        raise AutomationError(
            ErrorCode.WEBSITE_CHANGED,
            f"Pre-flight failed: {first.name} ({first.detail}).",
            details={"source": source, "failed_checks": [c.as_dict() for c in self.failed]},
            step="HEALTH_CHECK",
        )


async def _check(page: Any, name: str, selector: str | None, requirement: Requirement,
                 timeout_ms: int) -> CheckResult:
    started = time.monotonic()
    if not selector or selector == "TODO_CAPTURE":
        return CheckResult(name, False, requirement, selector,
                           "selector is not captured (TODO_CAPTURE)")
    try:
        locator = page.locator(selector)
        if requirement == "visible":
            await locator.first.wait_for(state="visible", timeout=timeout_ms)
        else:
            await locator.first.wait_for(state="attached", timeout=timeout_ms)
        count = await locator.count()
        elapsed = int((time.monotonic() - started) * 1000)
        if count == 0:
            return CheckResult(name, False, requirement, selector, "no match", elapsed, count)
        # More than one match is not fatal for a container, but it is worth recording:
        # an ambiguous selector is how the wrong row gets read later.
        return CheckResult(name, True, requirement, selector,
                           "ok" if count == 1 else f"matched {count} elements (ambiguous)",
                           elapsed, count)
    except Exception as exc:
        return CheckResult(name, False, requirement, selector, str(exc)[:160],
                           int((time.monotonic() - started) * 1000))


# ── The four named verifications required after a capture ───────────────────
async def verify_serial_search_input(page: Any, selectors: dict[str, str],
                                     timeout_ms: int = 8000) -> CheckResult:
    return await _check(page, "verify_serial_search_input", selectors.get("search.input"),
                        "visible", timeout_ms)


async def verify_search_button(page: Any, selectors: dict[str, str],
                               timeout_ms: int = 8000) -> CheckResult:
    return await _check(page, "verify_search_button", selectors.get("search.submit"),
                        "visible", timeout_ms)


async def verify_result_container(page: Any, selectors: dict[str, str],
                                  timeout_ms: int = 8000) -> CheckResult:
    # After a successful search the container must be visible.
    return await _check(page, "verify_result_container", selectors.get("search.results"),
                        "visible", timeout_ms)


async def verify_no_result_state(page: Any, selectors: dict[str, str],
                                 timeout_ms: int = 8000) -> CheckResult:
    # The empty-state marker is the ONLY permissible proof of SERIAL_NOT_FOUND,
    # which is why it is verified as its own check.
    return await _check(page, "verify_no_result_state", selectors.get("search.no_results_marker"),
                        "visible", timeout_ms)


async def run_post_capture_verification(page: Any, selectors: dict[str, str],
                                        *, phase: str) -> HealthReport:
    """phase: 'empty' right after a no-result search, 'results' after a hit."""
    report = HealthReport()
    report.add(await verify_serial_search_input(page, selectors))
    report.add(await verify_search_button(page, selectors))
    if phase == "empty":
        report.add(await verify_no_result_state(page, selectors))
    else:
        report.add(await verify_result_container(page, selectors))
    return report


# ── Pre-flight gate, run before every real automation run ───────────────────
async def preflight(page: Any, selectors: dict[str, str], *, base_url: str,
                    login_host_markers: list[str] | None = None,
                    timeout_ms: int = 8000) -> HealthReport:
    """Answers requirement 13's five questions, cheaply, on the already-open page."""
    report = HealthReport()
    markers = [m.lower() for m in (login_host_markers or [])]

    # 1. Is the application reachable at all?
    started = time.monotonic()
    try:
        url = page.url or ""
        reachable = bool(url) and not url.startswith("about:")
        report.add(CheckResult("app_reachable", reachable, "reachable", base_url,
                               url[:120] or "no url", int((time.monotonic() - started) * 1000)))
    except Exception as exc:
        report.add(CheckResult("app_reachable", False, "reachable", base_url, str(exc)[:160]))

    # 2. Are we authenticated, or sitting on the sign-in host?
    on_login = any(m in (page.url or "").lower() for m in markers)
    report.add(CheckResult("authenticated_session", not on_login, "reachable", None,
                           "redirected to the sign-in host" if on_login else "session looks active"))

    # 3-5. The three elements the search step cannot run without.
    report.add(await _check(page, "app_shell_present", selectors.get("ready.app_shell"),
                            "visible", timeout_ms))
    report.add(await _check(page, "search_page_reachable", selectors.get("ready.search_page"),
                            "visible", timeout_ms))
    report.add(await _check(page, "serial_input_visible", selectors.get("search.input"),
                            "visible", timeout_ms))
    report.add(await _check(page, "search_button_visible", selectors.get("search.submit"),
                            "visible", timeout_ms))
    # The results container is typically absent until a search runs, so `attached`
    # is the honest requirement here — `visible` would fail on a healthy page.
    report.add(await _check(page, "result_container_available", selectors.get("search.results"),
                            "attached", 2000))
    return report
