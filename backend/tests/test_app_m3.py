"""The real app wiring (lifespan, controls, WS) with the model endpoint unreachable: no model is needed
to see that the alert still fires and that the failure is shown as unavailable, never as scripted text."""
import json
import time

from fastapi.testclient import TestClient


def poll(client, pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get("/state").json()
        if pred(st):
            return st
        time.sleep(0.05)
    raise AssertionError("condition not reached; last state: " + json.dumps(client.get("/state").json()["event"]))


def test_alert_fires_and_unreachable_lightning_is_shown_as_unavailable(monkeypatch):
    monkeypatch.setenv("LIGHTNING_URL", "http://127.0.0.1:9/v1")  # nothing listens here
    monkeypatch.setenv("OMNI_ENABLED", "0")
    from backend.app import app
    with TestClient(app) as client:
        h = client.get("/health").json()
        assert h["milestone"] == "M4a" and h["speak_or_hold"] is True
        assert h["lightning"]["url"] == "http://127.0.0.1:9/v1" and h["agents"] == "idle"
        client.post("/control/scenario/drug_effect")
        ev = client.post("/control/trigger").json()["event"]
        assert ev["alert_text"].startswith("SIMULATED") or ev["alert_text"].startswith("TELEMETRY")
        assert ev["context_status"] == "pending" and ev["event_to_alert_ms"] < 50
        st = poll(client, lambda s: s["event"]["context_status"] == "unavailable")
        assert all(a["state"] == "unavailable" and "cannot reach" in a["reason"] for k, a in st["agents"].items() if k not in ("procedure", "safety"))
        assert all(a["message"] is None for k, a in st["agents"].items() if k not in ("procedure", "safety"))
        assert st["deliberation"]["team_question"] is None and st["deliberation"]["summary"] is None
        assert st["labels"]["context_unavailable"] == "Context unavailable. Source record remains available for review."
        assert st["metrics"]["deliberation_s"] is None
        # reset clears everything the agents wrote
        client.post("/control/reset")
        st = client.get("/state").json()
        assert st["event"] is None and all(a["state"] == "idle" and a["reason"] is None for k, a in st["agents"].items() if k not in ("procedure", "safety"))
