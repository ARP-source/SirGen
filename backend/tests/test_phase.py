import json

import pytest

from backend.phase import PhaseRules, PhaseTracker


@pytest.fixture()
def rules(data_dir):
    return PhaseRules.load(data_dir / "procedure" / "phase_rules.json")


def feed(tracker, segments, t0=0):
    """segments: list of (seconds, tools). Returns (final t, list of (t, phase))."""
    t, history = t0, []
    for seconds, tools in segments:
        for _ in range(seconds):
            u = tracker.update(tools, t)
            history.append((t, u.name))
            t += 1
    return t, history


def test_preparation_to_calot_respects_dwell_and_confirm(rules):
    tr = PhaseTracker(rules)
    _, hist = feed(tr, [(30, {"Grasper", "Hook"})])
    phases = dict(hist)
    assert phases[14] == "Preparation"
    assert phases[15] == "CalotTriangleDissection"


def test_full_canonical_sequence(rules):
    tr = PhaseTracker(rules)
    t, hist = feed(tr, [
        (20, {"Grasper"}),
        (40, {"Grasper", "Hook"}),
        (20, {"Grasper", "Clipper"}),
        (10, {"Grasper", "Scissors"}),
        (40, {"Grasper", "Hook"}),
        (30, {"Grasper", "SpecimenBag"}),
    ])
    seq = [p for i, (_, p) in enumerate(hist) if i == 0 or p != hist[i - 1][1]]
    assert seq == ["Preparation", "CalotTriangleDissection", "ClippingCutting",
                   "GallbladderDissection", "GallbladderPackaging"]


def test_clipping_without_scissors_backtracks_to_calot(rules):
    tr = PhaseTracker(rules)
    tr.force("CalotTriangleDissection", 0)
    feed(tr, [(20, {"Grasper", "Clipper"}), (40, {"Grasper", "Hook"})])
    assert tr.phase == "CalotTriangleDissection"


def test_single_blip_does_not_flicker(rules):
    tr = PhaseTracker(rules)
    tr.force("CalotTriangleDissection", 0)
    feed(tr, [(30, {"Hook"}), (2, {"Hook", "Clipper"}), (30, {"Hook"})])
    assert tr.phase == "CalotTriangleDissection"


def test_detector_unavailable_holds_and_marks_stale(rules):
    tr = PhaseTracker(rules)
    tr.force("ClippingCutting", 0)
    u = tr.update(None, 50)
    assert u.stale and u.name == "ClippingCutting"


def test_out_of_phase_irrigator(rules):
    tr = PhaseTracker(rules)
    tr.force("ClippingCutting", 0)
    _, _ = feed(tr, [(5, {"Grasper", "Irrigator"})])
    u = tr.update({"Grasper", "Irrigator"}, 5)
    assert "oop-1" in u.out_of_phase and u.name == "ClippingCutting"
    assert u.tools_window["Irrigator"] == 6


def test_presenter_force_label(rules):
    tr = PhaseTracker(rules)
    tr.force("GallbladderDissection", 3)
    assert tr.update({"Hook"}, 4).set_by == "presenter"


def test_changed_rule_text_fails_loudly(tmp_path, data_dir):
    raw = json.loads((data_dir / "procedure" / "phase_rules.json").read_text())
    raw["rules"][0]["when"] = "f(Hook) >= 0.7"
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        PhaseRules.load(p)
