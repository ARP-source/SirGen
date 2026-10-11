import { useEffect, useRef } from 'react'
import { useSentinelState } from '../api'

const n = (v: number | null | undefined) => (v == null ? '--' : String(Math.round(v)))

/**
 * Screen 1: the surgeon's HUD. Nothing is drawn on the field: no boxes, tool names, anatomy or
 * classifier remarks. One colour (dim white). It shows the patient's vitals, a short status, and,
 * once the speak-or-hold gate has released it, the agents' outcome and the code-built question.
 * Everything fades to about 10% while a hold instrument is in use. The only full-strength element
 * is an emergency by the gate's thresholds (MAP < 55 or SpO2 < 90).
 */
export default function Surgical() {
  const { state, link } = useSentinelState()
  const spokenId = useRef<string | null>(null)
  const params = new URLSearchParams(window.location.search)
  const voice = params.get('voice') === '1'
  const boxes = params.get('boxes') === '1' // debug only; never on by default

  useEffect(() => {
    document.title = 'Sentinel-OR · Surgical view'
  }, [])

  const cur = state?.speech.current ?? null
  const spoken = cur != null && cur.kind !== 'safety_check' ? cur : null // classifier remarks are never shown or spoken here

  useEffect(() => {
    if (!voice || !spoken || spoken.id === spokenId.current || !spoken.text) return
    spokenId.current = spoken.id
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(spoken.text))
  }, [voice, spoken?.id, spoken?.text])

  const ev = state?.event
  const gate = state?.gate
  const v = state?.vitals
  const delib = state?.deliberation
  const holding = gate?.status === 'hold'
  // gate.emergency comes from the backend; the fallback (older backend) needs the vital to be past the threshold now
  const pastThreshold = (v?.map != null && v.map < 55) || (v?.spo2 != null && v.spo2 < 90)
  const emergency = gate?.emergency ?? (spoken?.kind === 'urgent_vitals' && pastThreshold)
  const emergencyFull = gate?.emergency_text ?? (spoken?.kind === 'urgent_vitals' ? spoken.text : null)
  const emergencyText = emergencyFull ? emergencyFull.split('. ')[0] : null
  const status = emergency ? 'Emergency' : ev ? (ev.alert_level === 'banner' ? 'BP alert' : 'BP flag') : 'No alert'
  const suggestion = spoken && spoken.kind !== 'urgent_vitals' ? spoken.text : null
  const outcome = suggestion && delib?.summary && delib.summary !== suggestion ? delib.summary : null
  const pending = ev && !suggestion && !emergency && ev.context_status === 'pending'
  const vitalsNote =
    v && v.source_kind !== 'none' ? (v.source_kind === 'real_time_shifted' ? 'vitals replayed, time-shifted' : `vitals ${v.source_kind}`) : null

  return (
    <div className={`surgical ${holding ? 'holding' : 'clear'} ${emergency ? 'emergency' : ''}`}>
      <img className="field" src={boxes ? '/video.mjpg?boxes=1' : '/video.mjpg?boxes=0'} alt="Laparoscopic video (MJPEG stream)" />

      <div className="hud">
        <div className="hud-status">
          <span className="status">{status}</span>
          {ev && !emergency && <span className="sub"> · see screen 2</span>}
          {pending && <span className="sub"> · assessing</span>}
        </div>
        {v && (
          <div className="hud-vitals" aria-label="Patient vitals">
            <span><i>HR</i>{n(v.hr)}</span>
            <span><i>BP</i>{n(v.sbp)}/{n(v.dbp)}</span>
            <span><i>MAP</i>{n(v.map)}</span>
            <span><i>SpO2</i>{n(v.spo2)}</span>
          </div>
        )}
      </div>

      {emergency && emergencyText && (
        <div className="hud-emergency" role="alert">
          {emergencyText}
          <small>demo threshold, not a clinical alarm</small>
        </div>
      )}

      {suggestion && !emergency && (
        <div className="hud-issue">
          {outcome && <div className="outcome">{outcome}</div>}
          <div className="ask">{suggestion}</div>
        </div>
      )}

      {state && (state.video.decode_error || state.video.black_frames) && (
        <div className="hud-fault">
          Video problem: {state.video.decode_error || 'the frames are black (mean brightness ' + String(state.video.mean_brightness) + ')'}
        </div>
      )}
      {link !== 'live' && <div className="hud-fault">state link {link}</div>}

      <div className="hud-foot">
        {state?.labels.footage ?? 'Recorded footage · Research prototype'}
        {vitalsNote ? ` · ${vitalsNote}` : ''}
        {' · '}
        {state?.labels.attribution ??
          'Video: Cholec80, CAMMA (CC BY-NC-SA 4.0). Vitals: VitalDB case 59, Lee HC et al., Sci Data 2022 (CC BY 4.0).'}
      </div>
    </div>
  )
}
