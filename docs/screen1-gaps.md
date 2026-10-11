# Screen 1 (`/surgical`) against the "minimal, almost transparent" spec

Spec (Raag): very minimal, almost transparent; alerts only for emergencies; text fully visible only when the
surgeon is not about to do anything important or dangerous; never a disturbance.

Read from `frontend/src/pages/Surgical.tsx`, `frontend/src/styles.css` (`.surgical ...`), `backend/gate.py`
(`GateView`, `SpeechView`) on branch `overnight`, 2026-10-11. No screenshots yet: they need playback, and the
app was in use for a live viewing when this was written.

## What screen 1 shows today

| Element | When it shows | Look | During a hold (Clipper, Scissors, Bipolar, Hook in view) |
|---|---|---|---|
| Step line (`.hud-step`): step, "next ...", gate chip | Always | 17 px text on a 28 % black pill, top left | Whole HUD drops to 22 % opacity |
| Safety-view dots (`.hud-cvs`): "safety view n/3" + three labelled dots | Whenever the CVS model is loaded | Same pill style, top right | 22 % opacity |
| Detection boxes on the video | Always (default `?boxes=1`) | Drawn into the frames by the backend | Unchanged |
| Vitals banner (`.alert`) | Any event whose `alert_level` is `banner`, until reset | 26 px bold, 92 % opaque dark box, centred near the top, second line of small print | Unchanged: full strength |
| Deviation box (`.alert.deviation`) | 20 s after any classifier deviation | 22 px, 92 % opaque box above the footer | Unchanged: full strength |
| Speech caption (`.speech-caption`) | While a spoken line is current | Caption box with a small "held n s, released ..." line | Dimmed, unless urgent |
| Footer: "Recorded footage · Research prototype", model ms/frame, full attribution, model-training credit | Always | Full-width bar at the bottom | Unchanged |
| "Video problem" / "state link" notes | On a fault | Small red text, top right | Unchanged |

## Gaps

1. **The banner is not limited to emergencies.** It shows for every `banner`-level event, including the true_bleed
   event (MAP 10 mmHg below its baseline, MAP 72). The gate's own emergency thresholds (MAP < 55 or SpO2 < 90) are
   much stricter. A non-emergency alert should be a quiet mark, not a 26 px box over the field.
2. **The banner ignores the hold.** It is at full strength while the surgeon is clipping or cutting, and it stays
   until reset. Only a real emergency should break through at full strength.
3. **The deviation box is loud and comes from a weak classifier.** It is a second 92 % opaque box on the field for
   20 s. The CVS "achieved" call is right about 37 % of the times it fires on held-out video. Speech for it is
   already removed on this branch; the box should go from screen 1 too (screen 2 and the log keep it).
4. **Safety-view dots are always on.** Three labelled dots plus a counter sit on the field whenever the model is
   loaded, on the same weak classifier. They belong on screen 2.
5. **Detection boxes are always drawn on the field.** They are the largest visual disturbance and are on by
   default. Default should be off on screen 1 (keep `?boxes=1` for showing the detector).
6. **Nothing is fully hidden during a hold.** The HUD only dims to 22 %; the gate chip, "next ..." and the dots
   are still there. The spec asks for text to be fully visible only when it is clear.
7. **The footer is always opaque and long.** Attribution plus a model-training credit plus ms/frame run across
   the whole bottom edge at all times. Hard rule 5 needs the labels on screen, but they can be one short faint
   line (the full attribution is already on screen 2).
8. **The speech caption carries bookkeeping.** "held 7.6 s, released: instruments withdrawn" is for the audit
   trail and screen 2, not for the surgeon.
9. **No transitions are defined.** Opacity changes are instant, so the HUD flickers when a tool enters and leaves
   the frame. (CLAUDE.md asks for no animation on the operative field; a slow opacity fade on overlay text only is
   the least disturbing reading of both.)
10. **The gate chip is wording for the presenter** ("Holding · Dissecting with hook"). On screen 1 a single small
    dot is enough; the wording belongs on screen 2.

## Proposed screen 1

- **Clear** (no hold instrument, no event): one faint line, top left, step name only, about 60 % opacity, no pill.
  One short faint label line bottom left: "Recorded footage · Research prototype · sources on screen 2".
- **Hold**: the step line and any caption fade to about 10 % (slow fade). No boxes, no dots, no chip.
- **Non-emergency event** (banner-level but not past the emergency thresholds): a small marker next to the step
  line ("BP alert · see screen 2"), which also fades during a hold. The team question is spoken or captioned only
  when the gate is open, as now.
- **Emergency** (the gate's urgent thresholds): the one thing that appears at full strength at any time, and the
  only thing spoken during a hold.
- Deviation remarks and CVS dots: screen 2 and the audit log only.
- Fault notes stay as they are (hard rule 4: a stopped detector must be marked).

## What needs a decision

- Whether the true_bleed alert (MAP 72) should count as an emergency on screen 1. By the gate's thresholds it does
  not, so under this proposal the bleed scenario shows a small marker, not a banner, until MAP goes below 55.
- Whether detection boxes should be off by default on screen 1 for the demo (judges may want to see them).
