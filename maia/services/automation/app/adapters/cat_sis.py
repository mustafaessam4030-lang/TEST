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
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.adapters.base import RawPayload, SearchOutcome, SourceCapabilities
from app.adapters.browser import RunContext
from app.adapters.selector_health import HealthReport, preflight as run_preflight
from app.adapters.selector_store import flatten_config
from app.core.errors import AutomationError, ErrorCode
from app.core.logging import log

logger = logging.getLogger(__name__)

PLACEHOLDER = "TODO_CAPTURE"

# The same reader the capture tool proved the selectors with. Sharing one
# implementation is what stops "captured" and "read at run time" from drifting.
SIS_DOM_JS = (Path(__file__).parent / "sis_dom.js").read_text(encoding="utf-8")

#: Read in this order, and first: the four fields the detail page must yield.
DETAIL_FIELD_ORDER = ("machine_serial_number", "machine_build_date",
                      "engine_serial_number", "engine_build_date")

#: Detail selectors that point at STRUCTURE, not at a value. Sweeping their text
#: into the record is how a table ends up stored as a field.
STRUCTURAL_DETAIL_KEYS = frozenset({
    "spec_rows", "spec_cell", "scroll_container", "details_anchor",
    "parts_group", "parts_table", "parts_header_cells", "parts_rows", "parts_cell",
    *DETAIL_FIELD_ORDER,          # read by _extract_labelled, which strips the label
})

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
        await self._dismiss_consent(ctx)

        needs_login = force_relogin or self._is_login_page(ctx.page.url)
        if not needs_login:
            needs_login = not await self._looks_authenticated(ctx)

        if needs_login:
            await self._detect_challenge(ctx)     # MFA/CAPTCHA before we touch the form
            await self._login(ctx)

    async def _dismiss_consent(self, ctx: RunContext) -> None:
        """Click the cookie/consent accept control if one was captured.

        The banner overlays the page and silently swallows clicks; without this a
        capture-perfect selector still fails. Only ever a captured selector — if
        none is configured, we do nothing rather than hunt for a likely button.
        """
        selector = (self.sel.get("consent") or {}).get("accept")
        if not selector or selector == PLACEHOLDER:
            return
        try:
            locator = ctx.page.locator(selector).first
            if await locator.count() and await locator.is_visible():
                await locator.click(timeout=5000)
                log(logger, logging.INFO, "sis.consent_dismissed", run_id=ctx.run_id)
        except Exception as exc:
            log(logger, logging.WARNING, "sis.consent_dismiss_failed", error=str(exc)[:120])

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
        # Identity providers commonly split sign-in across two screens: username,
        # then password. When the contract captured that step, walk it.
        user_submit_sel = self._selector("login", "username_submit", required=False)
        try:
            await ctx.page.fill(user_sel, creds["username"])
            if user_submit_sel:
                await ctx.page.click(user_submit_sel)
                await ctx.page.wait_for_selector(pass_sel, state="visible",
                                                 timeout=ctx.step_timeout_ms)
                await self._detect_challenge(ctx)   # MFA often lands between the steps
            await ctx.page.fill(pass_sel, creds["password"])
            await ctx.page.click(submit_sel)
        except AutomationError:
            raise
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
        # MAIA_SIS_SECRET_REF points the worker at the real secret store without
        # editing the source contract. It names a location, never a value.
        ref = os.environ.get("MAIA_SIS_SECRET_REF") or self.auth.get("secret_ref")
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

    async def extract(self, ctx: RunContext, serial_number: str | None = None) -> RawPayload:
        """Read the record — details first, then every "Product - …" parts group.

        Order matters and is not cosmetic. The detail page renders inside the
        SIS content pane, and the equipment details sit below its fold: reading
        before scrolling finds nothing and looks exactly like a changed website.
        So: scroll the pane, wait for the SPA to settle, read the four
        machine/engine fields, then collect the parts groups.
        """
        payload = RawPayload(source_url=ctx.page.url, retrieved_at=datetime.now(timezone.utc))
        strategy = self.extraction.get("strategy", "dom_then_xhr")

        await self._install_reader(ctx)
        # The detail page as it first rendered — before anything was scrolled.
        await self._shot(ctx, payload, "page")
        reveal = await self._reveal_details(ctx)
        # The equipment details, now that the pane has been scrolled to them.
        await self._shot(ctx, payload, "details")

        if strategy != "dom_only":
            xhr = self._pick_xhr(ctx)
            if xhr is not None:
                payload.fields = self._flatten(xhr)
                payload.payload_kind = "xhr"

        dom_fields = await self._extract_dom(ctx)
        labelled = await self._extract_labelled(ctx)
        # The four labelled fields are read from the DOM even when the SPA's own
        # JSON was available: the page is what the user is looking at.
        if not payload.fields:
            payload.fields = dom_fields
            payload.payload_kind = "dom"
        else:
            for key, value in dom_fields.items():
                payload.fields.setdefault(key, value)
        payload.fields.update(labelled)

        payload.specifications = await self._extract_specs(ctx)
        payload.parts_data = await self._extract_parts(ctx, serial_number)
        await self._shot_parts(ctx, payload)
        payload.artifacts["scroll"] = str(reveal)[:500]
        payload.page_title = await self._page_title(ctx)
        payload.source_url = ctx.page.url

        required = self.extraction.get("required_fields", list(DETAIL_FIELD_ORDER))
        missing = [f for f in required if not payload.fields.get(f)]
        if missing:
            # Unprovable is unprovable: the page changed, or we are not where we
            # think we are. Never a partial answer dressed up as a complete one.
            raise AutomationError(
                ErrorCode.WEBSITE_CHANGED,
                "The record was reached but required fields could not be read.",
                details={"missing": missing, "payload_kind": payload.payload_kind,
                         "scroll": reveal, "url": ctx.page.url[:200],
                         "hint": "re-run the selector capture for this source"})

        self._cross_check_serial(payload, serial_number)
        return payload

    # ── evidence for a person: three screenshots, named for what they show ──
    async def _shot(self, ctx: RunContext, payload: RawPayload, name: str) -> None:
        """Take one screenshot. A failed screenshot never fails a lookup."""
        try:
            path = ctx.artifact_dir / f"{name}.png"
            await ctx.page.screenshot(path=str(path), full_page=False)
            payload.artifacts[f"screenshot.{name}"] = str(path)
        except Exception as exc:
            log(logger, logging.WARNING, "sis.screenshot_failed", name=name,
                error=str(exc)[:120])

    async def _shot_parts(self, ctx: RunContext, payload: RawPayload) -> None:
        """Scroll the first "Product - …" group into view, then photograph it."""
        heading = (self.sel.get("detail") or {}).get("parts_group")
        try:
            handle = await self._handle(heading, page=ctx.page)
            if handle is not None:
                await handle.scroll_into_view_if_needed(timeout=5000)
                await ctx.page.wait_for_timeout(400)
        except Exception as exc:
            log(logger, logging.WARNING, "sis.parts_scroll_failed", error=str(exc)[:120])
        await self._shot(ctx, payload, "parts")

    @staticmethod
    async def _page_title(ctx: RunContext) -> str | None:
        try:
            return (await ctx.page.title() or "").strip() or None
        except Exception:
            return None

    # ── the detail page: scroll, settle, then read ──────────────────────────
    async def _install_reader(self, ctx: RunContext) -> None:
        """Inject the shared reader. Idempotent, and CSP-safe (no script tag)."""
        try:
            if await ctx.page.evaluate("() => !!window.__maiaSisDom"):
                return
        except Exception:
            pass
        await ctx.page.evaluate(SIS_DOM_JS)

    async def _reveal_details(self, ctx: RunContext) -> dict[str, Any]:
        """Scroll the SIS CONTENT PANE — not the window — to the details section."""
        scroll = self.extraction.get("scroll") or {}
        if scroll.get("enabled") is False:
            return {"skipped": True}
        pattern = scroll.get("anchor_pattern")
        if not pattern:
            raise AutomationError(
                ErrorCode.WEBSITE_CHANGED,
                "No anchor is configured for the equipment-details section.",
                details={"hint": "extraction.scroll.anchor_pattern is missing from the contract"})

        # A captured selector is not always CSS (role=, text=, xpath=), so the
        # element is resolved by Playwright and handed to the reader directly.
        container = await self._handle((self.sel.get("detail") or {}).get("scroll_container"),
                                       page=ctx.page)
        try:
            result = await ctx.page.evaluate(
                "(a) => window.__maiaSisDom.revealSection(a[0], "
                "{maxSteps: a[1], pause: a[2], container: a[3]})",
                [pattern, int(scroll.get("max_steps", 30)),
                 int(scroll.get("step_pause_ms", 300)), container])
            settled = await ctx.page.evaluate(
                "(ms) => window.__maiaSisDom.settle({limitMs: ms})",
                int(scroll.get("settle_ms", 4000)))
        except Exception as exc:
            raise AutomationError(
                ErrorCode.EXTRACTION_ERROR,
                "The detail page could not be scrolled to the equipment details.",
                details={"error": str(exc)[:200]}) from exc

        result = dict(result or {})
        result["settle"] = settled
        if not result.get("found"):
            raise AutomationError(
                ErrorCode.WEBSITE_CHANGED,
                "The equipment-details section never appeared after scrolling the page.",
                details={"anchor_pattern": pattern, "scroll": result,
                         "url": ctx.page.url[:200],
                         "hint": "the detail layout changed, or this record has no details"})
        log(logger, logging.INFO, "sis.detail_revealed", run_id=ctx.run_id,
            pane=result.get("pane"), steps=result.get("steps"),
            settled=(settled or {}).get("settled"))
        return result

    async def _extract_labelled(self, ctx: RunContext) -> dict[str, Any]:
        """The four machine/engine fields, read from their proven selectors.

        SIS writes them as "<label> - <value>" inside a single element, so the
        reader strips the label pattern the contract records. When the captured
        element holds the value alone, nothing is stripped.
        """
        labels = self.extraction.get("detail_labels") or {}
        detail = self.sel.get("detail") or {}
        out: dict[str, Any] = {}
        for field_name in DETAIL_FIELD_ORDER:
            selector = detail.get(field_name)
            if not selector or selector == PLACEHOLDER:
                continue
            handle = await self._handle(selector, page=ctx.page)
            if handle is None:
                continue
            try:
                read = await ctx.page.evaluate(
                    "(a) => window.__maiaSisDom.readLabelledFrom(a[0], a[1])",
                    [handle, labels.get(field_name)])
            except Exception:
                continue
            value = (read or {}).get("value")
            if value:
                out[field_name] = str(value).strip()
        return out

    async def _extract_parts(self, ctx: RunContext,
                             serial_number: str | None) -> dict[str, Any] | None:
        """Every "Product - …" group on the page, each with its parts rows."""
        cfg = self.extraction.get("parts") or {}
        heading = (self.sel.get("detail") or {}).get("parts_group")
        heading = heading if heading and heading != PLACEHOLDER else None
        limit = int(cfg.get("max_rows_per_group", 500))

        groups: list[dict[str, Any]] = []
        try:
            handles = await self._handles(heading, page=ctx.page) if heading else []
            if handles:
                for group in await ctx.page.evaluate(
                        "(a) => window.__maiaSisDom.readProductGroupsFrom(a[0], a[1])",
                        [handles, limit]):
                    group["discovered_by"] = "selector:detail.parts_group"
                    groups.append(group)
            if cfg.get("all_groups", True):
                # The user's requirement is "all Product - …", and that is a rule
                # about the page's own wording, not a second guessed selector.
                # Anything the proven selector already covered is not repeated.
                seen = {g.get("title") for g in groups}
                for group in await ctx.page.evaluate(
                        "(a) => window.__maiaSisDom.readProductGroups(null, a)", limit):
                    if group.get("title") in seen:
                        continue
                    group["discovered_by"] = "heading_text:Product -"
                    groups.append(group)
        except Exception as exc:
            log(logger, logging.WARNING, "sis.parts_extract_partial", error=str(exc)[:200])

        if not groups:
            return None
        groups.sort(key=lambda g: (not g.get("is_entire_group"), g.get("title") or ""))
        entire = next((g for g in groups if g.get("is_entire_group")), None)
        mismatched = [g["title"] for g in groups
                      if serial_number and g.get("group_serial")
                      and self._same_serial(g["group_serial"], serial_number) is False]
        return {
            "group_titles": [g.get("title") for g in groups],
            "group_count": len(groups),
            "entire_group_title": (entire or {}).get("title"),
            "columns": (entire or groups[0]).get("columns") or [],
            "total_rows": sum(int(g.get("row_count") or 0) for g in groups),
            "groups": groups,
            "serial_mismatched_groups": mismatched,
            "selector_id": "detail.parts_group",
        }

    async def _handle(self, selector: str | None, *, page: Any = None) -> Any:
        """Resolve one captured selector to a live element, or None."""
        handles = await self._handles(selector, page=page)
        return handles[0] if handles else None

    async def _handles(self, selector: str | None, *, page: Any = None) -> list[Any]:
        """Playwright resolves the selector; the reader only ever sees elements.

        Captured selectors are whatever proved most stable — CSS, role=, xpath.
        Handing those to document.querySelector would silently find nothing.
        """
        if not selector or selector == PLACEHOLDER or page is None:
            return []
        try:
            return await page.locator(selector).element_handles()
        except Exception:
            return []

    @staticmethod
    def _same_serial(left: str | None, right: str | None) -> bool | None:
        """None when either side is absent — unknown is not a mismatch."""
        if not left or not right:
            return None
        clean = lambda v: re.sub(r"[^A-Z0-9]", "", str(v).upper())   # noqa: E731
        return clean(left) == clean(right)

    def _cross_check_serial(self, payload: RawPayload, serial_number: str | None) -> None:
        """The record on screen must be the record that was asked for.

        Without this, a stale render or a mis-clicked row answers confidently
        about a different machine — the single worst failure this system can
        have. A mismatch is an error, never a footnote on the data.
        """
        field_name = self.extraction.get("serial_cross_check")
        if not field_name or not serial_number:
            return
        found = payload.fields.get(field_name)
        same = self._same_serial(found, serial_number)
        if same is None:
            return
        if not same:
            raise AutomationError(
                ErrorCode.EXTRACTION_ERROR,
                "The page showed a different machine than the one requested.",
                details={"requested": str(serial_number)[:40], "field": field_name,
                         "page_value": str(found)[:40]})
        payload.fields["serial_cross_check"] = field_name

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
            if not selector or selector == PLACEHOLDER:
                continue
            if field_name in STRUCTURAL_DETAIL_KEYS:
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
