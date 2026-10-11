# Sentinel-OR (milestone M4a: audit log and post-op report, on M3 agents and the M1 skeleton)

Offline research prototype for the ASUS Local AI hackathon. Not for clinical use. The build spec is
[`CLAUDE.md`](CLAUDE.md), and the data files are described in [`data/README.md`](data/README.md).

## Quickstart

```bash
# backend (Python 3.11+; the spec targets 3.12)
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt

# frontend (Node 22)
cd frontend && npm ci && cd ..

# run both: backend on :7000, frontend on :5173
scripts/dev.sh
```

Open these on the two monitors, plus a third device for the presenter:

| URL | What |
|---|---|
| http://localhost:5173/surgical | Screen 1: full-screen video, step label, alert banner, labels |
| http://localhost:5173/dashboard | Screen 2: vitals, drug log, patient chart, timeline, agents, baseline rule, performance |
| http://localhost:5173/control | Presenter: play, pause, restart, reset, trigger, scenario picker, step-label override |

To run the backend on its own, from the repo root: `uvicorn backend.app:app --port 7000`.

### Checks

```bash
pytest -q                          # from the repo root
cd frontend && npm run build       # tsc + vite build
scripts/smoke.sh                   # needs a running backend; BASE=http://host:7000 to override
python scripts/e2e_m3.py [--shots] # M3 end-to-end through the running backend, against the TEST-ONLY stand-in LLM
```

## Pointing the backend at Lightning (M3)

The agents call Lightning's OpenAI-compatible endpoint on the GX10 (vLLM, port 8000). Set these before starting
the backend (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `LIGHTNING_URL` | `http://localhost:8000/v1` | Base URL including `/v1`. From another machine: `http://<gx10-host>:8000/v1`. |
| `LIGHTNING_MODEL` | first id from `GET /v1/models` | Model name to send. Resolved lazily on the first agent call, so the backend starts without the model. |
| `AGENT_TIMEOUT_S` | `8` | Seconds per model call (connect, generation and reading the body). |
| `RECORD_EVIDENCE` | `full` | `full` gives the record agent the whole chart; `subset` gives it the scenario's `agent_evidence.record` ids. |
| `SENTINEL_PROMPTS` | `prompts/` | Folder with `surgical.md`, `anesthesia.md`, `record.md` (sent verbatim as the system message). |
| `OMNI_URL`, `OMNI_MODEL`, `OMNI_TIMEOUT_S`, `OMNI_ENABLED` | `http://localhost:8001/v1`, first id, `4`, `1` | Optional Omni question, see below. |

```bash
LIGHTNING_URL=http://gx10.local:8000/v1 scripts/dev.sh
curl -s localhost:7000/health        # shows the Lightning URL, the model once resolved, and the agents' state
```

With Lightning down or unreachable the dashboard still works: the alert fires as before, each agent shows
"unavailable" with the reason, and the event context reads "Context unavailable. Source record remains available
for review."

## Video file

The backend decodes a file with OpenCV (bundled FFmpeg; no system ffmpeg needed). It looks in this order:

1. `SENTINEL_VIDEO=/path/to/file`. This can be the HoloHub `surgical_video.264` (raw H.264; FPS falls back to 25 if
   the stream doesn't report one) or a Cholec80 `videoNN.mp4`.
2. The scenario's `video.file`, resolved against `SENTINEL_ASSETS` (default `../assets/`) and then the repo root.
   The scenario files don't set this yet (`video_id` is still TODO). `video.start_s` is used as the seek point
   when it's set.

Keep videos in `../assets/`, outside the repo. With no file, `/video.mjpg` shows a plain test pattern that says
"NO VIDEO LOADED". Surgery is never faked.

Example: `SENTINEL_VIDEO=../assets/cholec80/videos/video50.mp4 scripts/dev.sh`

VitalDB: put `vitaldb_case59_tracks.csv` (from `data/fetch_vitaldb_case.py`) in `data/`. The drug_effect scenario
then replays the real samples from its `vitals_source` window, labelled time-shifted. It starts at
`excerpt_real_start_s` once that is chosen; until then it starts at the window start. Without the CSV it uses the
fallback keyframes, labelled "approximate".

## What M4a adds: audit log and post-op report

Two features, equal in weight with the live assistant (alerts, agents, procedure tracking, voice).

**Audit log** (`backend/audit.py`). Every alert, baseline alarm, drug entry, cuff reading, procedure-step change,
agent message (both rounds, with the source ids it cited), agent failure, deliberation outcome, presenter control
and report generation is appended to `audit/audit-<session>.jsonl` (override with `SENTINEL_AUDIT_DIR`). Each entry
carries the SHA-256 of the entry before it, so a changed, deleted, inserted or reordered line is detectable.

- Screen 2 shows the entry count, the head hash, and a **Verify chain** button that re-reads the file from disk and
  re-computes every hash. Dropped lines at the end of the file are caught against the head the running process holds.
- `python3 scripts/verify_audit.py audit/audit-....jsonl [head-hash-you-noted]` checks a file with the standard
  library only.
- Reset, restart and scenario switches never erase anything; each starts a new `run` inside the same chain.
- Say **tamper-evident**, never tamper-proof: someone who can rewrite the file and the running process could
  rebuild the whole chain. The head hash on screen is the anchor outside the file. The log records what the system
  observed and said, not whether anyone was right.
- Writing is one small append (flush + fsync) after the alert is already published; a failing disk shows "degraded"
  on screen 2 and never stops the alert or the video.
- Voice (M5) must call `audit.record("voice_qa", summary, {"question", "answer", "source_ids", "latency_ms"}, actor="voice")`
  for every question asked aloud (`app.state.svc.audit`).
- Detector (M2): phase changes are picked up automatically from `state.phase` (0.25 s watcher). If M2 wants per-frame
  detections in the log, record `phase_change` / a new kind sparingly (not per frame).

**Post-op report** (`backend/report.py`, page `/post-op`, `GET /report.html`). `POST /report/generate[?run=N]` builds a
draft for a run (default the latest) from the log: procedure steps, each alert with the three agents' messages and the
code-decided outcome and team question, the fixed-rule comparison, drugs given, cuff readings, questions asked aloud,
chart records cited, presenter actions, and the chain verification result. Every line carries its log id.

- The tables are plain code and complete without any model.
- The short summary on top is written by Lightning under the same rules as the agents: every sentence cites ids that
  exist for the run, may only use numbers found in its cited entries, passes the banned-wording check, one retry,
  and otherwise reads "Narrative unavailable" (nothing is written in its place). Prompt: `prompts/report.md`.
- Env: `REPORT_TIMEOUT_S` (default 30, the report call is longer than an agent call). Print to PDF from the page.
- Generating a report is itself logged (`report_generated`, with the SHA-256 of the HTML).
- Labelled "Draft for clinician review", with the composite/simulated labels and attribution.

**Measured numbers for the pitch.** Each deliberation ends with a `metrics_snapshot` log entry: deliberation time,
agent tokens/s (all three at once, and median per call, from vLLM's `usage`; includes prompt processing, so it is not
pure decode speed), event-to-alert ms, memory in use (whole box, `MemTotal - MemAvailable`), render FPS, bytes out and,
once M2 exists, detector ms and FPS (M2 must write `metrics.detector_ms` / `detector_fps`; they stay None until then).
`python3 scripts/pitch_numbers.py` prints them, with medians, mapped to the placeholders in pitch/pitch_script.md.
Screen 2's performance panel shows the same values live. None means not measured; do not quote it.

Not done yet: the stand-in LLM is the only thing the report summary has been run against. Check on real Lightning:
the schema is accepted (enum of ids), `max_tokens` 450 is enough, the 30 s timeout, and that validation passes often
enough (the numbers rule is strict on purpose; loosen the prompt, not the rule, if it keeps retrying).

## What M3 adds

An event (scenario event or presenter trigger) now starts a deliberation on its own asyncio task, **after** the
alert has been published, so a slow or dead model never delays the alert or the video.

- **Evidence** (`backend/agents/evidence.py`), assembled as `prompts/README.md` describes. Each agent gets only its
  own block: system prompt (the prompt file, verbatim) + `ROUND`, `EVENT`, `EVIDENCE`, `ALLOWED_SOURCE_IDS` (+ `OTHER_AGENTS` in
  round 2), with every time as `rel_alert_s`.
  - surgical: phase, 10 s tool window, surgical events up to now, out-of-phase observations (`oop-1`, `oop-2`), Omni answer.
  - anesthesia: vitals trend (10 s steps, 2 s near the alert and fast changes) with a code-computed summary, cuff readings, drug log up to now.
  - record: the whole chart minus null-valued entries.
  - **No detector runs yet (M2).** The phase, tool window and surgical events therefore come from the scenario file and are
    labelled that way (`set_by` / `basis`: "scenario file (no live detector yet)") in the model input and on screen. A
    presenter override of the step label or a live tracker takes precedence when present.
- **Round 1** runs the three agents concurrently (`max_tokens` 200). **Round 2** gives each agent the other available
  round-1 messages (`max_tokens` 256). Temperature 0.2, thinking off, 8 s per call, `json_schema` strict, narrowed per call
  (`narrowed_schema`: fixed agent and round, `source_id` enum of the agent's own ids, length limits, exactly one response per
  available other agent in round 2).
- **Validation** (`check_message`): pydantic shape, agent and round as asked, every `source_id` in the agent's loaded evidence,
  one-sentence hypothesis, the banned-wording list from `prompts/README.md`, round-2 responses complete. A failure is retried
  once; after that the agent is marked unavailable with the reason. A timeout or unreachable endpoint marks it unavailable
  without a retry. **Nothing is ever shown in place of failed output.** Fewer than two valid agents means the deliberation
  is "unavailable" and the UI shows the context-unavailable sentence.
- **Convergence and the team question** (`backend/agents/convergence.py`) are code. Converged = all three round-2
  categories equal, or two equal and the third `unclear`; unavailable agents count as `unclear`. The summary and team
  question are filled from `prompts/team_question_templates.md` using the top two categories and evidence phrases built from the
  loaded records (not from the model's notes). The unit tests reproduce the three worked examples in that file word for word.
- **`art_spike`** events wait (on the video clock) until SBP has recovered and a cuff reading exists (the artifact scenario:
  t 75), at most 40 s after the alert, then deliberate. The alert and the quiet flag are not delayed by this.
- **State**: agents and deliberation update as each message arrives (round 1 shows before round 2), `event.context_status` goes
  `pending`, then `available` or `unavailable`, and `metrics.deliberation_s` is the measured wall time from round 1 start
  to the outcome (`null`, shown as "not measured", until a deliberation completes). Reset, restart, scenario switch (and a new
  event) cancel a running deliberation and clear agents and deliberation.
- **Dashboard**: three agent columns (what it sees, category, confidence, hypothesis, evidence chips that scroll to and
  highlight the cited chart, drug-log or surgical-event row, round-2 stance lines), the deliberation panel (round, converged
  or split, side-by-side explanations on a split, the team question with its source ids), the context status, and the baseline
  rule box next to it.
- **Omni (optional, `backend/omni.py`)**: on an event the last 10 s of the video file are cut to a small mp4 and sent to
  Omni on :8001 with a fixed question. The surgical agent waits for it (bounded by `OMNI_TIMEOUT_S`) while the other two
  start at once. On timeout, error, no video file, or banned wording in the answer, it is skipped and the surgical block
  carries `omni: null`. When used, the answer is labelled as a video model's description. Not enabled in effect until a video
  file is configured, so in this sandbox it is always skipped.

## What M1 does

- **`backend/state.py`**: the single authoritative state (the SPEC shape plus labels, timeline and video info). It
  pushes the full JSON over `WS /ws/state` on every change, throttled to 10 Hz. Alerts bypass the throttle.
- **`backend/scenario.py` + `backend/telemetry.py`**: loads `data/scenarios/*.json` and interpolates the vitals
  keyframes linearly at 1 Hz. The drug log and cuff readings are revealed on the scenario clock.
  - **Baseline rule**: applied exactly as in the data README (R1: MAP < 65 for >= 60 s; R2: SBP > 180 for >= 5 s).
    It fires at t 141 (true_bleed), 81 (drug_effect) and 54 (artifact), matching `fires_at_t_s`.
  - **Alerts**: raised in code in the same tick that the scenario event (`event_t_s`) or the presenter trigger
    fires. The alert is published to WS clients immediately.
    - `event_to_alert_ms` is measured from event detection in code to that publication.
    - `detect_lag_ms` is the scenario-clock tick quantization (20 Hz tick).
    - artifact (`art_spike`) is a quiet flag: a timeline mark, with no banner on screen 1.
- **`backend/video_worker.py`**: decode thread, then a bounded queue (size 2; the oldest frame is dropped when
  behind), then a render thread (JPEG), then `/video.mjpg`. Each part carries an `X-Frame-Id` header.
  - Render FPS is measured.
  - Play and pause follow the scenario clock, and reset/restart rewind the video.
  - **M2 detection hook**: clearly marked no-op (`detection_hook`). No boxes are drawn.
- **`backend/metrics.py`**: bytes out is the sum of tx_bytes deltas on non-loopback interfaces since startup, read
  from `/proc/net/dev`. It is `null` where that file doesn't exist, never a fake 0.
  - Model metrics (detector FPS and latency, memory, models resident) are `null`. The UI shows them as "not measured",
    and so it does for the deliberation time until M3 has measured one.
- **REST**:
  - `GET /health`, `/scenarios`, `/patient`, `/state`, `/vitals/history`
  - `POST /control/{play,pause,reset,restart,trigger}`, `/control/scenario/{id}`
  - `POST /control/phase/{name|auto}` (presenter step-label override, as described in `phase_rules.json`)
- **Frontend**: Vite + React + TS on :5173. It proxies `/ws`, `/video.mjpg`, `/control` (POST) and the read
  endpoints to :7000. GET navigations to `/control` serve the SPA page.

### Still stubbed (no fake output)

- **`backend/voice.py`** (M5): does nothing and speaks nothing.
- **`backend/phase.py`** is implemented and tested on synthetic tool sequences, but not wired to the video until the M2
  detector exists. The detector, its metrics and the memory/models-resident metrics are `null` ("not measured").
- **`scripts/start_models.sh`** (M0) starts Lightning and Omni; ASR and TTS are skipped until `ASR_IMAGE`/`ASR_CMD` and
  `TTS_IMAGE`/`TTS_CMD` are set (no wrapper servers exist yet). `scripts/smoke_models.py` checks every port and prints latency.

## Tests and what they do not show

`pytest -q` runs the M1 tests plus M3's. The M3 tests use a **TEST-ONLY stand-in server** (`backend/tests/fake_llm.py`;
never imported by product code, and a test checks that). It is not a model. It returns schema-valid messages derived from each
scenario's `expected_hypotheses`, so it proves plumbing and rules, not model quality.

**Verified with the stand-in only:** concurrency, request shape (`max_tokens`, temperature, thinking off, `json_schema`),
each agent seeing only its own evidence, validation and retry-once, unavailable handling, convergence and templates, cancel on
reset, alert timing with a slow model, `art_spike` waiting, the UI states (`scripts/e2e_m3.py`, screenshots in
`docs/screenshots/m3_*.png`).

**Unverified (needs the GX10):**
- Real-model behaviour: whether Lightning reaches the expected categories (true_bleed split, drug_effect and artifact
  converged), cites sensibly, and keeps to one sentence and the banned-wording list. Run each scenario at least 5 times
  and use the self-check in `prompts/README.md`.
- Prompt quality, including the worked examples leaking into outputs (README item 12).
- Latency: the real `deliberation_s` (two rounds, one call each per agent) and whether 200/256 `max_tokens` is enough for the
  strict schema. A `finish_reason: length` counts as a validation failure and costs a retry.
- That vLLM's structured-output backend accepts the narrowed schema as written (`enum`, `minItems`/`maxItems`, `maxLength`).
- Omni: the `video_url` data-URI request shape against the served Omni build.

## Layout

```
backend/   app.py audit.py report.py state.py scenario.py telemetry.py video_worker.py metrics.py phase.py omni.py voice.py
           agents/{orchestrator,evidence,convergence,llm_client,schemas}.py  tests/ (incl. fake_llm.py, test-only)
           requirements.txt
frontend/  src/{main.tsx,api.ts,types.ts,styles.css,pages/,components/}  vite.config.ts
data/      scenarios, patient chart, phase rules, VitalDB fetch script
prompts/   agent prompts (used by M3) and voice prompt (M5)
scripts/   dev.sh smoke.sh e2e_m3.py start_models.sh verify_audit.py pitch_numbers.py
docs/screenshots/
```

## Attribution

Video: Cholec80, CAMMA (CC BY-NC-SA 4.0). Vitals: VitalDB case 59, Lee HC et al., Sci Data 2022 (CC BY 4.0).
