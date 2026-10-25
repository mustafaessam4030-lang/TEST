"""Machine details (opened from a ranked machine card): OLGA jobs, fault history and service-meter hours for one
serial. Read-only SELECTs over the approved curated tables, with the serial and dates as bind parameters; every
spec passes the same guard (check_spec) as the rest of the app."""
from __future__ import annotations

from datetime import date, timedelta

from .metrics import Spec

T_OPP = "SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES"
T_FAULT = "SERVICE_INTELLIGENCE.CURATED.FAULT_EVENTS"
T_DAILY = "SERVICE_INTELLIGENCE.CURATED.ASSET_DAILY"


def olga_jobs(serial: str) -> Spec:
    """OLGA lists one line per part/interval; group them per job and component, largest value first."""
    return Spec(f"SELECT job, component, smcs_group, COUNT(*) AS lines, MIN(target_date) AS first_due, "
                f"MAX(target_date) AS last_due, SUM(total_value) AS total_value FROM {T_OPP} WHERE asset_serial = ? "
                f"AND snapshot_date = (SELECT MAX(snapshot_date) FROM {T_OPP}) GROUP BY job, component, smcs_group "
                f"ORDER BY total_value DESC LIMIT 40", (serial,), "machine_olga", None)


def faults(serial: str, as_of: date, days: int = 90) -> Spec:
    start = (as_of - timedelta(days=days - 1)).isoformat()
    end = (as_of + timedelta(days=1)).isoformat()
    return Spec(f"SELECT fault_description, severity, COUNT(*) AS events, COUNT(DISTINCT occurred_at::DATE) AS fault_days, "
                f"MAX(occurred_at) AS last_seen FROM {T_FAULT} WHERE asset_serial = ? AND occurred_at >= ? AND occurred_at < ? "
                f"GROUP BY fault_description, severity ORDER BY fault_days DESC, events DESC LIMIT 25",
                (serial, start, end), "machine_faults", None)


def hours(serial: str, as_of: date, days: int = 120) -> Spec:
    start = (as_of - timedelta(days=days)).isoformat()
    return Spec(f"SELECT reading_date, smh FROM {T_DAILY} WHERE asset_serial = ? AND reading_date >= ? "
                f"ORDER BY reading_date LIMIT 400", (serial, start), "machine_hours", None)
