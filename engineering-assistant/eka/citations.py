"""Citation construction and validation.

Citations are built only from retrieved chunks; page numbers come from the
chunk record written at extraction time, never from model output. Before an
answer is returned every citation is re-checked against the store, and every
claim is checked to be grounded in the passages it cites.
"""
from __future__ import annotations

from .store import Store
from .textutil import compact, excerpt, identifiers, numbers, normalize

SEARCHABLE = ("ready", "partial")


def build(cid: str, chunk, doc: dict, quote: str | None = None) -> dict:
    return {
        "id": cid,
        "chunk_id": chunk.id,
        "doc_id": chunk.doc_id,
        "title": doc.get("title") or doc.get("filename"),
        "filename": doc.get("filename"),
        "revision": doc.get("revision", ""),
        "superseded": bool(doc.get("superseded_by")),
        "page": chunk.page_no,
        "section": chunk.section,
        "kind": chunk.kind,
        "ocr": chunk.ocr,
        "excerpt": excerpt(quote or chunk.text, 400),
        "open_url": "/api/documents/{0}/file#page={1}".format(chunk.doc_id, chunk.page_no),
        "page_image_url": "/api/documents/{0}/pages/{1}/image?chunk={2}".format(
            chunk.doc_id, chunk.page_no, chunk.id),
    }


def validate(citations: list[dict], store: Store) -> tuple[list[dict], list[str]]:
    """Keep citations that match a stored chunk exactly; report the rest."""
    valid, problems = [], []
    for c in citations:
        row = store.chunk(c["chunk_id"])
        doc = store.document(c["doc_id"])
        problem = None
        if row is None or doc is None:
            problem = "unknown passage"
        elif row["doc_id"] != c["doc_id"]:
            problem = "passage belongs to another document"
        elif row["page_no"] != c["page"]:
            problem = "page {0} does not match the passage's page {1}".format(c["page"], row["page_no"])
        elif not 1 <= c["page"] <= max(doc["page_count"], 1):
            problem = "page {0} is outside the document".format(c["page"])
        elif doc["status"] not in SEARCHABLE:
            problem = "document is not searchable (status {0})".format(doc["status"])
        elif compact(c["excerpt"].rstrip("…")) not in compact(row["text"]):
            problem = "excerpt is not in the cited passage"
        if problem:
            problems.append("{0}: {1}".format(c["id"], problem))
        else:
            valid.append(c)
    return valid, problems


def unsupported_values(claim: str, source_texts: list[str]) -> list[str]:
    """Numbers and identifiers in a claim that none of its cited sources contain."""
    joined = "\n".join(source_texts)
    source_numbers = numbers(joined)
    source_compact = compact(joined)
    missing = sorted(n for n in numbers(claim) if n not in source_numbers)
    missing += sorted(i for i in identifiers(claim) if compact(i) not in source_compact)
    return missing


def quote_in_source(quote: str, source: str) -> bool:
    return compact(normalize(quote)) in compact(source)
