import { useEffect, useMemo, useRef, useState } from 'react'
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, type SimulationLinkDatum, type SimulationNodeDatum } from 'd3-force'
import { api, type GraphData } from '../../api'

const COLORS: Record<string, string> = {
  Organization: '#0b7285', Programme: '#c2571a', Policy: '#5f3dc4', Place: '#2b8a3e', HealthCondition: '#c92a2a', Person: '#a61e4d', Facility: '#1864ab', Entity: '#868e96',
}
type N = SimulationNodeDatum & { id: string; name: string; type: string; degree: number }
type L = SimulationLinkDatum<N> & { relation: string; fact: string; invalidated: boolean }

export default function GraphTab({ cityId, ready }: { cityId: string; ready: boolean }) {
  const [data, setData] = useState<GraphData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [, setTick] = useState(0)
  const [sel, setSel] = useState<string | null>(null)
  const [view, setView] = useState({ x: 0, y: 0, k: 1 })
  const drag = useRef<{ x: number; y: number } | null>(null)
  const W = 1100, H = 560

  useEffect(() => { api.graph(cityId).then(setData).catch(e => setErr(e.message)) }, [cityId, ready])

  const { nodes, links } = useMemo(() => {
    if (!data) return { nodes: [] as N[], links: [] as L[] }
    const deg: Record<string, number> = {}
    data.edges.forEach(e => { deg[e.source] = (deg[e.source] ?? 0) + 1; deg[e.target] = (deg[e.target] ?? 0) + 1 })
    const nodes: N[] = data.nodes.map(n => ({ ...n, degree: deg[n.id] ?? 0 }))
    const links: L[] = data.edges.map(e => ({ ...e }))
    return { nodes, links }
  }, [data])

  useEffect(() => {
    if (!nodes.length) return
    const sim = forceSimulation(nodes)
      .force('link', forceLink<N, L>(links).id(d => d.id).distance(95).strength(0.5))
      .force('charge', forceManyBody().strength(-260))
      .force('center', forceCenter(W / 2, H / 2))
      .force('collide', forceCollide(30))
      .on('tick', () => setTick(t => t + 1))
    return () => { sim.stop() }
  }, [nodes, links])

  if (err) return <div className="notice bad">{err}</div>
  if (!data) return <div className="muted"><span className="spinner" /> Loading the knowledge graph…</div>
  if (!data.nodes.length) return <div className="notice">{ready ? 'No relationships were found for this city.' : 'The knowledge graph is still being built. This view fills in as it grows.'}</div>

  const facts = sel ? links.filter(l => (l.source as N).id === sel || (l.target as N).id === sel) : []
  const selNode = nodes.find(n => n.id === sel)
  const types = [...new Set(nodes.map(n => n.type))]

  return (
    <div className="stack">
      <div className="row">
        <span className="muted small grow">{nodes.length} entities · {links.length} relationships, built only from fact-checked claims with Graphiti on Neo4j. Click an entity to see what we know about it.</span>
        <span className="legend">{types.map(t => <span key={t}><i style={{ background: COLORS[t] ?? COLORS.Entity }} />{t}</span>)}</span>
      </div>
      <div className="grid" style={{ gridTemplateColumns: sel ? 'minmax(0,2fr) minmax(0,1fr)' : '1fr' }}>
        <div className="card graph-wrap"
          onWheel={e => setView(v => ({ ...v, k: Math.min(3, Math.max(0.4, v.k * (e.deltaY < 0 ? 1.1 : 0.9))) }))}
          onMouseDown={e => { drag.current = { x: e.clientX - view.x, y: e.clientY - view.y } }}
          onMouseMove={e => { if (drag.current) setView(v => ({ ...v, x: e.clientX - drag.current!.x, y: e.clientY - drag.current!.y })) }}
          onMouseUp={() => { drag.current = null }} onMouseLeave={() => { drag.current = null }}>
          <svg viewBox={`0 0 ${W} ${H}`}>
            <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
              {links.map((l, i) => {
                const s = l.source as N, t = l.target as N
                const on = sel && (s.id === sel || t.id === sel)
                return <g key={i} opacity={sel && !on ? 0.12 : 1}>
                  <line x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke={on ? 'var(--brand)' : 'var(--line-strong)'} strokeWidth={on ? 2 : 1.2} strokeDasharray={l.invalidated ? '4 3' : undefined} />
                  {on && <text x={((s.x ?? 0) + (t.x ?? 0)) / 2} y={((s.y ?? 0) + (t.y ?? 0)) / 2 - 4} fontSize="9" textAnchor="middle" fill="var(--muted)">{l.relation.replace(/_/g, ' ').toLowerCase()}</text>}
                </g>
              })}
              {nodes.map(n => {
                const on = !sel || n.id === sel || facts.some(l => (l.source as N).id === n.id || (l.target as N).id === n.id)
                return <g key={n.id} transform={`translate(${n.x ?? 0},${n.y ?? 0})`} opacity={on ? 1 : 0.18} style={{ cursor: 'pointer' }}
                  onClick={e => { e.stopPropagation(); setSel(s => (s === n.id ? null : n.id)) }}>
                  <circle r={7 + Math.min(10, n.degree * 1.6)} fill={COLORS[n.type] ?? COLORS.Entity} stroke="var(--surface)" strokeWidth="2" />
                  <text y={-(12 + Math.min(10, n.degree * 1.6))} fontSize="10.5" textAnchor="middle" fill="var(--text)" fontWeight={n.id === sel ? 700 : 500}>{n.name.length > 34 ? n.name.slice(0, 32) + '…' : n.name}</text>
                </g>
              })}
            </g>
          </svg>
        </div>
        {selNode && (
          <div className="card pad stack" style={{ maxHeight: 560, overflow: 'auto' }}>
            <div><span className="badge plain">{selNode.type}</span><h3 style={{ marginTop: 8, fontSize: 17 }}>{selNode.name}</h3></div>
            {facts.map((l, i) => <div key={i} className="small" style={{ borderTop: '1px solid var(--line)', paddingTop: 10 }}>
              <div className="faint" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.05em' }}>{l.relation.replace(/_/g, ' ')}</div>{l.fact}</div>)}
            <p className="small faint">Every relationship traces back to fact-checked claims and their sources; ask about this entity in “Ask the city” to see them.</p>
          </div>
        )}
      </div>
    </div>
  )
}
