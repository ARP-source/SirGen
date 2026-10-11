"""Optional Omni question (backend/omni.py). Tested against stand-ins only: the request SHAPE is checked,
not how a real Omni build handles it."""
import asyncio
import base64
import json
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest

from backend import omni
from backend.state import Event

from .conftest import make_stack


@pytest.fixture()
def clip_file(tmp_path) -> Path:
    path = tmp_path / "video.mp4"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 180))
    for i in range(150):  # 15 s
        w.write(np.full((180, 320, 3), (i * 3) % 255, np.uint8))
    w.release()
    return path


def event(sc):
    return Event(event_id="ev-1", event_type="map_drop", event_timestamp=12.0, trigger="scenario", simulated=True)


def client_for(path, handler, **kw):
    return omni.OmniClient(Path("."), base_url="http://omni.test/v1", transport=httpx.MockTransport(handler),
                           resolver=lambda sc, repo: (path, "test"), **kw)


def answer(text):
    return lambda req: httpx.Response(200, json={"choices": [{"message": {"content": text}}]}) \
        if not req.url.path.endswith("/models") else httpx.Response(200, json={"data": [{"id": "omni-served"}]})


def test_extract_clip_cuts_ten_seconds_as_mp4(clip_file, tmp_path):
    data = omni.extract_clip(clip_file, start_s=2.0)
    assert data and len(data) < 500_000
    out = tmp_path / "out.mp4"
    out.write_bytes(data)
    cap = cv2.VideoCapture(str(out))
    assert cap.isOpened()
    assert 9.5 <= cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS) <= 10.5  # 10 s of video at real-time speed
    assert omni.extract_clip(tmp_path / "missing.mp4", 0) is None


def test_request_shape_and_answer(scenarios, clip_file):
    seen = []

    def handler(req):
        seen.append((req.url.path, json.loads(req.content) if req.content else None))
        return answer("Grasper and irrigator are in view. No blood is visible.")(req)

    async def go():
        c = client_for(clip_file, handler)
        out = await c(event(None), scenarios["true_bleed"], at_s=12.0)
        await c.aclose()
        return out
    assert asyncio.run(go()) == "Grasper and irrigator are in view. No blood is visible."
    assert [p for p, _ in seen] == ["/v1/models", "/v1/chat/completions"]
    body = seen[1][1]
    assert body["model"] == "omni-served" and body["max_tokens"] == 120 and body["temperature"] == 0.2
    assert body["mm_processor_kwargs"] == {"use_audio_in_video": False}  # top level, not inside chat_template_kwargs
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    parts = body["messages"][0]["content"]
    assert parts[0]["type"] == "video_url" and parts[0]["video_url"]["url"].startswith("data:video/mp4;base64,")
    assert base64.b64decode(parts[0]["video_url"]["url"].split(",", 1)[1])[4:8] == b"ftyp"
    assert parts[1]["type"] == "text" and "last 10 seconds" in parts[1]["text"] and "Do not give a diagnosis" in parts[1]["text"]


def test_skipped_without_a_video_file_and_on_error_timeout_or_banned_wording(scenarios, clip_file):
    calls = []

    async def go():
        sc = scenarios["true_bleed"]
        none = omni.OmniClient(Path("."), base_url="http://omni.test/v1", resolver=lambda s, r: (None, "no video"),
                               transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(500)))
        assert await none(event(sc), sc, 12.0) is None and calls == []  # nothing is sent without a clip
        err = client_for(clip_file, lambda r: httpx.Response(500, text="x"), model="m")
        assert await err(event(sc), sc, 12.0) is None

        async def slow(req):
            await asyncio.sleep(5)
            return httpx.Response(200, json={})
        t0 = time.perf_counter()
        late = client_for(clip_file, slow, model="m", timeout_s=0.3)
        assert await late(event(sc), sc, 12.0) is None and time.perf_counter() - t0 < 1.5
        bad = client_for(clip_file, answer("This looks like a haemorrhage; you should transfuse."), model="m")
        assert await bad(event(sc), sc, 12.0) is None
        empty = client_for(clip_file, answer("   "), model="m")
        assert await empty(event(sc), sc, 12.0) is None
        for c in (none, err, late, bad, empty):
            await c.aclose()
    asyncio.run(go())


def test_orchestrator_gives_the_answer_to_the_surgical_agent_only_and_labels_it(scenarios):
    async def fake_omni(ev, sc, at_s):
        return "Grasper and irrigator in view; no blood visible."

    async def go():
        st = make_stack(scenarios, "true_bleed", omni=fake_omni)
        st.fire()
        await st.orch.join()
        surg = st.fake.requests_for("surgical", 1)[0]
        block = json.loads(surg["labels"]["EVIDENCE"])
        assert block["omni"] == {"source_id": "omni-ev-1", "answer": "Grasper and irrigator in view; no blood visible."}
        assert "omni-ev-1" in json.loads(surg["labels"]["ALLOWED_SOURCE_IDS"])
        for other in ("anesthesia", "record"):
            assert "omni" not in st.fake.requests_for(other, 1)[0]["user"].lower()
        assert any("Video model (Omni) description" in s["text"] for s in st.state.agents["surgical"].sees)
        assert st.state.deliberation.status == "done"
        await st.orch.aclose()
    asyncio.run(go())


def test_slow_omni_is_skipped_and_does_not_hold_back_the_other_agents(scenarios):
    class SlowOmni:
        timeout_s = 0.3

        async def __call__(self, ev, sc, at_s):
            await asyncio.sleep(10)

    async def go():
        st = make_stack(scenarios, "drug_effect", omni=SlowOmni())
        st.fire()
        await asyncio.sleep(0.05)
        assert st.state.agents["surgical"].state == "waiting" and st.state.agents["anesthesia"].state in ("thinking", "done")
        await st.orch.join()
        assert json.loads(st.fake.requests_for("surgical", 1)[0]["labels"]["EVIDENCE"])["omni"] is None
        first = {a: st.fake.requests_for(a, 1)[0]["received_at"] for a in ("surgical", "anesthesia", "record")}
        assert first["anesthesia"] < first["surgical"] and first["record"] < first["surgical"]
        assert st.state.deliberation.status == "done"
        await st.orch.aclose()
    asyncio.run(go())


def test_reset_cancels_a_pending_omni_call(scenarios):
    started = []

    class HangOmni:
        timeout_s = 30

        async def __call__(self, ev, sc, at_s):
            started.append(1)
            await asyncio.sleep(30)

    async def go():
        st = make_stack(scenarios, "true_bleed", omni=HangOmni())
        st.fire()
        await asyncio.sleep(0.05)
        task = st.orch.task
        st.player.reset()
        await asyncio.sleep(0.05)
        assert started and task.cancelled()
        await st.orch.aclose()
    asyncio.run(go())
