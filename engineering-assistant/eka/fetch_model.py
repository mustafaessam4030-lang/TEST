"""Download the search model into the models folder once: ``python -m eka.fetch_model``.

Setup runs this right after installing the packages. The model
(BAAI/bge-small-en-v1.5, about 65 MB) comes from Hugging Face. If it is
already there nothing is downloaded. If the download fails, the assistant
still works with keyword search, and running this again (or setup) retries.
"""
from __future__ import annotations

import sys

from .config import load_config_file, load_settings
from .embeddings import EmbeddingError, FastEmbedEmbedder

MODEL = "BAAI/bge-small-en-v1.5"


def main() -> int:
    load_config_file()
    settings = load_settings()
    model = settings.embed_model or MODEL
    cache = str(settings.models_dir)
    try:
        FastEmbedEmbedder(model, cache, offline=True)
        print("Search model already present in", cache)
        return 0
    except EmbeddingError:
        pass
    print("Downloading the search model ({0}, about 65 MB) into {1} ...".format(model, cache))
    try:
        FastEmbedEmbedder(model, cache, offline=False)
        FastEmbedEmbedder(model, cache, offline=True)     # proves it now loads without the internet
    except EmbeddingError as exc:
        print("Could not download the search model: {0}".format(str(exc)[:300]))
        print("The assistant still works with keyword search. Run setup again later to retry.")
        return 1
    print("Search model ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
