import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_samples  # noqa: E402
from eka.config import load_settings  # noqa: E402
from eka.service import Assistant  # noqa: E402


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("samples")
    make_samples.main(out)
    return out


@pytest.fixture(scope="session")
def assistant(samples, tmp_path_factory):
    """One assistant with every sample ingested (OCR runs once per session)."""
    a = Assistant(load_settings(data_dir=tmp_path_factory.mktemp("data")))
    for path in sorted(samples.glob("*.pdf")):
        a.ingest.submit(path.read_bytes(), path.name)
    assert a.ingest.wait_idle(300)
    yield a
    a.close()


@pytest.fixture(scope="session")
def docs(assistant) -> dict:
    return {d["filename"]: d for d in assistant.store.documents()}


@pytest.fixture
def settings(tmp_path):
    return load_settings(data_dir=tmp_path)
