from si.config import ROOT
from si.metric_docs import render


def test_metric_definitions_document_matches_registry():
    doc = (ROOT / "SERVICE_METRIC_DEFINITIONS.md").read_text(encoding="utf-8")
    assert doc == render(), "Regenerate: python -m si.metric_docs > SERVICE_METRIC_DEFINITIONS.md"
