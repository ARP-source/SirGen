"""Aftercare and insurance-coding sections: built by code from the chart, the log and fixed reference files."""
import json
from pathlib import Path

from backend.aftercare import build_aftercare, build_coding

DATA = Path(__file__).resolve().parents[2] / "data"
PATIENT = json.loads((DATA / "patient/patient_record.json").read_text())
AC = json.loads((DATA / "procedure/lap_chole_aftercare.json").read_text())
CMAP = json.loads((DATA / "billing/coding_map.json").read_text())


def test_every_chart_medicine_gets_a_decision_for_the_team_never_a_dose():
    ac = build_aftercare(PATIENT, AC, [])
    by = {m.drug: m for m in ac.meds}
    assert set(by) == {m["drug"] for m in PATIENT["medications"]}
    assert by["apixaban"].group == "anticoagulant" and "surgical team to decide" in by["apixaban"].decision
    assert by["losartan"].group == "antihypertensive" and by["piperacillin-tazobactam"].group == "antibiotic"
    for m in ac.meds:
        assert "decide" in m.decision and not any(ch.isdigit() for ch in m.decision)
    assert ac.allergies and ac.allergies[0]["source_id"] == "alg-01"


def test_deviations_and_bleeding_concern_are_listed_for_review_with_their_log_ids():
    entries = [
        {"id": "log-5", "kind": "deviation", "clock_s": 30.0, "summary": "Clipper in view before the CVS was seen",
         "data": {"why_it_matters": "w", "note": "n"}},
        {"id": "log-6", "kind": "video_event", "clock_s": 31.0, "summary": "Hook in view", "data": {"kind": "tool_in"}},
        {"id": "log-9", "kind": "deliberation_result", "clock_s": 70.0, "summary": "x",
         "data": {"shared_category": "bleeding_concern", "summary": "all three"}},
    ]
    ids = [x["log_id"] for x in build_aftercare(PATIENT, AC, entries).review]
    assert ids == ["log-5", "log-9"]


def test_coding_draft_from_chart():
    cd = build_coding(PATIENT, CMAP)
    codes = {c.code: c for c in cd.rows}
    assert "47562" in codes and codes["47562"].from_ids == ["enc-01"]
    assert "00790-P3" in codes and "enc-03" in codes["00790-P3"].from_ids
    assert "K80.00" in codes and "hx-04" in codes["K80.00"].from_ids  # acute cholecystitis + gallstones on the chart
    assert "K81.0" not in codes and "K80.20" not in codes  # one gallbladder code, from the encounter diagnosis
    assert {"I48.0", "I10", "E78.5", "Z87.891", "Z79.01"} <= set(codes)
    assert cd.times["surgery_min"] == 15.0 and cd.times["source_id"] == "enc-06"
    assert "payer and member id" in cd.missing


def test_coding_with_nothing_on_the_chart_says_what_is_missing():
    cd = build_coding({"encounter": [], "history": [], "medications": []}, CMAP)
    assert cd.rows == [] and {"procedure performed", "primary diagnosis"} <= set(cd.missing)
