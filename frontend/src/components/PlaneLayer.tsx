import { useCallback, useEffect, useRef, type RefObject } from 'react'

/**
 * Paper-plane messages. A plane is spawned only by the page when a real validated message arrived
 * (see AgentsPage: message identity changed). The layer is fixed and pointer-transparent; each plane
 * is a plain div whose inner sheet folds (clip-path keyframe) and whose outer element flies with the
 * Web Animations API from the source card to the target card, then is removed.
 */

export const FOLD_MS = 420
export const FLIGHT_MS = 1000
const REMOVE_MS = FOLD_MS + FLIGHT_MS + 80

const reducedMotion = (): boolean =>
  typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

/** Where a message is written on a card: the paper, if it is showing, else the icon area, else the card centre. */
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

    const a = originOf(from)
    const b = targetOf(to)
    const dx = b.x - a.x
    const dy = b.y - a.y
    const dist = Math.hypot(dx, dy)
    const heading = (Math.atan2(dy, dx) * 180) / Math.PI
    // arc: bow the path away from the straight line, more for longer hops
    const bow = Math.min(90, 24 + dist * 0.12) * (dx >= 0 ? -1 : 1)
    const nx = -dy / (dist || 1)
    const ny = dx / (dist || 1)
    const mx = a.x + dx / 2 + nx * bow
    const my = a.y + dy / 2 + ny * bow

    const el = document.createElement('div')
    el.className = 'ag-plane'
    const sheet = document.createElement('div')
    sheet.className = 'ag-sheet'
    sheet.style.animationDelay = `${delayMs}ms`
    el.appendChild(sheet)
    // the plane element is positioned at its centre via translate; keep everything on the compositor
    el.style.transform = `translate(${a.x}px, ${a.y}px) translate(-50%, -50%)`
    layer.appendChild(el)
    live.current.add(el)

    const anim = el.animate(
      [
        { transform: `translate(${a.x}px, ${a.y}px) translate(-50%, -50%) rotate(0deg) scale(1)`, opacity: 1, offset: 0 },
        { transform: `translate(${a.x}px, ${a.y}px) translate(-50%, -50%) rotate(${heading}deg) scale(0.9)`, opacity: 1, offset: 0.12 },
        { transform: `translate(${mx}px, ${my}px) translate(-50%, -50%) rotate(${heading - 10 * Math.sign(bow)}deg) scale(0.8)`, opacity: 1, offset: 0.6 },
        { transform: `translate(${b.x}px, ${b.y}px) translate(-50%, -50%) rotate(${heading}deg) scale(0.55)`, opacity: 0.15, offset: 1 },
      ],
      { duration: FLIGHT_MS, delay: delayMs + FOLD_MS, easing: 'cubic-bezier(0.45, 0.05, 0.3, 1)', fill: 'forwards' },
    )
    const done = () => { el.remove(); live.current.delete(el); flash(to, 'ag-recv', 700) }
    anim.onfinish = done
    anim.oncancel = () => { el.remove(); live.current.delete(el) }
    window.setTimeout(() => { if (live.current.has(el)) done() }, delayMs + REMOVE_MS + 200)
  }, [])

  return { layerRef, fly }
}

export default function PlaneLayer({ layerRef }: { layerRef: RefObject<HTMLDivElement> }) {
  return <div className="ag-planes" ref={layerRef} aria-hidden="true" />
}
