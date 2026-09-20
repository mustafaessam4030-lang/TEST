"""Canonical hashing: two retrievals of unchanged data must produce one hash."""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Volatile bookkeeping is excluded so the hash tracks the *business* data only.
EXCLUDED_KEYS = {
    "retrieved_at", "updated_at", "last_verified_at", "automation_run_id",
    "data_hash", "raw_data", "source_url", "quality", "field_provenance",
}


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items()) if k not in EXCLUDED_KEYS}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def data_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def idempotency_key(source: str, serial: str, mode: str) -> str:
    return hashlib.sha256(f"{source}|{serial}|{mode}".encode()).hexdigest()[:32]
