"""Plain, explainable statistics. No sampling, no randomness."""
from __future__ import annotations

import math
import statistics
from typing import Any


def describe(values: list[float]) -> dict[str, Any]:
    """min / max / mean / median / population stdev / quartiles / IQR."""
    if not values:
        return {"n": 0}
    vals = sorted(float(v) for v in values)
    out: dict[str, Any] = {
        "n": len(vals), "min": vals[0], "max": vals[-1],
        "mean": round(statistics.fmean(vals), 4), "median": statistics.median(vals),
        "stdev": round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0,
        "sum": sum(vals),
    }
    if len(vals) >= 2:
        q1, q2, q3 = statistics.quantiles(vals, n=4, method="inclusive")
        out.update({"q1": round(q1, 4), "q3": round(q3, 4), "iqr": round(q3 - q1, 4)})
    return out


def z_score(value: float, mean: float, stdev: float) -> float | None:
    if not stdev or math.isclose(stdev, 0.0):
        return None
    return round((value - mean) / stdev, 3)


def pct(part: float, whole: float, digits: int = 1) -> float | None:
    return round(100.0 * part / whole, digits) if whole else None
