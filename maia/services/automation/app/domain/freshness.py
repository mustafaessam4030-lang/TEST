"""Freshness policy engine. Config-driven, server-side, never model-driven."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models.schemas import Freshness, RecordStatus


@dataclass(frozen=True)
class FreshnessDecision:
    freshness: Freshness
    action: str           # use_cache | refresh | refresh_or_fallback | no_cache
    age_days: float | None
    ttl_days: float
    reason: str


class FreshnessPolicy:
    """Loaded from config/freshness.yaml. Changing it is a deploy, not a prompt edit."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.default = {
            "ttl_days": 30,
            "hard_stale_days": 365,
            "negative_cache_ttl_days": 7,
            "on_stale": "refresh_if_source_healthy",
            "allow_stale_fallback": True,
            **(cfg.get("default") or {}),
        }
        self.sources: dict[str, dict[str, Any]] = cfg.get("sources") or {}
        self.overrides: list[dict[str, Any]] = cfg.get("overrides") or []

    def for_source(self, source: str) -> dict[str, Any]:
        return {**self.default, **(self.sources.get(source) or {})}

    def _apply_overrides(self, policy: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        result = dict(policy)
        for rule in self.overrides:
            when = rule.get("when") or {}
            if all(str(ctx.get(k)) == str(v) for k, v in when.items()):
                result.update(rule.get("then") or {})
        return result

    def evaluate(
        self,
        record: dict[str, Any] | None,
        *,
        source: str,
        mode: str = "auto",
        circuit_state: str = "CLOSED",
        actor_role: str = "user",
        now: datetime | None = None,
    ) -> FreshnessDecision:
        policy = self._apply_overrides(
            self.for_source(source),
            {"reason": mode, "actor_role": actor_role, "circuit_state": circuit_state.lower()},
        )
        ttl = float(policy["ttl_days"])

        if record is None:
            return FreshnessDecision(Freshness.MISSING, "no_cache", None, ttl, "not_in_store")

        now = now or datetime.now(timezone.utc)
        retrieved = record.get("retrieved_at")
        if isinstance(retrieved, str):
            retrieved = datetime.fromisoformat(retrieved.replace("Z", "+00:00"))
        if retrieved is None:
            return FreshnessDecision(Freshness.MISSING, "no_cache", None, ttl, "no_timestamp")
        if retrieved.tzinfo is None:
            retrieved = retrieved.replace(tzinfo=timezone.utc)
        age_days = round((now - retrieved).total_seconds() / 86400.0, 3)

        status = record.get("status")
        status = status.value if isinstance(status, RecordStatus) else status
        if status == RecordStatus.NOT_FOUND.value:
            neg_ttl = float(policy["negative_cache_ttl_days"])
            if age_days <= neg_ttl:
                return FreshnessDecision(Freshness.NEGATIVE_CACHED, "use_cache", age_days,
                                         neg_ttl, "negative_cache_valid")
            return FreshnessDecision(Freshness.MISSING, "refresh", age_days, neg_ttl,
                                     "negative_cache_expired")
        if status == RecordStatus.QUARANTINED.value:
            return FreshnessDecision(Freshness.MISSING, "refresh", age_days, ttl, "quarantined")

        if mode == "force_refresh":
            return FreshnessDecision(Freshness.STALE, "refresh", age_days, ttl, "force_refresh")
        if mode == "cache_only":
            fresh = Freshness.FRESH if age_days <= ttl else Freshness.STALE
            return FreshnessDecision(fresh, "use_cache", age_days, ttl, "cache_only_mode")

        if age_days <= ttl:
            return FreshnessDecision(Freshness.FRESH, "use_cache", age_days, ttl, "within_ttl")

        hard = float(policy["hard_stale_days"])
        freshness = Freshness.HARD_STALE if age_days > hard else Freshness.STALE
        on_stale = policy["on_stale"]
        if on_stale == "serve_cache":
            return FreshnessDecision(freshness, "use_cache", age_days, ttl,
                                     "policy_serve_cache_while_source_unhealthy")
        if on_stale == "refresh_if_source_healthy" and circuit_state == "OPEN":
            return FreshnessDecision(freshness, "use_cache", age_days, ttl, "circuit_open")
        action = "refresh_or_fallback" if policy["allow_stale_fallback"] else "refresh"
        return FreshnessDecision(freshness, action, age_days, ttl, "beyond_ttl")
