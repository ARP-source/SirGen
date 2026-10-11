import { useEffect, useMemo, useRef, useState } from 'react'
import type uPlot from 'uplot'
import { fmt, getJson } from '../api'
import type { SentinelState, VitalsSample } from '../types'
import UChart, { type Marker } from './UChart'
import { SourceBadge } from './Badges'

const C = {
  sbp: 'var(--s-sbp)', map: 'var(--s-map)', dbp: 'var(--s-dbp)', hr: 'var(--s-hr)', spo2: 'var(--s-spo2)',
}
// canvas needs resolved colors
const hex = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v.slice(4, -1)).trim()

interface History {
  scenario: string | null
  samples: Map<number, VitalsSample>
}

export default function VitalsPanel({ state }: { state: SentinelState }) {
  const v = state.vitals
  const hist = useRef<History>({ scenario: null, samples: new Map() })
  const [version, setVersion] = useState(0)

  useEffect(() => {
    const h = hist.current
    const lastT = h.samples.size ? Math.max(...h.samples.keys()) : -1
    if (h.scenario !== state.scenario_id || (v.t_s !== null && v.t_s < lastT)) {
      h.scenario = state.scenario_id
      h.samples = new Map()
      getJson<{ scenario_id: string; samples: VitalsSample[] }>('/vitals/history')
        .then((res) => {
          if (res.scenario_id !== hist.current.scenario) return
          for (const s of res.samples) hist.current.samples.set(s.t_s, s)
          setVersion((n) => n + 1)
        })
        .catch(() => undefined)
    }
    if (v.t_s !== null) {
      h.samples.set(v.t_s, { t_s: v.t_s, hr: v.hr, sbp: v.sbp, dbp: v.dbp, map: v.map, spo2: v.spo2 })
      setVersion((n) => n + 1)
    }
  }, [state.scenario_id, v.t_s, v.hr, v.sbp, v.dbp, v.map, v.spo2])

  const { bp, hr, spo2 } = useMemo(() => {
    const rows = [...hist.current.samples.values()].sort((a, b) => a.t_s - b.t_s)
    const xs = rows.map((r) => r.t_s)
    const col = (k: keyof VitalsSample) => rows.map((r) => r[k] as number | null)
    return {
      bp: [xs, col('sbp'), col('map'), col('dbp')] as uPlot.AlignedData,
      hr: [xs, col('hr')] as uPlot.AlignedData,
      spo2: [xs, col('spo2')] as uPlot.AlignedData,
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version])

  const ev = state.event
  const evMarker: Marker[] = ev ? [{ kind: 'v', value: ev.event_timestamp, label: 'alert', color: hex('var(--warning)') }] : []
  const bpMarkers: Marker[] = [
    { kind: 'h', value: 65, label: 'R1: MAP 65', color: hex('var(--muted)') },
    { kind: 'h', value: 180, label: 'R2: SBP 180', color: hex('var(--muted)') },
    ...evMarker,
  ]
  const xMax = state.scenario.duration_s || 120
  const nums: [string, number | null, string, string][] = [
    ['MAP', v.map, 'mmHg', C.map], ['SBP', v.sbp, 'mmHg', C.sbp], ['DBP', v.dbp, 'mmHg', C.dbp],
    ['HR', v.hr, 'bpm', C.hr], ['SpO2', v.spo2, '%', C.spo2],
  ]

  return (
    <section className="panel" id="src-vit-art">
      <h2>
        Vitals (arterial line, 1 Hz) <span className="sid">{v.source_id}</span> <SourceBadge kind={v.source_kind} />
      </h2>
      <div className="vnums">
        {nums.map(([k, val, unit, color]) => (
          <div className="vnum" key={k} style={{ ['--c' as string]: color }}>
            <div className="k">{k}</div>
            <div className="v mono">{fmt(val)}</div>
            <div className="u">{unit}</div>
          </div>
        ))}
      </div>
      <UChart
        title="Arterial pressure"
        series={[
          { label: 'SBP', color: hex(C.sbp), unit: 'mmHg' },
          { label: 'MAP', color: hex(C.map), unit: 'mmHg' },
          { label: 'DBP', color: hex(C.dbp), unit: 'mmHg' },
        ]}
        data={bp} xMax={xMax} yRange={[20, 300]} height={200} markers={bpMarkers}
      />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <UChart title="Heart rate" series={[{ label: 'HR', color: hex(C.hr), unit: 'bpm' }]}
          data={hr} xMax={xMax} yRange={[30, 150]} height={130} markers={evMarker} />
        <UChart title="SpO2" series={[{ label: 'SpO2', color: hex(C.spo2), unit: '%' }]}
          data={spo2} xMax={xMax} yRange={[80, 100]} height={130} markers={evMarker} />
      </div>
      <div className="src">Source: {v.source}</div>
      {state.nibp.length > 0 && (
        <div className="src">
          Cuff (NIBP):{' '}
          {state.nibp.map((n) => (
            <span key={n.source_id} id={`src-${n.source_id}`} style={{ marginRight: 12 }}>
              <span className="sid">{n.source_id}</span> t {n.t_s} s: {fmt(n.sbp)}/{fmt(n.dbp)} ({fmt(n.map)}){' '}
              <span className="muted">{n.provenance}</span>
            </span>
          ))}
        </div>
      )}
    </section>
  )
}
