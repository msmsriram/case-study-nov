import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type RunEvent } from '../api'

const STEPS: { key: string; title: string; what: string }[] = [
  { key: 'planning', title: 'Plan the research', what: 'Resolve the city, choose six intelligence categories, write targeted queries' },
  { key: 'searching', title: 'Search the live web', what: 'Every run searches now; nothing is pre-loaded' },
  { key: 'checking_sources', title: 'Check each source permits reading', what: 'robots.txt, platform terms, access probes — before anything is fetched' },
  { key: 'extracting', title: 'Read sources and extract claims', what: 'Each claim must carry a verbatim quote found in the source' },
  { key: 'verifying', title: 'Independent fact check', what: 'A different model tries to disprove every claim' },
  { key: 'analysing_gaps', title: 'Find conflicts and gaps', what: 'What disagrees, what is only national, what is missing' },
  { key: 'storing', title: 'Save findings', what: 'Audit trail and evidence index' },
  { key: 'building_graph', title: 'Build the knowledge graph', what: 'Organisations, programmes and policies and how they connect' },
]

export default function RunView({ runId }: { runId: string }) {
  const [events, setEvents] = useState<RunEvent[]>([])
  const [lost, setLost] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const es = new EventSource(api.eventsUrl(runId))
    es.onmessage = m => {
      const e = JSON.parse(m.data) as RunEvent
      setEvents(prev => [...prev, e])
      if (e.type === 'complete' || e.type === 'failed') es.close()
    }
    es.onerror = () => { setLost(true); es.close() }
    return () => es.close()
  }, [runId])

  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }) }, [events.length])

  const { stage, lastMsg, cityId, findingsReady, final, failed } = useMemo(() => {
    let stage = 'planning'; const lastMsg: Record<string, string> = {}
    let cityId: string | undefined; let findingsReady = false; let final: RunEvent | undefined; let failed: RunEvent | undefined
    for (const e of events) {
      if (e.stage) { stage = e.stage; if (e.message) lastMsg[e.stage] = e.message }
      if (e.city_id) cityId = e.city_id
      if (e.findings_ready) findingsReady = true
      if (e.type === 'complete') final = e
      if (e.type === 'failed') failed = e
    }
    return { stage, lastMsg, cityId, findingsReady, final, failed }
  }, [events])

  const idx = final ? STEPS.length : STEPS.findIndex(s => s.key === stage)
  const elapsed = events.length ? events[events.length - 1].t : 0

  return (
    <div className="grid g2" style={{ alignItems: 'start' }}>
      <section className="card pad stack">
        <div className="row"><h2 className="grow">{final ? 'Research complete' : failed ? 'Research failed' : 'Researching…'}</h2>
          <span className="muted small">{Math.round(elapsed)}s</span></div>
        <div className="steps">
          {STEPS.map((s, i) => {
            const cls = i < idx ? 'done' : i === idx && !failed ? 'active' : 'todo'
            return (
              <div key={s.key} className={`step ${cls}`}>
                <div className="dot">{cls === 'done' ? '✓' : ''}</div>
                <div><div className="title">{s.title}</div><div className="msg">{lastMsg[s.key] ?? s.what}</div></div>
              </div>
            )
          })}
        </div>
        {failed && <div className="notice bad">{failed.message}</div>}
        {lost && !final && !failed && <div className="notice">Connection to the run was lost. The run may still be finishing; check the library on the home page.</div>}
        {findingsReady && cityId && (
          <div className="row">
            <a className="btn primary" href={`#/city/${cityId}`}>{final ? 'Open the city briefing' : 'View findings now'}</a>
            {!final && <span className="muted small">Findings are ready. The knowledge graph keeps building; questions open when it finishes.</span>}
          </div>
        )}
      </section>

      <section className="card pad stack">
        <h3>Activity</h3>
        <div className="log" ref={logRef}>
          {events.filter(e => e.message).map((e, i) => (
            <div key={i}><span className="t">{e.t.toFixed(0)}s</span>{e.message}
              {e.denied_reasons && Object.entries(e.denied_reasons).map(([r, n]) => <div key={r} style={{ paddingLeft: 54 }}>· {n} × {r}</div>)}
            </div>
          ))}
        </div>
        <p className="small faint">Sources we are not permitted to read are kept on record as discovered, never extracted.</p>
      </section>
    </div>
  )
}
