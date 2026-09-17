import { GEO_LABEL, isLocal, type Overview } from '../../api'

const GEO_COLOR: Record<string, string> = { city: 'var(--brand)', metro: 'var(--brand)', district: 'var(--brand)', state: 'var(--info)', national: 'var(--line-strong)', global: 'var(--faint)', unknown: 'var(--warn)' }

export default function OverviewTab({ ov, cityId }: { ov: Overview; cityId: string }) {
  const c = ov.counts
  const notAccepted = Object.values(c.not_accepted).reduce((a, b) => a + b, 0)
  const local = Object.entries(c.by_geo).filter(([g]) => isLocal(g)).reduce((a, [, n]) => a + n, 0)
  const pctLocal = c.verified ? Math.round((100 * local) / c.verified) : 0
  const sev = { high: 0, medium: 1, low: 2 } as const

  return (
    <div className="stack" style={{ gap: 22 }}>
      <div className="grid g4">
        <div className="card stat ok"><div className="n">{c.verified}</div><div className="l">facts verified against their source{c.partially_supported ? `, ${c.partially_supported} only partially` : ''}</div></div>
        <div className="card stat"><div className="n">{c.sources_read}<span className="muted" style={{ fontSize: 16 }}> / {c.sources}</span></div><div className="l">sources read / discovered</div></div>
        <div className="card stat bad"><div className="n">{notAccepted}</div><div className="l">claims rejected by the fact checker</div></div>
        <div className="card stat warn"><div className="n">{c.gaps}</div><div className="l">knowledge gaps{c.conflicts ? ` · ${c.conflicts} conflicts` : ''}</div></div>
      </div>

      <div className="card pad stack">
        <div className="row"><h3 className="grow">How local is this evidence?</h3><span className="small muted">{pctLocal}% is about {ov.city.name} itself</span></div>
        <div className="bar">{Object.entries(c.by_geo).sort((a, b) => Number(isLocal(b[0])) - Number(isLocal(a[0]))).map(([g, n]) =>
          <span key={g} title={`${GEO_LABEL[g] ?? g}: ${n}`} style={{ width: `${(100 * n) / c.verified}%`, background: GEO_COLOR[g] ?? 'var(--faint)' }} />)}</div>
        <div className="legend">{Object.entries(c.by_geo).map(([g, n]) => <span key={g}><i style={{ background: GEO_COLOR[g] ?? 'var(--faint)' }} />{GEO_LABEL[g] ?? g} · {n}</span>)}</div>
        {pctLocal < 30 && <p className="small muted">Most health statistics are published nationally. They are shown as national context and never presented as figures for {ov.city.name}.</p>}
      </div>

      <div className="grid g3">
        {ov.categories.map(k => (
          <a key={k.key} className="card pad" href={`#/city/${cityId}/findings`} style={{ color: 'inherit', textDecoration: 'none' }}>
            <div className="row"><h3 className="grow">{k.name}</h3>
              {k.verified === 0 ? <span className="badge bad">no evidence</span> : k.city_level === 0 ? <span className="badge broad">national only</span> : <span className="badge local">{k.city_level} local</span>}</div>
            <p className="small muted" style={{ margin: '6px 0 12px' }}>{k.why}</p>
            <div><b style={{ fontFamily: 'var(--serif)', fontSize: 22 }}>{k.verified}</b> <span className="small muted">verified facts</span></div>
          </a>
        ))}
      </div>

      <div className="grid g2" style={{ alignItems: 'start' }}>
        <div className="card pad stack">
          <h3>What we do not know</h3>
          {ov.gaps.length === 0 && <p className="muted small">No gaps recorded.</p>}
          {[...ov.gaps].sort((a, b) => sev[a.severity] - sev[b.severity]).map((g, i) => (
            <div key={i} className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap' }}>
              <span className={`badge ${g.severity === 'high' ? 'bad' : g.severity === 'medium' ? 'warn' : 'plain'}`}>{g.severity}</span>
              <div className="small"><div>{g.description}</div>{g.suggestion && <div className="faint" style={{ wordBreak: 'break-word' }}>Next step: {g.suggestion}</div>}</div>
            </div>
          ))}
        </div>
        <div className="stack">
          {ov.conflicts.length > 0 && <div className="card pad stack"><h3>Sources that disagree</h3>
            {ov.conflicts.map((k, i) => <p key={i} className="small">{k.description}</p>)}</div>}
          <div className="card pad stack">
            <h3>Assumptions the planner made</h3>
            {(ov.run?.assumptions ?? []).length === 0 && <p className="muted small">None recorded.</p>}
            {(ov.run?.assumptions ?? []).map((a, i) => <p key={i} className="small">· {a}</p>)}
            <p className="small faint">Assumptions are not facts. They shaped where we searched; nothing here is stated on their strength.</p>
          </div>
          <div className="card pad stack">
            <h3>Sources we found but could not read</h3>
            <p className="small muted">{c.sources_denied} of {c.sources} sources did not permit automated reading. We record them and do not work around the refusal.</p>
            {Object.entries(c.denied_reasons).sort((a, b) => b[1] - a[1]).map(([r, n]) => <div key={r} className="row small"><span className="badge plain">{n}</span><span>{r}</span></div>)}
          </div>
        </div>
      </div>
    </div>
  )
}
