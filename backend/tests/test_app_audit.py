"""Audit and report endpoints on the real app wiring (model endpoint unreachable on purpose)."""
import time

from fastapi.testclient import TestClient


def wait_unavailable(client):
    end = time.time() + 10
    while time.time() < end:
        if client.get("/state").json()["event"]["context_status"] == "unavailable":
            return
        time.sleep(0.05)
    raise AssertionError("deliberation did not finish")


def test_audit_endpoints_and_state(monkeypatch):
    monkeypatch.setenv("LIGHTNING_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OMNI_ENABLED", "0")
    from backend.app import app
    with TestClient(app) as client:
        st = client.get("/state").json()["audit"]
        assert st["enabled"] and st["entries"] >= 2 and st["status"] == "ok" and st["verified"] is None
        client.post("/control/scenario/drug_effect")
        client.post("/control/trigger")
        wait_unavailable(client)
        client.post("/control/phase/ClippingCutting")
        time.sleep(0.6)  # phase watcher period
        entries = client.get("/audit/entries").json()
        kinds = [e["kind"] for e in entries["entries"]]
        for k in ("session_start", "run_start", "control", "alert", "agent_unavailable", "deliberation_result", "phase_change"):
            assert k in kinds, k
        assert entries["head_hash"] == entries["entries"][-1]["hash"]
        ph = next(e for e in entries["entries"] if e["kind"] == "phase_change")
        assert ph["actor"] == "presenter" and ph["data"]["name"] == "ClippingCutting"
        v = client.post("/audit/verify").json()
        assert v["ok"] and v["entries"] == entries["total"]
        time.sleep(0.3)
        s = client.get("/state").json()["audit"]
        assert s["verified"] is True and s["head_hash"] == v["head_hash"]
        assert client.get("/audit/file").text.count("\n") == entries["total"]
        assert client.get("/health").json()["audit"]["entries"] == entries["total"]

        assert client.get("/report.html").status_code == 404
        summary = client.post("/report/generate").json()
        assert summary["run"] == 2 and summary["narrative"] == "unavailable" and summary["chain_ok"] is True
        html = client.get("/report.html").text
        assert "Narrative unavailable" in html and "ev-1" in html and "Agent" in html
        assert client.get("/report").json()["reports"][0]["sha256"] == summary["sha256"]
        assert client.post("/report/generate?run=99").status_code == 404
        # the report generation was logged, and the chain still verifies
        assert client.get("/audit/entries").json()["entries"][-1]["kind"] == "report_generated"
        assert client.post("/audit/verify").json()["ok"]


def test_reset_keeps_the_log(monkeypatch):
    monkeypatch.setenv("LIGHTNING_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OMNI_ENABLED", "0")
    from backend.app import app
    with TestClient(app) as client:
        client.post("/control/trigger")
        n = client.get("/audit/info").json()["entries"]
        client.post("/control/reset")
        assert client.get("/audit/info").json()["entries"] > n
        assert client.get("/state").json()["event"] is None
        assert client.post("/audit/verify").json()["ok"]
