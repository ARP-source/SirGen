"""Surgery log and summary: built only from log entries, every row traceable, nothing inferred."""
import asyncio
import json

from backend import report as R
from backend.surgery_log import build_surgery_log

from .conftest import DATA
from .test_report import PATIENT, PROMPT, good_handler, narrative_client, ran

STEPS = json.loads((DATA / "procedure" / "lap_chole_steps.json").read_text())


def entry(seq, log_kind, clock_s, summary="", actor="system", **data):
    return {"seq": seq, "id": f"log-{seq}", "run": 1, "kind": log_kind, "clock_s": clock_s, "actor": actor,
            "summary": summary, "data": data}


def phase(seq, t, name, display, set_by="tracker"):
    return entry(seq, "phase_change", t, f"procedure step: {display}", name=name, display=display, set_by=set_by)


def video(seq, t, kind, summary, **data):
    return entry(seq, "video_event", t + 3, summary, actor="vision", kind=kind, t_s=t, **data)


def sample_run():
    return [
        entry(1, "run_start", 0.0, "run"),
        phase(2, 0.0, "Preparation", "Preparation"),
        video(3, 2.0, "tool_in", "Grasper in view", tool="Grasper"),
        phase(4, 30.0, "CalotTriangleDissection", "Calot triangle dissection"),
        video(5, 31.0, "tool_in", "Hook in view", tool="Hook"),
        video(6, 50.0, "cvs", "CVS criterion seen on video: two structures", criterion="c1"),
        entry(7, "alert", 60.0, "alert", event_id="ev-1", event_type="hypotension"),
        entry(8, "deliberation_result", 64.0, "result", event_id="ev-1", outcome="converged"),
        entry(9, "deviation", 92.0, "Clipper in view before the Critical View of Safety was seen on video (1 of 3 criteria seen)",
              actor="vision", t_s=90.0, deviation="clip_before_cvs", why_it_matters="bile duct injury risk"),
        phase(10, 95.0, "ClippingCutting", "Clipping and cutting"),
        entry(11, "drug_given", 100.0, "phenylephrine", drug="phenylephrine"),
        entry(12, "segment_end", 120.0, "end"),
    ]


def test_steps_have_start_end_duration_and_their_own_events():
    s = build_surgery_log(sample_run(), STEPS)
    assert [x.display for x in s.steps] == ["Preparation", "Calot triangle dissection", "Clipping and cutting"]
    prep, calot, clip = s.steps
    assert (prep.start_s, prep.end_s, prep.duration_s) == (0.0, 30.0, 30.0)
    assert clip.end_s == 120.0 and s.length_s == 120.0
    assert prep.instruments == ["Grasper"] and calot.instruments == ["Hook"]
    assert calot.deviations == 1 and calot.alerts == 1 and clip.deviations == 0


def test_summary_counts_and_cvs_and_first_cut():
    s = build_surgery_log(sample_run(), STEPS)
    assert s.cvs["seen_at_s"] is None and s.cvs["criteria"] == ["c1"]
    assert len(s.deviations) == 1 and s.deviations[0]["clock_s"] == 90.0
    assert s.deviations[0]["step"] == "Calot triangle dissection"
    assert s.first_cut and s.first_cut["log_id"] == "log-9"
    assert s.alerts[0]["outcome"] == "converged" and s.drugs == 1
    assert "Gallbladder dissection" in s.not_observed and s.out_of_usual_order == []


def test_log_rows_are_in_time_order_skip_instrument_noise_and_flag_deviations():
    s = build_surgery_log(sample_run(), STEPS)
    times = [r.clock_s for r in s.rows]
    assert times == sorted(times)
    assert not any("in view" in r.what and r.kind == "video: tool in" for r in s.rows)
    dev = next(r for r in s.rows if r.flag == "deviation")
    assert dev.log_id == "log-9" and dev.step == "Calot triangle dissection"
    assert all(r.log_id.startswith("log-") for r in s.rows)


def test_out_of_usual_order_is_reported_without_calling_it_a_mistake():
    entries = [phase(1, 0.0, "CalotTriangleDissection", "Calot triangle dissection"),
               phase(2, 10.0, "Preparation", "Preparation"), entry(3, "segment_end", 20.0)]
    s = build_surgery_log(entries, STEPS)
    assert s.out_of_usual_order == ["Preparation"]
    assert s.deviations == []


def test_empty_run_says_nothing_was_logged():
    s = build_surgery_log([entry(1, "run_start", 0.0)], STEPS)
    assert s.steps == [] and s.rows == [] and s.cvs["seen_at_s"] is None


def test_report_has_surgery_summary_and_log(scenarios, tmp_path):
    async def go():
        st, audit = await ran(scenarios, tmp_path)
        rep = await R.generate(audit, narrative_client(good_handler), PATIENT, PROMPT)
        h = rep.html_text
        assert "Surgery summary (computed from the log)" in h and "Surgery log (step by step)" in h
        assert rep.surgery is not None and rep.summary()["deviations"] == 0
        assert "No deviation from the expected procedure" in h
    asyncio.run(go())
