import { useEffect, useState } from 'react'
import { fmt, getJson, postControl, useSentinelState } from '../api'
import type { ScenarioSummary } from '../types'
import AppNav from '../components/AppNav'

const PHASES = [
  'Preparation', 'CalotTriangleDissection', 'ClippingCutting', 'GallbladderDissection',
  'GallbladderPackaging', 'CleaningCoagulation', 'GallbladderRetraction',
]

export default function Control() {
  const { state: s, link } = useSentinelState()
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([])
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    document.title = 'Sentinel-OR · Presenter control'
    getJson<ScenarioSummary[]>('/scenarios').then(setScenarios).catch((e: Error) => setErr(e.message))
  }, [])

  const run = (path: string) => () => {
    setErr(null)
    postControl(path).catch((e: Error) => setErr(e.message))
  }

  return (
    <div className="control">
      <AppNav />
      <h1>Presenter control</h1>
      <div className="secondary" style={{ fontSize: 20 }}>
        {s ? (
          <>
            <b>{s.scenario.title}</b> · t {s.clock_s.toFixed(1)} / {s.scenario.duration_s} s ·{' '}
            {s.ended ? 'ended' : s.playing ? 'playing' : 'paused'} · event at t {fmt(s.scenario.event_t_s)} s
            {s.event ? ` · ${s.event.event_id} fired at t ${fmt(s.event.alert_shown_at, 1)} s` : ''}
          </>
        ) : (
          `backend link ${link}`
        )}
      </div>
      {err && <div className="err">{err}</div>}

      <div className="buttons">
        <button className="big primary" onClick={run('play')}>Play</button>
        <button className="big" onClick={run('pause')}>Pause</button>
        <button className="big" onClick={run('restart')}>Restart</button>
        <button className="big" onClick={run('reset')}>Reset</button>
        <button className="big trigger" onClick={run('trigger')}>Trigger alert</button>
        <button className="big primary" onClick={run('surgeon-asks')}>Surgeon asks</button>
      </div>
      {s?.gate && (
        <div className="secondary" style={{ marginTop: 8 }}>
          <span className={`gate-chip ${s.gate.status}`}>{s.gate.chip}</span>
          {' '}{s.gate.basis}
          {s.gate.tools.length > 0 ? ` · now: ${s.gate.tools.join(', ')}` : ' · no instrument'}
          {s.speech.queued.length > 0 ? ` · ${s.speech.queued.length} held` : ''}
        </div>
      )}

      <h2 style={{ fontSize: 18 }} className="muted">Scenario</h2>
      <div className="scenarios">
        {scenarios.map((sc) => (
          <button key={sc.id} className={`scenario-card ${s?.scenario_id === sc.id ? 'active' : ''}`}
            onClick={run(`scenario/${sc.id}`)}>
            <b>{sc.id}</b> · {sc.title}
            <small>
              {sc.duration_s} s · {sc.event_type} at t {fmt(sc.event_t_s)} s · alert: {sc.alert_level} · vitals: {sc.vitals_source}
            </small>
          </button>
        ))}
      </div>

      <h2 style={{ fontSize: 18, marginTop: 20 }} className="muted">Step label override</h2>
      <select value={s?.phase.set_by === 'presenter' ? s.phase.name ?? 'auto' : 'auto'}
        onChange={(e) => run(`phase/${e.target.value}`)()}>
        <option value="auto">auto (detector, M2)</option>
        {PHASES.map((p) => <option key={p} value={p}>{p}</option>)}
      </select>
    </div>
  )
}
