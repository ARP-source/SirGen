# Raag vision, requirements, and current state (~12 hours left)

**Purpose:** Single handoff for Claude Code / Cursor / any agent. Captures what Raag needs for the pitch, what the repo actually does today, what was built recently, and the gap between them. Written 2026-10-11 after Raag rejected framing Sentinel-OR as a “separate research dashboard” — the product story is **built into the surgical tower**, with **AR in the surgeon’s optical path**.

**Repo:** `~/sentinel-or-app-m3` on GX10 (`asus@169.233.72.133`). Mirror also at Cursor workspace `/workspace` (sync with `origin/main` / GX10 as needed). Spec: `CLAUDE.md`. Older status table: `HANDOFF.md` (partially stale vs main branch).

---

## 1. Raag’s product vision (what we are pitching)

Sentinel-OR is an **offline AI copilot integrated into the laparoscopic stack**, not a laptop browser toy.

- **Hardware:** ASUS Ascent GX10 (GB10, ~128 GB unified memory) runs vision + LLM + app **on the box**.
- **Integration:** The GX10 is **hooked up to the surgery machine**. When the surgeon looks through the **endoscope eyepiece / tower display**, they see the **same feed plus AR overlay** (minimal HUD: step, safety cues, spoken assistant text when safe — not a full dashboard blocking the field).
- **Perception:** The system watches **live video from the scope** — frames the models have **not been trained on as that exact clip** (unseen case / unseen session). Pitch must support that claim honestly (live ingest + generalization story).
- **Understanding (must sound complete, not “partly”):**
  - **Three trained vision models** (instruments, anatomy, CVS) → surgery-relevant structure on the feed.
  - **Procedure + surgeon intent** → not optional; must read as a **complete** pipeline (not “heuristics + maybe LLM”).
  - **Vitals + medical history** → processed **together with video** for agents (anesthesia, record, surgical reasoning), not a side panel of fake numbers.
- **Swarm:** Multi-agent **communication must be visible** during the demo (not one vitals event every two minutes with silent cards). Judges should **see agents thinking and messaging each other**.
- **Danger overlay:** Must **help** the surgeon (peripheral, accurate enough to trust), **not** a huge box that blocks the operative view. If it blocks the field, the story “AI understands where danger is” fails.

**Non-negotiables (Raag, 2026-10-11):**

| # | Requirement |
|---|-------------|
| N1 | **Live video** the system was **not trained on** (that session / that clip) — demo and architecture must support this. |
| N2 | **Swarm communication** visibly active (not barely any paper planes / one deliberation per scenario). |
| N3 | **Danger / caution overlay** fixed: understandable, does not continuously obstruct the operative view; must align with “AI sees risk,” not random boxes. |
| N4 | Pitch claims for **training**, **vitals**, and **chart/medical** must go from **“partly true” → “completely defensible”** (or engineering must close the gap). |
| N5 | **Built into the machine / AR in the optical path** — demo may still use screens at the venue, but **story and UX** are tower-integrated AR, not “open localhost on a monitor.” |

---

## 2. What we have today (honest technical inventory)

### 2.1 Runtime architecture (works on GX10)

| Piece | Port / path | Role |
|-------|-------------|------|
| FastAPI app | `:7000` | Video decode → MJPEG `/video.mjpg`, WebSocket state, scenarios, gate, agents, audit, report, TTS proxy |
| vLLM Lightning | `:8000` | Nemotron structured JSON for deliberation, procedure, safety, report narrative |
| Vision service | `:8004` | `vision/server.py` — YOLO tools, YOLO anatomy, ConvNeXt CVS on JPEG frames |
| Frontend (Vite) | `:5173` | `/surgical`, `/dashboard`, `/agents`, `/control`, `/post-op` |
| TTS (optional) | `:8003` | Offline speech for released lines |

**Video today:** OpenCV reads a **file** (`SENTINEL_VIDEO`, default long chole clip e.g. `video13_dissect_to_clip_90s.mp4`). “Live” in code means **live inference on the current playback clock**, not necessarily a **physical scope SDI/HDMI capture** unless that path is wired (not documented as done in repo).

**Three trained vision models (real):**

1. `tools.pt` — Cholec80-Boxes instruments (7 classes).
2. `anatomy.pt` — Endoscapes2023 anatomy boxes (6 classes).
3. `cvs.pt` — Endoscapes CVS criteria (ConvNeXt-Tiny, 3 criteria).

**Procedure step on HUD:** Mostly **deterministic phase tracker** (`backend/phase.py`, `data/procedure/phase_rules.json`) over **detected tools per second** — explicitly “heuristic, not a validated model.”

**Procedure + intent language:** **Procedure agent** (`backend/agents/procedure.py`) — LLM every ~6–25 s while playing, JSON schema, evidence from **live scene only** (no scenario cheat sheet). Outputs doing / intent / next / watch / concern.

**Eight agents on `/agents`:** Vision (scrub tech), Attending (procedure), First Assistant / Anesthesiologist / Chart Reader (deliberation LLMs), Safety Officer, Gatekeeper (code), Archivist (audit/report). UI: glass cards, 3D icons, paper-plane animations, Communication panel, live “THINKING” strip.

**Deliberation swarm (event-driven):** On scenario **vitals events** (`map_drop`, `art_spike` at `event_t_s`), surgical + anesthesia + record run **round 1 → round 2**; convergence by code; safety vetting optional; speak/hold gate. **Frequency:** typically **one deliberation per event** per scenario run (~once per 60–150 s of scripted demo unless presenter triggers again).

**Vitals & chart:**

- Scenarios (`data/scenarios/*.json`) replay **simulated** vitals and drug log on **real Cholec80 footage** (`true_bleed` label: simulated vitals on recorded footage).
- VitalDB case 59 CSV can be used (`drug_effect`); chart in `data/patient/patient_record.json` = **mix of real VitalDB fields + synthetic history** (`data/README.md`).
- Agents at deliberation time cite vitals/chart **source_ids** from loaded evidence — fusion is **real at event time**, but the **demo composite** is **not** “one live OR patient stream” unless re-wired.

**Screen 1 (`/surgical`):** MJPEG + HUD (vitals, step, procedure one-liner, speech). **Caution glow:** bbox proximity heuristic — tool near cystic duct/artery box + padding (`backend/scene.py` `compute_caution`). **Known problem:** large overlay on operative field; Raag: “terrible,” blocks view. See also `docs/screen1-gaps.md`.

**Accountability:** Tamper-evident audit JSONL, post-op report, surgery log — strong for “research + liability trail,” not the main pitch gap.

**Tests:** Large pytest suite (150+); frontend `npm run build` passes on GX10 when deps installed.

### 2.2 What “partly true” meant in the last honest review (must fix for pitch)

| Pitch angle | Current truth |
|-------------|----------------|
| Integrated in surgery machine | **Software prototype** on GX10; surgeon view today = **browser `/surgical`**, not documented **optical AR compositor** on tower output. |
| 3 models → full surgery + procedure + intent | **3 CV models** + **rules for step** + **LLM for narrative/intent** — not one end-to-end “surgery understanding” model. |
| Vitals + history with feed | **At deliberation** and in evidence for LLMs; demo data **simulated / time-shifted / synthetic** portions; not live OR monitor ingest in repo. |
| Live untrained video | **File playback** with models trained on **public lap chole datasets** — same **procedure family**, not the **exact pixels**, but **not** “zero training” and **not** nephrectomy/other ops without domain shift. |
| Swarm always communicating | **Sparse:** planes tied to **new LLM messages** (deliberation rounds, procedure → safety); few events per run. |
| Danger overlay | **Weak geometry on jittery boxes**; can obscure field; does not prove spatial understanding. |

---

## 3. What Cursor / recent agent work added (UI & agents branch)

Roughly 2026-10-10 → 2026-10-11 on `main` (also deployed to GX10):

- **Navigation:** `AppNav.tsx`, redirects `/agent`, `/swarm` → `/agents`; banner on dashboard pointing to swarm page.
- **`/agents` page:** 8 agent roster (`agentsRoster.ts`), animated cards, sparklines, 3D SVG icons (`AgentIcon3D.tsx`), paper-plane layer (`PlaneLayer.tsx` + user PNG), Communication panel from audit log, **Live now** strip for thinking/waiting (`LiveActivityStrip.tsx`, `liveAgents.ts`).
- **Procedure agent** continuous scene reading; **Safety agent** before speech; caution on surgical HUD; offline TTS path; surgery log / post-op (M4a lineage).
- **Default demo video:** longer chole clip (`video13` ~90 s dissect segment) for uninterrupted demo vs short montage.
- **Honest labels** on surgical footer (recorded footage, research prototype, caution = AI estimate).

This work **does not yet close** N1–N5 above; it ** improves judge-facing swarm UI** but not optical integration, live capture, overlay quality, or communication **frequency**.

---

## 4. Gap list (requirements → engineering)

Priority order aligned with Raag’s non-negotiables:

### N1 — Live video not trained on

**Need:** Ingest **live or “fresh” feed** (capture card, tower HDMI, or at minimum a **new file never used in training/eval** with explicit label). Pipeline already drops frames and runs newest-frame inference — **swap source** from `cv2.VideoCapture(file)` to capture device or RTSP if available at venue.

**Pitch language (defensible):** “Models trained on public lap chole datasets; **this case’s feed was not in training**; inference runs frame-by-frame on the Ascent box.” Do **not** claim nephrectomy without retraining or a general model.

**Open questions for venue hardware:** What is the **actual output** from the machine (resolution, latency, connector)? Is compositing on **tower monitor** vs **eyepiece** feasible in 12 h (may need **full-screen surgical view on the tower’s secondary output** as stand-in for AR)?

### N2 — Swarm communication visible

**Need:** More **agent_message**-like activity visible on `/agents`: e.g. trigger **inter-agent traffic** from procedure readings, safety checks, vision events, or **shortened deliberation cadence**; optional **synthetic “heartbeat” deliberation** is **not** acceptable if it violates hard rule 2 (no scripted agent text). Legitimate levers:

- Lower procedure agent gaps / speak concerns → safety → gate (more planes).
- **Second scenario event** or presenter **Trigger** during demo.
- **Round-2** already flies planes — ensure demo **plays through event** and LLM stays up.
- UI: show **thinking** + **message preview on cards** even before audit flush.

### N3 — Danger overlay

**Need:** Redesign screen 1 overlay per `docs/screen1-gaps.md` + Raag feedback:

- **Peripheral** cue (edge glow, thin outline, corner tag) — **not** center mass box + label.
- **Hold / fade** with gate (overlay dim or off while Hook/Clipper/Scissors/Bipolar active).
- **Temporal smoothing** (hold caution 1.5 s already; add EMA on box, higher gap threshold, or only show when ** sustained** proximity).
- Default **no** debug boxes on field (`boxes=0` already default on surgical).

### N4 — Training / vitals / medical “complete”

**Training story — make complete in engineering or copy:**

- Document **exact three models**, datasets, and metrics on `/health` and one slide.
- Add or expose **phase/intent** as: “CV detections → phase rules + attending LLM with cited evidence” (single diagram, no “partly”).
- Optional: run **`scripts/score_phases.py`** on demo video and cite **numbers** if good enough.

**Vitals/medical — make complete:**

- Run `fetch_vitaldb_case.py`; fill `patient_record.json` pending fields; switch scenario to **real excerpt** with on-screen **real / time-shifted** labels.
- Ensure **record + anesthesia agents** show chart + vitals citations on dashboard during event (already designed — verify in demo script).
- If live monitor unavailable, pitch: “**Architecture fuses the same clock**: video frames + vitals samples + chart rows by `source_id`; demo replays VitalDB excerpt **as if** live.”

### N5 — Built into the machine / AR path

**Need (minimum for 12 h):**

- **Narrative + UX:** Rename/reframe `/surgical` as **tower overlay mode** (fullscreen, minimal chrome, black letterbox, faint legal line).
- **Physical (if hardware allows):** Route GX10 HDMI output to **tower display input** or secondary monitor **where surgeon stands**; document “integrated stack” diagram: scope → machine → **GX10 inferencing** → **composited output**.
- **Not required to fake eyepiece** if impossible — required to **stop saying “open browser on laptop.”**

---

## 5. Suggested 12-hour execution order (for Claude)

1. **Confirm GX10 state:** health, vision :8004, Lightning :8000, video file, one full scenario run with deliberation (HANDOFF.md §4).
2. **N1:** Identify live capture path; implement `SENTINEL_VIDEO=0` or `SENTINEL_CAPTURE=/dev/videoX` (or venue-specific); test one uninterrupted minute of inference + HUD.
3. **N3:** Ship peripheral caution + hide during hold (Surgical.tsx + scene caution policy).
4. **N2:** Tune procedure/safety traffic + demo script (control: play, trigger event, show `/agents`).
5. **N4:** VitalDB + chart fill; one-page “data provenance” for judges; align prompt/procedure.md wording to **live** not **recorded** where true.
6. **N5:** Fullscreen surgical + integration diagram in pitch; optional second output.
7. **Record backup demo video** before hardware return.

**Do not cut:** audit, alert path, agents, gate, vision service. **Can cut:** Omni, polish unrelated to N1–N5, extra scenarios.

---

## 6. Demo URLs (venue)

From laptop browser to GX10:

- `http://169.233.72.133:5173/surgical` — screen 1 / tower overlay target  
- `http://169.233.72.133:5173/agents` — swarm (judges)  
- `http://169.233.72.133:5173/dashboard` — vitals, chart, timeline  
- `http://169.233.72.133:5173/control` — play / pause / scenario  

Password / access: see `HANDOFF.md` (GX10 user `asus`).

---

## 7. Hard rules still apply (`CLAUDE.md`)

No scripted agent text. Alerts never wait for models. No diagnosis/treatment. Cite `source_id`s. Label simulated/time-shifted data. Do not weaken validation to make demos pretty.

---

## 8. One-paragraph summary for Claude

Raag is pitching **Sentinel-OR as offline AR on the surgical tower (GX10 on the machine)**, with **live untrained lap feed**, **three trained vision models plus attending-level intent**, **vitals and chart fused with video**, **visible multi-agent swarm**, and a **non-obstructive danger cue**. The repo **already runs** offline vision + LLM + gate + audit on chole-like **file video** with **composite vitals** and **event-sparse deliberation**; **caution overlay and “integrated machine” story are weak**. Next work must prioritize **live ingest**, **overlay UX**, **swarm activity in the demo window**, and **provenance-complete vitals/chart**, without faking agent outputs.

---

*Update this file when N1–N5 are closed or when the venue hardware path is known.*
