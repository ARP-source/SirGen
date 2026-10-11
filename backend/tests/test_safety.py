"""Safety agent: every proposed sentence is checked by a second model before the gate; it can only hold,
never rewrite; when it fails the sentence passes through and everything says it was unvetted."""
import asyncio
import json

import httpx

from backend.agents import safety as S
from backend.agents.llm_client import LightningClient
from backend.state import StateStore

from .conftest import DATA
from .test_procedure import FakePlayer, scene_with

STEPS = json.loads((DATA / "procedure" / "lap_chole_steps.json").read_text())
PROMPT = (DATA.parent / "prompts" / "safety.md").read_text()


class Audit:
    def __init__(self):
        self.log = []

    def record(self, kind, summary, data, **kw):
        self.log.append((kind, summary, data))


def client_with(handler, calls=None, delay_s=0.0):
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "stub"}]})
        body = json.loads(request.content)
        if calls is not None:
            calls.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": handler(body)}, "finish_reason": "stop"}]})

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay_s)  # a model that takes time, so a reset can land while it thinks
        return serve(request)
    return LightningClient(base_url="http://stub/v1", timeout_s=5, transport=httpx.MockTransport(slow if delay_s else serve))


def allowed(body):
    return body["response_format"]["json_schema"]["schema"]["properties"]["evidence"]["items"]["properties"]["source_id"]["enum"]


def speak(body):
    return json.dumps({"verdict": "speak", "reason": "The sentence names a logged video event for the current step as an estimate.",
                       "confidence": "high", "evidence": [{"source_id": "msg", "note": "about the clipper"}, {"source_id": "phase", "note": "same step"}]})


def hold(body):
    return json.dumps({"verdict": "hold", "reason": "The sentence names a medication, which is outside the speaking rules.",
                       "confidence": "high", "evidence": [{"source_id": "rules", "note": "no treatment"}]})


def make(handler, audit=None, calls=None, delay_s=0.0):
    sc = scene_with(["Clipper"])
    store, player = StateStore(), FakePlayer()
    agent = S.SafetyAgent(store, player, sc, STEPS, client_with(handler, calls, delay_s), PROMPT, audit=audit)
    return agent, store, player


def test_evidence_quotes_the_proposal_and_live_state_only():
    agent, store, _ = make(speak)
    block, ids, sees = agent.evidence("On video a clipper is in view before the three criteria, an estimate.", "shared_view", "procedure", 20.0)
    assert block["proposal"]["text"].startswith("On video a clipper") and block["proposal"]["from"] == S.ORIGIN_LABEL["procedure"]
    assert {"msg", "rules", "gate", "said", "phase", "proc"} <= set(ids)
    assert block["rules"]["items"] == S.RULES and block["said_recently"]["items"] == []
    assert all(e["source_id"] in ids for e in block["video_events"])
    assert any(s["source_id"] == "msg" for s in sees)


def test_cleared_sentence_reaches_the_gate_and_is_logged():
    audit = Audit()
    calls = []
    agent, store, player = make(speak, audit, calls)
    v = asyncio.run(agent.vet("On video a clipper is in view before the three criteria, an estimate.", "shared_view", "procedure"))
    assert v.verdict == "speak" and v.by == "model"
    assert player.spoken == [("On video a clipper is in view before the three criteria, an estimate.", "shared_view", False)]
    slot = store.state.agents["safety"]
    assert slot.state == "done" and slot.message["verdict"] == "speak" and slot.message["hypothesis"].startswith("cleared:")
    assert slot.message["proposal"] == player.spoken[0][0] and slot.message["origin"] == "procedure"
    assert audit.log[0][0] == "safety_verdict" and audit.log[0][2]["verdict"] == "speak" and audit.log[0][2]["proposal"] == player.spoken[0][0]
    assert len(calls) == 1 and calls[0]["response_format"]["json_schema"]["name"] == "SafetyVerdict"
    assert "msg" in allowed(calls[0]) and "rules" in allowed(calls[0])


def test_held_sentence_never_reaches_the_gate_and_text_is_never_rewritten():
    audit = Audit()
    agent, store, player = make(hold, audit)
    v = asyncio.run(agent.vet("Give heparin now.", "shared_view", "deliberation"))
    assert v.verdict == "hold"
    assert player.spoken == []
    slot = store.state.agents["safety"]
    assert slot.message["verdict"] == "hold" and slot.message["proposal"] == "Give heparin now."
    assert audit.log[0][2]["verdict"] == "hold" and audit.log[0][2]["proposal"] == "Give heparin now."
    assert agent.held == 1 and agent.checked == 1


def test_repeat_within_ninety_seconds_is_held_by_code_without_a_model_call():
    calls = []
    agent, store, player = make(speak, calls=calls)
    text = "On video a clipper is in view before the three criteria, an estimate."
    asyncio.run(agent.vet(text, "shared_view", "procedure"))
    v = asyncio.run(agent.vet("On video a clipper is in view before the three criteria, estimate.", "shared_view", "deliberation"))
    assert v.verdict == "hold" and v.by == "code"
    assert len(calls) == 1  # the second sentence never went to the model
    assert len(player.spoken) == 1
    player.clock = 20.0 + S.SAID_WINDOW_S + 1
    v = asyncio.run(agent.vet(text, "shared_view", "procedure"))
    assert v.verdict == "speak" and len(player.spoken) == 2


def test_unavailable_checker_passes_the_sentence_through_unvetted_and_says_so():
    audit = Audit()
    calls = []

    def bad(body):
        return json.dumps({"verdict": "speak", "reason": "The patient has a bleed.", "confidence": "high",
                           "evidence": [{"source_id": "msg", "note": "n"}]})
    agent, store, player = make(bad, audit, calls)
    v = asyncio.run(agent.vet("On video a clipper is in view, an estimate.", "shared_view", "procedure"))
    assert v is None and len(calls) == 2  # one retry
    assert player.spoken == [("On video a clipper is in view, an estimate.", "shared_view", False)]
    slot = store.state.agents["safety"]
    assert slot.state == "unavailable" and "unvetted" in slot.status and "banned" in slot.reason
    assert slot.message["verdict"] == "unvetted"
    assert audit.log[0][2]["unvetted"] is True and audit.log[0][2]["proposal"] == player.spoken[0][0]
    assert agent.unvetted == 1

    def down(request):
        raise httpx.ConnectError("refused")
    agent.client = LightningClient(base_url="http://stub/v1", timeout_s=5, transport=httpx.MockTransport(down))
    asyncio.run(agent.vet("Another sentence about the clipper on video.", "shared_view", "procedure"))
    assert len(player.spoken) == 2 and store.state.agents["safety"].state == "unavailable"


def test_urgent_speech_never_waits_for_the_checker():
    calls = []
    agent, store, player = make(speak, calls=calls)
    agent.submit("MAP below threshold.", kind="urgent_vitals", urgent=True)
    assert player.spoken == [("MAP below threshold.", "urgent_vitals", True)] and calls == []


def test_submit_runs_as_a_task_and_a_reset_drops_a_stale_sentence():
    agent, store, player = make(speak, delay_s=0.05)

    async def go():
        agent.submit("On video a clipper is in view, an estimate.", kind="shared_view", origin="procedure")
        await asyncio.sleep(0.01)
        assert store.state.agents["safety"].state == "thinking"
        await asyncio.gather(*agent._tasks)
    asyncio.run(go())
    assert len(player.spoken) == 1

    async def reset_mid_flight():
        agent.submit("A second sentence about the clipper and the duct on video.", kind="shared_view", origin="procedure")
        await asyncio.sleep(0.01)
        agent.reset()
        await asyncio.gather(*agent._tasks)
    asyncio.run(reset_mid_flight())
    assert len(player.spoken) == 1 and store.state.agents["safety"].state == "idle"


def test_check_verdict_rules():
    ids = ["msg", "rules", "phase"]
    base = {"verdict": "speak", "reason": "It names a logged event for this step.", "confidence": "medium",
            "evidence": [{"source_id": "msg", "note": "ok"}]}
    S.check_verdict(json.dumps(base), ids, "clipper 20")
    for change, why in (({"verdict": "rewrite"}, "verdict"), ({"evidence": [{"source_id": "vid-9", "note": ""}]}, "source"),
                        ({"reason": "One. Two sentences."}, "sentence"), ({"reason": "You should hold it."}, "banned"),
                        ({"reason": "Said 45 s ago."}, "number"), ({"evidence": []}, "empty")):
        try:
            S.check_verdict(json.dumps({**base, **change}), ids, "clipper 20")
        except S.InvalidVerdict:
            continue
        raise AssertionError(f"accepted: {why}")
