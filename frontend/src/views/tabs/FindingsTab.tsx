import { useMemo, useState } from 'react'
import { GEO_LABEL, TIER_LABEL, fmtDate, isLocal, type Claim, type Overview } from '../../api'

const GEO_ORDER = ['city', 'metro', 'district', 'state', 'national', 'global', 'unknown']

function Highlighted({ text, quote }: { text: string; quote: string }) {
  const i = text.toLowerCase().indexOf(quote.toLowerCase().slice(0, 60))
  if (i < 0) return <>{text}</>
  const end = Math.min(text.length, i + quote.length)
  return <>{text.slice(0, i)}<mark>{text.slice(i, end)}</mark>{text.slice(end)}</>
}

export function VerdictBadge({ c }: { c: Pick<Claim, 'verdict' | 'status'> }) {
  if (c.status === 'rejected_grounding') return <span className="badge bad">Quote not found in source</span>
  if (c.verdict === 'SUPPORTED') return <span className="badge ok">✓ Supported</span>
  if (c.verdict === 'PARTIALLY_SUPPORTED') return <span className="badge warn">◐ Partially supported</span>
  if (c.verdict === 'UNSUPPORTED') return <span className="badge bad">✕ Unsupported</span>
  return <span className="badge bad">{c.verdict ?? 'Not verified'}</span>
}

export function ClaimCard({ c }: { c: Claim }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="claim">
      <div className="statement">{c.statement}</div>
      <div className="meta">
        <VerdictBadge c={c} />
        <span className={`badge ${isLocal(c.geo_level) ? 'local' : 'broad'}`}>{GEO_LABEL[c.geo_level] ?? c.geo_level}</span>
        {c.geo_mismatch && <span className="badge warn" title={`The extractor labelled this "${c.extractor_geo_level}"; the fact checker corrected it.`}>geography corrected</span>}
        {c.in_conflict && <span className="badge bad">conflicts with another source</span>}
        {c.year && <span className="badge plain">{c.year}</span>}
        <span className="small muted">{TIER_LABEL[c.source.tier ?? ''] ?? ''}{c.source.publisher ? ` · ${c.source.publisher}` : ''}</span>
        <span className="grow" />
        <button className="btn ghost small" onClick={() => setOpen(o => !o)}>{open ? 'Hide evidence' : 'Where did this come from?'}</button>
      </div>
      {open && (
        <div className="evidence">
          <blockquote>“{c.quote}”</blockquote>
          {c.evidence_window && <p className="small muted" style={{ marginBottom: 12 }}><b>In context:</b> <Highlighted text={c.evidence_window} quote={c.quote} /></p>}
          <dl>
            <dt>Source</dt><dd><a href={c.source.url} target="_blank" rel="noreferrer">{c.source.title || c.source.url}</a></dd>
            <dt>Published</dt><dd>{c.source.published_date ? fmtDate(c.source.published_date) : 'date not stated by the source'}</dd>
            <dt>Retrieved</dt><dd>{fmtDate(c.source.retrieved_at)}{c.source.robots_status ? ` · robots.txt: ${c.source.robots_status.replace('_', ' ')}` : ''}</dd>
            <dt>Quote check</dt><dd>{c.quote_verified ? 'Quote located verbatim in the source text' : 'Quote could NOT be located in the source text'}</dd>
            <dt>Fact checker</dt><dd>{c.verdict_rationale ?? 'No verdict recorded'}{c.checker_confidence != null && <span className="faint"> (confidence {Math.round(c.checker_confidence * 100)}%)</span>}</dd>
            <dt>Models</dt><dd className="faint">extracted by {c.extractor_model ?? '?'} · checked independently by {c.checker_model ?? '?'}</dd>
            <dt>Knowledge graph</dt><dd className="faint">{c.in_graph ? 'Included' : 'Not included (statistics and unlinked facts stay out of the graph)'}</dd>
          </dl>
        </div>
      )}
    </div>
  )
}

export default function FindingsTab({ ov, claims, rejected }: { ov: Overview; claims: Claim[] | null; rejected: Claim[] | null }) {
  const [localOnly, setLocalOnly] = useState(false)
  const [showRejected, setShowRejected] = useState(false)
  const [q, setQ] = useState('')
  const filtered = useMemo(() => (claims ?? []).filter(c => (!localOnly || isLocal(c.geo_level)) &&
    (!q || (c.statement + ' ' + c.entities.join(' ')).toLowerCase().includes(q.toLowerCase()))), [claims, localOnly, q])

  if (!claims) return <div className="muted"><span className="spinner" /> Loading findings…</div>
  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="row">
        <input className="input" style={{ maxWidth: 340 }} placeholder="Filter findings…" value={q} onChange={e => setQ(e.target.value)} />
        <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={localOnly} onChange={e => setLocalOnly(e.target.checked)} /> Only evidence about {ov.city.name} itself</label>
        <span className="grow" /><span className="small muted">{filtered.length} of {claims.length} verified facts</span>
      </div>

      {ov.categories.map(k => {
        const cs = filtered.filter(c => c.category === k.key)
        const byGeo = GEO_ORDER.map(g => [g, cs.filter(c => c.geo_level === g)] as const).filter(([, v]) => v.length)
        return (
          <section key={k.key} className="card">
            <div className="cat-head"><h2 style={{ fontSize: 17 }}>{k.name}</h2><span className="small muted grow">{k.why}</span><span className="badge plain">{cs.length}</span></div>
            {cs.length === 0 && <div className="pad"><div className="notice">No verified evidence{localOnly ? ` specific to ${ov.city.name}` : ''} was found for this category. This is a gap, not an omission.</div></div>}
            {byGeo.map(([g, list]) => (
              <div key={g}>
                <div className="geo-head">{GEO_LABEL[g] ?? g}{!isLocal(g) && ' — context, not a fact about the city'}</div>
                {list.map(c => <ClaimCard key={c.id} c={c} />)}
              </div>
            ))}
          </section>
        )
      })}

      <section className="card">
        <div className="cat-head"><h2 style={{ fontSize: 17 }}>Claims we did not accept</h2>
          <span className="small muted grow">Extracted, then rejected. Kept for audit; never used in answers, the graph or the briefing.</span>
          <button className="btn ghost small" onClick={() => setShowRejected(s => !s)}>{showRejected ? 'Hide' : `Show ${rejected?.length ?? 0}`}</button></div>
        {showRejected && (rejected ?? []).map(c => <ClaimCard key={c.id} c={c} />)}
      </section>
    </div>
  )
}
