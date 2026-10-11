import { useState } from 'react'
import type { AgentCardConfig } from '../agentsRoster'
import { entriesForAgent, kindLabel, tLabel, type AuditRow } from '../agentsAudit'

const PAGE = 60

/** Everything this agent did, newest first, straight from the tamper-evident audit log. */
export default function ActionsTimeline({ cfg, rows, error, loaded }: { cfg: AgentCardConfig; rows: AuditRow[]; error: string | null; loaded: boolean }) {
  const [limit, setLimit] = useState(PAGE)
  const mine = entriesForAgent(cfg, rows)
  const shown = mine.slice(0, limit)
  return (
    <div className="ag-actions">
      <div className="sees-title">
        Actions · {mine.length} logged
        <span className="muted"> · from the tamper-evident audit log, newest first, refreshed every 5 s</span>
      </div>
      {error && <div className="unavail"><b>Audit log unreachable</b><div className="small">{error}</div></div>}
      {!error && loaded && mine.length === 0 && <div className="muted small">No actions logged for this agent yet.</div>}
      {!error && !loaded && <div className="muted small">Reading the audit log…</div>}
      {shown.length > 0 && (
        <ul className="ag-tl">
          {shown.map((e) => (
            <li key={e.seq} className={`ag-tl-row kind-${e.kind}`}>
              <span className="ag-tl-t mono">{tLabel(e.clock_s)}</span>
              <span className="ag-tl-kind">{kindLabel(e.kind)}</span>
              <details className="ag-tl-body">
                <summary>
                  <span className="ag-tl-sum">{e.summary}</span>
                  <span className="ag-tl-meta mono">{e.id} · run {e.run}</span>
                </summary>
                <pre className="ag-raw mono">{JSON.stringify(e.data, null, 2)}</pre>
              </details>
            </li>
          ))}
        </ul>
      )}
      {mine.length > shown.length && (
        <button className="small-btn" onClick={() => setLimit((n) => n + PAGE)}>Show {Math.min(PAGE, mine.length - shown.length)} older</button>
      )}
    </div>
  )
}
