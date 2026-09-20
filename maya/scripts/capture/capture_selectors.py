#!/usr/bin/env python3
"""
INTERACTIVE SELECTOR CAPTURE MODE — Caterpillar SIS2 (https://sis2.cat.com/#/)
═══════════════════════════════════════════════════════════════════════════════
Captures REAL selectors from a REAL authenticated session. Never invents one.

  live      (default)  headed browser, human signs in (MFA included), then
                       click-to-pick each required element
  auto-login           same pipeline, credentials from the environment; stops
                       immediately and cleanly on MFA or any bot challenge
  self-test            runs the identical engine against a local fixture page,
                       to prove the machinery before pointing it at SIS

For every target the tool derives candidates from what is actually in the DOM,
ranked data-testid > id > name > aria-label > role+name > stable CSS > XPath,
and keeps one only if it resolves back to that exact element, uniquely. If no
candidate survives, the target is written as {"status": "TODO_CAPTURE"} and the
run STOPS before verification. Nothing is ever approximated.

Outputs
  config/sis_selectors.json          the contract (live runs only)
  artifacts/capture/<run>/*.png      a screenshot per step
  artifacts/capture/<run>/*.html     DOM snapshots (secrets redacted)
  artifacts/capture/<run>/report.json

Usage
  python scripts/capture/capture_selectors.py --serial CAT0336LKBW00123
  python scripts/capture/capture_selectors.py --self-test
  python scripts/capture/capture_selectors.py --auto-login \
      --secret-ref vault://kv/maya/cat_sis --serial SN123456
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "automation"))

from app.core.secrets import resolve_secret  # noqa: E402
from app.core.errors import AutomationError  # noqa: E402
from app.adapters.selector_health import (  # noqa: E402
    run_post_capture_verification, verify_no_result_state, verify_result_container,
    verify_search_button, verify_serial_search_input,
)

PICKER_JS = (Path(__file__).parent / "picker.js").read_text()
FIXTURE = Path(__file__).parent / "fixture.html"
DEFAULT_BASE = "https://sis2.cat.com/#/"
CHROMIUM_PATH = os.environ.get("MAYA_CHROMIUM_PATH") or None

# Text that means "a human must finish this" — we stop, we never work around it.
MFA_PATTERNS = re.compile(
    r"(one[- ]time|verification code|authenticat(or|ion) app|security code|"
    r"two[- ]factor|multi[- ]factor|\bmfa\b|\botp\b|send (a )?code|trust this (browser|device))", re.I)
CHALLENGE_SELECTORS = [
    "iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "#challenge-form",
    "#cf-challenge-running", "[data-testid='bot-challenge']", "iframe[title*='challenge']",
]
LOGIN_HOST_MARKERS = ["signin.cat.com", "login.cat.com", "/cws", "authenticate", "oauth", "idp"]


# ── what must be captured ───────────────────────────────────────────────────
class NavigationFailed(RuntimeError):
    """The browser could not load the base URL at all — nothing else can proceed."""


@dataclass
class Target:
    key: str
    title: str
    hint: str
    phase: str
    required: bool = True
    kind: str = "element"


TARGETS: list[Target] = [
    Target("ready.app_shell", "Application shell",
           "any element that exists only once you are signed in and the app is usable "
           "(header, nav bar, user menu)", "app_shell"),
    Target("ready.authenticated", "Signed-in marker",
           "an element that appears ONLY once you are signed in (user menu, avatar, "
           "sign-out link) — this is how the automation proves it has a session",
           "app_shell"),
    Target("nav.search_link", "Navigation → equipment search",
           "the menu link that opens equipment search", "app_shell", required=False),
    Target("ready.search_page", "Search page ready marker",
           "a heading or container unique to the equipment search screen", "search"),
    Target("search.input", "Serial-number input", "the field you type the serial into", "search"),
    Target("search.submit", "Search button", "the button that submits the search", "search",
           required=False),
    Target("search.no_results_marker", "Empty / no-result state",
           "the message shown for a serial with no records — this is the ONLY proof of "
           "SERIAL_NOT_FOUND, so it must be exact", "empty"),
    Target("search.results", "Results container",
           "the table or list that holds search hits", "results"),
    Target("search.first_result", "First result row",
           "the clickable row that opens the equipment detail", "results", required=False),
    Target("ready.detail_page", "Detail page ready marker",
           "a heading or container unique to the equipment detail screen", "detail"),
    Target("detail.equipment_model", "Detail → model", "the model value", "detail"),
    Target("detail.equipment_type", "Detail → equipment type", "the type/family value",
           "detail", required=False),
    Target("detail.build_date", "Detail → build date", "the build/manufacture date value",
           "detail", required=False),
    Target("detail.engine_family", "Detail → engine", "the engine model/arrangement value",
           "detail", required=False),
    Target("detail.parts_manual_url", "Detail → parts manual link", "the parts manual link",
           "detail", required=False),
    Target("detail.spec_rows", "Detail → specification rows",
           "ONE row of the specifications table (the row, not a cell)", "detail", required=False),
]


@dataclass
class CaptureRecord:
    name: str
    selector: str | None = None
    strategy: str | None = None
    element_text: str = ""
    url: str = ""
    captured_at: str = ""
    confidence: str = "verified"
    status: str | None = None
    match_count: int | None = None
    alternates: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    screenshot: str | None = None

    #: keys the contract guarantees on every captured entry
    CONTRACT_KEYS = ("name", "selector", "strategy", "element_text", "url",
                     "captured_at", "confidence")

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        out = {k: data[k] for k in self.CONTRACT_KEYS}
        for key, value in data.items():
            if key not in out and value not in (None, [], ""):
                out[key] = value
        return out


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Redactor:
    """Secrets never reach a screenshot caption, an HTML snapshot, or the report."""

    def __init__(self, secrets: list[str]) -> None:
        self.secrets = [s for s in secrets if s and len(s) >= 4]

    def __call__(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***REDACTED***")
        return text


class CaptureSession:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.mode = args.mode
        self.base = FIXTURE.as_uri() if self.mode == "self-test" else args.base
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.outdir = ROOT / "artifacts" / "capture" / f"{self.mode}-{self.run_id}"
        self.outdir.mkdir(parents=True, exist_ok=True)
        self.records: dict[str, CaptureRecord] = {}
        self.xhr: list[str] = []
        self.steps: list[dict[str, Any]] = []
        self.redact = Redactor([])   # re-armed with the real secret at resolve time
        self._pending: asyncio.Future | None = None
        self.page: Any = None
        self.context: Any = None
        self.browser: Any = None
        self.stopped_reason: str | None = None
        self.recon: dict[str, Any] = {}

    # ── lifecycle ───────────────────────────────────────────────────────────
    @staticmethod
    def explain_navigation_failure(error: str) -> str:
        """Turn a browser-level failure into something a person can act on."""
        if "ERR_CERT" in error or "SSL" in error.upper():
            return ("the browser does not trust the certificate presented for this host. "
                    "A TLS-inspecting proxy is almost certainly in the path: install its CA "
                    "in the browser trust store (Chrome/Chromium: Settings → Privacy and "
                    "security → Security → Manage certificates → Authorities), or run the "
                    "capture from a network without interception. Do not disable "
                    "certificate checks — you would be typing SIS credentials into a "
                    "connection you cannot verify")
        if "ERR_NAME_NOT_RESOLVED" in error:
            return "the hostname did not resolve — check DNS or the corporate VPN"
        if "ERR_CONNECTION" in error or "ERR_TIMED_OUT" in error:
            return "the host could not be reached — check network access or the VPN"
        if "ERR_BLOCKED" in error or "ERR_ACCESS_DENIED" in error:
            return "the network blocked this request — a proxy or policy is in the way"
        return error[:200]

    async def start(self) -> None:
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        headed = self.mode == "live"
        launch: dict[str, Any] = {"headless": not headed,
                                  "args": ["--disable-dev-shm-usage", "--no-sandbox"]}
        if CHROMIUM_PATH:
            launch["executable_path"] = CHROMIUM_PATH
        self.browser = await self._pw.chromium.launch(**launch)
        self.context = await self.browser.new_context(viewport={"width": 1536, "height": 960})
        self.context.set_default_timeout(self.args.timeout_ms)

        await self.context.add_init_script(PICKER_JS)
        await self.context.expose_binding("__mayaPickBinding", self._on_pick)
        await self.context.expose_binding("__mayaSkipBinding", self._on_skip)

        self.page = await self.context.new_page()
        self.page.on("response", self._on_response)
        try:
            await self.page.goto(self.base, wait_until="domcontentloaded")
        except Exception as exc:
            raise NavigationFailed(self.explain_navigation_failure(str(exc))) from exc
        self.log(f"opened {self.base}")

    async def close(self) -> None:
        try:
            if self.browser:
                await self.browser.close()
        finally:
            await self._pw.stop()

    def log(self, message: str) -> None:
        print(f"  · {self.redact(message)}", flush=True)

    def step(self, name: str, status: str, **extra: Any) -> None:
        self.steps.append({"step": name, "status": status, "at": now_iso(),
                           **{k: self.redact(str(v)) if isinstance(v, str) else v
                              for k, v in extra.items()}})

    # ── browser event handlers ──────────────────────────────────────────────
    def _on_response(self, response: Any) -> None:
        try:
            ctype = (response.headers or {}).get("content-type", "")
            if "json" in ctype and response.request.resource_type in ("xhr", "fetch"):
                url = re.sub(r"[?#].*$", "", response.url)
                if url not in self.xhr and not url.startswith("data:"):
                    self.xhr.append(url)
        except Exception:
            pass

    async def _on_pick(self, source: Any, payload: dict[str, Any]) -> None:
        if self._pending and not self._pending.done():
            self._pending.set_result(payload)

    async def _on_skip(self, source: Any, reason: str = "skipped") -> None:
        if self._pending and not self._pending.done():
            self._pending.set_result(None)

    # ── artifacts ───────────────────────────────────────────────────────────
    async def shot(self, name: str) -> str | None:
        path = self.outdir / f"{name}.png"
        try:
            await self.page.screenshot(path=str(path), full_page=False)
            return str(path.relative_to(ROOT))
        except Exception as exc:
            self.log(f"screenshot failed for {name}: {exc}")
            return None

    async def snapshot(self, name: str) -> None:
        try:
            html = self.redact(await self.page.content())
            (self.outdir / f"{name}.html").write_text(html[:2_000_000])
        except Exception as exc:
            self.log(f"DOM snapshot failed for {name}: {exc}")

    # ── picking ─────────────────────────────────────────────────────────────
    async def pick(self, target: Target) -> CaptureRecord:
        """Return a verified record, or one marked TODO_CAPTURE. Never a guess."""
        print(f"\n▸ {target.title}\n    {target.hint}")
        loop = asyncio.get_running_loop()
        self._pending = loop.create_future()

        if self.mode == "self-test":
            picked = await self.page.evaluate("s => window.__mayaPicker.pickBySelector(s)",
                                              f'[data-maya-fixture="{target.key}"]')
            if not picked:
                self._pending = None
                return self._todo(target, "fixture has no hook for this target")
        else:
            await self.page.evaluate("t => window.__mayaPicker.activate(t[0], t[1])",
                                     [target.title, target.hint])
            print("    → click the element in the browser (Esc to skip)")

        try:
            # A human needs minutes; a scripted pick either happens at once or not at all.
            timeout = self.args.pick_timeout_s if self.mode == "live" else 20
            payload = await asyncio.wait_for(self._pending, timeout=timeout)
        except asyncio.TimeoutError:
            await self.page.evaluate("() => window.__mayaPicker.deactivate()")
            return self._todo(target, "no element was picked before the timeout")

        if payload is None:
            return self._todo(target, "operator skipped this target")

        # The picked element, for selectors only Playwright can resolve.
        handle = await self.page.evaluate_handle("() => window.__mayaPicked")
        info = payload.get("info") or {}
        candidates = payload.get("candidates") or []

        verified: list[dict[str, Any]] = []
        for cand in candidates:
            if cand.get("verified"):
                verified.append(cand)
                continue
            if cand.get("verified") is None:          # role=… must be checked by Playwright
                ok, count = await self._verify_with_playwright(cand["selector"], handle)
                cand["verified"], cand["count"] = ok, count
                if ok:
                    verified.append(cand)

        if not verified:
            return self._todo(target, "no candidate selector resolved uniquely to the "
                                      "picked element", extra_candidates=candidates)

        best = verified[0]
        record = CaptureRecord(
            name=target.key, selector=best["selector"], strategy=best["strategy"],
            element_text=info.get("element_text", "")[:120], url=info.get("url", ""),
            captured_at=now_iso(), confidence="verified", match_count=best.get("count"),
            alternates=[{"selector": c["selector"], "strategy": c["strategy"]}
                        for c in verified[1:4]],
            notes=[n for n in [best.get("note")] if n],
        )
        if not info.get("visible"):
            record.notes.append("element was not visible at capture time")
        record.screenshot = await self.shot(f"pick-{target.key.replace('.', '_')}")
        self.records[target.key] = record
        self.step(f"pick:{target.key}", "OK", selector=best["selector"],
                  strategy=best["strategy"])
        print(f"    ✓ {best['strategy']}: {best['selector']}")
        if len(verified) > 1:
            print(f"      alternates: {', '.join(c['strategy'] for c in verified[1:4])}")
        return record

    async def _verify_with_playwright(self, selector: str, handle: Any) -> tuple[bool, int]:
        try:
            locator = self.page.locator(selector)
            count = await locator.count()
            if count != 1:
                return False, count
            other = await locator.element_handle()
            same = await handle.evaluate("(el, other) => el === other", other)
            return bool(same), count
        except Exception:
            return False, 0

    def _todo(self, target: Target, reason: str,
              extra_candidates: list[dict[str, Any]] | None = None) -> CaptureRecord:
        record = CaptureRecord(name=target.key, status="TODO_CAPTURE", confidence="unverified",
                               captured_at=now_iso(), url=self.page.url,
                               notes=[reason] + ([
                                   "rejected candidates: " + ", ".join(
                                       f"{c['strategy']}({c.get('count')} matches)"
                                       for c in extra_candidates[:4])] if extra_candidates else []))
        self.records[target.key] = record
        self.step(f"pick:{target.key}", "TODO_CAPTURE", reason=reason)
        print(f"    ✗ TODO_CAPTURE — {reason}")
        return record

    # ── operator interaction ────────────────────────────────────────────────
    async def prompt(self, message: str) -> None:
        if self.mode != "live":
            return
        await asyncio.to_thread(input, f"\n[operator] {message}\n           press Enter when ready… ")

    async def detect_blockers(self, where: str) -> str | None:
        """MFA and bot challenges are hard stops. We do not automate around them."""
        for selector in CHALLENGE_SELECTORS:
            try:
                if await self.page.locator(selector).count():
                    return f"bot challenge detected at {where} ({selector})"
            except Exception:
                continue
        try:
            if await self.page.locator("input[autocomplete='one-time-code']").count():
                return f"MFA one-time-code field present at {where}"
            body = (await self.page.inner_text("body"))[:4000]
            if MFA_PATTERNS.search(body):
                return f"MFA prompt detected at {where}"
        except Exception:
            pass
        return None

    # ── authentication ──────────────────────────────────────────────────────
    async def authenticate(self) -> bool:
        if self.mode == "self-test":
            return await self._fixture_login()
        if self.mode == "auto-login":
            return await self._auto_login()
        await self.prompt("Sign in to SIS in the browser window (complete MFA if asked).")
        await self.shot("01-after-auth")
        await self.snapshot("01-after-auth")
        return True

    async def _fixture_login(self) -> bool:
        await self.page.fill('[data-maya-fixture="login.username"]', "fixture-user")
        await self.page.fill('[data-maya-fixture="login.password"]', "fixture-pass")
        await self.page.click('[data-maya-fixture="login.submit"]')
        await self.page.wait_for_selector("#page-search", state="visible")
        await self.shot("01-after-auth")
        return True

    async def _auto_login(self) -> bool:
        """One attempt, credentials from the environment, stop on any challenge.

        The login fields are found semantically (an input of type=password is a
        password field by definition, not by guesswork) — and whatever is found is
        still put through the same candidate + verification pipeline before it is
        written anywhere.
        """
        # Credentials come from the configured secret store (vault://…) or from the
        # environment (env://PREFIX). Never from a CLI argument, a file in the repo,
        # or anything a human typed into a chat window.
        try:
            creds = resolve_secret(self.args.secret_ref)
        except AutomationError as exc:
            self.stopped_reason = f"credentials unavailable: {exc.message}"
            return False
        username, password = creds["username"], creds["password"]
        # Anything the resolver returned is redacted out of every artifact from here.
        self.redact = Redactor([password])

        await self.shot("00-entry")
        await self.snapshot("00-entry")
        self.log(f"landed on {self.page.url}")

        blocker = await self.detect_blockers("entry")
        if blocker:
            self.stopped_reason = blocker
            await self.shot("00-blocker")
            return False

        # Username step
        user_field = None
        for selector in ["input[type='email']", "input[name='username']", "input[id*='user' i]",
                         "input[autocomplete='username']", "input[type='text']"]:
            loc = self.page.locator(selector).first
            try:
                if await loc.count() and await loc.is_visible():
                    user_field = loc
                    self.log(f"username field found via {selector}")
                    break
            except Exception:
                continue

        pwd_field = self.page.locator("input[type='password']").first
        has_pwd = bool(await pwd_field.count()) and await pwd_field.is_visible()

        if user_field is None and not has_pwd:
            self.stopped_reason = ("no sign-in form found on the landing page "
                                   f"(url={self.page.url[:120]})")
            return False

        if user_field is not None:
            await user_field.fill(username)
            self.step("auth:username", "OK")
            if not has_pwd:                      # two-step identity flow: submit, then password
                await self._submit_auth_step()
                await self.page.wait_for_timeout(2500)
                blocker = await self.detect_blockers("after username")
                if blocker:
                    self.stopped_reason = blocker
                    await self.shot("02-blocker-after-username")
                    return False
                pwd_field = self.page.locator("input[type='password']").first

        try:
            await pwd_field.wait_for(state="visible", timeout=15000)
        except Exception:
            self.stopped_reason = f"password field never appeared (url={self.page.url[:120]})"
            await self.shot("02-no-password-field")
            await self.snapshot("02-no-password-field")
            return False

        await pwd_field.fill(password)           # value is never logged or screenshotted unmasked
        self.step("auth:password", "OK")
        await self._submit_auth_step()
        await self.page.wait_for_timeout(5000)

        await self.shot("02-after-submit")
        await self.snapshot("02-after-submit")

        blocker = await self.detect_blockers("after sign-in")
        if blocker:
            self.stopped_reason = blocker
            return False

        still_on_login = any(m in self.page.url.lower() for m in LOGIN_HOST_MARKERS)
        if still_on_login:
            body = ""
            try:
                body = (await self.page.inner_text("body"))[:500]
            except Exception:
                pass
            # One attempt only: repeatedly retrying a rejected credential is how an
            # account gets locked at the partner.
            self.stopped_reason = (f"still on the sign-in host after one attempt "
                                   f"(url={self.page.url[:120]}) — not retrying. "
                                   f"page said: {self.redact(body)[:200]}")
            return False

        self.log(f"authenticated; now at {self.page.url}")
        return True

    async def _submit_auth_step(self) -> None:
        for selector in ["button[type='submit']", "input[type='submit']",
                         "role=button[name=/sign in|log in|next|continue/i]", "button"]:
            try:
                loc = self.page.locator(selector).first
                if await loc.count() and await loc.is_visible():
                    await loc.click()
                    return
            except Exception:
                continue
        await self.page.keyboard.press("Enter")

    async def reconnoitre(self, label: str) -> dict[str, Any]:
        """Record what is really on the page: screenshot, DOM, and an element inventory."""
        await self.shot(f"recon-{label}")
        await self.snapshot(f"recon-{label}")
        try:
            data = await self.page.evaluate("() => window.__mayaPicker.inventory(80)")
        except Exception as exc:
            data = {"error": str(exc)[:200]}
        self.recon[label] = data
        (self.outdir / f"inventory-{label}.json").write_text(
            self.redact(json.dumps(data, indent=2, ensure_ascii=False)))
        count = data.get("count", 0)
        self.log(f"inventory[{label}]: {count} interactive elements at {self.page.url[:90]}")
        return data

    # ── capture phases ──────────────────────────────────────────────────────
    async def run_capture(self) -> bool:
        await self._phase("app_shell", "You are signed in and looking at the application shell.")
        await self.prompt("Open the EQUIPMENT SEARCH page.")
        if self.mode == "self-test":
            await self.page.evaluate("() => location.hash = '#/search'")
            await self.page.wait_for_selector("#page-search", state="visible")
        await self._phase("search", "Equipment search page.")

        missing = self.args.missing_serial
        await self.prompt(f"Search for {missing} — a serial with NO results — and wait for the "
                          "empty state.")
        if self.mode != "live":
            await self._run_search_in_page(missing)
        await self._phase("empty", f"No-result state for {missing}.")

        await self.prompt(f"Now search for {self.args.serial} (a serial that EXISTS).")
        if self.mode != "live":
            await self._run_search_in_page(self.args.serial)
        await self._phase("results", f"Results for {self.args.serial}.")

        await self.prompt("Open the first result so the equipment DETAIL page is showing.")
        if self.mode == "self-test":
            await self.page.click('[data-maya-fixture="search.first_result"]')
            await self.page.wait_for_selector("#page-detail", state="visible")
        await self._phase("detail", "Equipment detail page.")
        return True

    async def _phase(self, phase: str, banner: str) -> None:
        print(f"\n─── {phase.upper()} ─── {banner}")
        await self.shot(f"phase-{phase}")
        await self.snapshot(f"phase-{phase}")
        for target in [t for t in TARGETS if t.phase == phase]:
            record = await self.pick(target)
            if target.key == "detail.spec_rows" and record.selector:
                await self._derive_spec_cell(record.selector)

    async def _derive_spec_cell(self, row_selector: str) -> None:
        """Read the cell element out of the row the operator picked.

        This is derived from the live DOM, not assumed: if the table is built from
        <td>, we see <td>; if it is divs, we see divs. If the row has fewer than two
        children there is nothing to derive and the entry stays TODO_CAPTURE.
        """
        try:
            info = await self.page.evaluate(
                """(sel) => {
                     const row = document.querySelector(sel);
                     if (!row) return null;
                     const kids = [...row.children];
                     if (kids.length < 2) return null;
                     const tags = [...new Set(kids.map(k => k.tagName.toLowerCase()))];
                     return { tag: tags.length === 1 ? tags[0] : null, count: kids.length,
                              sample: kids.slice(0, 2).map(k => (k.innerText || '').trim().slice(0, 40)) };
                   }""", row_selector)
        except Exception as exc:
            info = None
            self.log(f"could not derive the spec cell selector: {exc}")

        if not info or not info.get("tag"):
            self.records["detail.spec_cell"] = CaptureRecord(
                name="detail.spec_cell", status="TODO_CAPTURE", confidence="unverified",
                captured_at=now_iso(), url=self.page.url,
                notes=["could not derive a single cell element from the captured row"])
            print("    ✗ detail.spec_cell — TODO_CAPTURE (row children are not uniform)")
            return

        self.records["detail.spec_cell"] = CaptureRecord(
            name="detail.spec_cell", selector=info["tag"], strategy="derived-from-row",
            element_text=" | ".join(info.get("sample") or []), url=self.page.url,
            captured_at=now_iso(), confidence="verified", match_count=info["count"],
            notes=[f"child element of {row_selector}"])
        print(f"    ✓ derived detail.spec_cell: {info['tag']} ({info['count']} per row)")

    async def _run_search_in_page(self, serial: str) -> None:
        """Drive the search using only what has been captured so far."""
        input_sel = self._selector("search.input")
        if not input_sel:
            return
        await self.page.fill(input_sel, serial)
        submit_sel = self._selector("search.submit")
        if submit_sel:
            await self.page.click(submit_sel)
        else:
            await self.page.press(input_sel, "Enter")
        await self.page.wait_for_timeout(1200)

    def _selector(self, key: str) -> str | None:
        rec = self.records.get(key)
        return rec.selector if rec and rec.selector and rec.status != "TODO_CAPTURE" else None

    # ── verification + one real search ──────────────────────────────────────
    def selector_map(self) -> dict[str, str]:
        return {k: r.selector for k, r in self.records.items()
                if r.selector and r.status != "TODO_CAPTURE"}

    async def verify(self) -> dict[str, Any]:
        print("\n─── VERIFICATION ───")
        selectors = self.selector_map()
        results: list[dict[str, Any]] = []

        await self._goto_search()
        for check in (verify_serial_search_input, verify_search_button):
            res = await check(self.page, selectors)
            results.append(res.as_dict())
            print(f"  {'✓' if res.ok else '✗'} {res.name}: {res.detail}")

        await self._run_search_in_page(self.args.missing_serial)
        await self.page.wait_for_timeout(800)
        res = await verify_no_result_state(self.page, selectors)
        results.append(res.as_dict())
        print(f"  {'✓' if res.ok else '✗'} {res.name}: {res.detail}")
        await self.shot("verify-empty-state")

        await self._run_search_in_page(self.args.serial)
        await self.page.wait_for_timeout(800)
        res = await verify_result_container(self.page, selectors)
        results.append(res.as_dict())
        print(f"  {'✓' if res.ok else '✗'} {res.name}: {res.detail}")
        await self.shot("verify-results")

        passed = all(r["ok"] for r in results)
        return {"passed": passed, "checks": results}

    async def _goto_search(self) -> None:
        link = self._selector("nav.search_link")
        marker = self._selector("ready.search_page")
        try:
            if marker and await self.page.locator(marker).first.is_visible():
                return
        except Exception:
            pass
        if link:
            try:
                await self.page.click(link)
                if marker:
                    await self.page.wait_for_selector(marker, state="visible", timeout=10000)
            except Exception as exc:
                self.log(f"could not return to the search page: {exc}")

    async def real_search(self) -> dict[str, Any]:
        """One real serial-number search, driven only by captured selectors."""
        print(f"\n─── REAL SEARCH: {self.args.serial} ───")
        out: dict[str, Any] = {"serial": self.args.serial, "ok": False, "fields": {}}
        try:
            await self._goto_search()
            await self._run_search_in_page(self.args.serial)
            results_sel = self._selector("search.results")
            empty_sel = self._selector("search.no_results_marker")
            await self.page.wait_for_selector(f"{results_sel}, {empty_sel}" if empty_sel
                                              else results_sel, state="visible", timeout=20000)
            if empty_sel and await self.page.locator(empty_sel).is_visible():
                out["outcome"] = "SERIAL_NOT_FOUND"
                out["evidence"] = f"empty-state marker matched: {empty_sel}"
                await self.shot("real-search-empty")
                return out

            first = self._selector("search.first_result")
            if first:
                await self.page.click(first)
                detail_marker = self._selector("ready.detail_page")
                if detail_marker:
                    await self.page.wait_for_selector(detail_marker, state="visible", timeout=15000)

            for key in ("detail.equipment_model", "detail.equipment_type",
                        "detail.build_date", "detail.engine_family"):
                sel = self._selector(key)
                if not sel:
                    out["fields"][key] = None
                    continue
                try:
                    out["fields"][key] = (await self.page.locator(sel).first.inner_text()).strip()
                except Exception as exc:
                    out["fields"][key] = f"<unreadable: {str(exc)[:60]}>"

            out["ok"] = bool(out["fields"].get("detail.equipment_model"))
            out["outcome"] = "FOUND" if out["ok"] else "EXTRACTION_ERROR"
            out["url"] = self.page.url
            await self.shot("real-search-detail")
            await self.snapshot("real-search-detail")
        except Exception as exc:
            out["outcome"] = "FAILED"
            out["error"] = str(exc)[:240]
            await self.shot("real-search-failure")
        for key, value in out["fields"].items():
            print(f"  {key:28s} = {value}")
        return out

    # ── output ──────────────────────────────────────────────────────────────
    def unresolved(self) -> list[str]:
        return [k for k, r in self.records.items() if r.status == "TODO_CAPTURE"]

    def missing_required(self) -> list[str]:
        required = {t.key for t in TARGETS if t.required}
        return sorted(k for k in required
                      if k not in self.records or self.records[k].status == "TODO_CAPTURE")

    def write(self, verification: dict[str, Any] | None,
              search: dict[str, Any] | None, *, complete: bool = False) -> tuple[Path, Path]:
        payload = {
            "schema_version": "1.0",
            "source_id": "cat_sis",
            "base_url": self.base,
            "capture_profile": {"live": "LIVE_SIS", "auto-login": "LIVE_SIS_AUTOLOGIN",
                                "self-test": "SELF_TEST_FIXTURE",
                                "recon": "UNAUTHENTICATED_RECON"}[self.mode],
            "selector_version": f"{self.mode}-{self.run_id}",
            "captured_at": now_iso(),
            "captured_by": os.environ.get("MAYA_OPERATOR", os.environ.get("USER", "unknown")),
            "browser": "chromium",
            "selectors": {k: r.as_dict() for k, r in self.records.items()},
            "xhr_endpoints": self.xhr[:20],
            "steps": self.steps,
            "unresolved": self.unresolved(),
            "missing_required": self.missing_required(),
            "reconnaissance": self.recon,
            "verification": verification,
            "real_search": search,
            "stopped_reason": self.stopped_reason,
        }
        report = self.outdir / "report.json"
        report.write_text(self.redact(json.dumps(payload, indent=2, ensure_ascii=False)))

        # Fixture and recon output must never be mistaken for a real SIS contract.
        captured_anything = any(r.status != "TODO_CAPTURE" and r.selector
                                for r in self.records.values())
        if self.mode not in ("live", "auto-login") or not captured_anything:
            # A run that captured nothing has no business creating a contract file.
            target = self.outdir / f"sis_selectors.{self.mode}.json"
        else:
            target = ROOT / "config" / "sis_selectors.json"
            # A stopped or partial run must not overwrite a contract that already
            # works. The partial result goes to the run folder instead.
            if target.exists() and not complete:
                existing_ok = not json.loads(target.read_text()).get("missing_required", ["?"])
                if existing_ok:
                    target = self.outdir / "sis_selectors.partial.json"
                    print(f"  ! keeping the existing contract; partial run written to "
                          f"{target.relative_to(ROOT)}")
        target.write_text(self.redact(json.dumps(payload, indent=2, ensure_ascii=False)))
        return target, report


async def run(args: argparse.Namespace) -> int:
    session = CaptureSession(args)
    print(f"\n╔═ SELECTOR CAPTURE · mode={session.mode} · {session.base}")
    print(f"╚═ artifacts → {session.outdir.relative_to(ROOT)}\n")
    try:
        await session.start()
    except NavigationFailed as exc:
        # A clean, actionable stop — not a traceback and a broken pipe.
        print(f"\n✗ CANNOT REACH {session.base}\n  {exc}")
        session.stopped_reason = f"navigation failed: {exc}"
        try:
            session.write(None, None)
        finally:
            await session.close()
        return 6
    verification = search = None
    try:
        if session.mode == "recon":
            # Reachability + what the entry page really contains. No credentials
            # are read, sent, or stored, and nothing is written to the contract.
            await session.reconnoitre("entry")
            blocker = await session.detect_blockers("entry")
            if blocker:
                session.stopped_reason = blocker
                print(f"\n  ! {blocker}")
            path, report = session.write(None, None)
            print(f"\n  recon written → {report.parent.relative_to(ROOT)}")
            return 5

        if not await session.authenticate():
            print(f"\n✗ STOPPED: {session.stopped_reason}")
            await session.reconnoitre("stopped")
            path, report = session.write(None, None)
            print(f"  artifacts: {report.parent.relative_to(ROOT)}")
            return 3

        if session.mode == "auto-login":
            # Authentication is automatable; deciding which element is the serial
            # box is not. We inventory the real DOM and hand it to a human rather
            # than inventing a selector.
            await session.reconnoitre("authenticated")
            path, report = session.write(None, None)
            print("\n✓ AUTHENTICATED — DOM inventory written for human confirmation.")
            print("  Selector capture still needs the headed `live` mode (a human picks).")
            print(f"  artifacts: {report.parent.relative_to(ROOT)}")
            return 4

        await session.run_capture()

        missing = session.missing_required()
        if missing:
            # Requirement 12: mark TODO_CAPTURE and STOP. No guessed substitutes.
            print(f"\n✗ STOPPED — required targets unresolved: {', '.join(missing)}")
            path, report = session.write(None, None)
            print(f"  wrote {path.relative_to(ROOT)} with TODO_CAPTURE entries")
            print(f"  artifacts: {report.parent.relative_to(ROOT)}")
            return 2

        verification = await session.verify()
        search = await session.real_search()
    finally:
        complete = bool(verification and verification.get("passed")
                        and search and search.get("ok"))
        path, report = session.write(verification, search, complete=complete)
        await session.close()

    print(f"\n  selectors → {path.relative_to(ROOT)}")
    print(f"  report    → {report.relative_to(ROOT)}")
    ok = bool(verification and verification["passed"] and search and search.get("ok"))
    print(f"\n{'✓ CAPTURE COMPLETE' if ok else '✗ CAPTURE INCOMPLETE'}"
          f" — verification {'passed' if verification and verification['passed'] else 'failed'}"
          f", real search {(search or {}).get('outcome', 'not run')}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["live", "auto-login", "self-test", "recon"],
                        default="live")
    parser.add_argument("--self-test", action="store_true", help="shorthand for --mode self-test")
    parser.add_argument("--auto-login", action="store_true", help="shorthand for --mode auto-login")
    parser.add_argument("--recon-only", action="store_true",
                        help="open the base URL unauthenticated and inventory it; no credentials")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--serial", default="SN123456", help="a serial that EXISTS in the source")
    parser.add_argument("--missing-serial", default="ZZZ00000",
                        help="a serial that does NOT exist, to capture the empty state")
    parser.add_argument("--secret-ref",
                        default=os.environ.get("MAYA_SIS_SECRET_REF", "env://MAYA_CAT_SIS"),
                        help="where the credentials live: vault://kv/maya/cat_sis or "
                             "env://MAYA_CAT_SIS. A value is never accepted on the command line.")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--pick-timeout-s", type=int, default=300)
    args = parser.parse_args()
    if args.self_test:
        args.mode = "self-test"
    if args.auto_login:
        args.mode = "auto-login"
    if args.recon_only:
        args.mode = "recon"
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
