// #/preview/cost — the cost card over the live map, with a few cases one click away. The card itself
// reads whatever case is on the map, so any drop, storm or time of day works too.
import { useOverload } from '../../store'
import { Button } from '../../ui'
import CostCard from './CostCard'

const CASES = [
  { id: 'fm-big', label: 'Fort Myers · 1,500 MW', region: 'FL', lat: 26.64, lon: -81.87, mw: 1500 },
  { id: 'fm-firm', label: 'Fort Myers · 1,500 MW firm', region: 'FL', lat: 26.64, lon: -81.87, mw: 1500, firm: true },
  { id: 'fm-small', label: 'Fort Myers · 500 MW', region: 'FL', lat: 26.64, lon: -81.87, mw: 500 },
  { id: 'orl', label: 'Orlando · 500 MW', region: 'FL', lat: 28.5384, lon: -81.3789, mw: 500 },
  { id: 'abi', label: 'Abilene, Texas · 1,200 MW', region: 'TX', lat: 32.45, lon: -99.73, mw: 1200 },
]

export default function Preview() {
  const { region, setRegion, setMw, place, setFirm, resetAll } = useOverload()
  const pick = (c) => {
    if (c.region !== region) {
      setRegion(c.region, { place: [c.lat, c.lon], mw: c.mw })
      return
    }
    setMw(c.mw)
    setFirm(!!c.firm)
    place(c.lat, c.lon)
  }
  return (
    <div className="stack">
      <div className="row" role="group" aria-label="Try a case">
        {CASES.map((c) => (
          <Button key={c.id} variant="secondary" onClick={() => pick(c)}>
            {c.label}
          </Button>
        ))}
        <Button variant="secondary" onClick={resetAll}>
          Start over
        </Button>
      </div>
      <CostCard />
    </div>
  )
}
