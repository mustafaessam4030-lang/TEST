"""Composition root: builds every component from Settings."""
from __future__ import annotations

import logging

from . import ocr
from .answer import AnswerEngine
from .config import Settings
from .conversation import Conversations
from .embeddings import make_embedder
from .generation import OllamaGenerator
from .ingest import IngestService
from .retrieval import HybridIndex, Retriever
from .store import Store

log = logging.getLogger(__name__)


class Assistant:
    def __init__(self, settings: Settings, start_worker: bool = True):
        settings.ensure_dirs()
        self.settings = settings
        self.store = Store(settings.db_path)
        self.embedder = make_embedder(settings)
        self.index = HybridIndex(self.store, self.embedder)
        self.index.rebuild()
        self.retriever = Retriever(self.index, settings.top_k, settings.candidate_pool,
                                   settings.min_coverage, settings.min_semantic)
        self.generator = None
        if settings.generator.lower() == "ollama":
            self.generator = OllamaGenerator(settings.ollama_url, settings.llm_model, settings.llm_timeout_s,
                                             settings.llm_retries, settings.llm_num_ctx)
            ok, detail = self.generator.health()
            if not ok:
                log.warning("LLM generator configured but not ready (%s); answers fall back to extractive", detail)
        elif settings.generator.lower() != "extractive":
            raise ValueError("unknown EKA_GENERATOR: " + settings.generator)
        self.conversations = Conversations()
        self.engine = AnswerEngine(settings, self.store, self.retriever, self.conversations, self.generator)
        self.ingest = IngestService(settings, self.store, self.index, self.embedder)
        if start_worker:
            self.ingest.start()

    def health(self) -> dict:
        docs = self.store.documents()
        status = {"documents": len(docs),
                  "searchable_documents": sum(d["status"] in ("ready", "partial") for d in docs),
                  "indexed_passages": len(self.index),
                  "embedder": self.embedder.name,
                  "generator": self.generator.name if self.generator else "extractive",
                  "ocr": "tesseract" if ocr.available() and self.settings.ocr_enabled else "unavailable"}
        if self.generator:
            ok, detail = self.generator.health()
            status["generator_ready"], status["generator_detail"] = ok, detail
        return status

    def close(self):
        self.ingest.stop()
        self.store.close()
