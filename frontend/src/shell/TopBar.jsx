// The one bar across the top of every page (user, Sat 08:13-08:16: "the bar on the top is horribly shit…
// add a nice high quality bar"; top tabs picked Sat 08:14): the name, the state (on the map), the pages,
// and how AI is used. Tabs are plain links; on the map page `onPick` switches between the destruction demo
// and Strengthen without leaving the page.
import HowAiIsUsed from '../features/ai/HowAiIsUsed'
import { useOverload } from '../store'
import './topbar.css'

const PAGES = [
  { id: 'demo', label: 'Destruction demo', href: '#/', title: 'Drop a data center on the grid and watch what fails' },
  { id: 'strengthen', label: 'Strengthen', href: '#/strengthen', title: 'Find the weak points and the cheapest upgrades that let more data centers connect' },
  { id: 'agreement', label: 'Build agreement', href: '#/plans', title: "Where two utilities' planned projects overlap, and how they could build them together" },
  { id: 'proposals', label: 'Proposals', href: '#/vote', title: 'Look up a real proposed data center: what it could do to a grid, what it could cost, what to ask before it is approved' },
  { id: 'views', label: 'Views', href: '#/views', title: 'Where data centers are; population and energy by state' },
]

export default function TopBar({ active, onPick, withState = false }) {
  return (
    <header className="appbar">
      <a className="appbar__brand" href="#/" onClick={(e) => onPick?.('demo', e)}>
        Overload
      </a>
      {withState && <StatePicker />}
      <nav className="appbar__tabs" aria-label="Pages">
        {PAGES.map((p) => (
          <a
            key={p.id}
            href={p.href}
            title={p.title}
            className={`appbar__tab${active === p.id ? ' is-on' : ''}`}
            aria-current={active === p.id ? 'page' : undefined}
            onClick={(e) => onPick?.(p.id, e)}
          >
            {p.label}
          </a>
        ))}
      </nav>
      <div className="appbar__end">
        <HowAiIsUsed className="appbar__ai" />
      </div>
    </header>
  )
}

// The state switcher: any of the 48 state models, or the whole-U.S. map (click a state there to open it).
// Changing state clears the case, like Start over; the map flies to the new state.
function StatePicker() {
  const { region, regions, setRegion } = useOverload()
  if (!regions) return null
  const sorted = [...regions].sort((a, b) => a.name.localeCompare(b.name))
  return (
    <label className="appbar__state">
      <span className="appbar__sr">State</span>
      <select value={region} onChange={(e) => setRegion(e.target.value)}>
        <option value="US">Whole U.S. (click a state)</option>
        {sorted.map((r) => (
          <option key={r.code} value={r.code}>
            {r.name}
          </option>
        ))}
      </select>
    </label>
  )
}
