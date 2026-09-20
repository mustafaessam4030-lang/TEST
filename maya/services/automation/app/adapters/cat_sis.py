"""Caterpillar SIS2 adapter — https://sis2.cat.com/#/

The only module in the platform that knows this site exists.

Three properties of SIS2 shape everything here:
  * Angular SPA  → wait on application ready-markers, never on `networkidle`
  * hash routing → route changes fire no document load
  * CWS/Cat Login (OIDC redirect) → session is the expensive resource; reuse it

Every selector comes from `config/sources/cat_sis.yaml` (captured from a real
authenticated session and version-stamped), never hard-coded here and never
guessed. A missing required selector is WEBSITE_CHANGED — never "not found".
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.adapters.base import RawPayload, SearchOutcome, SourceCapabilities
from app.adapters.browser import RunContext
from app.adapters.selector_health import HealthReport, preflight as run_preflight
from app.adapters.selector_store import flatten_config
from app.core.errors import AutomationError, ErrorCode
from app.core.logging import log

logger = logging.getLogger(__name__)

PLACEHOLDER = "TODO_CAPTURE"

# Text that means a person has to act. We detect it and stop; we never satisfy it.
MFA_PATTERNS = re.compile(
    r"(one[- ]time (code|passcode)|verification code|authenticat(or|ion) app|security code|"
    r"two[- ]factor|multi[- ]factor|\bmfa\b|\botp\b|send (a )?code|verify your identity|"
    r"trust this (browser|device))", re.I)


class SelectorContractError(AutomationError):
    def __init__(self, selector_id: str, detail: str = "") -> None:
        super().__init__(
            ErrorCode.WEBSITE_CHANGED,
            f"Expected element '{selector_id}' was not present on the page.",
            details={"selector_id": selector_id, "detail": detail[:200]},
        )


class CatSisAdapter:
    def __init__(self, config: dict[str, Any], *, secret_provider: Any = None) -> None:
        self.cfg = config
        self.sel = config.get("selectors", {})
        self.routes = config.get("routes", {})
        self.ready = config.get("ready_markers", {})
        self.auth = config.get("auth", {})
        self.extraction = config.get("extraction", {})
        self.secret_provider = secret_provider  # resolves vault:// refs; never a request input

    # ── identity ────────────────────────────────────────────────────────────
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            source_id=self.cfg.get("source_id", "cat_sis"),
            label=self.cfg.get("label", "Caterpillar SIS"),
            needs_login=True,
            kind="browser",
            selector_version=self.cfg.get("selector_version"),
            politeness_delay_ms=int(self.cfg.get("politeness", {}).get("min_delay_ms", 1500)),
        )

    # ── helpers ─────────────────────────────────────────────────────────────
    def _selector(self, group: str, name: str, *, required: bool = True) -> str | None:
        value = (self.sel.get(group) or {}).get(name)
        if not value or value == PLACEHOLDER:
            if required:
                raise AutomationError(
                    ErrorCode.WEBSITE_CHANGED,
                    f"Selector '{group}.{name}' is not configured for this source.",
                    details={"hint": "Run scripts/capture_selectors.py and confirm the contract.",
                             "selector_id": f"{group}.{name}"},
                )
            return None
        return value

    def _is_login_page(self, url: str) -> bool:
        markers = self.auth.get("login_host_markers", [])
        return any(m.lower() in (url or "").lower() for m in markers)

    async def _detect_challenge(self, ctx: RunContext) -> None:
        """Human gates are hard stops: we detect them and stop, never satisfy them."""
        for marker in self.cfg.get("challenge_markers", []):
            try:
                if await ctx.page.locator(marker).count():
                    raise AutomationError(ErrorCode.CAPTCHA_DETECTED,
                                          "The source presented a security challenge.",
                                          details={"marker": marker})
            except AutomationError:
                raise
            except Exception:
                continue

        # MFA: a one-time-code field, or the page saying so in words.
        try:
            if await ctx.page.locator("input[autocomplete='one-time-code']").count():
                raise AutomationError(ErrorCode.MFA_REQUIRED,
                                      "Sign-in is asking for a one-time code.",
                                      details={"marker": "input[autocomplete=one-time-code]"})
        except AutomationError:
            raise
        except Exception:
            pass
        try:
            text = (await ctx.page.inner_text("body"))[:4000]
        except Exception:
            return
        match = MFA_PATTERNS.search(text)
        if match:
            raise AutomationError(
                ErrorCode.MFA_REQUIRED,
                "Sign-in requires human verification at the source.",
                details={"matched_text": match.group(0)[:60], "url": ctx.page.url[:160]})

    async def _wait_ready(self, ctx: RunContext, marker_key: str) -> None:
        marker = self.ready.get(marker_key)
        if not marker or marker == PLACEHOLDER:
            raise AutomationError(
                ErrorCode.WEBSITE_CHANGED,
                f"Ready-marker '{marker_key}' is not configured.",
                details={"hint": "Capture it from an authenticated session first."})
        try:
            await ctx.page.wait_for_selector(marker, state="visible",
                                             timeout=ctx.step_timeout_ms)
        except Exception as exc:
            await self._detect_challenge(ctx)
            if self._is_login_page(ctx.page.url):
                raise AutomationError(ErrorCode.SESSION_EXPIRED,
                                      "Redirected to sign-in while waiting for the app.") from exc
            raise SelectorContractError(f"ready.{marker_key}", str(exc)) from exc

    @staticmethod
    def _classify_navigation_error(exc: Exception) -> AutomationError:
        text = str(exc).lower()
        if "timeout" in text:
            return AutomationError(ErrorCode.TIMEOUT, "The source did not respond in time.")
        if any(t in text for t in ("net::err", "dns", "connection", "ssl", "socket")):
            return AutomationError(ErrorCode.NETWORK_ERROR, "The source could not be reached.")
        return AutomationError(ErrorCode.INTERNAL_ERROR, f"Navigation failed: {str(exc)[:160]}")

    # ── steps ───────────────────────────────────────────────────────────────
    async def preflight(self, ctx: RunContext) -> HealthReport:
        """Answer the five health questions before any real work.

        A failure here is WEBSITE_CHANGED and the run stops. We never fall back to
        clicking around to find something that looks close enough.
        """
        report = await run_preflight(
            ctx.page, flatten_config(self.cfg),
            base_url=self.cfg.get("base_url", ""),
            login_host_markers=self.auth.get("login_host_markers", []),
            timeout_ms=min(ctx.step_timeout_ms, 8000))
        log(logger, logging.INFO if report.ok else logging.ERROR, "sis.preflight",
            run_id=ctx.run_id, ok=report.ok, failed=[c.name for c in report.failed])
        return report

    async def ensure_session(self, ctx: RunContext, *, force_relogin: bool = False) -> None:
        base = self.cfg.get("base_url", "https://sis2.cat.com/#/")
        try:
            await ctx.page.goto(base, wait_until="domcontentloaded",
                                timeout=ctx.step_timeout_ms)
        except Exception as exc:
            raise self._classify_navigation_error(exc) from exc

        await self._detect_challenge(ctx)

        needs_login = force_relogin or self._is_login_page(ctx.page.url)
        if not needs_login:
            needs_login = not await self._looks_authenticated(ctx)

        if needs_login:
            await self._detect_challenge(ctx)     # MFA/CAPTCHA before we touch the form
            await self._login(ctx)

    async def _looks_authenticated(self, ctx: RunContext) -> bool:
        """Positive evidence of a session — never "the page rendered, so we must be in".

        An app shell usually renders before authentication resolves, so treating it
        as proof silently skips sign-in and the failure surfaces much later as a
        confusing WEBSITE_CHANGED. Prefer an explicit post-login marker; failing
        that, require the shell AND the absence of a sign-in form.
        """
        marker = self.ready.get("authenticated")
        if marker and marker != PLACEHOLDER:
            try:
                await ctx.page.wait_for_selector(marker, state="visible", timeout=8000)
                return True
            except Exception:
                return False

        shell = self.ready.get("app_shell")
        if not shell or shell == PLACEHOLDER:
            return False          # cannot prove it → prove it by signing in
        try:
            await ctx.page.wait_for_selector(shell, state="visible", timeout=8000)
        except Exception:
            return False
        try:
            pwd = ctx.page.locator("input[type='password']").first
            if await pwd.count() and await pwd.is_visible():
                return False      # a sign-in form is on screen: we are not in
        except Exception:
            pass
        return True

    async def _login(self, ctx: RunContext) -> None:
        creds = self._credentials()
        user_sel = self._selector("login", "username")
        pass_sel = self._selector("login", "password")
        submit_sel = self._selector("login", "submit")
        try:
            await ctx.page.fill(user_sel, creds["username"])
            await ctx.page.fill(pass_sel, creds["password"])
            await ctx.page.click(submit_sel)
        except Exception as exc:
            await self._detect_challenge(ctx)
            raise SelectorContractError("login.form", str(exc)) from exc

        try:
            await self._wait_ready(ctx, "authenticated"
                                   if self.ready.get("authenticated") not in (None, PLACEHOLDER)
                                   else "app_shell")
        except AutomationError as exc:
            await self._detect_challenge(ctx)
            error_sel = self._selector("login", "error", required=False)
            if error_sel:
                try:
                    if await ctx.page.locator(error_sel).count():
                        raise AutomationError(ErrorCode.LOGIN_FAILED,
                                              "The source rejected the automation credentials.")
                except AutomationError:
                    raise
                except Exception:
                    pass
            if self._is_login_page(ctx.page.url):
                raise AutomationError(ErrorCode.LOGIN_FAILED,
                                      "Sign-in did not complete.") from exc
            raise
        log(logger, logging.INFO, "sis.login.ok", run_id=ctx.run_id)

    def _credentials(self) -> dict[str, str]:
        ref = self.auth.get("secret_ref")
        if not ref or self.secret_provider is None:
            raise AutomationError(ErrorCode.LOGIN_FAILED,
                                  "No credential source is configured for this adapter.")
        creds = self.secret_provider(ref)  # values are never logged or returned upward
        if not creds.get("username") or not creds.get("password"):
            raise AutomationError(ErrorCode.LOGIN_FAILED, "Stored credentials are incomplete.")
        return creds

    async def search(self, ctx: RunContext, serial_number: str) -> SearchOutcome:
        route = self.routes.get("search")
        if route:
            # Hash navigation fires no document load: change the hash, then wait on the app.
            await ctx.page.evaluate("(h) => { window.location.hash = h; }", route.lstrip("#"))
        await self._wait_ready(ctx, "search_page")

        input_sel = self._selector("search", "input")
        submit_sel = self._selector("search", "submit", required=False)
        results_sel = self._selector("search", "results")
        empty_sel = self._selector("search", "no_results_marker")

        try:
            await ctx.page.fill(input_sel, serial_number)
            if submit_sel:
                await ctx.page.click(submit_sel)
            else:
                await ctx.page.press(input_sel, "Enter")
        except Exception as exc:
            raise SelectorContractError("search.input", str(exc)) from exc

        # Positive evidence for BOTH outcomes. Whichever appears first decides.
        try:
            await ctx.page.wait_for_selector(f"{results_sel}, {empty_sel}", state="visible",
                                             timeout=ctx.step_timeout_ms)
        except Exception as exc:
            await self._detect_challenge(ctx)
            if self._is_login_page(ctx.page.url):
                raise AutomationError(ErrorCode.SESSION_EXPIRED,
                                      "Session expired during search.") from exc
            if "timeout" in str(exc).lower():
                raise AutomationError(ErrorCode.TIMEOUT,
                                      "Search results did not render in time.") from exc
            raise SelectorContractError("search.results", str(exc)) from exc

        # Visibility, not presence. SPAs keep both the results container and the
        # empty state in the DOM and toggle them, so count() would report every
        # search as "not found" — the exact false negative this design forbids.
        if await self._is_visible(ctx, empty_sel):
            return SearchOutcome(found=False, evidence=f"empty_state_visible:{empty_sel}")

        if not await self._is_visible(ctx, results_sel):
            # Neither a result nor a recognised empty state: the page changed.
            raise SelectorContractError(
                "search.results",
                "neither the results container nor the empty state became visible")

        first = self._selector("search", "first_result", required=False)
        if first:
            try:
                await ctx.page.click(first)
                await self._wait_ready(ctx, "detail_page")
            except AutomationError:
                raise
            except Exception as exc:
                raise SelectorContractError("search.first_result", str(exc)) from exc

        return SearchOutcome(found=True, detail_url=ctx.page.url,
                             evidence=f"results:{results_sel}")

    @staticmethod
    async def _is_visible(ctx: RunContext, selector: str) -> bool:
        """True only when the element is actually on screen, not merely in the DOM."""
        try:
            locator = ctx.page.locator(selector).first
            return bool(await locator.count()) and await locator.is_visible()
        except Exception:
            return False

    async def extract(self, ctx: RunContext) -> RawPayload:
        """Prefer the app's own XHR JSON; fall back to reading the DOM."""
        payload = RawPayload(source_url=ctx.page.url, retrieved_at=datetime.now(timezone.utc))
        strategy = self.extraction.get("strategy", "dom_then_xhr")

        if strategy != "dom_only":
            xhr = self._pick_xhr(ctx)
            if xhr is not None:
                payload.fields = self._flatten(xhr)
                payload.payload_kind = "xhr"

        if not payload.fields:
            payload.fields = await self._extract_dom(ctx)
            payload.payload_kind = "dom"

        payload.specifications = await self._extract_specs(ctx)

        required = self.extraction.get("required_fields", ["equipment_model"])
        missing = [f for f in required if not payload.fields.get(f)]
        if missing:
            raise AutomationError(
                ErrorCode.EXTRACTION_ERROR,
                "The record was reached but required fields could not be read.",
                details={"missing": missing, "payload_kind": payload.payload_kind})
        return payload

    def _pick_xhr(self, ctx: RunContext) -> dict[str, Any] | None:
        patterns = [re.compile(p, re.I) for p in self.extraction.get("xhr_url_patterns", [])]
        if not patterns:
            return None
        for captured in reversed(ctx.captured_xhr):
            if any(p.search(captured["url"]) for p in patterns) and captured["status"] == 200:
                body = captured.get("body")
                if isinstance(body, dict):
                    return body
        return None

    @staticmethod
    def _flatten(payload: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for key, value in payload.items():
            path = f"{prefix}{key}"
            if isinstance(value, dict):
                flat.update(CatSisAdapter._flatten(value, f"{path}."))
            else:
                flat[path] = value
        return flat

    async def _extract_dom(self, ctx: RunContext) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for field_name, selector in (self.sel.get("detail") or {}).items():
            if not selector or selector == PLACEHOLDER or field_name in ("spec_rows", "spec_cell"):
                continue
            try:
                locator = ctx.page.locator(selector).first
                if not await locator.count():
                    continue
                # A URL field means the href, not the link text. Reading "Parts manual"
                # into a *_url field is how a nonsense value reaches validation.
                value = None
                if field_name.endswith("_url"):
                    value = await locator.get_attribute("href") or await locator.get_attribute("src")
                if not value:
                    value = (await locator.inner_text()).strip()
                if value:
                    out[field_name] = value.strip()
            except Exception:
                continue  # a field we cannot read is absent, not invented
        return out

    async def _extract_specs(self, ctx: RunContext) -> list[dict[str, Any]]:
        row_sel = (self.sel.get("detail") or {}).get("spec_rows")
        if not row_sel or row_sel == PLACEHOLDER:
            return []
        cell = (self.sel.get("detail") or {}).get("spec_cell", "td")
        specs: list[dict[str, Any]] = []
        try:
            rows = ctx.page.locator(row_sel)
            for i in range(min(await rows.count(), 300)):
                cells = rows.nth(i).locator(cell)
                if await cells.count() < 2:
                    continue
                name = (await cells.nth(0).inner_text()).strip()
                value = (await cells.nth(1).inner_text()).strip()
                if name:
                    specs.append({"name": name, "value": value})
        except Exception as exc:
            log(logger, logging.WARNING, "sis.spec_extract_partial", error=str(exc)[:200])
        return specs
