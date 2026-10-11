"""Event-triggered question to Nemotron Nano Omni (vLLM, :8001) about the last 10 s of video.

Optional, and never on the alert path. The orchestrator awaits it for the surgical agent only,
with a hard timeout: on timeout, error, no video file, or answer with banned wording, the answer
is skipped and the surgical agent gets `omni: null`. When it arrives, the surgical evidence block
carries it as `omni-<event_id>`, and the UI labels it as a video model's description.

Request (CLAUDE.md stack table): OpenAI-compatible chat completion with a `video_url` data URI,
`mm_processor_kwargs: {"use_audio_in_video": false}` at the top level (Cholec80 has no useful
audio) and `chat_template_kwargs: {"enable_thinking": false}`. Free text answer, 120 tokens.

Environment:
  OMNI_URL        base URL including /v1  (default http://localhost:8001/v1)
  OMNI_MODEL      model id                (default: first id from GET /v1/models, resolved lazily)
  OMNI_TIMEOUT_S  seconds for the whole call including clip extraction (default 4)
  OMNI_ENABLED    0 turns the call off     (default 1; with no video file it is skipped anyway)

Unverified here: the exact `video_url` handling of the served Omni build. Only the request shape
is tested (backend/tests/test_omni.py), against a stand-in server, not against Omni.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Optional

import cv2
import httpx

from .agents.schemas import BANNED_PATTERNS
from .scenario import Scenario
from .state import Event
from .video_worker import resolve_video

log = logging.getLogger("sentinel.omni")

DEFAULT_URL = "http://localhost:8001/v1"
DEFAULT_TIMEOUT_S = 4.0
CLIP_S = 10.0
CLIP_FPS = 4.0
CLIP_WIDTH = 480
MAX_ANSWER_CHARS = 320
QUESTION = (
    "This is the last 10 seconds of a recorded laparoscopic cholecystectomy video. In one or two short "
    "sentences, say which instruments are in view and whether any blood or fluid is visible in the "
    "operative field. Describe only what is visible. Do not give a diagnosis or any advice."
)
_BANNED = [re.compile(p, re.IGNORECASE) for p in BANNED_PATTERNS]


def enabled() -> bool:
    return os.environ.get("OMNI_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def extract_clip(path: Path, start_s: float, length_s: float = CLIP_S, fps: float = CLIP_FPS,
                 width: int = CLIP_WIDTH) -> Optional[bytes]:
    """Re-encode `length_s` seconds of the file from `start_s` as a small mp4. None if it cannot be read."""
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        src_fps = cap.get(cv2.CAP_PROP_FPS)
        src_fps = src_fps if src_fps and 1 <= src_fps <= 240 else 25.0
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, start_s) * 1000.0)
        step = max(1, int(round(src_fps / fps)))
        frames = []
        for i in range(int(length_s * src_fps)):
            ok, frame = cap.read()
            if not ok:
                break
            if i % step == 0:
                h, w = frame.shape[:2]
                frames.append(cv2.resize(frame, (width, max(2, int(h * width / w) // 2 * 2))))
        if len(frames) < 2:
            return None
    finally:
        cap.release()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clip.mp4"
        h, w = frames[0].shape[:2]
        writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), src_fps / step, (w, h))  # real-time speed
        if not writer.isOpened():
            return None
        for f in frames:
            writer.write(f)
        writer.release()
        return out.read_bytes() if out.exists() else None


class OmniClient:
    """Callable used by the orchestrator: `await omni(event, scenario, at_s) -> Optional[str]`."""

    def __init__(self, repo_dir: Path, base_url: Optional[str] = None, model: Optional[str] = None,
                 timeout_s: Optional[float] = None, transport: Optional[httpx.AsyncBaseTransport] = None,
                 resolver: Callable[..., tuple[Optional[Path], str]] = resolve_video) -> None:
        self.repo_dir = repo_dir
        self.base_url = (base_url or os.environ.get("OMNI_URL") or DEFAULT_URL).rstrip("/")
        self._model = model or os.environ.get("OMNI_MODEL") or None
        self.timeout_s = float(timeout_s if timeout_s is not None else os.environ.get("OMNI_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
        self._http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(self.timeout_s))
        self._resolver = resolver

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __call__(self, event: Event, scenario: Scenario, at_s: float) -> Optional[str]:
        try:
            return await asyncio.wait_for(self._ask(scenario, at_s), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            log.warning("omni skipped for %s: timed out after %g s", event.event_id, self.timeout_s)
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
            log.warning("omni skipped for %s: %s: %s", event.event_id, type(exc).__name__, exc)
        return None

    async def _ask(self, scenario: Scenario, at_s: float) -> Optional[str]:
        path, note = self._resolver(scenario, self.repo_dir)
        if path is None:
            log.info("omni skipped: no video file (%s)", note)
            return None
        start_pos = float(scenario.video.get("start_s") or 0.0) + max(0.0, at_s - CLIP_S)
        clip = await asyncio.to_thread(extract_clip, path, start_pos)
        if clip is None:
            log.warning("omni skipped: could not cut a clip from %s at %.1f s", path, start_pos)
            return None
        if not self._model:
            res = await self._http.get(f"{self.base_url}/models")
            res.raise_for_status()
            self._model = str(res.json()["data"][0]["id"])
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": [
                {"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(clip).decode()}},
                {"type": "text", "text": QUESTION}]}],
            "temperature": 0.2,
            "max_tokens": 120,
            "mm_processor_kwargs": {"use_audio_in_video": False},
            "chat_template_kwargs": {"enable_thinking": False},
        }
        res = await self._http.post(f"{self.base_url}/chat/completions", json=body)
        res.raise_for_status()
        text = str(res.json()["choices"][0]["message"]["content"] or "").strip()
        return clean_answer(text)


def clean_answer(text: str) -> Optional[str]:
    """Hard rule 1 applies to model text on screen too: drop empty answers and answers with banned wording."""
    text = " ".join(text.split())[:MAX_ANSWER_CHARS]
    if not text:
        return None
    for pat in _BANNED:
        if pat.search(text):
            log.warning("omni answer dropped: banned wording /%s/", pat.pattern)
            return None
    return text
