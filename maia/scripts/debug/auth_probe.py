#!/usr/bin/env python3
"""
SIS2 AUTHENTICATION-FLOW PROBE — diagnosis only, changes nothing.
═══════════════════════════════════════════════════════════════════════════════
Opens the real SIS2 entry point in a visible browser, follows the Cat / Azure AD
B2C sign-in flow, and records every redirect, failed request, console error and
cookie along the way. With --wait-for-human it stops and lets a person sign in
(including MFA), then keeps recording.

It never reads, types, stores or prints a credential. Authorization codes and
tokens in URLs are redacted before anything is written or displayed.

    python scripts/debug/auth_probe.py --wait-for-human

Prints the AUTH_START / AUTH_REDIRECTS / FINAL_URL / FINAL_PAGE / ERROR /
SCREENSHOTS / ROOT_CAUSE / NEXT_ACTION block and writes auth-probe-report.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SIS = "https://sis2.cat.com/#/"
RETURN_PATH = "/login/authorization"
# Hosts this flow depends on; a block on any of them breaks sign-in.
DEPENDENCIES = ["https://signin.cat.com/", "https://aadcdn.msauth.net/",
                "https://login.microsoftonline.com/", f"https://sis2.cat.com{RETURN_PATH}"]
SENSITIVE_PARAMS = ("code", "id_token", "access_token", "refresh_token", "client-request-id")


def redact_url(url: str) -> str:
    """Strip anything token-shaped. An auth code is as good as a password."""
    for param in SENSITIVE_PARAMS:
        url = re.sub(rf"([?&]{param}=)[^&]+", r"\1<redacted>", url, flags=re.I)
    return url


def classify(hops: list[dict], final_url: str, failures: list[dict],
             error_params: dict, signed_in: bool) -> tuple[str, str]:
    """Map the evidence onto the five candidate causes."""
    hosts = [urlparse(h["url"]).netloc for h in hops]
    tls = [f for f in failures if "CERT" in (f.get("failure") or "").upper()]
    blocked = [f for f in failures
               if any(d in f["url"] for d in ("signin.cat.com", "msauth", "microsoftonline"))]
    final_host = urlparse(final_url).netloc

    if tls:
        return ("C) certificate/TLS trust",
                "The browser rejected a certificate in the chain — a TLS-inspecting proxy "
                "whose CA the browser does not trust. Install that CA in the browser's own "
                "authority list; do not disable certificate checking.")
    if blocked:
        return ("E) network/proxy configuration",
                f"{len(blocked)} request(s) to the identity hosts failed outright. A proxy or "
                "firewall is dropping them; allow-list signin.cat.com, *.msauth.net and "
                "login.microsoftonline.com.")
    if error_params:
        return ("B) the redirect back to SIS",
                f"The identity provider returned to SIS with an error: "
                f"{error_params.get('error', ['?'])[0]} — "
                f"{error_params.get('error_description', ['no description'])[0][:160]}")
    if not signed_in:
        return ("undetermined — sign-in was not completed in this run",
                "Re-run with --wait-for-human and complete the sign-in; the flow cannot be "
                "judged from the login page alone.")
    if "sis2.cat.com" in final_host:
        return ("no fault — authentication completed",
                "The flow returned to SIS2 and the session is established.")
    if hosts.count("signin.cat.com") > 2 and "signin.cat.com" in final_host:
        return ("D) cookies/session state",
                "The flow bounced back to the identity provider after signing in — a loop. "
                "The B2C session cookies (x-ms-cpim-*, SameSite=None; Secure) are not making "
                "the round trip: check third-party-cookie blocking, storage partitioning, or "
                "a non-persistent browser profile.")
    if "signin.cat.com" in final_host or "microsoftonline" in final_host:
        return ("A) Microsoft authentication",
                "The flow never left the identity provider: sign-in itself did not complete "
                "(credentials rejected, MFA not satisfied, or the account needs registration).")
    return (f"unclear — the flow ended on {final_host}",
            "Inspect the final screenshot and auth-probe-report.json.")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="artifacts/auth-probe")
    ap.add_argument("--wait-for-human", action="store_true",
                    help="pause at the login page so a person can sign in, MFA included")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--chromium", default=None, help="path to a Chromium binary")
    ap.add_argument("--timeout-ms", type=int, default=45000)
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) / stamp
    out.mkdir(parents=True, exist_ok=True)

    from playwright.async_api import async_playwright

    hops: list[dict] = []
    failures: list[dict] = []
    console: list[dict] = []
    http_errors: list[dict] = []
    shots: list[str] = []
    seen_idp = False

    async with async_playwright() as pw:
        launch = {"headless": args.headless,
                  "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if args.chromium:
            launch["executable_path"] = args.chromium
        browser = await pw.chromium.launch(**launch)
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()

        def on_nav(frame) -> None:
            if frame != page.main_frame:
                return
            url = redact_url(frame.url)
            hops.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "url": url})
            print(f"  → {url[:150]}")

        page.on("framenavigated", on_nav)
        page.on("requestfailed", lambda r: failures.append(
            {"url": redact_url(r.url)[:200], "method": r.method,
             "failure": (r.failure or "")[:140], "type": r.resource_type}))
        page.on("console", lambda m: console.append({"type": m.type, "text": m.text[:200]})
                if m.type == "error" else None)
        page.on("response", lambda r: http_errors.append(
            {"url": redact_url(r.url).split("?")[0][:140], "status": r.status})
                if r.status >= 400 else None)

        print(f"AUTH_START: {SIS}\n")
        error = None
        try:
            resp = await page.goto(SIS, wait_until="domcontentloaded", timeout=args.timeout_ms)
            first_status = resp.status if resp else None
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            await page.wait_for_timeout(2500)
            shot = out / "01-cat-login.png"
            await page.screenshot(path=str(shot))
            shots.append(str(shot))

            if "signin.cat.com" in page.url or "microsoftonline" in page.url:
                seen_idp = True
                shot = out / "02-microsoft-login.png"
                await page.screenshot(path=str(shot))
                shots.append(str(shot))
        except Exception as exc:
            first_status = None
            error = f"{type(exc).__name__}: {str(exc).split('Call log')[0].strip()[:300]}"
            print(f"\n  ! navigation failed: {error}")

        signed_in = False
        if args.wait_for_human and not error:
            print("\n" + "=" * 72)
            print("  SIGN IN IN THE BROWSER WINDOW NOW — username, password, and any MFA.")
            print("  This script does not read, type, store or print your credentials.")
            print("  It is still recording every redirect while you do it.")
            print("=" * 72)
            await asyncio.to_thread(input, "  Press Enter once you are fully signed in… ")
            signed_in = True
            try:
                await page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)
            shot = out / "03-after-auth.png"
            await page.screenshot(path=str(shot))
            shots.append(str(shot))

        final_url = redact_url(page.url)
        try:
            title = await page.title()
            body = (await page.inner_text("body"))[:600].replace("\n", " | ")
        except Exception:
            title, body = "<unavailable>", ""

        query = parse_qs(urlparse(page.url).query)
        error_params = {k: v for k, v in query.items() if k.startswith("error")}

        cookies_by_domain: dict[str, list] = {}
        for c in await ctx.cookies():                    # names and flags only, never values
            cookies_by_domain.setdefault(c["domain"], []).append(
                {"name": c["name"], "secure": c.get("secure"),
                 "httpOnly": c.get("httpOnly"), "sameSite": c.get("sameSite")})

        reach: dict[str, object] = {}
        for dep in DEPENDENCIES:
            try:
                r = await ctx.request.get(dep, timeout=15000)
                reach[dep] = r.status
            except Exception as exc:
                reach[dep] = f"FAILED: {type(exc).__name__}: {str(exc)[:90]}"

        shot = out / "04-final.png"
        try:
            await page.screenshot(path=str(shot))
            shots.append(str(shot))
        except Exception:
            pass

        root_cause, next_action = classify(hops, page.url, failures, error_params, signed_in)

        report = {
            "auth_start": SIS, "first_status": first_status,
            "redirects": hops, "reached_identity_provider": seen_idp,
            "final_url": final_url, "final_title": title, "final_body_excerpt": body,
            "error": error, "error_params": error_params,
            "failed_requests": failures, "http_errors": http_errors[:20],
            "console_errors": console[:20], "cookies_by_domain": cookies_by_domain,
            "dependency_reachability": reach, "screenshots": shots,
            "signed_in_by_human": signed_in,
            "root_cause": root_cause, "next_action": next_action,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        (out / "auth-probe-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        await browser.close()

    print("\n" + "═" * 72)
    print(f"AUTH_START:     {SIS}")
    print(f"AUTH_REDIRECTS: {len(hops)}")
    for hop in hops:
        print(f"                {hop['url'][:130]}")
    print(f"FINAL_URL:      {final_url[:130]}")
    print(f"FINAL_PAGE:     {title!r}")
    print(f"ERROR:          {error or (error_params or 'none')}")
    print(f"SCREENSHOTS:    {len(shots)}")
    for s in shots:
        print(f"                {s}")
    print(f"ROOT_CAUSE:     {root_cause}")
    print(f"NEXT_ACTION:    {next_action}")
    print("═" * 72)
    print(f"full report → {out / 'auth-probe-report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
