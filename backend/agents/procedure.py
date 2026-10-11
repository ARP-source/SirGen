"""The procedure agent: a continuous reading of the live scene against the procedure knowledge.

It answers, every few seconds while video plays, four things the vision models alone cannot:
what the surgeon appears to be doing, what that is for (intent), which step is likely next, and
what to watch before the next irreversible action. A `concern` is allowed only when the evidence
block holds a video observation that supports it.

Honesty rules (the same as the deliberation agents):
  - The evidence block is built by code from the live scene and the procedure knowledge file.
    Nothing comes from a scenario file. Every sentence must cite ids from that block.
  - Output is constrained by a JSON schema and validated (one sentence per field, banned wording,
    the next step must be the current step, the usual next step or the one after it, no numbers that
    are not in the evidence). A failure is retried once; after that the reading is "unavailable" with
    the reason, and nothing is written in its place.
  - A concern is spoken only through the speak-or-hold gate (never urgent, never while a hold
    instrument is in use) and only once per distinct concern.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional, Protocol

from ..audit import AuditLog
from ..scene import LiveScene, SceneEvent
from ..state import AgentSlot, ProcedureView, StateStore
from .evidence import compact
from .llm_client import LightningClient, LLMBadResponse, LLMError, LLMTimeout, LLMUnreachable
from .schemas import BANNED_PATTERNS, is_one_sentence, trim_at_word

log = logging.getLogger("sentinel.procedure")

SENTENCE_MAX = 170
NOTE_MAX = 90
MAX_EVIDENCE = 4
MAX_EVENTS = 8
MIN_GAP_S = 6.0        # a changed scene is re-read at most this often
MAX_GAP_S = 25.0       # an unchanged scene is re-read at least this often
WINDOW_S = 30
MAX_TOKENS = 360
TEMPERATURE = 0.2
ATTEMPTS = 2
CONCERN_KINDS = ("deviation", "observation", "first_cut", "caution")
_BANNED = [re.compile(p, re.IGNORECASE) for p in BANNED_PATTERNS]
_NUM = re.compile(r"\d+(?:\.\d+)?")


class Player(Protocol):
    clock: float
    playing: bool

    def offer_speech(self, text: str, kind: str = "team_question", urgent: bool = False) -> None: ...


class InvalidReading(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def reading_schema(allowed_ids: list[str], next_steps: list[str]) -> dict[str, Any]:
    sentence = {"type": "string", "maxLength": SENTENCE_MAX}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["doing", "intent", "next", "watch", "concern", "confidence", "evidence"],
        "properties": {
            "doing": sentence, "intent": sentence,
            "next": {"type": "string", "enum": next_steps},
            "watch": sentence,
            "concern": {"anyOf": [sentence, {"type": "null"}]},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "evidence": {"type": "array", "minItems": 1, "maxItems": MAX_EVIDENCE,
                         "items": {"type": "object", "additionalProperties": False, "required": ["source_id", "note"],
                                   "properties": {"source_id": {"type": "string", "enum": allowed_ids},
                                                  "note": {"type": "string", "maxLength": NOTE_MAX}}}},
        },
    }


class Reading:
    """A validated model output."""

    def __init__(self, obj: dict[str, Any]) -> None:
        cut = lambda s: trim_at_word(s.strip(), SENTENCE_MAX)  # noqa: E731  (a sentence stopped by the length cap)
        self.doing: str = cut(obj["doing"])
        self.intent: str = cut(obj["intent"])
        self.next: str = obj["next"]
        self.watch: str = cut(obj["watch"])
        self.concern: Optional[str] = cut(obj["concern"]) if obj.get("concern") else None
        self.confidence: str = obj["confidence"]
        self.evidence: list[dict[str, str]] = [{"source_id": e["source_id"], "note": e["note"]} for e in obj["evidence"]]

    def dump(self) -> dict[str, Any]:
        return {"doing": self.doing, "intent": self.intent, "next": self.next, "watch": self.watch,
                "concern": self.concern, "confidence": self.confidence, "evidence": self.evidence}


def check_reading(text: str, allowed_ids: list[str], next_steps: list[str], concern_ids: set[str],
                  visible_text: str) -> Reading:
    import json
    try:
        obj = json.loads(text)
        r = Reading(obj)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InvalidReading("not the expected JSON shape") from exc
    if r.next not in next_steps:
        raise InvalidReading(f"next step {r.next!r} is not the current, the usual next or the one after")
    if not r.evidence or any(e["source_id"] not in allowed_ids for e in r.evidence):
        raise InvalidReading("cites a source id that is not in the evidence block")
    fields = [("doing", r.doing), ("intent", r.intent), ("watch", r.watch)] + ([("concern", r.concern)] if r.concern else [])
    for name, t in fields:
        if not t or len(t) > SENTENCE_MAX:
            raise InvalidReading(f"{name} is empty or too long")
        if not is_one_sentence(t):
            raise InvalidReading(f"{name} is more than one sentence")
    for name, t in fields + [(f"note {e['source_id']}", e["note"]) for e in r.evidence]:
        for pat in _BANNED:
            if pat.search(t):
                raise InvalidReading(f"banned wording in {name}: /{pat.pattern}/")
    seen = set(_NUM.findall(visible_text))
    stray = [n for _, t in fields for n in _NUM.findall(t) if n not in seen]
    if stray:
        raise InvalidReading(f"number not in the evidence: {stray[0]}")
    if r.concern and not any(e["source_id"] in concern_ids for e in r.evidence):
        raise InvalidReading("a concern must cite a video observation, deviation, caution or the CVS block")
    return r


class ProcedureAgent:
    def __init__(self, store: StateStore, player: Player, scene: LiveScene, steps: dict[str, Any],
                 client: LightningClient, system_prompt: str, audit: Optional[AuditLog] = None,
                 min_gap_s: float = MIN_GAP_S, max_gap_s: float = MAX_GAP_S) -> None:
        self.store = store
        self.player = player
        self.scene = scene
        self.steps = steps
        self.client = client
        self.system = system_prompt
        self.audit = audit
        self.min_gap_s = min_gap_s
        self.max_gap_s = max_gap_s
        self.view = ProcedureView()
        self._last_turn_wall: float = -1e9
        self._last_fingerprint: Any = None
        self._spoken_concerns: list[str] = []
        self._task: Optional["asyncio.Task[None]"] = None
        self._generation = 0

    # ---- lifecycle --------------------------------------------------------------------------
    def reset(self) -> None:
        self._generation += 1
        self._last_turn_wall = -1e9
        self._last_fingerprint = None
        self._spoken_concerns = []
        self.view = ProcedureView()
        self._store()

    async def run(self, poll_s: float = 1.0) -> None:
        while True:
            await asyncio.sleep(poll_s)
            try:
                if self.due(time.monotonic()):
                    await self.turn()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("procedure agent turn failed")

    def due(self, now: float) -> bool:
        if not self.player.playing or not self.scene.live(now):
            return False
        fp = self.fingerprint()
        since = now - self._last_turn_wall
        if fp != self._last_fingerprint and since >= self.min_gap_s:
            return True
        return since >= self.max_gap_s

    def fingerprint(self) -> Any:
        u = self.scene._phase_update
        ev = self.scene.events[-3:]
        view = self.scene.view(time.monotonic())
        return (u.name if u else None, tuple(e.source_id for e in ev), tuple(c.name for c in view.cvs if c.met),
                len(self.scene.deviations), bool(view.caution))

    # ---- evidence -----------------------------------------------------------------------------
    def evidence(self, now_s: float) -> tuple[dict[str, Any], list[str], list[str], set[str], list[dict[str, Any]]]:
        """(block, allowed_ids, next_steps, concern_ids, sees) built by code from the live scene."""
        view = self.scene.view(time.monotonic())
        step = view.step
        steps = self.steps.get("steps", {})
        order = list(steps)
        block: dict[str, Any] = {}
        sees: list[dict[str, Any]] = []
        allowed: list[str] = []
        concern_ids: set[str] = set()

        cur = step.name or order[0]
        info = steps.get(cur, {})
        block["step"] = {"source_id": "phase", "name": cur, "display": step.display or cur,
                         "for_s": None if step.since_s is None else int(max(0, now_s - step.since_s)),
                         "set_by": "presenter" if "presenter" in (step.basis or "") else "instrument heuristic",
                         "stale": step.stale}
        allowed.append("phase")
        sees.append({"source_id": "phase", "text": f"Step: {block['step']['display']} for {block['step']['for_s']} s"})

        nxt = info.get("next")
        after = steps.get(nxt, {}).get("next") if nxt else None
        next_steps = [cur] + [s for s in (nxt, after) if s]
        block["knowledge"] = {"source_id": "proc", "goal": info.get("goal", ""), "how": info.get("how", ""),
                              "usual_instruments": info.get("instruments", []), "watch": info.get("watch", ""),
                              "common_errors": info.get("common_errors", []),
                              "usual_next": nxt, "usual_order": order, "principles": self.steps.get("principles", [])}
        allowed.append("proc")
        sees.append({"source_id": "proc", "text": f"Procedure knowledge for this step (goal, usual instruments, usual next: {nxt or 'end'})"})

        tools = self.scene.tools_seconds(WINDOW_S)
        block["tools_window"] = {"source_id": "tools", "window_s": WINDOW_S, "seconds_present": tools,
                                 "now": view.tools, "basis": "live instrument detector"}
        allowed.append("tools")
        sees.append({"source_id": "tools", "text": "Instruments in the last 30 s: "
                     + (", ".join(f"{k} {v} s" for k, v in tools.items()) or "none detected")})

        if view.cvs_available:
            met = [c.name for c in view.cvs if c.met]
            block["anatomy_cvs"] = {"source_id": "cvs", "structures_in_view": self.scene.anatomy_now(),
                                    "criteria_seen_now": met, "cvs_seen_all_three": view.cvs_achieved,
                                    "cvs_seen_s_ago": None if view.cvs_seen_at_s is None else int(max(0, now_s - view.cvs_seen_at_s))}
            allowed.append("cvs")
            concern_ids.add("cvs")
            sees.append({"source_id": "cvs", "text": f"Anatomy in view: {', '.join(self.scene.anatomy_now()) or 'none'}; "
                         f"CVS criteria seen now: {len(met)} of 3" + ("; all three seen earlier" if view.cvs_achieved else "")})
        if view.caution:
            block["caution_now"] = {"source_id": "caution", "tool": view.caution.tool, "structure": view.caution.structure_name,
                                    "kind": view.caution.kind, "note": "two detectors, screen distance only"}
            allowed.append("caution")
            concern_ids.add("caution")
            sees.append({"source_id": "caution", "text": view.caution.label})

        events: list[SceneEvent] = [e for e in self.scene.visible_events(now_s) if e.kind != "tool_in"][-MAX_EVENTS:]
        block["video_events"] = []
        for e in events:
            row = {"source_id": e.source_id, "s_ago": int(max(0, now_s - e.t_s)), "kind": e.kind, "event": e.text}
            if e.data.get("note"):
                row["note"] = e.data["note"]
            block["video_events"].append(row)
            allowed.append(e.source_id)
            if e.kind in CONCERN_KINDS:
                concern_ids.add(e.source_id)
            sees.append({"source_id": e.source_id, "text": f"{row['s_ago']} s ago: {e.text}"})
        return block, allowed, next_steps, concern_ids, sees

    # ---- one turn -----------------------------------------------------------------------------
    async def turn(self) -> None:
        gen = self._generation
        now_wall = time.monotonic()
        now_s = self.player.clock
        block, allowed, next_steps, concern_ids, sees = self.evidence(now_s)
        self._last_turn_wall = now_wall
        self._last_fingerprint = self.fingerprint()
        self._publish(state="thinking", status="reading the scene", sees=sees)
        user = ("Read the live scene and answer the schema. EVIDENCE: " + compact(block)
                + "\nALLOWED_SOURCE_IDS: " + compact(allowed)
                + "\nNEXT_STEP_OPTIONS: " + compact(next_steps))
        schema = reading_schema(allowed, next_steps)
        visible = compact(block)
        reason = "no attempt"
        rejected: Optional[str] = None
        for attempt in range(1, ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                res = await self.client.chat_json(self.system, user, schema, name="ProcedureReading",
                                                 max_tokens=MAX_TOKENS, temperature=TEMPERATURE)
                rejected = res.text[:600]
                reading = check_reading(res.text, allowed, next_steps, concern_ids, visible)
            except (LLMTimeout, LLMUnreachable) as exc:
                return self._fail(gen, str(exc), attempt, None, block["step"]["name"], now_s)
            except (LLMBadResponse, InvalidReading, LLMError) as exc:
                reason = exc.reason if isinstance(exc, InvalidReading) else str(exc)
                log.warning("procedure attempt=%d invalid=%s", attempt, reason)
                continue
            latency = round((time.perf_counter() - started) * 1000, 1)
            return self._accept(gen, reading, latency, attempt, block["step"]["name"], now_s, sees)
        self._fail(gen, f"invalid output after one retry: {reason}", ATTEMPTS, rejected, block["step"]["name"], now_s)

    def _accept(self, gen: int, r: Reading, latency_ms: float, attempts: int, step: str, at_s: float,
                sees: list[dict[str, Any]]) -> None:
        if gen != self._generation:
            return
        displays = {n: (self.steps["steps"].get(n, {}).get("display") or _pretty(n)) for n in self.steps.get("steps", {})}
        self.view = ProcedureView(status=f"read at t {at_s:.0f} s", state="done", doing=r.doing, intent=r.intent,
                                  next_name=r.next, next_display=displays.get(r.next, r.next), watch=r.watch,
                                  concern=r.concern, confidence=r.confidence, evidence=r.evidence, sees=sees,
                                  step_name=step, at_s=round(at_s, 1), latency_ms=latency_ms,
                                  turns=self.view.turns + 1, reason=None)
        self._store()
        if self.audit is not None:
            self.audit.record("procedure_reading", f"procedure agent: {r.doing}",
                              {**r.dump(), "step": step, "latency_ms": latency_ms, "attempts": attempts},
                              actor="agent:procedure", clock_s=at_s)
        if r.concern and not any(_similar(r.concern, c) for c in self._spoken_concerns):
            self._spoken_concerns.append(r.concern)
            self.player.offer_speech(r.concern, kind="shared_view")
        log.info("procedure ok latency_ms=%.0f next=%s concern=%s", latency_ms, r.next, bool(r.concern))

    def _fail(self, gen: int, reason: str, attempts: int, rejected: Optional[str], step: str, at_s: float) -> None:
        if gen != self._generation:
            return
        self.view = self.view.model_copy(update={"state": "unavailable", "status": f"unavailable at t {at_s:.0f} s",
                                                 "reason": reason})
        self._store()
        if self.audit is not None:
            self.audit.record("procedure_reading", f"procedure agent unavailable: {reason}",
                              {"unavailable": True, "reason": reason, "attempts": attempts, "rejected_text": rejected,
                               "step": step}, actor="agent:procedure", clock_s=at_s)
        log.warning("procedure unavailable: %s", reason)

    def _publish(self, **fields: Any) -> None:
        self.view = self.view.model_copy(update=fields)
        self._store()

    def _store(self) -> None:
        self.store.update(procedure=self.view, agents={**self.store.state.agents, "procedure": self.slot()})

    def slot(self) -> AgentSlot:
        """The same reading as an agent column for the agents page."""
        v = self.view
        msg = None
        if v.doing:
            msg = {"agent": "procedure", "round": 0, "category": "reading", "hypothesis": v.doing,
                   "confidence": v.confidence, "evidence": v.evidence, "responds_to": [],
                   "intent": v.intent, "next": v.next_display, "watch": v.watch, "concern": v.concern}
        return AgentSlot(status=v.status, state=v.state, message=msg, reason=v.reason, sees=v.sees,
                         latency_ms=v.latency_ms)


def _pretty(name: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).capitalize()


def _similar(a: str, b: str) -> bool:
    wa, wb = set(re.findall(r"[a-z]+", a.lower())), set(re.findall(r"[a-z]+", b.lower()))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.6
