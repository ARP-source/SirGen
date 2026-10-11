"""Surgery log and summary for the post-op report, built only from the audit log of one run.

Every row points at a log entry. Nothing here is written by a model, and nothing is inferred beyond what was
logged: a step is "not observed" when no phase_change entry named it, not when it did not happen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

LOG_KINDS = ("phase_change", "video_event", "deviation", "alert", "baseline_alarm", "deliberation_result",
             "drug_given", "nibp_reading", "voice_qa", "speech_urgent", "speech_spoken", "vision_status", "control")


@dataclass
class StepRow:
    log_id: str
    name: str
    display: str
    set_by: str
    start_s: float
    end_s: Optional[float]
    instruments: list[str] = field(default_factory=list)
    deviations: int = 0
    alerts: int = 0
    entries: int = 0

    @property
    def duration_s(self) -> Optional[float]:
        return None if self.end_s is None else round(self.end_s - self.start_s, 1)


@dataclass
class LogRow:
    log_id: str
    clock_s: Optional[float]
    step: str
    kind: str
    what: str
    by: str
    flag: str = ""


@dataclass
class SurgeryLog:
    steps: list[StepRow]
    rows: list[LogRow]
    length_s: Optional[float]
    not_observed: list[str]
    out_of_usual_order: list[str]
    cvs: dict[str, Any]
    first_cut: Optional[dict[str, Any]]
    deviations: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    drugs: int
    vision: str


def _clock(e: dict[str, Any]) -> Optional[float]:
    d = e.get("data") or {}
    t = d.get("t_s") if e["kind"] in ("video_event", "deviation") else None
    t = e.get("clock_s") if t is None else t
    return None if t is None else float(t)


def _pretty(name: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).capitalize()


def _kind_label(e: dict[str, Any]) -> str:
    if e["kind"] == "video_event":
        return "video: " + str((e.get("data") or {}).get("kind", "")).replace("_", " ")
    return e["kind"].replace("_", " ")


def build_surgery_log(entries: list[dict[str, Any]], steps_ref: Optional[dict[str, Any]] = None) -> SurgeryLog:
    """`entries` are one run's audit entries in log order."""
    usual = list((steps_ref or {}).get("steps", {}))
    displays = {e["data"].get("name"): e["data"].get("display") for e in entries if e["kind"] == "phase_change"}
    ended = next((e for e in entries if e["kind"] == "segment_end"), None)
    clocks = [c for c in (_clock(e) for e in entries) if c is not None]
    length = _clock(ended) if ended and _clock(ended) is not None else (max(clocks) if clocks else None)

    steps: list[StepRow] = []
    for e in entries:
        if e["kind"] != "phase_change" or not e["data"].get("name"):
            continue
        t = _clock(e) or 0.0
        if steps and steps[-1].name == e["data"]["name"]:
            continue
        if steps:
            steps[-1].end_s = t
        steps.append(StepRow(log_id=e["id"], name=e["data"]["name"], display=e["data"].get("display") or e["data"]["name"],
                             set_by=e["data"].get("set_by") or e["actor"], start_s=t, end_s=None))
    if steps and length is not None:
        steps[-1].end_s = max(length, steps[-1].start_s)

    def step_at(t: Optional[float]) -> Optional[StepRow]:
        if t is None:
            return None
        cur = None
        for s in steps:
            if s.start_s <= t:
                cur = s
        return cur

    rows: list[LogRow] = []
    deviations: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    cvs: dict[str, Any] = {"seen_at_s": None, "log_id": None, "criteria": []}
    first_cut: Optional[dict[str, Any]] = None
    drugs = 0
    vision = "no change logged during this run"
    for e in entries:
        k, d = e["kind"], e.get("data") or {}
        if k not in LOG_KINDS:
            continue
        t = _clock(e)
        st = step_at(t)
        if k == "vision_status":
            vision = e["summary"]
        if k == "video_event" and d.get("kind") == "tool_in":
            if st is not None and d.get("tool") and d["tool"] not in st.instruments:
                st.instruments.append(d["tool"])
            continue
        flag = ""
        if k == "deviation":
            flag = "deviation"
            deviations.append({"log_id": e["id"], "clock_s": t, "what": e["summary"],
                               "why": d.get("why_it_matters", ""), "step": st.display if st else ""})
            if st:
                st.deviations += 1
        elif k == "alert":
            flag = "alert"
            alerts.append({"log_id": e["id"], "clock_s": t, "event_id": d.get("event_id"),
                           "what": d.get("event_type") or e["summary"], "outcome": None})
            if st:
                st.alerts += 1
        elif k == "deliberation_result":
            for a in alerts:
                if a["event_id"] == d.get("event_id"):
                    a["outcome"] = d.get("outcome") or "unavailable"
        elif k == "drug_given":
            drugs += 1
        elif k == "speech_urgent":
            flag = "spoken urgently"
        if k == "video_event" and d.get("kind") == "cvs":
            if d.get("criterion"):
                cvs["criteria"].append(d["criterion"])
            elif cvs["seen_at_s"] is None:
                cvs["seen_at_s"], cvs["log_id"] = t, e["id"]
        if k == "video_event" and d.get("kind") == "first_cut" and first_cut is None:
            first_cut = {"log_id": e["id"], "clock_s": t, "what": e["summary"]}
        if k == "deviation" and first_cut is None and d.get("deviation") == "clip_before_cvs":
            first_cut = {"log_id": e["id"], "clock_s": t, "what": e["summary"]}
        if st:
            st.entries += 1
        rows.append(LogRow(log_id=e["id"], clock_s=t, step=st.display if st else "before the first step",
                           kind=_kind_label(e), what=e["summary"], by=e["actor"], flag=flag))
    rows.sort(key=lambda r: (r.clock_s is None, r.clock_s or 0.0))

    seen_order: list[str] = []
    for s in steps:
        if s.name not in seen_order:
            seen_order.append(s.name)
    not_observed = [displays.get(n) or _pretty(n) for n in usual if n not in seen_order]
    ranked = [n for n in seen_order if n in usual]
    out_of_order = [displays.get(b) or _pretty(b) for a, b in zip(ranked, ranked[1:]) if usual.index(b) < usual.index(a)]
    return SurgeryLog(steps=steps, rows=rows, length_s=length, not_observed=not_observed,
                      out_of_usual_order=out_of_order, cvs=cvs, first_cut=first_cut, deviations=deviations,
                      alerts=alerts, drugs=drugs, vision=vision)
