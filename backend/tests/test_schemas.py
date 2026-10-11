import json

import pytest
from pydantic import ValidationError

from backend.agents.schemas import AgentMessage, validate_sources

GOOD = {
    "agent": "anesthesia", "round": 1, "category": "drug_effect",
    "hypothesis": "MAP fell after a propofol increase with HR flat.", "confidence": "high",
    "evidence": [{"source_id": "drug-03", "note": "propofol rate up 75 s before MAP fell"},
                 {"source_id": "vit-art", "note": "MAP 71 to 61 mmHg"}],
    "responds_to": [],
}
KNOWN = {"drug-01", "drug-02", "drug-03", "vit-art", "meds-01"}


def test_valid_message_parses_and_validates():
    msg = AgentMessage.model_validate(GOOD)
    check = validate_sources(msg, KNOWN)
    assert check.ok and check.unknown_ids == []


def test_unknown_source_id_fails():
    bad = json.loads(json.dumps(GOOD))
    bad["evidence"][1]["source_id"] = "lab-99"
    check = validate_sources(AgentMessage.model_validate(bad), KNOWN)
    assert not check.ok and check.unknown_ids == ["lab-99"]


def test_no_evidence_fails():
    msg = AgentMessage.model_validate({**GOOD, "evidence": []})
    assert not validate_sources(msg, KNOWN).ok


@pytest.mark.parametrize("field,value", [
    ("category", "hemorrhage"), ("confidence", "87%"), ("agent", "nurse"), ("round", 3),
])
def test_enums_are_enforced(field, value):
    with pytest.raises(ValidationError):
        AgentMessage.model_validate({**GOOD, field: value})


def test_extra_fields_rejected_and_stance_enum():
    with pytest.raises(ValidationError):
        AgentMessage.model_validate({**GOOD, "risk": "high"})
    with pytest.raises(ValidationError):
        AgentMessage.model_validate({**GOOD, "round": 2,
                                     "responds_to": [{"agent": "record", "stance": "maybe", "note": "x"}]})


def test_schema_field_order_matches_spec():
    assert list(AgentMessage.model_json_schema()["properties"]) == [
        "agent", "round", "category", "hypothesis", "confidence", "evidence", "responds_to"]


# ---- M3: narrowed per-call schema and output validation ---------------------------------------
from backend.agents.schemas import InvalidMessage, check_message, narrowed_schema  # noqa: E402

R1 = {"agent": "anesthesia", "round": 1, "category": "drug_effect",
      "hypothesis": "The fall fits a propofol increase 85 s before the alert.", "confidence": "low",
      "evidence": [{"source_id": "drug-03", "note": "propofol target 2.8 to 3.2"}], "responds_to": []}
ALLOWED = ["vit-art", "drug-01", "drug-03"]


def dump(**over):
    return json.dumps({**R1, **over})


def test_narrowed_schema_round1_and_round2():
    s1 = narrowed_schema("anesthesia", 1, ALLOWED, [])
    assert list(s1["properties"]) == ["agent", "round", "category", "hypothesis", "confidence", "evidence", "responds_to"]
    assert s1["properties"]["agent"]["enum"] == ["anesthesia"] and s1["properties"]["round"]["enum"] == [1]
    assert s1["properties"]["evidence"]["items"]["properties"]["source_id"]["enum"] == ALLOWED
    assert (s1["properties"]["evidence"]["minItems"], s1["properties"]["evidence"]["maxItems"]) == (1, 3)
    assert s1["properties"]["hypothesis"]["maxLength"] == 240
    assert s1["properties"]["evidence"]["items"]["properties"]["note"]["maxLength"] == 90
    assert s1["properties"]["responds_to"]["maxItems"] == 0
    s2 = narrowed_schema("anesthesia", 2, ALLOWED, ["surgical", "record"])
    rt = s2["properties"]["responds_to"]
    assert rt["minItems"] == rt["maxItems"] == 2 and rt["items"]["properties"]["agent"]["enum"] == ["surgical", "record"]
    assert rt["items"]["properties"]["note"]["maxLength"] == 140
    assert "$ref" not in json.dumps(s2) and "$defs" not in s2 and s2["additionalProperties"] is False
    json.dumps(s2)  # serialisable for response_format


def test_check_message_accepts_valid_and_rejects_each_rule():
    assert check_message(dump(), "anesthesia", 1, ALLOWED, []).category == "drug_effect"
    bad = {
        "not json": "{oops",
        "unknown source_id": dump(evidence=[{"source_id": "lab-99", "note": "x"}]),
        "id outside own evidence": dump(evidence=[{"source_id": "meds-01", "note": "x"}]),
        "no evidence": dump(evidence=[]),
        "wrong agent": dump(agent="record"),
        "wrong round": dump(round=2),
        "two sentences": dump(hypothesis="It fell. The drug was raised."),
        "diagnosis wording": dump(hypothesis="This is a diagnosis of drug effect."),
        "percent": dump(hypothesis="It is about 40% likely."),
        "treatment wording": dump(hypothesis="The team should check the drug."),
        "banned in note": dump(evidence=[{"source_id": "drug-03", "note": "give blood"}]),
        "bleeding statement": dump(hypothesis="The patient has a haemorrhage."),
        "round 1 with responses": dump(responds_to=[{"agent": "record", "stance": "agree", "note": "ok"}]),
        "too long": dump(hypothesis="x" * 241),
    }
    for name, text in bad.items():
        with pytest.raises(InvalidMessage):
            check_message(text, "anesthesia", 1, ALLOWED, [])
            pytest.fail(f"{name} was accepted")


def test_check_message_round2_requires_one_response_per_available_other():
    r2 = {**R1, "round": 2, "responds_to": [{"agent": "surgical", "stance": "adds", "note": "fits their concern"},
                                            {"agent": "record", "stance": "agree", "note": "same view"}]}
    assert check_message(json.dumps(r2), "anesthesia", 2, ALLOWED, ["surgical", "record"]).round == 2
    with pytest.raises(InvalidMessage, match="responds_to"):
        check_message(json.dumps(r2), "anesthesia", 2, ALLOWED, ["surgical"])  # record unavailable: answer only surgical
    r2["responds_to"] = r2["responds_to"][:1]
    with pytest.raises(InvalidMessage, match="responds_to"):
        check_message(json.dumps(r2), "anesthesia", 2, ALLOWED, ["surgical", "record"])


def test_record_agent_is_never_high_and_cannot_quote_vitals_it_does_not_see():
    rec = {"agent": "record", "round": 1, "category": "unclear", "confidence": "low",
           "hypothesis": "Losartan about 5 h before incision is on the chart.",
           "evidence": [{"source_id": "meds-02", "note": "losartan about 5 h before incision"}], "responds_to": []}
    visible = '{"chart":[{"source_id":"meds-02","last_dose_h_before_incision":5}]} MAP down >= 10 mmHg'
    assert check_message(json.dumps(rec), "record", 1, ["meds-02"], [], visible_text=visible).confidence == "low"
    assert narrowed_schema("record", 1, ["meds-02"], [])["properties"]["confidence"]["enum"] == ["low", "medium"]
    assert "enum" in narrowed_schema("anesthesia", 1, ALLOWED, [])["properties"]["confidence"]
    with pytest.raises(InvalidMessage, match="at most medium"):
        check_message(json.dumps({**rec, "confidence": "high"}), "record", 1, ["meds-02"], [])
    copied = {**rec, "hypothesis": "Arterial SBP jump from 122 to 277 mmHg while HR stays flat fits an artifact."}
    with pytest.raises(InvalidMessage, match="vital-sign values"):
        check_message(json.dumps(copied), "record", 1, ["meds-02"], [], visible_text=visible)
    ok = {**rec, "hypothesis": "A MAP fall of 10 mmHg has losartan about 5 h before incision as chart background."}
    assert check_message(json.dumps(ok), "record", 1, ["meds-02"], [], visible_text=visible)


def test_hypothesis_cut_at_the_length_cap_is_trimmed_at_a_word_with_an_ellipsis():
    from backend.agents.schemas import trim_at_word
    cut = ("word " * 48)[:240 - 4] + "brok"
    assert len(cut) == 240 and trim_at_word(cut).endswith("word …") and "brok" not in trim_at_word(cut)
    assert trim_at_word("A short complete sentence.") == "A short complete sentence."
    assert check_message(dump(hypothesis=cut), "anesthesia", 1, ALLOWED, []).hypothesis.endswith(" …")


def test_anesthesia_heart_rate_direction_must_match_the_measured_change():
    wrong = dump(hypothesis="The propofol increase with a falling heart rate fits a drug effect.")
    with pytest.raises(InvalidMessage, match="heart rate is falling"):
        check_message(wrong, "anesthesia", 1, ALLOWED, [], hr_delta=9.0)
    assert check_message(wrong, "anesthesia", 1, ALLOWED, [], hr_delta=-9.0)
    assert check_message(wrong, "anesthesia", 1, ALLOWED, [], hr_delta=1.0)  # near flat: not checked
    with pytest.raises(InvalidMessage, match="rising"):
        check_message(dump(hypothesis="HR rose as the pressure fell."), "anesthesia", 1, ALLOWED, [], hr_delta=-5.0)


def test_one_sentence_check_ignores_abbreviation_stops_but_still_rejects_two_sentences():
    from backend.agents.schemas import is_one_sentence
    assert is_one_sentence("The cuff reading vs. HR and approx. 277 mmHg at the peak fits an artifact.")
    assert is_one_sentence("SBP rose to 277.5 mmHg within 2 s and fell back, e.g. Within 10 s.")
    assert not is_one_sentence("The SBP jumped within 2 s. HR stayed flat.")
    assert not is_one_sentence("It fell. The drug was raised.")
    with pytest.raises(InvalidMessage, match="more than one sentence"):
        check_message(dump(hypothesis="It fell to 61 mmHg. The drug was raised."), "anesthesia", 1, ALLOWED, [])
