import { useState } from 'react'
import { postJson } from '../api'
import type { SentinelState } from '../types'

interface Verify { ok: boolean; entries: number; head_hash: string; first_bad_seq: number | null; reason: string }

/** Screen-2 panel for the tamper-evident audit log. The head hash is shown so the team can note it down. */
export default function AuditPanel({ s }: { s: SentinelState }) {
  const a = s.audit
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const verify = () => {
    setBusy(true)
    setErr(null)
    postJson<Verify>('/audit/verify').catch((e: Error) => setErr(e.message)).finally(() => setBusy(false))
  }

  const chain =
    a.verified === null ? (
      <span className="muted">not checked yet</span>
    ) : a.verified ? (
      <b className="chain-ok">verified</b>
    ) : (
      <b className="chain-bad">BROKEN</b>
    )

  return (
    <section className="panel">
      <h2>Audit log <span className="sub">tamper-evident, local</span></h2>
      {!a.enabled ? (
        <div className="muted">Audit log is off.</div>
      ) : (
        <>
          <div className="audit-grid">
            <span>Entries</span>
            <span className="mono">{a.entries}{a.last_kind ? <span className="muted"> · last: {a.last_kind.replace(/_/g, ' ')}</span> : null}</span>
            <span>Hash chain</span>
            <span>{chain}{a.verified ? <span className="muted"> · {a.verified_entries} entries re-checked from the file</span> : null}</span>
            <span>Head hash</span>
            <span className="mono" title={a.head_hash ?? ''}>{a.head_hash?.slice(0, 16)}…</span>
          </div>
          {a.verified === false && <div className="err">{a.verify_note}</div>}
          {a.status === 'degraded' && <div className="err">Log file not writable: {a.error}. Entries are kept in memory only.</div>}
          {err && <div className="err">{err}</div>}
          <div className="audit-actions">
            <button className="small-btn" onClick={verify} disabled={busy}>{busy ? 'Checking…' : 'Verify chain'}</button>
            <a className="small-btn" href="/post-op" target="_blank" rel="noreferrer">Post-op report</a>
            <a className="small-btn" href="/audit/file">Download log</a>
          </div>
        </>
      )}
    </section>
  )
}
