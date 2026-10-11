import { useEffect, useRef, useState } from 'react'
import { fmt, useSentinelState } from '../api'
import type { AgentMessage, SentinelState } from '../types'
import { AGENT_ROSTER, type AgentCardConfig } from '../agentsRoster'
import { CAT_LABEL, useAuditEntries } from '../agentsAudit'
import AgentCard, { deriveStatus, sampleFor, slotFor, visionLive } from '../components/AgentCard'
import ActionsTimeline from '../components/ActionsTimeline'
import CommunicationPanel from '../components/CommunicationPanel'
import PlaneLayer, { usePlaneLayer } from '../components/PlaneLayer'
import { CATEGORY_LABEL } from '../components/Agents'
import SourceChip from '../components/SourceChip'

const HISTORY = 60
const DELIB_CARD_IDS = ['surgical', 'anesthesia', 'record'] as const
const AGENT_TO_CARD: Record<string, string> = { surgical: 'surgical', anesthesia: 'anesthesia', record: 'record', procedure: 'procedure', safety: 'safety' }

type History = Record<string, number[]>

function catLabel(cat: string): string {
  return CAT_LABEL[cat] ?? CATEGORY_LABEL[cat] ?? cat
}

/** Ring buffer of the last HISTORY distinct samples per card, filled only while the page is open. Never seeded. */
function pushSamples(prev: History, s: SentinelState): History {
  let next: History | null = null
  for (const cfg of AGENT_ROSTER) {
    const v = sampleFor(cfg, s)
    if (v === null || !Number.isFinite(v)) continue
    const buf = prev[cfg.id] ?? []
    if (buf.length && buf[buf.length - 1] === v) continue
    if (!next) next = { ...prev }
    next[cfg.id] = [...buf, v].slice(-HISTORY)
  }
  return next ?? prev
}

function Message({ msg }: { msg: AgentMessage }) {
  return (
    <>
      <div className="verdict">
        <span className={`cat ${msg.category}`}>{catLabel(msg.category)}</span>
        <span className="conf">confidence: <b>{msg.confidence}</b></span>
        <span className="rnd">round {msg.round}</span>
      </div>
      <p className="hyp">{msg.hypothesis}</p>
      <ul className="evid">
        {msg.evidence.map((e, i) => (
          <li key={i}><SourceChip id={e.source_id} /> <span>{e.note}</span></li>
        ))}
      </ul>
      {msg.responds_to.length > 0 && (
        <ul className="stances">
          {msg.responds_to.map((r, i) => (
            <li key={i}><span className="to">to {r.agent}</span> <span className={`stance ${r.stance}`}>{r.stance}</span> {r.note}</li>
          ))}
        </ul>
      )}
    </>
  )
}

function LlmDetail({ cfg, s }: { cfg: AgentCardConfig; s: SentinelState }) {
  const slot = slotFor(s, cfg.stateKey)
  if (!slot) {
    return (
      <div className="agent state-idle">
        <h3>{cfg.title} <span className="pill idle">standby</span></h3>
        <div className="muted small">Not yet wired: state.agents has no “{cfg.stateKey}” slot from the backend. Nothing is shown in its place.</div>
      </div>
    )
  }
  const msg = slot.message as (AgentMessage & { intent?: string; next?: string; concern?: string; verdict?: string; proposal?: string; origin?: string }) | null
  const changed = slot.round1 && msg && msg.round === 2 && slot.round1.category !== msg.category
  return (
    <div className={`agent state-${slot.state}`}>
      <h3>{cfg.title} <span className={`pill ${slot.state}`}>{slot.status}</span></h3>
      <div className="sees-title">Sees ({slot.sees.length}) · allowed sources: {slot.allowed_source_ids.length}</div>
      {slot.sees.length === 0 ? (
        <div className="muted small">Nothing yet. The agent reads its evidence when an event fires.</div>
      ) : (
        <ul className="sees ag-sees">
          {slot.sees.map((x, i) => (
            <li key={i}>{x.source_id && <SourceChip id={x.source_id} />} <span>{x.text}</span></li>
          ))}
        </ul>
      )}
      {msg ? (
        <>
          <Message msg={msg} />
          {msg.intent && <p className="small">Intent: {msg.intent}</p>}
          {msg.next && <p className="small">Next: {msg.next}</p>}
          {msg.concern && <p className="small chain-bad">Concern: {msg.concern}</p>}
          {msg.proposal && cfg.id === 'safety' && <p className="small muted">Checked: {msg.proposal}</p>}
        </>
      ) : (
        <div className="muted small">No message yet.</div>
      )}
      {changed && slot.round1 && (
        <div className="small muted">Changed from round 1: {catLabel(slot.round1.category)} ({slot.round1.confidence}).</div>
      )}
      {slot.state === 'unavailable' && (
        <div className="unavail">
          <b>{s.labels.context_unavailable}</b>
          <div className="small">Reason: {slot.reason ?? 'not recorded'}</div>
        </div>
      )}
      <div className="small muted">
        {slot.latency_ms === null ? 'last call: not measured' : `last call ${(slot.latency_ms / 1000).toFixed(2)} s`}
        {slot.attempts > 1 ? ` · ${slot.attempts} attempts` : ''}
      </div>
    </div>
  )
}

function VisionDetail({ cfg, s }: { cfg: AgentCardConfig; s: SentinelState }) {
  const sc = s.scene
  const live = visionLive(s)
  const models = sc?.models ? Object.entries(sc.models) : []
  return (
    <div className={`agent state-${live ? 'done' : 'idle'}`}>
      <h3>{cfg.title} <span className={`pill ${live ? 'done' : 'idle'}`}>{sc?.status ?? 'not reported'}</span></h3>
      {sc?.note && <div className="small secondary">{sc.note}</div>}
      <div className="ag-kv">
        <span>Detector</span><span className="mono">{fmt(sc?.detector_ms ?? s.metrics.detector_ms)} ms · {fmt(sc?.detector_fps ?? s.metrics.detector_fps, 1)} fps · frame {sc?.last_frame_id ?? '--'}</span>
        <span>Video</span><span>{s.video.test_pattern ? 'test pattern (no video loaded)' : s.video.source ?? '--'} · detector: {s.video.detector || '--'}{s.video.decode_error ? ` · ${s.video.decode_error}` : ''}</span>
        <span>Instruments</span><span>{sc?.tools?.length ? sc.tools.join(', ') : 'none detected'}</span>
        <span>Anatomy</span><span>{sc?.anatomy?.length ? sc.anatomy.join(', ') : 'none detected'}</span>
        <span>Step</span><span>{sc?.step?.display ?? 'not determined'}{sc?.step?.stale ? ' (stale)' : ''}{sc?.step?.next_display ? ` · next: ${sc.step.next_display}` : ''}<span className="muted"> · {sc?.step?.basis ?? 'basis not reported'}</span></span>
        <span>CVS</span>
        <span>
          {!sc?.cvs_available ? 'classifier not available' : sc.cvs_achieved ? `achieved${sc.cvs_seen_at_s !== null ? ` at t ${sc.cvs_seen_at_s.toFixed(1)} s` : ''}` : 'not yet'}
          {sc?.cvs?.length ? (
            <ul className="ag-list">
              {sc.cvs.map((c) => (
                <li key={c.name}><span className={c.met ? 'chain-ok' : 'muted'}>{c.met ? 'met' : 'not met'}</span> {c.text}{c.p !== null ? <span className="mono muted"> p {c.p.toFixed(2)}{c.threshold !== null ? ` / ${c.threshold.toFixed(2)}` : ''}</span> : null}</li>
              ))}
            </ul>
          ) : null}
        </span>
        <span>Models</span>
        <span>
          {models.length === 0 ? <span className="muted">not reported</span> : (
            <ul className="ag-list">
              {models.map(([name, m]) => (
                <li key={name}><span className={m.loaded ? 'chain-ok' : 'chain-bad'}>{m.loaded ? 'loaded' : 'not loaded'}</span> <b>{name}</b>{m.attribution ? <span className="muted"> · {m.attribution}</span> : null}</li>
              ))}
            </ul>
          )}
        </span>
      </div>
      {sc?.events?.length ? (
        <>
          <div className="sees-title">Recent video events</div>
          <ul className="sees ag-sees">
            {sc.events.slice(-8).reverse().map((e) => (
              <li key={e.source_id}><SourceChip id={e.source_id} /> <span className="mono muted">t {e.t_s.toFixed(1)} s</span> <span>{e.text}</span></li>
            ))}
          </ul>
        </>
      ) : null}
      {sc?.deviations?.length ? (
        <>
          <div className="sees-title">Deviations</div>
          <ul className="sees ag-sees">
            {sc.deviations.slice(-5).reverse().map((e) => (
              <li key={e.source_id}><SourceChip id={e.source_id} /> <span>{e.text}</span></li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  )
}

function GateDetail({ cfg, s }: { cfg: AgentCardConfig; s: SentinelState }) {
  const g = s.gate
  const cls = g.emergency ? 'unavailable' : g.status === 'hold' ? 'thinking' : 'done'
  return (
    <div className={`agent state-${cls}`}>
      <h3>{cfg.title} <span className={`gate-chip ${g.status}`}>{g.chip || g.status}</span></h3>
      <div className="ag-kv">
        <span>Gate</span><span>{g.status}{g.settling ? ` · settling, ${g.settle_remaining_s.toFixed(1)} s left` : ''}</span>
        <span>Instrument</span><span>{g.instrument ?? 'none in use'}{g.tools.length ? <span className="muted"> · in view: {g.tools.join(', ')}</span> : null}</span>
        <span>Basis</span><span>{g.basis || '--'}</span>
        <span>Emergency</span><span>{g.emergency ? <b className="chain-bad">{g.emergency_text ?? 'yes'}</b> : 'none'}</span>
        <span>Thresholds</span><span className="secondary">{g.thresholds_note || '--'}</span>
        <span>Speaking</span><span>{s.speech.current ? <>{s.speech.current.text}{s.speech.current.release_reason ? <span className="muted"> · released: {s.speech.current.release_reason}</span> : null}</> : <span className="muted">nothing</span>}</span>
        <span>Held</span>
        <span>
          {s.speech.queued.length === 0 ? <span className="muted">queue empty</span> : (
            <ul className="ag-list">
              {s.speech.queued.map((q) => (
                <li key={q.id}>{q.urgent && <b className="chain-bad">urgent </b>}{q.text}<span className="muted"> · {q.kind}{q.held_s !== null ? `, held ${q.held_s.toFixed(1)} s` : ''}</span></li>
              ))}
            </ul>
          )}
        </span>
      </div>
    </div>
  )
}

function ArchivistDetail({ cfg, s }: { cfg: AgentCardConfig; s: SentinelState }) {
  const a = s.audit
  const cls = !a.enabled ? 'idle' : a.status === 'degraded' || a.verified === false ? 'unavailable' : 'done'
  return (
    <div className={`agent state-${cls}`}>
      <h3>{cfg.title} <span className={`pill ${cls}`}>{a.enabled ? a.status : 'off'}</span></h3>
      {!a.enabled ? <div className="muted">Audit log is off.</div> : (
        <div className="ag-kv">
          <span>Entries</span><span className="mono">{a.entries}{a.last_kind ? <span className="muted"> · last: {a.last_kind.replace(/_/g, ' ')}</span> : null}</span>
          <span>Hash chain</span>
          <span>
            {a.verified === null ? <span className="muted">not checked yet</span> : a.verified ? <b className="chain-ok">verified</b> : <b className="chain-bad">BROKEN</b>}
            {a.verified ? <span className="muted"> · {a.verified_entries} entries re-checked from the file</span> : null}
            {a.verified === false && a.verify_note ? <div className="err small">{a.verify_note}</div> : null}
          </span>
          <span>Head hash</span><span className="mono" title={a.head_hash ?? ''}>{a.head_hash ? `${a.head_hash.slice(0, 16)}…` : '--'}</span>
          <span>Session</span><span className="mono">{a.session_id ?? '--'}</span>
          <span>File</span><span className="mono small">{a.path ?? '--'}</span>
          {a.status === 'degraded' && (<><span>Fault</span><span className="err">Log file not writable: {a.error}. Entries are kept in memory only.</span></>)}
        </div>
      )}
      <div className="audit-actions">
        <a className="small-btn" href="/post-op" target="_blank" rel="noreferrer">Post-op report</a>
        <a className="small-btn" href="/audit/file">Download log</a>
      </div>
    </div>
  )
}

function Detail({ cfg, s }: { cfg: AgentCardConfig; s: SentinelState }) {
  switch (cfg.kind) {
    case 'vision': return <VisionDetail cfg={cfg} s={s} />
    case 'llm': return <LlmDetail cfg={cfg} s={s} />
    case 'code': return <GateDetail cfg={cfg} s={s} />
    case 'report': return <ArchivistDetail cfg={cfg} s={s} />
  }
}

function llmMessageKey(cfg: AgentCardConfig, s: SentinelState): string | null {
  if (cfg.kind !== 'llm') return null
  const msg = slotFor(s, cfg.stateKey)?.message
  if (!msg?.hypothesis) return null
  return `${msg.round}:${msg.category}:${msg.hypothesis}`
}

export default function AgentsPage() {
  const { state: s, link } = useSentinelState()
  const [hist, setHist] = useState<History>({})
  const [commOpen, setCommOpen] = useState(false)
  const [selected, setSelected] = useState<string | null>(() => {
    const q = new URLSearchParams(window.location.search).get('agent')
    return q && AGENT_ROSTER.some((c) => c.id === q) ? q : null
  })
  const detailRef = useRef<HTMLElement>(null)
  const cardRefs = useRef<Record<string, HTMLButtonElement | null>>({})
  const prevMsg = useRef<Record<string, string>>({})
  const { layerRef, fly } = usePlaneLayer()
  const { rows: auditRows, error: auditError, loaded: auditLoaded } = useAuditEntries(commOpen || selected !== null)

  useEffect(() => { document.title = 'Sentinel-OR · Agents' }, [])
  useEffect(() => { if (s) setHist((h) => pushSamples(h, s)) }, [s])

  useEffect(() => {
    if (!selected) return
    detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [selected])

  useEffect(() => {
    if (!s) return
    const prev = prevMsg.current
    for (const cfg of AGENT_ROSTER) {
      if (cfg.kind !== 'llm') continue
      const key = llmMessageKey(cfg, s)
      if (!key || key === prev[cfg.id]) continue
      prev[cfg.id] = key
      const from = cardRefs.current[cfg.id]
      const msg = slotFor(s, cfg.stateKey)?.message
      if (!from || !msg) continue
      if (msg.round === 2 && msg.responds_to.length > 0) {
        msg.responds_to.forEach((r, i) => {
          const toId = AGENT_TO_CARD[r.agent] ?? r.agent
          fly(from, cardRefs.current[toId], i * 110)
        })
      } else if (msg.round === 1 && (DELIB_CARD_IDS as readonly string[]).includes(cfg.id)) {
        let delay = 0
        for (const other of DELIB_CARD_IDS) {
          if (other === cfg.id) continue
          const st = slotFor(s, other)?.state
          if (st && st !== 'idle') {
            fly(from, cardRefs.current[other], delay)
            delay += 90
          }
        }
        fly(from, cardRefs.current.gate, delay)
      } else if (cfg.id === 'procedure') {
        fly(from, cardRefs.current.safety ?? cardRefs.current.gate, 0)
      } else if (cfg.id === 'safety') {
        fly(from, cardRefs.current.gate, 0)
      }
    }
  }, [s, fly])

  const pick = (id: string) => () => setSelected((cur) => (cur === id ? null : id))

  if (!s) {
    return (
      <div className="agents-page">
        <header className="ag-header">
          <div className="ag-brand"><b>SWARM</b> <span>AGENTS</span></div>
          <div className="ag-right"><span className="ag-pill tone-idle"><i className="ag-dot" />{link.toUpperCase()}</span></div>
        </header>
        <div className="ag-connecting">Connecting to backend ({link})...</div>
      </div>
    )
  }

  const liveCount = AGENT_ROSTER.filter((c) => deriveStatus(c, s).tone === 'live').length
  const sel = AGENT_ROSTER.find((c) => c.id === selected) ?? null
  const d = s.deliberation
  const m = s.metrics

  return (
    <div className={`agents-page${commOpen ? ' ag-comm-open' : ''}`}>
      <PlaneLayer layerRef={layerRef} />
      {commOpen && <CommunicationPanel rows={auditRows} onClose={() => setCommOpen(false)} />}
      <header className="ag-header">
        <div className="ag-brand"><b>SWARM</b> <span>AGENTS</span></div>
        <div className="ag-right">
          <button type="button" className="small-btn ag-comm-btn" onClick={() => setCommOpen(true)}>Communication</button>
          <span className={`ag-pill tone-${link === 'live' ? 'live' : 'down'}`}><i className="ag-dot" />{link === 'live' ? 'ACTIVE' : `LINK ${link.toUpperCase()}`}</span>
          <span className="ag-hw">
            <b>128 GB unified memory</b>
            <small>ASUS Ascent GX10 (GB10) · {m.gb_used === null ? 'in use: not measured' : `${m.gb_used.toFixed(1)} GB in use`}
              {m.llm_tps_per_agent !== null ? ` · LLM ${m.llm_tps_per_agent.toFixed(0)} tok/s per call (median)` : ''}</small>
          </span>
        </div>
      </header>

      <h1 className="ag-h1">Agents <span className="ag-h1-sub">{liveCount} of {AGENT_ROSTER.length} live · t {s.clock_s.toFixed(1)} s · {s.ended ? 'ended' : s.playing ? 'playing' : 'paused'}</span></h1>

      <div className="ag-row">
        {AGENT_ROSTER.map((cfg) => (
          <AgentCard
            key={cfg.id}
            cfg={cfg}
            s={s}
            history={hist[cfg.id] ?? []}
            selected={selected === cfg.id}
            onSelect={pick(cfg.id)}
            cardRef={(el) => { cardRefs.current[cfg.id] = el }}
          />
        ))}
      </div>

      <div className="ag-delib">
        <span className="delib-k">Deliberation</span>
        {d.outcome ? <span className={`outcome ${d.outcome}`}>{d.outcome}</span> : <span className="outcome pending">no event yet</span>}
        <span className="ag-delib-text">
          {d.summary ?? <span className="muted">{d.status}</span>}
          {d.team_question && <> · <b>Question to the team:</b> {d.team_question}</>}
          {d.unavailable_agents.length > 0 && <span className="muted"> · unavailable: {d.unavailable_agents.join(', ')}</span>}
          {m.deliberation_s !== null && <span className="muted mono"> · {m.deliberation_s.toFixed(2)} s</span>}
        </span>
      </div>

      <section className="ag-detail" ref={detailRef}>
        {sel ? (
          <>
            <div className="ag-detail-head">
              <span className="ag-detail-title">{sel.title} <span className="muted">· {sel.subtitle}</span></span>
              <button className="small-btn" onClick={() => setSelected(null)}>Close</button>
            </div>
            <div className="ag-detail-model">{sel.category} <span className="ag-slash">/</span> {sel.model}{sel.note ? <> · {sel.note}</> : null}</div>
            <Detail cfg={sel} s={s} />
            <ActionsTimeline cfg={sel} rows={auditRows} error={auditError} loaded={auditLoaded} />
          </>
        ) : (
          <div className="muted ag-hint">Click a card to scroll here: what that agent sees, its latest message, and every action it logged.</div>
        )}
      </section>

      <footer className="ag-footer">
        <span>Research prototype · outcomes and questions are decided by code from validated agent messages · no scripted text stands in for a failed agent</span>
        <span>{s.labels.attribution}</span>
      </footer>
    </div>
  )
}
