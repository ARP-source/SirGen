"""Sentinel-OR vision service: the trained models behind one local HTTP endpoint.

Runs in the training venv (torch + ultralytics), separate from the app, so the
app's video path never waits on a model (hard rule 3). Local only, no internet.

    POST /infer   body = JPEG bytes, header X-Frame-Id: <int>
                  -> {"frame_id", "w", "h", "ms", "tools": [...], "anatomy": [...], "cvs": {...} | null}
    GET  /health  -> which models are loaded, their classes and held-out metrics

Models are read from $SENTINEL_MODELS (default ~/sentinel-train/models):
    tools.pt + tools_classes.json     Cholec80-Boxes instrument detector (7 classes)
    anatomy.pt + anatomy_metrics.json Endoscapes2023 anatomy detector (6 classes)
    cvs.pt + cvs_meta.json            Endoscapes2023 CVS classifier (3 criteria)
A missing model is reported as not loaded; the others still run.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
import torch

log = logging.getLogger("sentinel.vision")
MODELS = Path(os.environ.get("SENTINEL_MODELS", Path.home() / "sentinel-train/models")).expanduser()
PORT = int(os.environ.get("SENTINEL_VISION_PORT", "8004"))
ANATOMY_CONF = float(os.environ.get("SENTINEL_ANATOMY_CONF", "0.30"))
DEV = "cuda" if torch.cuda.is_available() else "cpu"


def _read_json(p: Path) -> Optional[dict[str, Any]]:
    return json.loads(p.read_text()) if p.is_file() else None


class Models:
    def __init__(self) -> None:
        from ultralytics import YOLO

        self.lock = threading.Lock()
        self.status: dict[str, Any] = {}
        self.tools = self.anatomy = self.cvs = None
        self.tool_conf: dict[str, float] = {}

        meta = _read_json(MODELS / "tools_classes.json") or {}
        if (MODELS / "tools.pt").is_file():
            self.tools = YOLO(str(MODELS / "tools.pt"))
            self.tool_conf = meta.get("per_class_conf", {})
            self.status["tools"] = {"loaded": True, "classes": list(self.tools.names.values()),
                                    "per_class_conf": self.tool_conf, "metrics": meta.get("overall"),
                                    "metrics_note": meta.get("note"), "attribution": meta.get("attribution")}
        else:
            self.status["tools"] = {"loaded": False}

        if (MODELS / "anatomy.pt").is_file():
            self.anatomy = YOLO(str(MODELS / "anatomy.pt"))
            self.status["anatomy"] = {"loaded": True, "classes": list(self.anatomy.names.values()),
                                      "conf": ANATOMY_CONF, "metrics": _read_json(MODELS / "anatomy_metrics.json"),
                                      "attribution": "Trained on Endoscapes2023 BBox201 (CAMMA; CC BY-NC-SA 4.0)."}
        else:
            self.status["anatomy"] = {"loaded": False}

        cmeta = _read_json(MODELS / "cvs_meta.json")
        if (MODELS / "cvs.pt").is_file() and cmeta:
            from torchvision.models import convnext_tiny

            m = convnext_tiny()
            m.classifier[2] = torch.nn.Linear(m.classifier[2].in_features, len(cmeta["criteria"]))
            m.load_state_dict(torch.load(MODELS / "cvs.pt", map_location="cpu"))
            self.cvs = m.eval().to(DEV)
            self.cvs_meta = cmeta
            self._mean = torch.tensor(cmeta["mean"], device=DEV).view(1, 3, 1, 1)
            self._std = torch.tensor(cmeta["std"], device=DEV).view(1, 3, 1, 1)
            self.status["cvs"] = {"loaded": True, "criteria": cmeta["criteria"], "thresholds": cmeta["thresholds"],
                                  "metrics": _read_json(MODELS / "cvs_metrics.json"),
                                  "attribution": "Trained on Endoscapes2023 CVS201 (CAMMA; CC BY-NC-SA 4.0)."}
        else:
            self.status["cvs"] = {"loaded": False}
        self.warmup()

    def warmup(self) -> None:
        dummy = np.zeros((480, 854, 3), dtype=np.uint8)
        for _ in range(2):
            self.infer(dummy)

    @staticmethod
    def _boxes(result: Any, names: dict[int, str], conf_for: dict[str, float], default: float) -> list[dict]:
        out = []
        b = result.boxes
        if b is None:
            return out
        for xyxy, c, k in zip(b.xyxy.tolist(), b.conf.tolist(), b.cls.tolist()):
            name = names[int(k)]
            if c >= conf_for.get(name, default):
                out.append({"cls": name, "conf": round(float(c), 3), "box": [round(v, 1) for v in xyxy]})
        return out

    @torch.no_grad()
    def infer(self, frame: np.ndarray) -> dict[str, Any]:
        out: dict[str, Any] = {"tools": None, "anatomy": None, "cvs": None}
        with self.lock:
            if self.tools is not None:
                r = self.tools.predict(frame, imgsz=640, conf=0.2, half=DEV == "cuda", verbose=False)[0]
                out["tools"] = self._boxes(r, self.tools.names, self.tool_conf, 0.3)
            if self.anatomy is not None:
                r = self.anatomy.predict(frame, imgsz=640, conf=min(ANATOMY_CONF, 0.2), half=DEV == "cuda",
                                         verbose=False)[0]
                out["anatomy"] = self._boxes(r, self.anatomy.names, {}, ANATOMY_CONF)
            if self.cvs is not None:
                h, w = self.cvs_meta["input_hw"]
                rgb = cv2.cvtColor(cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
                x = torch.from_numpy(rgb).to(DEV).permute(2, 0, 1)[None].float().div_(255)
                x = (x - self._mean) / self._std
                with torch.autocast(DEV, dtype=torch.bfloat16, enabled=DEV == "cuda"):
                    p = torch.sigmoid(self.cvs(x).float())[0].tolist()
                out["cvs"] = {c: round(v, 3) for c, v in zip(self.cvs_meta["criteria"], p)}
        return out


MODELS_OBJ: Optional[Models] = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: Any) -> None:
        pass

    def _send(self, code: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True, "device": DEV, "gpu": torch.cuda.get_device_name(0) if DEV == "cuda" else None,
                             "models": MODELS_OBJ.status if MODELS_OBJ else {}})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/infer":
            self._send(404, {"error": "not found"})
            return
        n = int(self.headers.get("Content-Length", "0"))
        buf = np.frombuffer(self.rfile.read(n), dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            self._send(400, {"error": "not a JPEG"})
            return
        t0 = time.perf_counter()
        res = MODELS_OBJ.infer(frame)
        res.update(frame_id=int(self.headers.get("X-Frame-Id", "0")), w=frame.shape[1], h=frame.shape[0],
                   ms=round((time.perf_counter() - t0) * 1000, 1))
        self._send(200, res)


def main() -> None:
    global MODELS_OBJ
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    MODELS_OBJ = Models()
    log.info("models: %s", {k: v.get("loaded") for k, v in MODELS_OBJ.status.items()})
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log.info("vision service on 127.0.0.1:%d (%s)", PORT, DEV)
    srv.serve_forever()


if __name__ == "__main__":
    main()
