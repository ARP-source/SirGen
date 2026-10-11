import { useCallback, useEffect, useRef, type RefObject } from 'react'

/**
 * Paper-plane messages: a folded note becomes an SVG dart plane, flies in a shallow arc with
 * perspective scale (larger mid-flight, smaller at the ends), plus a short train of followers.
 */

export const FOLD_MS = 480
export const FLIGHT_MS = 1100
const TRAIL = 3
const TRAIL_GAP_MS = 130

const reducedMotion = (): boolean =>
  typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

function originOf(card: HTMLElement): { x: number; y: number } {
  const paper = card.querySelector<HTMLElement>('.ag-paper')
  const icon = card.querySelector<HTMLElement>('.ag-icon-stage') ?? card.querySelector<HTMLElement>('.ag-icon')
  const r = (paper ?? icon ?? card).getBoundingClientRect()
  if (paper) return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
  return { x: r.left + r.width * 0.78, y: r.top + r.height * 0.78 }
}

function targetOf(card: HTMLElement): { x: number; y: number } {
  const icon = card.querySelector<HTMLElement>('.ag-icon-stage') ?? card.querySelector<HTMLElement>('.ag-icon')
  const r = (icon ?? card).getBoundingClientRect()
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
}

/** Scale from simulated depth along the arc: smallest at launch/landing, largest mid-air. */
function depthScale(t: number, base: number): number {
  const d = 0.68 + 0.42 * Math.sin(Math.PI * t)
  return base * d
}

function createPlaneSvg(): SVGSVGElement {
  const uid = `pp${Math.random().toString(36).slice(2, 9)}`
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
  svg.setAttribute('viewBox', '0 0 80 48')
  svg.setAttribute('class', 'ag-plane-svg')
  // Nose points right (+X). Fold lines + two wing tones like folded paper.
  svg.innerHTML = `
    <defs>
      <linearGradient id="${uid}-top" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stop-color="#faf6ec"/>
        <stop offset="100%" stop-color="#e8dcc4"/>
      </linearGradient>
      <linearGradient id="${uid}-bot" x1="0%" y1="100%" x2="100%" y2="0%">
        <stop offset="0%" stop-color="#ddd0b8"/>
        <stop offset="100%" stop-color="#f0e8d8"/>
      </linearGradient>
    </defs>
    <path d="M6 24 L74 8 L44 24 L74 40 Z" fill="url(#${uid}-top)" stroke="#b8a992" stroke-width="1.2" stroke-linejoin="round"/>
    <path d="M6 24 L44 24 L74 40 Z" fill="url(#${uid}-bot)" stroke="#b8a992" stroke-width="1" stroke-linejoin="round"/>
    <path d="M6 24 L44 24 L74 8" fill="none" stroke="#9a8b78" stroke-width="0.9" opacity="0.65"/>
    <path d="M44 24 L74 40" fill="none" stroke="#9a8b78" stroke-width="0.7" opacity="0.5"/>
    <path d="M18 24 L44 22 L44 26 Z" fill="rgba(255,255,255,0.35)"/>
  `
  return svg
}

function createNote(): HTMLDivElement {
  const note = document.createElement('div')
  note.className = 'ag-plane-note'
  for (let i = 0; i < 4; i++) {
    const line = document.createElement('span')
    note.appendChild(line)
  }
  return note
}

type Path = { a: { x: number; y: number }; b: { x: number; y: number }; mx: number; my: number; heading: number; bow: number }

function pathBetween(from: HTMLElement, to: HTMLElement): Path {
  const a = originOf(from)
  const b = targetOf(to)
  const dx = b.x - a.x
  const dy = b.y - a.y
  const dist = Math.hypot(dx, dy)
  const heading = (Math.atan2(dy, dx) * 180) / Math.PI
  const bow = Math.min(90, 24 + dist * 0.12) * (dx >= 0 ? -1 : 1)
  const nx = -dy / (dist || 1)
  const ny = dx / (dist || 1)
  const mx = a.x + dx / 2 + nx * bow
  const my = a.y + dy / 2 + ny * bow
  return { a, b, mx, my, heading, bow }
}

function frame(path: Path, t: number, baseScale: number, headingWobble: number): string {
  const { a, b, mx, my, heading } = path
  let x: number
  let y: number
  let rot = heading + headingWobble
  if (t <= 0.5) {
    const u = t / 0.5
    const e = 1 - (1 - u) ** 2
    x = a.x + (mx - a.x) * e
    y = a.y + (my - a.y) * e
    rot += -8 * Math.sign(path.bow) * u
  } else {
    const u = (t - 0.5) / 0.5
    const e = u * u
    x = mx + (b.x - mx) * e
    y = my + (b.y - my) * e
    rot += -8 * Math.sign(path.bow) * (1 - u)
  }
  const s = depthScale(t, baseScale)
  return `translate(${x}px, ${y}px) translate(-50%, -50%) rotate(${rot}deg) scale(${s.toFixed(3)})`
}

function flightKeyframes(path: Path, baseScale: number): Keyframe[] {
  const steps = [0, 0.08, 0.35, 0.5, 0.72, 0.92, 1]
  return steps.map((t) => ({
    offset: t,
    transform: frame(path, t, baseScale, 0),
    opacity: t >= 0.95 ? 0.2 : 1,
  }))
}

function spawnPlane(
  layer: HTMLDivElement,
  live: Set<HTMLElement>,
  path: Path,
  delayMs: number,
  baseScale: number,
  onLeaderLand: () => void,
  isLeader: boolean,
) {
  const el = document.createElement('div')
  el.className = `ag-plane${isLeader ? ' leader' : ' trail'}`
  const wrap = document.createElement('div')
  wrap.className = 'ag-plane-body'
  const note = createNote()
  const svg = createPlaneSvg()
  wrap.appendChild(note)
  wrap.appendChild(svg)
  el.appendChild(wrap)
  el.style.transform = frame(path, 0, baseScale * 0.85, 0)
  layer.appendChild(el)
  live.add(el)

  const foldDone = delayMs + FOLD_MS
  window.setTimeout(() => wrap.classList.add('folded'), foldDone)

  const anim = el.animate(flightKeyframes(path, baseScale), {
    duration: FLIGHT_MS,
    delay: foldDone,
    easing: 'cubic-bezier(0.42, 0, 0.25, 1)',
    fill: 'forwards',
  })

  const cleanup = () => {
    el.remove()
    live.delete(el)
    if (isLeader) onLeaderLand()
  }
  anim.onfinish = cleanup
  anim.oncancel = cleanup
  window.setTimeout(() => { if (live.has(el)) cleanup() }, foldDone + FLIGHT_MS + 400)
}

export function flash(card: HTMLElement | null | undefined, cls: string, ms: number): void {
  if (!card) return
  card.classList.add(cls)
  window.setTimeout(() => card.classList.remove(cls), ms)
}

export function usePlaneLayer(): { layerRef: RefObject<HTMLDivElement>; fly: (from: HTMLElement | null | undefined, to: HTMLElement | null | undefined, delayMs?: number) => void } {
  const layerRef = useRef<HTMLDivElement>(null)
  const live = useRef<Set<HTMLElement>>(new Set())

  useEffect(() => {
    const set = live.current
    return () => { set.forEach((el) => el.remove()); set.clear() }
  }, [])

  const fly = useCallback((from: HTMLElement | null | undefined, to: HTMLElement | null | undefined, delayMs = 0) => {
    const layer = layerRef.current
    if (!layer || !from || !to || from === to) return
    if (reducedMotion()) { flash(to, 'ag-recv', 700); return }

    const path = pathBetween(from, to)
    const onLand = () => flash(to, 'ag-recv', 700)

    for (let i = TRAIL; i >= 0; i--) {
      const trailIndex = i
      const isLeader = trailIndex === 0
      const baseScale = isLeader ? 1 : 0.88 - trailIndex * 0.14
      spawnPlane(layer, live.current, path, delayMs + trailIndex * TRAIL_GAP_MS, baseScale, onLand, isLeader)
    }
  }, [])

  return { layerRef, fly }
}

export default function PlaneLayer({ layerRef }: { layerRef: RefObject<HTMLDivElement> }) {
  return <div className="ag-planes" ref={layerRef} aria-hidden="true" />
}
