"""Phase 1 source: a folder of PDFs on disk (sub-folders included).

Drop PDFs into the folder and they are indexed within one scan interval. A
file still being copied (modified less than ``settle_s`` seconds ago) waits for
the next scan, so half-written files are never indexed.
"""
from __future__ import annotations

import time
from pathlib import Path

from .base import Source, SourceItem

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".wmv", ".webm"}


class FolderSource(Source):
    name = "folder"
    label = "PDF folder"

    def __init__(self, root: Path, settle_s: float = 5.0):
        self.root = Path(root)
        self.settle_s = settle_s

    def location(self) -> str:
        return str(self.root)

    def list(self) -> list[SourceItem]:
        if not self.root.is_dir():
            raise FileNotFoundError("the folder {0} does not exist".format(self.root))
        now = time.time()
        items = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.name.startswith((".", "~$")):
                continue
            ext = path.suffix.lower()
            kind = "pdf" if ext == ".pdf" else "video" if ext in VIDEO_EXT else "other"
            if kind == "other" and path.name.lower() in ("readme.txt", "readme.md", "desktop.ini", "thumbs.db"):
                continue
            stat = path.stat()
            rel = path.relative_to(self.root).as_posix()
            items.append(SourceItem(
                key=rel, name=path.name, version="{0}-{1}".format(stat.st_size, stat.st_mtime_ns), kind=kind,
                ready=now - stat.st_mtime >= self.settle_s, location=str(path),
                meta={"folder": str(Path(rel).parent)} if "/" in rel else {}))
        return items

    def fetch(self, item: SourceItem) -> bytes:
        return (self.root / item.key).read_bytes()
