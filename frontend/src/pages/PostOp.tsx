import { useEffect, useRef, useState } from 'react'
import { getJson, postJson, useSentinelState } from '../api'
import type { ReportSummary } from '../types'

export default function PostOp() {
  const { state: s } = useSentinelState()
  const [summary, setSummary] = useState<ReportSummary | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const frame = useRef<HTMLIFrameElement>(null)

  useEffect(() => {
    document.title = 'Sentinel-OR · Post-op report draft'
    getJson<{ reports: ReportSummary[] }>('/report')
      .then((r) => { if (r.reports.length) { setSummary(r.reports[r.reports.length - 1]); setNonce((n) => n + 1) } })
      .catch(() => undefined)
  }, [])

  const generate = () => {
    setBusy(true)
    setErr(null)
    postJson<ReportSummary>('/report/generate')
      .then((r) => { setSummary(r); setNonce((n) => n + 1) })
      .catch((e: Error) => setErr(e.message))
      .finally(() => setBusy(false))
  }

  return (
    <div className="postop">
      <header>
        <h1>Post-op report draft</h1>
        <button className="big primary" onClick={generate} disabled={busy}>
          {busy ? 'Writing…' : summary ? 'Regenerate from the log' : 'Generate from the log'}
        </button>
        <button className="big" onClick={() => frame.current?.contentWindow?.print()} disabled={!summary}>Print / save as PDF</button>
        <a className="big linkbtn" href="/report.html" download="post-op-report-draft.html">Download HTML</a>
      </header>
      <div className="secondary">
        {s ? <>Log: {s.audit.entries} entries · chain {s.audit.verified === null ? 'not checked yet' : s.audit.verified ? 'verified' : 'BROKEN'}</> : null}
        {summary && (
          <> · report covers run {summary.run}, entries {summary.covers_entries[0]} to {summary.covers_entries[1]} · summary {summary.narrative}
            {summary.narrative_ms ? ` in ${(summary.narrative_ms / 1000).toFixed(1)} s` : ''}
            {summary.video_events != null && ` · ${summary.video_events} video events`}
            {summary.review_items != null && ` · ${summary.review_items} items to review`}
            {summary.medications_to_decide != null && ` · ${summary.medications_to_decide} medications to decide`}
            {summary.codes && summary.codes.length > 0 && ` · draft codes ${summary.codes.join(', ')}`}</>
        )}
      </div>
      {err && <div className="err">{err}</div>}
      {summary ? (
        <iframe ref={frame} key={nonce} className="report-frame" title="Post-op report draft" src={`/report.html?v=${nonce}`} />
      ) : (
        <p className="muted" style={{ fontSize: 20 }}>Run a scenario, then generate the report. It is written locally from the audit log.</p>
      )}
    </div>
  )
}
