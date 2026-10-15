"""HTTP API end to end through FastAPI's TestClient (real ingestion, no network)."""
import time

import pytest
from fastapi.testclient import TestClient

from eka.api import create_app
from eka.config import load_settings


@pytest.fixture(scope="module")
def client(samples, tmp_path_factory):
    settings = load_settings(data_dir=tmp_path_factory.mktemp("api"), max_upload_mb=1)
    with TestClient(create_app(settings)) as c:
        yield c


def upload(client, path, name=None):
    return client.post("/api/documents", params={"filename": name or path.name}, content=path.read_bytes(),
                       headers={"Content-Type": "application/pdf"})


def wait_ready(client, doc_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        doc = client.get("/api/documents/" + doc_id).json()["document"]
        if doc["status"] not in ("queued", "processing"):
            return doc
        time.sleep(0.1)
    raise AssertionError("document did not finish processing")


def test_ui_and_health(client):
    page = client.get("/").text
    assert "<title>Luma" in page and "Mantrac Engineering Intelligence" in page
    health = client.get("/api/health").json()
    assert health["generator"] == "extractive" and health["embedder"].startswith("hashing")


def test_upload_process_chat_and_view(client, samples):
    r = upload(client, samples / "CV-90_Gearbox_Datasheet.pdf")
    assert r.status_code == 202
    doc_id = r.json()["document"]["id"]
    doc = wait_ready(client, doc_id)
    assert doc["status"] == "ready" and doc["page_count"] == 1 and doc["revision"] == "1"

    dup = upload(client, samples / "CV-90_Gearbox_Datasheet.pdf", "renamed.pdf")
    assert dup.status_code == 200 and dup.json()["duplicate"]

    pages = client.get("/api/documents/" + doc_id).json()["pages"]
    assert pages == [dict(pages[0], page_no=1, status="ok")]

    answer = client.post("/api/chat", json={"message": "What is the oil capacity of the CV-90?"}).json()
    assert answer["status"] == "answered" and "2.4 L" in answer["answer_text"]
    citation = answer["citations"][0]
    image = client.get(citation["page_image_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
    pdf = client.get("/api/documents/{0}/file".format(doc_id))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")

    follow = client.post("/api/chat", json={"message": "and the output speed?",
                                            "conversation_id": answer["conversation_id"]}).json()
    assert "45 rpm" in follow["answer_text"]


def test_upload_errors(client, samples):
    assert client.post("/api/documents", content=b"plain text").status_code == 415
    assert client.post("/api/documents", content=b"").status_code == 400
    big = b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 10)
    assert client.post("/api/documents", content=big).status_code == 413
    r = upload(client, samples / "corrupted.pdf")
    doc = wait_ready(client, r.json()["document"]["id"])
    assert doc["status"] == "failed" and "corrupted" in doc["error"]


def test_not_found_and_validation(client):
    assert client.get("/api/documents/0123456789abcdef").status_code == 404
    assert client.get("/api/documents/../../etc/passwd/file").status_code == 404
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_delete_document(client, samples):
    r = upload(client, samples / "HX-200_Service_Manual_RevA.pdf")
    doc_id = r.json()["document"]["id"]
    wait_ready(client, doc_id)
    assert client.delete("/api/documents/" + doc_id).status_code == 200
    assert client.get("/api/documents/" + doc_id).status_code == 404


def test_token_is_required_when_configured(samples, tmp_path):
    settings = load_settings(data_dir=tmp_path, api_token="s3cret")
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/documents").status_code == 401
        assert c.get("/api/documents", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/documents", headers={"Authorization": "Bearer s3cret"}).status_code == 200
