import { useEffect, useState } from 'react'
import { api, fmtDate, type Claim, type Overview, type Source } from '../api'
import OverviewTab from './tabs/OverviewTab'
import FindingsTab from './tabs/FindingsTab'
import SourcesTab from './tabs/SourcesTab'
import GraphTab from './tabs/GraphTab'
import AskTab from './tabs/AskTab'
import MethodTab from './tabs/MethodTab'

export default function CityView({ cityId, tab }: { cityId: string; tab: string }) {
  const [ov, setOv] = useState<Overview | null>(null)
  const [claims, setClaims] = useState<Claim[] | null>(null)
  const [rejected, setRejected] = useState<Claim[] | null>(null)
  const [sources, setSources] = useState<Source[] | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setOv(null); setClaims(null); setSources(null); setRejected(null); setErr(null)
    api.overview(cityId).then(setOv).catch(e => setErr(e.message))
    api.claims(cityId).then(setClaims).catch(() => setClaims([]))
    api.claims(cityId, 'not_accepted').then(setRejected).catch(() => setRejected([]))
    api.sources(cityId).then(setSources).catch(() => setSources([]))
  }, [cityId])

  // keep polling while the graph is still building so "Ask" unlocks by itself
  useEffect(() => {
    if (!ov || ov.run?.graph_status === 'ready' || ov.run?.graph_status === 'failed' || ov.run?.graph_status === 'skipped') return
    const t = setInterval(() => api.overview(cityId).then(setOv).catch(() => undefined), 8000)
    return () => clearInterval(t)
  }, [ov, cityId])

  if (err) return <div className="notice bad">{err} <a href="#/">Back to the library</a></div>
  if (!ov) return <div className="muted"><span className="spinner" /> Loading the city briefing…</div>

  const graphReady = ov.run?.graph_status === 'ready'
  const tabs: [string, string, number | null][] = [
    ['overview', 'Overview', null], ['findings', 'Findings', ov.counts.verified], ['sources', 'Sources', ov.counts.sources],
    ['graph', 'Knowledge graph', null], ['ask', 'Ask the city', null], ['method', 'How this was made', null],
  ]

  return (
    <div>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div className="grow">
          <a className="small" href="#/">← Library</a>
          <h1 style={{ marginTop: 6 }}>{ov.city.name}</h1>
          <div className="muted">{[ov.city.admin_region, ov.city.country].filter(Boolean).join(' · ')} · researched {fmtDate(ov.city.last_researched_at)}</div>
        </div>
        <a className="btn" href={api.reportUrl(cityId, 'html')}>Download briefing (HTML)</a>
        <a className="btn" href={api.reportUrl(cityId, 'md')}>Markdown</a>
      </div>

      <nav className="tabs">
        {tabs.map(([k, label, n]) => (
          <a key={k} className={`tab ${tab === k ? 'on' : ''}`} href={`#/city/${cityId}/${k}`} style={{ textDecoration: 'none' }}>
            {label}{n !== null && <span className="count">{n}</span>}
            {k === 'ask' && !graphReady && <span className="count">· building</span>}
          </a>
        ))}
      </nav>

      {tab === 'overview' && <OverviewTab ov={ov} cityId={cityId} />}
      {tab === 'findings' && <FindingsTab ov={ov} claims={claims} rejected={rejected} />}
      {tab === 'sources' && <SourcesTab ov={ov} sources={sources} />}
      {tab === 'graph' && <GraphTab cityId={cityId} ready={graphReady} />}
      {tab === 'ask' && <AskTab cityId={cityId} cityName={ov.city.name} ready={graphReady} status={ov.run?.graph_status ?? 'pending'} />}
      {tab === 'method' && <MethodTab ov={ov} />}
    </div>
  )
}
