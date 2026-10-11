import type { AgentMessage, AgentSlot, SentinelState } from '../types'
import SourceChip from './SourceChip'

const AGENTS = ['surgical', 'anesthesia', 'record'] as const
const AGENT_TITLE: Record<(typeof AGENTS)[number], string> = {
  surgical: 'Surgical agent',
  anesthesia: 'Anesthesia agent',
  record: 'Record agent',
}
const AGENT_SEES: Record<(typeof AGENTS)[number], string> = {
  surgical: 'Sees: phase, tool window, surgical events',
  anesthesia: 'Sees: vitals trend, cuff readings, drug log',
  record: 'Sees: patient chart',
}
export const CATEGORY_LABEL: Record<string, string> = {
  bleeding_concern: 'bleeding concern',
  drug_effect: 'drug effect',
  artifact: 'artifact',
  unclear: 'unclear',
  no_concern: 'no concern',
}
// Surgical-agent ids have no other panel; their rows in the surgical column are the link target.
const SURGICAL_ONLY = /^(phase|tools|surg-\d+|oop-\d+|omni-.+)$/

function Message({ msg }: { msg: AgentMessage }) {
  return (
    <>
      <div className="verdict">
        <span className={`cat ${msg.category}`}>{CATEGORY_LABEL[msg.category]}</span>
        <span className="conf">confidence: <b>{msg.confidence}</b></span>
        <span className="rnd">round {msg.round}</span>
      </div>
      <p className="hyp">{msg.hypothesis}</p>
      <ul className="evid">
        {msg.evidence.map((e, i) => (
          <li key={i}>
            <SourceChip id={e.source_id} /> <span>{e.note}</span>
          </li>
        ))}
      </ul>
      {msg.responds_to.length > 0 && (
        <ul className="stances">
          {msg.responds_to.map((r, i) => (
            <li key={i}>
              <span className="to">to {r.agent}</span> <span className={`stance ${r.stance}`}>{r.stance}</span> {r.note}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function Column({ agent, slot, unavailableText }: { agent: (typeof AGENTS)[number]; slot: AgentSlot; unavailableText: string }) {
  const msg = slot.message
  const changed = slot.round1 && msg && msg.round === 2 && slot.round1.category !== msg.category
  return (
    <div className={`agent state-${slot.state}`}>
      <h3>
        {AGENT_TITLE[agent]}
        <span className={`pill ${slot.state}`}>{slot.status}</span>
      </h3>
      <div className="sees-title">{AGENT_SEES[agent]}</div>
      {slot.sees.length === 0 ? (
        <div className="muted small">Nothing yet. The agent reads its evidence when an event fires.</div>
      ) : (
        <ul className="sees">
          {slot.sees.map((s, i) => (
            <li key={i} id={s.source_id && agent === 'surgical' && SURGICAL_ONLY.test(s.source_id) ? `src-${s.source_id}` : undefined}>
              {s.source_id && <SourceChip id={s.source_id} />} <span>{s.text}</span>
            </li>
          ))}
        </ul>
      )}
      {msg && <Message msg={msg} />}
      {changed && slot.round1 && (
        <div className="small muted">Changed from round 1: {CATEGORY_LABEL[slot.round1.category]} ({slot.round1.confidence}).</div>
      )}
      {slot.state === 'unavailable' && (
        <div className="unavail">
          <b>{unavailableText}</b>
          <div className="small">Reason: {slot.reason ?? 'not recorded'}</div>
        </div>
      )}
      {slot.latency_ms !== null && msg && (
        <div className="small muted">last call {(slot.latency_ms / 1000).toFixed(2)} s{slot.attempts > 1 ? `, after ${slot.attempts - 1} retry` : ''}</div>
      )}
    </div>
  )
}

export function AgentColumns({ s }: { s: SentinelState }) {
  return (
    <section className="panel agents-panel">
      <h2>Agents <span className="sub">each sees only its own evidence · round 1, then round 2</span></h2>
      <div className="agents">
        {AGENTS.map((a) => (
          <Column key={a} agent={a} slot={s.agents[a]} unavailableText={s.labels.context_unavailable} />
        ))}
      </div>
    </section>
  )
}

export function ContextStatus({ s }: { s: SentinelState }) {
  const ev = s.event
  if (!ev) {
    return <div className="ctx idle">Context: no event yet. Agents run when an event fires; the alert never waits for them.</div>
  }
  return (
    <div className={`ctx ${ev.context_status}`}>
      <span className="label">Context: {ev.context_status}</span>
      {ev.context_note && <span className="note"> · {ev.context_note}</span>}
      {ev.context_status === 'unavailable' && <b className="unavail-text"> {s.labels.context_unavailable}</b>}
    </div>
  )
}

export function DeliberationPanel({ s }: { s: SentinelState }) {
  const d = s.deliberation
  const reported = AGENTS.filter((a) => s.agents[a].message?.round === d.round).length
  const down = AGENTS.filter((a) => s.agents[a].state === 'unavailable')
  return (
    <section className="panel delib">
      <h2>
        Deliberation
        <span className="sub">round {d.round || '-'} of 2</span>
        {d.outcome && <span className={`outcome ${d.outcome}`}>{d.outcome}</span>}
      </h2>
      <ContextStatus s={s} />
      {d.outcome === 'pending' && (
        <div className="status">
          {d.status === 'waiting'
            ? 'Waiting for the arterial spike to recover and a cuff reading.'
            : `Round ${d.round}: ${reported} of ${AGENTS.length - down.length} agents have reported.`}
        </div>
      )}
      {d.status === 'idle: waits for an event' && !s.event && <div className="muted">Waiting for an event.</div>}
      {d.status === 'unavailable' && (
        <div className="unavail"><b>{s.labels.context_unavailable}</b></div>
      )}
      {d.outcome === 'converged' && (
        <div className="shared">
          <div className="k">Shared view</div>
          <div className="big-text">{d.summary}</div>
        </div>
      )}
      {d.team_question && (
        <div className="question">
          <div className="k">Question to the team</div>
          <div className="big-text">{d.team_question}</div>
          <div className="small">Sources: {d.question_source_ids.map((id) => <SourceChip key={id} id={id} />)}</div>
        </div>
      )}
      {d.outcome === 'split' && (
        <>
          <div className="sides">
            {d.sides.map((side) => (
              <div className="side" key={side.category}>
                <div className="k">Explanation: <b>{side.label}</b></div>
                <div className="small">{side.agents.map((a) => AGENT_TITLE[a]).join(', ')}</div>
                {side.agents.map((a) => (
                  <p className="hyp" key={a}><span className="who">{a}:</span> {side.hypotheses[a]}</p>
                ))}
                <ul className="evid">
                  {side.source_ids.map((id, i) => (
                    <li key={id}><SourceChip id={id} /> <span>{side.phrases[i]}</span></li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          {d.summary && <div className="small">{d.summary}</div>}
        </>
      )}
      {d.unavailable_agents.length > 0 && d.outcome && d.outcome !== 'pending' && (
        <div className="small muted">Unavailable agents count as unclear: {d.unavailable_agents.join(', ')}.</div>
      )}
      <div className="src">Outcome, shared view and team question are decided by code from the validated round-2 messages, never by a model. No scripted text stands in for a failed agent.</div>
    </section>
  )
}
