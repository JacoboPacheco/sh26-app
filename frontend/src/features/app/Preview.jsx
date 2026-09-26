import { href } from './router'

// #/preview/app — the new application shell lives at #/next; these open its pages.
const PAGES = [
  ['Home: the map of America', href()],
  ['Florida workspace: Fort Myers, 1,500 MW (the hero)', href('/state/FL', { lat: 26.64, lon: -81.87, mw: 1500 })],
  ['Texas workspace: Abilene, 1,200 MW', href('/state/TX', { lat: 32.45, lon: -99.73, mw: 1200 })],
  ['A data center: Fort Meade campus', href('/dc/stonebridge-fort-meade')],
  ['Is my area at risk? (Florida)', href('/state/FL', { panel: 'area' })],
  ['Library', href('/library')],
  ['Compare', href('/compare')],
]

export default function Preview() {
  return (
    <div className="stack">
      <p className="muted">The application shell is its own page. Open one:</p>
      <ul>
        {PAGES.map(([label, to]) => (
          <li key={to}>
            <a href={to}>{label}</a>
          </li>
        ))}
      </ul>
    </div>
  )
}
