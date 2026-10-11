import { useEffect, useState } from 'react'
import { fmt, fmtT, useSentinelState } from '../api'
import type { SentinelState } from '../types'
import VitalsPanel from '../components/VitalsPanel'
import PatientChart from '../components/PatientChart'
import { ProvenanceBadge } from '../components/Badges'
import AuditPanel from '../components/AuditPanel'
import { AgentColumns, DeliberationPanel } from '../components/Agents'
import SimpleBoard from '../components/SimpleBoard'

const NM = <span className="nm">not measured</span>

function DrugLog({ s }: { s: SentinelState }) {
  return (
    <section className="panel">
      <h2>Anaesthesia drug log</h2>
      {s.drug_log.length === 0 ? (
        <div className="muted">No entries yet.</div>
      ) : (
        <table className="list">
          <tbody>
            {s.drug_log.map((d) => (
              <tr key={d.source_id} id={`src-${d.source_id}`}>
                <td><span className="sid">{d.source_id}</span></td>
                <td className="mono" style={{ whiteSpace: 'nowrap' }}>{d.t_s === null ? 'running' : `t ${d.t_s} s`}</td>
                <td><b>{d.drug}</b> · {d.detail}</td>
                <td><ProvenanceBadge provenance={d.provenance} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

function Baseline({ s }: { s: SentinelState }) {
  const b = s.baseline
  return (
    <section className="panel">
      <h2>Baseline rule (comparison, no context)</h2>
      <div>{b.rule_text || b.rule}</div>
      <div style={{ marginTop: 8, fontSize: 20 }} className="status-line">
        <span className={`dot ${b.alarm ? 'on' : 'off'}`} />
        {b.alarm ? <b>Fired: {b.fired_rule} at t {b.fired_at_t_s} s</b> : <span>Not fired</span>}
      </div>
      <div className="src">
        Scenario file expects {b.expected_fires ?? '--'} at t {fmt(b.expected_fires_at_t_s)} s
        {b.expected_correct === false ? ' (a needless alarm)' : ''}. Demo stand-in thresholds, not clinical recommendations.
      </div>
    </section>
  )
}

function Timeline({ s }: { s: SentinelState }) {
  const ev = s.event
  return (
    <section className="panel">
      <h2>Event timeline</h2>
      {ev && (
        <div style={{ marginBottom: 8 }}>
          <b>{ev.event_id}</b> {ev.event_type} · {ev.trigger === 'presenter' ? 'triggered by presenter' : 'scenario event'} ·{' '}
          {ev.simulated ? <span className="badge simulated">simulated telemetry</span> : <span className="badge time-shifted">real, time-shifted</span>}{' '}
          {ev.alert_level === 'quiet' && <span className="badge">quiet flag, no banner</span>}
          <div className="src">
            Context: {ev.context_status}
            {ev.context_note ? ` (${ev.context_note})` : ''}
            {ev.context_status === 'unavailable' ? `. ${s.labels.context_unavailable}` : ''}
          </div>
        </div>
      )}
      {s.timeline.length === 0 ? (
        <div className="muted">Nothing yet. Event at t {fmt(s.scenario.event_t_s)} s in this scenario.</div>
      ) : (
        <ul className="timeline">
          {s.timeline.map((i, n) => (
            <li key={n}>
              <span className="mono">{fmtT(i.t_s)}</span>
              <span className={`kind ${i.kind}`}>{i.kind}</span>
              <span>{i.label}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Perf({ s }: { s: SentinelState }) {
  const m = s.metrics
  const row = (label: string, value: number | null, unit: string, digits = 1, note?: string) => (
    <>
      <span title={note}>{label}</span>
      <span className="val mono">{value === null ? NM : `${value.toFixed(digits)} ${unit}`}</span>
    </>
  )
  return (
    <section className="panel">
      <h2>Performance (measured)</h2>
      <div className="perf">
        {row('Render FPS', m.render_fps, 'fps', 1, m.notes.render_fps)}
        {row('Event to alert', m.event_to_alert_ms, 'ms', 2, m.notes.event_to_alert_ms)}
        <span title={m.notes.bytes_out}>Bytes out (non-loopback)</span>
        <span className="val mono">{m.bytes_out === null ? NM : m.bytes_out.toLocaleString()}</span>
        {row('Detector FPS', m.detector_fps, 'fps')}
        {row('Detector latency', m.detector_ms, 'ms')}
        {row('Deliberation (round 1 to outcome)', m.deliberation_s, 's', 2, m.notes.deliberation_s)}
        {row('Agents, all three at once', m.llm_tps_aggregate, 'tok/s', 0, m.notes.llm_tps_aggregate)}
        {row('Agents, per call (median)', m.llm_tps_per_agent, 'tok/s', 0, m.notes.llm_tps_per_agent)}
        {row('Memory in use (whole box)', m.gb_used, 'GB', 1, m.notes.gb_used)}
        <span>Models resident</span>
        <span className="val">{m.models_resident === null ? NM : m.models_resident.join(', ')}</span>
      </div>
      <div className="src">
        Video: {s.video.test_pattern ? 'test pattern (no video loaded)' : s.video.source} · frame {s.video.frame_id} · dropped{' '}
        {s.video.dropped_frames} · detector {s.video.detector}
      </div>
    </section>
  )
}

export default function Dashboard() {
  const { state: s, link } = useSentinelState()
  const [details, setDetails] = useState(false)
  useEffect(() => {
    document.title = 'Sentinel-OR · How it thinks'
  }, [])
  if (!s) return <div className="home">Connecting to backend ({link})...</div>

  return (
    <div className="dash">
      <header>
        <h1>Sentinel-OR · how it thinks</h1>
        <span className="scenario">
          <b>{s.scenario.title}</b>
        </span>
        <span className="secondary">{s.scenario.on_screen_label}</span>
        <button className="small-btn" onClick={() => setDetails((d) => !d)}>
          {details ? 'Simple view' : 'Details'}
        </button>
        <span className="clock mono">
          t {s.clock_s.toFixed(1)} / {s.scenario.duration_s} s · {s.ended ? 'ended' : s.playing ? 'playing' : 'paused'}
          {link !== 'live' && <span className="err"> · link {link}</span>}
        </span>
      </header>
      {!details && <SimpleBoard s={s} />}
      {details && <div className="grid">
        <div className="col">
          <VitalsPanel state={s} />
          <DrugLog s={s} />
          <Timeline s={s} />
        </div>
        <div className="col center">
          <AgentColumns s={s} />
          <div className="delib-row">
            <DeliberationPanel s={s} />
            <Baseline s={s} />
          </div>
        </div>
        <div className="col">
          <PatientChart />
          <AuditPanel s={s} />
          <Perf s={s} />
        </div>
      </div>}
      <footer>
        <span>Research prototype, not for clinical use. {s.labels.composite}</span>
        <span>{s.labels.attribution}</span>
      </footer>
    </div>
  )
}
