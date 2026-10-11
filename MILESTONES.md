# Remaining milestones: copy-paste prompts for Cursor

How to use: open a **new Agent chat** for each step (so old context doesn't pile up), paste the prompt in the box, and let it run. Each prompt tells the agent to read the handoff files first, so it starts cold. Tick the box when done.

---

## [x] Step 0: check where things stand (do this first, every time you switch to Cursor)

```
Read AGENTS.md, HANDOFF.md and CLAUDE.md. Then check the real state of the GX10 without changing anything yet:
1. If this folder is not a git repo, run git init and commit everything as "handoff baseline". If it is, show git status and commit any uncommitted work as "wip before cursor".
2. curl -s localhost:7000/health and tell me which milestone it reports.
3. docker ps -a, and curl -s localhost:8000/v1/models. If the lightning container exists, show the last 15 lines of docker logs lightning.
4. Run the backend tests and the frontend build.
5. ls ~/sentinel_or_detector.zip ~/M4a.patch and ls ~/assets/models.
If health says M3 (not M4a) and ~/M4a.patch exists: apply it with patch -p1 from the repo root; if parts fail because of fixes already made, merge by hand and keep everything that works; rerun the tests; commit.
Then update the status table in HANDOFF.md to match what you found, and tell me in five lines or fewer what works, what is running, and which step in MILESTONES.md is next.
```

---

## [ ] Step 1: real-model test (Lightning on :8000)

Wait until `curl -s localhost:8000/v1/models` lists `lightning`.

```
Read AGENTS.md, HANDOFF.md and CLAUDE.md. Lightning is up on localhost:8000. Goal: the three scenarios work against the real model.
1. Make sure the app is running (curl localhost:7000/health). If not, start it in a new tmux window with the command in HANDOFF.md section 4. Do not stop an app that is already running.
2. For each scenario (true_bleed, drug_effect, artifact): select it and play it through the /control API, wait for the deliberation to finish, and show me each agent's category, confidence, hypothesis and cited ids, plus the outcome (converged or split) and deliberation_s. Expected: true_bleed = split, drug_effect = converged, artifact = converged. Run each scenario 5 times and give me a small table of results.
3. If agents come back unavailable, find out why (schema rejected by vLLM, finish_reason length, timeout, validation failure) from the backend log and fix it. Allowed fixes: prompts in prompts/, max_tokens, timeouts, the schema shape vLLM accepts. NOT allowed: weakening the validation rules (source ids, banned wording, one sentence) or showing scripted text.
4. Then generate a post-op report (POST /report/generate) and check the summary is written, not "Narrative unavailable". If validation keeps failing, fix the prompt in prompts/report.md, not the numbers rule.
5. Run pytest and the frontend build again, commit, update HANDOFF.md, and run python3 scripts/pitch_numbers.py and show me its output.
Tell me plainly what works on the real model and what does not.
```

---

## [x] Step 1b: speak-or-hold + simple screen 2 (judge advice)

Use this only if the patch from the Claude project never arrives.

```
Read CLAUDE.md and HANDOFF.md. Build two things, keep every hard rule.
1) Speak-or-hold: a backend module that decides when Sentinel may speak to the surgeon. HOLD while a cutting, clipping, energy or hook-dissection instrument is in use (Clipper, Scissors, Bipolar, Hook), OPEN when only Grasper/Irrigator or no instrument is in view, after a 1.5 s settle. Non-urgent messages (the team question or shared view after deliberation) queue while HOLD and are released when OPEN or when the surgeon asks (a /control "Surgeon asks" button). Urgent messages (demo thresholds MAP < 55 or SpO2 < 90, labelled as demo thresholds) are never held. The visual alert banner is never held (hard rule 3). Tools come from the detector when M2 runs, else from a scenario instrument timeline; label which on screen. Log speech_held, speech_released, speech_urgent in the audit log. Screen 1: a small chip "Holding: clipping" / "Clear to speak", and a caption with the released message plus "held 4.2 s, released: instruments withdrawn". Tests for the gate.
2) Simple screen 2: default view = three agent nodes (big category word + 1-3 confidence bars + which sources they used, as icons/short labels), animated arrows during round 2, outcome card in the middle (Converged: X / Split: X vs Y), one-line team question, compact vitals strip, speak-or-hold status. No paragraphs on the main view; click an agent for its hypothesis and evidence; a "Details" toggle shows the old full dashboard. CSS animations only, no new libraries.
Run pytest and the frontend build, restart only the app, and check both screens.
```

## [ ] Step 2: detector (M2)

```
Read AGENTS.md, HANDOFF.md and CLAUDE.md, then do milestone M2 (vision). The trained detector is ~/sentinel_or_detector.zip (best.pt, best_e2e.onnx, display_classes.json with per-class thresholds, GX10_EXPORT.txt). Unzip it to ~/assets/detector/, never into the repo.
1. Get inference running on this GX10 (aarch64, CUDA 13). Try a TensorRT engine first using GX10_EXPORT.txt (engines must be built on this box). If TensorRT takes more than 30 minutes to get working, run best.pt with Ultralytics on the GPU instead and say so.
2. Wire it into backend/video_worker.py at detection_hook: bounded queue, frame ids, drop frames when behind, draw boxes server-side only on the frame they came from, only the classes in display_classes.json with their thresholds. Video must never wait for the detector; if the detector stops, mark it unavailable on screen.
3. Feed detections to backend/phase.py so the step label comes from the video, and replace the "scenario file (no live detector yet)" basis for the surgical agent's tool window when the live tracker is running (keep the label honest about which source is used).
4. Write metrics.detector_ms and metrics.detector_fps (render FPS stays separate) so screen 2 and scripts/pitch_numbers.py show real values.
5. Log phase changes to the audit log (they are picked up from state.phase already); do not log every frame.
6. Add tests, run pytest and the frontend build, commit, update HANDOFF.md. Tell me the measured detector ms per frame and FPS, and show me how to see the boxes on /surgical.
Do not use the GPU so heavily that Lightning on :8000 stops; check free -g before and after.
```

---

## [ ] Step 3: voice (M5)

First start speech services. Prompt:

```
Read AGENTS.md, HANDOFF.md and CLAUDE.md, then do milestone M5 (voice). Speech must run locally on this GX10.
1. Speech to text on :8002: start Parakeet with
   docker run -d --name parakeet --gpus all -p 8002:8000 -v ~/assets/hf-cache:/cache/huggingface -e HF_HUB_OFFLINE=1 martinb78/parakeet-tdt-v3-spark:latest
   (pull it first if missing; check its README for the model id). If it won't start in 30 minutes, use another local Whisper/Parakeet option that runs on aarch64 and say which.
2. Text to speech on :8003: use the simplest one that starts offline. Try Kokoro first:
   docker run -d --name tts --gpus all -p 8003:8880 ghcr.io/remsky/kokoro-fastapi-gpu:v0.8.1-cu129-arm64
   Use Magpie only if Kokoro fails. Wrap whichever runs so the backend calls POST /tts (text in, wav out).
3. Implement backend/voice.py and a push-to-talk control: hold the space bar (or a USB pedal that sends a key) on /dashboard or /control, record from the browser mic, send to the backend, transcribe, answer with Lightning using prompts/voice_answer.md (at most 2 sentences, grounded in loaded data with source ids, same validation and banned-wording rules as the agents), speak it sentence by sentence, show the answer and ids on screen 2. Pressing again stops playback.
4. Every question asked aloud MUST be logged: audit.record("voice_qa", summary, {"question", "answer", "source_ids", "latency_ms"}, actor="voice"), and must show in the post-op report.
5. Voice must never block the alert or the video.
6. Tests, frontend build, commit, update HANDOFF.md. Tell me the measured time from releasing the key to first spoken word, and how I test it from my laptop browser (the browser may need http://localhost or a mic permission; if mic access from a remote browser is blocked over plain http, give me the simplest workaround, such as an SSH tunnel so the page is localhost).
```

---

## [ ] Step 4 (optional): VSS rolling captions spike, 45-minute time box

Only if Steps 1 to 3 work, the wifi is up, and Omni is serving on :8001 (Omni download and start: `DOWNLOADS.md` section 6 step 5). Read `docs/vss-findings.md` first.

```
Read AGENTS.md, HANDOFF.md and docs/vss-findings.md. Do the M3b spike exactly as described in the "Recommendation" section of docs/vss-findings.md, with a hard 45-minute time box: pull the NVIDIA VSS RT-VLM sbsa container, point it at our Omni on :8001 (openai-compat), use a port other than 8000, and test captions on the demo video. Only if captions arrive within a chunk or two, add the small backend adapter (rolling captions as vss-<chunk> sources in the surgical agent's evidence, written to the audit log, an env flag to switch between this and omni.py). Never put captions on the alert path. If any step fails inside 45 minutes, stop, undo, and tell me; we keep omni.py. Do not stop or restart lightning.
```

---

## [ ] Step 5: pitch numbers

After at least three real runs of each scenario (with the detector on, if M2 is done):

```
Read AGENTS.md and HANDOFF.md. Run python3 scripts/pitch_numbers.py against the running backend. Write the results to PITCH_NUMBERS.md in plain words: one line per number (bytes out, event-to-alert ms, detector ms per frame, tokens per second across the three agents, GB of memory in use, deliberation seconds), each with the median and how many runs it came from. Mark any None as "not measured, do not quote". Note that tokens per second includes prompt processing, so phrase it as "about N tokens a second across the agents". Do not invent or round up any number. Commit.
```
Then paste `PITCH_NUMBERS.md` into the blanks in the pitch script (`pitch/pitch_script.md` in the Claude project), or ask Claude there to do it.

---

## [ ] Step 6: offline check and backup demo video

```
Read AGENTS.md and HANDOFF.md. Prepare the backup demo video.
1. Write scripts/demo_run.py: drives /control through all three scenarios in a fixed order with pauses long enough for each deliberation to finish, then opens the post-op report and runs the audit Verify. It should print what is on screen at each moment so I can narrate.
2. Before recording, run the offline check: tell me the exact commands to turn wifi off on the GX10 (nmcli radio wifi off) and back on, restart nothing that is already working, and confirm the app still works with bytes out staying at 0. Do not turn the network off yourself while I am connected over SSH through wifi; tell me first, because it will cut my connection.
3. Give me the simplest way to record screen 1 and screen 2 together from my Windows laptop (for example Snipping Tool's video record with Win+Shift+R, or OBS), at 1080p, 3 minutes or less.
Commit, update HANDOFF.md.
```
Recording must be finished before Sunday 1:30 pm (Devpost) and the GX10 goes back at 3:45 pm.
