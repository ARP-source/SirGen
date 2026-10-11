"""Evidence blocks: each agent gets only its own evidence, built from the loaded data (prompts/README.md)."""
import json
import re
from pathlib import Path

from backend.agents.evidence import EvidenceContext, build_user_message, recovery_s
from backend.scenario import load_all
from backend.state import Event, PhaseState

from .scripted_video import ScriptedScene

DATA = Path(__file__).resolve().parents[2] / "data"
PATIENT = json.loads((DATA / "patient/patient_record.json").read_text())
PHASE = json.loads((DATA / "procedure/phase_rules.json").read_text())


def ctx_for(scenarios, sid, now, video=True, **kw):
    sc = scenarios[sid]
    ev = Event(event_id="ev-1", event_type=sc.event_type, event_timestamp=sc.event_t_s, trigger="scenario", simulated=True)
    if video:
        scene = ScriptedScene(sc, PHASE)
        kw.setdefault("phase_state", scene.phase_state(now))
        kw.setdefault("scene", scene.view())
        kw.setdefault("video_events", scene.visible_events(now))
    return EvidenceContext(sc, ev, now, PATIENT, PHASE, **kw)


def test_each_agent_sees_only_its_own_ids(scenarios):
    ctx = ctx_for(scenarios, "true_bleed", 60)
    surg, anes, rec = ctx.surgical(), ctx.anesthesia(), ctx.record()
    assert set(surg.allowed_ids) == {"phase", "tools", "surg-01", "surg-02", "surg-03", "oop-1"}
    assert set(anes.allowed_ids) == {"vit-art", "vit-nibp-01", "drug-01", "drug-02", "drug-03"}
    assert not set(rec.allowed_ids) & (set(surg.allowed_ids) | set(anes.allowed_ids))
    text = {a: json.dumps(e.block) for a, e in (("s", surg), ("a", anes), ("r", rec))}
    assert "propofol" not in text["s"] and "apixaban" not in text["s"] and "Irrigator" not in text["r"]
    assert "apixaban" not in text["a"] and "Scissors" not in text["a"]
    assert "surg-" not in text["a"] and "surg-" not in text["r"] and "drug-0" not in text["r"]
    assert "meds-" not in text["s"] and "meds-" not in text["a"] and "vit-" not in text["r"]


def test_future_entries_are_not_shown(scenarios):
    surg = ctx_for(scenarios, "true_bleed", 60).surgical()
    assert "surg-04" not in surg.allowed_ids  # bipolar appears at t 95, after the alert (README mismatch 1)
    later = ctx_for(scenarios, "true_bleed", 100).surgical()
    assert "surg-04" in later.allowed_ids and "oop-2" in later.allowed_ids
    art = ctx_for(scenarios, "artifact", 50).anesthesia()
    assert "vit-nibp-02" not in art.allowed_ids  # cuff reading at t 75 does not exist yet
    assert "vit-nibp-02" in ctx_for(scenarios, "artifact", 76).anesthesia().allowed_ids


def test_times_are_relative_to_the_alert_and_null_times_stay_null(scenarios):
    block = ctx_for(scenarios, "drug_effect", 30).anesthesia().block
    drugs = {d["source_id"]: d["rel_alert_s"] for d in block["drug_log"]}
    assert drugs == {"drug-01": None, "drug-02": None, "drug-03": -90}
    assert block["vitals"]["summary"]["map_60s_before"] == 71 and block["vitals"]["summary"]["map_now"] == 61
    assert block["vitals"]["summary"]["baseline_rel_alert_s"] == -30  # segment starts 30 s before the alert
    assert block["cuff"] == []  # vit-nibp-01 has no time or value yet: not an allowed id


def test_vitals_sampling_density_and_summary(scenarios):
    b = ctx_for(scenarios, "artifact", 76).anesthesia().block["vitals"]
    rels = [s["rel_alert_s"] for s in b["samples"]]
    assert rels == sorted(rels) and rels[0] == -50 and rels[-1] == 26
    assert {0, 2, 4, 6, 8, 10}.issubset(rels)  # every 2 s around the spike
    assert b["summary"]["sbp_max"] == 277 and b["summary"]["sbp_back_near_baseline_after_s"] == 9
    assert b["summary"]["hr_now"] == 71 and b["summary"]["spo2_now"] == 98


def test_recovery_s(scenarios):
    sbp = scenarios["artifact"].series.values["sbp"]
    assert recovery_s(sbp, 50, 54) is None  # still up
    assert recovery_s(sbp, 50, 60) == 9


def test_record_whole_chart_drops_null_values_and_subset_mode(scenarios):
    full = ctx_for(scenarios, "true_bleed", 60).record()
    ids = set(full.allowed_ids)
    assert {"lab-03", "lab-04", "lab-05"}.isdisjoint(ids) and {"meds-01", "lab-06", "note-02", "demo-01"} <= ids
    assert full.block["chart"][0]["source_id"] == "demo-01"  # copied as is, file order
    sub = ctx_for(scenarios, "true_bleed", 60, record_mode="subset").record()
    assert sub.allowed_ids == ["meds-01", "hx-01", "lab-01", "lab-06", "lab-07", "note-01"]


def test_phase_from_state_overrides_scenario_data(scenarios):
    ps = PhaseState(name="CleaningCoagulation", display="Cleaning and coagulation", since_s=40.0,
                    tools_window={"Irrigator": 8, "Bipolar": 4}, status="set by presenter", set_by="presenter")
    surg = ctx_for(scenarios, "true_bleed", 60, phase_state=ps).surgical()
    assert surg.block["phase"]["display"] == "Cleaning and coagulation" and surg.block["phase"]["set_by"] == "presenter"
    assert surg.block["phase"]["since_rel_alert_s"] == -20
    assert surg.block["tools_window"]["seconds_present"] == {"Irrigator": 8, "Bipolar": 4}
    assert surg.block["tools_window"]["basis"] == "live instrument detector"


def test_no_video_models_means_no_surgical_evidence_and_omni_is_labelled(scenarios):
    surg = ctx_for(scenarios, "true_bleed", 60, video=False).surgical()
    assert surg.allowed_ids == [] and surg.block["detector"]["status"] == "not running"
    assert surg.block["surgical_events"] == [] and "tools_window" not in surg.block and "phase" not in surg.block
    assert "surg-" not in json.dumps(surg.block)  # the scenario file's events are never read by product code
    assert ctx_for(scenarios, "true_bleed", 60).surgical().block["omni"] is None
    withomni = ctx_for(scenarios, "true_bleed", 60, omni={"source_id": "omni-ev-1", "answer": "No blood is visible."}).surgical()
    assert withomni.block["omni"] == {"source_id": "omni-ev-1", "answer": "No blood is visible."}
    assert "omni-ev-1" in withomni.allowed_ids
    assert any("Video model (Omni)" in s["text"] for s in withomni.sees)


def test_user_message_blocks_and_order(scenarios):
    ctx = ctx_for(scenarios, "true_bleed", 60)
    ev = ctx.anesthesia()
    r1 = build_user_message(1, ctx.event, ctx.trigger_text, ev)
    assert [l.split(":")[0] for l in r1.splitlines()] == ["ROUND", "EVENT", "EVIDENCE", "ALLOWED_SOURCE_IDS"]
    r2 = build_user_message(2, ctx.event, ctx.trigger_text, ev, [{"agent": "record", "status": "unavailable"}])
    assert [l.split(":")[0] for l in r2.splitlines()][-1] == "OTHER_AGENTS"
    event_line = json.loads(r1.splitlines()[1].split(": ", 1)[1])
    assert event_line == {"event_id": "ev-1", "event_type": "map_drop", "trigger": scenarios["true_bleed"].event_note}
    assert json.loads(r1.splitlines()[3].split(": ", 1)[1]) == ev.allowed_ids
    assert not re.search(r"\n\s", r1)  # compact JSON, one line per block


def test_round_two_rule_is_picked_from_categories_only():
    from backend.agents.evidence import round_two_rule
    o = lambda a, c: {"agent": a, "category": c}
    assert "Keep bleeding_concern" in round_two_rule("surgical", "bleeding_concern", [o("anesthesia", "drug_effect"), o("record", "unclear")])
    assert "Adopt artifact at low unless" in round_two_rule("surgical", "unclear", [o("anesthesia", "artifact"), o("record", "unclear")])
    assert "Stay unclear" in round_two_rule("anesthesia", "unclear", [o("surgical", "unclear"), {"agent": "record", "status": "unavailable"}])
    assert "different categories" in round_two_rule("record", "unclear", [o("surgical", "bleeding_concern"), o("anesthesia", "drug_effect")])
    assert "Adopt drug_effect at low if at least one chart entry" in round_two_rule("record", "bleeding_concern", [o("surgical", "unclear"), o("anesthesia", "drug_effect")])
    assert "Keep bleeding_concern" in round_two_rule("record", "bleeding_concern", [o("surgical", "bleeding_concern"), o("anesthesia", "drug_effect")])
    assert "chart entry supports it" in round_two_rule("record", "unclear", [o("surgical", "unclear"), o("anesthesia", "artifact")])
