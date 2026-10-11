#!/usr/bin/env bash
# Step 0: verify live scope ingest path on the GX10 (honest capture, not file loop).
# Usage: bash scripts/step0_live_ingest.sh [/dev/video0]
set -euo pipefail
DEV="${1:-/dev/video0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"
[ -x "$PY" ] || PY=python3

echo "=== 1. Capture device (need HDMI/USB capture FROM tower → GX10, not the portable monitor alone) ==="
if [ -e "$DEV" ]; then
  ls -la "$DEV"
  command -v v4l2-ctl >/dev/null && v4l2-ctl -d "$DEV" --all 2>/dev/null | head -40 || true
else
  echo "MISSING: $DEV — plug capture card/dongle; portable monitor on GX10 HDMI is OUTPUT only."
  ls -la /dev/video* 2>/dev/null || echo "No /dev/video* nodes on this machine."
fi

echo ""
echo "=== 2. One OpenCV frame from $DEV ==="
SENTINEL_VIDEO="$DEV" "$PY" - <<'PY' || true
import os, sys
import cv2
dev = os.environ["SENTINEL_VIDEO"]
cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
if not cap.isOpened():
    cap = cv2.VideoCapture(dev)
if not cap.isOpened():
    sys.exit("OpenCV could not open " + dev)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
ok, frame = cap.read()
cap.release()
if not ok or frame is None:
    sys.exit("opened but no frame — check tower output / capture format")
print(f"OK: shape={frame.shape} mean={float(frame.mean()):.1f}")
PY

echo ""
echo "=== 3. Stack health (start vision + backend with live env first) ==="
curl -sf localhost:7000/health | "$PY" -c "import sys,json; d=json.load(sys.stdin); v=d.get('video',{}); print('video:', v.get('label')); print('live_capture:', v.get('live_capture')); print('vision up:', d.get('vision',{}).get('up'))" 2>/dev/null \
  || echo "Backend not on :7000 — restart with: SENTINEL_VIDEO=$DEV bash scripts/dev.sh"

echo ""
echo "=== 4. Surgeon display (portable monitor on GX10) ==="
echo "Open fullscreen: http://$(hostname -I | awk '{print $1}'):5173/surgical on the portable monitor."
echo "Start playback on /control (Play) so the clock and models run."
