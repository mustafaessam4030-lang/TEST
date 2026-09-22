"""Writing the learned contract to disk — and refusing to, when it is not earned.

`config/sis_selectors.json` is the file every later start trusts without
question. What lands there must be a contract that drove a real search against
the real site and came back with a record, never one that merely looked right.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.adapters import selector_store
from app.services.capture_service import CaptureService


def _payload() -> dict[str, Any]:
    entry = lambda name: {                                    # noqa: E731
        "name": name, "selector": f"#{name.replace('.', '-')}", "strategy": "id",
        "element_text": "", "url": "https://sis2.cat.com/#/",
        "captured_at": "2026-09-22T12:00:00+00:00", "confidence": "verified"}
    return {"selectors": {name: entry(name) for name in selector_store.REQUIRED_SELECTORS},
            "selector_version": "auto-20260922T121155Z"}


class _Registry:
    def __init__(self, base_url: str) -> None:
        self._cfg = {"base_url": base_url, "source_id": "cat_sis"}

    def entry(self, _source: str) -> Any:
        return type("E", (), {"config": self._cfg})()


def _service(tmp_path: Path, base_url: str, monkeypatch) -> CaptureService:
    monkeypatch.setenv("MAIA_SIS_SELECTORS", str(tmp_path / "sis_selectors.json"))
    settings = type("S", (), {"sources_dir": str(tmp_path / "sources"),
                              "headless": True, "chromium_path": None})()
    service = CaptureService(registry=_Registry(base_url), settings=settings)
    service._learned["cat_sis"] = _payload()
    return service


def test_a_contract_that_produced_a_record_is_written_to_disk(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path, "https://sis2.cat.com/#/", monkeypatch)
    written = service.promote("cat_sis")
    assert written is not None
    body = json.loads(Path(written).read_text(encoding="utf-8"))
    assert body["promoted_because"].startswith("a live lookup")
    assert not selector_store.missing_required(body)


def test_fixture_output_can_never_become_the_sis_contract(tmp_path, monkeypatch) -> None:
    """The one write that would poison every later run."""
    service = _service(tmp_path, "http://127.0.0.1:5199/fixture.html", monkeypatch)
    assert service.promote("cat_sis") is None
    assert not (tmp_path / "sis_selectors.json").exists()


def test_an_incomplete_contract_is_not_written(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path, "https://sis2.cat.com/#/", monkeypatch)
    service._learned["cat_sis"]["selectors"].pop("search.input")
    assert service.promote("cat_sis") is None
    assert not (tmp_path / "sis_selectors.json").exists()


def test_promoting_twice_is_harmless(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path, "https://sis2.cat.com/#/", monkeypatch)
    assert service.promote("cat_sis") is not None
    assert service.promote("cat_sis") is None      # nothing left to promote


def test_nothing_learned_means_nothing_written(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path, "https://sis2.cat.com/#/", monkeypatch)
    service._learned.clear()
    assert service.promote("cat_sis") is None


def test_an_unwritable_contract_path_never_fails_the_lookup(tmp_path, monkeypatch) -> None:
    """The run already succeeded. The contract is a convenience, not the answer."""
    monkeypatch.setenv("MAIA_SIS_SELECTORS", str(tmp_path / "nope" / "x.json"))
    service = _service(tmp_path, "https://sis2.cat.com/#/", monkeypatch)
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    assert service.promote("cat_sis") is None      # logged, not raised
