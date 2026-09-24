"""Get a DSP API token from a saved Cat.com browser session.

DSP (dsp.cat.com) is an Angular app that signs in through Cat's Azure AD B2C
(signin.cat.com) and calls its backend with ``Authorization: Bearer <token>``
and ``x-dealer-code: <code>`` headers. We keep a persistent Chromium profile so
the sign-in (and its refresh token) survives between runs, open the portal,
and take the token from the first backend call the portal makes that succeeds.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Response, sync_playwright

log = logging.getLogger(__name__)

PORTAL_URL = os.environ.get("DSP_PORTAL_URL", "https://dsp.cat.com/")
LOGIN_HOSTS = ("signin.cat.com", "fedlogin.cat.com", "login.cat.com")


class LoginRequired(RuntimeError):
    """The saved session has expired; run `python main.py login` again."""


@dataclass(frozen=True)
class DspSession:
    token: str
    dealer_code: str


def get_session(
    profile_dir: Path,
    api_url: str,
    *,
    interactive: bool = False,
    timeout_s: int = 120,
) -> DspSession:
    """Open DSP with the saved profile and capture a working API token.

    ``interactive=True`` shows the browser and waits (up to ``timeout_s``) for a
    person to finish signing in, MFA included. Otherwise the browser is headless
    and a sign-in page means the session has expired.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    captured: dict[str, str] = {}

    def on_response(resp: Response) -> None:
        if captured or not resp.url.startswith(api_url) or not resp.ok:
            return
        headers = resp.request.headers
        auth = headers.get("authorization", "")
        if auth.startswith("Bearer ") and len(auth) > len("Bearer ") + 20:
            captured["token"] = auth[len("Bearer "):]
            captured["dealer_code"] = headers.get("x-dealer-code", "")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=not interactive,
            accept_downloads=False,
            executable_path=os.environ.get("DSP_CHROMIUM_PATH") or None,
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("response", on_response)
            page.goto(PORTAL_URL, wait_until="domcontentloaded")
            if interactive:
                print(
                    "\nA browser window has opened. Sign in to DSP with your "
                    f"Cat.com account (MFA included). Waiting up to {timeout_s}s...\n"
                )

            deadline = time.monotonic() + timeout_s
            while not captured and time.monotonic() < deadline:
                page.wait_for_timeout(1000)
                if not interactive and any(h in page.url for h in LOGIN_HOSTS):
                    raise LoginRequired(
                        "DSP redirected to the Cat.com sign-in page: the saved "
                        "session has expired. Run `python main.py login` on a "
                        "machine with a screen, then re-run."
                    )

            if not captured:
                if any(h in page.url for h in LOGIN_HOSTS):
                    raise LoginRequired("Sign-in was not completed in time.")
                raise RuntimeError(
                    f"DSP loaded ({page.url}) but made no authenticated API call "
                    f"within {timeout_s}s."
                )

            dealer_code = captured["dealer_code"] or _dealer_from_storage(page)
            if not dealer_code:
                raise RuntimeError("Could not determine the DSP dealer code.")
            log.info("DSP session ready for dealer %s", dealer_code)
            return DspSession(token=captured["token"], dealer_code=dealer_code)
        finally:
            ctx.close()


def _dealer_from_storage(page) -> str:
    """The portal keeps the signed-in dealer in sessionStorage['dealer']."""
    raw = page.evaluate("() => sessionStorage.getItem('dealer')")
    try:
        return (json.loads(raw) or {}).get("dealerCode", "") if raw else ""
    except (TypeError, ValueError):
        return ""
