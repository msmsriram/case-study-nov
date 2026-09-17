import { useEffect, useState, type FormEvent } from 'react'
import { api, fmtDate, type CitySummary } from '../api'
import { go } from '../App'

const EXAMPLES = ['Nairobi, Kenya', 'Pune, India', 'Dakar, Senegal', 'São Paulo, Brazil', 'Ulaanbaatar, Mongolia']

export default function Home() {
  const [city, setCity] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [cities, setCities] = useState<CitySummary[] | null>(null)
  const [menuFor, setMenuFor] = useState<string | null>(null)      // step 1: three-dot menu open on this card
  const [confirm, setConfirm] = useState<CitySummary | null>(null) // step 2: confirmation dialog
  const [deleting, setDeleting] = useState(false)
  const [delErr, setDelErr] = useState<string | null>(null)

  const load = () => api.cities().then(setCities).catch(e => setErr(`Cannot reach the research service: ${e.message}`))
  useEffect(() => { load() }, [])

  useEffect(() => {                                                 // click anywhere / Escape closes the menu
    const close = () => setMenuFor(null)
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { setMenuFor(null); if (!deleting) setConfirm(null) } }
    window.addEventListener('click', close); window.addEventListener('keydown', key)
    return () => { window.removeEventListener('click', close); window.removeEventListener('keydown', key) }
  }, [deleting])

  async function start(e: FormEvent) {
    e.preventDefault()
    if (city.trim().length < 2) return
    setBusy(true); setErr(null)
    try { const r = await api.research(city.trim()); go(`/run/${r.run_id}`) }
    catch (e2) { setErr((e2 as Error).message); setBusy(false) }
  }

  async function reallyDelete() {
    if (!confirm) return
    setDeleting(true); setDelErr(null)
    try {
      await api.deleteCity(confirm.city_id)
      try { localStorage.removeItem(`c4c.conversation.${confirm.city_id}`) } catch { /* storage unavailable */ }
      setCities(cs => (cs ?? []).filter(c => c.city_id !== confirm.city_id))
      setConfirm(null)
    } catch (e2) { setDelErr((e2 as Error).message) } finally { setDeleting(false) }
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
            <div key={c.city_id} className="card pad city-card" role="link" tabIndex={0}
              onClick={() => go(`/city/${c.city_id}`)} onKeyDown={e => { if (e.key === 'Enter') go(`/city/${c.city_id}`) }}>
              <div className="row" style={{ flexWrap: 'nowrap' }}>
                <h3 className="grow" style={{ fontSize: 17 }}>{c.name}</h3>
                <span className={`badge ${c.graph_status === 'ready' ? 'ok' : 'warn'}`}>{c.graph_status === 'ready' ? 'Ask ready' : `Graph ${c.graph_status ?? 'pending'}`}</span>
                <div className="menu-wrap">
                  <button className="kebab" aria-label={`Options for ${c.name}`} aria-haspopup="menu" aria-expanded={menuFor === c.city_id}
                    onClick={e => { e.stopPropagation(); setMenuFor(m => (m === c.city_id ? null : c.city_id)) }}>⋮</button>
                  {menuFor === c.city_id && (
                    <div className="menu" role="menu" onClick={e => e.stopPropagation()}>
                      <button role="menuitem" className="menu-item danger" onClick={() => { setMenuFor(null); setDelErr(null); setConfirm(c) }}>Delete city…</button>
                    </div>
                  )}
                </div>
              </div>
              <div className="muted small">{[c.admin_region, c.country].filter(Boolean).join(' · ')}</div>
              <div className="row" style={{ marginTop: 14 }}>
                <span><b style={{ fontSize: 20, fontFamily: 'var(--serif)' }}>{c.verified_claims}</b> <span className="muted small">verified facts</span></span>
                <span className="grow" /><span className="faint small">researched {fmtDate(c.last_researched_at)}</span>
              </div>
            </div>
          ))}
        </div>
      </section>

      {confirm && (
        <div className="modal-back" onClick={() => !deleting && setConfirm(null)}>
          <div className="card modal" role="alertdialog" aria-modal="true" aria-labelledby="del-title" onClick={e => e.stopPropagation()}>
            <h2 id="del-title">Delete {confirm.name}?</h2>
            <p className="muted" style={{ margin: '10px 0 6px' }}>
              This permanently removes everything researched about {confirm.name}, {confirm.country}:
            </p>
            <ul className="small muted" style={{ margin: '0 0 14px', paddingLeft: 20 }}>
              <li>{confirm.verified_claims} verified facts, plus rejected claims, sources and gaps (relational store)</li>
              <li>its indexed facts and source passages (vector store)</li>
              <li>its entities and relationships (knowledge graph)</li>
              <li>all saved conversations about it</li>
            </ul>
            <p className="small"><b>This cannot be undone.</b> Researching the city again will start from scratch.</p>
            {delErr && <div className="notice bad" style={{ marginTop: 12 }}>{delErr}</div>}
            <div className="row" style={{ justifyContent: 'flex-end', marginTop: 18 }}>
              <button className="btn" onClick={() => setConfirm(null)} disabled={deleting} autoFocus>Cancel</button>
              <button className="btn danger" onClick={reallyDelete} disabled={deleting}>{deleting ? <><span className="spinner" /> Deleting…</> : 'Delete permanently'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
