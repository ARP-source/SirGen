import { collectLiveActivity, type LiveActivity } from '../liveAgents'
import type { SentinelState } from '../types'

function Row({ item, clock }: { item: LiveActivity; clock: number }) {
  return (
    <div className={`ag-live-chip state-${item.state}`}>
      <span className="ag-live-dot" />
      <b>{item.title}</b>
      <span className="ag-live-word">{item.state === 'waiting' ? 'WAITING' : 'THINKING'}</span>
      <span className="ag-live-status">{item.status}</span>
      {item.seesPreview && <span className="ag-live-sees">{item.seesPreview}</span>}
    </div>
  )
}

/** Always visible on /agents while someone is on a model call or the gate is holding speech. */
export default function LiveActivityStrip({ s }: { s: SentinelState }) {
  const items = collectLiveActivity(s)
  if (items.length === 0) return null
  return (
    <section className="ag-live-strip" aria-live="polite">
      <span className="delib-k">Live now</span>
      <span className="mono muted">t {s.clock_s.toFixed(1)} s</span>
      <div className="ag-live-chips">
        {items.map((item) => (
          <Row key={item.id} item={item} clock={s.clock_s} />
        ))}
      </div>
    </section>
  )
}

export function LiveActivityPanel({ s }: { s: SentinelState }) {
  const items = collectLiveActivity(s)
  if (items.length === 0) {
    return <div className="muted small ag-comm-live-empty">No agent is on a model call right now.</div>
  }
  return (
    <section className="ag-comm-live">
      <h3 className="ag-comm-run-title">Live now <span className="mono muted">· t {s.clock_s.toFixed(1)} s</span></h3>
      <ul className="ag-comm-list">
        {items.map((item) => (
          <li key={item.id} className="ag-comm-row kind-live-thinking">
            <span className="ag-comm-meta">
              <span className="ag-live-pulse">{item.state === 'waiting' ? 'waiting' : 'thinking'}</span>
              {' · '}{item.title}
            </span>
            <p className="hyp">{item.status}</p>
            {item.seesPreview && <p className="small muted">{item.seesPreview}</p>}
          </li>
        ))}
      </ul>
    </section>
  )
}
