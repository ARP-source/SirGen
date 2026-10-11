import type { AgentSlot, SentinelState } from '../types'
import type { AgentCardConfig, AgentKind } from '../agentsRoster'

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

/* ---------- inline SVG line icons ---------- */

function Icon({ id }: { id: string }) {
  const p = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  switch (id) {
    case 'vision': // camera lens
      return (
        <svg viewBox="0 0 96 96" {...p}>
          <circle cx="48" cy="48" r="36" />
          <circle cx="48" cy="48" r="26" strokeWidth="1.2" opacity="0.6" />
          <circle cx="48" cy="48" r="15" />
          <circle cx="48" cy="48" r="6" fill="currentColor" stroke="none" opacity="0.85" />
          <path d="M33 33 L41 41 M63 33 L55 41 M33 63 L41 55 M63 63 L55 55" opacity="0.6" />
          <path d="M40 18 Q48 14 56 18" opacity="0.5" />
        </svg>
      )
    case 'procedure': // steps joined in sequence
      return (
        <svg viewBox="0 0 96 96" {...p}>
          <rect x="14" y="14" width="24" height="18" rx="4" />
          <rect x="58" y="39" width="24" height="18" rx="4" />
          <rect x="14" y="64" width="24" height="18" rx="4" />
          <path d="M38 23 H48 Q52 23 52 27 V44 Q52 48 56 48 H58" />
          <path d="M58 48 H56 Q52 48 52 52 V69 Q52 73 48 73 H38" />
          <path d="M20 23 H32 M64 48 H76 M20 73 H32" opacity="0.6" />
          <path d="M86 20 v10 M81 25 h10" opacity="0.6" />
        </svg>
      )
    case 'surgical': // grasper / forceps
      return (
        <svg viewBox="0 0 96 96" {...p}>
          <path d="M18 78 L58 38" />
          <path d="M78 18 L58 38 L66 30" />
          <path d="M18 78 L30 66" opacity="0.5" />
          <circle cx="18" cy="78" r="6" />
          <path d="M24 84 L48 60" opacity="0.7" />
          <path d="M60 36 L76 20" opacity="0.6" />
          <path d="M80 42 L70 32" opacity="0.6" />
          <path d="M14 46 Q24 36 34 46 Q44 56 54 46" opacity="0.35" strokeWidth="1.2" />
        </svg>
      )
    case 'anesthesia': // heart with trace
      return (
        <svg viewBox="0 0 96 96" {...p}>
          <path d="M48 82 C20 62 10 46 16 32 C21 20 38 18 48 32 C58 18 75 20 80 32 C86 46 76 62 48 82 Z" />
          <path d="M20 52 H34 L40 40 L48 64 L54 46 L58 52 H76" />
        </svg>
      )
    case 'record': // chart document
      return (
        <svg viewBox="0 0 96 96" {...p}>
          <path d="M26 12 H58 L72 26 V84 H26 Z" />
          <path d="M58 12 V26 H72" />
          <path d="M36 42 H62 M36 52 H62 M36 62 H54" opacity="0.7" />
          <path d="M36 32 H46" />
          <path d="M60 70 l3 3 l6 -7" />
        </svg>
      )
    case 'safety': // shield with check
      return (
        <svg viewBox="0 0 96 96" {...p}>
          <path d="M48 12 L78 22 V46 C78 64 66 76 48 84 C30 76 18 64 18 46 V22 Z" />
          <path d="M36 48 L45 57 L62 38" />
          <path d="M48 12 V84" opacity="0.2" strokeWidth="1" />
        </svg>
      )
    case 'gate': // speech bubble on hold
      return (
        <svg viewBox="0 0 96 96" {...p}>
          <path d="M18 20 H78 Q84 20 84 26 V58 Q84 64 78 64 H44 L28 78 V64 H18 Q12 64 12 58 V26 Q12 20 18 20 Z" />
          <path d="M41 32 V52 M55 32 V52" strokeWidth="3" />
          <path d="M22 70 H30" opacity="0.4" />
        </svg>
      )
    case 'archivist': // folder with chained pages
      return (
        <svg viewBox="0 0 96 96" {...p}>
          <path d="M12 30 V76 Q12 82 18 82 H78 Q84 82 84 76 V38 Q84 32 78 32 H48 L40 24 H18 Q12 24 12 30 Z" />
          <path d="M34 46 H62 M34 56 H62 M34 66 H52" opacity="0.7" />
          <path d="M62 62 a5 5 0 0 1 7 0 l5 5 a5 5 0 0 1 -7 7 l-2 -2" opacity="0.8" />
          <path d="M72 72 a5 5 0 0 1 -7 0 l-5 -5 a5 5 0 0 1 7 -7 l2 2" opacity="0.8" />
        </svg>
      )
    default:
      return (
        <svg viewBox="0 0 96 96" {...p}>
          <circle cx="48" cy="48" r="30" />
          <path d="M48 30 V52 M48 62 V64" />
        </svg>
      )
  }
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
  // The model is composing: the card pops out and a sheet of paper is being written on.
  const thinking = cfg.kind === 'llm' && slotFor(s, cfg.stateKey)?.state === 'thinking'
  return (
    <button
      type="button"
      ref={cardRef}
      data-agent={cfg.id}
      className={`ag-card tone-${st.tone}${selected ? ' selected' : ''}${thinking ? ' thinking' : ''}`}
      onClick={onSelect}
      aria-pressed={selected}
      aria-label={`${cfg.title}, ${cfg.subtitle}, ${st.word}`}
    >
      <div className="ag-head">
        <div className="ag-title">{cfg.title}</div>
        <div className="ag-sub">{cfg.subtitle}</div>
      </div>
      <div className="ag-icon">
        <div className="ag-3d">
          <div className="ag-obj">
            <div className="ag-layer back"><Icon id={cfg.id} /></div>
            <div className="ag-layer mid"><Icon id={cfg.id} /></div>
            <div className="ag-layer front"><Icon id={cfg.id} /></div>
          </div>
          <i className="ag-shadow" />
        </div>
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
