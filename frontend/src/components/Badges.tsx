import type { SourceKind } from '../types'

/** Provenance from the data files: real / real (derived) / pending / synthetic / simulated. */
export function provenanceClass(p: string): { cls: string; text: string } {
  const s = p.toLowerCase()
  if (s.startsWith('pending')) return { cls: 'pending', text: 'pending' }
  if (s.startsWith('real (derived)')) return { cls: 'real', text: 'real (derived)' }
  if (s.startsWith('real')) return { cls: 'real', text: 'real' }
  if (s.startsWith('synthetic')) return { cls: 'synthetic', text: 'synthetic' }
  if (s.startsWith('simulated')) return { cls: 'simulated', text: 'simulated' }
  return { cls: '', text: p.split(':')[0] || 'unknown' }
}

export function ProvenanceBadge({ provenance }: { provenance: string }) {
  const { cls, text } = provenanceClass(provenance)
  return (
    <span className={`badge ${cls}`} title={provenance}>
      {text}
    </span>
  )
}

const KIND_TEXT: Record<SourceKind, [string, string]> = {
  simulated: ['simulated', 'simulated'],
  approximate: ['approximate', 'approximate (not real samples)'],
  real_time_shifted: ['time-shifted', 'real, time-shifted'],
  none: ['pending', 'no source'],
}

export function SourceBadge({ kind }: { kind: SourceKind }) {
  const [cls, text] = KIND_TEXT[kind]
  return <span className={`badge ${cls}`}>{text}</span>
}
