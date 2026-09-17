import { useState } from 'react'
import { TIER_LABEL, fmtDate, type Overview, type Source } from '../../api'

export default function SourcesTab({ ov, sources }: { ov: Overview; sources: Source[] | null }) {
  const [filter, setFilter] = useState<'all' | 'read' | 'denied'>('all')
  if (!sources) return <div className="muted"><span className="spinner" /> Loading sources…</div>
  const rows = sources.filter(s => filter === 'all' || (filter === 'read' ? s.fetch_status === 'ok' : !s.crawl_allowed))
  return (
    <div className="stack">
      <div className="notice info">
        Before reading any page we check whether the site permits automated access. We identify ourselves honestly as a research bot and
        do not disguise ourselves as a browser. {ov.counts.sources_denied} of {ov.counts.sources} sources said no; they stay on record
        so you can open them yourself.
      </div>
      <div className="row">
        {(['all', 'read', 'denied'] as const).map(f => <button key={f} className={`btn ${filter === f ? 'primary' : ''}`} onClick={() => setFilter(f)}>
          {f === 'all' ? `All ${sources.length}` : f === 'read' ? `Read ${ov.counts.sources_read}` : `Not permitted ${ov.counts.sources_denied}`}</button>)}
        <span className="grow" />
        <span className="legend">{Object.entries(ov.counts.sources_by_tier).sort((a, b) => b[1] - a[1]).map(([t, n]) => <span key={t}>{TIER_LABEL[t] ?? t} · {n}</span>)}</span>
      </div>
      <div className="card" style={{ overflowX: 'auto' }}>
        <table>
          <thead><tr><th>Source</th><th>Type</th><th>Permission to read</th><th>Read</th><th>Facts used</th></tr></thead>
          <tbody>
            {rows.map(s => (
              <tr key={s.id}>
                <td style={{ maxWidth: 420 }}><a className="t" href={s.url} target="_blank" rel="noreferrer">{s.title || s.url}</a>
                  <div className="u">{s.url.slice(0, 90)}</div>
                  <div className="small faint">{s.published_date ? `published ${fmtDate(s.published_date)}` : 'no publication date'} · found via {s.provider}</div></td>
                <td><span className="badge plain">{TIER_LABEL[s.tier] ?? s.tier}</span></td>
                <td>{s.crawl_allowed ? <span className="badge ok">Permitted</span> : <span className="badge bad">Not permitted</span>}
                  <div className="small muted" style={{ marginTop: 4 }}>{s.crawl_allowed ? `robots.txt: ${(s.robots_status ?? '').replace('_', ' ')}` : s.crawl_reason}</div></td>
                <td className="small">{s.fetch_status === 'ok' ? <>{s.word_count.toLocaleString()} words<div className="faint">extraction: {s.extraction_quality}</div></>
                  : s.fetch_status ? <span className="muted">{s.fetch_status}</span> : <span className="faint">—</span>}</td>
                <td>{s.verified_claims > 0 ? <b>{s.verified_claims}</b> : <span className="faint">0</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
