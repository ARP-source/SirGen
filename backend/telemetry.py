"""Replays a scenario's vitals and drug log on the scenario clock, applies the
baseline rule and raises the alert immediately, in code, when the event fires.

Alerts never wait for models (hard rule 3): the alert is published with an
urgent state update in the same tick that detects the event.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Optional, Protocol

from .audit import AuditLog
from .gate import SpeechGate, tools_at
from .scenario import Scenario
from .scene import LiveScene
from .state import (
    AGENTS_NOT_ATTACHED, Baseline, DrugEntry, Event, NibpEntry, ScenarioInfo, StateStore,
    TimelineItem, Vitals, footage_text,
)
from .video_worker import resolve_video

log = logging.getLogger("sentinel.telemetry")

R1_MAP_BELOW = 65.0
R1_HOLD_S = 60
R2_SBP_ABOVE = 180.0
R2_HOLD_S = 5
RUN_S = float(os.environ.get("SENTINEL_RUN_S", "0"))


class BaselineRule:
    """Fixed BP rule (data/README.md): alarm if arterial MAP < 65 mmHg for >= 60 s (R1)
    or arterial SBP > 180 mmHg for >= 5 s (R2). Fed one 1 Hz sample per call."""

    def __init__(self) -> None:
        self._map_low_since: Optional[int] = None
        self._sbp_high_since: Optional[int] = None
        self.fired_rule: Optional[str] = None
        self.fired_at: Optional[int] = None

    def step(self, t: int, map_: Optional[float], sbp: Optional[float]) -> Optional[str]:
        """Returns the rule id the first time the alarm fires, else None."""
        self._map_low_since = _since(self._map_low_since, t, map_ is not None and map_ < R1_MAP_BELOW)
        self._sbp_high_since = _since(self._sbp_high_since, t, sbp is not None and sbp > R2_SBP_ABOVE)
        if self.fired_rule is not None:
            return None
        if self._map_low_since is not None and t - self._map_low_since >= R1_HOLD_S:
            self.fired_rule, self.fired_at = "R1", t
        elif self._sbp_high_since is not None and t - self._sbp_high_since >= R2_HOLD_S:
            self.fired_rule, self.fired_at = "R2", t
        return self.fired_rule


def _since(current: Optional[int], t: int, condition: bool) -> Optional[int]:
    if not condition:
        return None
    return t if current is None else current


class VideoControl(Protocol):
    def set_playing(self, playing: bool) -> None: ...
    def rewind(self, scenario: Scenario) -> None: ...


class EventListener(Protocol):
    """Receives events after the alert has been published, and run resets."""
    def on_event(self, event: Event) -> None: ...
    def on_reset(self) -> None: ...


class ScenarioPlayer:
    """Owns the scenario clock. All state changes go through the StateStore."""

    def __init__(self, store: StateStore, scenarios: dict[str, Scenario],
                 video: Optional[VideoControl] = None, initial: Optional[str] = None,
                 scene: Optional[LiveScene] = None) -> None:
        if not scenarios:
            raise ValueError("no scenarios loaded")
        self.store = store
        self.scenarios = scenarios
        self.video = video
        self.scene = scene
        self._deviations_seen = 0
        self._event_seq = 0
        self.listener: Optional[EventListener] = None
        self.reset_hooks: list[Callable[[], None]] = []  # e.g. the procedure agent's reset
        self.audit: Optional[AuditLog] = None  # attached by attach_audit(); None in unit tests
        self.gate = SpeechGate(log=self._log)
        self._logged_sources: set[str] = set()
        self.scenario = scenarios[initial] if initial in scenarios else next(iter(scenarios.values()))
        self._reset_run()
        self._sync()

    # ---- audit ----------------------------------------------------------------
    def attach_audit(self, audit: AuditLog) -> None:
        """Start logging. The current run is run 1 of the log."""
        self.audit = audit
        audit.run = max(audit.run, 1)
        self._log_run_start()

    def _log(self, kind: str, summary: str, data: Optional[dict] = None, actor: str = "system") -> None:
        if self.audit is not None:
            self.audit.record(kind, summary, data, actor=actor, clock_s=self.clock, scenario_id=self.scenario.id)

    def _log_run_start(self) -> None:
        sc = self.scenario
        self._log("run_start", f"scenario loaded: {sc.title}",
                  {"title": sc.title, "on_screen_label": sc.on_screen_label, "duration_s": sc.duration_s,
                   "vitals_source": sc.series.source, "vitals_source_kind": sc.series.source_kind,
                   "event_type": sc.event_type})

    @property
    def run_s(self) -> float:
        """Run length: the scenario's segment, or longer with SENTINEL_RUN_S (e.g. a full surgery video).
        Vitals past the end of the recorded series read as not measured."""
        return float(max(self.scenario.duration_s, RUN_S))

    # ---- presenter controls -------------------------------------------------
    def select(self, scenario_id: str) -> None:
        self.scenario = self.scenarios[scenario_id]
        self.reset(_action=f"select scenario {scenario_id}")

    def play(self) -> None:
        restarted = self.ended
        if restarted:
            self._reset_run()
        self._set_playing(True)
        self._log("control", "play" + (" (restarted after the segment ended)" if restarted else ""),
                  {"action": "play"}, actor="presenter")
        self._sync(info=f"play at t {self.clock:.1f} s")

    def pause(self) -> None:
        self._set_playing(False)
        self._log("control", "pause", {"action": "pause"}, actor="presenter")
        self._sync(info=f"pause at t {self.clock:.1f} s")

    def reset(self, _action: str = "reset") -> None:
        self._reset_run()
        self._set_playing(False)
        self._log("control", _action, {"action": _action}, actor="presenter")
        self._sync()

    def restart(self) -> None:
        self._reset_run()
        self._set_playing(True)
        self._log("control", "restart", {"action": "restart"}, actor="presenter")
        self._sync()

    def trigger(self) -> Event:
        """Presenter trigger: raise the alert now."""
        self._log("control", "trigger alert now", {"action": "trigger"}, actor="presenter")
        return self._fire("presenter", self.clock, detected_at=time.perf_counter())

    def surgeon_asks(self) -> None:
        """Release held speech even while a hold instrument is in use."""
        self._log("control", "surgeon asks", {"action": "surgeon-asks"}, actor="presenter")
        self.gate.surgeon_asks()
        self._sync(urgent=True)

    def offer_speech(self, text: str, kind: str = "team_question", urgent: bool = False) -> None:
        """Queue or speak a non-banner message. The visual alert never goes through here."""
        self.gate.offer(text, kind=kind, urgent=urgent)  # type: ignore[arg-type]
        self.store.update(urgent=True, gate=self.gate.view(), speech=self.gate.speech_view())

    # ---- clock ----------------------------------------------------------------
    def tick(self, dt: float) -> None:
        """Advance the clock by dt seconds (if playing) and process what happened."""
        if self.playing:
            self.clock = min(self.clock + dt, float(self.run_s))
        while self._next_second <= int(self.clock):
            self._process_second(self._next_second)
            self._next_second += 1
        if self.scene is not None:
            self.scene.step(self.clock, time.monotonic())
            self._announce_deviations()
        self._sync()
        ev_t = self.scenario.event_t_s
        if ev_t is not None and not self._scheduled_fired and self.clock >= ev_t:
            self._fire("scenario", float(ev_t), detected_at=time.perf_counter())
        if self.playing and self.clock >= self.run_s:
            self._set_playing(False)
            self.ended = True
            self._add_timeline("info", "segment end")
            self._log("segment_end", "scenario segment ended", {"duration_s": self.run_s})
            self._sync()

    async def run(self, hz: float = 20.0) -> None:
        period = 1.0 / hz
        last = time.monotonic()
        while True:
            await asyncio.sleep(period)
            now = time.monotonic()
            try:
                self.tick(now - last)
            except Exception:
                log.exception("telemetry tick failed")
            last = now

    # ---- internals ----------------------------------------------------------
    def set_context(self, event_id: str, status: str, note: str = "", timeline: Optional[str] = None) -> bool:
        """Update the event's context status (pending/available/unavailable). Only the
        player writes `event` to the state, so the orchestrator goes through here."""
        if self._event is None or self._event.event_id != event_id:
            return False
        self._event = self._event.model_copy(update={"context_status": status, "context_note": note})
        if timeline:
            self._add_timeline("context", timeline, event_id)
        self._sync(urgent=status != "pending")
        return True

    def _reset_run(self) -> None:
        if self.listener is not None:
            self.listener.on_reset()  # cancels a running deliberation, clears agents/deliberation
        self.gate.reset()
        if self.scene is not None:
            self.scene.reset()
        for hook in self.reset_hooks:
            hook()
        self._deviations_seen = 0
        self.clock = 0.0
        self.playing = False
        self.ended = False
        self._next_second = 0
        self._scheduled_fired = False
        self._baseline = BaselineRule()
        self._logged_sources = set()
        if self.audit is not None:
            self.audit.run += 1
            self._log_run_start()
        self._timeline: list[TimelineItem] = []
        self._event: Optional[Event] = None
        if self.video is not None:
            self.video.rewind(self.scenario)
        self._process_second(0)
        self._next_second = 1
        sc = self.scenario
        repo_dir = getattr(self.video, "repo_dir", None)
        video_path = resolve_video(sc, repo_dir)[0] if repo_dir is not None else None
        self.store.update(
            scenario_id=sc.id,
            scenario=ScenarioInfo(id=sc.id, title=sc.title,
                                  on_screen_label=footage_text(sc.on_screen_label, str(video_path) if video_path else None),
                                  duration_s=sc.duration_s, event_t_s=sc.event_t_s,
                                  event_note=sc.event_note, alert_level=sc.alert_level),
            event=None, timeline=[],
            metrics=self.store.state.metrics.model_copy(update={"event_to_alert_ms": None, "deliberation_s": None}),
        )

    def _process_second(self, t: int) -> None:
        sample = self.scenario.series.at(t)
        fired = self._baseline.step(t, sample["map"], sample["sbp"])
        if fired:
            self._add_timeline("baseline", f"baseline rule {fired} alarm", t_s=float(t))
            self._log("baseline_alarm", f"fixed baseline rule {fired} fired at t {t} s",
                      {"rule": fired, "t_s": t, "rule_text": self.scenario.baseline.get("rule", "")})
        self._vitals_t = t

    def _vitals(self) -> Vitals:
        t = self._vitals_t
        s = self.scenario.series
        return Vitals(t_s=t, **s.at(t), source=s.source, source_kind=s.source_kind)

    def _baseline_state(self) -> Baseline:
        b = self.scenario.baseline
        return Baseline(rule_text=b.get("rule", ""), alarm=self._baseline.fired_rule is not None,
                        fired_rule=self._baseline.fired_rule, fired_at_t_s=self._baseline.fired_at,
                        expected_fires=b.get("fires"), expected_fires_at_t_s=b.get("fires_at_t_s"),
                        expected_correct=b.get("correct"), comment=b.get("comment", ""))

    def _visible_drugs(self) -> list[DrugEntry]:
        return [DrugEntry(t_s=d.get("t_s"), drug=d["drug"], detail=d["detail"], source_id=d["source_id"],
                          provenance=d.get("provenance", ""))
                for d in self.scenario.drug_log if d.get("t_s") is None or d["t_s"] <= self.clock]

    def _visible_nibp(self) -> list[NibpEntry]:
        return [NibpEntry(**{k: n.get(k) for k in ("t_s", "sbp", "dbp", "map", "source_id", "provenance")})
                for n in self.scenario.nibp if n["t_s"] <= self.clock]

    def _add_timeline(self, kind: str, label: str, event_id: Optional[str] = None,
                      t_s: Optional[float] = None) -> None:
        t = round(self.clock, 2) if t_s is None else t_s
        self._timeline.append(TimelineItem(t_s=t, kind=kind, label=label, event_id=event_id))

    def _set_playing(self, playing: bool) -> None:
        self.playing = playing
        if self.video is not None:
            self.video.set_playing(playing)

    def _log_new_sources(self, drugs: list[DrugEntry], nibp: list[NibpEntry]) -> None:
        """Log each drug entry and cuff reading once, when it becomes visible on the scenario clock."""
        if self.audit is None:
            return
        for d in drugs:
            if d.source_id not in self._logged_sources:
                self._logged_sources.add(d.source_id)
                when = "running at start" if d.t_s is None else f"t {d.t_s:g} s"
                self._log("drug_given", f"{d.drug}: {d.detail} ({when})",
                          {**d.model_dump(), "scenario_data": self.scenario.series.source_kind})
        for n in nibp:
            if n.source_id not in self._logged_sources:
                self._logged_sources.add(n.source_id)
                self._log("nibp_reading", f"cuff reading {n.sbp:g}/{n.dbp:g} (MAP {n.map:g}) at t {n.t_s:g} s"
                          if None not in (n.sbp, n.dbp, n.map, n.t_s) else "cuff reading",
                          n.model_dump())

    def _sync(self, urgent: bool = False, info: Optional[str] = None) -> float:
        if info:
            self._add_timeline("info", info)
        drugs, nibp = self._visible_drugs(), self._visible_nibp()
        self._log_new_sources(drugs, nibp)
        vitals = self._vitals()
        scene_fields = {}
        if self.scene is not None:
            now = time.monotonic()
            live = self.scene.current_tools(now)
            self.gate.set_live_tools(None if live is None else sorted(live))
            scene_fields = {"scene": self.scene.view(now), "phase": self.scene.phase_state(now)}
        tools, basis = tools_at(self.gate.live_tools)
        self.gate.on_clock(self.clock, tools, basis, map_=vitals.map, spo2=vitals.spo2)
        return self.store.update(
            urgent=urgent, clock_s=round(self.clock, 2), playing=self.playing, ended=self.ended,
            vitals=vitals, drug_log=drugs, nibp=nibp,
            baseline=self._baseline_state(), event=self._event, timeline=list(self._timeline),
            gate=self.gate.view(), speech=self.gate.speech_view(), **scene_fields,
        )

    def _announce_deviations(self) -> None:
        """A classifier remark (clip or cut seen before the Critical View of Safety) goes on the
        timeline and into the audit log as a video-model observation. It is never spoken and never
        urgent: the CVS call is too imprecise on held-out video to interrupt a surgeon with."""
        devs = self.scene.deviations if self.scene is not None else []
        for d in devs[self._deviations_seen:]:
            self._add_timeline("video", f"video-model observation: {d.text}", t_s=d.t_s)
        self._deviations_seen = len(devs)

    def force_phase(self, name: str) -> None:
        if self.scene is not None:
            self.scene.force_phase(name, self.clock)
        self._sync(urgent=True)

    def _alert_text(self) -> str:
        s = self.scenario.series
        vt = min(self._vitals_t, len(s) - 1)  # the last recorded second if the clock has run past the series
        now = s.at(vt)
        if self.scenario.event_type == "art_spike":
            prev = s.at(max(0, vt - 2))["sbp"]
            body = f"arterial SBP {now['sbp']:.0f} mmHg (was {prev:.0f} mmHg 2 s earlier)"
        else:
            window = [s.at(t)["map"] for t in range(max(0, vt - 60), vt + 1)]
            base = max(v for v in window if v is not None)
            body = f"MAP {now['map']:.0f} mmHg, {base - now['map']:.0f} mmHg below its 60-s baseline"
        if s.source_kind == "real_time_shifted":
            return f"TELEMETRY ALERT (VitalDB case 59, time-shifted): {body}"
        return f"SIMULATED TELEMETRY ALERT: {body}"

    def _fire(self, trigger: str, event_ts: float, detected_at: float) -> Event:
        """Raise the alert at once. `detected_at` is the perf_counter time the event
        was detected in code; event_to_alert_ms runs from there to publication."""
        if trigger == "scenario":
            self._scheduled_fired = True
        self._event_seq += 1
        sc = self.scenario
        event = Event(
            event_id=f"ev-{self._event_seq}", event_type=sc.event_type, event_timestamp=round(event_ts, 2),
            trigger=trigger, simulated=sc.series.source_kind != "real_time_shifted",
            alert_shown_at=round(self.clock, 3), alert_level=sc.alert_level, alert_text=self._alert_text(),
            detect_lag_ms=round(max(0.0, self.clock - event_ts) * 1000, 1),
            context_status="pending" if self.listener else "unavailable",
            context_note="agents deliberating" if self.listener else AGENTS_NOT_ATTACHED,
        )
        self._event = event
        how = "banner" if sc.alert_level == "banner" else "quiet flag"
        self._add_timeline("event", f"{event.event_type} triggered ({trigger})", event.event_id)
        self._add_timeline("alert", f"alert shown ({how})", event.event_id)
        published_at = self._sync(urgent=True)
        ms = round((published_at - detected_at) * 1000, 3)
        self._event = event.model_copy(update={"event_to_alert_ms": ms})
        self._log("alert", f"{event.alert_text} ({event.trigger})",
                  {"event_id": event.event_id, "event_type": event.event_type, "trigger": event.trigger,
                   "event_timestamp": event.event_timestamp, "alert_text": event.alert_text,
                   "alert_level": event.alert_level, "simulated": event.simulated,
                   "event_to_alert_ms": ms, "detect_lag_ms": event.detect_lag_ms,
                   "vitals": self._vitals().model_dump(), "phase": self.store.state.phase.model_dump()})
        if self.listener is None:
            self._add_timeline("context", f"context unavailable: {AGENTS_NOT_ATTACHED}", event.event_id)
        else:
            self._add_timeline("context", "context pending: agents deliberating", event.event_id)
        self.store.update(metrics=self.store.state.metrics.model_copy(update={"event_to_alert_ms": ms}))
        self._sync()
        log.info("alert %s %s at t=%.2f, event_to_alert_ms=%.3f", event.event_id, trigger, self.clock, ms)
        if self.listener is not None:  # after the alert is out: the agents never delay it (hard rule 3)
            try:
                self.listener.on_event(self._event)
            except Exception:
                log.exception("event listener failed")
        return self._event
