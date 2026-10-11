"""Deterministic phase tracker from data/procedure/phase_rules.json.

Heuristic, not a validated model. The rule predicates are written in code and
checked against the JSON `when` text at load, so editing a threshold in the JSON
without updating the code fails loudly instead of silently diverging.
Not wired to the video in M1 (no detector until M2).
"""
from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

EXPECTED_WHEN: dict[str, str] = {
    "r1": "f(Hook) >= 0.5",
    "r2": "f(Clipper) >= 0.3 or f(Scissors) >= 0.3",
    "r3": "Clipper and Scissors absent for >= 20 s and f(Hook) >= 0.5 and Scissors seen at least once in this phase",
    "r4": "Clipper and Scissors absent for >= 20 s and f(Hook) >= 0.5 and Scissors never seen in this phase",
    "r5": "f(SpecimenBag) >= 0.3",
    "r6": "(f(Irrigator) >= 0.3 and f(Bipolar) >= 0.2) or f(Irrigator) >= 0.6 or (f(Bipolar) >= 0.3 and f(Hook) < 0.2)",
    "r7": "f(SpecimenBag) >= 0.3 and Irrigator and Bipolar absent for >= 10 s",
    "r8": "dwell in GallbladderPackaging >= 60 s and SpecimenBag absent for >= 10 s, then f(SpecimenBag) >= 0.3 again",
}
OOP_PHASES = {
    "oop-1": ("Irrigator", 0.3, {"CalotTriangleDissection", "ClippingCutting", "GallbladderDissection"}),
    "oop-2": ("Bipolar", 0.2, {"ClippingCutting", "GallbladderDissection"}),
}


@dataclass(frozen=True)
class Rule:
    id: str
    from_phases: frozenset[str]
    to: str


@dataclass(frozen=True)
class PhaseRules:
    tools: tuple[str, ...]
    display: dict[str, str]
    window_s: int
    confirm_steps: int
    min_dwell_s: float
    start_phase: str
    allowed: frozenset[tuple[str, str]]
    rules: tuple[Rule, ...]

    @classmethod
    def load(cls, path: Path) -> "PhaseRules":
        raw = json.loads(path.read_text())
        rules = []
        for r in raw["rules"]:
            if EXPECTED_WHEN.get(r["id"]) != r["when"]:
                raise ValueError(f"phase rule {r['id']} text changed; update phase.py: {r['when']!r}")
            froms = r["from"] if isinstance(r["from"], list) else [r["from"]]
            rules.append(Rule(r["id"], frozenset(froms), r["to"]))
        allowed = frozenset((a, b) for a, b in raw["allowed_transitions"])
        for rule in rules:
            for f in rule.from_phases:
                if (f, rule.to) not in allowed:
                    raise ValueError(f"rule {rule.id}: {f} -> {rule.to} is not an allowed transition")
        for oop in raw.get("out_of_phase_events", []):
            if oop["id"] not in OOP_PHASES:
                raise ValueError(f"unknown out-of-phase event {oop['id']}")
        return cls(
            tools=tuple(raw["tools"]), display={p["name"]: p["display"] for p in raw["phases"]},
            window_s=int(raw["evaluation"]["window_s"]), confirm_steps=int(raw["hysteresis"]["confirm_steps"]),
            min_dwell_s=float(raw["hysteresis"]["min_dwell_s"]), start_phase=raw["start_phase"],
            allowed=allowed, rules=tuple(rules),
        )


@dataclass
class PhaseUpdate:
    name: str
    display: str
    since_s: float
    tools_window: dict[str, int]
    candidate: Optional[str]
    stale: bool
    out_of_phase: list[str] = field(default_factory=list)
    set_by: str = "tracker"


class PhaseTracker:
    """Call `update(tools_present, t)` once per 1-s step. `tools_present=None`
    means the detector is unavailable: hold the phase and mark it stale."""

    def __init__(self, rules: PhaseRules, start_t: float = 0.0) -> None:
        self.rules = rules
        self.phase = rules.start_phase
        self.since = start_t
        self._start_t = start_t
        self._window: deque[frozenset[str]] = deque(maxlen=rules.window_s)
        self._last_seen: dict[str, float] = {}
        self._seen_in_phase: set[str] = set()
        self._candidate: Optional[str] = None
        self._streak = 0
        self._r8_armed = False
        self._set_by = "tracker"

    def f(self, tool: str) -> float:
        # While the opening label is still the placeholder, score against samples seen so far
        # so a hook already in view is not diluted by an empty 10-second window.
        opening = self.phase == self.rules.start_phase and self.since == self._start_t
        denom = len(self._window) if opening and self._window else self.rules.window_s
        return sum(tool in s for s in self._window) / denom

    def absent_for(self, tool: str, t: float) -> float:
        return t - self._last_seen.get(tool, self._start_t)

    def force(self, phase: str, t: float) -> None:
        """Presenter override; the label then reads 'set by presenter'."""
        if phase not in self.rules.display:
            raise ValueError(f"unknown phase {phase}")
        self._enter(phase, t)
        self._set_by = "presenter"

    def update(self, tools_present: Optional[Iterable[str]], t: float) -> PhaseUpdate:
        if tools_present is None:
            return self._result(t, stale=True)
        present = frozenset(tools_present)
        self._window.append(present)
        for tool in present:
            self._last_seen[tool] = t
        self._seen_in_phase |= present
        dwell = t - self.since
        if self.phase == "GallbladderPackaging" and dwell >= 60 and self.absent_for("SpecimenBag", t) >= 10:
            self._r8_armed = True
        candidate = self._first_match(t)
        self._streak = self._streak + 1 if candidate is not None and candidate == self._candidate else (
            1 if candidate is not None else 0)
        self._candidate = candidate
        # Preparation is a placeholder until tools say otherwise. Later phases still wait min_dwell_s.
        opening = self.phase == self.rules.start_phase and self.since == self._start_t
        dwell_ok = opening or dwell >= self.rules.min_dwell_s
        if (candidate is not None and self._streak >= self.rules.confirm_steps
                and dwell_ok and (self.phase, candidate) in self.rules.allowed):
            self._enter(candidate, t)
        return self._result(t, stale=False)

    # ---- internals -----------------------------------------------------------------
    def _enter(self, phase: str, t: float) -> None:
        self.phase, self.since = phase, t
        self._seen_in_phase = set()
        self._candidate, self._streak, self._r8_armed = None, 0, False
        self._set_by = "tracker"

    def _predicates(self, t: float) -> dict[str, Callable[[], bool]]:
        f, absent = self.f, (lambda tool: self.absent_for(tool, t))
        clip_scissors_gone = lambda: absent("Clipper") >= 20 and absent("Scissors") >= 20 and f("Hook") >= 0.5
        return {
            "r1": lambda: f("Hook") >= 0.5,
            "r2": lambda: f("Clipper") >= 0.3 or f("Scissors") >= 0.3,
            "r3": lambda: clip_scissors_gone() and "Scissors" in self._seen_in_phase,
            "r4": lambda: clip_scissors_gone() and "Scissors" not in self._seen_in_phase,
            "r5": lambda: f("SpecimenBag") >= 0.3,
            "r6": lambda: ((f("Irrigator") >= 0.3 and f("Bipolar") >= 0.2) or f("Irrigator") >= 0.6
                          or (f("Bipolar") >= 0.3 and f("Hook") < 0.2)),
            "r7": lambda: f("SpecimenBag") >= 0.3 and absent("Irrigator") >= 10 and absent("Bipolar") >= 10,
            "r8": lambda: self._r8_armed and f("SpecimenBag") >= 0.3,
        }

    def _first_match(self, t: float) -> Optional[str]:
        preds = self._predicates(t)
        for rule in self.rules.rules:
            if self.phase in rule.from_phases and preds[rule.id]():
                return rule.to
        return None

    def _result(self, t: float, stale: bool) -> PhaseUpdate:
        oop = [oid for oid, (tool, thr, phases) in OOP_PHASES.items()
               if self.phase in phases and self.f(tool) >= thr]
        counts = {tool: sum(tool in s for s in self._window) for tool in self.rules.tools}
        return PhaseUpdate(name=self.phase, display=self.rules.display[self.phase], since_s=self.since,
                           tools_window={k: v for k, v in counts.items() if v}, candidate=self._candidate,
                           stale=stale, out_of_phase=oop, set_by=self._set_by)
