import { STATES } from '../../geo'
import { useOverload } from '../../store'
import { useCatalog } from './catalogData'
import { href } from './router'
import { areaName } from './sentence'

// The top bar on every page: the wordmark (home), where you are (breadcrumbs), and the two places
// you can always go (the map of America, the library).
export default function AppBar({ route }) {
  const crumbs = useCrumbs(route)
  const onHome = route.page === 'home'
  return (
    <header className="nx-bar">
      <a className="nx-brand" href={href()} aria-label="Overload home: the map of America">
        <span className="nx-brand__mark" aria-hidden="true" />
        Overload
      </a>
      <nav className="nx-crumbs" aria-label="Breadcrumb">
        <ol>
          {crumbs.map((c, i) => {
            const last = i === crumbs.length - 1
            return (
              <li key={`${i}-${c.label}`}>
                {last || !c.to ? (
                  <span aria-current={last ? 'page' : undefined}>{c.label}</span>
                ) : (
                  <a href={c.to}>{c.label}</a>
                )}
              </li>
            )
          })}
        </ol>
      </nav>
      <nav className="nx-nav" aria-label="Main">
        <a href={href()} className={onHome ? 'nx-nav__on' : undefined} aria-current={onHome ? 'page' : undefined}>
          Map
        </a>
        <a
          href={href('/library')}
          className={route.page === 'library' ? 'nx-nav__on' : undefined}
          aria-current={route.page === 'library' ? 'page' : undefined}
        >
          Library
        </a>
      </nav>
    </header>
  )
}

function useCrumbs(route) {
  const { result, site, region } = useOverload()
  const { entries } = useCatalog()
  const america = { label: 'America', to: href() }
  if (route.page === 'home') return [{ label: 'America' }]
  if (route.page === 'state') {
    if (!STATES[route.code] || route.code === 'DC') return [america, { label: 'No grid model' }]
    const name = STATES[route.code].name
    const crumbs = [america, { label: name, to: href(`/state/${route.code}`) }]
    const area = region === route.code && site && result?.sub_name ? result.sub_area || areaName(result.sub_name) : null
    crumbs.push({ label: area ? `${area} scenario` : 'New scenario' })
    return crumbs
  }
  if (route.page === 'dc') {
    const e = entries?.find((x) => x.id === route.id)
    return [america, { label: 'Data centers', to: href() }, { label: e?.name || 'Data center' }]
  }
  if (route.page === 'library') return [america, { label: 'Library' }]
  if (route.page === 'compare') return [america, { label: 'Library', to: href('/library') }, { label: 'Compare' }]
  if (route.page === 'brief') return [america, { label: 'Library', to: href('/library') }, { label: 'Brief' }]
  return [america, { label: 'Not found' }]
}
