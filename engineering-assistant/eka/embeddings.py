"""Embedding back-ends behind one small interface.

    hashing    offline, deterministic, no model download. Signed feature hashing
               of words and character n-grams. It captures spelling-level
               similarity (plurals, partial identifiers), not meaning. Default,
               because it always works and needs nothing.
    fastembed  BAAI/bge-small-en-v1.5 through ONNX Runtime on CPU (~67 MB model,
               384 dimensions). Real semantic similarity. Optional dependency.
    ollama     any Ollama embedding model (default nomic-embed-text, 768 dims).

Every vector is L2-normalised so cosine similarity is a dot product. Vectors
are stored with the embedder's ``name``; when the configured embedder changes,
the index re-embeds stored chunks instead of mixing incompatible vectors.
"""
from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.request

import numpy as np

from .config import Settings
from .textutil import normalize

log = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    pass


def _normalise_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class Embedder:
    name: str = ""
    dim: int = 0
    # Cosine at which a passage is considered relevant on semantic similarity alone.
    strong_similarity: float = 1.01

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        raise NotImplementedError

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


class HashingEmbedder(Embedder):
    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.name = "hashing-v1-{0}".format(dim)
        # Hashing similarity tracks shared surface features, not meaning, so it is
        # never trusted on its own to declare a passage relevant.
        self.strong_similarity = 1.01

    def _features(self, text: str):
        words = normalize(text).lower().split()
        for word in words:
            word = word.strip(".,;:()[]\"'")
            if not word:
                continue
            yield "w:" + word, 1.0
            padded = "<{0}>".format(word)
            for n in (3, 4):
                for i in range(len(padded) - n + 1):
                    yield "c:" + padded[i:i + n], 0.35

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature, weight in self._features(text):
                digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
                h = int.from_bytes(digest, "little")
                matrix[row, h % self.dim] += weight if (h >> 63) & 1 else -weight
        # Sub-linear term frequency so one repeated word cannot dominate.
        return _normalise_rows(np.sign(matrix) * np.log1p(np.abs(matrix)))


class FastEmbedEmbedder(Embedder):
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", cache_dir: str | None = None):
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingError("fastembed is not installed: pip install -r requirements-optional.txt") from exc
        self._model = TextEmbedding(model_name=model, cache_dir=cache_dir)
        self.name = "fastembed:" + model
        self.dim = len(next(iter(self._model.query_embed(["probe"]))))
        self.strong_similarity = 0.80

    def embed_documents(self, texts):
        # Small batches: fastembed pads each batch, and its default of 256 passages
        # measured 3.5 GB peak memory on a 240-page manual; 16 keeps it near 250 MB.
        return _normalise_rows(np.array(list(self._model.passage_embed(texts, batch_size=16)), dtype=np.float32))

    def embed_query(self, text):
        return _normalise_rows(np.array(list(self._model.query_embed([text])), dtype=np.float32))[0]


class OllamaEmbedder(Embedder):
    def __init__(self, url: str, model: str = "nomic-embed-text", timeout: float = 60.0):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.name = "ollama:" + model
        # nomic-embed-text is trained with task prefixes.
        self._prefix = ("search_document: ", "search_query: ") if "nomic" in model else ("", "")
        self.dim = len(self._call([self._prefix[1] + "probe"])[0])
        self.strong_similarity = 0.80

    def _call(self, inputs: list[str]) -> list[list[float]]:
        body = json.dumps({"model": self.model, "input": inputs}).encode()
        request = urllib.request.Request(self.url + "/api/embed", data=body,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise EmbeddingError("Ollama embedding call failed: {0}".format(exc)) from exc
        vectors = payload.get("embeddings")
        if not vectors or len(vectors) != len(inputs):
            raise EmbeddingError("Ollama returned no embeddings for model " + self.model)
        return vectors

    def embed_documents(self, texts):
        out = []
        for start in range(0, len(texts), 32):
            out.extend(self._call([self._prefix[0] + t for t in texts[start:start + 32]]))
        return _normalise_rows(np.array(out, dtype=np.float32))

    def embed_query(self, text):
        return _normalise_rows(np.array(self._call([self._prefix[1] + text]), dtype=np.float32))[0]


def make_embedder(settings: Settings) -> Embedder:
    kind = settings.embedder.lower()
    if kind == "hashing":
        return HashingEmbedder()
    if kind == "fastembed":
        return FastEmbedEmbedder(settings.embed_model or "BAAI/bge-small-en-v1.5",
                                 str(settings.data_dir / "models"))
    if kind == "ollama":
        return OllamaEmbedder(settings.ollama_url, settings.embed_model or "nomic-embed-text")
    raise EmbeddingError("unknown EKA_EMBEDDER: " + settings.embedder)
