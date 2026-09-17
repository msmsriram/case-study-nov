import { useEffect, useState, type FormEvent } from 'react'
import { api, fmtDate, type CitySummary } from '../api'
import { go } from '../App'

const EXAMPLES = ['Nairobi, Kenya', 'Pune, India', 'Dakar, Senegal', 'São Paulo, Brazil', 'Ulaanbaatar, Mongolia']

export default function Home() {
  const [city, setCity] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [cities, setCities] = useState<CitySummary[] | null>(null)

  useEffect(() => { api.cities().then(setCities).catch(e => setErr(`Cannot reach the research service: ${e.message}`)) }, [])

  async function start(e: FormEvent) {
    e.preventDefault()
    if (city.trim().length < 2) return
    setBusy(true); setErr(null)
    try { const r = await api.research(city.trim()); go(`/run/${r.run_id}`) }
    catch (e2) { setErr((e2 as Error).message); setBusy(false) }
  }

  return (
    <div className="stack" style={{ gap: 28 }}>
      <section className="card hero">
        <h1>Understand a city before you walk into the room.</h1>
        <p className="lede">
          Name any city. We research the public web live, check that each source permits reading, extract claims with
          verbatim quotes, have a second model try to disprove them, and tell you plainly what is known, what is only
          national data, and what nobody seems to know.
        </p>
        <form onSubmit={start}>
          <input className="input" placeholder="City, country — e.g. Kisumu, Kenya" value={city} onChange={e => setCity(e.target.value)} autoFocus />
          <button className="btn primary" disabled={busy || city.trim().length < 2}>{busy ? 'Starting…' : 'Research city'}</button>
        </form>
        <div className="chips">
          <span className="small faint" style={{ alignSelf: 'center' }}>Try</span>
          {EXAMPLES.map(x => <button key={x} type="button" className="chip" onClick={() => setCity(x)}>{x}</button>)}
        </div>
        {err && <div className="notice bad" style={{ marginTop: 16 }}>{err}</div>}
      </section>

      <section className="stack">
        <div className="row"><h2>City intelligence library</h2><span className="muted small">Researched once, reusable by every team</span></div>
        {cities === null && !err && <div className="muted"><span className="spinner" /> Loading…</div>}
        {cities?.length === 0 && <div className="card pad muted">No cities researched yet. Start with one above; a typical run takes two to three minutes.</div>}
        <div className="grid g3">
          {cities?.map(c => (
            <a key={c.city_id} className="card pad" href={`#/city/${c.city_id}`} style={{ color: 'inherit', textDecoration: 'none' }}>
              <div className="row"><h3 className="grow" style={{ fontSize: 17 }}>{c.name}</h3>
                <span className={`badge ${c.graph_status === 'ready' ? 'ok' : 'warn'}`}>{c.graph_status === 'ready' ? 'Ask ready' : `Graph ${c.graph_status ?? 'pending'}`}</span></div>
              <div className="muted small">{[c.admin_region, c.country].filter(Boolean).join(' · ')}</div>
              <div className="row" style={{ marginTop: 14 }}>
                <span><b style={{ fontSize: 20, fontFamily: 'var(--serif)' }}>{c.verified_claims}</b> <span className="muted small">verified facts</span></span>
                <span className="grow" /><span className="faint small">researched {fmtDate(c.last_researched_at)}</span>
              </div>
            </a>
          ))}
        </div>
      </section>
    </div>
  )
}
