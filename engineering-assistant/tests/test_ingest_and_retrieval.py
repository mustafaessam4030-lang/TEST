"""Ingestion status, duplicates, re-indexing, revisions and retrieval behaviour."""
import pytest

from eka.ingest import UploadError


def test_document_statuses(docs):
    assert docs["HX-200_Service_Manual_RevB.pdf"]["status"] == "ready"
    assert docs["TL-5_Inspection_Checklist_scanned.pdf"]["status"] == "ready"
    assert docs["corrupted.pdf"]["status"] == "failed"
    assert "corrupted" in docs["corrupted.pdf"]["error"]
    assert docs["blank.pdf"]["status"] == "failed"
    assert "no text could be extracted" in docs["blank.pdf"]["error"]


def test_page_status_is_recorded_for_every_page(assistant, docs):
    pages = assistant.store.pages(docs["HX-200_Service_Manual_RevB.pdf"]["id"])
    assert [p["page_no"] for p in pages] == [1, 2, 3, 4, 5, 6]
    assert pages[4]["status"] == "empty"


def test_duplicate_upload_returns_existing_document(assistant, samples, docs):
    before = len(assistant.store.documents())
    doc, duplicate = assistant.ingest.submit((samples / "CV-90_Gearbox_Datasheet.pdf").read_bytes(), "again.pdf")
    assert duplicate and doc["id"] == docs["CV-90_Gearbox_Datasheet.pdf"]["id"]
    assert len(assistant.store.documents()) == before


def test_reindex_does_not_duplicate_chunks(assistant, docs):
    doc_id = docs["CV-90_Gearbox_Datasheet.pdf"]["id"]
    count = lambda: sum(1 for c in assistant.store.all_chunks() if c["doc_id"] == doc_id)
    before = count()
    assistant.ingest.reindex(doc_id)
    assert assistant.ingest.wait_idle(60)
    assert count() == before > 0
    assert assistant.store.document(doc_id)["status"] == "ready"


def test_upload_validation(assistant):
    with pytest.raises(UploadError) as err:
        assistant.ingest.submit(b"not a pdf", "x.pdf")
    assert err.value.status == 415
    with pytest.raises(UploadError):
        assistant.ingest.submit(b"", "empty.pdf")


def test_older_revision_is_superseded(docs):
    rev_a, rev_b = docs["HX-200_Service_Manual_RevA.pdf"], docs["HX-200_Service_Manual_RevB.pdf"]
    assert rev_a["superseded_by"] == rev_b["id"]
    assert rev_b["superseded_by"] == ""


def test_hybrid_retrieval_finds_table_row(assistant, docs):
    result = assistant.retriever.search("torque for the port cover bolts on the HX-200")
    rev_b = docs["HX-200_Service_Manual_RevB.pdf"]["id"]
    top3 = [c.chunk for c in result.selected[:3]]
    row = next(c for c in top3 if c.kind == "table_row")
    assert row.doc_id == rev_b and row.page_no == 2 and "Port cover bolt" in row.text


def test_document_filter_is_applied_before_scoring(assistant, docs):
    cv90 = docs["CV-90_Gearbox_Datasheet.pdf"]["id"]
    result = assistant.retriever.search("mounting flange bolt torque", doc_ids=[cv90])
    assert result.searched_doc_ids == [cv90]
    assert result.selected and all(c.chunk.doc_id == cv90 for c in result.selected + result.rejected)


def test_unknown_identifier_is_reported_and_nothing_selected(assistant):
    result = assistant.retriever.search("rated pressure of the HX-300 pump")
    assert result.missing_identifiers == ["HX-300"]
    assert result.selected == []
    assert all("HX-300" in c.decision for c in result.rejected)


def test_trace_explains_rejections(assistant):
    trace = assistant.retriever.search("relief valve cartridge part number").trace()
    assert trace["selected"] and all(s["decision"] == "selected" for s in trace["selected"])
    assert all(r["decision"].startswith("rejected:") for r in trace["rejected"])
    assert {"bm25", "cosine", "coverage", "page", "doc_id"} <= set(trace["selected"][0])
