"""Parts intelligence over the "… Entire Group (…)" tables SIS published.

Works on the rows exactly as stored (see app/domain/parts.flatten_parts). A
column that the page did not publish is reported as *not published*, never as
"missing on every row": SIS's parts table for a machine does not always carry
a quantity column, and treating that as 428 gaps would be a false finding.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from app.analysis.evidence import derived
from app.analysis.stats import describe, pct
from app.domain.parts import canonical_column, flatten_parts, group_label


def published_columns(parts_data: dict[str, Any] | None) -> dict[str, list[str]]:
    """canonical column → the header(s) the page used for it."""
    out: dict[str, set[str]] = defaultdict(set)
    for group in (parts_data or {}).get("groups") or []:
        headers = list(group.get("columns") or (parts_data or {}).get("columns") or [])
        for row in group.get("rows") or []:
            headers += list((row.get("values") or {}).keys())
        for h in headers:
            col = canonical_column(h)
            if col:
                out[col].add(h)
    return {k: sorted(v) for k, v in sorted(out.items())}


def _group_titles(parts_data: dict[str, Any] | None) -> list[str]:
    titles = [g.get("title") or "" for g in (parts_data or {}).get("groups") or []]
    return [t for t in titles if t]


def analyze_parts(snap: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    pd = snap.parts_data
    rows = flatten_parts(pd)
    pcfg = cfg.get("parts") or {}
    columns = published_columns(pd)
    titles = _group_titles(pd)
    groups = [group_label(t) for t in titles]

    per_group = Counter(r["group_name"] or "(unnamed)" for r in rows)
    for g in groups:                       # empty groups still count as groups
        per_group.setdefault(g, 0)
    numbers = [r["part_number"] for r in rows if r["part_number"]]
    counts = Counter(numbers)
    groups_of: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        if r["part_number"]:
            groups_of[r["part_number"]].add(r["group_name"] or "(unnamed)")

    duplicates = {pn: n for pn, n in sorted(counts.items()) if n > 1}
    across = {pn: sorted(gs) for pn, gs in sorted(groups_of.items()) if len(gs) > 1}
    seen_rows: Counter = Counter((r["group_name"], tuple(r["raw"]["cells"])) for r in rows)
    exact_dupes = sorted([{"group": g, "cells": list(c), "times": n}
                          for (g, c), n in seen_rows.items() if n > 1],
                         key=lambda d: (str(d["group"]), d["cells"]))

    has_qty = "quantity_required" in columns
    has_desc = "description" in columns
    has_name = "part_name" in columns
    missing_pn = [r["locator"] for r in rows if not r["part_number"]]
    missing_desc = ([r for r in rows if r["part_number"] and not r["description"]]
                    if has_desc else [])
    missing_qty = ([r for r in rows if r["part_number"] and r["quantity_required"] is None]
                   if has_qty else [])
    missing_name = ([r for r in rows if r["part_number"] and not r["part_name"]]
                    if has_name else [])

    pattern = re.compile(pcfg.get("part_number_pattern") or r".+")
    malformed = sorted({pn for pn in numbers if not pattern.match(pn)})

    # Category: the first configured column the page actually published.
    category_col = None
    for candidate in pcfg.get("category_columns") or []:
        if any(candidate in (g.get("columns") or []) for g in (pd or {}).get("groups") or []):
            category_col = candidate
            break
    by_category = (dict(sorted(Counter((r["raw"]["values"].get(category_col) or "(blank)")
                                       for r in rows).items()))
                   if category_col else None)

    quantities = [float(r["quantity_required"]) for r in rows
                  if r["quantity_required"] is not None]
    sizes = [n for n in per_group.values()]
    total = len(rows)
    largest = max(per_group.items(), key=lambda kv: (kv[1], kv[0])) if per_group else None

    def d(value: Any, calc: str) -> dict[str, Any]:
        return derived(value, calculation=calc, snap=snap)

    return {
        "columns_published": columns,
        "rows": rows,                                   # used by rules/anomalies; not reported
        "total_records": d(total, "COUNT(part_rows)"),
        "unique_part_numbers": d(len(counts), "COUNT(DISTINCT part_number)"),
        "groups": d(len(per_group), "COUNT(DISTINCT group)"),
        "records_per_group": d(dict(sorted(per_group.items())),
                               "COUNT(part_rows) GROUP BY group"),
        "group_size_stats": d(describe(sizes), "min/max/mean/median/stdev/IQR of group sizes"),
        "largest_group": d({"group": largest[0], "records": largest[1],
                            "share_pct": pct(largest[1], total)} if largest and total else None,
                           "MAX(records per group), share = records / total"),
        "empty_groups": d(sorted(g for g, n in per_group.items() if n == 0),
                          "groups with COUNT(part_rows) = 0"),
        "duplicate_part_numbers": d(duplicates, "part_number HAVING COUNT(*) > 1"),
        "duplicate_entries": d(sum(n - 1 for n in duplicates.values()),
                               "SUM(COUNT(part_number) − 1) over duplicated part numbers"),
        "repeated_across_groups": d(across, "part_number HAVING COUNT(DISTINCT group) > 1"),
        "exact_duplicate_rows": d(exact_dupes, "identical cells within one group, COUNT > 1"),
        "missing_part_numbers": d(len(missing_pn), "COUNT(rows WHERE part_number IS NULL)"),
        "missing_part_number_rows": missing_pn,
        "missing_descriptions": d(len(missing_desc) if has_desc else None,
                                  "COUNT(rows WHERE description IS NULL)"
                                  if has_desc else "Description column not published"),
        "missing_names": d(len(missing_name) if has_name else None,
                           "COUNT(rows WHERE part_name IS NULL)"
                           if has_name else "Part Name column not published"),
        "missing_quantities": d(len(missing_qty) if has_qty else None,
                                "COUNT(rows WHERE quantity IS NULL)"
                                if has_qty else "Quantity column not published"),
        "missing_description_parts": sorted({r["part_number"] for r in missing_desc}),
        "missing_quantity_parts": sorted({r["part_number"] for r in missing_qty}),
        "quantity_total": d(sum(quantities) if has_qty and quantities else None,
                            "SUM(quantity)" if has_qty else "Quantity column not published"),
        "category_column": category_col,
        "parts_by_category": d(by_category, f"COUNT(part_rows) GROUP BY '{category_col}'"
                               if category_col else "no category column published"),
        "malformed_part_numbers": d(malformed, "part_number NOT MATCHING part_number_pattern"),
        "group_titles": titles,
        "serial_mismatched_groups": list((pd or {}).get("serial_mismatched_groups") or []),
    }
