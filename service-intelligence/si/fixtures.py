"""SYNTHETIC development data. Not company data.

Generates a deterministic, made-up service-request table for development, demos
and automated tests while Snowflake is unavailable. Column names deliberately
differ from the contract so the mapping layer is exercised end to end. Known
patterns are planted so the recommendation detectors have something to find:

* region "South" resolves more slowly, and got slower in the last 60 days
* category "Hydraulics" volume surges in the last 60 days
* team "Field Team C" carries an aged open backlog
* about 5% of rows have no SLA target, 2% no priority, 3% no region
* a few rows are closed before they were opened, use an unmapped status, or are
  duplicated with an older modification time
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

SYNTHETIC_LABEL = "SYNTHETIC development data — not company data"
SYNTHETIC_AS_OF = datetime(2026, 10, 20, 6, 0)          # UTC, the fixture's "last refresh"
RAW_TABLE = "SI_SYNTHETIC.RAW.SERVICE_REQUESTS"
RAW_COLUMNS = ("TICKET_NO", "OPENED_TS", "RESOLVED_TS", "TARGET_TS", "LAST_MODIFIED_TS", "ETL_LOADED_TS",
               "STATUS_TXT", "PRIO", "ISSUE_CATEGORY", "SVC_TYPE", "WORK_CENTRE", "REGION_NAME",
               "CUSTOMER_NAME", "PROBLEM_DESCRIPTION")

REGIONS = ["North", "South", "East", "West", "Delta"]
TEAMS = ["Field Team A", "Field Team B", "Field Team C", "Workshop 1", "Workshop 2"]
CATEGORIES = ["Engine", "Hydraulics", "Electrical", "Undercarriage", "Transmission", "Cooling"]
SERVICE_TYPES = ["Repair", "Inspection", "Preventive maintenance", "Warranty"]
PRIORITIES = {"P1": 8, "P2": 24, "P3": 72, "P4": 168}           # SLA target hours (synthetic)
PRIORITY_WEIGHTS = [0.1, 0.3, 0.4, 0.2]
MEDIAN_HOURS = {"P1": 6, "P2": 20, "P3": 55, "P4": 120}


def generate(seed: int = 7, start: datetime = datetime(2025, 4, 1), as_of: datetime = SYNTHETIC_AS_OF,
             base_per_day: float = 10.0) -> list[tuple]:
    rng = random.Random(seed)
    rows: list[tuple] = []
    recent = as_of - timedelta(days=60)
    n = 0
    day = start
    while day < as_of:
        progress = (day - start).days / max(1, (as_of - start).days)
        count = max(0, int(rng.gauss(base_per_day * (0.9 + 0.3 * progress), 2.5)))
        for _ in range(count):
            n += 1
            opened = day + timedelta(seconds=rng.randint(0, 86399))
            if opened >= as_of:
                continue
            cat_weights = [1, 1, 1, 1, 1, 1]
            if opened >= recent:
                cat_weights[1] = 3.0                                   # Hydraulics surge
            category = rng.choices(CATEGORIES, cat_weights)[0]
            prio = rng.choices(list(PRIORITIES), PRIORITY_WEIGHTS)[0]
            region = rng.choice(REGIONS)
            team = rng.choice(TEAMS)
            svc = rng.choice(SERVICE_TYPES)
            factor = 1.0
            if region == "South":
                factor *= 1.8 if opened < recent else 2.6
            if team == "Field Team C":
                factor *= 1.3
            hours = MEDIAN_HOURS[prio] * factor * math.exp(rng.gauss(0, 0.6))
            resolved = opened + timedelta(hours=hours)
            target = opened + timedelta(hours=PRIORITIES[prio])
            status = rng.choice(["Resolved", "Closed"])
            # Field Team C leaves a share of its work open for a long time.
            stuck = team == "Field Team C" and rng.random() < 0.06
            if resolved > as_of or stuck or rng.random() < 0.004:
                resolved = None
                status = rng.choice(["New", "In Progress", "Waiting Parts"])
            if rng.random() < 0.03:
                status, resolved = "Cancelled", None
            if rng.random() < 0.003:
                status = "Pending Review"                               # not in the status mapping
            if resolved is not None and rng.random() < 0.003:
                resolved = opened - timedelta(hours=rng.randint(1, 48))  # data-entry error
            modified = min(as_of, (resolved or opened) + timedelta(minutes=rng.randint(1, 600)))
            loaded = min(as_of, modified + timedelta(hours=rng.randint(1, 20)))
            if rng.random() < 0.05:
                target = None
            row = (f"SR-{100000 + n}", opened, resolved, target, modified, loaded, status,
                   None if rng.random() < 0.02 else prio, category, svc, team,
                   None if rng.random() < 0.03 else region,
                   f"Customer {rng.randint(1, 400)}", f"Free-text fault description {n}")
            rows.append(row)
            if rng.random() < 0.01:                                     # stale duplicate extract row
                stale = list(row)
                stale[4] = modified - timedelta(days=2)
                stale[6] = "In Progress"
                stale[2] = None
                rows.append(tuple(stale))
        day += timedelta(days=1)
    return rows
