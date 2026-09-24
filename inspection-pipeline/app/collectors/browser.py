"""Playwright fallback collector: normal browser automation of the web UI.

* One browser session per run (login, search, paginate, extract, close).
* Elements are located by accessible role / label / name taken from the
  ``browser`` section of the source configuration - never by generated CSS.
* Table cells are read by column header text, so a column re-order in the UI
  does not break extraction; a renamed/removed column fails loudly.
* No stealth, fingerprint or anti-bot techniques: a stock Chromium launch.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from app.auth.session import login_with_form
from app.collectors.base import CollectionResult, InspectionCollector
from app.config import Settings
from app.diagnostics import Diagnostics
from app.errors import (
    AuthenticationError,
    ConfigurationError,
    PageStructureError,
    PaginationError,
    PipelineError,
    SourceUnavailableError,
)
from app.models import SourceRecord
from app.services.normalization import LINKS_KEY
from app.source_config import BrowserSource, LoginConfig

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page

logger = logging.getLogger(__name__)

# Reads a table or ARIA grid in one round trip. Semantic selectors only:
# ARIA roles first, native table elements as their implicit equivalent.
_EXTRACT_TABLE_JS = """
(table) => {
  const text = el => (el.innerText ?? el.textContent ?? '').replace(/\\s+/g, ' ').trim();
  const own = (row, sel) => Array.from(row.querySelectorAll(sel))
      .filter(c => c.closest('[role=row], tr') === row);
  let headers = null;
  const rows = [];
  for (const row of table.querySelectorAll('[role=row], tr')) {
    if (row.closest('[role=table], [role=grid], [role=treegrid], table') !== table) continue;
    const heads = own(row, '[role=columnheader], th:not([scope=row])');
    const cells = own(row, '[role=cell], [role=gridcell], [role=rowheader], td, th[scope=row]');
    if (heads.length && !cells.length) { headers = heads.map(text); continue; }
    if (!cells.length) continue;
    const rec = {}; const links = {};
    cells.forEach((cell, i) => {
      const h = (headers && headers[i]) || `column_${i + 1}`;
      rec[h] = text(cell);
      const anchors = Array.from(cell.querySelectorAll('a[href]')).map(a => ({
        name: text(a) || a.getAttribute('download') || null, url: a.href }));
      if (anchors.length) links[h] = anchors;
    });
    rec['__LINKS__'] = links;
    rows.push(rec);
  }
  return { headers, rows };
}
""".replace("__LINKS__", LINKS_KEY)


class PlaywrightInspectionCollector(InspectionCollector):
    collector_type = "playwright"
    lookup_mode = "key"

    def __init__(
        self, settings: Settings, source: BrowserSource, login: LoginConfig | None, diagnostics: Diagnostics
    ) -> None:
        self.settings = settings
        self.source = source
        self.field_mapping = source.fields
        self.login = login
        self.diagnostics = diagnostics

    async def collect(
        self, serial_number: str | None = None, inspection_number: str | None = None
    ) -> CollectionResult:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        from playwright.async_api import async_playwright

        base = self.settings.require_base_url()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.settings.browser_headless)
            page: Page | None = None
            stage = "startup"
            try:
                context = await browser.new_context()
                context.set_default_timeout(self.settings.browser_timeout_ms)
                page = await context.new_page()
                if self.settings.auth_mode == "browser_session":
                    stage = "login"
                    if self.login is None:
                        raise ConfigurationError("AUTH_MODE=browser_session requires the 'auth' section")
                    await login_with_form(page, self.settings, self.login, self.diagnostics)
                stage = "navigate"
                await page.goto(base + self.source.inspections_path, wait_until="domcontentloaded")
                stage = "search"
                await self._search(page, serial_number, inspection_number)
                stage = "extract"
                return await self._extract_all(page)
            except (AuthenticationError, ConfigurationError):
                raise  # login diagnostics are captured inside login_with_form
            except PipelineError as exc:
                if stage != "login":  # login_with_form captures its own diagnostics
                    await self.diagnostics.capture(page, stage, exc)
                raise
            except PlaywrightTimeout as exc:
                await self.diagnostics.capture(page, stage, exc)
                raise PageStructureError(f"Timed out during {stage}: {exc.message.splitlines()[0]}") from exc
            except PlaywrightError as exc:
                await self.diagnostics.capture(page, stage, exc)
                raise SourceUnavailableError(f"Browser error during {stage}: {exc.message.splitlines()[0]}") from exc
            finally:
                await browser.close()

    # ------------------------------------------------------------------ steps
    async def _search(self, page: Page, serial_number: str | None, inspection_number: str | None) -> None:
        search = self.source.search
        filled = False
        for value, label, name in (
            (serial_number, search.serial_number_label, "serial_number_label"),
            (inspection_number, search.inspection_number_label, "inspection_number_label"),
        ):
            if value is None:
                continue
            if not label:
                raise ConfigurationError(f"Filtering requested but browser.search.{name} is not configured")
            await page.get_by_label(label, exact=True).fill(value)
            filled = True
        if filled:
            if search.submit_button_name:
                await page.get_by_role("button", name=search.submit_button_name, exact=True).click()
            else:
                await page.keyboard.press("Enter")

    def _table(self, page: Page) -> Locator:
        t = self.source.table
        return page.get_by_role(t.role, name=t.name, exact=True) if t.name else page.get_by_role(t.role)  # type: ignore[arg-type]

    async def _read_page(self, page: Page) -> dict[str, Any]:
        table = self._table(page).first
        await table.wait_for(state="visible")
        return await table.evaluate(_EXTRACT_TABLE_JS)

    def _check_headers(self, headers: list[str] | None) -> None:
        if not headers:
            raise PageStructureError("Results table has no column headers")
        m = self.field_mapping
        wanted = [m.inspection_number, m.serial_number, m.status, m.inspection_date, m.attachments,
                  *m.summary.values()]
        missing = [h for h in wanted if h and h not in headers]
        if missing:
            raise PageStructureError(f"Expected column(s) {missing} not found; table shows {headers}")

    @staticmethod
    def _signature(data: dict[str, Any]) -> str:
        rows = data.get("rows") or []
        return repr([{k: v for k, v in r.items() if k != LINKS_KEY} for r in rows[:3]])

    async def _settle(self, page: Page) -> None:
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        try:
            await page.wait_for_load_state("networkidle", timeout=min(10_000, self.settings.browser_timeout_ms))
        except PlaywrightTimeout:
            pass  # apps that poll never go idle; the table wait below is authoritative

    async def _extract_all(self, page: Page) -> CollectionResult:
        records: list[SourceRecord] = []
        seen: set[str] = set()
        await self._settle(page)
        data = await self._read_page(page)
        self._check_headers(data.get("headers"))
        page_no = 1
        while True:
            signature = self._signature(data)
            if signature in seen and data.get("rows"):
                raise PaginationError(f"Page {page_no} repeats an earlier page; aborting")
            seen.add(signature)
            for i, row in enumerate(data.get("rows") or []):
                records.append(SourceRecord(raw=row, locator=f"page={page_no},row={i + 1}"))
            logger.info("Extracted page %d with %d rows", page_no, len(data.get("rows") or []))

            if not await self._go_next(page):
                break
            page_no += 1
            if page_no > self.source.max_pages:
                raise PaginationError(f"Stopped after max_pages={self.source.max_pages}")
            data = await self._wait_for_new_page(page, signature, page_no)
        return CollectionResult(records=records, pages_fetched=page_no)

    async def _go_next(self, page: Page) -> bool:
        nxt = self.source.next_page
        if nxt is None:
            return False
        button = page.get_by_role(nxt.role, name=nxt.name, exact=True) if nxt.name else page.get_by_role(nxt.role)  # type: ignore[arg-type]
        if await button.count() == 0:
            return False
        button = button.first
        if await button.is_disabled() or (await button.get_attribute("aria-disabled")) == "true":
            return False
        await button.click()
        return True

    async def _wait_for_new_page(self, page: Page, previous: str, page_no: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.settings.browser_timeout_ms / 1000
        while time.monotonic() < deadline:
            data = await self._read_page(page)
            if self._signature(data) != previous:
                return data
            await asyncio.sleep(0.25)
        raise PaginationError(f"Page {page_no} did not load after clicking next")
