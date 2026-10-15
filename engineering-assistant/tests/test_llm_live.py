"""The language-model path against a REAL local model (Ollama).

Skipped unless EKA_TEST_OLLAMA_MODEL names a pulled model, e.g.

    EKA_TEST_OLLAMA_MODEL=qwen2.5:3b-instruct python -m pytest tests/test_llm_live.py

Nothing is mocked: the model's JSON goes through the same citation checks as in production.
"""
import os

import pytest

from eka.config import load_settings
from eka.service import Assistant
from eka.textutil import identifiers, numbers

MODEL = os.environ.get("EKA_TEST_OLLAMA_MODEL", "")
URL = os.environ.get("EKA_TEST_OLLAMA_URL", "http://127.0.0.1:11434")
pytestmark = pytest.mark.skipif(not MODEL, reason="set EKA_TEST_OLLAMA_MODEL to run against a real model")


@pytest.fixture(scope="module")
def llm(samples, tmp_path_factory):
    a = Assistant(load_settings(data_dir=tmp_path_factory.mktemp("llm"), generator="ollama", llm_model=MODEL,
                                ollama_url=URL, llm_timeout_s=300))
    ok, detail = a.engine.generator.health()
    if not ok:
        pytest.skip(detail)
    for path in sorted(samples.glob("*.pdf")):
        a.ingest.submit(path.read_bytes(), path.name)
    assert a.ingest.wait_idle(300)
    yield a
    a.close()


def test_model_answers_directly_with_a_validated_citation(llm):
    out = llm.engine.ask("What is the oil capacity of the CV-90?")
    assert out["status"] == "answered" and out["generator"] == "ollama:" + MODEL, out["fallback_reason"]
    first = out["claims"][0]
    assert "2.4 L" in first["text"] and first["citations"]
    assert not first["text"].lower().startswith("according to")


def test_model_keeps_value_and_condition(llm):
    out = llm.engine.ask("What is the rated pressure of the HX-200 pump?")
    assert out["status"] == "answered"
    text = " ".join(c["text"] for c in out["claims"])
    assert "350 bar" in text and "320 bar" not in text      # never the superseded revision as the answer


def test_model_does_not_invent_missing_values(llm):
    out = llm.engine.ask("What is the maximum lifting capacity of the TL-5?")
    assert out["status"] == "insufficient_evidence" and out["claims"] == []


def test_explanation_labels_general_knowledge_and_keeps_it_value_free(llm):
    out = llm.engine.ask("Explain why the HX-200 pump makes a whining noise under load")
    assert out["status"] == "answered"
    grounded = [c for c in out["claims"] if c["kind"] in ("direct", "inference")]
    assert grounded and all(c["citations"] for c in grounded)
    assert any("cavitation" in c["text"].lower() for c in grounded)
    for c in out["claims"]:
        if c["kind"] == "general":
            assert not numbers(c["text"]) and not identifiers(c["text"]) and c["citations"] == []


def test_simpler_terms_keeps_the_value(llm):
    first = llm.engine.ask("What is the oil capacity of the CV-90?")
    out = llm.engine.ask("Explain that in simpler terms", first["conversation_id"])
    assert out["status"] == "answered" and out["mode"] == "simplify"
    assert any("2.4" in c["text"] and c["citations"] for c in out["claims"])
