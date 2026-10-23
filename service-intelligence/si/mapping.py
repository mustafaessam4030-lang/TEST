"""Source mapping: reviewed YAML -> the governed contract view.

The mapping names the real source table and columns. It is written by a person
after running `python -m si.discover`, reviewed, and applied by someone who owns
the target schema (`python -m si.mapping ddl` prints the CREATE VIEW). The
application itself never runs DDL.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
import yaml

from . import contract

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
QUALIFIED = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){2}$")
TS_KINDS = ("ntz", "ltz", "tz")


class MappingError(ValueError):
    pass


def _literal(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


@dataclass(frozen=True)
class SourceMapping:
    source_table: str
    source_timezone: str
    timestamp_kind: str
    synthetic: bool
    columns: dict[str, str | None]
    status_classes: dict[str, list[str]]
    sla_targets_hours: dict[str, float] = field(default_factory=dict)
    sla_basis: str | None = None
    dedupe_order_by: str | None = None
    sensitive_columns: tuple[str, ...] = ()
    notes: str = ""

    @property
    def available(self) -> frozenset[str]:
        """Contract columns that carry real data (not a NULL placeholder)."""
        have = {k for k, v in self.columns.items() if v}
        if "status" in have and self.status_classes:
            have.add("status_class")
        if self.sla_targets_hours and self.sla_basis in have:
            have.add("due_at")
        return frozenset(have)

    @property
    def due_at_source(self) -> str | None:
        if self.columns.get("due_at"):
            return "source column"
        if "due_at" in self.available:
            return f"SLA policy by {self.sla_basis}"
        return None


def load_mapping(path: Path) -> SourceMapping:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise MappingError(f"Mapping file not found: {path}. Run discovery and write it first.") from exc
    return parse_mapping(raw)


def parse_mapping(raw: dict) -> SourceMapping:
    src = raw.get("source") or {}
    table = str(src.get("table") or "")
    if not QUALIFIED.match(table):
        raise MappingError("source.table must be a fully qualified DATABASE.SCHEMA.TABLE name")
    kind = str(src.get("timestamp_kind", "ntz")).lower()
    if kind not in TS_KINDS:
        raise MappingError(f"source.timestamp_kind must be one of {TS_KINDS}")
    cols_raw = raw.get("columns") or {}
    unknown = set(cols_raw) - set(contract.NAMES)
    if unknown:
        raise MappingError(f"Unknown contract columns in mapping: {sorted(unknown)}")
    if "status_class" in cols_raw:
        raise MappingError("status_class is derived from status_classes; do not map it")
    columns: dict[str, str | None] = {}
    for col in contract.NAMES:
        if col == "status_class":
            continue
        value = cols_raw.get(col)
        if value is not None and not IDENT.match(str(value)):
            raise MappingError(f"Mapped column for {col} is not a plain identifier: {value!r}")
        columns[col] = str(value).upper() if value else None
        if contract.BY_NAME[col].required and not value:
            raise MappingError(f"Contract column {col} is required and must be mapped")
    sensitive = tuple(str(s).upper() for s in raw.get("sensitive_columns") or ())
    leaked = sorted({v for v in columns.values() if v} & set(sensitive))
    if leaked:
        raise MappingError(f"Sensitive source columns cannot be mapped into the contract: {leaked}")

    classes: dict[str, list[str]] = {}
    seen: dict[str, str] = {}
    for cls, values in (raw.get("status_classes") or {}).items():
        cls = str(cls).upper()
        if cls not in contract.STATUS_CLASSES:
            raise MappingError(f"status_classes keys must be {contract.STATUS_CLASSES}")
        norm = []
        for v in values or []:
            key = str(v).strip().upper()
            if key in seen:
                raise MappingError(f"Status value {v!r} is mapped to both {seen[key]} and {cls}")
            seen[key] = cls
            norm.append(key)
        classes[cls] = norm
    if not classes.get("OPEN") or not classes.get("CLOSED"):
        raise MappingError("status_classes must list at least one OPEN and one CLOSED source value")

    sla = raw.get("sla_policy") or {}
    basis = sla.get("basis")
    targets = {str(k).strip().upper(): float(v) for k, v in (sla.get("targets_hours") or {}).items()}
    if sla and (basis not in contract.DIMENSIONS or not targets):
        raise MappingError("sla_policy needs a dimension `basis` and non-empty `targets_hours`")
    if any(h <= 0 for h in targets.values()):
        raise MappingError("sla_policy targets must be positive hours")
    dedupe = (raw.get("dedupe") or {}).get("order_by")
    if dedupe and not IDENT.match(str(dedupe)):
        raise MappingError("dedupe.order_by must be a plain identifier")
    return SourceMapping(
        source_table=table.upper(),
        source_timezone=str(src.get("timezone", "UTC")),
        timestamp_kind=kind,
        synthetic=bool(src.get("synthetic", False)),
        columns=columns,
        status_classes=classes,
        sla_targets_hours=targets,
        sla_basis=basis,
        dedupe_order_by=str(dedupe).upper() if dedupe else None,
        sensitive_columns=sensitive,
        notes=str(raw.get("notes") or ""),
    )


def _ts(m: SourceMapping, col: str) -> str:
    if m.timestamp_kind == "ntz":
        if m.source_timezone.upper() == "UTC":
            return f"CAST({col} AS TIMESTAMP_NTZ)"
        return f"CAST(CONVERT_TIMEZONE({_literal(m.source_timezone)}, 'UTC', {col}) AS TIMESTAMP_NTZ)"
    return f"CAST(CONVERT_TIMEZONE('UTC', {col}) AS TIMESTAMP_NTZ)"


def view_select_sql(m: SourceMapping) -> str:
    """The contract view's SELECT, in Snowflake SQL."""
    parts = []
    for c in contract.COLUMNS:
        if c.name == "status_class":
            src = m.columns["status"]
            whens = " ".join(
                f"WHEN UPPER(TRIM({src})) IN ({', '.join(_literal(v) for v in vals)}) THEN {_literal(cls)}"
                for cls, vals in m.status_classes.items() if vals)
            parts.append(f"CASE {whens} ELSE {_literal(contract.UNMAPPED_STATUS)} END AS status_class")
            continue
        src = m.columns.get(c.name)
        if c.name == "due_at" and not src and "due_at" in m.available:
            basis = m.columns[m.sla_basis]
            whens = " ".join(f"WHEN UPPER(TRIM({basis})) = {_literal(k)} THEN DATEADD('minute', {int(round(h * 60))}, {_ts(m, m.columns['created_at'])})"
                             for k, h in m.sla_targets_hours.items())
            parts.append(f"CASE {whens} ELSE NULL END AS due_at")
        elif not src:
            parts.append(f"CAST(NULL AS {c.sql_type}) AS {c.name}")
        elif c.role == "timestamp":
            parts.append(f"{_ts(m, src)} AS {c.name}")
        else:
            parts.append(f"CAST({src} AS VARCHAR) AS {c.name}")
    sql = "SELECT\n  " + ",\n  ".join(parts) + f"\nFROM {m.source_table}"
    sql += f"\nWHERE {m.columns['request_id']} IS NOT NULL"
    if m.dedupe_order_by:
        sql += f"\nQUALIFY ROW_NUMBER() OVER (PARTITION BY {m.columns['request_id']} ORDER BY {m.dedupe_order_by} DESC) = 1"
    sqlglot.parse_one(sql, read="snowflake")    # fail early on anything malformed
    return sql


def view_ddl(m: SourceMapping, contract_view: str) -> str:
    comment = _literal("Service Intelligence contract view generated from a reviewed source mapping")
    return f"CREATE OR REPLACE SECURE VIEW {contract_view}\n  COMMENT = {comment}\nAS\n{view_select_sql(m)};"


def main(argv: list[str]) -> int:
    from .config import load_settings
    if not argv or argv[0] not in ("ddl", "check"):
        print("usage: python -m si.mapping ddl|check", file=sys.stderr)
        return 2
    settings = load_settings()
    m = load_mapping(settings.mapping_path)
    if argv[0] == "ddl":
        print(view_ddl(m, settings.contract_view))
        return 0
    from .discover import check_mapping
    return check_mapping(settings, m)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
