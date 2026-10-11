import { useEffect, useRef } from 'react'
import { fmt, useSentinelState } from '../api'

const CVS_SHORT: Record<string, string> = {
  c1_two_structures: 'two structures',
  c2_hepatocystic_triangle: 'triangle cleared',
  c3_cystic_plate: 'cystic plate',
}

/**
 * Screen 1: the field, nearly bare. A faint step line and safety-view dots; both fade almost out while a
 * hold instrument is in use. Only the vitals banner and the clip-before-safety-view check are ever loud.
 */
export default function Surgical() {
  const { state, link } = useSentinelState()
  const spokenId = useRef<string | null>(null)
  const params = new URLSearchParams(window.location.search)
  const voice = params.get('voice') === '1'
  const boxes = params.get('boxes') !== '0'

  useEffect(() => {
    document.title = 'Sentinel-OR · Surgical view'
  }, [])

  useEffect(() => {
    if (!voice || !state?.speech.current) return
    const cur = state.speech.current
    if (cur.id === spokenId.current || !cur.text) return
    spokenId.current = cur.id
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(new SpeechSynthesisUtterance(cur.text))
  }, [voice, state?.speech.current?.id, state?.speech.current?.text])

  const ev = state?.event
  const showBanner = ev && ev.alert_level === 'banner'
  const gate = state?.gate
  const speech = state?.speech
  const scene = state?.scene
  const step = scene?.step
  const holding = gate?.status === 'hold'
  const live = scene?.status === 'live'
  const lastDeviation = scene?.deviations.length ? scene.deviations[scene.deviations.length - 1] : null
  const deviationFresh = lastDeviation != null && state != null && state.clock_s - lastDeviation.t_s < 20
  const metCount = scene?.cvs.filter((c) => c.met).length ?? 0
  const urgentCaption = speech?.current?.urgent ?? false

  return (
    <div className={`surgical ${holding ? 'holding' : 'clear'}`}>
      <img className="field" src={boxes ? '/video.mjpg?boxes=1' : '/video.mjpg'} alt="Laparoscopic video (MJPEG stream)" />

      <div className="hud">
        <div className="hud-step">
          {step?.display ? (
            <>
              <b>{step.display}</b>
              {step.next_display && <span className="next"> · next {step.next_display.toLowerCase()}</span>}
              {state?.phase.set_by === 'presenter' && <span className="next"> · set by presenter</span>}
              {step.stale && <span className="next"> · stale</span>}
            </>
          ) : (
            <span className="next">{live ? 'reading the field' : `video models ${scene?.status ?? 'not running'}`}</span>
          )}
          {gate && <span className={`gate-chip ${gate.status}`}>{gate.chip}</span>}
        </div>
        {scene?.cvs_available && (
          <div className={`hud-cvs ${scene.cvs_achieved ? 'achieved' : ''}`} title="Critical View of Safety, from the video classifier">
            <span className="lbl">safety view {scene.cvs_achieved ? 'seen' : `${metCount}/3`}</span>
            {scene.cvs.map((c) => (
              <span key={c.name} className={`dot ${c.met ? 'met' : ''}`} title={`${c.text}${c.p != null ? ` (score ${c.p.toFixed(2)})` : ''}`}>
                <i />
                {CVS_SHORT[c.name] ?? c.name}
              </span>
            ))}
          </div>
        )}
      </div>

      {showBanner && (
        <div className="alert" role="alert">
          {ev.alert_text}
          <small>
            t {fmt(ev.alert_shown_at, 1)} s · {ev.context_status === 'available' ? 'context available on screen 2' : ev.context_status === 'pending' ? 'context pending on screen 2' : state?.labels.context_unavailable}
            {' · banner never held'}
          </small>
        </div>
      )}
      {deviationFresh && lastDeviation && (
        <div className="alert deviation" role="alert">
          {lastDeviation.text}
          <small>video-model observation, not a diagnosis · t {fmt(lastDeviation.t_s, 0)} s · {lastDeviation.source_id}</small>
        </div>
      )}
      {speech?.caption && !(deviationFresh && speech.current?.kind === 'safety_check') && (
        <div className={`speech-caption ${urgentCaption ? 'urgent' : ''} ${holding && !urgentCaption ? 'dim' : ''}`}>
          {speech.caption}
          <small>
            {speech.held_s != null && speech.held_s > 0
              ? `held ${speech.held_s.toFixed(1)} s, released: ${speech.release_reason ?? 'cleared'}`
              : speech.release_reason ?? 'spoken'}
          </small>
        </div>
      )}
      {state && (state.video.decode_error || state.video.black_frames) && (
        <div className="link">
          Video problem: {state.video.decode_error || 'the frames are black (mean brightness ' + String(state.video.mean_brightness) + ')'}
        </div>
      )}
      {link !== 'live' && <div className="link">state link {link}</div>}
      <div className="footer">
        <span className="label">{state?.labels.footage ?? 'Recorded footage · Research prototype'}</span>
        <span>
          {live && scene?.detector_ms != null ? `models ${scene.detector_ms.toFixed(0)} ms/frame · ` : ''}
          {state?.labels.attribution ??
            'Video: Cholec80, CAMMA (CC BY-NC-SA 4.0). Vitals: VitalDB case 59, Lee HC et al., Sci Data 2022 (CC BY 4.0).'}
          {' Models trained on Cholec80-Boxes and Endoscapes2023 (CC BY-NC-SA 4.0).'}
        </span>
      </div>
    </div>
  )
}
