"""Installation self-check: ``python -m eka.check``.

Prints one line per requirement and exits non-zero if something required is
missing. Optional items (OCR, semantic model, Ollama) are reported as warnings.
"""
from __future__ import annotations

import importlib
import socket
import sys

from .config import load_config_file, load_settings

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


def main() -> int:
    config_file = load_config_file()
    settings = load_settings()
    problems = 0

    def line(state, what, detail=""):
        nonlocal problems
        problems += state == FAIL
        print("[{0}] {1}{2}".format(state, what, ": " + detail if detail else ""))

    v = sys.version_info
    line(OK if v >= (3, 10) else FAIL, "Python {0}.{1}.{2}".format(*v[:3]), "" if v >= (3, 10) else "3.10 or newer is required")
    line(OK, "Config file", str(config_file) if config_file else "none, using defaults")
    for module, label in (("pymupdf", "PyMuPDF (PDF reading)"), ("numpy", "numpy"), ("fastapi", "FastAPI"),
                          ("uvicorn", "uvicorn")):
        try:
            mod = importlib.import_module(module)
            line(OK, label, getattr(mod, "__version__", getattr(mod, "VersionBind", "")) or "installed")
        except Exception as exc:
            line(FAIL, label, "not installed ({0}); run setup again".format(exc))

    from . import ocr
    path = ocr.tesseract_path()
    line(OK if path else WARN, "OCR (tesseract) for scanned PDFs",
         path or "not found: scanned pages will be reported as unreadable. Install it, or set EKA_TESSERACT_CMD")

    if settings.models_dir.is_dir() and any(settings.models_dir.rglob("*.parts.json")):
        line(WARN, "Search model", "delivered in parts and not joined yet: extract every model-part zip, then run setup")
    from .embeddings import make_embedder
    try:
        embedder = make_embedder(settings)
        line(OK if embedder.semantic else WARN, "Retrieval", embedder.label +
             ("" if embedder.semantic else " -- search model not found in {0}; run setup again to download it".format(settings.models_dir)))
    except Exception as exc:
        line(FAIL, "Retrieval", str(exc))

    if settings.generator.lower() == "ollama":
        from .generation import OllamaGenerator
        ok, detail = OllamaGenerator(settings.ollama_url, settings.llm_model).health()
        line(OK if ok else WARN, "Ollama model {0}".format(settings.llm_model), "ready" if ok else
             detail + " (answers fall back to extractive)")
    else:
        line(OK, "Answers", "extractive (quoted from the documents; no language model)")

    try:
        settings.ensure_dirs()
        probe = settings.data_dir / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        line(OK, "Data folder", str(settings.data_dir))
    except OSError as exc:
        line(FAIL, "Data folder", "{0} is not writable: {1}".format(settings.data_dir, exc))
    inbox = settings.inbox_path
    if inbox is None:
        line(OK, "PDF folder", "disabled")
    else:
        inbox.mkdir(parents=True, exist_ok=True)
        pdfs = sum(1 for p in inbox.rglob("*") if p.suffix.lower() == ".pdf")
        line(OK, "PDF folder", "{0} ({1} PDF{2})".format(inbox, pdfs, "" if pdfs == 1 else "s"))

    with socket.socket() as sock:
        free = sock.connect_ex((settings.host if settings.host not in ("0.0.0.0", "::") else "127.0.0.1",
                                settings.port)) != 0
    line(OK if free else WARN, "Port {0}".format(settings.port),
         "free" if free else "already in use (the assistant may already be running, or set EKA_PORT)")
    print("\n{0}".format("All required checks passed." if not problems else
                         "{0} required check(s) failed.".format(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
