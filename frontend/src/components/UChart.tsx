import { useEffect, useRef } from 'react'
import uPlot from 'uplot'

export interface Marker {
  kind: 'h' | 'v'
  value: number
  label: string
  color: string
}

interface Props {
  title: string
  series: { label: string; color: string; unit: string }[]
  data: uPlot.AlignedData
  xMax: number
  yRange: [number, number]
  height: number
  markers: Marker[]
}

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim()

/** Thin wrapper: creates the plot once, then only calls setData (no re-creation per tick). */
export default function UChart({ title, series, data, xMax, yRange, height, markers }: Props) {
  const host = useRef<HTMLDivElement>(null)
  const plot = useRef<uPlot | null>(null)
  const markersRef = useRef(markers)
  markersRef.current = markers
  const xMaxRef = useRef(xMax)
  xMaxRef.current = xMax

  useEffect(() => {
    if (!host.current) return
    const grid = { stroke: css('--grid'), width: 1 }
    const axis = { stroke: css('--muted'), grid, ticks: { stroke: css('--axis'), width: 1 }, font: '13px system-ui' }
    const drawMarkers = (u: uPlot) => {
      const ctx = u.ctx
      ctx.save()
      ctx.font = `${12 * devicePixelRatio}px system-ui`
      ctx.lineWidth = 1 * devicePixelRatio
      for (const m of markersRef.current) {
        ctx.strokeStyle = m.color
        ctx.fillStyle = m.color
        ctx.setLineDash(m.kind === 'h' ? [6, 4] : [])
        ctx.beginPath()
        if (m.kind === 'h') {
          const y = u.valToPos(m.value, 'y', true)
          if (y < u.bbox.top || y > u.bbox.top + u.bbox.height) continue
          ctx.moveTo(u.bbox.left, y)
          ctx.lineTo(u.bbox.left + u.bbox.width, y)
          ctx.stroke()
          ctx.textAlign = 'right'
          ctx.fillText(m.label, u.bbox.left + u.bbox.width - 4, y - 4)
          ctx.textAlign = 'left'
        } else {
          const x = u.valToPos(m.value, 'x', true)
          ctx.moveTo(x, u.bbox.top)
          ctx.lineTo(x, u.bbox.top + u.bbox.height)
          ctx.stroke()
          ctx.fillText(m.label, x + 4, u.bbox.top + 14 * devicePixelRatio)
        }
      }
      ctx.restore()
    }
    const opts: uPlot.Options = {
      title,
      width: host.current.clientWidth || 600,
      height,
      scales: { x: { time: false, range: () => [0, xMaxRef.current] }, y: { range: yRange } },
      axes: [{ ...axis, label: 't (s)', labelSize: 18, labelFont: '13px system-ui' }, { ...axis, size: 44 }],
      series: [
        { label: 't_s' },
        ...series.map((s) => ({ label: `${s.label} (${s.unit})`, stroke: s.color, width: 2, spanGaps: false })),
      ],
      cursor: { drag: { x: false, y: false } },
      legend: { live: true },
      hooks: { draw: [drawMarkers] },
    }
    plot.current = new uPlot(opts, data, host.current)
    const ro = new ResizeObserver(() => {
      if (host.current && plot.current) plot.current.setSize({ width: host.current.clientWidth, height })
    })
    ro.observe(host.current)
    return () => {
      ro.disconnect()
      plot.current?.destroy()
      plot.current = null
    }
    // created once per mount; data flows through setData below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    plot.current?.setData(data)
  }, [data, markers, xMax])

  return <div className="chart" ref={host} />
}
