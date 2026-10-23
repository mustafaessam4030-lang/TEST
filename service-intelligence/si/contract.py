"""The service-request data contract.

The application never queries company tables directly. It queries one governed
view (SI_CONTRACT_VIEW) whose shape is fixed here. `mapping.py` generates that
view from a reviewed mapping file, so the real source tables and columns are
named exactly once, by a person, after discovery — never guessed in code.

A column whose mapping is empty is still present in the view (as NULL) so the
SQL stays valid, but every metric that needs it is reported as unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass

STATUS_CLASSES = ("OPEN", "CLOSED", "CANCELLED")
UNMAPPED_STATUS = "UNMAPPED"
NOT_SET = "(not set)"


@dataclass(frozen=True)
class ContractColumn:
    name: str
    sql_type: str
    role: str            # id | timestamp | status | dimension | derived
    required: bool
    description: str


COLUMNS: tuple[ContractColumn, ...] = (
    ContractColumn("request_id", "VARCHAR", "id", True, "Unique identifier of the service request / work order / case."),
    ContractColumn("created_at", "TIMESTAMP_NTZ", "timestamp", True, "When the request was opened, in UTC."),
    ContractColumn("closed_at", "TIMESTAMP_NTZ", "timestamp", False, "When the request was resolved or closed, in UTC."),
    ContractColumn("due_at", "TIMESTAMP_NTZ", "timestamp", False, "SLA target time in UTC (source column, or derived from an approved SLA policy)."),
    ContractColumn("updated_at", "TIMESTAMP_NTZ", "timestamp", False, "Last change to the source record, in UTC."),
    ContractColumn("loaded_at", "TIMESTAMP_NTZ", "timestamp", False, "When the row was loaded into Snowflake, in UTC (data freshness)."),
    ContractColumn("status", "VARCHAR", "status", True, "Source status value, unchanged."),
    ContractColumn("status_class", "VARCHAR", "derived", True, "OPEN, CLOSED, CANCELLED or UNMAPPED, from the reviewed status mapping."),
    ContractColumn("priority", "VARCHAR", "dimension", False, "Priority as recorded in the source."),
    ContractColumn("category", "VARCHAR", "dimension", False, "Issue or service category."),
    ContractColumn("service_type", "VARCHAR", "dimension", False, "Type of service (e.g. repair, inspection)."),
    ContractColumn("team", "VARCHAR", "dimension", False, "Team or work centre that owns the request."),
    ContractColumn("region", "VARCHAR", "dimension", False, "Region, branch or site."),
)

BY_NAME = {c.name: c for c in COLUMNS}
NAMES = tuple(c.name for c in COLUMNS)
DIMENSIONS = tuple(c.name for c in COLUMNS if c.role == "dimension")
TIMESTAMPS = tuple(c.name for c in COLUMNS if c.role == "timestamp")
# Columns a drill-down may show. Free text, customer and person fields are not in
# the contract at all, so they cannot be selected through the application.
DRILLDOWN_COLUMNS = ("request_id", "created_at", "closed_at", "due_at", "status", "status_class",
                     "priority", "category", "service_type", "team", "region")
