"""Selector store + health check: the gate that turns a changed page into an error."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters import selector_store as store
from app.adapters.selector_health import (
    CheckResult, HealthReport, preflight, verify_no_result_state, verify_result_container,
    verify_search_button, verify_serial_search_input,
)
from app.core.errors import AutomationError, ErrorCode

ROOT = Path(__file__).resolve().parents[3]


def _entry(name: str, selector: str, **over):
    return {"name": name, "selector": selector, "strategy": "data-testid",
            "element_text": "", "url": "https://sis2.cat.com/#/", "captured_at": "2026-09-20T00:00:00Z",
            "confidence": "verified", **over}


def _payload(**over):
    return {"schema_version": "1.0", "source_id": "cat_sis", "selectors": {
        "ready.app_shell": _entry("ready.app_shell", "[data-testid=shell]"),
        "ready.search_page": _entry("ready.search_page", "#search-heading"),
        "search.input": _entry("search.input", "input[name=serial]"),
        "search.submit": _entry("search.submit", "[data-testid=go]"),
        "search.results": _entry("search.results", "#results"),
        "search.no_results_marker": _entry("search.no_results_marker", "#empty"),
    }, **over}


# ── store ───────────────────────────────────────────────────────────────────
def test_only_verified_entries_are_used() -> None:
    payload = _payload()
    payload["selectors"]["search.first_result"] = {"name": "search.first_result",
                                                   "status": "TODO_CAPTURE",
                                                   "confidence": "unverified"}
    payload["selectors"]["detail.model"] = _entry("detail.model", "#m", confidence="probable")
    used = store.verified_selectors(payload)
    assert "search.input" in used
    assert "search.first_result" not in used      # TODO_CAPTURE is never substituted
    assert "detail.model" not in used             # anything short of verified is ignored


def test_missing_required_is_reported_not_filled() -> None:
    payload = _payload()
    complete = set(store.REQUIRED_SELECTORS) - set(store.missing_required(payload))
    del payload["selectors"]["search.input"]
    after = store.missing_required(payload)
    # Removing one entry adds exactly that entry to the missing list; the rest of
    # the report does not move.
    assert "search.input" in after
    assert set(after) - {"search.input"} == set(store.REQUIRED_SELECTORS) - complete


def test_merge_overlays_yaml_contract() -> None:
    config = {"selectors": {"search": {"input": "TODO_CAPTURE", "submit": "TODO_CAPTURE"}},
              "ready_markers": {"app_shell": "TODO_CAPTURE"}, "extraction": {}}
    merged = store.merge_into_config(config, _payload(xhr_endpoints=["https://sis2.cat.com/api/x"]))
    assert merged["selectors"]["search"]["input"] == "input[name=serial]"
    assert merged["ready_markers"]["app_shell"] == "[data-testid=shell]"
    assert merged["extraction"]["xhr_url_patterns"] == ["https://sis2.cat.com/api/x"]
    assert config["selectors"]["search"]["input"] == "TODO_CAPTURE"   # input not mutated


def test_todo_capture_survives_merge_as_absent() -> None:
    payload = _payload()
    payload["selectors"]["search.input"] = {"name": "search.input", "status": "TODO_CAPTURE",
                                            "confidence": "unverified"}
    merged = store.merge_into_config(
        {"selectors": {"search": {"input": "TODO_CAPTURE"}}}, payload)
    assert merged["selectors"]["search"]["input"] == "TODO_CAPTURE"


def test_validate_flags_bad_entries() -> None:
    payload = _payload()
    payload["selectors"]["search.input"] = {"name": "search.input", "selector": "#x",
                                            "confidence": "guessed"}
    problems = store.validate(payload)
    assert "search.input" in problems
    assert any("confidence" in p for p in problems["search.input"])


def test_flatten_config_round_trips() -> None:
    flat = store.flatten_config({"ready_markers": {"app_shell": "#a"},
                                 "selectors": {"search": {"input": "#i"}}})
    assert flat == {"ready.app_shell": "#a", "search.input": "#i"}


def test_shipped_yaml_is_still_honest_about_placeholders() -> None:
    import yaml
    cfg = yaml.safe_load((ROOT / "config" / "sources" / "cat_sis.yaml").read_text())
    flat = store.flatten_config(cfg)
    # Until a real capture runs, every selector must remain an explicit placeholder.
    assert all(v == "TODO_CAPTURE" for v in flat.values()), flat


# ── health check ────────────────────────────────────────────────────────────
class FakeLocator:
    def __init__(self, count: int, visible: bool = True) -> None:
        self._count, self._visible = count, visible
        self.first = self

    async def wait_for(self, state: str = "visible", timeout: int = 0) -> None:
        if self._count == 0 or (state == "visible" and not self._visible):
            raise TimeoutError(f"waiting for {state}")

    async def count(self) -> int:
        return self._count


class FakePage:
    def __init__(self, table: dict[str, FakeLocator], url: str = "https://sis2.cat.com/#/search"):
        self.table, self.url = table, url

    def locator(self, selector: str) -> FakeLocator:
        return self.table.get(selector, FakeLocator(0))


SELECTORS = {"ready.app_shell": "#shell", "ready.search_page": "#search-heading",
             "search.input": "#serial", "search.submit": "#go",
             "search.results": "#results", "search.no_results_marker": "#empty"}


async def test_named_verifications_pass_on_a_healthy_page() -> None:
    page = FakePage({v: FakeLocator(1) for v in SELECTORS.values()})
    for check in (verify_serial_search_input, verify_search_button,
                  verify_result_container, verify_no_result_state):
        result = await check(page, SELECTORS)
        assert result.ok, result.detail


async def test_verification_fails_on_a_missing_element() -> None:
    page = FakePage({"#serial": FakeLocator(0)})
    result = await verify_serial_search_input(page, SELECTORS)
    assert not result.ok


async def test_todo_capture_selector_fails_the_check_without_touching_the_page() -> None:
    result = await verify_search_button(FakePage({}), {"search.submit": "TODO_CAPTURE"})
    assert not result.ok and "TODO_CAPTURE" in result.detail


async def test_ambiguous_match_is_recorded() -> None:
    page = FakePage({"#results": FakeLocator(4)})
    result = await verify_result_container(page, SELECTORS)
    assert result.ok and "ambiguous" in result.detail and result.match_count == 4


async def test_preflight_passes_when_everything_is_present() -> None:
    page = FakePage({v: FakeLocator(1) for v in SELECTORS.values()})
    report = await preflight(page, SELECTORS, base_url="https://sis2.cat.com/#/",
                             login_host_markers=["signin.cat.com"])
    assert report.ok, report.as_dict()
    report.raise_if_failed()


async def test_preflight_detects_a_bounced_session() -> None:
    page = FakePage({v: FakeLocator(1) for v in SELECTORS.values()},
                    url="https://signin.cat.com/oauth/authorize")
    report = await preflight(page, SELECTORS, base_url="https://sis2.cat.com/#/",
                             login_host_markers=["signin.cat.com"])
    assert not report.ok
    assert "authenticated_session" in [c.name for c in report.failed]


async def test_changed_page_raises_website_changed_not_a_fallback() -> None:
    table = {v: FakeLocator(1) for v in SELECTORS.values()}
    table["#serial"] = FakeLocator(0)              # the search box moved
    report = await preflight(FakePage(table), SELECTORS, base_url="https://sis2.cat.com/#/")
    with pytest.raises(AutomationError) as exc:
        report.raise_if_failed(source="cat_sis")
    assert exc.value.code is ErrorCode.WEBSITE_CHANGED
    assert exc.value.step == "HEALTH_CHECK"
    assert exc.value.details["failed_checks"][0]["name"] == "serial_input_visible"


async def test_absent_results_container_is_a_warning_not_a_blocker() -> None:
    """It is usually rendered only after a search, so its absence proves nothing
    at pre-flight — but a missing search box still stops the run."""
    table = {v: FakeLocator(1) for v in SELECTORS.values()}
    table["#results"] = FakeLocator(0)                 # not in the DOM yet at all
    report = await preflight(FakePage(table), SELECTORS, base_url="https://sis2.cat.com/#/")
    assert report.ok
    assert [w.name for w in report.warnings] == ["result_container_available"]


# ── capture-tool contract ───────────────────────────────────────────────────
def test_capture_tool_ships_no_hardcoded_sis_selectors() -> None:
    source = (ROOT / "scripts" / "capture" / "capture_selectors.py").read_text()
    # The tool may know target NAMES; it must not carry SIS DOM paths.
    for smell in ["#mat-", ".sis-", "[data-testid=\"sis", "div.equipment"]:
        assert smell not in source, f"capture tool contains a hardcoded selector: {smell}"


def test_fixture_hooks_are_excluded_from_generated_selectors() -> None:
    picker = (ROOT / "scripts" / "capture" / "picker.js").read_text()
    # Neither the fixture hook nor the discovery probe may become a selector.
    assert "BANNED_ATTRS = ['data-maia-fixture', 'data-maia-probe']" in picker


def test_only_live_modes_may_write_the_real_contract_path() -> None:
    source = (ROOT / "scripts" / "capture" / "capture_selectors.py").read_text()
    # Fixture and recon runs write into their own run folder; only a live capture
    # may touch config/sis_selectors.json, and only when it completed.
    assert 'target = self.outdir / f"sis_selectors.{self.mode}.json"' in source
    # ...and a run that captured nothing, did not complete, or did not run against
    # the real SIS origin, never touches it.
    assert "captured_anything = any(" in source
    assert "or not is_real_sis" in source
    assert "if target.exists() and not complete:" in source


# ── regression: presence is not visibility ───────────────────────────────────
class _Loc:
    def __init__(self, count: int, visible: bool) -> None:
        self._count, self._visible = count, visible
        self.first = self

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._visible


class _Page:
    def __init__(self, table):
        self.table, self.url = table, "https://sis2.cat.com/#/search"

    def locator(self, selector):
        return self.table.get(selector, _Loc(0, False))


async def test_hidden_empty_state_is_not_serial_not_found() -> None:
    """A results container that is visible wins over an empty state that is merely
    in the DOM. Reporting SERIAL_NOT_FOUND here would be a false negative served
    to a customer as fact."""
    from app.adapters.cat_sis import CatSisAdapter

    ctx = type("Ctx", (), {"page": _Page({"#empty": _Loc(1, False),     # present, hidden
                                          "#results": _Loc(1, True)})})()
    assert await CatSisAdapter._is_visible(ctx, "#empty") is False
    assert await CatSisAdapter._is_visible(ctx, "#results") is True


async def test_visible_empty_state_is_the_only_proof_of_not_found() -> None:
    from app.adapters.cat_sis import CatSisAdapter

    ctx = type("Ctx", (), {"page": _Page({"#empty": _Loc(1, True),
                                          "#results": _Loc(1, False)})})()
    assert await CatSisAdapter._is_visible(ctx, "#empty") is True
    assert await CatSisAdapter._is_visible(ctx, "#results") is False


def test_contract_file_is_reserved_for_the_real_sis_origin() -> None:
    """A capture against a fixture, mirror or staging clone must never be able to
    write config/sis_selectors.json, whatever mode produced it."""
    source = (ROOT / "scripts" / "capture" / "capture_selectors.py").read_text()
    assert 'host.endswith("sis2.cat.com")' in source
    assert "or not is_real_sis" in source


def test_an_incomplete_capture_never_becomes_the_contract() -> None:
    """A file of TODO_CAPTURE entries at config/sis_selectors.json reads as
    "we have a contract" to every later check, while proving nothing. Only a
    complete capture may occupy that path — existing file or not."""
    source = (ROOT / "scripts" / "capture" / "capture_selectors.py").read_text(encoding="utf-8")
    assert "usable = complete and not self.missing_required()" in source
    assert "or not usable):" in source
