# VSS: what the judges meant, and whether Sentinel-OR should use it

Checked on the web 2026-10-10 (sources at the bottom). Nothing in app-m3/ or app-m4a/ was changed.

## What it is
"VSS" is almost certainly NVIDIA's **AI Blueprint for Video Search and Summarization** (current release 3.2.1, July 2026, Apache-2.0 repo). It is a set of microservices for video agents: live captions, real-time alerts, video Q&A, search, summaries and reports.

- **Models:** VLM = Cosmos Reason 3 Nano by default (Nemotron Nano Omni, Qwen3-VL/Omni, Qwen 3.5 also supported through vLLM). LLM = **Nemotron 3.5 Lightning 30B A3B**, the same model we already run.
- **Live video:** yes. The RT-VLM microservice takes RTSP streams or files, cuts them into chunks, captions each chunk with the VLM, and streams results back (REST, SSE or Kafka).
- **Alerts:** yes, but they are VLM yes/no prompts per chunk ("is X happening?"); a "yes" becomes an incident.
- **Audio in:** only with an Omni model. Set `VLM_MODEL_SUPPORTS_AUDIO=true` and each chunk gets an `audio_transcript` of the video's soundtrack. It is not a microphone/voice assistant.
- **Audio out:** **none.** No text-to-speech anywhere in VSS. Spoken answers stay with our planned Parakeet + Magpie/Kokoro path (NVIDIA's separate "nemotron-voice-agent" blueprint is the voice one; the judges may have blended the two).

## Does it run on the GX10, offline?
- **Partly.** DGX Spark (same GB10 chip) is a validated platform with `-sbsa` images, but the docs say Spark currently supports only **remote-LLM** layouts; "fully local" is listed as a future release.
- **Driver:** docs ask for 580.95.05 (prereqs page) or 580.173.02 (repo README). The GX10 has 580.142, so it is between the two. Unverified whether that matters.
- **Online setup needed:** NGC account + Personal API key, NGC CLI, HF token, NVIDIA AI Enterprise developer license to host NIMs locally. Pulls from nvcr.io. After pulling, the containers should run without network, but no doc says it is tested air-gapped.
- **Size and effort:** one engineer's full VSS 3.0 deployment on a DGX Spark pulled about 36 images, ran 41 compose services, used about 64 GB of unified memory, needed manual fixes (broken ARM LLM NIM, four Alert Bridge patches, missing Spark profile files) and saw responses take minutes. That is a day of work, not an evening.

## Fit with our box
Today's budget: Lightning vLLM at 0.35 GPU memory (~42 GB), Omni at 0.30 (~36 GB), YOLO ~1 GB, speech a few GB.
- **Full blueprint:** does not fit beside that, and duplicates Lightning. No.
- **RT-VLM container alone, pointed at our own Omni vLLM** (`VLM_MODEL_TO_USE=openai-compat`, `VIA_VLM_ENDPOINT=http://localhost:8001/v1`): adds only the pipeline container (decode + chunking), no new model weights. This is the only version that fits. Whether audio still works through openai-compat is not documented.
- **RT-VLM loading its own Cosmos Reason 3 Nano:** adds a second VLM (size not documented; inferred tens of GB). Only if we drop Omni.
- Docs warn: on DGX Spark keep to 2 live streams or fewer. Fine for us (one video).

## Where it helps Sentinel-OR
1. **Surgical agent's video understanding (best fit).** Instead of omni.py's one question on each event, RT-VLM gives a rolling caption every ~10 s ("grasper retracting gallbladder, small amount of blood near clip site"). The surgical agent then has a running video narrative, not just YOLO boxes. This matches Raag's "understands the whole surgery at all times" goal.
2. **A second, VLM-based alert lane** ("Is there active bleeding in view? yes/no") shown next to the YOLO and vitals evidence, logged to the audit chain.
3. **Pitch:** "built on NVIDIA's VSS real-time VLM service, the thing your judges pointed us to" is a good line, and VSS already uses Lightning, so the stack reads as one NVIDIA story.

## Where it adds risk
- Setup needs NGC keys and big pulls, and the Spark path is the less-tested one.
- VLM captions lag by a chunk (seconds), so they must never be on the alert path; the code-fired alert stays primary.
- Yes/no VLM alerts can false-fire; keep them as evidence, not as the alarm.
- It competes for the hours left tonight (venue closes 10 pm) with the real-Lightning test, M2 detector and voice.
- It does **not** give us voice in or out.

## Recommendation
Do not deploy the VSS blueprint. Do a **time-boxed RT-VLM spike** as milestone **M3b**, only after Lightning passes the real-model test and Omni is serving on :8001, and only while the venue wifi is up.

**M3b: rolling video captions via VSS RT-VLM (45-minute time box)**
1. Online: create NGC Personal API key, `docker login nvcr.io`, pull `nvcr.io/nvidia/vss-core/vss-rt-vlm:3.2.1-sbsa`; clone the repo at tag v3.2.1, go to `services/rtvi/rt-vlm/docker`.
2. `.env`: `VLM_MODEL_TO_USE=openai-compat`, `VIA_VLM_ENDPOINT=http://<host>:8001/v1`, `KAFKA_ENABLED=false`, port other than 8000 (Lightning has it), small `VLM_BATCH_SIZE`.
3. Smoke test: `POST /v1/generate_captions` on the demo video file (`file://`, `FILE_URL_ALLOWED_DIRS` set), `chunk_duration` 10, `stream: true`. Pass = captions arrive within a chunk length or two.
4. Only if step 3 passes: a small backend adapter that reads the SSE stream, stores the last few captions with timestamps, adds them to the surgical agent's evidence block as `vss-<chunk>` sources, and writes them to the audit log. omni.py stays as the fallback; one env flag picks which runs.
5. If any step fails inside 45 minutes: stop, keep omni.py, and use the pitch line "designed to plug into NVIDIA VSS RT-VLM".

Order tonight stays: real-Lightning test, then M2 detector, then voice (Parakeet + Magpie/Kokoro). M3b slots in after the Lightning test only if the box and wifi cooperate.

## Sources
- VSS repo: https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization
- Release notes 3.2.1: https://docs.nvidia.com/vss/3.2.1/release-notes.html
- RT-VLM microservice: https://docs.nvidia.com/vss/3.2.1/real-time-vlm.html
- Prerequisites (Spark = remote-LLM only): https://docs.nvidia.com/vss/latest/prerequisites.html
- DGX Spark VSS 3.0 write-up (memory, image count, fixes): https://dev.classmethod.jp/articles/dgx-spark-vss-3-early-access/
- DGX Spark playbook summary: https://deepwiki.com/NVIDIA/dgx-spark-playbooks/6.4-video-search-and-summarization
- NGC container: https://catalog.ngc.nvidia.com/orgs/nvidia/vss-core/containers/vss-rt-vlm/-
