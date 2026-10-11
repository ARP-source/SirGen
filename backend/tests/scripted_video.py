"""TEST-ONLY stand-in for the live vision models. Never imported by product code.

Replays a scenario file's `surgical_events` as if the video models had reported
them, so the orchestrator, evidence and convergence tests can run without a GPU
or a video. Event source ids are kept (surg-01 ...); out-of-phase observations
are derived the way the old scripted path did (oop-1 / oop-2 from the latest
event's tools and `procedure_phase_at_event`).
"""
from __future__ import annotations

from typing import Any

from backend.phase import OOP_PHASES
from backend.scenario import Scenario
from backend.scene import SceneEvent, SceneView
from backend.state import PhaseState

TOOLS_WINDOW_S = 10


class ScriptedScene:
    def __init__(self, sc: Scenario, phase_json: dict[str, Any]) -> None:
        self.sc = sc
        self.phase_json = phase_json
        self.display = {p["name"]: p["display"] for p in phase_json.get("phases", [])}

    def _events(self, now_s: float) -> list[dict[str, Any]]:
        return [e for e in self.sc.raw.get("surgical_events", []) if e["t_s"] <= now_s]

    def visible_events(self, now_s: float) -> list[SceneEvent]:
        evs = self._events(now_s)
        out = [SceneEvent(source_id=e["source_id"], t_s=e["t_s"], kind="tool_in", text=e["event"],
                          tools=e.get("tools", [])) for e in evs]
        phase = self.sc.raw.get("procedure_phase_at_event")
        if evs and phase:
            latest = max(evs, key=lambda e: e["t_s"])
            for oop in self.phase_json.get("out_of_phase_events", []):
                tool, _, phases = OOP_PHASES[oop["id"]]
                if phase in phases and tool in latest.get("tools", []):
                    out.append(SceneEvent(source_id=oop["id"], t_s=latest["t_s"], kind="observation",
                                          text=oop["event"], tools=latest.get("tools", []), data={"note": oop["note"]}))
        return out

    def phase_state(self, now_s: float) -> PhaseState:
        name = self.sc.raw.get("procedure_phase_at_event")
        evs = self._events(now_s)
        tools = {t: TOOLS_WINDOW_S for t in max(evs, key=lambda e: e["t_s"]).get("tools", [])} if evs else {}
        return PhaseState(name=name, display=self.display.get(name), since_s=None, tools_window=tools,
                          status="live: phase heuristic over detected instruments", set_by="tracker")

    def view(self) -> SceneView:
        return SceneView(status="live", note="scripted test stand-in for the vision models")
