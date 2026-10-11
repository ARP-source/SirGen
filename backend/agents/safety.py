"""The safety agent: a second model that checks every sentence the system wants to say before it
reaches the speak-or-hold gate.

Where the deliberation and the procedure agent propose speech, the safety agent decides `speak` or
`hold` against the current step, the procedure knowledge, what was already said and the speaking
rules. It can only hold: it never rewrites a sentence and never adds one. Emergencies never pass
through it (alerts and urgent speech never wait for a model).

Honesty rules:
  - The evidence block is built by code from live state; the proposal is quoted verbatim.
  - Output is constrained by a JSON schema and validated (one sentence, banned wording, cited ids,
    no numbers that are not in the evidence). One retry; after that the proposal passes through
    *unvetted*, and the slot, the audit log and the agents page say exactly that. Nothing is
    written in place of the model's verdict.
  - A plain code check runs first: a sentence that repeats what was said in the last 90 s is held
    by code (no model), and that is labelled as such.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional, Protocol

from ..audit import AuditLog
from ..scene import LiveScene
from ..state import AgentSlot, StateStore
from .evidence import compact
from .llm_client import LightningClient, LLMBadResponse, LLMError, LLMTimeout, LLMUnreachable
from .schemas import BANNED_PATTERNS, is_one_sentence, trim_at_word

log = logging.getLogger("sentinel.safety")

SENTENCE_MAX = 170
NOTE_MAX = 90
MAX_EVIDENCE = 3
MAX_EVENTS = 6
MAX_TOKENS = 220
TEMPERATURE = 0.1
ATTEMPTS = 2
SAID_WINDOW_S = 90.0
REPEAT_OVERLAP = 0.6
_BANNED = [re.compile(p, re.IGNORECASE) for p in BANNED_PATTERNS]
_NUM = re.compile(r"\d+(?:\.\d+)?")

RULES = [
    "The sentence must not diagnose, name a treatment or quote a risk.",
    "Every number in it must appear in the evidence it came from.",
    "It must be about the current step or a logged event, not a general remark.",
    "It must not repeat what was said in the last ninety seconds.",
    "Timing is the gate's job: do not hold a sentence only because an instrument is in use.",
    "When in doubt about relevance, hold; a held sentence is still in the log.",
]

ORIGIN_LABEL = {"procedure": "the procedure agent's concern", "deliberation": "the three deliberation agents' agreed line"}


class Player(Protocol):
    clock: float
    playing: bool

    def offer_speech(self, text: str, kind: str = "team_question", urgent: bool = False) -> None: ...


class InvalidVerdict(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def verdict_schema(allowed_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["verdict", "reason", "confidence", "evidence"],
        "properties": {
            "verdict": {"type": "string", "enum": ["speak", "hold"]},
            "reason": {"type": "string", "maxLength": SENTENCE_MAX},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "evidence": {"type": "array", "minItems": 1, "maxItems": MAX_EVIDENCE,
                         "items": {"type": "object", "additionalProperties": False, "required": ["source_id", "note"],
                                   "properties": {"source_id": {"type": "string", "enum": allowed_ids},
                                                  "note": {"type": "string", "maxLength": NOTE_MAX}}}},
        },
    }


class Verdict:
    def __init__(self, verdict: str, reason: str, confidence: str, evidence: list[dict[str, str]], by: str = "model") -> None:
        self.verdict = verdict
        self.reason = trim_at_word(reason.strip(), SENTENCE_MAX)
        self.confidence = confidence
        self.evidence = evidence
        self.by = by  # "model" | "code"

    def dump(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "reason": self.reason, "confidence": self.confidence,
                "evidence": self.evidence, "by": self.by}


def check_verdict(text: str, allowed_ids: list[str], visible_text: str) -> Verdict:
    try:
        obj = json.loads(text)
        v = Verdict(obj["verdict"], obj["reason"], obj["confidence"],
                    [{"source_id": e["source_id"], "note": e["note"]} for e in obj["evidence"]])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise InvalidVerdict("not the expected JSON shape") from exc
    if v.verdict not in ("speak", "hold"):
        raise InvalidVerdict("verdict is neither speak nor hold")
    if not v.evidence or any(e["source_id"] not in allowed_ids for e in v.evidence):
        raise InvalidVerdict("cites a source id that is not in the evidence block")
    if not v.reason or len(v.reason) > SENTENCE_MAX:
        raise InvalidVerdict("reason is empty or too long")
    if not is_one_sentence(v.reason):
        raise InvalidVerdict("reason is more than one sentence")
    for name, t in [("reason", v.reason)] + [(f"note {e['source_id']}", e["note"]) for e in v.evidence]:
        for pat in _BANNED:
            if pat.search(t):
                raise InvalidVerdict(f"banned wording in {name}: /{pat.pattern}/")
    seen = set(_NUM.findall(visible_text))
    stray = [n for n in _NUM.findall(v.reason) if n not in seen]
    if stray:
        raise InvalidVerdict(f"number not in the evidence: {stray[0]}")
    return v


class SafetyAgent:
    """`submit()` is the one entry point: it is what the procedure agent and the orchestrator call
    instead of `player.offer_speech`. Urgent speech is passed straight through."""

    def __init__(self, store: StateStore, player: Player, scene: Optional[LiveScene], steps: dict[str, Any],
                 client: LightningClient, system_prompt: str, audit: Optional[AuditLog] = None) -> None:
        self.store = store
        self.player = player
        self.scene = scene
        self.steps = steps
        self.client = client
        self.system = system_prompt
        self.audit = audit
        self.slot = AgentSlot(status="idle: checks each sentence before it is spoken")
        self.checked = 0
        self.held = 0
        self.unvetted = 0
        self._said: list[tuple[float, str]] = []
        self._tasks: set["asyncio.Task[None]"] = set()
        self._generation = 0
        self._store()

    # ---- lifecycle --------------------------------------------------------------------------
    def reset(self) -> None:
        self._generation += 1
        self._said = []
        self.checked = self.held = self.unvetted = 0
        self.slot = AgentSlot(status="idle: checks each sentence before it is spoken")
        self._store()

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await self.client.aclose()

    # ---- entry point ------------------------------------------------------------------------
    def submit(self, text: str, kind: str = "shared_view", urgent: bool = False, origin: str = "deliberation") -> None:
        """Vet `text` and, if cleared, offer it to the gate. Urgent speech never waits for a model."""
        if urgent:
            self.player.offer_speech(text, kind=kind, urgent=True)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            asyncio.run(self.vet(text, kind, origin))
            return
        task = loop.create_task(self.vet(text, kind, origin), name="safety-vet")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- evidence -----------------------------------------------------------------------------
    def evidence(self, text: str, kind: str, origin: str, now_s: float) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
        block: dict[str, Any] = {}
        allowed: list[str] = []
        sees: list[dict[str, Any]] = []

        block["proposal"] = {"source_id": "msg", "text": text, "kind": kind, "from": ORIGIN_LABEL.get(origin, origin)}
        allowed.append("msg")
        sees.append({"source_id": "msg", "text": f"Proposed ({ORIGIN_LABEL.get(origin, origin)}): {text}"})

        block["rules"] = {"source_id": "rules", "items": RULES}
        allowed.append("rules")
        sees.append({"source_id": "rules", "text": f"Speaking rules ({len(RULES)})"})

        st = self.store.state
        g = st.gate
        block["gate"] = {"source_id": "gate", "status": g.status, "instrument_in_use": g.instrument, "chip": g.chip,
                         "note": "the gate decides timing; a hold here is about content"}
        allowed.append("gate")
        sees.append({"source_id": "gate", "text": f"Gate: {g.chip}"})

        said = [t for (c, t) in self._said if now_s - c <= SAID_WINDOW_S]
        if st.speech.current and st.speech.current.text not in said:
            said.append(st.speech.current.text)
        block["said_recently"] = {"source_id": "said", "window_s": int(SAID_WINDOW_S), "items": said}
        allowed.append("said")
        sees.append({"source_id": "said", "text": f"Said in the last {int(SAID_WINDOW_S)} s: {len(said)} sentence(s)"})

        if self.scene is not None:
            view = self.scene.view(time.monotonic())
            step = view.step
            steps = self.steps.get("steps", {})
            cur = step.name or (next(iter(steps)) if steps else None)
            info = steps.get(cur, {}) if cur else {}
            block["step"] = {"source_id": "phase", "name": cur, "display": step.display or cur,
                             "for_s": None if step.since_s is None else int(max(0, now_s - step.since_s)), "stale": step.stale}
            allowed.append("phase")
            sees.append({"source_id": "phase", "text": f"Step: {block['step']['display']}"})
            block["knowledge"] = {"source_id": "proc", "goal": info.get("goal", ""), "watch": info.get("watch", ""),
                                  "common_errors": info.get("common_errors", []), "usual_next": info.get("next")}
            allowed.append("proc")
            sees.append({"source_id": "proc", "text": "Procedure knowledge for this step"})
            events = [e for e in self.scene.visible_events(now_s) if e.kind != "tool_in"][-MAX_EVENTS:]
            block["video_events"] = [{"source_id": e.source_id, "s_ago": int(max(0, now_s - e.t_s)), "kind": e.kind, "event": e.text}
                                     for e in events]
            for e in events:
                allowed.append(e.source_id)
                sees.append({"source_id": e.source_id, "text": f"{int(max(0, now_s - e.t_s))} s ago: {e.text}"})
        return block, allowed, sees

    # ---- one check ----------------------------------------------------------------------------
    async def vet(self, text: str, kind: str, origin: str) -> Optional[Verdict]:
        gen = self._generation
        now_s = self.player.clock
        block, allowed, sees = self.evidence(text, kind, origin, now_s)
        self.slot = self.slot.model_copy(update={"state": "thinking", "status": f"checking {ORIGIN_LABEL.get(origin, origin)}",
                                                 "sees": sees, "allowed_source_ids": allowed})
        self._store()

        repeat = next((t for t in block["said_recently"]["items"] if _similar(t, text)), None)
        if repeat is not None:
            v = Verdict("hold", "The sentence repeats what was said in the last ninety seconds.", "high",
                        [{"source_id": "said", "note": repeat[:NOTE_MAX]}], by="code")
            return self._decide(gen, v, text, kind, origin, now_s, 0.0, 0, sees)

        user = ("Check the proposed sentence and answer the schema. EVIDENCE: " + compact(block)
                + "\nALLOWED_SOURCE_IDS: " + compact(allowed))
        schema = verdict_schema(allowed)
        visible = compact(block)
        reason = "no attempt"
        rejected: Optional[str] = None
        for attempt in range(1, ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                res = await self.client.chat_json(self.system, user, schema, name="SafetyVerdict",
                                                 max_tokens=MAX_TOKENS, temperature=TEMPERATURE)
                rejected = res.text[:600]
                v = check_verdict(res.text, allowed, visible)
            except (LLMTimeout, LLMUnreachable) as exc:
                return self._unvetted(gen, str(exc), attempt, None, text, kind, origin, now_s)
            except (LLMBadResponse, InvalidVerdict, LLMError) as exc:
                reason = exc.reason if isinstance(exc, InvalidVerdict) else str(exc)
                log.warning("safety attempt=%d invalid=%s", attempt, reason)
                continue
            latency = round((time.perf_counter() - started) * 1000, 1)
            return self._decide(gen, v, text, kind, origin, now_s, latency, attempt, sees)
        return self._unvetted(gen, f"invalid output after one retry: {reason}", ATTEMPTS, rejected, text, kind, origin, now_s)

    def _decide(self, gen: int, v: Verdict, text: str, kind: str, origin: str, at_s: float, latency_ms: float,
                attempts: int, sees: list[dict[str, Any]]) -> Optional[Verdict]:
        if gen != self._generation:
            return None  # the run was reset while the model was thinking: a stale sentence is dropped
        self.checked += 1
        if v.verdict == "hold":
            self.held += 1
        else:
            self._said.append((at_s, text))
        word = "held" if v.verdict == "hold" else "cleared"
        self.slot = AgentSlot(status=f"{word} at t {at_s:.0f} s · {self.checked} checked, {self.held} held"
                              + (f", {self.unvetted} unvetted" if self.unvetted else ""),
                              state="done", message=self._message(v, text, origin, word), sees=sees,
                              allowed_source_ids=self.slot.allowed_source_ids, latency_ms=latency_ms or None, attempts=attempts)
        self._store()
        if self.audit is not None:
            self.audit.record("safety_verdict", f"safety agent {word} ({v.by}): {text}",
                              {**v.dump(), "proposal": text, "origin": origin, "kind": kind, "latency_ms": latency_ms,
                               "attempts": attempts}, actor="agent:safety", clock_s=at_s)
        if v.verdict == "speak":
            self.player.offer_speech(text, kind=kind)
        log.info("safety %s by %s latency_ms=%.0f: %s", word, v.by, latency_ms, v.reason)
        return v

    def _unvetted(self, gen: int, reason: str, attempts: int, rejected: Optional[str], text: str, kind: str,
                  origin: str, at_s: float) -> None:
        """The checker failed: the sentence still goes to the gate, and everything says it was not checked."""
        if gen != self._generation:
            return
        self.checked += 1
        self.unvetted += 1
        self._said.append((at_s, text))
        self.slot = self.slot.model_copy(update={
            "state": "unavailable", "status": f"unavailable at t {at_s:.0f} s · sentence passed unvetted",
            "reason": reason, "attempts": attempts,
            "message": {"agent": "safety", "round": 0, "category": "verdict", "hypothesis": "passed unvetted: the checker was unavailable",
                        "confidence": "low", "evidence": [], "responds_to": [], "verdict": "unvetted", "proposal": text,
                        "origin": origin}})
        self._store()
        if self.audit is not None:
            self.audit.record("safety_verdict", f"safety agent unavailable, sentence passed unvetted: {text}",
                              {"verdict": "unvetted", "unvetted": True, "reason": reason, "attempts": attempts,
                               "rejected_text": rejected, "proposal": text, "origin": origin, "kind": kind},
                              actor="agent:safety", clock_s=at_s)
        self.player.offer_speech(text, kind=kind)
        log.warning("safety unavailable (%s); sentence passed unvetted", reason)

    def _message(self, v: Verdict, text: str, origin: str, word: str) -> dict[str, Any]:
        return {"agent": "safety", "round": 0, "category": "verdict", "hypothesis": f"{word}: {v.reason}",
                "confidence": v.confidence, "evidence": v.evidence, "responds_to": [],
                "verdict": v.verdict, "by": v.by, "proposal": text, "origin": origin}

    def _store(self) -> None:
        self.store.update(agents={**self.store.state.agents, "safety": self.slot})


def _similar(a: str, b: str) -> bool:
    wa, wb = set(re.findall(r"[a-z]+", a.lower())), set(re.findall(r"[a-z]+", b.lower()))
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= REPEAT_OVERLAP
