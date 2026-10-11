"""The audit log wired into the player and the orchestrator (stand-in LLM only)."""
import asyncio

from backend.audit import AuditLog
from backend.state import AuditInfo

from .conftest import make_stack
from .test_orchestrator import run_event


def attach(st, tmp_path):
    audit = AuditLog(tmp_path, session_id="t", context=lambda: (st.player.clock, st.player.scenario.id),
                     on_change=lambda a: st.store.update(audit=AuditInfo(**a.info())))
    st.orch.audit = audit
    st.player.attach_audit(audit)
    return audit


def kinds(audit, run=None):
    return [e["kind"] for e in audit.entries(run=run)]


def test_full_event_is_logged_in_order(scenarios, tmp_path):
    async def go():
        st = make_stack(scenarios, "drug_effect")
        audit = attach(st, tmp_path)
        await run_event(st, "drug_effect")
        es = audit.entries()
        ks = [e["kind"] for e in es]
        assert ks[0] == "run_start" and "alert" in ks and "deliberation_result" in ks
        assert ks.count("agent_message") == 6  # 3 agents x 2 rounds
        assert ks.index("alert") < ks.index("agent_message") < ks.index("deliberation_result")
        alert = next(e for e in es if e["kind"] == "alert")
        assert alert["data"]["vitals"]["map"] is not None and alert["data"]["event_to_alert_ms"] < 50
        assert "drug_given" in ks  # drugs visible by the event time
        msg = next(e for e in es if e["kind"] == "agent_message")
        assert msg["actor"].startswith("agent:") and msg["data"]["evidence"] and msg["data"]["event_id"] == "ev-1"
        res = next(e for e in es if e["kind"] == "deliberation_result")
        assert res["data"]["outcome"] == "converged" and res["data"]["elapsed_s"] >= 0
        assert audit.verify().ok
        assert st.state.audit.entries == audit.count and st.state.audit.status == "ok"
    asyncio.run(go())


def test_each_drug_is_logged_once(scenarios, tmp_path):
    async def go():
        st = make_stack(scenarios, "drug_effect")
        audit = attach(st, tmp_path)
        st.player.clock = 300.0
        for _ in range(5):
            st.player.tick(0.0)
        ids = [e["data"]["source_id"] for e in audit.entries() if e["kind"] == "drug_given"]
        assert ids and len(ids) == len(set(ids))
    asyncio.run(go())


def test_controls_runs_and_reset_keep_the_chain(scenarios, tmp_path):
    async def go():
        st = make_stack(scenarios, "drug_effect")
        audit = attach(st, tmp_path)
        st.player.play()
        st.player.pause()
        st.player.select("true_bleed")
        st.player.restart()
        before = audit.count
        st.player.reset()
        assert audit.count > before  # reset never erases anything
        es = audit.entries()
        assert [e["data"].get("action") for e in es if e["kind"] == "control"] == [
            "play", "pause", "select scenario true_bleed", "restart", "reset"]
        assert all(e["actor"] == "presenter" for e in es if e["kind"] == "control")
        runs = [e["run"] for e in es if e["kind"] == "run_start"]
        assert runs == [1, 2, 3, 4]  # initial, select, restart, reset
        assert {e["scenario_id"] for e in es if e["run"] == 2 and e["kind"] == "run_start"} == {"true_bleed"}
        assert audit.verify().ok
    asyncio.run(go())


def test_unavailable_agent_is_logged_with_its_reason(scenarios, tmp_path):
    async def go():
        st = make_stack(scenarios, "drug_effect")
        audit = attach(st, tmp_path)
        st.fake.fail("record", "http500")
        await run_event(st, "drug_effect")
        es = [e for e in audit.entries() if e["kind"] == "agent_unavailable"]
        assert es and all(e["data"]["agent"] == "record" and e["data"]["reason"] for e in es)
        res = next(e for e in audit.entries() if e["kind"] == "deliberation_result")
        assert res["data"]["unavailable_agents"] == ["record"]
    asyncio.run(go())


def test_reset_during_deliberation_logs_nothing_stale(scenarios, tmp_path):
    async def go():
        st = make_stack(scenarios, "drug_effect", delay_s=0.3)
        audit = attach(st, tmp_path)
        st.fire()
        await asyncio.sleep(0.05)
        st.player.reset()
        await asyncio.sleep(0.5)
        run1 = [e["kind"] for e in audit.entries(run=1)]
        assert "alert" in run1 and "agent_message" not in run1 and "deliberation_result" not in run1
        assert audit.verify().ok
    asyncio.run(go())


def test_player_without_audit_still_works(scenarios):
    async def go():
        st = make_stack(scenarios, "drug_effect")
        await run_event(st, "drug_effect")
        assert st.state.deliberation.outcome == "converged"
    asyncio.run(go())
