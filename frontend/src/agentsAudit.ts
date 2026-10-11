// Audit-log access for the /agents page: the Actions timeline and the Communication panel are
// read from GET /audit/entries (the tamper-evident log). Nothing here is synthesised.
import { useCallback, useEffect, useRef, useState } from 'react'
import { getJson } from './api'
import type { AgentCardConfig } from './agentsRoster'

/** One audit entry as written by backend/audit.py (types.ts AuditEntry omits `data`). */
export interface AuditRow {
  seq: number
  id: string
  run: number
  wall_time: string
  clock_s: number | null
  scenario_id: string | null
  kind: string
  actor: string
  summary: string
  data: Record<string, unknown>
  hash: string
}

interface EntriesResponse { session_id: string | null; total: number; head_hash: string; entries: AuditRow[] }

export const AUDIT_POLL_MS = 5000
const LIMIT = 2000

/** Polls /audit/entries every AUDIT_POLL_MS while `active`; keeps the last good result when a poll fails. */
export function useAuditEntries(active: boolean, pollMs: number = AUDIT_POLL_MS): { rows: AuditRow[]; error: string | null; loaded: boolean; fetchedAt: number | null; refresh: () => void } {
  const [rows, setRows] = useState<AuditRow[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [fetchedAt, setFetchedAt] = useState<number | null>(null)
  const inflight = useRef(false)

  const refresh = useCallback(() => {
    if (inflight.current) return
    inflight.current = true
    getJson<EntriesResponse>(`/audit/entries?limit=${LIMIT}`)
      .then((r) => { setRows(r.entries); setError(null); setLoaded(true); setFetchedAt(Date.now()) })
      .catch((e: Error) => { setError(e.message); setLoaded(true) })
      .finally(() => { inflight.current = false })
  }, [])

  useEffect(() => {
    if (!active) return
    refresh()
    const t = window.setInterval(refresh, pollMs)
    return () => window.clearInterval(t)
  }, [active, pollMs, refresh])

  return { rows, error, loaded, fetchedAt, refresh }
}

/* ---------- which entries belong to which card ---------- */

const VISION_KINDS = new Set(['video_event', 'deviation', 'vision_status', 'phase_change'])
const GATE_KINDS = new Set(['speech_held', 'speech_released', 'speech_spoken', 'speech_urgent'])
const ARCHIVE_KINDS = new Set(['report_generated', 'metrics_snapshot', 'session_start', 'run_start', 'segment_end'])

export function belongsTo(cfg: AgentCardConfig, e: AuditRow): boolean {
  switch (cfg.kind) {
    case 'llm': return !!cfg.stateKey && e.actor === `agent:${cfg.stateKey}`
    case 'vision': return VISION_KINDS.has(e.kind) || e.actor === 'vision'
    case 'code': return GATE_KINDS.has(e.kind) || e.actor === 'gate'
    case 'report': return ARCHIVE_KINDS.has(e.kind) || e.actor === 'report'
  }
}

/** Newest first. */
export function entriesForAgent(cfg: AgentCardConfig, rows: AuditRow[]): AuditRow[] {
  const out = rows.filter((e) => belongsTo(cfg, e))
  out.sort((a, b) => b.seq - a.seq)
  return out
}

/* ---------- the conversation between agents ---------- */

export const CONVERSATION_KINDS = new Set([
  'alert', 'agent_message', 'agent_unavailable', 'deliberation_result', 'procedure_reading', 'safety_verdict',
  'speech_held', 'speech_released', 'speech_spoken', 'speech_urgent',
])

export interface RunGroup { run: number; scenario_id: string | null; rows: AuditRow[] }

/** Conversation rows grouped by run, runs ascending, rows in seq order. */
export function conversationByRun(rows: AuditRow[]): RunGroup[] {
  const byRun = new Map<number, RunGroup>()
  for (const e of rows) {
    if (!CONVERSATION_KINDS.has(e.kind)) continue
    let g = byRun.get(e.run)
    if (!g) { g = { run: e.run, scenario_id: e.scenario_id, rows: [] }; byRun.set(e.run, g) }
    if (!g.scenario_id && e.scenario_id) g.scenario_id = e.scenario_id
    g.rows.push(e)
  }
  const groups = [...byRun.values()].sort((a, b) => a.run - b.run)
  for (const g of groups) g.rows.sort((a, b) => a.seq - b.seq)
  return groups
}

export const tLabel = (t: number | null | undefined): string => (t === null || t === undefined ? 't --' : `t ${Math.round(t)} s`)

export const kindLabel = (k: string): string => k.replace(/_/g, ' ')

/** Category labels for this page; the shared CATEGORY_LABEL lacks the procedure/safety categories. */
export const CAT_LABEL: Record<string, string> = {
  bleeding_concern: 'bleeding concern',
  drug_effect: 'drug effect',
  artifact: 'artifact',
  unclear: 'unclear',
  no_concern: 'no concern',
  verdict: 'verdict',
  reading: 'reading',
}

export const STANCE_WORD: Record<string, string> = { agree: 'agrees', disagree: 'disagrees', adds: 'adds' }

/* small typed readers for `data` */
export const str = (d: Record<string, unknown>, k: string): string | null => (typeof d[k] === 'string' ? (d[k] as string) : null)
export const num = (d: Record<string, unknown>, k: string): number | null => (typeof d[k] === 'number' ? (d[k] as number) : null)
export const arr = <T = unknown>(d: Record<string, unknown>, k: string): T[] => (Array.isArray(d[k]) ? (d[k] as T[]) : [])
