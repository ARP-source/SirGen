"""Three-agent deliberation (M3): surgical, anesthesia and record agents on Lightning.

An event runs on its own asyncio task, started after the alert has been published, so models
never delay the alert or the video (hard rule 3).

  1. For `art_spike`, wait until SBP has recovered and a cuff reading exists (prompts/README.md).
  2. Round 1: the three agents concurrently, each with ONLY its own evidence block.
  3. Round 2: each agent that has a valid round-1 message sees the other available round-1
     messages and answers once.
  4. Every output is validated (agents/schemas.py: shape, source ids in the loaded data, one
     sentence, banned wording, round-2 responses). A failure is retried once. After that the agent
     is marked unavailable with the reason. Nothing is ever written in place of failed model output.
     A timeout or unreachable endpoint marks the agent unavailable without a retry (8 s budget).
  5. Convergence, the summary and the team question are decided by code (agents/convergence.py).

The state is updated as each message arrives. `reset`, `restart` and scenario switches call
`on_reset`, which cancels a running deliberation and clears agents and deliberation.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

from ..audit import AuditLog
from ..scenario import Scenario
from ..state import AgentSlot, Deliberation, Event, StateStore
from . import convergence
from .convergence import AGENT_ORDER
from .evidence import AgentEvidence, EvidenceContext, build_user_message, compact, recovery_s
from .llm_client import LightningClient, LLMBadResponse, LLMError, LLMTimeout, LLMUnreachable
from .schemas import AgentMessage, InvalidMessage, check_message, narrowed_schema

log = logging.getLogger("sentinel.agents")

REPO_DIR = Path(__file__).resolve().parents[2]
ROUND_MAX_TOKENS = {1: 256, 2: 320}
TEMPERATURE = 0.2
REJECTED_TEXT_MAX = 600  # characters of a rejected output kept in the log and the audit entry
ATTEMPTS = 2  # first try plus one retry, for validation failures only
SPIKE_MAX_WAIT_S = 40.0  # video seconds after the alert; then deliberate with what exists
WAIT_NOTE = "waiting for the arterial spike to recover and a cuff reading"
OMNI_SOURCE_ID = "omni-{event_id}"


class Player(Protocol):
    scenario: Scenario
    clock: float
    ended: bool

    def set_context(self, event_id: str, status: str, note: str = "", timeline: Optional[str] = None) -> bool: ...


class OmniFn(Protocol):
    async def __call__(self, event: Event, scenario: Scenario, at_s: float) -> Optional[str]: ...


@dataclass
class _Run:
    event: Event
    task: Optional["asyncio.Task[None]"] = None
    slots: dict[str, AgentSlot] = field(default_factory=lambda: {a: AgentSlot() for a in AGENT_ORDER})


@dataclass
class _Turn:
    message: Optional[AgentMessage] = None
    reason: Optional[str] = None
    latency_ms: Optional[float] = None
    attempts: int = 0
    rejected_text: Optional[str] = None  # raw model text of the last output that failed validation
    completion_tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    started: float = 0.0  # perf_counter of the successful attempt's start and end
    ended: float = 0.0

    @property
    def tokens_per_s(self) -> Optional[float]:
        if not self.completion_tokens or not self.latency_ms:
            return None
        return round(self.completion_tokens / (self.latency_ms / 1000), 1)


def llm_throughput(rounds: list[list[_Turn]]) -> tuple[Optional[float], Optional[float]]:
    """(aggregate tokens/s, median per-call tokens/s) over the calls that reported usage. Aggregate = all
    completion tokens / the summed wall-clock windows of the rounds (first start to last end in each round).
    Includes prefill and queueing. None when the server reported no usage."""
    tokens, window, per_call = 0, 0.0, []
    for turns in rounds:
        ok = [t for t in turns if t.completion_tokens and t.ended > t.started]
        if not ok:
            continue
        tokens += sum(t.completion_tokens or 0 for t in ok)
        window += max(t.ended for t in ok) - min(t.started for t in ok)
        per_call += [t.tokens_per_s for t in ok if t.tokens_per_s]
    if not tokens or window <= 0 or not per_call:
        return None, None
    per_call.sort()
    return round(tokens / window, 1), per_call[len(per_call) // 2]


class Orchestrator:
    def __init__(self, store: StateStore, player: Player, patient: dict[str, Any], phase_json: dict[str, Any],
                 client: Optional[LightningClient] = None, prompts_dir: Optional[Path] = None,
                 poll_s: float = 0.2,
                 spike_max_wait_s: float = SPIKE_MAX_WAIT_S, omni: Optional[OmniFn] = None,
                 scene: Optional[Any] = None) -> None:
        self.store = store
        self.scene = scene  # LiveScene: the surgical agent's video evidence
        self.player = player
        self.patient = patient
        self.phase_json = phase_json
        self.client = client or LightningClient()
        self.poll_s = poll_s
        self.spike_max_wait_s = spike_max_wait_s
        self.omni = omni
        self.audit: Optional[AuditLog] = None  # attached by the app; every validated message and outcome is logged
        pdir = prompts_dir or Path(os.environ.get("SENTINEL_PROMPTS", REPO_DIR / "prompts"))
        # system message = the prompt file, verbatim (prompts/README.md step 1); fail loudly if missing
        self.system = {a: (pdir / f"{a}.md").read_text(encoding="utf-8") for a in AGENT_ORDER}
        self._run: Optional[_Run] = None
        self.runs_started = 0

    # ---- public --------------------------------------------------------------------------------
    @property
    def status(self) -> str:
        run = self._run
        return "idle" if run is None else ("running" if run.task and not run.task.done() else "finished")

    @property
    def task(self) -> Optional["asyncio.Task[None]"]:
        return self._run.task if self._run else None

    def on_event(self, event: Event) -> None:
        """Called by the player after the alert is published. Only schedules a task."""
        self._cancel_run()
        run = _Run(event=event)
        self._run = run
        self.runs_started += 1
        run.task = asyncio.get_running_loop().create_task(self._guarded(run), name=f"deliberation-{event.event_id}")

    def on_reset(self) -> None:
        """reset, restart and scenario switch: cancel a running deliberation, clear agents/deliberation."""
        self._cancel_run()
        self.store.update(agents={a: AgentSlot() for a in AGENT_ORDER}, deliberation=Deliberation(),
                          metrics=self.store.state.metrics.model_copy(update={"deliberation_s": None}))

    async def join(self) -> None:
        """Wait for the current deliberation to finish (tests and scripts)."""
        task = self.task
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def aclose(self) -> None:
        task = self.task
        self._cancel_run()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.client.aclose()
        closer = getattr(self.omni, "aclose", None)
        if closer is not None:
            await closer()

    # ---- run control ----------------------------------------------------------------------------
    def _cancel_run(self) -> None:
        run, self._run = self._run, None
        if run is not None and run.task is not None and not run.task.done():
            run.task.cancel()
            log.info("deliberation %s cancelled", run.event.event_id)

    def _live(self, run: _Run) -> bool:
        return self._run is run

    async def _guarded(self, run: _Run) -> None:
        try:
            await self._deliberate(run)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never leave the UI pending, and never invent output
            log.exception("deliberation %s failed", run.event.event_id)
            if self._live(run):
                note = f"internal error: {type(exc).__name__}"
                self._audit(run, "deliberation_result", f"deliberation unavailable: {note}",
                            {"outcome": None, "unavailable_agents": list(AGENT_ORDER), "reason": note})
                self._set_delib(run, status="unavailable", outcome=None)
                self.player.set_context(run.event.event_id, "unavailable", note, f"context unavailable: {note}")

    # ---- audit ----------------------------------------------------------------------------------
    def _audit(self, run: _Run, kind: str, summary: str, data: dict[str, Any], agent: Optional[str] = None) -> None:
        """Log only what the screen also shows: nothing from a run that was reset or replaced."""
        if self.audit is not None and self._live(run):
            self.audit.record(kind, summary, {"event_id": run.event.event_id, **data},
                              actor=f"agent:{agent}" if agent else "system", clock_s=self.player.clock)

    # ---- state writes ---------------------------------------------------------------------------
    def _set_slot(self, run: _Run, agent: str, **fields: Any) -> None:
        if not self._live(run):
            return
        run.slots[agent] = run.slots[agent].model_copy(update=fields)
        self.store.update(agents=dict(run.slots))

    def _set_delib(self, run: _Run, **fields: Any) -> None:
        if not self._live(run):
            return
        self.store.update(deliberation=self.store.state.deliberation.model_copy(update=fields))

    # ---- the deliberation -----------------------------------------------------------------------
    async def _deliberate(self, run: _Run) -> None:
        event, sc = run.event, self.player.scenario
        if event.event_type == "art_spike":
            await self._wait_for_spike(run, sc)
        t0 = time.perf_counter()
        ctx = EvidenceContext(sc, event, self.player.clock, self.patient, self.phase_json,
                              phase_state=self.store.state.phase, omni=None,
                              scene=self.store.state.scene,
                              video_events=self.scene.visible_events(self.player.clock) if self.scene else [])
        # Omni (optional) is asked for the surgical agent only; the other two start at once.
        omni_task = asyncio.create_task(self._omni(event, sc, self.player.clock)) if self.omni else None
        try:
            await self._rounds(run, ctx, omni_task, t0)
        finally:
            if omni_task is not None and not omni_task.done():
                omni_task.cancel()

    async def _rounds(self, run: _Run, ctx: EvidenceContext, omni_task: Optional["asyncio.Task[Optional[dict[str, str]]]"],
                      t0: float) -> None:
        event = run.event
        evidence: dict[str, AgentEvidence] = {}
        for a in ("anesthesia", "record"):
            evidence[a] = ctx.build(a)
            self._set_slot(run, a, state="thinking", status="thinking (round 1)", sees=evidence[a].sees,
                           allowed_source_ids=evidence[a].allowed_ids, reason=None)
        if omni_task is None:
            evidence["surgical"] = ctx.build("surgical")
            self._set_slot(run, "surgical", state="thinking", status="thinking (round 1)", sees=evidence["surgical"].sees,
                           allowed_source_ids=evidence["surgical"].allowed_ids, reason=None)
        else:
            self._set_slot(run, "surgical", state="waiting", status="waiting for the video model (Omni)", reason=None)
        self._set_delib(run, status="round 1", round=1, outcome="pending")
        self.player.set_context(event.event_id, "pending", "round 1: the agents read their own evidence")

        # ---- round 1: concurrent, each agent sees only its own evidence
        async def round1(agent: str) -> tuple[str, _Turn]:
            if agent == "surgical" and omni_task is not None:
                ctx.omni = await omni_task  # None on timeout or error: the block then carries `omni: null`
                evidence["surgical"] = ctx.build("surgical")
                self._set_slot(run, "surgical", state="thinking", status="thinking (round 1)",
                               sees=evidence["surgical"].sees, allowed_source_ids=evidence["surgical"].allowed_ids)
            turn = await self._turn(ctx, evidence[agent], agent, 1, [], {})
            self._record_turn(run, agent, 1, turn)
            return agent, turn
        r1 = dict(await asyncio.gather(*(round1(a) for a in AGENT_ORDER)))
        r1_msgs = {a: t.message for a, t in r1.items() if t.message is not None}
        if len(r1_msgs) < 2:
            return self._finish_unavailable(run, r1, "fewer than two agents produced a valid round-1 message")

        # ---- round 2: each agent sees the other available round-1 messages and replies once
        self._set_delib(run, status="round 2", round=2)
        self.player.set_context(event.event_id, "pending", "round 2: the agents answer each other")
        for a in r1_msgs:
            self._set_slot(run, a, state="thinking", status="thinking (round 2)")

        async def round2(agent: str) -> tuple[str, _Turn]:
            others = {o: r1_msgs.get(o) for o in AGENT_ORDER if o != agent}
            turn = await self._turn(ctx, evidence[agent], agent, 2, [o for o in others if others[o]], others,
                                    own=r1_msgs[agent])
            self._record_turn(run, agent, 2, turn)
            return agent, turn
        r2 = dict(await asyncio.gather(*(round2(a) for a in r1_msgs)))
        r2_msgs: dict[str, Optional[AgentMessage]] = {a: (r2[a].message if a in r2 else None) for a in AGENT_ORDER}
        if sum(m is not None for m in r2_msgs.values()) < 2:
            return self._finish_unavailable(run, {**r1, **r2}, "fewer than two agents produced a valid round-2 message")

        # ---- stances: set by code from the two round-2 categories; the model's note is kept
        r2_msgs = convergence.with_code_stances(r2_msgs)
        for a, m in r2_msgs.items():
            if m is not None:
                self._set_slot(run, a, message=m.model_dump())

        # ---- convergence, summary and team question: code only
        result = convergence.build(r2_msgs, ctx.phrase)
        elapsed = round(time.perf_counter() - t0, 3)
        tps_agg, tps_each = llm_throughput([list(r1.values()), list(r2.values())])
        down = [a for a in AGENT_ORDER if r2_msgs[a] is None]
        if not self._live(run):
            return
        self.store.update(deliberation=Deliberation(
            status="done", round=2, outcome=result.outcome, team_question=result.team_question,
            summary=result.summary, shared_category=result.shared_category,
            sides=[asdict(s) for s in result.sides], question_source_ids=result.question_source_ids,
            unavailable_agents=down),
            metrics=self.store.state.metrics.model_copy(update={
                "deliberation_s": elapsed, "llm_tps_aggregate": tps_agg, "llm_tps_per_agent": tps_each}))
        self._audit(run, "metrics_snapshot", f"measured numbers after deliberation {event.event_id}",
                    self._snapshot(elapsed, tps_agg, tps_each))
        note = result.outcome + (f"; unavailable: {', '.join(down)}" if down else "")
        self._audit(run, "deliberation_result", f"deliberation {result.outcome} in {elapsed:.1f} s",
                    {"outcome": result.outcome, "summary": result.summary, "team_question": result.team_question,
                     "shared_category": result.shared_category, "question_source_ids": result.question_source_ids,
                     "sides": [asdict(s) for s in result.sides], "unavailable_agents": down, "elapsed_s": elapsed})
        self.player.set_context(event.event_id, "available", note, f"context available: {note} in {elapsed:.1f} s")
        spoken = result.team_question or result.summary
        if spoken and hasattr(self.player, "offer_speech"):
            kind = "team_question" if result.team_question else "shared_view"
            self.player.offer_speech(spoken, kind=kind)
        log.info("deliberation %s %s in %.2f s (unavailable: %s)", event.event_id, result.outcome, elapsed, down or "none")

    def _snapshot(self, elapsed: float, tps_agg: Optional[float], tps_each: Optional[float]) -> dict[str, Any]:
        """The measured numbers at the end of a deliberation (None = not measured). For the pitch:
        scripts/pitch_numbers.py reads these back out of the log."""
        m = self.store.state.metrics
        return {"deliberation_s": elapsed, "llm_tps_aggregate": tps_agg, "llm_tps_per_agent": tps_each,
                "event_to_alert_ms": m.event_to_alert_ms, "gb_used": m.gb_used, "render_fps": m.render_fps,
                "detector_fps": m.detector_fps, "detector_ms": m.detector_ms, "bytes_out": m.bytes_out,
                "models_resident": m.models_resident, "scenario_id": self.player.scenario.id,
                "lightning_model": self.client.model}

    def _record_turn(self, run: _Run, agent: str, round_: int, turn: _Turn) -> None:
        """Publish one agent's result as soon as it arrives."""
        if turn.message is not None:
            dump = turn.message.model_dump()
            fields: dict[str, Any] = {"state": "done", "status": f"round {round_} done", "message": dump,
                                      "latency_ms": turn.latency_ms, "attempts": turn.attempts, "reason": None}
            if round_ == 1:
                fields["round1"] = dump
            self._set_slot(run, agent, **fields)
            m = turn.message
            self._audit(run, "agent_message", f"{agent} round {round_}: {m.category} ({m.confidence}): {m.hypothesis}",
                        {**dump, "latency_ms": turn.latency_ms, "attempts": turn.attempts,
                         "completion_tokens": turn.completion_tokens, "prompt_tokens": turn.prompt_tokens,
                         "tokens_per_s": turn.tokens_per_s}, agent)
        else:
            self._set_slot(run, agent, state="unavailable", status=f"unavailable (round {round_})",
                           reason=turn.reason, attempts=turn.attempts)
            self._audit(run, "agent_unavailable", f"{agent} round {round_} unavailable: {turn.reason}",
                        {"agent": agent, "round": round_, "reason": turn.reason, "attempts": turn.attempts,
                         "rejected_text": turn.rejected_text}, agent)

    def _finish_unavailable(self, run: _Run, turns: dict[str, _Turn], why: str) -> None:
        if not self._live(run):
            return
        down = [a for a in AGENT_ORDER if run.slots[a].state == "unavailable" or a not in turns]
        self._set_delib(run, status="unavailable", outcome=None, unavailable_agents=down,
                        summary=None, team_question=None)
        log.warning("deliberation %s unavailable: %s", run.event.event_id, why)
        self._audit(run, "deliberation_result", f"deliberation unavailable: {why}",
                    {"outcome": None, "unavailable_agents": down, "reason": why})
        self.player.set_context(run.event.event_id, "unavailable", why, f"context unavailable: {why}")

    # ---- one agent call with validation and one retry ---------------------------------------
    async def _turn(self, ctx: EvidenceContext, ev: AgentEvidence, agent: str, round_: int,
                    available_others: list[str], others: dict[str, Optional[AgentMessage]],
                    own: Optional[AgentMessage] = None) -> _Turn:
        payload: list[dict[str, Any]] = []
        if round_ == 2:
            payload = [others[o].model_dump() if others.get(o) is not None else {"agent": o, "status": "unavailable"}
                       for o in AGENT_ORDER if o != agent]
        user = build_user_message(round_, ctx.event, ctx.trigger_text, ev, payload,
                                  own=own.model_dump() if own is not None else None)
        schema = narrowed_schema(agent, round_, ev.allowed_ids, available_others)
        # The record and surgical agents cannot see the vitals: a pressure or heart-rate value in
        # their text must come from their own block, the trigger text or, in round 2, the other
        # agents' round-1 messages they were shown; anything else is invented.
        visible = (compact(ev.block) + " " + ctx.trigger_text + " " + compact(payload)
                   if agent in ("record", "surgical") else None)
        hr_delta: Optional[float] = None
        if agent == "anesthesia":  # a stated heart-rate direction must match the agent's own summary
            summ = ev.block.get("vitals", {}).get("summary", {})
            if summ.get("hr_now") is not None and summ.get("hr_60s_before") is not None:
                hr_delta = float(summ["hr_now"] - summ["hr_60s_before"])
        reason = "no attempt"
        rejected: Optional[str] = None
        for attempt in range(1, ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                res = await self.client.chat_json(self.system[agent], user, schema,
                                                  max_tokens=ROUND_MAX_TOKENS[round_], temperature=TEMPERATURE)
                rejected = res.text[:REJECTED_TEXT_MAX]
                msg = check_message(res.text, agent, round_, ev.allowed_ids, available_others,
                                    visible_text=visible, hr_delta=hr_delta)
            except (LLMTimeout, LLMUnreachable) as exc:  # no retry: the 8 s budget is spent
                log.warning("agent=%s round=%d attempt=%d error=%s", agent, round_, attempt, exc)
                return _Turn(reason=str(exc), attempts=attempt)
            except (LLMBadResponse, InvalidMessage, LLMError) as exc:
                reason = exc.reason if isinstance(exc, InvalidMessage) else str(exc)
                if not isinstance(exc, InvalidMessage):
                    rejected = None  # no usable text (cut at the token limit, empty, or HTTP error)
                log.warning("agent=%s round=%d attempt=%d invalid=%s rejected_text=%r", agent, round_, attempt, reason, rejected)
                continue
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            log.info("agent=%s round=%d attempt=%d ok category=%s confidence=%s latency_ms=%.0f",
                     agent, round_, attempt, msg.category, msg.confidence, latency_ms)
            return _Turn(message=msg, latency_ms=latency_ms, attempts=attempt, completion_tokens=res.completion_tokens,
                         prompt_tokens=res.prompt_tokens, started=started, ended=time.perf_counter())
        return _Turn(reason=f"invalid output after one retry: {reason}", attempts=ATTEMPTS, rejected_text=rejected)

    # ---- waiting for an art_spike to recover; Omni -------------------------------------------
    async def _wait_for_spike(self, run: _Run, sc: Scenario) -> None:
        event = run.event
        t_alert = int(round(event.event_timestamp))
        for a in AGENT_ORDER:
            self._set_slot(run, a, state="waiting", status="waiting for the spike to recover and a cuff reading")
        self._set_delib(run, status="waiting", round=0, outcome="pending")
        self.player.set_context(event.event_id, "pending", WAIT_NOTE)
        while True:
            clock = self.player.clock
            now = int(clock)
            recovered = recovery_s(sc.series.values["sbp"], t_alert, now) is not None
            cuff = any(n.get("t_s") is not None and t_alert <= n["t_s"] <= now for n in sc.nibp)
            if recovered and cuff:
                return
            if self.player.ended or clock >= t_alert + self.spike_max_wait_s:
                log.warning("art_spike wait gave up at t=%.1f (recovered=%s cuff=%s); deliberating anyway",
                            clock, recovered, cuff)
                return
            await asyncio.sleep(self.poll_s)

    async def _omni(self, event: Event, sc: Scenario, at_s: float) -> Optional[dict[str, str]]:
        """Optional Omni answer about the last 10 s of video. Skipped (None) when not configured,
        on timeout or on any error; the surgical block then carries `omni: null`."""
        if self.omni is None:
            return None
        try:
            answer = await asyncio.wait_for(self.omni(event, sc, at_s), timeout=getattr(self.omni, "timeout_s", 4.0) + 1.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # includes asyncio.TimeoutError
            log.warning("omni skipped: %s", exc)
            return None
        return {"source_id": OMNI_SOURCE_ID.format(event_id=event.event_id), "answer": answer} if answer else None
