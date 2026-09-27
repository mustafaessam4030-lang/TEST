"""Loads config/analysis.yaml. Defaults live in the file, not in code."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PATH = ROOT / "config" / "analysis.yaml"


def load_config(path: str | Path | None = None,
                overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_PATH
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    cfg = cfg or {}
    for key, value in (overrides or {}).items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    return cfg
