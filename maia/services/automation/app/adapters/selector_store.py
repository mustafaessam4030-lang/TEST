"""Load, validate and merge captured selectors.

`config/sis_selectors.json` is written by scripts/capture/capture_selectors.py from
a real authenticated session. Only entries marked confidence=verified are used.
TODO_CAPTURE entries are carried through untouched so the adapter fails loudly
instead of running against a half-known page.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TODO = "TODO_CAPTURE"
REQUIRED_KEYS = ("name", "selector", "strategy", "element_text", "url",
                 "captured_at", "confidence")

# Selector names the automation cannot run without.
REQUIRED_SELECTORS = (
    "ready.app_shell", "ready.search_page",
    "search.input", "search.submit", "search.results", "search.no_results_marker",
)


class SelectorStoreError(ValueError):
    pass


def load(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SelectorStoreError(f"{p} is not valid JSON: {exc}") from exc
    if not isinstance(payload.get("selectors"), dict):
        raise SelectorStoreError(f"{p} has no 'selectors' object")
    return payload


def verified_selectors(payload: dict[str, Any]) -> dict[str, str]:
    """Only entries a real session confirmed. Everything else is ignored."""
    out: dict[str, str] = {}
    for name, entry in (payload.get("selectors") or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == TODO or entry.get("confidence") != "verified":
            continue
        selector = entry.get("selector")
        if isinstance(selector, str) and selector and selector != TODO:
            out[name] = selector
    return out


def validate_entry(entry: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if entry.get("status") == TODO:
        return problems                      # an honest gap is valid; it just blocks running
    for key in REQUIRED_KEYS:
        if key not in entry:
            problems.append(f"missing '{key}'")
    if entry.get("confidence") not in ("verified", None):
        problems.append(f"confidence must be 'verified', got {entry.get('confidence')!r}")
    return problems


def validate(payload: dict[str, Any]) -> dict[str, list[str]]:
    return {name: probs for name, entry in (payload.get("selectors") or {}).items()
            if isinstance(entry, dict) and (probs := validate_entry(entry))}


def missing_required(payload: dict[str, Any]) -> list[str]:
    have = verified_selectors(payload)
    return [name for name in REQUIRED_SELECTORS if name not in have]


def merge_into_config(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Overlay verified selectors onto the YAML source contract.

    Names are dotted: 'search.input' -> config['selectors']['search']['input'],
    'ready.app_shell' -> config['ready_markers']['app_shell'].
    """
    merged = json.loads(json.dumps(config))          # cheap deep copy; config is plain data
    for name, selector in verified_selectors(payload).items():
        group, _, leaf = name.partition(".")
        if not leaf:
            continue
        if group == "ready":
            merged.setdefault("ready_markers", {})[leaf] = selector
        else:
            merged.setdefault("selectors", {}).setdefault(group, {})[leaf] = selector
    if payload.get("xhr_endpoints"):
        patterns = merged.setdefault("extraction", {}).setdefault("xhr_url_patterns", [])
        for pattern in payload["xhr_endpoints"]:
            if pattern not in patterns:
                patterns.append(pattern)
    if payload.get("selector_version"):
        merged["selector_version"] = payload["selector_version"]
    merged["_captured_from"] = payload.get("capture_profile", "unknown")
    return merged


def flatten_config(config: dict[str, Any]) -> dict[str, str]:
    """Config -> {'search.input': '...', 'ready.app_shell': '...'} for the health check."""
    flat: dict[str, str] = {}
    for leaf, value in (config.get("ready_markers") or {}).items():
        flat[f"ready.{leaf}"] = value
    for group, entries in (config.get("selectors") or {}).items():
        for leaf, value in (entries or {}).items():
            flat[f"{group}.{leaf}"] = value
    return flat
