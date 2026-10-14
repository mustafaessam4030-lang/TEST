"""SQLite persistence: documents, per-page status, chunks and their vectors.

One file, no server. Writes for a document (its pages, chunks and vectors) are
committed in a single transaction, so a crash mid-ingest never leaves a
half-indexed document that looks complete. All SQL is parameterised.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id             TEXT PRIMARY KEY,
    sha256         TEXT NOT NULL UNIQUE,
    filename       TEXT NOT NULL,
    title          TEXT NOT NULL DEFAULT '',
    doc_number     TEXT NOT NULL DEFAULT '',
    revision       TEXT NOT NULL DEFAULT '',
    revision_date  TEXT NOT NULL DEFAULT '',
    equipment      TEXT NOT NULL DEFAULT '',
    doc_type       TEXT NOT NULL DEFAULT '',
    page_count     INTEGER NOT NULL DEFAULT 0,
    bytes          INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL,          -- queued|processing|ready|partial|failed
    error          TEXT NOT NULL DEFAULT '',
    progress_done  INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    stage          TEXT NOT NULL DEFAULT '',
    superseded_by  TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    doc_id         TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no        INTEGER NOT NULL,
    status         TEXT NOT NULL,
    method         TEXT NOT NULL,
    chars          INTEGER NOT NULL DEFAULT 0,
    images         INTEGER NOT NULL DEFAULT 0,
    ocr_confidence REAL,
    reason         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (doc_id, page_no)
);
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no     INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    section     TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    search_text TEXT NOT NULL,
    bboxes      TEXT NOT NULL DEFAULT '[]',
    ocr         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id, page_no);
CREATE TABLE IF NOT EXISTS vectors (
    chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model    TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vec      BLOB NOT NULL
);
"""

DOC_FIELDS = ("title", "doc_number", "revision", "revision_date", "equipment", "doc_type")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params=()) -> dict | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    # ── documents ──
    def add_document(self, doc_id: str, sha256: str, filename: str, size: int, meta: dict) -> dict:
        now = time.time()
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO documents (id, sha256, filename, bytes, status, equipment, doc_type, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                (doc_id, sha256, filename, size, meta.get("equipment", ""), meta.get("doc_type", ""), now, now))
        return self.document(doc_id)

    def document(self, doc_id: str) -> dict | None:
        return self._one("SELECT * FROM documents WHERE id = ?", (doc_id,))

    def document_by_sha(self, sha256: str) -> dict | None:
        return self._one("SELECT * FROM documents WHERE sha256 = ?", (sha256,))

    def documents(self) -> list[dict]:
        return self._all("SELECT * FROM documents ORDER BY created_at DESC")

    def update_document(self, doc_id: str, **fields):
        if not fields:
            return
        fields["updated_at"] = time.time()
        columns = ", ".join("{0} = ?".format(k) for k in fields)
        with self._lock:
            self._conn.execute("UPDATE documents SET {0} WHERE id = ?".format(columns),
                               (*fields.values(), doc_id))

    def delete_document(self, doc_id: str):
        with self.transaction() as conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.execute("UPDATE documents SET superseded_by = '' WHERE superseded_by = ?", (doc_id,))

    def pages(self, doc_id: str) -> list[dict]:
        return self._all("SELECT * FROM pages WHERE doc_id = ? ORDER BY page_no", (doc_id,))

    def page_summary(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for row in self._all("SELECT doc_id, status, COUNT(*) AS n FROM pages GROUP BY doc_id, status"):
            out.setdefault(row["doc_id"], {})[row["status"]] = row["n"]
        return out

    # ── index content ──
    def replace_content(self, doc_id: str, pages: list[dict], chunks: list[dict],
                        vectors: np.ndarray, model: str, doc_fields: dict):
        """Atomically replace a document's pages, chunks and vectors (re-index safe)."""
        with self.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM pages WHERE doc_id = ?", (doc_id,))
            conn.executemany(
                "INSERT INTO pages (doc_id, page_no, status, method, chars, images, ocr_confidence, reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(doc_id, p["page_no"], p["status"], p["method"], p["chars"], p["images"],
                  p["ocr_confidence"], p["reason"]) for p in pages])
            conn.executemany(
                "INSERT INTO chunks (id, doc_id, page_no, seq, kind, section, text, search_text, bboxes, ocr)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(c["id"], doc_id, c["page_no"], c["seq"], c["kind"], c["section"], c["text"],
                  c["search_text"], json.dumps(c["bboxes"]), int(c["ocr"])) for c in chunks])
            conn.executemany(
                "INSERT INTO vectors (chunk_id, model, dim, vec) VALUES (?, ?, ?, ?)",
                [(c["id"], model, vectors.shape[1], vectors[i].astype(np.float32).tobytes())
                 for i, c in enumerate(chunks)])
            fields = dict(doc_fields, updated_at=time.time())
            columns = ", ".join("{0} = ?".format(k) for k in fields)
            conn.execute("UPDATE documents SET {0} WHERE id = ?".format(columns), (*fields.values(), doc_id))

    def chunk(self, chunk_id: str) -> dict | None:
        return self._one("SELECT * FROM chunks WHERE id = ?", (chunk_id,))

    def all_chunks(self) -> list[dict]:
        """Chunks of searchable documents only (ready or partial)."""
        return self._all(
            "SELECT c.* FROM chunks c JOIN documents d ON d.id = c.doc_id"
            " WHERE d.status IN ('ready', 'partial') ORDER BY c.doc_id, c.seq")

    def vectors(self, model: str) -> dict[str, np.ndarray]:
        with self._lock:
            rows = self._conn.execute("SELECT chunk_id, vec FROM vectors WHERE model = ?", (model,)).fetchall()
        return {r[0]: np.frombuffer(r[1], dtype=np.float32) for r in rows}

    def chunks_missing_vectors(self, model: str) -> list[dict]:
        return self._all(
            "SELECT c.id, c.search_text FROM chunks c LEFT JOIN vectors v ON v.chunk_id = c.id AND v.model = ?"
            " WHERE v.chunk_id IS NULL", (model,))

    def put_vectors(self, items: list[tuple[str, np.ndarray]], model: str):
        with self.transaction() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO vectors (chunk_id, model, dim, vec) VALUES (?, ?, ?, ?)",
                [(cid, model, len(v), v.astype(np.float32).tobytes()) for cid, v in items])
