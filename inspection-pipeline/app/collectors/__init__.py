"""Collector factory: ``COLLECTOR_TYPE`` selects the implementation."""

from __future__ import annotations

from app.auth.session import build_authenticator
from app.collectors.base import CollectionResult, InspectionCollector
from app.config import Settings
from app.diagnostics import Diagnostics
from app.source_config import SourceConfig


def build_collector(settings: Settings, source: SourceConfig, diagnostics: Diagnostics) -> InspectionCollector:
    source.require(settings.collector_type, settings.auth_mode)
    if settings.collector_type == "api":
        from app.collectors.api import ApiInspectionCollector

        assert source.api is not None
        return ApiInspectionCollector(
            settings, source.api, build_authenticator(settings, source.auth, diagnostics), source.auth
        )
    from app.collectors.browser import PlaywrightInspectionCollector

    assert source.browser is not None
    return PlaywrightInspectionCollector(settings, source.browser, source.auth, diagnostics)


__all__ = ["CollectionResult", "InspectionCollector", "build_collector"]
