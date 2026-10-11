"""Voice I/O for Sentinel-OR: offline text-to-speech (and speech-to-text) via the local
speech service in speech/tts_server.py (Piper on :8003, CPU only).

The app never depends on this service: `synthesize`/`transcribe` return None on any error
and the router answers 503, so a missing service only means silence.

Environment:
    SENTINEL_TTS_URL   base URL of the speech service (default http://127.0.0.1:8003)
    SENTINEL_TTS       "0"/"false"/"off" disables TTS entirely (default on)

Not yet wired: the full push-to-talk loop (record -> transcribe -> grounded answer -> speak,
with barge-in). `answer()` still returns None so existing callers keep working.
"""
from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from typing import Optional

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

log = logging.getLogger("sentinel.voice")

TTS_URL = os.environ.get("SENTINEL_TTS_URL", "http://127.0.0.1:8003").rstrip("/")
TIMEOUT_S = 4.0
STT_TIMEOUT_S = 15.0
CACHE_SIZE = 64
WARN_INTERVAL_S = 60.0
MAX_TEXT = 600

STATUS = "tts via piper (:8003); push-to-talk loop not wired"


def tts_enabled() -> bool:
    return os.environ.get("SENTINEL_TTS", "1").strip().lower() not in ("0", "false", "off", "no")


class _Cache:
    """Tiny LRU of text -> wav bytes so repeated alerts cost one synthesis."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._d: "OrderedDict[str, bytes]" = OrderedDict()

    def get(self, key: str) -> Optional[bytes]:
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v

    def put(self, key: str, value: bytes) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.size:
            self._d.popitem(last=False)

    def clear(self) -> None:
        self._d.clear()


_cache = _Cache(CACHE_SIZE)
_last_warn: dict[str, float] = {}

# Tests inject an httpx.MockTransport here; production leaves it None (real network).
_transport: Optional[httpx.BaseTransport] = None


def _warn(key: str, msg: str, *args) -> None:
    now = time.monotonic()
    if now - _last_warn.get(key, -WARN_INTERVAL_S) >= WARN_INTERVAL_S:
        _last_warn[key] = now
        log.warning(msg, *args)


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=TTS_URL, timeout=timeout, transport=_transport)


async def synthesize(text: str) -> Optional[bytes]:
    """Return 16-bit PCM wav bytes for `text`, or None if TTS is off, the text is unusable or the service fails."""
    if not tts_enabled():
        return None
    text = (text or "").strip()
    if not text or len(text) > MAX_TEXT:
        return None
    hit = _cache.get(text)
    if hit is not None:
        return hit
    try:
        async with _client(TIMEOUT_S) as c:
            r = await c.post("/tts", json={"text": text})
        if r.status_code != 200 or not r.content:
            _warn("tts", "tts service returned %s for %d chars", r.status_code, len(text))
            return None
        wav = r.content
    except Exception as exc:  # noqa: BLE001 - any transport/timeout error means "no audio"
        _warn("tts", "tts unavailable at %s: %s", TTS_URL, exc.__class__.__name__)
        return None
    _cache.put(text, wav)
    return wav


async def transcribe(wav: bytes) -> Optional[str]:
    """Return the recognised text for 16-bit PCM wav bytes, or None if STT is unavailable or fails."""
    if not wav:
        return None
    try:
        async with _client(STT_TIMEOUT_S) as c:
            r = await c.post("/transcribe", content=wav, headers={"Content-Type": "audio/wav"})
        if r.status_code != 200:
            _warn("stt", "stt service returned %s", r.status_code)
            return None
        text = r.json().get("text")
        return text if isinstance(text, str) else None
    except Exception as exc:  # noqa: BLE001
        _warn("stt", "stt unavailable at %s: %s", TTS_URL, exc.__class__.__name__)
        return None


async def tts_health() -> dict:
    out: dict = {"up": False, "voice": None, "url": TTS_URL, "enabled": tts_enabled()}
    try:
        async with _client(TIMEOUT_S) as c:
            r = await c.get("/health")
        if r.status_code == 200:
            body = r.json()
            out.update(up=bool(body.get("ok")), voice=body.get("voice"), stt=bool(body.get("stt", False)))
    except Exception:  # noqa: BLE001
        pass
    return out


async def answer(question_wav: bytes) -> Optional[dict]:
    """Push-to-talk loop is not wired yet: returns None; nothing is spoken."""
    return None


router = APIRouter(tags=["voice"])


@router.get("/tts")
async def tts_endpoint(text: str = Query(..., min_length=1, max_length=MAX_TEXT)):
    wav = await synthesize(text)
    if wav is None:
        return JSONResponse({"error": "tts unavailable"}, status_code=503)
    return Response(content=wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.get("/tts/health")
async def tts_health_endpoint():
    return await tts_health()


@router.post("/transcribe")
async def transcribe_endpoint(request: Request):
    wav = await request.body()
    if len(wav) < 44:
        return JSONResponse({"error": "body must be a wav file"}, status_code=400)
    text = await transcribe(wav)
    if text is None:
        return JSONResponse({"error": "stt unavailable"}, status_code=503)
    return {"text": text}
