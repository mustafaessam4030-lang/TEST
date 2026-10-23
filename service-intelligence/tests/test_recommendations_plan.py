"""Recommendation ranking and evidence attribution, plan workflow, permissions and audit trail."""
import pytest

from conftest import AS_OF, MARCH
from si.plans import PlanError, visible
from si.recommend import Thresholds, priority, sample_confidence

APPROVER = {"plan.read", "plan.add", "plan.review", "plan.approve"}
ANALYST = {"plan.read", "plan.add", "plan.review"}


@pytest.fixture(scope="module")
def recs(synthetic):
    fresh = synthetic.analytics.freshness({})
    period = synthetic.analytics.default_period(fresh["as_of"])
    return synthetic, period, fresh, synthetic.recommender.generate(period, {}, {}, fresh)


def test_detectors_find_the_planted_synthetic_patterns(recs):
    _, _, _, out = recs
    keys = {c["key"] for c in out["candidates"]}
    assert "volume_surge:category=Hydraulics" in keys
    assert "slow_segment:region=South" in keys
    assert "backlog_aging:team=Field Team C" in keys
    assert not out["skipped"]


def test_ranking_is_by_transparent_score(recs):
    _, _, _, out = recs
    scores = [c["priority"]["score"] for c in out["candidates"]]
    assert scores == sorted(scores, reverse=True)
    assert [c["rank"] for c in out["candidates"]] == list(range(1, len(scores) + 1))
    for c in out["candidates"]:
        p = c["priority"]
        assert p["score"] == pytest.approx(round(0.45 * p["impact"] + 0.30 * p["urgency"] + 0.25 * p["confidence"], 2))


def test_every_candidate_is_traceable_and_labelled(recs):
    _, _, _, out = recs
    for c in out["candidates"]:
        assert c["evidence"], c["key"]
        for e in c["evidence"]:
            assert e["query_id"] and e["sql"] and e["kind"] == "measured"
        assert c["expected_impact"]["kind"] == "projection"
        assert c["effort"]["estimate"] is None
        assert "does not establish the cause" in c["causation_note"]
        assert c["snapshot"]["snapshot_id"].startswith("S-") and c["snapshot"]["registry_version"]
        if c["owner_suggestion"]:
            assert c["affected"]["dimension"] == "team"


def test_recommendation_evidence_equals_dashboard_metric(recs):
    svc, period, fresh, out = recs
    slow = next(c for c in out["candidates"] if c["key"] == "slow_segment:region=South")
    dash = svc.analytics.metric("median_resolution_hours", period, {"region": ("South",)}, {}, fresh["as_of"], compare=False)
    assert slow["evidence"][0]["value"] == pytest.approx(dash["value"])
    overall = svc.analytics.metric("median_resolution_hours", period, {}, {}, fresh["as_of"], compare=False)
    assert slow["evidence"][1]["value"] == pytest.approx(overall["value"])


def test_small_segments_never_produce_candidates(known):
    fresh = known.analytics.freshness({}, now=AS_OF)
    out = known.recommender.generate(MARCH, {}, {}, fresh)
    assert all(c["detector"] == "data_quality" or c["detector"] == "past_due" for c in out["candidates"]) or not out["candidates"]
    strict = type(known.recommender)(known.analytics, Thresholds(min_segment_n=10 ** 6, past_due_share=2.0,
                                                                 completeness_floor=0.0, issue_share_floor=2.0))
    assert strict.generate(MARCH, {}, {}, fresh)["candidates"] == []


def test_scoring_helpers():
    assert sample_confidence(500, 1.0, False) == 5
    assert sample_confidence(500, 0.5, True) == 3
    assert sample_confidence(5, None, True) == 1
    assert priority(5, 5, 5)["label"] == "High" and priority(1, 1, 1)["label"] == "Low"


def test_plan_workflow_and_audit_trail(recs):
    svc, _, _, out = recs
    plans = svc.plans
    cand = next(c for c in out["candidates"] if c["key"] == "backlog_aging:team=Field Team C")
    rec = plans.add(cand, "analyst.user", "worth a look")
    rid = rec["rec_id"]
    assert rid.startswith("R-") and rec["status"] == "proposed" and rec["version"] == 1
    with pytest.raises(PlanError) as dup:
        plans.add(cand, "analyst.user")
    assert dup.value.status == 409

    with pytest.raises(PlanError) as no:
        plans.transition(rid, "approved", "analyst.user", ANALYST, 1)
    assert no.value.status == 403
    with pytest.raises(PlanError) as bad:
        plans.transition(rid, "completed", "approver.user", APPROVER, 1)
    assert bad.value.status == 409
    rec = plans.transition(rid, "reviewed", "analyst.user", ANALYST, 1, "checked figures")
    with pytest.raises(PlanError) as stale:
        plans.transition(rid, "approved", "approver.user", APPROVER, 1)
    assert stale.value.status == 409
    with pytest.raises(PlanError, match="note is required"):
        plans.transition(rid, "rejected", "approver.user", APPROVER, rec["version"])
    rec = plans.transition(rid, "approved", "approver.user", APPROVER, rec["version"])
    rec = plans.assign(rid, "Field Team C supervisor", "approver.user", APPROVER, rec["version"])
    with pytest.raises(PlanError):
        plans.assign(rid, "x", "analyst.user", ANALYST, rec["version"])
    rec = plans.transition(rid, "in_progress", "approver.user", APPROVER, rec["version"])
    rec = plans.comment(rid, "analyst.user", ANALYST, "work started")
    rec = plans.transition(rid, "completed", "approver.user", APPROVER, rec["version"])
    actions = [(e["action"], e["from_status"], e["to_status"], e["actor"]) for e in rec["history"]]
    assert actions == [
        ("created", None, "proposed", "analyst.user"),
        ("status_changed", "proposed", "reviewed", "analyst.user"),
        ("status_changed", "reviewed", "approved", "approver.user"),
        ("assigned", "approved", "approved", "approver.user"),
        ("status_changed", "approved", "in_progress", "approver.user"),
        ("commented", "in_progress", "in_progress", "analyst.user"),
        ("status_changed", "in_progress", "completed", "approver.user"),
    ]
    assert rec["owner"] == "Field Team C supervisor"
    assert rec["payload"]["evidence"] == cand["evidence"]          # evidence frozen with the plan item
    with pytest.raises(PlanError):
        plans.transition(rid, "in_progress", "approver.user", APPROVER, rec["version"])
    # a completed item no longer blocks saving the same finding again
    assert plans.add(cand, "analyst.user")["rec_id"] != rid


def test_plan_ids_are_validated(known):
    with pytest.raises(PlanError):
        known.plans.get("R-1; DROP TABLE x")
    with pytest.raises(PlanError) as e:
        known.plans.get("R-999999")
    assert e.value.status == 404


def test_scope_visibility():
    north = {"region": ("North",)}
    assert visible({"snapshot": {"scope": {"region": ["North"]}, "filters": {}}}, north)
    assert visible({"snapshot": {"scope": {}, "filters": {"region": ["North"]}}}, north)
    assert not visible({"snapshot": {"scope": {}, "filters": {}}, "affected": {"dimension": "region", "segment": "North"}}, north)
    assert not visible({"snapshot": {"scope": {}, "filters": {}}}, north)
    assert visible({"snapshot": {}}, {})
