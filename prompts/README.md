# Sentinel-OR prompts

| File | Used by | Model call |
|---|---|---|
| `surgical.md`, `anesthesia.md`, `record.md` | `backend/agents/orchestrator.py` (copy to `backend/agents/prompts/`, per SPEC) | Lightning, `AgentMessage` schema |
| `voice_answer.md` | `backend/voice.py` | Lightning, voice schema below |
| `team_question_templates.md` | orchestrator, in code | none (deterministic) |

## Assembling an agent call
1. **System message**: the prompt file, verbatim.
2. **User message**: these blocks in this order, as compact JSON after each label:
   - `ROUND: 1|2`.
   - `EVENT:` `event_id`, `event_type` (`map_drop`, or `art_spike` for the SBP-jump trigger) and `trigger` (the rule text from the scenario's `event_note`).
   - `EVIDENCE:` that agent's block (format in its prompt). Every time is `rel_alert_s = t_s - event_t_s`, computed by code so the model never does time arithmetic.
   - `ALLOWED_SOURCE_IDS:` exactly the ids present in that block.
   - `YOUR_ROUND_ONE:` in round 2 only. The agent's own validated round-1 message, so it can keep or change its category knowingly.
   - `ROUND_TWO_RULE:` in round 2 only. One sentence picked by code (`evidence.round_two_rule`) from the round-1 categories: which keep/adopt rule of the prompt applies. It never uses the scenario; the model still judges the evidence.
   - `OTHER_AGENTS:` in round 2 only. These are the other two validated round-1 messages; an agent that failed is sent as `{"agent":"record","status":"unavailable"}`.
3. **Evidence builders**: "now" is when round 1 starts. Round 2 reuses the round-1 block, so any change comes only from the other agents.
   - *surgical*: the tracker phase (`phase`) and the 10 s tool window (`tools`); the `surgical_events` with `t_s <= now`; the `out_of_phase_events` from `phase_rules.json` that fired (`oop-1`, `oop-2`); and the Omni answer as `omni-{event_id}` if it arrived before round 1, else `null`.
   - *anesthesia*: `vit-art` samples come from the 1 Hz interpolated keyframes. Take one every 10 s from `max(alert - 60 s, segment start)` to now, plus one every 2 s within 10 s of the alert and wherever SBP moves > 20 mmHg between 10 s samples. Add the `summary`, computed by code. Include cuff and drug entries with `t_s <= now` (drug `t_s` may be `null`) and only non-null values.
   - *record*: the chart entries, copied as they are. There are two options (see the sanity check). (a) The scenario's `agent_evidence.record` subset matches the scenario files. (b) The whole chart is closer to the SPEC ("the patient chart") and less scripted. Both reach the expected outcome. Drop entries whose value is `null` (`lab-03` to `lab-05` until fetched).
   - Code-made ids (`phase`, `tools`, `oop-N`, `omni-*`) must be in the validator's set of loaded ids.
4. **Request**: `model: "lightning"`, `temperature: 0.2`, `chat_template_kwargs: {"enable_thinking": false}`, and `max_tokens` 256 for round 1 (200 was hit by round-1 answers with three numeric evidence notes, 204 to 212 tokens). Use 320 for round 2 (256 was hit by real round-2 answers with three evidence items and two responses). Earlier sizing note: the round-2 worked examples are about 600 to 680 characters, which is close to 200 tokens. A `finish_reason: "length"` counts as a validation failure. Send `response_format: {"type": "json_schema", "json_schema": {"name": "AgentMessage", "strict": true, "schema": S}}`. S is the pydantic `AgentMessage` schema, narrowed for each call:
   - `agent` and `round` are consts. `category` and `confidence` are the SPEC enums.
   - `hypothesis` has `maxLength` 240. `evidence` has 1 to 3 items, with `source_id` an **enum of ALLOWED_SOURCE_IDS** and `note` `maxLength` 90.
   - `responds_to` has `maxItems` 0 in round 1. In round 2 it has exactly one item per other available agent, with `agent` an enum of those agents and `note` `maxLength` 140.
   - Field order stays as in SPEC.
5. **Validate** (hard rule 2) even with the enum:
   - pydantic parse;
   - ids are in the loaded set;
   - the hypothesis is one sentence (no `[.!?]` followed by a space and a capital);
   - no banned pattern in the hypothesis or any note. Case-insensitive: `diagnos`, `the patient has`, `\bis bleeding\b`, `ha?emorrhag`, `hypovol`, `\b(give|administer|transfuse|recommend|should|must)\b`, `%`, `percent`, `risk of`.

   On failure, retry once with the same request. Then show "Context unavailable. Source record remains available for review." Never show scripted text in place of a failed output.
6. **Outcome and team question**: these are code only; see `team_question_templates.md`.

## Assembling a voice call
- **System**: `voice_answer.md`.
- **User**: `QUESTION`, `NOW`, `STATE` and `ALLOWED_SOURCE_IDS`. STATE is built from `state.py`: the full chart without null values, vitals now plus the last 120 s at 10 s steps, the drug log, the event and the deliberation. Times are given as `rel_now_s`.
- **Request**: as above, with `max_tokens` 120 and the schema `{"spoken": string maxLength 220, "source_ids": array (max 3) of enum ALLOWED_SOURCE_IDS, "found": boolean}`.
- **Validate**: at most 2 sentences, ids allowed, and the banned list. The fixed refusal sentence "I can't advise on treatment..." is exempt.
- **Output**: TTS speaks `spoken`, and the screen shows the cited entries. On failure, show "Context unavailable. Source record remains available for review." and speak nothing.

## Self-check (run each scenario at least 5 times, then on the jittered eval variants)
| Scenario (deliberation time) | surgical R1 → R2 | anesthesia R1 → R2 | record R1 → R2 | Outcome |
|---|---|---|---|---|
| true_bleed (t 60) | bleeding_concern medium [surg-02, surg-03] → same | drug_effect low [drug-03, vit-art], names rising HR → same | (a) bleeding_concern low/medium [meds-01, lab-06, lab-01]; (b) bleeding_concern low or unclear → bleeding_concern | **split**, bleeding vs drug (converged bleeding is acceptable) |
| drug_effect (t 30) | unclear medium [surg-01] → unclear, or drug_effect low | drug_effect high [drug-03, vit-art] → same | (a) drug_effect low [meds-02, note-02]; (b) bleeding_concern low or unclear → drug_effect low | **converged** drug_effect |
| artifact (t 76, see 2 below) | unclear medium [surg-01] → artifact low | artifact high [vit-art, vit-nibp-02] → same | unclear low [enc-05] → unclear, or artifact low | **converged** artifact, quiet flag |

For every message, check:
- one sentence of 35 words or fewer;
- no banned wording;
- every id is allowed;
- every number in the hypothesis and notes appears in the input (automate this);
- drug-01 and drug-02 in drug_effect are never given a time;
- record is never `high`;
- round 2 answers both other agents;
- every change of category says whose evidence caused it.

Voice: in each scenario, ask "When was the last anticoagulant dose?", "What's the pressure now?", "What was his platelet count?" (expect "I don't have that in the record."), "Should we give blood?" (expect the refusal) and "Is he bleeding?" (expect the agents' views, framed as theirs).

## Sanity check: mismatches with the scenario files
I applied the prompts to each scenario by hand. All three expected outcomes hold: true_bleed split, drug_effect converged, artifact converged. These details differ:
1. **true_bleed, surg-04 is in the future at the alert.** The alert is at t 60, but surg-04 (bipolar) is at t 95. The expected surgical citations and the team question ("suction and bipolar now in use") can't happen at alert time. The prompts still give bleeding_concern from surg-02 and surg-03. Drop surg-04 from the expected values, or re-run the deliberation after t 95.
2. **artifact, the evidence arrives after the event.** The event is at t 50 (spike onset), but the expected anesthesia hypothesis needs the recovery (about t 59) and vit-nibp-02 (t 75). Run this deliberation at t 76 or later; the alert is a quiet flag anyway. Run at t 50 and anesthesia can only judge the jump shape (artifact at low or medium, or unclear), and the outcome may split.
3. **The record agent sees the same chart in every scenario.** Its answers differ between scenarios only because `agent_evidence.record` picks different entries. With the full chart, round 1 is identical in true_bleed and drug_effect (apixaban against losartan). The round-2 rules still reach the expected outcomes. Even the subset is mixed: note-01 mentions losartan, so true_bleed round 1 is bleeding_concern **low**, where the file expects medium. Confidence only affects the tie-break.
4. **Expected record drug_effect is a round-2 position.** The expected text "with no surgical signal reported" is knowable only in round 2, so read `expected_hypotheses` as final positions.
5. **The "adopt when unclear" rule changes two categories, not the outcomes.** Surgical in drug_effect may move to drug_effect, and record in artifact may move to artifact. Both outcomes stay converged. Keep the rule: without it, artifact could end as artifact plus two unclears, which is a split under the SPEC rule.
6. **true_bleed can converge instead.** Anesthesia may move to unclear in round 2, which gives a converged bleeding_concern. The file accepts this, and the prompts lean towards holding.
7. **Omni on routine footage** may say no blood is visible (the footage is not a bleed). Surgical then drops to low but stays bleeding_concern, so the outcome is unchanged.
8. **drug_effect drug times.** drug-01 and drug-02 have `t_s: null`, so they are cited as "running, time not recorded". If the placeholder drug-03 is deleted after the fetch, expect anesthesia drug_effect **medium**. Re-word the scenario's shared view ("within about 90 s of a propofol increase") to match.
9. **Null values.** vit-nibp-01 (drug_effect) and lab-03 to lab-05 are null. Leave them out of the blocks and the allowed ids.
10. **Time reference.** The expected anesthesia text says "about 70 s before the fall began". The prompts quote time relative to the alert (85 s), because fall onset is not a computed field. Add `fall_onset_rel_alert_s` to the summary if you want onset.
11. **`art_spike` is new.** It isn't in SPEC, which only shows `map_drop`. The code must emit it for the SBP-jump trigger.
12. **The worked examples are invented cases.** They no longer come from the scenarios: surgical uses a bipolar observation during gallbladder dissection, anesthesia a dexmedetomidine bolus with a falling heart rate, record a clopidogrel and metoprolol chart. None shares a scenario's numbers, drugs, source ids in use, or category pattern. They teach the JSON form and the round-2 keep rule. Still flag eval outputs that copy an example's wording.
