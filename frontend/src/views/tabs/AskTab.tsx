import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { marked } from 'marked'
import { api, GEO_LABEL, TIER_LABEL, isLocal, type Answer, type ConversationSummary, type Evidence } from '../../api'

const KIND: Record<Evidence['kind'], string> = { graph_fact: 'Knowledge graph', claim: 'Verified fact', passage: 'Source passage' }

// tolerate citation styles in answers stored before server-side normalisation
const canon = (t: string) => t.replace(/【/g, '[').replace(/】/g, ']')
const citedOf = (a: Answer) => (a.cited.length ? a.cited : [...canon(a.answer).matchAll(/\[E(\d+)\]/g)].map(m => Number(m[1])))

function AnswerBody({ a, onCite }: { a: Answer; onCite: (n: number) => void }) {
  const html = useMemo(() => {
    const md = canon(a.answer).replace(/\[E(\d+)\]/g, (_, n) => `<button class="cite" data-n="${n}">${n}</button>`)
    return marked.parse(md, { async: false }) as string
  }, [a.answer])
  return <div className="answer" dangerouslySetInnerHTML={{ __html: html }}
    onClick={e => { const n = (e.target as HTMLElement).dataset?.n; if (n) onCite(Number(n)) }} />
}

export default function AskTab({ cityId, cityName, ready, status }: { cityId: string; cityName: string; ready: boolean; status: string }) {
  const storeKey = `c4c.conversation.${cityId}`
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [thread, setThread] = useState<Answer[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [focus, setFocus] = useState<number>(-1)            // which turn's evidence is shown
  const [active, setActive] = useState<number | null>(null) // highlighted evidence number
  const [past, setPast] = useState<ConversationSummary[]>([])
  const endRef = useRef<HTMLDivElement>(null)

  const loadPast = () => api.conversations(cityId).then(setPast).catch(() => undefined)

  async function open(cid: string) {
    try {
      const c = await api.conversation(cid)
      const answers = c.messages.filter(m => m.role === 'assistant' && m.answer).map(m => m.answer as Answer)
      setThread(answers); setConversationId(cid); setFocus(answers.length - 1); setActive(null)
      try { localStorage.setItem(storeKey, cid) } catch { /* storage unavailable */ }
    } catch { setConversationId(null); setThread([]) }
  }

  useEffect(() => {
    if (!ready) return
    loadPast()
    let saved: string | null = null
    try { saved = localStorage.getItem(storeKey) } catch { /* storage unavailable */ }
    if (saved) open(saved)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cityId, ready])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [thread.length, pending])

  function reset() {
    setThread([]); setConversationId(null); setFocus(-1); setActive(null); setErr(null)
    try { localStorage.removeItem(storeKey) } catch { /* storage unavailable */ }
  }

  async function ask(e: FormEvent | null, text?: string) {
    e?.preventDefault()
    const question = (text ?? q).trim()
    if (question.length < 3 || busy) return
    setBusy(true); setErr(null); setActive(null); setPending(question); setQ('')
    try {
      const a = await api.ask(cityId, question, conversationId)
      setThread(t => { setFocus(t.length); return [...t, a] })
      setConversationId(a.conversation_id)
      try { localStorage.setItem(storeKey, a.conversation_id) } catch { /* storage unavailable */ }
      loadPast()
    } catch (e2) { setErr((e2 as Error).message); setQ(question) } finally { setBusy(false); setPending(null) }
  }

  if (!ready) return (
    <div className="notice">
      <b>Questions open when the knowledge graph is ready</b> (currently: {status}). Every answer draws on the graph as well as the
      verified facts and source passages, so we wait for it rather than answer without it. Findings, sources and the briefing are available now.
    </div>
  )

  const suggestions = thread.length === 0 ? [
    `Which organisations run hypertension or diabetes programmes in ${cityName}, and how are they connected?`,
    `What do we know about hypertension prevalence, and is any of it specific to ${cityName}?`,
    'What policies affect cardiovascular risk here?', 'What are the biggest gaps I should ask the health department about?',
  ] : ['Who are its partners?', 'How recent is that evidence?', 'Is any of that specific to the city itself?']
  const shown = thread[focus]

  return (
    <div className="chat">
      <div className="stack">
        <div className="card pad stack" style={{ minHeight: 320 }}>
          <div className="row"><h3 className="grow">{thread.length ? `Conversation · ${thread.length} ${thread.length === 1 ? 'turn' : 'turns'}` : `Ask about ${cityName}`}</h3>
            {thread.length > 0 && <button className="btn ghost small" onClick={reset}>New conversation</button>}</div>
          {thread.length === 0 && !pending && <p className="muted small">Ask follow-up questions naturally. The conversation is remembered so “it” and “that programme” are understood, but evidence is retrieved fresh for every answer: nothing is answered from memory.</p>}

          {thread.map((a, i) => (
            <div key={i} className="stack" style={{ gap: 8, borderTop: i ? '1px solid var(--line)' : 0, paddingTop: i ? 14 : 0 }} onClick={() => setFocus(i)}>
              <div className="row" style={{ justifyContent: 'flex-end' }}><div style={{ background: 'var(--brand-soft)', padding: '8px 14px', borderRadius: '14px 14px 2px 14px', maxWidth: '85%' }}>{a.question}</div></div>
              {a.rewritten && <div className="small faint" style={{ textAlign: 'right' }}>understood as: “{a.resolved_question}”</div>}
              {a.insufficient_evidence && <div className="notice">The stored evidence does not answer this. We say so rather than guess.</div>}
              <AnswerBody a={a} onCite={n => { setFocus(i); setActive(n) }} />
              <div className="row">
                <span className={`badge ${a.confidence === 'high' ? 'ok' : a.confidence === 'medium' ? 'warn' : 'bad'}`}>{a.confidence} confidence</span>
                <span className="small faint">drew on: {a.stores_used_in_answer.map(k => KIND[k as Evidence['kind']]).join(', ') || 'no evidence'} · {a.timings.total_s}s</span>
                {focus !== i && <button className="btn ghost small" onClick={() => setFocus(i)}>show evidence</button>}
              </div>
              {a.caveats.map((c, k) => <div key={k} className="small muted">⚠ {c}</div>)}
              {a.gaps.length > 0 && <div className="small muted">Related gaps on record: {a.gaps.slice(0, 3).map(g => g.description).join(' · ')}</div>}
            </div>
          ))}
          {pending && <div className="stack" style={{ gap: 8, borderTop: thread.length ? '1px solid var(--line)' : 0, paddingTop: 14 }}>
            <div className="row" style={{ justifyContent: 'flex-end' }}><div style={{ background: 'var(--brand-soft)', padding: '8px 14px', borderRadius: '14px 14px 2px 14px' }}>{pending}</div></div>
            <div className="muted small"><span className="spinner" /> Searching the knowledge graph, verified facts and source passages…</div></div>}
          <div ref={endRef} />
          {err && <div className="notice bad">{err}</div>}

          <form className="row" onSubmit={ask} style={{ marginTop: 'auto' }}>
            <input className="input grow" placeholder={thread.length ? 'Ask a follow-up…' : `Ask anything about ${cityName}…`} value={q} onChange={e => setQ(e.target.value)} disabled={busy} />
            <button className="btn primary" disabled={busy || q.trim().length < 3}>Ask</button>
          </form>
          <div className="chips">{suggestions.map(s => <button key={s} className="chip" onClick={() => ask(null, s)} disabled={busy}>{s}</button>)}</div>
        </div>

        {past.filter(p => p.conversation_id !== conversationId).length > 0 && (
          <div className="card pad stack"><h3>Earlier conversations</h3>
            {past.filter(p => p.conversation_id !== conversationId).slice(0, 6).map(p => (
              <button key={p.conversation_id} className="chip" style={{ textAlign: 'left', borderRadius: 10 }} onClick={() => open(p.conversation_id)}>
                {p.title} <span className="faint">· {p.turns} {p.turns === 1 ? 'turn' : 'turns'}</span></button>))}
          </div>)}
      </div>

      <div className="stack">
        <div className="row"><h3 className="grow">Evidence{shown ? ` for turn ${focus + 1}` : ''}</h3>
          {shown && <span className="small faint">{shown.retrieval.graph_facts} graph · {shown.retrieval.claims} facts · {shown.retrieval.passages} passages retrieved</span>}</div>
        {!shown && <div className="card pad muted small">The evidence behind each answer appears here. Click a citation number to jump to its source.</div>}
        {shown?.evidence.map(e => {
          const cited = citedOf(shown).includes(e.n)
          return (
            <div key={e.n} className={`ev ${active === e.n ? 'on' : ''} ${!cited ? 'dim' : ''}`} onMouseEnter={() => cited && setActive(e.n)}
              ref={el => { if (el && active === e.n) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }}>
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
  )
}
