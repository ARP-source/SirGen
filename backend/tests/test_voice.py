"""backend.voice against an httpx.MockTransport (TEST ONLY: no speech service, no model)."""
import asyncio
import io
import wave

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import voice


def tiny_wav(n_frames: int = 220, rate: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x01" * n_frames)
    return buf.getvalue()


WAV = tiny_wav()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Fresh cache, warn throttle and transport for every test; TTS forced on."""
    monkeypatch.delenv("SENTINEL_TTS", raising=False)
    voice._cache.clear()
    voice._last_warn.clear()
    yield
    voice._transport = None
    voice._cache.clear()


def use(handler):
    """Install a mock server whose body is a plain function request -> Response; returns the call list."""
    calls: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    voice._transport = httpx.MockTransport(serve)
    return calls


def ok_tts(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/tts"
    assert request.method == "POST"
    return httpx.Response(200, content=WAV, headers={"Content-Type": "audio/wav"})


def test_synthesize_returns_wav_bytes():
    calls = use(ok_tts)
    out = asyncio.run(voice.synthesize("Check the field for bleeding."))
    assert out == WAV
    assert out[:4] == b"RIFF"
    assert len(calls) == 1
    with wave.open(io.BytesIO(out), "rb") as wf:
        assert wf.getsampwidth() == 2 and wf.getframerate() == 22050


def test_synthesize_none_on_connection_error():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    use(down)
    assert asyncio.run(voice.synthesize("hello")) is None


def test_synthesize_none_on_timeout():
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    use(slow)
    assert asyncio.run(voice.synthesize("hello")) is None


def test_synthesize_none_on_server_error_and_bad_input():
    calls = use(lambda r: httpx.Response(500, json={"error": "boom"}))
    assert asyncio.run(voice.synthesize("hello")) is None
    assert asyncio.run(voice.synthesize("")) is None
    assert asyncio.run(voice.synthesize("x" * (voice.MAX_TEXT + 1))) is None
    assert len(calls) == 1  # empty / oversize text never reach the service


def test_synthesize_caches_identical_text():
    calls = use(ok_tts)

    async def go():
        a = await voice.synthesize("Confirm the drug timing.")
        b = await voice.synthesize("Confirm the drug timing.")
        c = await voice.synthesize("Different sentence.")
        return a, b, c

    a, b, c = asyncio.run(go())
    assert a == b == c == WAV
    assert len(calls) == 2  # second identical call served from cache


def test_cache_is_bounded_lru():
    cache = voice._Cache(2)
    cache.put("a", b"1")
    cache.put("b", b"2")
    assert cache.get("a") == b"1"  # touch a so b is the oldest
    cache.put("c", b"3")
    assert cache.get("b") is None and cache.get("a") == b"1" and cache.get("c") == b"3"


def test_synthesize_disabled_by_env(monkeypatch):
    calls = use(ok_tts)
    monkeypatch.setenv("SENTINEL_TTS", "0")
    assert not voice.tts_enabled()
    assert asyncio.run(voice.synthesize("hello")) is None
    assert calls == []


def test_warning_is_rate_limited(caplog):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    use(down)
    with caplog.at_level("WARNING", logger="sentinel.voice"):
        for _ in range(5):
            asyncio.run(voice.synthesize("hello"))
    assert sum("tts unavailable" in r.message for r in caplog.records) == 1


def test_tts_health_up_and_down():
    use(lambda r: httpx.Response(200, json={"ok": True, "voice": "en_US-lessac-medium", "engine": "piper"}))
    h = asyncio.run(voice.tts_health())
    assert h["up"] is True and h["voice"] == "en_US-lessac-medium" and h["url"] == voice.TTS_URL

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    use(down)
    h = asyncio.run(voice.tts_health())
    assert h == {"up": False, "voice": None, "url": voice.TTS_URL, "enabled": True}


def test_transcribe_text_and_none_on_failure():
    calls = use(lambda r: httpx.Response(200, json={"text": "what is the blood pressure", "latency_ms": 300}))
    assert asyncio.run(voice.transcribe(WAV)) == "what is the blood pressure"
    assert calls[0].url.path == "/transcribe" and calls[0].content == WAV
    use(lambda r: httpx.Response(503, json={"error": "stt unavailable"}))
    assert asyncio.run(voice.transcribe(WAV)) is None
    assert asyncio.run(voice.transcribe(b"")) is None


def app_client() -> TestClient:
    app = FastAPI()
    app.include_router(voice.router)
    return TestClient(app)


def test_router_tts_returns_wav():
    use(ok_tts)
    r = app_client().get("/tts", params={"text": "Check the field."})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == WAV


def test_router_returns_503_when_synthesis_fails():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    use(down)
    c = app_client()
    r = c.get("/tts", params={"text": "Check the field."})
    assert r.status_code == 503 and r.json() == {"error": "tts unavailable"}
    assert c.get("/tts").status_code == 422  # text is required
    h = c.get("/tts/health").json()
    assert h["up"] is False and h["url"] == voice.TTS_URL


def test_router_transcribe():
    use(lambda r: httpx.Response(200, json={"text": "what is the blood pressure", "latency_ms": 1}))
    c = app_client()
    r = c.post("/transcribe", content=WAV, headers={"Content-Type": "audio/wav"})
    assert r.status_code == 200 and r.json() == {"text": "what is the blood pressure"}
    assert c.post("/transcribe", content=b"xx").status_code == 400

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    use(down)
    assert c.post("/transcribe", content=WAV).status_code == 503


def test_answer_stub_and_status():
    assert asyncio.run(voice.answer(WAV)) is None
    assert isinstance(voice.STATUS, str) and voice.STATUS
