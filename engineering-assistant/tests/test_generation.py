"""The Ollama generator, tested against a stand-in HTTP server (no real model here).

Covers: valid claims accepted with real citations, invented values and invented
source ids rejected, empty/malformed/slow responses falling back to the
extractive answer instead of producing an empty answer.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eka.answer import AnswerEngine
from eka.conversation import Conversations
from eka.generation import OllamaGenerator


class FakeOllama:
    def __init__(self):
        self.reply = None      # dict -> JSON content; str -> raw content; callable(body) -> either
        self.delay = 0.0
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self._send({"models": [{"name": "qwen2.5:7b-instruct"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                if outer.delay:
                    threading.Event().wait(outer.delay)
                reply = outer.reply(body) if callable(outer.reply) else outer.reply
                content = reply if isinstance(reply, str) else json.dumps(reply)
                try:
                    self._send({"message": {"role": "assistant", "content": content}})
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _send(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:{0}".format(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture(scope="module")
def fake():
    server = FakeOllama()
    yield server
    server.server.shutdown()


@pytest.fixture
def engine(assistant, fake):
    fake.reply, fake.delay, fake.requests[:] = None, 0.0, []
    generator = OllamaGenerator(fake.url, "qwen2.5:7b-instruct", timeout_s=2, retries=0)
    return AnswerEngine(assistant.settings, assistant.store, assistant.retriever, Conversations(), generator)


def source_id(body, needle):
    """Find the S-number the prompt gave to the passage containing ``needle``."""
    prompt = body["messages"][1]["content"]
    for block in prompt.split("\n\n["):
        if needle in block:
            return block.lstrip("[").split("]")[0]
    raise AssertionError("passage not in prompt: " + needle)


def test_health_reports_model(fake):
    ok, detail = OllamaGenerator(fake.url, "qwen2.5:7b-instruct").health()
    assert ok, detail
    ok, detail = OllamaGenerator(fake.url, "qwen3:8b").health()
    assert not ok and "not pulled" in detail


def test_valid_model_claims_are_used_with_real_citations(engine, fake):
    fake.reply = lambda body: {"status": "answered", "claims": [
        {"text": "The HX-200 rated pressure is 350 bar (5076 psi).",
         "sources": [source_id(body, "350 bar")], "kind": "direct"}]}
    out = engine.ask("What is the rated pressure of the HX-200?")
    assert out["generator"] == "ollama:qwen2.5:7b-instruct" and out["fallback_reason"] == ""
    assert out["claims"][0]["text"].startswith("The HX-200 rated pressure is 350 bar")
    assert out["citations"][0]["page"] == 2
    request = fake.requests[0]
    assert request["options"]["temperature"] == 0 and request["format"]["type"] == "object"


def test_invented_value_is_rejected(engine, fake):
    fake.reply = lambda body: {"status": "answered", "claims": [
        {"text": "The rated pressure is 350 bar.", "sources": [source_id(body, "350 bar")], "kind": "direct"},
        {"text": "The burst pressure is 900 bar.", "sources": [source_id(body, "350 bar")], "kind": "direct"}]}
    out = engine.ask("What is the rated pressure of the HX-200?")
    assert "900" not in out["answer_text"]
    assert any("900" in r.get("text", "") for r in out["rejected_claims"])


def test_invented_source_id_falls_back_to_extractive(engine, fake):
    fake.reply = {"status": "answered", "claims": [
        {"text": "The rated pressure is 350 bar.", "sources": ["S99"], "kind": "direct"}]}
    out = engine.ask("What is the rated pressure of the HX-200?")
    assert out["generator"] == "extractive" and "citation checks" in out["fallback_reason"]
    assert "350 bar" in out["claims"][0]["text"]


@pytest.mark.parametrize("reply, reason", [
    ("", "empty response"),
    ("I think it is about 350 bar", "not JSON"),
    ('{"status": "maybe", "claims": []}', "valid status"),
])
def test_bad_model_output_falls_back(engine, fake, reply, reason):
    fake.reply = reply
    out = engine.ask("What is the rated pressure of the HX-200?")
    assert out["generator"] == "extractive" and reason in out["fallback_reason"]
    assert out["status"] == "answered" and out["claims"]


def test_timeout_falls_back(engine, fake):
    fake.delay = 3.0
    fake.reply = {"status": "answered", "claims": []}
    out = engine.ask("What is the rated pressure of the HX-200?")
    assert out["generator"] == "extractive" and out["fallback_reason"]
    assert out["claims"]


def test_model_that_declines_does_not_hide_validated_evidence(engine, fake):
    """Small models sometimes decline answerable questions; the quoted, validated answer is shown instead."""
    fake.reply = {"status": "insufficient_evidence", "claims": []}
    out = engine.ask("What is the rated pressure of the HX-200?")
    assert fake.requests                                  # the model was asked
    assert out["status"] == "answered" and out["generator"] == "extractive"
    assert "350 bar" in out["claims"][0]["text"] and "did not answer" in out["fallback_reason"]


def test_model_abstention_stands_when_there_is_no_evidence(engine, fake):
    out = engine.ask("What is the warranty period of the HX-200?")
    assert out["status"] == "insufficient_evidence" and out["claims"] == []


def test_no_model_call_without_evidence(engine, fake):
    out = engine.ask("What is the rated pressure of the HX-300?")
    assert out["status"] == "insufficient_evidence" and fake.requests == []


def test_unreachable_ollama_falls_back(assistant):
    generator = OllamaGenerator("http://127.0.0.1:9", "qwen2.5:7b-instruct", timeout_s=1, retries=0)
    engine = AnswerEngine(assistant.settings, assistant.store, assistant.retriever, Conversations(), generator)
    out = engine.ask("What is the dry weight of the HX-200?")
    assert out["generator"] == "extractive" and "48 kg" in out["answer_text"]
