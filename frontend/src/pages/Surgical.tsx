import { useEffect, useRef, useState } from 'react'
import { useSentinelState } from '../api'

const n = (v: number | null | undefined) => (v == null ? '--' : String(Math.round(v)))

/**
 * Screen 1: the surgeon's HUD. One colour (dim white). It shows the patient's vitals, the step the
 * video models see and the usual next step, the procedure agent's one-line reading of the scene, and,
 * once the speak-or-hold gate has released it, what the assistant says. Everything fades to about 30%
 * while a hold instrument is in use. Two things never fade: an emergency by the gate's thresholds
 * (MAP < 55 or SpO2 < 90) and the caution glow, which marks a cutting or energy instrument detected
 * close to the detected cystic duct or artery (an AI estimate, labelled so).
 */
export default function Surgical() {
  const { state, link } = useSentinelState()
  const spokenId = useRef<string | null>(null)
  const img = useRef<HTMLImageElement>(null)
  const [frameBox, setFrameBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const params = new URLSearchParams(window.location.search)
  const voice = params.get('voice') !== '0' // offline TTS on the box, browser voice as fallback
  const boxes = params.get('boxes') === '1' // debug only; never on by default

  useEffect(() => {
    document.title = 'Sentinel-OR · Surgical view'
  }, [])

  // Where the video frame actually sits inside the letterboxed <img> (object-fit: contain).
  useEffect(() => {
    const measure = () => {
      const el = img.current
      if (!el || !el.naturalWidth || !el.naturalHeight) return
      const scale = Math.min(el.clientWidth / el.naturalWidth, el.clientHeight / el.naturalHeight)
      const w = el.naturalWidth * scale
      const h = el.naturalHeight * scale
      setFrameBox({ x: (el.clientWidth - w) / 2, y: (el.clientHeight - h) / 2, w, h })
    }
    measure()
    const id = window.setInterval(measure, 1000)
    window.addEventListener('resize', measure)
    return () => {
      window.clearInterval(id)
      window.removeEventListener('resize', measure)
    }
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
  const glow =
    caution && frameBox
      ? {
          left: frameBox.x + caution.box[0] * frameBox.w - 14,
          top: frameBox.y + caution.box[1] * frameBox.h - 14,
          width: (caution.box[2] - caution.box[0]) * frameBox.w + 28,
          height: (caution.box[3] - caution.box[1]) * frameBox.h + 28,
        }
      : null

  return (
    <div className={`surgical ${holding ? 'holding' : 'clear'} ${emergency ? 'emergency' : ''}`}>
      <img ref={img} className="field" src={boxes ? '/video.mjpg?boxes=1' : '/video.mjpg?boxes=0'} alt="Laparoscopic video (MJPEG stream)" />

      {glow && caution && (
        <div className={`hud-caution ${caution.kind}`} style={glow} aria-label={caution.label}>
          <span className="caution-label">{caution.label}</span>
        </div>
      )}

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

      <div className="hud-foot">
        {state?.labels.footage ?? 'Recorded footage · Research prototype'}
        {vitalsNote ? ` · ${vitalsNote}` : ''}
        {live ? ' · step from an instrument heuristic · caution glow is an AI estimate' : ''}
        {' · '}
        {state?.labels.attribution ??
          'Video: Cholec80, CAMMA (CC BY-NC-SA 4.0). Vitals: VitalDB case 59, Lee HC et al., Sci Data 2022 (CC BY 4.0).'}
      </div>
    </div>
  )
}
