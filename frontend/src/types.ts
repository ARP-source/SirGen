// Mirrors backend/state.py (SentinelState). Keep in sync.

export type SourceKind = 'simulated' | 'approximate' | 'real_time_shifted' | 'none'

export interface Vitals {
  t_s: number | null
  hr: number | null
  sbp: number | null
  dbp: number | null
  map: number | null
  spo2: number | null
  source: string
  source_kind: SourceKind
  source_id: string
}

export interface DrugEntry {
  t_s: number | null
  drug: string
  detail: string
  source_id: string
  provenance: string
}

export interface NibpEntry {
  t_s: number | null
  sbp: number | null
  dbp: number | null
  map: number | null
  source_id: string
  provenance: string
}

export interface SentinelEvent {
  event_id: string
  event_type: 'map_drop' | 'art_spike'
  event_timestamp: number
  trigger: 'scenario' | 'presenter'
  simulated: boolean
  alert_shown_at: number | null
  alert_level: 'banner' | 'quiet'
  alert_text: string
  detect_lag_ms: number | null
  event_to_alert_ms: number | null
  context_status: 'pending' | 'available' | 'unavailable'
  context_note: string
}

export interface TimelineItem {
  t_s: number
  kind: 'event' | 'alert' | 'context' | 'baseline' | 'info'
  label: string
  event_id: string | null
}

export type Category = 'bleeding_concern' | 'drug_effect' | 'artifact' | 'unclear' | 'no_concern'

export interface AgentEvidence {
  source_id: string
  note: string
}

export interface AgentResponse {
  agent: 'surgical' | 'anesthesia' | 'record'
  stance: 'agree' | 'disagree' | 'adds'
  note: string
}

/** Mirrors backend/agents/schemas.py AgentMessage. */
export interface AgentMessage {
  agent: 'surgical' | 'anesthesia' | 'record'
  round: 1 | 2
  category: Category
  hypothesis: string
  confidence: 'low' | 'medium' | 'high'
  evidence: AgentEvidence[]
  responds_to: AgentResponse[]
}

export interface AgentSlot {
  status: string
  state: 'idle' | 'waiting' | 'thinking' | 'done' | 'unavailable'
  message: AgentMessage | null
  round1: AgentMessage | null
  reason: string | null
  sees: { source_id: string | null; text: string }[]
  allowed_source_ids: string[]
  latency_ms: number | null
  attempts: number
}

export interface DeliberationSide {
  category: Category
  label: string
  agents: ('surgical' | 'anesthesia' | 'record')[]
  source_ids: string[]
  phrases: string[]
  hypotheses: Record<string, string>
}

export interface Metrics {
  render_fps: number | null
  detector_fps: number | null
  detector_ms: number | null
  event_to_alert_ms: number | null
  deliberation_s: number | null
  gb_used: number | null
  llm_tps_aggregate: number | null
  llm_tps_per_agent: number | null
  bytes_out: number | null
  models_resident: string[] | null
  notes: Record<string, string>
}

export interface AuditInfo {
  enabled: boolean
  session_id: string | null
  path: string | null
  entries: number
  head_hash: string | null
  status: 'ok' | 'degraded' | 'disabled'
  error: string
  verified: boolean | null
  verified_entries: number | null
  verify_note: string
  last_kind: string | null
}

export interface AuditEntry {
  seq: number
  id: string
  run: number
  wall_time: string
  clock_s: number | null
  scenario_id: string | null
  kind: string
  actor: string
  summary: string
  hash: string
}

export interface ReportSummary {
  run: number
  generated: string
  scenario: string
  covers_entries: [number, number]
  events: number
  narrative: 'ok' | 'unavailable'
  narrative_note: string
  narrative_ms: number | null
  chain_ok: boolean
  sha256: string
  video_events?: number
  review_items?: number | null
  medications_to_decide?: number | null
  codes?: string[]
}

export interface SentinelState {
  scenario_id: string | null
  scenario: {
    id: string
    title: string
    on_screen_label: string
    duration_s: number
    event_t_s: number | null
    event_note: string
    alert_level: 'banner' | 'quiet'
  }
  clock_s: number
  playing: boolean
  ended: boolean
  phase: {
    name: string | null
    display: string | null
    since_s: number | null
    tools_window: Record<string, number>
    status: string
    set_by: 'tracker' | 'presenter' | null
  }
  vitals: Vitals
  nibp: NibpEntry[]
  drug_log: DrugEntry[]
  event: SentinelEvent | null
  timeline: TimelineItem[]
  agents: Record<'surgical' | 'anesthesia' | 'record', AgentSlot>
  deliberation: {
    status: string
    round: number
    outcome: 'converged' | 'split' | 'pending' | null
    team_question: string | null
    summary: string | null
    shared_category: string | null
    sides: DeliberationSide[]
    question_source_ids: string[]
    unavailable_agents: string[]
  }
  baseline: {
    rule: string
    rule_text: string
    alarm: boolean
    fired_rule: string | null
    fired_at_t_s: number | null
    expected_fires: string | null
    expected_fires_at_t_s: number | null
    expected_correct: boolean | null
    comment: string
  }
  metrics: Metrics
  video: {
    source: string | null
    label: string
    test_pattern: boolean
    frame_id: number
    source_fps: number | null
    dropped_frames: number
    detector: string
    frames_decoded: number
    decode_error: string
    mean_brightness: number | null
    black_frames: boolean
  }
  labels: {
    footage: string
    attribution: string
    composite: string
    context_unavailable: string
  }
  audit: AuditInfo
  gate: {
    status: 'hold' | 'open'
    settling: boolean
    instrument: string | null
    tools: string[]
    basis: string
    settle_remaining_s: number
    chip: string
    thresholds_note: string
  }
  speech: {
    queued: Array<{
      id: string
      text: string
      kind: string
      urgent: boolean
      created_t: number
      spoken_t: number | null
      held_s: number | null
      release_reason: string | null
    }>
    current: {
      id: string
      text: string
      kind: string
      urgent: boolean
      created_t: number
      spoken_t: number | null
      held_s: number | null
      release_reason: string | null
    } | null
    caption: string | null
    held_s: number | null
    release_reason: string | null
  }
  scene: SceneView
}

export interface SceneEvent {
  source_id: string
  t_s: number
  kind: 'tool_in' | 'anatomy' | 'step' | 'cvs' | 'deviation' | 'observation' | 'first_cut' | string
  text: string
  tools: string[]
  data: Record<string, unknown>
}

export interface SceneView {
  status: 'live' | 'stale' | 'unavailable' | 'not running' | string
  note: string
  detector_ms: number | null
  detector_fps: number | null
  last_frame_id: number | null
  tools: string[]
  anatomy: string[]
  cvs: Array<{ name: string; text: string; p: number | null; threshold: number | null; met: boolean }>
  cvs_available: boolean
  cvs_achieved: boolean
  cvs_seen_at_s: number | null
  step: {
    name: string | null
    display: string | null
    since_s: number | null
    goal: string
    watch: string
    next_name: string | null
    next_display: string | null
    stale: boolean
    basis: string
  }
  events: SceneEvent[]
  deviations: SceneEvent[]
  models: Record<string, { loaded: boolean; metrics?: unknown; attribution?: string } & Record<string, unknown>>
}

export interface ScenarioSummary {
  id: string
  title: string
  on_screen_label: string
  duration_s: number
  event_t_s: number | null
  event_type: string
  alert_level: string
  vitals_source: string
  vitals_source_kind: SourceKind
}

export interface VitalsSample {
  t_s: number
  hr: number | null
  sbp: number | null
  dbp: number | null
  map: number | null
  spo2: number | null
}
