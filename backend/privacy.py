"""Display-only outline view for the surgeon screen.

The vision service and the agents receive the original frame from the video worker.
/video.mjpg?privacy=1 sends a near-black copy with white contours so shapes and bumps still read.
The HUD is drawn on top of that copy.
"""
from __future__ import annotations

import cv2
import numpy as np

RELIEF_CAP = 56
OUTLINE = 236


def outline_surgical_frame(frame: np.ndarray) -> np.ndarray:
    """Black field, white edges, a little gray on slopes so bumps stay visible."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 28, 84)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8))
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    relief = cv2.magnitude(gx, gy)
    peak = float(relief.max()) or 1.0
    shade = np.clip(relief / peak * RELIEF_CAP, 0, RELIEF_CAP).astype(np.uint8)
    shade = np.where(edges > 0, OUTLINE, shade)
    return cv2.cvtColor(shade, cv2.COLOR_GRAY2BGR)
