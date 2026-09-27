"""Troubleshooting component / part → 3D-model component. Deterministic, or nothing.

Only names the 3D viewer itself exposed are candidates. A target maps when its
normalised form equals a component's normalised form (directly, through a
configured alias, by the same set of words, or by an exact part-number token).
One distinct component → VERIFIED. More than one → AMBIGUOUS ("3D component
mapping is ambiguous."). None → NOT_FOUND. No names at all → NOT_AVAILABLE.
Nothing is ever picked by "closest".
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.domain.parts import PART_NUMBER_RE

ALIASES_PATH = Path(__file__).resolve().parents[4] / "config" / "component_aliases.yaml"


def normalize(name: str) -> str:
    s = (name or "").lower().replace("#", " ").replace("&", " and ")
    s = re.sub(r"\bcyl\b\.?", "cylinder", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _alias_index(path: Path | None = None) -> dict[str, str]:
    p = path or ALIASES_PATH
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    index: dict[str, str] = {}
    for canonical, spellings in ((data or {}).get("aliases") or {}).items():
        c = normalize(canonical)
        index[c] = c
        for sp in spellings or []:
            index[normalize(sp)] = c
    return index


def map_targets(targets: list[str], components: list[dict[str, Any]], *,
                aliases: dict[str, str] | None = None) -> list[dict[str, Any]]:
    if not components:
        return [{"target": t, "status": "NOT_AVAILABLE",
                 "message": "3D component mapping is not currently available."}
                for t in targets]
    aliases = aliases if aliases is not None else _alias_index()
    canon = [(c, normalize(c.get("name", ""))) for c in components if c.get("name")]
    out = []
    for target in targets:
        t = normalize(target)
        t_alias = aliases.get(t, t)
        t_tokens = set(t.split())
        pn = PART_NUMBER_RE.search(target or "")
        hits: list[tuple[dict[str, Any], str]] = []
        for comp, n in canon:
            if n == t:
                hits.append((comp, "exact normalised name"))
            elif aliases.get(n, n) == t_alias:
                hits.append((comp, "configured alias"))
            elif t_tokens and set(n.split()) == t_tokens:
                hits.append((comp, "same words, different order"))
            elif pn and re.search(rf"(?<![0-9a-z]){re.escape(pn.group(0).lower())}(?![0-9a-z])",
                                  comp.get("name", "").lower()):
                hits.append((comp, "exact part-number token"))
        distinct = {normalize(c.get("name", "")) for c, _ in hits}
        ids = {str(c.get("id")) for c, _ in hits}
        if not hits:
            out.append({"target": target, "status": "NOT_FOUND",
                        "message": f"No 3D component named '{target}' was exposed by the viewer."})
        elif len(distinct) == 1 and len(ids) == 1:
            comp, how = hits[0]
            out.append({"target": target, "status": "VERIFIED", "component": comp,
                        "classification": "DERIVED", "method": how,
                        "evidence": {"troubleshooting": {"source": "SIS", "classification": "DIRECT"},
                                     "3d_component": {"source": "SIS 3D Model",
                                                      "classification": "DIRECT"},
                                     "relationship": {"classification": "DERIVED",
                                                      "method": "deterministic_component_mapping"}}})
        else:
            out.append({"target": target, "status": "AMBIGUOUS",
                        "message": "3D component mapping is ambiguous.",
                        "candidates": [c for c, _ in hits][:10]})
    return out
