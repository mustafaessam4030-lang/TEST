"""Authentication using the application's normal login.

Modes (``AUTH_MODE``):

* ``browser_session`` - log in through the real login form with Playwright,
  then hand the session cookies (in memory only) to the HTTP client. This is
  exactly what the web application itself does before calling its backend.
* ``bearer_token``   - a token issued to the service account, supplied via
  ``WEBSITE_API_TOKEN`` (environment / secrets manager).
* ``none``           - no authentication (test fixtures, open intranet apps).

Credentials and cookies are never written to disk or logs.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.config import Settings
from app.diagnostics import Diagnostics
from app.errors import AuthenticationError, ConfigurationError, PageStructureError
from app.logger import register_secret
from app.source_config import LoginConfig

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = logging.getLogger(__name__)


@dataclass
class AuthContext:
    """Session material, held in memory only."""

    cookies: list[dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:  # never print secrets
        return f"AuthContext(cookies={len(self.cookies)}, headers={sorted(self.headers)})"


class Authenticator(ABC):
    @abstractmethod
    async def authenticate(self) -> AuthContext: ...


class NoAuthenticator(Authenticator):
    async def authenticate(self) -> AuthContext:
        return AuthContext()


class BearerTokenAuthenticator(Authenticator):
    def __init__(self, settings: Settings) -> None:
        if settings.website_api_token is None:
            raise ConfigurationError("AUTH_MODE=bearer_token requires WEBSITE_API_TOKEN")
        self._token = settings.website_api_token

    async def authenticate(self) -> AuthContext:
        return AuthContext(headers={"Authorization": f"Bearer {self._token.get_secret_value()}"})


async def login_with_form(page: Page, settings: Settings, login: LoginConfig, diagnostics: Diagnostics) -> None:
    """Fill and submit the normal login form using accessible labels and roles."""
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    if not settings.website_username or settings.website_password is None:
        raise ConfigurationError("AUTH_MODE=browser_session requires WEBSITE_USERNAME and WEBSITE_PASSWORD")
    if not (login.success_url_pattern or login.success_text):
        raise ConfigurationError("auth.success_url_pattern or auth.success_text must be configured")

    base = settings.require_base_url()
    try:
        await page.goto(base + login.login_path, wait_until="domcontentloaded")
        await page.get_by_label(login.username_label, exact=True).fill(settings.website_username)
        await page.get_by_label(login.password_label, exact=True).fill(
            settings.website_password.get_secret_value()
        )
        submit = page.get_by_role("button", name=login.submit_button_name, exact=True)
        await submit.click()
    except PlaywrightTimeout as exc:
        await diagnostics.capture(page, "login_form", exc)
        raise PageStructureError(f"Login form not found or not usable: {exc.message.splitlines()[0]}") from exc

    try:
        if login.success_url_pattern:
            await page.wait_for_url(re.compile(login.success_url_pattern))
        else:
            await page.get_by_text(login.success_text, exact=False).first.wait_for(state="visible")
    except PlaywrightTimeout as exc:
        await diagnostics.capture(page, "login_result", exc)
        raise AuthenticationError(
            "Login did not succeed (credentials rejected, MFA/SSO prompt, or success condition changed)"
        ) from exc
    logger.info("Authentication successful")


class BrowserSessionAuthenticator(Authenticator):
    """Log in with a short-lived browser, keep only the resulting cookies in memory."""

    def __init__(self, settings: Settings, login: LoginConfig | None, diagnostics: Diagnostics) -> None:
        if login is None:
            raise ConfigurationError("AUTH_MODE=browser_session requires the 'auth' section in source config")
        self.settings = settings
        self.login = login
        self.diagnostics = diagnostics

    async def authenticate(self) -> AuthContext:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=self.settings.browser_headless)
                try:
                    context = await browser.new_context()
                    context.set_default_timeout(self.settings.browser_timeout_ms)
                    page = await context.new_page()
                    await login_with_form(page, self.settings, self.login, self.diagnostics)
                    cookies = [dict(c) for c in await context.cookies()]
                finally:
                    await browser.close()
        except PlaywrightError as exc:
            raise AuthenticationError(f"Browser login failed: {type(exc).__name__}") from exc
        for cookie in cookies:
            register_secret(cookie.get("value"))
        return AuthContext(cookies=cookies)


def build_authenticator(settings: Settings, login: LoginConfig | None, diagnostics: Diagnostics) -> Authenticator:
    if settings.auth_mode == "bearer_token":
        return BearerTokenAuthenticator(settings)
    if settings.auth_mode == "browser_session":
        return BrowserSessionAuthenticator(settings, login, diagnostics)
    return NoAuthenticator()
