"""Agent message schema (SPEC "Agent protocol") and source-id validation (hard rule 2).

`narrowed_schema` narrows this schema per call (fixed agent and round, enums of allowed ids,
max lengths; prompts/README.md) for `response_format: json_schema`. `check_message` is the
validation every model output must pass before it reaches the state.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, ValidationError

AgentName = Literal["surgical", "anesthesia", "record"]
Category = Literal["bleeding_concern", "drug_effect", "artifact", "unclear", "no_concern"]
Confidence = Literal["low", "medium", "high"]
Stance = Literal["agree", "disagree", "adds"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    note: str


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: AgentName
    stance: Stance
    note: str


class AgentMessage(BaseModel):
    """Field order matches the SPEC."""
    model_config = ConfigDict(extra="forbid")
    agent: AgentName
    round: Literal[1, 2]
    category: Category
    hypothesis: str
    confidence: Confidence
    evidence: list[Evidence]
    responds_to: list[Response]


@dataclass(frozen=True)
class SourceCheck:
    ok: bool
    unknown_ids: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def validate_sources(msg: AgentMessage, known_ids: Collection[str]) -> SourceCheck:
    """Every statement must cite at least one source_id, and every cited id must
    exist in the loaded data. Returns a SourceCheck; `ok` is False on any problem."""
    known = set(known_ids)
    problems: list[str] = []
    if not msg.evidence:
        problems.append("no evidence cited")
    unknown = [e.source_id for e in msg.evidence if e.source_id not in known]
    for sid in unknown:
        problems.append(f"unknown source_id: {sid}")
    return SourceCheck(ok=not problems, unknown_ids=unknown, problems=problems)


# ---- per-call schema (prompts/README.md step 4) and output validation (step 5) -----------------
HYPOTHESIS_MAX = 240
EVIDENCE_NOTE_MAX = 90
RESPONSE_NOTE_MAX = 140
EVIDENCE_MAX_ITEMS = 3
AGENTS: tuple[str, ...] = get_args(AgentName)

# prompts/README.md step 5. Case-insensitive; applied to the hypothesis and every note.
BANNED_PATTERNS: tuple[str, ...] = (
    r"diagnos", r"the patient has", r"\bis bleeding\b", r"ha?emorrhag", r"hypovol",
    r"\b(give|administer|transfuse|recommend|should|must)\b", r"%", r"percent", r"risk of",
)
_BANNED = [re.compile(p, re.IGNORECASE) for p in BANNED_PATTERNS]
_SECOND_SENTENCE = re.compile(r"[.!?]\s+[A-Z]")
# A full stop after an abbreviation or a unit is not a sentence end ("vs. HR", "approx. 277", "2 s. HR"
# is still read as a break only when what follows starts a clause: see `is_one_sentence`).
_ABBREVIATION = re.compile(r"\b(?:vs|approx|ca|e\.g|i\.e|etc|no|incl|min|max|resp)\.(?=\s)", re.IGNORECASE)


def is_one_sentence(text: str) -> bool:
    """One sentence: no sentence-ending mark followed by a new capitalised start, other than at
    the very end. Abbreviation stops ("vs.", "approx.", "e.g.") do not count as sentence ends."""
    return _SECOND_SENTENCE.search(_ABBREVIATION.sub(lambda m: m.group(0)[:-1], text.strip())) is None
# The chart is background: it can never be strong evidence of what is happening now (prompts/record.md).
MAX_CONFIDENCE: dict[str, str] = {"record": "medium"}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_N = r"(\d+(?:\.\d+)?)"
# A number stated as a vital sign: "MAP 82", "SBP jump from 122 to 277", "72 to 81 bpm", "55 mmHg".
_VITAL_NUMBER = re.compile(
    rf"\b(?:MAP|SBP|DBP|HR|SpO2)\b(?:\s+[a-z]+){{0,2}}\s+{_N}(?:\s+to\s+{_N})?"
    rf"|(?:{_N}\s+to\s+)?{_N}\s*(?:mmHg|bpm)\b", re.IGNORECASE)


def unseen_vital_numbers(texts: Sequence[str], visible_text: str) -> list[str]:
    """Vital-sign values quoted in `texts` whose number appears nowhere in `visible_text`."""
    seen = set(_NUMBER.findall(visible_text))
    return [n for t in texts for m in _VITAL_NUMBER.finditer(t) for n in m.groups() if n and n not in seen]


class InvalidMessage(ValueError):
    """The model output failed validation. `reason` is shown in the agent column."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline_refs(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]), defs)
        return {k: _inline_refs(v, defs) for k, v in node.items() if k not in ("$defs", "title", "description")}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def narrowed_schema(agent: str, round_: int, allowed_ids: Sequence[str], others: Sequence[str]) -> dict[str, Any]:
    """The AgentMessage JSON schema narrowed for one call (prompts/README.md step 4):
    agent and round fixed; `source_id` an enum of the ids in the agent's own block;
    `responds_to` empty in round 1 and exactly one item per other available agent in round 2."""
    base = AgentMessage.model_json_schema()
    s = _inline_refs(base, base.get("$defs", {}))
    props = s["properties"]
    props["agent"] = {"type": "string", "enum": [agent]}
    props["round"] = {"type": "integer", "enum": [round_]}
    props["hypothesis"] = {"type": "string", "maxLength": HYPOTHESIS_MAX}
    if agent in MAX_CONFIDENCE:
        levels = list(get_args(Confidence))
        props["confidence"] = {"type": "string", "enum": levels[:levels.index(MAX_CONFIDENCE[agent]) + 1]}
    ev = props["evidence"]
    ev["minItems"], ev["maxItems"] = 1, EVIDENCE_MAX_ITEMS
    ev["items"]["properties"]["source_id"] = {"type": "string", "enum": list(allowed_ids)}
    ev["items"]["properties"]["note"] = {"type": "string", "maxLength": EVIDENCE_NOTE_MAX}
    rt = props["responds_to"]
    n = len(others) if round_ == 2 else 0
    rt["minItems"], rt["maxItems"] = n, n
    if n:
        rt["items"]["properties"]["agent"] = {"type": "string", "enum": list(others)}
        rt["items"]["properties"]["note"] = {"type": "string", "maxLength": RESPONSE_NOTE_MAX}
    s["additionalProperties"] = False
    s["required"] = list(props)
    for sub in (ev["items"], rt["items"]):
        sub["additionalProperties"] = False
        sub["required"] = list(sub["properties"])
    return s


def check_message(text: str, agent: str, round_: int, allowed_ids: Collection[str],
                  others: Sequence[str], visible_text: Optional[str] = None,
                  hr_delta: Optional[float] = None) -> AgentMessage:
    """Parse and validate one model output. Raises InvalidMessage. Checks, in order:
    pydantic shape; agent and round as asked; every source_id is in the loaded data
    (the agent's allowed set); one-sentence hypothesis; banned wording; round-2 responses;
    the agent's confidence ceiling; and, when `visible_text` is given, that the hypothesis
    and evidence notes quote no vital-sign value that is absent from it; and, when `hr_delta`
    (measured heart-rate change, bpm) is given, that the hypothesis does not state the opposite direction."""
    try:
        msg = AgentMessage.model_validate_json(text)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise InvalidMessage(f"not a valid AgentMessage: {'.'.join(str(p) for p in first['loc'])}: {first['msg']}") from exc
    if msg.agent != agent or msg.round != round_:
        raise InvalidMessage(f"expected agent={agent} round={round_}, got agent={msg.agent} round={msg.round}")
    check = validate_sources(msg, allowed_ids)
    if not check.ok:
        raise InvalidMessage("; ".join(check.problems))
    if len(msg.evidence) > EVIDENCE_MAX_ITEMS:
        raise InvalidMessage(f"more than {EVIDENCE_MAX_ITEMS} evidence items")
    if not msg.hypothesis.strip() or len(msg.hypothesis) > HYPOTHESIS_MAX:
        raise InvalidMessage("hypothesis empty or too long")
    if not is_one_sentence(msg.hypothesis):
        raise InvalidMessage("hypothesis is more than one sentence")
    texts = [("hypothesis", msg.hypothesis)] + [(f"evidence note {e.source_id}", e.note) for e in msg.evidence] \
        + [(f"response to {r.agent}", r.note) for r in msg.responds_to]
    for where, text_ in texts:
        for pat in _BANNED:
            if pat.search(text_):
                raise InvalidMessage(f"banned wording in {where}: /{pat.pattern}/")
    if round_ == 1 and msg.responds_to:
        raise InvalidMessage("responds_to must be empty in round 1")
    if round_ == 2:
        got = sorted(r.agent for r in msg.responds_to)
        if got != sorted(others):
            raise InvalidMessage(f"responds_to must answer exactly {sorted(others)}, got {got}")
    ceiling = MAX_CONFIDENCE.get(agent)
    levels = get_args(Confidence)
    if ceiling is not None and levels.index(msg.confidence) > levels.index(ceiling):
        raise InvalidMessage(f"{agent} confidence can be at most {ceiling}, got {msg.confidence}")
    if visible_text is not None:
        unseen = unseen_vital_numbers([msg.hypothesis] + [e.note for e in msg.evidence], visible_text)
        if unseen:
            raise InvalidMessage(f"quotes vital-sign values not in its own evidence: {', '.join(unseen[:4])}")
    if hr_delta is not None:
        said = hr_trend_conflict(msg.hypothesis, hr_delta)
        if said:
            raise InvalidMessage(f"hypothesis says heart rate is {said} but it changed by {hr_delta:+g} bpm")
    return msg.model_copy(update={"hypothesis": trim_at_word(msg.hypothesis)})


_HR = r"(?:heart rate|HR)"
_DOWN = r"(?:fall\w*|fell|drop\w*|declin\w*|decreas\w*|slow\w*)"
_UP = r"(?:ris\w*|rose|increas\w*|climb\w*)"
_HR_DOWN = re.compile(rf"\b{_DOWN}\s+{_HR}\b|\b{_HR}\s+(?:is\s+|was\s+|has\s+)?{_DOWN}", re.IGNORECASE)
_HR_UP = re.compile(rf"\b{_UP}\s+{_HR}\b|\b{_HR}\s+(?:is\s+|was\s+|has\s+)?{_UP}", re.IGNORECASE)
HR_TREND_MIN_BPM = 3.0


def hr_trend_conflict(text: str, hr_delta: float) -> Optional[str]:
    """The stated heart-rate direction when it is the opposite of the measured change, else None."""
    if hr_delta >= HR_TREND_MIN_BPM and _HR_DOWN.search(text):
        return "falling"
    if hr_delta <= -HR_TREND_MIN_BPM and _HR_UP.search(text):
        return "rising"
    return None


def trim_at_word(text: str, limit: int = HYPOTHESIS_MAX) -> str:
    """A hypothesis that ran into the schema's length cap stops mid-word. Cut it back to the last
    whole word and mark the cut with an ellipsis. Shorter or complete sentences are unchanged."""
    if len(text) < limit or text.rstrip().endswith((".", "!", "?")):
        return text
    return text[:text.rstrip().rfind(" ")].rstrip(" ,;:") + " …"
