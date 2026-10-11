"""Video decode -> vision client mailbox -> JPEG -> latest frame for /video.mjpg.

Two threads joined by a bounded queue. The decoder paces itself to the source
FPS and drops the oldest frame when the renderer is behind (hard rule 3), so
playback never waits for anything downstream. With no video file the worker
publishes a plain test pattern that says "NO VIDEO LOADED" (never fake surgery).
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np

from .scenario import Scenario

log = logging.getLogger("sentinel.video")

TEST_PATTERN_FPS = 5.0
DEFAULT_FPS = 25.0
BLACK_RUN_FRAMES = 15  # this many consecutive near-black frames are reported as "black_frames"
BLACK_MEAN = 4.0
Frame = tuple[int, np.ndarray]


FrameSink = Callable[[int, np.ndarray], None]


def is_live_capture(path: Path | str) -> bool:
    """True for V4L2 device nodes (/dev/video*)."""
    return str(path).startswith("/dev/video")


def resolve_video(scenario: Optional[Scenario], repo_dir: Path) -> tuple[Optional[Path], str]:
    """Video path from $SENTINEL_VIDEO, else the scenario's `video.file`. Returns (path, note)."""
    env = os.environ.get("SENTINEL_VIDEO")
    if env:
        p = Path(env).expanduser()
        if is_live_capture(p):
            present = p.exists()
            return (p, f"live capture {p}" + ("" if present else " · device not present"))
        return (p, f"SENTINEL_VIDEO={p}") if p.exists() else (None, f"SENTINEL_VIDEO not found: {p}")
    rel = (scenario.video.get("file") if scenario else None) or None
    if rel:
        assets = Path(os.environ.get("SENTINEL_ASSETS", repo_dir.parent / "assets"))
        for base in (assets, repo_dir):
            if (base / rel).is_file():
                return base / rel, f"scenario video {rel}"
        return None, f"scenario video not found: {rel}"
    return None, "no video file configured (set SENTINEL_VIDEO)"


def make_test_pattern(width: int = 1280, height: int = 720) -> np.ndarray:
    img = np.full((height, width, 3), 32, dtype=np.uint8)
    for x in range(0, width, 80):
        cv2.line(img, (x, 0), (x, height), (48, 48, 48), 1)
    for y in range(0, height, 80):
        cv2.line(img, (0, y), (width, y), (48, 48, 48), 1)
    font = cv2.FONT_HERSHEY_SIMPLEX
    text = "NO VIDEO LOADED"
    (tw, th), _ = cv2.getTextSize(text, font, 2.0, 4)
    cv2.putText(img, text, ((width - tw) // 2, (height + th) // 2), font, 2.0, (200, 200, 200), 4, cv2.LINE_AA)
    sub = "Test pattern - set SENTINEL_VIDEO to a Cholec80 mp4 or HoloHub surgical_video.264"
    (sw, _), _ = cv2.getTextSize(sub, font, 0.7, 2)
    cv2.putText(img, sub, ((width - sw) // 2, (height + th) // 2 + 60), font, 0.7, (140, 140, 140), 2, cv2.LINE_AA)
    return img


class VideoWorker:
    def __init__(self, repo_dir: Path, jpeg_quality: int = 80, queue_size: int = 2) -> None:
        self.repo_dir = repo_dir
        self.jpeg_quality = jpeg_quality
        self._queue: queue.Queue[Frame] = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._playing = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[tuple[int, bytes]] = None
        self._render_times: deque[float] = deque(maxlen=256)
        self._rewind_to: Optional[Scenario] = None
        self._rewind_pending = threading.Event()
        self._rewind_pending.set()
        self._threads: list[threading.Thread] = []
        self.source: Optional[Path] = None
        self.source_note = ""
        self.source_fps: Optional[float] = None
        self.frame_id = 0
        self.dropped = 0
        self.frames_decoded = 0  # real frames decoded from the file since it was (re)opened
        self.decode_error = ""  # why the file shows nothing, when it opens but yields no frames
        self.mean_brightness: Optional[float] = None  # of the last rendered frame, 0 (black) to 255
        self._black_run = 0
        self.live_capture = False
        self.on_frame: Optional[FrameSink] = None  # the vision client's mailbox; never blocks

    # ---- control (called from the event loop) --------------------------------
    def start(self) -> None:
        self._threads = [threading.Thread(target=self._decode_loop, name="video-decode", daemon=True),
                         threading.Thread(target=self._render_loop, name="video-render", daemon=True)]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)

    def set_playing(self, playing: bool) -> None:
        (self._playing.set if playing else self._playing.clear)()

    def rewind(self, scenario: Scenario) -> None:
        self._rewind_to = scenario
        self._rewind_pending.set()

    def latest(self) -> Optional[tuple[int, bytes]]:
        with self._lock:
            return self._latest

    def render_fps(self) -> Optional[float]:
        if not self._render_times:
            return None
        now = time.monotonic()
        recent = [t for t in self._render_times if now - t <= 2.0]
        return round(len(recent) / 2.0, 1)

    def stats(self) -> dict[str, Any]:
        return {"source": str(self.source) if self.source else None, "label": self.source_note,
                "test_pattern": self.source is None, "live_capture": self.live_capture,
                "frame_id": self.frame_id, "source_fps": self.source_fps, "dropped_frames": self.dropped,
                "frames_decoded": self.frames_decoded, "decode_error": self.decode_error,
                "mean_brightness": self.mean_brightness, "black_frames": self._black_run >= BLACK_RUN_FRAMES}

    # ---- threads ---------------------------------------------------------------
    def _open(self) -> Optional[cv2.VideoCapture]:
        self._rewind_pending.clear()
        self.frames_decoded = 0
        self.decode_error = ""
        scenario = self._rewind_to
        path, note = resolve_video(scenario, self.repo_dir)
        self.source, self.source_note = path, note
        self.live_capture = path is not None and is_live_capture(path)
        if path is None:
            self.source_fps = TEST_PATTERN_FPS
            log.info("video: test pattern (%s)", note)
            return None
        if self.live_capture and not path.exists():
            self.source, self.source_note = None, f"live capture device missing: {path}"
            self.source_fps = TEST_PATTERN_FPS
            log.warning("video: %s", self.source_note)
            return None
        cap = cv2.VideoCapture(str(path), cv2.CAP_V4L2) if self.live_capture else cv2.VideoCapture(str(path))
        if not cap.isOpened() and self.live_capture:
            cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            self.source, self.source_note = None, f"could not open {path}"
            self.source_fps = TEST_PATTERN_FPS
            log.warning("video: %s", self.source_note)
            return None
        if self.live_capture:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            env_fps = os.environ.get("SENTINEL_CAPTURE_FPS")
            self.source_fps = float(env_fps) if env_fps else DEFAULT_FPS
        else:
            fps = cap.get(cv2.CAP_PROP_FPS)
            self.source_fps = float(fps) if fps and 1 <= fps <= 240 else DEFAULT_FPS
            start_s = scenario.video.get("start_s") if scenario else None
            if isinstance(start_s, (int, float)):
                cap.set(cv2.CAP_PROP_POS_MSEC, float(start_s) * 1000.0)
        log.info("video: %s at %.1f fps%s", path, self.source_fps, " (live)" if self.live_capture else "")
        return cap

    def _push(self, frame: np.ndarray) -> None:
        self.frame_id += 1
        item = (self.frame_id, frame)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            try:
                self._queue.get_nowait()  # drop the oldest; the renderer is behind
                self.dropped += 1
            except queue.Empty:
                pass
            self._queue.put_nowait(item)

    def _decode_loop(self) -> None:
        cap: Optional[cv2.VideoCapture] = None
        pattern = make_test_pattern()
        next_due = time.monotonic()
        while not self._stop.is_set():
            if self._rewind_pending.is_set():
                if cap is not None:
                    cap.release()
                cap = self._open()
                frame = pattern if cap is None else self._read(cap)
                if frame is not None:
                    if cap is not None:
                        self.frames_decoded += 1
                    self._push(frame)  # show the first frame even while paused
                next_due = time.monotonic()
            if not self._playing.wait(timeout=0.05):
                continue
            period = 1.0 / (self.source_fps or DEFAULT_FPS)
            now = time.monotonic()
            if now < next_due:
                time.sleep(min(next_due - now, 0.05))
                continue
            next_due = next_due + period if now - next_due < period else now + period
            if cap is None:
                self._push(pattern)
                continue
            frame = self._read(cap)
            if frame is None:
                if self.live_capture:
                    time.sleep(0.02)
                    continue
                if self.frames_decoded == 0:
                    # Opens but never yields a frame (codec or container the build of OpenCV cannot decode):
                    # say so, show the test pattern, and stop re-opening in a tight loop.
                    self.decode_error = (f"{self.source} opens but no frame could be decoded. "
                                         "Convert it: ffmpeg -i <file> -c:v libx264 -pix_fmt yuv420p out.mp4")
                    self.source_note = self.decode_error
                    log.warning("video: %s", self.decode_error)
                    cap.release()
                    cap = None
                    self.source = None
                    self.source_fps = TEST_PATTERN_FPS
                    continue
                self._rewind_pending.set()  # end of file: loop from the segment start
                continue
            self.frames_decoded += 1
            self._push(frame)
        if cap is not None:
            cap.release()

    @staticmethod
    def _read(cap: cv2.VideoCapture) -> Optional[np.ndarray]:
        ok, frame = cap.read()
        return frame if ok else None

    def _render_loop(self) -> None:
        params = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        while not self._stop.is_set():
            try:
                frame_id, frame = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if self.on_frame is not None and self.source is not None:
                self.on_frame(frame_id, frame)  # must return at once (VisionClient.submit)
            if self.source is not None:  # only real footage is checked for black frames
                self.mean_brightness = round(float(frame[::16, ::16].mean()), 1)
                self._black_run = self._black_run + 1 if self.mean_brightness < BLACK_MEAN else 0
            else:
                self._black_run = 0
            ok, buf = cv2.imencode(".jpg", frame, params)
            if not ok:
                continue
            with self._lock:
                self._latest = (frame_id, buf.tobytes())
            self._render_times.append(time.monotonic())
