"""Client for the local vision service (vision/server.py, default 127.0.0.1:8004).

The video render loop hands every frame to `submit()`, which only replaces a
one-slot mailbox and returns at once (hard rule 3: video never waits). A worker
thread sends the newest frame to the service, passes the result to the
LiveScene, and draws the boxes on a copy of that same frame (hard rule 4),
which /video.mjpg?boxes=1 serves. If the service stops answering, the scene
goes stale and the boxed stream falls back to plain frames.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any, Optional

import cv2
import httpx
import numpy as np

from .scene import ANATOMY_NAMES, LiveScene, compute_caution

log = logging.getLogger("sentinel.vision")

VISION_URL = os.environ.get("SENTINEL_VISION_URL", "http://127.0.0.1:8004")
TIMEOUT_S = float(os.environ.get("SENTINEL_VISION_TIMEOUT_S", "3"))
SEND_WIDTH = 854
RETRY_S = 2.0
MAX_FPS = float(os.environ.get("SENTINEL_VISION_MAX_FPS", "12"))  # leaves GPU time for the agents' LLM
TOOL_COLOR = (80, 200, 255)
ANATOMY_COLORS = {"cystic_duct": (60, 220, 60), "cystic_artery": (60, 60, 240), "calot_triangle": (230, 200, 40),
                  "cystic_plate": (200, 120, 230), "gallbladder": (40, 160, 200), "tool": (150, 150, 150)}
# Short names for the on-frame tags. The anatomy class "tool" is the detector's
# own instrument box; the instrument model draws those, so it is not drawn twice.
SIGHT_NAMES = {"cystic_plate": "cystic plate", "calot_triangle": "Calot triangle",
               "cystic_artery": "cystic artery", "cystic_duct": "cystic duct", "gallbladder": "gallbladder"}
TOOL_NAMES = {"Grasper": "grasper", "Bipolar": "bipolar", "Hook": "hook", "Scissors": "scissors",
              "Clipper": "clipper", "Irrigator": "irrigator", "SpecimenBag": "specimen bag"}


def enabled() -> bool:
    return os.environ.get("SENTINEL_VISION", "1") not in ("0", "false", "off")


def _tag(img: np.ndarray, text: str, x: int, y: int, color: tuple[int, int, int]) -> None:
    """A small name tag sitting on the top edge of a box, kept inside the frame."""
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale, thick = 0.55, 1
    (tw, th), base = cv2.getTextSize(text, font, scale, thick)
    h, w = img.shape[:2]
    x = int(max(0, min(x, w - tw - 8)))
    y = int(max(th + 6, min(y, h - base - 2)))
    cv2.rectangle(img, (x, y - th - 6), (x + tw + 8, y + base + 2), (12, 12, 12), -1)
    cv2.rectangle(img, (x, y - th - 6), (x + tw + 8, y + base + 2), color, 1, cv2.LINE_AA)
    cv2.putText(img, text, (x + 4, y), font, scale, (255, 255, 255), thick, cv2.LINE_AA)


def draw(frame: np.ndarray, res: dict[str, Any], scale: float) -> np.ndarray:
    """Boxes and names for AI sight. The caution estimate stays on the side rail, not on the field."""
    out = frame.copy()
    anatomy = [d for d in (res.get("anatomy") or []) if d.get("cls") not in ("tool",)]
    anatomy.sort(key=lambda d: abs((d["box"][2] - d["box"][0]) * (d["box"][3] - d["box"][1])), reverse=True)
    for d in anatomy:
        x1, y1, x2, y2 = (int(v * scale) for v in d["box"])
        c = ANATOMY_COLORS.get(d["cls"], (200, 200, 200))
        cv2.rectangle(out, (x1, y1), (x2, y2), c, 2)
        _tag(out, SIGHT_NAMES.get(d["cls"], ANATOMY_NAMES.get(d["cls"], d["cls"])), x1, y1 - 2, c)
    for d in res.get("tools") or []:
        x1, y1, x2, y2 = (int(v * scale) for v in d["box"])
        cv2.rectangle(out, (x1, y1), (x2, y2), TOOL_COLOR, 2)
        _tag(out, TOOL_NAMES.get(d["cls"], d["cls"]), x1, y1 - 2, TOOL_COLOR)
    return out


class VisionClient:
    def __init__(self, scene: LiveScene, clock: Callable[[], float], jpeg_quality: int = 80,
                 on_status: Optional[Callable[[str, dict[str, Any]], None]] = None) -> None:
        self.scene = scene
        self.clock = clock
        self.jpeg_quality = jpeg_quality
        self.on_status = on_status
        self._slot: Optional[tuple[int, np.ndarray]] = None
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._annotated: Optional[tuple[int, bytes]] = None
        self._thread: Optional[threading.Thread] = None
        self._sent_id = -1
        self.up = False
        self.error = ""
        self.health: dict[str, Any] = {}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="vision-client", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=2)

    def submit(self, frame_id: int, frame: np.ndarray) -> None:
        with self._cv:
            self._slot = (frame_id, frame)
            self._cv.notify()

    def annotated(self) -> Optional[tuple[int, bytes]]:
        with self._lock:
            return self._annotated if self.up else None

    def _set_up(self, up: bool, error: str = "") -> None:
        changed = up != self.up
        self.up, self.error = up, error
        if changed and self.on_status is not None:
            self.on_status("vision service connected" if up else f"vision service unavailable: {error}",
                           {"url": VISION_URL, "models": {k: v.get("loaded") for k, v in self.health.get("models", {}).items()}})

    def _connect(self, client: httpx.Client) -> bool:
        try:
            self.health = client.get(f"{VISION_URL}/health", timeout=TIMEOUT_S).json()
            self.scene.set_service(self.health.get("models", {}), "vision service connected")
            self._set_up(True)
            return True
        except Exception as exc:
            self.scene.set_service({}, f"vision service not reachable at {VISION_URL}")
            self._set_up(False, type(exc).__name__)
            return False

    def _loop(self) -> None:
        params = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        with httpx.Client() as client:
            while not self._stop.is_set():
                if not self.up and not self._connect(client):
                    self._stop.wait(RETRY_S)
                    continue
                with self._cv:
                    while not self._stop.is_set() and (self._slot is None or self._slot[0] == self._sent_id):
                        self._cv.wait(timeout=0.5)
                    if self._stop.is_set():
                        break
                    frame_id, frame = self._slot  # type: ignore[misc]
                self._sent_id = frame_id
                started = time.monotonic()
                scale = 1.0
                small = frame
                if frame.shape[1] > SEND_WIDTH:
                    scale = frame.shape[1] / SEND_WIDTH
                    small = cv2.resize(frame, (SEND_WIDTH, int(frame.shape[0] / scale)), interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(".jpg", small, params)
                if not ok:
                    continue
                try:
                    r = client.post(f"{VISION_URL}/infer", content=buf.tobytes(),
                                    headers={"X-Frame-Id": str(frame_id)}, timeout=TIMEOUT_S)
                    r.raise_for_status()
                    res = r.json()
                except Exception as exc:
                    log.warning("vision: %s", exc)
                    self._set_up(False, type(exc).__name__)
                    continue
                res["caution"] = compute_caution(res, small.shape[1], small.shape[0])
                self.scene.add(res, self.clock(), time.monotonic())
                ok, jpg = cv2.imencode(".jpg", draw(frame, res, scale), params)
                if ok:
                    with self._lock:
                        self._annotated = (frame_id, jpg.tobytes())
                if MAX_FPS > 0:
                    self._stop.wait(max(0.0, 1.0 / MAX_FPS - (time.monotonic() - started)))
