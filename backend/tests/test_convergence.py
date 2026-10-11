"""Convergence and team-question templates are code, not model output (CLAUDE.md, team_question_templates.md)."""
import json
from pathlib import Path

import pytest

from backend.agents import convergence as cv
from backend.agents.evidence import EvidenceContext
from backend.agents.schemas import AgentMessage
from backend.state import Event

DATA = Path(__file__).resolve().parents[2] / "data"


def msg(agent, category, confidence="low", cites=("vit-art",), round_=2):
    return AgentMessage(agent=agent, round=round_, category=category, hypothesis=f"{agent} reading.",
                        confidence=confidence, evidence=[{"source_id": c, "note": "n"} for c in cites],
                        responds_to=[])


def cats(s, a, r):
    return {"surgical": s, "anesthesia": a, "record": r}


@pytest.mark.parametrize("c,outcome,shared", [
    (cats("drug_effect", "drug_effect", "drug_effect"), "converged", "drug_effect"),
    (cats("unclear", "drug_effect", "drug_effect"), "converged", "drug_effect"),
    (cats("artifact", "unclear", "artifact"), "converged", "artifact"),
    (cats("bleeding_concern", "bleeding_concern", "unclear"), "converged", "bleeding_concern"),
    (cats("unclear", "unclear", "unclear"), "converged", "unclear"),
    (cats("bleeding_concern", "drug_effect", "bleeding_concern"), "split", None),
    (cats("bleeding_concern", "drug_effect", "unclear"), "split", None),
    (cats("bleeding_concern", "unclear", "unclear"), "split", None),  # one named category is not agreement
    (cats("bleeding_concern", "drug_effect", "artifact"), "split", None),
    (cats("no_concern", "no_concern", "drug_effect"), "split", None),
])
def test_decide_matches_spec_rule(c, outcome, shared):
    r = cv.decide(c)
    assert (r.outcome, r.shared_category) == (outcome, shared)


def test_unavailable_agent_counts_as_unclear():
    round2 = {"surgical": msg("surgical", "drug_effect"), "anesthesia": msg("anesthesia", "drug_effect"), "record": None}
    assert cv.categories(round2)["record"] == "unclear"
    assert cv.decide(cv.categories(round2)).outcome == "converged"
    round2["anesthesia"] = None
    assert cv.decide(cv.categories(round2)).outcome == "split"


def test_ranking_by_count_then_confidence_then_fixed_order():
    r = {"surgical": msg("surgical", "artifact", "high"), "anesthesia": msg("anesthesia", "drug_effect", "low"),
         "record": msg("record", "bleeding_concern", "low")}
    assert cv.rank(r) == ["artifact", "bleeding_concern", "drug_effect"]  # confidence, then fixed order on the tie
    r = {"surgical": msg("surgical", "bleeding_concern", "low"), "anesthesia": msg("anesthesia", "drug_effect", "low"),
         "record": msg("record", "drug_effect", "low")}
    assert cv.rank(r)[0] == "drug_effect"  # more agents first


def test_evidence_ids_round_robin_skip_duplicates_max_three():
    r = {"surgical": msg("surgical", "bleeding_concern", "medium", ("surg-02", "surg-03", "phase")),
         "anesthesia": msg("anesthesia", "drug_effect"),
         "record": msg("record", "bleeding_concern", "medium", ("meds-01", "lab-06", "lab-01"))}
    assert cv.evidence_ids("bleeding_concern", r) == ["surg-02", "meds-01", "surg-03"]
    r["record"] = msg("record", "bleeding_concern", "high", ("meds-01", "surg-02"))
    assert cv.evidence_ids("bleeding_concern", r) == ["meds-01", "surg-02", "surg-03"]  # high first, dup skipped


def make_ctx(scenario_id, scenarios, now):
    sc = scenarios[scenario_id]
    ev = Event(event_id="ev-1", event_type=sc.event_type, event_timestamp=sc.event_t_s, trigger="scenario", simulated=True)
    return EvidenceContext(sc, ev, now, json.loads((DATA / "patient/patient_record.json").read_text()),
                           json.loads((DATA / "procedure/phase_rules.json").read_text()))


def test_template_true_bleed_matches_documented_example(scenarios):
    ctx = make_ctx("true_bleed", scenarios, 60)
    r = {"surgical": msg("surgical", "bleeding_concern", "medium", ("surg-02", "surg-03")),
         "anesthesia": msg("anesthesia", "drug_effect", "low", ("drug-03", "vit-art")),
         "record": msg("record", "bleeding_concern", "medium", ("meds-01", "lab-06"))}
    out = cv.build(r, ctx.phrase)
    assert out.outcome == "split"
    assert out.team_question == (
        "Two explanations fit: possible bleeding (scissors in view, from 35 s before the alert; apixaban last dose "
        "about 19 h before incision; suction/irrigator appears and stays in view, from 15 s before the alert) and "
        "drug effect (propofol increase 85 s before the alert; MAP 82 to 72 and HR 72 to 81 over the last 60 s). "
        "Check the field for bleeding and confirm the drug timing.")
    assert [s.category for s in out.sides] == ["bleeding_concern", "drug_effect"]
    assert out.sides[0].agents == ["surgical", "record"]


def test_template_drug_effect_matches_documented_example(scenarios):
    ctx = make_ctx("drug_effect", scenarios, 30)
    r = {"surgical": msg("surgical", "unclear", "medium", ("surg-01",)),
         "anesthesia": msg("anesthesia", "drug_effect", "high", ("drug-03", "vit-art")),
         "record": msg("record", "drug_effect", "low", ("meds-02",))}
    out = cv.build(r, ctx.phrase)
    assert out.outcome == "converged" and out.shared_category == "drug_effect"
    assert out.summary == ("The sources agree this fits a drug effect (propofol increase 90 s before the alert; "
                           "losartan last dose about 5 h before incision; MAP 71 to 61 and HR 62 to 60 over the last 30 s).")
    assert out.team_question == "Does the drug timing match what the anaesthesia team sees, and is the field unchanged?"


def test_template_artifact_matches_documented_example(scenarios):
    ctx = make_ctx("artifact", scenarios, 76)
    r = {"surgical": msg("surgical", "artifact", "low", ("surg-01",)),
         "anesthesia": msg("anesthesia", "artifact", "high", ("vit-art", "vit-nibp-02")),
         "record": msg("record", "unclear", "low", ("enc-05",))}
    out = cv.build(r, ctx.phrase)
    assert out.summary == ("Likely artifact (arterial SBP peak 277, back near baseline within 9 s; grasper and hook in "
                           "view, from 50 s before the alert; cuff 126/70, 25 s after the alert).")
    assert out.team_question == "Was the arterial line just flushed or sampled?"
    # record adopting artifact on enc-05 replaces the cuff phrase, as the template doc says
    r["record"] = msg("record", "artifact", "low", ("enc-05",))
    assert "arterial line: left radial" in cv.build(r, ctx.phrase).summary


def test_other_templates(scenarios):
    ctx = make_ctx("true_bleed", scenarios, 60)
    allu = {a: msg(a, "unclear") for a in cv.AGENT_ORDER}
    out = cv.build(allu, ctx.phrase)
    assert out.outcome == "converged" and out.summary == "The sources do not explain this change."
    assert out.team_question == "What does the team see in the field and on the monitor?"
    one = {"surgical": msg("surgical", "bleeding_concern", "medium", ("surg-02",)),
           "anesthesia": msg("anesthesia", "unclear"), "record": msg("record", "unclear")}
    out = cv.build(one, ctx.phrase)
    assert out.outcome == "split"
    assert out.team_question == ("One source suggests possible bleeding (scissors in view, from 35 s before the alert); "
                                 "the others cannot explain the change. Check the field for bleeding.")
    third = {"surgical": msg("surgical", "bleeding_concern", "medium", ("surg-02",)),
             "anesthesia": msg("anesthesia", "drug_effect", "low", ("drug-03",)),
             "record": msg("record", "artifact", "low", ("enc-05",))}
    q = cv.build(third, ctx.phrase).team_question
    assert "possible bleeding" in q and "drug effect" in q and "artifact" not in q  # third category stays in its column


def test_template_text_is_never_taken_from_model_notes(scenarios):
    ctx = make_ctx("true_bleed", scenarios, 60)
    m = AgentMessage(agent="surgical", round=2, category="bleeding_concern", hypothesis="x.", confidence="low",
                     evidence=[{"source_id": "surg-02", "note": "MODEL WORDS THAT MUST NOT APPEAR"}], responds_to=[])
    rest = {"anesthesia": msg("anesthesia", "unclear"), "record": msg("record", "unclear")}
    out = cv.build({"surgical": m, **rest}, ctx.phrase)
    assert "MODEL WORDS" not in out.team_question and "MODEL WORDS" not in out.summary


@pytest.mark.parametrize("mine,theirs,stance", [
    ("drug_effect", "drug_effect", "agree"), ("unclear", "unclear", "agree"),
    ("unclear", "artifact", "adds"), ("bleeding_concern", "unclear", "adds"),
    ("bleeding_concern", "drug_effect", "disagree"),
])
def test_stance_is_decided_by_code_from_the_two_categories(mine, theirs, stance):
    assert cv.stance_for(mine, theirs) == stance


def test_code_stances_replace_the_models_and_keep_its_note():
    def r2(agent, category, responses):
        return AgentMessage.model_validate({**msg(agent, category).model_dump(), "responds_to": [
            {"agent": a, "stance": s, "note": n} for a, s, n in responses]})
    msgs = {"surgical": r2("surgical", "unclear", [("anesthesia", "disagree", "note a"), ("record", "disagree", "note b")]),
            "anesthesia": r2("anesthesia", "artifact", [("surgical", "agree", "note c"), ("record", "disagree", "note d")]),
            "record": None}
    out = cv.with_code_stances(msgs)
    assert [(r.agent, r.stance, r.note) for r in out["surgical"].responds_to] == [
        ("anesthesia", "adds", "note a"), ("record", "disagree", "note b")]  # record has no round 2: kept
    assert [(r.agent, r.stance) for r in out["anesthesia"].responds_to] == [("surgical", "adds"), ("record", "disagree")]
    assert out["record"] is None
