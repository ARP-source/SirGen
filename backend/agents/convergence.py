"""Outcome and team question, decided by CODE (CLAUDE.md "Agent protocol",
prompts/team_question_templates.md). No model writes any of this text.

Inputs are the validated round-2 categories. An agent that is unavailable (timeout or two failed
validations) counts as `unclear`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Optional

from .schemas import AgentMessage

AGENT_ORDER = ("surgical", "anesthesia", "record")
CATEGORY_ORDER = ("bleeding_concern", "drug_effect", "artifact", "no_concern")
CONF_SCORE = {"high": 3, "medium": 2, "low": 1}
LABELS = {"bleeding_concern": "possible bleeding", "drug_effect": "drug effect",
          "artifact": "possible measurement artifact", "no_concern": "no concern"}
CHECKS = {"bleeding_concern": "Check the field for bleeding", "drug_effect": "confirm the drug timing",
          "artifact": "check whether the arterial line was just flushed or sampled",
          "no_concern": "confirm the reading"}
MAX_PHRASES = 3


@dataclass(frozen=True)
class Outcome:
    outcome: str  # "converged" | "split"
    shared_category: Optional[str] = None


@dataclass
class Side:
    category: str
    label: str
    agents: list[str]
    source_ids: list[str]
    phrases: list[str]
    hypotheses: dict[str, str] = field(default_factory=dict)


@dataclass
class Deliberated:
    outcome: str
    shared_category: Optional[str]
    summary: str
    team_question: str
    sides: list[Side]
    question_source_ids: list[str]


def categories(round2: Mapping[str, Optional[AgentMessage]]) -> dict[str, str]:
    """Round-2 category per agent; None (unavailable) counts as unclear."""
    return {a: (round2[a].category if round2.get(a) is not None else "unclear") for a in AGENT_ORDER}


def stance_for(mine: str, theirs: str) -> str:
    """Stance between two categories: same (including both `unclear`) = agree; exactly one
    `unclear` = adds; two different named categories = disagree."""
    if mine == theirs:
        return "agree"
    return "adds" if "unclear" in (mine, theirs) else "disagree"


def with_code_stances(round2: Mapping[str, Optional[AgentMessage]]) -> dict[str, Optional[AgentMessage]]:
    """Round-2 messages with every `responds_to.stance` set by code from the two round-2
    categories. The model's note is kept. A response to an agent with no round-2 message
    keeps the model's stance: there is no second category to compare."""
    out: dict[str, Optional[AgentMessage]] = {}
    for agent, msg in round2.items():
        if msg is None:
            out[agent] = None
            continue
        responses = [r.model_copy(update={"stance": stance_for(msg.category, round2[r.agent].category)})
                     if round2.get(r.agent) is not None else r for r in msg.responds_to]
        out[agent] = msg.model_copy(update={"responds_to": responses})
    return out


def decide(cats: Mapping[str, str]) -> Outcome:
    """Converged = all three equal, or two equal (named) and the third `unclear`. Split = anything else."""
    values = [cats[a] for a in AGENT_ORDER]
    if len(set(values)) == 1:
        return Outcome("converged", values[0])
    named = [v for v in values if v != "unclear"]
    if len(named) == 2 and named[0] == named[1]:
        return Outcome("converged", named[0])
    return Outcome("split", None)


def rank(round2: Mapping[str, Optional[AgentMessage]]) -> list[str]:
    """Named categories ranked by agent count, then summed confidence, then the fixed order."""
    cats = categories(round2)
    names = [c for c in CATEGORY_ORDER if any(cats[a] == c for a in AGENT_ORDER)]

    def key(c: str) -> tuple[int, int, int]:
        members = [a for a in AGENT_ORDER if cats[a] == c]
        return (-len(members), -sum(CONF_SCORE[round2[a].confidence] for a in members if round2[a]),  # type: ignore[union-attr]
                CATEGORY_ORDER.index(c))
    return sorted(names, key=key)


def evidence_ids(category: str, round2: Mapping[str, Optional[AgentMessage]]) -> list[str]:
    """Cited ids for a category: agents in it ordered by confidence (ties surgical, anesthesia, record),
    taken round-robin (each agent's first, then each agent's second, ...), duplicates skipped, at most 3."""
    members = [a for a in AGENT_ORDER if round2.get(a) is not None and round2[a].category == category]  # type: ignore[union-attr]
    members.sort(key=lambda a: (-CONF_SCORE[round2[a].confidence], AGENT_ORDER.index(a)))  # type: ignore[union-attr]
    lists = [[e.source_id for e in round2[a].evidence] for a in members]  # type: ignore[union-attr]
    out: list[str] = []
    for i in range(max((len(x) for x in lists), default=0)):
        for lst in lists:
            if i < len(lst) and lst[i] not in out:
                out.append(lst[i])
            if len(out) == MAX_PHRASES:
                return out
    return out


def build(round2: Mapping[str, Optional[AgentMessage]], phrase: Callable[[str], str]) -> Deliberated:
    """Fill the templates. `phrase(source_id)` builds an evidence phrase from the loaded data."""
    cats = categories(round2)
    result = decide(cats)
    ranked = rank(round2)

    def side(category: str) -> Side:
        ids = evidence_ids(category, round2)
        members = [a for a in AGENT_ORDER if cats[a] == category]
        return Side(category=category, label=LABELS[category], agents=members, source_ids=ids,
                    phrases=[phrase(i) for i in ids],
                    hypotheses={a: round2[a].hypothesis for a in members if round2.get(a)})  # type: ignore[union-attr]

    def ev(s: Side) -> str:
        return "; ".join(s.phrases)

    if result.outcome == "converged":
        cat = result.shared_category or "unclear"
        if cat == "unclear":
            return Deliberated("converged", "unclear", "The sources do not explain this change.",
                               "What does the team see in the field and on the monitor?", [], [])
        s = side(cat)
        summary, question = {
            "bleeding_concern": (f"The sources agree this fits possible bleeding ({ev(s)}).", "Is bleeding visible in the field?"),
            "drug_effect": (f"The sources agree this fits a drug effect ({ev(s)}).",
                            "Does the drug timing match what the anaesthesia team sees, and is the field unchanged?"),
            "artifact": (f"Likely artifact ({ev(s)}).", "Was the arterial line just flushed or sampled?"),
            "no_concern": (f"The sources see nothing that needs attention ({ev(s)}).",
                           "Does the reading match what the team sees?"),
        }[cat]
        return Deliberated("converged", cat, summary, question, [s], s.source_ids)

    a = side(ranked[0])
    if len(ranked) >= 2:
        b = side(ranked[1])
        question = (f"Two explanations fit: {a.label} ({ev(a)}) and {b.label} ({ev(b)}). "
                    f"{_cap(CHECKS[a.category])} and {CHECKS[b.category]}.")
        return Deliberated("split", None, f"The sources do not agree: {a.label} and {b.label} are shown side by side.",
                           question, [a, b], a.source_ids + [i for i in b.source_ids if i not in a.source_ids])
    question = (f"One source suggests {a.label} ({ev(a)}); the others cannot explain the change. "
                f"{_cap(CHECKS[a.category])}.")
    return Deliberated("split", None, f"One source suggests {a.label}; the others cannot explain the change.",
                       question, [a], a.source_ids)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]
