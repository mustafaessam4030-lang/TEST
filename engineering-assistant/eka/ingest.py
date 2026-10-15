"""Document ingestion: upload -> extract -> chunk -> embed -> index, in a background worker.

Ingestion is kept apart from question answering: uploads return immediately
with a queued document, a single worker thread processes documents in order and
records progress, and the search index is swapped only when a document's
content has been committed.

* Duplicates are detected by SHA-256 of the file bytes; re-uploading the same
  file returns the existing document instead of indexing it twice.
* Re-indexing replaces a document's pages, chunks and vectors in one
  transaction, so repeated ingestion never duplicates records.
* Documents sharing a document number are ordered by revision; older
  revisions are marked ``superseded_by`` the newest one.
"""
from __future__ import annotations

import hashlib
import logging
import os
import queue
import re
import threading
import time
from pathlib import Path

from .chunking import chunk_pages
from .config import Settings
from .embeddings import Embedder
from .extract import ExtractionError, extract
from .figures import find_figures
from .retrieval import HybridIndex
from .store import Store
from .textutil import normalize

log = logging.getLogger(__name__)

FAILED_PAGE_STATES = ("error", "ocr_failed", "ocr_unavailable")


class UploadError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class IngestService:
    def __init__(self, settings: Settings, store: Store, index: HybridIndex, embedder: Embedder):
        self.settings = settings
        self.store = store
        self.index = index
        self.embedder = embedder
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ── lifecycle ──
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="eka-ingest", daemon=True)
        self._thread.start()
        for doc in self.store.documents():  # resume work interrupted by a restart
            if doc["status"] in ("queued", "processing"):
                self.store.update_document(doc["id"], status="queued", stage="queued (resumed)")
                self._queue.put(doc["id"])

    def stop(self):
        self._stop.set()
        self._queue.put("")

    def wait_idle(self, timeout: float = 300.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.05)
        return False

    def _run(self):
        while not self._stop.is_set():
            doc_id = self._queue.get()
            try:
                if doc_id:
                    self.process(doc_id)
            except Exception:  # the worker must survive any single document
                log.exception("ingestion crashed for %s", doc_id)
                self.store.update_document(doc_id, status="failed", stage="",
                                           error="internal error during processing; see server log")
            finally:
                self._queue.task_done()

    # ── API ──
    def submit(self, data: bytes, filename: str, meta: dict | None = None, source: str = "",
               source_version: str = "") -> tuple[dict, bool]:
        """Queue a PDF. ``source`` names where it came from (e.g. "folder:manuals/a.pdf");
        uploads through the UI have no source."""
        meta = {k: normalize(v)[:200] for k, v in (meta or {}).items() if v}
        filename = safe_filename(filename)
        if not data:
            raise UploadError("the uploaded file is empty")
        if len(data) > self.settings.max_upload_mb * 1024 * 1024:
            raise UploadError("the file exceeds the {0} MB upload limit".format(self.settings.max_upload_mb), 413)
        if not data.lstrip()[:5].startswith(b"%PDF"):
            raise UploadError("only PDF files are supported (the file has no %PDF header)", 415)
        sha = hashlib.sha256(data).hexdigest()
        existing = self.store.document_by_sha(sha)
        if existing:
            if source and not existing["source"]:
                # an uploaded copy now also lives in a source: let the source own it
                self.store.update_document(existing["id"], source=source, source_version=source_version)
                existing = self.store.document(existing["id"])
            return existing, True
        doc_id = sha[:16]
        path = self.file_path(doc_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        doc = self.store.add_document(doc_id, sha, filename, len(data), meta, source, source_version)
        self._queue.put(doc_id)
        return doc, False

    def reindex(self, doc_id: str) -> dict:
        self.store.update_document(doc_id, status="queued", stage="queued for re-index", error="",
                                   progress_done=0, progress_total=0)
        self._queue.put(doc_id)
        return self.store.document(doc_id)

    def delete(self, doc_id: str):
        self.store.delete_document(doc_id)
        self.file_path(doc_id).unlink(missing_ok=True)
        self._update_supersession()
        self.index.rebuild()

    def file_path(self, doc_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{16}", doc_id):
            raise UploadError("invalid document id", 404)
        return self.settings.files_dir / (doc_id + ".pdf")

    # ── processing ──
    def process(self, doc_id: str):
        doc = self.store.document(doc_id)
        if doc is None:
            return
        started = time.time()
        self.store.update_document(doc_id, status="processing", stage="extracting text", error="",
                                   progress_done=0, progress_total=0)
        try:
            data = self.file_path(doc_id).read_bytes()
        except OSError as exc:
            self.store.update_document(doc_id, status="failed", stage="", error="stored file is unreadable: {0}".format(exc))
            return

        def progress(done, total):
            self.store.update_document(doc_id, progress_done=done, progress_total=total,
                                       stage="extracting page {0} of {1}".format(done, total))

        try:
            extraction = extract(data, self.settings, progress)
        except ExtractionError as exc:
            self.store.update_document(doc_id, status="failed", stage="", error=str(exc))
            self.index.rebuild()
            return

        chunks = chunk_pages(extraction.pages, self.settings.chunk_max_chars)
        meta = extraction.metadata
        title = (doc.get("title") or meta.get("title") or Path(doc["filename"]).stem.replace("_", " "))[:200]
        rows = [{"id": "{0}:{1}:{2}".format(doc_id, c.page_no, c.seq), "page_no": c.page_no, "seq": c.seq,
                 "kind": c.kind, "section": c.section, "text": c.text, "search_text": c.search_text(title),
                 "bboxes": [list(b) for b in c.bboxes], "ocr": c.ocr} for c in chunks]

        self.store.update_document(doc_id, stage="embedding {0} passages".format(len(rows)))
        try:
            vectors = self.embedder.embed_documents([r["search_text"] for r in rows]) if rows else \
                self.embedder.embed_documents(["empty"])[:0]
        except Exception as exc:
            log.exception("embedding failed for %s", doc_id)
            self.store.update_document(doc_id, status="failed", stage="", error="embedding failed: {0}".format(exc))
            return

        failed = [p for p in extraction.pages if p.status in FAILED_PAGE_STATES]
        if not rows:
            status = "failed"
            reasons = sorted({p.reason for p in extraction.pages if p.reason})
            error = "no text could be extracted from any page" + (": " + "; ".join(reasons) if reasons else "")
        elif failed:
            status = "partial"
            error = "{0} of {1} pages could not be read: {2}".format(
                len(failed), extraction.page_count, ", ".join(str(p.page_no) for p in failed))
        else:
            status, error = "ready", ""
        fields = {"title": title, "page_count": extraction.page_count, "status": status, "error": error,
                  "stage": "", "progress_done": extraction.page_count, "progress_total": extraction.page_count}
        for key in ("doc_number", "revision", "revision_date", "equipment"):
            if not doc.get(key):
                fields[key] = meta.get(key, "")
        pages = [{"page_no": p.page_no, "status": p.status, "method": p.method, "chars": p.chars,
                  "images": p.images, "ocr_confidence": p.ocr_confidence, "reason": p.reason}
                 for p in extraction.pages]
        self.store.update_document(doc_id, stage="finding figures")
        figures = find_figures(data)
        for f in figures:   # the section a figure sits in: the last passage that starts above it on its page
            above = [r for r in rows if r["page_no"] == f["page_no"] and r["bboxes"] and r["bboxes"][0][1] <= f["bbox"][1]]
            f["section"] = max(above, key=lambda r: r["bboxes"][0][1])["section"] if above else ""
        self.store.replace_content(doc_id, pages, rows, vectors, self.embedder.name, fields, figures)
        self._update_supersession()
        self.index.rebuild()
        log.info("indexed document %s: %s, %d pages, %d chunks, %.1fs", doc_id, status,
                 extraction.page_count, len(rows), time.time() - started)

    def _update_supersession(self):
        groups: dict[str, list[dict]] = {}
        for doc in self.store.documents():
            if doc["status"] in ("ready", "partial") and doc["doc_number"]:
                groups.setdefault(doc["doc_number"].upper(), []).append(doc)
        for docs in groups.values():
            latest = max(docs, key=revision_key)
            for doc in docs:
                target = "" if doc["id"] == latest["id"] or len(docs) == 1 else latest["id"]
                if doc["superseded_by"] != target:
                    self.store.update_document(doc["id"], superseded_by=target)
        for doc in self.store.documents():  # documents that left a group
            if doc["superseded_by"] and not doc["doc_number"]:
                self.store.update_document(doc["id"], superseded_by="")


def revision_key(doc: dict):
    rev = (doc.get("revision") or "").upper()
    numeric = rev.replace(".", "", 1).isdigit()
    return (doc.get("revision_date") or "", float(rev) if numeric else -1.0, len(rev), rev)


def safe_filename(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = re.sub(r"[^\w.\- ()]+", "_", name)[:150]
    return name or "document.pdf"
