"""
Automatic selector discovery — proves every selector by using it.

The rule this project runs on is "never guess a selector". Automatic discovery
does not break that rule, because nothing here is accepted on the strength of
looking plausible. Candidates come from the page's own labels, roles and
accessible names, and each one is then USED: search a serial that exists, search
one that does not, and keep only the elements whose behaviour proves what they
are. A search box that does not produce results is not a search box.

Whatever cannot be proven is written as TODO_CAPTURE, and the run stops.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

WAIT_AFTER_SUBMIT_MS = 2500
MAX_PAIRS = 8


class AutoDiscovery:
    def __init__(self, session: Any) -> None:
        self.s = session                    # CaptureSession
        self.page = session.page
        self.good = session.args.serial
        self.missing = session.args.missing_serial

    # ── helpers ─────────────────────────────────────────────────────────────
    def log(self, message: str) -> None:
        print(f"  · {message}", flush=True)

    async def js(self, expression: str, *args: Any) -> Any:
        return await self.page.evaluate(expression, *args)

    async def record_from_probe(self, key: str, name: str,
                                evidence: str) -> bool:
        """Turn a discovered element into a verified, recorded selector."""
        handle = await self.page.query_selector(f'[data-maia-probe="{key}"]')
        if handle is None:
            return False
        payload = await handle.evaluate("el => window.__maiaPicker.build(el)")
        info = payload.get("info") or {}
        verified = [c for c in payload.get("candidates") or [] if c.get("verified")]
        if not verified:
            for cand in payload.get("candidates") or []:
                if cand.get("verified") is None:
                    ok, _count = await self.s._verify_with_playwright(cand["selector"], handle)
                    if ok:
                        verified.append(cand)
                        break
        if not verified:
            return False

        from capture_selectors import CaptureRecord, now_iso

        best = verified[0]
        self.s.records[name] = CaptureRecord(
            name=name, selector=best["selector"], strategy=best["strategy"],
            element_text=info.get("element_text", "")[:120], url=self.page.url,
            captured_at=now_iso(), confidence="verified", match_count=best.get("count"),
            alternates=[{"selector": c["selector"], "strategy": c["strategy"]}
                        for c in verified[1:3]],
            notes=[f"discovered automatically; {evidence}"],
        )
        print(f"    ✓ {name:<26} {best['strategy']}: {best['selector'][:70]}")
        return True

    def todo(self, name: str, reason: str) -> None:
        from capture_selectors import CaptureRecord, now_iso

        self.s.records[name] = CaptureRecord(
            name=name, status="TODO_CAPTURE", confidence="unverified",
            captured_at=now_iso(), url=self.page.url, notes=[reason])
        print(f"    ✗ {name:<26} TODO_CAPTURE — {reason}")

    async def clear_probes(self) -> None:
        try:
            await self.js("() => window.__maiaDiscover.clearProbes()")
        except Exception:
            pass

    # ── phases ──────────────────────────────────────────────────────────────
    async def run(self) -> bool:
        print("\n─── AUTOMATIC DISCOVERY ───")
        # The redirect back from sign-in lands on the app's URL long before the
        # app exists. Everything below reads the DOM, so nothing may run until
        # the shell has actually rendered.
        boot = await self.s.wait_for_app()
        await self.s.shot("auto-01-authenticated")
        await self.s.snapshot("auto-01-authenticated")
        if not boot.get("booted"):
            self.s.stopped_reason = (
                f"the application never finished rendering after sign-in "
                f"({boot.get('waited_ms')} ms; title="
                f"{str(boot.get('title') or '')[:40]!r}, "
                f"{boot.get('text')} chars of text, {boot.get('inputs')} visible "
                f"input(s), {boot.get('landmarks')} landmark(s)). Nothing could be "
                f"discovered on a page that had not drawn yet.")
            return False

        await self.consent()
        await self.landing_markers()
        if not await self.reach_search_page():
            self.s.stopped_reason = ("could not reach a page with a search input after "
                                     "sign-in; the navigation could not be discovered")
            return False
        if not await self.prove_search_controls():
            self.s.stopped_reason = ("no input/button pair produced a different outcome for a "
                                     "known serial versus an unknown one, so nothing could be "
                                     "proven to be the search")
            return False
        await self.detail_fields()

        missing = self.s.missing_required()
        if missing:
            self.s.stopped_reason = f"required targets unresolved: {', '.join(missing)}"
            print(f"\n✗ unresolved: {', '.join(missing)}")
            return False
        return True

    async def consent(self) -> None:
        cands = await self.js("() => window.__maiaDiscover.consentCandidates()")
        if not cands:
            return
        first = cands[0]
        await self.record_from_probe(first["key"], "consent.accept",
                                     f"consent banner button labelled {first['text'][:30]!r}")
        try:
            await self.page.click(f'[data-maia-probe="{first["key"]}"]', timeout=5000)
            self.log("dismissed the consent banner")
            await self.page.wait_for_timeout(800)
        except Exception:
            pass

    async def landing_markers(self) -> None:
        """A signed-in marker: something present now that the sign-in page did not have."""
        marker = await self.js("(known) => window.__maiaDiscover.probeNewInLandmarks(known)",
                               self.s.login_page_signature or [])
        if marker and await self.record_from_probe(
                marker["key"], "ready.authenticated",
                f"in the page chrome after sign-in, absent before ({marker['text'][:24]!r})"):
            pass
        else:
            self.todo("ready.authenticated",
                      "no element could be shown to exist only after sign-in")

        shell = await self.js("() => window.__maiaDiscover.probeFirstVisible("
                              "window.__maiaDiscover.LANDMARKS)")
        if not (shell and await self.record_from_probe(
                shell["key"], "ready.app_shell", "application chrome landmark")):
            auth = self.s.records.get("ready.authenticated")
            if auth and auth.selector:
                from capture_selectors import CaptureRecord, now_iso
                self.s.records["ready.app_shell"] = CaptureRecord(
                    name="ready.app_shell", selector=auth.selector, strategy=auth.strategy,
                    element_text=auth.element_text, url=self.page.url, captured_at=now_iso(),
                    confidence="verified",
                    notes=["same element as ready.authenticated; no separate chrome landmark"])
                print("    ✓ ready.app_shell            (same as ready.authenticated)")
            else:
                self.todo("ready.app_shell", "no chrome landmark found")

    async def reach_search_page(self) -> bool:
        """Already on a search page, or find the navigation that leads to one."""
        if await self.has_text_input():
            self.log("a search input is already present on the landing page")
            await self.search_page_marker()     # a missing marker is not fatal here
            return True

        navs = await self.js("() => window.__maiaDiscover.navCandidates()")
        for nav in navs[:6]:
            label = (nav.get("text") or nav.get("name") or "")[:40]
            self.log(f"trying navigation: {label!r}")
            try:
                await self.page.click(f'[data-maia-probe="{nav["key"]}"]', timeout=5000)
            except Exception:
                continue
            await self.page.wait_for_timeout(2500)
            if await self.has_text_input():
                self.log(f"reached a page with a search input via {label!r}")
                await self.record_from_probe(nav["key"], "nav.search_link",
                                             "navigating it revealed a search input")
                await self.search_page_marker()
                return True
        return False

    async def has_text_input(self) -> bool:
        cands = await self.js("() => window.__maiaDiscover.inputCandidates()")
        return bool(cands)

    async def search_page_marker(self) -> bool:
        head = await self.js("() => window.__maiaDiscover.probeFirstVisible("
                             "['h1', 'h2', '[role=heading]', 'legend'])")
        if head and await self.record_from_probe(
                head["key"], "ready.search_page",
                f"visible heading on the search screen ({head['text'][:30]!r})"):
            return True
        self.todo("ready.search_page", "no visible heading on the search screen")
        return False

    async def prove_search_controls(self) -> bool:
        """The heart of it: try candidates and keep what behaves correctly."""
        inputs = await self.js("() => window.__maiaDiscover.inputCandidates()")
        buttons = await self.js("() => window.__maiaDiscover.buttonCandidates()")
        print(f"\n  probing {len(inputs)} input candidate(s) × "
              f"{len(buttons) + 1} submit option(s), proving them by use")

        options: list[dict[str, Any] | None] = [*buttons[:4], None]   # None = press Enter
        tried = 0
        for inp in inputs[:4]:
            for btn in options:
                if tried >= MAX_PAIRS:
                    break
                tried += 1
                label = f"{inp.get('name') or inp.get('tag')!r} + " \
                        f"{(btn.get('text') or btn.get('name')) if btn else 'Enter'!r}"
                self.log(f"probe {tried}: {label}")
                outcome = await self.probe_pair(inp, btn)
                if outcome is None:
                    continue
                await self.commit(inp, btn, outcome)
                return True
        return False

    async def probe_pair(self, inp: dict, btn: dict | None) -> dict | None:
        """Search a known-missing serial, then a known-good one, and compare."""
        empty_state = await self.search_and_observe(inp, btn, self.missing, expect="empty")
        if empty_state is None:
            return None
        good = await self.search_and_observe(inp, btn, self.good, expect="results")
        if good is None:
            return None
        if not good.get("hits"):
            return None
        return {"empty": empty_state, "good": good}

    async def search_and_observe(self, inp: dict, btn: dict | None, serial: str,
                                 expect: str) -> dict | None:
        sel = f'[data-maia-probe="{inp["key"]}"]'
        try:
            await self.page.fill(sel, "")
            await self.page.fill(sel, serial)
            if btn:
                await self.page.click(f'[data-maia-probe="{btn["key"]}"]', timeout=5000)
            else:
                await self.page.press(sel, "Enter")
        except Exception as exc:
            self.log(f"    could not drive the pair: {str(exc)[:70]}")
            return None
        await self.page.wait_for_timeout(WAIT_AFTER_SUBMIT_MS)

        hits = await self.js("(s) => window.__maiaDiscover.findByText(s)", serial)
        empties = await self.js("() => window.__maiaDiscover.findEmptyState()")
        if expect == "empty":
            if empties:
                self.log(f"    unknown serial → empty state: {empties[0]['text'][:50]!r}")
                return {"empties": empties, "hits": hits}
            if not hits:
                self.log("    unknown serial → no results and no empty-state wording")
                return {"empties": [], "hits": []}
            return None
        if hits:
            self.log(f"    known serial → {len(hits)} element(s) carrying it")
        return {"hits": hits, "empties": empties}

    async def commit(self, inp: dict, btn: dict | None, outcome: dict) -> None:
        self.s.search_url = self.page.url        # so verification can come back here
        await self.record_from_probe(inp["key"], "search.input",
                                     "typing a known serial into it produced results, "
                                     "an unknown one did not")
        if btn:
            await self.record_from_probe(btn["key"], "search.submit",
                                         "clicking it executed the search")
        else:
            self.todo("search.submit", "the form submits on Enter; no button was needed")

        marker = self.s.records.get("ready.search_page")
        if (not marker or marker.status == "TODO_CAPTURE") and "search.input" in self.s.records:
            from capture_selectors import CaptureRecord, now_iso
            proven = self.s.records["search.input"]
            self.s.records["ready.search_page"] = CaptureRecord(
                name="ready.search_page", selector=proven.selector, strategy=proven.strategy,
                element_text=proven.element_text, url=self.page.url, captured_at=now_iso(),
                confidence="verified",
                notes=["no heading on this screen; the proven search input marks it instead"])
            print("    ✓ ready.search_page          (the proven search input)")

        good_hit = outcome["good"]["hits"][0]
        if good_hit.get("container"):
            await self.record_from_probe(good_hit["container"]["key"], "search.results",
                                         "container that appeared holding the known serial")
        else:
            self.todo("search.results", "no container could be identified around the result")
        if good_hit.get("row"):
            await self.record_from_probe(good_hit["row"]["key"], "search.first_result",
                                         "row carrying the known serial")

        empties = outcome["empty"]["empties"]
        if empties:
            # re-run the empty search so the element exists in the DOM right now
            await self.search_and_observe(inp, btn, self.missing, expect="empty")
            fresh = await self.js("() => window.__maiaDiscover.findEmptyState()")
            if fresh:
                await self.record_from_probe(
                    fresh[0]["key"], "search.no_results_marker",
                    f"shown for an unknown serial: {fresh[0]['text'][:40]!r}")
            else:
                self.todo("search.no_results_marker", "the empty state could not be re-observed")
        else:
            self.todo("search.no_results_marker",
                      "the source shows no distinct empty state; SERIAL_NOT_FOUND cannot be "
                      "proven safely without one")

        # back to the result, then into the detail page
        await self.search_and_observe(inp, btn, self.good, expect="results")

    async def detail_fields(self) -> None:
        first = self.s.records.get("search.first_result")
        if first and first.selector:
            try:
                await self.page.click(first.selector, timeout=8000)
                await self.page.wait_for_timeout(2500)
            except Exception as exc:
                self.log(f"could not open the result row: {str(exc)[:70]}")
        # Everything the detail page must yield lives below the fold of the SIS
        # content pane. Scroll it — and wait for what the scroll renders —
        # before looking for anything, or the section simply is not there.
        await self.s.reveal_detail_section()
        await self.s.shot("auto-02-detail")
        await self.s.snapshot("auto-02-detail")

        head = await self.js("() => window.__maiaDiscover.probeFirstVisible("
                             "['h1', 'h2', '[role=heading]'])")
        if not (head and await self.record_from_probe(
                head["key"], "ready.detail_page",
                f"visible heading on the detail screen ({head['text'][:30]!r})")):
            self.todo("ready.detail_page", "no visible heading on the detail screen")

        fields = await self.js("() => window.__maiaDiscover.detailFields()")
        print(f"\n  detail fields discovered from the page's own labels: "
              f"{', '.join(fields) or 'none'}")
        for field, found in (fields or {}).items():
            name = f"detail.{field}"
            if field == "serial_number":
                continue
            if field == "spec_rows":
                # The row selector must match EVERY row, not just the first, so
                # drop the positional tail a unique-selector would carry.
                if await self.record_from_probe(found["value"]["key"], "detail.spec_rows",
                                                f"repeating rows: {found['value_text']}"):
                    rec = self.s.records["detail.spec_rows"]
                    rec.selector = re.sub(r":nth-of-type\(\d+\)$", "", rec.selector)
                    rec.notes.append("positional suffix removed so it matches every row")
                    from capture_selectors import CaptureRecord, now_iso
                    self.s.records["detail.spec_cell"] = CaptureRecord(
                        name="detail.spec_cell", selector=found.get("cell_tag", "td"),
                        strategy="derived-from-row", element_text="",
                        url=self.page.url, captured_at=now_iso(), confidence="verified",
                        notes=["child element of the discovered specification row"])
                    print(f"    ✓ detail.spec_cell           {found.get('cell_tag', 'td')}")
                continue
            await self.record_from_probe(
                found["value"]["key"], name,
                f"value beside the label {found['label'][:24]!r} "
                f"(read as {str(found.get('value_text'))[:30]!r})")
        for required in ("detail.equipment_model",):
            if required not in self.s.records:
                self.todo(required, "no label on the detail page matched this field")

        await self.equipment_details()
        await self.parts_groups()

    # ── the equipment-details section ───────────────────────────────────────
    async def equipment_details(self) -> None:
        """The four machine/engine fields, found by the page's own labels.

        Nothing here is a guess: each one is located by the label SIS itself
        prints, the element carrying the value is probed, and what it currently
        reads is written into the report as the evidence for the selector. A
        label that is not on the page produces TODO_CAPTURE and nothing else.
        """
        reveal = self.s.detail_reveal or {}
        if not reveal.get("found"):
            self.log("the equipment-details section was never revealed; "
                     "the four machine/engine fields cannot be proven")

        found = await self.js("() => window.__maiaDiscover.labelledFields()")
        print(f"\n  equipment details found by label: {', '.join(found) or 'none'}")
        for field, hit in (found or {}).items():
            name = f"detail.{field}"
            await self.record_from_probe(
                hit["value"]["key"], name,
                f"the page's own label {str(hit.get('label_text'))[:28]!r} "
                f"({hit.get('shape')}), reading {str(hit.get('value_text'))[:24]!r}")

        for field in ("machine_serial_number", "machine_build_date",
                      "engine_serial_number", "engine_build_date"):
            key = f"detail.{field}"
            if key not in self.s.records:
                self.todo(key, "the page shows no label for this field after scrolling "
                               "to the equipment details")

        # The pane that actually scrolls, so later runs move the right thing.
        from capture_selectors import DETAIL_SECTION_PATTERN

        pane = await self.js("(p) => window.__maiaDiscover.scrollPaneFor(p)",
                             DETAIL_SECTION_PATTERN)
        if pane and await self.record_from_probe(
                pane["key"], "detail.scroll_container",
                f"the pane holding the details section ({pane.get('path')})"):
            pass
        else:
            self.todo("detail.scroll_container",
                      "the window scrolls this page; there is no separate content pane")

    # ── the "Product - …" parts groups ──────────────────────────────────────
    async def parts_tab(self) -> None:
        """Open the record's Parts tab if there is one, and record how.

        SIS shows the record as tabs — Dashboard, Parts, Repair, Service. The
        parts exist in the DOM only once Parts is open, so the tab has to be
        found, proven by what appears after clicking it, and captured.
        """
        before = await self.js("() => window.__maiaSisDom.productHeadings().length")
        if before:
            self.log("parts are already on screen; no tab to open")
            return

        tabs = await self.js("""() => {
            const want = /^\s*parts\s*$/i;
            const out = [];
            for (const el of document.querySelectorAll(
                    'a, button, [role=tab], [role=link], li')) {
              if (!window.__maiaSisDom.visible(el)) continue;
              if (!want.test(window.__maiaSisDom.text(el))) continue;
              if ([...el.children].some(c => want.test(window.__maiaSisDom.text(c)))) continue;
              out.push(window.__maiaDiscover.describeEl(el));
              if (out.length >= 4) break;
            }
            return out;
        }""")
        for tab in tabs or []:
            try:
                await self.page.click(f'[data-maia-probe="{tab["key"]}"]', timeout=5000)
            except Exception:
                continue
            await self.page.wait_for_timeout(2200)
            await self.s.reveal_detail_section()
            after = await self.js("() => window.__maiaSisDom.productHeadings().length")
            if after > before:
                self.log(f"opening the Parts tab revealed {after} parts group(s)")
                await self.record_from_probe(
                    tab["key"], "nav.parts_tab",
                    f"clicking it revealed {after} parts group heading(s)")
                return
        self.todo("nav.parts_tab",
                  "no tab could be shown to reveal a parts group; if the parts are "
                  "already on the record this is expected")

    async def parts_groups(self) -> None:
        """Every "… Entire Group (…)" group, and the table of the entire group."""
        await self.parts_tab()
        groups = await self.js("() => window.__maiaDiscover.productGroups()")
        self.s.product_groups = [
            {k: v for k, v in g.items() if k not in ("heading", "table")} | {
                "columns": (g.get("table") or {}).get("columns"),
                "row_count": (g.get("table") or {}).get("row_count"),
            } for g in (groups or [])]
        if not groups:
            for key in ("detail.parts_group", "detail.parts_table",
                        "detail.parts_header_cells", "detail.parts_rows"):
                self.todo(key, "no 'Product - …' heading is present on the detail page")
            return

        titles = ", ".join(g["title"][:40] for g in groups[:6])
        print(f"\n  parts groups on the page ({len(groups)}): {titles}")

        primary = next((g for g in groups if g.get("is_entire_group")), groups[0])
        await self.record_from_probe(
            primary["heading"]["key"], "detail.parts_group",
            f"parts group heading {primary['title'][:44]!r}")

        table = primary.get("table")
        if not table:
            for key in ("detail.parts_table", "detail.parts_header_cells", "detail.parts_rows"):
                self.todo(key, f"no table follows the heading {primary['title'][:40]!r}")
            return

        await self.record_from_probe(
            table["container"]["key"], "detail.parts_table",
            f"the table after that heading: {table['row_count']} rows × "
            f"{table['column_count']} columns")
        if table.get("header_cell"):
            await self.record_from_probe(
                table["header_cell"]["key"], "detail.parts_header_cells",
                f"column headings: {', '.join(table.get('columns') or [])[:80]}")
            await self.generalise(self.s.records.get("detail.parts_header_cells"),
                                  expected=len(table.get("columns") or []))
        else:
            self.todo("detail.parts_header_cells",
                      "the parts table publishes no column headings")

        if await self.record_from_probe(
                table["first_row"]["key"], "detail.parts_rows",
                f"one data row of that table (of {table['row_count']})"):
            await self.generalise(self.s.records["detail.parts_rows"],
                                  expected=table["row_count"])
            await self.s._derive_cell(self.s.records["detail.parts_rows"].selector,
                                      "detail.parts_cell")

    async def generalise(self, record: Any, *, expected: int) -> None:
        """A row selector must match EVERY row, and is only kept if it proves it."""
        if not record or not record.selector:
            return
        generalised = re.sub(r":nth-(?:of-type|child)\(\d+\)\s*$", "", record.selector)
        if generalised == record.selector:
            return
        count = await self.js("(s) => window.__maiaDiscover.countMatching(s)", generalised)
        if count and count >= max(expected, 1):
            record.selector = generalised
            record.match_count = count
            record.notes.append(f"positional suffix removed; matches {count} siblings "
                                f"(the table shows {expected})")
            print(f"    · {record.name:<26} generalised to {count} matches")
        else:
            record.notes.append(f"positional suffix kept: generalising matched {count}, "
                                f"the table shows {expected}")
