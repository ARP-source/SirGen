"""Speak-or-hold gate: hold vs open, settle, urgent never held, surgeon-asks, timelines."""
from __future__ import annotations

from backend.gate import DETECTOR_UNAVAILABLE, SETTLE_S, SpeechGate, hold_instrument, tools_at
from backend.scenario import load_all


def test_hold_instrument_priority():
    assert hold_instrument(["Grasper", "Irrigator"]) is None
    assert hold_instrument([]) is None
    assert hold_instrument(["Grasper", "Clipper"]) == "Clipper"
    assert hold_instrument(["Hook", "Bipolar"]) == "Bipolar"


def test_hold_then_open_after_settle():
    g = SpeechGate()
    g.on_clock(0.0, ["Grasper", "Hook"], "scenario timeline")
    item = g.offer("team question", kind="team_question")
    assert g.status == "hold"
    assert g.view().chip == "Holding: dissection"
    assert len(g.speech_view().queued) == 1
    assert g.speech_view().current is None
    g.on_clock(10.0, ["Grasper"], "scenario timeline")  # withdrawn, not yet settled
    assert g.status == "hold"
    assert g.view().settling is True
    assert len(g.speech_view().queued) == 1
    g.on_clock(10.0 + SETTLE_S, ["Grasper"], "scenario timeline")
    assert g.status == "open"
    assert g.view().chip == "Clear to speak"
    assert g.speech_view().queued == []
    spoken = g.speech_view().current
    assert spoken is not None and spoken.id == item.id
    assert spoken.held_s == 10.0 + SETTLE_S
    assert spoken.release_reason == "instruments withdrawn"


def test_urgent_never_held():
    kinds: list[str] = []
    g = SpeechGate(log=lambda kind, summary, data=None, actor="system": kinds.append(kind))
    g.on_clock(0.0, ["Bipolar"], "scenario timeline", map_=70.0, spo2=98.0)
    g.offer("please wait", kind="team_question")
    assert len(g.speech_view().queued) == 1
    g.on_clock(1.0, ["Bipolar"], "scenario timeline", map_=52.0, spo2=98.0)
    assert "speech_urgent" in kinds
    assert "speech_spoken" in kinds
    cur = g.speech_view().current
    assert cur is not None and cur.kind == "urgent_vitals" and cur.urgent is True
    assert cur.held_s == 0.0
    assert "please wait" in [q.text for q in g.speech_view().queued]


def test_urgent_fires_once_per_excursion():
    g = SpeechGate()
    g.on_clock(0.0, ["Hook"], "t", map_=52.0)
    first = g.speech_view().current
    g.on_clock(1.0, ["Hook"], "t", map_=51.0)
    assert g.speech_view().current is first
    g.on_clock(2.0, ["Hook"], "t", map_=60.0)
    g.on_clock(3.0, ["Hook"], "t", map_=50.0)
    assert g.speech_view().current is not first
    assert g.speech_view().current.kind == "urgent_vitals"


def test_surgeon_asks_releases_while_hold():
    g = SpeechGate()
    g.on_clock(5.0, ["Clipper"], "scenario timeline")
    g.offer("check the field")
    assert g.status == "hold" and g.speech_view().queued
    g.surgeon_asks()
    cur = g.speech_view().current
    assert cur is not None and cur.release_reason == "surgeon asks"
    assert g.speech_view().queued == []
    assert g.status == "hold"  # instruments still in use; only speech was released


def test_banner_path_is_not_the_gate():
    """The gate never sees the alert banner; offering nothing must not create speech."""
    g = SpeechGate()
    g.on_clock(0.0, ["Clipper"], "t")
    assert g.speech_view().current is None and g.speech_view().queued == []


def test_open_speaks_immediately():
    g = SpeechGate()
    g.on_clock(45.0, ["Grasper", "Irrigator"], "scenario timeline")
    g.offer("two explanations fit")
    assert g.status == "open"
    assert g.speech_view().queued == []
    assert g.speech_view().current is not None
    assert g.speech_view().current.held_s == 0.0
    assert g.speech_view().current.release_reason == "clear to speak"


def test_tools_come_only_from_the_live_detector():
    tools, basis = tools_at(["Hook", "Grasper"])
    assert tools == ["Grasper", "Hook"] and basis == "live detector" and hold_instrument(tools) == "Hook"
    tools, basis = tools_at(None)
    assert tools == [] and basis == DETECTOR_UNAVAILABLE


def test_set_live_tools_none_means_unknown_not_scripted():
    g = SpeechGate()
    g.set_live_tools(["Clipper"])
    tools, basis = tools_at(g.live_tools)
    g.on_clock(1.0, tools, basis)
    assert g.view().status == "hold" and g.view().basis == "live detector"
    g.set_live_tools(None)
    tools, basis = tools_at(g.live_tools)
    assert tools == [] and basis == DETECTOR_UNAVAILABLE


def test_video_model_observations_are_never_spoken():
    import pytest
    g = SpeechGate()
    g.on_clock(0.0, [], "live detector")
    with pytest.raises(ValueError):
        g.offer("Check: Clipper in view before the Critical View of Safety was seen on video.", kind="safety_check", urgent=True)
    assert g.speech_view().current is None


def test_load_all_still_works():
    from pathlib import Path
    sc = load_all(Path(__file__).resolve().parents[2] / "data")
    assert set(sc) == {"artifact", "drug_effect", "true_bleed"}


def test_a_deviation_reaches_the_timeline_but_is_never_offered_as_speech(scenarios):
    from backend.scene import SceneEvent
    from backend.state import StateStore
    from backend.telemetry import ScenarioPlayer

    class Scene:  # TEST ONLY: the one attribute `_announce_deviations` reads
        deviations = [SceneEvent(source_id="vid-11", t_s=15.0, kind="deviation", tools=["Clipper"],
                                 text="Clipper in view before the Critical View of Safety was seen on video (0 of 3 criteria seen)")]

    store = StateStore()
    player = ScenarioPlayer(store, scenarios, initial="true_bleed")
    player.scene = Scene()  # type: ignore[assignment]
    player._announce_deviations()
    labels = [t.label for t in player._timeline]
    assert any(l.startswith("video-model observation: Clipper in view") for l in labels)
    sv = player.gate.speech_view()
    assert sv.current is None and not sv.queued
