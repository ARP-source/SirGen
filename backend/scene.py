"""Live scene understanding from the vision service output.

Turns per-frame model output (instruments, anatomy, CVS scores) into what the
rest of the app needs, on the scenario clock:
  - instruments present now (for the speak-or-hold gate) and per 1-s step (for the phase tracker)
  - procedure step from the phase tracker, with its goal and the usual next step
  - anatomy identified on video and the Critical View of Safety (CVS) criteria
  - a log of video events with source ids `vid-<n>` that the surgical agent cites
  - deviations from the expected procedure (first clip or cut before CVS was seen)

Everything here comes from the video models; nothing is read from the scenario
file. When no model result has arrived recently the scene says so and the gate
and phase tracker are told the detector is unavailable (hard rule 4).
"""
from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from typing import Any, Optional

from pydantic import BaseModel, Field

from .phase import PhaseRules, PhaseTracker

STALE_S = 2.0
PRESENT_FRACTION = 0.34
CVS_EMA = 0.3
CVS_SUSTAIN_S = 2
ENTER_SUSTAIN_S = 2
REENTER_GAP_S = 5
DISSECTION_MIN_S = 15
CUT_TOOLS = ("Clipper", "Scissors")
KEY_TOOLS = ("Hook", "Clipper", "Scissors", "Bipolar", "Irrigator", "SpecimenBag")
KEY_ANATOMY = ("cystic_duct", "cystic_artery", "calot_triangle", "cystic_plate")
ANATOMY_NAMES = {"cystic_plate": "cystic plate", "calot_triangle": "hepatocystic (Calot) triangle",
                 "cystic_artery": "cystic artery", "cystic_duct": "cystic duct", "gallbladder": "gallbladder",
                 "tool": "instrument"}
MAX_EVENTS = 200

LogFn = Callable[[str, str, Optional[dict], str], None]


class SceneEvent(BaseModel):
    source_id: str
    t_s: float
    kind: str  # tool_in | anatomy | step | cvs | deviation | observation | first_cut
    text: str
    tools: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class CvsCriterion(BaseModel):
    name: str
    text: str
    p: Optional[float] = None
    threshold: Optional[float] = None
    met: bool = False


class StepInfo(BaseModel):
    name: Optional[str] = None
    display: Optional[str] = None
    since_s: Optional[float] = None
    goal: str = ""
    watch: str = ""
    next_name: Optional[str] = None
    next_display: Optional[str] = None
    stale: bool = False
    basis: str = "phase heuristic over detected instruments (data/procedure/phase_rules.json)"


class SceneView(BaseModel):
    status: str = "not running"  # live | stale | unavailable | not running
    note: str = "vision service not connected"
    detector_ms: Optional[float] = None
    detector_fps: Optional[float] = None
    last_frame_id: Optional[int] = None
    tools: list[str] = Field(default_factory=list)
    anatomy: list[str] = Field(default_factory=list)
    cvs: list[CvsCriterion] = Field(default_factory=list)
    cvs_available: bool = False
    cvs_achieved: bool = False
    cvs_seen_at_s: Optional[float] = None
    step: StepInfo = Field(default_factory=StepInfo)
    events: list[SceneEvent] = Field(default_factory=list)
    deviations: list[SceneEvent] = Field(default_factory=list)
    models: dict[str, Any] = Field(default_factory=dict)


class LiveScene:
    def __init__(self, rules: PhaseRules, steps: dict[str, Any], oop_events: Optional[list[dict[str, Any]]] = None,
                 log: Optional[LogFn] = None) -> None:
        self.rules = rules
        self.steps = steps
        self._oop = oop_events or []
        self._log_fn = log
        self._lock = threading.Lock()
        self.models: dict[str, Any] = {}
        self.service_note = "vision service not connected"
        self.reset()

    # ---- inputs ---------------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._pending: list[tuple[float, dict[str, Any]]] = []
            self._recent: deque[tuple[float, dict[str, Any]]] = deque(maxlen=4)
            self._arrivals: deque[float] = deque(maxlen=64)
            self._last_at: Optional[float] = None
            self._last_ms: Optional[float] = None
            self._last_frame: Optional[int] = None
        self.tracker = PhaseTracker(self.rules, start_t=0.0)
        self._next_second = 0
        self._phase_update = None
        self._seen_phase: Optional[str] = None
        self._tool_last: dict[str, float] = {}
        self._tool_streak: dict[str, int] = {}
        self._anat_streak: dict[str, int] = {}
        self._anat_announced: set[str] = set()
        self._tools_s: Optional[set[str]] = None
        self._anat_s: set[str] = set()
        self._cvs_ema: dict[str, float] = {}
        self._cvs_thr: dict[str, float] = {}
        self._cvs_met_streak = 0
        self._cvs_announced: set[str] = set()
        self.cvs_seen_at: Optional[float] = None
        self._dissection_s = 0
        self._cut_streak = 0
        self._first_cut_at: Optional[float] = None
        self._oop_announced: set[tuple[str, str]] = set()
        self.events: list[SceneEvent] = []
        self.deviations: list[SceneEvent] = []
        self._seq = 0

    def add(self, result: dict[str, Any], t: float, wall: float) -> None:
        """One vision result (any thread). `t` is the scenario clock, `wall` time.monotonic()."""
        with self._lock:
            self._pending.append((t, result))
            self._recent.append((wall, result))
            self._arrivals.append(wall)
            self._last_at = wall
            self._last_ms = result.get("ms")
            self._last_frame = result.get("frame_id")

    def set_service(self, models: dict[str, Any], note: str) -> None:
        self.models, self.service_note = models, note

    # ---- outputs --------------------------------------------------------------
    def live(self, now: float) -> bool:
        return self._last_at is not None and now - self._last_at <= STALE_S

    def current_tools(self, now: float) -> Optional[set[str]]:
        """Instruments in the last two results (fast, for the gate). None = detector unavailable."""
        with self._lock:
            recent = [r for w, r in self._recent if now - w <= 1.0][-2:]
        if not recent or not self.live(now):
            return None
        if all(r.get("tools") is None for r in recent):
            return None
        return {d["cls"] for r in recent for d in (r.get("tools") or [])}

    def phase_state(self, now: float) -> "PhaseState":
        from .state import PhaseState

        u = self._phase_update
        if u is None:
            return PhaseState(status="waiting for the vision models" if not self.live(now) else "starting")
        stale = u.stale or not self.live(now)
        return PhaseState(name=u.name, display=u.display, since_s=u.since_s, tools_window=u.tools_window,
                          status="detector unavailable, step held (stale)" if stale else "live: phase heuristic over detected instruments",
                          set_by=u.set_by)

    def view(self, now: float) -> SceneView:
        live = self.live(now)
        with self._lock:
            arrivals = [w for w in self._arrivals if now - w <= 2.0]
            last_ms, last_frame = self._last_ms, self._last_frame
            last_tools = [r for _, r in list(self._recent)[-1:]]
        if live:
            status, note = "live", "vision models running on this machine"
        elif self._last_at is not None:
            status, note = "stale", f"no model result for {now - self._last_at:.0f} s"
        else:
            status, note = ("not running", self.service_note)
        tools = sorted({d["cls"] for r in last_tools for d in (r.get("tools") or [])}) if live else []
        anatomy = sorted({d["cls"] for r in last_tools for d in (r.get("anatomy") or [])}) if live else []
        crit = self.steps.get("cvs_criteria", {})
        cvs = [CvsCriterion(name=c, text=crit.get(c, c), p=round(self._cvs_ema[c], 3) if c in self._cvs_ema else None,
                            threshold=self._cvs_thr.get(c), met=c in self._cvs_ema and self._cvs_ema[c] >= self._cvs_thr.get(c, 0.5))
               for c in crit]
        return SceneView(status=status, note=note, detector_ms=last_ms if live else None,
                         detector_fps=round(len(arrivals) / 2.0, 1) if live else None, last_frame_id=last_frame,
                         tools=tools, anatomy=anatomy, cvs=cvs, cvs_available=bool(self._cvs_ema),
                         cvs_achieved=self.cvs_seen_at is not None, cvs_seen_at_s=self.cvs_seen_at,
                         step=self._step_info(now), events=self.events[-30:], deviations=list(self.deviations),
                         models=self.models)

    def visible_events(self, now_s: float) -> list[SceneEvent]:
        return [e for e in self.events if e.t_s <= now_s]

    def event(self, source_id: str) -> Optional[SceneEvent]:
        return next((e for e in self.events if e.source_id == source_id), None)

    # ---- 1 Hz processing (event loop) ----------------------------------------
    def step(self, clock: float, now: float) -> None:
        while self._next_second < int(clock):
            self._process_second(self._next_second, now)
            self._next_second += 1

    def _process_second(self, s: int, now: float) -> None:
        with self._lock:
            batch = [r for t, r in self._pending if s <= t < s + 1]
            self._pending = [(t, r) for t, r in self._pending if t >= s + 1]
        t = float(s)
        if not batch or all(r.get("tools") is None for r in batch):
            self._tools_s = None
            self._phase_update = self.tracker.update(None, t)
            return
        n = len(batch)
        tools = _present(batch, "tools", n)
        anat = _present(batch, "anatomy", n)
        self._tools_s, self._anat_s = tools, anat
        for r in batch:
            self._cvs_step(r.get("cvs"))

        prev_phase = self._phase_update.name if self._phase_update else None
        self._phase_update = u = self.tracker.update(tools, t)
        if u.name != prev_phase and prev_phase is not None:
            info = self.steps.get("steps", {}).get(u.name, {})
            self._emit("step", t, f"Step now {u.display.lower()} (phase heuristic over detected instruments)",
                       sorted(tools), {"phase": u.name, "from": prev_phase, "goal": info.get("goal", "")})
        for oid in u.out_of_phase:
            if (oid, u.name) not in self._oop_announced:
                self._oop_announced.add((oid, u.name))
                ev = next((o for o in self._oop_defs() if o["id"] == oid), None)
                if ev:
                    self._emit("observation", t, f"{ev['event']} during {u.display.lower()}", sorted(tools),
                               {"oop_id": oid, "note": ev["note"]})

        for tool in KEY_TOOLS:
            if tool in tools:
                self._tool_streak[tool] = self._tool_streak.get(tool, 0) + 1
                gap = t - self._tool_last.get(tool, -1e9)
                if self._tool_streak[tool] == ENTER_SUSTAIN_S and gap > REENTER_GAP_S + ENTER_SUSTAIN_S:
                    self._emit("tool_in", t - ENTER_SUSTAIN_S + 1, f"{tool} in view", sorted(tools), {"tool": tool})
                if self._tool_streak[tool] >= ENTER_SUSTAIN_S:
                    self._tool_last[tool] = t
            else:
                self._tool_streak[tool] = 0

        for a in KEY_ANATOMY:
            self._anat_streak[a] = self._anat_streak.get(a, 0) + 1 if a in anat else 0
            if self._anat_streak[a] == 2 and a not in self._anat_announced:
                self._anat_announced.add(a)
                self._emit("anatomy", t - 1, f"{ANATOMY_NAMES[a].capitalize()} identified on video", sorted(tools),
                           {"structure": a})

        if "Hook" in tools or anat & {"calot_triangle", "cystic_duct", "cystic_artery"}:
            self._dissection_s += 1
        self._cvs_second(t, tools)
        self._cut_check(t, tools)

    def _cvs_step(self, cvs: Optional[dict[str, float]]) -> None:
        if not cvs:
            return
        thr = (self.models.get("cvs") or {}).get("thresholds")
        names = (self.models.get("cvs") or {}).get("criteria") or list(cvs)
        if thr:
            self._cvs_thr = dict(zip(names, thr))
        for c, p in cvs.items():
            prev = self._cvs_ema.get(c)
            self._cvs_ema[c] = p if prev is None else (1 - CVS_EMA) * prev + CVS_EMA * p

    def _cvs_second(self, t: float, tools: set[str]) -> None:
        if not self._cvs_ema:
            return
        crit = self.steps.get("cvs_criteria", {})
        met = {c for c, p in self._cvs_ema.items() if p >= self._cvs_thr.get(c, 0.5)}
        for c in met - self._cvs_announced:
            self._cvs_announced.add(c)
            self._emit("cvs", t, f"CVS criterion seen on video: {crit.get(c, c).lower()}", sorted(tools),
                       {"criterion": c, "p": round(self._cvs_ema[c], 3)})
        self._cvs_met_streak = self._cvs_met_streak + 1 if len(met) == len(self._cvs_ema) else 0
        if self._cvs_met_streak == CVS_SUSTAIN_S and self.cvs_seen_at is None:
            self.cvs_seen_at = t - CVS_SUSTAIN_S + 1
            self._emit("cvs", self.cvs_seen_at, "Critical View of Safety seen on video (all three criteria)",
                       sorted(tools), {"scores": {c: round(p, 3) for c, p in self._cvs_ema.items()}})

    def _cut_check(self, t: float, tools: set[str]) -> None:
        cutting = sorted(set(CUT_TOOLS) & tools)
        self._cut_streak = self._cut_streak + 1 if cutting else 0
        if self._cut_streak != ENTER_SUSTAIN_S or self._first_cut_at is not None:
            return
        self._first_cut_at = t0 = t - ENTER_SUSTAIN_S + 1
        tool = " and ".join(cutting)
        if self.cvs_seen_at is not None:
            self._emit("first_cut", t0, f"First {tool.lower()} use; the Critical View of Safety was seen on video "
                       f"{t0 - self.cvs_seen_at:.0f} s earlier", cutting, {"cvs_seen_at_s": self.cvs_seen_at})
        elif not self._cvs_ema:
            self._emit("first_cut", t0, f"First {tool.lower()} use; CVS model not loaded, the check was not made",
                       cutting, {})
        elif self._dissection_s < DISSECTION_MIN_S:
            self._emit("first_cut", t0, f"First {tool.lower()} use; dissection was not observed on video "
                       f"({self._dissection_s} s), so the CVS check was not made", cutting,
                       {"dissection_observed_s": self._dissection_s})
        else:
            dev = self.steps.get("deviations", {}).get("clip_before_cvs", {})
            scores = {c: round(p, 3) for c, p in self._cvs_ema.items()}
            met = [c for c, p in self._cvs_ema.items() if p >= self._cvs_thr.get(c, 0.5)]
            self._emit("deviation", t0, f"{tool} in view before the Critical View of Safety was seen on video "
                       f"({len(met)} of 3 criteria seen)", cutting,
                       {"deviation": "clip_before_cvs", "cvs_scores": scores, "criteria_met": met,
                        "why_it_matters": dev.get("why_it_matters", ""), "note": dev.get("note", ""),
                        "dissection_observed_s": self._dissection_s})

    # ---- helpers ------------------------------------------------------------------
    def _oop_defs(self) -> list[dict[str, Any]]:
        return self._oop

    def force_phase(self, name: str, t: float) -> None:
        """Presenter override; the tracker carries on from the forced step."""
        self.tracker.force(name, t)
        self._phase_update = self.tracker._result(t, stale=False)

    def _emit(self, kind: str, t: float, text: str, tools: list[str], data: dict[str, Any]) -> None:
        self._seq += 1
        ev = SceneEvent(source_id=f"vid-{self._seq}", t_s=round(t, 1), kind=kind, text=text, tools=tools, data=data)
        self.events.append(ev)
        if len(self.events) > MAX_EVENTS:
            self.events = self.events[-MAX_EVENTS:]
        if kind == "deviation":
            self.deviations.append(ev)
        if self._log_fn is not None:
            self._log_fn("deviation" if kind == "deviation" else "video_event", text,
                         {"source_id": ev.source_id, "kind": kind, "t_s": ev.t_s, "tools": tools, **data}, "vision")

    def _step_info(self, now: float) -> StepInfo:
        u = self._phase_update
        if u is None:
            return StepInfo(stale=not self.live(now))
        info = self.steps.get("steps", {}).get(u.name, {})
        nxt = info.get("next")
        return StepInfo(name=u.name, display=u.display, since_s=u.since_s, goal=info.get("goal", ""),
                        watch=info.get("watch", ""), next_name=nxt,
                        next_display=self.rules.display.get(nxt) if nxt else None,
                        stale=u.stale or not self.live(now))


def _present(batch: list[dict[str, Any]], key: str, n: int) -> set[str]:
    counts: dict[str, int] = {}
    for r in batch:
        for name in {d["cls"] for d in (r.get(key) or [])}:
            counts[name] = counts.get(name, 0) + 1
    return {k for k, v in counts.items() if v / n >= PRESENT_FRACTION}
