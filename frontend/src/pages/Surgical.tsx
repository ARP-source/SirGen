import { useEffect } from 'react'
import { fmt, useSentinelState } from '../api'

/** Screen 1: full-screen MJPEG, thin step label, restrained alert banner, labels. No animation. */
export default function Surgical() {
  const { state, link } = useSentinelState()
  useEffect(() => {
    document.title = 'Sentinel-OR · Surgical view'
  }, [])

  const phase = state?.phase
  const ev = state?.event
  const showBanner = ev && ev.alert_level === 'banner'

  return (
    <div className="surgical">
      <img className="field" src="/video.mjpg" alt="Recorded laparoscopic footage (MJPEG stream)" />
      <div className="step">
        Step:{' '}
        {phase?.display ? (
          <>
            <b>{phase.display}</b>
            {phase.set_by === 'presenter' ? ' · set by presenter' : ''}
          </>
        ) : (
          <span>not detected ({phase?.status ?? 'detector not running (M2)'})</span>
        )}
      </div>
      {showBanner && (
        <div className="alert" role="alert">
          {ev.alert_text}
          <small>
            t {fmt(ev.alert_shown_at, 1)} s · {ev.context_status === 'available' ? 'context available on screen 2' : ev.context_status === 'pending' ? 'context pending on screen 2' : state?.labels.context_unavailable}
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
          {state?.labels.attribution ??
            'Video: Cholec80, CAMMA (CC BY-NC-SA 4.0). Vitals: VitalDB case 59, Lee HC et al., Sci Data 2022 (CC BY 4.0).'}
        </span>
      </div>
    </div>
  )
}
