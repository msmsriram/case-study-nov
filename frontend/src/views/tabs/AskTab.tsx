import { useMemo, useState, type FormEvent } from 'react'
import { marked } from 'marked'
import { api, GEO_LABEL, TIER_LABEL, isLocal, type Answer, type Evidence } from '../../api'

const KIND: Record<Evidence['kind'], string> = { graph_fact: 'Knowledge graph', claim: 'Verified fact', passage: 'Source passage' }

function AnswerBody({ a, active, onCite }: { a: Answer; active: number | null; onCite: (n: number) => void }) {
  const html = useMemo(() => {
    const md = a.answer.replace(/\[E(\d+)\]/g, (_, n) => `<button class="cite" data-n="${n}">${n}</button>`)
    return marked.parse(md, { async: false }) as string
  }, [a.answer])
  return <div className="answer" data-active={active ?? ''} dangerouslySetInnerHTML={{ __html: html }}
    onClick={e => { const n = (e.target as HTMLElement).dataset?.n; if (n) onCite(Number(n)) }} />
}

export default function AskTab({ cityId, cityName, ready, status }: { cityId: string; cityName: string; ready: boolean; status: string }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [history, setHistory] = useState<Answer[]>([])
  const [active, setActive] = useState<number | null>(null)
  const current = history[0]
  const suggestions = [
    `Which organisations run hypertension or diabetes programmes in ${cityName}, and how are they connected?`,
    `What do we know about hypertension prevalence, and is any of it specific to ${cityName}?`,
    `What policies affect cardiovascular risk here?`, `What are the biggest gaps I should ask the health department about?`,
  ]

  async function ask(e: FormEvent | null, text?: string) {
    e?.preventDefault()
    const question = (text ?? q).trim()
    if (question.length < 3) return
    setBusy(true); setErr(null); setActive(null)
    try { const a = await api.ask(cityId, question); setHistory(h => [a, ...h]); setQ('') }
    catch (e2) { setErr((e2 as Error).message) } finally { setBusy(false) }
  }

  if (!ready) return (
    <div className="notice">
      <b>Questions open when the knowledge graph is ready</b> (currently: {status}). Every answer draws on the graph as well as the
      verified facts and source passages, so we wait for it rather than answer without it. Findings, sources and the briefing are available now.
    </div>
  )

  return (
    <div className="stack">
      <form className="row" onSubmit={ask}>
        <input className="input grow" placeholder={`Ask anything about ${cityName}…`} value={q} onChange={e => setQ(e.target.value)} />
        <button className="btn primary" disabled={busy || q.trim().length < 3}>{busy ? <><span className="spinner" /> Searching evidence…</> : 'Ask'}</button>
      </form>
      {!current && <div className="chips">{suggestions.map(s => <button key={s} className="chip" onClick={() => ask(null, s)} disabled={busy}>{s}</button>)}</div>}
      {err && <div className="notice bad">{err}</div>}

      {current && (
        <div className="chat">
          <div className="card pad stack">
            <div className="small muted">{current.question}</div>
            {current.insufficient_evidence && <div className="notice">The stored evidence does not answer this. We say so rather than guess.</div>}
            <AnswerBody a={current} active={active} onCite={setActive} />
            <div className="row">
              <span className={`badge ${current.confidence === 'high' ? 'ok' : current.confidence === 'medium' ? 'warn' : 'bad'}`}>{current.confidence} confidence</span>
              <span className="small faint">drew on: {current.stores_used_in_answer.map(k => KIND[k as Evidence['kind']]).join(', ') || 'nothing'} ·
                retrieved {current.retrieval.graph_facts} graph facts, {current.retrieval.claims} verified facts, {current.retrieval.passages} passages · {current.timings.total_s}s</span>
            </div>
            {current.caveats.length > 0 && <div className="stack" style={{ gap: 4 }}>{current.caveats.map((c, i) => <div key={i} className="small muted">⚠ {c}</div>)}</div>}
            {current.gaps.length > 0 && <div className="stack" style={{ gap: 4 }}><h3>Related gaps on record</h3>{current.gaps.slice(0, 4).map((g, i) => <div key={i} className="small muted">· {g.description}</div>)}</div>}
          </div>

          <div className="stack">
            <div className="row"><h3 className="grow">Evidence</h3><span className="small faint">click a number in the answer</span></div>
            {current.evidence.map(e => {
              const cited = current.cited.includes(e.n)
              return (
                <div key={e.n} className={`ev ${active === e.n ? 'on' : ''} ${!cited ? 'dim' : ''}`} onMouseEnter={() => cited && setActive(e.n)}>
                  <div className="row" style={{ gap: 6 }}>
                    <span className="n">{e.n}</span><span className="badge plain">{KIND[e.kind]}</span>
                    {e.kind === 'claim' && <span className={`badge ${e.verdict === 'SUPPORTED' ? 'ok' : 'warn'}`}>{e.verdict === 'SUPPORTED' ? 'Supported' : 'Partially supported'}</span>}
                    {e.kind === 'passage' && <span className="badge warn">not fact-checked</span>}
                    {e.geo_level && <span className={`badge ${isLocal(e.geo_level) ? 'local' : 'broad'}`}>{GEO_LABEL[e.geo_level]}</span>}
                    {!cited && <span className="small faint">retrieved, not used</span>}
                  </div>
                  <div style={{ margin: '8px 0' }}>{e.text.length > 320 ? e.text.slice(0, 320) + '…' : e.text}</div>
                  {e.relation && <div className="small faint">{e.relation} · backed by {e.backing_claim_ids?.length ?? 0} fact-checked claims</div>}
                  {e.quote && <div className="small muted" style={{ fontStyle: 'italic' }}>“{e.quote.slice(0, 200)}”</div>}
                  <div className="small" style={{ marginTop: 6 }}>{e.source_urls.map(u => <div key={u}><a href={u} target="_blank" rel="noreferrer">{e.source_title || u.slice(0, 70)}</a>
                    {e.source_tier && <span className="faint"> · {TIER_LABEL[e.source_tier] ?? e.source_tier}</span>}</div>)}</div>
                </div>
              )
            })}
          </div>
        </div>
      )}
      {history.length > 1 && <div className="stack"><h3>Earlier questions</h3>{history.slice(1).map((h, i) =>
        <button key={i} className="chip" style={{ textAlign: 'left' }} onClick={() => setHistory(x => [h, ...x.filter(y => y !== h)])}>{h.question}</button>)}</div>}
    </div>
  )
}
