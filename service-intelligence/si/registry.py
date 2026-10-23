"""Load and validate the metric registry (config/metrics.yaml)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp
import yaml

from . import contract

TEMPLATE = re.compile(r"\{(\w+)\}")
TEMPLATE_PARAMS = ("as_of", "aged_days")
UNITS = ("count", "hours", "days", "ratio")
DIRECTIONS = ("higher_is_better", "lower_is_better", "neutral")


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    description: str
    kind: str
    expression: str
    unit: str
    direction: str
    requires: tuple[str, ...]
    time_column: str | None = None
    filter: str | None = None
    min_sample: int | None = None
    synonyms: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def public(self, available: frozenset[str] | None = None) -> dict:
        missing = [c for c in self.requires if available is not None and c not in available]
        return {"id": self.id, "name": self.name, "description": self.description, "kind": self.kind,
                "unit": self.unit, "direction": self.direction, "time_column": self.time_column,
                "expression": self.expression, "filter": self.filter, "requires": list(self.requires),
                "min_sample": self.min_sample, "limitations": list(self.limitations),
                "available": not missing, "missing_columns": missing}


@dataclass(frozen=True)
class Dimension:
    id: str
    label: str
    synonyms: tuple[str, ...]


@dataclass(frozen=True)
class Registry:
    version: str
    metrics: dict[str, Metric]
    dimensions: dict[str, Dimension]
    parameters: dict[str, dict] = field(default_factory=dict)

    def metric(self, metric_id: str) -> Metric:
        try:
            return self.metrics[metric_id]
        except KeyError:
            raise RegistryError(f"Unknown metric {metric_id!r}") from None

    def param(self, name: str):
        return self.parameters[name]["default"]


def _columns_in(sql: str) -> set[str]:
    wrapped = f"SELECT {sql} FROM t"
    tree = sqlglot.parse_one(wrapped.replace("{", "").replace("}", ""), read="snowflake")
    return {c.name.lower() for c in tree.find_all(exp.Column)}


def load_registry(path: Path) -> Registry:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    params = raw.get("parameters") or {}
    dims = {}
    for key, d in (raw.get("dimensions") or {}).items():
        if key not in contract.DIMENSIONS:
            raise RegistryError(f"Dimension {key} is not a contract dimension")
        dims[key] = Dimension(key, d["label"], tuple(s.lower() for s in d.get("synonyms", [])))
    metrics: dict[str, Metric] = {}
    for m in raw.get("metrics") or []:
        mid = m["id"]
        if mid in metrics:
            raise RegistryError(f"Duplicate metric id {mid}")
        kind = m["kind"]
        if kind not in ("period", "snapshot"):
            raise RegistryError(f"{mid}: kind must be period or snapshot")
        if kind == "period" and m.get("time_column") not in contract.TIMESTAMPS:
            raise RegistryError(f"{mid}: period metrics need a contract timestamp as time_column")
        if m["unit"] not in UNITS or m["direction"] not in DIRECTIONS:
            raise RegistryError(f"{mid}: bad unit or direction")
        for text in (m["expression"], m.get("filter") or "TRUE"):
            unknown_params = set(TEMPLATE.findall(text)) - set(TEMPLATE_PARAMS)
            if unknown_params:
                raise RegistryError(f"{mid}: unknown template parameters {sorted(unknown_params)}")
            cols = _columns_in(text) - set(TEMPLATE_PARAMS)
            if cols - set(contract.NAMES):
                raise RegistryError(f"{mid}: references non-contract columns {sorted(cols - set(contract.NAMES))}")
        requires = tuple(m["requires"])
        if set(requires) - set(contract.NAMES):
            raise RegistryError(f"{mid}: requires unknown columns")
        used = (_columns_in(m["expression"]) | _columns_in(m.get("filter") or "TRUE")) - set(TEMPLATE_PARAMS)
        if m.get("time_column"):
            used.add(m["time_column"])
        if used - set(requires) - {"request_id"}:
            raise RegistryError(f"{mid}: uses {sorted(used - set(requires))} without listing them in requires")
        metrics[mid] = Metric(
            id=mid, name=m["name"], description=m["description"], kind=kind, expression=m["expression"],
            unit=m["unit"], direction=m["direction"], requires=requires, time_column=m.get("time_column"),
            filter=m.get("filter"), min_sample=m.get("min_sample"),
            synonyms=tuple(s.lower() for s in m.get("synonyms", [])), limitations=tuple(m.get("limitations", [])))
    return Registry(version=str(raw["version"]), metrics=metrics, dimensions=dims, parameters=params)
