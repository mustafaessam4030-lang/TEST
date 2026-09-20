#!/usr/bin/env python3
"""Capture SIS2 selectors from a real authenticated session — do not guess them.

Opens a visible browser, lets an engineer sign in by hand (SSO, MFA, whatever
CWS asks for), then records candidate selectors for each contract slot and
writes a draft config next to the live one for review.

    python scripts/capture_selectors.py --serial CAT0336LKBW00123

Nothing here writes to the live config automatically: a human confirms the
contract and bumps `selector_version`. That review is exactly why the adapter
refuses TODO_CAPTURE placeholders instead of improvising.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sources" / "cat_sis.yaml"

CANDIDATE_JS = """
() => {
  const uniq = (el) => {
    if (!el) return null;
    if (el.id) return `#${CSS.escape(el.id)}`;
    for (const attr of ['data-testid','data-test','data-cy','name','formcontrolname','aria-label']) {
      const v = el.getAttribute?.(attr);
      if (v) return `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(v)}"]`;
    }
    const cls = (el.className || '').toString().trim().split(/\\s+/)
      .filter(c => c && !/^ng-|^cdk-|active|open|selected/.test(c)).slice(0, 2);
    return cls.length ? `${el.tagName.toLowerCase()}.${cls.join('.')}` : el.tagName.toLowerCase();
  };
  const grab = (sel) => [...document.querySelectorAll(sel)].slice(0, 8).map(el => ({
    selector: uniq(el),
    text: (el.innerText || el.value || '').trim().slice(0, 80),
    visible: !!(el.offsetWidth || el.offsetHeight),
  }));
  return {
    url: location.href,
    hash: location.hash,
    inputs: grab('input:not([type=hidden]), textarea'),
    buttons: grab('button, [role=button], input[type=submit]'),
    tables: grab('table, [role=table], [role=grid]'),
    headings: grab('h1, h2, h3, [role=heading]'),
    lists: grab('[role=list], ul[class], .results, [class*=result]'),
    emptyStates: grab('[class*=empty], [class*=no-result], [class*=notfound]'),
  };
}
"""


async def main() -> int:
    from playwright.async_api import async_playwright

    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True, help="A serial you know exists in SIS")
    parser.add_argument("--missing-serial", default="ZZZ00000",
                        help="A serial you know does NOT exist, to capture the empty state")
    parser.add_argument("--base", default="https://sis2.cat.com/#/")
    args = parser.parse_args()

    captures: dict[str, object] = {"captured_at": datetime.now(timezone.utc).isoformat(),
                                   "base_url": args.base}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)  # a human signs in here
        page = await (await browser.new_context()).new_page()
        await page.goto(args.base)

        input("\n[1/4] Sign in to SIS2 in the browser window, then press Enter…")
        captures["app_shell"] = await page.evaluate(CANDIDATE_JS)

        input("[2/4] Navigate to the serial-number search screen, then press Enter…")
        captures["search_page"] = await page.evaluate(CANDIDATE_JS)

        input(f"[3/4] Search for {args.missing_serial} (a serial with NO results), "
              "wait for the empty state, then press Enter…")
        captures["empty_state"] = await page.evaluate(CANDIDATE_JS)

        input(f"[4/4] Search for {args.serial} and open its detail page, then press Enter…")
        captures["detail_page"] = await page.evaluate(CANDIDATE_JS)
        # The SPA's own JSON is the better extraction target — note the endpoints.
        captures["note"] = ("Check DevTools → Network → XHR for the JSON endpoints that "
                            "back this page; prefer them over DOM text in extraction.xhr_url_patterns.")

        await browser.close()

    out = ROOT / "config" / "sources" / "cat_sis.captured.json"
    out.write_text(json.dumps(captures, indent=2, ensure_ascii=False))
    print(f"\nWrote {out}")
    print(f"Next: fill the TODO_CAPTURE slots in {CONFIG.name}, bump selector_version,\n"
          "      then run POST /v1/admin/selectors/validate in CI on a schedule.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
