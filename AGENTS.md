# AGENTS.md: Sentinel-OR

Read this first, then `HANDOFF.md` (current status and what to do next), then `CLAUDE.md` (the full build spec and its hard rules). `MILESTONES.md` has the remaining work in order.

## What this is
An offline research prototype for the UCSC AI Hackathon (ASUS Local AI track), running entirely on one ASUS Ascent GX10 (NVIDIA GB10, ARM64 / aarch64, CUDA 13, 128 GB unified memory, Ubuntu). It watches a recorded laparoscopic surgery video, replays patient vitals, fires an alert from code when blood pressure drops, has three AI agents (surgical, anesthesia, record) explain it with cited sources, writes everything to a tamper-evident audit log, and drafts a post-op report. Not for clinical use.

## The person you are working with
Raag is not a deep developer. He pastes commands and reads short answers.
- Lead with the answer. Keep replies short and in plain words.
- When he must do something, give one copy-paste command per step and say what he should see.
- Run commands yourself when you can instead of asking him to.
- Say plainly what you tested and what you did not. Never say something works if you did not run it.

## Stack and ports (all on the GX10)
| Port | Service |
|---|---|
| 8000 | Lightning LLM (vLLM, Docker container `lightning`, model name `lightning`) |
| 8001 | Omni video model (optional, not downloaded yet) |
| 8002 | Speech to text (Parakeet), not built yet |
| 8003 | Text to speech (Magpie or Kokoro), not built yet |
| 7000 | Backend: Python FastAPI (`backend/`) |
| 5173 | Frontend: React + Vite + TypeScript (`frontend/`), pages `/surgical`, `/dashboard`, `/control`, `/post-op` |

## Commands
```bash
# tests (from the repo root)
.venv/bin/python -m pytest -q
cd frontend && npx tsc --noEmit && npm run build && cd ..

# run the app (backend :7000 + frontend :5173)
SENTINEL_VIDEO=$HOME/assets/data/holoscan_sample/surgical_video.264 bash scripts/dev.sh

# checks against the running app
curl -s localhost:7000/health
bash scripts/smoke.sh
curl -s localhost:8000/v1/models          # is Lightning up?
python3 scripts/pitch_numbers.py          # measured numbers for the pitch
python3 scripts/verify_audit.py audit/<file>.jsonl
```
Run the tests before and after every change. Commit to git before each milestone.

## Hard rules (from CLAUDE.md, never break them)
1. No diagnosis, no treatment instructions, no risk percentages. Confidence is only low, medium or high.
2. Every agent or report sentence cites `source_id`s that exist in the loaded data. On failure: retry once, then show "Context unavailable. Source record remains available for review." Never put scripted text in place of failed model output.
3. Alerts never wait for models. Video never waits for detection or agents.
4. Draw boxes only on the frame they came from.
5. Keep every honesty label: simulated data, real-but-time-shifted data, "Recorded footage · Research prototype", "Composite of two de-identified public cases", "Draft for clinician review", and the attribution line for Cholec80 and VitalDB.
6. Every number on screen or in the pitch is a real measurement. `None` means "not measured" and must not be quoted or faked.
7. Zero internet at runtime. No cloud APIs in the app.
8. Say "tamper-evident", never "tamper-proof".

## Do not touch
- `~/assets/` (models, videos, data, about 30+ GB of downloads). Read only.
- Running Docker containers, especially `lightning` (a restart can mean a long recompile). Never run `docker rm`, `docker system prune` or restart Docker unless Raag asks.
- The tmux window running `dev.sh` (the app). Use a new window.
- Files in `audit/`. Never edit or delete them; the chain is the evidence.
- `backend/tests/fake_llm.py` is a TEST-ONLY stand-in. Never import it in product code.
- Do not loosen the validation rules (source ids, banned wording, numbers rule) to make the model pass. Fix the prompt instead.
- The pitch files live outside this repo, in Raag's Claude project. Do not invent pitch numbers.
