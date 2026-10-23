"""Read-only Snowflake discovery: what the configured role can see, and which tables look like service data.

    python -m si.discover inventory [--databases DB1,DB2] [--out discovery]
    python -m si.discover profile DB.SCHEMA.TABLE --id COL --created COL [--status COL] [--out discovery]
    python -m si.mapping check            (verifies the reviewed mapping against the live source)

Only metadata queries (CURRENT_*, SHOW, INFORMATION_SCHEMA) and aggregate
profiles (counts, min/max, null counts, status value counts) are run. No row of
company data is sampled or written anywhere. Results go to JSON and Markdown
files for a person to review before writing config/source_mapping.yaml.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .mapping import IDENT, QUALIFIED, SourceMapping

Runner = Callable[[str, tuple], list[dict]]
NAME_HINTS = re.compile(r"(service|request|ticket|case|incident|work_?order|wo_|job|repair|maint|inspection|"
                        r"complaint|claim|warranty|sr_|call)", re.I)
COLUMN_HINTS = {
    "id": re.compile(r"(^id$|_id$|_no$|number|ticket|request|case|order)", re.I),
    "created": re.compile(r"(creat|open|logged|reported|received|start)", re.I),
    "closed": re.compile(r"(clos|resol|complet|finish|end_|done)", re.I),
    "due": re.compile(r"(due|target|sla|deadline|promis)", re.I),
    "status": re.compile(r"(status|state|stage)", re.I),
    "dimension": re.compile(r"(priority|severity|category|type|team|group|region|branch|site|location|work_?cent)", re.I),
}
SENSITIVE_HINTS = re.compile(r"(name|email|phone|mobile|address|description|comment|note|remark|text|ssn|"
                             r"passport|national|salary|iban|card)", re.I)


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def score_table(name: str, columns: list[dict]) -> dict:
    """Heuristic only: ranks candidates for a person to review; never decides a mapping."""
    cols = [c["column_name"] for c in columns]
    types = {c["column_name"]: str(c.get("data_type", "")).upper() for c in columns}
    found = {}
    for role, rx in COLUMN_HINTS.items():
        hits = [c for c in cols if rx.search(c) and (role not in ("created", "closed", "due") or "TIME" in types[c] or "DATE" in types[c])]
        if hits:
            found[role] = hits[:5]
    score = (3 if NAME_HINTS.search(name) else 0) + sum(2 for r in ("id", "created", "status") if r in found) \
        + sum(1 for r in ("closed", "due", "dimension") if r in found)
    return {"score": score, "candidate_columns": found,
            "possibly_sensitive": [c for c in cols if SENSITIVE_HINTS.search(c)]}


def inventory(run: Runner, databases: list[str] | None = None, max_databases: int = 25) -> dict:
    ctx = run("SELECT CURRENT_ACCOUNT() AS account, CURRENT_REGION() AS region, CURRENT_ROLE() AS role, "
              "CURRENT_WAREHOUSE() AS warehouse, CURRENT_AVAILABLE_ROLES() AS available_roles, "
              "CURRENT_VERSION() AS version", ())[0]
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "context": ctx,
           "warehouses": [], "databases": [], "tables": [], "errors": []}
    try:
        out["warehouses"] = [{k: w.get(k) for k in ("name", "state", "size", "auto_suspend", "auto_resume")}
                             for w in run("SHOW WAREHOUSES", ())]
    except Exception as exc:                      # visibility depends on the role
        out["errors"].append(f"SHOW WAREHOUSES: {type(exc).__name__}")
    dbs = [d["name"] for d in run("SHOW DATABASES", ())]
    if databases:
        dbs = [d for d in dbs if d.upper() in {x.upper() for x in databases}]
    out["databases"] = dbs
    for db in [d for d in dbs if d.upper() not in ("SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA")][:max_databases]:
        try:
            tables = run(f"SELECT table_schema, table_name, table_type, row_count, bytes, last_altered "
                         f"FROM {_q(db)}.INFORMATION_SCHEMA.TABLES WHERE table_schema <> 'INFORMATION_SCHEMA' "
                         f"ORDER BY table_schema, table_name LIMIT 5000", ())
            cols = run(f"SELECT table_schema, table_name, column_name, data_type, is_nullable "
                       f"FROM {_q(db)}.INFORMATION_SCHEMA.COLUMNS WHERE table_schema <> 'INFORMATION_SCHEMA' "
                       f"ORDER BY table_schema, table_name, ordinal_position LIMIT 100000", ())
        except Exception as exc:
            out["errors"].append(f"{db}: {type(exc).__name__}")
            continue
        by_table: dict[tuple, list] = {}
        for c in cols:
            by_table.setdefault((c["table_schema"], c["table_name"]), []).append(c)
        for t in tables:
            key = (t["table_schema"], t["table_name"])
            columns = by_table.get(key, [])
            out["tables"].append({"name": f"{db}.{t['table_schema']}.{t['table_name']}", "type": t.get("table_type"),
                                  "row_count": t.get("row_count"), "bytes": t.get("bytes"),
                                  "last_altered": str(t.get("last_altered")) if t.get("last_altered") else None,
                                  "columns": [{"name": c["column_name"], "type": c["data_type"],
                                               "nullable": c.get("is_nullable")} for c in columns],
                                  **score_table(t["table_name"], columns)})
    out["tables"].sort(key=lambda t: (-t["score"], t["name"]))
    return out


def profile(run: Runner, table: str, id_col: str, created_col: str, status_col: str | None, columns: list[str]) -> dict:
    if not QUALIFIED.match(table) or not all(IDENT.match(c) for c in [id_col, created_col] + columns + ([status_col] if status_col else [])):
        raise ValueError("Table must be DB.SCHEMA.TABLE and columns plain identifiers")
    nn = ", ".join(f"COUNT({c}) AS nn_{i}" for i, c in enumerate(columns))
    agg = run(f"SELECT COUNT(*) AS total_rows, COUNT(DISTINCT {id_col}) AS distinct_ids, "
              f"MIN({created_col}) AS first_created, MAX({created_col}) AS last_created"
              + (f", {nn}" if nn else "") + f" FROM {table}", ())[0]
    out = {"table": table, "total_rows": agg["total_rows"], "distinct_ids": agg["distinct_ids"],
           "duplicate_rows": (agg["total_rows"] or 0) - (agg["distinct_ids"] or 0),
           "first_created": str(agg["first_created"]), "last_created": str(agg["last_created"]),
           "null_share": {c: (1 - (agg[f"nn_{i}"] or 0) / agg["total_rows"]) if agg["total_rows"] else None
                          for i, c in enumerate(columns)}}
    monthly = run(f"SELECT DATE_TRUNC('month', {created_col}) AS month, COUNT(*) AS n FROM {table} "
                  f"GROUP BY 1 ORDER BY 1 LIMIT 600", ())
    out["monthly_volume"] = [{"month": str(r["month"])[:7], "n": r["n"]} for r in monthly]
    if status_col:
        out["status_values"] = [{"value": r["value"], "n": r["n"]} for r in run(
            f"SELECT {status_col} AS value, COUNT(*) AS n FROM {table} GROUP BY 1 ORDER BY 2 DESC LIMIT 100", ())]
    return out


def check_mapping_with(run: Runner, m: SourceMapping) -> list[str]:
    db, schema, table = m.source_table.split(".")
    cols = {r["column_name"].upper() for r in run(
        f"SELECT column_name FROM {_q(db)}.INFORMATION_SCHEMA.COLUMNS WHERE table_schema = ? AND table_name = ?",
        (schema, table))}
    problems = []
    if not cols:
        return [f"{m.source_table} is not visible to the configured role (or does not exist)"]
    for contract_col, src in m.columns.items():
        if src and src not in cols:
            problems.append(f"{contract_col}: column {src} not found in {m.source_table}")
    if m.dedupe_order_by and m.dedupe_order_by not in cols:
        problems.append(f"dedupe.order_by column {m.dedupe_order_by} not found")
    if not problems:
        mapped = {v for vals in m.status_classes.values() for v in vals}
        for r in run(f"SELECT UPPER(TRIM({m.columns['status']})) AS value, COUNT(*) AS n FROM {m.source_table} "
                     f"GROUP BY 1 ORDER BY 2 DESC LIMIT 200", ()):
            if r["value"] is not None and r["value"] not in mapped:
                problems.append(f"status value {r['value']!r} ({r['n']} rows) is not in status_classes")
    return problems


def _snowflake_runner(settings) -> Runner:
    from .backends.snowflake import SnowflakeBackend
    backend = SnowflakeBackend(settings)

    def run(sql: str, params: tuple) -> list[dict]:
        return backend.query(sql, params, timeout_s=settings.query_timeout_s, max_rows=100000, tag="discovery").records()
    return run


def check_mapping(settings, m: SourceMapping) -> int:
    problems = check_mapping_with(_snowflake_runner(settings), m)
    for p in problems:
        print("PROBLEM:", p)
    print("Mapping check passed." if not problems else f"{len(problems)} problem(s).")
    return 1 if problems else 0


def to_markdown(inv: dict, top: int = 40) -> str:
    c = inv["context"]
    lines = [f"# Snowflake discovery — generated {inv['generated_at']}", "",
             f"Role `{c.get('role')}`, warehouse `{c.get('warehouse')}`, region `{c.get('region')}`, "
             f"Snowflake {c.get('version')}.", "", f"Databases visible: {', '.join(inv['databases']) or 'none'}", ""]
    if inv["warehouses"]:
        lines += ["| Warehouse | Size | State | Auto-suspend (s) |", "|---|---|---|---|"]
        lines += [f"| {w['name']} | {w['size']} | {w['state']} | {w['auto_suspend']} |" for w in inv["warehouses"]]
        lines.append("")
    lines += ["## Candidate service tables (heuristic ranking — review before mapping)", "",
              "| Score | Table | Rows | Last altered | id / created / closed / status columns | Possibly sensitive |",
              "|---|---|---|---|---|---|"]
    for t in inv["tables"][:top]:
        cc = t["candidate_columns"]
        lines.append(f"| {t['score']} | `{t['name']}` | {t['row_count']} | {t['last_altered']} | "
                     f"{', '.join(cc.get('id', [])[:2])} / {', '.join(cc.get('created', [])[:2])} / "
                     f"{', '.join(cc.get('closed', [])[:2])} / {', '.join(cc.get('status', [])[:2])} | "
                     f"{', '.join(t['possibly_sensitive'][:4])} |")
    if inv["errors"]:
        lines += ["", "## Not readable with this role", ""] + [f"- {e}" for e in inv["errors"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    from .config import load_settings
    ap = argparse.ArgumentParser(prog="python -m si.discover")
    sub = ap.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("--databases")
    inv.add_argument("--out", default="discovery")
    pr = sub.add_parser("profile")
    pr.add_argument("table")
    pr.add_argument("--id", required=True)
    pr.add_argument("--created", required=True)
    pr.add_argument("--status")
    pr.add_argument("--columns", default="", help="comma-separated columns to measure completeness for")
    pr.add_argument("--out", default="discovery")
    args = ap.parse_args(argv)
    settings = load_settings()
    if settings.backend != "snowflake":
        print("Discovery needs SI_BACKEND=snowflake and Snowflake credentials.", file=sys.stderr)
        return 2
    run = _snowflake_runner(settings)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.cmd == "inventory":
        result = inventory(run, args.databases.split(",") if args.databases else None)
        (out / "inventory.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        (out / "inventory.md").write_text(to_markdown(result), encoding="utf-8")
        print(f"Wrote {out / 'inventory.md'} ({len(result['tables'])} tables)")
    else:
        cols = [c for c in args.columns.split(",") if c]
        result = profile(run, args.table.upper(), args.id, args.created, args.status, cols)
        name = args.table.lower().replace(".", "_")
        (out / f"profile_{name}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items() if k != "monthly_volume"}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
