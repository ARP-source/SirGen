import { CONVERSATION_KINDS, str, tLabel, type AuditRow } from '../agentsAudit'

const SHOW = CONVERSATION_KINDS

function line(e: AuditRow): { who: string; text: string } {
  const d = e.data
  switch (e.kind) {
    case 'agent_message':
      return { who: str(d, 'agent') ?? e.actor.replace('agent:', ''), text: str(d, 'hypothesis') ?? e.summary }
    case 'procedure_reading':
      return { who: 'attending', text: str(d, 'doing') ?? e.summary }
    case 'safety_verdict':
      return { who: 'safety', text: str(d, 'proposal') ?? e.summary }
    case 'deliberation_result':
      return { who: 'team', text: str(d, 'summary') ?? e.summary }
    case 'alert':
      return { who: 'system', text: e.summary }
    case 'speech_spoken':
    case 'speech_released':
      return { who: 'gate', text: str(d, 'text') ?? e.summary }
    default:
      return { who: e.actor, text: e.summary }
  }
}

/** Always-visible tail of the swarm conversation from the audit log (judges see traffic without opening the drawer). */
export default function SwarmFeed({ rows, limit = 8 }: { rows: AuditRow[]; limit?: number }) {
  const currentRun = rows.length ? rows[rows.length - 1].run : null
  const tail = rows
    .filter((e) => e.run === currentRun && SHOW.has(e.kind))
    .slice(-limit)

  if (tail.length === 0) {
    return <div className="ag-swarm-feed empty muted small">Swarm log empty for this run — press Play on /control.</div>
  }

  return (
    <section className="ag-swarm-feed" aria-label="Recent agent communication">
      <div className="ag-swarm-feed-head">
        <span className="delib-k">Swarm log</span>
        <span className="muted small">run {currentRun} · from audit · newest last</span>
      </div>
      <ul className="ag-swarm-feed-list">
        {tail.map((e) => {
          const { who, text } = line(e)
          return (
            <li key={e.seq} className={`ag-swarm-feed-row kind-${e.kind}`}>
              <span className="mono ag-swarm-feed-meta">{tLabel(e.clock_s)} · {who}</span>
              <span className="ag-swarm-feed-text">{text}</span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
