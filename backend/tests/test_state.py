import asyncio
import json

from fastapi.testclient import TestClient

from backend.state import StateStore


def test_broadcast_is_throttled_and_conflated():
    async def go():
        store = StateStore(min_interval_s=0.1)
        q = store.subscribe()
        q.get_nowait()
        task = asyncio.create_task(store.run_broadcaster())
        start = store.publish_count
        for i in range(50):
            store.update(clock_s=float(i))
            await asyncio.sleep(0.005)  # 50 changes over ~0.25 s
        await asyncio.sleep(0.15)
        published = store.publish_count - start
        assert 2 <= published <= 5  # ~10 Hz, not 50
        assert json.loads(q.get_nowait())["clock_s"] == 49.0  # latest state only
        task.cancel()
    asyncio.run(go())


def test_urgent_update_bypasses_throttle():
    async def go():
        store = StateStore(min_interval_s=10)
        q = store.subscribe()
        q.get_nowait()
        store.update(clock_s=1.0)
        assert q.empty()
        assert store.update(urgent=True, playing=True) > 0
        assert json.loads(q.get_nowait())["playing"] is True
    asyncio.run(go())


def test_unknown_field_rejected():
    store = StateStore()
    try:
        store.update(nonsense=1)
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_ws_state_and_controls_end_to_end():
    from backend.app import app
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        ids = {s["id"] for s in client.get("/scenarios").json()}
        assert ids == {"true_bleed", "drug_effect", "artifact"}
        with client.websocket_connect("/ws/state") as ws:
            first = json.loads(ws.receive_text())
            assert "vitals" in first and first["labels"]["footage"] == "Recorded footage · Research prototype"
            assert first["agents"]["surgical"]["state"] == "idle" and first["agents"]["surgical"]["message"] is None
            assert first["deliberation"]["outcome"] is None and first["metrics"]["deliberation_s"] is None
            assert client.post("/control/scenario/artifact").json()["scenario_id"] == "artifact"
            for _ in range(50):
                msg = json.loads(ws.receive_text())
                if msg["scenario_id"] == "artifact":
                    break
            assert msg["scenario_id"] == "artifact"
            ev = client.post("/control/trigger").json()["event"]
            assert ev["trigger"] == "presenter" and ev["event_to_alert_ms"] is not None
        assert client.post("/control/scenario/nope").status_code == 404
