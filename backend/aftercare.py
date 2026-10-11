"""Aftercare and insurance-coding sections of the post-op report draft.

Both are built by code from the patient chart, the run's audit log and two
fixed reference files; no model writes them:
  data/procedure/lap_chole_aftercare.json  general aftercare teaching + medication-decision prompts
  data/billing/coding_map.json             chart wording -> CPT / ICD-10-CM lookup

Sentinel never decides a dose, a restart or a stop (hard rule 1). The medication
table lists what the chart says and names the decision the team has to make.
Every coding line names the chart record it came from, for a certified coder to
confirm.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class MedRow:
    source_id: str
    drug: str
    dose: str
    chart_status: str
    group: Optional[str]
    decision: str


@dataclass
class CodeRow:
    kind: str  # procedure | anesthesia | diagnosis | status
    code: str
    description: str
    from_ids: list[str]
    note: str = ""


@dataclass
class Aftercare:
    general: list[dict[str, str]]
    sources: list[str]
    meds: list[MedRow]
    allergies: list[dict[str, str]]
    review: list[dict[str, Any]]  # intra-operative items for the team to review, each with its log id


@dataclass
class Coding:
    rows: list[CodeRow]
    missing: list[str]
    times: dict[str, Any] = field(default_factory=dict)
    label: str = ""


def _lower(*parts: Any) -> str:
    return " ".join(str(p) for p in parts if p is not None).lower()


def _group(med: dict[str, Any], groups: dict[str, Any]) -> Optional[str]:
    text = _lower(med.get("drug"), med.get("class"))
    for name, g in groups.items():
        if any(m in text for m in g["match"]):
            return name
    return None


def build_aftercare(patient: dict[str, Any], ref: dict[str, Any], entries: list[dict[str, Any]]) -> Aftercare:
    groups = ref.get("medication_groups", {})
    meds = []
    for m in patient.get("medications", []):
        g = _group(m, groups)
        meds.append(MedRow(source_id=m["source_id"], drug=m.get("drug", ""), dose=m.get("dose", ""),
                           chart_status=m.get("last_dose") or "not recorded", group=g,
                           decision=groups[g]["decision"] if g else ref.get("default_decision", "")))
    allergies = [{"source_id": a["source_id"], "text": f"{a.get('substance')}: {a.get('reaction')} ({a.get('severity')})",
                  "prompt": ref.get("allergy_prompt", "")} for a in patient.get("allergies", [])]
    review = []
    for e in entries:
        d = e.get("data", {})
        if e["kind"] == "deviation":
            review.append({"log_id": e["id"], "clock_s": e.get("clock_s"), "what": e["summary"],
                           "why": "", "note": d.get("note", "")})
        elif e["kind"] == "video_event" and d.get("kind") == "observation":
            review.append({"log_id": e["id"], "clock_s": e.get("clock_s"), "what": e["summary"], "why": "",
                           "note": d.get("note", "")})
        elif e["kind"] == "deliberation_result" and d.get("shared_category") == "bleeding_concern":
            review.append({"log_id": e["id"], "clock_s": e.get("clock_s"), "what": "Agents shared a bleeding concern",
                           "why": "", "note": d.get("summary") or ""})
    return Aftercare(general=ref.get("general", []), sources=ref.get("sources", []), meds=meds,
                     allergies=allergies, review=review)


def build_coding(patient: dict[str, Any], cmap: dict[str, Any]) -> Coding:
    enc = {e.get("field"): e for e in patient.get("encounter", [])}
    rows: list[CodeRow] = []
    missing: list[str] = []
    proc = enc.get("procedure")
    if proc:
        text = _lower(proc.get("value"))
        hit = next((p for p in cmap["procedures"] if all(w in text for w in p["match_all"])), None)
        if hit:
            rows.append(CodeRow("procedure", hit["cpt"], hit["description"], [proc["source_id"]]))
        an = next((p for p in cmap.get("anesthesia", []) if all(w in text for w in p["match_all"])), None)
        if an:
            asa = enc.get("asa_class")
            mod = cmap.get("asa_modifier", {}).get(str(asa.get("value"))) if asa else None
            ids = [proc["source_id"]] + ([asa["source_id"]] if asa and mod else [])
            rows.append(CodeRow("anesthesia", an["cpt"] + (f"-{mod}" if mod else ""), an["description"], ids,
                                note=f"physical status modifier {mod} from ASA {asa.get('value')}" if mod else
                                "no ASA class in chart"))
    else:
        missing.append("procedure performed")

    history = patient.get("history", [])
    hist_text = _lower(*[h.get("condition") for h in history], *[h.get("detail") for h in history])
    dx_sources: list[tuple[str, str, bool]] = []
    if enc.get("diagnosis"):
        dx_sources.append((enc["diagnosis"]["source_id"], _lower(enc["diagnosis"].get("value")), True))
    else:
        missing.append("primary diagnosis")
    dx_sources += [(h["source_id"], _lower(h.get("condition")), False) for h in history]
    seen: set[str] = set()
    for sid, text, primary in dx_sources:
        for d in cmap["diagnoses"]:
            if d.get("primary_only") and not primary:
                continue  # gallbladder codes come only from the encounter diagnosis, never stacked from history
            if not all(w in text for w in d["match_all"]):
                continue
            if d.get("with_history") and not all(w in hist_text for w in d["with_history"]):
                continue
            if d["icd10"] not in seen:
                seen.add(d["icd10"])
                hist_ids = [h["source_id"] for h in history
                            if d.get("with_history") and all(w in _lower(h.get("condition"), h.get("detail")) for w in d["with_history"])]
                rows.append(CodeRow("diagnosis", d["icd10"], d["description"], [sid] + hist_ids, note=d.get("note", "")))
            break
    for m in patient.get("medications", []):
        for s in cmap.get("medication_status", []):
            if any(w in _lower(m.get("drug")) for w in s["match_any"]) and s["icd10"] not in seen:
                seen.add(s["icd10"])
                rows.append(CodeRow("status", s["icd10"], s["description"], [m["source_id"]]))

    times: dict[str, Any] = {}
    ct = enc.get("case_times_s_from_casestart")
    if ct and isinstance(ct.get("value"), dict):
        v = ct["value"]
        if v.get("opstart") is not None and v.get("opend") is not None:
            times["surgery_min"] = round((v["opend"] - v["opstart"]) / 60, 1)
        if v.get("casestart") is not None and v.get("caseend") is not None:
            times["case_min"] = round((v["caseend"] - v["casestart"]) / 60, 1)
        times["source_id"] = ct["source_id"]
    else:
        missing.append("case and surgery times")
    missing += [f for f in cmap.get("claim_fields", [])]
    return Coding(rows=rows, missing=missing, times=times, label=cmap.get("label", ""))
