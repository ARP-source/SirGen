import { useState } from 'react'
import type { AgentMessage, SentinelState } from '../types'
import { CATEGORY_LABEL } from './Agents'
import { fmt } from '../api'

const AGENTS = ['surgical', 'anesthesia', 'record'] as const
const TITLE: Record<(typeof AGENTS)[number], string> = {
  surgical: 'Surgical',
  anesthesia: 'Anesthesia',
  record: 'Record',
}

function bars(confidence: string | undefined) {
  const n = confidence === 'high' ? 3 : confidence === 'medium' ? 2 : confidence === 'low' ? 1 : 0
  return (
    <span className="conf-bars" title={confidence ?? 'no confidence yet'} aria-label={confidence ?? 'none'}>
      {[1, 2, 3].map((i) => <i key={i} className={i <= n ? 'on' : ''} />)}
    </span>
  )
}

function kindOf(id: string): string {
  if (/^vit/.test(id)) return 'vitals'
  if (/^drug/.test(id)) return 'drugs'
  if (/^(surg|phase|tools)/.test(id)) return 'tools'
  if (/^omni/.test(id)) return 'video'
  return 'chart'
}

function kinds(msg: AgentMessage | null): string[] {
  if (!msg) return []
  const out: string[] = []
  for (const e of msg.evidence) {
    const k = kindOf(e.source_id)
    if (!out.includes(k)) out.push(k)
  }
  return out.slice(0, 3)
}

function stanceClass(from: AgentMessage | null, to: AgentMessage | null): string {
  if (!from || !to) return 'unclear'
  const hit = from.responds_to.find((r) => r.agent === to.agent)
  if (!hit) return from.category === to.category ? 'agree' : 'disagree'
  if (hit.stance === 'agree') return 'agree'
  if (hit.stance === 'disagree') return 'disagree'
  return 'unclear'
}

export default function SimpleBoard({ s }: { s: SentinelState }) {
  const [open, setOpen] = useState<(typeof AGENTS)[number] | null>(null)
  const d = s.deliberation
  const round2 = d.round >= 2
  const v = s.vitals

  return (
    <div className="simple">
      <div className="vitals-strip">
        <span className="k">MAP</span><b className="mono">{v.map ?? '--'}</b>
        <span className="k">HR</span><b className="mono">{v.hr ?? '--'}</b>
        <span className="k">SpO2</span><b className="mono">{v.spo2 ?? '--'}</b>
        <span className="k">SBP</span><b className="mono">{v.sbp ?? '--'}</b>
        <span className={`gate-chip ${s.gate.status}`}>{s.gate.chip}</span>
        <span className="muted small">{s.gate.basis}</span>
      </div>

      <div className="simple-board">
        {AGENTS.map((a) => {
          const slot = s.agents[a]
          const msg = slot.message
          return (
            <button key={a} className={`node state-${slot.state} ${open === a ? 'open' : ''}`}
              onClick={() => setOpen(open === a ? null : a)}>
              <span className="who">{TITLE[a]}</span>
              <span className={`cat-word ${msg?.category ?? 'none'}`}>
                {msg ? CATEGORY_LABEL[msg.category] : slot.state === 'thinking' || slot.state === 'waiting' ? 'thinking' : '—'}
              </span>
              {bars(msg?.confidence)}
              <span className="kinds">{kinds(msg).map((k) => <em key={k}>{k}</em>)}</span>
            </button>
          )
        })}

        <svg className="links" viewBox="0 0 100 36" preserveAspectRatio="none" aria-hidden="true">
          <path className={`link ${round2 ? stanceClass(s.agents.surgical.message, s.agents.anesthesia.message) : ''} ${d.round === 2 && d.outcome === 'pending' ? 'run' : ''}`}
            d="M18 28 C 35 8, 65 8, 82 28" />
          <path className={`link ${round2 ? stanceClass(s.agents.anesthesia.message, s.agents.record.message) : ''} ${d.round === 2 && d.outcome === 'pending' ? 'run' : ''}`}
            d="M18 32 C 40 48, 60 48, 82 32" />
          <path className={`link ${round2 ? stanceClass(s.agents.surgical.message, s.agents.record.message) : ''} ${d.round === 2 && d.outcome === 'pending' ? 'run' : ''}`}
            d="M20 30 L 80 30" />
        </svg>

        <div className={`outcome-card ${d.outcome ?? 'idle'}`}>
          {d.outcome === 'converged' && <>Converged: {d.shared_category ? CATEGORY_LABEL[d.shared_category] : d.summary}</>}
          {d.outcome === 'split' && <>Split: {d.sides.map((side) => CATEGORY_LABEL[side.category] ?? side.label).join(' vs ')}</>}
          {d.outcome === 'pending' && <>Deliberating · round {d.round}</>}
          {!d.outcome && <span className="muted">Waiting for an event</span>}
        </div>
      </div>

      {d.team_question && (
        <div className="team-q">{d.team_question}</div>
      )}
      {s.speech.queued.length > 0 && (
        <div className="muted small">Speech held ({s.speech.queued.length}): {s.gate.chip}</div>
      )}
      {s.speech.caption && (
        <div className="speech-line">
          {s.speech.caption}
          {s.speech.held_s != null && s.speech.held_s > 0 && (
            <span className="muted"> · held {s.speech.held_s.toFixed(1)} s, released: {s.speech.release_reason}</span>
          )}
        </div>
      )}

      {open && s.agents[open].message && (
        <div className="node-detail">
          <b>{TITLE[open]}</b>
          <p>{s.agents[open].message!.hypothesis}</p>
          <ul>
            {s.agents[open].message!.evidence.map((e, i) => (
              <li key={i}><span className="sid">{e.source_id}</span> {e.note}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="simple-clock mono">
        t {s.clock_s.toFixed(1)} / {s.scenario.duration_s} s · {s.ended ? 'ended' : s.playing ? 'playing' : 'paused'}
        {s.event ? ` · alert t ${fmt(s.event.alert_shown_at, 1)} s` : ''}
      </div>
    </div>
  )
}
