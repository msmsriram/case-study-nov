import { useEffect, useState } from 'react'
import Home from './views/Home'
import RunView from './views/RunView'
import CityView from './views/CityView'

function useHashRoute(): string[] {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const on = () => setHash(window.location.hash)
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return hash.replace(/^#\/?/, '').split('/').filter(Boolean)
}

export const go = (path: string) => { window.location.hash = path }

export default function App() {
  const parts = useHashRoute()
  let view = <Home />
  if (parts[0] === 'run' && parts[1]) view = <RunView runId={parts[1]} />
  if (parts[0] === 'city' && parts[1]) view = <CityView cityId={parts[1]} tab={parts[2] ?? 'overview'} />
  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/"><span className="mark">♥</span>CARDIO4Cities · City Intelligence</a>
        <span className="spacer" />
        <span className="sub">Live research · independently fact-checked · every fact traceable</span>
      </header>
      {view}
    </div>
  )
}
