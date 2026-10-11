import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useSentinelState } from '../api'

const n = (v: number | null | undefined) => (v == null ? '--' : String(Math.round(v)))

/**
 * Screen 1: surgeon HUD. Video stays clear; proximity caution is text on the side only (no field overlay).
 * Emergency and released speech use the existing banners.
 */
export default function Surgical() {
  const { state, link } = useSentinelState()
  const spokenId = useRef<string | null>(null)
  const params = new URLSearchParams(window.location.search)
  const voice = params.get('voice') !== '0'
  const boxes = params.get('boxes') === '1'
  const [outline, setOutline] = useState(() => params.get('raw') !== '1')
  const [aiSight, setAiSight] = useState(boxes)

  useEffect(() => {
    document.title = 'Sentinel-OR · Surgical view'
  }, [])

  const cur = state?.speech.current ?? null
  const spoken = cur != null && cur.kind !== 'safety_check' ? cur : null

  useEffect(() => {
    if (!voice || !spoken || spoken.id === spokenId.current || !spoken.text) return
    spokenId.current = spoken.id
    const text = spoken.text
    const fallback = () => {
      if (!('speechSynthesis' in window)) return
      window.speechSynthesis.cancel()
      window.speechSynthesis.speak(new SpeechSynthesisUtterance(text))
    }
    fetch(`/tts?text=${encodeURIComponent(text)}`)
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => {
        const url = URL.createObjectURL(b)
        const audio = new Audio(url)
        audio.onended = () => URL.revokeObjectURL(url)
        return audio.play()
      })
      .catch(fallback)
  }, [voice, spoken?.id, spoken?.text])

  const ev = state?.event
  const gate = state?.gate
  const v = state?.vitals
  const delib = state?.deliberation
  const scene = state?.scene
  const proc = state?.procedure
  const holding = gate?.status === 'hold'
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
  const live = scene?.status === 'live'
  const step = live && scene?.step.display ? scene.step.display : null
  const next = live ? scene?.step.next_display ?? null : null
  const reading = proc?.state === 'done' && proc.doing ? proc : null
  const caution = live ? scene?.caution ?? null : null
  const procConcern = reading?.concern ?? null
  /** Side rail: attending concern, vitals event, or proximity estimate — never a box on the field. */
  const sideIssue = emergency
    ? null
    : procConcern
      ? { kind: 'concern', text: procConcern }
      : ev
        ? { kind: 'alert', text: ev.alert_text ?? 'Vitals event — see detail on screen 2' }
        : caution
          ? { kind: 'caution', text: caution.label }
          : null

  return (
    <div className={`surgical ${holding ? 'holding' : 'clear'} ${emergency ? 'emergency' : ''} ${aiSight ? 'ai-sight' : ''}`}>
      <img
        className="field"
        src={`/video.mjpg?boxes=${aiSight ? 1 : 0}&privacy=${aiSight ? 0 : outline ? 1 : 0}`}
        alt={aiSight ? 'Laparoscopic video with detection boxes' : 'Laparoscopic video (MJPEG stream)'}
      />

      <div className="hud">
        <div className="hud-left">
          <div className="hud-status">
            <span className="status">{status}</span>
            {ev && !emergency && <span className="sub"> · see screen 2</span>}
            {pending && <span className="sub"> · assessing</span>}
          </div>
          {step && (
            <div className="hud-step">
              {step}
              {next && <span className="sub"> · next: {next.toLowerCase()}</span>}
              {scene?.step.stale && <span className="sub"> · detector stale</span>}
            </div>
          )}
        </div>
        <div className="hud-vitals" aria-label="Patient vitals">
          <span><i>HR</i>{n(v?.hr)}</span>
          <span><i>BP</i>{n(v?.sbp)}/{n(v?.dbp)}</span>
          <span><i>MAP</i>{n(v?.map)}</span>
          <span><i>SpO2</i>{n(v?.spo2)}</span>
        </div>
      </div>

      {aiSight && (
        <aside className="hud-cvs" aria-label="Critical View of Safety classifier">
          <div className="hud-side-k">Critical View classifier</div>
          {live && scene?.cvs_available && scene.cvs.length ? (
            <ul>
              {scene.cvs.map((c) => (
                <li key={c.name}>
                  <div className="row">
                    <span className={c.met ? 'met' : 'off'}>{c.met ? 'met' : 'not met'}</span>
                    <span className="mono">
                      {c.p == null ? 'p --' : `p ${c.p.toFixed(2)}`}
                      {c.threshold != null ? ` / ${c.threshold.toFixed(2)}` : ''}
                    </span>
                  </div>
                  <p>{c.text}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p>{live ? 'classifier not reporting' : 'waiting for the vision models'}</p>
          )}
          <small>Classifier read only. Not a clinical call.</small>
        </aside>
      )}

      {sideIssue && (
        <aside className={`hud-side kind-${sideIssue.kind}`} aria-live="polite">
          <div className="hud-side-k">
            {sideIssue.kind === 'concern' ? 'Attending' : sideIssue.kind === 'alert' ? 'Alert' : 'Vision estimate'}
          </div>
          <p>{sideIssue.text}</p>
          {sideIssue.kind === 'caution' && <small>AI proximity estimate · not drawn on the field</small>}
        </aside>
      )}

      {reading && !emergency && (
        <div className="hud-reading" aria-label="Procedure agent reading">
          <span className="who">attending agent · </span>
          {reading.doing}
          {reading.intent && <span className="sub"> {reading.intent}</span>}
        </div>
      )}

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

      <div className="hud-screen-links">
        <button
          type="button"
          className={`hud-swarm-link${aiSight ? ' on' : ''}`}
          onClick={() => setAiSight((on) => {
            if (on) setOutline(true)
            return !on
          })}
        >
          {aiSight ? 'Surgeon sight' : 'AI sight'}
        </button>
        {!aiSight && (
          <button type="button" className="hud-swarm-link" onClick={() => setOutline((v) => !v)}>
            {outline ? 'Original view' : 'Outline view'}
          </button>
        )}
        <Link to="/agents" className="hud-swarm-link">Swarm agents</Link>
      </div>

      <div className="hud-foot">
        {state?.labels.footage ?? 'Recorded footage · Research prototype'}
        {vitalsNote ? ` · ${vitalsNote}` : ''}
        {live ? ' · step from instrument heuristic' : ''}
        {aiSight
          ? ' · AI sight: boxes, names, and the Critical View classifier; the surgeon view shows none of them'
          : outline
            ? ' · outline view is display only; models see the original frames'
            : ''}
        {' · '}
        {state?.labels.attribution ??
          'Video: Cholec80, CAMMA (CC BY-NC-SA 4.0). Vitals: VitalDB case 59, Lee HC et al., Sci Data 2022 (CC BY 4.0).'}
      </div>
    </div>
  )
}
