"""Static validation of every SQL statement before it reaches a warehouse.

Defence in depth: the application only builds SQL from the registry, but every
statement — including the registry's — passes this check, so a bug, a bad
registry edit or a future natural-language-to-SQL feature cannot run anything
but one bounded, read-only SELECT over approved relations and columns.
Snowflake role grants remain the real authority; this check never widens them.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

MAX_SQL_CHARS = 20000

_FORBIDDEN_NAMES = ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "Command",
                    "Use", "Set", "Transaction", "Commit", "Rollback", "Copy", "Grant", "Revoke", "TruncateTable",
                    "Describe", "Show", "Into", "Lateral", "Pragma", "Execute", "LoadData", "Put", "Get", "Call")
FORBIDDEN = tuple(t for t in (getattr(exp, n, None) for n in _FORBIDDEN_NAMES) if isinstance(t, type))
# Functions sqlglot does not recognise are refused unless listed here.
ALLOWED_UNKNOWN_FUNCTIONS = frozenset()


class GuardError(ValueError):
    """The statement was refused before execution."""


@dataclass(frozen=True)
class Policy:
    relations: frozenset[str]               # fully qualified, upper case
    columns: frozenset[str]                 # lower case
    max_rows: int
    denied_columns: frozenset[str] = frozenset()
    relation_prefixes: tuple[str, ...] = ()  # e.g. "DB.INFORMATION_SCHEMA." for discovery
    any_column: bool = False                 # discovery only: metadata views have their own columns


def _qualified(t: exp.Table) -> str:
    return ".".join(p.name.upper() for p in (t.args.get("catalog"), t.args.get("db"), t.this) if p is not None)


def validate(sql: str, policy: Policy) -> exp.Expression:
    if not sql or not sql.strip():
        raise GuardError("Empty statement")
    if len(sql) > MAX_SQL_CHARS:
        raise GuardError("Statement is too long")
    try:
        statements = [s for s in sqlglot.parse(sql, read="snowflake") if s is not None]
    except ParseError as exc:
        raise GuardError("Statement could not be parsed as Snowflake SQL") from exc
    if len(statements) != 1:
        raise GuardError("Exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise GuardError("Only SELECT statements are allowed")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN):
            raise GuardError(f"{type(node).__name__} is not allowed")

    ctes = {c.alias_or_name.upper() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            raise GuardError("Table functions are not allowed")
        name = _qualified(t)
        if name in ctes:
            continue
        if name.count(".") != 2:
            raise GuardError(f"Relation {name} must be fully qualified")
        if name not in policy.relations and not any(name.startswith(p) for p in policy.relation_prefixes):
            raise GuardError(f"Relation {name} is not approved for this application")

    for f in tree.find_all(exp.Func):
        fname = (f.sql_name() if not isinstance(f, exp.Anonymous) else str(f.this)).upper()
        if "$" in fname:
            raise GuardError(f"System function {fname} is not allowed")
        if isinstance(f, exp.Anonymous) and fname not in ALLOWED_UNKNOWN_FUNCTIONS:
            raise GuardError(f"Function {fname} is not allowed")

    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise GuardError("SELECT * is not allowed; name the columns")

    aliases = {a.alias.lower() for a in tree.find_all(exp.Alias)}
    for col in tree.find_all(exp.Column):
        name = col.name.lower()
        if name in policy.denied_columns:
            raise GuardError(f"Column {name} is restricted")
        if not policy.any_column and name not in policy.columns and name not in aliases:
            raise GuardError(f"Column {name} is not approved for this application")

    selects = [tree] if isinstance(tree, exp.Select) else list(tree.find_all(exp.Select))
    top = selects[0]
    limit = tree.args.get("limit") or top.args.get("limit")
    aggregate_only = (isinstance(tree, exp.Select) and not tree.args.get("group")
                      and all(e.find(exp.AggFunc) is not None for e in tree.expressions))
    if limit is None:
        if not aggregate_only:
            raise GuardError("Row-returning statements must have a LIMIT")
    else:
        value = limit.expression if isinstance(limit, exp.Limit) else limit
        if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) > policy.max_rows:
            raise GuardError(f"LIMIT must be a literal no greater than {policy.max_rows}")
    return tree
