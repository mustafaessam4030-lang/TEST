#!/usr/bin/env python3
"""
Pre-flight doctor for the REAL SIS run.

Checks every precondition on THIS machine and prints what is missing and how to
fix it. Reads no secret values and prints none — a credential check reports only
whether the reference resolves.

    python scripts/e2e/doctor.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "automation"))

SIS = "https://sis2.cat.com/#/"
OK, WARN, FAIL = "PASS", "WARN", "FAIL"
results: list[tuple[str, str, str, str]] = []   # status, name, detail, remedy


def add(status: str, name: str, detail: str, remedy: str = "") -> None:
    results.append((status, name, detail, remedy))


def check_playwright() -> None:
    try:
        import playwright  # noqa: F401
        from playwright.async_api import async_playwright  # noqa: F401
        add(OK, "playwright installed", "python package present")
    except ImportError:
        add(FAIL, "playwright installed", "not importable",
            "pip install playwright && python -m playwright install chromium")


def check_display() -> None:
    if os.environ.get("DISPLAY") or sys.platform in ("darwin", "win32"):
        add(OK, "display available", os.environ.get("DISPLAY", sys.platform))
    else:
        add(FAIL, "display available", "no DISPLAY — a headed browser has nowhere to draw",
            "run this on a desktop machine; selector capture needs a human at a screen")


def check_credentials() -> None:
    from app.core.errors import AutomationError
    from app.core.secrets import resolve_secret
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "sources" / "cat_sis.yaml").read_text(encoding="utf-8"))
    ref = os.environ.get("MAIA_SIS_SECRET_REF") or (cfg.get("auth") or {}).get("secret_ref", "")
    try:
        creds = resolve_secret(ref)          # the value is never printed or returned upward
        add(OK, "SIS credentials", f"reference {ref} resolves "
                                   f"(username {len(creds['username'])} chars, password hidden)")
    except FileNotFoundError as exc:
        add(FAIL, "SIS credentials", str(exc)[:120],
            "create login.txt next to START-MAIA.bat (Windows may have named it login.txt.txt "
            "— any name starting with 'login' is accepted by the launcher)")
    except AutomationError as exc:
        add(FAIL, "SIS credentials", f"{ref}: {exc.message}",
            "export MAIA_SIS_SECRET_REF=vault://kv/maia/cat_sis (with VAULT_ADDR/VAULT_TOKEN), "
            "or env://MAIA_CAT_SIS with MAIA_CAT_SIS_USERNAME / _PASSWORD set")


def check_selectors() -> None:
    from app.adapters import selector_store as store

    path = ROOT / "config" / "sis_selectors.json"
    if not path.exists():
        add(FAIL, "SIS selector contract", "config/sis_selectors.json does not exist",
            "make capture SERIAL=<a serial that exists in SIS>   (see docs/11)")
        return
    payload = store.load(path)
    verified = store.verified_selectors(payload)
    missing = store.missing_required(payload)
    profile = payload.get("capture_profile", "?")
    if profile not in ("LIVE_SIS", "LIVE_SIS_AUTOLOGIN"):
        add(FAIL, "SIS selector contract",
            f"capture_profile is {profile} — not captured from the real site",
            "re-run the capture against https://sis2.cat.com")
    elif missing:
        add(FAIL, "SIS selector contract",
            f"{len(verified)} verified, missing required: {', '.join(missing)}",
            "re-run the capture and pick the missing targets")
    else:
        add(OK, "SIS selector contract",
            f"{len(verified)} verified selectors, version {payload.get('selector_version')}")


def check_switch() -> None:
    on = os.environ.get("MAIA_ALLOW_LIVE_AUTOMATION", "").lower() == "true"
    add(OK if on else WARN, "live automation switch",
        "enabled" if on else "off (make e2e turns it on for you)",
        "" if on else "export MAIA_ALLOW_LIVE_AUTOMATION=true for a manual run")


def check_local_store() -> None:
    """A lookup now FAILS if its result cannot be saved, so the store is a
    precondition, not an afterthought. Check the folder is really writable —
    existing is not the same as writable on Windows or on a mounted share."""
    from app.config import get_settings

    settings = get_settings()
    store = Path(settings.local_store_dir)
    if not store.is_absolute():
        store = ROOT / store
    add(OK, "store implementation",
        f"{settings.repository} → {store}",
        "" if settings.repository in ("local_json", "snowflake")
        else "MAIA_REPOSITORY=local_json keeps results on disk")
    if settings.repository != "local_json":
        return
    try:
        store.mkdir(parents=True, exist_ok=True)
        probe = store / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        add(FAIL, "store is writable", str(exc)[:110],
            f"create {store} and give this user write access, or set "
            f"MAIA_LOCAL_STORE_DIR to a folder you can write to")
        return

    free_mb = shutil.disk_usage(store).free // (1024 * 1024)
    # A lookup writes a JSON, a TXT and three screenshots — well under a
    # megabyte. 50 MB is "something is wrong", not "we are nearly out".
    add(OK if free_mb > 50 else FAIL, "store is writable",
        f"writable, {free_mb} MB free",
        "" if free_mb > 50 else "free disk space: a failed write now fails the lookup")

    saved = sorted(store.glob("*_run_*.json"))
    add(OK, "results already saved",
        f"{len(saved)} lookup(s) in the store"
        + (f", newest {saved[-1].name}" if saved else " (none yet — expected before the first run)"))


async def check_browser_reach() -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        add(FAIL, "browser reaches SIS", "playwright missing", "install it first")
        return
    launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if os.environ.get("MAIA_CHROMIUM_PATH"):
        launch["executable_path"] = os.environ["MAIA_CHROMIUM_PATH"]
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**launch)
            page = await (await browser.new_context()).new_page()
            try:
                resp = await page.goto(SIS, wait_until="domcontentloaded", timeout=40000)
                await page.wait_for_timeout(2500)
                host = page.url.split("/")[2] if "//" in page.url else page.url
                add(OK, "browser reaches SIS",
                    f"HTTP {resp.status if resp else '?'} → {host} · title {await page.title()!r}")
            except Exception as exc:
                text = str(exc)
                if "ERR_CERT" in text:
                    add(FAIL, "browser reaches SIS", "certificate not trusted by the browser",
                        "a TLS-inspecting proxy is in the path: add its CA to the browser's own "
                        "Authorities list. Never disable certificate checks.")
                else:
                    add(FAIL, "browser reaches SIS", text.split("Call log")[0].strip()[:120],
                        "check VPN / network access to sis2.cat.com")
            finally:
                await browser.close()
    except Exception as exc:
        add(FAIL, "browser launches", str(exc)[:140],
            "python -m playwright install chromium, or set MAIA_CHROMIUM_PATH")


def main() -> int:
    print("\n  MAIA — real-SIS readiness\n" + "  " + "─" * 66)
    check_playwright()
    check_display()
    check_credentials()
    check_selectors()
    check_switch()
    check_local_store()
    asyncio.run(check_browser_reach())

    icon = {OK: "✓", WARN: "!", FAIL: "✗"}
    for status, name, detail, remedy in results:
        print(f"  {icon[status]} {name:<26} {detail}")
        if remedy and status != OK:
            print(f"      → {remedy}")

    blockers = [r for r in results if r[0] == FAIL]
    print("  " + "─" * 66)
    if blockers:
        print(f"  NOT READY — {len(blockers)} blocker(s): "
              f"{', '.join(b[1] for b in blockers)}")
        print("  Fix those, re-run this doctor, then: make capture … && make e2e")
    else:
        print("  READY — run:  make sis-lookup SERIAL=<real serial>")
        print("             or:  make capture SERIAL=<real serial>   then   make e2e")
    print()
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
