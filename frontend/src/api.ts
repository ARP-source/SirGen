import { useEffect, useRef, useState } from 'react'
import type { SentinelState } from './types'

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`)
  return (await res.json()) as T
}

export async function postControl(path: string): Promise<unknown> {
  const res = await fetch(`/control/${path}`, { method: 'POST', headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`/control/${path}: HTTP ${res.status}`)
  return res.json()
}

export async function postJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: 'POST', headers: { Accept: 'application/json' } })
  if (!res.ok) {
    let detail = ''
    try { detail = (await res.json()).detail ?? '' } catch { /* not JSON */ }
    throw new Error(`${path}: HTTP ${res.status}${detail ? ` ${detail}` : ''}`)
  }
  return (await res.json()) as T
}

export type LinkStatus = 'connecting' | 'live' | 'lost'

/** Live state from WS /ws/state with reconnect (backoff up to 5 s). */
export function useSentinelState(): { state: SentinelState | null; link: LinkStatus } {
  const [state, setState] = useState<SentinelState | null>(null)
  const [link, setLink] = useState<LinkStatus>('connecting')
  const retry = useRef(0)

  useEffect(() => {
    let ws: WebSocket | null = null
    let timer: number | undefined
    let closed = false
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/state`)
      ws.onopen = () => {
        retry.current = 0
        setLink('live')
      }
      ws.onmessage = (msg) => setState(JSON.parse(msg.data as string) as SentinelState)
      ws.onclose = () => {
        if (closed) return
        setLink('lost')
        const delay = Math.min(5000, 500 * 2 ** retry.current++)
        timer = window.setTimeout(connect, delay)
      }
    }
    connect()
    return () => {
      closed = true
      window.clearTimeout(timer)
      ws?.close()
    }
  }, [])

  return { state, link }
}

export const fmt = (v: number | null | undefined, digits = 0): string =>
  v === null || v === undefined ? '--' : v.toFixed(digits)

export const fmtT = (t: number | null | undefined): string =>
  t === null || t === undefined ? 'time not recorded' : `t ${t.toFixed(1)} s`
