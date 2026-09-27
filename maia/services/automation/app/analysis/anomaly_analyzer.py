"""Statistical observations about the DATA — never about the machine.

Every anomaly says what was measured and against what: "Group X holds 41% of
all retrieved part records (median group: 6)". None of them says anything
about wear, failure or condition, because the data does not.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.analysis.stats import describe, pct, z_score


def analyze_anomalies(parts: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    acfg = cfg.get("anomalies") or {}
    sizes: dict[str, int] = parts["records_per_group"]["value"]
    total = parts["total_records"]["value"]
    out: dict[str, Any] = {"large_groups": [], "small_groups": [], "dominant_groups": [],
                           "missing_concentration": [], "method": {}}
    nonempty = {g: n for g, n in sizes.items() if n > 0}
    if len(nonempty) >= int(acfg.get("min_groups_for_statistics", 4)):
        d = describe(list(nonempty.values()))
        k = float(acfg.get("iqr_multiplier", 1.5))
        zlim = float(acfg.get("z_score", 2.0))
        upper = d["q3"] + k * d["iqr"]
        lower = d["q1"] - k * d["iqr"]
        out["method"] = {"group_size_stats": d, "iqr_upper_fence": round(upper, 3),
                         "iqr_lower_fence": round(lower, 3), "z_threshold": zlim,
                         "rule": f"size > Q3 + {k}·IQR, or |z| > {zlim}"}
        for g, n in sorted(nonempty.items()):
            z = z_score(n, d["mean"], d["stdev"])
            if n > upper or (z is not None and z > zlim):
                out["large_groups"].append({"group": g, "records": n, "z": z,
                                            "median": d["median"],
                                            "times_median": round(n / d["median"], 2)
                                            if d["median"] else None})
            elif n < lower or (z is not None and z < -zlim):
                out["small_groups"].append({"group": g, "records": n, "z": z,
                                            "median": d["median"]})
    else:
        out["method"] = {"skipped": f"{len(nonempty)} non-empty group(s); statistics need "
                                    f"at least {acfg.get('min_groups_for_statistics', 4)}"}

    share = float(acfg.get("dominant_share", 0.40))
    if total and len(sizes) > 1:
        for g, n in sorted(sizes.items()):
            if n / total >= share:
                out["dominant_groups"].append({"group": g, "records": n,
                                               "share_pct": pct(n, total)})

    # Where are the gaps? A column whose missing values cluster in one group.
    need_share = float(acfg.get("missing_concentration_share", 0.60))
    need_min = int(acfg.get("missing_concentration_min", 3))
    for col in ("part_number", "description", "quantity_required", "part_name"):
        if col != "part_number" and col not in parts["columns_published"]:
            continue
        gaps = Counter(r["group_name"] or "(unnamed)" for r in parts["rows"]
                       if r.get(col) in (None, ""))
        n_gaps = sum(gaps.values())
        if n_gaps >= need_min:
            g, n = max(gaps.items(), key=lambda kv: (kv[1], kv[0]))
            if n / n_gaps >= need_share and len(sizes) > 1:
                out["missing_concentration"].append({"column": col, "group": g,
                                                     "gaps_in_group": n, "gaps_total": n_gaps,
                                                     "share_pct": pct(n, n_gaps)})
    out["classification"] = "DERIVED"
    return out
