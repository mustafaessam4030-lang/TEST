"""Collector abstraction: the rest of the pipeline does not care where data came from."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models import SourceRecord
from app.services.normalization import LookupMode
from app.source_config import FieldMapping


@dataclass
class CollectionResult:
    records: list[SourceRecord]
    pages_fetched: int
    expected_total: int | None = None  # as reported by the source, when it reports one
    warnings: list[str] = field(default_factory=list)


class InspectionCollector(ABC):
    collector_type: str
    lookup_mode: LookupMode
    field_mapping: FieldMapping

    @abstractmethod
    async def collect(
        self,
        serial_number: str | None = None,
        inspection_number: str | None = None,
    ) -> CollectionResult:
        """Return every raw record matching the (optional) filters."""
