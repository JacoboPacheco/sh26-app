import { useEffect, useState } from 'react'
import DataCentersTab from './DataCentersTab'
import EnergyTab from './EnergyTab'
import PopulationTab from './PopulationTab'
import './views.css'

// Views (#/views): the data-center locator (filter by state, company, status and size), population graphs and
// energy graphs. Read-only, from committed data; nothing runs on the grid engine here. Tabs are hash routes
// (#/views/datacenters, #/views/population, #/views/energy) so a view can be linked; a tab stays mounted once
// opened, so its filters and map position survive a visit to another tab.

const TABS = [
  { id: 'datacenters', label: 'Data centers' },
  { id: 'population', label: 'Population' },
  { id: 'energy', label: 'Energy' },
]

const readHash = () => {
  const m = window.location.hash.match(/^#\/views(?:\/([a-z]+))?(?:\?(.*))?/)
  const tab = TABS.some((t) => t.id === m?.[1]) ? m[1] : 'datacenters'
  const site = new URLSearchParams(m?.[2] || '').get('site')
  return { tab, site }
}

export default function ViewsPage() {
  const [route, setRoute] = useState(readHash)
  const [seen, setSeen] = useState(() => new Set([readHash().tab]))

  useEffect(() => {
    const on = () => {
      const r = readHash()
      setRoute(r)
      setSeen((s) => (s.has(r.tab) ? s : new Set([...s, r.tab])))
    }
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])

  useEffect(() => {
    const prev = document.title
    document.title = 'Views: data centers, population, energy | Overload'
    return () => {
      document.title = prev
    }
  }, [])

  const { tab } = route
  return (
    <div className="vw-shell">
      <header className="vw-top">
        <a className="vw-top__map" href="#/">
          <span aria-hidden="true">←</span> Overload map
        </a>
        <span className="vw-top__name">Views</span>
        <nav className="vw-tabs" aria-label="Views">
          {TABS.map((t) => (
            <a key={t.id} className="vw-tab" href={`#/views/${t.id}`} aria-current={t.id === tab ? 'page' : undefined}>
              {t.label}
            </a>
          ))}
        </nav>
        <span className="vw-top__syn">Synthetic grid models · reported sizes as reported</span>
      </header>

      <main className="vw-main" id="vw-main">
        <div hidden={tab !== 'datacenters'}>
          {seen.has('datacenters') && (
            <>
              <div className="vw-lead">
                <h1 className="vw-h2">Where the data centers are</h1>
                <p>
                  Reported sites across the United States, from three open or researched sources. Filter by state, company, status or size, and set the reported capacity beside each state&apos;s grid model. Every site links its
                  sources; nothing here says a company causes a blackout.
                </p>
              </div>
              <DataCentersTab initialSite={route.site} />
            </>
          )}
        </div>
        <div hidden={tab !== 'population'}>{seen.has('population') && <PopulationTab />}</div>
        <div hidden={tab !== 'energy'}>{seen.has('energy') && <EnergyTab />}</div>
      </main>

      <footer className="vw-foot">
        <p>
          <strong>Sources.</strong> Data centers: the Overload catalog (researched from public news and company sources), <a href="https://www.compute-atlas.com">Compute Atlas</a> (Kubiak, E.; CC BY 4.0; doi 10.5281/zenodo.22284476) and{' '}
          <a href="https://epoch.ai/data/ai-data-centers">Epoch AI</a> (AI data centers, epoch.ai; CC BY). Sizes, places and statuses are as reported by the sources linked on each site; a site listed by two sources appears once.
        </p>
        <p>
          <strong>Grid models.</strong> Synthetic: the Breakthrough Energy / Texas A&amp;M test system (CC-BY 4.0), never a real utility&apos;s network. Residents: U.S. Census Bureau, Vintage 2024 estimates. A campus tested on a model is
          a campus of the reported size at that location on a synthetic model, not a prediction about the real project or utility.
        </p>
      </footer>
    </div>
  )
}
