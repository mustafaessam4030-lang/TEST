"""Streaming, conversation history, retry, feedback, document search and suggestions.

Real components end to end through FastAPI's TestClient: PyMuPDF, tesseract OCR,
SQLite and the answer engine. No mocks.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from eka.api import create_app
from eka.config import load_settings


@pytest.fixture(scope="module")
def client(samples, tmp_path_factory):
    settings = load_settings(data_dir=tmp_path_factory.mktemp("conv"), model_dir=str(tmp_path_factory.mktemp("nomodel")))
    with TestClient(create_app(settings)) as c:
        for path in sorted(samples.glob("*.pdf")):
            c.post("/api/documents", params={"filename": path.name}, content=path.read_bytes())
        deadline = time.time() + 120
        while time.time() < deadline:
            if all(d["status"] not in ("queued", "processing") for d in c.get("/api/documents").json()["documents"]):
                break
            time.sleep(0.1)
        yield c


def stream(client, **body):
    with client.stream("POST", "/api/chat/stream", json=body) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        lines = list(r.iter_lines())
    events = []
    for kind, data in zip([l for l in lines if l.startswith("event: ")], [l for l in lines if l.startswith("data: ")]):
        events.append((kind[7:], json.loads(data[6:])))
    return events


def doc_id(client, prefix):
    return next(d["id"] for d in client.get("/api/documents").json()["documents"] if d["filename"].startswith(prefix))


def test_health_reports_keyword_mode_honestly(client):
    health = client.get("/api/health").json()
    assert health["semantic_search"] is False
    assert health["retrieval_mode"].startswith("Keyword only")


def test_stream_emits_real_stages_then_a_validated_answer(client):
    events = stream(client, message="What is the rated pressure of the HX-200?")
    kinds = [k for k, _ in events]
    assert kinds[0] == "stage" and kinds[-1] == "answer" and kinds.count("answer") == 1
    stages = [d["text"] for k, d in events if k == "stage"]
    assert any(s.startswith("Searching") for s in stages)
    assert any(s.startswith("Reading") for s in stages)
    assert "Checking every citation against its page" in stages
    answer = events[-1][1]
    assert answer["status"] == "answered" and answer["citations"][0]["page"] == 2
    assert answer["message_id"] and answer["conversation_id"]


def test_streamed_answer_is_saved_and_history_reloads(client):
    first = stream(client, message="What is the rated pressure of the HX-200?")[-1][1]
    cid = first["conversation_id"]
    follow = stream(client, message="And its dry weight?", conversation_id=cid)[-1][1]
    assert "48 kg" in follow["answer_text"]
    conv = client.get("/api/conversations/" + cid).json()
    assert conv["conversation"]["title"] == "What is the rated pressure of the HX-200?"
    assert [t["question"] for t in conv["turns"]] == ["What is the rated pressure of the HX-200?", "And its dry weight?"]
    assert conv["turns"][1]["answer"]["claims"][0]["citations"]


def test_history_search_rename_delete(client):
    cid = stream(client, message="What is the gear ratio of the CV-90?")[-1][1]["conversation_id"]
    found = client.get("/api/conversations", params={"q": "gear ratio"}).json()["conversations"]
    assert cid in [c["id"] for c in found]
    assert client.get("/api/conversations", params={"q": "zzz-no-such"}).json()["conversations"] == []
    assert client.patch("/api/conversations/" + cid, json={"title": "CV-90 ratio"}).json()["conversation"]["title"] == "CV-90 ratio"
    assert client.delete("/api/conversations/" + cid).status_code == 200
    assert client.get("/api/conversations/" + cid).status_code == 404


def test_follow_up_survives_a_restart_of_conversation_memory(client):
    cid = stream(client, message="What is the gear ratio of the CV-90?")[-1][1]["conversation_id"]
    client.app.state.assistant.conversations.forget(cid)          # as after a server restart
    follow = stream(client, message="And its oil capacity?", conversation_id=cid)[-1][1]
    assert "2.4 L" in follow["answer_text"]


def test_retry_replaces_the_answer(client):
    first = stream(client, message="What is the output speed of the CV-90?")[-1][1]
    cid = first["conversation_id"]
    again = stream(client, message=first["question"], conversation_id=cid, retry_of=first["message_id"])[-1][1]
    turns = client.get("/api/conversations/" + cid).json()["turns"]
    assert [t["message_id"] for t in turns] == [again["message_id"]]
    bad = client.post("/api/chat/stream", json={"message": "x", "conversation_id": cid, "retry_of": "0" * 16})
    assert bad.status_code == 404


def phases(events):
    return [d["phase"] for k, d in events if k == "stage"]


def test_stream_phases_follow_the_real_pipeline(client):
    """Luma's mascot follows these phases; each must come from the step that actually ran, in order."""
    assert phases(stream(client, message="What is the oil capacity of the CV-90?")) == \
        ["understanding", "searching", "reading", "writing", "checking"]
    # nothing to search for: no search or writing phase is claimed
    assert phases(stream(client, message="hello")) == ["understanding"]
    # an unanswerable question searches and reads, then stops: it never claims to write an answer
    assert phases(stream(client, message="What is the warranty period of the HX-200?")) == \
        ["understanding", "searching", "reading", "checking"]
    assert phases(stream(client, message="Summarize the CV-90 datasheet")) == ["understanding", "reading", "analyzing"]
    every = {p for p in phases(stream(client, message="Compare the mounting flange bolt torque of the HX-200 "
                                                        "and the CV-90"))}
    assert "analyzing" in every and set(every) <= {"understanding", "searching", "reading", "analyzing", "writing",
                                                     "checking"}


def test_stage_delay_is_off_by_default(settings):
    assert settings.debug_stage_delay_ms == 0


def test_feedback(client):
    answer = stream(client, message="What is the output speed of the CV-90?")[-1][1]
    assert client.post("/api/messages/{0}/feedback".format(answer["message_id"]), json={"value": "down"}).status_code == 200
    turns = client.get("/api/conversations/" + answer["conversation_id"]).json()["turns"]
    assert turns[0]["feedback"] == "down"
    assert client.post("/api/messages/{0}/feedback".format(answer["message_id"]), json={"value": "meh"}).status_code == 422


def test_clarification_options_scope_the_follow_up(client):
    ask = stream(client, message="What torque for the mounting flange bolts?")[-1][1]
    assert ask["status"] == "needs_clarification" and ask["claims"] == [] and ask["safety_notice"] == ""
    option = next(o for o in ask["clarification"]["options"] if "CV-90" in o["label"])
    answer = stream(client, message=option["label"], conversation_id=ask["conversation_id"],
                    document_ids=[option["doc_id"]])[-1][1]
    assert answer["status"] == "answered" and "180" in answer["answer_text"] and "210" not in answer["answer_text"]


def test_document_search_native_and_ocr(client):
    hx = doc_id(client, "HX-200_Service_Manual_RevB")
    results = client.get("/api/documents/{0}/search".format(hx), params={"q": "relief valve"}).json()["results"]
    assert {r["page"] for r in results} == {2, 4, 7}   # specs, troubleshooting, hydraulic circuit
    assert all(r["hits"] >= 1 and "elief valve" in r["snippet"] for r in results)
    tl5 = doc_id(client, "TL-5")
    ocr = client.get("/api/documents/{0}/search".format(tl5), params={"q": "track sag"}).json()["results"]
    assert ocr == [dict(ocr[0], page=1, ocr=True)]
    assert client.get("/api/documents/{0}/search".format(hx), params={"q": "x"}).status_code == 400
    png = client.get("/api/documents/{0}/pages/4/image".format(hx), params={"q": "relief valve"})
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"


def test_suggestions_are_answerable(client):
    suggestions = client.get("/api/suggestions").json()["suggestions"]
    assert 1 <= len(suggestions) <= 4
    for s in suggestions:
        answer = client.post("/api/chat", json={"message": s}).json()
        assert answer["status"] == "answered" and answer["citations"], s
