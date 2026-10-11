#!/usr/bin/env python
"""Cut one continuous demo clip around the first frame of a Cholec80 phase.

    python scripts/cut_clip.py --video video13.mp4 --phases video13-phase.txt \
        --out ~/sentinel-demo/video13_dissect_to_clip_90s.mp4 --before 50 --after 40 --phase ClippingCutting

The clip starts `--before` seconds before the first frame labelled `--phase` and ends
`--after` seconds after it, as one uncut stretch of the source video (no stitching).
Encoding uses the ffmpeg binary bundled with imageio-ffmpeg (libx264, crf 18, source
fps and size, no audio); if that is unavailable or fails it falls back to OpenCV's
VideoWriter with mp4v. A JSON sidecar (`--info`, default <out dir>/clip_info.json)
records start/end seconds, the phase labels at both ends and the clip-relative second
at which the phase begins. `--verify-only` re-opens an existing clip with cv2 and
prints its properties (run that with the app venv to check the app can play it).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np


def read_phases(path: Path) -> list[str]:
    labels: list[str] = []
    with path.open() as fh:
        header = fh.readline()
        if not header.lower().startswith("frame"):
            fh.seek(0)
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                labels.append(parts[1])
    return labels


def video_props(path: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {path}")
    props = {"frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), "fps": float(cap.get(cv2.CAP_PROP_FPS)),
             "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}
    cap.release()
    return props


def cut_ffmpeg(video: Path, out: Path, start_s: float, n_frames: int, fps: float, w: int, h: int) -> None:
    import imageio_ffmpeg

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{start_s:.3f}", "-i", str(video),
           "-frames:v", str(n_frames), "-an",
           "-vf", f"fps={fps:g},scale={w}:{h}",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(out)]
    print("ffmpeg:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def cut_opencv(video: Path, out: Path, start_frame: int, n_frames: int, fps: float, w: int, h: int) -> None:
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        raise SystemExit("OpenCV VideoWriter could not open output")
    written = 0
    while written < n_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if frame.shape[1] != w or frame.shape[0] != h:
            frame = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        writer.write(frame)
        written += 1
    writer.release()
    cap.release()
    print(f"opencv: wrote {written} frames", flush=True)


def verify(out: Path) -> dict:
    cap = cv2.VideoCapture(str(out))
    if not cap.isOpened():
        raise SystemExit(f"verify: cannot open {out}")
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    # Decode every frame: CAP_PROP_FRAME_COUNT is a container header value, the decode count is the truth.
    decoded = 0
    means: dict[str, float | None] = {"first": None, "middle": None, "last": None}
    last = None
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if decoded == 0:
            means["first"] = float(frame.mean())
        if decoded == n // 2:
            means["middle"] = float(frame.mean())
        last = frame
        decoded += 1
    if last is not None:
        means["last"] = float(last.mean())
    cap.release()
    res = {"path": str(out), "bytes": out.stat().st_size, "frame_count_header": n, "frames_decoded": decoded,
           "fps": fps, "width": w, "height": h, "duration_s": decoded / fps if fps else None,
           "mean_brightness": means,
           "not_black": all(m is not None and m > 20 for m in means.values()),
           "opencv": cv2.__version__, "python": sys.executable}
    print(json.dumps(res, indent=2))
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path, help="source video (required unless --verify-only)")
    ap.add_argument("--phases", type=Path, help="Cholec80 frame-level phase file (Frame<TAB>Phase)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--before", type=float, default=50.0, help="seconds before the first frame of --phase")
    ap.add_argument("--after", type=float, default=40.0, help="seconds after the first frame of --phase")
    ap.add_argument("--phase", default="ClippingCutting")
    ap.add_argument("--info", type=Path, help="JSON sidecar path (default: <out dir>/clip_info.json)")
    ap.add_argument("--encoder", choices=["auto", "ffmpeg", "opencv"], default="auto")
    ap.add_argument("--verify-only", action="store_true", help="only re-open --out and print its properties")
    args = ap.parse_args()

    if args.verify_only:
        verify(args.out)
        return
    if args.video is None or args.phases is None:
        ap.error("--video and --phases are required unless --verify-only")

    labels = read_phases(args.phases)
    props = video_props(args.video)
    fps = props["fps"] or 25.0
    w, h = props["width"], props["height"]
    n_labels = len(labels)
    n_video = props["frames"]
    if abs(n_labels - n_video) > fps:
        print(f"warning: {n_labels} phase labels vs {n_video} video frames", file=sys.stderr)

    try:
        first = labels.index(args.phase)
    except ValueError:
        raise SystemExit(f"phase {args.phase!r} does not occur in {args.phases}")
    anchor_s = first / fps
    start_frame = max(0, int(round((anchor_s - args.before) * fps)))
    end_frame = min(n_video, n_labels, int(round((anchor_s + args.after) * fps)))  # exclusive
    n_frames = end_frame - start_frame
    if n_frames <= 0:
        raise SystemExit("empty clip")
    start_s, end_s = start_frame / fps, end_frame / fps
    print(f"{args.phase} first frame {first} ({anchor_s:.3f} s); clip frames [{start_frame}, {end_frame}) "
          f"= {start_s:.3f}-{end_s:.3f} s, {n_frames} frames at {fps:g} fps, {w}x{h}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    encoder_used = None
    if args.encoder in ("auto", "ffmpeg"):
        try:
            cut_ffmpeg(args.video, args.out, start_s, n_frames, fps, w, h)
            encoder_used = "ffmpeg(libx264 crf18)"
        except Exception as exc:  # noqa: BLE001 - fall back to OpenCV on any ffmpeg problem
            print(f"ffmpeg failed: {exc!r}", file=sys.stderr)
            if args.encoder == "ffmpeg":
                raise
    if encoder_used is None:
        cut_opencv(args.video, args.out, start_frame, n_frames, fps, w, h)
        encoder_used = "opencv(mp4v)"

    res = verify(args.out)
    phases_in_clip: list[dict] = []
    for i in range(start_frame, end_frame):
        p = labels[i]
        if not phases_in_clip or phases_in_clip[-1]["phase"] != p:
            phases_in_clip.append({"phase": p, "clip_s": round((i - start_frame) / fps, 3), "video_s": round(i / fps, 3)})
    info = {
        "source_video": str(args.video), "phase_file": str(args.phases), "clip": str(args.out),
        "encoder": encoder_used, "fps": fps, "width": w, "height": h,
        "start_s": round(start_s, 3), "end_s": round(end_s, 3), "duration_s": round(n_frames / fps, 3),
        "start_frame": start_frame, "end_frame_exclusive": end_frame, "n_frames": n_frames,
        "phase_at_start": labels[start_frame], "phase_at_end": labels[end_frame - 1],
        "anchor_phase": args.phase, "anchor_first_frame": first, "anchor_video_s": round(anchor_s, 3),
        f"{args.phase}_begins_at_clip_s": round((first - start_frame) / fps, 3),
        "phase_segments_in_clip": phases_in_clip,
        "verified": {k: res[k] for k in ("frames_decoded", "fps", "width", "height", "duration_s",
                                           "mean_brightness", "not_black")},
    }
    info_path = args.info or args.out.parent / "clip_info.json"
    info_path.write_text(json.dumps(info, indent=2) + "\n")
    print(f"wrote {info_path}")


if __name__ == "__main__":
    main()
