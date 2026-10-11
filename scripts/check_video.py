#!/usr/bin/env python3
"""Why is /surgical black? Open the video the way the backend does and say what happens.

    python3 scripts/check_video.py ~/assets/data/holoscan_sample/surgical_video.264
    python3 scripts/check_video.py <file> --convert        # writes <file>.mp4 with ffmpeg if decoding fails

Use the same Python as the backend (the .venv one if there is one). Prints: can OpenCV open it, fps, frame count,
whether frames decode, and how bright they are (a mean near 0 means the frames really are black).
"""
import shutil
import subprocess
import sys
from pathlib import Path

import cv2


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1]).expanduser()
    print(f"file: {path} exists={path.is_file()} size={path.stat().st_size if path.is_file() else 'n/a'} bytes")
    if not path.is_file():
        print("RESULT: the file is not there. Check SENTINEL_VIDEO is set for the backend and the path is right.")
        return 1
    cap = cv2.VideoCapture(str(path))
    print(f"opened={cap.isOpened()} fps={cap.get(cv2.CAP_PROP_FPS)} frames={cap.get(cv2.CAP_PROP_FRAME_COUNT)} "
          f"size={cap.get(cv2.CAP_PROP_FRAME_WIDTH)}x{cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}")
    means, ok_count = [], 0
    for i in range(60):
        ok, frame = cap.read()
        if not ok:
            break
        ok_count += 1
        if i % 10 == 0:
            means.append(round(float(frame.mean()), 1))
    print(f"decoded {ok_count} of the first 60 frames; mean brightness of every 10th: {means}")
    if not cap.isOpened() or ok_count == 0:
        print("RESULT: OpenCV cannot decode this file. Convert it to mp4 (see below) and set SENTINEL_VIDEO to the mp4.")
        verdict = 1
    elif max(means, default=0) < 4:
        print("RESULT: frames decode but they are black. Try a later start point or another clip; check ffprobe.")
        verdict = 1
    else:
        print("RESULT: decodes and has picture. If /surgical is still black, the backend is not using this file "
              "(check SENTINEL_VIDEO in the dev.sh window and curl localhost:7000/health, 'video' block) "
              "or the page needs Play pressed on /control.")
        verdict = 0
    if verdict and shutil.which("ffprobe"):
        print("\nffprobe:")
        subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,pix_fmt,width,height,r_frame_rate",
                        "-of", "default=nw=1", str(path)])
    out = path.with_suffix(".mp4")
    cmd = ["ffmpeg", "-y", "-i", str(path), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out)]
    if verdict:
        print("\nconvert command:", " ".join(cmd))
        if "--convert" in sys.argv:
            if not shutil.which("ffmpeg"):
                print("ffmpeg is not installed (sudo apt install ffmpeg)")
            else:
                subprocess.run(cmd)
                print(f"wrote {out}; now restart the backend with SENTINEL_VIDEO={out}")
    return verdict


if __name__ == "__main__":
    sys.exit(main())
