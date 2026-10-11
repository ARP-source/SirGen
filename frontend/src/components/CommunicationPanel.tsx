import SourceChip from './SourceChip'
import { CAT_LABEL, STANCE_WORD, arr, conversationByRun, kindLabel, str, tLabel, type AuditRow } from '../agentsAudit'

/** Right-side panel: the conversation between agents for this run, read from the audit log. */
export default function CommunicationPanel({ rows, onClose }: { rows: AuditRow[]; onClose: () => void }) {
  const groups = conversationByRun(rows)
  return (
    <aside className="ag-comm" role="dialog" aria-label="Agent communication">
      <header className="ag-comm-head">
        <div>
          <b>Communication</b>
          <p className="ag-comm-legend">
            Everything here is a validated model message or a code decision, read from the tamper-evident audit log. Nothing is scripted.
          </p>
        </div>
        <button type="button" className="small-btn" onClick={onClose}>Close</button>
      </header>
      <div className="ag-comm-body">
        {groups.length === 0 && <div className="muted small">No deliberation or speech events logged yet for this session.</div>}
        {groups.map((g) => (
          <section key={g.run} className="ag-comm-run">
            <h3 className="ag-comm-run-title">Run {g.run}{g.scenario_id ? <span className="muted"> · {g.scenario_id}</span> : null}</h3>
            <ul className="ag-comm-list">
              {g.rows.map((e) => (
                <li key={e.seq} className={`ag-comm-row kind-${e.kind}`}>
                  <CommunicationRow e={e} />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </aside>
  )
}

function CommunicationRow({ e }: { e: AuditRow }) {
  const d = e.data
  const t = tLabel(e.clock_s)
  switch (e.kind) {
    case 'alert':
      return (
        <>
          <span className="ag-comm-meta mono">{t} · alert</span>
          <p>{e.summary}</p>
          {str(d, 'alert_text') && <p className="hyp">{str(d, 'alert_text')}</p>}
        </>
      )
    case 'agent_message': {
      const cat = str(d, 'category') ?? 'message'
      const hyp = str(d, 'hypothesis') ?? e.summary
      const conf = str(d, 'confidence')
      const round = d.round
      const evidence = arr<{ source_id: string; note: string }>(d, 'evidence')
      const stances = arr<{ agent: string; stance: string; note: string }>(d, 'responds_to')
      return (
        <>
          <span className="ag-comm-meta mono">{t} · {str(d, 'agent') ?? e.actor} · round {String(round)}</span>
          <div className="verdict">
            <span className={`cat ${cat}`}>{CAT_LABEL[cat] ?? cat}</span>
            {conf && <span className="conf">confidence: <b>{conf}</b></span>}
          </div>
          <p className="hyp">{hyp}</p>
          {evidence.length > 0 && (
            <ul className="evid">
              {evidence.map((x, i) => (
                <li key={i}><SourceChip id={x.source_id} /> <span>{x.note}</span></li>
              ))}
            </ul>
          )}
          {stances.length > 0 && (
            <ul className="stances">
              {stances.map((r, i) => (
                <li key={i}><span className="to">to {r.agent}</span> <span className={`stance ${r.stance}`}>{STANCE_WORD[r.stance] ?? r.stance}</span> {r.note}</li>
              ))}
            </ul>
          )}
        </>
      )
    }
    case 'agent_unavailable':
      return (
        <>
          <span className="ag-comm-meta mono">{t} · unavailable</span>
          <p className="unavail"><b>{e.summary}</b></p>
          {str(d, 'reason') && <p className="small muted">Reason: {str(d, 'reason')}</p>}
        </>
      )
    case 'deliberation_result':
      return (
        <>
          <span className="ag-comm-meta mono">{t} · deliberation</span>
          <p><span className={`outcome ${str(d, 'outcome') ?? 'pending'}`}>{str(d, 'outcome') ?? 'pending'}</span> {str(d, 'summary')}</p>
          {str(d, 'team_question') && <p><b>Question to the team:</b> {str(d, 'team_question')}</p>}
          {arr<string>(d, 'unavailable_agents').length > 0 && (
            <p className="muted small">Unavailable: {arr<string>(d, 'unavailable_agents').join(', ')}</p>
          )}
        </>
      )
    case 'procedure_reading':
      if (d.unavailable) return null
      return (
        <>
          <span className="ag-comm-meta mono">{t} · procedure agent · reading</span>
          <p className="hyp">{str(d, 'doing') ?? e.summary}</p>
          {str(d, 'intent') && <p className="small">Intent: {str(d, 'intent')}</p>}
          {str(d, 'next') && <p className="small">Next: {str(d, 'next')}</p>}
          {str(d, 'concern') && <p className="small chain-bad">Concern: {str(d, 'concern')}</p>}
        </>
      )
    case 'safety_verdict': {
      const unv = d.unvetted === true
      const verdict = str(d, 'verdict') ?? 'unknown'
      const word = unv ? 'passed unvetted (checker unavailable)' : verdict === 'hold' ? 'held' : verdict === 'speak' ? 'cleared to speak' : verdict
      return (
        <>
          <span className="ag-comm-meta mono">{t} · safety officer · {word}</span>
          <p className="hyp">{str(d, 'proposal') ?? e.summary}</p>
          {str(d, 'reason') && !unv && <p className="small">{str(d, 'reason')}</p>}
          {unv && str(d, 'reason') && <p className="small muted">Checker: {str(d, 'reason')}</p>}
        </>
      )
    }
    case 'speech_held':
    case 'speech_released':
    case 'speech_spoken':
    case 'speech_urgent':
      return (
        <>
          <span className="ag-comm-meta mono">{t} · gate · {kindLabel(e.kind)}</span>
          <p>{str(d, 'text') ?? e.summary}</p>
          {str(d, 'instrument') && <p className="small muted">Instrument: {str(d, 'instrument')}</p>}
          {str(d, 'release_reason') && <p className="small muted">Released: {str(d, 'release_reason')}</p>}
        </>
      )
    default:
      return (
        <>
          <span className="ag-comm-meta mono">{t} · {kindLabel(e.kind)}</span>
          <p>{e.summary}</p>
        </>
      )
  }
}
