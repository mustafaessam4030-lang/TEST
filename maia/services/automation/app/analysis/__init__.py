"""Maia's deterministic analysis engine.

Input: verified SIS results already in the local store. Output: statistics,
data quality, anomalies, changes and insights — each value labelled DIRECT
(read from SIS) or DERIVED (calculated here, with the calculation named).

No model, no network, no browser. The same stored data and the same `as_of`
date always give the same result. A future AI layer may *explain* these
results; it may never replace them.
"""
from app.analysis.service import AnalysisService  # noqa: F401
