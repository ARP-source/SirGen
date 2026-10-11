import asyncio

from backend.state import StateStore
from backend.telemetry import ScenarioPlayer


def run_to(player, t, dt=0.1):
    while player.clock < t - 1e-9 and not player.ended:
        player.tick(dt)


def test_scenario_event_raises_alert_immediately(scenarios):
    async def go():
        store = StateStore()
        player = ScenarioPlayer(store, scenarios, initial="drug_effect")
        queue = store.subscribe()
        queue.get_nowait()  # initial snapshot
        player.play()
        run_to(player, 29.9)
        assert store.state.event is None
        player.tick(0.1)  # crosses event_t_s = 30
        ev = store.state.event
        assert ev is not None and ev.trigger == "scenario" and ev.event_type == "map_drop"
        assert ev.event_timestamp == 30.0 and abs(ev.alert_shown_at - 30.0) < 0.2
        if scenarios["drug_effect"].series.source_kind == "real_time_shifted":
            assert ev.simulated is False and "time-shifted" in ev.alert_text
        else:
            assert ev.alert_text.startswith("SIMULATED TELEMETRY ALERT")
        assert ev.event_to_alert_ms is not None and 0 <= ev.event_to_alert_ms < 50
        assert store.state.metrics.event_to_alert_ms == ev.event_to_alert_ms
        assert ev.context_status == "unavailable"
        # urgent publish reached the subscriber without the throttled broadcaster running
        assert '"alert_shown_at"' in queue.get_nowait()
        kinds = [i.kind for i in store.state.timeline]
        assert kinds.count("event") == 1 and "alert" in kinds and "context" in kinds
        run_to(player, 60)
        assert store.state.event.event_id == "ev-1"  # scheduled event fires once
    asyncio.run(go())


def test_presenter_trigger_and_reset(scenarios):
    store = StateStore()
    player = ScenarioPlayer(store, scenarios, initial="true_bleed")
    player.play()
    run_to(player, 12)
    ev = player.trigger()
    assert ev.trigger == "presenter" and abs(ev.event_timestamp - 12) < 0.2
    assert ev.event_to_alert_ms is not None
    player.reset()
    assert store.state.event is None and store.state.clock_s == 0 and not store.state.playing
    assert store.state.metrics.event_to_alert_ms is None


def test_quiet_flag_for_artifact(scenarios):
    store = StateStore()
    player = ScenarioPlayer(store, scenarios, initial="artifact")
    player.play()
    run_to(player, 50.1)
    assert store.state.event.event_type == "art_spike"
    assert store.state.event.alert_level == "quiet"


def test_vitals_and_drug_log_follow_clock(scenarios):
    store = StateStore()
    player = ScenarioPlayer(store, scenarios, initial="true_bleed")
    assert [d.source_id for d in store.state.drug_log] == ["drug-01", "drug-02", "drug-03"]  # all t_s < 0
    player.play()
    run_to(player, 65.4)
    v = store.state.vitals
    assert v.t_s == 65 and v.map == 70.0 and v.source_kind == "simulated"


def test_pause_stops_clock_and_end_stops_play(scenarios):
    store = StateStore()
    player = ScenarioPlayer(store, scenarios, initial="artifact")
    player.play()
    run_to(player, 5)
    player.pause()
    player.tick(3.0)
    assert store.state.clock_s == 5.0
    player.play()
    run_to(player, 200)
    assert store.state.ended and not store.state.playing and store.state.clock_s == 120
