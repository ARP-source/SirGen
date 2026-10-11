import { useEffect, useState } from 'react'
import { getJson } from '../api'
import { ProvenanceBadge } from './Badges'

type Entry = Record<string, unknown> & { source_id: string; provenance: string }
type Record_ = { label?: string; display_name?: string; attribution?: string } & Record<string, unknown>

const SECTIONS: [string, string][] = [
  ['demographics', 'Demographics'], ['encounter', 'Encounter'], ['history', 'History'],
  ['medications', 'Medications'], ['allergies', 'Allergies'], ['labs', 'Labs'], ['notes', 'Notes'],
]

function describe(section: string, e: Entry): string {
  const val = (v: unknown) => (v === null || v === undefined ? 'not yet fetched' : typeof v === 'object' ? JSON.stringify(v) : String(v))
  switch (section) {
    case 'demographics':
    case 'encounter':
      return `${String(e.field).replace(/_/g, ' ')}: ${val(e.value)}${e.unit ? ` ${e.unit}` : ''}${e.detail ? `. ${e.detail}` : ''}`
    case 'history':
      return `${e.condition}. ${e.detail ?? ''}`
    case 'medications':
      return `${e.drug} ${e.dose ?? ''}. Last dose: ${e.last_dose ?? 'not recorded'}`
    case 'allergies':
      return `${e.substance}: ${e.reaction} (${e.severity})`
    case 'labs':
      return `${e.test}: ${val(e.value)}${e.value !== null && e.unit ? ` ${e.unit}` : ''}`
    case 'notes':
      return `${e.type} (${e.time}): ${e.text}`
    default:
      return JSON.stringify(e)
  }
}

export default function PatientChart() {
  const [rec, setRec] = useState<Record_ | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    getJson<Record_>('/patient').then(setRec).catch((e: Error) => setErr(e.message))
  }, [])

  return (
    <section className="panel fill">
      <h2>Patient chart {rec?.display_name ? <span className="secondary">{String(rec.display_name)}</span> : null}</h2>
      {err && <div className="err">{err}</div>}
      {rec && (
        <>
          <div className="src" style={{ marginTop: 0 }}>{String(rec.label ?? '')}</div>
          <div className="patient chart-sections">
            {SECTIONS.map(([key, title]) => {
              const entries = (rec[key] as Entry[] | undefined) ?? []
              if (!entries.length) return null
              return (
                <div key={key}>
                  <h3>{title}</h3>
                  {entries.map((e) => (
                    <div className="entry" key={e.source_id} id={`src-${e.source_id}`}>
                      <span className="sid">{e.source_id}</span>
                      <span>{describe(key, e)}</span>
                      <ProvenanceBadge provenance={e.provenance} />
                    </div>
                  ))}
                </div>
              )
            })}
          </div>
        </>
      )}
    </section>
  )
}
