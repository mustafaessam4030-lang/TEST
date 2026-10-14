"""Settings, read once from environment variables (prefix ``EKA_``).

Every setting has a safe default so the application runs with no configuration:
local SQLite storage, the offline hashing embedder and the deterministic
extractive answerer. Nothing here talks to a network service unless it is
explicitly configured.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


PATH_KEYS = ("EKA_DATA_DIR", "EKA_INBOX_DIR", "EKA_MODEL_DIR")
OFF = ("off", "none", "disabled", "0")


def load_config_file(path: str | Path | None = None) -> Path | None:
    """Read ``KEY=VALUE`` lines from config.env into the environment.

    Real environment variables win: a value is only set if it is not already
    defined. Lines starting with # are comments. Relative folder paths are
    taken relative to the config file, so the app works from any current
    directory. Returns the file used, if any.
    """
    candidate = Path(path or os.environ.get("EKA_CONFIG") or "config.env")
    if not candidate.is_file():
        return None
    for raw in candidate.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key in PATH_KEYS and value and value.lower() not in OFF and not Path(value).is_absolute():
            value = str((candidate.parent / value).resolve())
        if key.startswith("EKA_") and key not in os.environ:
            os.environ[key] = value
    return candidate.resolve()


def _env(name: str, default: str) -> str:
    value = os.environ.get("EKA_" + name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")).resolve())

    # HTTP
    host: str = field(default_factory=lambda: _env("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("PORT", 8765))
    api_token: str = field(default_factory=lambda: _env("API_TOKEN", ""))
    max_upload_mb: int = field(default_factory=lambda: _env_int("MAX_UPLOAD_MB", 100))
    open_browser: bool = field(default_factory=lambda: _env_bool("OPEN_BROWSER", False))

    # Phase 1 source: a folder of PDFs that is indexed automatically ("off" disables it)
    inbox_dir: str = field(default_factory=lambda: _env("INBOX_DIR", "pdfs"))
    inbox_scan_s: float = field(default_factory=lambda: _env_float("INBOX_SCAN_S", 15.0))
    inbox_settle_s: float = field(default_factory=lambda: _env_float("INBOX_SETTLE_S", 5.0))
    inbox_remove_deleted: bool = field(default_factory=lambda: _env_bool("INBOX_REMOVE_DELETED", False))

    # Extraction
    ocr_enabled: bool = field(default_factory=lambda: _env_bool("OCR_ENABLED", True))
    ocr_min_chars: int = field(default_factory=lambda: _env_int("OCR_MIN_CHARS", 40))
    ocr_dpi: int = field(default_factory=lambda: _env_int("OCR_DPI", 300))
    ocr_lang: str = field(default_factory=lambda: _env("OCR_LANG", "eng"))
    ocr_timeout_s: int = field(default_factory=lambda: _env_int("OCR_TIMEOUT_S", 120))
    tables_enabled: bool = field(default_factory=lambda: _env_bool("TABLES_ENABLED", True))

    # Chunking
    chunk_max_chars: int = field(default_factory=lambda: _env_int("CHUNK_MAX_CHARS", 1000))

    # Embeddings: auto | hashing | fastembed | ollama.  "auto" uses a real semantic
    # model (fastembed bge-small) only if it is already in EKA_MODEL_DIR, and never
    # downloads; otherwise retrieval is keyword-only and says so.
    embedder: str = field(default_factory=lambda: _env("EMBEDDER", "auto"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", ""))
    model_dir: str = field(default_factory=lambda: _env("MODEL_DIR", ""))

    # Generation: extractive | ollama
    generator: str = field(default_factory=lambda: _env("GENERATOR", "extractive"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "qwen2.5:7b-instruct"))
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://127.0.0.1:11434"))
    llm_timeout_s: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT_S", 120.0))
    llm_retries: int = field(default_factory=lambda: _env_int("LLM_RETRIES", 1))
    llm_num_ctx: int = field(default_factory=lambda: _env_int("LLM_NUM_CTX", 8192))

    # Retrieval
    top_k: int = field(default_factory=lambda: _env_int("TOP_K", 6))
    candidate_pool: int = field(default_factory=lambda: _env_int("CANDIDATE_POOL", 40))
    min_coverage: float = field(default_factory=lambda: _env_float("MIN_COVERAGE", 0.5))
    # Cosine similarity at which a passage counts as relevant on semantics alone.
    # 0 means "use the embedder's own default" (see embeddings.py).
    min_semantic: float = field(default_factory=lambda: _env_float("MIN_SEMANTIC", 0.0))

    # Logging
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    log_query_text: bool = field(default_factory=lambda: _env_bool("LOG_QUERY_TEXT", False))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "eka.sqlite3"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def models_dir(self) -> Path:
        return Path(self.model_dir).resolve() if self.model_dir else self.data_dir / "models"

    @property
    def inbox_path(self) -> Path | None:
        if not self.inbox_dir or self.inbox_dir.lower() in OFF:
            return None
        return Path(self.inbox_dir).resolve()

    @property
    def log_path(self) -> Path:
        return self.data_dir / "logs" / "queries.jsonl"

    def ensure_dirs(self) -> None:
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)


def load_settings(**overrides) -> Settings:
    load_config_file()
    settings = Settings()
    for key, value in overrides.items():
        if not hasattr(settings, key):
            raise AttributeError("unknown setting: " + key)
        setattr(settings, key, Path(value).resolve() if key == "data_dir" else value)
    return settings
