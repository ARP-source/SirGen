"""LiveScene: per-frame model output -> instruments, step, CVS, video events and deviations."""
import json
from pathlib import Path

from backend.phase import PhaseRules
from backend.scene import LiveScene

DATA = Path(__file__).resolve().parents[2] / "data"
STEPS = json.loads((DATA / "procedure/lap_chole_steps.json").read_text())
PHASE = json.loads((DATA / "procedure/phase_rules.json").read_text())
CVS_MODEL = {"cvs": {"loaded": True, "criteria": list(STEPS["cvs_criteria"]), "thresholds": [0.5, 0.5, 0.5]}}


def scene(log=None):
    s = LiveScene(PhaseRules.load(DATA / "procedure/phase_rules.json"), STEPS, PHASE["out_of_phase_events"], log=log)
    s.set_service(CVS_MODEL, "test")
    return s


def res(tools=(), anatomy=(), cvs=None, frame=1):
    return {"frame_id": frame, "ms": 30.0, "tools": [{"cls": t, "conf": 0.9, "box": [0, 0, 1, 1]} for t in tools],
            "anatomy": [{"cls": a, "conf": 0.9, "box": [0, 0, 1, 1]} for a in anatomy], "cvs": cvs}


def feed(s, t0, t1, wall0, **kw):
    """Two results per second from t0 to t1 (scenario clock), wall clock alongside."""
    t = t0
    while t < t1:
        s.add(res(**kw), t, wall0 + (t - t0))
        t += 0.5
    s.step(t1, wall0 + (t1 - t0))
    return wall0 + (t1 - t0)


CVS_NO = {"c1_two_structures": 0.1, "c2_hepatocystic_triangle": 0.1, "c3_cystic_plate": 0.1}
CVS_YES = {"c1_two_structures": 0.9, "c2_hepatocystic_triangle": 0.9, "c3_cystic_plate": 0.9}


def test_no_results_means_unavailable():
    s = scene()
    assert s.current_tools(100.0) is None
    assert s.view(100.0).status == "not running"
    s.step(3.0, 100.0)
    assert s.phase_state(100.0).status.startswith("waiting") or "stale" in s.phase_state(100.0).status


def test_live_tools_and_staleness():
    s = scene()
    s.add(res(tools=("Hook", "Grasper")), 0.2, 10.0)
    assert s.current_tools(10.1) == {"Hook", "Grasper"}
    assert s.view(10.1).status == "live"
    assert s.current_tools(13.0) is None and s.view(13.0).status == "stale"


def test_tool_in_event_and_step_change():
    s = scene()
    w = feed(s, 0, 20, 0.0, tools=("Grasper", "Hook"), cvs=CVS_NO)
    kinds = [(e.kind, e.text) for e in s.events]
    assert ("tool_in", "Hook in view") in kinds
    assert s.phase_state(w).name == "CalotTriangleDissection"
    assert any(e.kind == "step" and "calot" in e.text.lower() for e in s.events)
    v = s.view(w)
    assert v.step.next_name == "ClippingCutting" and v.step.goal


def test_clip_before_cvs_is_a_deviation_and_logged():
    logged = []
    s = scene(log=lambda kind, summary, data, actor: logged.append((kind, summary)))
    w = feed(s, 0, 25, 0.0, tools=("Grasper", "Hook"), anatomy=("cystic_duct",), cvs=CVS_NO)
    w = feed(s, 25, 30, w, tools=("Grasper", "Clipper"), anatomy=("cystic_duct",), cvs=CVS_NO)
    assert len(s.deviations) == 1
    d = s.deviations[0]
    assert d.kind == "deviation" and "Clipper" in d.text and d.data["deviation"] == "clip_before_cvs"
    assert ("deviation", d.text) in logged
    assert s.view(w).deviations[0].source_id == d.source_id


def test_clip_after_cvs_is_not_a_deviation():
    s = scene()
    w = feed(s, 0, 20, 0.0, tools=("Grasper", "Hook"), cvs=CVS_NO)
    w = feed(s, 20, 30, w, tools=("Grasper", "Hook"), cvs=CVS_YES)
    assert s.cvs_seen_at is not None and s.view(w).cvs_achieved
    feed(s, 30, 35, w, tools=("Grasper", "Clipper"), cvs=CVS_YES)
    assert s.deviations == []
    assert any(e.kind == "first_cut" and "was seen on video" in e.text for e in s.events)


def test_no_dissection_seen_means_no_judgement():
    s = scene()
    feed(s, 0, 6, 0.0, tools=("Grasper", "Clipper"), cvs=CVS_NO)
    assert s.deviations == []
    assert any(e.kind == "first_cut" and "not observed" in e.text for e in s.events)


def test_reset_clears_everything():
    s = scene()
    feed(s, 0, 25, 0.0, tools=("Grasper", "Hook"), anatomy=("cystic_duct",), cvs=CVS_NO)
    s.reset()
    assert s.events == [] and s.deviations == [] and s.cvs_seen_at is None
