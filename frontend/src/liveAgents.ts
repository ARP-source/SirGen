/** Live agent activity from WebSocket state (not in the audit log until a turn finishes). */
import type { SentinelState } from './types'
import { AGENT_ROSTER } from './agentsRoster'
import { slotFor } from './components/AgentCard'

export interface LiveActivity {
  id: string
  title: string
  state: 'thinking' | 'waiting'
  status: string
  /** One line of what they are reading, if the backend published sees. */
  seesPreview: string | null
}

export function collectLiveActivity(s: SentinelState): LiveActivity[] {
  const out: LiveActivity[] = []

  for (const cfg of AGENT_ROSTER) {
    if (cfg.kind !== 'llm' || !cfg.stateKey) continue
    const slot = slotFor(s, cfg.stateKey)
    if (!slot || (slot.state !== 'thinking' && slot.state !== 'waiting')) continue
    const preview = slot.sees.length
      ? slot.sees.slice(0, 2).map((x) => x.text).join(' · ')
      : null
    out.push({ id: cfg.id, title: cfg.title, state: slot.state, status: slot.status, seesPreview: preview })
  }

  const d = s.deliberation
  if ((s.event?.context_status === 'pending' || /round/i.test(d.status)) && d.outcome !== 'converged' && d.outcome !== 'split') {
    out.push({
      id: 'deliberation',
      title: 'Team deliberation',
      state: 'thinking',
      status: d.status,
      seesPreview: s.event ? `Event ${s.event.event_id} · ${s.event.event_type}` : null,
    })
  }

  if (s.gate.status === 'hold' && s.speech.queued.length > 0) {
    out.push({
      id: 'gate',
      title: 'The Gatekeeper',
      state: 'waiting',
      status: s.gate.chip,
      seesPreview: `${s.speech.queued.length} message(s) held${s.gate.instrument ? ` · ${s.gate.instrument} in use` : ''}`,
    })
  }

  return out
}
