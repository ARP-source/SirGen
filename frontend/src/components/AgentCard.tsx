import type { AgentSlot, SentinelState } from '../types'
import type { AgentCardConfig, AgentKind } from '../agentsRoster'
import AgentIcon3D from './AgentIcon3D'

/* ---------- live status, derived from state only ---------- */

export type Tone = 'live' | 'busy' | 'down' | 'idle'

export interface LiveStatus {
  word: string
  tone: Tone
  caption: string
  /** Live numbers for the status row, one short phrase per entry. Empty when nothing is measured. */
  numbers: string[]
}

/** state.agents only has three keys in types.ts; the roster may name more. Index by string and handle undefined. */
export function slotFor(s: SentinelState, key: string | undefined): AgentSlot | undefined {
  if (!key) return undefined
  return (s.agents as Partial<Record<string, AgentSlot>>)[key]
}

const ms = (v: number | null | undefined, d = 0) => (v === null || v === undefined ? '--' : `${v.toFixed(d)} ms`)

export function visionLive(s: SentinelState): boolean {
  const fps = s.scene?.detector_fps ?? s.metrics.detector_fps
  return s.scene?.status === 'live' || (fps !== null && fps !== undefined && fps > 0)
}

export function deriveStatus(cfg: AgentCardConfig, s: SentinelState): LiveStatus {
  switch (cfg.kind) {
    case 'vision': {
      const sc = s.scene
      const live = visionLive(s)
      const detMs = sc?.detector_ms ?? s.metrics.detector_ms
      const fps = sc?.detector_fps ?? s.metrics.detector_fps
      const models = sc?.models ? Object.values(sc.models) : []
      const loaded = models.filter((m) => m.loaded).length
      const word = live ? 'ACTIVE' : sc?.status === 'stale' ? 'STALE' : sc?.status === 'unavailable' ? 'UNAVAILABLE' : 'STANDBY'
      const tone: Tone = live ? 'live' : sc?.status === 'stale' ? 'busy' : sc?.status === 'unavailable' ? 'down' : 'idle'
      const numbers: string[] = []
      numbers.push(`${ms(detMs)} · ${fps === null || fps === undefined ? '--' : fps.toFixed(1)} fps`)
      numbers.push(models.length ? `${loaded}/${models.length} models loaded` : 'models: not reported')
      return { word, tone, caption: sc?.note || s.video.detector || sc?.status || 'not reported', numbers }
    }
    case 'llm': {
      const slot = slotFor(s, cfg.stateKey)
      if (!slot) return { word: 'STANDBY', tone: 'idle', caption: 'not yet wired', numbers: [] }
      const word =
        slot.state === 'thinking' ? 'THINKING'
        : slot.state === 'waiting' ? 'WAITING'
        : slot.state === 'done' ? 'REPORTED'
        : slot.state === 'unavailable' ? 'UNAVAILABLE'
        : 'STANDBY'
      const tone: Tone =
        slot.state === 'done' ? 'live' : slot.state === 'thinking' || slot.state === 'waiting' ? 'busy' : slot.state === 'unavailable' ? 'down' : 'idle'
      const numbers: string[] = []
      numbers.push(slot.latency_ms === null ? 'last call: --' : `last call ${(slot.latency_ms / 1000).toFixed(2)} s${slot.attempts > 1 ? `, ${slot.attempts} attempts` : ''}`)
      return { word, tone, caption: slot.status, numbers }
    }
    case 'code': {
      const g = s.gate
      const word = g.emergency ? 'EMERGENCY' : g.status === 'hold' ? 'HOLDING' : 'OPEN'
      const tone: Tone = g.emergency ? 'down' : g.status === 'hold' ? 'busy' : 'live'
      const held = s.speech.queued.length
      const numbers = [
        `${held} held${g.settling && g.settle_remaining_s > 0 ? ` · settle ${g.settle_remaining_s.toFixed(1)} s` : ''}`,
        g.instrument ? `in use: ${g.instrument}` : 'no instrument in use',
      ]
      return { word, tone, caption: g.chip || g.basis || g.status, numbers }
    }
    case 'report': {
      const a = s.audit
      const word = !a.enabled ? 'OFF' : a.status === 'degraded' ? 'DEGRADED' : a.verified === false ? 'CHAIN BROKEN' : 'LOGGING'
      const tone: Tone = !a.enabled ? 'idle' : a.status === 'degraded' || a.verified === false ? 'down' : 'live'
      const chain = a.verified === null ? 'chain not checked yet' : a.verified ? 'chain verified' : 'chain BROKEN'
      const numbers = [`${a.entries} entries`, chain]
      const caption = a.last_kind ? `last: ${a.last_kind.replace(/_/g, ' ')}` : a.enabled ? 'no entries yet' : 'audit log is off'
      return { word, tone, caption, numbers }
    }
  }
}

/** The one live number a card's sparkline follows. null when not measured right now. */
export function sampleFor(cfg: AgentCardConfig, s: SentinelState): number | null {
  switch (cfg.kind) {
    case 'vision':
      return s.scene?.detector_ms ?? s.metrics.detector_ms ?? null
    case 'llm':
      return slotFor(s, cfg.stateKey)?.latency_ms ?? null
    case 'code':
      return s.speech.queued.length
    case 'report':
      return s.audit.enabled ? s.audit.entries : null
  }
}

export const SPARK_LABEL: Record<AgentKind, string> = {
  vision: 'detector ms',
  llm: 'call latency',
  code: 'messages held',
  report: 'log entries',
}

/* ---------- sparkline from real samples only ---------- */

export function Sparkline({ values, label }: { values: number[]; label: string }) {
  const W = 120
  const H = 28
  if (values.length < 2) {
    return <span className="ag-spark-empty" title={`${label}: waiting for samples`}>no data yet</span>
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pts = values
    .map((v, i) => `${((i / (values.length - 1)) * (W - 2) + 1).toFixed(1)},${(H - 2 - ((v - min) / span) * (H - 4)).toFixed(1)}`)
    .join(' ')
  return (
    <svg className="ag-spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
      aria-label={`${label}: ${values.length} samples, ${min.toFixed(1)} to ${max.toFixed(1)}`}>
      <title>{`${label}: last ${values.length} samples, min ${min.toFixed(1)}, max ${max.toFixed(1)}`}</title>
      <polyline points={pts} />
    </svg>
  )
}

/* ---------- card ---------- */

export default function AgentCard({
  cfg, s, history, selected, onSelect, cardRef,
}: {
  cfg: AgentCardConfig
  s: SentinelState
  history: number[]
  selected: boolean
  onSelect: () => void
  cardRef?: (el: HTMLButtonElement | null) => void
}) {
  const st = deriveStatus(cfg, s)
  const slot = cfg.kind === 'llm' ? slotFor(s, cfg.stateKey) : undefined
  const procThinking = cfg.id === 'procedure' && s.procedure.state === 'thinking'
  const active = slot?.state === 'thinking' || slot?.state === 'waiting' || procThinking
  const thinking = cfg.kind === 'llm' && active
  return (
    <button
      type="button"
      ref={cardRef}
      data-agent={cfg.id}
      className={`ag-card tone-${st.tone}${selected ? ' selected' : ''}${thinking ? ' thinking' : ''}${slot?.state === 'waiting' ? ' waiting' : ''}`}
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${cfg.title}, ${cfg.subtitle}, ${st.word}`}
    >
      <div className="ag-head">
        <div className="ag-title">{cfg.title}</div>
        <div className="ag-sub">{cfg.subtitle}</div>
      </div>
      <div className="ag-icon">
        <AgentIcon3D id={cfg.id} tone={st.tone} />
        {thinking && (
          <div className="ag-paper" title="writing a message" aria-hidden="true">
            <i /><i /><i /><i />
          </div>
        )}
      </div>
      <div className="ag-status">
        <span className="ag-state"><i className="ag-dot" />{st.word}</span>
        <Sparkline values={history} label={SPARK_LABEL[cfg.kind]} />
      </div>
      <div className="ag-nums mono">
        {st.numbers.length ? st.numbers.map((n, i) => <span key={i}>{n}</span>) : <span className="muted">not measured</span>}
      </div>
      <div className="ag-caption" title={st.caption}>{st.caption}</div>
      <div className="ag-foot">
        <div className="ag-title2">{cfg.title}</div>
        <div className="ag-sub">{cfg.subtitle}</div>
        <div className="ag-model"><span className="ag-cat">{cfg.category}</span> <span className="ag-slash">/</span> {cfg.model}</div>
        <div className="ag-task"><span className="ag-k">TASK:</span> {cfg.task}</div>
      </div>
    </button>
  )
}
