"""Sentinel-OR backend (M3): REST + WS /ws/state, MJPEG /video.mjpg, presenter controls.

Run from the repo root:  uvicorn backend.app:app --port 7000
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from . import report as report_module
from .agents.llm_client import LightningClient
from .agents.orchestrator import Orchestrator
from .agents.procedure import ProcedureAgent
from . import omni as omni_module
from . import voice as voice_module
from .audit import AuditLog
from .metrics import BytesOutCounter, read_mem_used_gb
from .phase import PhaseRules
from . import vision as vision_module
from .scenario import load_all
from .scene import LiveScene
from .state import AuditInfo, Labels, PhaseState, StateStore, VideoInfo
from .telemetry import ScenarioPlayer
from .video_worker import VideoWorker

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SENTINEL_DATA", REPO_DIR / "data"))
DEFAULT_SCENARIO = os.environ.get("SENTINEL_SCENARIO", "true_bleed")
AUDIT_DIR = Path(os.environ.get("SENTINEL_AUDIT_DIR", REPO_DIR / "audit"))
REPORT_TIMEOUT_S = float(os.environ.get("REPORT_TIMEOUT_S", "30"))
BOUNDARY = "frame"
PAUSED_RESEND_S = 0.5  # /video.mjpg repeats the current frame at this interval while nothing new is decoded

logging.basicConfig(level=os.environ.get("SENTINEL_LOG", "INFO"),
                    format="%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s")
log = logging.getLogger("sentinel.app")


@dataclass
class Services:
    store: StateStore
    player: ScenarioPlayer
    video: VideoWorker
    bytes_out: BytesOutCounter
    phase_rules: PhaseRules
    orchestrator: Orchestrator
    patient: dict[str, Any]
    started: float
    audit: AuditLog
    report_client: LightningClient
    report_prompt: str
    reports: dict[int, report_module.Report]
    report_lock: asyncio.Lock
    vision: Optional[vision_module.VisionClient] = None
    procedure: Optional[ProcedureAgent] = None


def procedure_enabled() -> bool:
    return os.environ.get("SENTINEL_PROCEDURE_AGENT", "1") not in ("0", "false", "off")


async def metrics_loop(svc: Services, period_s: float = 1.0) -> None:
    while True:
        m = svc.store.state.metrics.model_copy(update={
            "render_fps": svc.video.render_fps(), "bytes_out": svc.bytes_out.read(), "gb_used": read_mem_used_gb(),
            "notes": {**svc.store.state.metrics.notes, "bytes_out": svc.bytes_out.note()},
        })
        sc = svc.store.state.scene
        m = m.model_copy(update={"detector_ms": sc.detector_ms, "detector_fps": sc.detector_fps})
        stats = svc.video.stats()
        stats["detector"] = f"{sc.status}: {sc.note}"
        svc.store.update(metrics=m, video=VideoInfo(**stats), labels=Labels.for_video(stats["source"]))
        await asyncio.sleep(period_s)


async def phase_watch(svc: Services, period_s: float = 0.25) -> None:
    """Log every change of the procedure step (tracker or presenter) once, whoever set it."""
    last: tuple[Any, Any] = (None, None)
    while True:
        ph = svc.store.state.phase
        if (ph.name, ph.set_by) != last:
            last = (ph.name, ph.set_by)
            if ph.name:
                svc.audit.record("phase_change", f"procedure step: {ph.display or ph.name}",
                                 {"name": ph.name, "display": ph.display, "status": ph.status, "set_by": ph.set_by,
                                  "since_s": ph.since_s}, actor=ph.set_by or "system", clock_s=svc.store.state.clock_s)
        await asyncio.sleep(period_s)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = StateStore()
    scenarios = load_all(DATA_DIR)
    video = VideoWorker(REPO_DIR)
    phase_path = DATA_DIR / "procedure" / "phase_rules.json"
    phase_json = json.loads(phase_path.read_text())
    steps = json.loads((DATA_DIR / "procedure" / "lap_chole_steps.json").read_text())
    audit_ref: dict[str, AuditLog] = {}

    def scene_log(kind: str, summary: str, data: dict | None, actor: str) -> None:
        if "audit" in audit_ref:
            audit_ref["audit"].record(kind, summary, data, actor=actor)

    scene = LiveScene(PhaseRules.load(phase_path), steps, phase_json.get("out_of_phase_events", []), log=scene_log)
    player = ScenarioPlayer(store, scenarios, video=video, initial=DEFAULT_SCENARIO, scene=scene)
    vision_client = None
    if vision_module.enabled():
        vision_client = vision_module.VisionClient(scene, clock=lambda: player.clock,
                                                   on_status=lambda s, d: scene_log("vision_status", s, d, "vision"))
        video.on_frame = vision_client.submit
    patient = json.loads((DATA_DIR / "patient" / "patient_record.json").read_text())
    client = LightningClient()  # LIGHTNING_URL, LIGHTNING_MODEL, AGENT_TIMEOUT_S
    omni = omni_module.OmniClient(REPO_DIR) if omni_module.enabled() else None  # OMNI_URL, OMNI_TIMEOUT_S; skipped with no video
    orchestrator = Orchestrator(store, player, patient, phase_json, client=client, omni=omni, scene=scene)
    player.listener = orchestrator
    audit = AuditLog(AUDIT_DIR, context=lambda: (player.clock, player.scenario.id),
                     on_change=lambda a: store.update(audit=AuditInfo(**a.info())))
    audit.record("session_start", "Sentinel-OR backend started",
                 {"version": app.version, "scenarios": list(scenarios), "lightning_url": client.base_url,
                  "audit_file": str(audit.path)}, clock_s=0.0, scenario_id=player.scenario.id)
    orchestrator.audit = audit
    audit_ref["audit"] = audit
    prompts_dir = Path(os.environ.get("SENTINEL_PROMPTS", REPO_DIR / "prompts"))
    procedure = ProcedureAgent(store, player, scene, steps, LightningClient(), (prompts_dir / "procedure.md").read_text(encoding="utf-8"),
                               audit=audit)
    player.reset_hooks.append(procedure.reset)
    player.attach_audit(audit)
    svc = Services(store=store, player=player, video=video, bytes_out=BytesOutCounter(),
                   phase_rules=PhaseRules.load(phase_path), orchestrator=orchestrator,
                   patient=patient, started=time.monotonic(), audit=audit,
                   report_client=LightningClient(timeout_s=REPORT_TIMEOUT_S),
                   report_prompt=(prompts_dir / "report.md").read_text(encoding="utf-8"),
                   reports={}, report_lock=asyncio.Lock())
    svc.vision = vision_client
    svc.procedure = procedure
    app.state.svc = svc
    if vision_client is not None:
        vision_client.start()
    video.start()
    tasks = [asyncio.create_task(store.run_broadcaster(), name="broadcaster"),
             asyncio.create_task(player.run(), name="telemetry"),
             asyncio.create_task(metrics_loop(svc), name="metrics"),
             asyncio.create_task(phase_watch(svc), name="phase-audit")]
    if procedure_enabled():
        tasks.append(asyncio.create_task(procedure.run(), name="procedure-agent"))
    log.info("started: scenarios=%s data=%s lightning=%s model=%s timeout=%gs", ",".join(scenarios), DATA_DIR,
             client.base_url, client.model or "(first id from /models)", client.timeout_s)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        await orchestrator.aclose()
        await procedure.client.aclose()
        await svc.report_client.aclose()
        audit.close()
        video.stop()
        if vision_client is not None:
            vision_client.stop()


app = FastAPI(title="Sentinel-OR", version="0.5.0-m5", lifespan=lifespan)
if getattr(voice_module, "router", None) is not None:  # TTS/STT endpoints once backend/voice.py provides them
    app.include_router(voice_module.router)


def services(request: Request | WebSocket) -> Services:
    return request.app.state.svc


# ---- read endpoints -----------------------------------------------------------------
@app.get("/health")
async def health(request: Request) -> dict[str, Any]:
    svc = services(request)
    st = svc.store.state
    return {"status": "ok", "milestone": "M4a", "speak_or_hold": True,
            "scenario_id": st.scenario_id, "clock_s": st.clock_s,
            "playing": st.playing, "ws_clients": svc.store.subscriber_count,
            "video": svc.video.stats(), "agents": svc.orchestrator.status,
            "lightning": {"url": svc.orchestrator.client.base_url, "model": svc.orchestrator.client.model,
                          "timeout_s": svc.orchestrator.client.timeout_s},
            "audit": {"entries": svc.audit.count, "status": svc.audit.status, "file": str(svc.audit.path)},
            "omni": None if svc.orchestrator.omni is None else {"url": svc.orchestrator.omni.base_url},
            "vision": None if svc.vision is None else {"url": vision_module.VISION_URL, "up": svc.vision.up,
                                                       "error": svc.vision.error, "scene": st.scene.status,
                                                       "models": {k: v.get("loaded") for k, v in
                                                                  svc.vision.health.get("models", {}).items()}},
            "uptime_s": round(time.monotonic() - svc.started, 1)}


@app.get("/scenarios")
async def scenarios(request: Request) -> list[dict[str, Any]]:
    return [sc.summary() for sc in services(request).player.scenarios.values()]


@app.get("/patient")
async def patient(request: Request) -> dict[str, Any]:
    return services(request).patient


@app.get("/state")
async def state(request: Request) -> dict[str, Any]:
    return services(request).store.snapshot()


@app.get("/vitals/history")
async def vitals_history(request: Request) -> dict[str, Any]:
    """1 Hz samples revealed so far (t_s 0..now), for charts that join mid-run."""
    player = services(request).player
    series = player.scenario.series
    return {"scenario_id": player.scenario.id, "source": series.source,
            "samples": series.window(0, int(player.clock))}


# ---- audit log and post-op report ------------------------------------------------------
@app.get("/audit/info")
async def audit_info(request: Request) -> dict[str, Any]:
    return services(request).audit.info()


@app.get("/audit/entries")
async def audit_entries(request: Request, run: int | None = None, since_seq: int = 0, limit: int = 500) -> dict[str, Any]:
    audit = services(request).audit
    rows = audit.entries(run=run, since_seq=since_seq)
    return {"session_id": audit.session_id, "total": audit.count, "head_hash": audit.head_hash,
            "entries": rows[-max(1, min(limit, 5000)):]}


@app.post("/audit/verify")
async def audit_verify(request: Request) -> dict[str, Any]:
    """Re-read the file from disk and re-compute every hash."""
    return services(request).audit.verify().as_dict()


@app.get("/audit/file")
async def audit_file(request: Request) -> FileResponse:
    audit = services(request).audit
    return FileResponse(audit.path, media_type="application/x-ndjson", filename=audit.path.name)


@app.post("/report/generate")
async def report_generate(request: Request, run: int | None = None) -> dict[str, Any]:
    """Build the post-op report draft for a run (default: the latest) from the audit log."""
    svc = services(request)
    async with svc.report_lock:
        try:
            rep = await report_module.generate(svc.audit, svc.report_client, svc.patient, svc.report_prompt,
                                               run=run, out_dir=AUDIT_DIR)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        svc.reports[rep.run] = rep
    return rep.summary()


@app.get("/report")
async def report_latest(request: Request) -> dict[str, Any]:
    svc = services(request)
    return {"reports": [r.summary() for r in svc.reports.values()]}


@app.get("/report.html", response_class=HTMLResponse)
async def report_html(request: Request, run: int | None = None) -> HTMLResponse:
    svc = services(request)
    if not svc.reports:
        raise HTTPException(404, "no report generated yet; POST /report/generate first")
    rep = svc.reports[max(svc.reports)] if run is None else svc.reports.get(run)
    if rep is None:
        raise HTTPException(404, f"no report for run {run}")
    return HTMLResponse(rep.html_text)


# ---- presenter controls -----------------------------------------------------------------
def _ack(svc: Services) -> dict[str, Any]:
    st = svc.store.state
    return {"ok": True, "scenario_id": st.scenario_id, "clock_s": st.clock_s, "playing": st.playing}


@app.post("/control/play")
async def control_play(request: Request) -> dict[str, Any]:
    svc = services(request)
    svc.player.play()
    return _ack(svc)


@app.post("/control/pause")
async def control_pause(request: Request) -> dict[str, Any]:
    svc = services(request)
    svc.player.pause()
    return _ack(svc)


@app.post("/control/reset")
async def control_reset(request: Request) -> dict[str, Any]:
    svc = services(request)
    svc.player.reset()
    svc.store.update(phase=PhaseState())
    return _ack(svc)


@app.post("/control/restart")
async def control_restart(request: Request) -> dict[str, Any]:
    svc = services(request)
    svc.player.restart()
    return _ack(svc)


@app.post("/control/trigger")
async def control_trigger(request: Request) -> dict[str, Any]:
    svc = services(request)
    event = svc.player.trigger()
    return {**_ack(svc), "event": event.model_dump(mode="json")}


@app.post("/control/scenario/{scenario_id}")
async def control_scenario(scenario_id: str, request: Request) -> dict[str, Any]:
    svc = services(request)
    if scenario_id not in svc.player.scenarios:
        raise HTTPException(404, f"unknown scenario {scenario_id}")
    svc.player.select(scenario_id)
    svc.store.update(phase=PhaseState())
    return _ack(svc)


@app.post("/control/surgeon-asks")
async def control_surgeon_asks(request: Request) -> dict[str, Any]:
    """Release held speech now (judge beat: the surgeon asked). Banner is never held."""
    svc = services(request)
    svc.player.surgeon_asks()
    return _ack(svc)


@app.post("/control/phase/{name}")
async def control_phase(name: str, request: Request) -> dict[str, Any]:
    """Presenter override of the step label ('auto' clears it)."""
    svc = services(request)
    if name == "auto":  # the tracker already carries on from a forced step; nothing to clear
        return _ack(svc)
    if name not in svc.phase_rules.display:
        raise HTTPException(404, f"unknown phase {name}")
    svc.player.force_phase(name)
    return _ack(svc)


# ---- streams ------------------------------------------------------------------------------
@app.websocket("/ws/state")
async def ws_state(ws: WebSocket) -> None:
    await ws.accept()
    store = services(ws).store
    queue = store.subscribe()
    try:
        while True:
            await ws.send_text(await queue.get())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        store.unsubscribe(queue)


@app.get("/video.mjpg")
async def video_mjpg(request: Request, boxes: int = 0) -> StreamingResponse:
    """Plain frames, or with ?boxes=1 the newest frame the models have seen with its own boxes
    drawn on it (falls back to plain frames while the vision service is down)."""
    svc = services(request)
    plain = svc.video
    boxed = svc.vision if boxes and svc.vision is not None else None

    class _Source:
        @staticmethod
        def latest() -> Any:
            if boxed is not None:
                a = boxed.annotated()
                if a is not None:
                    return a
            return plain.latest()

    video = _Source

    async def frames() -> AsyncIterator[bytes]:
        # Each part is closed by the next boundary right away. Some browsers still paint a
        # part only when the following one arrives, so while no new frame exists (paused)
        # the same frame, with the same frame id, is sent again every PAUSED_RESEND_S.
        yield f"--{BOUNDARY}\r\n".encode()
        last_id, sent_at = -1, 0.0
        while True:
            latest = video.latest()
            now = time.monotonic()
            if latest is None or (latest[0] == last_id and now - sent_at < PAUSED_RESEND_S):
                await asyncio.sleep(0.01)
                continue
            (last_id, jpeg), sent_at = latest, now
            yield (f"Content-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n"
                   f"X-Frame-Id: {last_id}\r\n\r\n").encode() + jpeg + f"\r\n--{BOUNDARY}\r\n".encode()

    return StreamingResponse(frames(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                             headers={"Cache-Control": "no-store"})
