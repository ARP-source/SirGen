import { useCallback, useEffect, useRef, type RefObject } from 'react'

/** User-supplied paper plane art (nose points up in the PNG). We rotate by heading + 90° so the tip leads. */

export const FOLD_MS = 200
export const FLIGHT_MS = 1750
const TRAIL_PLANES = 3
const TRAIL_GAP_MS = 180
const DOTS = 7
/** PNG nose is up (−Y); atan2 heading 0 = east (+X) → rotate 90° clockwise. */
const HEADING_OFFSET = 90

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

function depthScale(t: number, base: number): number {
  return base * (0.68 + 0.42 * Math.sin(Math.PI * t))
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

function pointOnPath(path: Path, t: number): { x: number; y: number; rot: number } {
  const { a, b, mx, my, heading, bow } = path
  let x: number
  let y: number
  let wobble = 0
  if (t <= 0.5) {
    const u = t / 0.5
    const e = 1 - (1 - u) ** 2
    x = a.x + (mx - a.x) * e
    y = a.y + (my - a.y) * e
    wobble = -8 * Math.sign(bow) * u
  } else {
    const u = (t - 0.5) / 0.5
    const e = u * u
    x = mx + (b.x - mx) * e
    y = my + (b.y - my) * e
    wobble = -8 * Math.sign(bow) * (1 - u)
  }
  return { x, y, rot: heading + HEADING_OFFSET + wobble }
}

function frame(path: Path, t: number, baseScale: number): string {
  const { x, y, rot } = pointOnPath(path, t)
  const s = depthScale(t, baseScale)
  return `translate(${x}px, ${y}px) translate(-50%, -50%) rotate(${rot}deg) scale(${s.toFixed(3)})`
}

function flightKeyframes(path: Path, baseScale: number): Keyframe[] {
  const steps = [0, 0.1, 0.35, 0.5, 0.72, 0.92, 1]
  return steps.map((t) => ({
    offset: t,
    transform: frame(path, t, baseScale),
    opacity: t >= 0.96 ? 0.15 : 1,
  }))
}

function addDots(body: HTMLDivElement): void {
  const trail = document.createElement('div')
  trail.className = 'ag-plane-dots'
  for (let i = 0; i < DOTS; i++) {
    const d = document.createElement('i')
    d.style.setProperty('--i', String(i))
    trail.appendChild(d)
  }
  body.appendChild(trail)
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
  const body = document.createElement('div')
  body.className = 'ag-plane-body'
  const img = document.createElement('img')
  img.src = '/paper-plane.png'
  img.alt = ''
  img.className = 'ag-plane-img'
  img.draggable = false
  body.appendChild(img)
  addDots(body)
  el.appendChild(body)
  el.style.transform = frame(path, 0, baseScale * 0.85)
  layer.appendChild(el)
  live.add(el)

  const start = delayMs + FOLD_MS
  window.setTimeout(() => body.classList.add('visible'), delayMs)

  const anim = el.animate(flightKeyframes(path, baseScale), {
    duration: FLIGHT_MS,
    delay: start,
    easing: 'cubic-bezier(0.38, 0, 0.2, 1)',
    fill: 'forwards',
  })

  const cleanup = () => {
    el.remove()
    live.delete(el)
    if (isLeader) onLeaderLand()
  }
  anim.onfinish = cleanup
  anim.oncancel = cleanup
  window.setTimeout(() => { if (live.has(el)) cleanup() }, start + FLIGHT_MS + 500)
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

    for (let i = TRAIL_PLANES; i >= 0; i--) {
      const isLeader = i === 0
      const baseScale = isLeader ? 1 : 0.9 - i * 0.12
      spawnPlane(layer, live.current, path, delayMs + i * TRAIL_GAP_MS, baseScale, onLand, isLeader)
    }
  }, [])

  return { layerRef, fly }
}

export default function PlaneLayer({ layerRef }: { layerRef: RefObject<HTMLDivElement> }) {
  return <div className="ag-planes" ref={layerRef} aria-hidden="true" />
}
