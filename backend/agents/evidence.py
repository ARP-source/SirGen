"""Per-agent evidence blocks (prompts/README.md step 2 and 3) and the evidence phrases
used by the deterministic team question (prompts/team_question_templates.md section 3).

Each agent gets ONLY its own block:
  surgical   phase, 10 s tool window, surgical events with t <= now, out-of-phase observations, Omni answer
  anesthesia vitals trend (1 Hz series), cuff readings and drug log with t <= now
  record     the patient chart (whole chart by default; RECORD_EVIDENCE=subset uses the scenario's
             `agent_evidence.record` ids). Entries whose value is null are left out.

All times are `rel_alert_s = t_s - alert_t_s`, computed here so the model never does time arithmetic.
Everything is built from the loaded scenario, state and chart. Nothing is invented: where this
prototype has no live detector (M2), the phase, tool window and surgical events come from the
scenario file and are labelled so in the block (`basis` / `set_by`) and in the UI.
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Optional

from ..phase import OOP_PHASES
from ..scenario import Scenario
from ..state import Event, PhaseState

CHART_SECTIONS = ("demographics", "encounter", "history", "medications", "allergies", "labs", "notes")
TOOLS_WINDOW_S = 10
RECOVERY_BAND_MMHG = 10.0
# Presentation metadata (real / synthetic / simulated / pending). Shown in the UI, never sent to the
# agents: a provenance tag is not a clinical finding.
CHART_METADATA_KEYS = ("provenance", "verification", "fill_from")
SPIKE_NAMES = {"art_spike": "arterial SBP jumps > 50 mmHg within 2 s"}


@dataclass
class AgentEvidence:
    agent: str
    block: dict[str, Any]
    allowed_ids: list[str]
    sees: list[dict[str, Any]] = field(default_factory=list)  # [{"source_id": ..|None, "text": ..}] for the UI


def compact(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def flatten_chart(patient: dict[str, Any]) -> list[dict[str, Any]]:
    """Chart entries in file order, copied as they are, without null-valued entries."""
    out: list[dict[str, Any]] = []
    for section in CHART_SECTIONS:
        for entry in patient.get(section, []):
            if "value" in entry and entry["value"] is None:
                continue
            out.append(entry)
    return out


def recovery_s(sbp: list[Optional[float]], t_alert: int, now_s: int) -> Optional[int]:
    """Seconds from the alert until arterial SBP is back within 10 mmHg of its pre-alert value,
    measured after the peak. None while it has not recovered by `now_s`."""
    pre_vals = [v for v in sbp[max(0, t_alert - 30):max(0, t_alert - 2)] if v is not None]
    pre = statistics.median(pre_vals) if pre_vals else sbp[0]
    window = [(t, sbp[t]) for t in range(t_alert, min(now_s, len(sbp) - 1) + 1) if sbp[t] is not None]
    if pre is None or not window:
        return None
    peak_t = max(window, key=lambda p: p[1])[0]
    for t, v in window:
        if t > peak_t and abs(v - pre) <= RECOVERY_BAND_MMHG:
            return t - t_alert
    return None


class EvidenceContext:
    """Everything needed to build the three blocks for one event. `now_s` is when round 1 starts."""

    def __init__(self, scenario: Scenario, event: Event, now_s: float, patient: dict[str, Any],
                 phase_json: dict[str, Any], phase_state: Optional[PhaseState] = None,
                 omni: Optional[dict[str, str]] = None, record_mode: str = "full") -> None:
        self.sc = scenario
        self.event = event
        self.t_alert = int(round(event.event_timestamp))
        self.now_s = max(int(now_s), self.t_alert)
        self.patient = patient
        self.phase_json = phase_json
        self.phase_display = {p["name"]: p["display"] for p in phase_json.get("phases", [])}
        self.phase_state = phase_state or PhaseState()
        self.omni = omni
        self.record_mode = record_mode
        self.chart = flatten_chart(patient)
        self.by_id: dict[str, dict[str, Any]] = {e["source_id"]: e for e in self.chart}
        self._surgical: Optional[AgentEvidence] = None

    # ---- helpers ------------------------------------------------------------------------------
    def rel(self, t_s: Optional[float]) -> Optional[int]:
        return None if t_s is None else int(round(t_s - self.t_alert))

    def _rel_text(self, rel: int) -> str:
        if rel == 0:
            return "at the alert"
        return f"{abs(rel)} s before the alert" if rel < 0 else f"{rel} s after the alert"

    def _vit(self, t: int) -> dict[str, Optional[float]]:
        return self.sc.series.at(t)

    @property
    def trigger_text(self) -> str:
        note = self.sc.event_note
        if self.event.trigger == "presenter":
            note += " (This alert was raised by the presenter's manual trigger, not by the rule.)"
        return note

    @property
    def visible_events(self) -> list[dict[str, Any]]:
        return [e for e in self.sc.raw.get("surgical_events", []) if e["t_s"] <= self.now_s]

    def baseline_start(self) -> int:
        return max(self.t_alert - 60, 0)

    # ---- surgical -------------------------------------------------------------------------------
    def surgical(self) -> AgentEvidence:
        if self._surgical is not None:
            return self._surgical
        block: dict[str, Any] = {}
        sees: list[dict[str, Any]] = []
        ps = self.phase_state
        phase_name: Optional[str] = None
        if ps.name:
            phase_name = ps.name
            stale = "stale" in (ps.status or "").lower() or "unavailable" in (ps.status or "").lower()
            block["phase"] = {"source_id": "phase", "display": ps.display or self.phase_display.get(ps.name, ps.name),
                              "since_rel_alert_s": self.rel(ps.since_s), "stale": stale,
                              "set_by": "presenter" if ps.set_by == "presenter" else "detector heuristic"}
        elif self.sc.raw.get("procedure_phase_at_event") in self.phase_display:
            phase_name = self.sc.raw["procedure_phase_at_event"]
            block["phase"] = {"source_id": "phase", "display": self.phase_display[phase_name],
                              "since_rel_alert_s": None, "stale": False,
                              "set_by": "scenario file (no live detector yet)"}
        if "phase" in block:
            p = block["phase"]
            sees.append({"source_id": "phase", "text": f"Phase: {p['display']} (set by {p['set_by']})"})

        events = self.visible_events
        latest = max(events, key=lambda e: e["t_s"]) if events else None
        tools: dict[str, int] = dict(ps.tools_window) if ps.tools_window else {}
        tools_rel = 0
        basis = "detector window"
        if not tools and latest is not None:
            tools = {t: TOOLS_WINDOW_S for t in latest.get("tools", [])}
            tools_rel = self.rel(latest["t_s"]) or 0
            basis = "scenario surgical_events (no live detector yet)"
        if tools:
            block["tools_window"] = {"source_id": "tools", "window_s": TOOLS_WINDOW_S,
                                     "seconds_present": tools, "basis": basis}
            sees.append({"source_id": "tools", "text": "Tools in the last 10 s: "
                         + ", ".join(f"{k} {v} s" for k, v in tools.items()) + f" ({basis})"})

        block["surgical_events"] = []
        for e in events:
            rel = self.rel(e["t_s"])
            block["surgical_events"].append({"source_id": e["source_id"], "rel_alert_s": rel,
                                             "event": e["event"], "tools": e.get("tools", [])})
            sees.append({"source_id": e["source_id"], "text": f"{self._rel_text(rel)}: {e['event']}"})

        block["observations"] = []
        for oop in self.phase_json.get("out_of_phase_events", []):
            rule = OOP_PHASES.get(oop["id"])
            if rule is None or phase_name is None:
                continue
            tool, threshold, phases = rule
            if phase_name in phases and tools.get(tool, 0) / TOOLS_WINDOW_S >= threshold:
                block["observations"].append({"source_id": oop["id"], "rel_alert_s": tools_rel,
                                              "event": oop["event"], "note": oop["note"]})
                sees.append({"source_id": oop["id"], "text": f"{oop['event']} (code observation, not a diagnosis)"})

        if self.omni:
            block["omni"] = {"source_id": self.omni["source_id"], "answer": self.omni["answer"]}
            sees.append({"source_id": self.omni["source_id"],
                         "text": f"Video model (Omni) description of the last 10 s: {self.omni['answer']}"})
        else:
            block["omni"] = None

        allowed = [i for i, key in (("phase", "phase"), ("tools", "tools_window")) if key in block]
        allowed += [e["source_id"] for e in block["surgical_events"]]
        allowed += [o["source_id"] for o in block["observations"]]
        if self.omni:
            allowed.append(self.omni["source_id"])
        self._surgical = AgentEvidence("surgical", block, allowed, sees)
        return self._surgical

    # ---- anesthesia -------------------------------------------------------------------------------
    def _sample_times(self) -> list[int]:
        start, now, alert = self.baseline_start(), self.now_s, self.t_alert
        grid = list(range(start, now + 1, 10))
        times = set(grid) | {now}
        times |= set(range(max(alert - 10, start), min(alert + 10, now) + 1, 2))  # every 2 s near the alert
        for a, b in zip(grid, grid[1:]):  # and wherever SBP moves > 20 mmHg between 10 s samples
            sa, sb = self._vit(a)["sbp"], self._vit(b)["sbp"]
            if sa is not None and sb is not None and abs(sb - sa) > 20:
                times |= set(range(a, b + 1, 2))
        return sorted(times)

    def _num(self, v: Optional[float]) -> Optional[int]:
        return None if v is None else int(round(v))

    def anesthesia(self) -> AgentEvidence:
        sc, start, now = self.sc, self.baseline_start(), self.now_s
        samples = []
        for t in self._sample_times():
            row = {"rel_alert_s": t - self.t_alert}
            row.update({k: self._num(v) for k, v in self._vit(t).items() if v is not None})
            samples.append(row)
        v0, v1 = self._vit(start), self._vit(now)
        window = [self._vit(t) for t in range(start, now + 1)]
        maps = [w["map"] for w in window if w["map"] is not None]
        sbps = [w["sbp"] for w in window if w["sbp"] is not None]
        summary: dict[str, Any] = {
            "baseline_rel_alert_s": start - self.t_alert,
            "map_60s_before": self._num(v0["map"]), "map_now": self._num(v1["map"]),
            "map_min": self._num(min(maps)) if maps else None,
            "sbp_max": self._num(max(sbps)) if sbps else None,
            "hr_60s_before": self._num(v0["hr"]), "hr_now": self._num(v1["hr"]),
            "spo2_now": self._num(v1["spo2"]),
        }
        if self.event.event_type == "art_spike":
            summary["sbp_back_near_baseline_after_s"] = recovery_s(sc.series.values["sbp"], self.t_alert, now)
        summary = {k: v for k, v in summary.items() if v is not None or k == "sbp_back_near_baseline_after_s"}
        cuff = []
        for n in sc.nibp:
            if n.get("t_s") is not None and n["t_s"] <= now:
                row = {"source_id": n["source_id"], "rel_alert_s": self.rel(n["t_s"])}
                row.update({k: self._num(n[k]) for k in ("sbp", "dbp", "map") if n.get(k) is not None})
                cuff.append(row)
        drugs = []
        for d in sc.drug_log:
            if d.get("t_s") is None or d["t_s"] <= now:
                drugs.append({"source_id": d["source_id"], "rel_alert_s": self.rel(d.get("t_s")),
                              "drug": d["drug"], "detail": d["detail"]})
        block = {"vitals": {"source_id": "vit-art", "samples": samples, "summary": summary},
                 "cuff": cuff, "drug_log": drugs}
        sees = [{"source_id": "vit-art", "text": f"Arterial trend {-summary['baseline_rel_alert_s']} s before the alert to now: "
                 f"MAP {summary.get('map_60s_before', '--')} to {summary.get('map_now', '--')}, "
                 f"HR {summary.get('hr_60s_before', '--')} to {summary.get('hr_now', '--')}, "
                 f"SpO2 {summary.get('spo2_now', '--')} ({sc.series.source_kind})"}]
        sees += [{"source_id": c["source_id"], "text": self.phrase(c["source_id"])} for c in cuff]
        for d in drugs:
            when = "time not recorded" if d["rel_alert_s"] is None else self._rel_text(d["rel_alert_s"])
            sees.append({"source_id": d["source_id"], "text": f"{d['drug']}: {d['detail']} ({when})"})
        allowed = ["vit-art"] + [c["source_id"] for c in cuff] + [d["source_id"] for d in drugs]
        return AgentEvidence("anesthesia", block, allowed, sees)

    # ---- record -------------------------------------------------------------------------------------
    def record(self) -> AgentEvidence:
        entries = self.chart
        if self.record_mode == "subset":
            wanted = [i for i in self.sc.raw.get("agent_evidence", {}).get("record", []) if i in self.by_id]
            entries = [self.by_id[i] for i in wanted]
        note = (f"patient chart, {len(entries)} entries " +
                ("(whole chart; null values left out)" if self.record_mode != "subset" else "(scenario subset; null values left out)"))
        sees = [{"source_id": None, "text": note}]
        if self.record_mode == "subset":
            sees += [{"source_id": e["source_id"], "text": self.phrase(e["source_id"])} for e in entries]
        chart = [{k: v for k, v in e.items() if k not in CHART_METADATA_KEYS} for e in entries]
        return AgentEvidence("record", {"chart": chart}, [e["source_id"] for e in entries], sees)

    def build(self, agent: str) -> AgentEvidence:
        return {"surgical": self.surgical, "anesthesia": self.anesthesia, "record": self.record}[agent]()

    # ---- phrases for the team question (templates section 3) -----------------------------------
    def phrase(self, sid: str) -> str:
        """A short phrase built from the loaded record behind `sid`, never from the model's note."""
        if sid == "phase":
            d = self.surgical().block.get("phase", {}).get("display")
            return f"phase: {d.lower()}" if d else "phase"
        if sid == "tools":
            tw = self.surgical().block.get("tools_window", {}).get("seconds_present", {})
            names = [k.lower() for k, v in tw.items() if v >= 3]
            return (_join_and(names) + " in view") if names else "tools in view"
        if sid.startswith("surg-"):
            e = next((x for x in self.sc.raw.get("surgical_events", []) if x["source_id"] == sid), None)
            if e is None:
                return sid
            text = re.sub(r"\s*\([^)]*\)", "", e["event"].split(";")[0]).strip()
            rel = self.rel(e["t_s"])
            when = f"from {abs(rel)} s before the alert" if rel < 0 else self._rel_text(rel)
            return f"{_lower_first(text)}, {when}"
        if sid.startswith("oop-"):
            o = next((x for x in self.phase_json.get("out_of_phase_events", []) if x["id"] == sid), None)
            return _lower_first(o["event"]) if o else sid
        if sid.startswith("omni-"):
            return "video review of the last 10 s"
        if sid.startswith("drug-"):
            d = next((x for x in self.sc.drug_log if x["source_id"] == sid), None)
            if d is None:
                return sid
            m = re.search(r"\b(increase|decrease|bolus|change)", d["detail"], re.IGNORECASE)
            if d.get("t_s") is None or m is None:
                return f"{d['drug']} running"
            return f"{d['drug']} {m.group(1).lower()} {abs(self.rel(d['t_s']))} s before the alert" \
                if self.rel(d["t_s"]) <= 0 else f"{d['drug']} {m.group(1).lower()} {self.rel(d['t_s'])} s after the alert"
        if sid == "vit-art":
            return self._vit_phrase()
        if sid.startswith("vit-nibp-"):
            n = next((x for x in self.sc.nibp if x["source_id"] == sid), None)
            if n is None:
                return sid
            return f"cuff {self._num(n.get('sbp'))}/{self._num(n.get('dbp'))}, {self._rel_text(self.rel(n['t_s']))}"
        e = self.by_id.get(sid)
        if e is None:
            return sid
        return _chart_phrase(sid, e)

    def _vit_phrase(self) -> str:
        start, now = self.baseline_start(), self.now_s
        if self.event.event_type == "art_spike":
            sbp = self.sc.series.values["sbp"]
            vals = [v for v in sbp[self.t_alert:now + 1] if v is not None]
            peak = self._num(max(vals)) if vals else None
            k = recovery_s(sbp, self.t_alert, now)
            tail = f"back near baseline within {k} s" if k is not None else "not back near baseline yet"
            return f"arterial SBP peak {peak}, {tail}"
        a, b = self._vit(start), self._vit(now)
        return (f"MAP {self._num(a['map'])} to {self._num(b['map'])} and HR {self._num(a['hr'])} to "
                f"{self._num(b['hr'])} over the last {now - start} s")


def _join_and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:]


def _chart_phrase(sid: str, e: dict[str, Any]) -> str:
    kind = sid.split("-")[0]
    if kind == "meds":
        h = e.get("last_dose_h_before_incision")
        return f"{e['drug']} last dose about {h} h before incision" if h is not None else f"{e['drug']} on the chart"
    if kind == "lab":
        test = re.sub(r"\s*\(pre-op\)", "", e.get("test", "")).strip()
        return " ".join(str(x) for x in (test, e.get("value"), e.get("unit")) if x not in (None, ""))
    if kind == "hx":
        return _lower_first(e.get("condition", sid))
    if kind == "enc":
        v = e.get("value")
        v = _lower_first(v) if isinstance(v, str) else json.dumps(v)
        return f"{str(e.get('field', sid)).replace('_', ' ')}: {v}"
    if kind == "note":
        return e.get("type", sid)
    if kind == "alg":
        return f"{e.get('substance', 'unknown')} intolerance"
    if kind == "demo":
        return f"{e.get('field', sid)}: {e.get('value')}{(' ' + e['unit']) if e.get('unit') else ''}"
    return sid


def round_two_rule(agent: str, own: str, others: list[dict[str, Any]]) -> str:
    """Which round-2 rule of the agent's prompt applies, picked by code from the round-1 categories
    alone (never from the scenario). The model still judges the evidence and writes the message."""
    named = {o["agent"]: o["category"] for o in others if o.get("category") not in (None, "unclear")}
    theirs = sorted(set(named.values()))
    chart = agent == "record"
    if own != "unclear":
        lone_other = len(theirs) == 1 and theirs[0] != own
        if chart and lone_other:
            who = " and ".join(named)
            return (f"You named {own}. {who} names {theirs[0]} from what is happening now and nobody names {own}. "
                    f"Adopt {theirs[0]} at low if at least one chart entry supports it, otherwise keep {own}. "
                    "Keep the chart facts that bear on the alert in your evidence.")
        return (f"You named {own}. Keep {own} unless another agent cites evidence that directly contradicts "
                "an item you cited. Different or missing evidence is not a contradiction. You may lower your confidence.")
    if not theirs:
        return "You were unclear and no other agent names a category. Stay unclear."
    if len(theirs) == 1:
        who = " and ".join(named)
        cond = ("if at least one chart entry supports it, citing that entry; otherwise stay unclear" if chart
                else "unless an item in your evidence contradicts it")
        return (f"You were unclear and {who} names {theirs[0]}; no other category is named. Adopt {theirs[0]} at low "
                f"{cond}. Say which of their evidence made you change.")
    return ("You were unclear and the others name different categories (" + ", ".join(theirs) + "). Choose one only if "
            "your own evidence supports it more than the other; otherwise stay unclear.")


def build_user_message(round_: int, event: Event, trigger: str, ev: AgentEvidence,
                       others: Optional[list[dict[str, Any]]] = None,
                       own: Optional[dict[str, Any]] = None) -> str:
    """User message per prompts/README.md step 2: labelled blocks, compact JSON after each label.
    Round 2 also carries the agent's own round-1 message, so "keep your category" has a referent."""
    lines = [f"ROUND: {round_}",
             "EVENT: " + compact({"event_id": event.event_id, "event_type": event.event_type, "trigger": trigger}),
             "EVIDENCE: " + compact(ev.block),
             "ALLOWED_SOURCE_IDS: " + compact(ev.allowed_ids)]
    if round_ == 2:
        if own is not None:
            lines.append("YOUR_ROUND_ONE: " + compact(own))
            lines.append("ROUND_TWO_RULE: " + round_two_rule(ev.agent, own.get("category", "unclear"), others or []))
        lines.append("OTHER_AGENTS: " + compact(others or []))
    return "\n".join(lines)
