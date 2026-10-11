"""AI sight draws named boxes for organs, vessels and instruments, and nothing else on the field."""
import numpy as np

from backend.vision import ANATOMY_COLORS, TOOL_COLOR, draw


def test_ai_sight_labels_structures_and_leaves_the_field_clear_of_caution():
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    res = {
        "anatomy": [
            {"cls": "cystic_artery", "conf": 0.8, "box": [40, 40, 90, 80]},
            {"cls": "gallbladder", "conf": 0.7, "box": [10, 20, 120, 160]},
            {"cls": "tool", "conf": 0.9, "box": [200, 8, 240, 30]},
        ],
        "tools": [{"cls": "Hook", "conf": 0.9, "box": [100, 30, 140, 70]}],
        "caution": {"box": [0.4, 0.4, 0.6, 0.6], "label": "near the cystic duct"},
    }
    out = draw(frame, res, 1.0)

    assert tuple(int(v) for v in out[80, 90]) == ANATOMY_COLORS["cystic_artery"]
    assert tuple(int(v) for v in out[160, 120]) == ANATOMY_COLORS["gallbladder"]
    assert tuple(int(v) for v in out[70, 140]) == TOOL_COLOR
    # The anatomy model's own "instrument" box is not drawn a second time.
    assert int(out[8, 200].sum()) == 0
    # Caution stays off the field (the ellipse would cross this point).
    assert int(out[100, 198].sum()) == 0
