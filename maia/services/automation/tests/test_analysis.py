"""Maia's deterministic analysis engine. No model, no network, no browser."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.analysis.config import load_config
from app.analysis.intent import route
from app.analysis.sample import write_sample_store
from app.analysis.service import AnalysisService
from app.analysis.snapshots import InMemorySnapshots, LocalStoreSnapshots

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
COLS = ["Part Number", "Serial Number", "Part Name", "Install Ind.", "Install Date",
        "Description", "Quantity"]


def _row(pn, name="Part", desc="DESC", qty="1"):
    cells = [pn, "", name, "Factory", "", desc, qty]
    return {"cells": cells, "values": dict(zip(COLS, cells))}


def _doc(serial="JAZ01865", run="run_A", days_ago=1, groups=None, **fields):
    groups = groups if groups is not None else [
        {"title": f"Product - Entire Group ({serial})", "group_serial": serial,
         "is_entire_group": True, "columns": COLS,
         "rows": [_row("111-1111"), _row("222-2222"), _row("111-1111", qty="")]},
        {"title": "Engine - Entire Group (PRH04588)", "group_serial": "PRH04588",
         "columns": COLS, "rows": [_row("333-3333", desc=""), _row("222-2222")]},
    ]
    record = {"serial_number": serial, "source_system": "cat_sis", "status": "ACTIVE",
              "retrieved_at": (NOW - timedelta(days=days_ago)).isoformat(),
              "automation_run_id": run, "machine_serial_number": serial,
              "machine_build_date": "2014-08-02", "engine_serial_number": "PRH04588",
              "engine_build_date": "2014-06-30", "equipment_model": "C32",
              "parts_data": {"groups": groups, "entire_group_title": groups[0]["title"]
                             if groups else None, "serial_mismatched_groups": []}}
    record.update(fields)
    return {"store_format": "maia.local-json/1", "run_id": run, "source": "Caterpillar SIS",
            "record": record}


def _svc(*docs, cfg=None):
    return AnalysisService(InMemorySnapshots([(f"{d['record']['serial_number']}_{d['run_id']}"
                                               ".json", d) for d in docs]), cfg=cfg)


# ── statistics ──────────────────────────────────────────────────────────────
def test_parts_statistics_are_exact() -> None:
    r = _svc(_doc()).analyze("JAZ01865", as_of=NOW)
    s = r["summary"]
    assert (s["part_records"], s["unique_part_numbers"], s["part_groups"]) == (5, 3, 2)
    assert r["parts_analysis"]["duplicate_part_numbers"]["value"] == {"111-1111": 2,
                                                                     "222-2222": 2}
    assert r["parts_analysis"]["repeated_across_groups"]["value"] == {
        "222-2222": ["Engine - Entire Group", "Entire Group"]}
    assert s["missing_quantities"] == 1 and s["missing_descriptions"] == 1
    assert r["equipment_analysis"]["age"]["value"] == 12.2
    assert s["completeness_pct"] == 62.5                       # 5 of 8 expected fields


def test_missing_critical_field_is_a_direct_finding() -> None:
    r = _svc(_doc(engine_serial_number=None)).analyze("JAZ01865", as_of=NOW)
    hit = [i for i in r["insights"] if i["rule"] == "missing_critical_field"]
    assert hit and hit[0]["insight"] == "Engine serial number is missing from the retrieved SIS data."
    assert hit[0]["classification"] == "DIRECT"


def test_quantity_column_not_published_is_not_counted_as_gaps() -> None:
    cols = COLS[:-1]
    groups = [{"title": "Product - Entire Group (JAZ01865)", "columns": cols,
               "rows": [{"cells": ["111-1111", "", "P", "F", "", "D"],
                         "values": dict(zip(cols, ["111-1111", "", "P", "F", "", "D"]))}]}]
    r = _svc(_doc(groups=groups)).analyze("JAZ01865", as_of=NOW)
    assert r["summary"]["missing_quantities"] is None
    assert any(i["rule"] == "quantity_column_absent" for i in r["insights"])
    assert not any(i["rule"] == "missing_quantity" for i in r["insights"])


def test_group_anomalies_use_explainable_statistics() -> None:
    big = [_row(f"{n:03d}-0001") for n in range(40)]
    groups = [{"title": f"G{k} - Entire Group (JAZ01865)", "columns": COLS,
               "rows": [_row(f"9{k}0-000{j}") for j in range(3)]} for k in range(5)]
    groups.append({"title": "Big - Entire Group (JAZ01865)", "columns": COLS, "rows": big})
    r = _svc(_doc(groups=groups)).analyze("JAZ01865", as_of=NOW)
    large = r["anomalies"]["large_groups"]
    assert [g["group"] for g in large] == ["Big - Entire Group"]
    assert r["anomalies"]["method"]["rule"].startswith("size > Q3")
    text = next(i["insight"] for i in r["insights"] if i["rule"] == "large_group")
    assert "significantly more records than the median group" in text


def test_stale_data_rule() -> None:
    r = _svc(_doc(days_ago=200)).analyze("JAZ01865", as_of=NOW)
    assert r["summary"]["freshness_status"] == "STALE"
    assert any(i["rule"] == "stale_data" for i in r["insights"])


def test_quality_score_is_transparent_and_refused_without_data() -> None:
    r = _svc(_doc()).analyze("JAZ01865", as_of=NOW)
    q = r["data_quality"]
    assert q["status"] == "OK" and 0 < q["score_pct"] <= 100
    assert set(q["components"]) == {"completeness", "parts_completeness", "validity",
                                    "consistency", "freshness"}
    assert q["reasons"] and "Σ(weight × component)" in q["calculation"]
    empty = _doc(groups=[], machine_serial_number=None, machine_build_date=None,
                 engine_serial_number=None, engine_build_date=None, equipment_model=None,
                 retrieved_at=None)
    q2 = _svc(empty).analyze("JAZ01865", as_of=NOW)["data_quality"]
    assert q2["score_pct"] is None
    assert q2["message"] == "Insufficient data to calculate quality score."


def test_weights_come_from_config() -> None:
    cfg = load_config(overrides={"quality": {"weights": {"completeness": 1.0,
                                                         "parts_completeness": 0,
                                                         "validity": 0, "consistency": 0,
                                                         "freshness": 0},
                                             "min_components": 1}})
    q = _svc(_doc(), cfg=cfg).analyze("JAZ01865", as_of=NOW)["data_quality"]
    assert q["score_pct"] == 62.5                          # completeness alone


# ── history and comparison ──────────────────────────────────────────────────
def test_history_names_both_snapshots_and_the_changes() -> None:
    old = _doc(run="run_OLD", days_ago=10)
    new = _doc(run="run_NEW", days_ago=1, equipment_model="C32B")
    new["record"]["parts_data"]["groups"][1]["rows"].append(_row("444-4444"))
    new["record"]["parts_data"]["groups"][0]["rows"][1] = _row("222-2222", qty="4")
    c = _svc(old, new).analyze("JAZ01865", as_of=NOW)["changes"]
    assert c["previous"]["run_id"] == "run_OLD" and c["latest"]["run_id"] == "run_NEW"
    assert c["parts_added"] == [{"group": "Engine - Entire Group", "part_number": "444-4444"}]
    assert c["quantity_changes"] == [{"group": "Entire Group", "part_number": "222-2222",
                                      "previous": "1", "latest": "4"}]
    assert [f["field"] for f in c["fields_changed"]] == ["equipment_model"]


def test_equipment_comparison() -> None:
    a = _doc()
    b = _doc(serial="JAZ01866", run="run_B", engine_serial_number="PRH09999",
             groups=[{"title": "Product - Entire Group (JAZ01866)", "columns": COLS,
                      "rows": [_row("111-1111"), _row("555-5555")]}])
    out = _svc(a, b).handle("compare JAZ01865 and JAZ01866", as_of=NOW)
    c = out["comparison"]
    assert out["status"] == "OK"
    status = {x["attribute"]: x["status"] for x in c["attributes"]}
    assert status["equipment_model"] == "IDENTICAL"
    assert status["engine_serial_number"] == "DIFFERENT"
    assert c["parts"]["common"] == ["111-1111"]
    assert c["parts"]["only_a"] == ["222-2222", "333-3333"] and c["parts"]["only_b"] == ["555-5555"]
    assert "Both machines share 1 part number(s)" in out["text"]


# ── robustness ──────────────────────────────────────────────────────────────
def test_invalid_and_unknown_serials_are_never_substituted() -> None:
    svc = _svc(_doc())
    assert svc.analyze("J!", as_of=NOW)["status"] == "INVALID_SERIAL"
    out = svc.analyze("JAZ01856", as_of=NOW)
    assert out["status"] == "NO_DATA" and out["candidates"][0]["serial"] == "JAZ01865"
    assert "not substituted" in out["message"]


def test_corrupted_json_is_reported_not_guessed(tmp_path: Path) -> None:
    (tmp_path / "JAZ01865_run_BAD.json").write_text("{not json", encoding="utf-8")
    r = AnalysisService(LocalStoreSnapshots(tmp_path)).analyze("JAZ01865", as_of=NOW)
    assert r["status"] == "DATA_CORRUPTED" and "JAZ01865_run_BAD.json" in r["issues"]


def test_empty_sis_result_still_answers_honestly() -> None:
    r = _svc(_doc(groups=[])).analyze("JAZ01865", as_of=NOW)
    assert r["status"] == "OK" and r["summary"]["part_records"] == 0


def test_samples_and_fixture_results_are_never_real_sis(tmp_path: Path) -> None:
    write_sample_store(tmp_path, NOW)
    assert LocalStoreSnapshots(tmp_path).serials() == []           # refused by default
    fixture = _doc()
    fixture["record"]["source_system"] = "local_fixture"
    assert InMemorySnapshots([("JAZ01865_run_F.json", fixture)]).serials() == []


def test_same_input_same_output(tmp_path: Path) -> None:
    write_sample_store(tmp_path, NOW)
    svc = AnalysisService(LocalStoreSnapshots(tmp_path, include_samples=True))
    one = json.dumps(svc.analyze("JAZ01865", as_of=NOW), sort_keys=True, default=str)
    two = json.dumps(svc.analyze("JAZ01865", as_of=NOW), sort_keys=True, default=str)
    assert one == two


# ── evidence and wording ────────────────────────────────────────────────────
def test_every_insight_has_evidence_and_derived_values_are_never_direct() -> None:
    r = _svc(_doc(run="run_1", days_ago=5), _doc(run="run_2")).analyze("JAZ01865", as_of=NOW)
    assert r["insights"]
    for i in r["insights"]:
        assert i["classification"] in ("DIRECT", "DERIVED")
        assert i["evidence"]["source"] == "SIS" and i["evidence"]["calculation"]
        assert i["evidence"].get("run_id") or i["evidence"].get("snapshots")
    for d in r["classification"]["derived"]:
        assert d["calculation"]
    assert all(v["classification"] == "DIRECT" for v in r["equipment"].values())
    assert r["parts_analysis"]["total_records"]["classification"] == "DERIVED"


def test_no_mechanical_conclusions_in_any_wording(tmp_path: Path) -> None:
    write_sample_store(tmp_path, NOW)
    svc = AnalysisService(LocalStoreSnapshots(tmp_path, include_samples=True))
    text = svc.handle("Analyze JAZ01865", as_of=NOW)["text"].lower()
    for banned in ("failing", "failure", "broken", "unsafe", "maintenance required",
                   "worn", "defect", "malfunction"):
        assert banned not in text


# ── routing, English and Arabic ─────────────────────────────────────────────
@pytest.mark.parametrize("text,intent", [
    ("Analyze JAZ01865", "ANALYZE_EQUIPMENT"), ("show parts", "SHOW_PARTS"),
    ("show missing data", "MISSING_DATA"), ("what changed", "WHAT_CHANGED"),
    ("compare JAZ01865 and JAZ01866", "COMPARE_EQUIPMENT"), ("data quality", "DATA_QUALITY"),
    ("show anomalies", "SHOW_ANOMALIES"), ("give me summary", "SUMMARY"),
    ("show duplicate parts", "DUPLICATE_PARTS"),
    ("حلل المعدة JAZ01865", "ANALYZE_EQUIPMENT"), ("ايه القطع الناقصة؟", "MISSING_DATA"),
    ("قارن JAZ01865 و JAZ01866", "COMPARE_EQUIPMENT"),
    ("get equipment JAZ01865", "NONE"), ("Find a part", "NONE"), ("thanks", "NONE"),
])
def test_intent_routing(text: str, intent: str) -> None:
    assert route(text, "JAZ01865").intent == intent


def test_follow_up_uses_the_machine_in_context() -> None:
    r = route("what changed", "JAZ01865")
    assert r.serials == ["JAZ01865"] and r.serial_source == "context"


def test_sample_demo_report_reads_like_a_report(tmp_path: Path) -> None:
    write_sample_store(tmp_path, NOW)
    out = AnalysisService(LocalStoreSnapshots(tmp_path, include_samples=True)).handle(
        "Analyze JAZ01865", as_of=NOW)
    for section in ("MAIA ANALYSIS", "Data freshness", "Data completeness", "Key findings",
                    "Anomalies", "Historical changes", "Evidence", "SIS coverage"):
        assert section in out["text"]
    assert "{" not in out["text"]                                  # no raw JSON
