"""Embedding back-ends: honest labelling, offline-only auto mode, and a REAL semantic model.

The fastembed tests load the actual BAAI/bge-small-en-v1.5 ONNX model. They are
skipped (with the reason) when fastembed or the model files are not available
locally; they never download anything. Point EKA_TEST_MODEL_DIR at a directory
containing the model (or run once with EKA_EMBEDDER=fastembed) to run them.
"""
import os

import numpy as np
import pytest

from eka.config import load_settings
from eka.embeddings import EmbeddingError, FastEmbedEmbedder, HashingEmbedder, make_embedder

MODEL_DIR = os.environ.get("EKA_TEST_MODEL_DIR", "")


def real_model():
    if not MODEL_DIR:
        pytest.skip("EKA_TEST_MODEL_DIR not set: no local embedding model to test against")
    try:
        return FastEmbedEmbedder("BAAI/bge-small-en-v1.5", MODEL_DIR, offline=True)
    except EmbeddingError as exc:
        pytest.skip("real embedding model not available offline: {0}".format(exc))


def test_hashing_is_labelled_keyword_not_semantic():
    e = HashingEmbedder()
    assert e.semantic is False
    assert "Keyword only" in e.label and "semantic model" in e.label


def test_auto_without_a_local_model_falls_back_to_keyword_without_downloading(tmp_path):
    settings = load_settings(data_dir=tmp_path, model_dir=str(tmp_path / "empty-models"))
    e = make_embedder(settings)
    assert isinstance(e, HashingEmbedder) and not e.semantic
    # nothing was fetched into the model directory
    assert not any((tmp_path / "empty-models").rglob("*.onnx"))


def test_offline_fastembed_refuses_to_download(tmp_path):
    pytest.importorskip("fastembed")
    with pytest.raises(EmbeddingError, match="not available offline"):
        FastEmbedEmbedder("BAAI/bge-small-en-v1.5", str(tmp_path / "nothing"), offline=True)


def test_real_model_produces_normalised_384_dim_vectors():
    e = real_model()
    assert e.semantic and e.dim == 384
    v = e.embed_documents(["Parameter: Oil capacity | Value: 2.4 L", "Output speed 45 rpm"])
    assert v.shape == (2, 384)
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-4)
    assert not np.allclose(v[0], v[1])


def test_real_model_ranks_by_meaning_where_keywords_cannot():
    """'hold' shares no word with 'capacity'; the semantic model still ranks the capacity row first."""
    query = "How much oil does the gearbox hold?"
    passages = ["Parameter: Oil capacity | Value: 2.4 L", "Parameter: Output speed | Value: 45 rpm",
                "Parameter: Gear ratio | Value: 32:1"]
    real = real_model()
    scores = real.embed_documents(passages) @ real.embed_query(query)
    assert int(np.argmax(scores)) == 0
    # The hashing embedder has no notion of meaning: it is not expected to agree.
    hashed = HashingEmbedder()
    assert hashed.semantic is False
    _ = hashed.embed_documents(passages) @ hashed.embed_query(query)
