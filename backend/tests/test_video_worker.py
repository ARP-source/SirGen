"""Video worker diagnostics: decoded-frame count, black-frame flag, files that open but decode nothing."""
import time

import cv2
import numpy as np

from backend import video_worker as vw
from backend.video_worker import VideoWorker


def write_clip(path, value, n=40, size=(160, 120), fps=20):
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(n):
        out.write(np.full((size[1], size[0], 3), value, dtype=np.uint8))
    out.release()


def run(tmp_path, monkeypatch, path, seconds=1.5):
    monkeypatch.setenv("SENTINEL_VIDEO", str(path))
    w = VideoWorker(tmp_path)
    w.start()
    w.set_playing(True)
    time.sleep(seconds)
    stats = w.stats()
    w.stop()
    return w, stats


def test_good_clip_counts_frames_and_is_not_black(tmp_path, monkeypatch):
    clip = tmp_path / "ok.mp4"
    write_clip(clip, 120)
    w, st = run(tmp_path, monkeypatch, clip)
    assert not st["test_pattern"] and st["frames_decoded"] > 5 and st["decode_error"] == ""
    assert 100 < st["mean_brightness"] < 140 and st["black_frames"] is False
    assert w.latest() is not None


def test_black_clip_is_flagged(tmp_path, monkeypatch):
    clip = tmp_path / "black.mp4"
    write_clip(clip, 0, n=80)
    _, st = run(tmp_path, monkeypatch, clip, seconds=2.0)
    assert st["frames_decoded"] > 5 and st["mean_brightness"] < 4 and st["black_frames"] is True


def test_file_that_opens_but_yields_nothing_falls_back_to_the_pattern_and_says_why(tmp_path, monkeypatch):
    class Dud:
        def __init__(self, *_): pass
        def isOpened(self): return True
        def get(self, *_): return 0.0
        def set(self, *_): return True
        def read(self): return False, None
        def release(self): pass
    monkeypatch.setattr(vw.cv2, "VideoCapture", Dud)
    clip = tmp_path / "x.264"
    clip.write_bytes(b"not a video")
    w, st = run(tmp_path, monkeypatch, clip)
    assert st["test_pattern"] and "no frame could be decoded" in st["decode_error"] and "ffmpeg" in st["decode_error"]
    assert st["frames_decoded"] == 0 and w.latest() is not None  # the test pattern is being shown, not a black page


def test_video_problems_reach_the_state(tmp_path, monkeypatch):
    from backend.state import VideoInfo
    clip = tmp_path / "black.mp4"
    write_clip(clip, 0, n=80)
    _, st = run(tmp_path, monkeypatch, clip, seconds=2.0)
    info = VideoInfo(**st)
    assert info.black_frames is True and info.mean_brightness is not None and info.frames_decoded > 5
