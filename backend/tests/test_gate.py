"""Speak-or-hold gate: hold vs open, settle, urgent never held, surgeon-asks, timelines."""
from __future__ import annotations

from backend.gate import SETTLE_S, SpeechGate, hold_instrument, tools_at
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


def test_true_bleed_open_at_event_bipolar_later(scenarios):
    raw = scenarios["true_bleed"].raw
    tools, basis = tools_at(raw, 60.0)
    assert "Irrigator" in tools and hold_instrument(tools) is None
    assert "scenario" in basis
    tools_bi, _ = tools_at(raw, 95.0)
    assert hold_instrument(tools_bi) == "Bipolar"


def test_drug_effect_holds_then_opens(scenarios):
    raw = scenarios["drug_effect"].raw
    assert hold_instrument(tools_at(raw, 30.0)[0]) == "Hook"
    assert hold_instrument(tools_at(raw, 46.0)[0]) is None


def test_artifact_holds_until_92(scenarios):
    raw = scenarios["artifact"].raw
    assert hold_instrument(tools_at(raw, 50.0)[0]) == "Hook"
    assert hold_instrument(tools_at(raw, 91.9)[0]) == "Hook"
    assert hold_instrument(tools_at(raw, 92.0)[0]) is None


def test_live_detector_overrides_scenario(scenarios):
    raw = scenarios["artifact"].raw
    tools, basis = tools_at(raw, 10.0, live_tools=["Grasper"])
    assert tools == ["Grasper"] and basis == "live detector"
    tools2, basis2 = tools_at(raw, 10.0, live_tools=None)
    assert hold_instrument(tools2) == "Hook" and "scenario" in basis2


def test_set_live_tools_none_falls_back(scenarios):
    g = SpeechGate()
    g.set_live_tools(["Clipper"])
    tools, basis = tools_at(scenarios["true_bleed"].raw, 60.0, live_tools=g.live_tools)
    assert tools == ["Clipper"] and basis == "live detector"
    g.set_live_tools(None)
    tools, basis = tools_at(scenarios["true_bleed"].raw, 60.0, live_tools=g.live_tools)
    assert hold_instrument(tools) is None


def test_load_all_still_works():
    from pathlib import Path
    sc = load_all(Path(__file__).resolve().parents[2] / "data")
    assert set(sc) == {"artifact", "drug_effect", "true_bleed"}
