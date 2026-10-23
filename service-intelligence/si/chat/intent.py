"""Deterministic question understanding.

Maps a question to a structured plan that can only name registry metrics,
registry dimensions, named periods and plan ids. Nothing in a plan is SQL, so a
question — however it is worded — can only select from approved calculations.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from ..query import Period
from ..registry import Registry

INTENTS = ("metric", "what_changed", "recommendations", "evidence", "missing_data", "definition", "help",
           "unsupported", "restricted")
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
SENSITIVE = re.compile(r"\b(customer names?|names? of customers?|phone|e-?mail|address(es)?|password|credential|"
                       r"secret|token|api key|private key|descriptions?|free[- ]text|personal|salary|ssn)\b", re.I)
SQL_LIKE = re.compile(r"\b(select\s+.+\s+from|insert\s+into|update\s+\w+\s+set|delete\s+from|drop\s+(table|view)|"
                      r"create\s+(table|view)|grant\s+|alter\s+|truncate\s+|merge\s+into|call\s+\w+)\b", re.I | re.S)
REC_REF = re.compile(r"\bR-(\d{1,9})\b", re.I)
NAMED_PERIODS = ("this_month", "last_month", "this_week", "last_week", "this_quarter", "last_quarter", "this_year",
                 "last_year", "last_7_days", "last_30_days", "last_90_days")


@dataclass(frozen=True)
class Plan:
    intent: str
    metric_id: str | None = None
    dimension: str | None = None
    period: Period | None = None
    period_text: str | None = None
    compare: bool = False
    trend: bool = False
    order: str = "desc"            # for rankings: desc = highest first
    rec_id: str | None = None
    reason: str | None = None
    source: str = "rules"
    notes: tuple[str, ...] = field(default_factory=tuple)


def _month_period(year: int, month: int) -> Period:
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return Period(start, end)


def named_period(name: str, today: date) -> Period:
    """`today` is the last day with data (reporting time zone)."""
    tomorrow = today + timedelta(days=1)
    if name == "this_month":
        return Period(today.replace(day=1), tomorrow)
    if name == "last_month":
        first = today.replace(day=1)
        prev = first - timedelta(days=1)
        return _month_period(prev.year, prev.month)
    if name == "this_week":
        return Period(today - timedelta(days=today.weekday()), tomorrow)
    if name == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        return Period(start, start + timedelta(days=7))
    q_start_month = 3 * ((today.month - 1) // 3) + 1
    if name == "this_quarter":
        return Period(date(today.year, q_start_month, 1), tomorrow)
    if name == "last_quarter":
        end = date(today.year, q_start_month, 1)
        start_month = q_start_month - 3
        start = date(today.year - (start_month < 1), (start_month - 1) % 12 + 1, 1)
        return Period(start, end)
    if name == "this_year":
        return Period(date(today.year, 1, 1), tomorrow)
    if name == "last_year":
        return Period(date(today.year - 1, 1, 1), date(today.year, 1, 1))
    m = re.match(r"last_(\d+)_days", name)
    if m:
        return Period(tomorrow - timedelta(days=int(m.group(1))), tomorrow)
    raise ValueError(f"unknown period {name}")


def parse_period(text: str, today: date) -> tuple[Period | None, str | None]:
    t = text.lower()
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:to|until|-|–|and)\s*(\d{4}-\d{2}-\d{2})", t)
    if m:
        a, b = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        if b >= a:
            return Period(a, b + timedelta(days=1)), f"{a.isoformat()} to {b.isoformat()}"
    m = re.search(r"\b(?:last|past|previous)\s+(\d{1,3})\s+(day|days|week|weeks|month|months)\b", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * (1 if unit.startswith("day") else 7 if unit.startswith("week") else 30)
        days = max(1, min(days, 3650))
        return named_period(f"last_{days}_days", today), f"last {n} {unit}"
    for phrase, name in (("this month", "this_month"), ("month to date", "this_month"), ("mtd", "this_month"),
                         ("last month", "last_month"), ("previous month", "last_month"),
                         ("this week", "this_week"), ("last week", "last_week"),
                         ("this quarter", "this_quarter"), ("last quarter", "last_quarter"),
                         ("this year", "this_year"), ("year to date", "this_year"), ("ytd", "this_year"),
                         ("last year", "last_year")):
        if re.search(rf"\b{phrase}\b", t):
            return named_period(name, today), phrase
    if re.search(r"\byesterday\b", t):
        y = today - timedelta(days=1)
        return Period(y, today), "yesterday"
    if re.search(r"\btoday\b", t):
        return Period(today, today + timedelta(days=1)), "today"
    m = re.search(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b(?:\s+(\d{4}))?", t)
    if m and not (m.group(1) == "may" and not m.group(2)):
        month = MONTHS[m.group(1)]
        year = int(m.group(2)) if m.group(2) else (today.year if month <= today.month else today.year - 1)
        return _month_period(year, month), f"{calendar.month_name[month]} {year}"
    return None, None


def _find(text: str, candidates: dict[str, tuple[str, ...]]) -> str | None:
    best, best_len = None, 0
    for key, words in candidates.items():
        for w in words:
            if len(w) > best_len and re.search(rf"\b{re.escape(w)}\b", text):
                best, best_len = key, len(w)
    return best


def parse(question: str, registry: Registry, today: date, previous: Plan | None = None) -> Plan:
    q = " ".join(question.strip().split())
    t = q.lower()
    if not t:
        return Plan("help")
    if SQL_LIKE.search(t):
        return Plan("restricted", reason="The assistant does not run SQL typed into the chat. Ask in plain language "
                                         "and it will use the approved metric definitions.")
    if SENSITIVE.search(t):
        return Plan("restricted", reason="Personal, customer, free-text and credential fields are not part of the "
                                         "approved service data, so the assistant cannot show them.")
    m = REC_REF.search(q)
    if m:
        return Plan("evidence", rec_id=f"R-{m.group(1)}")

    period, period_text = parse_period(t, today)
    metrics = {mid: (mt.name.lower(),) + mt.synonyms for mid, mt in registry.metrics.items()}
    metric_id = _find(t, metrics)
    dims = {d: dim.synonyms for d, dim in registry.dimensions.items()}
    dimension = _find(t, dims)
    compare = bool(re.search(r"\b(compare|compared|comparison|vs\.?|versus|previous period|change[sd]?|"
                             r"than last|than the previous|trend(ing)?|improv(ed|ing)|worse|better)\b", t))
    trend = bool(re.search(r"\b(trend|over time|monthly|weekly|daily|by month|by week|per month|per week)\b", t))
    order = "asc" if re.search(r"\b(shortest|fastest|lowest|least|best|smallest|quickest)\b", t) else "desc"

    if re.search(r"\b(missing|gaps?|completeness|data quality|reliabl[ey]|what data|incomplete|fresh|freshness|"
                 r"up to date|last refresh)\b", t) and not metric_id:
        return Plan("missing_data", period=period, period_text=period_text)
    if re.search(r"\b(defin(e|ed|ition)|calculated|how (is|do you) (calculate|measure|compute)|formula|"
                 r"what does .+ mean|meaning of)\b", t) and metric_id:
        return Plan("definition", metric_id=metric_id)
    if re.search(r"\b(opportunit(y|ies)|improve(ment)?s?|recommend(ation)?s?|what should we|priorit(y|ies|ise|ize)|"
                 r"action plan|biggest problems?|focus on)\b", t) and not (metric_id and dimension):
        return Plan("recommendations", period=period, period_text=period_text)
    if re.search(r"\b(what changed|what has changed|what's changed|changes in|how did .* (change|perform)|"
                 r"performance (this|last)|summary|overview|how are we doing|how is service)\b", t) and not metric_id:
        return Plan("what_changed", period=period, period_text=period_text, compare=True)
    if re.search(r"^(hi|hello|hey|help|what can you do|\?)\b", t) and not metric_id:
        return Plan("help")

    if not metric_id and previous and previous.metric_id and (dimension or period or compare or trend):
        return replace(previous, intent="metric", dimension=dimension or previous.dimension,
                       period=period or previous.period, period_text=period_text or previous.period_text,
                       compare=compare, trend=trend, order=order,
                       notes=(f"Continuing with {registry.metric(previous.metric_id).name} from the previous question.",))
    if metric_id:
        return Plan("metric", metric_id=metric_id, dimension=dimension, period=period, period_text=period_text,
                    compare=compare, trend=trend, order=order)
    return Plan("unsupported", reason="The question does not match an approved metric.")
