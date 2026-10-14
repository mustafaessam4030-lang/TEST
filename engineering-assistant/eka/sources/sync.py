"""Keep the library in step with a source, in a background thread.

Each scan reconciles the source with the documents that came from it:

* new item            -> read and queued for indexing
* unchanged item      -> nothing is read (its version matches the stored one)
* changed item        -> the new content is indexed; the older copy is removed
                         once it is no longer being processed
* item gone           -> removed only when ``remove_deleted`` is on (default off)
* still being written -> waits for a later scan
* not a PDF           -> listed as skipped with the reason (videos: phase 2)

The source is only ever read. Errors (folder missing, unreadable file, an
item that is not a valid PDF) are recorded per item and shown in the UI;
one bad item never stops the others.
"""
from __future__ import annotations

import logging
import threading
import time

from ..ingest import IngestService, UploadError
from .base import Source

log = logging.getLogger(__name__)
BUSY = ("queued", "processing")


class SourceSync:
    def __init__(self, source: Source, ingest: IngestService, interval_s: float = 15.0,
                 remove_deleted: bool = False):
        self.source = source
        self.ingest = ingest
        self.store = ingest.store
        self.interval_s = max(1.0, interval_s)
        self.remove_deleted = remove_deleted
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._duplicates: dict[str, tuple[str, str]] = {}   # key -> (version, reason): content already indexed elsewhere
        self._failed: dict[str, tuple[str, str]] = {}   # key -> (version, reason): not retried until changed
        self._cleanup_waiting = False   # a replaced file's old copy waits for the new one to finish indexing
        self.status = {"source": source.name, "label": source.label, "location": source.location(),
                       "last_scan": None, "items": 0, "pdfs": 0, "indexed": 0, "pending": 0,
                       "added": 0, "updated": 0, "removed": 0, "skipped": [], "errors": [], "error": ""}

    # ── lifecycle ──
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="eka-source-" + self.source.name, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception:  # keep syncing whatever happens in one scan
                log.exception("source scan failed")
            # while a replacement is being indexed, check back soon so the old copy does not linger
            self._wake.wait(min(self.interval_s, 2.0) if self._cleanup_waiting else self.interval_s)
            self._wake.clear()

    def snapshot(self) -> dict:
        """Status of the last scan, with document counts as they are now (not as they were then)."""
        mine = self.store.documents_with_source_prefix(self.source.name + ":")
        return {**self.status, "indexed": sum(d["status"] in ("ready", "partial") for d in mine),
                "processing": sum(d["status"] in BUSY for d in mine),
                "failed": sum(d["status"] == "failed" for d in mine)}

    def scan_soon(self):
        self._wake.set()

    # ── one reconciliation pass ──
    def scan(self) -> dict:
        with self._lock:
            self._scan()
        return self.snapshot()

    def _scan(self) -> dict:
        st = {**self.status, "added": 0, "updated": 0, "removed": 0, "skipped": [], "errors": [], "error": ""}
        self._cleanup_waiting = False
        try:
            items = self.source.list()
        except Exception as exc:
            st.update(error=str(exc)[:300], last_scan=time.time())
            self.status = st
            return st
        seen = set()
        pending = 0
        for item in items:
            key = self.source.source_key(item)
            seen.add(key)
            if item.kind != "pdf":
                reason = ("videos are not indexed yet (planned for phase 2)" if item.kind == "video"
                          else "only PDF files are indexed")
                st["skipped"].append({"name": item.key, "reason": reason})
                continue
            if not item.ready:
                pending += 1
                continue
            docs = self.store.documents_by_source(key)
            current = docs[0] if docs else None
            if current and current["source_version"] == item.version:
                self._cleanup_older(docs, st)
                continue
            dup = self._duplicates.get(key)
            if dup and dup[0] == item.version:
                st["skipped"].append({"name": item.key, "reason": dup[1]})
                continue
            failed = self._failed.get(key)
            if failed and failed[0] == item.version:
                st["errors"].append({"name": item.key, "reason": failed[1]})
                continue
            try:
                data = self.source.fetch(item)
                doc, duplicate = self.ingest.submit(data, item.name, item.meta, source=key, source_version=item.version)
            except (UploadError, OSError) as exc:
                self._failed[key] = (item.version, str(exc)[:200])
                st["errors"].append({"name": item.key, "reason": str(exc)[:200]})
                continue
            self._failed.pop(key, None)
            self._duplicates.pop(key, None)
            if duplicate and doc["source"] != key:
                # identical content is already in the library (another file or an upload);
                # an older version of this file is no longer what the folder holds, so drop it
                reason = "same content as \"{0}\"".format(doc["filename"])
                self._duplicates[key] = (item.version, reason)
                st["skipped"].append({"name": item.key, "reason": reason})
                for old in docs:
                    if old["status"] not in BUSY:
                        self.ingest.delete(old["id"])
                        st["removed"] += 1
                continue
            if duplicate:
                self.store.update_document(doc["id"], source_version=item.version)
            st["updated" if current else "added"] += 1
            self._cleanup_older(self.store.documents_by_source(key), st)
        if self.remove_deleted:
            for doc in self.store.documents_with_source_prefix(self.source.name + ":"):
                if doc["source"] not in seen and doc["status"] not in BUSY:
                    self.ingest.delete(doc["id"])
                    st["removed"] += 1
        mine = self.store.documents_with_source_prefix(self.source.name + ":")
        st.update(last_scan=time.time(), items=len(items), pdfs=sum(i.kind == "pdf" for i in items),
                  indexed=sum(d["status"] in ("ready", "partial") for d in mine), pending=pending)
        st["skipped"] = st["skipped"][:50]
        st["errors"] = st["errors"][:50]
        if st["added"] or st["updated"] or st["removed"]:
            log.info("%s scan: %d added, %d updated, %d removed", self.source.name, st["added"], st["updated"], st["removed"])
        self.status = st
        return st

    def _cleanup_older(self, docs: list[dict], st: dict):
        """An item replaced by new content: remove its older copies once they are idle."""
        for old in docs[1:]:
            if old["status"] not in BUSY and docs[0]["status"] not in BUSY:
                self.ingest.delete(old["id"])
                st["removed"] += 1
            else:
                self._cleanup_waiting = True
