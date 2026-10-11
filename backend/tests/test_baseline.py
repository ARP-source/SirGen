import pytest

from backend.state import StateStore
from backend.telemetry import BaselineRule, ScenarioPlayer

SCENARIOS = ["true_bleed", "drug_effect", "artifact"]


def first_fire(series, duration):
    rule = BaselineRule()
    for t in range(duration + 1):
        v = series.at(t)
        if rule.step(t, v["map"], v["sbp"]):
            return rule.fired_rule, t
    return None, None


@pytest.mark.parametrize("sid", SCENARIOS)
def test_baseline_fires_at_scenario_time(scenarios, sid):
    sc = scenarios[sid]
    rule_id, t = first_fire(sc.series, sc.duration_s)
    assert rule_id == sc.baseline["fires"]
    assert abs(t - sc.baseline["fires_at_t_s"]) <= 1


@pytest.mark.parametrize("sid", SCENARIOS)
def test_player_reports_baseline_alarm(scenarios, sid):
    store = StateStore()
    player = ScenarioPlayer(store, scenarios, initial=sid)
    player.play()
    expected = scenarios[sid].baseline["fires_at_t_s"]
    while player.clock < expected - 1.5:
        player.tick(0.25)
    assert store.state.baseline.alarm is False
    while player.clock < expected + 1:
        player.tick(0.25)
    b = store.state.baseline
    assert b.alarm and abs(b.fired_at_t_s - expected) <= 1


def test_r1_needs_a_full_60_seconds():
    rule = BaselineRule()
    for t in range(60):
        assert rule.step(t, 60, 100) is None
    assert rule.step(60, 60, 100) == "R1"


def test_r2_resets_when_condition_breaks():
    rule = BaselineRule()
    for t, sbp in enumerate([190, 190, 190, 190, 120, 190, 190, 190, 190]):
        assert rule.step(t, 80, sbp) is None
    assert rule.step(9, 80, 190) is None  # held 4 s since t=5
    assert rule.step(10, 80, 190) == "R2"
