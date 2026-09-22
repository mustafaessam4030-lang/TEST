"""Parts rows, one per part, exactly as the source page published them.

The adapter reads each "Product - …" / "… Entire Group (…)" section with its
table: column headers plus, per row, the cell texts and `values` keyed by
header. This module turns that into flat rows for EQUIPMENT_PARTS and for the
tools Cortex calls.

A column is mapped only when the page has a header for it. Nothing is guessed:
a page without "Quantity" gives rows with `quantity_required = None`, and every
cell — mapped or not — is kept in `raw`.
"""
from __future__ import annotations

import re
from typing import Any

# header (lower-cased, punctuation-squashed) → canonical column
HEADER_MAP: dict[str, str] = {
    "part number": "part_number", "part no": "part_number", "part #": "part_number",
    "part num": "part_number", "p/n": "part_number",
    "part name": "part_name", "name": "part_name",
    "quantity": "quantity_required", "qty": "quantity_required",
    "quantity required": "quantity_required", "qty required": "quantity_required",
    "qty req": "quantity_required", "quantity req": "quantity_required",
    "where used": "where_used",
    "service article": "service_article", "service articles": "service_article",
    "group part": "group_part", "group": "group_part",
    "s/n applicability": "sn_applicability", "serial number": "component_serial",
    "sn applicability": "sn_applicability",
    "part of": "part_of",
    "description": "description",
    "install ind": "install_indicator", "install ind.": "install_indicator",
    "install date": "install_date",
}
CANONICAL = ("group_part", "part_number", "part_name", "quantity_required", "where_used",
             "service_article", "sn_applicability", "component_serial", "part_of", "description",
             "install_indicator", "install_date")
PART_NUMBER_RE = re.compile(r"\b\d{1,4}[A-Z]?-\d{3,5}\b|\b\d[A-Z]-\d{4}\b", re.I)
_GROUP_SERIAL = re.compile(r"\(([A-Z0-9]{3,20})\)\s*$")


def _canon(header: str) -> str | None:
    key = re.sub(r"\s+", " ", str(header or "").strip().lower()).rstrip(":")
    return HEADER_MAP.get(key) or HEADER_MAP.get(key.rstrip("."))


def group_label(title: str) -> str:
    """"Engine - Entire Group (PRH04588)" → "Engine - Entire Group"; the page's
    own words, minus the trailing serial."""
    label = _GROUP_SERIAL.sub("", str(title or "")).strip()
    return re.sub(r"^\s*Product\s*[-–—]\s*", "", label, flags=re.I).strip() or label


def product_of(parts_data: dict[str, Any] | None) -> str | None:
    """The PRODUCT the page names: the entire-group heading, as written."""
    if not isinstance(parts_data, dict):
        return None
    title = parts_data.get("entire_group_title")
    if not title:
        for g in parts_data.get("groups") or []:
            if g.get("is_entire_group"):
                title = g.get("title")
                break
    return re.sub(r"^\s*Product\s*[-–—]\s*", "", str(title)).strip() if title else None


def _quantity(value: Any) -> float | int | None:
    """"4" → 4, "2.5" → 2.5, "" / "—" / "AR" → None (not a number, not invented)."""
    text = str(value or "").strip()
    if re.fullmatch(r"\d+", text):
        return int(text)
    if re.fullmatch(r"\d+\.\d+", text):
        return float(text)
    return None


def flatten_parts(parts_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(parts_data, dict):
        return []
    out: list[dict[str, Any]] = []
    for g_index, group in enumerate(parts_data.get("groups") or []):
        columns = list(group.get("columns") or parts_data.get("columns") or [])
        title = group.get("title") or ""
        for r_index, row in enumerate(group.get("rows") or []):
            values = dict(row.get("values") or {})
            if not values and columns:
                values = {c: v for c, v in zip(columns, row.get("cells") or []) if c}
            mapped: dict[str, Any] = {k: None for k in CANONICAL}
            for header, text in values.items():
                col = _canon(header)
                if col and mapped.get(col) in (None, ""):
                    mapped[col] = (str(text).strip() or None) if text is not None else None
            quantity_text = mapped.get("quantity_required")
            out.append({
                "group_name": group_label(title) or None,
                "group_title": title or None,
                "group_serial": group.get("group_serial"),
                "is_entire_group": bool(group.get("is_entire_group")),
                **mapped,
                "quantity_required": _quantity(quantity_text),
                "quantity_text": quantity_text,
                "locator": f"group {g_index + 1} '{group_label(title)}' row {r_index + 1}",
                "raw": {"cells": row.get("cells") or [], "values": values,
                        "links": row.get("links") or {}},
            })
    return out
