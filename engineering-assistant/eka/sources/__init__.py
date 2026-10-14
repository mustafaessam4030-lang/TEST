"""Document sources: where PDFs come from besides manual upload.

Phase 1: ``FolderSource`` — a folder on disk (``pdfs/`` by default).
Phase 2: a SharePoint list source implements the same ``Source`` interface
(see docs/PHASE2_SHAREPOINT.md); ``SourceSync`` needs no change for it.
"""
from .base import Source, SourceItem
from .folder import FolderSource
from .sync import SourceSync

__all__ = ["Source", "SourceItem", "FolderSource", "SourceSync"]
