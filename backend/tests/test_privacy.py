"""Outline view is mostly black, with white edges where the tissue changes."""
import cv2
import numpy as np

from backend.privacy import outline_surgical_frame


def test_outline_is_black_with_a_white_edge():
    frame = np.zeros((80, 160, 3), dtype=np.uint8)
    frame[:, :80] = (30, 40, 90)
    frame[:, 80:] = (40, 160, 170)
    cv2.circle(frame, (40, 40), 18, (80, 90, 200), -1)
    out = outline_surgical_frame(frame)
    assert out.shape == frame.shape
    assert float(out.mean()) < 50
    assert int(out.max()) > 200
    assert float(out[5:15, 5:15].mean()) < 40
    assert int(out[:, 78:84].max()) > 200
