"""Phase 1 source: the PDF folder. Real files, real ingestion, no mocks."""
import os
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from eka.api import create_app
from eka.config import load_config_file, load_settings
from eka.service import Assistant


def make(tmp_path, inbox, **kw):
    settings = load_settings(data_dir=tmp_path / "data", inbox_dir=str(inbox), inbox_settle_s=kw.pop("settle", 0.0),
                             inbox_scan_s=3600, **kw)
    return Assistant(settings, start_sources=False)     # the test decides when to scan


def scan(a):
    status = a.sources[0].scan()
    assert a.ingest.wait_idle(120)
    return status


def by_source(a):
    return {d["source"]: d for d in a.store.documents() if d["source"]}


@pytest.fixture
def inbox(tmp_path):
    path = tmp_path / "pdfs"
    path.mkdir()
    return path


def test_new_files_are_indexed_and_answerable(tmp_path, inbox, samples):
    shutil.copy(samples / "CV-90_Gearbox_Datasheet.pdf", inbox)
    (inbox / "pumps").mkdir()
    shutil.copy(samples / "HX-200_Service_Manual_RevB.pdf", inbox / "pumps")
    (inbox / "training.mp4").write_bytes(b"\x00" * 64)
    (inbox / "notes.docx").write_bytes(b"x")
    a = make(tmp_path, inbox)
    try:
        st = scan(a)
        assert st["added"] == 2 and st["pdfs"] == 2 and st["error"] == ""
        reasons = {s["name"]: s["reason"] for s in st["skipped"]}
        assert "phase 2" in reasons["training.mp4"] and "only PDF" in reasons["notes.docx"]
        docs = by_source(a)
        assert set(docs) == {"folder:CV-90_Gearbox_Datasheet.pdf", "folder:pumps/HX-200_Service_Manual_RevB.pdf"}
        assert all(d["status"] == "ready" for d in docs.values())
        answer = a.engine.ask("What is the oil capacity of the CV-90?")
        assert answer["status"] == "answered" and "2.4 L" in answer["answer_text"]
        assert scan(a)["indexed"] == 2
    finally:
        a.close()


def test_unchanged_files_are_not_read_again(tmp_path, inbox, samples, monkeypatch):
    shutil.copy(samples / "CV-90_Gearbox_Datasheet.pdf", inbox)
    a = make(tmp_path, inbox)
    try:
        scan(a)
        reads = []
        source = a.sources[0].source
        original = source.fetch
        monkeypatch.setattr(source, "fetch", lambda item: reads.append(item.key) or original(item))
        st = scan(a)
        assert reads == [] and st["added"] == st["updated"] == 0
        assert len(a.store.documents()) == 1
    finally:
        a.close()


def test_replaced_file_supersedes_the_old_copy(tmp_path, inbox, samples):
    target = inbox / "manual.pdf"
    shutil.copy(samples / "HX-200_Service_Manual_RevA.pdf", target)
    a = make(tmp_path, inbox)
    try:
        scan(a)
        old = by_source(a)["folder:manual.pdf"]
        shutil.copy(samples / "HX-200_Service_Manual_RevB.pdf", target)
        os.utime(target, (time.time() - 30, time.time() - 30))
        assert scan(a)["updated"] == 1
        st = scan(a)                                   # older copy removed once the new one is ready
        docs = a.store.documents_by_source("folder:manual.pdf")
        assert len(docs) == 1 and docs[0]["id"] != old["id"] and docs[0]["revision"] == "B"
        assert a.store.document(old["id"]) is None and st["removed"] >= 0
        answer = a.engine.ask("What is the rated pressure of the HX-200?")
        assert "350 bar" in answer["answer_text"] and "320 bar" not in answer["answer_text"]
    finally:
        a.close()


def test_old_copy_is_removed_soon_after_the_new_one_is_ready(tmp_path, inbox, samples):
    """The background sync re-checks quickly while a replacement indexes (interval here is an hour)."""
    target = inbox / "manual.pdf"
    shutil.copy(samples / "HX-200_Service_Manual_RevA.pdf", target)
    a = make(tmp_path, inbox)
    try:
        scan(a)
        sync = a.sources[0]
        shutil.copy(samples / "HX-200_Service_Manual_RevB.pdf", target)
        os.utime(target, (time.time() - 30, time.time() - 30))
        sync.start()                                   # first loop pass picks up the change
        deadline = time.time() + 60
        while time.time() < deadline:
            docs = a.store.documents_by_source("folder:manual.pdf")
            if len(docs) == 1 and docs[0]["revision"] == "B" and docs[0]["status"] == "ready":
                break
            time.sleep(0.2)
        docs = a.store.documents_by_source("folder:manual.pdf")
        assert len(docs) == 1 and docs[0]["revision"] == "B" and docs[0]["status"] == "ready"
    finally:
        a.close()


def test_file_still_being_copied_waits(tmp_path, inbox, samples):
    shutil.copy(samples / "CV-90_Gearbox_Datasheet.pdf", inbox)
    a = make(tmp_path, inbox, settle=60.0)
    try:
        st = scan(a)
        assert st["pending"] == 1 and st["added"] == 0 and a.store.documents() == []
    finally:
        a.close()


def test_bad_files_are_reported_not_retried(tmp_path, inbox, samples, monkeypatch):
    shutil.copy(samples / "corrupted.pdf", inbox)
    (inbox / "renamed.pdf").write_bytes(b"this is not a pdf")
    a = make(tmp_path, inbox)
    try:
        st = scan(a)
        assert any(e["name"] == "renamed.pdf" and "PDF" in e["reason"] for e in st["errors"])
        corrupted = by_source(a)["folder:corrupted.pdf"]
        assert corrupted["status"] == "failed" and "corrupted" in corrupted["error"]
        reads = []
        source = a.sources[0].source
        original = source.fetch
        monkeypatch.setattr(source, "fetch", lambda item: reads.append(item.key) or original(item))
        st = scan(a)
        assert reads == [] and any(e["name"] == "renamed.pdf" for e in st["errors"])
    finally:
        a.close()


def test_same_content_is_not_indexed_twice(tmp_path, inbox, samples):
    a = make(tmp_path, inbox)
    try:
        uploaded, _ = a.ingest.submit((samples / "CV-90_Gearbox_Datasheet.pdf").read_bytes(), "upload.pdf")
        assert a.ingest.wait_idle(60)
        shutil.copy(samples / "CV-90_Gearbox_Datasheet.pdf", inbox / "a.pdf")
        shutil.copy(samples / "CV-90_Gearbox_Datasheet.pdf", inbox / "b.pdf")
        st = scan(a)
        docs = a.store.documents()
        assert len(docs) == 1 and docs[0]["id"] == uploaded["id"] and docs[0]["source"] == "folder:a.pdf"
        assert any(s["name"] == "b.pdf" and "same content" in s["reason"] for s in st["skipped"])
        assert any(s["name"] == "b.pdf" and "same content" in s["reason"] for s in scan(a)["skipped"])  # still shown
        # a file whose content is replaced by a copy of another file: its old version is dropped
        shutil.copy(samples / "HX-200_Service_Manual_RevB.pdf", inbox / "c.pdf")
        scan(a)
        assert "folder:c.pdf" in by_source(a)
        shutil.copy(samples / "CV-90_Gearbox_Datasheet.pdf", inbox / "c.pdf")
        os.utime(inbox / "c.pdf", (time.time() - 60, time.time() - 60))
        st = scan(a)
        assert "folder:c.pdf" not in by_source(a) and st["removed"] == 1
        assert any(s["name"] == "c.pdf" and "same content" in s["reason"] for s in st["skipped"])
        # and once it gets content of its own again, it is indexed
        shutil.copy(samples / "HX-200_Service_Manual_RevA.pdf", inbox / "c.pdf")
        st = scan(a)
        assert by_source(a)["folder:c.pdf"]["status"] == "ready" and not any(s["name"] == "c.pdf" for s in st["skipped"])
    finally:
        a.close()


def test_deleted_files_kept_by_default_removed_when_enabled(tmp_path, inbox, samples):
    shutil.copy(samples / "CV-90_Gearbox_Datasheet.pdf", inbox)
    a = make(tmp_path, inbox)
    try:
        scan(a)
        (inbox / "CV-90_Gearbox_Datasheet.pdf").unlink()
        scan(a)
        assert len(a.store.documents()) == 1
        a.sources[0].remove_deleted = True
        assert scan(a)["removed"] == 1 and a.store.documents() == []
    finally:
        a.close()


def test_missing_folder_is_reported(tmp_path, inbox):
    a = make(tmp_path, inbox)
    try:
        shutil.rmtree(inbox)
        st = scan(a)
        assert "does not exist" in st["error"]
    finally:
        a.close()


def test_restart_does_not_reindex(tmp_path, inbox, samples, monkeypatch):
    shutil.copy(samples / "CV-90_Gearbox_Datasheet.pdf", inbox)
    a = make(tmp_path, inbox)
    scan(a)
    a.close()
    b = make(tmp_path, inbox)
    try:
        reads = []
        source = b.sources[0].source
        original = source.fetch
        monkeypatch.setattr(source, "fetch", lambda item: reads.append(item.key) or original(item))
        st = scan(b)
        assert reads == [] and st["indexed"] == 1 and len(b.store.documents()) == 1
    finally:
        b.close()


def test_config_env_file(tmp_path, monkeypatch):
    cfg = tmp_path / "config.env"
    cfg.write_text("# comment\nEKA_PORT=9123\nEKA_LLM_MODEL=\"qwen3:4b-instruct\"\nEKA_TOP_K=9\nOTHER=ignored\n"
                   "EKA_DATA_DIR=data\nEKA_INBOX_DIR=pdfs\nEKA_MODEL_DIR=models\n")
    for key in ("EKA_PORT", "EKA_LLM_MODEL", "EKA_DATA_DIR", "EKA_INBOX_DIR", "EKA_MODEL_DIR"):
        monkeypatch.setenv(key, "x")                  # recorded, so teardown restores the original state
        monkeypatch.delenv(key)
    monkeypatch.setenv("EKA_TOP_K", "3")              # a real environment variable wins
    monkeypatch.chdir(tmp_path.parent)                # relative paths follow the config file, not the cwd
    assert load_config_file(cfg) == cfg.resolve()
    s = load_settings()
    assert (s.port, s.llm_model, s.top_k) == (9123, "qwen3:4b-instruct", 3)
    assert (s.data_dir, s.inbox_path, s.models_dir) == (tmp_path / "data", tmp_path / "pdfs", tmp_path / "models")
    assert "OTHER" not in os.environ


def test_sources_api(tmp_path, inbox, samples):
    settings = load_settings(data_dir=tmp_path / "api", inbox_dir=str(inbox), inbox_settle_s=0.0, inbox_scan_s=3600)
    with TestClient(create_app(settings)) as c:
        shutil.copy(samples / "CV-90_Gearbox_Datasheet.pdf", inbox)
        st = c.post("/api/sources/scan").json()["sources"][0]
        assert st["source"] == "folder" and st["location"] == str(inbox.resolve())
        deadline = time.time() + 60
        while time.time() < deadline and c.get("/api/documents").json()["documents"][0]["status"] != "ready":
            time.sleep(0.1)
        doc = c.get("/api/documents").json()["documents"][0]
        assert doc["source"] == "folder:CV-90_Gearbox_Datasheet.pdf" and doc["status"] == "ready"
        assert c.get("/api/health").json()["sources"][0]["location"] == str(inbox.resolve())
