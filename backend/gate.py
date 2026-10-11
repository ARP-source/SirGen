"""Speak-or-hold: when Sentinel may talk to the surgeon.

HOLD while a cutting, clipping, energy or hook-dissection instrument is in view
(Clipper, Scissors, Bipolar, Hook). OPEN when only Grasper / Irrigator (or nothing)
is in view, after a 1.5 s settle on the scenario clock.

Non-urgent speech (the team question after deliberation) queues while HOLD and is
released when OPEN, or when the surgeon asks. Urgent speech (demo thresholds:
MAP < 55 mmHg or SpO2 < 90 %) is never held. The visual alert banner is not gated
(hard rule 3).

Tools come only from the live detector via `set_live_tools()`. With no recent model
result the instruments are unknown, the gate stays open and the on-screen `basis`
label says the detector is unavailable.
"""
from __future__ import annotations

from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, Field

HOLD_TOOLS = ("Clipper", "Scissors", "Bipolar", "Hook")
HOLD_SET = frozenset(HOLD_TOOLS)
SETTLE_S = 1.5
URGENT_MAP = 55.0
URGENT_SPO2 = 90.0

HOLD_LABEL = {
    "Clipper": "clipping",
    "Scissors": "cutting",
    "Bipolar": "energy",
    "Hook": "dissection",
}

LogFn = Callable[..., Any]


class SpeechItem(BaseModel):
    id: str
    text: str
    kind: Literal["team_question", "shared_view", "urgent_vitals", "safety_check"]
    urgent: bool = False
    created_t: float
    spoken_t: Optional[float] = None
    held_s: Optional[float] = None
    release_reason: Optional[str] = None


class GateView(BaseModel):
    status: Literal["hold", "open"] = "open"
    settling: bool = False
    instrument: Optional[str] = None
    tools: list[str] = Field(default_factory=list)
    basis: str = "detector unavailable: instruments unknown"
    settle_remaining_s: float = 0.0
    chip: str = "Clear to speak"
    thresholds_note: str = "Urgent speech demo thresholds: MAP < 55 mmHg or SpO2 < 90 %. Not clinical."


class SpeechView(BaseModel):
    queued: list[SpeechItem] = Field(default_factory=list)
    current: Optional[SpeechItem] = None
    caption: Optional[str] = None
    held_s: Optional[float] = None
    release_reason: Optional[str] = None


def hold_instrument(tools: list[str]) -> Optional[str]:
    present = set(tools)
    for name in HOLD_TOOLS:
        if name in present:
            return name
    return None


DETECTOR_UNAVAILABLE = "detector unavailable: instruments unknown"


def tools_at(live_tools: Optional[list[str]]) -> tuple[list[str], str]:
    """Instruments from the live detector only. None means no recent model result."""
    if live_tools is None:
        return [], DETECTOR_UNAVAILABLE
    return sorted(live_tools), "live detector"


class SpeechGate:
    """Pure scenario-clock gate. Pause does not advance settle time."""

    def __init__(self, log: Optional[LogFn] = None) -> None:
        self._log = log
        self.live_tools: Optional[list[str]] = None
        self.reset()

    def reset(self) -> None:
        self._seq = 0
        self._queue: list[SpeechItem] = []
        self._current: Optional[SpeechItem] = None
        self._hold_since: Optional[float] = None
        self._clear_since: Optional[float] = None
        self._urgent_active = False
        self._tools: list[str] = []
        self._basis = DETECTOR_UNAVAILABLE
        self._clock = 0.0

    def set_live_tools(self, tools: Optional[list[str]]) -> None:
        """Instruments from the live scene. Pass None when the detector is down."""
        self.live_tools = None if tools is None else list(tools)

    def on_clock(self, clock_s: float, tools: list[str], basis: str,
                 map_: Optional[float] = None, spo2: Optional[float] = None) -> None:
        prev_hold = hold_instrument(self._tools)
        self._clock = clock_s
        self._tools = list(tools)
        self._basis = basis
        inst = hold_instrument(tools)
        if inst is not None:
            if self._hold_since is None:
                self._hold_since = clock_s
            self._clear_since = None
        else:
            if prev_hold is not None and self._clear_since is None:
                self._clear_since = clock_s
            self._hold_since = None
            if self.status == "open" and self._queue:
                self._release("instruments withdrawn")
        self._maybe_urgent(map_, spo2)

    def offer(self, text: str, kind: Literal["team_question", "shared_view", "urgent_vitals", "safety_check"] = "team_question",
              urgent: bool = False) -> SpeechItem:
        self._seq += 1
        item = SpeechItem(id=f"sp-{self._seq}", text=text, kind=kind, urgent=urgent, created_t=round(self._clock, 2))
        if urgent or self.status == "open":
            reason = ("safety check before clip or cut (not held)" if kind == "safety_check"
                      else "urgent (demo threshold)") if urgent else "clear to speak"
            self._speak(item, reason)
            if urgent:
                self._record("speech_urgent", f"urgent speech (not held): {text}",
                             {**item.model_dump(), "reason": reason})
        else:
            inst = hold_instrument(self._tools)
            label = HOLD_LABEL.get(inst or "", inst or "instrument")
            self._queue.append(item)
            self._record("speech_held", f"held while {label}: {text}",
                         {**item.model_dump(), "instrument": inst, "tools": list(self._tools)})
        return item

    def surgeon_asks(self) -> Optional[SpeechItem]:
        """Release queued non-urgent speech even while HOLD."""
        if not self._queue:
            return self._current
        return self._release("surgeon asks")

    @property
    def status(self) -> Literal["hold", "open"]:
        inst = hold_instrument(self._tools)
        if inst is not None:
            return "hold"
        if self._clear_since is None:
            return "open"
        if self._clock - self._clear_since >= SETTLE_S:
            return "open"
        return "hold"  # still settling: keep holding speech

    def view(self) -> GateView:
        inst = hold_instrument(self._tools)
        settling = inst is None and self._clear_since is not None and (self._clock - self._clear_since) < SETTLE_S
        remaining = 0.0
        if settling and self._clear_since is not None:
            remaining = round(max(0.0, SETTLE_S - (self._clock - self._clear_since)), 2)
        if inst is not None:
            chip = f"Holding: {HOLD_LABEL.get(inst, inst)}"
            status: Literal["hold", "open"] = "hold"
        elif settling:
            chip = f"Holding: settling {remaining:.1f} s"
            status = "hold"
        else:
            chip = "Clear to speak"
            status = "open"
        return GateView(status=status, settling=settling, instrument=inst, tools=list(self._tools),
                        basis=self._basis, settle_remaining_s=remaining, chip=chip)

    def speech_view(self) -> SpeechView:
        cur = self._current
        return SpeechView(queued=list(self._queue), current=cur,
                          caption=None if cur is None else cur.text,
                          held_s=None if cur is None else cur.held_s,
                          release_reason=None if cur is None else cur.release_reason)

    def _maybe_urgent(self, map_: Optional[float], spo2: Optional[float]) -> None:
        trip = (map_ is not None and map_ < URGENT_MAP) or (spo2 is not None and spo2 < URGENT_SPO2)
        if not trip:
            self._urgent_active = False
            return
        if self._urgent_active:
            return
        self._urgent_active = True
        parts = []
        if map_ is not None and map_ < URGENT_MAP:
            parts.append(f"MAP {map_:.0f} mmHg (demo threshold {URGENT_MAP:.0f})")
        if spo2 is not None and spo2 < URGENT_SPO2:
            parts.append(f"SpO2 {spo2:.0f} % (demo threshold {URGENT_SPO2:.0f})")
        text = "; ".join(parts) + ". Demo threshold, not a clinical alarm."
        self.offer(text, kind="urgent_vitals", urgent=True)

    def _release(self, reason: str) -> Optional[SpeechItem]:
        if not self._queue:
            return self._current
        last = None
        queued = list(self._queue)
        self._queue.clear()
        for item in queued:
            last = self._speak(item, reason)
        self._record("speech_released", f"released ({reason}): {len(queued)} message(s)",
                     {"reason": reason, "count": len(queued), "ids": [i.id for i in queued]})
        return last

    def _speak(self, item: SpeechItem, reason: str) -> SpeechItem:
        held = round(max(0.0, self._clock - item.created_t), 2)
        spoken = item.model_copy(update={"spoken_t": round(self._clock, 2), "held_s": held, "release_reason": reason})
        self._current = spoken
        self._record("speech_spoken", spoken.text,
                     {**spoken.model_dump(), "reason": reason})
        return spoken

    def _record(self, kind: str, summary: str, data: Optional[dict[str, Any]] = None) -> None:
        if self._log is not None:
            self._log(kind, summary, data or {}, actor="gate")
