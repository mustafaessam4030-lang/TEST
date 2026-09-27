"""Investigation modes: PARTS · TROUBLESHOOTING · FULL, and the shared 3D step.

Fixtures here are clearly synthetic documents in the store format. The real
check against Caterpillar SIS is scripts/e2e/investigate_real.py (INVESTIGATE.bat).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analysis.component_mapper import map_targets, normalize
from app.analysis.intent import route
from app.analysis.investigation import InvestigationService
from app.analysis.snapshots import InMemorySnapshots
from app.analysis.troubleshooting_analyzer import analyze_troubleshooting, parse_row

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
COLS = ["Part Number", "Serial Number", "Part Name", "Install Ind.", "Install Date", "Description"]
TR_ROWS = ["36-1-5 Cylinder #1 Injector Current Below Normal",
           "36-2-6 Cylinder #2 Injector Current Above Normal",
           "36-1-2 Cylinder #1 Injector Erratic, Intermittent, or Incorrect"]


def _doc(*, investigation=None, run="run_A"):
    rows = [{"cells": ["421-8926", "", "Exhaust Gp", "Factory", "", "EXHAUST GROUP"],
             "values": dict(zip(COLS, ["421-8926", "", "Exhaust Gp", "Factory", "", "EXHAUST GROUP"]))}]
    doc = {"run_id": run, "source": "Caterpillar SIS", "record": {
        "serial_number": "JAZ01865", "source_system": "cat_sis", "status": "ACTIVE",
        "retrieved_at": (NOW - timedelta(days=1)).isoformat(), "automation_run_id": run,
        "machine_serial_number": "JAZ01865", "engine_serial_number": "PRH04588",
        "parts_data": {"groups": [{"title": "Engine - Entire Group (PRH04588)",
                                   "group_serial": "PRH04588", "columns": COLS, "rows": rows}]}}}
    if investigation:
        doc["evidence"] = {"investigation": investigation}
    return doc


def _inv(names=None, status="VISUAL_ONLY", highlight=None):
    inv = {"troubleshooting": {"status": "CAPTURED", "sections": [
               {"section": "Troubleshooting", "count_displayed": 3, "items_read": 3,
                "rows": TR_ROWS}]},
           "model_3d": {"status": status, "tab": {"opened": True},
                        "viewer": {"canvases": [{"width": 900, "height": 500}], "libs": {}},
                        "component_names": names or [],
                        "component_names_source": "hoops_communicator_api" if names else None}}
    if highlight:
        inv["highlight"] = highlight
    return inv


def _svc(*docs):
    return InvestigationService(InMemorySnapshots([(f"JAZ01865_{d['run_id']}.json", d)
                                                   for d in docs]))


# ── routing ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,intent", [
    ("Show me parts for JAZ01865", "PARTS"), ("Search parts for JAZ01865", "PARTS"),
    ("What parts are on JAZ01865?", "PARTS"),
    ("What troubleshooting codes does JAZ01865 have?", "TROUBLESHOOTING"),
    ("Check the troubleshooting for JAZ01865", "TROUBLESHOOTING"),
    ("What problems are reported for JAZ01865?", "TROUBLESHOOTING"),
    ("Analyze JAZ01865", "ASK_MODE"), ("Full analysis of JAZ01865", "FULL_ANALYSIS"),
    ("هاتلي قطع JAZ01865", "PARTS"), ("هاتلي الـ troubleshooting بتاع JAZ01865", "TROUBLESHOOTING"),
    ("حلل JAZ01865", "ASK_MODE"),
])
def test_modes(text: str, intent: str) -> None:
    assert route(text).intent == intent


def test_arabic_with_context_and_filters() -> None:
    assert route("ايه المشاكل الموجودة على المعدة؟", "JAZ01865").intent == "TROUBLESHOOTING"
    r = route("دورلي على القطعة 123-4567", "JAZ01865")
    assert r.intent == "PARTS" and r.filters == {"part_number": "123-4567"}
    assert route("What is code 36-1-5 on JAZ01865?").filters == {"code": "36-1-5"}
    assert route("Find part 123-4567 on JAZ01865").filters == {"part_number": "123-4567"}


# ── ask mode, and nothing assumed ───────────────────────────────────────────
def test_analyze_asks_which_investigation() -> None:
    out = _svc(_doc()).handle("Analyze JAZ01865", as_of=NOW)
    assert out["status"] == "ASK_MODE"
    assert [c["label"] for c in out["choices"]] == ["🔧 Parts", "⚠️ Troubleshooting",
                                                     "📊 Full Analysis"]
    assert out["state"] == {"serial": "JAZ01865", "mode": "ASK_MODE", "filters": {}}


def test_invalid_serial_and_missing_data() -> None:
    svc = _svc(_doc())
    # no machine in the sentence or the conversation: not an investigation
    assert svc.handle("Show me parts for JA!", as_of=NOW)["handled"] is False
    out = svc.handle("Show me parts for JAZ01856", as_of=NOW)
    assert out["status"] == "NO_DATA" and out["candidates"][0]["serial"] == "JAZ01865"


# ── PARTS ───────────────────────────────────────────────────────────────────
def test_parts_workflow_and_part_search_request_the_3d_step() -> None:
    svc = _svc(_doc())
    parts = svc.handle("Show me parts for JAZ01865", as_of=NOW)
    assert parts["status"] == "OK" and parts["state"]["mode"] == "PARTS"
    found = svc.handle("Find part 421-8926 on JAZ01865", as_of=NOW)
    # found in the verified parts, and no 3D inspection stored yet → ask for it
    assert found["status"] == "NEEDS_RETRIEVAL"
    assert found["retrieve"] == {"serial": "JAZ01865", "investigate": ["model_3d"],
                                 "locate": ["421-8926", "Exhaust Gp"]}


def test_part_search_with_3d_metadata_maps_deterministically() -> None:
    names = [{"id": 7, "name": "Exhaust Gp"}, {"id": 8, "name": "Cooling Gp"}]
    out = _svc(_doc(investigation=_inv(names, "METADATA_AVAILABLE"))).handle(
        "Find part 421-8926 on JAZ01865", as_of=NOW)
    assert out["status"] == "OK" and out["matches"][0]["classification"] == "DIRECT"
    assert "Exhaust Gp → 3D component 'Exhaust Gp' (VERIFIED" in out["text"]


# ── TROUBLESHOOTING ─────────────────────────────────────────────────────────
def test_troubleshooting_needs_a_live_read_the_first_time() -> None:
    out = _svc(_doc()).handle("Check troubleshooting for JAZ01865", as_of=NOW)
    assert out["status"] == "NEEDS_RETRIEVAL"
    assert out["retrieve"]["investigate"] == ["model_3d", "troubleshooting"]


def test_troubleshooting_relationships_and_code_search() -> None:
    svc = _svc(_doc(investigation=_inv()))
    out = svc.handle("Check troubleshooting for JAZ01865", as_of=NOW)
    tr = out["troubleshooting"]
    assert tr["codes"] == ["36-1-2", "36-1-5", "36-2-6"]
    assert tr["components_with_multiple_codes"] == {"Cylinder #1 Injector": ["36-1-2", "36-1-5"]}
    assert ("Troubleshooting code 36-1-5 identifies Cylinder #1 Injector with Current Below "
            "Normal.") in out["text"]
    code = svc.handle("What is code 36-1-5 on JAZ01865?", as_of=NOW)["troubleshooting"]
    assert [e["code"] for e in code["entries"]] == ["36-1-5"]
    assert code["entries"][0]["evidence"]["row"]["classification"] == "DIRECT"
    assert code["entries"][0]["evidence"]["split"]["classification"] == "DERIVED"
    assert code["entries"][0]["fmi_check"] == "CONSISTENT"


def test_rows_are_parsed_deterministically_and_honestly() -> None:
    e = parse_row("36-1-5 Cylinder #1 Injector Current Below Normal", "Troubleshooting")
    assert (e["code"], e["component"], e["condition"], e["fmi"]) == (
        "36-1-5", "Cylinder #1 Injector", "Current Below Normal", 5)
    assert e["system"] is None                       # not inferred from the number
    odd = parse_row("123-4 Something Unusual Here", "Troubleshooting")
    assert odd["component"] == "Something Unusual Here" and odd["condition"] is None
    sym = parse_row("Low Power", "Symptoms")
    assert sym["kind"] == "symptom" and sym["code"] is None


def test_duplicates_and_conflicts() -> None:
    tr = analyze_troubleshooting({"sections": [{"section": "Troubleshooting", "rows": [
        "36-1-5 Cylinder #1 Injector Current Below Normal",
        "36-1-5 Cylinder #1 Injector Voltage Below Normal"]}]})
    assert tr["duplicate_codes"] == ["36-1-5"] and tr["conflicting_codes"] == ["36-1-5"]


# ── 3D: verified, ambiguous, unavailable ────────────────────────────────────
def test_3d_from_troubleshooting_unavailable_metadata_is_stated_not_guessed() -> None:
    out = _svc(_doc(investigation=_inv())).handle("Check troubleshooting for JAZ01865", as_of=NOW)
    assert out["model_3d"]["status"] == "VISUAL_ONLY"
    assert "component-level mapping is not currently available" in out["text"]
    assert all(m["status"] == "NOT_AVAILABLE" for m in out["model_3d"]["mapping"])


def test_3d_mapping_verified_with_highlight_evidence() -> None:
    names = [{"id": 11, "name": "Injector - Cylinder 1"}, {"id": 12, "name": "Cylinder 2 Injector"}]
    inv = _inv(names, "METADATA_AVAILABLE",
               {"ok": True, "how": "selectionManager.selectNode + view.fitNodes"})
    out = _svc(_doc(investigation=inv)).handle("Check troubleshooting for JAZ01865", as_of=NOW)
    mapping = {m["target"]: m for m in out["model_3d"]["mapping"]}
    assert mapping["Cylinder #1 Injector"]["status"] == "VERIFIED"
    assert mapping["Cylinder #1 Injector"]["component"]["id"] == 11
    assert mapping["Cylinder #1 Injector"]["evidence"]["relationship"]["method"] == \
        "deterministic_component_mapping"
    assert "located and highlighted" in out["text"]


def test_ambiguous_mapping_is_never_resolved_by_guessing() -> None:
    names = [{"id": 1, "name": "Cylinder 1 Injector"}, {"id": 2, "name": "Injector Cylinder 1"}]
    m = map_targets(["Cylinder #1 Injector"], names)[0]
    assert m["status"] == "AMBIGUOUS" and m["message"] == "3D component mapping is ambiguous."
    assert map_targets(["Cylinder #1 Injector"], [])[0]["status"] == "NOT_AVAILABLE"
    assert normalize("Cyl. #1  Injector") == "cylinder 1 injector"


# ── FULL ────────────────────────────────────────────────────────────────────
def test_full_analysis_runs_both_workflows_on_the_same_data() -> None:
    out = _svc(_doc(investigation=_inv())).handle("Full analysis of JAZ01865", as_of=NOW)
    assert out["status"] == "OK"
    assert "MAIA ANALYSIS" in out["text"] and "TROUBLESHOOTING" in out["text"]
    assert out["troubleshooting"]["codes"] and out["result"]["summary"]["part_records"] == 1


def test_the_run_request_reuses_one_session() -> None:
    """A retrieval request is ONE existing lookup with extra sections — never a
    second browser or a second sign-in."""
    from app.models.schemas import EquipmentSearchRequest

    req = EquipmentSearchRequest(serial_number="JAZ01865", investigate=["troubleshooting",
                                                                       "model_3d"],
                                 locate=["Cylinder #1 Injector"])
    assert req.investigate == ["troubleshooting", "model_3d"]
    assert EquipmentSearchRequest(serial_number="JAZ01865").investigate == []  # unchanged default


@pytest.mark.asyncio
async def test_a_failed_investigation_never_fails_the_lookup(make_service, repo) -> None:
    """The fake browser page cannot run the investigator; the lookup must still
    succeed and store what happened."""
    from app.models.schemas import EquipmentSearchRequest

    svc = make_service("ok")
    res = await svc.lookup(EquipmentSearchRequest(serial_number="SN123456",
                                                  investigate=["troubleshooting", "model_3d"]))
    assert res.persisted is True
    run = await repo.get_run(res.automation_run_id)
    assert "INVESTIGATE" in [s["step"] for s in run["steps_executed"]]
