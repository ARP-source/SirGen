# Sentinel-OR handoff (for Cursor or a fresh Claude Code)

Status as of 2026-10-11 01:15 UTC (Saturday 6:15 pm venue time; venue closes 10 pm, no overnight). The next agent to finish a milestone updates the table in section 2.

## 1. Get set up in Cursor (once)

**Put these files in the repo.** The repo on the GX10 is `~/sentinel-or-app-m3` (user `asus`, address `169.233.72.133`). Download `cursor-handoff.zip` from the Claude project to your laptop, then in PowerShell:
```powershell
scp C:\Users\orien\Downloads\cursor-handoff.zip asus@169.233.72.133:~/
ssh asus@169.233.72.133 "cd ~/sentinel-or-app-m3 && unzip -o ~/cursor-handoff.zip"
```
That adds `AGENTS.md`, `HANDOFF.md`, `MILESTONES.md`, `IF-SOMETHING-BREAKS.md` and `.cursor/rules/` at the repo root. Nothing else changes.

**Option A (best): Cursor on your laptop, connected to the GX10.**
1. Open Cursor. Press `Ctrl+Shift+P`, type `Remote-SSH: Connect to Host`, press Enter. If it is not listed, install the "Remote - SSH" extension from the Extensions panel first.
2. Type `asus@169.233.72.133`, press Enter, pick **Linux**, type the GX10 password.
3. **File → Open Folder** → `/home/asus/sentinel-or-app-m3` → OK.
4. Open the chat panel (`Ctrl+L`), switch it to **Agent** mode, and pick the strongest model in the dropdown.
5. Paste the first prompt from `MILESTONES.md` ("Step 0").

The agent's terminal now runs on the GX10, so it can run tests, Docker and curl itself.

**Option B: Cursor's command-line agent on the GX10** (if Remote-SSH won't connect). In your normal SSH window:
```bash
curl https://cursor.com/install -fsS | bash
cd ~/sentinel-or-app-m3 && cursor-agent
```
Log in with the link it prints, then paste prompts the same way as in Claude Code.

**Option C: no agent on the box.** Use Cursor on your laptop as a chat only, paste it terminal output, and copy its commands into your SSH window. Slowest; use only if A and B fail.

## 2. Status

| Part | State | Tested how |
|---|---|---|
| M1 skeleton: video, vitals replay, code-fired alert, baseline rule, 3 screens, `/control` | Works on the GX10 | pytest; running in tmux `dl` |
| `/surgical` black video | **Fixed.** The backend was running code from before an `app.py` edit that re-sends frames while paused; restarting the app fixed it. Video = NVIDIA HoloHub endoscopy sample (683 frames, 854x480) | Seen on the portable screen |
| M3 three agents + convergence + team question | **Works against real Lightning** after tuning (details below) | One pass per scenario; 3x stability reruns in progress |
| Agent tuning on the GX10 (each changed file has a `.bak-m3`; full pre-merge backup `~/sentinel-pre-m4a.tgz`) | Done: agents see their own round-1 answer; code-picked `ROUND_TWO_RULE:` line (code runs the protocol, model reasons and cites); stances agree/disagree/adds computed by code; record agent can't say "high" (schema + validation); vitals-number check on record and surgical agents; provenance labels stripped from agent evidence; invented worked examples in prompts; round-2 `max_tokens` 320; footer credits "NVIDIA HoloHub endoscopy sample data" (license not confirmed, only NVIDIA SDK EULA on disk) | Last pass: true_bleed split bleeding vs drug 8.0 s (record agent cites apixaban 19 h + anti-Xa not sent); drug_effect converged drug 7.6 s; artifact converged artifact 11.0 s (anesthesia ran out of tokens, fixed by 320) |
| M4a audit log (hash chain, Verify button) + post-op report (`/post-op`) + measured pitch numbers | **Merged and running on the GX10** (`/health` = M4a, 158 tests before speak-or-hold). Round-1 max_tokens 256, round-2 320 | 9 real runs: bleed 3/3 split, drug 3/3 converged, artifact 2/3 (run 2: anesthesia failed the one-sentence check twice, outcome unclear; rejected text now being logged). Report narrative 9/9 ok, audit verify 9/9 ok |
| Lightning LLM on :8000 | UP. Smoke 190-350 ms; structured output works; 51 of 121 GB in use | Real scenario runs |
| **Speak-or-hold** (judge advice) | **In this repo** (`backend/gate.py`). Patch never arrived on the GX10, so it was built here. Holds non-urgent speech while Clipper/Scissors/Bipolar/Hook is in use, releases 1.5 s after withdrawal or when the surgeon asks (`/control` Surgeon asks), urgent (MAP < 55 or SpO2 < 90, demo thresholds) never held, banner never held. Instruments from `instrument_timeline` until M2 calls `gate.set_live_tools()`. Audit: speech_held / speech_released / speech_spoken / speech_urgent. `?voice=1` on /surgical uses the browser voice | pytest `test_gate.py`. drug_effect Hook until t 46; artifact Hook until t 92; true_bleed irrigator at the event (speaks at once), bipolar from t 95 (MAP < 55 at t 140 breaks through) |
| **Simple animated screen 2** (judge advice) | **In this repo**. Default `/dashboard` = 3 agent cards, confidence bars, evidence kinds, CSS round-2 links, outcome card, one-line team question; Details = old dashboard | frontend build |
| M2 detector (YOLO, boxes, step label from video) | Not started. Weights in `~/sentinel_or_detector.zip` | n/a |
| M5 voice | Not started (`backend/voice.py` stub). Must route answers through the speak-or-hold gate and log `voice_qa` | n/a |
| Omni video model on :8001 | Not running; contributes nothing | n/a |
| Pitch numbers | Measured 2026-10-11 01:30Z, medians of 9 real runs: alert 0.16 ms; deliberation 8.5 s; 121 tokens/s across three agents, 44 per call; 58 GB in use; render 25 FPS. Detector not measured. **Bytes out was ~35 MB, not 0**: it counts all interfaces incl. remote sessions and browsers on other machines. For the claim, run with the uplink unplugged and `SENTINEL_BYTES_OUT_IFACES` set to the uplink (being added) | `scripts/pitch_numbers.py` |
| Pitch + Q&A | Done incl. "Why it has to be this box". Add: speak-or-hold beat; "code runs the protocol, the model does the reasoning" | n/a |
| Backup demo video | Not recorded | n/a |

## 3. Next, in order
1. Done: git init on `~/sentinel-or-app-m3` (`75f5ff8` handoff baseline). `/health` = M4a. Lightning up. 158 tests passed on that baseline.
2. Done: speak-or-hold + simple screen 2 (patch was not on the box). Next after this lands: restart only the app (`dev.sh`), not Lightning.
3. M2 detector (feeds live tools to speak-or-hold and the step label).
4. M5 voice (answers go through speak-or-hold; every Q&A logged).
5. Pitch numbers from real runs, then the backup demo video (before hardware return Sunday 3:45 pm; Devpost closes 1:30 pm).
Open from M4a: log rejected agent text / one-sentence splitter (artifact run 2); bytes-out interface filter.

If time runs short, cut in this order: Omni, the batch eval script, TensorRT (run the detector from `best.pt`), visual polish beyond the simple screen 2. Never cut the audit log, the report, the alert, the agents, speak-or-hold, the detector or voice. The live assistant and the accountability record are equal priority.

Copy-paste prompts for each: `MILESTONES.md`.

## 4. How things are started on the GX10

**The app** runs in a tmux window (Raag named it `dl`). To see it: `tmux ls`, then `tmux attach`, then `Ctrl-b` and the window number. To start it fresh in a new window (`Ctrl-b` then `c`):
```bash
cd ~/sentinel-or-app-m3
SENTINEL_VIDEO=$HOME/assets/data/holoscan_sample/surgical_video.264 bash scripts/dev.sh 2>&1 | tee -a ~/sentinel-dev.log
```
Screens (from the laptop browser): `http://169.233.72.133:5173/surgical`, `/dashboard`, `/control`, `/post-op`. Press **Play** on `/control` or the video stays still.

**Lightning** (already started once; only re-run if `docker ps` does not list `lightning`):
```bash
sudo sh -c 'sync; echo 3 > /proc/sys/vm/drop_caches'
mkdir -p ~/.cache/vllm ~/.cache/flashinfer
docker run -d --name lightning --gpus all --ipc=host -p 8000:8000 \
  -v ~/assets/models:/models:ro -v ~/.cache/vllm:/root/.cache/vllm -v ~/.cache/flashinfer:/root/.cache/flashinfer \
  -e HF_HUB_OFFLINE=1 vllm/vllm-openai:v0.27.1-aarch64 \
  --model /models/lightning --served-model-name lightning \
  --speculative_config.model /models/lightning-dspark --speculative_config.num_speculative_tokens 3 \
  --moe-backend marlin --kv-cache-dtype fp8 --enable-prefix-caching \
  --mamba-backend flashinfer --mamba-cache-mode align \
  --gpu-memory-utilization 0.35 --max-model-len 65536 --max-num-seqs 8 \
  --reasoning-parser nemotron_v3 --tool-call-parser qwen3_coder --enable-auto-tool-choice
```
If it already exists but is stopped: `docker start lightning`. Ready when `docker logs lightning` shows "Application startup complete" and `curl -s localhost:8000/v1/models` lists `lightning`. If compiling never finishes, remove and re-run with `--enforce-eager` added at the end (slower, but starts).

Quick model test:
```bash
curl -s localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"lightning","messages":[{"role":"user","content":"Say ready."}],"max_tokens":20,"chat_template_kwargs":{"enable_thinking":false}}'
```

Omni, speech and the offline test: exact commands are in the project file `sentinel-or/DOWNLOADS.md` section 6 (steps 5 to 8). The relevant ones are repeated in `MILESTONES.md`.

## 5. Where the logs are
| What | Where |
|---|---|
| App (backend + frontend) | The tmux window running `dev.sh`; also `~/sentinel-dev.log` if started with `tee` as above |
| Lightning | `docker logs --tail 50 lightning` |
| Other containers | `docker ps -a`, then `docker logs --tail 50 <name>` |
| Audit log | `~/sentinel-or-app-m3/audit/audit-<session>.jsonl` (never edit) |
| App health | `curl -s localhost:7000/health` |
| Memory / GPU | `free -g`, `nvidia-smi` |
| Earlier downloads | `~/dl-lightning.log` |

## 6. Known issues
- `/surgical` black: SOLVED 00:30 UTC. The HoloHub `.264` decodes fine; the backend was running old code from before a 15:26 PDT edit to `backend/app.py` that re-sends frames while paused. After any code change, restart the app (`dev.sh`) and hard-refresh the page. The repo on the GX10 has local edits to `backend/app.py`, so M4a.patch may need hand-merging there.
- A red "write EPIPE" or node stack trace in the `dev.sh` window when a tab closes is harmless.
- Docker needed `sudo usermod -aG docker $USER` (done once; log out and in if `permission denied` returns).
- First real-model results (2026-10-11 ~01:00Z, one run each, GX10 still on M3 + tuning): true_bleed split bleeding vs drug (7.1 s), drug_effect converged drug (7.1 s), artifact converged artifact (8.0 s); no agent unavailable, all ids valid. Tuning made on the GX10 (each changed file has a .bak-m3): agents see their own round-1 answer (`YOUR_ROUND_ONE:` line), keep-unless-contradicted round-2 rule, provenance keys stripped from agent evidence, footer credits "NVIDIA HoloHub endoscopy sample data" when that clip plays (confirm its license; only NVIDIA SDK EULA on disk).
- Still open: stances from the model are unreliable (plan: compute in code), record agent once said "high" (plan: validate), prompt worked examples copy scenario numbers (plan: invented examples), record agent in true_bleed doesn't bring forward apixaban/anti-Xa, 3x stability reruns pending, M4a not yet applied on the GX10.
- Untested against real Lightning: whether vLLM accepts the strict per-call JSON schema (enums, min/max items, max length); whether `max_tokens` 200 / 256 (agents) and 450 (report) are enough (a cut-off answer counts as a failure and costs a retry); whether the 8 s agent and 30 s report timeouts hold; whether the report's numbers rule is too strict. Fix prompts or token limits, not the rules.
- The surgical agent's evidence comes from the scenario files until M2 is wired in, and is labelled that way. Don't pitch "watches the surgeon" until M2 works.
- The demo video must not be Cholec80 videos 41 to 45 (detector training data).
- Wifi at the venue is slow (about 5 to 6 MB/s). Venue closes 10 pm Saturday, no overnight.
- VSS (NVIDIA Video Search and Summarization): do not deploy the full blueprint. Only the optional RT-VLM spike. Findings: `docs/vss-findings.md` in this pack.

## 7. Files outside this repo (in Raag's Claude project)
`sentinel-or/SPEC.md` (same as CLAUDE.md), `DOWNLOADS.md`, `pitch/` (pitch script with number blanks, judge Q&A, Devpost draft, social post), `vss-findings.md`, the M3/M4a patches and zips. Raag can download any of them and `scp` them to `~/` if an agent needs one.
