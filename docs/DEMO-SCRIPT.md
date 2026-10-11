# Judge demo script (~3 min) · GX10 · SSH not required at podium

**Screens:** Portable monitor = **`/surgical`** (fullscreen F11). Laptop = **`/control`** + **`/agents`**.

## Before judges

```bash
# On GX10 (already set if dev.sh was restarted recently):
# SENTINEL_VIDEO=~/sentinel-demo/video13_full.mp4
# SENTINEL_VIDEO_START_S=20
# SENTINEL_RUN_S=1800
curl -s localhost:7000/health   # vision scene live when playing
curl -s localhost:8000/v1/models # lightning
```

## Beat 1 — “It watches as it plays” (0:00–0:45)

1. **`/control` → Restart** (scenario **true_bleed**).
2. **`/control` → Play**.
3. **`/surgical`**: point at **clean field** (no box on video), **step line**, **footer** (*recorded clip · frame-by-frame on this machine · not trained on this demo clip*).
4. **`/agents`**: cards active; **Live now** / **THINKING** on attending while procedure reads land (~every 6–25 s).

**Say:** *Recorded lap chole clip; every frame runs through three local vision models on the Ascent box, then an attending agent reads the scene. Weights are from public datasets, not this clip.*

## Beat 2 — Vitals + swarm (0:45–1:45)

5. Keep playing until **~60 s** (scenario MAP event) — or **`/control` → Trigger alert now** if short on time.
6. **`/surgical`**: **Alert** in status + **left rail** (not on the video).
7. **`/agents`**: **Swarm log** (always visible) fills during play; paper planes on deliberation; **Communication** for full detail.

**Say:** *Alert is immediate from code. Then surgical, anesthesia, and chart agents deliberate locally; outcome and citations are in the tamper-evident log.*

## Beat 3 — Speak when safe (1:45–2:30)

8. If **`/control`** or screen 2 shows speech **held** (hook/clipper in view), click **`Surgeon asks`** once to release the team line for the demo — *explain: we never speak over active dissection unless the surgeon asks or it’s emergency.*

**Say:** *Speak-or-hold gate — assistant waits while cutting instruments are detected.*

## Beat 4 — Accountability (2:30–3:00)

9. **`/dashboard`**: timeline, vitals source labels, verify chain count.
10. Optional **`/post-op`**: generate draft report.

**Say:** *Same run produced an auditable log and post-op draft — local only.*

---

## Rehearsal checked (automated)

- **true_bleed** to ~90 s: `alert` + **6** `agent_message` + `deliberation_result` (split) + **10** `procedure_reading`, vision **live**.

## Fallbacks

| Problem | Fix |
|---------|-----|
| Frozen video | `/control` → Play |
| Agents unavailable | `docker ps` lightning; wait 30 s, Restart |
| No swarm motion | Trigger alert; open `/agents` + Communication |
| No speech | Surgeon asks after hold clears or instrument leaves frame |

## Honest limits (if asked)

- Recorded clip today, not OR live scope.
- Lap chole–trained detectors; not other operations.
- Caution is a **text estimate** on the left, not drawn on the field.
