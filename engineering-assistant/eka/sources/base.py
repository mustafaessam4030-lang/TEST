"""The interface every document source implements."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceItem:
    key: str                 # stable identity inside the source (relative path, list item id, ...)
    name: str                # file name shown in the library
    version: str             # changes whenever the content may have changed (mtime+size, eTag, ...)
    kind: str = "pdf"        # pdf | video | other
    ready: bool = True       # False while the item is still being written / uploaded
    meta: dict = field(default_factory=dict)   # equipment, doc_type, ... (source columns)
    location: str = ""       # human-readable location (path or URL), for the UI


class Source:
    """A place documents come from. Implementations must be read-only."""

    name = "source"          # short id, used as the prefix of documents.source ("folder:...")
    label = "Source"         # shown in the UI

    def location(self) -> str:
        raise NotImplementedError

    def list(self) -> list[SourceItem]:
        """All items currently in the source. Raise on connectivity/permission errors."""
        raise NotImplementedError

    def fetch(self, item: SourceItem) -> bytes:
        """The item's content."""
        raise NotImplementedError

    def source_key(self, item: SourceItem) -> str:
        return "{0}:{1}".format(self.name, item.key)
