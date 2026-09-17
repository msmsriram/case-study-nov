import type { Overview } from '../../api'

const FLOW: { node: string; title: string; what: string; kind: 'agent' | 'gate' | 'code' | 'store' }[] = [
  { node: 'plan', title: 'Research Planner', kind: 'agent', what: 'Resolves the city and writes targeted queries for six categories. Records its assumptions.' },
  { node: 'search', title: 'Live Search', kind: 'code', what: 'Searches the public web at request time. Nothing is pre-seeded.' },
  { node: 'crawl_check', title: 'Crawlability Agent', kind: 'gate', what: 'Platform terms, robots.txt (RFC 9309) and an access probe decide whether a source may be read, before any fetch.' },
  { node: 'fetch', title: 'Polite Reader', kind: 'code', what: 'Reads permitted pages and PDFs, rate-limited per site, official sources first.' },
  { node: 'extract_claims', title: 'Claim Extractor', kind: 'agent', what: 'One task per document. Every claim needs a verbatim quote; code then checks the quote exists in the source.' },
  { node: 'fact_check', title: 'Independent Fact Checker', kind: 'gate', what: 'A different model that only sees the claim and the source excerpt. Unsupported claims stop here.' },
  { node: 'detect_conflicts', title: 'Conflict Detector', kind: 'agent', what: 'Compares statistics across sources and reports disagreements instead of choosing.' },
  { node: 'gap_analysis', title: 'Gap Analyst', kind: 'code', what: 'Turns missing, national-only, single-source and outdated evidence into explicit gaps.' },
  { node: 'persist', title: 'Knowledge Builder', kind: 'store', what: 'Writes the audit trail and the evidence index.' },
  { node: 'build_graph', title: 'Graph Builder', kind: 'store', what: 'Feeds verified relationship claims to Graphiti, which builds the temporal knowledge graph.' },
]
const KIND_BADGE = { agent: ['info', 'LLM agent'], gate: ['bad', 'Gate'], code: ['plain', 'Deterministic'], store: ['ok', 'Datastore'] } as const

export default function MethodTab({ ov }: { ov: Overview }) {
  return (
    <div className="grid g2" style={{ alignItems: 'start' }}>
      <div className="card pad stack">
        <h3>The workflow behind this briefing (LangGraph)</h3>
        <div className="steps">
          {FLOW.map(f => (
            <div key={f.node} className="step done">
              <div className="dot" style={f.kind === 'gate' ? { background: 'var(--bad)', borderColor: 'var(--bad)' } : undefined}>{f.kind === 'gate' ? '!' : '✓'}</div>
              <div><div className="row" style={{ gap: 8 }}><span className="title">{f.title}</span><span className={`badge ${KIND_BADGE[f.kind][0]}`}>{KIND_BADGE[f.kind][1]}</span>
                <code className="small faint">{f.node}</code></div><div className="msg">{f.what}</div></div>
            </div>
          ))}
        </div>
      </div>
      <div className="stack">
        <div className="card pad stack">
          <h3>What lives where</h3>
          <table><tbody>
            <tr><td><b>Relational</b><div className="small faint">{ov.stores.relational}</div></td><td className="small">The system of record: runs, every source and its crawl decision, every claim with verdict, reasoning and geography, including rejected ones. Answers “where did this come from?”.</td></tr>
            <tr><td><b>Vector</b><div className="small faint">{ov.stores.vector}</div></td><td className="small">Verified facts and verbatim source passages, found by meaning. Rejected claims are never indexed.</td></tr>
            <tr><td><b>Graph</b><div className="small faint">{ov.stores.graph}</div></td><td className="small">Who runs, funds, governs and partners with what, over time. Verified relationship claims only; statistics stay out.</td></tr>
          </tbody></table>
        </div>
        <div className="card pad stack">
          <h3>What keeps this honest</h3>
          <p className="small">· A claim without a quote found in its source is discarded by code, not by a model’s judgement.</p>
          <p className="small">· The fact checker is a different model from the extractor and never sees the extractor’s reasoning.</p>
          <p className="small">· National and global figures are labelled as such; the checker corrects the geography when the extractor gets it wrong.</p>
          <p className="small">· Answers may cite only retrieved evidence. Citations that point nowhere are removed.</p>
          <p className="small">· Missing information is a first-class result: gaps and unread official sources are reported, not hidden.</p>
        </div>
        <div className="card pad stack">
          <h3>Known limits</h3>
          <p className="small muted">No JavaScript rendering, so some modern sites read as empty. Publication dates come from page metadata only. Free-tier models and search quotas bound depth to about 24 documents per run. Human review before storage is not implemented; the audit trail is designed so a reviewer could be added at the fact-check gate.</p>
        </div>
      </div>
    </div>
  )
}
