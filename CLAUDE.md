# Sentinel-OR: build spec (save as CLAUDE.md at the repo root)

> **For Raag, not Claude Code:** put this file at the root of the code repo as `CLAUDE.md`. Claude Code reads it automatically, so prompts can be one line, e.g. "Do milestone M1." Start a fresh session (or `/clear`) per milestone. Use Sonnet for M1, M2, M4 and M6, and Opus for M3 and for hard bugs. Keep videos, weights and datasets in `../assets/`, outside the repo.
>
> Background: research/sentinel-or-build-plan.md. Data files: sentinel-or/data/. Detector notebook: sentinel-or/training/. Downloads: sentinel-or/DOWNLOADS.md.

## What we're building

Sentinel-OR is an offline research prototype for a hackathon (ASUS Local AI track), running entirely on one ASUS Ascent GX10 (NVIDIA GB10, ARM64, CUDA 13, 128 GB unified memory). **Nothing may call the internet at runtime.**

- **Screen 1, surgical view:** recorded laparoscopic cholecystectomy video (Cholec80) with live instrument boxes, a thin procedure-step label, a restrained alert banner, and the persistent label "Recorded footage · Research prototype".
- **Screen 2, "how it thinks":**
  - vitals (replayed from VitalDB or simulated, always labelled);
  - anesthesia drug log;
  - patient chart with source IDs;
  - event timeline;
  - three agent columns (surgical, anesthesia, record) and their deliberation;
  - performance panel.
- **Voice:** the presenter holds a pedal or key, asks a question, and gets a short spoken answer plus details on screen.

When blood pressure drops, the alert appears **immediately, from code**. Then the three agents each form a hypothesis from their own evidence and answer each other once. Code (not an LLM) decides whether they converged:
- **Converged:** show the shared view.
- **Split:** show both explanations side by side, with evidence, and a question to the team.

## Hard rules (never break these)

1. No diagnosis, no treatment instructions, no medical risk percentages. Use the categorical confidence low, medium or high.
2. Every agent statement cites `source_id`s that exist in the loaded data. Validate this; on failure, retry once, then show "Context unavailable. Source record remains available for review." Never substitute scripted text for failed model output.
3. Alerts never wait for models. Video playback never waits for detection or for agents. Use separate workers and bounded queues, and drop frames when behind.
4. Draw boxes only on the frame they were computed from. Mark the detector as unavailable if it stops.
5. Label everything:
   - simulated or synthetic data;
   - real but time-shifted data;
   - attribution: "Video: Cholec80, CAMMA (CC BY-NC-SA 4.0). Vitals: VitalDB case 59, Lee HC et al., Sci Data 2022 (CC BY 4.0)."
6. All metrics shown must be real measurements. No placeholders presented as live values.
7. Zero outbound network at runtime. A "bytes out" counter on screen 2 reads from the host's network interface counters.

## Equal features (do not trade one for the other)

Sentinel-OR does two jobs and both must work in the demo: (1) the live assistant (follows the procedure, watches the
surgeon on video and the patient's vitals together, alerts, deliberates, answers spoken questions) and (2) the
accountability record (a tamper-evident audit log of everything that happens, and a post-op report drafted locally from
that log plus the drug log and the chart). If time gets short, cut Omni, the eval harness, TensorRT and polish, not
either of these.

## Stack and ports

| Service | What | Port |
|---|---|---|
| vLLM | Nemotron 3.5 Lightning NVFP4 (+ DSpark draft), OpenAI-compatible, structured output via `response_format: json_schema` | 8000 |
| vLLM | Nemotron 3 Nano Omni NVFP4, used only on events (last 10 s of video + question). `mm_processor_kwargs` (top level): `use_audio_in_video: false` (Cholec80 has no useful audio; `true` needs `vllm[audio]`). `chat_template_kwargs: {enable_thinking: false}`. | 8001 |
| ASR | Parakeet TDT 0.6B v3 in the NGC PyTorch arm64 container, a tiny FastAPI `POST /transcribe` (wav in, text out) | 8002 |
| TTS | Magpie via NeMo-Speech.cpp (Riva gRPC, no NGC key) or the Magpie NIM; fallback Kokoro-FastAPI. Wrap whichever runs in a tiny `POST /tts` (text in, wav out). See DOWNLOADS.md step 6. | 8003 |
| Backend | Python 3.12, FastAPI + WebSockets, asyncio | 7000 |
| Frontend | React + Vite + TypeScript, uPlot for charts; two routes, `/surgical` and `/dashboard`, opened full-screen on two monitors | 5173 |

Start Lightning first: NVFP4 kernels can take up to ~45 min to compile on first boot; keep the cache volumes. Exact flags: DOWNLOADS.md section 6.

Model memory budget: about 90–100 GB. Skip Nemotron Super. Check with `nvidia-smi` and `free -g`.

## Audit log and report rules (M4a)

- `backend/audit.py`: append-only JSONL, each entry hashed together with the previous hash. Everything that reaches the
  screen as an alert, agent message, outcome, presenter control, drug entry, cuff reading, step change or voice answer
  is also logged, with the source ids it cited. Never log something the screen did not show.
- Say tamper-evident, not tamper-proof. Show the head hash on screen 2.
- `backend/report.py`: tables from the log by code; the model only writes a short cited summary (ids must exist, numbers
  must appear in the cited entries, banned wording check, one retry, then "Narrative unavailable").
- Voice answers must be logged as `voice_qa` entries.

## Repo layout

```
sentinel-or/
  CLAUDE.md
  data/                  # copied from project files (JSON, CSV, fetch script). Small files only.
  backend/
    app.py               # FastAPI: REST + WS /ws/state, MJPEG /video.mjpg, presenter controls
    audit.py             # tamper-evident hash-chained audit log
    report.py            # post-op report draft from the log
    state.py             # single authoritative EventState + pub/sub to WS clients
    video_worker.py      # decode -> detect (TensorRT YOLO) -> draw -> JPEG queue; frame ids; FPS stats
    phase.py             # deterministic phase tracker from data/procedure/phase_rules.json
    telemetry.py         # replays scenario vitals + drug log on the video clock; baseline rule; alert
    agents/
      orchestrator.py    # runs the 3 agents concurrently, round 1 + round 2, validation, convergence (code)
      schemas.py         # pydantic models = JSON schemas sent to vLLM
      prompts/{surgical,anesthesia,record}.md
    omni.py              # event-triggered video question to Omni; timeout -> skip
    voice.py             # push-to-talk: record -> ASR -> answer (Lightning, grounded) -> TTS -> play; barge-in
    metrics.py           # latencies, FPS, GB used, bytes out
    eval/run_eval.py     # headless: every scenario variation, agents vs baseline -> results.csv + summary
  frontend/
  scripts/start_models.sh, scripts/offline_check.sh
../assets/               # cholec80 videos, weights, engines (NOT in repo)
```

## Shared state (one source of truth, pushed over WS on every change)

```json
{
  "scenario_id": "drug_effect", "clock_s": 412.3, "playing": true,
  "phase": {"name": "ClippingCutting", "since_s": 380.0, "tools_window": {"Clipper": 7, "Grasper": 9}},
  "vitals": {"t_s": 412, "hr": 64, "sbp": 82, "dbp": 41, "map": 55, "spo2": 98, "source": "VitalDB case 59 (time-shifted)"},
  "drug_log": [{"t_s": 330, "drug": "propofol", "detail": "rate increase", "source_id": "drug-03"}],
  "event": {"event_id": "ev-1", "event_type": "map_drop|art_spike", "event_timestamp": 405.0, "simulated": false,
            "alert_shown_at": 405.02, "context_status": "pending|available|unavailable"},
  "agents": {"surgical": {...AgentMessage}, "anesthesia": {...}, "record": {...}},
  "deliberation": {"round": 2, "outcome": "converged|split|pending", "team_question": "...", "summary": "..."},
  "baseline": {"rule": "MAP < 65 for >= 60 s, or SBP > 180 for >= 5 s", "alarm": true},
  "metrics": {"render_fps": 25, "detector_fps": 60, "detector_ms": 1.8, "event_to_alert_ms": 18,
              "deliberation_s": 3.9, "gb_used": 94.1, "bytes_out": 0, "models_resident": ["lightning", "omni", "parakeet", "magpie", "yolo"]}
}
```

## Agent protocol

`AgentMessage` (pydantic, sent as json_schema):

```json
{
  "agent": "surgical|anesthesia|record", "round": 1,
  "category": "bleeding_concern|drug_effect|artifact|unclear|no_concern",
  "hypothesis": "one sentence, no diagnosis or treatment",
  "confidence": "low|medium|high",
  "evidence": [{"source_id": "drug-03", "note": "propofol rate up 75 s before MAP fell"}],
  "responds_to": [{"agent": "record", "stance": "agree|disagree|adds", "note": "..."}]
}
```

- **Each agent sees only its own evidence:**
  - surgical: phase, tool window and recent tool events, plus the Omni answer if available;
  - anesthesia: vitals trend and drug log;
  - record: the patient chart.
- **Round 1:** all three run concurrently on the event. **Round 2:** each agent sees the other two round-1 messages and replies once. The round-2 message also carries the agent's own round-1 message and a `ROUND_TWO_RULE` line: code picks which keep/adopt rule of the protocol applies from the round-1 categories alone (never from the scenario); the model still reasons over its evidence, writes the message and cites its sources. Stances (`agree`/`disagree`/`adds`) are set by code from the two round-2 categories.
- **Converged** = all three round-2 categories are the same, or two agree and the third is `unclear`. **Split** = anything else.
- **Team question** comes from a template filled with the top two categories and their evidence, e.g. "Two explanations fit: possible bleeding (phase: clipping and cutting; irrigator active) and drug effect (propofol increase 75 s ago). Check the field and confirm the drug timing."
- Event types: `map_drop` (baseline-rule style low MAP trend) and `art_spike` (arterial SBP spike). For `art_spike`, wait until the spike has recovered and a cuff (NIBP) reading exists before deliberating (about 20 s; see prompts/README.md).
- Prompts live in `prompts/` (copy into the repo); README there lists known mismatches with the scenarios.
- Keep it fast: thinking off, `max_tokens` 256 in round 1 and 320 in round 2, temperature 0.2, at most two rounds, 8 s timeout per call, then mark that agent unavailable.

## Milestones (do them in order; each must run end-to-end before the next)

- **M0, models up (the first hour on the GX10):**
  - `scripts/start_models.sh` starts all services.
  - A smoke test hits each port and prints latency.
  - `scripts/offline_check.sh` checks with the network disabled.
- **M1, skeleton:**
  - two routes;
  - WS state;
  - MJPEG video from a file;
  - vitals replay from a scenario JSON;
  - presenter controls (play/pause, select scenario, trigger, reset, restart) on a small `/control` page;
  - all labels.
- **M2, vision:** TensorRT YOLO in `video_worker` (bounded queue, frame ids, drop when behind), boxes drawn server-side, phase tracker, step label. Measure render FPS and detector FPS separately.
- **M3, agents:**
  - orchestrator with both rounds and validation against loaded `source_id`s;
  - convergence decided by code;
  - screen 2 columns and deliberation feed;
  - Omni call on events (skip it on timeout).
- **M4a, audit log and post-op report (built; check against real Lightning):** `backend/audit.py`, `backend/report.py`,
  screen-2 audit panel, `/post-op` page, `scripts/verify_audit.py`.
- **M4, scenarios and proof:**
  - all three scenarios replay correctly;
  - baseline rule shown;
  - `eval/run_eval.py` runs N variations per scenario (jittered timings and magnitudes) and writes `results.csv`;
  - a summary table on screen 2: correct explanations and needless alarms, agents vs baseline.
- **M5, voice:**
  - push-to-talk (space bar or USB pedal);
  - Parakeet → grounded answer of at most 2 sentences with `source_id`s → TTS;
  - stream sentence by sentence; pressing again stops playback.
- **M6, polish:**
  - performance panel;
  - failure states;
  - reset;
  - full rehearsal with the network unplugged;
  - record the backup video.

## Data (in `data/`)

- `patient/patient_record.json`: chart entries with `source_id` and `provenance` (real VitalDB case 59 vs synthetic).
- `scenarios/{true_bleed,drug_effect,artifact}.json`: video segment, vitals keyframes or VitalDB window, drug log, expected agent behaviour, ground truth, and expected baseline behaviour.
- `procedure/phase_rules.json`: Cholec80 phases, tools, transition rules.
- `vitaldb_case59_*.csv/json`: produced by `fetch_vitaldb_case.py`. Interpolate vitals at 1 Hz onto the video clock.

## Style

- Python: type hints, pydantic v2, asyncio, no global mutable state outside `state.py`, structured logs.
- Frontend: dark clinical theme, large type for screen 1, no animation on the operative field.
- Small modules. Before each change, run the smoke test for the touched component.
