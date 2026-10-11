# Sentinel-OR data files

Data for the Sentinel-OR hackathon demo (research prototype, offline on the GX10). Nothing here is clinically validated. Every value is labelled as real, real but time-shifted, synthetic or simulated.

| File | What it is |
|---|---|
| `fetch_vitaldb_case.py` | Downloads one VitalDB case (default 59) on a networked laptop: 1 Hz tracks, the `/cases` row and labs. Prints the lap chole cases that have an arterial line, minutes with MAP < 65, arterial spikes > 200 and TCI target changes. |
| `vitaldb_case59_tracks.csv` / `_clinical.json` / `_labs.csv` | Produced by the script (not in the folder yet). `time_s` = seconds from casestart. |
| `patient/patient_record.json` | Chart for the record agent. Every entry has a `source_id` (`demo-`, `enc-`, `hx-`, `meds-`, `alg-`, `lab-`, `note-`) and a `provenance`. |
| `scenarios/true_bleed.json`, `drug_effect.json`, `artifact.json` | One schema: video segment (TODO), phase at event, vitals keyframes, drug log, surgical events, evidence per agent, expected hypotheses and convergence, expected Sentinel question, baseline rule outcome, scoring. `t_s` = seconds from the video segment start. |
| `procedure/phase_rules.json` | Cholec80's 7 phases and 7 tools, with heuristic sliding-window rules, hysteresis and allowed transitions. Not a validated model. |

## Provenance labels

- `real: VitalDB case 59 /cases`: a real de-identified value. It was read through a web-page fetcher, not downloaded, so re-check it against `vitaldb_case59_clinical.json`.
- `pending: fill from fetch script`: a real field that hasn't been read yet (value `null`).
- `synthetic`: invented, Synthea-style chart content (apixaban, losartan, AF, notes and so on). No real person.
- `simulated`: scripted vitals and drug log (true bleed, artifact, and the true-bleed confounder).
- `approximate`: the drug_effect fallback keyframes, shaped from 1-minute summaries. Replace them with the real case-59 excerpt (minutes 6–19) after fetching.
- Footage is real Cholec80 video from routine cases. The bleed is simulated, and the video doesn't show one.

On-screen label for the composite: "Composite of two de-identified public cases (Cholec80 video, VitalDB case 59 vitals). Some chart fields and scenarios synthetic." The drug_effect vitals are post-induction (before incision) and are shown during dissection, so label them time-shifted.

## Baseline rule (the comparison)

Fixed BP rule: alarm if arterial MAP < 65 mmHg for ≥ 60 s (R1), or arterial SBP > 180 mmHg for ≥ 5 s (R2). It uses arterial-line numerics only, with no context. R1 is the SPEC's "MAP < 65 for >= 60 s". R2 stands in for a monitor's fixed high limit, so the artifact scenario tests it. With the keyframes linearly interpolated at 1 Hz:

| Scenario | Baseline | Right? |
|---|---|---|
| true_bleed | R1 alarm at t = 141 s | Yes, but late and with no cause |
| drug_effect | R1 alarm at t = 81 s (fallback data; recompute on real data) | Yes, with no cause |
| artifact | R2 alarm at t = 54 s | No (needless alarm) |

These thresholds are demo stand-ins, not clinical recommendations.

## Run the fetch script (laptop with internet)

```
pip install vitaldb pandas pyarrow
python fetch_vitaldb_case.py 59        # caseid (default 59), interval in s (default 1)
python fetch_vitaldb_case.py 59 1 --outdir .
```

Copy the outputs into this folder on the GX10. Then:
1. Fill the `pending` labs in `patient_record.json` and check the `real` values.
2. Pick the 120 s excerpt for `drug_effect.json` (`excerpt_real_start_s`).
3. Replace or delete the placeholder `drug-03`.
4. Pick the Cholec80 video IDs and times once the detector has been tested.

Missing tracks become empty columns and are reported.

## Attribution

- **Video:** Cholec80, CAMMA (University of Strasbourg / IHU Strasbourg), CC BY-NC-SA 4.0. Twinanda AP et al., "EndoNet: A Deep Architecture for Recognition Tasks on Laparoscopic Videos", arXiv:1602.03012 (2016); IEEE Trans Med Imaging 2017. Non-commercial use only.
- **Vitals:** VitalDB, Seoul National University Hospital, CC BY 4.0. Lee HC, Park Y, Yoon SB, Yang SM, Park D, Jung CW. "VitalDB, a high-fidelity multi-parameter vital signs database in surgical patients." Sci Data 2022. doi:10.1038/s41597-022-01411-5.
- On screen: "Video: Cholec80, CAMMA (CC BY-NC-SA 4.0). Vitals: VitalDB case 59, Lee HC et al., Sci Data 2022 (CC BY 4.0)."
