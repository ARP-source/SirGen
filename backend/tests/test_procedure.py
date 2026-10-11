"""Procedure agent (the attending's reading of the scene) and the caution highlight: evidence built by code,
model output validated, nothing written in place of a failed reading."""
import asyncio
import json
import time

import httpx

from backend.agents import procedure as P
from backend.agents.llm_client import LightningClient
from backend.phase import PhaseRules
from backend.scene import LiveScene, compute_caution
from backend.state import StateStore

from .conftest import DATA

STEPS = json.loads((DATA / "procedure" / "lap_chole_steps.json").read_text())
PROMPT = (DATA.parent / "prompts" / "procedure.md").read_text()


def scene_with(tools, anatomy=(), cvs=None, seconds=20, caution=None):
    sc = LiveScene(PhaseRules.load(DATA / "procedure" / "phase_rules.json"), STEPS)
    now = time.monotonic()
    for s in range(seconds):
        for k in range(3):
            res = {"tools": [{"cls": t, "conf": 0.8, "box": [10, 10, 100, 100]} for t in tools],
                   "anatomy": [{"cls": a, "conf": 0.6, "box": [120, 120, 200, 200]} for a in anatomy],
                   "cvs": cvs, "ms": 30, "frame_id": s * 3 + k}
            if caution:
                res["caution"] = caution
            sc.add(res, s + k / 3, now)
    sc.step(float(seconds), now)
    return sc


class FakePlayer:
    def __init__(self):
        self.clock = 20.0
        self.playing = True
        self.spoken = []

    def offer_speech(self, text, kind="team_question", urgent=False):
        self.spoken.append((text, kind, urgent))


def client_with(handler):
    def serve(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "stub"}]})
        body = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": handler(body)}, "finish_reason": "stop"}]})
    return LightningClient(base_url="http://stub/v1", timeout_s=5, transport=httpx.MockTransport(serve))


def allowed(body):
    return body["response_format"]["json_schema"]["schema"]["properties"]["evidence"]["items"]["properties"]["source_id"]["enum"]


def good(body, concern=None):
    ids = allowed(body)
    nxt = body["response_format"]["json_schema"]["schema"]["properties"]["next"]["enum"]
    return json.dumps({"doing": "The detector shows a hook working in the hepatocystic triangle.",
                       "intent": "Clearing the triangle so that two structures can be seen entering the gallbladder.",
                       "next": nxt[-1], "watch": "All three Critical View of Safety criteria before any clip.",
                       "concern": concern, "confidence": "medium",
                       "evidence": [{"source_id": ids[0], "note": "step"}, {"source_id": "tools", "note": "hook 20 s"}]})


def test_evidence_is_built_from_the_live_scene_only():
    sc = scene_with(["Hook", "Grasper"], anatomy=["cystic_duct"], cvs={"c1_two_structures": 0.2, "c2_hepatocystic_triangle": 0.1, "c3_cystic_plate": 0.9})
    agent = P.ProcedureAgent(StateStore(), FakePlayer(), sc, STEPS, client_with(good), PROMPT)
    block, ids, next_steps, concern_ids, sees = agent.evidence(20.0)
    assert block["tools_window"]["seconds_present"]["Hook"] == 20
    assert block["knowledge"]["usual_next"] in next_steps and block["step"]["name"] in next_steps
    assert "phase" in ids and "proc" in ids and "tools" in ids and "cvs" in ids
    assert "cvs" in concern_ids
    assert all(e["source_id"] in ids for e in block["video_events"])
    assert any(s["source_id"] == "tools" for s in sees)


def test_valid_reading_is_published_and_logged():
    sc = scene_with(["Hook"])
    store, player = StateStore(), FakePlayer()
    log = []

    class Audit:
        def record(self, kind, summary, data, **kw):
            log.append((kind, summary, data))
    agent = P.ProcedureAgent(store, player, sc, STEPS, client_with(good), PROMPT, audit=Audit())
    asyncio.run(agent.turn())
    v = store.state.procedure
    assert v.state == "done" and v.doing.startswith("The detector shows") and v.next_display and v.turns == 1
    assert store.state.agents["procedure"].message["hypothesis"] == v.doing
    assert log and log[0][0] == "procedure_reading" and log[0][2]["doing"] == v.doing
    assert player.spoken == []  # no concern, nothing offered to the gate


def test_concern_requires_a_video_observation_and_is_spoken_through_the_gate():
    cv = {"c1_two_structures": 0.1, "c2_hepatocystic_triangle": 0.1, "c3_cystic_plate": 0.1}
    sc = scene_with(["Clipper"], cvs=cv)
    store, player = StateStore(), FakePlayer()

    def handler(body):
        ids = allowed(body)
        nxt = body["response_format"]["json_schema"]["schema"]["properties"]["next"]["enum"]
        return json.dumps({"doing": "A clipper is in view on video.", "intent": "Moving to clip the cystic structures.",
                           "next": nxt[0], "watch": "The three criteria of the Critical View of Safety.",
                           "concern": "On video the clipper appears before the classifier saw the three criteria, an estimate.",
                           "confidence": "low", "evidence": [{"source_id": "cvs", "note": "0 of 3 seen"}]})
    agent = P.ProcedureAgent(store, player, sc, STEPS, client_with(handler), PROMPT)
    asyncio.run(agent.turn())
    asyncio.run(agent.turn())
    assert store.state.procedure.concern
    assert len(player.spoken) == 1 and player.spoken[0][1] == "shared_view" and player.spoken[0][2] is False


def test_invalid_output_is_retried_once_then_unavailable_with_reason():
    sc = scene_with(["Hook"])
    store = StateStore()
    calls = []

    def bad(body):
        calls.append(1)
        return json.dumps({"doing": "The patient has a bleed.", "intent": "x", "next": "CalotTriangleDissection",
                           "watch": "y", "concern": None, "confidence": "high", "evidence": [{"source_id": "phase", "note": "n"}]})
    agent = P.ProcedureAgent(store, FakePlayer(), sc, STEPS, client_with(bad), PROMPT)
    asyncio.run(agent.turn())
    assert len(calls) == 2
    assert store.state.procedure.state == "unavailable" and "banned" in store.state.procedure.reason
    assert store.state.procedure.doing is None


def test_check_reading_rules():
    ids, nxt = ["phase", "proc", "tools"], ["CalotTriangleDissection", "ClippingCutting"]
    base = {"doing": "Hook in the triangle on video.", "intent": "Clearing the triangle.", "next": "ClippingCutting",
            "watch": "Three criteria first.", "concern": None, "confidence": "medium",
            "evidence": [{"source_id": "tools", "note": "hook"}]}
    P.check_reading(json.dumps(base), ids, nxt, set(), "hook 20")
    for change, why in (({"next": "GallbladderRetraction"}, "next"), ({"evidence": [{"source_id": "vid-9", "note": ""}]}, "source"),
                        ({"doing": "One. Two sentences."}, "sentence"), ({"watch": "You should clip now."}, "banned"),
                        ({"doing": "Hook for 45 s on video."}, "number"),
                        ({"concern": "Clipper before CVS on video."}, "concern")):
        try:
            P.check_reading(json.dumps({**base, **change}), ids, nxt, set(), "hook 20")
        except P.InvalidReading:
            continue
        raise AssertionError(f"accepted: {why}")


def test_due_only_while_playing_and_live_and_paced():
    sc = scene_with(["Hook"])
    player = FakePlayer()
    agent = P.ProcedureAgent(StateStore(), player, sc, STEPS, client_with(good), PROMPT, min_gap_s=6, max_gap_s=25)
    now = time.monotonic()
    assert agent.due(now)
    agent._last_turn_wall = now
    agent._last_fingerprint = agent.fingerprint()
    assert not agent.due(now + 3)
    assert not agent.due(now + 26)  # the scene went stale: no reading of a dead detector
    sc.add({"tools": [{"cls": "Hook", "conf": 0.8, "box": [0, 0, 10, 10]}], "ms": 30, "frame_id": 99}, 46.0, now + 26)
    assert agent.due(now + 26)
    player.playing = False
    assert not agent.due(now + 26)


def test_compute_caution_and_scene_event():
    res = {"tools": [{"cls": "Clipper", "conf": 0.7, "box": [100, 100, 200, 200]}, {"cls": "Grasper", "conf": 0.7, "box": [600, 100, 700, 200]}],
           "anatomy": [{"cls": "cystic_artery", "conf": 0.5, "box": [230, 120, 300, 180]}, {"cls": "gallbladder", "conf": 0.9, "box": [0, 0, 800, 400]}]}
    c = compute_caution(res, 854, 480)
    assert c and c["tool"] == "Clipper" and c["structure"] == "cystic_artery" and c["kind"] == "cutting"
    assert 0 < c["box"][0] < c["box"][2] <= 1 and "caution" in c["label"] and "AI estimate" in c["label"]
    far = {"tools": [{"cls": "Hook", "conf": 0.7, "box": [0, 0, 50, 50]}], "anatomy": [{"cls": "cystic_duct", "conf": 0.5, "box": [700, 400, 800, 450]}]}
    assert compute_caution(far, 854, 480) is None
    assert compute_caution({"tools": None, "anatomy": None}, 854, 480) is None
    sc = scene_with(["Clipper"], anatomy=["cystic_artery"], caution=c, seconds=5)
    kinds = [e.kind for e in sc.events]
    assert kinds.count("caution") == 1  # once per 20 s, not once per second
    assert sc.view(time.monotonic()).caution is not None
