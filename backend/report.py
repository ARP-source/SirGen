"""Post-op report draft (M4a), built locally from the audit log, the drug log and the patient chart.

Two parts, kept apart on purpose:

  1. Deterministic sections, produced by code from audit entries: what happened, in order, with the
     log entry id of every line. These are complete without any model.
  2. A short narrative summary written by Lightning. Each sentence must cite log ids / chart source
     ids that exist for this run, must not contain a number that is absent from the cited entries,
     and must pass the same banned-wording check as the agents. One retry; after that the narrative
     reads "Narrative unavailable." and nothing is written in its place (hard rule 2).

The report is labelled a DRAFT for clinician review, built from simulated / composite research data,
with the audit chain verification result printed in it. Generating it is itself logged
(`report_generated`, with the SHA-256 of the HTML).
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .agents.evidence import flatten_chart
from .agents.llm_client import LightningClient, LLMBadResponse, LLMError, LLMTimeout, LLMUnreachable
from .agents.schemas import BANNED_PATTERNS
from .audit import AuditLog, Verification
from .state import ATTRIBUTION, COMPOSITE_LABEL, CONTEXT_UNAVAILABLE

log = logging.getLogger("sentinel.report")

NARRATIVE_UNAVAILABLE = "Narrative unavailable. The sections below are complete and come straight from the log."
MAX_SENTENCES = 5
MAX_SENTENCE_CHARS = 300
NARRATIVE_MAX_TOKENS = 450
_BANNED = [re.compile(p, re.IGNORECASE) for p in BANNED_PATTERNS]
_NUM = re.compile(r"\d+(?:\.\d+)?")
AGENTS = ("surgical", "anesthesia", "record")


# ---- facts ------------------------------------------------------------------------------------
def describe_chart_entry(e: dict[str, Any]) -> str:
    """One line of plain text for a chart entry (mirrors the screen-2 chart)."""
    if "field" in e:
        return f"{str(e['field']).replace('_', ' ')}: {e.get('value')}{' ' + e['unit'] if e.get('unit') else ''}"
    if "condition" in e:
        return f"{e['condition']}. {e.get('detail', '')}".strip()
    if "drug" in e:
        return f"{e['drug']} {e.get('dose', '')}. Last dose: {e.get('last_dose', 'not recorded')}".strip()
    if "substance" in e:
        return f"{e['substance']}: {e.get('reaction')} ({e.get('severity')})"
    if "test" in e:
        return f"{e['test']}: {e.get('value')}{' ' + e['unit'] if e.get('value') is not None and e.get('unit') else ''}"
    if "text" in e:
        return f"{e.get('type', 'note')} ({e.get('time', '')}): {e['text']}"
    return json.dumps(e, ensure_ascii=False)


def t_label(t: Optional[float]) -> str:
    return "time not recorded" if t is None else f"t {t:.1f} s"


@dataclass
class Facts:
    run: int
    scenario: dict[str, Any]
    first_seq: int
    last_seq: int
    entries: list[dict[str, Any]]
    controls: list[dict[str, Any]] = field(default_factory=list)
    phases: list[dict[str, Any]] = field(default_factory=list)
    drugs: list[dict[str, Any]] = field(default_factory=list)
    nibp: list[dict[str, Any]] = field(default_factory=list)
    baseline: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    voice: list[dict[str, Any]] = field(default_factory=list)
    ended: Optional[dict[str, Any]] = None
    cited_chart_ids: list[str] = field(default_factory=list)


def latest_run(entries: list[dict[str, Any]]) -> int:
    return max((e["run"] for e in entries), default=0)


def extract_facts(entries: list[dict[str, Any]], run: Optional[int] = None) -> Facts:
    run = latest_run(entries) if run is None else run
    mine = [e for e in entries if e["run"] == run]
    if not mine:
        raise ValueError(f"no log entries for run {run}")
    start = next((e for e in mine if e["kind"] == "run_start"), mine[0])
    f = Facts(run=run, scenario={**start["data"], "scenario_id": start.get("scenario_id")},
              first_seq=mine[0]["seq"], last_seq=mine[-1]["seq"], entries=mine)
    msgs: dict[str, dict[str, dict[str, Any]]] = {}  # event_id -> agent -> latest message entry
    unavailable: dict[str, list[dict[str, Any]]] = {}
    results: dict[str, dict[str, Any]] = {}
    alerts: list[dict[str, Any]] = []
    cited: list[str] = []
    for e in mine:
        k, d = e["kind"], e["data"]
        if k == "control":
            f.controls.append(e)
        elif k == "phase_change":
            f.phases.append(e)
        elif k == "drug_given":
            f.drugs.append(e)
        elif k == "nibp_reading":
            f.nibp.append(e)
        elif k == "baseline_alarm":
            f.baseline.append(e)
        elif k == "alert":
            alerts.append(e)
        elif k == "agent_message":
            ev = d.get("event_id", "")
            prev = msgs.setdefault(ev, {}).get(d["agent"])
            if prev is None or d["round"] >= prev["data"]["round"]:
                msgs[ev][d["agent"]] = e
            cited += [x["source_id"] for x in d.get("evidence", [])]
        elif k == "agent_unavailable":
            unavailable.setdefault(d.get("event_id", ""), []).append(e)
        elif k == "deliberation_result":
            results[d.get("event_id", "")] = e
        elif k == "voice_qa":
            f.voice.append(e)
            cited += list(d.get("source_ids", []))
        elif k == "segment_end":
            f.ended = e
    for a in alerts:
        ev = a["data"]["event_id"]
        f.events.append({"alert": a, "messages": msgs.get(ev, {}), "unavailable": unavailable.get(ev, []),
                         "result": results.get(ev)})
        res = results.get(ev)
        if res:
            cited += list(res["data"].get("question_source_ids", []))
    seen: set[str] = set()
    f.cited_chart_ids = [x for x in cited if not (x in seen or seen.add(x))]
    return f


# ---- narrative ----------------------------------------------------------------------------------
def fact_lines(f: Facts, chart: dict[str, dict[str, Any]]) -> dict[str, str]:
    """id -> one line of text. These are the only things the narrative may cite or quote numbers from."""
    lines: dict[str, str] = {}
    for e in f.entries:
        if e["kind"] in ("run_start", "control", "session_start", "metrics_snapshot"):
            continue
        lines[e["id"]] = f"{t_label(e['clock_s'])} | {e['kind']} | {e['actor']} | {e['summary']}"
        if e["kind"] == "alert":
            v = e["data"].get("vitals", {})
            lines[e["id"]] += (f" | vitals at alert: HR {v.get('hr')}, SBP {v.get('sbp')}, DBP {v.get('dbp')}, "
                               f"MAP {v.get('map')}, SpO2 {v.get('spo2')}")
        if e["kind"] == "agent_message":
            ev = ", ".join(f"{x['source_id']}: {x['note']}" for x in e["data"].get("evidence", []))
            lines[e["id"]] += f" | evidence: {ev}"
        if e["kind"] == "deliberation_result" and e["data"].get("team_question"):
            lines[e["id"]] += f" | question to the team: {e['data']['team_question']}"
    for sid in f.cited_chart_ids:
        if sid in chart:
            lines[sid] = describe_chart_entry(chart[sid])
    return lines


def narrative_schema(allowed: list[str]) -> dict[str, Any]:
    return {
        "type": "object", "additionalProperties": False, "required": ["sentences"],
        "properties": {"sentences": {
            "type": "array", "minItems": 1, "maxItems": MAX_SENTENCES,
            "items": {"type": "object", "additionalProperties": False, "required": ["text", "cites"],
                      "properties": {"text": {"type": "string", "maxLength": MAX_SENTENCE_CHARS},
                                     "cites": {"type": "array", "minItems": 1, "maxItems": 4,
                                               "items": {"type": "string", "enum": allowed}}}}}},
    }


class InvalidNarrative(ValueError):
    pass


def check_narrative(text: str, lines: dict[str, str]) -> list[dict[str, Any]]:
    """Parse and validate. Raises InvalidNarrative with a short reason."""
    try:
        obj = json.loads(text)
        sentences = obj["sentences"]
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidNarrative("not the expected JSON shape") from exc
    if not isinstance(sentences, list) or not 1 <= len(sentences) <= MAX_SENTENCES:
        raise InvalidNarrative(f"expected 1 to {MAX_SENTENCES} sentences")
    out: list[dict[str, Any]] = []
    for i, s in enumerate(sentences, start=1):
        t, cites = s.get("text", ""), s.get("cites", [])
        if not isinstance(t, str) or not t.strip() or len(t) > MAX_SENTENCE_CHARS:
            raise InvalidNarrative(f"sentence {i} is empty or too long")
        if not cites or any(c not in lines for c in cites):
            raise InvalidNarrative(f"sentence {i} cites an id that does not exist for this run")
        for pat in _BANNED:
            if pat.search(t):
                raise InvalidNarrative(f"banned wording in sentence {i}: /{pat.pattern}/")
        allowed_numbers = {float(n) for n in _NUM.findall(" ".join(lines[c] for c in cites))}
        stray = [n for n in _NUM.findall(t) if float(n) not in allowed_numbers]
        if stray:
            raise InvalidNarrative(f"sentence {i} has a number not in its cited entries: {stray[0]}")
        out.append({"text": t.strip(), "cites": list(cites)})
    return out


def narrative_user_message(f: Facts, lines: dict[str, str]) -> str:
    head = (f"Scenario: {f.scenario.get('title')}. Data: {f.scenario.get('vitals_source')} "
            f"({f.scenario.get('vitals_source_kind')}). Times are scenario clock seconds.")
    body = "\n".join(f"[{k}] {v}" for k, v in lines.items())
    return f"{head}\nLog entries and chart records you may cite (cite the id in square brackets):\n{body}"


async def write_narrative(client: LightningClient, system: str, f: Facts, lines: dict[str, str]
                          ) -> tuple[Optional[list[dict[str, Any]]], str, Optional[float]]:
    """Returns (sentences or None, status note, latency ms). One retry on validation failure only."""
    if not lines:
        return None, "nothing to summarise", None
    schema = narrative_schema(list(lines))
    user = narrative_user_message(f, lines)
    reason = "no attempt"
    for attempt in (1, 2):
        started = time.perf_counter()
        try:
            res = await client.chat_json(system, user, schema, name="ReportNarrative",
                                         max_tokens=NARRATIVE_MAX_TOKENS, temperature=0.2)
            sentences = check_narrative(res.text, lines)
        except (LLMTimeout, LLMUnreachable) as exc:
            return None, str(exc), None
        except (LLMBadResponse, InvalidNarrative, LLMError) as exc:
            reason = str(exc)
            log.warning("report narrative attempt=%d invalid=%s", attempt, reason)
            continue
        return sentences, "ok", round((time.perf_counter() - started) * 1000, 1)
    return None, f"invalid output after one retry: {reason}", None


# ---- the report object and HTML ------------------------------------------------------------------
@dataclass
class Report:
    run: int
    generated_wall: str
    facts: Facts
    chart: dict[str, dict[str, Any]]
    verification: Verification
    narrative: Optional[list[dict[str, Any]]]
    narrative_note: str
    narrative_ms: Optional[float]
    html_text: str = ""
    sha256: str = ""

    def summary(self) -> dict[str, Any]:
        return {"run": self.run, "generated": self.generated_wall, "scenario": self.facts.scenario.get("title"),
                "covers_entries": [self.facts.first_seq, self.facts.last_seq], "events": len(self.facts.events),
                "narrative": "ok" if self.narrative else "unavailable", "narrative_note": self.narrative_note,
                "narrative_ms": self.narrative_ms, "chain_ok": self.verification.ok, "sha256": self.sha256}


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _ref(e: dict[str, Any]) -> str:
    return f'<span class="ref">{_e(e["id"])}</span>'


def _table(head: list[str], rows: list[list[str]], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{_e(empty)}</p>'
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>"


def render_html(r: Report) -> str:
    f, v = r.facts, r.verification
    sc = f.scenario
    out: list[str] = []
    a = out.append
    a("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    a(f"<title>Sentinel-OR post-op report draft, run {r.run}</title>")
    a("<style>body{font:15px/1.5 system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#111}"
      "h1{font-size:24px;margin:0}h2{font-size:17px;margin:28px 0 8px;border-bottom:1px solid #ccc;padding-bottom:4px}"
      ".banner{background:#fff4d6;border:1px solid #d9b100;padding:10px 12px;border-radius:6px;margin:12px 0}"
      "table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #ccc;padding:5px 8px;text-align:left;vertical-align:top}"
      "th{background:#f2f2f2}.ref{font:12px ui-monospace,monospace;background:#eef;padding:1px 5px;border-radius:4px;white-space:nowrap}"
      ".muted{color:#666}.bad{color:#b00020;font-weight:600}.ok{color:#0a6b2c;font-weight:600}.small{font-size:12px;color:#555}"
      "@media print{body{margin:0}.noprint{display:none}}</style></head><body>")
    a("<h1>Post-operative report: DRAFT</h1>")
    a(f"<div class='small'>Sentinel-OR research prototype · generated locally {_e(r.generated_wall)} · run {r.run}</div>")
    a("<div class='banner'><b>Draft for clinician review.</b> Produced on this machine from the audit log of a replayed "
      f"scenario. {_e(COMPOSITE_LABEL)} Not a medical record. It contains no diagnosis and no treatment advice.</div>")

    a("<h2>Procedure</h2>")
    a(f"<p><b>{_e(sc.get('title'))}</b><br>{_e(sc.get('on_screen_label'))}<br>"
      f"Segment length {_e(sc.get('duration_s'))} s. Vitals source: {_e(sc.get('vitals_source'))} "
      f"({_e(sc.get('vitals_source_kind'))}).</p>")
    a(f"<p class='small'>{_e(ATTRIBUTION)}</p>")
    a("<h3 style='font-size:14px;margin:12px 0 4px'>Procedure steps seen</h3>")
    a(_table(["Log", "Time", "Step", "Set by"],
             [[_ref(e), _e(t_label(e["clock_s"])), _e(e["data"].get("display") or e["data"].get("name")),
               _e(e["data"].get("set_by") or e["actor"])] for e in f.phases],
             "No step changes were logged in this run."))

    a("<h2>Summary (written by the local model)</h2>")
    if r.narrative:
        a("<ul>" + "".join(f"<li>{_e(s['text'])} " + " ".join(f"<span class='ref'>{_e(c)}</span>" for c in s["cites"])
                           + "</li>" for s in r.narrative) + "</ul>")
        a("<p class='small'>Each sentence cites the log entries or chart records it comes from.</p>")
    else:
        a(f"<p class='muted'>{_e(NARRATIVE_UNAVAILABLE)} <span class='small'>({_e(r.narrative_note)})</span></p>")

    a("<h2>Alerts and what the team's assistant said</h2>")
    if not f.events:
        a("<p class='muted'>No alert fired in this run.</p>")
    for ev in f.events:
        al, d = ev["alert"], ev["alert"]["data"]
        vit = d.get("vitals", {})
        a(f"<h3 style='font-size:15px;margin:14px 0 4px'>{_ref(al)} {_e(d['event_id'])}: {_e(d['event_type'])} "
          f"at {_e(t_label(al['clock_s']))} ({_e(d['trigger'])})</h3>")
        a(f"<p>{_e(d['alert_text'])}<br><span class='small'>Vitals at the alert: HR {_e(vit.get('hr'))}, "
          f"SBP {_e(vit.get('sbp'))}, DBP {_e(vit.get('dbp'))}, MAP {_e(vit.get('map'))}, SpO2 {_e(vit.get('spo2'))}. "
          f"Alert shown {_e(d.get('event_to_alert_ms'))} ms after detection. "
          f"{'Simulated telemetry.' if d.get('simulated') else 'Real data, time-shifted.'}</span></p>")
        rows = []
        for agent in AGENTS:
            m = ev["messages"].get(agent)
            if m:
                md = m["data"]
                cites = " ".join(f"<span class='ref'>{_e(x['source_id'])}</span>" for x in md.get("evidence", []))
                rows.append([_ref(m), _e(agent), _e(f"round {md['round']}"), _e(md["category"].replace("_", " ")),
                             _e(md["confidence"]), _e(md["hypothesis"]), cites])
            else:
                un = next((u for u in ev["unavailable"] if u["data"]["agent"] == agent), None)
                rows.append([_ref(un) if un else "", _e(agent), "", "<span class='bad'>unavailable</span>", "",
                             _e(un["data"]["reason"] if un else "no message logged"), ""])
        a(_table(["Log", "Agent", "Round", "Category", "Confidence", "Hypothesis", "Sources"], rows, ""))
        res = ev["result"]
        if res is None:
            a("<p class='muted'>No deliberation outcome was logged for this alert.</p>")
        else:
            rd = res["data"]
            outcome = rd.get("outcome") or "unavailable"
            a(f"<p>{_ref(res)} Outcome decided by code: <b>{_e(outcome)}</b>. {_e(rd.get('summary') or '')}</p>")
            if rd.get("team_question"):
                a(f"<p><b>Question put to the team:</b> {_e(rd['team_question'])}</p>")
            if rd.get("outcome") is None:
                a(f"<p class='muted'>{_e(CONTEXT_UNAVAILABLE)}</p>")
    a("<h3 style='font-size:14px;margin:16px 0 4px'>Fixed blood-pressure rule, for comparison</h3>")
    a(_table(["Log", "Time", "What"],
             [[_ref(e), _e(t_label(e["clock_s"])), _e(e["summary"])] for e in f.baseline],
             "The fixed rule did not fire in this run."))

    a("<h2>Medications given (drug log)</h2>")
    a(_table(["Log", "Time", "Drug", "Detail", "Chart id", "Provenance"],
             [[_ref(e), _e("running at start" if e["data"].get("t_s") is None else t_label(e["data"]["t_s"])),
               _e(e["data"]["drug"]), _e(e["data"]["detail"]), f"<span class='ref'>{_e(e['data']['source_id'])}</span>",
               _e(e["data"].get("provenance"))] for e in f.drugs], "No drug entries were logged in this run."))
    a("<h2>Cuff blood-pressure readings</h2>")
    a(_table(["Log", "Time", "SBP", "DBP", "MAP", "Chart id"],
             [[_ref(e), _e(t_label(e["data"].get("t_s"))), _e(e["data"].get("sbp")), _e(e["data"].get("dbp")),
               _e(e["data"].get("map")), f"<span class='ref'>{_e(e['data']['source_id'])}</span>"] for e in f.nibp],
             "No cuff readings were logged in this run."))

    a("<h2>Questions asked aloud</h2>")
    a(_table(["Log", "Time", "Question", "Answer", "Sources"],
             [[_ref(e), _e(t_label(e["clock_s"])), _e(e["data"].get("question")), _e(e["data"].get("answer")),
               " ".join(f"<span class='ref'>{_e(x)}</span>" for x in e["data"].get("source_ids", []))] for e in f.voice],
             "No voice questions were logged in this run."))

    a("<h2>Chart records referred to</h2>")
    rows = [[f"<span class='ref'>{_e(sid)}</span>", _e(describe_chart_entry(r.chart[sid])),
             _e(r.chart[sid].get("provenance", ""))] for sid in f.cited_chart_ids if sid in r.chart]
    a(_table(["Id", "Record", "Provenance"], rows, "No chart records were cited in this run."))

    a("<h2>Presenter and team actions</h2>")
    a(_table(["Log", "Time", "Action", "By"],
             [[_ref(e), _e(t_label(e["clock_s"])), _e(e["summary"]), _e(e["actor"])] for e in f.controls],
             "No controls were used."))

    a("<h2>Log integrity</h2>")
    status = ("<span class='ok'>chain verified</span>" if v.ok else f"<span class='bad'>CHAIN BROKEN: {_e(v.reason)}</span>")
    a(f"<p>{status}. This report covers log entries {f.first_seq} to {f.last_seq} (run {r.run}); the log held "
      f"{v.entries} entries when this was generated. Head hash <span class='ref'>{_e(v.head_hash)}</span>.</p>")
    a("<p class='small'>The log is tamper-evident: every entry includes the hash of the one before it, so a changed, "
      "removed or reordered entry is detectable. It is not tamper-proof. It records what the system observed and said, "
      "not whether anyone was right.</p>")
    a("</body></html>")
    return "".join(out)


async def generate(audit: AuditLog, client: LightningClient, patient: dict[str, Any], system_prompt: str,
                   run: Optional[int] = None, out_dir: Optional[Path] = None) -> Report:
    """Verify the chain, build the report for `run` (default: the latest), write it and log it."""
    verification = audit.verify()
    entries = audit.entries()
    facts = extract_facts(entries, run)
    chart = {e["source_id"]: e for e in flatten_chart(patient)}
    lines = fact_lines(facts, chart)
    sentences, note, ms = await write_narrative(client, system_prompt, facts, lines)
    report = Report(run=facts.run, generated_wall=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), facts=facts, chart=chart,
                    verification=verification, narrative=sentences, narrative_note=note, narrative_ms=ms)
    report.html_text = render_html(report)
    report.sha256 = hashlib.sha256(report.html_text.encode("utf-8")).hexdigest()
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"report-{audit.session_id}-run{facts.run}.html").write_text(report.html_text, encoding="utf-8")
    audit.record("report_generated", f"post-op report draft for run {facts.run}",
                 {"run": facts.run, "covers_entries": [facts.first_seq, facts.last_seq], "sha256": report.sha256,
                  "chain_ok": verification.ok, "narrative": "ok" if sentences else "unavailable",
                  "narrative_note": note, "narrative_ms": ms}, actor="report")
    return report
